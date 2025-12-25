from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


_CURRENT_RUNTIME: contextvars.ContextVar["ToolRuntime | None"] = contextvars.ContextVar(
    "moose_tool_runtime_current",
    default=None,
)


@dataclass(frozen=True)
class ToolSpan:
    span_id: str
    tool_name: str
    parent_span_id: Optional[str]
    depth: int


class ToolRuntime:
    """
    Per-request runtime for tool execution.

    Key features:
    - Tools can call other tools via `await runtime.call_tool(...)`
    - Nested calls are INTERNAL to the runtime (not emitted as LLM ToolMessages)
    - Shared lifecycle (start/close) + shared tracing and safety guardrails
    """

    def __init__(
        self,
        *,
        tool_map: Dict[str, Any],
        invoke_tool: Callable[[Any, str, Dict[str, Any]], Awaitable[Any]],
        request_id: str,
        agent_name: Optional[str],
        logger: Any,
        max_depth: int = 6,
        per_call_timeout_s: Optional[float] = 60.0,
    ) -> None:
        self.tool_map = tool_map
        self._invoke_tool = invoke_tool
        self.request_id = request_id
        self.agent_name = agent_name
        self.logger = logger
        self.max_depth = int(max_depth)
        self.per_call_timeout_s = per_call_timeout_s

        self._active: set[str] = set()
        self._depth: int = 0

        # Accumulates LLM usage/cost incurred "externally" during this request (e.g., meeting-room helper calls).
        self.external_cost: float = 0.0
        self.external_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def add_external_llm_usage(
        self,
        *,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[float] = None,
    ) -> None:
        """
        Add LLM usage/cost that occurred outside the current LLMClient request but should be attributed to it.
        """
        if isinstance(cost, (int, float)):
            self.external_cost += float(cost)

        if isinstance(usage, dict):
            self.external_usage["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            self.external_usage["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            self.external_usage["total_tokens"] += int(usage.get("total_tokens", 0) or 0)

    async def start(self) -> None:
        """Optional hook for initializing shared resources (no-op by default)."""
        return None

    async def close(self) -> None:
        """Optional hook for cleaning up shared resources (no-op by default)."""
        return None

    @staticmethod
    def current() -> "ToolRuntime | None":
        """Return the current runtime if running inside a tool execution context."""
        return _CURRENT_RUNTIME.get()

    async def call_tool(
        self,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        parent_span_id: Optional[str] = None,
    ) -> Any:
        """
        Call another tool from within a tool.

        Notes:
        - Nested calls are NOT added to the LLM conversation.
        - This method enforces recursion depth and cycle detection.
        """
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("Tool name is required")

        if tool_name not in self.tool_map:
            raise KeyError(f"Tool '{tool_name}' not found")

        if self._depth >= self.max_depth:
            raise RuntimeError(f"Max tool nesting depth exceeded (max_depth={self.max_depth})")

        # Simple cycle detection: prevent re-entering the same tool name in the active stack.
        if tool_name in self._active:
            raise RuntimeError(f"Tool call cycle detected: '{tool_name}' is already active")

        span = ToolSpan(
            span_id=str(uuid.uuid4()),
            tool_name=tool_name,
            parent_span_id=parent_span_id,
            depth=self._depth + 1,
        )

        tool = self.tool_map[tool_name]
        tool_args = args if isinstance(args, dict) else {}

        token = _CURRENT_RUNTIME.set(self)
        self._active.add(tool_name)
        self._depth += 1
        t0 = time.perf_counter()
        try:
            if self.logger:
                self.logger.debug(
                    f"[tool_runtime] start span={span.span_id} parent={span.parent_span_id} "
                    f"depth={span.depth} tool={tool_name} args_keys={sorted(tool_args.keys())}"
                )

            coro = self._invoke_tool(tool, tool_name, tool_args)
            if self.per_call_timeout_s is not None:
                result = await asyncio.wait_for(coro, timeout=float(self.per_call_timeout_s))
            else:
                result = await coro

            if self.logger:
                dt_ms = (time.perf_counter() - t0) * 1000.0
                self.logger.debug(
                    f"[tool_runtime] end span={span.span_id} depth={span.depth} tool={tool_name} "
                    f"ms={dt_ms:.1f}"
                )
            return result
        except Exception as e:
            if self.logger:
                dt_ms = (time.perf_counter() - t0) * 1000.0
                self.logger.warning(
                    f"[tool_runtime] error span={span.span_id} depth={span.depth} tool={tool_name} "
                    f"ms={dt_ms:.1f} err={e}"
                )
            raise
        finally:
            self._depth = max(0, self._depth - 1)
            self._active.discard(tool_name)
            _CURRENT_RUNTIME.reset(token)


