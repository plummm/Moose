# Moose Logging & Tracing

This folder contains the core **logging** and **distributed tracing** building blocks used across Moose agents.

## Overview

- **Standard logs**: human-readable log lines (and some structured JSON entries) written to:
  - `projects/<project_id>/logs/moose.log`
  - `projects/<project_id>/logs/agents/<agent_name>.log*`
- **Structured LLM logs**: emitted by `LLMLogger` as JSON entries (and now also persisted into SQLite).
- **Tracing**: `request_id` is a **trace-wide correlation ID** (one per user/business request). Every step is recorded as a **span** with parent/child relationships.
- **SQLite trace store**: `projects/<project_id>/logs/trace.db` stores spans, LLM messages, tool calls, and trace metadata so the Web UI can render the full chain.

## Data flow

```mermaid
sequenceDiagram
participant Client as ExternalClient
participant Agent as MooseAgent
participant Trace as TraceContext(contextvars)
participant LLM as LLMClient
participant Tool as ToolRuntime
participant DB as trace_db(SQLite)

Client->>Agent: HTTP request (X-Moose-Request-Id optional)
Agent->>Trace: ensure_trace(request_id)
Agent->>DB: span_start(ingress.http)

Agent->>LLM: with span(llm.call)
LLM->>DB: span_start(llm.call)
LLM->>DB: llm_messages(request/response/tool_result)
LLM->>DB: llm_calls(usage/cost)
LLM->>DB: span_end(llm.call)

Agent->>Tool: with span(tool.call)
Tool->>DB: tool_calls(args/result)
Tool->>DB: span_end(tool.call)

Agent->>DB: span_end(ingress.http)
Agent-->>Client: HTTP response
```

## Key concepts

### Trace ID (`request_id`)

Moose uses **one `request_id` per end-to-end request** (telegram → finance_office → investment_research_team → specialists → team_merge).

Propagation:
- **HTTP**: `X-Moose-Request-Id` + `X-Moose-Parent-Span-Id`
- **in-process**: `contextvars` (no need to thread IDs through function signatures)

### Spans (`span_id`, `parent_span_id`)

Every operation (HTTP ingress/egress, workflow node, LLM call, tool call) runs inside a span:
- `span_id`: unique identifier for the operation
- `parent_span_id`: span that caused/triggered this operation (for tree reconstruction)

### SQLite store (`trace.db`)

Tables (v1):
- `traces`: one row per `request_id`
- `spans`: one row per span (kind/name/timing/status/attrs)
- `llm_messages`: ordered messages per `llm.call` span
- `llm_calls`: usage/cost summary for `llm.call` spans
- `tool_calls`: args/result for `tool.call` spans

## Important files

- `tracing.py`: TraceContext + span context manager + exporter registration
- `trace_db.py`: SQLite schema + background writer + SpanExporter implementation
- `http_client.py`: httpx/requests helpers that create egress spans and inject propagation headers
- `__init__.py`: existing MooseLogger/LLMLogger and project logging initialization (`set_project`)

## Operational notes

- SQLite is configured with **WAL** + `busy_timeout=30000` to work across processes/containers.
- DB writes are **best-effort** and performed in a background thread to avoid blocking request handling.
- Message/tool payloads are size-capped when persisted to SQLite.


