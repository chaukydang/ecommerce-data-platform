from __future__ import annotations

import os
from typing import Any

import requests

from ecomplatform.util import redact

from .retry_policy import AuthError, PermanentError, RetryPolicy, TransientError


class ApiClient:
    """Thin REST client: bearer auth, timeouts, retry/backoff, error classification.

    The token is read from the environment variable named by ``token_env`` and is
    never included in exception messages (all errors pass through ``redact``).
    """

    def __init__(
        self,
        base_url: str,
        token_env: str | None = None,
        timeout: float = 10.0,
        retry: RetryPolicy | None = None,
        session: requests.Session | None = None,
        token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token if token is not None else (os.getenv(token_env) if token_env else None)
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self.session = session or requests.Session()

    def _secrets(self) -> list[str]:
        return [self._token] if self._token else []

    def _once(self, path: str, params: dict[str, Any] | None) -> dict:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(redact(f"{type(exc).__name__} calling {path}: {exc}", self._secrets())) from None
        code = resp.status_code
        if code == 429:
            ra = resp.headers.get("Retry-After")
            raise TransientError(f"HTTP 429 rate limited on {path}", retry_after=float(ra) if ra else None)
        if code >= 500:
            raise TransientError(f"HTTP {code} from {path}")
        if code in (401, 403):
            raise AuthError(f"HTTP {code} authentication/authorization failed for {path}")
        if code >= 400:
            raise PermanentError(f"HTTP {code} from {path}: {redact(resp.text[:200], self._secrets())}")
        try:
            return resp.json()
        except ValueError:
            raise PermanentError(f"Invalid JSON from {path}") from None

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict:
        return self.retry.call(lambda: self._once(path, params))
