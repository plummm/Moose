from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MeetingRole(str, Enum):
    SYSTEM = "system"
    HUMAN = "human"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MeetingMode(str, Enum):
    ACTIVE = "active"
    PASSIVE = "passive"


class DoneBehavior(str, Enum):
    LEAVE = "leave"
    WAIT_ONLY = "wait_only"


@dataclass(frozen=True)
class MeetingMessage:
    """
    Message unit for MeetingRoom.

    - targets=None => broadcast (stored in room transcript); in PASSIVE mode this does not trigger responses by default
    - targets=[...] => directed message; only targets are asked to respond
    - thread_id=None => room transcript
    - thread_id!=None => private thread transcript (visible only to participants in that thread)
    """

    id: str
    ts: float
    sender_id: str
    role: MeetingRole
    content: str
    targets: Optional[list[str]] = None
    thread_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(
        *,
        sender_id: str,
        role: MeetingRole,
        content: str,
        targets: Optional[list[str]] = None,
        thread_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> "MeetingMessage":
        return MeetingMessage(
            id=str(uuid.uuid4()),
            ts=time.time(),
            sender_id=str(sender_id),
            role=role,
            content=str(content),
            targets=list(targets) if isinstance(targets, list) else None,
            thread_id=str(thread_id) if thread_id is not None else None,
            metadata=dict(metadata or {}),
        )

    def is_done_signal(self) -> bool:
        # Primary: explicit metadata marker (preferred for programmatic participants)
        try:
            if self.metadata.get("done") is True:
                return True
        except Exception:
            pass

        # Secondary: allow participants to signal done via JSON in content:
        #   {"done": true}
        # This is required for LLM participants that cannot reliably set metadata.
        try:
            content = str(self.content or "").strip()
        except Exception:
            content = ""
        if not content:
            return False

        def _is_done_obj(obj: Any) -> bool:
            return isinstance(obj, dict) and obj.get("done") is True

        # Try parsing whole content as JSON
        try:
            parsed = json.loads(content)
            if _is_done_obj(parsed):
                return True
        except Exception:
            pass

        return False


@dataclass
class Guardrails:
    # Global stop conditions
    max_turns: int = 30
    max_time_s: float = 120.0

    # Participant guardrails
    max_messages_per_participant: int = 12
    min_reply_interval_s: float = 0.25

    # Passive-mode response policy
    respond_only_if_targeted_in_passive: bool = True

    # Done handling
    allow_done_exit: bool = True
    done_behavior: DoneBehavior = DoneBehavior.WAIT_ONLY

    # ask_private wait timeout (seconds)
    help_timeout_s: float = 30.0


@dataclass
class HostPrompts:
    """
    Host prompt templates for orders that require host coordination (e.g., defense order).
    
    Fields are templates that can be formatted with participant names, round numbers, etc.
    """
    introduction_prompt: str = ""
    ask_candidate_prompt: str = ""
    candidate_agree_prompt: str = ""
    candidate_disagree_prompt: str = ""
    final_round_begin_prompt: str = ""
    conclusion_round_begin_prompt: str = ""
    turn_notification_template: str = "{participant_id}, it's your turn"


