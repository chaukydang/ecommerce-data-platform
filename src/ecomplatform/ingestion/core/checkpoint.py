from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.util import parse_iso, redact, to_iso, utcnow


class CheckpointStore:
    """Per (source_system, entity) watermark.

    The watermark only moves forward and only via :meth:`advance`, which the runner
    calls *after* a batch was durably written and validated.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    def get(self, source_system: str, entity: str) -> datetime | None:
        t = db.ingestion_checkpoints
        with self.engine.connect() as c:
            row = c.execute(select(t.c.watermark).where(t.c.source_system == source_system, t.c.entity == entity)).first()
        return parse_iso(row[0]) if row and row[0] else None

    def window_start(self, source_system: str, entity: str, overlap: timedelta, default: datetime) -> datetime:
        """Start of the next extraction window = watermark - overlap (late-update safety net)."""
        wm = self.get(source_system, entity)
        return (wm - overlap) if wm else default

    def advance(self, source_system: str, entity: str, watermark: datetime, batch_id: str) -> None:
        t = db.ingestion_checkpoints
        current = self.get(source_system, entity)
        if current and watermark < current:
            watermark = current           # never move backwards
        values = dict(watermark=to_iso(watermark), status="success", last_batch_id=batch_id, updated_at=utcnow())
        with self.engine.begin() as c:
            exists = c.execute(select(t.c.source_system).where(t.c.source_system == source_system, t.c.entity == entity)).first()
            if exists:
                c.execute(update(t).where(t.c.source_system == source_system, t.c.entity == entity).values(**values))
            else:
                c.execute(insert(t).values(source_system=source_system, entity=entity, **values))

    def reset(self, source_system: str, entity: str, to: datetime | None = None) -> None:
        """Operational tool for controlled backfills."""
        t = db.ingestion_checkpoints
        with self.engine.begin() as c:
            c.execute(delete(t).where(t.c.source_system == source_system, t.c.entity == entity))
        if to:
            self.advance(source_system, entity, to, "manual_reset")

    # ---- failed batch bookkeeping -------------------------------------------------
    def record_failure(self, batch_id: str, source_system: str, entity: str, window_start: datetime,
                       window_end: datetime, error_category: str, error_message: str) -> None:
        t = db.failed_batches
        row = dict(batch_id=batch_id, source_system=source_system, entity=entity,
                   window_start=to_iso(window_start), window_end=to_iso(window_end),
                   error_category=error_category, error_message=redact(error_message)[:2000],
                   failed_at=utcnow(), resolved_at=None)
        with self.engine.begin() as c:
            if c.execute(select(t.c.batch_id).where(t.c.batch_id == batch_id)).first():
                c.execute(update(t).where(t.c.batch_id == batch_id).values(**row))
            else:
                c.execute(insert(t).values(**row))

    def resolve_failures(self, source_system: str, entity: str) -> None:
        t = db.failed_batches
        with self.engine.begin() as c:
            c.execute(update(t).where(t.c.source_system == source_system, t.c.entity == entity,
                                      t.c.resolved_at.is_(None)).values(resolved_at=utcnow()))
