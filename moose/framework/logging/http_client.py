"""
HTTP client helpers with Moose tracing propagation.

These helpers:
- create an egress span for outbound HTTP calls
- inject X-Moose-Request-Id (trace id) + X-Moose-Parent-Span-Id (egress span id)

They are intentionally lightweight and can wrap existing httpx/requests usage.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore

from moose.framework.logging.tracing import ensure_trace, span as trace_span
from moose.framework.logging import get_project_id


def _base_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if isinstance(headers, dict):
        for k, v in headers.items():
            if k is None:
                continue
            out[str(k)] = "" if v is None else str(v)
    return out


async def traced_httpx_request(
    client: Any,
    *,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    if httpx is None:
        raise ImportError("httpx is required for traced_httpx_request")

    m = str(method or "GET").upper()
    u = str(url or "")
    ctx = ensure_trace(project_id=get_project_id())

    with trace_span(
        kind="egress.http",
        name=f"{m} {u}",
        attrs={"http.method": m, "http.url": u},
        request_id=ctx.request_id,
        project_id=ctx.project_id,
        agent_name=ctx.agent_name,
    ) as sp:
        h = _base_headers(headers)
        h["X-Moose-Request-Id"] = ctx.request_id
        h["X-Moose-Parent-Span-Id"] = sp.span_id
        if ctx.agent_name:
            h["X-Moose-Origin-Agent"] = ctx.agent_name

        resp = await client.request(m, u, headers=h, **kwargs)
        try:
            sp.attrs["http.status_code"] = int(getattr(resp, "status_code", 0) or 0)
        except Exception:
            pass
        return resp


async def traced_httpx_post(client: Any, url: str, *, headers: Optional[Dict[str, str]] = None, **kwargs):
    return await traced_httpx_request(client, method="POST", url=url, headers=headers, **kwargs)


def traced_requests_request(
    *,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    if requests is None:
        raise ImportError("requests is required for traced_requests_request")

    m = str(method or "GET").upper()
    u = str(url or "")
    ctx = ensure_trace(project_id=get_project_id())

    with trace_span(
        kind="egress.http",
        name=f"{m} {u}",
        attrs={"http.method": m, "http.url": u},
        request_id=ctx.request_id,
        project_id=ctx.project_id,
        agent_name=ctx.agent_name,
    ) as sp:
        h = _base_headers(headers)
        h["X-Moose-Request-Id"] = ctx.request_id
        h["X-Moose-Parent-Span-Id"] = sp.span_id
        if ctx.agent_name:
            h["X-Moose-Origin-Agent"] = ctx.agent_name

        resp = requests.request(m, u, headers=h, **kwargs)
        try:
            sp.attrs["http.status_code"] = int(getattr(resp, "status_code", 0) or 0)
        except Exception:
            pass
        return resp


def traced_requests_post(url: str, *, headers: Optional[Dict[str, str]] = None, **kwargs):
    return traced_requests_request(method="POST", url=url, headers=headers, **kwargs)


