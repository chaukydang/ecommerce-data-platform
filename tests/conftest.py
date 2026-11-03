from __future__ import annotations

import os
import socket
import threading
import time
from datetime import datetime

import pytest
import requests
import uvicorn

from ecomplatform import db
from ecomplatform.ingestion.core.retry_policy import RetryPolicy
from ecomplatform.simulators.generator import generate
from ecomplatform.simulators.server import Faults, create_app

AS_OF = datetime(2026, 11, 3, 9, 0, 0)
TOKEN = "test-sim-token-123456"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SimServer:
    def __init__(self, dataset):
        self.dataset = dataset
        self.app = create_app(dataset, token=TOKEN, faults=Faults())
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            try:
                if requests.get(f"{self.url}/health", timeout=0.5).status_code == 200:
                    return self
            except requests.RequestException:
                time.sleep(0.05)
        raise RuntimeError("simulator did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def faults(self) -> Faults:
        return self.app.state.faults


@pytest.fixture()
def sim(monkeypatch):
    ds = generate(n_orders=300, seed=7, as_of=AS_OF)
    s = SimServer(ds).start()
    for env in ("MARKETPLACE", "OMS", "POS", "FINANCE"):
        monkeypatch.setenv(f"{env}_API_URL", s.url)
        monkeypatch.setenv(f"{env}_API_TOKEN", TOKEN)
    yield s
    s.stop()


@pytest.fixture()
def engine(tmp_path):
    """SQLite by default; set TEST_DB_URL=postgresql+psycopg2://... to run the same suite on PostgreSQL."""
    url = os.getenv("TEST_DB_URL") or f"sqlite:///{tmp_path / 'platform.db'}"
    eng = db.get_engine(url)
    db.metadata.drop_all(eng)
    db.init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def duck_path(tmp_path):
    return str(tmp_path / "warehouse.duckdb")


@pytest.fixture()
def fast_retry():
    return RetryPolicy(max_attempts=4, base_delay=0.0, jitter=0.0, sleep=lambda s: None)


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return True


@pytest.fixture()
def notifier():
    return FakeNotifier()


class ShopifyMockServer(SimServer):
    """Same lifecycle as SimServer but serves the Shopify REST API mock."""

    def __init__(self, data):
        from ecomplatform.simulators.shopify_mock import create_shopify_mock
        self.dataset = data
        self.app = create_shopify_mock(data, faults=Faults())
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)


@pytest.fixture()
def shopify(monkeypatch):
    from ecomplatform.simulators.shopify_mock import CLIENT_ID, CLIENT_SECRET, generate_shopify
    s = ShopifyMockServer(generate_shopify(n_orders=60, seed=11, as_of=AS_OF)).start()
    monkeypatch.setenv("SHOPIFY_STORE_URL", s.url)
    monkeypatch.delenv("SHOPIFY_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("SHOPIFY_API_VERSION", "2026-01")
    yield s
    s.stop()
