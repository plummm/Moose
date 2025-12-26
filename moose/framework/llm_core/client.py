"""Universal LLM client for interacting with multiple LLM providers using LangChain."""

import os
import inspect
import uuid
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple, TYPE_CHECKING
from moose.framework.llm_core.models import Message, MessageRole, LLMResponse
from moose.framework.llm_core.providers import LLMProvider, get_provider
from moose.framework.llm_core.cost_tracker import CostTracker
from moose.framework.llm_core.langchain_integration import LangChainLLM
from moose.framework.llm_core.config import ModelConfig
from moose.framework.logging import get_core_logger
from moose.framework.llm_core.tool_runtime import ToolRuntime

if TYPE_CHECKING:
    from moose.framework.llm_core.tool_runtime import ToolRuntime

# Try to import tiktoken for token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    tiktoken = None


def _infer_agent_name_from_stack() -> Optional[str]:
    """
    Best-effort inference of main agent name from call stack.

    Looks for a frame whose filename contains: moose/agents/<agent_name>/...
    Returns <agent_name> if found, else None.
    """
    try:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back  # caller
        while frame is not None:
            filename = frame.f_code.co_filename or ""
            parts = Path(filename).parts
            for i in range(len(parts) - 2):
                if parts[i] == "moose" and parts[i + 1] == "agents":
                    cand = parts[i + 2]
                    if cand:
                        return cand
            frame = frame.f_back
    except Exception:
        return None
    return None


