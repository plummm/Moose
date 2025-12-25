"""
FMP (FinancialModelingPrep) MCP tools package.

This package groups MCP-exposed tools for the FinancialModelingPrep (FMP) API by category and
provides a `FMPAllMCPTools` aggregator for convenient use in host-side orchestration.

FMP tools are **general-purpose market/company/macro** data helpers (quotes, news, calendars,
fundamentals, ratios, technical indicators, and macro datasets). In contrast, EDGAR tools focus
on **SEC filing extraction**.
"""

from typing import Any, Dict, List

from .analyst import AnalystMCPTools
from .basic import FMPMCPTools, mcp_tool
from .calendar import CalendarMCPTools
from .chart import ChartMCPTools
from .company import CompanyMCPTools
from .economics import EconomicsMCPTools
from .finance import FinanceMCPTools
from .indicator import IndicatorMCPTools
from .market import MarketMCPTools
from .news import NewsMCPTools
from .quote import QuoteMCPTools

try:
    from langchain_core.tools import StructuredTool
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    StructuredTool = None
    LANGCHAIN_TOOLS_AVAILABLE = False

__all__ = [
    "AnalystMCPTools",
    "FMPMCPTools",
    "mcp_tool",
    "CalendarMCPTools",
    "ChartMCPTools",
    "CompanyMCPTools",
    "EconomicsMCPTools",
    "FinanceMCPTools",
    "IndicatorMCPTools",
    "MarketMCPTools",
    "NewsMCPTools",
    "QuoteMCPTools",
    "FMPAllMCPTools",
]


class FMPAllMCPTools(
    AnalystMCPTools,
    CalendarMCPTools,
    ChartMCPTools,
    CompanyMCPTools,
    EconomicsMCPTools,
    FinanceMCPTools,
    IndicatorMCPTools,
    MarketMCPTools,
    NewsMCPTools,
    QuoteMCPTools,
):
    """
    FMP tool suite for general market + company + macro research.

    Category coverage (via mixins):
    - Quotes and price change snapshots
    - Company fundamentals / people / governance-style endpoints
    - Financial metrics, ratios, and growth series
    - News search (stock + crypto)
    - Market sector/industry performance and valuation snapshots/series
    - Technical indicators and chart time series
    - Earnings/IPO/corporate action calendars and other event schedules
    - Macro/economics datasets (rates, indicators, risk premia)

    Note: Category classes should avoid defining __init__; initialization is handled
    by the shared base class (`FMPMCPTools`) via normal Python MRO.
    """

    def get_langchain_tools(self, *, meeting_room_enabled: bool = False) -> List[Any]:
        """
        Create LangChain `StructuredTool` wrappers for all inherited `@mcp_tool` methods.

        This is built via reflection (see `FMPMCPTools.list_mcp_tools()`), so adding new
        `@mcp_tool` methods to any inherited category class automatically shows up here.
        """
        cache = getattr(self, "_langchain_tools_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_langchain_tools_cache", cache)
        cached = cache.get(bool(meeting_room_enabled))
        if cached is not None:
            return cached

        if not LANGCHAIN_TOOLS_AVAILABLE or StructuredTool is None:
            raise ImportError(
                "LangChain tools not available. Install with: pip install langchain-core"
            )

        tools: List[Any] = []
        m: Dict[str, Any] = {}

        for spec in self.__class__.list_mcp_tools():
            if bool(spec.get("meeting_room_only")) and not bool(meeting_room_enabled):
                continue
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
        cache[bool(meeting_room_enabled)] = tools
        return tools

    async def close(self) -> None:
        """Compatibility no-op (no external MCP client to close)."""
        return
