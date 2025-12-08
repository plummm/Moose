"""Universal LLM client for interacting with multiple LLM providers."""

import os
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from framework.llm_core.models import Message, MessageRole, LLMResponse
from framework.llm_core.providers import LLMProvider, get_provider, get_provider_model_string
from framework.llm_core.proxy_manager import ProxyManager
from framework.llm_core.cost_tracker import CostTracker
from framework.logging import get_core_logger

try:
    import litellm
    from litellm import completion, responses
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    litellm = None
    completion = None
    responses = None


class LLMClient:
    """
    Universal LLM client that provides a unified interface for multiple LLM providers.
    
    Supports: OpenAI, Anthropic (Claude), Google (Gemini), Cohere, Mistral, Ollama, and more.
    Routes requests through LiteLLM proxy for cost management and load balancing.
    """
    
    _proxy_initialized = False
    _cost_tracker: Optional[CostTracker] = None
    
    def __init__(
        self,
        model: str,
        provider: Optional[LLMProvider] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 1.0,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        use_proxy: Optional[float] = None,
        **kwargs
    ):
        """
        Initialize the LLM client.
        
        Args:
            model: Model name (e.g., "gpt-4", "claude-3-opus", "gemini-pro")
            provider: Explicit provider. If None, will be inferred from model name.
            api_key: API key for the provider. If None, will use environment variables.
            base_url: Base URL for API (useful for custom endpoints or proxies)
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate
            timeout: Request timeout in seconds
            use_proxy: Whether to route through LiteLLM proxy. Reads from MOOSE_LITELLM_USE_PROXY (default: True)
            **kwargs: Additional provider-specific parameters
        """
        if not LITELLM_AVAILABLE:
            raise ImportError(
                "litellm is required for LLM functionality. "
                "Install it with: pip install 'litellm[proxy]'"
            )
        
        self.logger = get_core_logger()
        self.model = model
        self.provider = provider or get_provider(model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra_params = kwargs
        
        self.proxy_manager = None
        
        # Read use_proxy from environment variable if not explicitly provided
        use_proxy_env = os.getenv("MOOSE_LITELLM_USE_PROXY", "false").lower()
        if use_proxy is None:
            use_proxy = use_proxy_env in ("true", "1", "yes", "on")
        
        self.use_proxy = use_proxy
        
        # Set up API key
        self.api_key = api_key or self._get_api_key()
        
        # Initialize proxy if not already done
        if use_proxy and not LLMClient._proxy_initialized:
            self._initialize_proxy()
        
        # Initialize cost tracker if not already done
        if LLMClient._cost_tracker is None:
            LLMClient._cost_tracker = CostTracker()
        
        # Configure LiteLLM
        self._configure_litellm()
        
        # Get model string (use proxy format if using proxy)
        if use_proxy:
            # Use model name as-is when routing through proxy
            self.provider_model = model
        else:
            # Get provider-prefixed model string for direct calls
            self.provider_model = get_provider_model_string(self.provider, model)
        
        self.logger.debug(f"Initialized LLM client: {self.provider.value}/{model} (proxy: {use_proxy})")
    
    def _initialize_proxy(self):
        """Initialize the LiteLLM proxy server."""
        if LLMClient._proxy_initialized:
            return
        
        try:
            # Start proxy manager
            self.proxy_manager = ProxyManager.get_instance()
            
            # Start proxy server (will use env vars for port/host and detect config)
            if self.proxy_manager.start():
                LLMClient._proxy_initialized = True
                self.logger.info("LiteLLM proxy initialized successfully")
            else:
                self.logger.warning("Failed to start proxy, falling back to direct calls")
                self.use_proxy = False
        except Exception as e:
            self.logger.warning(f"Failed to initialize proxy: {e}, falling back to direct calls")
            self.use_proxy = False
    
    def _get_api_key(self) -> Optional[str]:
        """Get API key from environment variables based on provider."""
        key_map = {
            LLMProvider.OPENAI: "OPENAI_API_KEY",
            LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
            LLMProvider.GOOGLE: "GOOGLE_API_KEY",
            LLMProvider.GEMINI: "GOOGLE_API_KEY",
            LLMProvider.COHERE: "COHERE_API_KEY",
            LLMProvider.MISTRAL: "MISTRAL_API_KEY",
            LLMProvider.OLLAMA: None,  # Ollama doesn't need API key
            LLMProvider.AZURE_OPENAI: "AZURE_API_KEY",
            LLMProvider.BEDROCK: "AWS_ACCESS_KEY_ID",  # AWS credentials
        }
        
        env_var = key_map.get(self.provider)
        if env_var:
            return os.getenv(env_var)
        return None
    
    def _configure_litellm(self):
        """Configure LiteLLM settings."""
        # Set API keys in environment if provided
        if self.api_key:
            if self.provider == LLMProvider.OPENAI:
                os.environ["OPENAI_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.ANTHROPIC:
                os.environ["ANTHROPIC_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.GOOGLE:
                os.environ["GOOGLE_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.COHERE:
                os.environ["COHERE_API_KEY"] = self.api_key
            elif self.provider == LLMProvider.MISTRAL:
                os.environ["MISTRAL_API_KEY"] = self.api_key
    
    def send_message(
        self,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send a message to the LLM and receive a response.
        
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
        # Build messages list
        message_list = []
        
        # Add system message if provided
        if system_message:
            if isinstance(system_message, str):
                message_list.append(Message(role=MessageRole.SYSTEM, content=system_message).to_dict())
            else:
                message_list.append(system_message.to_dict())
        
        # Add conversation history if provided
        if messages:
            message_list.extend([msg.to_dict() for msg in messages])
        
        # Add current message
        if isinstance(message, str):
            message_list.append(Message(role=MessageRole.USER, content=message).to_dict())
        else:
            message_list.append(message.to_dict())
        
        # Prepare parameters
        params = {
            "model": self.provider_model,
            "messages": message_list,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if self.max_tokens is not None:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if self.timeout is not None:
            params["timeout"] = kwargs.get("timeout", self.timeout)
        
        # Route through proxy if enabled
        if self.use_proxy:
            if self.proxy_manager.is_running():
                # Set base URL to proxy endpoint
                proxy_url = self.proxy_manager.get_proxy_url()
                params["api_base"] = proxy_url
                # Use model name as configured in proxy config (not provider-prefixed)
                # The proxy will route based on model_name in config.yaml
                params["model"] = self.model
                # Set headers for proxy authentication if needed
                try:
                    config = self.proxy_manager.get_config()
                    master_key = config.get_master_key()
                    if master_key:
                        params.setdefault("headers", {})
                        params["headers"]["x-litellm-api-key"] = master_key
                except:
                    pass  # If config fails, continue without auth
            else:
                self.logger.warning("Proxy not running, falling back to direct call")
                # Fall back to direct call with provider prefix
                params["model"] = get_provider_model_string(self.provider, self.model)
        
        # Add any extra parameters
        params.update(self.extra_params)
        params.update(kwargs)
        
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Sending message to {self.provider.value}/{self.model} (request_id: {request_id})")
        
        try:
            # Call LiteLLM
            response = completion(**params)
            
            # Extract response content
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            
            # Extract usage if available
            usage = None
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }
            
            # Extract cost from response
            cost = None
            if hasattr(response, '_hidden_params') and response._hidden_params:
                cost = response._hidden_params.get("response_cost")
            elif hasattr(response, 'cost'):
                cost = response.cost
            elif hasattr(response, '_response_cost'):
                cost = response._response_cost
            
            # Log cost if available
            if cost is not None and LLMClient._cost_tracker:
                LLMClient._cost_tracker.log_cost(
                    model=self.model,
                    cost=cost,
                    tokens=usage,
                    request_id=request_id
                )
            
            llm_response = LLMResponse(
                content=content or "",
                model=self.model,
                finish_reason=finish_reason,
                usage=usage,
                cost=cost,
                raw_response=response
            )
            
            self.logger.debug(f"Received response from {self.provider.value}/{self.model}")
            if usage:
                self.logger.debug(f"Token usage: {usage}")
            if cost is not None:
                self.logger.debug(f"Cost: ${cost:.6f}")
            
            return llm_response
            
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
        
        Args:
            message: User message
            messages: Optional conversation history
            system_message: Optional system message
            **kwargs: Additional parameters
        
        Yields:
            Chunks of the response as they arrive
        """
        # Build messages list (same as send_message)
        message_list = []
        
        if system_message:
            if isinstance(system_message, str):
                message_list.append(Message(role=MessageRole.SYSTEM, content=system_message).to_dict())
            else:
                message_list.append(system_message.to_dict())
        
        if messages:
            message_list.extend([msg.to_dict() for msg in messages])
        
        if isinstance(message, str):
            message_list.append(Message(role=MessageRole.USER, content=message).to_dict())
        else:
            message_list.append(message.to_dict())
        
        # Prepare parameters
        params = {
            "model": self.provider_model,
            "messages": message_list,
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": True,
        }
        
        if self.max_tokens is not None:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        # Route through proxy if enabled
        if self.use_proxy:
            if self.proxy_manager.is_running():
                proxy_url = self.proxy_manager.get_proxy_url()
                params["api_base"] = proxy_url
                params["model"] = self.model
                # Set headers for proxy authentication if needed
                try:
                    config = self.proxy_manager.get_config()
                    master_key = config.get_master_key()
                    if master_key:
                        params.setdefault("headers", {})
                        params["headers"]["x-litellm-api-key"] = master_key
                except:
                    pass  # If config fails, continue without auth
            else:
                self.logger.warning("Proxy not running, falling back to direct call")
                params["model"] = get_provider_model_string(self.provider, self.model)
        
        params.update(self.extra_params)
        params.update(kwargs)
        
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Streaming message to {self.provider.value}/{self.model} (request_id: {request_id})")
        
        try:
            response = completion(**params)
            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            self.logger.error(f"Error streaming from LLM: {e}")
            raise
    
    def _upload_pdf_file(
        self,
        file_path: Path,
        purpose: str = "assistants"
    ) -> str:
        """
        Internal method to upload a PDF file to the LLM provider.
        
        Args:
            file_path: Path to the PDF file to upload
            purpose: Purpose of the file (e.g., "assistants", "batch", "fine-tune")
        
        Returns:
            File ID that can be used in messages
        """
        self.logger.info(f"Uploading PDF file: {file_path}")
        
        try:
            # Use LiteLLM's file upload function
            # According to docs: https://docs.litellm.ai/docs/files_endpoints
            # We can use litellm.create_file for synchronous upload
            with open(file_path, 'rb') as f:
                # Prepare file upload parameters
                file_params = {
                    "file": f,
                    "purpose": purpose,
                }
                
                # Add provider-specific parameters
                if self.provider:
                    file_params["custom_llm_provider"] = self.provider.value
                
                if self.api_key:
                    file_params["api_key"] = self.api_key
                
                # If using proxy, set api_base
                if self.use_proxy and self.proxy_manager and self.proxy_manager.is_running():
                    proxy_url = self.proxy_manager.get_proxy_url()
                    file_params["api_base"] = proxy_url
                    try:
                        config = self.proxy_manager.get_config()
                        master_key = config.get_master_key()
                        if master_key:
                            file_params.setdefault("headers", {})
                            file_params["headers"] = {"x-litellm-api-key": master_key}
                    except:
                        pass
                
                file_obj = litellm.create_file(**file_params)
            
            file_id = file_obj.id if hasattr(file_obj, 'id') else str(file_obj)
            self.logger.info(f"PDF file uploaded successfully. File ID: {file_id}")
            return file_id
            
        except Exception as e:
            self.logger.error(f"Error uploading PDF file: {e}")
            raise
    
    def _upload_image_file(
        self,
        file_path: Path,
        purpose: str = "assistants"
    ) -> str:
        """
        Internal method to upload an image file to the LLM provider.
        
        Args:
            file_path: Path to the image file to upload
            purpose: Purpose of the file (e.g., "assistants", "batch", "fine-tune")
        
        Returns:
            File ID that can be used in messages
        """
        self.logger.info(f"Uploading image file: {file_path}")
        
        try:
            # Use LiteLLM's file upload function for images
            with open(file_path, 'rb') as f:
                # Prepare file upload parameters
                file_params = {
                    "file": f,
                    "purpose": purpose,
                }
                
                # Add provider-specific parameters
                if self.provider:
                    file_params["custom_llm_provider"] = self.provider.value
                
                if self.api_key:
                    file_params["api_key"] = self.api_key
                
                # If using proxy, set api_base
                if self.use_proxy and self.proxy_manager and self.proxy_manager.is_running():
                    proxy_url = self.proxy_manager.get_proxy_url()
                    file_params["api_base"] = proxy_url
                    try:
                        config = self.proxy_manager.get_config()
                        master_key = config.get_master_key()
                        if master_key:
                            file_params.setdefault("headers", {})
                            file_params["headers"] = {"x-litellm-api-key": master_key}
                    except:
                        pass
                
                file_obj = litellm.create_file(**file_params)
            
            file_id = file_obj.id if hasattr(file_obj, 'id') else str(file_obj)
            self.logger.info(f"Image file uploaded successfully. File ID: {file_id}")
            return file_id
            
        except Exception as e:
            self.logger.error(f"Error uploading image file: {e}")
            raise
    
    def upload_file(
        self,
        file_path: Union[str, Path],
        purpose: str = "assistants"
    ) -> str:
        """
        Upload a file to the LLM provider and get a file ID.
        
        According to LiteLLM documentation: https://docs.litellm.ai/docs/files_endpoints
        Files must be uploaded first using the /files endpoint, then the file ID
        can be used in messages.
        
        This method routes to the appropriate sub-function based on file type:
        - PDF files: Uses _upload_pdf_file
        - Image files: Uses _upload_image_file
        
        Args:
            file_path: Path to the file to upload
            purpose: Purpose of the file (e.g., "assistants", "batch", "fine-tune")
        
        Returns:
            File ID that can be used in messages
        
        Example:
            >>> client = LLMClient(model="gpt-4o")
            >>> file_id = client.upload_file("/path/to/report.pdf")
            >>> # Use file_id in send_message_with_file_id()
        """
        if not LITELLM_AVAILABLE:
            raise ImportError(
                "litellm is required for file uploads. "
                "Install it with: pip install 'litellm[proxy]'"
            )
            
        if self.provider.value != LLMProvider.OPENAI.value:
            raise ValueError(f"File upload is only supported for OpenAI. Provider: {self.provider.value}")
        
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Determine file type based on extension
        file_ext = file_path.suffix.lower()
        is_image = file_ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']
        is_pdf = file_ext == '.pdf'
        
        # Route to appropriate upload function
        if is_pdf:
            return self._upload_pdf_file(file_path, purpose)
        elif is_image:
            return self._upload_image_file(file_path, purpose)
        else:
            # For other file types, default to PDF upload method
            self.logger.warning(f"Unknown file type {file_ext}, using PDF upload method")
            return self._upload_pdf_file(file_path, purpose)
    
    def _send_message_with_pdf_file(
        self,
        file_path: Path,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Internal method to send a message with a PDF file attachment using responses API.
        
        Args:
            file_path: Path to the PDF file
            message: User message (string or Message object) describing what to do with the file
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message to set behavior
            **kwargs: Additional parameters to override defaults
        
        Returns:
            LLMResponse object containing the response
        """
        # For PDFs and other files, upload first to get file ID, then use responses API
        # The responses API supports file IDs via input parameter
        file_id = self.upload_file(file_path, purpose=kwargs.get("file_purpose", "assistants"))
        
        # Build input for responses API
        # Format: List of input items with input_text and input_file
        if isinstance(message, str):
            text_content = message
        else:
            if isinstance(message.content, str):
                text_content = message.content
            else:
                text_content = "Please analyze the attached file."
        
        # Create input list for responses API
        # Format: [{"type": "input_text", "text": "..."}, {"type": "input_file", "file_id": "file-abc123"}]
        input_items = [
            {
                "type": "input_text",
                "text": text_content
            },
            {
                "type": "input_file",
                "file_id": file_id
            }
        ]
        
        # Prepare parameters for responses API
        params = {
            "input": [
                {
                    "role": "user",
                    "content": input_items
                }
            ],
            "model": self.provider_model,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        # Add instructions (system message) if provided
        if system_message:
            if isinstance(system_message, str):
                params["instructions"] = system_message
            else:
                if isinstance(system_message.content, str):
                    params["instructions"] = system_message.content
        
        if self.max_tokens is not None:
            params["max_output_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if self.timeout is not None:
            params["timeout"] = kwargs.get("timeout", self.timeout)
        
        # Route through proxy if enabled
        if self.use_proxy:
            if self.proxy_manager.is_running():
                proxy_url = self.proxy_manager.get_proxy_url()
                params["api_base"] = proxy_url
                params["model"] = self.model
                try:
                    config = self.proxy_manager.get_config()
                    master_key = config.get_master_key()
                    if master_key:
                        params.setdefault("extra_headers", {})
                        params["extra_headers"]["x-litellm-api-key"] = master_key
                except:
                    pass
            else:
                self.logger.warning("Proxy not running, falling back to direct call")
                params["model"] = get_provider_model_string(self.provider, self.model)
        
        # Add any extra parameters
        params.update(self.extra_params)
        params.update(kwargs)
        
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Sending message with file ID {file_id} to {self.provider.value}/{self.model} using responses API (request_id: {request_id})")
        
        try:
            # Call LiteLLM responses API
            response = responses(**params)
            
            # Extract response content from responses API format
            # Responses API returns ResponsesAPIResponse with output list
            if hasattr(response, 'output') and response.output:
                # Get text from output items
                output_texts = []
                for item in response.output:
                    if hasattr(item, 'content') and item.content:
                        for content_part in item.content:
                            if hasattr(content_part, 'text'):
                                output_texts.append(content_part.text)
                            elif isinstance(content_part, dict) and 'text' in content_part:
                                output_texts.append(content_part['text'])
                content = "\n".join(output_texts) if output_texts else ""
            elif hasattr(response, 'text') and response.text:
                # Some responses may have direct text attribute
                if isinstance(response.text, str):
                    content = response.text
                elif hasattr(response.text, 'text'):
                    content = response.text.text
                else:
                    content = str(response.text)
            else:
                content = ""
            
            # Extract usage if available
            usage = None
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": getattr(response.usage, 'input_tokens', getattr(response.usage, 'prompt_tokens', 0)),
                    "completion_tokens": getattr(response.usage, 'output_tokens', getattr(response.usage, 'completion_tokens', 0)),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }
            
            # Extract cost from response
            cost = None
            if hasattr(response, 'usage') and hasattr(response.usage, 'cost') and response.usage.cost != None:
                cost = response.usage.cost
            elif hasattr(response, '_hidden_params') and response._hidden_params:
                cost = response._hidden_params.get("response_cost")
            elif hasattr(response, 'cost'):
                cost = response.cost
            elif hasattr(response, '_response_cost'):
                cost = response._response_cost
            
            # Log cost if available
            if cost is not None and LLMClient._cost_tracker:
                LLMClient._cost_tracker.log_cost(
                    model=self.model,
                    cost=cost,
                    tokens=usage,
                    request_id=request_id
                )
            
            # Get finish_reason from status
            finish_reason = None
            if hasattr(response, 'status'):
                finish_reason = response.status
            
            llm_response = LLMResponse(
                content=content or "",
                model=self.model,
                finish_reason=finish_reason,
                usage=usage,
                cost=cost,
                raw_response=response
            )
            
            self.logger.debug(f"Received response from {self.provider.value}/{self.model}")
            if usage:
                self.logger.debug(f"Token usage: {usage}")
            if cost is not None:
                self.logger.debug(f"Cost: ${cost:.6f}")
            
            return llm_response
            
        except Exception as e:
            self.logger.error(f"Error calling LLM with file via responses API: {e}")
            raise
    
    def _send_message_with_image_file(
        self,
        file_path: Path,
        message: Union[str, Message],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Internal method to send a message with an image file attachment using completion API with base64.
        
        Args:
            file_path: Path to the image file
            message: User message (string or Message object) describing what to do with the file
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message to set behavior
            **kwargs: Additional parameters to override defaults
        
        Returns:
            LLMResponse object containing the response
        """
        # For images, use base64 encoding directly (works with vision models)
        import base64
        
        file_ext = file_path.suffix.lower()
        
        self.logger.info(f"Reading image file: {file_path}")
        with open(file_path, 'rb') as f:
            file_data = f.read()
        
        # Determine MIME type
        mime_type_map = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
        }
        mime_type = mime_type_map.get(file_ext, 'image/png')
        
        # Encode to base64
        base64_data = base64.b64encode(file_data).decode('utf-8')
        
        # Build message content with base64 image
        if isinstance(message, str):
            text_content = message
        else:
            if isinstance(message.content, str):
                text_content = message.content
            else:
                text_content = "Please analyze the attached image."
        
        content_blocks = [
            {
                "type": "text",
                "text": text_content
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64_data}"
                }
            }
        ]
        
        # Build messages list for completion API
        message_list = []
        
        # Add system message if provided
        if system_message:
            if isinstance(system_message, str):
                message_list.append(Message(role=MessageRole.SYSTEM, content=system_message).to_dict())
            else:
                message_list.append(system_message.to_dict())
        
        # Add conversation history if provided
        if messages:
            message_list.extend([msg.to_dict() for msg in messages])
        
        # Add current message with image
        user_message = {
            "role": "user",
            "content": content_blocks
        }
        message_list.append(user_message)
        
        # Prepare parameters for completion API
        params = {
            "model": self.provider_model,
            "messages": message_list,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if self.max_tokens is not None:
            params["max_tokens"] = kwargs.get("max_tokens", self.max_tokens)
        
        if self.timeout is not None:
            params["timeout"] = kwargs.get("timeout", self.timeout)
        
        # Route through proxy if enabled
        if self.use_proxy:
            if self.proxy_manager.is_running():
                proxy_url = self.proxy_manager.get_proxy_url()
                params["api_base"] = proxy_url
                params["model"] = self.model
                try:
                    config = self.proxy_manager.get_config()
                    master_key = config.get_master_key()
                    if master_key:
                        params.setdefault("headers", {})
                        params["headers"]["x-litellm-api-key"] = master_key
                except:
                    pass
            else:
                self.logger.warning("Proxy not running, falling back to direct call")
                params["model"] = get_provider_model_string(self.provider, self.model)
        
        # Add any extra parameters
        params.update(self.extra_params)
        params.update(kwargs)
        
        request_id = str(uuid.uuid4())
        self.logger.debug(f"Sending message with base64 image to {self.provider.value}/{self.model} (request_id: {request_id})")
        
        try:
            # Call LiteLLM completion API for images
            response = completion(**params)
            
            # Extract response content
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            
            # Extract usage if available
            usage = None
            if hasattr(response, 'usage') and response.usage:
                usage = {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }
            
            # Extract cost from response
            cost = None
            if hasattr(response, '_hidden_params') and response._hidden_params:
                cost = response._hidden_params.get("response_cost")
            elif hasattr(response, 'cost'):
                cost = response.cost
            elif hasattr(response, '_response_cost'):
                cost = response._response_cost
            
            # Log cost if available
            if cost is not None and LLMClient._cost_tracker:
                LLMClient._cost_tracker.log_cost(
                    model=self.model,
                    cost=cost,
                    tokens=usage,
                    request_id=request_id
                )
            
            llm_response = LLMResponse(
                content=content or "",
                model=self.model,
                finish_reason=finish_reason,
                usage=usage,
                cost=cost,
                raw_response=response
            )
            
            self.logger.debug(f"Received response from {self.provider.value}/{self.model}")
            if usage:
                self.logger.debug(f"Token usage: {usage}")
            if cost is not None:
                self.logger.debug(f"Cost: ${cost:.6f}")
            
            return llm_response
            
        except Exception as e:
            self.logger.error(f"Error calling LLM with file: {e}")
            raise
    
    def send_message_with_file(
        self,
        message: Union[str, Message],
        file_path: Union[str, Path],
        messages: Optional[List[Message]] = None,
        system_message: Optional[Union[str, Message]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        Send a message with a file attachment (PDF, image, etc.) to a vision-capable LLM.
        
        This method routes to the appropriate sub-function based on file type:
        - PDF files: Uses _send_message_with_pdf_file (responses API)
        - Image files: Uses _send_message_with_image_file (completion API with base64)
        
        Args:
            message: User message (string or Message object) describing what to do with the file
            file_path: Path to the file to upload (PDF, image, etc.)
            messages: Optional list of previous messages for conversation context
            system_message: Optional system message to set behavior
            **kwargs: Additional parameters to override defaults
        
        Returns:
            LLMResponse object containing the response
        
        Example:
            >>> client = LLMClient(model="gpt-4o")
            >>> response = client.send_message_with_file(
            ...     message="Analyze this earning report",
            ...     file_path="/path/to/report.pdf"
            ... )
        """
        file_path = Path(file_path)
        file_ext = file_path.suffix.lower()
        
        # Determine if this is an image (can use base64) or other file (needs upload)
        is_image = file_ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']
        is_pdf = file_ext == '.pdf'
        
        # Route to appropriate sub-function
        if is_image:
            return self._send_message_with_image_file(
                file_path=file_path,
                message=message,
                messages=messages,
                system_message=system_message,
                **kwargs
            )
        elif is_pdf:
            return self._send_message_with_pdf_file(
                file_path=file_path,
                message=message,
                messages=messages,
                system_message=system_message,
                **kwargs
            )
        else:
            # For other file types, default to PDF method
            self.logger.warning(f"Unknown file type {file_ext}, using PDF upload method")
            return self._send_message_with_pdf_file(
                file_path=file_path,
                message=message,
                messages=messages,
                system_message=system_message,
                **kwargs
            )

