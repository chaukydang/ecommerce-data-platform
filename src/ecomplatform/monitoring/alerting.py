from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import requests
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from ecomplatform import db
from ecomplatform.util import redact, stable_hash, utcnow

log = logging.getLogger(__name__)

CATEGORIES = {
    "PIPELINE_FAILURE", "FRESHNESS_VIOLATION", "RECONCILIATION_DISCREPANCY",
    "DATA_QUALITY_FAILURE", "REPEATED_RETRIES", "SCHEMA_DRIFT",
}
SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


@dataclass
class Alert:
    category: str
    severity: str
    pipeline: str
    source: str
    issue: str
    dataset: str = ""
    run_id: str = ""
    details: dict[str, str] = field(default_factory=dict)
    action: str = ""

    @property
    def key(self) -> str:
        """Same incident -> same key (drives grouping + cooldown)."""
        return stable_hash(self.category, self.pipeline, self.source, self.dataset)


def format_message(alert: Alert, *, recovered: bool = False, suppressed: int = 0) -> str:
    head = "[DATA PIPELINE RECOVERED]" if recovered else "[DATA PIPELINE ALERT]"
    lines = [head, "", f"Severity: {'RESOLVED' if recovered else alert.severity}",
             f"Category: {alert.category}", f"Pipeline: {alert.pipeline}", f"Source: {alert.source}",
             f"Issue: {alert.issue}"]
    for k, v in alert.details.items():
        lines += ["", f"{k}:", str(v)]
    if alert.dataset:
        lines += ["", "Affected Dataset:", alert.dataset]
    if alert.action and not recovered:
        lines += ["", "Action:", alert.action]
    if suppressed and not recovered:
        lines += ["", f"(+{suppressed} repeat notifications grouped since last message)"]
    if alert.run_id:
        lines += ["", f"Run ID: {alert.run_id}"]
    return redact("\n".join(lines))


class Notifier(Protocol):
    def send(self, text: str) -> bool: ...


class LarkNotifier:
    """Lark / Feishu custom-bot webhook. URL (and optional signing secret) come from env vars and are
    never logged. With no URL configured it logs to stdout (dry run) instead of failing the pipeline."""

    def __init__(self, webhook_url: str | None = None, secret: str | None = None, timeout: float = 10.0,
                 session: requests.Session | None = None):
        self.webhook_url = webhook_url if webhook_url is not None else os.getenv("LARK_WEBHOOK_URL")
        self.secret = secret if secret is not None else os.getenv("LARK_WEBHOOK_SECRET")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _sign(self, ts: str) -> str:
        key = f"{ts}\n{self.secret}".encode()
        return base64.b64encode(hmac.new(key, b"", digestmod=hashlib.sha256).digest()).decode()

    def send(self, text: str) -> bool:
        if not self.webhook_url:
            log.info("LARK_WEBHOOK_URL not set - alert (dry run):\n%s", text)
            return False
        payload: dict = {"msg_type": "text", "content": {"text": text}}
        if self.secret:
            ts = str(int(time.time()))
            payload.update(timestamp=ts, sign=self._sign(ts))
        try:
            r = self.session.post(self.webhook_url, json=payload, timeout=self.timeout)
            ok = r.status_code == 200 and r.json().get("code", 0) == 0
            if not ok:
                log.warning("Lark webhook rejected the message (HTTP %s)", r.status_code)
            return ok
        except Exception as exc:   # alerting must never take the pipeline down
            log.warning("Lark webhook call failed: %s", redact(str(exc), [self.webhook_url or "", self.secret or ""]))
            return False


class AlertManager:
    """Alert grouping + cooldown + recovery notices, persisted in ``alert_state``.

    * first occurrence of an incident            -> send
    * same incident again inside the cooldown    -> suppressed (counted, not sent)
    * same incident again after the cooldown     -> re-sent (with the number of grouped repeats)
    * severity escalates (e.g. WARNING->CRITICAL)-> send immediately
    * ``resolve`` on an open incident            -> one "RECOVERED" message, incident closed
    """

    def __init__(self, engine: Engine, notifier: Notifier, cooldown_minutes: int = 60,
                 min_severity: str = "WARNING"):
        self.engine, self.notifier = engine, notifier
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.min_severity = min_severity

    def raise_alert(self, alert: Alert, now: datetime | None = None) -> bool:
        if SEVERITY_ORDER[alert.severity] < SEVERITY_ORDER[self.min_severity]:
            return False
        now = now or utcnow()
        t = db.alert_state
        with self.engine.begin() as c:
            row = c.execute(select(t).where(t.c.alert_key == alert.key)).mappings().first()
            if row is None:
                send, suppressed = True, 0
                c.execute(insert(t).values(alert_key=alert.key, category=alert.category, pipeline=alert.pipeline,
                                           severity=alert.severity, status="OPEN", first_seen_at=now,
                                           sent_count=0, suppressed_count=0, last_run_id=alert.run_id))
            else:
                suppressed = row["suppressed_count"] or 0
                reopened = row["status"] == "RESOLVED"
                escalated = SEVERITY_ORDER[alert.severity] > SEVERITY_ORDER.get(row["severity"], 0)
                cooled = row["last_sent_at"] is None or now - row["last_sent_at"] >= self.cooldown
                send = reopened or escalated or cooled
                if reopened:
                    suppressed = 0
            if send:
                self.notifier.send(format_message(alert, suppressed=suppressed))
                fresh = row is None or row["status"] == "RESOLVED"
                values = dict(status="OPEN", severity=alert.severity, last_sent_at=now, suppressed_count=0,
                              sent_count=1 if fresh else (row["sent_count"] or 0) + 1,
                              resolved_at=None, last_run_id=alert.run_id)
                if fresh:
                    values["first_seen_at"] = now
                c.execute(update(t).where(t.c.alert_key == alert.key).values(**values))
                return True
            c.execute(update(t).where(t.c.alert_key == alert.key).values(
                suppressed_count=suppressed + 1, last_run_id=alert.run_id))
            return False

    def resolve(self, alert: Alert, now: datetime | None = None) -> bool:
        """Close an open incident and send one recovery notice. No-op if nothing was open."""
        now = now or utcnow()
        t = db.alert_state
        with self.engine.begin() as c:
            row = c.execute(select(t).where(t.c.alert_key == alert.key, t.c.status == "OPEN")).mappings().first()
            if row is None:
                return False
            c.execute(update(t).where(t.c.alert_key == alert.key).values(status="RESOLVED", resolved_at=now))
        self.notifier.send(format_message(alert, recovered=True))
        return True