class LLMClient:
    """
    Universal LLM client that provides a unified interface for multiple LLM providers.
    
    Supports: OpenAI, Anthropic (Claude), and Google (Gemini) models.
    Uses LangChain native provider classes internally.
    """
    
    _cost_tracker: Optional[CostTracker] = None
    
    def __init__(
        self,
        model: str,
        provider: Optional[LLMProvider] = None,
        api_key: Optional[str] = None,
        temperature: float = 1.0,
        max_output_tokens: Optional[int] = None,
        max_input_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        config: Optional[ModelConfig] = None,
        tools: Optional[List[Any]] = None,
        enable_multi_stage_reasoning: bool = False,
        multi_stage_marker: str = "<FINAL_ANSWER>",
        max_tool_iterations: int = 20,
        agent_name: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize the LLM client.
        
        Args:
            model: Model name (e.g., "gpt-4", "claude-3-opus-20240229", "gemini-pro")
            provider: Explicit provider. If None, will be inferred from model name.
            api_key: API key for the provider. If None, will use environment variables.
            temperature: Sampling temperature (0.0 to 2.0)
            max_output_tokens: Maximum output tokens to generate (if unset, uses config max_output_tokens when available)
            max_input_tokens: Maximum input tokens for the model (if unset, uses config max_input_tokens when available; default fallback: 128000)
            timeout: Request timeout in seconds
            config: Optional ModelConfig instance (for cost calculation)
            tools: Optional list of LangChain tools to bind to the LLM
            enable_multi_stage_reasoning: Enable planner/executor loop for iterative tool calling
            multi_stage_marker: Marker text that signals completion in multi-stage mode
            max_tool_iterations: Maximum number of tool call iterations (default: 20)
            **kwargs: Additional provider-specific parameters
        """
        self.logger = get_core_logger()
        self.model = model
        self.provider = provider or get_provider(model)
        self.temperature = temperature
        # Allow legacy kwargs-style overrides too
        if max_input_tokens is None and "max_input_tokens" in kwargs:
            try:
                max_input_tokens = int(kwargs.pop("max_input_tokens"))
            except Exception:
                max_input_tokens = None

        if max_output_tokens is None and "max_output_tokens" in kwargs:
            try:
                max_output_tokens = int(kwargs.pop("max_output_tokens"))
            except Exception:
                max_output_tokens = None

        self.max_output_tokens = max_output_tokens
        self.max_input_tokens = max_input_tokens  # may be filled from config below
        self.timeout = timeout
        self.extra_params = kwargs
        # Main-agent attribution for cost tracking + UI rollups
        self.agent_name = agent_name or _infer_agent_name_from_stack()
        
        # Initialize token encoder for counting
        self._token_encoder = None
        if TIKTOKEN_AVAILABLE:
            try:
                self._token_encoder = self._get_token_encoder()
            except Exception as e:
                self.logger.warning(f"Failed to initialize token encoder: {e}. Chunking may not work correctly.")
        
        # Set up API key
        self.api_key = api_key or self._get_api_key()
        if self.api_key:
            self._set_api_key_env()
        
        # Initialize cost tracker if not already done
        if LLMClient._cost_tracker is None:
            LLMClient._cost_tracker = CostTracker()
        
        # Load config for cost calculation if not provided
        if config is None:
            try:
                config = ModelConfig()
            except Exception as e:
                self.logger.warning(f"Failed to load config: {e}, cost calculation may not work")
                config = None

        # Apply model token limits from config if caller didn't provide explicit overrides.
        if config is not None:
            try:
                mi = config.get_model_info(self.model) or {}
            except Exception:
                mi = {}
            if self.max_input_tokens is None:
                try:
                    v = mi.get("max_input_tokens")
                    self.max_input_tokens = int(v) if v is not None else None
                except Exception:
                    self.max_input_tokens = None
            if self.max_output_tokens is None:
                try:
                    v = mi.get("max_output_tokens")
                    self.max_output_tokens = int(v) if v is not None else None
                except Exception:
                    self.max_output_tokens = None

        # Final fallback defaults when config doesn't have the model.
        if self.max_input_tokens is None:
            self.max_input_tokens = 128000
        # Keep the historical attribute name for internal/backward compatibility.
        self.max_tokens = self.max_output_tokens
        
        # Store tools for potential tool execution
        self.tools = tools or []
        self.tool_map = {}
        if self.tools:
            # Create a map of tool names to tool instances for easy lookup
            for tool in self.tools:
                if hasattr(tool, 'name'):
                    self.tool_map[tool.name] = tool
        
        # Multi-stage reasoning configuration
        self.enable_multi_stage_reasoning = enable_multi_stage_reasoning
        self.multi_stage_marker = multi_stage_marker
        self.max_tool_iterations = max_tool_iterations
        
        # Initialize LangChain LLM wrapper
        try:
            self.langchain_llm = LangChainLLM(
                model=model,
                temperature=temperature,
                max_tokens=self.max_output_tokens,
                timeout=timeout,
                config=config,
                tools=tools,
                **kwargs
            )
            self.logger.debug(f"Initialized LangChain LLM wrapper for {model}")
            if self.tools:
                self.logger.debug(f"Bound {len(self.tools)} tools to LLM")
        except Exception as e:
            self.logger.error(f"Failed to initialize LangChain LLM: {e}")
            raise
        
        self.logger.debug(f"Initialized LLM client: {self.provider.value}/{model}")
    
    def send_message_sync(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Synchronous wrapper for send_message (for backward compatibility).
        
        This method runs the async send_message in an event loop.
        For new code, prefer using await send_message() directly.
        
        Args:
            message: User message (string or Message object)
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message to set behavior
            **kwargs: Additional parameters to override defaults
        
        Returns:
            LLMResponse object containing the response
        """
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running, we need to use a different approach
                # This can happen in Jupyter notebooks or async frameworks
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.send_message(message, messages, system_message, **kwargs)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(
                    self.send_message(message, messages, system_message, **kwargs)
                )
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(
                self.send_message(message, messages, system_message, **kwargs)
            )
    
    def _get_api_key(self) -> Optional[str]:
        """Get API key from environment variables based on provider."""
        key_map = {
            LLMProvider.OPENAI: "OPENAI_API_KEY",
            LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
            LLMProvider.GEMINI: "GOOGLE_API_KEY",
        }
        
        env_var = key_map.get(self.provider)
        if env_var:
            return os.getenv(env_var)
        return None
    
    def _set_api_key_env(self):
        """Set API key in environment for LangChain to use."""
        if self.api_key:
            if self.provider == LLMProvider.OPENAI:
                os.environ["OPENAI_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.ANTHROPIC:
                os.environ["ANTHROPIC_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.GEMINI:
                os.environ["GOOGLE_API_KEY"] = self.api_key
    
    def _get_token_encoder(self):
        """Get appropriate tiktoken encoder for the model."""
        if not TIKTOKEN_AVAILABLE:
            return None
        
        # Most OpenAI and Anthropic models use cl100k_base
        # Google models may use different encodings, but cl100k_base is a reasonable default
        try:
            # Try to get encoding for the specific model
            encoding = tiktoken.encoding_for_model(self.model)
            return encoding
        except KeyError:
            # Fallback to cl100k_base (used by GPT-3.5, GPT-4, Claude)
            self.logger.debug(f"Model {self.model} not found in tiktoken, using cl100k_base encoding")
            return tiktoken.get_encoding("cl100k_base")
    
    def _count_tokens(self, text: str) -> int:
        """
        Count tokens in text using tiktoken.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Number of tokens
        """
        if not text:
            return 0
        
        if not TIKTOKEN_AVAILABLE or not self._token_encoder:
            # Fallback: rough estimate (1 token ≈ 4 characters)
            return len(text) // 4
        
        try:
            return len(self._token_encoder.encode(text))
        except Exception as e:
            self.logger.warning(f"Error counting tokens: {e}, using fallback estimate")
            return len(text) // 4
    
    def _count_message_tokens(
        self,
        message: Union[str, Message],
        system_message: Optional[Union[str, Message]] = None,
        messages: Optional[List[Message]] = None
    ) -> int:
        """
        Count total tokens for a message request including system message and history.
        
        Args:
            message: Main message
            system_message: Optional system message
            messages: Optional conversation history
            
        Returns:
            Total token count
        """
        total = 0
        
        # Count system message
        if system_message:
            if isinstance(system_message, str):
                total += self._count_tokens(system_message)
            else:
                total += self._count_tokens(system_message.content or "")
        
        # Count conversation history
        if messages:
            for msg in messages:
                if isinstance(msg, str):
                    total += self._count_tokens(msg)
                else:
                    total += self._count_tokens(msg.content or "")
        
        # Count current message
        if isinstance(message, str):
            total += self._count_tokens(message)
        else:
            total += self._count_tokens(message.content or "")
        
        # Add overhead for message formatting (rough estimate)
        total += len(messages) if messages else 0
        total += 10  # Base overhead
        
        return total

    def _normalize_content_to_text(self, content: Any) -> str:
        """
        Normalize provider/LangChain content into a plain text string.

        Some providers (and some LangChain versions) can return content as a list of
        structured blocks (e.g., [{"type":"text","text":"..."}]). Downstream code frequently
        assumes `LLMResponse.content` is a string (e.g., for JSON extraction), so we
        normalize to text at the client boundary.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: List[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(str(block.get("text", "")))
                elif isinstance(block, str):
                    text_parts.append(block)
            out = "\n".join([t for t in text_parts if str(t).strip()])
            if out:
                return out
            return str(content)
        return str(content)

    def _safe_input_budget(self, *, safety_margin: int = 256) -> int:
        """
        Compute a conservative safe input token budget for the current model context window.

        We reserve space for:
        - expected output tokens
        - protocol/formatting overhead margin
        """
        try:
            max_in = int(self.max_input_tokens or 0)
        except Exception:
            max_in = 0
        if max_in <= 0:
            # Fallback to a reasonable default; upstream defaults max_input_tokens to 128k.
            max_in = 128000
        try:
            sm = int(safety_margin or 0)
        except Exception:
            sm = 256
        budget = max_in - max(0, sm)
        # Never allow a trivially small budget; compaction needs some room to work.
        return max(1024, int(budget))

    def _is_compaction_summary_message(self, m: Message) -> bool:
        try:
            if getattr(m, "role", None) != MessageRole.USER:
                return False
            c = getattr(m, "content", "") or ""
            if not isinstance(c, str):
                c = str(c)
            return c.lstrip().startswith("CONTEXT_PARTIAL_RESULT (internal):")
        except Exception:
            return False

    def _compact_conversation_messages_for_budget_truncate(
        self,
        *,
        conversation_messages: List[Message],
        system_message: Optional[Union[str, Message]],
        safe_budget: int,
    ) -> List[Message]:
        """
        Deterministic last-resort compaction:
        - Drop all assistant/tool messages (and prior internal partial-result messages).
        - Keep user messages (objects), but if user messages alone exceed budget, truncate oldest-first.

        This is intentionally lossy and exists only as a fallback when LLM-based partial-result compaction
        cannot fit within the budget.
        """
        user_msgs: List[Message] = [
            m
            for m in (conversation_messages or [])
            if getattr(m, "role", None) == MessageRole.USER and not self._is_compaction_summary_message(m)
        ]
        if not user_msgs:
            return conversation_messages

        new_msgs: List[Message] = list(user_msgs)
        after_tokens = self._count_message_tokens(message="", system_message=system_message, messages=new_msgs)
        if after_tokens <= safe_budget:
            return new_msgs

        # Truncate oldest-first; keep the last user message intact as long as possible.
        for i in range(max(0, len(new_msgs) - 1)):
            if after_tokens <= safe_budget:
                break
            m = new_msgs[i]
            c = getattr(m, "content", "")
            if not isinstance(c, str):
                c = str(c)
            c = c.strip()
            if len(c) <= 200:
                continue
            new_msgs[i] = Message(role=MessageRole.USER, content=c[:200] + " …(truncated due to token budget)")
            after_tokens = self._count_message_tokens(message="", system_message=system_message, messages=new_msgs)

        return new_msgs

    async def _compact_conversation_messages_for_budget_async(
        self,
        *,
        conversation_messages: List[Message],
        system_message: Optional[Union[str, Message]],
        safe_budget: int,
        reserved_output_tokens: int,
        iteration: int,
        request_id: str,
    ) -> List[Message]:
        """
        LLM-powered compaction with incremental updates (partial-result mode):
        - Keep original user messages (do not remove them).
        - Replace assistant/tool history with a single synthetic USER message containing a partial result.
        - If a previous partial result exists, update it using only new assistant/tool messages since then.

        Safety:
        - Uses a separate direct `langchain_llm.ainvoke` call with a tiny, bounded prompt (no recursion).
        - Falls back to deterministic truncation if compaction fails or still cannot fit within the budget.
        """
        try:
            # Preserve only real user messages; treat prior partial results as internal and replaceable.
            user_msgs: List[Message] = [
                m
                for m in conversation_messages
                if getattr(m, "role", None) == MessageRole.USER and not self._is_compaction_summary_message(m)
            ]
            if not user_msgs:
                return conversation_messages
            last_user = user_msgs[-1]
            prefix_users = user_msgs[:-1]

            # Extract system text (keep original system prompt) and append compaction instructions.
            sys_text = ""
            if system_message:
                sys_text = system_message if isinstance(system_message, str) else str(getattr(system_message, "content", "") or "")
            compaction_instructions = (
                "\n\n"
                "COMPaction Mode (internal):\n"
                "Objective: generate/maintain a PARTIAL RESULT based on the information available so far, to avoid exceeding token limits.\n"
                "This partial result will be used later to produce a more complete final result; missing information is acceptable.\n"
                "Rules:\n"
                "- Do NOT call tools.\n"
                "- Do NOT invent facts or numbers.\n"
                "- Preserve key finance figures, dates, units, and source/tool hints when present.\n"
                "- Focus on producing results (not summarizing logs). If something is unknown, mark it as unknown.\n"
                "- Output plain text only (no markdown fences).\n"
            )
            compaction_system = (sys_text or "") + compaction_instructions

            # Find the most recent partial result (if any) and only summarize new assistant/tool messages after it.
            last_summary_idx = -1
            last_partial_text = ""
            for i in range(len(conversation_messages) - 1, -1, -1):
                if self._is_compaction_summary_message(conversation_messages[i]):
                    last_summary_idx = i
                    c = getattr(conversation_messages[i], "content", "") or ""
                    last_partial_text = c if isinstance(c, str) else str(c)
                    break

            if last_summary_idx >= 0:
                delta_msgs = [
                    m
                    for m in conversation_messages[last_summary_idx + 1 :]
                    if getattr(m, "role", None) != MessageRole.USER
                ]
            else:
                delta_msgs = [m for m in conversation_messages if getattr(m, "role", None) != MessageRole.USER]

            # Build a bounded "new events" digest from delta messages (newest-first, tool first).
            max_delta_tokens = max(256, int(safe_budget * 0.20))
            remaining = max_delta_tokens
            delta_lines: List[str] = []

            def _push(label: str, text: str) -> None:
                nonlocal remaining
                if remaining <= 0:
                    return
                s = (text or "").strip()
                if not s:
                    return
                s = s[:2500]  # cap per item
                line = f"{label}: {s}"
                t = self._count_tokens(line)
                if t <= 0:
                    t = max(1, len(line) // 4)
                if t > remaining:
                    approx_chars = max(80, remaining * 4)
                    line = line[:approx_chars] + " …(truncated)"
                    t = self._count_tokens(line)
                if t <= 0 or t > remaining:
                    return
                delta_lines.append(line)
                remaining -= t

            for m in reversed(delta_msgs):
                if remaining <= 0:
                    break
                if getattr(m, "role", None) == MessageRole.TOOL:
                    nm = getattr(m, "name", None) or "tool"
                    _push(f"TOOL[{nm}]", str(getattr(m, "content", "") or ""))
            for m in reversed(delta_msgs):
                if remaining <= 0:
                    break
                if getattr(m, "role", None) == MessageRole.ASSISTANT:
                    _push("ASSISTANT", str(getattr(m, "content", "") or ""))

            new_events = "\n".join(delta_lines) if delta_lines else "(no new assistant/tool messages)"

            # Provide user context to the compaction model (newest-first, bounded).
            user_context_budget = max(256, int(safe_budget * 0.20))
            remaining_uc = user_context_budget
            user_context_lines: List[str] = []
            for m in reversed(user_msgs):
                if remaining_uc <= 0:
                    break
                c = getattr(m, "content", "") or ""
                c = c if isinstance(c, str) else str(c)
                c = c.strip()
                if not c:
                    continue
                c = c[:3000]
                line = f"USER: {c}"
                t = self._count_tokens(line)
                if t <= 0:
                    t = max(1, len(line) // 4)
                if t > remaining_uc:
                    approx_chars = max(80, remaining_uc * 4)
                    line = line[:approx_chars] + " …(truncated)"
                    t = self._count_tokens(line)
                if t <= 0 or t > remaining_uc:
                    continue
                user_context_lines.append(line)
                remaining_uc -= t
            user_context = "\n".join(reversed(user_context_lines)) if user_context_lines else "(no user context)"

            # Two attempts with shrinking output caps.
            max_out_1 = max(512, int(safe_budget * 0.30))
            max_out_2 = max(256, int(max_out_1 * 0.6))
            for attempt, max_out in enumerate([max_out_1, max_out_2], start=1):
                partial_user = (
                    "Generate/Update PARTIAL RESULT.\n\n"
                    "USER_CONTEXT:\n"
                    f"{user_context}\n\n"
                    "PREVIOUS_PARTIAL_RESULT (may be empty):\n"
                    f"{(last_partial_text or '').strip()}\n\n"
                    "NEW_EVENTS (assistant/tool messages since previous partial result):\n"
                    f"{new_events}\n\n"
                    "Return an UPDATED PARTIAL RESULT that is directly usable later.\n"
                    "Prefer a structured format aligned with the task (headings + bullet points are OK).\n"
                    "Do not apologize for missing data.\n"
                )
                resp = await self.langchain_llm.ainvoke(
                    message=partial_user,
                    messages=None,
                    system_message=compaction_system,
                    request_id=f"{request_id}_partial_{iteration}_a{attempt}",
                    agent_name=self.agent_name,
                    temperature=0,
                    max_output_tokens=int(max_out),
                    tool_choice="none",
                )
                partial = self._normalize_content_to_text(getattr(resp, "content", "") or "").strip()
                if not partial:
                    continue

                header = (
                    "CONTEXT_PARTIAL_RESULT (internal):\n"
                    f"- reason: input context exceeded model limit; compacting history at iteration={iteration}\n"
                    f"- safe_input_budget: {safe_budget} (reserved_output_tokens={reserved_output_tokens})\n"
                    "\n"
                )
                partial = header + partial

                # Build new conversation order: keep older user msgs, then partial result, then last user.
                compact_msg = Message(role=MessageRole.USER, content=partial)
                new_msgs: List[Message] = [*prefix_users, compact_msg, last_user]

                after_tokens = self._count_message_tokens(message="", system_message=system_message, messages=new_msgs)
                if after_tokens > safe_budget:
                    continue
                return new_msgs

            # Fallback: truncate user messages (drop assistant/tool history entirely)
            return self._compact_conversation_messages_for_budget_truncate(
                conversation_messages=conversation_messages,
                system_message=system_message,
                safe_budget=safe_budget,
            )
        except Exception:
            return self._compact_conversation_messages_for_budget_truncate(
                conversation_messages=conversation_messages,
                system_message=system_message,
                safe_budget=safe_budget,
            )
    
    def _chunk_content(self, content: str, chunk_size_tokens: int) -> List[str]:
        """
        Split content into chunks with 10% overlap.
        
        Args:
            content: Content to chunk
            chunk_size_tokens: Maximum tokens per chunk (90% of model max)
            
        Returns:
            List of chunk strings
        """
        if not content:
            return []
        
        chunks = []
        
        # If content fits in one chunk, return as-is
        content_tokens = self._count_tokens(content)
        if content_tokens <= chunk_size_tokens:
            return [content]
        
        # Split into chunks
        # We'll split by characters and validate token count
        # This is a simplified approach - for better results, could split by sentences/paragraphs
        current_pos = 0
        content_length = len(content)
        
        while current_pos < content_length:
            # Estimate character position for chunk
            # Rough estimate: 1 token ≈ 4 characters
            chunk_end_estimate = min(
                current_pos + (chunk_size_tokens * 4),
                content_length
            )
            
            # Extract chunk
            chunk = content[current_pos:chunk_end_estimate]
            
            # Adjust chunk to fit token limit
            chunk_tokens = self._count_tokens(chunk)
            while chunk_tokens > chunk_size_tokens and len(chunk) > 0:
                # Reduce chunk size
                chunk = chunk[:-100]  # Remove 100 chars at a time
                chunk_tokens = self._count_tokens(chunk)
            
            if chunk:
                chunks.append(chunk)
            
            # Move position forward with overlap
            if current_pos + len(chunk) >= content_length:
                break
            
            # Next chunk starts with overlap (last 10% of current chunk)
            overlap_chars = max(1, int(len(chunk) * 0.1))
            current_pos = current_pos + len(chunk) - overlap_chars
        
        return chunks
    
    async def _process_chunk(
        self,
        chunk_content: str,
        chunk_index: int,
        total_chunks: int,
        original_system_message: Optional[str],
        messages: Optional[List[Message]] = None,
        request_id: str = None,
        **kwargs
    ) -> LLMResponse:
        """
        Process a single chunk with special chunk prompt (async).
        
        Args:
            chunk_content: Content of this chunk
            chunk_index: Index of this chunk (0-based)
            total_chunks: Total number of chunks
            original_system_message: Original system message from user
            messages: Optional conversation history to include
            request_id: Request ID for tracking
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse for this chunk
        """
        # Create chunk-specific system message
        chunk_system_message = f"""This is chunk {chunk_index + 1} of {total_chunks} from a larger input that was split due to token limitations.

IMPORTANT: This is only a portion of the complete input. Please process this chunk according to the original instructions below, but be aware that your response will be combined with responses from other chunks later.

Original instructions:
{original_system_message if original_system_message else "Process the following content according to your default behavior."}

Please provide your analysis/response for this chunk, following the same format as specified in the original instructions."""
        
        # Process chunk asynchronously (include conversation history if available)
        response = await self.langchain_llm.ainvoke(
            message=chunk_content,
            messages=messages,
            system_message=chunk_system_message,
            request_id=f"{request_id}_chunk_{chunk_index}" if request_id else None,
            agent_name=self.agent_name,
            **kwargs
        )
        
        return response
    
    async def _summarize_chunks(
        self,
        chunk_responses: List[LLMResponse],
        original_system_message: Optional[str],
        request_id: str,
        **kwargs
    ) -> LLMResponse:
        """
        Summarize aggregated chunk responses into final response (async).
        
        Args:
            chunk_responses: List of responses from all chunks
            original_system_message: Original system message
            request_id: Request ID for tracking
            **kwargs: Additional parameters
            
        Returns:
            Final aggregated LLMResponse
        """
        # Combine all chunk responses
        chunk_contents = []
        for i, response in enumerate(chunk_responses):
            chunk_contents.append(f"=== Chunk {i + 1} Response ===\n{response.content}")
        
        combined_content = "\n\n".join(chunk_contents)
        
        # Create summarization prompt
        summarization_system_message = f"""You are receiving responses from multiple chunks of a single input that was split due to token limitations.

IMPORTANT CONTEXT:
- The input was split into {len(chunk_responses)} chunks
- Each chunk has approximately 10% overlap with adjacent chunks to maintain context
- The chunks were processed separately, and now you need to combine them into one final response

Original instructions:
{original_system_message if original_system_message else "Process the following content according to your default behavior."}

Your task:
1. Review all chunk responses below
2. Combine them into a single, coherent response
3. Follow the same format as specified in the original instructions
4. Eliminate any redundancy caused by the overlap between chunks
5. Ensure the final response is complete and follows the original output format

Provide your final combined response:"""
        
        # Get summarization response asynchronously
        response = await self.langchain_llm.ainvoke(
            message=combined_content,
            messages=None,
            system_message=summarization_system_message,
            request_id=f"{request_id}_summary",
            agent_name=self.agent_name,
            **kwargs
        )
        
        return response
    
    async def send_message(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send a message to the LLM and receive a response.
        
        Uses LangChain native provider classes internally.
        Automatically chunks input if it exceeds 90% of max_input_tokens.
        
        Args:
            message: User message (string or Message object)
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message to set behavior
            **kwargs: Additional parameters to override defaults
        
        Returns:
            LLMResponse object containing the response
        
        Example:
            >>> client = LLMClient(model="gpt-4")
            >>> response = client.send_message("Hello, how are you?")
            >>> print(response.content)
        """
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Sending message via LangChain to {self.model} (request_id: {request_id})")
        
        try:
            # Extract message content for token counting
            if isinstance(message, str):
                message_content = message
            else:
                message_content = message.content or ""
            
            # Extract system message content
            system_message_content = None
            if system_message:
                if isinstance(system_message, str):
                    system_message_content = system_message
                else:
                    system_message_content = system_message.content or ""
            
            # Count total input tokens
            total_tokens = self._count_message_tokens(
                message=message,
                system_message=system_message,
                messages=messages
            )
            
            # Check if chunking is needed (90% threshold)
            chunk_threshold = int(self.max_input_tokens * 0.9)
            
            if total_tokens > chunk_threshold:
                return await self._send_message_chunked(
                    message=message,
                    total_tokens=total_tokens,
                    chunk_threshold=chunk_threshold,
                    message_content=message_content,
                    system_message_content=system_message_content,
                    messages=messages,
                    system_message=system_message,
                    request_id=request_id,
                    **kwargs
                )
            else:
                return await self._send_message_direct(
                    message=message,
                    messages=messages,
                    system_message=system_message,
                    request_id=request_id,
                    **kwargs
                )
            
        except Exception as e:
            self.logger.error(f"Error calling LLM: {e}")
            raise
    
    async def _send_message_chunked(
        self,
        message: Union[str, Message],
        total_tokens: int,
        chunk_threshold: int,
        message_content: str,
        system_message_content: str,
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: str = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send message in chunks (internal method).
        
        Args:
            message: User message
            messages: Optional conversation history
            system_message: Optional system message
            request_id: Request ID
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse object
        """
        self.logger.info(
            f"Input tokens ({total_tokens}) exceed 90% threshold ({chunk_threshold}). "
            f"Chunking input into smaller pieces."
        )
        
        # Calculate tokens used by system message and history
        context_tokens = self._count_message_tokens(
            message="",  # Empty message
            system_message=system_message,
            messages=messages
        )
        
        # Reserve tokens for chunk prompt overhead (chunk system message adds ~200-300 tokens)
        chunk_prompt_overhead = 500
        
        # Calculate available tokens for message content per chunk
        chunk_size_tokens = chunk_threshold - context_tokens - chunk_prompt_overhead
        
        if chunk_size_tokens <= 0:
            self.logger.warning(
                f"Chunk size too small ({chunk_size_tokens}) after accounting for context "
                f"({context_tokens} tokens). Processing without chunking."
            )
            return await self._send_message_direct(
                message=message,
                messages=messages,
                system_message=system_message,
                request_id=request_id,
                **kwargs
            )
        
        # Split message content into chunks
        chunks = self._chunk_content(message_content, chunk_size_tokens)
        
        if not chunks:
            # Fallback to non-chunked processing
            self.logger.warning("Chunking produced no chunks. Processing without chunking.")
            return await self._send_message_direct(
                message=message,
                messages=messages,
                system_message=system_message,
                request_id=request_id,
                **kwargs
            )
        
        self.logger.info(f"Split input into {len(chunks)} chunks")
        
        # Process all chunks in parallel using asyncio.gather
        self.logger.info(f"Processing {len(chunks)} chunks in parallel...")
        chunk_tasks = [
            self._process_chunk(
                chunk_content=chunk,
                chunk_index=i,
                total_chunks=len(chunks),
                original_system_message=system_message_content,
                messages=messages,  # Include conversation history in each chunk
                request_id=request_id,
                **kwargs
            )
            for i, chunk in enumerate(chunks)
        ]
        
        # Wait for all chunks to complete
        chunk_responses = await asyncio.gather(*chunk_tasks)
        self.logger.info(f"All {len(chunks)} chunks processed successfully")
        
        # Summarize all chunk responses
        self.logger.info("Aggregating chunk responses with summarization LLM")
        final_response = await self._summarize_chunks(
            chunk_responses=chunk_responses,
            original_system_message=system_message_content,
            request_id=request_id,
            **kwargs
        )

        # Normalize summary content to plain text (providers may return content blocks).
        try:
            final_response.content = self._normalize_content_to_text(getattr(final_response, "content", None))
        except Exception:
            pass
        
        # Aggregate usage and cost from all chunks + summary
        total_input_tokens = sum(
            r.usage.get('input_tokens', 0) if r.usage else 0
            for r in chunk_responses
        ) + (final_response.usage.get('input_tokens', 0) if final_response.usage else 0)
        
        total_output_tokens = sum(
            r.usage.get('output_tokens', 0) if r.usage else 0
            for r in chunk_responses
        ) + (final_response.usage.get('output_tokens', 0) if final_response.usage else 0)
        
        total_cost = sum(
            r.cost or 0 for r in chunk_responses
        ) + (final_response.cost or 0)
        
        # Update final response with aggregated metrics
        final_response.usage = {
            'input_tokens': total_input_tokens,
            'output_tokens': total_output_tokens,
            'total_tokens': total_input_tokens + total_output_tokens
        }
        final_response.cost = total_cost
        final_response.request_id = request_id
        
        # Log cost
        if final_response.cost is not None and LLMClient._cost_tracker:
            LLMClient._cost_tracker.log_cost(
                model=self.model,
                cost=final_response.cost,
                tokens=final_response.usage,
                request_id=request_id
            )
        
        self.logger.info(
            f"Chunking complete. Total tokens: {final_response.usage.get('total_tokens', 0)}, "
            f"Cost: ${final_response.cost:.6f}" if final_response.cost else ""
        )
        
        return final_response
    
    def _build_continuation_prompt(self, iteration: int) -> str:
        """
        Build a generic continuation prompt after tool execution.
        
        Args:
            iteration: Current iteration number
            
        Returns:
            Continuation prompt string
        """
        return (
            "Based on the tool results above, do you need to call any additional tools to gather more information?\n"
            "- If YES: Call the tools you need (you can call multiple in parallel)\n"
            f"- If NO: Respond with {self.multi_stage_marker} followed by your complete response\n"
            "\n"
            "Note: Tool results may include suggested next tools in their metadata."
        )
    
    def _has_final_answer_marker(self, content: str) -> bool:
        """
        Check if content contains the final answer marker.
        
        Args:
            content: Response content to check
            
        Returns:
            True if marker is present at the start
        """
        if not content:
            return False
        # Strip whitespace and check if starts with marker (case-insensitive)
        return content.strip().upper().startswith(self.multi_stage_marker.upper())
    
    def _extract_final_answer(self, content: str) -> str:
        """
        Extract the final answer by removing the marker.
        
        Args:
            content: Response content with marker
            
        Returns:
            Content without the marker
        """
        if not content:
            return content
        
        # Find marker (case-insensitive) and remove it
        content_stripped = content.strip()
        marker_upper = self.multi_stage_marker.upper()
        
        # Check various positions for marker
        if content_stripped.upper().startswith(marker_upper):
            # Remove marker from start
            result = content_stripped[len(self.multi_stage_marker):].strip()
            return result
        
        # If not at start, return as-is
        return content
    
    async def _execute_tool_calls(
        self,
        tool_calls: List[Any],
        messages: Optional[List[Message]] = None,
        runtime: Optional["ToolRuntime"] = None,
    ) -> List[Message]:
        """
        Execute tool calls and return tool result messages.
        
        Args:
            tool_calls: List of tool call objects from LLM response (can be dicts or LangChain tool call objects)
            messages: Current conversation messages
            
        Returns:
            List of ToolMessage objects with tool results
        """
        tool_messages = []
        
        for tool_call in tool_calls:
            # Handle both dict format and LangChain tool call object format
            if isinstance(tool_call, dict):
                tool_name = tool_call.get('name')
                tool_call_id = tool_call.get('id') or tool_call.get('tool_use_id') or tool_call.get('tool_call_id')
                tool_args = tool_call.get('args', {})
            else:
                # LangChain tool call object
                tool_name = getattr(tool_call, 'name', None)
                tool_call_id = (
                    getattr(tool_call, 'id', None)
                    or getattr(tool_call, 'tool_use_id', None)
                    or getattr(tool_call, 'tool_call_id', None)
                )
                tool_args = getattr(tool_call, 'args', {})
                # Convert tool_args to dict if it's not already
                if not isinstance(tool_args, dict):
                    tool_args = dict(tool_args) if hasattr(tool_args, '__dict__') else {}
            
            if not tool_name or tool_name not in self.tool_map:
                error_msg = f"Tool '{tool_name}' not found or not available"
                self.logger.warning(error_msg)
                tool_messages.append(Message(
                    role=MessageRole.TOOL,
                    content=f"Error: {error_msg}",
                    name=tool_name,
                    tool_call_id=tool_call_id
                ))
                continue
            
            tool = self.tool_map[tool_name]
            
            try:
                self.logger.debug(f"Executing tool: {tool_name} with args: {tool_args}")

                result = await self._invoke_one_tool(tool, tool_name, tool_args, runtime=runtime)

                # Convert result to string
                if isinstance(result, str):
                    result_str = result
                else:
                    result_str = str(result)
                
                tool_msg = Message(
                    role=MessageRole.TOOL,
                    content=result_str,
                    name=tool_name,
                    tool_call_id=tool_call_id
                )
                tool_messages.append(tool_msg)
                
                self.logger.debug(f"Tool {tool_name} executed successfully")
                
            except Exception as e:
                error_msg = f"Error executing tool {tool_name}: {str(e)}"
                self.logger.error(error_msg)
                tool_messages.append(Message(
                    role=MessageRole.TOOL,
                    content=f"Error: {error_msg}",
                    name=tool_name,
                    tool_call_id=tool_call_id
                ))
        
        return tool_messages

    async def _invoke_one_tool(
        self,
        tool: Any,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        runtime: Optional["ToolRuntime"] = None,
    ) -> Any:
        """
        Shared tool invocation logic used by both:
        - top-level LLM tool calls (_execute_tool_calls)
        - nested tool→tool calls (ToolRuntime.call_tool)

        Note: runtime is *not* passed via tool args (to avoid polluting tool schemas). Instead it is
        made available via ToolRuntime.current() contextvar while the tool executes.
        """
        # The ToolRuntime sets its own contextvar in call_tool, but top-level calls need to set it here.
        token = None
        if runtime is not None:
            import moose.framework.llm_core.tool_runtime as _tr
            token = _tr._CURRENT_RUNTIME.set(runtime)
        try:
            # Execute tool (LangChain tools support both sync and async)
            # LangChain StructuredTool has invoke/ainvoke methods
            if hasattr(tool, "ainvoke"):
                result = await tool.ainvoke(tool_args)
            elif hasattr(tool, "invoke"):
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: tool.invoke(tool_args))
            elif callable(tool):
                if asyncio.iscoroutinefunction(tool):
                    result = await tool(**tool_args)
                else:
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, lambda: tool(**tool_args))
            else:
                raise ValueError(f"Tool {tool_name} is not callable")

            # Some wrappers can still return awaitables.
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            if token is not None:
                import moose.framework.llm_core.tool_runtime as _tr
                _tr._CURRENT_RUNTIME.reset(token)
    
    async def _send_message_direct(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: str = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send message directly without chunking (internal async method).
        
        Supports tool calling with automatic execution and multi-turn conversations.
        
        Args:
            message: User message
            messages: Optional conversation history
            system_message: Optional system message
            request_id: Request ID
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse object
        """
        if request_id is None:
            request_id = str(uuid.uuid4())
        
        # Build conversation history
        conversation_messages = list(messages) if messages else []
        
        # Add current message
        if isinstance(message, str):
            conversation_messages.append(Message(role=MessageRole.USER, content=message))
        else:
            conversation_messages.append(message)
        
        # Track total cost and usage across tool calls
        total_cost = 0.0
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        final_response = None
        runtime = None
        
        # Augment system message for multi-stage reasoning mode
        if self.enable_multi_stage_reasoning and system_message:
            # Append multi-stage reasoning instructions to system message
            multi_stage_instructions = (
                "\n\nMULTI-STAGE REASONING MODE:\n"
                "You can call tools iteratively to gather information. After each tool execution round, you'll be asked if you need more tools.\n"
                "\n"
                "IMPORTANT: Do NOT generate your final complete response until you're done with all tool calls.\n"
                f"- To call more tools: Simply call the tools you need (can be multiple in parallel)\n"
                f"- To finish: Respond with {self.multi_stage_marker} prefix, then provide your complete response\n"
                "\n"
                f"The {self.multi_stage_marker} marker is REQUIRED to signal completion."
            )
            
            if isinstance(system_message, str):
                system_message = system_message + multi_stage_instructions
            elif isinstance(system_message, Message):
                system_message = Message(
                    role=system_message.role,
                    content=str(system_message.content) + multi_stage_instructions
                )
        
        # Iterate until we get a final response (no more tool calls) or hit max iterations
        for iteration in range(self.max_tool_iterations + 1):
            # Before calling the LLM, ensure our full prompt fits the model context window.
            # This loop can grow due to assistant/tool messages; we compact only when necessary.
            try:
                reserved_output_tokens = int(
                    kwargs.get("max_output_tokens")
                    or self.max_output_tokens
                    or 2048
                )
            except Exception:
                reserved_output_tokens = 2048
            safe_budget = self._safe_input_budget(safety_margin=2048)
            current_tokens = self._count_message_tokens(
                message="",
                system_message=system_message,
                messages=conversation_messages,
            )
            if current_tokens > safe_budget:
                self.logger.warning(
                    f"Context budget exceeded before LLM call (iter={iteration}): "
                    f"tokens_estimate={current_tokens} > safe_budget={safe_budget}. Compacting history."
                )
                conversation_messages = await self._compact_conversation_messages_for_budget_async(
                    conversation_messages=conversation_messages,
                    system_message=system_message,
                    safe_budget=safe_budget,
                    reserved_output_tokens=reserved_output_tokens,
                    iteration=iteration,
                    request_id=str(request_id),
                )

            # Use LangChain LLM wrapper asynchronously
            response = await self.langchain_llm.ainvoke(
                message=None,  # Don't split - pass everything in messages
                messages=conversation_messages,  # Pass full conversation history
                system_message=system_message,
                request_id=f"{request_id}_iter_{iteration}",
                agent_name=self.agent_name,
                **kwargs
            )
            
            # Accumulate cost and usage
            if response.cost:
                total_cost += response.cost
            if response.usage:
                total_usage["input_tokens"] += response.usage.get("input_tokens", 0)
                total_usage["output_tokens"] += response.usage.get("output_tokens", 0)
                total_usage["total_tokens"] += response.usage.get("total_tokens", 0)
            
            # Normalize content blocks (some providers may return a list) to text for all downstream logic.
            response_text = self._normalize_content_to_text(response.content)

            # Check for termination marker FIRST (multi-stage reasoning mode)
            if self.enable_multi_stage_reasoning and self._has_final_answer_marker(response_text):
                self.logger.debug(f"Final answer marker detected at iteration {iteration + 1}")
                final_answer_content = self._extract_final_answer(response_text)
                # Create final response with extracted content
                final_response = LLMResponse(
                    content=final_answer_content,
                    model=response.model,
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    cost=response.cost,
                    raw_response=response.raw_response,
                    request_id=response.request_id,
                    tool_calls=None  # No tool calls in final answer
                )
                break
            
            # Add assistant response to conversation
            conversation_messages.append(Message(
                role=MessageRole.ASSISTANT,
                content=response_text,
                tool_calls=response.tool_calls
            ))
            
            # Check if there are tool calls
            if response.tool_calls and self.tools:
                self.logger.debug(f"Iteration {iteration + 1}: LLM requested {len(response.tool_calls)} tool calls")
                
                # Execute tools
                # Create a per-request runtime so tools can call other tools (nested calls remain internal).
                # Initialized lazily here to avoid overhead when no tools are used.
                if runtime is None:
                    runtime = ToolRuntime(
                        tool_map=self.tool_map,
                        # Nested calls already run inside ToolRuntime.call_tool which sets the runtime contextvar.
                        invoke_tool=lambda t, n, a: self._invoke_one_tool(t, n, a),
                        request_id=request_id,
                        agent_name=self.agent_name,
                        logger=self.logger,
                    )
                    await runtime.start()

                tool_messages = await self._execute_tool_calls(response.tool_calls, conversation_messages, runtime=runtime)
                
                conversation_messages.extend(tool_messages)
                
                # If multi-stage reasoning enabled, add continuation prompt
                if self.enable_multi_stage_reasoning:
                    continuation_prompt = self._build_continuation_prompt(iteration)
                    conversation_messages.append(Message(
                        role=MessageRole.USER,
                        content=continuation_prompt
                    ))
                    self.logger.debug(f"Added continuation prompt after iteration {iteration + 1}")
                
                # Continue loop to get next response
                continue
            else:
                # No tool calls
                if self.enable_multi_stage_reasoning:
                    # In multi-stage mode, prompt for decision if no marker found
                    conversation_messages.append(Message(
                        role=MessageRole.USER,
                        content=f"Do you need more tools or is this your final answer? If it's final, start your response with {self.multi_stage_marker}. (e.g., {self.multi_stage_marker}My final answer)"
                    ))
                    self.logger.debug(f"No tool calls and no marker at iteration {iteration + 1}, prompting for decision")
                    continue
                else:
                    # Standard mode - return response
                    # Ensure we never return list-typed content to callers (breaks JSON extraction).
                    final_response = LLMResponse(
                        content=response_text,
                        model=response.model,
                        finish_reason=response.finish_reason,
                        usage=response.usage,
                        cost=response.cost,
                        raw_response=response.raw_response,
                        request_id=response.request_id,
                        tool_calls=response.tool_calls,
                    )
                    break
        
        if final_response is None:
            # Hit max iterations, force a final answer with a tool-less finalization call.
            self.logger.warning(
                f"Reached max tool iterations ({self.max_tool_iterations}). "
                "Making one final tool-less call to generate the final answer."
            )

            # Append a strong finalization instruction. Keep it as a USER message so the model treats it as the latest turn.
            conversation_messages.append(
                Message(
                    role=MessageRole.USER,
                    content=(
                        "Tool budget exhausted. Do NOT call any tools.\n"
                        f"Respond starting with {self.multi_stage_marker} and then provide your complete final answer now."
                    ),
                )
            )

            try:
                forced = await self.langchain_llm.ainvoke(
                    message=None, 
                    messages=conversation_messages, 
                    system_message=system_message,
                    request_id=f"{request_id}_final",
                    agent_name=self.agent_name,
                    **kwargs
                )
            except Exception as e:
                self.logger.error(f"Finalization call failed after max iterations: {e}")
                forced = response  # fallback to last response

            # Accumulate cost/usage from the forced final call as well
            if forced is not None:
                if getattr(forced, "cost", None):
                    total_cost += float(getattr(forced, "cost", 0.0) or 0.0)
                if getattr(forced, "usage", None):
                    fu = getattr(forced, "usage") or {}
                    if isinstance(fu, dict):
                        total_usage["input_tokens"] += int(fu.get("input_tokens", 0) or 0)
                        total_usage["output_tokens"] += int(fu.get("output_tokens", 0) or 0)
                        total_usage["total_tokens"] += int(fu.get("total_tokens", 0) or 0)

            # Normalize forced content and strip marker if present
            forced_content = getattr(forced, "content", "") if forced is not None else ""
            if isinstance(forced_content, list):
                text_parts: List[str] = []
                for block in forced_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(str(block.get("text", "")))
                forced_text = "\n".join([t for t in text_parts if t])
            elif isinstance(forced_content, str):
                forced_text = forced_content
            else:
                forced_text = "" if forced_content is None else str(forced_content)

            if self.enable_multi_stage_reasoning and self._has_final_answer_marker(forced_text):
                forced_text = self._extract_final_answer(forced_text)

            final_response = LLMResponse(
                content=forced_text,
                model=getattr(forced, "model", None) or self.model,
                finish_reason=getattr(forced, "finish_reason", None),
                usage=getattr(forced, "usage", None),
                cost=getattr(forced, "cost", None),
                raw_response=getattr(forced, "raw_response", None),
                request_id=getattr(forced, "request_id", None) or request_id,
                tool_calls=None,
            )

        # Include external LLM usage/cost accrued during tool execution (e.g., meeting-room helper calls).
        if runtime is not None:
            try:
                total_cost += float(getattr(runtime, "external_cost", 0.0) or 0.0)
                eu = getattr(runtime, "external_usage", None)
                if isinstance(eu, dict):
                    total_usage["input_tokens"] += int(eu.get("input_tokens", 0) or 0)
                    total_usage["output_tokens"] += int(eu.get("output_tokens", 0) or 0)
                    total_usage["total_tokens"] += int(eu.get("total_tokens", 0) or 0)
            except Exception:
                pass
        
        # Update final response with accumulated cost and usage
        final_response.cost = total_cost if total_cost > 0 else final_response.cost
        final_response.usage = total_usage if any(total_usage.values()) else final_response.usage
        
        # Log cost if available
        if final_response.cost is not None and LLMClient._cost_tracker:
            LLMClient._cost_tracker.log_cost(
                model=self.model,
                cost=final_response.cost,
                tokens=final_response.usage,
                request_id=request_id
            )
        
        self.logger.debug(f"Received response from {self.model}")
        if final_response.usage:
            self.logger.debug(f"Token usage: {final_response.usage}")
        if final_response.cost is not None:
            self.logger.debug(f"Cost: ${final_response.cost:.6f}")
        
        if runtime is not None:
            try:
                await runtime.close()
            except Exception:
                pass

        return final_response
    
    def send_messages_sync(
        self,
        messages: List[Union[str, Message]],
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Synchronous wrapper for send_messages (for backward compatibility).
        
        This method runs the async send_messages in an event loop.
        For new code, prefer using await send_messages() directly.
        
        Args:
            messages: List of messages (strings or Message objects)
            system_message: Optional system message
            **kwargs: Additional parameters
        
        Returns:
            LLMResponse object
        """
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.send_messages(messages, system_message, **kwargs)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(
                    self.send_messages(messages, system_message, **kwargs)
                )
        except RuntimeError:
            # No event loop exists, create one
            return asyncio.run(
                self.send_messages(messages, system_message, **kwargs)
            )
    
    async def send_messages(
        self,
        messages: List[Union[str, Message]],
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send multiple messages in a conversation (async).
        
        Args:
            messages: List of messages (strings or Message objects)
            system_message: Optional system message
            **kwargs: Additional parameters
        
        Returns:
            LLMResponse object
        """
        # Convert strings to Message objects
        message_objects = []
        for msg in messages:
            if isinstance(msg, str):
                message_objects.append(Message(role=MessageRole.USER, content=msg))
            else:
                message_objects.append(msg)
        
        # Use the last message as the current message, rest as history
        if len(message_objects) == 0:
            raise ValueError("At least one message is required")
        
        current_message = message_objects[-1]
        history = message_objects[:-1] if len(message_objects) > 1 else None
        
        return await self.send_message(
            message=current_message,
            messages=history,
            system_message=system_message,
            **kwargs
        )
    
    def stream_message(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ):
        """
        Send a message and stream the response.
        
        Uses LangChain native provider classes internally.
        
        Args:
            message: User message
            messages: Optional conversation history
            system_message: Optional system message
            **kwargs: Additional parameters
        
        Yields:
            Chunks of the response as they arrive
        """
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Streaming message via LangChain to {self.model} (request_id: {request_id})")
        
        try:
            # Use LangChain LLM wrapper for streaming
            for chunk in self.langchain_llm.stream(
                message=message,
                messages=messages,
                system_message=system_message,
                request_id=request_id,
                agent_name=self.agent_name,
                **kwargs
            ):
                yield chunk
        except Exception as e:
            self.logger.error(f"Error streaming from LLM: {e}")
            raise
