"""Pure data -> narrative logic (no Streamlit): timeframe filtering, aggregation, health ranking, plain-language story."""
from __future__ import annotations

import math
import os

import pandas as pd
from dash_i18n import check_name, finding_name, make_tr

TIMEFRAMES = {"all_time": None, "last_7": 7, "last_14": 14, "last_30": 30}


CURRENCY = os.getenv("DASH_CURRENCY", "$")          # e.g. "R$" for Olist, "₫" for VND


def money(x: float) -> str:
    x = float(x or 0)
    c = CURRENCY
    if abs(x) >= 1_000_000_000:
        return f"{c}{x / 1_000_000_000:.2f}B"
    if c == "₫":                                              # VND: no cents
        return f"{c}{x / 1_000_000:.1f}M" if abs(x) >= 1_000_000 else f"{c}{x / 1000:.0f}K" if abs(x) >= 10_000 else f"{c}{x:,.0f}"
    if abs(x) >= 1_000_000:
        return f"{c}{x / 1_000_000:.2f}M"
    if abs(x) >= 10_000:
        return f"{c}{x / 1000:.1f}K"
    return f"{c}{x:,.0f}" if abs(x) >= 1000 else f"{c}{x:,.2f}"


def pct(x: float, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%"


def filter_sales(sales: pd.DataFrame, timeframe: str, channel: str | None) -> pd.DataFrame:
    df = sales
    days = TIMEFRAMES.get(timeframe)
    if days and len(df):
        df = df[df["order_date"] > df["order_date"].max() - pd.Timedelta(days=days)]
    if channel:
        df = df[df["channel"] == channel]
    return df


def aggregate(sales: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Net sales per day / week / month (start date of the period)."""
    if sales.empty:
        return pd.DataFrame(columns=["period", "net_sales"])
    d = sales.groupby("order_date", as_index=False)["net_sales"].sum()
    if granularity == "week":
        d["period"] = d["order_date"].dt.to_period("W-SUN").dt.start_time
    elif granularity == "month":
        d["period"] = d["order_date"].dt.to_period("M").dt.start_time
    else:
        d["period"] = d["order_date"]
    out = d.groupby("period", as_index=False)["net_sales"].sum().sort_values("period")
    if granularity in ("week", "month") and len(out) > 3:          # the newest week/month is usually incomplete: its dip is not a trend
        last_day = d["order_date"].max()
        end = out["period"].iloc[-1] + (pd.Timedelta(days=6) if granularity == "week" else pd.offsets.MonthEnd(0))
        if last_day < end.normalize():
            out = out.iloc[:-1]
    return out


def smooth(values: list[float], window: int = 3) -> list[float]:
    if len(values) < window:
        return list(values)
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - window + 1), i + 1
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def daily_series(sales: pd.DataFrame, col: str) -> list[float]:
    if sales.empty:
        return []
    return smooth(sales.groupby("order_date")[col].sum().sort_index().tolist())


def check_health(summary: pd.DataFrame) -> pd.DataFrame:
    """Per check type: share of orders WITHOUT a confirmed finding ('Just waiting' is not a failure)."""
    s = summary[summary["result"] != "N/A"].copy()
    s["bad"] = s["finding_class"].eq("CONFIRMED") * s["orders"]
    g = s.groupby("check_type", as_index=False).agg(total=("orders", "sum"), bad=("bad", "sum"))
    g["pass_rate"] = 1 - g["bad"] / g["total"].where(g["total"] > 0, 1)
    return g.sort_values("pass_rate").reset_index(drop=True)


def split_weak_strong(health: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(health)
    k = math.ceil(n / 2)
    weak = health.head(k)
    strong = health.tail(n - k).sort_values("pass_rate", ascending=False)
    return weak, strong


def top_finding_types(summary: pd.DataFrame) -> pd.DataFrame:
    c = summary[summary["finding_class"] == "CONFIRMED"]
    return (c.groupby(["check_type", "result"], as_index=False)["orders"].sum().sort_values("orders", ascending=False))


def channel_board(sales: pd.DataFrame) -> pd.DataFrame:
    """Net sales per channel with trend = last 7 days vs the 7 days before (in the data's own calendar)."""
    if sales.empty:
        return pd.DataFrame(columns=["channel", "net_sales", "cancel_rate", "trend"])
    end = sales["order_date"].max()
    cur = sales[sales["order_date"] > end - pd.Timedelta(days=7)].groupby("channel")["net_sales"].sum()
    prev = sales[(sales["order_date"] <= end - pd.Timedelta(days=7)) &
                 (sales["order_date"] > end - pd.Timedelta(days=14))].groupby("channel")["net_sales"].sum()
    g = sales.groupby("channel", as_index=False).agg(net_sales=("net_sales", "sum"), orders=("orders", "sum"),
                                                     cancelled=("cancelled_orders", "sum"))
    g["cancel_rate"] = g["cancelled"] / g["orders"].where(g["orders"] > 0, 1)
    g["trend"] = g["channel"].map(lambda c: (cur.get(c, 0) - prev.get(c, 0)))
    return g.sort_values("net_sales", ascending=False).reset_index(drop=True)


def biggest_findings(findings: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    f = findings[findings["finding_class"] == "CONFIRMED"]
    return f.sort_values(["amount_difference", "created_at"], ascending=[False, False]).head(n)


def build_story(lang: str, sales: pd.DataFrame, recon_kpi: pd.Series, summary: pd.DataFrame) -> dict[str, str]:
    """Plain-language headline + what / why / next, computed from the same numbers as the charts."""
    tr = make_tr(lang)
    checked = int(recon_kpi["orders_checked"])
    issues = int(recon_kpi["orders_with_confirmed_issue"])
    timing = int(recon_kpi["orders_with_timing_only"])
    clean = float(recon_kpi["clean_orders_pct"]) / 100
    amount = float(recon_kpi["unresolved_amount_difference"])
    health = check_health(summary)
    out = {"headline": tr("headline_ok", clean=pct(clean, 1), issues=issues),
           "what": tr("story_what_body", checked=f"{checked:,}", issues=issues, timing=timing)}
    if issues == 0 or health.empty:
        out["why"] = out["todo"] = tr("story_all_good")
        return out
    weak = health.iloc[0]
    out["why"] = tr("story_why_body", amount=money(amount), weak=check_name(lang, weak["check_type"]),
                    weak_pct=pct(weak["pass_rate"], 0))
    top = top_finding_types(summary).iloc[0]
    out["todo"] = tr("story_todo_body", top=f"{check_name(lang, top['check_type'])}: {finding_name(lang, top['result'])}",
                     n=int(top["orders"]))
    return out
