import asyncio
import json

from moose.framework.meet_room import (
    DefenseOrder,
    DoneBehavior,
    Guardrails,
    HostPrompts,
    MeetingMessage,
    MeetingMode,
    MeetingRole,
    MeetingRoom,
    RoundRobinOrder,
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


class _DefenseTestParticipant(MeetingParticipant):
    """Test participant for defense order that can respond with custom messages."""
    def __init__(
        self,
        *,
        participant_id: str,
        system_prompt: str = "",
        responses: list[str] = None,
        json_response: dict = None,
    ):
        super().__init__(participant_id=participant_id)
        self.system_prompt = system_prompt
        self.responses = responses or []
        self.json_response = json_response
        self.turn_count = 0
        self.response_index = 0
    
    async def handle(self, msg: MeetingMessage, *, room):
        return []
    
    async def take_turn(self, *, room):
        self.turn_count += 1
        
        # If JSON response is set, return it
        if self.json_response is not None:
            import json
            return [
                MeetingMessage.new(
                    sender_id=self.participant_id,
                    role=MeetingRole.ASSISTANT,
                    content=json.dumps(self.json_response),
                )
            ]
        
        # Otherwise use responses list
        if self.response_index < len(self.responses):
            response = self.responses[self.response_index]
            self.response_index += 1
            return [
                MeetingMessage.new(
                    sender_id=self.participant_id,
                    role=MeetingRole.ASSISTANT,
                    content=response,
                )
            ]
        
        return []


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


def test_passive_done_via_json_content():
    """Participants can exit by emitting JSON {'done': true} in message content (no metadata)."""
    async def run():
        guard = Guardrails(
            max_turns=10,
            max_time_s=5.0,
            respond_only_if_targeted_in_passive=True,
            allow_done_exit=True,
            done_behavior=DoneBehavior.WAIT_ONLY,
        )
        room = MeetingRoom(room_id="t1b", mode=MeetingMode.PASSIVE, guardrails=guard)

        # done=False => no metadata marker; content itself is the done signal
        a = _TestParticipant(participant_id="A", reply_text='{"done": true}', done=False)
        await room.add_participant(a)

        await room.send_room(sender_id="user", role=MeetingRole.HUMAN, content="ping", targets=["A"])
        await asyncio.sleep(0.05)

        # Done signal sets wait_only
        assert room.participants()["A"].wait_only is True
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
            order=RoundRobinOrder(),
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


def test_active_mode_requires_order():
    """Test that ACTIVE mode raises ValueError if no order is specified."""
    try:
        MeetingRoom(
            room_id="test",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=5, max_time_s=5.0),
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "order" in str(e).lower()


def test_round_robin_order_basic():
    """Test basic round-robin order functionality."""
    async def run():
        room = MeetingRoom(
            room_id="t6",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=10, max_time_s=5.0),
            order=RoundRobinOrder(),
        )
        a = _TestParticipant(participant_id="A", reply_text="a")
        b = _TestParticipant(participant_id="B", reply_text="b")
        await room.add_participant(a)
        await room.add_participant(b)

        await room.run()
        msgs = room.room_messages()
        
        # Check that both participants spoke
        assert any(m.sender_id == "A" for m in msgs)
        assert any(m.sender_id == "B" for m in msgs)
        
        # Check round tracking
        assert room.current_round() > 0

    asyncio.run(run())


def test_round_robin_order_participant_inactive():
    """Test that inactive participants are skipped in round-robin."""
    async def run():
        room = MeetingRoom(
            room_id="t7",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=5, max_time_s=5.0),
            order=RoundRobinOrder(),
        )
        a = _TestParticipant(participant_id="A", reply_text="a")
        b = _TestParticipant(participant_id="B", reply_text="b")
        await room.add_participant(a)
        await room.add_participant(b)

        # Make B inactive
        b.active = False

        await room.run()
        msgs = room.room_messages()
        
        # Only A should have spoken
        assert any(m.sender_id == "A" for m in msgs)
        assert not any(m.sender_id == "B" for m in msgs)

    asyncio.run(run())


def test_round_tracking():
    """Test that rounds are tracked correctly."""
    async def run():
        room = MeetingRoom(
            room_id="t8",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=10, max_time_s=5.0),
            order=RoundRobinOrder(),
        )
        
        assert room.current_round() == 0
        room.increment_round()
        assert room.current_round() == 1
        room.reset_round()
        assert room.current_round() == 0

    asyncio.run(run())


