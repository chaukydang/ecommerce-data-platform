"""Omnichannel E-commerce Platform dashboard (SIMULATED data) - Sales, Financial Reconciliation, Pipeline Health.

    GCP_PROJECT_ID=my-project streamlit run streamlit_app/platform_dashboard.py          # BigQuery (ADC login)
    DASH_DUCKDB_PATH=.demo/warehouse.duckdb streamlit run streamlit_app/platform_dashboard.py   # offline demo
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platform_data as data  # noqa: E402

st.set_page_config(page_title="Omnichannel Data Platform", page_icon="📊", layout="wide")

PALETTE = ["#2F6FED", "#12A594", "#F5A524", "#E5484D", "#8E4EC6", "#6B7280"]
CHECK_COLORS = {"Order vs OMS": "#2F6FED", "Payment": "#12A594", "Currency": "#8E4EC6", "Refund": "#6B7280", "Settlement": "#0EA5E9"}
CLASS_COLORS = {"CONFIRMED": "#E5484D", "TIMING": "#F5A524", "OK": "#12A594"}
px.defaults.color_discrete_sequence = PALETTE
px.defaults.template = "plotly_white"


@st.cache_resource(show_spinner=False)
def backend():
    return data.get_backend()


@st.cache_data(ttl=300, show_spinner="Loading from the warehouse…")
def load(view: str) -> pd.DataFrame:
    return backend().view(view)


def money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 1000 else f"${x:,.2f}"


def style(fig, height=340):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=36, b=8), legend_title_text="",
                      title_font_size=15, font=dict(size=12))
    return fig


# ------------------------------------------------------------------ header
st.title("Omnichannel E-commerce Data Platform")
st.caption("Ingestion → BigQuery → dbt → reconciliation → monitoring. **All data is simulated** (marketplace / OMS / POS / finance simulators "
           "with deliberately injected discrepancies); it is not production data.")

try:
    sales = load("v_sales_daily")
    recon_kpi = load("v_recon_kpi").iloc[0]
    recon_sum = load("v_recon_summary")
    findings = load("v_recon_findings")
    health = load("v_pipeline_health")
except Exception as exc:                                   # surface setup problems clearly instead of a stack trace
    st.error(f"Could not load the reporting views: {exc}")
    st.info("Run `sql/bigquery_dashboard_views.sql` in BigQuery first, then `gcloud auth application-default login` "
            "and set `GCP_PROJECT_ID`. For an offline demo set `DASH_DUCKDB_PATH`.")
    st.stop()

tab_sales, tab_recon, tab_health = st.tabs(["Sales overview", "Financial reconciliation", "Pipeline health"])

# ------------------------------------------------------------------ sales
with tab_sales:
    c1, c2 = st.columns([2, 3])
    channels = c1.multiselect("Channel", sorted(sales["channel"].unique()), default=sorted(sales["channel"].unique()))
    lo, hi = sales["order_date"].min().date(), sales["order_date"].max().date()
    rng = c2.date_input("Order date range", (lo, hi), min_value=lo, max_value=hi)
    start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (lo, hi))
    f = sales[sales["channel"].isin(channels) & sales["order_date"].between(pd.Timestamp(start), pd.Timestamp(end))]

    orders, cancelled = f["orders"].sum(), f["cancelled_orders"].sum()
    gross, refunded, net = f["gross_sales"].sum(), f["refunded_amount"].sum(), f["net_sales"].sum()
    k = st.columns(5)
    k[0].metric("Net sales", money(net))
    k[1].metric("Gross sales", money(gross))
    k[2].metric("Orders", f"{orders:,.0f}")
    k[3].metric("Cancel rate", f"{(cancelled / orders if orders else 0):.1%}")
    k[4].metric("Avg order value", money(gross / (orders - cancelled) if orders - cancelled else 0))

    left, right = st.columns([3, 2])
    left.plotly_chart(style(px.line(f.sort_values("order_date"), x="order_date", y="net_sales", color="channel",
                                    title="Net sales by day", markers=True, labels={"order_date": "", "net_sales": "Net sales"})),
                      width="stretch")
    by_ch = f.groupby("channel", as_index=False)[["gross_sales", "refunded_amount"]].sum()
    right.plotly_chart(style(px.bar(by_ch.melt("channel", var_name="measure", value_name="amount"), x="channel", y="amount",
                                    color="measure", barmode="group", title="Gross sales vs refunds by channel",
                                    labels={"channel": "", "amount": ""})), width="stretch")
    with st.expander("Daily detail"):
        st.dataframe(f.sort_values("order_date", ascending=False), width="stretch", hide_index=True)

# ------------------------------------------------------------------ reconciliation
with tab_recon:
    k = st.columns(4)
    k[0].metric("Clean orders", f"{recon_kpi['clean_orders_pct']:.1f}%",
                help="Orders with no CONFIRMED finding (timing differences are not counted as errors).")
    k[1].metric("Confirmed findings", f"{recon_kpi['confirmed_findings']:,.0f}")
    k[2].metric("Timing differences", f"{recon_kpi['timing_findings']:,.0f}",
                help="Inside the grace window (e.g. settlement not yet due): expected lag, auto-resolves.")
    k[3].metric("Unresolved difference", money(recon_kpi["unresolved_amount_difference"]),
                help="Sum of amount-type CONFIRMED findings.")

    issues = recon_sum[recon_sum["finding_class"] != "OK"]
    a, b = st.columns(2)
    a.plotly_chart(style(px.bar(issues.groupby(["check_type", "finding_class"], as_index=False)["orders"].sum(),
                                x="orders", y="check_type", color="finding_class", orientation="h", barmode="stack",
                                color_discrete_map=CLASS_COLORS, title="Findings by check type",
                                labels={"orders": "Orders", "check_type": ""})), width="stretch")
    conf = issues[issues["finding_class"] == "CONFIRMED"].sort_values("orders")
    b.plotly_chart(style(px.bar(conf, x="orders", y="result", color="check_type", orientation="h", color_discrete_map=CHECK_COLORS,
                                title="Confirmed findings by type", labels={"orders": "Orders", "result": ""})), width="stretch")

    st.subheader("Findings")
    f1, f2 = st.columns(2)
    cls = f1.multiselect("Class", ["CONFIRMED", "TIMING"], default=["CONFIRMED"])
    types = f2.multiselect("Check type", sorted(findings["check_type"].unique()), default=sorted(findings["check_type"].unique()))
    view = findings[findings["finding_class"].isin(cls) & findings["check_type"].isin(types)] \
        .sort_values(["amount_difference", "created_at"], ascending=[False, False])
    st.dataframe(view, width="stretch", hide_index=True, column_config={
        "amount_difference": st.column_config.NumberColumn("Amount difference", format="$%.2f"),
        "created_at": st.column_config.DatetimeColumn("Created", format="YYYY-MM-DD HH:mm")})
    st.download_button("Download findings (CSV)", view.to_csv(index=False).encode(), "reconciliation_findings.csv", "text/csv")

# ------------------------------------------------------------------ pipeline health
with tab_health:
    st.caption("Derived from the raw layer. Retry counts, failures and recovery times are in the operational audit table "
               "(`pipeline_run_audit`).")
    k = st.columns(4)
    k[0].metric("Raw tables", f"{len(health):,}")
    k[1].metric("Rows loaded", f"{health['rows_loaded'].sum():,.0f}")
    k[2].metric("Record versions kept", f"{health['extra_versions'].sum():,.0f}", help="Updates preserved as history.")
    k[3].metric("Last load (UTC)", f"{health['last_loaded_at'].max():%d %b %H:%M}")
    st.plotly_chart(style(px.bar(health.sort_values("rows_loaded"), x="rows_loaded", y="dataset", orientation="h",
                                 title="Rows loaded per raw table", labels={"rows_loaded": "Rows", "dataset": ""})),
                    width="stretch")
    st.dataframe(health, width="stretch", hide_index=True, column_config={
        "minutes_since_last_load": st.column_config.NumberColumn("Minutes since last load", format="%d")})
