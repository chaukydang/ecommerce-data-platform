from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# schema: {"fields": {name: {"type": "string|integer|number|timestamp", "required": bool}}, "key": [...]}
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "timestamp": lambda v: isinstance(v, (str, datetime)),
}


@dataclass
class ValidationResult:
    valid: list[dict] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)
    unexpected_fields: set[str] = field(default_factory=set)   # schema drift (new fields in source)
    missing_fields: set[str] = field(default_factory=set)      # schema drift (fields vanished)

    @property
    def drift(self) -> bool:
        return bool(self.unexpected_fields or self.missing_fields)


def validate_records(records: list[dict], schema: dict[str, Any]) -> ValidationResult:
    fields: dict[str, dict] = schema["fields"]
    res = ValidationResult()
    seen: set[str] = set()
    for rec in records:
        seen.update(rec.keys())
        reason = None
        for name, spec in fields.items():
            val = rec.get(name)
            if val is None:
                if spec.get("required", False):
                    reason = f"missing required field '{name}'"
                    break
                continue
            if not _TYPE_CHECKS[spec["type"]](val):
                reason = f"field '{name}' expected {spec['type']}, got {type(val).__name__}"
                break
        if reason:
            res.rejected.append((rec, reason))
        else:
            res.valid.append(rec)
    res.unexpected_fields = {k for k in seen if k not in fields and not k.startswith("_")}
    res.missing_fields = {k for k, s in fields.items() if s.get("required") and records and k not in seen}
    return res
