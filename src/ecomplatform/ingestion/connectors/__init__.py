from __future__ import annotations

from typing import Any

from ecomplatform.ingestion.base_connector import BaseConnector
from ecomplatform.ingestion.core.api_client import ApiClient
from ecomplatform.ingestion.core.retry_policy import RetryPolicy

from .marketplace_connector import MarketplaceConnector
from .oms_connector import OmsConnector
from .pos_connector import PosConnector
from .settlement_connector import SettlementConnector
from .shopify_connector import ShopifyConnector

REGISTRY: dict[str, type[BaseConnector]] = {
    "marketplace": MarketplaceConnector,
    "oms": OmsConnector,
    "pos": PosConnector,
    "settlement": SettlementConnector,
    "shopify": ShopifyConnector,
}


def build_connector(cfg: dict[str, Any], client: ApiClient | None = None, retry: RetryPolicy | None = None) -> BaseConnector:
    import os
    cls = REGISTRY[cfg["connector"]]
    if client is None and hasattr(cls, "build_client"):       # connectors with their own auth (e.g. Shopify)
        client = cls.build_client(cfg, retry or RetryPolicy(max_attempts=cfg.get("max_attempts", 5),
                                                            base_delay=cfg.get("base_delay_seconds", 0.5)))
    if client is None:
        base_url = os.getenv(cfg.get("base_url_env", ""), cfg.get("base_url_default", "http://localhost:8000"))
        retry = retry or RetryPolicy(max_attempts=cfg.get("max_attempts", 5), base_delay=cfg.get("base_delay_seconds", 0.5))
        client = ApiClient(base_url, token_env=cfg.get("token_env"), timeout=cfg.get("timeout_seconds", 10), retry=retry)
    return cls(cfg, client)
