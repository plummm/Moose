from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass
class RoutingDecision:
    playbook: str
    rationale: str
    tickers: List[str]
    selected_agents: List[str]
    agent_tasks: Dict[str, Dict[str, Any]]


def load_playbooks(playbooks_path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise ImportError("PyYAML is required to load playbooks.yaml")
    with open(playbooks_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Invalid playbooks YAML")
    return data


def _extract_json(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if not s:
        return None
    # Attempt to pull JSON object if wrapped or if extra text exists
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except Exception:
        return None


async def route_request(
    *,
    llm_client: Any,
    playbooks: Dict[str, Any],
    context_text: str = "",
    instruction: str = "",
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
            pb_agents_list.extend([
                f"{n_agents}) {name} ({spec.get('description', '')})",
                f"- Best for: {spec.get('best_for', '')}",
                f"- Use when: {spec.get('use_when', '')}",
                "\n"
            ]
            )
    pb_agents_description = "\n".join(pb_agents_list)

    pb_defs = (playbooks.get("playbooks") or {})
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

Instruction (may be empty):
{instruction}

Metadata (may be empty): {json.dumps(metadata or {}, ensure_ascii=False)}

Context text (may be empty):
{(context_text or '').strip()}
"""

    resp = await llm_client.send_message(message=user_message, system_message=system_message)
    data = _extract_json(getattr(resp, "content", "") or "")
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