def test_round_robin_order_with_host():
    """Test round-robin order excludes host from rotation."""
    async def run():
        room = MeetingRoom(
            room_id="t9",
            mode=MeetingMode.ACTIVE,
            guardrails=Guardrails(max_turns=5, max_time_s=5.0),
            host_id="H",
            order=RoundRobinOrder(),
        )
        host = _TestParticipant(participant_id="H", reply_text="host")
        a = _TestParticipant(participant_id="A", reply_text="a")
        b = _TestParticipant(participant_id="B", reply_text="b")
        await room.add_participant(host)
        await room.add_participant(a)
        await room.add_participant(b)

        await room.run()
        msgs = room.room_messages()
        
        # Host should speak (opening)
        assert any(m.sender_id == "H" for m in msgs)
        # A and B should also speak
        assert any(m.sender_id == "A" for m in msgs)
        assert any(m.sender_id == "B" for m in msgs)

    asyncio.run(run())


# ==================== DefenseOrder Tests ====================

def test_defense_order_basic_flow():
    """Test basic defense order flow with 3 agents."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt=(
                "Welcome to the defense meeting. "
                "Defense candidate: team_merge, you have produced an analysis. "
                "Challenge agents: skeptic and critic, your task is to assess and challenge the analysis. "
                "Begin the discussion."
            ),
            ask_candidate_prompt=(
                "team_merge, after hearing from {challenge_agents}, "
                "do you agree or disagree with their challenges? "
                "If you agree, respond with JSON: {{\"done\": true, \"final_response\": ...}}. "
                "If you disagree, respond in free text."
            ),
            turn_notification_template="{participant_id}, it's your turn",
            final_round_begin_prompt="This is the final round (Round {round_number}).",
            conclusion_round_begin_prompt=(
                "This is the conclusion round. All agents, please provide your final thoughts. "
                "team_merge will speak last with your final response."
            ),
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=2,
        )
        
        room = MeetingRoom(
            room_id="defense_test1",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=20, max_time_s=10000.0),
        )
        
        # Defense candidate (team_merge agent)
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            system_prompt="You are the team merge agent defending your analysis.",
            responses=[
                "I disagree. My analysis is sound based on the evidence.",
            ],
        )
        
        # Challenge agent 1 (skeptic)
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            system_prompt="You are a skeptical analyst who challenges investment analysis.",
            responses=[
                "I find issue with the methodology used in the analysis.",
            ],
        )
        
        # Challenge agent 2 (critic)
        critic = _DefenseTestParticipant(
            participant_id="critic",
            system_prompt="You are a critical analyst who questions assumptions.",
            responses=[
                "The assumptions in the analysis seem too optimistic.",
            ],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        msgs = room.room_messages()
        
        # Check that host sent introduction
        assert any(m.sender_id == "host" and "Welcome" in m.content for m in msgs)
        
        # Check that challenge agents spoke
        assert any(m.sender_id == "skeptic" for m in msgs)
        assert any(m.sender_id == "critic" for m in msgs)
        
        # Check that host asked candidate
        assert any(m.sender_id == "host" and "do you agree" in m.content.lower() for m in msgs)
        
        # Check that defense candidate responded
        assert any(m.sender_id == "team_merge" for m in msgs)
        
        # Should have completed at least one round
        assert room.current_round() >= 1

    asyncio.run(run())


def test_defense_order_candidate_agrees():
    """Test defense order when candidate agrees early with JSON response."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt=(
                "Defense meeting: team_merge has produced an analysis. "
                "skeptic and critic, please challenge it."
            ),
            ask_candidate_prompt="team_merge, do you agree?",
            turn_notification_template="{participant_id}, it's your turn",
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=5,
        )
        
        room = MeetingRoom(
            room_id="defense_test2",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=20, max_time_s=10.0),
        )
        
        # Defense candidate agrees
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            system_prompt="You are the team merge agent.",
            json_response={"done": True, "final_response": {"updated_analysis": "I accept the feedback."}},
        )
        
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            system_prompt="You challenge analyses.",
            responses=["Your methodology has flaws."],
        )
        
        critic = _DefenseTestParticipant(
            participant_id="critic",
            system_prompt="You question assumptions.",
            responses=["Your assumptions are too optimistic."],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        msgs = room.room_messages()
        
        # Check that candidate sent JSON response
        candidate_msgs = [m for m in msgs if m.sender_id == "team_merge"]
        assert len(candidate_msgs) > 0
        assert any("done" in m.content and "true" in m.content for m in candidate_msgs)
        
        # Check that order detected agreement
        assert order._candidate_agreed is True
        assert order._candidate_exited is True

    asyncio.run(run())


def test_defense_order_multiple_rounds():
    """Test defense order going through multiple rounds."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt="Defense meeting begins.",
            ask_candidate_prompt="team_merge, do you agree?",
            candidate_disagree_prompt="Round {round_number} continues.",
            turn_notification_template="{participant_id}, it's your turn",
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=2,
        )
        
        room = MeetingRoom(
            room_id="defense_test3",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=30, max_time_s=10.0),
        )
        
        # Defense candidate disagrees first round, agrees second
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            system_prompt="You defend your analysis.",
            responses=[
                "I disagree, my analysis is correct.",
                "After further consideration, I accept your feedback.",
            ],
        )
        
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            system_prompt="You challenge analyses.",
            responses=[
                "First round challenge.",
                "Second round challenge.",
            ],
        )
        
        critic = _DefenseTestParticipant(
            participant_id="critic",
            system_prompt="You question assumptions.",
            responses=[
                "First round criticism.",
                "Second round criticism.",
            ],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        # Should have gone through multiple rounds
        assert room.current_round() >= 1
        
        msgs = room.room_messages()
        
        # Check multiple rounds occurred
        skeptic_msgs = [m for m in msgs if m.sender_id == "skeptic"]
        assert len(skeptic_msgs) >= 1

    asyncio.run(run())


def test_defense_order_conclusion_round():
    """Test defense order reaching conclusion round when max rounds reached."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt="Defense meeting begins.",
            ask_candidate_prompt="team_merge, do you agree?",
            final_round_begin_prompt="Final round (Round {round_number}) begins.",
            conclusion_round_begin_prompt=(
                "This is the conclusion round. All agents provide final thoughts. "
                "team_merge speaks last."
            ),
            turn_notification_template="{participant_id}, it's your turn",
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=1,  # Only 1 round before conclusion
        )
        
        room = MeetingRoom(
            room_id="defense_test4",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=30, max_time_s=10.0),
        )
        
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            system_prompt="You defend your analysis.",
            responses=[
                "I disagree.",  # First round
                "Final thoughts from team_merge.",  # Conclusion round
            ],
        )
        
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            system_prompt="You challenge analyses.",
            responses=[
                "Challenge in round 1.",
                "Final thoughts from skeptic.",
            ],
        )
        
        critic = _DefenseTestParticipant(
            participant_id="critic",
            system_prompt="You question assumptions.",
            responses=[
                "Criticism in round 1.",
                "Final thoughts from critic.",
            ],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        msgs = room.room_messages()
        
        # Check conclusion round message
        assert any(
            m.sender_id == "host" and "conclusion" in m.content.lower()
            for m in msgs
        )
        
        # Check that all agents spoke in conclusion round
        conclusion_round_reached = order._in_conclusion_round
        assert conclusion_round_reached
        
        # Check that defense candidate spoke last in conclusion
        team_merge_indices = [
            i for i, m in enumerate(msgs)
            if m.sender_id == "team_merge" and "Final thoughts" in m.content
        ]
        if team_merge_indices:
            # Should be near the end
            assert team_merge_indices[-1] > len(msgs) * 0.5

    asyncio.run(run())


def test_defense_order_requires_host():
    """Test that DefenseOrder requires a host_id to be set."""
    try:
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=HostPrompts(),
            max_rounds=2,
        )
        room = MeetingRoom(
            room_id="test",
            mode=MeetingMode.ACTIVE,
            order=order,
            guardrails=Guardrails(max_turns=10, max_time_s=5.0),
        )
        assert False, "Should have raised ValueError for missing host"
    except ValueError as e:
        assert "host" in str(e).lower()


