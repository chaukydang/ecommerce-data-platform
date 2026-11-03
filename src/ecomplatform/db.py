"""Operational metadata store (checkpoints, audit, alerts, reconciliation exceptions).

Uses SQLAlchemy Core so the same code runs on PostgreSQL (production / docker-compose)
and SQLite (unit tests, quick local runs). Select the backend with ``PLATFORM_DB_URL``.
"""
from __future__ import annotations

import os

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

ingestion_checkpoints = Table(
    "ingestion_checkpoints", metadata,
    Column("source_system", String(64), primary_key=True),
    Column("entity", String(64), primary_key=True),
    Column("watermark", String(40)),            # ISO-8601 UTC of last committed source_updated_at
    Column("status", String(16), nullable=False, default="success"),
    Column("last_batch_id", String(64)),
    Column("updated_at", DateTime(timezone=False), nullable=False),
)

failed_batches = Table(
    "failed_batches", metadata,
    Column("batch_id", String(64), primary_key=True),
    Column("source_system", String(64), nullable=False),
    Column("entity", String(64), nullable=False),
    Column("window_start", String(40)),
    Column("window_end", String(40)),
    Column("error_category", String(32)),
    Column("error_message", Text),
    Column("failed_at", DateTime, nullable=False),
    Column("resolved_at", DateTime),
)

pipeline_run_audit = Table(
    "pipeline_run_audit", metadata,
    Column("run_id", String(36), primary_key=True),
    Column("pipeline_name", String(96), nullable=False),
    Column("source_system", String(64)),
    Column("entity_name", String(64)),
    Column("batch_id", String(64)),
    Column("started_at", DateTime, nullable=False),
    Column("completed_at", DateTime),
    Column("status", String(20), nullable=False),   # RUNNING|SUCCESS|FAILED|PARTIAL_SUCCESS
    Column("records_extracted", Integer, default=0),
    Column("records_loaded", Integer, default=0),
    Column("records_rejected", Integer, default=0),
    Column("retry_count", Integer, default=0),
    Column("error_category", String(32)),
    Column("error_message", Text),
    Index("ix_audit_pipeline_started", "pipeline_name", "started_at"),
)

data_quality_results = Table(
    "data_quality_results", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36)),
    Column("dataset", String(96), nullable=False),
    Column("check_name", String(96), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("passed", Integer, nullable=False),
    Column("observed", Text),
    Column("checked_at", DateTime, nullable=False),
)

alert_state = Table(
    "alert_state", metadata,
    Column("alert_key", String(64), primary_key=True),
    Column("category", String(48), nullable=False),
    Column("pipeline", String(96)),
    Column("severity", String(16), nullable=False),
    Column("status", String(12), nullable=False),     # OPEN|RESOLVED
    Column("first_seen_at", DateTime, nullable=False),
    Column("last_sent_at", DateTime),
    Column("sent_count", Integer, default=0),
    Column("suppressed_count", Integer, default=0),
    Column("resolved_at", DateTime),
    Column("last_run_id", String(36)),
)

reconciliation_runs = Table(
    "reconciliation_runs", metadata,
    Column("reconciliation_run_id", String(36), primary_key=True),
    Column("started_at", DateTime, nullable=False),
    Column("completed_at", DateTime),
    Column("as_of", DateTime, nullable=False),
    Column("records_checked", Integer, default=0),
    Column("records_matched", Integer, default=0),
    Column("confirmed_exceptions", Integer, default=0),
    Column("timing_exceptions", Integer, default=0),
    Column("confirmed_difference_amount", Float, default=0.0),
)

reconciliation_exceptions = Table(
    "reconciliation_exceptions", metadata,
    Column("exception_id", String(32), primary_key=True),     # deterministic: rule+source+entity
    Column("reconciliation_run_id", String(36), nullable=False),   # last run that saw it
    Column("first_run_id", String(36), nullable=False),
    Column("rule_id", String(64), nullable=False),
    Column("source_system", String(64), nullable=False),
    Column("entity_type", String(32), nullable=False),
    Column("entity_id", String(128), nullable=False),
    Column("expected_value", String(128)),
    Column("actual_value", String(128)),
    Column("difference_amount", Float),
    Column("severity", String(16), nullable=False),
    Column("exception_class", String(12), nullable=False),    # TIMING|CONFIRMED
    Column("exception_status", String(20), nullable=False),   # OPEN|INVESTIGATING|RESOLVED|ACCEPTED_EXCEPTION
    Column("detected_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),
    Column("resolved_at", DateTime),
    Column("details", Text),
    Index("ix_recon_exc_status", "exception_status", "exception_class"),
)

reconciliation_exception_history = Table(
    "reconciliation_exception_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("exception_id", String(32), nullable=False),
    Column("reconciliation_run_id", String(36)),
    Column("event", String(24), nullable=False),   # DETECTED|UPDATED|CLASS_CHANGED|REOPENED|AUTO_RESOLVED|STATUS_CHANGED
    Column("from_value", String(32)),
    Column("to_value", String(32)),
    Column("note", Text),
    Column("at", DateTime, nullable=False),
)


def get_engine(url: str | None = None) -> Engine:
    url = url or os.getenv("PLATFORM_DB_URL", "sqlite:///platform.db")
    return create_engine(url, future=True)


def init_db(engine: Engine) -> Engine:
    metadata.create_all(engine)
    return engine
