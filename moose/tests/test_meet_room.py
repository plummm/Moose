import asyncio

from moose.framework.meet_room import (
    DoneBehavior,
    Guardrails,
    MeetingMessage,
    MeetingMode,
    MeetingRole,
    MeetingRoom,
)
from moose.framework.meet_room.participants import MeetingParticipant
from moose.framework.meet_room.participants import LLMClientParticipant


class _TestParticipant(MeetingParticipant):
    def __init__(self, *, participant_id: str, reply_text: str = "", done: bool = False):
        super().__init__(participant_id=participant_id)
        self.reply_text = reply_text
        self.done = done
        self.turn_count = 0

    async def handle(self, msg: MeetingMessage, *, room):
        if not self.reply_text:
            return []
        return [
            MeetingMessage.new(
                sender_id=self.participant_id,
                role=MeetingRole.ASSISTANT,
                content=self.reply_text,
                thread_id=msg.thread_id,
                metadata={"done": True} if self.done else None,
            )
        ]

    async def take_turn(self, *, room):
        self.turn_count += 1
        if not self.reply_text:
            return []
        return [
            MeetingMessage.new(
                sender_id=self.participant_id,
                role=MeetingRole.ASSISTANT,
                content=f"{self.reply_text}:{self.turn_count}",
            )
        ]


def test_passive_autostart_targeted_and_done():
    async def run():
        guard = Guardrails(
            max_turns=10,
            max_time_s=5.0,
            respond_only_if_targeted_in_passive=True,
            allow_done_exit=True,
            done_behavior=DoneBehavior.WAIT_ONLY,
        )
        room = MeetingRoom(room_id="t1", mode=MeetingMode.PASSIVE, guardrails=guard)

        a = _TestParticipant(participant_id="A", reply_text="pong", done=True)
        b = _TestParticipant(participant_id="B", reply_text="")
        await room.add_participant(a)
        await room.add_participant(b)

        # Targeted message triggers response from A only (auto-start dispatch)
        await room.send_room(sender_id="user", role=MeetingRole.HUMAN, content="ping", targets=["A"])
        await asyncio.sleep(0.05)

        msgs = room.room_messages()
        assert any(m.sender_id == "A" and "pong" in m.content for m in msgs)
        # Done signal sets wait_only
        assert room.participants()["A"].wait_only is True

        # Broadcast in passive mode should not trigger responses
        before = len(room.room_messages())
        await room.send_room(sender_id="user", role=MeetingRole.HUMAN, content="broadcast", targets=None)
        await asyncio.sleep(0.05)
        after = len(room.room_messages())
        assert after == before + 1  # only the broadcast itself

        await room.stop()

    asyncio.run(run())


def test_private_thread_visibility_and_dispatch():
    async def run():
        room = MeetingRoom(room_id="t2", mode=MeetingMode.PASSIVE, guardrails=Guardrails(max_time_s=5.0))
        a = _TestParticipant(participant_id="A", reply_text="thread_ok")
        b = _TestParticipant(participant_id="B", reply_text="should_not_reply")
        await room.add_participant(a)
        await room.add_participant(b)

        msg = await room.send_private(sender_id="user", role=MeetingRole.HUMAN, content="help", targets=["A"])
        await asyncio.sleep(0.05)

        thread_msgs = room.thread_messages(msg.thread_id or "")
        assert any(m.sender_id == "A" and "thread_ok" in m.content for m in thread_msgs)
        assert not any(m.sender_id == "B" and "should_not_reply" in m.content for m in thread_msgs)

        # Visibility: B should not see the private thread view
        view_b = room.render_view(for_participant="B", thread_id=msg.thread_id)
        assert "not visible" in view_b

        await room.stop()

    asyncio.run(run())


def test_active_round_robin_runs():
    async def run():
        room = MeetingRoom(
            room_id="t3",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=5, max_time_s=5.0),
            host_id="H",
        )
        host = _TestParticipant(participant_id="H", reply_text="host")
        a = _TestParticipant(participant_id="A", reply_text="a")
        b = _TestParticipant(participant_id="B", reply_text="b")
        await room.add_participant(host)
        await room.add_participant(a)
        await room.add_participant(b)

        await room.run()
        msgs = room.room_messages()
        assert any(m.sender_id == "H" for m in msgs)
        assert any(m.sender_id == "A" for m in msgs)
        assert any(m.sender_id == "B" for m in msgs)

    asyncio.run(run())


def test_ask_private_waits_and_times_out():
    async def run():
        # Fast timeout for test
        guard = Guardrails(max_time_s=5.0, help_timeout_s=0.15, respond_only_if_targeted_in_passive=True)
        room = MeetingRoom(room_id="t4", mode=MeetingMode.PASSIVE, guardrails=guard)

        a = _TestParticipant(participant_id="A", reply_text="ok")
        b = _TestParticipant(participant_id="B", reply_text="")  # never replies
        await room.add_participant(a)
        await room.add_participant(b)

        out = await room.ask_private(
            sender_id="caller",
            role=MeetingRole.HUMAN,
            content="need help",
            targets=["A", "B"],
        )

        assert out.get("thread_id")
        assert isinstance(out.get("request"), dict)
        assert isinstance(out.get("replies"), dict)
        assert out.get("replies", {}).get("A") is not None
        assert out.get("complete") is False
        assert "B" in (out.get("missing_targets") or [])

        await room.stop()

    asyncio.run(run())


def test_llmclient_participant_emits_usage_and_cost_metadata():
    class _FakeResp:
        def __init__(self):
            self.content = "hello back"
            self.model = "fake-model"
            self.request_id = "req-1"
            self.usage = {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
            self.cost = 0.00123

    class _FakeLLMClient:
        async def send_message(self, *, message, system_message=None, **kwargs):
            _ = message
            _ = system_message
            _ = kwargs
            return _FakeResp()

    async def run():
        room = MeetingRoom(room_id="t5", mode=MeetingMode.PASSIVE, guardrails=Guardrails(max_time_s=5.0))
        p = LLMClientParticipant(
            participant_id="P",
            llm_client=_FakeLLMClient(),
            system_prompt="sys",
            task_prompt="",
        )
        await room.add_participant(p)

        msg = await room.send_private(sender_id="caller", role=MeetingRole.HUMAN, content="help", targets=["P"])
        await asyncio.sleep(0.05)

        thread_msgs = room.thread_messages(msg.thread_id or "")
        reply = next((m for m in thread_msgs if m.sender_id == "P"), None)
        assert reply is not None
        assert isinstance(reply.metadata, dict)
        assert reply.metadata.get("llm_model") == "fake-model"
        assert reply.metadata.get("llm_request_id") == "req-1"
        assert reply.metadata.get("llm_usage", {}).get("total_tokens") == 7
        assert abs(float(reply.metadata.get("llm_cost") or 0.0) - 0.00123) < 1e-9

        await room.stop()

    asyncio.run(run())


