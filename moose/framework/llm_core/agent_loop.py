from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Union

from moose.framework.llm_core.models import LLMResponse, Message, MessageRole
from moose.framework.llm_core.tool_runtime import ToolRuntime
from moose.framework.logging.tracing import ensure_trace, get_current

if TYPE_CHECKING:
    from moose.framework.llm_core.client import LLMClient


class AgentLoopScope(str, Enum):
    MAIN = "main"
    CHUNK = "chunk"
    SUMMARY = "summary"
    FORCED_FINAL = "forced_final"
    NESTED_TOOL = "nested_tool"


class AgentLoopEventType(str, Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    RUN_ERROR = "run_error"
    ITERATION_START = "iteration_start"
    CONTEXT_TRIM = "context_trim"
    LLM_CALL_START = "llm_call_start"
    LLM_RESPONSE = "llm_response"
    TOOL_BATCH_START = "tool_batch_start"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_SUCCESS = "tool_call_success"
    TOOL_CALL_ERROR = "tool_call_error"
    CONTINUATION_PROMPT_ADDED = "continuation_prompt_added"
    FORCED_FINALIZATION_START = "forced_finalization_start"
    FORCED_FINALIZATION_COMPLETE = "forced_finalization_complete"
    CHUNKING_START = "chunking_start"
    CHUNKING_FALLBACK = "chunking_fallback"
    CHUNK_START = "chunk_start"
    CHUNK_COMPLETE = "chunk_complete"
    CHUNK_SUMMARY_START = "chunk_summary_start"
    CHUNK_SUMMARY_COMPLETE = "chunk_summary_complete"


class AgentLoopStopReason(str, Enum):
    DIRECT_RESPONSE = "direct_response"
    FINAL_ANSWER_MARKER = "final_answer_marker"
    FORCED_FINALIZATION = "forced_finalization"
    CHUNK_SUMMARY = "chunk_summary"
    ERROR = "error"


@dataclass(slots=True)
class AgentLoopOptions:
    callbacks: Sequence[Any] = field(default_factory=tuple)
    max_tool_iterations: Optional[int] = None
    allow_chunking: bool = True
    emit_nested_tool_events: bool = True
    run_id: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentLoopEventBase:
    run_id: str
    request_id: str
    sequence: int
    scope: AgentLoopScope
    iteration: Optional[int] = None
    emitted_at: float = field(default_factory=time.time)
    agent_name: Optional[str] = None
    model: Optional[str] = None
    trace_span_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_type: AgentLoopEventType = field(init=False)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for item in fields(self):
            payload[item.name] = _serialize_event_value(getattr(self, item.name))
        payload["event_type"] = self.event_type.value
        return payload


@dataclass(slots=True)
class RunStartEvent(AgentLoopEventBase):
    max_tool_iterations: int = 0
    allow_chunking: bool = True
    multimodal: bool = False
    tool_names: List[str] = field(default_factory=list)
    initial_message_count: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.RUN_START)


@dataclass(slots=True)
class RunEndEvent(AgentLoopEventBase):
    final_response: Optional[LLMResponse] = None
    stop_reason: Optional[AgentLoopStopReason] = None
    total_usage: Dict[str, int] = field(default_factory=dict)
    total_cost: Optional[float] = None
    iteration_count: int = 0
    conversation_message_count: int = 0
    callback_errors: List[str] = field(default_factory=list)
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.RUN_END)


@dataclass(slots=True)
class RunErrorEvent(AgentLoopEventBase):
    error_type: str = ""
    error_message: str = ""
    callback_errors: List[str] = field(default_factory=list)
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.RUN_ERROR)


@dataclass(slots=True)
class IterationStartEvent(AgentLoopEventBase):
    current_tokens: int = 0
    safe_budget: int = 0
    reserved_output_tokens: int = 0
    message_count: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.ITERATION_START)


@dataclass(slots=True)
class ContextTrimEvent(AgentLoopEventBase):
    removed_messages: int = 0
    token_estimate_after: int = 0
    safe_budget: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CONTEXT_TRIM)


@dataclass(slots=True)
class LLMCallStartEvent(AgentLoopEventBase):
    stage_name: str = ""
    message_count: int = 0
    request_kwargs_keys: List[str] = field(default_factory=list)
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.LLM_CALL_START)


@dataclass(slots=True)
class LLMResponseEvent(AgentLoopEventBase):
    response: Optional[LLMResponse] = None
    assistant_text: str = ""
    assistant_content: Any = None
    tool_call_count: int = 0
    total_usage: Dict[str, int] = field(default_factory=dict)
    total_cost: Optional[float] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.LLM_RESPONSE)


@dataclass(slots=True)
class ToolBatchStartEvent(AgentLoopEventBase):
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    executable_tool_count: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.TOOL_BATCH_START)


@dataclass(slots=True)
class ToolCallStartEvent(AgentLoopEventBase):
    tool_name: str = ""
    tool_call_id: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_depth: int = 1
    internal: bool = False
    parent_span_id: Optional[str] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.TOOL_CALL_START)


@dataclass(slots=True)
class ToolCallSuccessEvent(AgentLoopEventBase):
    tool_name: str = ""
    tool_call_id: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    tool_message: Optional[Message] = None
    tool_depth: int = 1
    internal: bool = False
    duration_ms: Optional[float] = None
    parent_span_id: Optional[str] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.TOOL_CALL_SUCCESS)


@dataclass(slots=True)
class ToolCallErrorEvent(AgentLoopEventBase):
    tool_name: str = ""
    tool_call_id: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    tool_message: Optional[Message] = None
    tool_depth: int = 1
    internal: bool = False
    duration_ms: Optional[float] = None
    parent_span_id: Optional[str] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.TOOL_CALL_ERROR)


