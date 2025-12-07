"""Unit tests for LLM Core module."""

import os
import pytest
import tempfile
from pathlib import Path
from framework.llm_core import LLMClient, Message, MessageRole, LLMResponse
from framework.llm_core.proxy_manager import ProxyManager
from framework.llm_core.cost_tracker import CostTracker
from framework.logging import init_core_logger


pytestmark = pytest.mark.llm


class TestLLMCore:
    """Test suite for LLM Core functionality."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment."""
        # Initialize logger
        init_core_logger(debug=True)
        
        # Check for required API keys
        self.has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
        self.has_anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        self.has_google_key = bool(os.getenv("GOOGLE_API_KEY"))
        
        # Create temporary directory for cost tracking
        self.temp_dir = tempfile.mkdtemp()
        yield
        # Cleanup
        import shutil
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_llm_client_initialization(self):
        """Test LLMClient can be initialized."""
        # Test with GPT-4o
        if self.has_openai_key:
            client = LLMClient(model="gpt-4o", use_proxy=False)
            assert client.model == "gpt-4o"
            assert client.provider.value == "openai"
        
        # Test with Claude Sonnet 4.5
        if self.has_anthropic_key:
            client = LLMClient(model="claude-sonnet-4-20250514", use_proxy=False)
            assert "claude" in client.model.lower()
            assert client.provider.value == "anthropic"
        
        # Test with Gemini 2.5
        if self.has_google_key:
            client = LLMClient(model="gemini-2.5-flash-lite", use_proxy=False)
            assert "gemini" in client.model.lower()
            assert client.provider.value == "gemini"
    
    @pytest.mark.llm
    def test_gpt4o_authentication_and_message(self):
        """Test GPT-4o authentication and message sending."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.model == "gpt-4o"
        assert response.usage is not None
        assert "prompt_tokens" in response.usage
        assert "completion_tokens" in response.usage
    
    @pytest.mark.llm
    def test_claude_sonnet_authentication_and_message(self):
        """Test Claude Sonnet 4.5 authentication and message sending."""
        if not self.has_anthropic_key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        
        # Try Claude Sonnet 4.5 (adjust model name if needed)
        # Using claude-3.5-sonnet as fallback if 4.5 not available
        try:
            client = LLMClient(model="claude-sonnet-4-20250514", use_proxy=False)
        except:
            # Fallback to claude-3.5-sonnet
            client = LLMClient(model="claude-3-5-sonnet-20241022", use_proxy=False)
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.usage is not None
    
    @pytest.mark.llm
    def test_gemini_pro_authentication_and_message(self):
        """Test Gemini 2 authentication and message sending."""
        if not self.has_google_key:
            pytest.skip("GOOGLE_API_KEY not set")
        
        client = LLMClient(model="gemini-2.5-flash-lite", use_proxy=False)
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.usage is not None
    
    def test_message_with_system_prompt(self):
        """Test sending message with system prompt."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        response = client.send_message(
            message="What is 2+2?",
            system_message="You are a helpful math assistant. Always respond with just the number."
        )
        assert isinstance(response, LLMResponse)
        assert response.content is not None
    
    def test_conversation_history(self):
        """Test conversation with message history."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # First message
        response1 = client.send_message("My name is Alice")
        assert response1.content is not None
        
        # Continue conversation
        messages = [
            Message(role=MessageRole.USER, content="My name is Alice"),
            Message(role=MessageRole.ASSISTANT, content=response1.content)
        ]
        
        response2 = client.send_message(
            message="What's my name?",
            messages=messages
        )
        assert "Alice" in response2.content or "alice" in response2.content.lower()
    
    def test_streaming_response(self):
        """Test streaming response."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        chunks = []
        for chunk in client.stream_message("Count from 1 to 5, one number per line"):
            chunks.append(chunk)
        
        assert len(chunks) > 0
        full_response = "".join(chunks)
        assert len(full_response) > 0
    
    def test_cost_tracking(self):
        """Test cost tracking functionality."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create cost tracker with temp directory
        cost_tracker = CostTracker(log_dir=Path(self.temp_dir))
        
        # Make a call
        client = LLMClient(model="gpt-4o", use_proxy=False)
        response = client.send_message("Hello")
        
        # Check if cost is tracked
        if response.cost is not None:
            assert response.cost >= 0
        
        # Test cost logging
        if response.cost is not None:
            cost_tracker.log_cost(
                model="gpt-4o",
                cost=response.cost,
                tokens=response.usage
            )
            
            # Check daily total
            daily_total = cost_tracker.get_daily_total()
            assert daily_total >= 0
    
    @pytest.mark.llm
    def test_proxy_integration(self):
        """Test proxy integration (if proxy is available)."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # This test requires proxy to be running
        # Skip if proxy not available
        try:
            proxy_manager = ProxyManager.get_instance()
            # Try to start proxy
            proxy_manager.start(config_path=Path(os.path.join(os.getcwd(), 'moose', 'tests', 'config.yaml')))
            if not proxy_manager.is_running():
                # Note: This requires config.yaml in project directory
                # For testing, we'll skip if proxy can't start
                pytest.skip("Proxy not running and cannot start automatically")
            
            # Test with proxy
            client = LLMClient(model="gpt-4", use_proxy=True)
            response = client.send_message("Hello")
            assert isinstance(response, LLMResponse)
            assert response.content is not None
            
            # Verify cost tracking works with proxy
            if response.cost is not None:
                assert response.cost >= 0
        except Exception as e:
            pytest.fail(f"Proxy test skipped: {e}")
    
    def test_multiple_providers_same_api(self):
        """Test that different providers use the same API interface."""
        providers_tested = 0
        
        if self.has_openai_key:
            client1 = LLMClient(model="gpt-4o", use_proxy=False)
            response1 = client1.send_message("Hi")
            assert isinstance(response1, LLMResponse)
            providers_tested += 1
        
        if self.has_anthropic_key:
            client2 = LLMClient(model="claude-sonnet-4-20250514", use_proxy=False)
            response2 = client2.send_message("Hi")
            assert isinstance(response2, LLMResponse)
            providers_tested += 1
        
        if self.has_google_key:
            client3 = LLMClient(model="gemini-2.5-flash-lite", use_proxy=False)
            response3 = client3.send_message("Hi")
            assert isinstance(response3, LLMResponse)
            providers_tested += 1
        
        assert providers_tested > 0, "At least one provider should be tested"
    
    def test_error_handling(self):
        """Test error handling for invalid requests."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # Test with invalid model (should handle gracefully)
        try:
            invalid_client = LLMClient(model="invalid-model-xyz", use_proxy=False)
            # This might fail or succeed depending on implementation
        except Exception:
            pass  # Expected to fail
    
    def test_response_structure(self):
        """Test that LLMResponse has correct structure."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        response = client.send_message("Test")
        
        # Check response structure
        assert hasattr(response, 'content')
        assert hasattr(response, 'model')
        assert hasattr(response, 'finish_reason')
        assert hasattr(response, 'usage')
        assert hasattr(response, 'cost')
        assert hasattr(response, 'raw_response')
        
        # Check usage structure
        if response.usage:
            assert 'prompt_tokens' in response.usage
            assert 'completion_tokens' in response.usage
            assert 'total_tokens' in response.usage