def test_defense_order_host_does_not_need_to_be_participant():
    """Test that host doesn't need to be a participant for DefenseOrder (uses deterministic messages)."""
    async def run():
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=HostPrompts(introduction_prompt="Test meeting begins."),
            max_rounds=1,
        )
        room = MeetingRoom(
            room_id="test",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",  # host_id set but no host participant added
            guardrails=Guardrails(max_turns=10, max_time_s=5.0),
        )
        # Add defense candidate but NOT host - should work for DefenseOrder
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            responses=["I disagree."],
        )
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            responses=["Challenge 1."],
        )
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        
        # Should run successfully without host participant
        await room.run()
        
        # Check that host messages were sent (via deterministic generation)
        msgs = room.room_messages()
        assert any(m.sender_id == "host" for m in msgs)
    
    asyncio.run(run())


def test_defense_order_defense_candidate_must_be_participant():
    """Test that defense candidate must be added as a participant before running."""
    async def run():
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=HostPrompts(introduction_prompt="Test"),
            max_rounds=2,
        )
        room = MeetingRoom(
            room_id="test",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=10, max_time_s=5.0),
        )
        # Add host but not defense candidate
        host = _DefenseTestParticipant(participant_id="host", responses=[])
        await room.add_participant(host)
        
        # Should raise error when running (defense candidate not in participants)
        try:
            await room.run()
            assert False, "Should have raised ValueError for missing defense candidate"
        except ValueError as e:
            assert "defense candidate" in str(e).lower() and "not in participants" in str(e).lower()
    
    asyncio.run(run())


