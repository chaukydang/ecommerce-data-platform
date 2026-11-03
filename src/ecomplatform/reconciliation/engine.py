"""Config-driven reconciliation engine (pandas). Rules live in configs/reconciliation_rules.yaml."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from sqlalchemy import insert, update
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.monitoring.alerting import Alert, AlertManager
from ecomplatform.util import utcnow

from .readers import WarehouseReader
from .store import ACTIVE, Detected, ExceptionStore

RuleResult = tuple[list[Detected], int]   # (exceptions, entities checked)


@dataclass
class ReconciliationSummary:
    run_id: str
    as_of: datetime
    checked: int = 0
    matched: int = 0
    confirmed: int = 0
    timing: int = 0
    confirmed_difference: float = 0.0
    store_stats: dict = field(default_factory=dict)
    per_rule: dict[str, dict] = field(default_factory=dict)

    @property
    def matched_pct(self) -> float:
        return round(100.0 * self.matched / self.checked, 2) if self.checked else 100.0


# --------------------------------------------------------------------------------------- helpers
def _klass(ts, as_of: datetime, grace_hours: float) -> str:
    if grace_hours and ts is not None and not pd.isna(ts):
        return "TIMING" if pd.Timestamp(ts) > pd.Timestamp(as_of - timedelta(hours=grace_hours)) else "CONFIRMED"
    return "CONFIRMED"


def _src(df: pd.DataFrame, mask_idx=None, default="multi") -> str:
    return str(df["source_system"].iloc[0]) if "source_system" in df.columns and len(df) else default


def _filter(df: pd.DataFrame, where: str | None) -> pd.DataFrame:
    return df.query(where) if where else df


def _fmt(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return f"{v:.2f}" if isinstance(v, (float,)) else str(v)


def _prep(df: pd.DataFrame, dedupe: str | None, key: str, agg_col: str | None = None, agg: str | None = None) -> pd.DataFrame:
    if dedupe:
        df = df.sort_values(["source_record_id"]).drop_duplicates(subset=[dedupe], keep="first")
    if agg == "sum":
        df = df.groupby(key, as_index=False).agg({agg_col: "sum"})
    return df


# --------------------------------------------------------------------------------------- rule types
def missing_in_target(rule, tables, as_of, defaults) -> RuleResult:
    left = _filter(tables[rule["left"]], rule.get("where"))
    right_keys = set(tables[rule["right"]][rule["right_key"]])
    out = []
    miss = left[~left[rule["left_key"]].isin(right_keys)]
    for _, r in miss.iterrows():
        out.append(Detected(rule["id"], str(r.get("source_system", "multi")), rule["entity_type"], str(r[rule["left_key"]]),
                            f"present in {rule['right']}", "absent", None, rule["severity"],
                            _klass(r.get(rule.get("time_col")), as_of, rule.get("grace_hours", 0)),
                            {"left": rule["left"], "right": rule["right"], "left_status": r.get("status")}))
    return out, len(left)


def duplicate_key(rule, tables, as_of, defaults) -> RuleResult:
    df = tables[rule["table"]]
    key = rule["key"]
    vc = df.groupby(key).size()
    out = [Detected(rule["id"], _src(df), rule["entity_type"], str(k), "1 record", f"{n} records", float(n - 1),
                    rule["severity"], "CONFIRMED", {"table": rule["table"], "occurrences": int(n)})
           for k, n in vc[vc > 1].items()]
    return out, int(vc.shape[0])


def amount_mismatch(rule, tables, as_of, defaults) -> RuleResult:
    tol = rule.get("tolerance_abs", defaults["tolerance_abs"])
    key, lc, rc = rule["key"], rule["left_col"], rule["right_col"]
    left = _filter(tables[rule["left"]], rule.get("where"))
    right = _prep(tables[rule["right"]], rule.get("right_dedupe"), key, rc, rule.get("right_agg"))
    j = left.merge(right[[key, rc]].rename(columns={rc: "_r"}), on=key, how="inner")
    j["_diff"] = (j["_r"] - j[lc]).round(defaults["amount_precision"])
    bad = j[j["_diff"].abs() > tol]
    out = [Detected(rule["id"], str(r.get("source_system", "multi")), rule["entity_type"], str(r[key]), _fmt(float(r[lc])),
                    _fmt(float(r["_r"])), float(r["_diff"]), rule["severity"],
                    _klass(r.get(rule.get("time_col")), as_of, rule.get("grace_hours", 0)),
                    {"left": rule["left"], "right": rule["right"], "tolerance": tol})
           for _, r in bad.iterrows()]
    return out, len(j)


def field_mismatch(rule, tables, as_of, defaults) -> RuleResult:
    key, lc, rc = rule["key"], rule["left_col"], rule["right_col"]
    left = tables[rule["left"]]
    right = tables[rule["right"]].drop_duplicates(subset=[key])[[key, rc]].rename(columns={rc: "_r"})
    j = left.merge(right, on=key, how="inner")
    bad = j[j[lc] != j["_r"]]
    out = [Detected(rule["id"], str(r.get("source_system", "multi")), rule["entity_type"], str(r[key]), str(r["_r"]), str(r[lc]),
                    None, rule["severity"], "CONFIRMED", {"field": lc}) for _, r in bad.iterrows()]
    return out, len(j)


def sum_exceeds(rule, tables, as_of, defaults) -> RuleResult:
    tol = rule.get("tolerance_abs", defaults["tolerance_abs"])
    key = rule["key"]
    l = _prep(tables[rule["left"]], rule.get("left_dedupe"), key, rule["left_col"], "sum").rename(columns={rule["left_col"]: "_l"})
    r = _prep(tables[rule["right"]], rule.get("right_dedupe"), key, rule["right_col"], "sum").rename(columns={rule["right_col"]: "_r"})
    j = l.merge(r, on=key, how="inner")
    bad = j[j["_l"] > j["_r"] + tol]
    out = [Detected(rule["id"], "multi", rule["entity_type"], str(x[key]), _fmt(float(x["_r"])), _fmt(float(x["_l"])),
                    round(float(x["_l"] - x["_r"]), 2), rule["severity"], "CONFIRMED",
                    {"meaning": f"{rule['left']} total exceeds {rule['right']} total"}) for _, x in bad.iterrows()]
    return out, len(j)


def settlement_formula(rule, tables, as_of, defaults, formulas=None) -> RuleResult:
    tol = rule.get("tolerance_abs", defaults["tolerance_abs"])
    df = tables[rule["table"]].copy()
    formulas = formulas or {}
    out = []
    for _, r in df.iterrows():
        f = formulas.get(r.get("channel"), formulas["default"])
        expected = round(sum(coef * float(r[col]) for col, coef in f.items()), defaults["amount_precision"])
        actual = float(r[rule["actual_col"]])
        if abs(actual - expected) > tol:
            out.append(Detected(rule["id"], str(r.get("source_system", "multi")), rule["entity_type"], str(r[rule["key"]]),
                                _fmt(expected), _fmt(actual), round(actual - expected, 2), rule["severity"], "CONFIRMED",
                                {"channel": r.get("channel"), "formula": f}))
    return out, len(df)


RULE_TYPES: dict[str, Callable] = {
    "missing_in_target": missing_in_target, "duplicate_key": duplicate_key, "amount_mismatch": amount_mismatch,
    "field_mismatch": field_mismatch, "sum_exceeds": sum_exceeds, "settlement_formula": settlement_formula,
}


# --------------------------------------------------------------------------------------- runner
def run_reconciliation(
    reader: WarehouseReader,
    engine: Engine,
    rules_cfg: dict,
    *,
    as_of: datetime | None = None,
    rule_ids: list[str] | None = None,
    alerts: AlertManager | None = None,
) -> ReconciliationSummary:
    as_of = as_of or utcnow()
    defaults = rules_cfg["defaults"]
    run_id = str(uuid.uuid4())
    started = utcnow()
    with engine.begin() as c:
        c.execute(insert(db.reconciliation_runs).values(reconciliation_run_id=run_id, started_at=started, as_of=as_of))

    tables = {name: reader.read_latest(fq) for name, fq in rules_cfg["tables"].items()}
    rules = [r for r in rules_cfg["rules"] if not rule_ids or r["id"] in rule_ids]
    summ = ReconciliationSummary(run_id=run_id, as_of=as_of)
    detected: list[Detected] = []
    for rule in rules:
        fn = RULE_TYPES[rule["type"]]
        if rule["type"] == "settlement_formula":
            found, checked = fn(rule, tables, as_of, defaults, rules_cfg["settlement_formulas"])
        else:
            found, checked = fn(rule, tables, as_of, defaults)
        n_conf = sum(1 for d in found if d.exception_class == "CONFIRMED")
        summ.per_rule[rule["id"]] = {"checked": checked, "confirmed": n_conf, "timing": len(found) - n_conf}
        summ.checked += checked
        summ.matched += max(checked - len(found), 0)
        detected.extend(found)

    store = ExceptionStore(engine)
    summ.store_stats = store.sync(run_id, [r["id"] for r in rules], detected, as_of)
    summ.confirmed = sum(1 for d in detected if d.exception_class == "CONFIRMED")
    summ.timing = len(detected) - summ.confirmed
    summ.confirmed_difference = round(sum(abs(d.difference or 0) for d in detected if d.exception_class == "CONFIRMED"), 2)
    with engine.begin() as c:
        c.execute(update(db.reconciliation_runs).where(db.reconciliation_runs.c.reconciliation_run_id == run_id).values(
            completed_at=utcnow(), records_checked=summ.checked, records_matched=summ.matched,
            confirmed_exceptions=summ.confirmed, timing_exceptions=summ.timing,
            confirmed_difference_amount=summ.confirmed_difference))

    if alerts:
        _alert(alerts, store, rules_cfg["alerting"], summ)
    return summ


def _alert(alerts: AlertManager, store: ExceptionStore, cfg: dict, summ: ReconciliationSummary) -> None:
    active = store.exceptions_df(status=ACTIVE, exception_class="CONFIRMED")
    base = dict(category="RECONCILIATION_DISCREPANCY", pipeline="reconcile_financial_data", source="reconciliation",
                dataset="reconciliation_exceptions", run_id=summ.run_id)
    if len(active) >= cfg["min_confirmed_exceptions"]:
        total = float(active["difference_amount"].abs().fillna(0).sum())
        sev = "CRITICAL" if total > cfg["critical_if_difference_above"] or (active["severity"] == "CRITICAL").any() else "WARNING"
        by_rule = active.groupby("rule_id").size().sort_values(ascending=False)
        alerts.raise_alert(Alert(severity=sev, issue=f"{len(active)} confirmed reconciliation exceptions are unresolved",
                                 details={"Unresolved difference": f"{total:.2f}",
                                          "By rule": "\n".join(f"{k}: {v}" for k, v in by_rule.head(8).items())},
                                 action="Review reconciliation_exceptions (status OPEN) and investigate per rule.", **base))
    else:
        alerts.resolve(Alert(severity="WARNING", issue="", **base))
