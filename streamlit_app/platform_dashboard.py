"""Omnichannel E-commerce Platform dashboard (SIMULATED data), designed to be readable by non-technical people.

    GCP_PROJECT_ID=my-project python -m streamlit run streamlit_app/platform_dashboard.py            # BigQuery (ADC login)
    DASH_DUCKDB_PATH=.demo/warehouse.duckdb python -m streamlit run streamlit_app/platform_dashboard.py   # offline demo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dash_story as story  # noqa: E402
import dash_ui as ui  # noqa: E402
import platform_data as data  # noqa: E402
from dash_i18n import LANGS, check_name, finding_name, make_tr  # noqa: E402

st.set_page_config(page_title="Omnichannel Data Platform", page_icon="📊", layout="wide")
ui.inject_css()

if "lang" not in st.session_state:
    st.session_state["lang"] = "vi"
lang = st.session_state["lang"]
tr = make_tr(lang)


# ------------------------------------------------------------------ data
@st.cache_resource(show_spinner=False)
def backend():
    return data.get_backend()


@st.cache_data(ttl=300, show_spinner="Loading…")
def load(view: str) -> pd.DataFrame:
    return backend().view(view)


def load_all():
    try:
        return {v: load(v) for v in ("v_sales_daily", "v_recon_kpi", "v_recon_summary", "v_recon_findings", "v_pipeline_health")}
    except Exception as exc:
        st.error(f"{tr('load_error')}: {exc}")
        st.info(tr("load_help"))
        st.stop()


# ------------------------------------------------------------------ shared widgets
def header(title: str, csv: tuple[str, bytes] | None = None) -> None:
    a, b = st.columns([8, 2])
    a.markdown(f'<div class="page-title">{ui.esc(title)}</div>', unsafe_allow_html=True)
    if csv:
        b.download_button("⬇  " + tr("download"), csv[1], csv[0], "text/csv", use_container_width=True)
    ui.render('<hr class="page-rule">')


def filters(page: str, sales: pd.DataFrame, with_view: bool = True):
    cols = st.columns(3 if with_view else 2)
    tf_keys = list(story.TIMEFRAMES)
    tf = cols[0].selectbox("tf", tf_keys, key=f"{page}_tf", label_visibility="collapsed",
                           format_func=lambda k: f"{tr('timeframe')}: {tr(k)}")
    channels = ["__all__"] + sorted(sales["channel"].unique())
    ch = cols[1].selectbox("ch", channels, key=f"{page}_ch", label_visibility="collapsed",
                           format_func=lambda k: f"{tr('channel')}: {tr('all') if k == '__all__' else k}")
    gran = "day"
    if with_view:
        gran = cols[2].selectbox("gr", ["day", "week", "month"], key=f"{page}_gr", label_visibility="collapsed",
                                 format_func=lambda k: f"{tr('view_by')}: {tr(k)}")
    return tf, (None if ch == "__all__" else ch), gran


def activity_card(f: pd.DataFrame, gran: str, height: int = 352) -> str:
    agg = story.aggregate(f, gran)
    fmt = {"day": "%d %b", "week": "%d %b", "month": "%b %Y"}[gran]
    labels = [d.strftime(fmt).upper() for d in agg["period"]]
    chart = ui.bar_chart(labels, agg["net_sales"].tolist(), fmt=lambda v: story.money(v).replace(".00", ""))
    return (f'<div class="card" style="min-height:{height}px"><h4>{ui.esc(tr("activity"))} · '
            f'<span style="color:#9CA3AF">{ui.esc(tr("activity_sub", unit=tr(gran).lower()))}</span></h4>{chart}</div>')


def nice_check(t: str) -> str:
    return check_name(lang, t)


# ------------------------------------------------------------------ pages
def page_overview():
    d = load_all()
    sales, kpi, summary, findings = d["v_sales_daily"], d["v_recon_kpi"].iloc[0], d["v_recon_summary"], d["v_recon_findings"]
    header(tr("title_overview"), ("reconciliation_findings.csv", findings.to_csv(index=False).encode()))
    tf, ch, gran = filters("ov", sales)
    f = story.filter_sales(sales, tf, ch)
    s = story.build_story(lang, f, kpi, summary)

    ui.render(f'<div class="banner"><div class="eyebrow">{ui.esc(tr("story_title"))}</div>'
              f'<div class="headline">{ui.esc(s["headline"])}</div><div class="sim">{ui.esc(tr("sim_note"))}</div></div>')
    c = st.columns(3)
    for col, key in zip(c, ("what", "why", "todo"), strict=True):
        body = ui.esc(s[key]).replace("**", "§").replace("*", "")
        parts = body.split("§")
        body = "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))
        col.markdown("".join(x.strip() for x in f'<div class="card story"><h5>{ui.esc(tr("story_" + key))}</h5><p>{body}</p></div>'.splitlines()),
                     unsafe_allow_html=True)
    st.write("")

    left, right = st.columns([6, 5])
    orders, cancelled = f["orders"].sum(), f["cancelled_orders"].sum()
    gross, net = f["gross_sales"].sum(), f["net_sales"].sum()
    aov_series = story.smooth((f.groupby("order_date")["gross_sales"].sum() /
                               (f.groupby("order_date")["orders"].sum() - f.groupby("order_date")["cancelled_orders"].sum()).clip(lower=1)).tolist())
    with left:
        r1 = st.columns(3)
        r1[0].markdown(ui.kpi(tr("kpi_net"), story.money(net), visual=ui.sparkline(story.daily_series(f, "net_sales"))), unsafe_allow_html=True)
        r1[1].markdown(ui.kpi(tr("kpi_orders"), f"{orders:,.0f}", visual=ui.sparkline(story.daily_series(f, "orders"))), unsafe_allow_html=True)
        r1[2].markdown(ui.kpi(tr("kpi_aov"), story.money(gross / (orders - cancelled) if orders - cancelled else 0),
                              visual=ui.sparkline(aov_series)), unsafe_allow_html=True)
        st.write("")
        r2 = st.columns(3)
        clean = float(kpi["clean_orders_pct"])
        r2[0].markdown(ui.kpi(tr("kpi_clean"), f"{clean:.1f}", "%", tr("kpi_of_orders", n=f"{int(kpi['orders_checked']):,}"),
                              ui.meter(clean / 100)), unsafe_allow_html=True)
        r2[1].markdown(ui.kpi(tr("kpi_issues"), f"{int(kpi['orders_with_confirmed_issue'])}", "",
                              tr("kpi_timing_note", n=int(kpi["orders_with_timing_only"])),
                              ui.meter(int(kpi["orders_with_confirmed_issue"]) / max(1, int(kpi["orders_checked"])))), unsafe_allow_html=True)
        r2[2].markdown(ui.kpi(tr("kpi_amount"), story.money(kpi["unresolved_amount_difference"]), "", tr("g_confirmed")),
                       unsafe_allow_html=True)
    right.markdown("".join(x.strip() for x in activity_card(f, gran, 352).splitlines()), unsafe_allow_html=True)
    st.write("")

    health = story.check_health(summary)
    weak, strong = story.split_weak_strong(health)
    mk = lambda df: [(nice_check(r.check_type), r.pass_rate, nice_check(r.check_type)[:2].upper()) for r in df.itertuples()]  # noqa: E731
    a, b = st.columns(2)
    a.markdown(ui.card(tr("weakest"), ui.progress_rows(mk(weak), tr("ok_suffix")), min_height=270), unsafe_allow_html=True)
    b.markdown(ui.card(tr("strongest"), ui.progress_rows(mk(strong), tr("ok_suffix")), min_height=270), unsafe_allow_html=True)
    st.write("")

    board = story.channel_board(sales)
    rows = [{"rank": i + 1, "name": r.channel, "trend": 1 if r.trend > 0 else (-1 if r.trend < 0 else 0),
             "sub": ui.esc(tr("net_sales_cancel", sales=story.money(r.net_sales), rate=story.pct(r.cancel_rate, 1)))}
            for i, r in enumerate(board.itertuples())]
    big = story.biggest_findings(findings)
    frows = [{"rank": i + 1, "name": f"{tr('order_label')} {r.order_id}", "trend": 0, "ini": nice_check(r.check_type)[:2].upper(),
              "sub": ui.esc(f"{nice_check(r.check_type)} · {finding_name(lang, r.finding)} · {story.money(r.amount_difference)}")}
             for i, r in enumerate(big.itertuples())]
    c1, c2 = st.columns(2)
    c1.markdown(ui.card(f"{tr('channel_board')} · ▲▼ {tr('trend_note')}", ui.leader_rows(rows), min_height=330), unsafe_allow_html=True)
    c2.markdown(ui.card(tr("findings_board"), ui.leader_rows(frows), min_height=330), unsafe_allow_html=True)


def page_sales():
    sales = load_all()["v_sales_daily"]
    header(tr("title_sales"))
    tf, ch, gran = filters("sales", sales)
    f = story.filter_sales(sales, tf, ch)
    orders = f["orders"].sum()
    gross, net, refunded = f["gross_sales"].sum(), f["net_sales"].sum(), f["refunded_amount"].sum()
    k = st.columns(4)
    k[0].markdown(ui.kpi(tr("kpi_net"), story.money(net), visual=ui.sparkline(story.daily_series(f, "net_sales"))), unsafe_allow_html=True)
    k[1].markdown(ui.kpi(tr("gross"), story.money(gross), visual=ui.sparkline(story.daily_series(f, "gross_sales"))), unsafe_allow_html=True)
    k[2].markdown(ui.kpi(tr("kpi_orders"), f"{orders:,.0f}", visual=ui.sparkline(story.daily_series(f, "orders"))), unsafe_allow_html=True)
    k[3].markdown(ui.kpi(tr("refunds"), story.money(refunded), "", f"{story.pct(refunded / gross if gross else 0, 1)} / {tr('gross').lower()}",
                         ui.sparkline(story.daily_series(f, "refunded_amount"), color="#F59E0B")), unsafe_allow_html=True)
    st.write("")
    st.markdown("".join(x.strip() for x in activity_card(f, gran, 340).splitlines()), unsafe_allow_html=True)
    st.write("")
    by = f.groupby("channel", as_index=False)[["gross_sales", "refunded_amount"]].sum().sort_values("gross_sales", ascending=False)
    rows = [(r.channel, r.gross_sales, r.channel[:2].upper(), tr("gross").lower()) for r in by.itertuples()]
    st.markdown(ui.card(tr("sales_by_channel"), ui.count_rows([(a, b, c, "$") for a, b, c, _ in rows])), unsafe_allow_html=True)
    with st.expander(tr("daily_detail")):
        st.dataframe(f.sort_values("order_date", ascending=False), width="stretch", hide_index=True)


def page_recon():
    d = load_all()
    kpi, summary, findings = d["v_recon_kpi"].iloc[0], d["v_recon_summary"], d["v_recon_findings"]
    header(tr("title_recon"), ("reconciliation_findings.csv", findings.to_csv(index=False).encode()))
    ui.render(ui.card("", f'<div class="story"><p>{ui.esc(tr("recon_intro")).replace("*", "")}</p></div>'))
    st.write("")
    k = st.columns(4)
    k[0].markdown(ui.kpi(tr("kpi_clean"), f"{kpi['clean_orders_pct']:.1f}", "%", tr("kpi_of_orders", n=f"{int(kpi['orders_checked']):,}"),
                         ui.meter(kpi["clean_orders_pct"] / 100)), unsafe_allow_html=True)
    k[1].markdown(ui.kpi(tr("confirmed"), f"{int(kpi['confirmed_findings'])}", "", tr("g_confirmed_d")), unsafe_allow_html=True)
    k[2].markdown(ui.kpi(tr("timing"), f"{int(kpi['timing_findings'])}", "", tr("g_timing_d")), unsafe_allow_html=True)
    k[3].markdown(ui.kpi(tr("kpi_amount"), story.money(kpi["unresolved_amount_difference"]), "", tr("g_confirmed")), unsafe_allow_html=True)
    st.write("")
    health = story.check_health(summary)
    top = story.top_finding_types(summary)
    a, b = st.columns(2)
    a.markdown(ui.card(tr("findings_by_check"), ui.progress_rows(
        [(nice_check(r.check_type), r.pass_rate, nice_check(r.check_type)[:2].upper()) for r in health.itertuples()], tr("ok_suffix"))),
        unsafe_allow_html=True)
    b.markdown(ui.card(tr("findings_by_type"), ui.count_rows(
        [(f"{nice_check(r.check_type)}: {finding_name(lang, r.result)}", r.orders, nice_check(r.check_type)[:2].upper(), tr("unit_orders"))
         for r in top.head(7).itertuples()])), unsafe_allow_html=True)
    st.write("")
    st.subheader(tr("findings_table"))
    st.caption(tr("class_help"))
    c1, c2 = st.columns(2)
    cls = c1.multiselect(tr("filter_class"), ["CONFIRMED", "TIMING"], default=["CONFIRMED"],
                         format_func=lambda x: tr("confirmed" if x == "CONFIRMED" else "timing"))
    types = sorted(findings["check_type"].unique())
    sel = c2.multiselect(tr("filter_check"), types, default=types, format_func=nice_check)
    v = findings[findings["finding_class"].isin(cls) & findings["check_type"].isin(sel)] \
        .sort_values(["amount_difference", "created_at"], ascending=[False, False]).copy()
    shown = pd.DataFrame({tr("col_order"): v["order_id"], tr("col_channel"): v["channel"],
                          tr("col_check"): v["check_type"].map(nice_check), tr("col_finding"): v["finding"].map(lambda x: finding_name(lang, x)),
                          tr("col_status"): v["finding_class"].map(lambda x: tr("confirmed" if x == "CONFIRMED" else "timing")),
                          tr("col_amount"): v["amount_difference"], tr("col_created"): v["created_at"]})
    st.dataframe(shown, width="stretch", hide_index=True, column_config={
        tr("col_amount"): st.column_config.NumberColumn(format="$%.2f"),
        tr("col_created"): st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")})


def page_pipeline():
    h = load_all()["v_pipeline_health"].sort_values("rows_loaded", ascending=False)
    header(tr("title_pipeline"))
    ui.render(ui.card("", f'<div class="story"><p>{ui.esc(tr("pipe_intro"))}</p></div>'))
    st.write("")
    k = st.columns(4)
    k[0].markdown(ui.kpi(tr("pipe_tables"), f"{len(h)}"), unsafe_allow_html=True)
    k[1].markdown(ui.kpi(tr("pipe_rows"), f"{h['rows_loaded'].sum():,.0f}"), unsafe_allow_html=True)
    k[2].markdown(ui.kpi(tr("pipe_versions"), f"{h['extra_versions'].sum():,.0f}", "", tr("g_oms") and ""), unsafe_allow_html=True)
    k[3].markdown(ui.kpi(tr("pipe_last"), f"{h['last_loaded_at'].max():%d %b %H:%M}"), unsafe_allow_html=True)
    st.write("")
    chart = ui.bar_chart([x.split(".")[-1] for x in h["dataset"]], h["rows_loaded"].tolist(), height=260)
    st.markdown(ui.card(tr("pipe_rows_chart"), chart), unsafe_allow_html=True)
    st.write("")
    shown = h.rename(columns={"dataset": tr("col_table"), "rows_loaded": tr("col_rows"), "batches": tr("col_batches"),
                              "last_loaded_at": tr("col_last"), "minutes_since_last_load": tr("col_age")})
    st.dataframe(shown[[tr("col_table"), tr("col_rows"), tr("col_batches"), tr("col_last"), tr("col_age")]], width="stretch", hide_index=True)
    st.caption(tr("pipe_note"))


def page_guide():
    header(tr("title_guide"))
    ui.render(ui.card(tr("guide_what"), f'<div class="story"><p>{ui.esc(tr("guide_what_body"))}</p></div>'))
    st.write("")
    ui.render(ui.card(tr("guide_flow"), ui.flow([(tr(f"flow_{i}"), tr(f"flow_{i}d")) for i in range(1, 6)])))
    st.write("")
    pairs = [(tr(k), tr(k + "_d")) for k in ("g_finding", "g_confirmed", "g_timing", "g_settle", "g_oms", "g_clean", "g_fresh")]
    ui.render(ui.card(tr("guide_glossary"), ui.glossary(pairs)))
    st.write("")
    st.info(tr("sim_note"))


def page_settings():
    header(tr("nav_settings"))
    project = os.getenv("GCP_PROJECT_ID") or "-"
    src = f"DuckDB ({os.getenv('DASH_DUCKDB_PATH')})" if data.backend_name() == "duckdb" else f"BigQuery ({project})"
    ui.render(ui.card("", ui.glossary([(tr("language"), LANGS[lang]), ("Data source", src), ("Views", "platform_reporting.*"),
                                       ("Cache", "5 min")])))


# ------------------------------------------------------------------ routing
PAGES = {"overview": page_overview, "sales": page_sales, "recon": page_recon, "pipeline": page_pipeline,
         "guide": page_guide, "settings": page_settings}

forced = os.getenv("DASH_PAGE")                    # tests / screenshots: render one page without navigation
if forced:
    PAGES[forced]()
else:
    def _p(key, icon, group_title=None):
        return st.Page(PAGES[key], title=tr(f"nav_{key}"), icon=icon, url_path=key, default=(key == "overview"))
    main = [_p("overview", ":material/space_dashboard:"), _p("sales", ":material/trending_up:"),
            _p("recon", ":material/fact_check:"), _p("pipeline", ":material/account_tree:")]
    support = [_p("guide", ":material/lightbulb:"), _p("settings", ":material/settings:")]
    current = st.navigation(main + support, position="hidden")
    with st.sidebar:
        ui.render(ui.brand(tr("brand"), tr("brand_sub")))
        for p in main:
            st.page_link(p)
        st.markdown(f'<div style="color:#9CA3AF;font-weight:500;padding:1.2rem .85rem .4rem">{ui.esc(tr("support"))}</div>', unsafe_allow_html=True)
        for p in support:
            st.page_link(p)
        st.write("")
        st.selectbox(tr("language"), list(LANGS), key="lang", format_func=lambda k: LANGS[k])
    current.run()