@dataclass(slots=True)
class ContinuationPromptAddedEvent(AgentLoopEventBase):
    prompt: str = ""
    reason: str = ""
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CONTINUATION_PROMPT_ADDED)


@dataclass(slots=True)
class ForcedFinalizationStartEvent(AgentLoopEventBase):
    reason: str = ""
    max_tool_iterations: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.FORCED_FINALIZATION_START)


@dataclass(slots=True)
class ForcedFinalizationCompleteEvent(AgentLoopEventBase):
    response: Optional[LLMResponse] = None
    assistant_text: str = ""
    total_usage: Dict[str, int] = field(default_factory=dict)
    total_cost: Optional[float] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.FORCED_FINALIZATION_COMPLETE)


@dataclass(slots=True)
class ChunkingStartEvent(AgentLoopEventBase):
    total_tokens: int = 0
    chunk_threshold: int = 0
    context_tokens: int = 0
    chunk_size_tokens: int = 0
    total_chunks: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNKING_START)


@dataclass(slots=True)
class ChunkingFallbackEvent(AgentLoopEventBase):
    reason: str = ""
    total_tokens: int = 0
    chunk_threshold: int = 0
    context_tokens: int = 0
    chunk_size_tokens: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNKING_FALLBACK)


@dataclass(slots=True)
class ChunkStartEvent(AgentLoopEventBase):
    chunk_index: int = 0
    total_chunks: int = 0
    chunk_chars: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNK_START)


@dataclass(slots=True)
class ChunkCompleteEvent(AgentLoopEventBase):
    chunk_index: int = 0
    total_chunks: int = 0
    response: Optional[LLMResponse] = None
    assistant_text: str = ""
    duration_ms: Optional[float] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNK_COMPLETE)


@dataclass(slots=True)
class ChunkSummaryStartEvent(AgentLoopEventBase):
    total_chunks: int = 0
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNK_SUMMARY_START)


@dataclass(slots=True)
class ChunkSummaryCompleteEvent(AgentLoopEventBase):
    total_chunks: int = 0
    response: Optional[LLMResponse] = None
    assistant_text: str = ""
    total_usage: Dict[str, int] = field(default_factory=dict)
    total_cost: Optional[float] = None
    event_type: AgentLoopEventType = field(init=False, default=AgentLoopEventType.CHUNK_SUMMARY_COMPLETE)


AgentLoopEvent = Union[
    RunStartEvent,
    RunEndEvent,
    RunErrorEvent,
    IterationStartEvent,
    ContextTrimEvent,
    LLMCallStartEvent,
    LLMResponseEvent,
    ToolBatchStartEvent,
    ToolCallStartEvent,
    ToolCallSuccessEvent,
    ToolCallErrorEvent,
    ContinuationPromptAddedEvent,
    ForcedFinalizationStartEvent,
    ForcedFinalizationCompleteEvent,
    ChunkingStartEvent,
    ChunkingFallbackEvent,
    ChunkStartEvent,
    ChunkCompleteEvent,
    ChunkSummaryStartEvent,
    ChunkSummaryCompleteEvent,
]


class AgentLoopCallback(Protocol):
    def on_event(self, event: AgentLoopEvent) -> Any: ...


@dataclass(slots=True)
class AgentLoopResult:
    run_id: str
    request_id: str
    final_response: Optional[LLMResponse]
    events: List[AgentLoopEvent]
    final_conversation_messages: List[Message]
    stop_reason: Optional[AgentLoopStopReason]
    iteration_count: int
    total_usage: Dict[str, int]
    total_cost: Optional[float]
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    callback_errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_type is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "final_response": _serialize_event_value(self.final_response),
            "events": [_serialize_event_value(event) for event in self.events],
            "final_conversation_messages": _serialize_event_value(self.final_conversation_messages),
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
            "iteration_count": self.iteration_count,
            "total_usage": dict(self.total_usage or {}),
            "total_cost": self.total_cost,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "callback_errors": list(self.callback_errors or []),
        }


@dataclass(slots=True)
class _ChunkExecutionResult:
    chunk_index: int
    chunk_content: str
    response: LLMResponse
    started_at: float
    completed_at: float

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at) * 1000.0


@dataclass(slots=True)
class _LoopOutcome:
    final_response: LLMResponse
    final_conversation_messages: List[Message]
    stop_reason: AgentLoopStopReason
    iteration_count: int
    total_usage: Dict[str, int]
    total_cost: Optional[float]


def _serialize_event_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, LLMResponse):
        return value.to_dict()
    if isinstance(value, Message):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_event_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_event_value(item) for key, item in value.items()}
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        try:
            return value.to_dict()
        except Exception:
            pass
    if dataclass_is_instance(value):
        try:
            return _serialize_event_value(asdict(value))
        except Exception:
            return str(value)
    return value


def dataclass_is_instance(value: Any) -> bool:
    return hasattr(value, "__dataclass_fields__") and not isinstance(value, type)


