"""Universal LLM client for interacting with multiple LLM providers using LangChain."""

import os
import uuid
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple
try:
    from moose.framework.llm_core.models import Message, MessageRole, LLMResponse
    from moose.framework.llm_core.providers import LLMProvider, get_provider
    from moose.framework.llm_core.cost_tracker import CostTracker
    from moose.framework.llm_core.langchain_integration import LangChainLLM
    from moose.framework.llm_core.config import ModelConfig
    from moose.framework.logging import get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.llm_core.models import Message, MessageRole, LLMResponse
    from framework.llm_core.providers import LLMProvider, get_provider
    from framework.llm_core.cost_tracker import CostTracker
    from framework.llm_core.langchain_integration import LangChainLLM
    from framework.llm_core.config import ModelConfig
    from framework.logging import get_core_logger

# Try to import tiktoken for token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    tiktoken = None


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
        max_input_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        config: Optional[ModelConfig] = None,
        tools: Optional[List[Any]] = None,
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
            max_input_tokens: Maximum input tokens for the model (default: 128000)
            timeout: Request timeout in seconds
            config: Optional ModelConfig instance (for cost calculation)
            tools: Optional list of LangChain tools to bind to the LLM
            **kwargs: Additional provider-specific parameters
        """
        self.logger = get_core_logger()
        self.model = model
        self.provider = provider or get_provider(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_input_tokens = max_input_tokens or kwargs.pop('max_input_tokens', 128000)
        self.timeout = timeout
        self.extra_params = kwargs
        
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
        
        # Store tools for potential tool execution
        self.tools = tools or []
        self.tool_map = {}
        if self.tools:
            # Create a map of tool names to tool instances for easy lookup
            for tool in self.tools:
                if hasattr(tool, 'name'):
                    self.tool_map[tool.name] = tool
        
        # Initialize LangChain LLM wrapper
        try:
            self.langchain_llm = LangChainLLM(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
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
    
    async def _execute_tool_calls(
        self,
        tool_calls: List[Any],
        messages: Optional[List[Message]] = None
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
                tool_call_id = tool_call.get('id')
                tool_args = tool_call.get('args', {})
            else:
                # LangChain tool call object
                tool_name = getattr(tool_call, 'name', None)
                tool_call_id = getattr(tool_call, 'id', None)
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
                    tool_call_id=tool_call_id
                ))
                continue
            
            tool = self.tool_map[tool_name]
            
            try:
                self.logger.debug(f"Executing tool: {tool_name} with args: {tool_args}")
                
                # Execute tool (LangChain tools support both sync and async)
                # LangChain StructuredTool has invoke/ainvoke methods
                if hasattr(tool, 'ainvoke'):
                    # Async invoke
                    result = await tool.ainvoke(tool_args)
                elif hasattr(tool, 'invoke'):
                    # Sync invoke - run in executor for async context
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, lambda: tool.invoke(tool_args))
                elif callable(tool):
                    # Direct function call (fallback)
                    if asyncio.iscoroutinefunction(tool):
                        result = await tool(**tool_args)
                    else:
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(None, lambda: tool(**tool_args))
                else:
                    raise ValueError(f"Tool {tool_name} is not callable")
                
                # Convert result to string
                if isinstance(result, str):
                    result_str = result
                else:
                    result_str = str(result)
                
                tool_msg = Message(
                    role=MessageRole.TOOL,
                    content=result_str,
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
                    tool_call_id=tool_call_id
                ))
        
        return tool_messages
    
    async def _send_message_direct(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        request_id: str = None,
        max_tool_iterations: int = 20,
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
            max_tool_iterations: Maximum number of tool call iterations (default: 10)
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
        
        # Iterate until we get a final response (no more tool calls) or hit max iterations
        for iteration in range(max_tool_iterations):
            # Use LangChain LLM wrapper asynchronously
            response = await self.langchain_llm.ainvoke(
                message=None,  # Don't split - pass everything in messages
                messages=conversation_messages,  # Pass full conversation history
                system_message=system_message,
                request_id=f"{request_id}_iter_{iteration}",
                **kwargs
            )
            
            # Accumulate cost and usage
            if response.cost:
                total_cost += response.cost
            if response.usage:
                total_usage["input_tokens"] += response.usage.get("input_tokens", 0)
                total_usage["output_tokens"] += response.usage.get("output_tokens", 0)
                total_usage["total_tokens"] += response.usage.get("total_tokens", 0)
            
            # Add assistant response to conversation
            conversation_messages.append(Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls
            ))
            
            # Check if there are tool calls
            if response.tool_calls and self.tools:
                self.logger.debug(f"Iteration {iteration + 1}: LLM requested {len(response.tool_calls)} tool calls")
                
                # Execute tools
                tool_messages = await self._execute_tool_calls(response.tool_calls, conversation_messages)
                
                conversation_messages.extend(tool_messages)
                
                # Continue loop to get final response with tool results
                continue
            else:
                # No more tool calls, this is the final response
                final_response = response
                break
        
        if final_response is None:
            # Hit max iterations, use last response
            self.logger.warning(f"Reached max tool iterations ({max_tool_iterations}), using last response")
            final_response = response
        
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
                **kwargs
            ):
                yield chunk
        except Exception as e:
            self.logger.error(f"Error streaming from LLM: {e}")
            raise
