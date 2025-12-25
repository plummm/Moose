"""Investment Research team lead (router) backed by an LLM."""

from pathlib import Path
from typing import Dict, Any, Optional, List, Iterable, Set, Tuple, Type, Mapping
from datetime import datetime
import json

from moose.framework.llm_core import LLMClient
LLM_AVAILABLE = True

try:
    from langchain_core.tools import StructuredTool
    LANGCHAIN_TOOLS_AVAILABLE = True
except ImportError:
    StructuredTool = None
    LANGCHAIN_TOOLS_AVAILABLE = False


class ResearchLead:
    """
    Team lead for the Investment Research team.
    
    This class owns the Investment Research team LangGraph workflow and provides the official invocation method
    (`run_task`) for arbitrary investment research tasks. News-specific analysis is handled at the department
    level (see `finance_office/assistant.py`) so the team remains generic.
    """
    
    def __init__(
        self,
        model: Optional[str] = None,
        temperature: float = 0.7,
        logger=None,
        sec_data_tools=None,
        enable_multi_stage_reasoning: bool = False,
        max_tool_iterations: int = 20,
        agent_name: Optional[str] = None,
        custom_config: Optional[Dict[str, Any]] = None,
        **llm_kwargs
    ):
        """
        Initialize the financial news analyzer.
        
        Args:
            model: LLM model name (e.g., "gpt-4", "claude-3-opus-20240229")
            temperature: Sampling temperature for LLM
            logger: Logger instance
            sec_data_tools: Optional SEC tools provider instance (e.g. EdgarMCPTools) for SEC data access
            enable_multi_stage_reasoning: Enable planner/executor loop for iterative tool calling
            max_tool_iterations: Maximum number of tool call iterations
            **llm_kwargs: Additional arguments for LLMClient
        """
        if not LLM_AVAILABLE:
            raise ImportError(
                "LLM support not available. Install with: "
                "pip install langchain langchain-openai langchain-anthropic langchain-google-genai"
            )
        
        # Derive configs from agent custom_config (single source of truth)
        derived_base_cfg: Dict[str, Any] = {}
        derived_node_cfgs: Dict[str, Dict[str, Any]] = {}
        if isinstance(custom_config, dict):
            base = custom_config.get("llm_config")
            if isinstance(base, dict):
                derived_base_cfg = dict(base)
            # Collect per-node llm_config overrides: custom.<node_name>_llm_config
            for k, v in (custom_config or {}).items():
                if not isinstance(k, str):
                    continue
                if k == "llm_config":
                    continue
                if not k.endswith("_llm_config"):
                    continue
                if isinstance(v, dict):
                    node_name = k[: -len("_llm_config")]
                    if node_name:
                        derived_node_cfgs[node_name] = dict(v)

        # Base llm_config (required: model)
        base_cfg: Dict[str, Any] = dict(derived_base_cfg or {})
        if not base_cfg:
            base_cfg = {
                "model": model,
                "temperature": temperature,
                "enable_multi_stage_reasoning": enable_multi_stage_reasoning,
                "max_tool_iterations": max_tool_iterations,
                "kwargs": dict(llm_kwargs or {}),
            }
        # Merge kwargs passed explicitly into base kwargs (explicit wins)
        base_kwargs = base_cfg.get("kwargs") if isinstance(base_cfg.get("kwargs"), dict) else {}
        merged_base_kwargs = dict(base_kwargs or {})
        merged_base_kwargs.update(dict(llm_kwargs or {}))
        base_cfg["kwargs"] = merged_base_kwargs

        base_model = str(base_cfg.get("model") or "").strip()
        if not base_model:
            raise ValueError("Missing required config: custom.llm_config.model must be set")

        self.base_llm_config: Dict[str, Any] = base_cfg
        self.node_llm_configs: Dict[str, Dict[str, Any]] = dict(derived_node_cfgs or {})

        self.model = base_model
        self.temperature = float(base_cfg.get("temperature", temperature) or temperature)
        self.logger = logger
        self.sec_data_tools = sec_data_tools
        self.mcp_tools: Dict[str, Any] = {}
        # Main agent name for cost/token attribution in llm.log (rolls up sub-agent usage)
        self.agent_name = str(agent_name or "").strip() or None
        
        # Get tools from the SEC tools provider if provided
        tools: Optional[List[Any]] = None
        if sec_data_tools:
            try:
                tools = sec_data_tools.get_langchain_tools()
                if self.logger:
                    self.logger.info(f"Loaded {len(tools)} SEC tools")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to load SEC tools: {e}")
                tools = None
        
        self.llm_client = LLMClient(
            model=self.model,
            temperature=self.temperature,
            tools=tools,
            enable_multi_stage_reasoning=bool(base_cfg.get("enable_multi_stage_reasoning", enable_multi_stage_reasoning)),
            max_tool_iterations=int(base_cfg.get("max_tool_iterations", max_tool_iterations) or max_tool_iterations),
            agent_name=self.agent_name,
            **(merged_base_kwargs or {})
        )
        
        if self.logger:
            self.logger.info(f"Initialized ResearchLead with model: {self.model}")
            if tools:
                self.logger.info(f"SEC data tools enabled: {len(tools)} tools available")

        # Investment Research team LangGraph app is owned/created by the team lead (this class) only.
        self._team_workflow_app: Any = None

    def get_node_llm_config(self, node_name: str) -> Dict[str, Any]:
        """
        Return effective llm_config for a given workflow node.

        Resolution:
        - start from `custom.llm_config`
        - overlay `custom.<node_name>_llm_config` if provided
        - merge kwargs dicts (base kwargs + node kwargs)
        """
        node_name = str(node_name or "").strip()
        base = dict(self.base_llm_config or {})
        base_kwargs = base.get("kwargs") if isinstance(base.get("kwargs"), dict) else {}

        override = dict(self.node_llm_configs.get(node_name) or {})
        override_kwargs = override.get("kwargs") if isinstance(override.get("kwargs"), dict) else {}

        out = dict(base)
        out.update({k: v for k, v in override.items() if k != "kwargs"})
        merged_kwargs = dict(base_kwargs or {})
        merged_kwargs.update(dict(override_kwargs or {}))
        out["kwargs"] = merged_kwargs

        model = str(out.get("model") or "").strip()
        if not model:
            raise ValueError(f"Missing required model for node {node_name}: custom.llm_config.model must be set")
        out["model"] = model
        return out

    @staticmethod
    def build_tool_scopes() -> Dict[str, Set[str]]:
        """
        Define tool-name sets per specialist agent using category classes' list_mcp_tools().
        """

        def _tool_names_from_specs(specs: Iterable[Mapping[str, Any]]) -> Set[str]:
            out: Set[str] = set()
            for s in specs or []:
                nm = (s or {}).get("name")
                if isinstance(nm, str) and nm:
                    out.add(nm)
            return out

        # Import locally to avoid import-time coupling.
        from .edgar_mcp_tools import EdgarAllMCPTools
        from .fmp_mcp_tools import (
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
        )

        scopes: Dict[str, Set[str]] = {}
        scopes["edgar"] = _tool_names_from_specs(EdgarAllMCPTools.list_mcp_tools())
        scopes["fmp_news"] = _tool_names_from_specs(NewsMCPTools.list_mcp_tools())
        scopes["fmp_fundamentals"] = (
            _tool_names_from_specs(FinanceMCPTools.list_mcp_tools())
            | _tool_names_from_specs(CompanyMCPTools.list_mcp_tools())
            | _tool_names_from_specs(AnalystMCPTools.list_mcp_tools())
            | _tool_names_from_specs(CalendarMCPTools.list_mcp_tools())
        )
        scopes["fmp_macro"] = _tool_names_from_specs(EconomicsMCPTools.list_mcp_tools()) | _tool_names_from_specs(
            MarketMCPTools.list_mcp_tools()
        )
        scopes["fmp_price"] = (
            _tool_names_from_specs(QuoteMCPTools.list_mcp_tools())
            | _tool_names_from_specs(ChartMCPTools.list_mcp_tools())
            | _tool_names_from_specs(IndicatorMCPTools.list_mcp_tools())
        )
        return scopes

    # Note: Edgar LangChain tool creation now lives in EdgarMCPTools.get_langchain_tools()

    def _get_team_workflow_app(self) -> Any:
        """
        Lazily create and cache the Investment Research team workflow app.
        """
        if self._team_workflow_app is None:
            # Inline compatibility shim (previously `team_workflow.py`).
            from moose.framework.logging import get_global_debug
            from .workflow.workflow import InvestmentResearchWorkflow

            # Follow the same debug-mode behavior as before (global debug → sequential specialists).
            debug_mode = False
            try:
                debug_mode = bool(get_global_debug())
            except Exception:
                debug_mode = False

            self._team_workflow_app = InvestmentResearchWorkflow(
                analyzer=self,
                logger=self.logger,
                debug_mode=debug_mode,
            ).compile()
        return self._team_workflow_app
    
    # News-specific analysis is handled by `moose.agents.finance_office.assistant.FinanceOfficeAssistant`.

    async def run_task(
        self,
        task_instruction: str,
        *,
        context_text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        merge_system_message: Optional[str] = None,
        merge_user_message: Optional[str] = None,
        additional_states: Optional[Dict[str, Any]] = {},
    ) -> Dict[str, Any]:
        """
        Run a general investment research task via the Investment Research team workflow.

        This uses the same *output schema* as `analyze_article()` (title/high_level_idea/companies/sentiment/...)
        so callers can rely on a consistent shape across task types.
        """
        # Pass merge prompts through as-is. If empty, the workflow will route to `prompt_engineer`.
        merge_system_message = str(merge_system_message or "")
        merge_user_message = str(merge_user_message or "")

        team_app = self._get_team_workflow_app()
        team_state = {
            "task_instruction": task_instruction,
            "metadata": metadata or {},
            "context_text": context_text,
            "merge_system_message": merge_system_message,
            "merge_user_message": merge_user_message,
            # future prompt-generator tool can use this
            **(additional_states),
        }
        team_out = await team_app.ainvoke(team_state) if hasattr(team_app, "ainvoke") else team_app.invoke(team_state)
        final = {}
        if isinstance(team_out, dict):
            final = team_out.get("final") if isinstance(team_out.get("final"), dict) else {}

        ok_flag = True
        if isinstance(final, dict) and isinstance(final.get("ok"), bool):
            ok_flag = bool(final.get("ok"))

        err_val = None
        if isinstance(final, dict):
            err_val = final.get("error")
        if isinstance(err_val, dict):
            try:
                err_val = json.dumps(err_val, ensure_ascii=False)
            except Exception:
                err_val = str(err_val)
        elif err_val is not None and not isinstance(err_val, str):
            err_val = str(err_val)

        return {
            "status": "success" if ok_flag else "error",
            "error": None if ok_flag else (err_val or "unknown_error"),
            "result": final if isinstance(final, dict) else {},
            "last_state": team_out if isinstance(team_out, dict) else {},
            # Convenience passthroughs for upstream callers (e.g., finance_office) so they don't have to dig into last_state.
            "llm_usage_total": (team_out.get("llm_usage_total") if isinstance(team_out, dict) else None),
            "llm_cost_total": (team_out.get("llm_cost_total") if isinstance(team_out, dict) else None),
        }
    
    def _summarize_tools(self, *, agent_name: Optional[str] = None) -> str:
        """
        Summarize tools available to the LLM (grouped by category).

        If `agent_name` is provided, only include tools that are in that specialist's scope.
        """

        def _first_paragraph(s: str, *, max_chars: int = 380) -> str:
            txt = (s or "").strip()
            if not txt:
                return ""
            # Take up to the first blank-line-separated paragraph
            para = txt.split("\n\n", 1)[0].strip()
            # Collapse internal newlines for readability
            para = " ".join([ln.strip() for ln in para.splitlines() if ln.strip()])
            if len(para) > max_chars:
                para = para[: max_chars - 1].rstrip() + "…"
            return para

        def _available_tool_names() -> Set[str]:
            names: Set[str] = set()
            for t in getattr(self.llm_client, "tools", []) or []:
                nm = getattr(t, "name", None)
                if isinstance(nm, str) and nm:
                    names.add(nm)
            return names

        # Store tools reference if available (for external inspection / executor lookup)
        if self.sec_data_tools and hasattr(self.sec_data_tools, "mcp_tools"):
            try:
                for tool_name, tool_obj in (self.sec_data_tools.mcp_tools or {}).items():
                    self.mcp_tools[str(tool_name)] = tool_obj
            except Exception:
                pass

        available = _available_tool_names()
        if not available:
            return ""

        if agent_name:
            try:
                scopes = self.build_tool_scopes()
                allowed = set(scopes.get(str(agent_name), set()) or set())
                available = available & allowed
            except Exception:
                # If we can't resolve scopes for some reason, fall back to showing all available.
                pass

        if not available:
            return ""

        # Determine grouping metadata
        tool_groups: List[Tuple[str, str, Iterable[Type[Any]]]] = []

        if self.sec_data_tools is not None and hasattr(self.sec_data_tools, "iter_tool_groups"):
            # Preferred: combined provider exposes stable hierarchy metadata.
            try:
                for g in self.sec_data_tools.iter_tool_groups():
                    tool_groups.append(
                        (
                            str(getattr(g, "group_name", "") or ""),
                            str(getattr(g, "group_description", "") or ""),
                            getattr(g, "category_classes", []) or [],
                        )
                    )
            except Exception:
                tool_groups = []

        # Fallback: support passing a single provider (EDGAR-only or FMP-only) directly.
        if not tool_groups and self.sec_data_tools is not None:
            try:
                from .edgar_mcp_tools import EdgarAllMCPTools
            except Exception:
                EdgarAllMCPTools = None  # type: ignore
            try:
                from .fmp_mcp_tools import FMPAllMCPTools
            except Exception:
                FMPAllMCPTools = None  # type: ignore

            if EdgarAllMCPTools is not None and isinstance(self.sec_data_tools, EdgarAllMCPTools):  # type: ignore[arg-type]
                tool_groups.append(
                    (
                        "SEC/EDGAR Tools",
                        str(getattr(EdgarAllMCPTools, "__doc__", "") or ""),
                        tuple(EdgarAllMCPTools.__bases__),
                    )
                )
            if FMPAllMCPTools is not None and isinstance(self.sec_data_tools, FMPAllMCPTools):  # type: ignore[arg-type]
                tool_groups.append(
                    (
                        "FMP (FinancialModelingPrep) Tools",
                        str(getattr(FMPAllMCPTools, "__doc__", "") or ""),
                        tuple(FMPAllMCPTools.__bases__),
                    )
                )

        if not tool_groups:
            return ""

        lines: List[str] = []
        lines.append("")
        if agent_name:
            lines.append(f"**Available Tools (scoped for `{agent_name}`):**")
            lines.append("Only tools bound to this specialist are listed below. Prefer the most relevant category first.")
        else:
            lines.append("**Available Tools:**")
            lines.append(
                "You have access to SEC filing tools and market/company data tools. Tools are grouped by category; use the most relevant category first."
            )
        lines.append("")

        # Render each top-level group in order.
        for group_name, group_desc, category_classes in tool_groups:
            # Compute unique tool names in this group, intersected with actually bound tools.
            group_tool_names: Set[str] = set()
            cat_to_tools: List[Tuple[str, List[Tuple[str, str]]]] = []

            for cls in category_classes:
                # Category description comes from class docstring (revised as part of this change).
                cat_desc = _first_paragraph(str(getattr(cls, "__doc__", "") or ""))

                # Extract tool specs from the category class
                specs = []
                try:
                    if hasattr(cls, "list_mcp_tools"):
                        specs = cls.list_mcp_tools()  # type: ignore[attr-defined]
                except Exception:
                    specs = []

                items: List[Tuple[str, str]] = []
                for spec in specs or []:
                    nm = (spec or {}).get("name")
                    doc = (spec or {}).get("doc")
                    if not isinstance(nm, str) or not nm:
                        continue
                    if nm not in available:
                        continue
                    group_tool_names.add(nm)
                    items.append((nm, _first_paragraph(str(doc or ""), max_chars=220)))

                # Stable sort tools by name
                items.sort(key=lambda x: x[0])
                if items:
                    cat_to_tools.append((cat_desc or cls.__name__, items))

            if not group_tool_names:
                continue

            header_desc = _first_paragraph(group_desc, max_chars=420)
            if header_desc:
                lines.append(f"**{group_name} ({len(group_tool_names)} tools):**: {header_desc}")
            else:
                lines.append(f"**{group_name} ({len(group_tool_names)} tools):**")

            for cat_desc, items in cat_to_tools:
                lines.append(f"- {cat_desc}:")
                for nm, desc in items:
                    if desc:
                        lines.append(f"   - {nm}: {desc}")
                    else:
                        lines.append(f"   - {nm}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