def _copy_usage(usage: Optional[Dict[str, int]]) -> Dict[str, int]:
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def _accumulate_usage(total: Dict[str, int], usage: Optional[Dict[str, int]]) -> None:
    if not isinstance(usage, dict):
        return
    total["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
    total["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
    total["total_tokens"] += int(usage.get("total_tokens", 0) or 0)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class AgentLoopRunner:
    WEB_SEARCH_TOOL_NAMES = {
        "web_search_preview",
        "web_search",
        "web_search_20250305",
        "google_search",
    }

    def __init__(
        self,
        *,
        client: "LLMClient",
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        call_kwargs: Optional[Dict[str, Any]] = None,
        options: Optional[AgentLoopOptions] = None,
        request_id: Optional[str] = None,
    ) -> None:
        self.client = client
        self.message = message
        self.messages = list(messages) if messages else []
        self.system_message = system_message
        self.call_kwargs = dict(call_kwargs or {})
        self.options = options or AgentLoopOptions()
        self.run_id = str(self.options.run_id or uuid.uuid4())
        resolved_request_id = str(
            request_id
            or self.options.request_id
            or getattr(get_current(), "request_id", None)
            or uuid.uuid4()
        )
        self.request_id = resolved_request_id
        self.agent_name = getattr(client, "agent_name", None)
        self.model = str(getattr(client, "model", "") or "")
        self.logger = getattr(client, "logger", None)
        self.max_tool_iterations = int(
            self.options.max_tool_iterations
            if self.options.max_tool_iterations is not None
            else getattr(client, "max_tool_iterations", 0)
        )
        self.allow_chunking = bool(self.options.allow_chunking)
        self.emit_nested_tool_events = bool(self.options.emit_nested_tool_events)
        self.callbacks = tuple(self.options.callbacks or ())

        self._sequence = 0
        self._events: List[AgentLoopEvent] = []
        self._callback_errors: List[str] = []
        self._result: Optional[AgentLoopResult] = None
        self._completed = False
        self._running = False
        self._captured_error: Optional[BaseException] = None
        self._queue: Optional[asyncio.Queue[AgentLoopEvent]] = None

    @property
    def result(self) -> Optional[AgentLoopResult]:
        return self._result

    async def collect(self, *, raise_on_error: bool = False) -> AgentLoopResult:
        if not self._completed:
            async for _event in self.run():
                pass
        if self._result is None:
            raise RuntimeError("AgentLoopRunner did not produce a result")
        if raise_on_error and self._captured_error is not None:
            raise self._captured_error
        return self._result

    async def run(self) -> AsyncIterator[AgentLoopEvent]:
        if self._completed:
            for event in self._events:
                yield event
            return
        if self._running:
            raise RuntimeError("AgentLoopRunner is already running")
        self._running = True
        self._queue = asyncio.Queue()
        driver = asyncio.create_task(self._drive())
        try:
            while True:
                event = await self._queue.get()
                yield event
                if event.event_type in {AgentLoopEventType.RUN_END, AgentLoopEventType.RUN_ERROR}:
                    break
        finally:
            await driver
            self._completed = True
            self._running = False
            self._queue = None

    async def _drive(self) -> None:
        ensure_trace(request_id=self.request_id, agent_name=self.agent_name)

        initial_message_count = len(self.messages) + 1
        max_tool_iterations = max(self.max_tool_iterations, 0)
        multimodal = self.client._has_multimodal_content(
            message=self.message,
            system_message=self.system_message,
            messages=self.messages,
        )
        await self._emit(
            RunStartEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.MAIN,
                agent_name=self.agent_name,
                model=self.model,
                metadata=dict(self.options.metadata or {}),
                max_tool_iterations=max_tool_iterations,
                allow_chunking=self.allow_chunking,
                multimodal=multimodal,
                tool_names=sorted(getattr(self.client, "tool_map", {}).keys()),
                initial_message_count=initial_message_count,
            )
        )

        try:
            total_tokens = self.client._count_message_tokens(
                message=self.message,
                system_message=self.system_message,
                messages=self.messages,
            )
            chunk_threshold = int(getattr(self.client, "max_input_tokens", 0) * 0.9)
            should_chunk = (
                self.allow_chunking
                and not multimodal
                and chunk_threshold > 0
                and total_tokens > chunk_threshold
            )

            if should_chunk:
                outcome = await self._run_chunked(total_tokens=total_tokens, chunk_threshold=chunk_threshold)
            else:
                outcome = await self._run_direct()

            self._result = AgentLoopResult(
                run_id=self.run_id,
                request_id=self.request_id,
                final_response=outcome.final_response,
                events=list(self._events),
                final_conversation_messages=list(outcome.final_conversation_messages),
                stop_reason=outcome.stop_reason,
                iteration_count=outcome.iteration_count,
                total_usage=_copy_usage(outcome.total_usage),
                total_cost=outcome.total_cost,
                callback_errors=list(self._callback_errors),
            )
            await self._emit(
                RunEndEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.MAIN,
                    agent_name=self.agent_name,
                    model=self.model,
                    final_response=outcome.final_response,
                    stop_reason=outcome.stop_reason,
                    total_usage=_copy_usage(outcome.total_usage),
                    total_cost=outcome.total_cost,
                    iteration_count=outcome.iteration_count,
                    conversation_message_count=len(outcome.final_conversation_messages),
                    callback_errors=list(self._callback_errors),
                )
            )
            self._result.events = list(self._events)
        except Exception as exc:
            self._captured_error = exc
            self._result = AgentLoopResult(
                run_id=self.run_id,
                request_id=self.request_id,
                final_response=None,
                events=list(self._events),
                final_conversation_messages=[],
                stop_reason=AgentLoopStopReason.ERROR,
                iteration_count=0,
                total_usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                total_cost=None,
                error_type=type(exc).__name__,
                error_message=str(exc),
                callback_errors=list(self._callback_errors),
            )
            await self._emit(
                RunErrorEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.MAIN,
                    agent_name=self.agent_name,
                    model=self.model,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    callback_errors=list(self._callback_errors),
                )
            )
            self._result.events = list(self._events)

    async def _run_direct(self) -> _LoopOutcome:
        conversation_messages = list(self.messages)
        if isinstance(self.message, str):
            conversation_messages.append(Message(role=MessageRole.USER, content=self.message))
        else:
            conversation_messages.append(self.message)

        system_message = self._augment_system_message_for_multi_stage(self.system_message)
        total_cost = 0.0
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        final_response: Optional[LLMResponse] = None
        runtime: Optional[ToolRuntime] = None
        iterations_completed = 0
        stop_reason = AgentLoopStopReason.DIRECT_RESPONSE

        try:
            for iteration in range(max(self.max_tool_iterations, 0) + 1):
                iterations_completed = iteration + 1
                reserved_output_tokens = self._resolve_reserved_output_tokens()
                safe_budget = self.client._safe_input_budget(safety_margin=2048)
                current_tokens = self.client._count_message_tokens(
                    message="",
                    system_message=system_message,
                    messages=conversation_messages,
                )
                await self._emit(
                    IterationStartEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        current_tokens=current_tokens,
                        safe_budget=safe_budget,
                        reserved_output_tokens=reserved_output_tokens,
                        message_count=len(conversation_messages),
                    )
                )

                removed_messages = 0
                if current_tokens > safe_budget and conversation_messages:
                    before_count = len(conversation_messages)
                    (
                        conversation_messages,
                        compaction_usage,
                        compaction_cost,
                    ) = await self.client._compact_conversation_messages_for_budget_async(
                        conversation_messages=conversation_messages,
                        system_message=system_message,
                        safe_budget=safe_budget,
                        reserved_output_tokens=reserved_output_tokens,
                        iteration=iteration,
                        request_id=self.request_id,
                    )
                    current_tokens = self.client._count_message_tokens(
                        message="",
                        system_message=system_message,
                        messages=conversation_messages,
                    )
                    removed_messages = max(0, before_count - len(conversation_messages))
                    _accumulate_usage(total_usage, compaction_usage)
                    if compaction_cost:
                        total_cost += float(compaction_cost or 0.0)
                if removed_messages > 0:
                    await self._emit(
                        ContextTrimEvent(
                            run_id=self.run_id,
                            request_id=self.request_id,
                            sequence=0,
                            scope=AgentLoopScope.MAIN,
                            iteration=iteration,
                            agent_name=self.agent_name,
                            model=self.model,
                            removed_messages=removed_messages,
                            token_estimate_after=current_tokens,
                            safe_budget=safe_budget,
                        )
                    )

                await self._emit(
                    LLMCallStartEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        stage_name="direct",
                        message_count=len(conversation_messages),
                        request_kwargs_keys=sorted(self.call_kwargs.keys()),
                    )
                )
                from moose.framework.logging.tracing import span as trace_span

                with trace_span(
                    kind="llm.call",
                    name=str(self.model),
                    attrs={"llm.model": str(self.model), "llm.iteration": int(iteration), "llm.stage": "direct"},
                ):
                    response = await self.client.langchain_llm.ainvoke(
                        message=None,
                        messages=conversation_messages,
                        system_message=system_message,
                        request_id=str(self.request_id),
                        agent_name=self.agent_name,
                        **self.call_kwargs,
                    )

                if response.cost:
                    total_cost += float(response.cost or 0.0)
                _accumulate_usage(total_usage, response.usage)

                response_text = self.client._extract_actual_response_text(getattr(response, "content", ""))
                assistant_content = self.client._extract_raw_assistant_content(response)
                await self._emit(
                    LLMResponseEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        response=response,
                        assistant_text=response_text,
                        assistant_content=assistant_content,
                        tool_call_count=len(response.tool_calls or []),
                        total_usage=_copy_usage(total_usage),
                        total_cost=total_cost,
                    )
                )

                if self.client.enable_multi_stage_reasoning and self.client._has_final_answer_marker(response_text):
                    final_answer_content = self.client._extract_final_answer(response_text)
                    final_response = LLMResponse(
                        content=final_answer_content,
                        model=response.model,
                        finish_reason=response.finish_reason,
                        usage=response.usage,
                        cost=response.cost,
                        raw_response=response.raw_response,
                        request_id=response.request_id,
                        tool_calls=None,
                    )
                    stop_reason = AgentLoopStopReason.FINAL_ANSWER_MARKER
                    break

                conversation_messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=assistant_content,
                        tool_calls=response.tool_calls,
                    )
                )

                if response.tool_calls and self.client.tools:
                    executable_tool_calls = [
                        tc for tc in (response.tool_calls or [])
                        if self._tool_name_from_call(tc) not in self.WEB_SEARCH_TOOL_NAMES or not self.client.enable_web_search
                    ]
                    await self._emit(
                        ToolBatchStartEvent(
                            run_id=self.run_id,
                            request_id=self.request_id,
                            sequence=0,
                            scope=AgentLoopScope.MAIN,
                            iteration=iteration,
                            agent_name=self.agent_name,
                            model=self.model,
                            tool_calls=list(response.tool_calls or []),
                            executable_tool_count=len(executable_tool_calls),
                        )
                    )

                    if runtime is None:
                        runtime = ToolRuntime(
                            tool_map=self.client.tool_map,
                            invoke_tool=lambda tool, tool_name, tool_args: self.client._invoke_one_tool(
                                tool, tool_name, tool_args, runtime=runtime
                            ),
                            request_id=self.request_id,
                            agent_name=self.agent_name,
                            logger=self.logger,
                            event_emitter=self._handle_runtime_tool_event if self.emit_nested_tool_events else None,
                        )
                        await runtime.start()
                    runtime.set_event_context(iteration=iteration, scope=AgentLoopScope.MAIN.value)

                    tool_messages = await self._execute_top_level_tool_calls(
                        response.tool_calls,
                        iteration=iteration,
                        runtime=runtime,
                    )
                    conversation_messages.extend(tool_messages)

                    if self.client.enable_multi_stage_reasoning:
                        continuation_prompt = self.client._build_continuation_prompt(iteration)
                        conversation_messages.append(Message(role=MessageRole.USER, content=continuation_prompt))
                        await self._emit(
                            ContinuationPromptAddedEvent(
                                run_id=self.run_id,
                                request_id=self.request_id,
                                sequence=0,
                                scope=AgentLoopScope.MAIN,
                                iteration=iteration,
                                agent_name=self.agent_name,
                                model=self.model,
                                prompt=continuation_prompt,
                                reason="tool_results_available",
                            )
                        )
                    continue

                if self.client.enable_multi_stage_reasoning:
                    continuation_prompt = (
                        f"Do you need more tools or is this your final answer? If it's final, start your response "
                        f"with {self.client.multi_stage_marker}. (e.g., {self.client.multi_stage_marker}My final answer)"
                    )
                    conversation_messages.append(Message(role=MessageRole.USER, content=continuation_prompt))
                    await self._emit(
                        ContinuationPromptAddedEvent(
                            run_id=self.run_id,
                            request_id=self.request_id,
                            sequence=0,
                            scope=AgentLoopScope.MAIN,
                            iteration=iteration,
                            agent_name=self.agent_name,
                            model=self.model,
                            prompt=continuation_prompt,
                            reason="awaiting_final_answer_or_more_tools",
                        )
                    )
                    continue

                final_response = LLMResponse(
                    content=response_text,
                    model=response.model,
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    cost=response.cost,
                    raw_response=response.raw_response,
                    request_id=response.request_id,
                    tool_calls=response.tool_calls,
                )
                stop_reason = AgentLoopStopReason.DIRECT_RESPONSE
                break

            if final_response is None:
                await self._emit(
                    ForcedFinalizationStartEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.FORCED_FINAL,
                        iteration=max(iterations_completed - 1, 0),
                        agent_name=self.agent_name,
                        model=self.model,
                        reason="max_tool_iterations_exhausted",
                        max_tool_iterations=max(self.max_tool_iterations, 0),
                    )
                )

                conversation_messages.append(
                    Message(
                        role=MessageRole.USER,
                        content=(
                            "Tool budget exhausted. Do NOT call any tools.\n"
                            f"Respond starting with {self.client.multi_stage_marker} and then provide your complete final answer now."
                        ),
                    )
                )

                await self._emit(
                    LLMCallStartEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.FORCED_FINAL,
                        iteration=max(iterations_completed - 1, 0),
                        agent_name=self.agent_name,
                        model=self.model,
                        stage_name="forced_final",
                        message_count=len(conversation_messages),
                        request_kwargs_keys=sorted(self.call_kwargs.keys()),
                    )
                )

                from moose.framework.logging.tracing import span as trace_span

                try:
                    with trace_span(
                        kind="llm.call",
                        name=str(self.model),
                        attrs={"llm.model": str(self.model), "llm.stage": "forced_final"},
                    ):
                        forced = await self.client.langchain_llm.ainvoke(
                            message=None,
                            messages=conversation_messages,
                            system_message=system_message,
                            request_id=str(self.request_id),
                            agent_name=self.agent_name,
                            **self.call_kwargs,
                        )
                except Exception as exc:
                    if self.logger:
                        self.logger.error(f"Finalization call failed after max iterations: {exc}")
                    raise

                if getattr(forced, "cost", None):
                    total_cost += float(getattr(forced, "cost", 0.0) or 0.0)
                _accumulate_usage(total_usage, getattr(forced, "usage", None))

                forced_content = getattr(forced, "content", "") if forced is not None else ""
                forced_text = self.client._extract_actual_response_text(forced_content)
                assistant_content = self.client._extract_raw_assistant_content(forced)
                await self._emit(
                    LLMResponseEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.FORCED_FINAL,
                        iteration=max(iterations_completed - 1, 0),
                        agent_name=self.agent_name,
                        model=self.model,
                        response=forced,
                        assistant_text=forced_text,
                        assistant_content=assistant_content,
                        tool_call_count=len(getattr(forced, "tool_calls", None) or []),
                        total_usage=_copy_usage(total_usage),
                        total_cost=total_cost,
                    )
                )

                if self.client.enable_multi_stage_reasoning and self.client._has_final_answer_marker(forced_text):
                    forced_text = self.client._extract_final_answer(forced_text)

                final_response = LLMResponse(
                    content=forced_text,
                    model=getattr(forced, "model", None) or self.model,
                    finish_reason=getattr(forced, "finish_reason", None),
                    usage=getattr(forced, "usage", None),
                    cost=getattr(forced, "cost", None),
                    raw_response=getattr(forced, "raw_response", None),
                    request_id=getattr(forced, "request_id", None) or self.request_id,
                    tool_calls=None,
                )
                await self._emit(
                    ForcedFinalizationCompleteEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.FORCED_FINAL,
                        iteration=max(iterations_completed - 1, 0),
                        agent_name=self.agent_name,
                        model=self.model,
                        response=final_response,
                        assistant_text=str(final_response.content or ""),
                        total_usage=_copy_usage(total_usage),
                        total_cost=total_cost,
                    )
                )
                stop_reason = AgentLoopStopReason.FORCED_FINALIZATION

            if runtime is not None:
                try:
                    total_cost += float(getattr(runtime, "external_cost", 0.0) or 0.0)
                    _accumulate_usage(total_usage, getattr(runtime, "external_usage", None))
                except Exception:
                    pass

            final_response.cost = total_cost if total_cost > 0 else final_response.cost
            final_response.usage = _copy_usage(total_usage) if any(total_usage.values()) else final_response.usage

            if final_response.cost is not None and getattr(self.client, "_cost_tracker", None):
                self.client._cost_tracker.log_cost(
                    model=self.model,
                    cost=final_response.cost,
                    tokens=final_response.usage,
                    request_id=self.request_id,
                )

            if self.logger:
                self.logger.debug(f"Received response from {self.model}")
                if final_response.usage:
                    self.logger.debug(f"Token usage: {final_response.usage}")
                if final_response.cost is not None:
                    self.logger.debug(f"Cost: ${final_response.cost:.6f}")

            return _LoopOutcome(
                final_response=final_response,
                final_conversation_messages=list(conversation_messages),
                stop_reason=stop_reason,
                iteration_count=iterations_completed,
                total_usage=_copy_usage(final_response.usage),
                total_cost=final_response.cost,
            )
        finally:
            if runtime is not None:
                try:
                    await runtime.close()
                except Exception:
                    pass

    async def _run_chunked(self, *, total_tokens: int, chunk_threshold: int) -> _LoopOutcome:
        message_content = self.message if isinstance(self.message, str) else self.client._content_text_for_token_count(self.message.content)
        system_message_content = None
        if self.system_message:
            if isinstance(self.system_message, str):
                system_message_content = self.system_message
            else:
                system_message_content = self.client._content_text_for_token_count(self.system_message.content)

        context_tokens = self.client._count_message_tokens(
            message="",
            system_message=self.system_message,
            messages=self.messages,
        )
        chunk_prompt_overhead = 500
        chunk_size_tokens = chunk_threshold - context_tokens - chunk_prompt_overhead

        if chunk_size_tokens <= 0:
            await self._emit(
                ChunkingFallbackEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.MAIN,
                    agent_name=self.agent_name,
                    model=self.model,
                    reason="chunk_size_too_small",
                    total_tokens=total_tokens,
                    chunk_threshold=chunk_threshold,
                    context_tokens=context_tokens,
                    chunk_size_tokens=chunk_size_tokens,
                )
            )
            return await self._run_direct()

        chunks = self.client._chunk_content(message_content, chunk_size_tokens)
        if not chunks:
            await self._emit(
                ChunkingFallbackEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.MAIN,
                    agent_name=self.agent_name,
                    model=self.model,
                    reason="chunking_produced_no_chunks",
                    total_tokens=total_tokens,
                    chunk_threshold=chunk_threshold,
                    context_tokens=context_tokens,
                    chunk_size_tokens=chunk_size_tokens,
                )
            )
            return await self._run_direct()

        await self._emit(
            ChunkingStartEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.MAIN,
                agent_name=self.agent_name,
                model=self.model,
                total_tokens=total_tokens,
                chunk_threshold=chunk_threshold,
                context_tokens=context_tokens,
                chunk_size_tokens=chunk_size_tokens,
                total_chunks=len(chunks),
            )
        )

        for idx, chunk in enumerate(chunks):
            await self._emit(
                ChunkStartEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.CHUNK,
                    iteration=idx,
                    agent_name=self.agent_name,
                    model=self.model,
                    chunk_index=idx,
                    total_chunks=len(chunks),
                    chunk_chars=len(chunk),
                )
            )

        async def _chunk_worker(chunk_content: str, chunk_index: int) -> _ChunkExecutionResult:
            started_at = time.time()
            response = await self.client._process_chunk(
                chunk_content=chunk_content,
                chunk_index=chunk_index,
                total_chunks=len(chunks),
                original_system_message=system_message_content,
                messages=self.messages,
                request_id=self.request_id,
                **self.call_kwargs,
            )
            completed_at = time.time()
            return _ChunkExecutionResult(
                chunk_index=chunk_index,
                chunk_content=chunk_content,
                response=response,
                started_at=started_at,
                completed_at=completed_at,
            )

        chunk_results = await asyncio.gather(*[_chunk_worker(chunk, idx) for idx, chunk in enumerate(chunks)])
        chunk_results = sorted(chunk_results, key=lambda item: item.chunk_index)

        for chunk_result in chunk_results:
            assistant_text = self.client._extract_actual_response_text(chunk_result.response.content)
            await self._emit(
                LLMCallStartEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.CHUNK,
                    iteration=chunk_result.chunk_index,
                    agent_name=self.agent_name,
                    model=self.model,
                    stage_name="chunk",
                    message_count=len(self.messages) + 1,
                    request_kwargs_keys=sorted(self.call_kwargs.keys()),
                    metadata={
                        "chunk_index": chunk_result.chunk_index,
                        "total_chunks": len(chunks),
                        "started_at": chunk_result.started_at,
                    },
                )
            )
            await self._emit(
                LLMResponseEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.CHUNK,
                    iteration=chunk_result.chunk_index,
                    agent_name=self.agent_name,
                    model=self.model,
                    response=chunk_result.response,
                    assistant_text=assistant_text,
                    assistant_content=self.client._extract_raw_assistant_content(chunk_result.response),
                    tool_call_count=len(chunk_result.response.tool_calls or []),
                    total_usage=_copy_usage(chunk_result.response.usage),
                    total_cost=chunk_result.response.cost,
                    metadata={
                        "chunk_index": chunk_result.chunk_index,
                        "total_chunks": len(chunks),
                        "completed_at": chunk_result.completed_at,
                    },
                )
            )
            await self._emit(
                ChunkCompleteEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.CHUNK,
                    iteration=chunk_result.chunk_index,
                    agent_name=self.agent_name,
                    model=self.model,
                    chunk_index=chunk_result.chunk_index,
                    total_chunks=len(chunks),
                    response=chunk_result.response,
                    assistant_text=assistant_text,
                    duration_ms=chunk_result.duration_ms,
                )
            )

        await self._emit(
            ChunkSummaryStartEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.SUMMARY,
                agent_name=self.agent_name,
                model=self.model,
                total_chunks=len(chunks),
            )
        )
        await self._emit(
            LLMCallStartEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.SUMMARY,
                agent_name=self.agent_name,
                model=self.model,
                stage_name="summary",
                message_count=0,
                request_kwargs_keys=sorted(self.call_kwargs.keys()),
            )
        )
        final_response = await self.client._summarize_chunks(
            chunk_responses=[item.response for item in chunk_results],
            original_system_message=system_message_content,
            request_id=self.request_id,
            **self.call_kwargs,
        )

        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = 0.0
        for response in [item.response for item in chunk_results] + [final_response]:
            _accumulate_usage(total_usage, response.usage)
            total_cost += float(response.cost or 0.0)
        final_response.usage = _copy_usage(total_usage)
        final_response.cost = total_cost
        final_response.request_id = self.request_id

        if final_response.cost is not None and getattr(self.client, "_cost_tracker", None):
            self.client._cost_tracker.log_cost(
                model=self.model,
                cost=final_response.cost,
                tokens=final_response.usage,
                request_id=self.request_id,
            )

        final_text = self.client._extract_actual_response_text(final_response.content)
        await self._emit(
            LLMResponseEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.SUMMARY,
                agent_name=self.agent_name,
                model=self.model,
                response=final_response,
                assistant_text=final_text,
                assistant_content=self.client._extract_raw_assistant_content(final_response),
                tool_call_count=len(final_response.tool_calls or []),
                total_usage=_copy_usage(final_response.usage),
                total_cost=final_response.cost,
            )
        )
        await self._emit(
            ChunkSummaryCompleteEvent(
                run_id=self.run_id,
                request_id=self.request_id,
                sequence=0,
                scope=AgentLoopScope.SUMMARY,
                agent_name=self.agent_name,
                model=self.model,
                total_chunks=len(chunks),
                response=final_response,
                assistant_text=final_text,
                total_usage=_copy_usage(final_response.usage),
                total_cost=final_response.cost,
            )
        )

        final_conversation_messages = list(self.messages)
        if isinstance(self.message, str):
            final_conversation_messages.append(Message(role=MessageRole.USER, content=self.message))
        else:
            final_conversation_messages.append(self.message)
        final_conversation_messages.append(Message(role=MessageRole.ASSISTANT, content=final_response.content))

        return _LoopOutcome(
            final_response=final_response,
            final_conversation_messages=final_conversation_messages,
            stop_reason=AgentLoopStopReason.CHUNK_SUMMARY,
            iteration_count=len(chunks),
            total_usage=_copy_usage(final_response.usage),
            total_cost=final_response.cost,
        )

    async def _execute_top_level_tool_calls(
        self,
        tool_calls: Iterable[Any],
        *,
        iteration: int,
        runtime: ToolRuntime,
    ) -> List[Message]:
        tool_messages: List[Message] = []
        for tool_call in tool_calls:
            tool_name, tool_call_id, tool_args = self._normalize_tool_call(tool_call)
            if self.client.enable_web_search and tool_name in self.WEB_SEARCH_TOOL_NAMES:
                continue

            await self._emit(
                ToolCallStartEvent(
                    run_id=self.run_id,
                    request_id=self.request_id,
                    sequence=0,
                    scope=AgentLoopScope.MAIN,
                    iteration=iteration,
                    agent_name=self.agent_name,
                    model=self.model,
                    tool_name=str(tool_name or ""),
                    tool_call_id=tool_call_id,
                    tool_args=dict(tool_args or {}),
                    tool_depth=1,
                    internal=False,
                )
            )

            if not tool_name or tool_name not in self.client.tool_map:
                error_msg = f"Tool '{tool_name}' not found or not available"
                tool_message = Message(
                    role=MessageRole.TOOL,
                    content=f"Error: {error_msg}",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_calls=[{"name": tool_name, "args": tool_args}] if tool_name else None,
                )
                tool_messages.append(tool_message)
                await self._emit(
                    ToolCallErrorEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        tool_name=str(tool_name or ""),
                        tool_call_id=tool_call_id,
                        tool_args=dict(tool_args or {}),
                        error_type="ToolNotFound",
                        error_message=error_msg,
                        tool_message=tool_message,
                        tool_depth=1,
                        internal=False,
                    )
                )
                continue

            started_at = time.perf_counter()
            try:
                parent_span_id = None
                try:
                    ctx = get_current()
                    parent_span_id = getattr(ctx, "current_span_id", None) if ctx is not None else None
                except Exception:
                    parent_span_id = None

                result = await runtime.call_tool(
                    tool_name,
                    tool_args,
                    parent_span_id=parent_span_id,
                    tool_call_id=tool_call_id,
                )

                tool_message = Message(
                    role=MessageRole.TOOL,
                    content=self._tool_result_content(result),
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_calls=[{"name": tool_name, "args": tool_args}],
                )
                tool_messages.append(tool_message)
                await self._emit(
                    ToolCallSuccessEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_args=dict(tool_args or {}),
                        tool_result=result,
                        tool_message=tool_message,
                        tool_depth=1,
                        internal=False,
                        duration_ms=(time.perf_counter() - started_at) * 1000.0,
                    )
                )
            except Exception as exc:
                error_msg = f"Error executing tool {tool_name}: {exc}"
                tool_message = Message(
                    role=MessageRole.TOOL,
                    content=f"Error: {error_msg}",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_calls=[{"name": tool_name, "args": tool_args}],
                )
                tool_messages.append(tool_message)
                await self._emit(
                    ToolCallErrorEvent(
                        run_id=self.run_id,
                        request_id=self.request_id,
                        sequence=0,
                        scope=AgentLoopScope.MAIN,
                        iteration=iteration,
                        agent_name=self.agent_name,
                        model=self.model,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        tool_args=dict(tool_args or {}),
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        tool_message=tool_message,
                        tool_depth=1,
                        internal=False,
                        duration_ms=(time.perf_counter() - started_at) * 1000.0,
                    )
                )
        return tool_messages

    async def _handle_runtime_tool_event(self, kind: str, payload: Dict[str, Any]) -> None:
        if not self.emit_nested_tool_events:
            return
        scope = AgentLoopScope.NESTED_TOOL
        iteration = payload.get("iteration")
        common_kwargs = dict(
            run_id=self.run_id,
            request_id=self.request_id,
            sequence=0,
            scope=scope,
            iteration=iteration,
            agent_name=self.agent_name,
            model=self.model,
        )
        if kind == "tool_call_start":
            await self._emit(
                ToolCallStartEvent(
                    **common_kwargs,
                    tool_name=str(payload.get("tool_name") or ""),
                    tool_call_id=payload.get("tool_call_id"),
                    tool_args=dict(payload.get("tool_args") or {}),
                    tool_depth=int(payload.get("depth", 0) or 0),
                    internal=True,
                    parent_span_id=payload.get("parent_span_id"),
                )
            )
            return
        if kind == "tool_call_success":
            await self._emit(
                ToolCallSuccessEvent(
                    **common_kwargs,
                    tool_name=str(payload.get("tool_name") or ""),
                    tool_call_id=payload.get("tool_call_id"),
                    tool_args=dict(payload.get("tool_args") or {}),
                    tool_result=payload.get("result"),
                    tool_depth=int(payload.get("depth", 0) or 0),
                    internal=True,
                    duration_ms=payload.get("duration_ms"),
                    parent_span_id=payload.get("parent_span_id"),
                )
            )
            return
        if kind == "tool_call_error":
            await self._emit(
                ToolCallErrorEvent(
                    **common_kwargs,
                    tool_name=str(payload.get("tool_name") or ""),
                    tool_call_id=payload.get("tool_call_id"),
                    tool_args=dict(payload.get("tool_args") or {}),
                    error_type=str(payload.get("error_type") or "RuntimeError"),
                    error_message=str(payload.get("error_message") or ""),
                    tool_depth=int(payload.get("depth", 0) or 0),
                    internal=True,
                    duration_ms=payload.get("duration_ms"),
                    parent_span_id=payload.get("parent_span_id"),
                )
            )

    def _augment_system_message_for_multi_stage(
        self,
        system_message: Optional[Union[str, Message]],
    ) -> Optional[Union[str, Message]]:
        if not (self.client.enable_multi_stage_reasoning and system_message):
            return system_message
        multi_stage_instructions = (
            "\n\nMULTI-STAGE REASONING MODE:\n"
            "You can call tools iteratively to gather information. After each tool execution round, you'll be asked if you need more tools.\n"
            "\n"
            "IMPORTANT: Do NOT generate your final complete response until you're done with all tool calls.\n"
            f"- To call more tools: Simply call the tools you need (can be multiple in parallel)\n"
            f"- To finish: Respond with {self.client.multi_stage_marker} prefix, then provide your complete response\n"
            "\n"
            f"The {self.client.multi_stage_marker} marker is REQUIRED to signal completion."
        )
        if isinstance(system_message, str):
            return system_message + multi_stage_instructions
        return Message(
            role=system_message.role,
            content=str(system_message.content) + multi_stage_instructions,
        )

    def _resolve_reserved_output_tokens(self) -> int:
        try:
            return int(
                self.call_kwargs.get("max_output_tokens")
                or getattr(self.client, "max_output_tokens", None)
                or 2048
            )
        except Exception:
            return 2048

    def _normalize_tool_call(self, tool_call: Any) -> tuple[Optional[str], Optional[str], Dict[str, Any]]:
        if isinstance(tool_call, dict):
            tool_name = tool_call.get("name") or tool_call.get("type")
            tool_call_id = tool_call.get("id") or tool_call.get("tool_use_id") or tool_call.get("tool_call_id")
            tool_args = tool_call.get("args", {})
        else:
            tool_name = getattr(tool_call, "name", None)
            tool_call_id = (
                getattr(tool_call, "id", None)
                or getattr(tool_call, "tool_use_id", None)
                or getattr(tool_call, "tool_call_id", None)
            )
            tool_args = getattr(tool_call, "args", {})
            if not isinstance(tool_args, dict):
                tool_args = dict(tool_args) if hasattr(tool_args, "__dict__") else {}
        return tool_name, tool_call_id, dict(tool_args or {})

    def _tool_name_from_call(self, tool_call: Any) -> Optional[str]:
        return self._normalize_tool_call(tool_call)[0]

    @staticmethod
    def _tool_result_content(value: Any) -> Union[str, List[Dict[str, Any]]]:
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(item, (str, dict)) for item in value):
            return value
        if isinstance(value, dict):
            block_type = str(value.get("type") or "").strip().lower()
            if block_type in {"input_image", "input_file", "input_text", "text"}:
                return [value]
        return str(value)

    async def _emit(self, event: AgentLoopEvent) -> AgentLoopEvent:
        self._sequence += 1
        event.sequence = self._sequence
        event.emitted_at = time.time()
        event.agent_name = event.agent_name or self.agent_name
        event.model = event.model or self.model
        try:
            trace_ctx = get_current()
            event.trace_span_id = getattr(trace_ctx, "current_span_id", None) if trace_ctx is not None else None
        except Exception:
            event.trace_span_id = None
        self._events.append(event)
        await self._dispatch_callbacks(event)
        if self._queue is not None:
            await self._queue.put(event)
        return event

    async def _dispatch_callbacks(self, event: AgentLoopEvent) -> None:
        method_name = f"on_{event.event_type.value}"
        for callback in self.callbacks:
            if callback is None:
                continue
            try:
                if callable(callback) and not hasattr(callback, "on_event"):
                    await _maybe_await(callback(event))
                hook = getattr(callback, method_name, None)
                if callable(hook):
                    await _maybe_await(hook(event))
                generic = getattr(callback, "on_event", None)
                if callable(generic):
                    await _maybe_await(generic(event))
            except Exception as exc:
                msg = (
                    f"{type(exc).__name__} in callback {type(callback).__name__} "
                    f"for {event.event_type.value}: {exc}"
                )
                self._callback_errors.append(msg)
                if self.logger:
                    self.logger.warning(msg)


async def collect_agent_loop(runner: AgentLoopRunner, *, raise_on_error: bool = False) -> AgentLoopResult:
    return await runner.collect(raise_on_error=raise_on_error)

