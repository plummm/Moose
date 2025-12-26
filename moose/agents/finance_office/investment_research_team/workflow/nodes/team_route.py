from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .utils import json_decode_error, normalize_usage, repair_json_once
from .base import BaseNode
from .specialists_runner import SpecialistsRunnerNode

from moose.framework.llm_core import LLMClient


@dataclass
class RoutingDecision:
    playbook: str
    rationale: str
    tickers: List[str]
    selected_agents: List[str]
    agent_tasks: Dict[str, Dict[str, Any]]


def _extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


class TeamRouteNode(BaseNode):
    """
    Node name: `team_route`
    Config key: `custom.team_route_llm_config` (via ResearchLead.get_node_llm_config)

    Reads:
    - state.task_instruction (str)
    - state.context_text (str)
    - state.metadata (dict)

    Writes:
    - state.routing (dict) including: playbook, rationale, tickers, update_memory, selected_agents
    - state.selected_agents (list[str])
    - state.agent_tasks (dict)
    - state.specialist_clients (dict)
    - state.ticker_memory (dict)
    - initializes state.subagent_reports, state.evidence, cost/usage totals
    """

    def __init__(self, *, analyzer: Any, logger: Any, playbooks: Dict[str, Any]):
        super().__init__(analyzer=analyzer, logger=logger)
        self.node_name = "team_route"
        self.playbooks = playbooks
        self.agent_client = self._build_agent_client(node_name="team_route", tools=[])

    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        context_text = str(state.get("context_text", "") or "")
        task_instruction = str(state.get("task_instruction", "") or "")
        metadata = state.get("metadata", {}) or {}

        decision = await self.route_request(
            llm_client=self.agent_client,
            playbooks=self.playbooks,
            context_text=context_text,
            task_instruction=task_instruction,
            metadata=metadata,
        )

        selected_agents = [str(x).strip() for x in (getattr(decision, "selected_agents", None) or []) if str(x).strip()]
        # `update_memory` is NOT decided by the router LLM anymore. It is sourced from the caller's run_task metadata.
        # Default is False unless explicitly provided.
        try:
            update_memory_flag = bool((metadata or {}).get("update_memory", False))
        except Exception:
            update_memory_flag = False
        routing = {
            "playbook": getattr(decision, "playbook", ""),
            "rationale": getattr(decision, "rationale", ""),
            "tickers": getattr(decision, "tickers", []),
            "update_memory": update_memory_flag,
            "selected_agents": selected_agents,
        }

        specialist_clients: Dict[str, Any] = {}
        tools_provider = getattr(self.analyzer, "sec_data_tools", None)
        if tools_provider is not None:
            sp_cfg = self._node_cfg("run_selected_specialists_parallel")
            specialist_clients = SpecialistsRunnerNode.build_specialist_llm_clients(
                analyzer=self.analyzer,
                base_model=str(sp_cfg.get("model") or "").strip(),
                base_temperature=float(sp_cfg.get("temperature", 0.7)),
                llm_extra_params=sp_cfg.get("kwargs") or {},
                tools_provider=tools_provider,
                max_tool_iterations=state.get("max_tool_iterations", 20),
                agent_name=self.main_agent_name,
            )

        return {
            **state,
            "routing": routing,
            "selected_agents": selected_agents,
            "agent_tasks": getattr(decision, "agent_tasks", {}) or {},
            "specialist_clients": specialist_clients,
            "subagent_reports": {},
            "evidence": [],
            "ticker_memory": {},
            "llm_cost_total": float(state.get("llm_cost_total") or 0.0),
            "llm_usage_total": normalize_usage(state.get("llm_usage_total")),
        }
        
    async def route_request(
        self,
        *,
        llm_client: LLMClient,
        playbooks: Dict[str, Any],
        context_text: str = "",
        task_instruction: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """
        Use an orchestrator LLM (no tools) to select a playbook and produce per-agent tasks.
        """
        pb_agents = playbooks.get("agents", {})
        pb_agents_list = []
        n_agents = 0
        if isinstance(pb_agents, dict):
            for name, spec in pb_agents.items():
                if name == "orchestrator":
                    continue
                n_agents += 1
                if not isinstance(spec, dict):
                    continue
                pb_agents_list.extend(
                    [
                        f"{n_agents}) {name} ({spec.get('description', '')})",
                        f"- Best for: {spec.get('best_for', '')}",
                        f"- Use when: {spec.get('use_when', '')}",
                        "\n",
                    ]
                )
        pb_agents_description = "\n".join(pb_agents_list)

        pb_defs = playbooks.get("playbooks") or {}
        pb_list = []
        if isinstance(pb_defs, dict):
            for name, spec in pb_defs.items():
                if not isinstance(spec, dict):
                    continue
                pb_list.append(
                    {
                        "name": name,
                        "description": spec.get("description", ""),
                        "phases": [p.get("agent") for p in (spec.get("phases") or []) if isinstance(p, dict)],
                    }
                )

        system_message = f"""You are a Senior Investment Research Lead running a small multi-agent Investment Research team.

You manage a team of specialist sub-agents, each with a limited toolset relevant to their specialty.

You do NOT call tools yourself. Your job is to:
- choose the best ONE playbook (strategy) for this request
- decide which specialist sub-agents to run (one or more)
- give each selected sub-agents a concrete, tool-friendly task with clear objectives, tickers/entities, and constraints

### Your team (sub-agent specialists) and what each can do
You can delegate to these sub-agent specialists ONLY:
{pb_agents_description}

### Available playbooks (name, description, phases)
{json.dumps(pb_list, indent=2)}

### How to choose playbook + sub-agents (critical)
- Pick the *smallest* playbook that fully covers the goal.
- Use the playbook’s phases as the default execution order, but you may omit phases by not selecting that agent.
- Select only sub-agents that materially improve the answer. Avoid “run all agents” unless the task truly requires it.
- If the request is under-specified (missing ticker/date/region), select the minimal sub-agent(s) that can disambiguate (often fmp_news or fmp_fundamentals) and write a clarifying question in that IC’s notes.

### What to put in each sub-agent task
For each selected sub-agent:
- goal: a single, specific research objective aligned to the playbook (what they should conclude/produce)
- notes: constraints and how to use their tools (e.g., “verify via filings”, “focus on last 4 quarters”, “include key numbers/snippets”, “don’t speculate”)
- tickers: list of tickers you believe are relevant (can be [])

Keep goals tool-oriented (so an sub-agent can translate it into concrete tool calls).

### Output format (STRICT)
Return STRICT JSON matching exactly this schema (no extra keys, no markdown):
{{
"playbook": "<one of the playbook names>",
"rationale": "<1-3 sentences explaining why this playbook and why these sub-agents>",
"tickers": ["<TICKER>", ...],
"selected_agents": ["edgar","fmp_news","fmp_fundamentals","fmp_macro","fmp_price"],
"agent_tasks": {{
    "edgar": {{"goal": "...", "notes": "...", "tickers": ["..."]}},
    "fmp_news": {{"goal": "...", "notes": "...", "tickers": ["..."]}},
    "fmp_fundamentals": {{"goal": "...", "notes": "...", "tickers": ["..."]}},
    "fmp_macro": {{"goal": "...", "notes": "...", "tickers": ["..."]}},
    "fmp_price": {{"goal": "...", "notes": "...", "tickers": ["..."]}}
}}
}}

Rules:
- selected_agents must be a subset of the allowed agent names.
- agent_tasks should include entries only for selected_agents (omit others).
- If you cannot infer any ticker reliably, set tickers to [] and ask for clarification in notes."""

        user_message = f"""Route this request:

Task instruction (may be empty):
{task_instruction}

Metadata (may be empty): {json.dumps(metadata or {}, ensure_ascii=False)}

Context text (may be empty):
{(context_text or '').strip()}
"""

        resp = await llm_client.send_message(message=user_message, system_message=system_message)
        content = getattr(resp, "content", "") or ""
        data = _extract_json(content)
        if data is None:
            # One-shot JSON repair retry
            try:
                repaired = await repair_json_once(llm_client, bad_output=str(content), error_hint=json_decode_error(content))
                data = _extract_json(getattr(repaired, "content", "") or "")
            except Exception:
                data = None
        if not isinstance(data, dict):
            # Conservative default: catalyst validation with minimal set
            data = {
                "playbook": "CatalystValidation",
                "rationale": "Defaulted due to routing parse failure.",
                "tickers": [],
                "selected_agents": ["fmp_news", "edgar", "fmp_fundamentals", "fmp_price"],
                "agent_tasks": {
                    "fmp_news": {"goal": "Summarize the catalyst claim and extract tickers.", "notes": "", "tickers": []},
                    "edgar": {"goal": "Verify the claim via SEC filings (8-K/exhibits) if applicable.", "notes": "", "tickers": []},
                    "fmp_fundamentals": {"goal": "Provide a compact fundamentals snapshot.", "notes": "", "tickers": []},
                    "fmp_price": {"goal": "Summarize price reaction and context.", "notes": "", "tickers": []},
                },
            }

        return RoutingDecision(
            playbook=str(data.get("playbook") or "CatalystValidation"),
            rationale=str(data.get("rationale") or ""),
            tickers=[str(x).upper().strip() for x in (data.get("tickers") or []) if str(x).strip()],
            selected_agents=[str(x).strip() for x in (data.get("selected_agents") or []) if str(x).strip()],
            agent_tasks={k: (v if isinstance(v, dict) else {}) for k, v in (data.get("agent_tasks") or {}).items()},
        )


