"""LangChain integration layer for LLM interactions.

This module provides a unified interface to LangChain using native provider classes:
- ChatOpenAI for OpenAI models
- ChatAnthropic for Anthropic models
- ChatGoogleGenerativeAI for Gemini models
"""

from typing import List, Optional, Dict, Any, Union, Iterator
from framework.llm_core.models import Message, MessageRole, LLMResponse
from framework.llm_core.providers import LLMProvider, get_provider
from framework.llm_core.config import ModelConfig
from framework.logging import get_core_logger

try:
    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    from langchain_google_genai import ChatGoogleGenerativeAI
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
            **kwargs: Additional parameters for the LangChain LLM
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LangChain is required. Install with: pip install langchain langchain-openai langchain-anthropic langchain-google-genai"
            )
        
        self.logger = get_core_logger()
        self.model = model
        self.provider = get_provider(model)
        self.config = config
        
        # Initialize appropriate LangChain class based on provider
        self.llm = self._create_langchain_llm(
            provider=self.provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            **kwargs
        )
        
        self.logger.debug(f"Initialized LangChainLLM for {self.provider.value} model: {model}")
    
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
            return AIMessage(content=message.content if isinstance(message.content, str) else str(message.content))
        elif message.role == MessageRole.TOOL:
            return ToolMessage(
                content=message.content if isinstance(message.content, str) else str(message.content),
                tool_call_id=message.tool_call_id or ""
            )
        else:
            # Default to human message
            return HumanMessage(content=str(message.content))
    
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
        Calculate cost from token usage using cost per token rates from config.
        
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
                self.logger.debug(f"Model info not found for {self.model}, cannot calculate cost")
                return None
            
            input_cost_per_token = model_info.get('input_cost_per_token')
            output_cost_per_token = model_info.get('output_cost_per_token')
            
            if input_cost_per_token is None or output_cost_per_token is None:
                self.logger.debug(f"Cost per token rates not found for {self.model}")
                return None
            
            # Calculate cost: (input_tokens * input_cost) + (output_tokens * output_cost)
            cost = (input_tokens * input_cost_per_token) + (output_tokens * output_cost_per_token)
            
            self.logger.debug(
                f"Calculated cost for {self.model}: "
                f"{input_tokens} prompt * ${input_cost_per_token:.8f} + "
                f"{output_tokens} completion * ${output_cost_per_token:.8f} = ${cost:.8f}"
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
        # First, try to extract cost from response metadata
        if hasattr(response, 'response_metadata'):
            metadata = response.response_metadata
            if metadata:
                # Check various possible cost fields
                cost = metadata.get('cost') or metadata.get('response_cost') or metadata.get('cost_usd')
                if cost is not None:
                    return float(cost)
        
        # Try direct attributes
        if hasattr(response, 'cost'):
            cost = response.cost
            if cost is not None:
                return float(cost)
        if hasattr(response, 'response_cost'):
            cost = response.response_cost
            if cost is not None:
                return float(cost)
        
        # If cost not found in response, calculate from token usage
        if usage:
            calculated_cost = self._calculate_cost_from_usage(usage)
            if calculated_cost is not None:
                return calculated_cost
        
        return None
    
    def invoke(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: Optional[str] = None,
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
        
        # Invoke LangChain LLM
        try:
            response = self.llm.invoke(langchain_messages, **kwargs)
        except Exception as e:
            if hasattr(e, 'status_code') and e.status_code == 404:
                self.logger.error(f"Model {self.model} not found")
            self.logger.fatal(f"Error invoking model {self.model}: {e}")
            response = None
        
        # Extract content
        content = response.content if hasattr(response, 'content') else str(response)
        
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
        
        return LLMResponse(
            content=content or "",
            model=self.model,
            finish_reason=finish_reason,
            usage=usage,
            cost=cost,
            raw_response=response,
            request_id=request_id
        )
    
    def stream(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: Optional[str] = None,
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
        
        # Stream from LangChain LLM
        for chunk in self.llm.stream(langchain_messages, **kwargs):
            if hasattr(chunk, 'content'):
                content = chunk.content
                if content:
                    yield content
            elif isinstance(chunk, str):
                yield chunk
    
    def get_langchain_llm(self):
        """
        Get the underlying LangChain LLM instance.
        
        Useful for advanced use cases like agents, chains, etc.
        
        Returns:
            LangChain LLM instance (ChatOpenAI, ChatAnthropic, or ChatGoogleGenerativeAI)
        """
        return self.llm
