from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.util import stable_hash

ACTIVE = ("OPEN", "INVESTIGATING")
VALID_STATUS = ("OPEN", "INVESTIGATING", "RESOLVED", "ACCEPTED_EXCEPTION")


@dataclass
class Detected:
    rule_id: str
    source_system: str
    entity_type: str
    entity_id: str
    expected: str | None
    actual: str | None
    difference: float | None
    severity: str
    exception_class: str       # TIMING | CONFIRMED
    details: dict

    @property
    def exception_id(self) -> str:
        return stable_hash(self.rule_id, self.entity_id, length=32)


class ExceptionStore:
    """Persists reconciliation exceptions with a lifecycle and full history.

    Idempotent: exception_id = hash(rule_id, entity_id), so re-running reconciliation updates the
    existing row instead of creating a duplicate. Resolved items are kept (audit trail) and are
    re-opened if the problem reappears. ACCEPTED_EXCEPTION is a human decision and is never overridden.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def sync(self, run_id: str, executed_rule_ids: list[str], detected: list[Detected], at: datetime) -> dict:
        t, h = db.reconciliation_exceptions, db.reconciliation_exception_history
        stats = {"new": 0, "updated": 0, "reopened": 0, "auto_resolved": 0, "class_changed": 0}
        by_id = {d.exception_id: d for d in detected}

        def hist(c, exc_id, event, frm=None, to=None, note=None):
            c.execute(insert(h).values(exception_id=exc_id, reconciliation_run_id=run_id, event=event,
                                       from_value=frm, to_value=to, note=note, at=at))

        with self.engine.begin() as c:
            existing = {r["exception_id"]: r for r in
                        c.execute(select(t).where(t.c.rule_id.in_(executed_rule_ids))).mappings()} if executed_rule_ids else {}
            for eid, d in by_id.items():
                vals = dict(reconciliation_run_id=run_id, expected_value=d.expected, actual_value=d.actual,
                            difference_amount=d.difference, severity=d.severity, exception_class=d.exception_class,
                            last_seen_at=at, details=json.dumps(d.details, default=str))
                row = existing.get(eid)
                if row is None:
                    c.execute(insert(t).values(exception_id=eid, first_run_id=run_id, rule_id=d.rule_id,
                                               source_system=d.source_system, entity_type=d.entity_type,
                                               entity_id=d.entity_id, exception_status="OPEN", detected_at=at,
                                               resolved_at=None, **vals))
                    hist(c, eid, "DETECTED", to=d.exception_class)
                    stats["new"] += 1
                    continue
                extra: dict = {}
                if row["exception_status"] == "RESOLVED":
                    extra.update(exception_status="OPEN", resolved_at=None, detected_at=at)
                    hist(c, eid, "REOPENED", "RESOLVED", "OPEN")
                    stats["reopened"] += 1
                elif row["exception_class"] != d.exception_class:
                    hist(c, eid, "CLASS_CHANGED", row["exception_class"], d.exception_class)
                    stats["class_changed"] += 1
                elif (row["actual_value"], row["expected_value"]) != (d.actual, d.expected):
                    hist(c, eid, "UPDATED", f"{row['expected_value']}/{row['actual_value']}", f"{d.expected}/{d.actual}")
                    stats["updated"] += 1
                c.execute(update(t).where(t.c.exception_id == eid).values(**vals, **extra))
            # no longer detected -> auto-resolve (only for rules that actually ran)
            for eid, row in existing.items():
                if eid not in by_id and row["exception_status"] in ACTIVE:
                    c.execute(update(t).where(t.c.exception_id == eid).values(
                        exception_status="RESOLVED", resolved_at=at, reconciliation_run_id=run_id))
                    hist(c, eid, "AUTO_RESOLVED", row["exception_status"], "RESOLVED", "condition no longer detected")
                    stats["auto_resolved"] += 1
        return stats

    def set_status(self, exception_id: str, status: str, note: str | None = None, at: datetime | None = None) -> None:
        """Manual workflow transitions (investigate / accept / resolve) with audit history."""
        from ecomplatform.util import utcnow
        if status not in VALID_STATUS:
            raise ValueError(f"status must be one of {VALID_STATUS}")
        at = at or utcnow()
        t, h = db.reconciliation_exceptions, db.reconciliation_exception_history
        with self.engine.begin() as c:
            row = c.execute(select(t).where(t.c.exception_id == exception_id)).mappings().first()
            if row is None:
                raise KeyError(exception_id)
            c.execute(update(t).where(t.c.exception_id == exception_id).values(
                exception_status=status, resolved_at=at if status == "RESOLVED" else None))
            c.execute(insert(h).values(exception_id=exception_id, reconciliation_run_id=None, event="STATUS_CHANGED",
                                       from_value=row["exception_status"], to_value=status, note=note, at=at))

    # ---- reporting -----------------------------------------------------------------------
    def exceptions_df(self, status: tuple[str, ...] | None = None, exception_class: str | None = None) -> pd.DataFrame:
        t = db.reconciliation_exceptions
        q = select(t)
        if status:
            q = q.where(t.c.exception_status.in_(status))
        if exception_class:
            q = q.where(t.c.exception_class == exception_class)
        with self.engine.connect() as c:
            return pd.read_sql(q, c)

    def summary(self) -> pd.DataFrame:
        df = self.exceptions_df()
        if df.empty:
            return df
        df["abs_diff"] = df["difference_amount"].abs().fillna(0)
        return (df.groupby(["rule_id", "exception_class", "exception_status"])
                  .agg(exceptions=("exception_id", "count"), total_abs_difference=("abs_diff", "sum"))
                  .reset_index())

    def unresolved_confirmed_amount(self) -> float:
        df = self.exceptions_df(status=ACTIVE, exception_class="CONFIRMED")
        return float(df["difference_amount"].abs().fillna(0).sum()) if not df.empty else 0.0
