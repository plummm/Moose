from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    LANGGRAPH_AVAILABLE = False
    StateGraph = None
    END = None


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


def create_team_workflow(*, analyzer: Any, logger: Any) -> Any:
    """
    Investment Research team LangGraph:
    team_route → run_selected_specialists_parallel → team_merge

    Note: team_merge is responsible for producing the final schema. For news analysis,
    the caller should pass in the existing `system_message` + `user_message` contract.
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("LangGraph is required. Install with: pip install langgraph")

    from moose.framework.llm_core import LLMClient
    from moose.agents.finance_office.investment_research_team.router import load_playbooks, route_request
    from moose.agents.finance_office.investment_research_team.specialists import build_specialist_llm_clients, run_specialist

    playbooks_path = Path(__file__).resolve().parent / "playbooks.yaml"
    playbooks = load_playbooks(playbooks_path)
    main_agent_name = str(getattr(analyzer, "agent_name", "") or "").strip() or None
    # If running with Moose global debug flag, prefer sequential specialist execution for easier debugging.
    debug_mode = False
    try:
        from moose.framework.logging import get_global_debug
    except Exception:  # pragma: no cover
        try:
            from framework.logging import get_global_debug  # type: ignore
        except Exception:  # pragma: no cover
            get_global_debug = None  # type: ignore
    try:
        debug_mode = bool(get_global_debug()) if get_global_debug is not None else False  # type: ignore[misc]
    except Exception:
        debug_mode = False

    def _node_cfg(node_name: str) -> Dict[str, Any]:
        if hasattr(analyzer, "get_node_llm_config"):
            return analyzer.get_node_llm_config(node_name)  # type: ignore[attr-defined]
        # Fallback: use analyzer.model/temperature, but do not invent a model string here.
        model = str(getattr(analyzer, "model", "") or "").strip()
        if not model:
            raise ValueError("Analyzer model is not set; custom.llm_config.model must be configured")
        return {"model": model, "temperature": float(getattr(analyzer, "temperature", 0.7)), "kwargs": {}}

    def _normalize_usage(u: Any) -> Dict[str, int]:
        if not isinstance(u, dict):
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        try:
            it = int(u.get("input_tokens", 0) or 0)
            ot = int(u.get("output_tokens", 0) or 0)
            tt = int(u.get("total_tokens", it + ot) or (it + ot))
        except Exception:
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {"input_tokens": max(0, it), "output_tokens": max(0, ot), "total_tokens": max(0, tt)}

    def _raw_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
        snap = dict(state or {})
        # Avoid recursive embedding if caller already set an envelope
        if isinstance(snap.get("final"), dict) and set(snap["final"].keys()) >= {"ok", "error", "result", "raw"}:
            snap.pop("final", None)
        return snap

    async def team_route(state: Dict[str, Any]) -> Dict[str, Any]:
        # Router uses no tools.
        cfg = _node_cfg("team_route")
        router_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.7)),
            tools=[],
            enable_multi_stage_reasoning=False,
            agent_name=main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        context_text = str(state.get("context_text", "") or "")
        instruction = str(state.get("instruction", "") or "")
        metadata = state.get("metadata", {}) or {}

        decision = await route_request(
            llm_client=router_client,
            playbooks=playbooks,
            context_text=context_text,
            instruction=instruction,
            metadata=metadata,
        )

        pb = playbooks.get("playbooks", {}).get(decision.playbook, {}) if isinstance(playbooks.get("playbooks"), dict) else {}
        max_iters: Dict[str, int] = {}
        if isinstance(pb, dict):
            for ph in pb.get("phases", []) or []:
                if isinstance(ph, dict) and ph.get("agent"):
                    try:
                        max_iters[str(ph["agent"])] = int(ph.get("max_tool_calls") or 6)
                    except Exception:
                        max_iters[str(ph["agent"])] = 6

        # Selected agents are independent; we will run them concurrently.
        selected_agents = [str(x).strip() for x in (decision.selected_agents or []) if str(x).strip()]

        routing = {
            "playbook": decision.playbook,
            "rationale": decision.rationale,
            "tickers": decision.tickers,
            "selected_agents": selected_agents,
        }

        return {
            **state,
            "routing": routing,
            "selected_agents": selected_agents,
            "agent_tasks": decision.agent_tasks,
            # built later by granularity_selector so it can enforce budgets
            "specialist_clients": {},
            "max_tool_iterations_by_agent": max_iters,
            "subagent_reports": {},
            "evidence": [],
            # total cost/token usage (debugging; UI uses llm.log as source of truth)
            "llm_cost_total": float(state.get("llm_cost_total") or 0.0),
            "llm_usage_total": _normalize_usage(state.get("llm_usage_total")),
        }

    async def prompt_engineer(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate `system_message` and `user_message` when missing, based on `instruction` and state.
        """
        instruction = str(state.get("instruction", "") or "").strip()
        if not instruction:
            envelope = {
                "result": {},
                "raw": _raw_snapshot(state),
                "ok": False,
                "error": {
                    "code": "instruction_required",
                    "message": "Please provide a clear instruction for the Investment Research team.",
                },
            }
            return {**state, "final": envelope, "abort": True}

        # Tool-less prompt generator
        cfg = _node_cfg("prompt_engineer")
        pe_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.2)),
            tools=[],
            enable_multi_stage_reasoning=False,
            agent_name=main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        pe_system = """You are a prompt engineer specializing in finance / investment research workflows.

Goal:
- Given an instruction from a Research Lead, produce a task-appropriate SYSTEM prompt and USER prompt for an Investment Research merge model.
- The merge model will synthesize specialist evidence into a final answer.

You MUST:
- Read and understand the instruction.
- Write a SYSTEM prompt that:
  - sets the role as an experienced investment research analyst
  - defines how to use evidence (prefer verifiable facts, do not fabricate)
  - defines the input fields the model will receive:
    - instruction
    - context_text (may be empty)
    - metadata (may include url/ticker/etc.)
    - routing/subagent_reports/evidence will be appended later
  - defines the output format as STRICT JSON (no markdown)
  - includes a suggested JSON schema appropriate to the task and briefly explains key fields (including confidence scale if you include confidence)
- Write a USER message that embeds the instruction and includes placeholders/sections for:
  - instruction
  - context_text
  - metadata
  - (later appended) routing/subagent_reports/evidence

Return STRICT JSON only:
{
  "system_message": "...",
  "user_message": "..."
}
No extra keys."""

        pe_input = {
            "instruction": state.get("instruction") or "",
            "context_text": state.get("context_text") or "",
            "metadata": state.get("metadata") or {},
            "task_goal": state.get("task_goal") or "",
        }
        pe_user = f"""Generate prompts for this Investment Research task.

Inputs:
{json.dumps(pe_input, ensure_ascii=False, indent=2)}

Return STRICT JSON only:
{{"system_message":"...","user_message":"..."}}"""

        last_err: Optional[str] = None
        for _ in range(3):
            resp = await pe_client.send_message(message=pe_user, system_message=pe_system)
            data = _extract_json(getattr(resp, "content", "") or "") or {}
            if not isinstance(data, dict):
                last_err = "Prompt engineer output was not JSON."
                continue
            sm = str(data.get("system_message") or "").strip()
            um = str(data.get("user_message") or "").strip()
            if sm and um:
                return {**state, "system_message": sm, "user_message": um, "abort": False}
            last_err = "Prompt engineer returned empty system_message or user_message."

        raise RuntimeError(f"prompt_engineer failed after 3 attempts: {last_err or 'unknown error'}")

    async def granularity_selector(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decide granularity (minimal/standard/maximum) using a tool-less LLM (semantic override included),
        rewrite instruction with guidance, and rebuild specialist clients with enforced tool-iteration caps.
        """
        instruction = str(state.get("instruction", "") or "").strip()
        if not instruction:
            # prompt_engineer abort should have handled this, but keep a safe default
            return {**state, "granularity": "standard"}

        # Preserve original instruction for debugging (one-time)
        if not isinstance(state.get("instruction_original"), str) or not str(state.get("instruction_original") or "").strip():
            state = {**state, "instruction_original": instruction}

        cfg = _node_cfg("granularity_selector")
        selector_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.2)),
            tools=[],
            enable_multi_stage_reasoning=False,
            agent_name=main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        selector_system = """You are a senior investment research operations analyst.

Task:
- Decide the required analysis granularity for an investment research task: minimal, standard, or maximum.
- You MUST interpret the instruction semantically (natural language).

Hard override (highest priority):
- If the instruction clearly indicates urgency/importance (e.g. critical/urgent/not important) OR explicitly requests granularity
  (e.g. use minimal/standard/maximum analysis; use detailed analysis), you MUST follow that request even if you disagree.

Otherwise, use these heuristics:
- maximum: complicated/high-stakes/high-impact tasks
- standard: meaningful but not market-moving tasks
- minimal: simple/low-priority tasks

Return STRICT JSON only:
{
  "granularity": "minimal|standard|maximum",
  "override_detected": true|false,
  "override_quote": "<short quote from instruction or empty string>",
  "rationale": "<1-3 sentences>"
}
No extra keys. No markdown."""

        selector_input = {
            "instruction": instruction,
            "metadata": state.get("metadata") or {},
            "routing": state.get("routing") or {},
            "context_text": state.get("context_text") or "",
        }
        selector_user = f"""Decide granularity for this task.\n\nInputs:\n{json.dumps(selector_input, ensure_ascii=False, indent=2)}"""

        data = {}
        try:
            resp = await selector_client.send_message(message=selector_user, system_message=selector_system)
            data = _extract_json(getattr(resp, "content", "") or "") or {}
        except Exception:
            data = {}

        granularity = str((data or {}).get("granularity") or "").strip().lower()
        if granularity not in {"minimal", "standard", "maximum"}:
            granularity = "standard"

        override_detected = bool((data or {}).get("override_detected")) if isinstance((data or {}).get("override_detected"), bool) else False
        override_quote = str((data or {}).get("override_quote") or "").strip()
        rationale = str((data or {}).get("rationale") or "").strip()

        # Append guidance to instruction
        maximum_guidance = (
            "This task requires detailed, high-granularity analysis. Gather as much verifiable information as you can, "
            "cross-check key facts across sources, and surface second-order implications. Use tools aggressively where helpful, "
            "and don’t stop at the first obvious answer. Prioritize primary sources (e.g., filings) when available, and clearly "
            "separate facts vs. inference."
        )
        standard_guidance = (
            "This task is standard priority. Apply a balanced analysis: focus on the highest-signal data first, then expand only "
            "if early results suggest meaningful follow-ups. Keep tool usage disciplined—aim for no more than 2 tool-iterations total, "
            "and in each iteration prefer 1–3 tool calls. Exceed these limits only if a result is genuinely interesting and additional "
            "calls are likely to change the conclusion."
        )
        minimal_guidance = (
            "This task is low priority. Keep analysis lightweight and cost-conscious. Use tools only if they are highly relevant and "
            "likely to resolve a key uncertainty quickly. Aim for 0–1 tool-iterations (hard cap 2). If the task can be answered without "
            "tools, do so and state assumptions/limits."
        )

        guidance = standard_guidance
        if granularity == "maximum":
            guidance = maximum_guidance
        elif granularity == "minimal":
            guidance = minimal_guidance

        rewritten_instruction = instruction
        if guidance and guidance not in rewritten_instruction:
            rewritten_instruction = rewritten_instruction.rstrip() + "\n\n" + guidance

        # Rebuild specialist clients with budgets enforced
        selected_agents = [str(x).strip() for x in (state.get("selected_agents") or []) if str(x).strip()]
        base_iters = state.get("max_tool_iterations_by_agent", {}) or {}
        max_iters: Dict[str, int] = {}
        for agent_name, iters in (base_iters.items() if isinstance(base_iters, dict) else []):
            try:
                max_iters[str(agent_name)] = int(iters)
            except Exception:
                max_iters[str(agent_name)] = 6

        if granularity in {"standard", "minimal"}:
            # cap only selected agents
            for a in selected_agents:
                prior = max_iters.get(a, 6)
                max_iters[a] = min(prior, 2)

        specialist_clients: Dict[str, Any] = {}
        tools_provider = getattr(analyzer, "sec_data_tools", None)
        if tools_provider is not None:
            sp_cfg = _node_cfg("run_selected_specialists_parallel")
            specialist_clients = build_specialist_llm_clients(
                base_model=str(sp_cfg.get("model") or "").strip(),
                base_temperature=float(sp_cfg.get("temperature", 0.7)),
                llm_extra_params=sp_cfg.get("kwargs") or {},
                tools_provider=tools_provider,
                max_tool_iterations_by_agent=max_iters,
                agent_name=main_agent_name,
            )

        try:
            logger.info(f"Granularity selected: {granularity} (override={override_detected})")
        except Exception:
            pass

        return {
            **state,
            "instruction": rewritten_instruction,
            "granularity": granularity,
            "granularity_override_detected": override_detected,
            "granularity_override_quote": override_quote,
            "granularity_rationale": rationale,
            "specialist_clients": specialist_clients,
            "max_tool_iterations_by_agent": max_iters,
        }

    async def run_selected_specialists_parallel(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all selected specialist ICs concurrently and merge their outputs into:
        - `subagent_reports`
        - `evidence`
        """
        selected_agents = [str(x).strip() for x in (state.get("selected_agents") or []) if str(x).strip()]
        if not selected_agents:
            return state

        context_text = str(state.get("context_text", "") or "")
        instruction = str(state.get("instruction", "") or "")
        agent_tasks = state.get("agent_tasks", {}) or {}
        clients = state.get("specialist_clients", {}) or {}
        prior_reports = state.get("subagent_reports", {}) or {}

        # Limit parallelism to reduce API throttling risk (can be overridden per-call).
        try:
            max_parallel = int(state.get("max_parallel_specialists") or 5)
        except Exception:
            max_parallel = 5
        max_parallel = max(1, min(5, max_parallel))
        sem = asyncio.Semaphore(max_parallel)

        async def _run_one(agent_name: str) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]], Dict[str, int], float]:
            async with sem:
                task = dict((agent_tasks or {}).get(agent_name, {}) or {})
                task.setdefault("instruction", instruction)

                client = clients.get(agent_name)
                if client is None:
                    # tool-less fallback
                    from moose.framework.llm_core import LLMClient

                    sp_cfg = _node_cfg("run_selected_specialists_parallel")
                    client = LLMClient(
                        model=str(sp_cfg.get("model") or "").strip(),
                        temperature=float(sp_cfg.get("temperature", 0.7)),
                        tools=[],
                        enable_multi_stage_reasoning=False,
                        agent_name=main_agent_name,
                        **(sp_cfg.get("kwargs") or {}),
                    )

                tool_summary = ""
                try:
                    if hasattr(analyzer, "_summarize_tools"):
                        tool_summary = analyzer._summarize_tools(agent_name=agent_name)
                except Exception:
                    tool_summary = ""

                res = await run_specialist(
                    agent=agent_name,
                    llm_client=client,
                    context_text=context_text,
                    task=task,
                    prior_reports=prior_reports,
                    tool_summary=tool_summary,
                )

                normalized: List[Dict[str, Any]] = []
                for e in (res.evidence or []):
                    if not isinstance(e, dict):
                        continue
                    ee = dict(e)
                    ee.setdefault("source", agent_name)
                    ee.setdefault("tool", ee.get("tool") or "")
                    ee.setdefault("key_fields", ee.get("key_fields") or {})
                    ee.setdefault("excerpt", ee.get("excerpt") or "")
                    normalized.append(ee)

                report = {
                    "summary": res.summary,
                    "key_findings": res.key_findings,
                    "confidence": res.confidence,
                    "next_steps": res.next_steps,
                    # fill evidence refs after global merge
                    "tool_evidence_refs": [],
                }
                llm_resp = None
                try:
                    if isinstance(res.raw, dict):
                        llm_resp = res.raw.get("text")
                except Exception:
                    llm_resp = None
                usage = _normalize_usage(getattr(llm_resp, "usage", None))
                try:
                    cost = float(getattr(llm_resp, "cost", 0.0) or 0.0)
                except Exception:
                    cost = 0.0
                return agent_name, report, normalized, usage, cost

        # Run selected agents sequentially in debug mode (or if explicitly requested)
        run_sequential = debug_mode
        if run_sequential:
            results = []
            for a in selected_agents:
                try:
                    results.append(await _run_one(a))
                except Exception as e:
                    results.append(e)
        else:
            # Run all selected agents concurrently
            results = await asyncio.gather(*[_run_one(a) for a in selected_agents], return_exceptions=True)

        reports_out = dict(prior_reports)
        evidence_out = list(state.get("evidence") or [])
        llm_usage_total = _normalize_usage(state.get("llm_usage_total"))
        try:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0)
        except Exception:
            llm_cost_total = 0.0

        for item in results:
            if isinstance(item, Exception):
                # If one agent errors, keep going; record a minimal failure report.
                nm = "unknown"
                try:
                    nm = getattr(item, "agent", None) or nm 
                except Exception:
                    pass
                reports_out[str(nm)] = {
                    "summary": f"Specialist failed: {item}",
                    "key_findings": [],
                    "confidence": 1,
                    "next_steps": [],
                    "tool_evidence_refs": [],
                }
                continue

            agent_name, report, normalized, usage, cost = item
            start_idx = len(evidence_out)
            evidence_out.extend(normalized)
            report["tool_evidence_refs"] = list(range(start_idx, start_idx + len(normalized)))
            reports_out[agent_name] = report
            llm_usage_total["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
            llm_usage_total["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
            llm_usage_total["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
            llm_cost_total += float(cost or 0.0)

        return {
            **state,
            "subagent_reports": reports_out,
            "evidence": evidence_out,
            "llm_usage_total": llm_usage_total,
            "llm_cost_total": llm_cost_total,
        }

    async def team_merge(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge specialist outputs into the final response.

        For news analysis, the caller should provide `system_message` and `user_message` that match the
        department-level news contract (see `finance_office/assistant.py`). For non-news tasks, we still emit the same
        schema (with neutral defaults) unless the caller provides a different system_message.
        """
        from moose.framework.llm_core import LLMClient

        system_message = str(state.get("system_message") or "")
        user_message = str(state.get("user_message") or "")
        routing = state.get("routing", {}) or {}
        subagent_reports = state.get("subagent_reports", {}) or {}
        evidence = state.get("evidence", []) or []

        if not system_message or not user_message:
            try:
                logger.error("team_merge missing required prompts: system_message/user_message must be provided in state")
            except Exception:
                pass
            raise RuntimeError("team_merge missing required prompts (system_message/user_message)")

        # Attach evidence to user message (additive; caller allowed minor additions).
        if evidence or subagent_reports or routing:
            addon = "\n\nADDITIONAL CONTEXT (from sub-agents):\n"
            addon += "Routing:\n" + json.dumps(routing, ensure_ascii=False, indent=2) + "\n"
            addon += "Sub-agent reports:\n" + json.dumps(subagent_reports, ensure_ascii=False, indent=2)[:12000] + "\n"
            addon += "Evidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)[:12000] + "\n"
            user_message = (user_message or "") + addon

        cfg = _node_cfg("team_merge")
        merge_client = LLMClient(
            model=str(cfg.get("model") or "").strip(),
            temperature=float(cfg.get("temperature", 0.7)),
            tools=[],
            enable_multi_stage_reasoning=False,
            agent_name=main_agent_name,
            **(cfg.get("kwargs") or {}),
        )

        resp = await merge_client.send_message(message=user_message, system_message=system_message)
        # Accumulate totals
        llm_usage_total = _normalize_usage(state.get("llm_usage_total"))
        llm_usage_total["input_tokens"] += int(getattr(resp, "usage", {}).get("input_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        llm_usage_total["output_tokens"] += int(getattr(resp, "usage", {}).get("output_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        llm_usage_total["total_tokens"] += int(getattr(resp, "usage", {}).get("total_tokens", 0) or 0) if getattr(resp, "usage", None) else 0
        try:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0) + float(getattr(resp, "cost", 0.0) or 0.0)
        except Exception:
            llm_cost_total = float(state.get("llm_cost_total") or 0.0)
        out = _extract_json(getattr(resp, "content", "") or "") or {}
        if not isinstance(out, dict):
            out = {}

        raw = _raw_snapshot(
            {
                **state,
                "routing": routing,
                "subagent_reports": subagent_reports,
                "evidence": evidence,
                "llm_usage_total": llm_usage_total,
                "llm_cost_total": llm_cost_total,
            }
        )
        envelope = {"result": out, "raw": raw, "ok": True, "error": None}
        return {**state, "final": envelope, "llm_usage_total": llm_usage_total, "llm_cost_total": llm_cost_total}

    def _route_after_team_route(state: Dict[str, Any]) -> str:
        sm = str(state.get("system_message") or "").strip()
        um = str(state.get("user_message") or "").strip()
        return "granularity_selector" if (sm and um) else "prompt_engineer"

    def _route_after_prompt_engineer(state: Dict[str, Any]) -> str:
        if state.get("abort"):
            return "end"
        return "granularity_selector"

    workflow = StateGraph(dict)  # state is plain dict
    workflow.add_node("team_route", team_route)
    workflow.add_node("prompt_engineer", prompt_engineer)
    workflow.add_node("granularity_selector", granularity_selector)
    workflow.add_node("run_selected_specialists_parallel", run_selected_specialists_parallel)
    workflow.add_node("team_merge", team_merge)

    workflow.set_entry_point("team_route")
    workflow.add_conditional_edges(
        "team_route",
        _route_after_team_route,
        {
            "prompt_engineer": "prompt_engineer",
            "granularity_selector": "granularity_selector",
        },
    )
    workflow.add_conditional_edges(
        "prompt_engineer",
        _route_after_prompt_engineer,
        {"granularity_selector": "granularity_selector", "end": END},
    )
    workflow.add_edge("granularity_selector", "run_selected_specialists_parallel")
    workflow.add_edge("run_selected_specialists_parallel", "team_merge")
    workflow.add_edge("team_merge", END)

    return workflow.compile()


