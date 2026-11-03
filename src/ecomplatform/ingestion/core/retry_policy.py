from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")


class IngestionError(Exception):
    category = "UNKNOWN"


class TransientError(IngestionError):
    """Retryable: timeouts, connection resets, 429, 5xx."""
    category = "TRANSIENT"

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(IngestionError):
    category = "AUTH"


class PermanentError(IngestionError):
    category = "PERMANENT"


class SchemaError(IngestionError):
    category = "SCHEMA"


@dataclass
class RetryPolicy:
    """Exponential backoff with jitter; honours server ``Retry-After`` (capped at ``max_delay``)."""
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: float = 0.2
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    retries_used: int = 0   # cumulative across calls; surfaced in the audit log

    def delay_for(self, attempt: int, err: TransientError | None = None) -> float:
        if err is not None and err.retry_after is not None:
            return min(err.retry_after, self.max_delay)
        d = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return d * (1 + random.uniform(-self.jitter, self.jitter)) if self.jitter else d

    def call(self, fn: Callable[[], T]) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except TransientError as err:
                if attempt >= self.max_attempts:
                    raise
                self.retries_used += 1
                self.sleep(self.delay_for(attempt, err))
