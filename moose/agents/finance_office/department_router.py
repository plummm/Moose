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
class DepartmentDecision:
    playbook: str
    rationale: str
    selected_teams: List[str]
    team_tasks: Dict[str, Dict[str, Any]]


def load_department_playbooks(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise ImportError("PyYAML is required to load department_playbooks.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("Invalid department playbooks YAML")
    return data


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


async def route_department_task(
    *,
    llm_client: Any,
    dept_playbooks: Dict[str, Any],
    instruction: str,
    context: Optional[Dict[str, Any]] = None,
) -> DepartmentDecision:
    """
    Department-head router: pick a department playbook and assign tasks to teams.
    """
    pb_defs = dept_playbooks.get("playbooks") or {}
    pb_list = []
    if isinstance(pb_defs, dict):
        for name, spec in pb_defs.items():
            if not isinstance(spec, dict):
                continue
            pb_list.append(
                {
                    "name": name,
                    "description": spec.get("description", ""),
                    "teams": [t.get("team") for t in (spec.get("teams") or []) if isinstance(t, dict)],
                }
            )

    system_message = f"""You are the Department Head (router/planner) for finance_office.

You receive arbitrary finance research instructions and must route them to the most suitable department playbook and team(s).
You do NOT call tools yourself.

Available department playbooks:
{json.dumps(pb_list, indent=2)}

Return STRICT JSON:
{{
  "playbook": "<one playbook name>",
  "rationale": "<1-3 sentences>",
  "selected_teams": ["investment_research_team"],
  "team_tasks": {{
    "investment_research_team": {{"goal": "...", "notes": "...", "inputs": {{...}}}}
  }}
}}

Rules:
- Choose the smallest playbook that fits.
- Only include the teams you truly need.
- If missing key inputs (e.g., ticker), ask for them by putting a question in `team_tasks.<team>.notes`."""

    user_message = f"""Instruction:
{instruction}

Context (optional):
{json.dumps(context or {}, ensure_ascii=False)}
"""

    resp = await llm_client.send_message(message=user_message, system_message=system_message)
    data = _extract_json(getattr(resp, "content", "") or "") or {}
    if not isinstance(data, dict):
        data = {}

    playbook = str(data.get("playbook") or "EarningsReport")
    rationale = str(data.get("rationale") or "")
    selected_teams = [str(x).strip() for x in (data.get("selected_teams") or []) if str(x).strip()]
    team_tasks = data.get("team_tasks") if isinstance(data.get("team_tasks"), dict) else {}
    team_tasks = {str(k): (v if isinstance(v, dict) else {}) for k, v in team_tasks.items()}

    if not selected_teams:
        selected_teams = ["investment_research_team"]
    if "investment_research_team" not in team_tasks:
        team_tasks["investment_research_team"] = {
            "goal": "Complete the requested finance research task.",
            "notes": "",
            "inputs": {"instruction": instruction, "context": context or {}},
        }

    return DepartmentDecision(
        playbook=playbook,
        rationale=rationale,
        selected_teams=selected_teams,
        team_tasks=team_tasks,
    )


