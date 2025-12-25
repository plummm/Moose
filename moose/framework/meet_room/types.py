from __future__ import annotations

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
        try:
            return bool(self.metadata.get("done") is True)
        except Exception:
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



