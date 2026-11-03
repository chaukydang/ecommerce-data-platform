from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.util import redact, utcnow

RUNNING, SUCCESS, FAILED, PARTIAL = "RUNNING", "SUCCESS", "FAILED", "PARTIAL_SUCCESS"


class AuditLogger:
    """Writes one row per pipeline execution to ``pipeline_run_audit``. Error text is always redacted."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def start_run(self, pipeline_name: str, source_system: str | None = None, entity_name: str | None = None,
                  batch_id: str | None = None) -> str:
        run_id = str(uuid.uuid4())
        with self.engine.begin() as c:
            c.execute(insert(db.pipeline_run_audit).values(
                run_id=run_id, pipeline_name=pipeline_name, source_system=source_system, entity_name=entity_name,
                batch_id=batch_id, started_at=utcnow(), status=RUNNING,
                records_extracted=0, records_loaded=0, records_rejected=0, retry_count=0))
        return run_id

    def finish_run(self, run_id: str, status: str, *, records_extracted: int = 0, records_loaded: int = 0,
                   records_rejected: int = 0, retry_count: int = 0, error_category: str | None = None,
                   error_message: str | None = None, secrets: list[str] | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(update(db.pipeline_run_audit).where(db.pipeline_run_audit.c.run_id == run_id).values(
                completed_at=utcnow(), status=status, records_extracted=records_extracted,
                records_loaded=records_loaded, records_rejected=records_rejected, retry_count=retry_count,
                error_category=error_category, error_message=redact(error_message, secrets)[:4000] if error_message else None))

    def last_success(self, pipeline_name: str) -> datetime | None:
        t = db.pipeline_run_audit
        with self.engine.connect() as c:
            row = c.execute(select(t.c.completed_at).where(
                t.c.pipeline_name == pipeline_name, t.c.status.in_([SUCCESS, PARTIAL])
            ).order_by(t.c.completed_at.desc()).limit(1)).first()
        return row[0] if row else None