def test_defense_order_turn_order():
    """Test that turn order follows: Host → Challenge agents → Host ask → Defense candidate."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt="Meeting begins.",
            ask_candidate_prompt="team_merge, do you agree?",
            turn_notification_template="{participant_id}, it's your turn",
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=1,
        )
        
        room = MeetingRoom(
            room_id="defense_test5",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=15, max_time_s=10.0),
        )
        
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            responses=["I disagree."],
        )
        
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            responses=["Challenge 1."],
        )
        
        critic = _DefenseTestParticipant(
            participant_id="critic",
            responses=["Challenge 2."],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        msgs = room.room_messages()
        sender_order = [m.sender_id for m in msgs if m.sender_id in ["host", "skeptic", "critic", "team_merge"]]
        
        # Host should speak first (introduction)
        assert sender_order[0] == "host"
        
        # Then challenge agents should speak
        skeptic_idx = sender_order.index("skeptic") if "skeptic" in sender_order else -1
        critic_idx = sender_order.index("critic") if "critic" in sender_order else -1
        assert skeptic_idx > 0
        assert critic_idx > 0
        
        # Host should ask candidate
        ask_idx = next(
            (i for i, m in enumerate(msgs) if m.sender_id == "host" and "do you agree" in m.content.lower()),
            -1
        )
        assert ask_idx > 0
        
        # Defense candidate should respond after host asks
        candidate_idx = sender_order.index("team_merge") if "team_merge" in sender_order else -1
        assert candidate_idx > ask_idx or ask_idx == -1

    asyncio.run(run())


def test_defense_order_challenge_agent_exit():
    """Test that challenge agents can exit and are skipped in future rounds."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt="Meeting begins.",
            ask_candidate_prompt="team_merge, do you agree?",
            turn_notification_template="{participant_id}, it's your turn",
        )
        
        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=2,
        )
        
        room = MeetingRoom(
            room_id="defense_test6",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=20, max_time_s=10.0),
        )
        
        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            responses=["I disagree.", "Still disagree."],
        )
        
        # Skeptic exits after first response
        skeptic = _DefenseTestParticipant(
            participant_id="skeptic",
            responses=["First challenge"],
            json_response={"done": True},  # Exits
        )
        
        critic = _DefenseTestParticipant(
            participant_id="critic",
            responses=["First criticism.", "Second criticism."],
        )
        
        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)
        
        await room.run()
        
        # Skeptic should be marked as wait_only after exit
        assert room.participants()["skeptic"].wait_only is True
        
        # Only critic should speak in second round (skeptic skipped)
        msgs = room.room_messages()
        skeptic_msgs = [m for m in msgs if m.sender_id == "skeptic"]
        # Should only have one message from skeptic (before exit)
        assert len(skeptic_msgs) <= 1

    asyncio.run(run())


def test_defense_order_timeout_jumps_to_conclusion_after_round():
    """If max_time_s is exceeded, defense order should finish current round then jump to conclusion."""
    async def run():
        host_prompts = HostPrompts(
            introduction_prompt="Meeting begins.",
            ask_candidate_prompt="team_merge, do you agree?",
            turn_notification_template="{participant_id}, it's your turn",
            conclusion_round_begin_prompt="CONCLUSION ROUND NOW",
        )

        order = DefenseOrder(
            defense_candidate_id="team_merge",
            host_prompts=host_prompts,
            max_rounds=99,  # don't enter conclusion via rounds; only via timeout
        )

        # max_time_s=0 triggers timeout immediately; order should still complete the round and then conclude
        room = MeetingRoom(
            room_id="defense_timeout",
            mode=MeetingMode.ACTIVE,
            order=order,
            host_id="host",
            guardrails=Guardrails(max_turns=50, max_time_s=0.0),
        )

        team_merge = _DefenseTestParticipant(
            participant_id="team_merge",
            responses=["I disagree."],
        )
        skeptic = _DefenseTestParticipant(participant_id="skeptic", responses=["Challenge"])
        critic = _DefenseTestParticipant(participant_id="critic", responses=["Criticism"])

        await room.add_participant(team_merge)
        await room.add_participant(skeptic)
        await room.add_participant(critic)

        await room.run()

        msgs = room.room_messages()
        assert any(m.sender_id == "host" and "CONCLUSION ROUND NOW" in m.content for m in msgs)

    asyncio.run(run())


