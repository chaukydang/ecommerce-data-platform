from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.config import load_sources
from ecomplatform.ingestion.core.batch_writer import META_COLUMNS
from ecomplatform.reconciliation.readers import WarehouseReader
from ecomplatform.util import utcnow

from .alerting import Alert, AlertManager


@dataclass
class CheckResult:
    dataset: str
    check: str
    severity: str
    passed: bool
    observed: dict

    def as_row(self, run_id: str | None, at: datetime) -> dict:
        return dict(run_id=run_id, dataset=self.dataset, check_name=self.check, severity=self.severity,
                    passed=int(self.passed), observed=json.dumps(self.observed, default=str), checked_at=at)


# ---- pure check functions (unit-testable on DataFrames) ---------------------------------------
def check_pk_unique(df: pd.DataFrame, cols: list[str]) -> tuple[bool, dict]:
    dup = int(df.duplicated(subset=cols, keep=False).sum())
    return dup == 0, {"duplicate_rows": dup}


def check_required(df: pd.DataFrame, cols: list[str]) -> tuple[bool, dict]:
    nulls = {c: int(df[c].isna().sum()) for c in cols if c in df.columns and df[c].isna().any()}
    missing = [c for c in cols if c not in df.columns]
    return not nulls and not missing, {"null_counts": nulls, "missing_columns": missing}


def check_accepted_values(df: pd.DataFrame, col: str, allowed: list) -> tuple[bool, dict]:
    bad = df[~df[col].isin(allowed) & df[col].notna()][col]
    return bad.empty, {"unexpected": sorted(map(str, bad.unique()))[:10], "rows": int(len(bad))}


def check_non_negative(df: pd.DataFrame, col: str) -> tuple[bool, dict]:
    n = int((df[col] < 0).sum())
    return n == 0, {"negative_rows": n}


def check_relationship(child: pd.DataFrame, col: str, parent: pd.DataFrame, parent_col: str) -> tuple[bool, dict]:
    orphans = child[~child[col].isin(set(parent[parent_col]))][col]
    return orphans.empty, {"orphan_rows": int(len(orphans)), "examples": list(map(str, orphans.unique()[:5]))}


def check_volume(rows: int, previous: int | None, max_change_pct: float) -> tuple[bool, dict]:
    if not previous:
        return rows > 0, {"rows": rows, "previous": previous}
    pct = abs(rows - previous) / previous * 100
    return pct <= max_change_pct, {"rows": rows, "previous": previous, "change_pct": round(pct, 1)}


def check_schema_drift(df: pd.DataFrame, schema: dict) -> tuple[bool, dict]:
    expected = set(schema["fields"]) | {n for n, _ in META_COLUMNS}
    actual = set(df.columns)
    return expected == actual, {"missing": sorted(expected - actual), "unexpected": sorted(actual - expected)}


# ---- orchestration -----------------------------------------------------------------------------
def _previous_rows(engine: Engine, dataset: str) -> int | None:
    t = db.data_quality_results
    with engine.connect() as c:
        row = c.execute(select(t.c.observed).where(t.c.dataset == dataset, t.c.check_name == "volume_change")
                        .order_by(t.c.id.desc()).limit(1)).first()
    return json.loads(row[0]).get("rows") if row else None


def run_quality_checks(reader: WarehouseReader, engine: Engine, cfg: dict, *, sources_cfg: dict | None = None,
                       run_id: str | None = None, alerts: AlertManager | None = None) -> list[CheckResult]:
    sources_cfg = sources_cfg or load_sources()
    at = utcnow()
    cache: dict[str, pd.DataFrame] = {}

    def table(name: str) -> pd.DataFrame:
        if name not in cache:
            cache[name] = reader.read_latest(name)
        return cache[name]

    results: list[CheckResult] = []
    for d in cfg["datasets"]:
        ds, sev = d["dataset"], d.get("severity", "CRITICAL")
        try:
            df = table(ds)
        except Exception as exc:    # table missing = a failed check, not a crashed monitor
            results.append(CheckResult(ds, "table_exists", sev, False, {"error": str(exc)[:200]}))
            continue
        def add(name, res, ds=ds, sev=sev):
            results.append(CheckResult(ds, name, sev, res[0], res[1]))

        if d.get("pk_unique"):
            add("pk_unique", check_pk_unique(df, d["pk_unique"]))
        if d.get("required"):
            add("required_fields", check_required(df, d["required"]))
        for col, allowed in (d.get("accepted_values") or {}).items():
            add(f"accepted_values:{col}", check_accepted_values(df, col, allowed))
        for col in d.get("non_negative") or []:
            add(f"non_negative:{col}", check_non_negative(df, col))
        for rel in d.get("relationships") or []:
            add(f"relationship:{rel['column']}->{rel['to']}", check_relationship(df, rel["column"], table(rel["to"]), rel["to_column"]))
        add("volume_change", check_volume(len(df), _previous_rows(engine, ds), cfg.get("volume_change_pct", 100)))
        if d.get("source"):
            schema = sources_cfg["sources"][d["source"]]["entities"][d["entity"]]["schema"]
            add("schema_drift", check_schema_drift(df, schema))

    with engine.begin() as c:
        if results:
            c.execute(insert(db.data_quality_results), [r.as_row(run_id, at) for r in results])
    if alerts:
        _alert(alerts, results, run_id or "")
    return results


def _alert(alerts: AlertManager, results: list[CheckResult], run_id: str) -> None:
    datasets = sorted({r.dataset for r in results})
    for ds in datasets:
        failed = [r for r in results if r.dataset == ds and not r.passed]
        base = dict(category="DATA_QUALITY_FAILURE", pipeline="monitor_data_quality", source=ds.split(".")[0],
                    dataset=ds, run_id=run_id)
        if failed:
            sev = "CRITICAL" if any(r.severity == "CRITICAL" for r in failed) else "WARNING"
            alerts.raise_alert(Alert(severity=sev, issue=f"{len(failed)} data quality check(s) failed",
                                     details={"Failed checks": "\n".join(f"{r.check}: {json.dumps(r.observed, default=str)[:160]}" for r in failed[:6])},
                                     action="Inspect the offending rows in the raw table; fix the source or quarantine.", **base))
        else:
            alerts.resolve(Alert(severity="WARNING", issue="Data quality restored", **base))
