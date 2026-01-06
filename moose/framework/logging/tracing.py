"""
Lightweight distributed tracing primitives for Moose.

Design goals:
- A single trace-wide correlation id (we use the existing term `request_id`)
- Spans for causal linkage across LLM/tool/http/workflow events
- Context propagation via `contextvars` for async + in-process sub-agent calls

This module is intentionally dependency-free (stdlib only). Storage/export is handled by
exporters (e.g., SQLite) registered at runtime.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Protocol


def _new_id() -> str:
    # UUIDv4 is good enough for now; we can switch to ULID later without changing API.
    return str(uuid.uuid4())


@dataclass(frozen=True)
class TraceContext:
    """
    Current trace context. `request_id` is the trace-wide correlation ID.

    `current_span_id` changes as code enters/exits spans.
    """

    request_id: str
    current_span_id: Optional[str] = None
    project_id: Optional[str] = None
    agent_name: Optional[str] = None


@dataclass
class Span:
    span_id: str
    request_id: str
    parent_span_id: Optional[str]
    kind: str
    name: str
    start_ts: float
    end_ts: Optional[float] = None
    status: str = "ok"  # ok | error
    error: Optional[str] = None
    attrs: Dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None
    agent_name: Optional[str] = None


class SpanExporter(Protocol):
    def on_span_start(self, span: Span) -> None: ...

    def on_span_end(self, span: Span) -> None: ...


_CURRENT: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "moose_trace_current", default=None
)

_EXPORTERS: list[SpanExporter] = []


def register_exporter(exporter: SpanExporter) -> None:
    """Register a global exporter (e.g., SQLite writer). Safe to call multiple times."""
    if exporter in _EXPORTERS:
        return
    _EXPORTERS.append(exporter)


def get_current() -> Optional[TraceContext]:
    return _CURRENT.get()


def set_current(ctx: Optional[TraceContext]) -> contextvars.Token:
    return _CURRENT.set(ctx)


def ensure_trace(
    *,
    request_id: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> TraceContext:
    """
    Ensure a TraceContext exists and return it.
    """
    cur = get_current()
    if cur is not None:
        # Best-effort enrich metadata without overwriting existing.
        if (project_id and not cur.project_id) or (agent_name and not cur.agent_name):
            enriched = TraceContext(
                request_id=cur.request_id,
                current_span_id=cur.current_span_id,
                project_id=cur.project_id or project_id,
                agent_name=cur.agent_name or agent_name,
            )
            set_current(enriched)
            return enriched
        return cur

    rid = str(request_id or "").strip() or _new_id()
    ctx = TraceContext(request_id=rid, project_id=project_id, agent_name=agent_name)
    set_current(ctx)
    return ctx


def _emit_start(span: Span) -> None:
    for ex in tuple(_EXPORTERS):
        try:
            ex.on_span_start(span)
        except Exception:
            # Never break app logic due to tracing failures.
            pass


def _emit_end(span: Span) -> None:
    for ex in tuple(_EXPORTERS):
        try:
            ex.on_span_end(span)
        except Exception:
            pass


@contextlib.contextmanager
def span(
    *,
    kind: str,
    name: str,
    attrs: Optional[Dict[str, Any]] = None,
    parent_span_id: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    request_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> Iterator[Span]:
    """
    Create a new span and set it as the current span for the duration of the context.

    Parent resolution:
    - explicit parent_span_id arg wins
    - otherwise use current trace's current_span_id
    """
    ctx = ensure_trace(request_id=request_id, project_id=project_id, agent_name=agent_name)
    parent = parent_span_id if parent_span_id is not None else ctx.current_span_id
    sid = str(span_id or "").strip() or _new_id()

    sp = Span(
        span_id=sid,
        request_id=ctx.request_id,
        parent_span_id=parent,
        kind=str(kind or "").strip(),
        name=str(name or "").strip(),
        start_ts=time.time(),
        attrs=dict(attrs or {}),
        project_id=ctx.project_id,
        agent_name=ctx.agent_name,
    )

    # Enter: set current span id
    token = set_current(
        TraceContext(
            request_id=ctx.request_id,
            current_span_id=sid,
            project_id=ctx.project_id,
            agent_name=ctx.agent_name,
        )
    )
    _emit_start(sp)
    try:
        yield sp
        sp.status = "ok"
    except Exception as e:
        sp.status = "error"
        sp.error = f"{type(e).__name__}: {e}"
        raise
    finally:
        sp.end_ts = time.time()
        _emit_end(sp)
        # Restore previous context
        try:
            _CURRENT.reset(token)
        except Exception:
            # If reset fails for any reason, clear.
            _CURRENT.set(ctx)


