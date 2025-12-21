from __future__ import annotations

from typing import Any, Dict, Optional, List

from moose.framework.llm_core import LLMClient


class BaseNode:
    """
    Shared base for investment_research_team LangGraph nodes.

    Standardizes:
    - access to analyzer + logger
    - main_agent_name attribution
    - `_node_cfg(node_name)` resolution via ResearchLead.get_node_llm_config
    """

    node_name: str = ""

    def __init__(self, *, analyzer: Any, logger: Any):
        self.analyzer = analyzer
        self.logger = logger
        self.main_agent_name = str(getattr(analyzer, "agent_name", "") or "").strip() or None

    def _node_cfg(self, node_name: Optional[str] = None) -> Dict[str, Any]:
        node_name = str(node_name or self.node_name or "").strip()
        if hasattr(self.analyzer, "get_node_llm_config"):
            return self.analyzer.get_node_llm_config(node_name)  # type: ignore[attr-defined]
        model = str(getattr(self.analyzer, "model", "") or "").strip()
        if not model:
            raise ValueError("Analyzer model is not set; custom.llm_config.model must be configured")
        return {"model": model, "temperature": float(getattr(self.analyzer, "temperature", 0.7)), "kwargs": {}}

    def _build_agent_client(self, *, node_name: Optional[str] = None, tools: Optional[List[Any]] = None) -> LLMClient:
        cfg = self._node_cfg(node_name)
        model = str(cfg.get("model") or "").strip()
        if not model:
            effective_node_name = str(node_name or self.node_name or "").strip() or "<unknown>"
            raise ValueError(
                "Missing required model for LLMClient in investment_research_team "
                f"(node={effective_node_name}). Configure `custom.llm_config.model` "
                "and/or `custom.<node>_llm_config.model`."
            )
        return LLMClient(
            model=model,
            temperature=float(cfg.get("temperature", 0.7)),
            tools=list(tools or []),
            enable_multi_stage_reasoning=False,
            agent_name=self.main_agent_name,
            **(cfg.get("kwargs") or {}),
        )


