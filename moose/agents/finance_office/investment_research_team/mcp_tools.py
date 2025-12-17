from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Type


@dataclass(frozen=True)
class ToolGroupSpec:
    """
    Metadata describing a tool group hierarchy used for summarization.

    `group_name` is the top-level heading (e.g., "SEC/EDGAR Tools").
    `group_description` should be concise; it will be shown to the LLM.
    `category_classes` are mixins that define the actual @mcp_tool methods and docstrings.
    """

    group_name: str
    group_description: str
    category_classes: Sequence[Type[Any]]


class CombinedFinanceMCPTools:
    """
    Convenience provider that combines EDGAR + FMP tool suites on one object.

    This is primarily used to:
    - provide a single `get_langchain_tools()` to bind into the LLM
    - expose a merged `mcp_tools` map for introspection/summarization
    - offer `iter_tool_groups()` for hierarchical tool summaries
    """

    def __init__(
        self,
        *,
        edgar: Optional[Any] = None,
        fmp: Optional[Any] = None,
        logger: Any = None,
    ):
        self.edgar = edgar
        self.fmp = fmp
        self.logger = logger
        self._langchain_tools: Optional[List[Any]] = None
        self.mcp_tools: Dict[str, Any] = {}

    def get_langchain_tools(self) -> List[Any]:
        """Return the concatenated LangChain tool list from enabled providers."""
        cached = self._langchain_tools
        if cached is not None:
            return cached

        tools: List[Any] = []
        merged: Dict[str, Any] = {}

        for provider in (self.edgar, self.fmp):
            if provider is None:
                continue
            if not hasattr(provider, "get_langchain_tools"):
                raise TypeError(f"Provider {provider!r} does not implement get_langchain_tools()")
            provider_tools = provider.get_langchain_tools()
            tools.extend(provider_tools or [])

            # Prefer provider.mcp_tools (StructuredTool objects by name) if present; otherwise derive from list.
            pt = getattr(provider, "mcp_tools", None)
            if isinstance(pt, dict):
                for k, v in pt.items():
                    merged[str(k)] = v
            else:
                for t in provider_tools or []:
                    nm = getattr(t, "name", None)
                    if nm:
                        merged[str(nm)] = t

        # Cache + expose for summarization
        self._langchain_tools = tools
        self.mcp_tools = merged
        return tools

    def iter_tool_groups(self) -> Iterator[ToolGroupSpec]:
        """
        Yield the tool hierarchy in stable, human-friendly order.

        Each yielded ToolGroupSpec includes:
        - top-level group heading + description
        - category classes (mixins) that define the tools and whose class docstrings explain the category
        """
        if self.edgar is not None:
            from .edgar_mcp_tools import EdgarAllMCPTools  # local import to avoid eager dependency

            desc = (getattr(EdgarAllMCPTools, "__doc__", "") or "").strip()
            yield ToolGroupSpec(
                group_name="SEC/EDGAR Tools",
                group_description=desc,
                category_classes=tuple(EdgarAllMCPTools.__bases__),
            )

        if self.fmp is not None:
            from .fmp_mcp_tools import FMPAllMCPTools  # local import to avoid eager dependency

            desc = (getattr(FMPAllMCPTools, "__doc__", "") or "").strip()
            yield ToolGroupSpec(
                group_name="FMP (FinancialModelingPrep) Tools",
                group_description=desc,
                category_classes=tuple(FMPAllMCPTools.__bases__),
            )

    async def close(self) -> None:
        """Compatibility no-op (delegates close to underlying providers when present)."""
        for provider in (self.edgar, self.fmp):
            if provider is None:
                continue
            close_fn = getattr(provider, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception:
                    # Best-effort cleanup; do not raise.
                    pass


