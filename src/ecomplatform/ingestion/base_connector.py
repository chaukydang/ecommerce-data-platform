from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Iterator

from ecomplatform.util import to_iso

from .core.api_client import ApiClient
from .core.pagination import cursor_pages, page_number_pages


class BaseConnector(ABC):
    """One connector instance = one (source_system, entity). Subclasses only implement
    ``normalize`` (source payload -> canonical flat record); paging/auth/retry live in core."""

    def __init__(self, cfg: dict[str, Any], client: ApiClient):
        self.cfg = cfg
        self.client = client
        self.source_system = cfg["source_system"]
        self.entity = cfg["entity"]

    @abstractmethod
    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        """Yield pages of raw source records modified in [since, until]."""

    @abstractmethod
    def normalize(self, raw: dict) -> dict:
        """Map one raw source record to the canonical schema defined in sources.yaml."""


class RestConnector(BaseConnector):
    """Generic REST paging implementation driven by sources.yaml."""

    #: entity -> function(raw) -> canonical record. Filled in by subclasses.
    normalizers: dict[str, Callable[[dict], dict]] = {}

    def fetch_pages(self, since: datetime, until: datetime) -> Iterator[list[dict]]:
        params = {self.cfg["incremental_param"]: to_iso(since), "until": to_iso(until),
                  "limit": self.cfg.get("page_size", 200)}
        if self.cfg.get("pagination") == "page":
            params["page_size"] = params.pop("limit")
            yield from page_number_pages(self.client, self.cfg["path"], params, items_key=self.cfg.get("items_key", "items"))
        else:
            yield from cursor_pages(self.client, self.cfg["path"], params, items_key=self.cfg.get("items_key", "data"))

    def normalize(self, raw: dict) -> dict:
        fn = self.normalizers.get(self.entity)
        if fn is None:
            return dict(raw)
        return fn(raw)
