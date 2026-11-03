from __future__ import annotations

import re
from typing import Any, Iterator

from .api_client import ApiClient


def cursor_pages(
    client: ApiClient,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    items_key: str = "data",
    cursor_param: str = "cursor",
    next_cursor_key: str = "next_cursor",
    max_pages: int = 10_000,
) -> Iterator[list[dict]]:
    """Follow an opaque-cursor paginated endpoint until the cursor is exhausted."""
    params = dict(params or {})
    for _ in range(max_pages):
        body = client.get_json(path, params)
        yield body.get(items_key, [])
        nxt = body.get(next_cursor_key)
        if not nxt:
            return
        params[cursor_param] = nxt
    raise RuntimeError(f"pagination did not terminate after {max_pages} pages for {path}")


def page_number_pages(
    client: ApiClient,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    items_key: str = "items",
    page_param: str = "page",
    total_pages_key: str = "total_pages",
    start_page: int = 1,
    max_pages: int = 10_000,
) -> Iterator[list[dict]]:
    """Follow a page-number paginated endpoint."""
    params = dict(params or {})
    page = start_page
    for _ in range(max_pages):
        params[page_param] = page
        body = client.get_json(path, params)
        yield body.get(items_key, [])
        total = body.get(total_pages_key)
        if total is None or page >= int(total):
            return
        page += 1
    raise RuntimeError(f"pagination did not terminate after {max_pages} pages for {path}")


_LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.I)


def next_link(header: str | None) -> str | None:
    """URL of rel="next" in an RFC 5988 ``Link`` header (Shopify, GitHub, ...)."""
    if not header:
        return None
    m = _LINK_NEXT.search(header)
    return m.group(1) if m else None


def link_header_pages(client: ApiClient, path: str, params: dict[str, Any] | None = None, *, items_key: str,
                      max_pages: int = 10_000) -> Iterator[list[dict]]:
    """Follow ``Link: <...>; rel="next"`` pagination. Subsequent URLs already carry all query parameters."""
    body, headers = client.get_response(path, params)
    for _ in range(max_pages):
        yield body.get(items_key, [])
        nxt = next_link(headers.get("Link"))
        if not nxt:
            return
        body, headers = client.get_response(nxt, None)
    raise RuntimeError(f"pagination did not terminate after {max_pages} pages for {path}")
