from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def render_prompt_template(text: str, *, replacements: dict[str, Any]) -> str:
    out = text
    for key, value in replacements.items():
        out = out.replace(f"{{{{{key}}}}}", str(value))
    return out


def _load_skills_text(*, skills_dir: str, logger: Any, label: str) -> str:
    if not skills_dir:
        return ""
    try:
        p = Path(skills_dir)
    except Exception:
        try:
            logger.warning(f"Skills path invalid for {label}: {skills_dir!r}; skipping skills.")
        except Exception:
            pass
        return ""
    if not p.exists():
        try:
            logger.warning(f"Skills dir not found for {label}: {p}; skipping skills.")
        except Exception:
            pass
        return ""
    if not p.is_dir():
        try:
            logger.warning(f"Skills path is not a directory for {label}: {p}; skipping skills.")
        except Exception:
            pass
        return ""
    files = sorted(p.glob("*.md"))
    if not files:
        return ""
    parts: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        if not text:
            continue
        parts.append(f"[Skill: {f.stem}]\n{text}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def load_prompt_text(*, path: str, logger: Any, label: str, required: bool = False) -> Optional[str]:
    if not path:
        if required:
            raise FileNotFoundError(f"Prompt path missing for {label}")
        try:
            logger.warning(f"Prompt path missing for {label}; using built-in default.")
        except Exception:
            pass
        return None
    try:
        p = Path(path)
    except Exception:
        if required:
            raise ValueError(f"Prompt path invalid for {label}: {path!r}")
        try:
            logger.warning(f"Prompt path invalid for {label}: {path!r}; using built-in default.")
        except Exception:
            pass
        return None
    if not p.exists():
        if required:
            raise FileNotFoundError(f"Prompt file not found for {label}: {p}")
        try:
            logger.warning(f"Prompt file not found for {label}: {p}; using built-in default.")
        except Exception:
            pass
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as exc:
        if required:
            raise RuntimeError(f"Prompt file read failed for {label}: {p} ({exc})") from exc
        try:
            logger.warning(f"Prompt file read failed for {label}: {p} ({exc}); using built-in default.")
        except Exception:
            pass
        return None
    text = text.strip()
    if not text:
        if required:
            raise ValueError(f"Prompt file empty for {label}: {p}")
        try:
            logger.warning(f"Prompt file empty for {label}: {p}; using built-in default.")
        except Exception:
            pass
        return None
    return text


def load_system_prompt(
    *, system_prompt_path: str, skills_dir: str, logger: Any, label: str, required: bool = True
) -> str:
    base = load_prompt_text(
        path=system_prompt_path,
        logger=logger,
        label=label,
        required=required,
    )
    base = base or ""
    skills_text = _load_skills_text(skills_dir=skills_dir, logger=logger, label=label)
    if skills_text:
        return base + "\n\nSKILLS\n" + skills_text
    return base
