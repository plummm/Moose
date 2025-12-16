"""
SEC/EDGAR MCP tools.

This package groups MCP-exposed tools for extracting and analyzing **SEC filings** via EDGAR
(e.g., 10-K/10-Q/8-K, exhibits, XBRL statements, ownership forms) and provides a convenience
aggregator (`EdgarAllMCPTools`) for host-side orchestration.
"""

from typing import Any, Dict, List

from .basic import EdgarMCPTools, mcp_envelope_err, mcp_envelope_ok, mcp_json_safe, mcp_tool
from .catalysts import CompanyUpdatesMCPTools
from .financing import FinancingMCPTools
from .fund_voting import FundVotingMCPTools
from .insiders import InsiderTradeMCPTools
from .institutional import InstitutionalHoldingsMCPTools
from .reporting import CompanyReportingMCPTools
from .statements import FinancialStatementsMCPTools

try:
    from langchain_core.tools import StructuredTool
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover
    StructuredTool = None
    LANGCHAIN_TOOLS_AVAILABLE = False

__all__ = [
    "EdgarMCPTools",
    "mcp_json_safe",
    "mcp_envelope_ok",
    "mcp_envelope_err",
    "mcp_tool",
    "CompanyUpdatesMCPTools",
    "InsiderTradeMCPTools",
    "InstitutionalHoldingsMCPTools",
    "FundVotingMCPTools",
    "FinancingMCPTools",
    "CompanyReportingMCPTools",
    "FinancialStatementsMCPTools",
    "EdgarAllMCPTools",
    "AllMCPTools",
]

class EdgarAllMCPTools(
    FinancialStatementsMCPTools,
    CompanyUpdatesMCPTools,
    InsiderTradeMCPTools,
    InstitutionalHoldingsMCPTools,
    FundVotingMCPTools,
    FinancingMCPTools,
    CompanyReportingMCPTools,
):
    """
    SEC/EDGAR tool suite for filings extraction and research.

    Category coverage (via mixins):
    - XBRL financial statements (income statement, balance sheet, cash flow)
    - Material updates / catalysts (8-K + exhibits like EX-99.*)
    - Insider trading (Forms 3/4/5)
    - Institutional ownership (13F holdings, crowded trades)
    - Fund proxy voting (N-PX filings)
    - Financing / dilution filings (S-1/S-3/424B* and related)
    - Reporting / governance / ownership-change analysis (10-K/10-Q/6-K/DEF 14A/13D/13G/NT/Reg CF)
    """

    def get_langchain_tools(self) -> List[Any]:
        """
        Create LangChain `StructuredTool` wrappers for all inherited `@mcp_tool` methods.

        This is built via reflection (see `EdgarMCPTools.list_mcp_tools()`), so adding new
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
                description=doc or f"Edgar tool: {name}",
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


# Backward-compatible alias (older docs referenced `AllMCPTools`)
AllMCPTools = EdgarAllMCPTools


