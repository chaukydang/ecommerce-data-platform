from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

from ecomplatform.util import redact

from .retry_policy import AuthError, PermanentError, RetryPolicy, TransientError


class ApiClient:
    """Thin REST client: auth header, timeouts, retry/backoff, error classification.

    * Default auth is ``Authorization: Bearer <token>``; set ``auth_header`` / ``auth_prefix`` for other schemes
      (e.g. Shopify: ``X-Shopify-Access-Token`` with an empty prefix).
    * ``token_provider`` (optional) fetches a fresh token; it is called lazily and once more after a 401 (expiring tokens).
    * Secrets never appear in exception messages (all errors pass through ``redact``).
    * ``signer(path, params, token)`` returns the final query parameters (APIs that sign every request, e.g. Shopee) and is
      applied on every attempt; ``auth_header=None`` disables the auth header for such APIs.
    * ``body_check(body)`` may raise Auth/Transient/Permanent errors for APIs that report failures inside HTTP 200 bodies.
    * Absolute URLs (pagination ``Link`` headers) are only followed on the configured host, so credentials cannot leak elsewhere.
    """

    def __init__(
        self,
        base_url: str,
        token_env: str | None = None,
        timeout: float = 10.0,
        retry: RetryPolicy | None = None,
        session: requests.Session | None = None,
        token: str | None = None,
        auth_header: str | None = "Authorization",
        auth_prefix: str = "Bearer ",
        token_provider: Callable[[], str] | None = None,
        signer: Callable[[str, dict[str, Any], str | None], dict[str, Any]] | None = None,
        body_check: Callable[[dict], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token if token is not None else (os.getenv(token_env) if token_env else None)
        self._provider = token_provider
        self._signer, self._body_check = signer, body_check
        self.auth_header, self.auth_prefix = auth_header, auth_prefix
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self.session = session or requests.Session()

    def _secrets(self) -> list[str]:
        return [self._token] if self._token else []

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            a, b = urlsplit(path), urlsplit(self.base_url)
            if (a.scheme, a.netloc) != (b.scheme, b.netloc):
                raise PermanentError("refusing to follow a URL on a different host (credentials stay on the configured host)")
            return path
        return f"{self.base_url}{path}"

    def _once(self, path: str, params: dict[str, Any] | None) -> tuple[dict, Mapping[str, str]]:
        if self._token is None and self._provider:
            self._token = self._provider()
        headers = {"Accept": "application/json"}
        if self._token and self.auth_header:
            headers[self.auth_header] = f"{self.auth_prefix}{self._token}"
        url = self._url(path)
        if self._signer:
            params = self._signer(urlsplit(url).path, dict(params or {}), self._token)
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
            body = resp.json()
        except ValueError:
            raise PermanentError(f"Invalid JSON from {path}") from None
        if self._body_check:
            self._body_check(body)
        return body, resp.headers

    def _with_refresh(self, path: str, params: dict[str, Any] | None) -> tuple[dict, Mapping[str, str]]:
        try:
            return self._once(path, params)
        except AuthError:
            if not self._provider:
                raise
            self._token = self._provider()              # token expired: fetch a new one and retry exactly once
            return self._once(path, params)

    def get_response(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict, Mapping[str, str]]:
        return self.retry.call(lambda: self._with_refresh(path, params))

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict:
        return self.get_response(path, params)[0]
