from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass
from typing import Any, Optional

from .participants import MeetingParticipant
from .types import DoneBehavior, Guardrails, MeetingMessage, MeetingMode, MeetingRole


@dataclass
class _ThreadInfo:
    participants: set[str]
    messages: list[MeetingMessage]


_CURRENT_MEETING_ROOM: contextvars.ContextVar["MeetingRoom | None"] = contextvars.ContextVar(
    "moose_meeting_room_current",
    default=None,
)

_CURRENT_MEETING_SENDER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "moose_meeting_sender_current",
    default=None,
)


class MeetingRoom:
    """
    MeetingRoom supports:
    - Shared room transcript (visible to all participants)
    - Private threads (visible only to thread participants)
    - ACTIVE mode: round-robin turn-taking (+ host-assisted opening/closing)
    - PASSIVE mode: help-style targeted messages, with auto-start dispatch loop
    """

    def __init__(
        self,
        *,
        room_id: str,
        mode: MeetingMode,
        guardrails: Optional[Guardrails] = None,
        host_id: Optional[str] = None,
    ) -> None:
        self.room_id = str(room_id)
        self.mode = mode
        self.guardrails = guardrails or Guardrails()
        self.host_id = str(host_id) if host_id is not None else None

        self._participants: dict[str, MeetingParticipant] = {}
        self._room_messages: list[MeetingMessage] = []
        self._threads: dict[str, _ThreadInfo] = {}
        self._thread_conds: dict[str, asyncio.Condition] = {}

        self._lock = asyncio.Lock()
        self._turns = 0
        self._started_at = time.time()

        # Passive mode dispatch loop
        self._queue: asyncio.Queue[MeetingMessage] = asyncio.Queue()
        self._dispatch_task: Optional[asyncio.Task] = None
        self._dispatch_started = False

    # -------------------------
    # Contextvar helpers (per-request)
    # -------------------------
    @staticmethod
    def current() -> "MeetingRoom | None":
        """Return the current MeetingRoom for this execution context (if set)."""
        return _CURRENT_MEETING_ROOM.get()

    @staticmethod
    def set_current(room: "MeetingRoom") -> contextvars.Token["MeetingRoom | None"]:
        """Set the current MeetingRoom for this execution context. Returns a token for reset()."""
        return _CURRENT_MEETING_ROOM.set(room)

    @staticmethod
    def reset_current(token: contextvars.Token["MeetingRoom | None"]) -> None:
        """Reset the meeting room contextvar using a prior token returned by set_current()."""
        _CURRENT_MEETING_ROOM.reset(token)

    @staticmethod
    def current_sender_id() -> str | None:
        """Return the current sender/participant id for this execution context (if set)."""
        return _CURRENT_MEETING_SENDER.get()

    @staticmethod
    def set_current_sender_id(sender_id: str) -> contextvars.Token[str | None]:
        """Set the current sender/participant id for this execution context. Returns a token for reset()."""
        return _CURRENT_MEETING_SENDER.set(str(sender_id))

    @staticmethod
    def reset_current_sender_id(token: contextvars.Token[str | None]) -> None:
        """Reset the sender contextvar using a prior token returned by set_current_sender_id()."""
        _CURRENT_MEETING_SENDER.reset(token)

    # -------------------------
    # Participant lifecycle
    # -------------------------
    async def add_participant(self, p: MeetingParticipant) -> None:
        if not p.participant_id:
            raise ValueError("participant_id is required")
        async with self._lock:
            self._participants[p.participant_id] = p
        await p.on_join(self)

    async def remove_participant(self, participant_id: str) -> None:
        pid = str(participant_id)
        p = self._participants.get(pid)
        if p is None:
            return
        async with self._lock:
            self._participants.pop(pid, None)
        await p.on_leave(self)

    def participants(self) -> dict[str, MeetingParticipant]:
        return dict(self._participants)

    # -------------------------
    # Transcripts / rendering
    # -------------------------
    def room_messages(self) -> list[MeetingMessage]:
        return list(self._room_messages)

    def thread_messages(self, thread_id: str) -> list[MeetingMessage]:
        info = self._threads.get(thread_id)
        return list(info.messages) if info else []

    def render_view(self, *, for_participant: str, thread_id: Optional[str]) -> str:
        """
        Render a participant-safe view:
        - room transcript is visible to all participants
        - private thread is visible only if the participant is in that thread
        """
        pid = str(for_participant)
        if thread_id:
            info = self._threads.get(thread_id)
            if info is None or pid not in info.participants:
                return f"[Private thread {thread_id}] (not visible)"
            msgs = info.messages
            header = f"[Private thread {thread_id}]"
        else:
            msgs = self._room_messages
            header = "[Meeting room]"

        lines = [header]
        for m in msgs[-80:]:
            tgt = "" if m.targets is None else f" -> {m.targets}"
            lines.append(f"{m.sender_id}{tgt} ({m.role.value}): {m.content}")
        return "\n".join(lines)

    # -------------------------
    # Stop conditions
    # -------------------------
    def _should_stop(self) -> bool:
        if self._turns >= int(self.guardrails.max_turns):
            return True
        if (time.time() - self._started_at) >= float(self.guardrails.max_time_s):
            return True
        return False

    # -------------------------
    # Publishing API
    # -------------------------
    async def publish(self, msg: MeetingMessage) -> None:
        # Auto-start passive dispatcher on first publish
        if self.mode == MeetingMode.PASSIVE and not self._dispatch_started:
            await self.start()

        async with self._lock:
            if msg.thread_id:
                info = self._threads.get(msg.thread_id)
                if info is None:
                    # Private threads should be created via send_private, but handle best-effort here.
                    participants = set(msg.targets or [])
                    participants.add(msg.sender_id)
                    info = _ThreadInfo(participants=participants, messages=[])
                    self._threads[msg.thread_id] = info
                info.messages.append(msg)

                # Notify any waiters on this thread (ask_private).
                cond = self._thread_conds.get(msg.thread_id)
                if cond is None:
                    cond = asyncio.Condition()
                    self._thread_conds[msg.thread_id] = cond
            else:
                self._room_messages.append(msg)

        # enqueue for dispatch (passive mode)
        if self.mode == MeetingMode.PASSIVE:
            await self._queue.put(msg)

        # done handling is applied during dispatch (so room can act on done signals even in active mode)

        if msg.thread_id:
            cond = self._thread_conds.get(msg.thread_id)
            if cond is not None:
                async with cond:
                    cond.notify_all()

    async def send_room(
        self,
        *,
        sender_id: str,
        role: MeetingRole,
        content: str,
        targets: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MeetingMessage:
        msg = MeetingMessage.new(
            sender_id=sender_id,
            role=role,
            content=content,
            targets=targets,
            thread_id=None,
            metadata=metadata,
        )
        await self.publish(msg)
        return msg

    async def send_private(
        self,
        *,
        sender_id: str,
        role: MeetingRole,
        content: str,
        targets: list[str],
        thread_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> MeetingMessage:
        tid = str(thread_id) if thread_id else f"thread:{sender_id}:{':'.join(sorted(set(targets)))}"
        msg = MeetingMessage.new(
            sender_id=sender_id,
            role=role,
            content=content,
            targets=list(targets),
            thread_id=tid,
            metadata=metadata,
        )
        # ensure thread participants include sender + targets
        async with self._lock:
            info = self._threads.get(tid)
            if info is None:
                info = _ThreadInfo(participants=set(), messages=[])
                self._threads[tid] = info
            info.participants |= set(targets) | {sender_id}
            self._thread_conds.setdefault(tid, asyncio.Condition())
        await self.publish(msg)
        return msg

    async def ask_private(
        self,
        *,
        sender_id: str,
        role: MeetingRole,
        content: str,
        targets: list[str],
        thread_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Send a private message and wait for replies from all targets.

        - Uses Guardrails.help_timeout_s (no per-call timeout arg).
        - Returns a dict:
          {
            "thread_id": str,
            "request": { ... MeetingMessage fields ... },
            "replies": { "<target_id>": { ... MeetingMessage fields ... }, ... },
            "complete": bool,
            "missing_targets": [ ... ]
          }
        """
        req = await self.send_private(
            sender_id=sender_id,
            role=role,
            content=content,
            targets=targets,
            thread_id=thread_id,
            metadata=metadata,
        )
        tid = req.thread_id or ""
        deadline = time.time() + float(self.guardrails.help_timeout_s)
        targets_set = {str(t) for t in (targets or []) if str(t)}

        def _msg_to_dict(m: MeetingMessage) -> dict[str, Any]:
            return {
                "id": m.id,
                "ts": m.ts,
                "sender_id": m.sender_id,
                "role": m.role.value,
                "content": m.content,
                "targets": m.targets,
                "thread_id": m.thread_id,
                "metadata": m.metadata,
            }

        def _collect_replies() -> dict[str, MeetingMessage]:
            replies: dict[str, MeetingMessage] = {}
            info = self._threads.get(tid)
            msgs = info.messages if info else []
            # Only accept replies that occur after the request message and are from target senders.
            seen_req = False
            for m in msgs:
                if m.id == req.id:
                    seen_req = True
                    continue
                if not seen_req:
                    continue
                if m.sender_id in targets_set and m.sender_id not in replies:
                    replies[m.sender_id] = m
                if len(replies) == len(targets_set):
                    break
            return replies

        cond = self._thread_conds.setdefault(tid, asyncio.Condition())
        replies = _collect_replies()
        while len(replies) < len(targets_set):
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            async with cond:
                try:
                    await asyncio.wait_for(cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
            replies = _collect_replies()

        missing = sorted(list(targets_set - set(replies.keys())))
        return {
            "thread_id": tid,
            "request": _msg_to_dict(req),
            "replies": {k: _msg_to_dict(v) for k, v in replies.items()},
            "complete": len(missing) == 0,
            "missing_targets": missing,
        }

    # -------------------------
    # Dispatch loop (PASSIVE)
    # -------------------------
    async def start(self) -> None:
        if self._dispatch_started:
            return
        self._dispatch_started = True
        if self.mode == MeetingMode.PASSIVE:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        task = self._dispatch_task
        self._dispatch_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _dispatch_loop(self) -> None:
        while True:
            msg = await self._queue.get()
            try:
                if self._should_stop():
                    continue
                await self.dispatch_message(msg)
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    # -------------------------
    # Dispatch logic (PASSIVE + targeted in ACTIVE)
    # -------------------------
    def _targets_for(self, msg: MeetingMessage) -> list[str]:
        if msg.targets is None:
            return list(self._participants.keys())
        return [t for t in msg.targets if t in self._participants]

    async def _apply_done(self, msg: MeetingMessage) -> None:
        if not msg.is_done_signal() or not self.guardrails.allow_done_exit:
            return
        pid = msg.sender_id
        p = self._participants.get(pid)
        if p is None:
            return
        if self.guardrails.done_behavior == DoneBehavior.LEAVE:
            await self.remove_participant(pid)
        else:
            p.wait_only = True

    async def dispatch_message(self, msg: MeetingMessage) -> None:
        # Apply done first (so a participant can mark done and then stop responding)
        await self._apply_done(msg)

        if self._should_stop():
            return

        # Passive help-only: broadcast does not trigger responses by default
        if self.mode == MeetingMode.PASSIVE and self.guardrails.respond_only_if_targeted_in_passive:
            if msg.targets is None:
                return

        targets = self._targets_for(msg)

        # Only targeted participants respond; sender does not auto-respond
        for pid in targets:
            if pid == msg.sender_id:
                continue
            p = self._participants.get(pid)
            if p is None or not p.active:
                continue
            if self.mode == MeetingMode.PASSIVE and p.wait_only and msg.targets is None:
                continue
            if not p.can_emit(self.guardrails):
                continue

            # Ensure MeetingRoom.current()/current_sender_id() are set for tool execution inside participant handlers.
            room_token = MeetingRoom.set_current(self)
            sender_token = MeetingRoom.set_current_sender_id(pid)
            try:
                outs = await p.handle(msg, room=self)
            finally:
                MeetingRoom.reset_current_sender_id(sender_token)
                MeetingRoom.reset_current(room_token)
            for out in outs or []:
                await self.publish(out)
                await self._apply_done(out)

    # -------------------------
    # ACTIVE run loop
    # -------------------------
    async def run(self) -> None:
        if self.mode == MeetingMode.ACTIVE:
            await self._run_active()
        else:
            # Passive meetings run via publish/dispatch; run() just ensures dispatcher is started.
            await self.start()

    async def _run_active(self) -> None:
        if self.host_id and self.host_id not in self._participants:
            raise ValueError("host_id is set but not present in participants")

        # Opening: host posts topic prompt if desired (host implements take_turn)
        if self.host_id:
            host = self._participants[self.host_id]
            if host.active and host.can_emit(self.guardrails):
                for out in await host.take_turn(room=self):
                    await self.publish(out)
                    await self._apply_done(out)

        rr: list[str] = list(self._participants.keys())
        idx = 0
        spoke_in_round = False
        round_start_count = len(self._room_messages)

        while not self._should_stop():
            self._turns += 1

            # update rr to include new joiners, drop leavers/inactive
            rr = [pid for pid in rr if pid in self._participants and self._participants[pid].active]
            for pid in self._participants.keys():
                if pid not in rr and self._participants[pid].active:
                    rr.append(pid)
            if not rr:
                break

            pid = rr[idx % len(rr)]
            idx += 1
            p = self._participants.get(pid)
            if p is None or not p.active or p.wait_only:
                continue
            if not p.can_emit(self.guardrails):
                continue

            before = len(self._room_messages)
            outs = await p.take_turn(room=self)
            for out in outs or []:
                await self.publish(out)
                await self._apply_done(out)
            after = len(self._room_messages)
            if after > before:
                spoke_in_round = True

            # If we completed a full cycle and nobody spoke, host concludes.
            if idx % max(1, len(rr)) == 0:
                if not spoke_in_round and self.host_id and self.host_id in self._participants:
                    await self.send_room(
                        sender_id=self.host_id,
                        role=MeetingRole.SYSTEM,
                        content="Final check: does anyone have last points? If not, we will conclude.",
                        targets=None,
                    )
                    break
                spoke_in_round = False

            # also detect room stagnation over a full round (alternative to spoke_in_round)
            if idx % max(1, len(rr)) == 0:
                if len(self._room_messages) == round_start_count and self.host_id and self.host_id in self._participants:
                    await self.send_room(
                        sender_id=self.host_id,
                        role=MeetingRole.SYSTEM,
                        content="No new messages this round. Concluding the meeting.",
                        targets=None,
                    )
                    break
                round_start_count = len(self._room_messages)




