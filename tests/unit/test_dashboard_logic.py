"""Pure-logic tests for the dashboard: story generation, aggregation, health ranking, SVG components, i18n completeness."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "streamlit_app"))
import dash_i18n as i18n  # noqa: E402
import dash_story as story  # noqa: E402

pytest.importorskip("streamlit")
import dash_ui as ui  # noqa: E402

SUMMARY = pd.DataFrame({
    "check_type": ["Payment", "Payment", "Refund", "Refund", "Currency", "Settlement", "Settlement"],
    "result": ["OK", "AMOUNT_MISMATCH", "OK", "REFUND_EXCEEDS_PAID", "OK", "OK", "PENDING_SETTLEMENT"],
    "finding_class": ["OK", "CONFIRMED", "OK", "CONFIRMED", "OK", "OK", "TIMING"],
    "orders": [90, 10, 20, 5, 100, 50, 40],
})
KPI = pd.Series({"orders_checked": 403, "orders_with_confirmed_issue": 24, "orders_with_timing_only": 60,
                 "clean_orders_pct": 94.04, "unresolved_amount_difference": 585.82})


def test_both_languages_define_the_same_keys():
    assert set(i18n.TEXT["vi"]) == set(i18n.TEXT["en"])


@pytest.mark.parametrize("lang", ["vi", "en"])
def test_story_uses_real_numbers_and_has_no_unfilled_placeholders(lang):
    s = story.build_story(lang, None, KPI, SUMMARY)
    assert "94.0%" in s["headline"] and "24" in s["headline"]
    assert "403" in s["what"] and "60" in s["what"]
    assert "$585.82" in s["why"]
    assert "10" in s["todo"] and not any(re.search(r"\{\w+\}", v) for v in s.values())


def test_story_all_clear_when_no_confirmed_issue():
    k = KPI.copy()
    k["orders_with_confirmed_issue"] = 0
    s = story.build_story("en", None, k, SUMMARY)
    assert "Nothing to fix" in s["why"]


def test_timing_is_not_counted_as_failure_in_health():
    h = story.check_health(SUMMARY).set_index("check_type")
    assert h.loc["Settlement", "pass_rate"] == 1.0                    # only 'just waiting' rows
    assert h.loc["Refund", "pass_rate"] == pytest.approx(0.8)
    assert list(story.check_health(SUMMARY)["check_type"])[0] == "Refund"   # weakest first
    weak, strong = story.split_weak_strong(story.check_health(SUMMARY))
    assert len(weak) + len(strong) == 4 and not set(weak.check_type) & set(strong.check_type)


def test_timeframe_and_channel_filters_and_aggregation():
    days = pd.date_range("2026-10-01", periods=40)
    df = pd.DataFrame({"order_date": list(days) * 2, "channel": ["a"] * 40 + ["b"] * 40, "net_sales": 10.0, "orders": 1,
                       "cancelled_orders": 0, "gross_sales": 10.0, "refunded_amount": 0.0})
    assert df["order_date"].nunique() == 40
    assert story.filter_sales(df, "last_7", None)["order_date"].nunique() == 7
    assert set(story.filter_sales(df, "all_time", "a")["channel"]) == {"a"}
    assert len(story.aggregate(df, "day")) == 40
    month = story.aggregate(df, "month")
    assert month["net_sales"].sum() == df["net_sales"].sum() and len(month) == 2
    assert story.aggregate(df.iloc[0:0], "week").empty


def test_channel_board_trend_compares_last_two_weeks():
    days = pd.date_range("2026-10-01", periods=14)
    rows = [{"order_date": d, "channel": "up", "net_sales": 1.0 if i < 7 else 5.0, "orders": 1, "cancelled_orders": 0} for i, d in enumerate(days)]
    rows += [{"order_date": d, "channel": "down", "net_sales": 5.0 if i < 7 else 1.0, "orders": 1, "cancelled_orders": 0} for i, d in enumerate(days)]
    b = story.channel_board(pd.DataFrame(rows)).set_index("channel")
    assert b.loc["up", "trend"] > 0 > b.loc["down", "trend"]


def test_money_formatting():
    assert story.money(585.82) == "$585.82" and story.money(91205) == "$91.2K" and story.money(2_500_000) == "$2.50M"


def test_svg_components_are_valid_xml():
    for svg in (ui.sparkline([1, 3, 2, 5, 4]), ui.bar_chart(["a", "b", "c"], [10, 20, 5])):
        ET.fromstring(svg)                                           # well-formed
    assert ui.sparkline([1]) == "" and ui.bar_chart([], []) == ""
    assert ui.nice_max(9300) == 10000 and ui.nice_max(0) == 1.0


def test_html_components_escape_user_text():
    assert "<script>" not in ui.kpi("<script>x</script>", "1")
    rows = ui.progress_rows([("a", 0.5, "A"), ("b", 0.9, "B"), ("c", 0.99, "C")], "ok")
    assert rows.count("#EF4444") >= 1 and rows.count("#F59E0B") >= 1 and rows.count("#22C55E") >= 1   # red / amber / green by level
    assert "▲" in ui.leader_rows([{"rank": 1, "name": "x", "sub": "s", "trend": 1}])
