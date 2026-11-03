from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.engine import Engine

from ecomplatform.ingestion.core.checkpoint import CheckpointStore
from ecomplatform.util import utcnow

from .alerting import Alert, AlertManager
from .audit import AuditLogger


@dataclass
class FreshnessResult:
    dataset: str
    pipeline: str
    max_delay_minutes: float
    last_success_at: datetime | None
    delay_minutes: float | None
    source_watermark: datetime | None
    severity: str

    @property
    def violated(self) -> bool:
        return self.last_success_at is None or (self.delay_minutes or 0) > self.max_delay_minutes


def check_freshness(engine: Engine, cfg: dict, now: datetime | None = None) -> list[FreshnessResult]:
    now = now or utcnow()
    audit, cps = AuditLogger(engine), CheckpointStore(engine)
    out = []
    for d in cfg["datasets"]:
        last = audit.last_success(d["pipeline"])
        src, ent = _split_pipeline(d["pipeline"], d["dataset"])
        out.append(FreshnessResult(
            dataset=d["dataset"], pipeline=d["pipeline"], max_delay_minutes=d["max_delay_minutes"],
            last_success_at=last, delay_minutes=round((now - last).total_seconds() / 60, 1) if last else None,
            source_watermark=cps.get(src, ent), severity=d.get("severity", "CRITICAL")))
    return out


def _split_pipeline(pipeline: str, dataset: str) -> tuple[str, str]:
    # pipeline = ingest_<source>_<entity>; entity is the table name of the dataset
    entity = dataset.split(".")[-1]
    body = pipeline.removeprefix("ingest_")
    return body.removesuffix(f"_{entity}"), entity


def evaluate_and_alert(results: list[FreshnessResult], alerts: AlertManager, now: datetime | None = None) -> int:
    """Raise one grouped alert per violated dataset; send a recovery notice once it is fresh again."""
    now = now or utcnow()
    violations = 0
    for r in results:
        base = dict(category="FRESHNESS_VIOLATION", pipeline=r.pipeline, source=r.dataset.split(".")[0],
                    dataset=r.dataset)
        if r.violated:
            violations += 1
            last = r.last_success_at.strftime("%Y-%m-%d %H:%M UTC") if r.last_success_at else "never"
            delay = f"{r.delay_minutes:.0f} minutes (target {r.max_delay_minutes:.0f})" if r.delay_minutes is not None else "no successful load"
            alerts.raise_alert(Alert(severity=r.severity, issue="Data freshness threshold exceeded",
                                     details={"Last Successful Load": last, "Current Delay": delay},
                                     action="Investigate upstream API availability and review Airflow execution logs.",
                                     **base), now=now)
        else:
            alerts.resolve(Alert(severity=r.severity, issue="Data freshness restored", **base), now=now)
    return violations
