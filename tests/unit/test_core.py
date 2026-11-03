from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta

import pandas as pd
import pytest

from ecomplatform.ingestion.core.api_client import ApiClient
from ecomplatform.ingestion.core.checkpoint import CheckpointStore
from ecomplatform.ingestion.core.pagination import cursor_pages, page_number_pages
from ecomplatform.ingestion.core.retry_policy import AuthError, RetryPolicy, TransientError
from ecomplatform.ingestion.core.schema_validator import validate_records
from ecomplatform.monitoring import quality
from ecomplatform.monitoring.alerting import Alert, AlertManager, LarkNotifier, format_message
from ecomplatform.reconciliation import engine as rec
from ecomplatform.simulators.generator import generate
from ecomplatform.util import record_hash, redact

T0 = datetime(2026, 11, 3, 9, 0, 0)


# ---------------------------------------------------------------- retry / pagination
def test_retry_backoff_is_exponential_and_capped():
    delays = []
    p = RetryPolicy(max_attempts=6, base_delay=1.0, max_delay=5.0, jitter=0.0, sleep=delays.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 6:
            raise TransientError("boom")
        return "ok"
    assert p.call(flaky) == "ok"
    assert delays == [1.0, 2.0, 4.0, 5.0, 5.0] and p.retries_used == 5


def test_retry_honours_retry_after_and_gives_up():
    delays = []
    p = RetryPolicy(max_attempts=3, jitter=0.0, sleep=delays.append)
    with pytest.raises(TransientError):
        p.call(lambda: (_ for _ in ()).throw(TransientError("x", retry_after=2.5)))
    assert delays == [2.5, 2.5]


def test_non_transient_errors_are_not_retried():
    p = RetryPolicy(sleep=lambda s: None)
    n = {"c": 0}

    def bad():
        n["c"] += 1
        raise AuthError("nope")
    with pytest.raises(AuthError):
        p.call(bad)
    assert n["c"] == 1


class FakeClient:
    def __init__(self, bodies):
        self.bodies, self.calls = list(bodies), []

    def get_json(self, path, params=None):
        self.calls.append(dict(params or {}))
        return self.bodies.pop(0)


def test_cursor_pagination_follows_cursor_until_exhausted():
    c = FakeClient([{"data": [1, 2], "next_cursor": "a"}, {"data": [3], "next_cursor": "b"}, {"data": [4], "next_cursor": None}])
    assert [x for p in cursor_pages(c, "/x", {"limit": 2}) for x in p] == [1, 2, 3, 4]
    assert [call.get("cursor") for call in c.calls] == [None, "a", "b"]


def test_page_number_pagination():
    c = FakeClient([{"items": [1], "total_pages": 2}, {"items": [2], "total_pages": 2}])
    assert [x for p in page_number_pages(c, "/x") for x in p] == [1, 2]
    assert [call["page"] for call in c.calls] == [1, 2]


# ---------------------------------------------------------------- validation / checkpoint / hashing
SCHEMA = {"fields": {"id": {"type": "string", "required": True}, "amt": {"type": "number", "required": True},
                     "n": {"type": "integer"}}}


def test_schema_validator_rejects_and_detects_drift():
    res = validate_records([{"id": "a", "amt": 1.5}, {"id": "b", "amt": "x"}, {"amt": 2}, {"id": "c", "amt": 3, "new": 1}], SCHEMA)
    assert [r["id"] for r in res.valid] == ["a", "c"]
    assert len(res.rejected) == 2 and "expected number" in res.rejected[0][1] and "missing required" in res.rejected[1][1]
    assert res.unexpected_fields == {"new"} and res.drift


def test_bool_is_not_a_number():
    assert validate_records([{"id": "a", "amt": True}], SCHEMA).rejected


def test_record_hash_ignores_metadata_and_key_order():
    assert record_hash({"a": 1, "b": 2}) == record_hash({"b": 2, "a": 1, "_meta": "x"})
    assert record_hash({"a": 1}) != record_hash({"a": 2})


def test_checkpoint_never_moves_backwards(engine):
    cp = CheckpointStore(engine)
    cp.advance("s", "e", T0, "b1")
    cp.advance("s", "e", T0 - timedelta(days=1), "b2")
    assert cp.get("s", "e") == T0
    assert cp.window_start("s", "e", timedelta(minutes=10), T0 - timedelta(days=9)) == T0 - timedelta(minutes=10)
    assert cp.window_start("x", "y", timedelta(minutes=10), T0 - timedelta(days=9)) == T0 - timedelta(days=9)


def test_redact_removes_tokens_and_webhooks():
    s = redact("Bearer abc123SECRET and token=xyz789 hook https://open.larksuite.com/open-apis/bot/v2/hook/abcdef-1234 pw", ["abc123SECRET"])
    assert "abc123SECRET" not in s and "xyz789" not in s and "abcdef-1234" not in s


def test_api_client_error_never_contains_token(monkeypatch):
    import requests

    class S:
        def get(self, *a, **k):
            raise requests.ConnectionError("failed with Bearer SUPERSECRETTOKEN at host")
    c = ApiClient("http://x", token="SUPERSECRETTOKEN", retry=RetryPolicy(max_attempts=1), session=S())
    with pytest.raises(TransientError) as e:
        c.get_json("/p")
    assert "SUPERSECRETTOKEN" not in str(e.value)


# ---------------------------------------------------------------- alerting
class Sink:
    def __init__(self):
        self.msgs = []

    def send(self, t):
        self.msgs.append(t)
        return True


def _alert(sev="WARNING", **kw):
    return Alert("FRESHNESS_VIOLATION", sev, "ingest_x", "src", "stale", dataset="raw.x", run_id="r1", **kw)


def test_alert_cooldown_grouping_escalation_and_recovery(engine):
    s = Sink()
    am = AlertManager(engine, s, cooldown_minutes=60)
    assert am.raise_alert(_alert(), now=T0)                                   # first -> sent
    assert not am.raise_alert(_alert(), now=T0 + timedelta(minutes=10))       # grouped
    assert not am.raise_alert(_alert(), now=T0 + timedelta(minutes=20))
    assert am.raise_alert(_alert("CRITICAL"), now=T0 + timedelta(minutes=30))  # escalation bypasses cooldown
    assert not am.raise_alert(_alert("CRITICAL"), now=T0 + timedelta(minutes=40))
    assert am.raise_alert(_alert("CRITICAL"), now=T0 + timedelta(minutes=95))  # cooldown over -> reminder
    assert "grouped" in s.msgs[-1]
    assert am.resolve(_alert(), now=T0 + timedelta(minutes=100))
    assert "RECOVERED" in s.msgs[-1]
    assert not am.resolve(_alert(), now=T0 + timedelta(minutes=101))          # already closed
    assert am.raise_alert(_alert(), now=T0 + timedelta(minutes=120))          # new incident after recovery
    assert len(s.msgs) == 5


def test_info_alerts_below_min_severity_are_dropped(engine):
    s = Sink()
    assert not AlertManager(engine, s).raise_alert(_alert("INFO"))
    assert not s.msgs


def test_message_matches_documented_format():
    m = format_message(_alert("CRITICAL", details={"Last Successful Load": "2026-11-03 09:00", "Current Delay": "75 minutes"},
                              action="Investigate upstream API availability."))
    for part in ("[DATA PIPELINE ALERT]", "Severity: CRITICAL", "Pipeline: ingest_x", "Last Successful Load:",
                 "Current Delay:", "Affected Dataset:", "raw.x", "Action:", "Run ID: r1"):
        assert part in m


def test_lark_signature_and_payload_and_no_url_leak(monkeypatch):
    posted = {}

    class R:
        status_code = 200
        def json(self): return {"code": 0}

    class Sess:
        def post(self, url, json, timeout):
            posted.update(url=url, json=json)
            return R()
    n = LarkNotifier("https://open.larksuite.com/open-apis/bot/v2/hook/SECRET-HOOK", secret="shh", session=Sess())
    assert n.send("hello")
    p = posted["json"]
    expect = base64.b64encode(hmac.new(f"{p['timestamp']}\nshh".encode(), b"", hashlib.sha256).digest()).decode()
    assert p["msg_type"] == "text" and p["content"]["text"] == "hello" and p["sign"] == expect


def test_lark_failure_never_raises_or_leaks(caplog):
    class Sess:
        def post(self, *a, **k):
            raise RuntimeError("cannot reach https://open.larksuite.com/open-apis/bot/v2/hook/SECRET-HOOK")
    n = LarkNotifier("https://open.larksuite.com/open-apis/bot/v2/hook/SECRET-HOOK", session=Sess())
    with caplog.at_level("WARNING"):
        assert n.send("x") is False
    assert "SECRET-HOOK" not in caplog.text


def test_no_webhook_configured_is_a_dry_run():
    assert LarkNotifier(webhook_url="", secret="").send("x") is False


# ---------------------------------------------------------------- data quality primitives
def test_quality_checks():
    df = pd.DataFrame({"id": ["a", "a", "b"], "s": ["X", "Y", None], "v": [1.0, -2.0, 3.0]})
    assert quality.check_pk_unique(df, ["id"]) == (False, {"duplicate_rows": 2})
    assert not quality.check_required(df, ["s"])[0]
    assert not quality.check_accepted_values(df, "s", ["X"])[0]
    assert not quality.check_non_negative(df, "v")[0]
    assert quality.check_relationship(df, "id", pd.DataFrame({"k": ["a"]}), "k")[1]["orphan_rows"] == 1
    assert quality.check_volume(100, 50, 100)[0] and not quality.check_volume(300, 50, 100)[0]
    assert quality.check_volume(0, None, 100)[0] is False


# ---------------------------------------------------------------- reconciliation primitives
DEF = {"tolerance_abs": 0.01, "amount_precision": 2}


def test_amount_tolerance_boundary():
    t = {"l": pd.DataFrame({"k": ["a", "b"], "amt": [10.00, 10.00], "created_at": [T0, T0]}),
         "r": pd.DataFrame({"k": ["a", "b"], "amt": [10.01, 10.02], "source_record_id": ["1", "2"]})}
    rule = {"id": "R", "entity_type": "order", "left": "l", "right": "r", "key": "k", "left_col": "amt", "right_col": "amt",
            "severity": "WARNING"}
    out, checked = rec.amount_mismatch(rule, t, T0, DEF)
    assert checked == 2 and [d.entity_id for d in out] == ["b"]            # 0.01 is within tolerance, 0.02 is not


def test_grace_window_classifies_timing_vs_confirmed():
    assert rec._klass(T0 - timedelta(hours=1), T0, 2) == "TIMING"
    assert rec._klass(T0 - timedelta(hours=3), T0, 2) == "CONFIRMED"
    assert rec._klass(T0, T0, 0) == "CONFIRMED"


def test_settlement_formula_per_channel():
    df = pd.DataFrame([{"channel": "c1", "order_id": "o", "gross_amount": 100.0, "seller_discount": 5.0, "refund_amount": 0.0,
                        "commission_fee": 5.0, "shipping_fee": 2.0, "adjustment_amount": 1.0, "net_payout": 89.0},
                       {"channel": "c2", "order_id": "p", "gross_amount": 100.0, "seller_discount": 0.0, "refund_amount": 0.0,
                        "commission_fee": 10.0, "shipping_fee": 0.0, "adjustment_amount": 0.0, "net_payout": 100.0}])
    formulas = {"default": {"gross_amount": 1, "seller_discount": -1, "refund_amount": -1, "commission_fee": -1,
                            "shipping_fee": -1, "adjustment_amount": 1},
                "c2": {"gross_amount": 1}}                                   # channel c2 pays out gross only
    rule = {"id": "S", "entity_type": "settlement", "table": "s", "key": "order_id", "actual_col": "net_payout", "severity": "CRITICAL"}
    out, _ = rec.settlement_formula(rule, {"s": df}, T0, DEF, formulas)
    assert out == []
    df.loc[0, "net_payout"] = 80.0
    out, _ = rec.settlement_formula(rule, {"s": df}, T0, DEF, formulas)
    assert [(d.entity_id, d.difference) for d in out] == [("o", -9.0)]


# ---------------------------------------------------------------- simulator
def test_generator_is_deterministic_and_labels_injections():
    a, b = generate(300, seed=7, as_of=T0), generate(300, seed=7, as_of=T0)
    assert a.marketplace_orders == b.marketplace_orders and a.manifest == b.manifest
    assert generate(300, seed=8, as_of=T0).marketplace_orders != a.marketplace_orders
    assert len(a.manifest) == sum(1 for _ in a.manifest) > 25
    assert not generate(300, seed=7, as_of=T0, inject=False).manifest
