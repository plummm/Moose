"""LangChain integration layer for LLM interactions.

This module provides a unified interface to LangChain using native provider classes:
- ChatOpenAI for OpenAI models
- ChatAnthropic for Anthropic models
- ChatGoogleGenerativeAI for Gemini models
"""

import asyncio
import json
import os
import random
import threading
import time
from typing import List, Optional, Dict, Any, Union, Iterator, Tuple
from moose.framework.llm_core.models import Message, MessageRole, LLMResponse
from moose.framework.llm_core.providers import LLMProvider, get_provider
from moose.framework.llm_core.config import ModelConfig
from moose.framework.logging import get_core_logger, get_llm_logger

try:
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_azure_ai.chat_models import AzureAIChatCompletionsModel
    from langchain_core.messages import (
        BaseMessage,
        HumanMessage,
        SystemMessage,
        AIMessage,
        ToolMessage
    )
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    ChatOpenAI = None
    ChatAnthropic = None
    ChatGoogleGenerativeAI = None
    AzureAIChatCompletionsModel = None
    BaseMessage = None
    HumanMessage = None
    SystemMessage = None
    AIMessage = None
    ToolMessage = None


class LangChainLLM:
    """
    LangChain LLM wrapper using native provider classes.
    
    Automatically selects the appropriate LangChain class based on model provider:
    - OpenAI models → ChatOpenAI
    - Anthropic models → ChatAnthropic
    - Gemini models → ChatGoogleGenerativeAI
    
    Example:
        >>> llm = LangChainLLM("gpt-4", temperature=0.7)
        >>> response = llm.invoke("Hello!")
        >>> 
        >>> llm = LangChainLLM("claude-3-opus-20240229")
        >>> response = llm.invoke("Hello!")
    """
    
    def __init__(
        self,
        model: str,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        config: Optional[ModelConfig] = None,
        tools: Optional[List[Any]] = None,
        enable_web_search: bool = False,
        **kwargs
    ):
        """
        Initialize LangChain LLM with native provider class.
        
        Args:
            model: Model name (e.g., "gpt-4", "claude-3-opus-20240229", "gemini-pro")
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            config: Optional ModelConfig instance (for cost calculation)
            tools: Optional list of LangChain tools to bind to the LLM
            **kwargs: Additional parameters for the LangChain LLM
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LangChain is required. Install with: pip install langchain langchain-openai langchain-anthropic langchain-google-genai"
            )
        
        self.logger = get_core_logger()
        self.llm_logger = get_llm_logger()
        self.model = model
        self.provider = get_provider(model)
        self.config = config
        self.tools = tools or []
        self.enable_web_search = bool(enable_web_search)

        # IMPORTANT:
        # Do NOT keep a single long-lived LangChain chat model instance here.
        #
        # Some providers (notably Gemini via langchain_google_genai) can create async objects/futures
        # that are bound to the event loop they were created/first used on. If a cached model instance
        # is later awaited on a different loop (common in multi-threaded async services), it can raise:
        #   "got Future ... attached to a different loop"
        #
        # To prevent cross-loop reuse, we cache a separate underlying model per:
        # - running asyncio event loop (for async ainvoke)
        # - OS thread (for sync invoke/stream)
        self._init_temperature = float(temperature)
        self._init_max_tokens = max_tokens
        self._init_timeout = timeout

        # Retry configuration for quota/rate-limit errors (not passed through to LangChain providers).
        # Defaults are intentionally conservative to avoid long stalls.
        def _env_int(name: str, default: int) -> int:
            try:
                v = str(os.getenv(name, "")).strip()
                return int(v) if v else int(default)
            except Exception:
                return int(default)

        def _env_float(name: str, default: float) -> float:
            try:
                v = str(os.getenv(name, "")).strip()
                return float(v) if v else float(default)
            except Exception:
                return float(default)

        self._retry_429_max_attempts = int(kwargs.pop("retry_429_max_attempts", _env_int("MOOSE_LLM_RETRY_429_MAX_ATTEMPTS", 2)))
        self._retry_429_base_seconds = float(kwargs.pop("retry_429_base_seconds", _env_float("MOOSE_LLM_RETRY_429_BASE_SECONDS", 1.0)))
        self._retry_429_max_seconds = float(kwargs.pop("retry_429_max_seconds", _env_float("MOOSE_LLM_RETRY_429_MAX_SECONDS", 20.0)))
        self._retry_429_jitter_ratio = float(kwargs.pop("retry_429_jitter_ratio", _env_float("MOOSE_LLM_RETRY_429_JITTER_RATIO", 0.15)))
        self._retry_429_max_attempts = max(0, min(10, int(self._retry_429_max_attempts)))
        self._retry_429_base_seconds = max(0.0, float(self._retry_429_base_seconds))
        self._retry_429_max_seconds = max(self._retry_429_base_seconds, float(self._retry_429_max_seconds))
        self._retry_429_jitter_ratio = max(0.0, min(1.0, float(self._retry_429_jitter_ratio)))

        self._init_kwargs: Dict[str, Any] = dict(kwargs)
        self._llm_cache: Dict[Tuple[str, int], Any] = {}
        self._llm_lock = threading.Lock()

        self.logger.debug(f"Initialized LangChainLLM for {self.provider.value} model: {model}")

    def _web_search_tool_for_provider(self) -> Optional[Any]:
        """
        Provider-native web search tool binding (LangChain bind_tools).

        Returns:
          - OpenAI: dict {"type": "web_search_preview"}
          - Anthropic: BetaWebSearchTool20250305Param(...)
          - Gemini: dict {"google_search": {}}
          - Other providers: None
        """
        if self.provider == LLMProvider.OPENAI:
            return {"type": "web_search_preview"}

        if self.provider == LLMProvider.GEMINI:
            return {"google_search": {}}

        if self.provider == LLMProvider.ANTHROPIC:
            try:
                from anthropic.types.beta import BetaWebSearchTool20250305Param
            except Exception as e:
                try:
                    self.logger.warning(f"Anthropic web search tool not available (missing beta types): {e}")
                except Exception:
                    pass
                return None

            return BetaWebSearchTool20250305Param(
                name="web_search",
                type="web_search_20250305",
            )

        return None

    @staticmethod
    def _is_quota_exhausted_429(e: Exception) -> bool:
        """
        Best-effort detection for provider quota/rate-limit exhaustion.

        Provider-agnostic: treats HTTP 429 / "Too Many Requests" as retryable regardless of vendor.
        """
        try:
            sc = getattr(e, "status_code", None)
            if sc == 429:
                return True
        except Exception:
            pass
        try:
            resp = getattr(e, "response", None)
            sc2 = getattr(resp, "status_code", None)
            if sc2 == 429:
                return True
        except Exception:
            pass

        msg = ""
        try:
            msg = str(e) or ""
        except Exception:
            msg = ""
        msg_u = msg.upper()
        if "429" in msg_u:
            return True
        if "TOO MANY REQUESTS" in msg_u:
            return True
        return False

    def _backoff_seconds(self, attempt_index: int) -> float:
        """
        attempt_index starts at 0 for the first retry backoff.
        """
        base = float(self._retry_429_base_seconds)
        mx = float(self._retry_429_max_seconds)
        delay = min(mx, base * (2.0 ** float(max(0, attempt_index))))
        jr = float(self._retry_429_jitter_ratio)
        if jr > 0 and delay > 0:
            delay = delay + random.uniform(0.0, jr * delay)
        return float(delay)

    def _build_langchain_llm_instance(self) -> Any:
        """Build a fresh underlying LangChain chat model (with tools bound if provided)."""
        base_llm = self._create_langchain_llm(
            provider=self.provider,
            model=self.model,
            temperature=self._init_temperature,
            max_tokens=self._init_max_tokens,
            timeout=self._init_timeout,
            **self._init_kwargs,
        )
        tools_to_bind: List[Any] = list(self.tools or [])
        if self.enable_web_search:
            t = self._web_search_tool_for_provider()
            if t is not None:
                tools_to_bind.append(t)

        # Anthropic (Claude) supports deferred tool loading via a tool-search helper.
        # If any bound tool is marked with extras={"defer_loading": True}, we best-effort
        # add Anthropic's regex tool search parameter object so the model can discover tools
        # without eagerly loading all schemas into context.
        if self.provider == LLMProvider.ANTHROPIC and tools_to_bind:
            def _is_deferred_tool(x: Any) -> bool:
                ex = getattr(x, "extras", None)
                return isinstance(ex, dict) and ex.get("defer_loading") is True

            if any(_is_deferred_tool(x) for x in tools_to_bind):
                try:
                    from anthropic.types.beta import BetaToolSearchToolBm25_20251119Param  # type: ignore

                    tools_to_bind.insert(
                        0,
                        BetaToolSearchToolBm25_20251119Param(
                            name="tool_search_tool_bm25",
                            type="tool_search_tool_bm25_20251119",
                        ),
                    )
                except Exception:
                    # Best-effort only; skip if anthropic beta types aren't installed.
                    pass

        if tools_to_bind:
            # Some providers (notably Gemini function calling) reject duplicate tool/function names.
            # Deduplicate by tool.name to keep the schema valid.
            deduped_tools: List[Any] = []
            seen_names: set[str] = set()
            for t in tools_to_bind:
                name = getattr(t, "name", None)
                name = str(name) if name is not None else ""
                if name and name in seen_names:
                    continue
                if name:
                    seen_names.add(name)
                deduped_tools.append(t)
            if len(deduped_tools) != len(tools_to_bind):
                try:
                    self.logger.warning(
                        "Deduplicated %d tool(s) by name before bind_tools for model=%s",
                        (len(tools_to_bind) - len(deduped_tools)),
                        self.model,
                    )
                except Exception:
                    pass
            try:
                llm = base_llm.bind_tools(deduped_tools)
                return llm
            except Exception as e:
                self.logger.warning(f"Failed to bind tools to LLM: {e}. Continuing without tools.")
                return base_llm
        return base_llm

    def _get_cached_llm(self, *, kind: str) -> Any:
        """
        Get a loop-/thread-scoped LLM instance.
        - kind="async": cache per running event loop
        - kind="sync": cache per current OS thread
        """
        if kind == "async":
            loop = asyncio.get_running_loop()
            key = ("async", id(loop))
        else:
            key = ("sync", threading.get_ident())

        with self._llm_lock:
            llm = self._llm_cache.get(key)
            if llm is None:
                llm = self._build_langchain_llm_instance()
                self._llm_cache[key] = llm
                # Optional debug to confirm loop/thread scoping in production.
                if str(os.getenv("MOOSE_DEBUG_LLM_LOOP_CACHE", "")).strip().lower() in {"1", "true", "yes"}:
                    try:
                        self.logger.info(
                            "LangChainLLM cache miss: model=%s provider=%s kind=%s key=%s tools=%d",
                            self.model,
                            getattr(self.provider, "value", str(self.provider)),
                            kind,
                            key,
                            len(self.tools or []),
                        )
                    except Exception:
                        pass
        return llm
    
    def _create_langchain_llm(
        self,
        provider: LLMProvider,
        model: str,
        temperature: float,
        max_tokens: Optional[int],
        timeout: Optional[float],
        **kwargs
    ):
        """Create appropriate LangChain LLM class based on provider."""
        llm_kwargs = {
            "model": model,
            "temperature": temperature,
        }
        
        if max_tokens is not None:
            llm_kwargs["max_tokens"] = max_tokens
        
        if timeout is not None:
            llm_kwargs["timeout"] = timeout
        
        # Add any extra kwargs
        llm_kwargs.update(kwargs)
        
        if provider == LLMProvider.OPENAI:
            return ChatOpenAI(**llm_kwargs)
        elif provider == LLMProvider.AZURE_AI:
            azure_model = model
            if isinstance(azure_model, str) and azure_model.lower().startswith("azure:"):
                azure_model = azure_model.split(":", 1)[1].strip() or azure_model
            azure_kwargs = dict(llm_kwargs)
            azure_kwargs.pop("model_name", None)
            azure_kwargs["model"] = azure_model
            return AzureAIChatCompletionsModel(**azure_kwargs)
        elif provider == LLMProvider.ANTHROPIC:
            return ChatAnthropic(**llm_kwargs)
        elif provider == LLMProvider.GEMINI:
            return ChatGoogleGenerativeAI(**llm_kwargs)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    def _message_to_langchain(self, message: Union[str, Message]) -> BaseMessage:
        """Convert our Message model to LangChain message."""
        if isinstance(message, str):
            return HumanMessage(content=message)
        
        if message.role == MessageRole.SYSTEM:
            return SystemMessage(content=message.content if isinstance(message.content, str) else str(message.content))
        elif message.role == MessageRole.USER:
            # Handle multimodal content
            if isinstance(message.content, list):
                # Content blocks for multimodal (e.g., images)
                return HumanMessage(content=message.content)
            else:
                return HumanMessage(content=message.content)
        elif message.role == MessageRole.ASSISTANT:
            # For ASSISTANT messages with tool calls, need to preserve them
            if message.tool_calls:
                # Convert tool_calls to LangChain format
                # LangChain AIMessage expects tool_calls in a specific format
                langchain_tool_calls = []
                for tc in message.tool_calls:
                    if isinstance(tc, dict):
                        langchain_tool_calls.append(tc)
                    else:
                        # If it's already a LangChain tool call object, use as-is
                        langchain_tool_calls.append(tc)
                return AIMessage(
                    content=message.content if isinstance(message.content, str) else str(message.content),
                    tool_calls=langchain_tool_calls
                )
            return AIMessage(content=message.content if isinstance(message.content, str) else str(message.content))
        elif message.role == MessageRole.TOOL:
            # Carry tool name through ToolMessage when available so logs/UI can display it.
            # Different LangChain versions may or may not accept `name=` in the constructor,
            # so we attempt it and fall back to attribute assignment.
            tm_content = message.content if isinstance(message.content, str) else str(message.content)
            tm_tool_call_id = message.tool_call_id or ""
            if message.name:
                try:
                    return ToolMessage(content=tm_content, tool_call_id=tm_tool_call_id, name=message.name)
                except TypeError:
                    tm = ToolMessage(content=tm_content, tool_call_id=tm_tool_call_id)
                    try:
                        setattr(tm, "name", message.name)
                    except Exception:
                        pass
                    return tm
            return ToolMessage(content=tm_content, tool_call_id=tm_tool_call_id)

    @staticmethod
    def _normalize_content_to_text(content: Any) -> str:
        """
        Provider-agnostic normalization for LangChain message content.

        Some providers return AIMessage.content as a list of blocks, e.g.:
          [{"type":"text","text":"..."}, ...]

        We normalize to a plain string for downstream JSON extraction + SQLite logging.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        # Common multimodal/content-block formats
        if isinstance(content, list):
            parts: List[str] = []
            for b in content:
                if b is None:
                    continue
                if isinstance(b, str):
                    if b:
                        parts.append(b)
                    continue
                if isinstance(b, dict):
                    # Most common: {"type":"text","text":"..."}
                    if b.get("type") == "text" and b.get("text") is not None:
                        parts.append(str(b.get("text") or ""))
                        continue
                    # Fallback: try a generic "text" field if present
                    if b.get("text") is not None:
                        parts.append(str(b.get("text") or ""))
                        continue
                    # Last resort: JSON-ish repr
                    try:
                        parts.append(json.dumps(b, ensure_ascii=False, default=str))
                    except Exception:
                        parts.append(str(b))
                    continue
                # Unknown element type
                parts.append(str(b))
            return "\n".join([p for p in parts if p])
        if isinstance(content, dict):
            # Some SDKs may return dict-typed content.
            try:
                if content.get("type") == "text" and content.get("text") is not None:
                    return str(content.get("text") or "")
            except Exception:
                pass
            try:
                return json.dumps(content, ensure_ascii=False, default=str)
            except Exception:
                return str(content)
        return str(content)

    def _normalized_response_for_logging(self, response: Any) -> Any:
        """
        Ensure the object we hand to LLMLogger has string content, so trace DB writes don't
        fail when providers return list/dict content blocks.
        """
        if response is None:
            return None
        try:
            raw = getattr(response, "content", None)
        except Exception:
            raw = None
        text = self._normalize_content_to_text(raw)
        # Best-effort: mutate in-place so all loggers see the normalized content.
        try:
            setattr(response, "content", text)
            return response
        except Exception:
            pass
        # Fallback: construct a new AIMessage preserving tool_calls/metadata if possible.
        try:
            tc = getattr(response, "tool_calls", None)
            nm = AIMessage(content=text, tool_calls=tc) if tc else AIMessage(content=text)
            for attr in ("additional_kwargs", "response_metadata", "usage_metadata", "id", "name"):
                if hasattr(response, attr):
                    try:
                        setattr(nm, attr, getattr(response, attr))
                    except Exception:
                        pass
            return nm
        except Exception:
            return response
    
    def _langchain_to_message(self, langchain_msg: BaseMessage) -> Message:
        """Convert LangChain message to our Message model."""
        if isinstance(langchain_msg, SystemMessage):
            role = MessageRole.SYSTEM
        elif isinstance(langchain_msg, AIMessage):
            role = MessageRole.ASSISTANT
        elif isinstance(langchain_msg, ToolMessage):
            role = MessageRole.TOOL
        else:
            role = MessageRole.USER
        
        content = langchain_msg.content
        
        return Message(
            role=role,
            content=content,
            tool_call_id=getattr(langchain_msg, 'tool_call_id', None)
        )
    
    def _extract_usage_from_response(self, response: Any) -> Optional[Dict[str, int]]:
        """Extract usage information from LangChain response."""
        if hasattr(response, 'usage_metadata'):
            usage = response.usage_metadata
            if isinstance(usage, dict):
                return {
                    "input_tokens": usage.get('input_tokens', 0),
                    "output_tokens": usage.get('output_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0),
                }
        
        return None
    
    def _calculate_cost_from_usage(
        self,
        usage: Optional[Dict[str, int]]
    ) -> Optional[float]:
        """
        Calculate cost from token usage using per-million-token rates from config.
        
        Args:
            usage: Token usage dictionary with input_tokens and output_tokens
        
        Returns:
            Calculated cost in USD, or None if rates not available
        """
        if not usage:
            return None
        
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        
        if input_tokens == 0 and output_tokens == 0:
            return None
        
        # Get cost per token rates from config
        if not self.config:
            self.logger.debug("No config provided, cannot calculate cost")
            return None
        
        try:
            model_info = self.config.get_model_info(self.model)
            
            if not model_info:
                self.logger.error(f"Model info not found for {self.model}, cannot calculate cost")
                return None
            
            input_cost_per_million = model_info.get('input_cost_per_million_token')
            output_cost_per_million = model_info.get('output_cost_per_million_token')
            
            if input_cost_per_million is None or output_cost_per_million is None:
                self.logger.debug(f"Cost per token rates not found for {self.model}")
                return None
            
            # Calculate cost using USD per 1,000,000 tokens.
            cost = ((input_tokens * float(input_cost_per_million)) + (output_tokens * float(output_cost_per_million))) / 1_000_000.0
            
            self.logger.debug(
                f"Calculated cost for {self.model}: "
                f"{input_tokens} input * ${float(input_cost_per_million):.6f}/1M + "
                f"{output_tokens} output * ${float(output_cost_per_million):.6f}/1M = ${cost:.8f}"
            )
            
            return cost
            
        except Exception as e:
            self.logger.warning(f"Failed to calculate cost from usage: {e}")
            return None
    
    def _extract_cost_from_response(self, response: Any, usage: Optional[Dict[str, int]] = None) -> Optional[float]:
        """
        Extract cost information from LangChain response.
        
        If cost is not in response metadata, calculates it from token usage.
        
        Args:
            response: LangChain response object
            usage: Optional token usage dictionary (if already extracted)
        
        Returns:
            Cost in USD, or None if unavailable
        """
        return self._calculate_cost_from_usage(usage)
    
    async def ainvoke(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Async invoke LLM with a message and return response.
        
        Args:
            message: User message (string or Message object)
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message
            request_id: Optional request identifier
            **kwargs: Additional parameters
            
        Returns:
            LLMResponse object
        """
        # Build LangChain message list
        langchain_messages: List[BaseMessage] = []
        
        # Add system message if provided
        if system_message:
            if isinstance(system_message, str):
                langchain_messages.append(SystemMessage(content=system_message))
            else:
                langchain_messages.append(self._message_to_langchain(system_message))
        
        # Add conversation history if provided
        if messages:
            for msg in messages:
                langchain_msg = self._message_to_langchain(msg)
                langchain_messages.append(langchain_msg)
        
        # Add current message only if message is provided and not None
        # If messages already contains everything, we don't need to add message separately
        if message is not None:
            current_langchain_msg = self._message_to_langchain(message)
            langchain_messages.append(current_langchain_msg)
        
        # Log LLM request with all messages
        log_meta: Dict[str, Any] = {}
        if agent_name:
            log_meta["agent_name"] = agent_name
        self.llm_logger.log_request(messages=langchain_messages, request_id=request_id or "unknown", model=self.model, **log_meta)
        
        # Invoke LangChain LLM asynchronously (loop-scoped instance)
        response = None
        llm = self._get_cached_llm(kind="async")
        retries = int(self._retry_429_max_attempts or 0)
        attempt = 0
        while True:
            try:
                if hasattr(llm, "ainvoke"):
                    response = await llm.ainvoke(langchain_messages, **kwargs)
                else:
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: llm.invoke(langchain_messages, **kwargs))
                break
            except Exception as e:
                if hasattr(e, 'status_code') and e.status_code == 404:
                    self.logger.error(f"Model {self.model} not found")
                else:
                    self.logger.error(f"Error invoking model {self.model}: {e}")
                    
                if self._is_quota_exhausted_429(e) and attempt < retries:
                    delay = self._backoff_seconds(attempt)
                    attempt += 1
                    try:
                        self.logger.warning(
                            "Model %s returned 429/RESOURCE_EXHAUSTED; backing off %.2fs (retry %d/%d)",
                            self.model,
                            delay,
                            attempt,
                            retries,
                        )
                    except Exception:
                        pass
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue

                # Final failure: log a response record (NoneType) for trace visibility, then stop.
                try:
                    self.llm_logger.log_response(
                        response=None,
                        request_id=request_id or "unknown",
                        model=self.model,
                        usage=None,
                        cost=None,
                        **log_meta,
                    )
                except Exception:
                    pass

                self.logger.critical(f"Error invoking model {self.model}: {e}")
                # Stop on 429 to avoid tight multi-stage loops hammering quota.
                if self._is_quota_exhausted_429(e):
                    raise RuntimeError(
                        f"LLM quota exhausted (HTTP 429) for model '{self.model}' after {attempt} retry(s): {e}"
                    ) from e
                response = None
                break
        
        # Extract content
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Extract tool calls if present
        tool_calls = None
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Convert LangChain tool call objects to dict format for easier handling
            tool_calls = []
            for tc in response.tool_calls:
                if isinstance(tc, dict):
                    tool_calls.append(tc)
                else:
                    # Convert LangChain tool call object to dict
                    tool_calls.append({
                        'id': getattr(tc, 'id', None),
                        'name': getattr(tc, 'name', None),
                        'args': dict(getattr(tc, 'args', {})) if hasattr(tc, 'args') else {}
                    })
        
        # Extract usage
        usage = self._extract_usage_from_response(response)
        
        # Extract cost (will calculate from usage if not in response)
        cost = self._extract_cost_from_response(response, usage=usage)
        
        # Extract finish reason
        finish_reason = None
        if hasattr(response, 'response_metadata'):
            metadata = response.response_metadata
            if metadata:
                finish_reason = metadata.get('finish_reason') or metadata.get('stop_reason')
        
        # Log LLM response
        response_for_log = self._normalized_response_for_logging(response)
        self.llm_logger.log_response(
            response=response_for_log,
            request_id=request_id or "unknown",
            model=self.model,
            usage=usage,
            cost=cost,
            **log_meta,
        )
        
        return LLMResponse(
            content=self._normalize_content_to_text(content),
            model=self.model,
            finish_reason=finish_reason,
            usage=usage,
            cost=cost,
            raw_response=response,
            request_id=request_id,
            tool_calls=tool_calls
        )
    
    def invoke(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Invoke LLM with a message and return response.
        
        Args:
            message: User message (string or Message object)
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message
            request_id: Optional request identifier
            **kwargs: Additional parameters
        
        Returns:
            LLMResponse object
        """
        # Build LangChain message list
        langchain_messages: List[BaseMessage] = []
        
        # Add system message if provided
        if system_message:
            if isinstance(system_message, str):
                langchain_messages.append(SystemMessage(content=system_message))
            else:
                langchain_messages.append(self._message_to_langchain(system_message))
        
        # Add conversation history if provided
        if messages:
            for msg in messages:
                langchain_messages.append(self._message_to_langchain(msg))
        
        # Add current message
        langchain_messages.append(self._message_to_langchain(message))
        
        log_meta: Dict[str, Any] = {}
        if agent_name:
            log_meta["agent_name"] = agent_name
        # Log LLM request with all messages
        self.llm_logger.log_request(messages=langchain_messages, request_id=request_id or "unknown", model=self.model, **log_meta)
        
        # Invoke LangChain LLM (thread-scoped instance)
        response = None
        llm = self._get_cached_llm(kind="sync")
        retries = int(self._retry_429_max_attempts or 0)
        attempt = 0
        while True:
            try:
                response = llm.invoke(langchain_messages, **kwargs)
                break
            except Exception as e:
                if hasattr(e, 'status_code') and e.status_code == 404:
                    self.logger.error(f"Model {self.model} not found")

                if self._is_quota_exhausted_429(e) and attempt < retries:
                    delay = self._backoff_seconds(attempt)
                    attempt += 1
                    try:
                        self.logger.warning(
                            "Model %s returned 429/RESOURCE_EXHAUSTED; backing off %.2fs (retry %d/%d)",
                            self.model,
                            delay,
                            attempt,
                            retries,
                        )
                    except Exception:
                        pass
                    if delay > 0:
                        time.sleep(delay)
                    continue

                try:
                    self.llm_logger.log_response(
                        response=None,
                        request_id=request_id or "unknown",
                        model=self.model,
                        usage=None,
                        cost=None,
                        **log_meta,
                    )
                except Exception:
                    pass

                self.logger.critical(f"Error invoking model {self.model}: {e}")
                if self._is_quota_exhausted_429(e):
                    raise RuntimeError(
                        f"LLM quota exhausted (HTTP 429) for model '{self.model}' after {attempt} retry(s): {e}"
                    ) from e
                response = None
                break
        
        # Extract content
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Extract tool calls if present
        tool_calls = None
        if hasattr(response, 'tool_calls') and response.tool_calls:
            # Convert LangChain tool call objects to dict format for easier handling
            tool_calls = []
            for tc in response.tool_calls:
                if isinstance(tc, dict):
                    tool_calls.append(tc)
                else:
                    # Convert LangChain tool call object to dict
                    tool_calls.append({
                        'id': getattr(tc, 'id', None),
                        'name': getattr(tc, 'name', None),
                        'args': dict(getattr(tc, 'args', {})) if hasattr(tc, 'args') else {}
                    })
        
        # Extract usage
        usage = self._extract_usage_from_response(response)
        
        # Extract cost (will calculate from usage if not in response)
        cost = self._extract_cost_from_response(response, usage=usage)
        
        # Extract finish reason
        finish_reason = None
        if hasattr(response, 'response_metadata'):
            metadata = response.response_metadata
            if metadata:
                finish_reason = metadata.get('finish_reason') or metadata.get('stop_reason')
        
        # Log LLM response
        response_for_log = self._normalized_response_for_logging(response)
        self.llm_logger.log_response(
            response=response_for_log,
            request_id=request_id or "unknown",
            model=self.model,
            usage=usage,
            cost=cost,
            **log_meta,
        )
        
        return LLMResponse(
            content=self._normalize_content_to_text(content),
            model=self.model,
            finish_reason=finish_reason,
            usage=usage,
            cost=cost,
            raw_response=response,
            request_id=request_id,
            tool_calls=tool_calls
        )
    
    def stream(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        **kwargs
    ) -> Iterator[str]:
        """
        Stream LLM response.
        
        Args:
            message: User message (string or Message object)
            messages: Optional list of previous messages
            system_message: Optional system message
            **kwargs: Additional parameters
        
        Yields:
            Chunks of response content as strings
        """
        # Build LangChain message list
        langchain_messages: List[BaseMessage] = []
        
        # Add system message if provided
        if system_message:
            if isinstance(system_message, str):
                langchain_messages.append(SystemMessage(content=system_message))
            else:
                langchain_messages.append(self._message_to_langchain(system_message))
        
        # Add conversation history if provided
        if messages:
            for msg in messages:
                langchain_messages.append(self._message_to_langchain(msg))
        
        # Add current message
        langchain_messages.append(self._message_to_langchain(message))
        
        log_meta: Dict[str, Any] = {"streaming": True}
        if agent_name:
            log_meta["agent_name"] = agent_name
        # Log LLM request with all messages
        self.llm_logger.log_request(messages=langchain_messages, request_id=request_id or "unknown", model=self.model, **log_meta)
        
        # Collect full response for logging
        full_response_content = []
        
        # Stream from LangChain LLM (thread-scoped instance)
        llm = self._get_cached_llm(kind="sync")
        for chunk in llm.stream(langchain_messages, **kwargs):
            if hasattr(chunk, 'content'):
                content = chunk.content
                if content:
                    full_response_content.append(content)
                    yield content
            elif isinstance(chunk, str):
                full_response_content.append(chunk)
                yield chunk
        
        # Log the complete streamed response
        self.llm_logger.log_response(
            response={"content": "".join(full_response_content), "type": "StreamedResponse"},
            request_id=request_id or "unknown",
            model=self.model,
            streaming=True,
            **({"agent_name": agent_name} if agent_name else {}),
        )
    
    def get_langchain_llm(self):
        """
        Get the underlying LangChain LLM instance.
        
        Useful for advanced use cases like agents, chains, etc.
        
        Returns:
            LangChain LLM instance (ChatOpenAI, ChatAnthropic, or ChatGoogleGenerativeAI)
        """
        # Best-effort: if we're in an event loop, return that loop's instance; else return thread instance.
        try:
            return self._get_cached_llm(kind="async")
        except RuntimeError:
            return self._get_cached_llm(kind="sync")
