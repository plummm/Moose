"""
FMP (FinancialModelingPrep) MCP tools package.

This package groups MCP-exposed tools for FinancialModelingPrep API by category 
and provides a `FMPAllMCPTools` aggregator for convenient use in host-side orchestration.
"""

from typing import Any, Dict, List

from .basic import FMPMCPTools, mcp_tool
from .company_basic import CompanyBasicMCPTools

try:
    from langchain_core.tools import StructuredTool
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    StructuredTool = None
    LANGCHAIN_TOOLS_AVAILABLE = False

__all__ = [
    "FMPMCPTools",
    "mcp_tool",
    "CompanyBasicMCPTools",
    "FMPAllMCPTools",
]


class FMPAllMCPTools(
    CompanyBasicMCPTools,
):
    """
    Convenience aggregator that exposes all FMP category tools on a single object.

    Note: Category classes should avoid defining __init__; initialization is handled
    by the shared base class (`FMPMCPTools`) via normal Python MRO.
    """

    def get_langchain_tools(self) -> List[Any]:
        """
        Create LangChain `StructuredTool` wrappers for all inherited `@mcp_tool` methods.

        This is built via reflection (see `FMPMCPTools.list_mcp_tools()`), so adding new
        `@mcp_tool` methods to any inherited category class automatically shows up here.
        """
        # Cache
        cached = getattr(self, "_langchain_tools", None)
        if cached is not None:
            return cached

        if not LANGCHAIN_TOOLS_AVAILABLE or StructuredTool is None:
            raise ImportError(
                "LangChain tools not available. Install with: pip install langchain-core"
            )

        tools: List[Any] = []
        m: Dict[str, Any] = {}

        for spec in self.__class__.list_mcp_tools():
            name = spec.get("name")
            method_name = spec.get("method_name")
            doc = (spec.get("doc") or "").strip()
            if not name or not method_name:
                continue

            fn = getattr(self, method_name, None)
            if not callable(fn):
                continue

            tool = StructuredTool.from_function(
                func=fn,
                name=name,
                description=doc or f"FMP tool: {name}",
            )
            tools.append(tool)
            m[name] = tool

        # Expose for summarization / external inspection
        setattr(self, "mcp_tools", m)
        setattr(self, "_langchain_tools", tools)
        return tools

    async def close(self) -> None:
        """Compatibility no-op (no external MCP client to close)."""
        return
