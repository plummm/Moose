import sqlite3
import time
from pathlib import Path

import pytest


def _wait_writer(ex, timeout_s: float = 2.0) -> None:
    """
    Best-effort wait for the background trace writer queue to drain.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if ex is None:
                return
            q = getattr(getattr(ex, "_writer", None), "q", None)
            if q is None:
                return
            if q.unfinished_tasks == 0:
                return
        except Exception:
            return
        time.sleep(0.01)


def _read_rows(db_path: Path, sql: str, args=()):
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def test_trace_db_writes_spans(tmp_path: Path):
    from moose.framework.logging import set_project
    from moose.framework.logging.trace_db import init_trace_db
    from moose.framework.logging.tracing import ensure_trace, span as trace_span

    set_project("testproj", base_dir=tmp_path)
    log_dir = tmp_path / "testproj" / "logs"
    ex = init_trace_db(project_id="testproj", log_dir=log_dir, agent_name="test_agent")

    ensure_trace(request_id="rid_test", project_id="testproj", agent_name="test_agent")
    with trace_span(kind="ingress.http", name="GET /health", span_id="span_ingress"):
        with trace_span(kind="workflow.node", name="finance_office.start", span_id="span_node"):
            pass

    _wait_writer(ex)
    db_path = log_dir / "trace.db"
    assert db_path.exists()

    spans = _read_rows(db_path, "SELECT span_id, request_id, parent_span_id, kind, name, status FROM spans ORDER BY start_ts;")
    assert any(s["span_id"] == "span_ingress" and s["request_id"] == "rid_test" for s in spans)
    assert any(s["span_id"] == "span_node" and s["parent_span_id"] == "span_ingress" for s in spans)


def test_llm_logger_persists_messages_and_call(tmp_path: Path):
    from moose.framework.logging import set_project, get_llm_logger
    from moose.framework.logging.trace_db import init_trace_db
    from moose.framework.logging.tracing import ensure_trace, span as trace_span

    set_project("testproj2", base_dir=tmp_path)
    log_dir = tmp_path / "testproj2" / "logs"
    ex = init_trace_db(project_id="testproj2", log_dir=log_dir, agent_name="agent_x")

    ensure_trace(request_id="rid_llm", project_id="testproj2", agent_name="agent_x")
    llm_logger = get_llm_logger(project_id="testproj2")

    with trace_span(kind="llm.call", name="gpt-test", span_id="span_llm"):
        llm_logger.log_message(
            message={"type": "HumanMessage", "content": "hi"},
            direction="request",
            request_id="rid_llm",
            model="gpt-test",
            agent_name="agent_x",
        )
        llm_logger.log_message(
            message={"type": "AIMessage", "content": "hello"},
            direction="response",
            request_id="rid_llm",
            model="gpt-test",
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            cost=0.001,
            agent_name="agent_x",
        )

    _wait_writer(ex)
    db_path = log_dir / "trace.db"

    msgs = _read_rows(db_path, "SELECT span_id, role, idx, content FROM llm_messages WHERE span_id=? ORDER BY idx;", ("span_llm",))
    assert len(msgs) >= 2
    assert msgs[0]["role"] == "user"
    assert "hi" in str(msgs[0]["content"])

    calls = _read_rows(db_path, "SELECT span_id, model, cost, usage_json FROM llm_calls WHERE span_id=?;", ("span_llm",))
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-test"


def test_tool_runtime_persists_tool_call(tmp_path: Path):
    from moose.framework.logging import set_project
    from moose.framework.logging.trace_db import init_trace_db
    from moose.framework.logging.tracing import ensure_trace, span as trace_span
    from moose.framework.llm_core.tool_runtime import ToolRuntime

    set_project("testproj3", base_dir=tmp_path)
    log_dir = tmp_path / "testproj3" / "logs"
    ex = init_trace_db(project_id="testproj3", log_dir=log_dir, agent_name="agent_tools")

    async def _tool_impl(**kwargs):
        return {"ok": True, "args": kwargs}

    async def _invoke_tool(tool, name, args):
        return await tool(**args)

    ensure_trace(request_id="rid_tool", project_id="testproj3", agent_name="agent_tools")
    rt = ToolRuntime(tool_map={"t1": _tool_impl}, invoke_tool=_invoke_tool, request_id="rid_tool", agent_name="agent_tools", logger=None)

    # Ensure there is a parent span in context
    with trace_span(kind="workflow.node", name="n1", span_id="span_parent"):
        import asyncio
        asyncio.run(rt.call_tool("t1", {"x": 1}))

    _wait_writer(ex)
    db_path = log_dir / "trace.db"
    tool_rows = _read_rows(db_path, "SELECT span_id, tool_name, args_json, result_json, error FROM tool_calls;")
    assert len(tool_rows) == 1
    assert tool_rows[0]["tool_name"] == "t1"

