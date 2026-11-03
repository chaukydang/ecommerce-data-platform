from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    """Naive UTC 'now' (all timestamps in the platform are UTC, stored naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.strftime(_ISO_FMT)


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def stable_hash(*parts: object, length: int = 16) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:length]


def record_hash(record: dict) -> str:
    """Deterministic content hash of a business record (ignores ingestion metadata)."""
    payload = {k: v for k, v in record.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


_SECRET_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/=]+"), r"\1***"),
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"), r"\1***"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|sign)['\"]?\s*[:=]\s*['\"]?)[^'\"\s,&}]+"), r"\1***"),
    (re.compile(r"(https?://[^\s'\"]*?/(?:hook|webhook)/[^\s'\"]*)", re.I), "<redacted-webhook-url>"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|access_token|sign)=)[^&\s]+"), r"\1***"),
]


def redact(text: str | None, extra_secrets: list[str] | None = None) -> str:
    """Strip tokens / webhook URLs / known secret values from any text before logging or storing."""
    if not text:
        return ""
    out = str(text)
    for secret in extra_secrets or []:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out
