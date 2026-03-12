"""Helpers for safely building LLM clients from config dicts."""

from __future__ import annotations

import copy
import inspect
from typing import Any, Dict, List, Optional

from .client import LLMClient


def _is_config_only_key(key: str) -> bool:
    if key == "kwargs":
        return True
    if key in {"system_prompt_path", "user_prompt_path", "skills_dir"}:
        return True
    if key.endswith("_prompt_path"):
        return True
    if key.endswith("_skills_dir"):
        return True
    return False


def _deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge_dicts(existing, value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _llm_client_direct_param_names() -> set[str]:
    sig = inspect.signature(LLMClient.__init__)
    return {
        name
        for name, param in sig.parameters.items()
        if name != "self" and param.kind is not inspect.Parameter.VAR_KEYWORD
    }


def merge_llm_config(
    base: Optional[Dict[str, Any]],
    override: Optional[Dict[str, Any]] = None,
    *,
    extra_kwargs: Optional[Dict[str, Any]] = None,
    extra_default_call_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _deep_merge_dicts(dict(base or {}), dict(override or {}))

    base_kwargs = merged.get("kwargs") if isinstance(merged.get("kwargs"), dict) else {}
    merged["kwargs"] = _deep_merge_dicts(base_kwargs, dict(extra_kwargs or {}))

    base_call_kwargs = (
        merged.get("default_call_kwargs")
        if isinstance(merged.get("default_call_kwargs"), dict)
        else {}
    )
    merged["default_call_kwargs"] = _deep_merge_dicts(
        base_call_kwargs,
        dict(extra_default_call_kwargs or {}),
    )

    model = str(merged.get("model") or "").strip()
    if not model:
        raise ValueError("Missing required llm_config.model")
    merged["model"] = model
    return merged


def create_llm_client_from_config(
    llm_config: Optional[Dict[str, Any]],
    *,
    tools: Optional[List[Any]] = None,
    agent_name: Optional[str] = None,
    runtime_overrides: Optional[Dict[str, Any]] = None,
) -> LLMClient:
    cfg = merge_llm_config(llm_config, runtime_overrides)
    direct_param_names = _llm_client_direct_param_names()

    ctor_kwargs: Dict[str, Any] = {}
    for key in sorted(direct_param_names):
        if key in {"tools", "agent_name"}:
            continue
        if key in cfg and not _is_config_only_key(key):
            ctor_kwargs[key] = copy.deepcopy(cfg[key])

    extra_kwargs = dict(cfg.get("kwargs") or {}) if isinstance(cfg.get("kwargs"), dict) else {}
    for key, value in cfg.items():
        if key in direct_param_names or _is_config_only_key(key):
            continue
        extra_kwargs[key] = copy.deepcopy(value)

    if tools is not None:
        ctor_kwargs["tools"] = tools
    elif "tools" in cfg:
        ctor_kwargs["tools"] = copy.deepcopy(cfg["tools"])

    if agent_name is not None:
        ctor_kwargs["agent_name"] = agent_name
    elif cfg.get("agent_name") is not None:
        ctor_kwargs["agent_name"] = copy.deepcopy(cfg["agent_name"])

    for key in list(extra_kwargs.keys()):
        if key in ctor_kwargs or _is_config_only_key(key):
            extra_kwargs.pop(key, None)

    return LLMClient(**ctor_kwargs, **extra_kwargs)
