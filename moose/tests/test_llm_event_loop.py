"""Unit tests for the llm_core event loop subsystem."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from moose.framework.llm_core import (
    AgentLoopEventType,
    AgentLoopScope,
    AgentLoopStopReason,
    LLMClient,
    LLMResponse,
    Message,
    MessageRole,
)
from moose.framework.llm_core.tool_runtime import ToolRuntime


def _response(
    *,
    content: Any,
    tool_calls: List[Dict[str, Any]] | None = None,
    usage: Dict[str, int] | None = None,
    cost: float | None = 0.001,
    finish_reason: str = "stop",
    request_id: str = "req",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="gpt-4o",
        finish_reason=finish_reason,
        usage=usage or {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        cost=cost,
        raw_response=None,
        request_id=request_id,
        tool_calls=tool_calls,
    )


class _AsyncTool:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    async def ainvoke(self, args: Dict[str, Any]) -> Any:
        result = self._fn(args)
        if hasattr(result, "__await__"):
            return await result
        return result


class _EventCollector:
    def __init__(self) -> None:
        self.events: List[str] = []

    def on_event(self, event) -> None:
        self.events.append(event.event_type.value)


class _RaisingCallback:
    def on_llm_response(self, _event) -> None:
        raise RuntimeError("callback boom")


def test_run_agent_loop_yields_full_direct_lifecycle():
    client = LLMClient(model="gpt-4o")
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(return_value=_response(content="done", request_id="req_1"))

    async def _collect_events():
        return [event async for event in client.run_agent_loop("hello")]

    events = asyncio.run(_collect_events())
    event_types = [event.event_type for event in events]

    assert event_types == [
        AgentLoopEventType.RUN_START,
        AgentLoopEventType.ITERATION_START,
        AgentLoopEventType.LLM_CALL_START,
        AgentLoopEventType.LLM_RESPONSE,
        AgentLoopEventType.RUN_END,
    ]
    assert events[-1].final_response.content == "done"


def test_collect_agent_loop_emits_top_level_and_nested_tool_events():
    async def tool_a_impl(_args: Dict[str, Any]) -> str:
        runtime = ToolRuntime.current()
        assert runtime is not None
        nested = await runtime.call_tool("tool_b", {"value": 2})
        return f"outer:{nested}"

    tool_a = _AsyncTool("tool_a", tool_a_impl)
    tool_b = _AsyncTool("tool_b", lambda _args: "inner")

    client = LLMClient(model="gpt-4o", tools=[tool_a, tool_b])
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(
        side_effect=[
            _response(
                content="Need tool",
                tool_calls=[{"name": "tool_a", "id": "call_1", "args": {}}],
                finish_reason="tool_calls",
                request_id="req_1",
            ),
            _response(content="All done", request_id="req_2"),
        ]
    )

    result = asyncio.run(client.collect_agent_loop("do it", raise_on_error=True))

    main_starts = [
        event for event in result.events
        if event.event_type == AgentLoopEventType.TOOL_CALL_START and event.scope == AgentLoopScope.MAIN
    ]
    nested_starts = [
        event for event in result.events
        if event.event_type == AgentLoopEventType.TOOL_CALL_START and event.scope == AgentLoopScope.NESTED_TOOL
    ]
    nested_success = [
        event for event in result.events
        if event.event_type == AgentLoopEventType.TOOL_CALL_SUCCESS and event.scope == AgentLoopScope.NESTED_TOOL
    ]

    assert len(main_starts) == 1
    assert main_starts[0].tool_name == "tool_a"
    assert main_starts[0].internal is False
    assert len(nested_starts) == 1
    assert nested_starts[0].tool_name == "tool_b"
    assert nested_starts[0].internal is True
    assert nested_starts[0].tool_depth == 2
    assert len(nested_success) == 1
    assert result.final_response.content == "All done"


def test_tool_failure_emits_error_event_and_appends_tool_message():
    broken_tool = _AsyncTool("broken_tool", lambda _args: (_ for _ in ()).throw(ValueError("boom")))

    client = LLMClient(model="gpt-4o", tools=[broken_tool])
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(
        side_effect=[
            _response(
                content="Call the broken tool",
                tool_calls=[{"name": "broken_tool", "id": "call_1", "args": {}}],
                finish_reason="tool_calls",
                request_id="req_1",
            ),
            _response(content="Recovered", request_id="req_2"),
        ]
    )

    result = asyncio.run(client.collect_agent_loop("test failure", raise_on_error=True))

    error_events = [
        event for event in result.events
        if event.event_type == AgentLoopEventType.TOOL_CALL_ERROR and event.scope == AgentLoopScope.MAIN
    ]
    assert len(error_events) == 1
    assert error_events[0].tool_name == "broken_tool"
    assert error_events[0].error_type == "ValueError"

    second_call_messages = client.langchain_llm.ainvoke.call_args_list[1].kwargs["messages"]
    tool_messages = [msg for msg in second_call_messages if getattr(msg, "role", None) == MessageRole.TOOL]
    assert tool_messages
    assert "Error executing tool broken_tool: boom" in str(tool_messages[0].content)
    assert result.final_response.content == "Recovered"


def test_reasoning_blocks_are_preserved_in_events_and_tool_turn_history():
    thinking_blocks = [
        {"type": "thinking", "thinking": "Need a tool", "signature": "sig_123"},
        {"type": "text", "text": "Calling tool now."},
    ]
    raw_response = MagicMock()
    raw_response.content = thinking_blocks

    tool = _AsyncTool("test_tool", lambda _args: "tool result")
    client = LLMClient(
        model="gpt-4o",
        tools=[tool],
        enable_multi_stage_reasoning=True,
        default_call_kwargs={"thinking": {"type": "adaptive"}},
    )
    client.langchain_llm = MagicMock()
    first_response = _response(
        content=thinking_blocks,
        tool_calls=[{"name": "test_tool", "id": "call_1", "args": {}}],
        finish_reason="tool_calls",
        request_id="req_1",
    )
    first_response.raw_response = raw_response
    second_response = _response(
        content="<FINAL_ANSWER>Done",
        request_id="req_2",
    )
    client.langchain_llm.ainvoke = AsyncMock(
        side_effect=[
            first_response,
            second_response,
        ]
    )

    result = asyncio.run(client.collect_agent_loop("Analyze this", raise_on_error=True))

    llm_events = [event for event in result.events if event.event_type == AgentLoopEventType.LLM_RESPONSE]
    assert isinstance(llm_events[0].assistant_content, list)
    assert llm_events[0].assistant_content[0]["type"] == "thinking"
    assert llm_events[0].assistant_content[0]["signature"] == "sig_123"

    second_call_messages = client.langchain_llm.ainvoke.call_args_list[1].kwargs["messages"]
    assistant_messages = [msg for msg in second_call_messages if getattr(msg, "role", None) == MessageRole.ASSISTANT]
    assert assistant_messages
    assert isinstance(assistant_messages[0].content, list)
    assert assistant_messages[0].content[0]["type"] == "thinking"
    assert result.stop_reason == AgentLoopStopReason.FINAL_ANSWER_MARKER
    assert result.final_response.content == "Done"


def test_forced_finalization_emits_dedicated_events():
    tool = _AsyncTool("test_tool", lambda _args: "tool result")
    client = LLMClient(model="gpt-4o", tools=[tool], enable_multi_stage_reasoning=True, max_tool_iterations=1)
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(
        side_effect=[
            _response(
                content="Need tool 1",
                tool_calls=[{"name": "test_tool", "id": "call_1", "args": {}}],
                finish_reason="tool_calls",
                request_id="req_1",
            ),
            _response(
                content="Need tool 2",
                tool_calls=[{"name": "test_tool", "id": "call_2", "args": {}}],
                finish_reason="tool_calls",
                request_id="req_2",
            ),
            _response(
                content="<FINAL_ANSWER>Forced answer",
                request_id="req_3",
            ),
        ]
    )

    result = asyncio.run(client.collect_agent_loop("Need more work", raise_on_error=True))
    event_types = [event.event_type for event in result.events]

    assert AgentLoopEventType.FORCED_FINALIZATION_START in event_types
    assert AgentLoopEventType.FORCED_FINALIZATION_COMPLETE in event_types
    assert result.stop_reason == AgentLoopStopReason.FORCED_FINALIZATION
    assert result.final_response.content == "Forced answer"


def test_chunked_execution_emits_chunk_lifecycle_events_and_aggregates_usage():
    client = LLMClient(model="gpt-4o", max_input_tokens=1000)
    client._count_message_tokens = MagicMock(side_effect=lambda message, system_message=None, messages=None: 950 if message else 100)
    client._chunk_content = MagicMock(return_value=["chunk-one", "chunk-two"])
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(
        side_effect=[
            _response(content="chunk result 1", usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}, request_id="chunk_1"),
            _response(content="chunk result 2", usage={"input_tokens": 6, "output_tokens": 3, "total_tokens": 9}, request_id="chunk_2"),
            _response(content="summary result", usage={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}, request_id="summary"),
        ]
    )

    result = asyncio.run(client.collect_agent_loop("x" * 500, raise_on_error=True))
    event_types = [event.event_type for event in result.events]

    assert event_types.count(AgentLoopEventType.CHUNK_START) == 2
    assert event_types.count(AgentLoopEventType.CHUNK_COMPLETE) == 2
    assert AgentLoopEventType.CHUNKING_START in event_types
    assert AgentLoopEventType.CHUNK_SUMMARY_START in event_types
    assert AgentLoopEventType.CHUNK_SUMMARY_COMPLETE in event_types
    assert result.stop_reason == AgentLoopStopReason.CHUNK_SUMMARY
    assert result.final_response.content == "summary result"
    assert result.final_response.usage == {"input_tokens": 15, "output_tokens": 7, "total_tokens": 22}


def test_callbacks_are_fan_out_and_callback_failures_are_isolated():
    collector = _EventCollector()
    client = LLMClient(model="gpt-4o")
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(return_value=_response(content="done", request_id="req_1"))

    result = asyncio.run(
        client.collect_agent_loop(
            "hello",
            callbacks=[collector, _RaisingCallback()],
            raise_on_error=True,
        )
    )

    assert collector.events[0] == AgentLoopEventType.RUN_START.value
    assert collector.events[-1] == AgentLoopEventType.RUN_END.value
    assert result.callback_errors
    assert any("callback boom" in item for item in result.callback_errors)
    assert result.final_response.content == "done"


def test_send_message_and_sync_wrappers_remain_backward_compatible():
    client = LLMClient(model="gpt-4o")
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(return_value=_response(content="sync done", request_id="req_1"))

    response = client.send_message_sync("hello")
    assert isinstance(response, LLMResponse)
    assert response.content == "sync done"


def test_send_messages_uses_collector_compatibly():
    client = LLMClient(model="gpt-4o")
    client.langchain_llm = MagicMock()
    client.langchain_llm.ainvoke = AsyncMock(return_value=_response(content="multi done", request_id="req_1"))

    response = asyncio.run(
        client.send_messages(
            [
                Message(role=MessageRole.USER, content="hello"),
                Message(role=MessageRole.USER, content="followup"),
            ]
        )
    )
    assert isinstance(response, LLMResponse)
    assert response.content == "multi done"
