from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.graph import END, StateGraph

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from .nodes.team_route import TeamRouteNode
from .nodes.ticker_memory_loader import TickerMemoryLoaderNode
from .nodes.prompt_engineer import PromptEngineerNode
from .nodes.specialists_runner import SpecialistsRunnerNode
from .nodes.team_merge import TeamMergeNode
from .nodes.monthly_memory_writer import MonthlyMemoryWriterNode
from .nodes.utils import normalize_usage, raw_snapshot
import inspect


def load_playbooks(playbooks_path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise ImportError("PyYAML is required to load playbooks.yaml")
    with open(playbooks_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Invalid playbooks YAML")
    return data


class InvestmentResearchWorkflow:
    """
    Builds and compiles the investment_research_team LangGraph.

    This class is intentionally small: node logic lives in `investment_research_team/workflow/nodes/*`.
    """

    def __init__(self, *, analyzer: Any, logger: Any, debug_mode: bool = False):
        self.analyzer = analyzer
        self.logger = logger
        self.debug_mode = bool(debug_mode)
        
        # Read per_ticker_merge_mode from config (default: True for new mode)
        config = getattr(analyzer, "config", {}) if analyzer else {}
        custom_config = config.get("custom", {}) if isinstance(config, dict) else {}
        self.per_ticker_merge_mode = custom_config.get("per_ticker_merge_mode", True)

        playbooks_path = Path(__file__).resolve().parents[1] / "playbooks.yaml"
        self.playbooks = load_playbooks(playbooks_path)

        # Node instances
        self.team_route = TeamRouteNode(analyzer=analyzer, logger=logger, playbooks=self.playbooks)
        self.load_ticker_memory = TickerMemoryLoaderNode(analyzer=analyzer, logger=logger)
        self.prompt_engineer = PromptEngineerNode(analyzer=analyzer, logger=logger)
        self.run_selected_specialists_parallel = SpecialistsRunnerNode(analyzer=analyzer, logger=logger, debug_mode=debug_mode)
        self.team_merge = TeamMergeNode(analyzer=analyzer, logger=logger)
        self.write_monthly_memory = MonthlyMemoryWriterNode(analyzer=analyzer, logger=logger)

        # Compiled per-ticker subgraph app (built lazily in compile()).
        self._per_ticker_app: Optional[Any] = None

    def _compile_per_ticker_app(self) -> Any:
        """
        Per-ticker subgraph.

        Contract:
        - expects state.current_ticker ("" means macro/economy mode)
        - consumes routing/agent_tasks/specialist_clients from team_route
        - produces state.final (single-run envelope from team_merge)
        """
        g = StateGraph(dict)

        def _wrap_node(node_name: str, fn: Any) -> Any:
            async def _wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
                from moose.framework.logging.tracing import span as trace_span

                with trace_span(
                    kind="workflow.node",
                    name=f"investment_research_team.{node_name}",
                    attrs={"workflow": "investment_research_team", "node": node_name},
                ):
                    if inspect.iscoroutinefunction(fn):
                        return await fn(state)
                    out = fn(state)
                    if inspect.isawaitable(out):
                        return await out
                    return out

            return _wrapped

        g.add_node("load_ticker_memory", _wrap_node("load_ticker_memory", self.load_ticker_memory.run))
        g.add_node("prompt_engineer", _wrap_node("prompt_engineer", self.prompt_engineer.run))
        g.add_node(
            "run_selected_specialists_parallel",
            _wrap_node("run_selected_specialists_parallel", self.run_selected_specialists_parallel.run),
        )
        g.add_node("team_merge", _wrap_node("team_merge", self.team_merge.run))
        g.add_node("write_monthly_memory", _wrap_node("write_monthly_memory", self.write_monthly_memory.run))

        def _route_after_load_ticker_memory(state: Dict[str, Any]) -> str:
            sm = str(state.get("merge_system_message") or "").strip()
            um = str(state.get("merge_user_message") or "").strip()
            return "run_selected_specialists_parallel" if (sm and um) else "prompt_engineer"

        def _route_after_prompt_engineer(state: Dict[str, Any]) -> str:
            if state.get("abort"):
                return "end"
            return "run_selected_specialists_parallel"

        def _route_after_team_merge(state: Dict[str, Any]) -> str:
            routing = state.get("routing", {}) if isinstance(state.get("routing"), dict) else {}
            update_memory = bool(routing.get("update_memory"))
            current_ticker = str(state.get("current_ticker") or "").strip()
            return "write_monthly_memory" if (update_memory and bool(current_ticker)) else "end"

        g.set_entry_point("load_ticker_memory")
        g.add_conditional_edges(
            "load_ticker_memory",
            _route_after_load_ticker_memory,
            {"prompt_engineer": "prompt_engineer", "run_selected_specialists_parallel": "run_selected_specialists_parallel"},
        )
        g.add_conditional_edges(
            "prompt_engineer",
            _route_after_prompt_engineer,
            {"run_selected_specialists_parallel": "run_selected_specialists_parallel", "end": END},
        )
        g.add_edge("run_selected_specialists_parallel", "team_merge")
        g.add_conditional_edges(
            "team_merge",
            _route_after_team_merge,
            {"write_monthly_memory": "write_monthly_memory", "end": END},
        )
        g.add_edge("write_monthly_memory", END)

        return g.compile()

    def compile(self) -> Any:
        if self.per_ticker_merge_mode:
            # New mode: per-ticker merge with single graph execution
            return self._compile_ticker_merge_mode()
        else:
            # Old mode: per-ticker graph (backward compatible)
            return self._compile_per_ticker_graph_mode()
    
    def _compile_ticker_merge_mode(self) -> Any:
        """
        New mode: Process ticker list in single graph with per-ticker team merge.
        
        State contract:
        - routing.tickers -> state.ticker_list
        - per-ticker merge results in state.final.result (dict by ticker)
        """
        g = StateGraph(dict)

        def _wrap_node(node_name: str, fn: Any) -> Any:
            async def _wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
                from moose.framework.logging.tracing import span as trace_span

                with trace_span(
                    kind="workflow.node",
                    name=f"investment_research_team.{node_name}",
                    attrs={"workflow": "investment_research_team", "node": node_name},
                ):
                    if inspect.iscoroutinefunction(fn):
                        return await fn(state)
                    out = fn(state)
                    if inspect.isawaitable(out):
                        return await out
                    return out

            return _wrapped
        
        g.add_node("load_ticker_memory", _wrap_node("load_ticker_memory", self.load_ticker_memory.run))
        g.add_node("prompt_engineer", _wrap_node("prompt_engineer", self.prompt_engineer.run))
        g.add_node(
            "run_selected_specialists_parallel",
            _wrap_node("run_selected_specialists_parallel", self.run_selected_specialists_parallel.run),
        )
        g.add_node("team_merge", _wrap_node("team_merge", self.team_merge.run))
        g.add_node("write_monthly_memory", _wrap_node("write_monthly_memory", self.write_monthly_memory.run))
        
        def _route_after_load_ticker_memory(state: Dict[str, Any]) -> str:
            sm = str(state.get("merge_system_message") or "").strip()
            um = str(state.get("merge_user_message") or "").strip()
            return "run_selected_specialists_parallel" if (sm and um) else "prompt_engineer"
        
        def _route_after_prompt_engineer(state: Dict[str, Any]) -> str:
            if state.get("abort"):
                return "end"
            return "run_selected_specialists_parallel"
        
        def _route_after_team_merge(state: Dict[str, Any]) -> str:
            routing = state.get("routing", {}) if isinstance(state.get("routing"), dict) else {}
            update_memory = bool(routing.get("update_memory"))
            ticker_list = state.get("ticker_list", []) if isinstance(state.get("ticker_list"), list) else []
            return "write_monthly_memory" if (update_memory and ticker_list) else "end"
        
        g.set_entry_point("load_ticker_memory")
        g.add_conditional_edges(
            "load_ticker_memory",
            _route_after_load_ticker_memory,
            {"prompt_engineer": "prompt_engineer", "run_selected_specialists_parallel": "run_selected_specialists_parallel"},
        )
        g.add_conditional_edges(
            "prompt_engineer",
            _route_after_prompt_engineer,
            {"run_selected_specialists_parallel": "run_selected_specialists_parallel", "end": END},
        )
        g.add_edge("run_selected_specialists_parallel", "team_merge")
        g.add_conditional_edges(
            "team_merge",
            _route_after_team_merge,
            {"write_monthly_memory": "write_monthly_memory", "end": END},
        )
        g.add_edge("write_monthly_memory", END)
        
        async def setup_ticker_list(state: Dict[str, Any]) -> Dict[str, Any]:
            """Extract ticker list from routing and set per_ticker_merge_mode flag."""
            routing = state.get("routing", {}) if isinstance(state.get("routing"), dict) else {}
            tickers = routing.get("tickers") if isinstance(routing.get("tickers"), list) else []
            tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
            ticker_list = tickers if tickers else [""]
            return {**state, "ticker_list": ticker_list, "per_ticker_merge_mode": True}
        
        per_ticker_app = g.compile()
        
        workflow = StateGraph(dict)
        workflow.add_node("team_route", self.team_route.run)
        workflow.add_node("setup_ticker_list", setup_ticker_list)
        workflow.add_node("process_tickers", per_ticker_app)
        workflow.set_entry_point("team_route")
        workflow.add_edge("team_route", "setup_ticker_list")
        workflow.add_edge("setup_ticker_list", "process_tickers")
        workflow.add_edge("process_tickers", END)
        return workflow.compile()
    
    def _compile_per_ticker_graph_mode(self) -> Any:
        """
        Old mode: Per-ticker graph fan-out (backward compatible).
        """
        # Lazily compile the per-ticker subgraph once per workflow instance.
        if self._per_ticker_app is None:
            self._per_ticker_app = self._compile_per_ticker_app()

        async def run_per_ticker(state: Dict[str, Any]) -> Dict[str, Any]:
            """
            Fan out the subgraph per detected ticker.

            State contract:
            - input: routing.tickers from team_route
            - output: final.result.by_ticker (dict[ticker -> merge_result])
            """
            routing = state.get("routing", {}) if isinstance(state.get("routing"), dict) else {}
            tickers = routing.get("tickers") if isinstance(routing.get("tickers"), list) else []
            tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
            tickers_to_run = tickers if tickers else [""]
            # Keep parity with the newer per-ticker mode: make ticker_list explicit in state.
            # (Some downstream nodes / debugging tools expect this field.)
            state = {**state, "ticker_list": list(tickers_to_run)}

            base_merge_user_message = str(state.get("merge_user_message") or "")

            by_ticker: Dict[str, Any] = {}
            usage_total: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            cost_total: float = 0.0

            for t in tickers_to_run:
                display_ticker = t or "MACRO/ECONOMY"

                # Per-ticker state: reset per-run accumulators to avoid double-counting.
                per_state = dict(state)
                per_state["current_ticker"] = t
                per_state["ticker_memory"] = {}
                per_state["subagent_reports"] = {}
                per_state["evidence"] = []
                per_state.pop("final", None)
                per_state["llm_usage_total"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                per_state["llm_cost_total"] = 0.0
                per_state["per_ticker_merge_mode"] = False  # Old mode flag
                per_state["ticker_list"] = list(tickers_to_run)

                # Format analyze_news merge_user_message template once per ticker.
                formatted_user = base_merge_user_message
                if base_merge_user_message:
                    try:
                        formatted_user = base_merge_user_message.format(display_ticker)
                    except Exception:
                        formatted_user = base_merge_user_message
                per_state["merge_user_message"] = formatted_user

                # Also carry the ticker in metadata for consumers/prompts that inspect metadata.
                md = per_state.get("metadata") if isinstance(per_state.get("metadata"), dict) else {}
                md_out = dict(md or {})
                md_out["current_ticker"] = t
                per_state["metadata"] = md_out

                per_out = (
                    await self._per_ticker_app.ainvoke(per_state)
                    if hasattr(self._per_ticker_app, "ainvoke")
                    else self._per_ticker_app.invoke(per_state)
                )

                per_final = per_out.get("final") if isinstance(per_out, dict) else {}
                per_final = per_final if isinstance(per_final, dict) else {}
                per_res = per_final.get("result") if isinstance(per_final.get("result"), dict) else {}
                by_ticker[t] = per_res

                u = normalize_usage(per_out.get("llm_usage_total") if isinstance(per_out, dict) else None)
                usage_total["input_tokens"] += int(u.get("input_tokens", 0) or 0)
                usage_total["output_tokens"] += int(u.get("output_tokens", 0) or 0)
                usage_total["total_tokens"] += int(u.get("total_tokens", 0) or 0)
                try:
                    cost_total += float(per_out.get("llm_cost_total") or 0.0) if isinstance(per_out, dict) else 0.0
                except Exception:
                    pass

            final_result = {"by_ticker": by_ticker, "tickers": tickers_to_run}
            envelope = {"result": final_result, "raw": raw_snapshot(state), "ok": True, "error": None}
            return {**state, "final": envelope, "llm_usage_total": usage_total, "llm_cost_total": cost_total}

        workflow = StateGraph(dict)
        workflow.add_node("team_route", self.team_route.run)
        workflow.add_node("run_per_ticker", run_per_ticker)
        workflow.set_entry_point("team_route")
        workflow.add_edge("team_route", "run_per_ticker")
        workflow.add_edge("run_per_ticker", END)
        return workflow.compile()


