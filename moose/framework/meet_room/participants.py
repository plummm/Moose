from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from .types import Guardrails, MeetingMessage, MeetingRole


class MeetingParticipant(ABC):
    """
    Standard participant interface for MeetingRoom.

    Participants decide what to publish by returning a list of MeetingMessage (or []).
    """

    def __init__(self, *, participant_id: str, display_name: Optional[str] = None) -> None:
        self.participant_id = str(participant_id).strip()
        self.display_name = str(display_name or participant_id).strip() or self.participant_id

        # state flags
        self.active: bool = True
        self.wait_only: bool = False  # when True, only respond to targeted messages (never take a proactive turn)

        # guardrail bookkeeping
        self._last_emit_ts: float = 0.0
        self._emit_count: int = 0

    async def on_join(self, room: Any) -> None:
        return None

    async def on_leave(self, room: Any) -> None:
        return None

    @abstractmethod
    async def handle(self, msg: MeetingMessage, *, room: Any) -> list[MeetingMessage]:
        """Respond to a message (room or private thread) that targets this participant."""
        raise NotImplementedError

    @abstractmethod
    async def take_turn(self, *, room: Any) -> list[MeetingMessage]:
        """ACTIVE mode round-robin turn. May return []."""
        raise NotImplementedError

    def can_emit(self, guardrails: Guardrails) -> bool:
        now = time.time()
        if self._emit_count >= int(guardrails.max_messages_per_participant):
            return False
        if (now - self._last_emit_ts) < float(guardrails.min_reply_interval_s):
            return False
        self._last_emit_ts = now
        self._emit_count += 1
        return True


class LLMClientParticipant(MeetingParticipant):
    """
    Wrap a Moose LLMClient. Tool scope remains on the underlying LLMClient.

    This class is intentionally light: it formats meeting context into a prompt and asks the LLMClient.
    """

    def __init__(
        self,
        *,
        participant_id: str,
        llm_client: Any,
        system_prompt: str,
        task_prompt: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> None:
        super().__init__(participant_id=participant_id, display_name=display_name)
        self.llm_client = llm_client
        self.system_prompt = str(system_prompt or "")
        self.task_prompt = str(task_prompt or "").strip()

    async def handle(self, msg: MeetingMessage, *, room: Any) -> list[MeetingMessage]:
        view = room.render_view(for_participant=self.participant_id, thread_id=msg.thread_id)
        user = f"""You are participating in a meeting.

Incoming message:
- sender: {msg.sender_id}
- role: {msg.role.value}
- content: {msg.content}

Meeting context:
{view}

Instructions:
- Reply ONLY if you are a target of this message (you are).
- You may ask for help by sending a message with targets=[...].
- If you are done with your task, set metadata.done=true in your output message.
Return plain text only (no markdown)."""
        if self.task_prompt:
            user = f"{self.task_prompt}\n\n{user}"

        resp = await self.llm_client.send_message(message=user, system_message=self.system_prompt)
        text = str(getattr(resp, "content", "") or "").strip()
        if not text:
            return []

        meta = {
            "llm_model": getattr(resp, "model", None),
            "llm_request_id": getattr(resp, "request_id", None),
            "llm_usage": getattr(resp, "usage", None),
            "llm_cost": getattr(resp, "cost", None),
        }

        # The LLM may not know how to set metadata; callers can wrap or post-process if desired.
        return [
            MeetingMessage.new(
                sender_id=self.participant_id,
                role=MeetingRole.ASSISTANT,
                content=text,
                targets=None,
                thread_id=msg.thread_id,
                metadata=meta,
            )
        ]

    async def take_turn(self, *, room: Any) -> list[MeetingMessage]:
        if self.wait_only:
            return []
        view = room.render_view(for_participant=self.participant_id, thread_id=None)
        user = f"""It is your turn in the meeting. Speak only if you have something useful.

Meeting context:
{view}

Rules:
- If you have nothing to add, respond with an empty message.
- If you are done, set metadata.done=true in your output message.
Return plain text only (no markdown)."""
        if self.task_prompt:
            user = f"{self.task_prompt}\n\n{user}"

        resp = await self.llm_client.send_message(message=user, system_message=self.system_prompt)
        text = str(getattr(resp, "content", "") or "").strip()
        if not text:
            return []
        meta = {
            "llm_model": getattr(resp, "model", None),
            "llm_request_id": getattr(resp, "request_id", None),
            "llm_usage": getattr(resp, "usage", None),
            "llm_cost": getattr(resp, "cost", None),
        }
        return [
            MeetingMessage.new(
                sender_id=self.participant_id,
                role=MeetingRole.ASSISTANT,
                content=text,
                metadata=meta,
            )
        ]


class BaseAgentParticipant(MeetingParticipant):
    """
    Wrap a BaseAgent-like object via an adapter function.

    talk_fn signature:
      await talk_fn(agent_obj, meeting_view: str, incoming: MeetingMessage | None) -> str | MeetingMessage | list[MeetingMessage]
    """

    def __init__(
        self,
        *,
        participant_id: str,
        agent_obj: Any,
        talk_fn: Callable[[Any, str, Optional[MeetingMessage]], Any],
        display_name: Optional[str] = None,
    ) -> None:
        super().__init__(participant_id=participant_id, display_name=display_name)
        self.agent_obj = agent_obj
        self.talk_fn = talk_fn

    async def _normalize_output(self, out: Any, *, thread_id: Optional[str]) -> list[MeetingMessage]:
        if out is None:
            return []
        if isinstance(out, MeetingMessage):
            return [out]
        if isinstance(out, list) and all(isinstance(x, MeetingMessage) for x in out):
            return list(out)
        # string fallback
        txt = str(out).strip()
        if not txt:
            return []
        return [
            MeetingMessage.new(
                sender_id=self.participant_id,
                role=MeetingRole.ASSISTANT,
                content=txt,
                thread_id=thread_id,
            )
        ]

    async def handle(self, msg: MeetingMessage, *, room: Any) -> list[MeetingMessage]:
        view = room.render_view(for_participant=self.participant_id, thread_id=msg.thread_id)
        out = await self.talk_fn(self.agent_obj, view, msg)
        return await self._normalize_output(out, thread_id=msg.thread_id)

    async def take_turn(self, *, room: Any) -> list[MeetingMessage]:
        if self.wait_only:
            return []
        view = room.render_view(for_participant=self.participant_id, thread_id=None)
        out = await self.talk_fn(self.agent_obj, view, None)
        return await self._normalize_output(out, thread_id=None)



