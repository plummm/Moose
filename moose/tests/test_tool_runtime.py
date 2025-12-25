import asyncio

from moose.framework.llm_core.tool_runtime import ToolRuntime


def test_tool_runtime_nested_calls_work():
    async def tool_b(x: int) -> int:
        return x + 1

    async def tool_a(x: int) -> int:
        rt = ToolRuntime.current()
        assert rt is not None
        y = await rt.call_tool("tool_b", {"x": x})
        return int(y) * 2

    async def invoke_tool(tool, _name: str, args: dict):
        # Minimal executor for tests: call async callables with kwargs.
        return await tool(**args)

    async def run():
        rt = ToolRuntime(
            tool_map={"tool_a": tool_a, "tool_b": tool_b},
            invoke_tool=invoke_tool,
            request_id="test",
            agent_name="test",
            logger=None,
            max_depth=4,
            per_call_timeout_s=2.0,
        )
        out = await rt.call_tool("tool_a", {"x": 10})
        assert out == 22  # (10 + 1) * 2

    asyncio.run(run())


def test_tool_runtime_external_usage_accumulates():
    async def invoke_tool(tool, _name: str, args: dict):
        return await tool(**args)

    async def noop_tool() -> None:
        return None

    async def run():
        rt = ToolRuntime(
            tool_map={"noop_tool": noop_tool},
            invoke_tool=invoke_tool,
            request_id="test",
            agent_name="test",
            logger=None,
            max_depth=2,
            per_call_timeout_s=1.0,
        )
        rt.add_external_llm_usage(usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}, cost=0.01)
        rt.add_external_llm_usage(usage={"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}, cost=0.02)
        assert abs(rt.external_cost - 0.03) < 1e-9
        assert rt.external_usage["input_tokens"] == 5
        assert rt.external_usage["output_tokens"] == 7
        assert rt.external_usage["total_tokens"] == 12

    asyncio.run(run())


