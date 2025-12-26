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
    llm_usage_total: Optional[Dict[str, int]] = None
    llm_cost_total: Optional[float] = None


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
    task_instruction: str,
    context: Optional[Any] = None,
) -> DepartmentDecision:
    """
    Department-head router: pick the best department playbook (method) and select the teams
    defined by that playbook.
    """
    pb_defs = dept_playbooks.get("playbooks") if isinstance(dept_playbooks.get("playbooks"), dict) else {}
    team_defs = dept_playbooks.get("teams") if isinstance(dept_playbooks.get("teams"), dict) else {}

    pb_list: List[Dict[str, Any]] = []
    for name, spec in (pb_defs or {}).items():
        if not isinstance(spec, dict):
            continue
        teams_spec = spec.get("teams") if isinstance(spec.get("teams"), list) else []
        teams_out: List[Dict[str, Any]] = []
        for t in teams_spec:
            if not isinstance(t, dict):
                continue
            team_name = str(t.get("team") or "").strip()
            if not team_name:
                continue
            base_goal = str(t.get("goal") or "").strip()
            team_desc = ""
            td = team_defs.get(team_name) if isinstance(team_defs.get(team_name), dict) else {}
            if isinstance(td, dict):
                team_desc = str(td.get("description") or "").strip()
            teams_out.append({"team": team_name, "base_goal": base_goal, "team_description": team_desc})

        pb_list.append(
            {
                "name": str(name),
                "description": str(spec.get("description") or ""),
                "teams": teams_out,
            }
        )

    system_message = f"""You are the Department Head (router/planner) for finance_office.

Your task:
- Read the user's instruction.
- Select the SINGLE best department playbook (method) from the provided list.
- Select the teams defined by that playbook.

Important:
- You do NOT call tools yourself.
- You MUST only use playbooks and team names provided below.
- The playbook defines the teams and each team's base goal; downstream code will construct per-team instructions from these goals.

Available department playbooks:
{json.dumps(pb_list, indent=2)}

Return STRICT JSON (no markdown, no extra keys, no trailing commentary):
{{
  "playbook": "<one playbook name>",
  "rationale": "<1-3 sentences>",
  "selected_teams": ["investment_research_team"]
}}

Rules:
- Choose the smallest playbook that fully fits the instruction.
- selected_teams MUST match the playbook's teams (same set; order doesn't matter).
- Do not invent teams or extra fields."""

    user_message = f"""User instruction:
{task_instruction}

Context (optional):
{json.dumps(context, ensure_ascii=False) if isinstance(context, (dict, list)) else str(context or "")}
"""

    resp = await llm_client.send_message(message=user_message, system_message=system_message)
    content = getattr(resp, "content", "") or ""
    data = _extract_json(content)
    usage_total: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    cost_total: float = 0.0
    try:
        u = getattr(resp, "usage", None) or {}
        usage_total["input_tokens"] += int(u.get("input_tokens", 0) or 0)
        usage_total["output_tokens"] += int(u.get("output_tokens", 0) or 0)
        usage_total["total_tokens"] += int(u.get("total_tokens", 0) or 0)
    except Exception:
        pass
    try:
        cost_total += float(getattr(resp, "cost", 0.0) or 0.0)
    except Exception:
        pass
    if data is None:
        # One-shot JSON repair retry
        try:
            from moose.agents.finance_office.investment_research_team.workflow.nodes.utils import json_decode_error, repair_json_once

            repaired = await repair_json_once(llm_client, bad_output=str(content), error_hint=json_decode_error(content))
            repaired_content = getattr(repaired, "content", "") or ""
            data = _extract_json(repaired_content)
            try:
                u2 = getattr(repaired, "usage", None) or {}
                usage_total["input_tokens"] += int(u2.get("input_tokens", 0) or 0)
                usage_total["output_tokens"] += int(u2.get("output_tokens", 0) or 0)
                usage_total["total_tokens"] += int(u2.get("total_tokens", 0) or 0)
            except Exception:
                pass
            try:
                cost_total += float(getattr(repaired, "cost", 0.0) or 0.0)
            except Exception:
                pass
        except Exception:
            data = None
    data = data or {}
    if not isinstance(data, dict):
        data = {}

    playbook = str(data.get("playbook") or "").strip()
    if playbook not in pb_defs:
        # Prefer a stable fallback if the model returns an unknown playbook.
        if "EarningsReport" in pb_defs:
            playbook = "EarningsReport"
        else:
            playbook = next(iter(pb_defs.keys()), "")
    rationale = str(data.get("rationale") or "")
    selected_teams_raw = [str(x).strip() for x in (data.get("selected_teams") or []) if str(x).strip()]

    # Enforce playbook-defined teams as the source of truth.
    pb_spec = pb_defs.get(playbook) if isinstance(pb_defs.get(playbook), dict) else {}
    pb_teams = pb_spec.get("teams") if isinstance(pb_spec.get("teams"), list) else []
    playbook_team_names = [str(t.get("team") or "").strip() for t in pb_teams if isinstance(t, dict) and str(t.get("team") or "").strip()]
    selected_teams = playbook_team_names or selected_teams_raw or []

    return DepartmentDecision(
        playbook=playbook,
        rationale=rationale,
        selected_teams=selected_teams,
        llm_usage_total=usage_total,
        llm_cost_total=cost_total,
    )