def test_defense_meeting_node_updates_by_ticker_from_candidate_final_response():
    """Ultra team_merge defense meeting should update by_ticker[ticker] when candidate emits done+final_response."""
    async def run():
        # Import inside test to avoid import-time coupling in other tests
        from moose.framework.meet_room import MeetingMessage, MeetingRole

        class _FakeRoom:
            def __init__(self, **kwargs):
                self._msgs = []
                self._participants = []

            async def add_participant(self, p):
                self._participants.append(p)

            async def send_room(self, *, sender_id: str, role: MeetingRole, content: str, targets=None, metadata=None):
                self._msgs.append(MeetingMessage.new(sender_id=sender_id, role=role, content=content, metadata=metadata))
                return self._msgs[-1]

            async def run(self):
                # Simulate meeting outputs including candidate done JSON
                self._msgs.append(
                    MeetingMessage.new(
                        sender_id="skeptic",
                        role=MeetingRole.ASSISTANT,
                        content="challenge",
                        metadata={"llm_usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}, "llm_cost": 0.01},
                    )
                )
                self._msgs.append(
                    MeetingMessage.new(
                        sender_id="candidate",
                        role=MeetingRole.ASSISTANT,
                        content=json.dumps({"done": True, "final_response": {"updated": 1}}),
                        metadata={"llm_usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9}, "llm_cost": 0.02},
                    )
                )

            def room_messages(self):
                return list(self._msgs)

        # Defense meeting is now integrated into TeamMergeNode (ultra path).
        from moose.agents.finance_office.investment_research_team.workflow.nodes import team_merge as tm_mod

        # Monkeypatch the MeetingRoom symbol used inside TeamMergeNode._run_defense_meeting_for_ticker (imported at runtime)
        # by patching `moose.framework.meet_room.MeetingRoom`.
        import moose.framework.meet_room as meet_room_mod
        old_room = meet_room_mod.MeetingRoom
        meet_room_mod.MeetingRoom = _FakeRoom
        try:
            class _Analyzer:
                def __init__(self):
                    self.agent_name = "test"
                    self.config = {
                        "custom": {
                            "llm_config": {"model": "dummy", "temperature": 0.0, "kwargs": {}},
                            "team_merge": {
                                "defense_meeting": {
                                    "enabled": True,
                                    "max_rounds": 2,
                                    "guardrails": {"max_turns": 10, "max_time_s": 1.0},
                                    "challenge_agents": [
                                        {"id": "skeptic", "display_name": "skeptic", "model": "dummy", "temperature": 0.0}
                                    ],
                                }
                            },
                        }
                    }

                def get_node_llm_config(self, node_name: str):
                    return {"model": "dummy", "temperature": 0.0, "kwargs": {}}

            node = tm_mod.TeamMergeNode(analyzer=_Analyzer(), logger=None)

            class _FakeMergeResp:
                def __init__(self):
                    self.content = json.dumps({"orig": 1})
                    self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                    self.cost = 0.0
                    self.model = "dummy"
                    self.request_id = "r0"

            class _FakeMergeClient:
                async def send_message(self, *, message: str, system_message: str):
                    return _FakeMergeResp()

            # Force team_merge to use a deterministic client (so the baseline call doesn't hit real providers).
            node._get_merge_client = lambda _state: _FakeMergeClient()  # type: ignore[assignment]

            state = {
                "per_ticker_merge_mode": True,
                "metadata": {"granularity": "ultra"},
                "ticker_list": ["AAPL"],
                "merge_system_message": "s",
                "merge_user_message": "u",
                "llm_usage_total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "llm_cost_total": 0.0,
                "routing": {"tickers": ["AAPL"], "update_memory": False},
                "subagent_reports": {},
                "evidence": [],
            }

            out = await node.run(state)
            assert out["final"]["result"]["by_ticker"]["AAPL"]["updated"] == 1
            assert out["llm_usage_total"]["total_tokens"] == 12
            assert abs(out["llm_cost_total"] - 0.03) < 1e-9
        finally:
            meet_room_mod.MeetingRoom = old_room

    asyncio.run(run())
