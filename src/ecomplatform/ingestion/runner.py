"""Orchestrates one incremental extraction for a (source, entity).

Guarantees
* The checkpoint advances only after every page was normalised, validated and durably written.
* Pages are committed as they stream in; a crash mid-way leaves committed rows in place and the
  retry re-reads the same window - the sink de-duplicates on (source_record_id, record_hash).
* Every execution (success or failure) produces exactly one ``pipeline_run_audit`` row.
* Errors are redacted before being stored or alerted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.engine import Engine

from ecomplatform.config import entity_config, load_sources
from ecomplatform.monitoring.alerting import Alert, AlertManager
from ecomplatform.monitoring.audit import FAILED, PARTIAL, SUCCESS, AuditLogger
from ecomplatform.util import redact, stable_hash, to_iso, utcnow

from .base_connector import BaseConnector
from .connectors import build_connector
from .core.batch_writer import Sink, enrich
from .core.checkpoint import CheckpointStore
from .core.retry_policy import IngestionError, RetryPolicy, SchemaError
from .core.schema_validator import validate_records

log = logging.getLogger(__name__)
REPEATED_RETRY_THRESHOLD = 3


@dataclass
class RunResult:
    run_id: str
    batch_id: str
    status: str
    extracted: int = 0
    loaded: int = 0
    rejected: int = 0
    retries: int = 0
    watermark: datetime | None = None
    error: str | None = None


def pipeline_name(source: str, entity: str) -> str:
    return f"ingest_{source}_{entity}"


def run_ingestion(
    source: str,
    entity: str,
    *,
    engine: Engine,
    sink: Sink,
    sources_cfg: dict | None = None,
    connector: BaseConnector | None = None,
    alerts: AlertManager | None = None,
    now: datetime | None = None,
    window_start: datetime | None = None,
    raise_on_failure: bool = True,
) -> RunResult:
    """Run one ingestion. ``window_start`` forces a historical backfill window (checkpoint untouched
    unless the window ends after the current watermark)."""
    sources_cfg = sources_cfg or load_sources()
    cfg = entity_config(sources_cfg, source, entity)
    schema: dict[str, Any] = cfg["schema"]
    now = now or utcnow()

    retry = RetryPolicy(max_attempts=cfg.get("max_attempts", 5), base_delay=cfg.get("base_delay_seconds", 0.5))
    connector = connector or build_connector(cfg, retry=retry)
    retry = connector.client.retry   # the policy actually used (may be injected)

    checkpoints, audit = CheckpointStore(engine), AuditLogger(engine)
    overlap = timedelta(minutes=cfg.get("overlap_minutes", 10))
    default_start = now - timedelta(days=cfg.get("initial_lookback_days", 365))
    start = window_start or checkpoints.window_start(source, entity, overlap, default_start)
    batch_id = stable_hash(source, entity, to_iso(start), to_iso(now), length=16)
    name = pipeline_name(source, entity)
    run_id = audit.start_run(name, source, entity, batch_id)
    alert_base = Alert(category="PIPELINE_FAILURE", severity="CRITICAL", pipeline=name, source=source,
                       issue="", dataset=f"{cfg['dataset']}.{cfg['table']}", run_id=run_id)

    res = RunResult(run_id=run_id, batch_id=batch_id, status=FAILED)
    retries_before = retry.retries_used
    max_updated: datetime | None = None
    drift_fields: set[str] = set()
    try:
        for page in connector.fetch_pages(start, now):
            res.extracted += len(page)
            normalized = [connector.normalize(r) for r in page]
            vr = validate_records(normalized, schema)
            drift_fields |= vr.unexpected_fields | vr.missing_fields
            if vr.missing_fields:
                raise SchemaError(f"required fields missing from source payload: {sorted(vr.missing_fields)}")
            res.rejected += len(vr.rejected)
            sink.write_rejected(cfg["dataset"], cfg["table"], batch_id, vr.rejected)
            rows = enrich(vr.valid, schema, source, batch_id, ingested_at=now)
            res.loaded += sink.write(cfg["dataset"], cfg["table"], schema, rows)
            for r in rows:
                u = r["source_updated_at"]
                if max_updated is None or u > max_updated:
                    max_updated = u
        if res.extracted and res.rejected / res.extracted > cfg.get("max_reject_ratio", 0.2):
            raise SchemaError(f"{res.rejected}/{res.extracted} records rejected (> max_reject_ratio)")
    except Exception as exc:                                 # noqa: BLE001 - we classify + re-raise below
        category = exc.category if isinstance(exc, IngestionError) else "UNEXPECTED"
        msg = redact(f"{type(exc).__name__}: {exc}")
        res.retries = retry.retries_used - retries_before
        res.error = msg
        checkpoints.record_failure(batch_id, source, entity, start, now, category, msg)
        audit.finish_run(run_id, FAILED, records_extracted=res.extracted, records_loaded=res.loaded,
                         records_rejected=res.rejected, retry_count=res.retries, error_category=category,
                         error_message=msg)
        log.error("ingestion failed: %s", msg)
        if alerts:
            a = Alert(**{**alert_base.__dict__, "issue": f"Ingestion failed ({category})",
                         "details": {"Error": msg[:500]},
                         "action": "Review Airflow task logs and upstream API availability."})
            alerts.raise_alert(a)
        if raise_on_failure:
            raise
        return res

    res.retries = retry.retries_used - retries_before
    res.watermark = max_updated
    if max_updated is not None:
        checkpoints.advance(source, entity, max_updated, batch_id)
    checkpoints.resolve_failures(source, entity)
    res.status = PARTIAL if res.rejected else SUCCESS
    audit.finish_run(run_id, res.status, records_extracted=res.extracted, records_loaded=res.loaded,
                     records_rejected=res.rejected, retry_count=res.retries,
                     error_message=(f"{res.rejected} records quarantined to {cfg['table']}__rejected" if res.rejected else None))
    if alerts:
        alerts.resolve(alert_base)
        if res.retries >= REPEATED_RETRY_THRESHOLD:
            alerts.raise_alert(Alert("REPEATED_RETRIES", "WARNING", name, source,
                                     f"{res.retries} API retries needed in one run",
                                     dataset=alert_base.dataset, run_id=run_id,
                                     action="Check upstream rate limits / latency."))
        if drift_fields:
            alerts.raise_alert(Alert("SCHEMA_DRIFT", "WARNING", name, source,
                                     "Source payload fields differ from the registered schema",
                                     dataset=alert_base.dataset, run_id=run_id,
                                     details={"Fields": ", ".join(sorted(drift_fields))},
                                     action="Update configs/sources.yaml and the staging model."))
    return res
