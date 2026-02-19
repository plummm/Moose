from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

_PROMPT_PATH_KEYS: tuple[str, ...] = ("system_prompt_path", "skills_dir")


def _is_prompt_path_key(key: str) -> bool:
    if key in _PROMPT_PATH_KEYS:
        return True
    if key.endswith("_prompt_path"):
        return True
    if key.endswith("_skills_dir"):
        return True
    return False


def load_agent_config(config_path: Path) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")
    return _load_with_includes(path.resolve(), [])


def _load_with_includes(path: Path, stack: list[Path]) -> Dict[str, Any]:
    if path in stack:
        chain = " -> ".join(str(p) for p in stack + [path])
        raise ValueError(f"Include cycle detected in agent config: {chain}")

    stack.append(path)
    data = _read_json(path)
    resolved = _resolve_obj(data, base_dir=path.parent, stack=stack)
    stack.pop()

    if not isinstance(resolved, dict):
        raise ValueError(f"Agent config must be a JSON object: {path}")
    return resolved


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")
    return data


def _resolve_obj(obj: Any, *, base_dir: Path, stack: list[Path]) -> Any:
    if isinstance(obj, dict):
        if "$include" in obj:
            include_path = obj.get("$include")
            if not isinstance(include_path, str) or not include_path.strip():
                raise ValueError(f"Invalid $include value in {base_dir}")
            include_abs = _resolve_path(include_path, base_dir, allow_empty=False)
            included = _load_with_includes(Path(include_abs), stack)
            overrides = {k: v for k, v in obj.items() if k != "$include"}
            if overrides:
                overrides = _resolve_obj(overrides, base_dir=base_dir, stack=stack)
            merged = _deep_merge(included, overrides if isinstance(overrides, dict) else {})
            return _normalize_prompt_fields(merged, base_dir)

        out = {k: _resolve_obj(v, base_dir=base_dir, stack=stack) for k, v in obj.items()}
        return _normalize_prompt_fields(out, base_dir)

    if isinstance(obj, list):
        return [_resolve_obj(v, base_dir=base_dir, stack=stack) for v in obj]

    return obj


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_prompt_fields(obj: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    if not _looks_like_llm_config(obj):
        return obj

    if "system_prompt_path" not in obj:
        obj["system_prompt_path"] = ""
    if "skills_dir" not in obj:
        obj["skills_dir"] = "skills/"

    for key, val in obj.items():
        if not _is_prompt_path_key(key):
            continue
        if isinstance(val, str):
            obj[key] = _resolve_path(val, base_dir, allow_empty=True)
    return obj


def _looks_like_llm_config(obj: Dict[str, Any]) -> bool:
    model = obj.get("model")
    return isinstance(model, str) and bool(model.strip())


def _resolve_path(value: str, base_dir: Path, *, allow_empty: bool) -> str:
    if not value:
        return "" if allow_empty else str(base_dir.resolve())
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    try:
        return str(path.resolve())
    except Exception:
        return str(path.absolute())
