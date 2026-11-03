"""Mock marketplace / OMS / POS / finance REST API (FastAPI) with controllable fault injection.

SIMULATED - for development, tests and demos. Run:  python -m ecomplatform.simulators.server
"""
from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from ecomplatform.util import parse_iso, utcnow

from .generator import SimDataset, generate


@dataclass
class Faults:
    """Fault injection keyed on the server-wide data-request counter."""
    rate_limit_on: set[int] = field(default_factory=set)     # these call numbers return 429 (Retry-After: 0)
    server_error_on: set[int] = field(default_factory=set)   # these return 500
    fail_from_call: int | None = None                        # every call >= N returns 500 (simulates an outage)
    latency_seconds: float = 0.0
    calls: int = 0

    def reset(self) -> None:
        self.rate_limit_on.clear(); self.server_error_on.clear()
        self.fail_from_call = None; self.latency_seconds = 0.0; self.calls = 0


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode().split(":")[1])
    except Exception:
        raise HTTPException(400, "invalid cursor")


def create_app(dataset: SimDataset, token: str = "sim-token", faults: Faults | None = None) -> FastAPI:
    app = FastAPI(title="E-commerce simulators (SIMULATED DATA)")
    app.state.dataset = dataset
    app.state.faults = faults or Faults()
    lock = threading.Lock()

    def auth(authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {token}":
            raise HTTPException(401, "invalid or missing bearer token")

    def tick() -> JSONResponse | None:
        f: Faults = app.state.faults
        with lock:
            f.calls += 1
            n = f.calls
        if f.latency_seconds:
            time.sleep(f.latency_seconds)
        if n in f.rate_limit_on:
            return JSONResponse({"error": "rate limited"}, status_code=429, headers={"Retry-After": "0"})
        if n in f.server_error_on or (f.fail_from_call is not None and n >= f.fail_from_call):
            return JSONResponse({"error": "upstream failure"}, status_code=500)
        return None

    def select(rows: list[dict], id_field: str, updated_since: str | None, until: str | None) -> list[dict]:
        lo = parse_iso(updated_since) if updated_since else None
        hi = parse_iso(until) if until else None
        out = [r for r in rows
               if (lo is None or parse_iso(r["updated_at"]) >= lo) and (hi is None or parse_iso(r["updated_at"]) <= hi)]
        return sorted(out, key=lambda r: (r["updated_at"], r[id_field]))

    def cursor_endpoint(path: str, attr: str, id_field: str):
        @app.get(path, dependencies=[Depends(auth)], name=attr)
        def _list(updated_since: str | None = None, until: str | None = None, cursor: str | None = None,
                  limit: int = Query(200, ge=1, le=1000)):
            if (err := tick()) is not None:
                return err
            rows = select(getattr(app.state.dataset, attr), id_field, updated_since, until)
            off = _decode(cursor)
            page = rows[off:off + limit]
            nxt = _encode(off + limit) if off + limit < len(rows) else None
            return {"data": page, "next_cursor": nxt}

    cursor_endpoint("/v1/marketplace/orders", "marketplace_orders", "order_id")
    cursor_endpoint("/v1/pos/transactions", "pos_transactions", "txn_id")
    cursor_endpoint("/v1/finance/payments", "payments", "payment_id")
    cursor_endpoint("/v1/finance/refunds", "refunds", "refund_id")
    cursor_endpoint("/v1/finance/settlements", "settlements", "settlement_id")

    @app.get("/v1/oms/orders", dependencies=[Depends(auth)])
    def oms_orders(updated_since: str | None = None, until: str | None = None, page: int = Query(1, ge=1),
                   page_size: int = Query(200, ge=1, le=1000)):
        if (err := tick()) is not None:
            return err
        rows = select(app.state.dataset.oms_orders, "oms_order_id", updated_since, until)
        total_pages = max(1, -(-len(rows) // page_size))
        s = (page - 1) * page_size
        return {"items": rows[s:s + page_size], "page": page, "total_pages": total_pages}

    @app.get("/health")
    def health():
        return {"status": "ok", "simulated": True, "now": utcnow().isoformat()}

    return app


def main() -> None:          # pragma: no cover - manual / docker entrypoint
    import uvicorn
    ds = generate(n_orders=int(os.getenv("SIM_ORDERS", "400")), seed=int(os.getenv("SIM_SEED", "7")), as_of=utcnow())
    uvicorn.run(create_app(ds, token=os.getenv("SIM_API_TOKEN", "sim-token")),
                host=os.getenv("SIM_HOST", "0.0.0.0"), port=int(os.getenv("SIM_PORT", "8000")))


if __name__ == "__main__":
    main()
