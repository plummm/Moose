from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Optional

from .types import HostPrompts, MeetingRole


class MeetingOrder(ABC):
    """
    Abstract base class for meeting room turn-taking orders.
    
    Orders control how participants speak in ACTIVE mode meetings,
    including turn sequence, host coordination, and round management.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this order (e.g., 'round_robin', 'defense')."""
        raise NotImplementedError

    @abstractmethod
    def requires_host(self) -> bool:
        """
        Whether this order requires a host participant.
        
        Returns:
            True if host_id must be set, False otherwise
        """
        raise NotImplementedError

    def validate_setup(self, room: Any) -> None:
        """
        Validate that the meeting room is properly configured for this order.
        
        This performs basic validation (e.g., host_id is set). Participant existence
        is validated at runtime when the meeting actually runs, since participants
        may be added after room creation.
        
        Args:
            room: MeetingRoom instance
            
        Raises:
            ValueError: If room configuration is invalid for this order
        """
        if self.requires_host():
            if not room.host_id:
                raise ValueError(f"Order '{self.name}' requires a host_id to be set")
            # Note: Participant existence is checked at runtime in _run_active()
            # because participants may be added after room creation

    @abstractmethod
    def get_next_speaker(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """
        Get the next participant who should speak.
        
        Args:
            room: MeetingRoom instance
            context: Context dict with meeting state information
            
        Returns:
            Participant ID to speak next, or None if no one should speak
        """
        raise NotImplementedError

    def should_host_speak(self, room: Any, context: dict[str, Any]) -> bool:
        """
        Determine if the host should speak at this point.
        
        Args:
            room: MeetingRoom instance
            context: Context dict with meeting state information
            
        Returns:
            True if host should speak now
        """
        return False

    def get_host_message_type(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """
        Get the type of host message to send.
        
        Args:
            room: MeetingRoom instance
            context: Context dict with meeting state information
            
        Returns:
            Message type string (e.g., "introduction", "ask_candidate", "turn_notification")
            or None if no specific message type
        """
        return None

    def is_conclusion_round(self, room: Any) -> bool:
        """
        Check if we're in the conclusion phase of the meeting.
        
        Args:
            room: MeetingRoom instance
            
        Returns:
            True if in conclusion phase
        """
        return False

    def is_final_round(self, room: Any) -> bool:
        """
        Check if the current round is the final round.
        
        Args:
            room: MeetingRoom instance
            
        Returns:
            True if current round is the final round
            
        Note:
            Base implementation returns False. Subclasses should override
            if they need to detect final rounds based on round count.
        """
        return False

    def on_round_start(self, room: Any) -> None:
        """
        Hook called when a new round starts.
        
        Args:
            room: MeetingRoom instance
        """
        pass

    def on_round_end(self, room: Any) -> None:
        """
        Hook called when a round ends.
        
        Args:
            room: MeetingRoom instance
        """
        pass

    @abstractmethod
    async def run_active(self, room: Any) -> None:
        """Execute the ACTIVE mode meeting loop for this order."""
        raise NotImplementedError


class RoundRobinOrder(MeetingOrder):
    """
    Round-robin turn-taking order: participants speak in circular order.
    
    This is the default order for traditional ACTIVE mode meetings.
    Host participation is optional; if present, host can open/close the meeting.
    """

    def __init__(self):
        self._speaker_index = 0
        self._last_round_speakers: set[str] = set()
        self._active_participant_list: list[str] = []

    @property
    def name(self) -> str:
        return "round_robin"

    def requires_host(self) -> bool:
        return False

    def _update_active_list(self, room: Any) -> list[str]:
        """Update and return the list of active participants."""
        participants = room.participants()
        active_list = [
            pid
            for pid in participants.keys()
            if participants[pid].active
            and not participants[pid].wait_only
            and (pid != room.host_id)  # Exclude host from round-robin
        ]
        
        # If list changed, reset index
        if active_list != self._active_participant_list:
            self._active_participant_list = active_list
            # Reset index if current speaker is no longer in list
            if (
                self._speaker_index >= len(active_list)
                or (
                    len(active_list) > 0
                    and self._speaker_index < len(active_list)
                    and active_list[self._speaker_index]
                    not in [
                        p
                        for p in participants.keys()
                        if participants[p].active
                    ]
                )
            ):
                self._speaker_index = 0
        
        return active_list

    def get_next_speaker(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """Get next participant in round-robin order."""
        active_list = self._update_active_list(room)
        
        if not active_list:
            return None
        
        # Wrap around if index exceeds list length (completed a full cycle)
        if self._speaker_index >= len(active_list):
            # Completed a full round - reset and signal round increment
            self._speaker_index = 0
            self._last_round_speakers = set()
            room.increment_round()
        
        speaker_id = active_list[self._speaker_index]
        self._speaker_index += 1
        
        # Track speakers in current round
        self._last_round_speakers.add(speaker_id)
        
        return speaker_id

    def should_host_speak(self, room: Any, context: dict[str, Any]) -> bool:
        """Host speaks at opening and optionally at closing."""
        # Opening handled separately in _run_active
        # Check for closing: if no one spoke in a full cycle
        if not room.host_id:
            return False
        
        # Check if we've completed a full cycle with no messages
        spoke_in_round = context.get("spoke_in_round", False)
        message_count_this_round = context.get("message_count_this_round", 0)
        
        # If we've seen all participants and no one spoke, host should conclude
        active_list = self._update_active_list(room)
        if len(self._last_round_speakers) >= len(active_list) and not spoke_in_round:
            return True
        
        return False

    def get_host_message_type(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """Get host message type based on context."""
        if self.should_host_speak(room, context):
            return "conclusion"
        return None

    def on_round_start(self, room: Any) -> None:
        """Reset round tracking when new round starts."""
        self._last_round_speakers = set()

    def on_round_end(self, room: Any) -> None:
        """Clean up when round ends."""
        # Reset speaker tracking for next round
        self._last_round_speakers = set()
    
    async def run_active(self, room: Any) -> None:
        """Execute round-robin meeting loop."""
        from .room import MeetingRoom
        
        # Context dict for tracking meeting state
        context: dict[str, Any] = {
            "last_speaker": None,
            "speakers_this_round": [],
            "message_count_this_round": 0,
            "spoke_in_round": False,
        }
        
        # Opening: host posts topic prompt if desired (host implements take_turn)
        if room.host_id:
            host = room.participants().get(room.host_id)
            if host and host.active and host.can_emit(room.guardrails):
                for out in await host.take_turn(room=room):
                    await room.publish(out)
                    await room.apply_done_signal(out)
        
        # Initialize round tracking
        round_start_count = len(room.room_messages())
        last_round_number = room.current_round()
        self.on_round_start(room)
        
        while not room.should_stop():
            room.increment_turn()
            
            # Check for round transition (detected by order)
            if room.current_round() != last_round_number:
                self.on_round_end(room)
                self.on_round_start(room)
                last_round_number = room.current_round()
                round_start_count = len(room.room_messages())
                context["speakers_this_round"] = []
                context["message_count_this_round"] = 0
                context["spoke_in_round"] = False
            
            # Check if host should speak before getting next speaker
            if self.should_host_speak(room, context) and room.host_id:
                host = room.participants().get(room.host_id)
                if host and host.active and host.can_emit(room.guardrails):
                    msg_type = self.get_host_message_type(room, context)
                    if msg_type == "conclusion":
                        await room.send_room(
                            sender_id=room.host_id,
                            role=MeetingRole.SYSTEM,
                            content="Final check: does anyone have last points? If not, we will conclude.",
                            targets=None,
                        )
                        break
                    # Host can implement custom logic in take_turn
                    for out in await host.take_turn(room=room):
                        await room.publish(out)
                        await room.apply_done_signal(out)
                        # Update context
                        context["last_speaker"] = room.host_id
                        if room.host_id not in context["speakers_this_round"]:
                            context["speakers_this_round"].append(room.host_id)
                        context["message_count_this_round"] += 1
                        context["spoke_in_round"] = True
                    continue  # Skip to next iteration after host speaks
            
            # Get next speaker from order
            next_speaker = self.get_next_speaker(room, context)
            if next_speaker is None:
                # No one should speak
                # Check for stagnation: if we completed a cycle and no messages
                active_participants = [
                    pid
                    for pid, p in room.participants().items()
                    if p.active and not p.wait_only and pid != room.host_id
                ]
                if len(active_participants) > 0 and len(context.get("speakers_this_round", [])) >= len(active_participants):
                    # Completed a full cycle
                    if not context.get("spoke_in_round", False):
                        # No one spoke this cycle - check for host conclusion
                        if room.host_id and self.should_host_speak(room, context):
                            continue
                        # Check for stagnation
                        if len(room.room_messages()) == round_start_count and room.host_id:
                            await room.send_room(
                                sender_id=room.host_id,
                                role=MeetingRole.SYSTEM,
                                content="No new messages this round. Concluding the meeting.",
                                targets=None,
                            )
                            break
                    context["spoke_in_round"] = False
                    context["speakers_this_round"] = []
                continue
            
            p = room.participants().get(next_speaker)
            if p is None or not p.active or p.wait_only:
                continue
            if not p.can_emit(room.guardrails):
                continue
            
            before = len(room.room_messages())
            context["last_speaker"] = next_speaker
            if next_speaker not in context["speakers_this_round"]:
                context["speakers_this_round"].append(next_speaker)
            
            # Ensure MeetingRoom.current()/current_sender_id() are set for tool execution
            room_token = MeetingRoom.set_current(room)
            sender_token = MeetingRoom.set_current_sender_id(next_speaker)
            try:
                outs = await p.take_turn(room=room)
            finally:
                MeetingRoom.reset_current_sender_id(sender_token)
                MeetingRoom.reset_current(room_token)
            
            for out in outs or []:
                await room.publish(out)
                await room.apply_done_signal(out)
            
            after = len(room.room_messages())
            context["message_count_this_round"] += after - before
            if after > before:
                context["spoke_in_round"] = True
        
        # Final cleanup
        self.on_round_end(room)


class DefenseOrder(MeetingOrder):
    """
    Defense order: A structured meeting where a defense candidate (e.g., team_merge agent)
    defends their analysis against challenge agents.
    
    Turn order per round:
    1. Host → sends introduction or round message
    2. Challenge agents → speak one by one
    3. Host → asks defense candidate if they agree
    4. Defense candidate → responds (can agree with JSON {"done": true, "final_response": ...} or disagree)
    5. If disagree → next round starts
    
    Special handling:
    - If defense candidate agrees (JSON with done: true), meeting ends early
    - If max rounds reached, enters conclusion round
    - In conclusion round, all agents speak, defense candidate speaks last
    - If defense candidate exits (done: true), meeting ends
    """
    
    def __init__(
        self,
        *,
        defense_candidate_id: str,
        host_prompts: HostPrompts,
        max_rounds: int = 5,
    ):
        """
        Initialize defense order.
        
        Args:
            defense_candidate_id: Participant ID of the defense candidate (e.g., "team_merge_agent")
            host_prompts: HostPrompts dataclass with message templates
            max_rounds: Maximum number of challenge rounds before conclusion round
        """
        self.defense_candidate_id = str(defense_candidate_id).strip()
        self.host_prompts = host_prompts
        self.max_rounds = int(max_rounds)
        
        # Phase tracking: "introduction", "challenge_round", "ask_agree", "candidate_response", "conclusion_round"
        self._phase: str = "introduction"
        
        # Round tracking
        self._challenge_agents_spoken_this_round: set[str] = set()
        self._challenge_agents_list: list[str] = []
        self._in_conclusion_round: bool = False
        self._conclusion_agents_spoken: set[str] = set()
        
        # Meeting termination flag. Under the new semantics, the meeting ends ONLY when all
        # challenge agents have exited (wait_only/left). Candidate output never ends the meeting.
        self._candidate_exited: bool = False

        # Timeout handling: when time is exceeded, finish the current round then jump to conclusion.
        self._timeout_triggered: bool = False
        self._force_conclusion_after_round: bool = False
        self._force_conclusion: bool = False
        
    
    @property
    def name(self) -> str:
        return "defense"
    
    def requires_host(self) -> bool:
        return True
    
    def validate_setup(self, room: Any) -> None:
        """
        Validate basic setup for defense order.
        
        Defense candidate existence is validated at runtime when the meeting runs,
        since participants may be added after room creation.
        """
        super().validate_setup(room)
        # Note: Defense candidate existence is checked at runtime in _run_active()
    
    def _get_challenge_agents(self, room: Any) -> list[str]:
        """Get list of challenge agents (all participants except host and defense candidate)."""
        participants = room.participants()
        challenge_agents = [
            pid
            for pid in participants.keys()
            if pid != room.host_id
            and pid != self.defense_candidate_id
            and participants[pid].active
            and not participants[pid].wait_only
        ]
        return challenge_agents
    
    def _update_challenge_agents_list(self, room: Any) -> None:
        """Update the list of challenge agents."""
        self._challenge_agents_list = self._get_challenge_agents(room)
    
    def _check_message_for_done(self, message: Any) -> tuple[bool, Optional[dict[str, Any]]]:
        """
        Check if a message contains {"done": true} JSON.
        
        Returns:
            (is_done, parsed_json) - is_done is True if done found, parsed_json is the JSON if parseable
        """
        if not hasattr(message, 'content'):
            return False, None
        
        content = str(message.content or "").strip()
        if not content:
            return False, None
        
        # Try to parse JSON from content
        # Look for JSON object in the content
        try:
            # Try parsing entire content as JSON
            parsed = json.loads(content)
            if isinstance(parsed, dict) and parsed.get("done") is True:
                return True, parsed
        except (json.JSONDecodeError, TypeError):
            pass
        
        # Try to find JSON object within content
        try:
            start_idx = content.find("{")
            end_idx = content.rfind("}")
            if start_idx >= 0 and end_idx > start_idx:
                json_str = content[start_idx:end_idx + 1]
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and parsed.get("done") is True:
                    return True, parsed
        except (json.JSONDecodeError, TypeError):
            pass
        
        return False, None
    
    def get_next_speaker(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """
        Get next speaker based on current phase.
        
        Turn order:
        - Introduction: return None (host handles via should_host_speak)
        - Challenge round: return next challenge agent
        - Ask agree: return None (host handles)
        - Candidate response: return defense candidate
        - Conclusion round: return next agent who hasn't spoken, defense candidate last
        """
        # Check if defense candidate exited
        if self._candidate_exited:
            return None
        
        # Update challenge agents list
        self._update_challenge_agents_list(room)
        
        if self._phase == "introduction":
            # Host will handle introduction, but we need to prepare next phase
            # After host sends introduction, it will transition automatically
            return None
        
        elif self._phase == "challenge_round":
            # Find next challenge agent who hasn't spoken this round
            for agent_id in self._challenge_agents_list:
                if agent_id not in self._challenge_agents_spoken_this_round:
                    return agent_id
            # All challenge agents have spoken, move to ask_agree phase
            if len(self._challenge_agents_spoken_this_round) >= len(self._challenge_agents_list):
                self._phase = "ask_agree"
                return None  # Host will ask
            return None
        
        if self._phase == "ask_agree":
            # Host will send ask message, then candidate responds
            # Transition happens after host sends message
            return None
        
        elif self._phase == "candidate_response":
            # Defense candidate's turn to respond
            candidate = room.participants().get(self.defense_candidate_id)
            if candidate and candidate.active and not candidate.wait_only:
                return self.defense_candidate_id
            return None
        
        elif self._phase == "conclusion_round":
            # In conclusion: all agents speak, defense candidate last
            participants = room.participants()
            
            # First, challenge agents
            for agent_id in self._challenge_agents_list:
                if agent_id not in self._conclusion_agents_spoken:
                    p = participants.get(agent_id)
                    if p and p.active and not p.wait_only:
                        return agent_id
            
            # Then defense candidate (if hasn't spoken in conclusion)
            if self.defense_candidate_id not in self._conclusion_agents_spoken:
                candidate = participants.get(self.defense_candidate_id)
                if candidate and candidate.active and not candidate.wait_only:
                    return self.defense_candidate_id
            
            return None
        
        return None
    
    def should_host_speak(self, room: Any, context: dict[str, Any]) -> bool:
        """Determine if host should speak now."""
        if self._phase == "introduction":
            # Host should send introduction at the start
            return True
        
        if self._phase == "ask_agree":
            # Host should ask defense candidate if they agree
            return True

        # In these phases, the host should announce the next speaker before they speak.
        #
        # Note: We intentionally do NOT announce "it's your turn" for the defense candidate
        # in candidate_response, because the host already asked them "do you agree?".
        if self._phase in ("challenge_round", "conclusion_round"):
            next_speaker = self._peek_next_speaker(room)
            if not next_speaker:
                return False

            # Normal alternation: participant spoke -> host announces next.
            if context.get("last_speaker") != room.host_id:
                return True

            # Special case: host just delivered an instruction (intro / ask_candidate),
            # and must immediately follow up with a "X, it's your turn" announcement.
            last_host_msg_type = context.get("last_host_msg_type")
            if last_host_msg_type in ("introduction", "ask_candidate"):
                return True
            return False
        
        return False
    
    def get_host_message_type(
        self, room: Any, context: dict[str, Any]
    ) -> Optional[str]:
        """Get host message type for generating appropriate message."""
        if self._phase == "introduction":
            return "introduction"
        
        if self._phase == "ask_agree":
            return "ask_candidate"
        
        if self._phase in ("challenge_round", "conclusion_round"):
            next_speaker = self._peek_next_speaker(room)
            if next_speaker:
                return "turn_notification"
        
        return None
    
    def _generate_host_message(self, room: Any, message_type: str) -> str:
        """Generate host message based on type and prompts."""
        if message_type == "introduction":
            # Conclusion round intro
            if self._in_conclusion_round:
                prompt = (
                    self.host_prompts.conclusion_round_begin_prompt
                    or "This is the conclusion round. Please provide your final thoughts."
                )
                try:
                    prompt = prompt.format(round_number=room.current_round())
                except (KeyError, ValueError):
                    pass
                return prompt

            # Final challenge round intro (before conclusion)
            if self.is_final_round(room):
                prompt = (
                    self.host_prompts.final_round_begin_prompt
                    or f"This is the final round (Round {room.current_round()})."
                )
                try:
                    prompt = prompt.format(round_number=room.current_round())
                except (KeyError, ValueError):
                    pass
                return prompt

            # Round 0 uses the full introduction. Subsequent non-final challenge rounds should
            # NOT repeat the full intro; instead, announce that a new round is beginning because
            # the candidate disagreed in the prior round.
            if room.current_round() == 0:
                return self.host_prompts.introduction_prompt or "Welcome to the defense meeting."

            prompt = (
                self.host_prompts.candidate_disagree_prompt
                or "The candidate did not agree. Round {round_number} begins."
            )
            try:
                prompt = prompt.format(round_number=room.current_round())
            except (KeyError, ValueError):
                pass
            return prompt
        
        if message_type == "ask_candidate":
            # Format ask_candidate_prompt if it has placeholders
            prompt = self.host_prompts.ask_candidate_prompt or f"{self.defense_candidate_id}, do you agree with the challenges?"
            try:
                # Try to format with challenge agents list
                challenge_names = ", ".join(self._challenge_agents_list)
                prompt = prompt.format(
                    defense_candidate_id=self.defense_candidate_id,
                    challenge_agents=challenge_names,
                    round_number=room.current_round(),
                )
            except (KeyError, ValueError):
                pass  # Use prompt as-is if formatting fails
            return prompt
        
        if message_type == "turn_notification":
            next_speaker = self._peek_next_speaker(room)
            if next_speaker:
                template = self.host_prompts.turn_notification_template or "{participant_id}, it's your turn"
                try:
                    return template.format(participant_id=next_speaker)
                except (KeyError, ValueError):
                    return f"{next_speaker}, it's your turn"
        
        return ""
    
    def is_conclusion_round(self, room: Any) -> bool:
        """Check if we're in conclusion round."""
        return self._in_conclusion_round
    
    def is_final_round(self, room: Any) -> bool:
        """
        Check if current round is the final *challenge* round (right before conclusion).

        Semantics:
        - challenge rounds: 0 .. max_rounds-1
        - conclusion round starts at round == max_rounds
        """
        if self.max_rounds <= 0:
            return False
        return (not self._in_conclusion_round) and (room.current_round() == (self.max_rounds - 1))
    
    def on_round_start(self, room: Any) -> None:
        """Called when a new round starts."""
        # Round lifecycle always begins with a host "introduction" message.
        # The meeting loop will transition into either challenge_round or conclusion_round
        # after the host sends that introduction.

        # Enter conclusion round once we have completed max_rounds challenge rounds,
        # OR if we were forced into conclusion by timeout.
        if self._force_conclusion or (room.current_round() >= self.max_rounds):
            self._in_conclusion_round = True
            self._conclusion_agents_spoken = set()
        else:
            self._in_conclusion_round = False
            self._challenge_agents_spoken_this_round = set()

        self._update_challenge_agents_list(room)
        self._phase = "introduction"

    def _peek_next_speaker(self, room: Any) -> Optional[str]:
        """
        Peek the next speaker without mutating phase or round state.

        This is used for host "turn notification" prompts; it must be side-effect free.
        """
        if self._candidate_exited:
            return None

        # Keep challenge list current
        self._update_challenge_agents_list(room)

        if self._phase == "challenge_round":
            for agent_id in self._challenge_agents_list:
                if agent_id not in self._challenge_agents_spoken_this_round:
                    p = room.participants().get(agent_id)
                    if p and p.active and not p.wait_only:
                        return agent_id
            return None

        if self._phase == "conclusion_round":
            participants = room.participants()
            for agent_id in self._challenge_agents_list:
                if agent_id not in self._conclusion_agents_spoken:
                    p = participants.get(agent_id)
                    if p and p.active and not p.wait_only:
                        return agent_id
            if self.defense_candidate_id not in self._conclusion_agents_spoken:
                c = participants.get(self.defense_candidate_id)
                if c and c.active and not c.wait_only:
                    return self.defense_candidate_id
            return None

        if self._phase == "candidate_response":
            c = room.participants().get(self.defense_candidate_id)
            if c and c.active and not c.wait_only:
                return self.defense_candidate_id
            return None

        # introduction / ask_agree: host speaks
        return None
    
    def on_round_end(self, room: Any) -> None:
        """Called when a round ends."""
        # Clean up round-specific state
        if self._phase == "challenge_round":
            self._challenge_agents_spoken_this_round = set()
        elif self._phase == "conclusion_round":
            self._conclusion_agents_spoken = set()
    
    async def run_active(self, room: Any) -> None:
        """Execute defense order meeting loop."""
        from .room import MeetingRoom
        
        # Context dict for tracking meeting state
        context: dict[str, Any] = {
            "last_speaker": None,
            "speakers_this_round": [],
            "message_count_this_round": 0,
            "spoke_in_round": False,
            "last_host_msg_type": None,
        }
        
        # Initialize round tracking
        last_round_number = room.current_round()
        self.on_round_start(room)
        
        # We do NOT stop immediately on timeouts; instead, we finish the current round
        # and then force a conclusion round. We still respect max_turns.
        while not room.is_turn_limit_exceeded():
            room.increment_turn()

            # End condition: meeting ends ONLY when all challenge agents have exited
            # (they become wait_only or are removed). We end at "round finish", i.e.,
            # after the candidate response in that round, so we do NOT exit here.

            # Timeout trigger: mark that we should force conclusion after finishing this round.
            if (not self._timeout_triggered) and room.is_time_exceeded() and (not self._in_conclusion_round):
                self._timeout_triggered = True
                self._force_conclusion_after_round = True
            
            # Check for round transition (detected by order)
            if room.current_round() != last_round_number:
                self.on_round_end(room)
                self.on_round_start(room)
                last_round_number = room.current_round()
                context["speakers_this_round"] = []
                context["message_count_this_round"] = 0
                context["spoke_in_round"] = False
            
            # Check if host should speak before getting next speaker
            if self.should_host_speak(room, context) and room.host_id:
                msg_type = self.get_host_message_type(room, context)
                host_content = self._generate_host_message(room, msg_type or "")
                
                if host_content:
                    host_msg = await room.send_room(
                        sender_id=room.host_id,
                        role=MeetingRole.SYSTEM,
                        content=host_content,
                        targets=None,
                    )
                    
                    # Update context after host message
                    context["last_speaker"] = room.host_id
                    context["last_host_msg_type"] = msg_type
                    if room.host_id not in context["speakers_this_round"]:
                        context["speakers_this_round"].append(room.host_id)
                    context["message_count_this_round"] += 1
                    context["spoke_in_round"] = True
                    
                    # Transition phases after host sends message
                    if self._phase == "introduction":
                        # After introduction, move to challenge_round or conclusion_round
                        if self._in_conclusion_round:
                            self._phase = "conclusion_round"
                        else:
                            self._phase = "challenge_round"
                            self._update_challenge_agents_list(room)
                    elif self._phase == "ask_agree":
                        # After asking, move to candidate_response
                        self._phase = "candidate_response"
                    continue  # Skip to next iteration after host speaks
            
            # Get next speaker from order
            next_speaker = self.get_next_speaker(room, context)
            if next_speaker is None:
                # In conclusion round, once everyone has spoken, we can end.
                if self._phase == "conclusion_round" and self._peek_next_speaker(room) is None:
                    break
                continue
            
            p = room.participants().get(next_speaker)
            if p is None or not p.active or p.wait_only:
                continue
            if not p.can_emit(room.guardrails):
                continue
            
            before = len(room.room_messages())
            context["last_speaker"] = next_speaker

            if next_speaker not in context["speakers_this_round"]:
                context["speakers_this_round"].append(next_speaker)
            
            # Ensure MeetingRoom.current()/current_sender_id() are set for tool execution
            room_token = MeetingRoom.set_current(room)
            sender_token = MeetingRoom.set_current_sender_id(next_speaker)
            try:
                outs = await p.take_turn(room=room)
            finally:
                MeetingRoom.reset_current_sender_id(sender_token)
                MeetingRoom.reset_current(room_token)
            
            for out in outs or []:
                await room.publish(out)
                await room.apply_done_signal(out)
                
                # Process message through order to track phases, exits, etc.
                should_end = self.process_participant_message(room, out, next_speaker)
                if should_end:
                    # Meeting end condition met
                    break
            
            after = len(room.room_messages())
            context["message_count_this_round"] += after - before
            if after > before:
                context["spoke_in_round"] = True
            
            # Check if we should end (e.g., defense candidate exited)
            if self._candidate_exited:
                break
        
        # Final cleanup
        self.on_round_end(room)
    
    def process_participant_message(self, room: Any, message: Any, participant_id: str) -> bool:
        """
        Process a message from a participant.
        
        Returns:
            True if meeting should end.
        """
        # Track who has spoken
        if self._phase == "challenge_round":
            if participant_id in self._challenge_agents_list:
                self._challenge_agents_spoken_this_round.add(participant_id)
        elif self._phase == "conclusion_round":
            self._conclusion_agents_spoken.add(participant_id)

        # New semantics: the candidate never ends the meeting by its output.
        # We only end when all challenge agents have exited (wait_only/left).
        #
        # To preserve "end at current round finish", we check this at the point where the round
        # naturally completes: right after the candidate's response in candidate_response,
        # or after the candidate speaks in conclusion_round.
        if participant_id == self.defense_candidate_id:
            if self._phase in ("candidate_response", "conclusion_round"):
                self._update_challenge_agents_list(room)
                if len(self._challenge_agents_list) == 0:
                    self._candidate_exited = True
                    return True

            # Candidate responded; proceed to next round as usual (unless we ended above).
            if self._phase == "candidate_response":
                # If timeout was triggered, finish this round and jump to conclusion next.
                if self._force_conclusion_after_round and not self._in_conclusion_round:
                    self._force_conclusion_after_round = False
                    self._force_conclusion = True
                room.increment_round()
                self._phase = "introduction"
                self._challenge_agents_spoken_this_round = set()
                self._update_challenge_agents_list(room)

        return False

