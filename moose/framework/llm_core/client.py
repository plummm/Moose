"""Universal LLM client for interacting with multiple LLM providers using LangChain."""

import os
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from framework.llm_core.models import Message, MessageRole, LLMResponse
from framework.llm_core.providers import LLMProvider, get_provider
from framework.llm_core.cost_tracker import CostTracker
from framework.llm_core.langchain_integration import LangChainLLM
from framework.llm_core.config import ModelConfig
from framework.logging import get_core_logger


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
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        config: Optional[ModelConfig] = None,
        **kwargs
    ):
        """
        Initialize the LLM client.
        
        Args:
            model: Model name (e.g., "gpt-4", "claude-3-opus-20240229", "gemini-pro")
            provider: Explicit provider. If None, will be inferred from model name.
            api_key: API key for the provider. If None, will use environment variables.
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            config: Optional ModelConfig instance (for cost calculation)
            **kwargs: Additional provider-specific parameters
        """
        self.logger = get_core_logger()
        self.model = model
        self.provider = provider or get_provider(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra_params = kwargs
        
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
        
        # Initialize LangChain LLM wrapper
        try:
            self.langchain_llm = LangChainLLM(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                config=config,
                **kwargs
            )
            self.logger.debug(f"Initialized LangChain LLM wrapper for {model}")
        except Exception as e:
            self.logger.error(f"Failed to initialize LangChain LLM: {e}")
            raise
        
        self.logger.debug(f"Initialized LLM client: {self.provider.value}/{model}")
    
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
    
    def send_message(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send a message to the LLM and receive a response.
        
        Uses LangChain native provider classes internally.
        
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
            # Use LangChain LLM wrapper
            response = self.langchain_llm.invoke(
                message=message,
                messages=messages,
                system_message=system_message,
                request_id=request_id,
                **kwargs
            )
            
            # Log cost if available
            if response.cost is not None and LLMClient._cost_tracker:
                LLMClient._cost_tracker.log_cost(
                    model=self.model,
                    cost=response.cost,
                    tokens=response.usage,
                    request_id=request_id
                )
            
            self.logger.debug(f"Received response from {self.model}")
            if response.usage:
                self.logger.debug(f"Token usage: {response.usage}")
            if response.cost is not None:
                self.logger.debug(f"Cost: ${response.cost:.6f}")
            
            return response
            
        except Exception as e:
            self.logger.error(f"Error calling LLM: {e}")
            raise
    
    def send_messages(
        self,
        messages: List[Union[str, Message]],
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send multiple messages in a conversation.
        
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
        
        return self.send_message(
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
                **kwargs
            ):
                yield chunk
        except Exception as e:
            self.logger.error(f"Error streaming from LLM: {e}")
            raise
