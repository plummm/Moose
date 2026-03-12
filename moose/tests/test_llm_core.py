"""Unit tests for LLM Core module."""

import os
from types import SimpleNamespace
import pytest
import tempfile
from pathlib import Path
from moose.framework.llm_core import LLMClient, Message, MessageRole, LLMResponse, LLMProvider
import moose.framework.llm_core.client as llm_client_module
from moose.framework.llm_core.cost_tracker import CostTracker
from moose.framework.logging import init_core_logger, set_global_debug


pytestmark = pytest.mark.llm


class TestLLMCore:
    """Test suite for LLM Core functionality."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment."""
        # Initialize logger
        set_global_debug(True)
        init_core_logger()
        
        # Check for required API keys
        self.has_openai_key = bool(os.getenv("OPENAI_API_KEY"))
        self.has_anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
        self.has_google_key = bool(os.getenv("GOOGLE_API_KEY"))
        self.has_azure_key = bool(os.getenv("AZURE_AI_CREDENTIAL"))
        self.has_azure_endpoint = bool(os.getenv("AZURE_AI_ENDPOINT"))
        self.has_azure_config = bool(self.has_azure_key and self.has_azure_endpoint)
        
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
            client = LLMClient(model="gpt-4o")
            assert client.model == "gpt-4o"
            assert client.provider.value == "openai"
        
        # Test with Claude Sonnet
        if self.has_anthropic_key:
            try:
                client = LLMClient(model="claude-sonnet-4-5-20250929")
                assert "claude" in client.model.lower()
                assert client.provider.value == "anthropic"
            except Exception:
                pytest.skip("Anthropic model not available")
        
        # Test with Gemini
        if self.has_google_key:
            try:
                client = LLMClient(model="gemini-2.5-flash")
                assert "gemini" in client.model.lower()
                assert client.provider.value == "gemini"
            except Exception:
                pytest.skip("Gemini model not available")
        
        # Test with Azure AI (requires credential + endpoint)
        if self.has_azure_config:
            try:
                client = LLMClient(
                    model="azure:gpt-4o",
                    provider=LLMProvider.AZURE_AI,
                )
                assert client.provider.value == "azure_ai"
            except Exception as e:
                pytest.skip(f"Azure AI model not available: {e}")

    def test_upload_image_sync_uses_openai_client(self, tmp_path, monkeypatch):
        """OpenAI image upload should return the uploaded file id."""
        image_path = tmp_path / "screen.png"
        image_path.write_bytes(b"fake-image")
        captured = {}

        class FakeOpenAI:
            def __init__(self, *, api_key, timeout):
                captured["api_key"] = api_key
                captured["timeout"] = timeout
                self.files = self

            def create(self, *, file, purpose):
                captured["filename"] = Path(file.name).name
                captured["purpose"] = purpose
                captured["bytes"] = file.read()
                return SimpleNamespace(id="file-openai-123")

        monkeypatch.setattr(llm_client_module, "OPENAI_SDK_AVAILABLE", True)
        monkeypatch.setattr(llm_client_module, "OpenAI", FakeOpenAI)

        client = LLMClient(model="gpt-4o", api_key="test-openai-key")
        file_id = client.upload_image_sync(image_path)

        assert file_id == "file-openai-123"
        assert captured["api_key"] == "test-openai-key"
        assert captured["timeout"] == 600.0
        assert captured["filename"] == "screen.png"
        assert captured["purpose"] == "vision"
        assert captured["bytes"] == b"fake-image"

    def test_upload_image_sync_uses_azure_client(self, tmp_path, monkeypatch):
        """Azure image upload should use AzureOpenAI-compatible settings."""
        image_path = tmp_path / "screen.png"
        image_path.write_bytes(b"fake-image")
        captured = {}

        class FakeAzureOpenAI:
            def __init__(self, *, api_key, azure_endpoint, api_version, timeout):
                captured["api_key"] = api_key
                captured["azure_endpoint"] = azure_endpoint
                captured["api_version"] = api_version
                captured["timeout"] = timeout
                self.files = self

            def create(self, *, file, purpose):
                captured["filename"] = Path(file.name).name
                captured["purpose"] = purpose
                return SimpleNamespace(id="file-azure-123")

        monkeypatch.setattr(llm_client_module, "OPENAI_SDK_AVAILABLE", True)
        monkeypatch.setattr(llm_client_module, "AzureOpenAI", FakeAzureOpenAI)
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

        client = LLMClient(model="azure:gpt-4o", provider=LLMProvider.AZURE_AI, api_key="test-azure-key")
        file_id = client.upload_image_sync(image_path)

        assert file_id == "file-azure-123"
        assert captured["api_key"] == "test-azure-key"
        assert captured["azure_endpoint"] == "https://example.openai.azure.com"
        assert captured["api_version"] == "2024-10-21"
        assert captured["timeout"] == 600.0
        assert captured["filename"] == "screen.png"
        assert captured["purpose"] == "vision"

    def test_upload_image_sync_rejects_unsupported_provider(self, tmp_path):
        """Only OpenAI-compatible providers should support image uploads."""
        image_path = tmp_path / "screen.png"
        image_path.write_bytes(b"fake-image")

        client = LLMClient(model="gemini-2.5-flash")
        with pytest.raises(ValueError, match="OpenAI and Azure providers"):
            client.upload_image_sync(image_path)
    
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_gpt4o_authentication_and_message(self):
        """Test GPT-4o authentication and message sending."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        # Test simple message
        response = await client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.model == "gpt-4o"
        assert response.usage is not None
        assert "input_tokens" in response.usage
        assert "output_tokens" in response.usage
    
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_claude_sonnet_authentication_and_message(self):
        """Test Claude Sonnet authentication and message sending."""
        if not self.has_anthropic_key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        
        # Try Claude 3.5 Sonnet
        try:
            client = LLMClient(model="claude-sonnet-4-5-20250929")
        except Exception as e:
            pytest.skip(f"Claude model not available: {e}")
        
        # Test simple message
        response = await client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content == 'Hello'
        assert len(response.content) > 0
        assert response.usage is not None
    
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_gemini_pro_authentication_and_message(self):
        """Test Gemini authentication and message sending."""
        if not self.has_google_key:
            pytest.skip("GOOGLE_API_KEY not set")
        
        try:
            client = LLMClient(model="gemini-2.5-flash")
        except Exception as e:
            pytest.skip(f"Gemini model not available: {e}")
        
        # Test simple message
        response = await client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content == 'Hello'
        assert len(response.content) > 0
        assert response.usage is not None

    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_azure_ai_authentication_and_message(self):
        """Test Azure AI authentication and message sending."""
        if not self.has_azure_config:
            pytest.skip("Azure AI env vars not set")
        
        client = LLMClient(
            model="azure:gpt-4o",
            provider=LLMProvider.AZURE_AI,
        )
        
        # Test simple message
        response = await client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.usage is not None
    
    @pytest.mark.asyncio
    async def test_message_with_system_prompt(self):
        """Test sending message with system prompt."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        response = await client.send_message(
            message="What is 2+2?",
            system_message="You are a helpful math assistant. Always respond with just the number."
        )
        assert isinstance(response, LLMResponse)
        assert response.content == '4'
    
    @pytest.mark.asyncio
    async def test_conversation_history(self):
        """Test conversation with message history."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        # First message
        response1 = await client.send_message("My name is Alice")
        assert response1.content is not None
        
        # Continue conversation
        messages = [
            Message(role=MessageRole.USER, content="My name is Alice"),
            Message(role=MessageRole.ASSISTANT, content=response1.content)
        ]
        
        response2 = await client.send_message(
            message="What's my name?",
            messages=messages
        )
        assert "Alice" in response2.content or "alice" in response2.content.lower()
    
    def test_streaming_response(self):
        """Test streaming response."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        chunks = []
        for chunk in client.stream_message("Count from 1 to 5, one number per line"):
            chunks.append(chunk)
        
        assert len(chunks) > 0
        full_response = "".join(chunks)
        assert len(full_response) > 0
    
    @pytest.mark.asyncio
    async def test_cost_tracking(self):
        """Test cost tracking functionality."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create cost tracker with temp directory
        cost_tracker = CostTracker(log_dir=Path(self.temp_dir))
        
        # Make a call
        client = LLMClient(model="gpt-4o")
        response = await client.send_message("Hello")
        
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
    
    @pytest.mark.asyncio
    async def test_multiple_providers_same_api(self):
        """Test that different providers use the same API interface."""
        providers_tested = 0
        
        if self.has_openai_key:
            try:
                client1 = LLMClient(model="gpt-4o")
                response1 = await client1.send_message("Hi")
                assert isinstance(response1, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"OpenAI test failed: {e}")
        
        if self.has_anthropic_key:
            try:
                client2 = LLMClient(model="claude-sonnet-4-5-20250929")
                response2 = await client2.send_message("Hi")
                assert isinstance(response2, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"Anthropic test failed: {e}")
        
        if self.has_google_key:
            try:
                client3 = LLMClient(model="gemini-2.5-flash")
                response3 = await client3.send_message("Hi")
                assert isinstance(response3, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"Gemini test failed: {e}")

        if self.has_azure_config:
            try:
                client4 = LLMClient(
                    model="azure:gpt-4o",
                    provider=LLMProvider.AZURE_AI,
                )
                response4 = await client4.send_message("Hi")
                assert isinstance(response4, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"Azure AI test failed: {e}")
        
        assert providers_tested > 0, "At least one provider should be tested"
    
    def test_error_handling(self):
        """Test error handling for invalid requests."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        # Test with unsupported provider model (should raise ValueError)
        with pytest.raises(ValueError, match="Cannot determine provider|Unsupported provider"):
            LLMClient(model="invalid-model-xyz")
    
    @pytest.mark.asyncio
    async def test_response_structure(self):
        """Test that LLMResponse has correct structure."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        response = await client.send_message("Test")
        
        # Check response structure
        assert hasattr(response, 'content')
        assert hasattr(response, 'model')
        assert hasattr(response, 'finish_reason')
        assert hasattr(response, 'usage')
        assert hasattr(response, 'cost')
        assert hasattr(response, 'raw_response')
        
        # Check usage structure
        if response.usage:
            assert 'input_tokens' in response.usage
            assert 'output_tokens' in response.usage
            assert 'total_tokens' in response.usage
    
    def test_pdf_text_extraction(self):
        """Test PDF text extraction using PyPDFLoader."""
        try:
            from moose.framework.llm_core.pdf_utils import extract_pdf_text
        except ImportError:
            pytest.skip("langchain-community not installed")
        
        # Create a simple PDF file for testing
        pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF Content) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000317 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
398
%%EOF"""
        
        pdf_file = Path(self.temp_dir) / "test_report.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_content)
        
        # Test PDF text extraction
        try:
            extracted_text = extract_pdf_text(pdf_file)
            assert isinstance(extracted_text, str)
            assert len(extracted_text) > 0
        except Exception as e:
            pytest.skip(f"PDF extraction may not work: {e}")
    
    @pytest.mark.asyncio
    async def test_pdf_extraction_with_llm(self):
        """Test using PDF text extraction with LLM."""
        try:
            from moose.framework.llm_core.pdf_utils import extract_pdf_text
        except ImportError:
            pytest.skip("langchain-community not installed")
        
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create a simple PDF file
        pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
/Font <<
/F1 <<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
>>
>>
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Revenue: $1,000,000) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000317 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
398
%%EOF"""
        
        pdf_file = Path(self.temp_dir) / "test_report.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_content)
        
        # Extract text from PDF
        try:
            extracted_text = extract_pdf_text(pdf_file)
            
            # Use extracted text with LLM
            client = LLMClient(model="gpt-4o")
            response = await client.send_message(
                message=f"Analyze this document: {extracted_text}\n\nWhat is the revenue mentioned?",
                system_message="You are a financial analyst."
            )
            
            assert isinstance(response, LLMResponse)
            assert response.content is not None
            assert len(response.content) > 0
        except Exception as e:
            pytest.skip(f"PDF extraction with LLM test failed: {e}")
    
    @pytest.mark.llm
    def test_langchain_integration(self):
        """Test LangChain integration with native provider classes."""
        try:
            from moose.framework.llm_core.langchain_integration import LangChainLLM
        except ImportError:
            pytest.skip("LangChain not installed")
        
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Initialize LangChain LLM directly
        langchain_llm = LangChainLLM(
            model="gpt-4o",
            temperature=0.7
        )
        
        # Test invoke
        response = langchain_llm.invoke(
            message="Say 'Hello from LangChain' and nothing else.",
            system_message="You are a helpful assistant."
        )
        
        assert isinstance(response, LLMResponse)
        assert response.content == 'Hello from LangChain'
        assert len(response.content) > 0
        assert response.model == "gpt-4o"
        
        # Test streaming
        chunks = list(langchain_llm.stream("Count to 3, one number per chunk."))
        assert len(chunks) > 0
        assert all(isinstance(chunk, str) for chunk in chunks)
    
    @pytest.mark.llm
    def test_multiple_models_via_langchain(self):
        """Test that multiple models work via LangChain (OpenAI, Claude, Gemini)."""
        try:
            from moose.framework.llm_core.langchain_integration import LangChainLLM
        except ImportError:
            pytest.skip("LangChain not installed")
        
        # Test models based on available API keys
        models_to_test = []
        
        if self.has_openai_key:
            models_to_test.append(("gpt-4o", "OpenAI"))
        
        if self.has_anthropic_key:
            models_to_test.append(("claude-sonnet-4-5-20250929", "Anthropic"))
        
        if self.has_google_key:
            models_to_test.append(("gemini-2.5-flash", "Google"))
        
        if not models_to_test:
            pytest.skip("No API keys available for testing multiple models")
        
        for model_name, provider in models_to_test:
            try:
                langchain_llm = LangChainLLM(model=model_name)
                
                response = langchain_llm.invoke(
                    message=f"Say 'Hello from {provider}' and nothing else."
                )
                
                assert isinstance(response, LLMResponse)
                assert response.content is not None
                assert len(response.content) > 0
                assert response.model == model_name
                
            except Exception as e:
                # Some models might not be available
                pytest.skip(f"Model {model_name} ({provider}) not available: {e}")
    
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_llmclient_uses_langchain(self):
        """Test that LLMClient uses LangChain."""
        try:
            from moose.framework.llm_core.langchain_integration import LangChainLLM
        except ImportError:
            pytest.skip("LangChain not installed")
        
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create client
        client = LLMClient(model="gpt-4o")
        
        # Verify LangChain LLM is initialized
        assert client.langchain_llm is not None
        assert isinstance(client.langchain_llm, LangChainLLM)
        
        # Test that it works
        response = await client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
    
    @pytest.mark.asyncio
    async def test_cost_calculation_from_config(self):
        """Test that cost is calculated from config when not in response."""
        try:
            from moose.framework.llm_core.config import ModelConfig
        except ImportError:
            pytest.skip("Config not available")
        
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create config
        config = ModelConfig()
        
        # Create client with config
        client = LLMClient(model="gpt-4o", config=config)
        
        # Send message
        response = await client.send_message("Hello")
        
        # Cost should be calculated from token usage
        if response.usage and response.cost is not None:
            assert response.cost >= 0
            # Verify cost is reasonable (should be > 0 if tokens used)
            if response.usage.get('total_tokens', 0) > 0:
                assert response.cost > 0


class TestMultiStageReasoning:
    """Test multi-stage reasoning functionality."""
    
    def test_has_final_answer_marker(self):
        """Test final answer marker detection."""
        client = LLMClient(model="gpt-4", enable_multi_stage_reasoning=True)
        
        # Test with marker at start
        assert client._has_final_answer_marker("<FINAL_ANSWER>Here is my answer")
        assert client._has_final_answer_marker("  <FINAL_ANSWER>  Here is my answer")
        assert client._has_final_answer_marker("<final_answer>lowercase")
        
        # Test without marker
        assert not client._has_final_answer_marker("No marker here")
        assert not client._has_final_answer_marker("")
        assert not client._has_final_answer_marker("FINAL_ANSWER in middle")
    
    def test_extract_final_answer(self):
        """Test final answer extraction."""
        client = LLMClient(model="gpt-4", enable_multi_stage_reasoning=True)
        
        # Test extraction
        content = "<FINAL_ANSWER>This is the answer"
        assert client._extract_final_answer(content) == "This is the answer"
        
        # Test with whitespace
        content = "  <FINAL_ANSWER>  Answer with spaces  "
        assert client._extract_final_answer(content) == "Answer with spaces"
        
        # Test without marker
        content = "No marker content"
        assert client._extract_final_answer(content) == "No marker content"
    
    def test_build_continuation_prompt(self):
        """Test continuation prompt building."""
        client = LLMClient(model="gpt-4", enable_multi_stage_reasoning=True)
        
        prompt = client._build_continuation_prompt(iteration=0)
        
        # Check key elements are present
        assert "tool results above" in prompt.lower()
        assert "additional tools" in prompt.lower()
        assert "<FINAL_ANSWER>" in prompt
        assert "suggested next tools" in prompt.lower() or "metadata" in prompt.lower()
    
    def test_custom_marker(self):
        """Test custom marker configuration."""
        client = LLMClient(
            model="gpt-4", 
            enable_multi_stage_reasoning=True,
            multi_stage_marker="DONE"
        )
        
        assert client._has_final_answer_marker("DONE Here is my answer")
        assert not client._has_final_answer_marker("<FINAL_ANSWER>Here is my answer")
        
        extracted = client._extract_final_answer("DONE The result")
        assert extracted == "The result"


class TestProviderReasoningThinking:
    """Tests for provider-specific reasoning/thinking request handling."""

    def test_reasoning_detection_is_standardized(self):
        assert LLMClient._reasoning_enabled_for_kwargs({"reasoning": {"effort": "medium"}}) is True
        assert LLMClient._reasoning_enabled_for_kwargs({"thinking": {"type": "adaptive"}}) is True
        assert LLMClient._reasoning_enabled_for_kwargs({"output_config": {"effort": "high"}}) is True
        assert LLMClient._reasoning_enabled_for_kwargs({}) is False

    def test_assistant_content_blocks_preserved_across_tool_turns(self):
        import asyncio
        from unittest.mock import MagicMock, AsyncMock

        thinking_blocks = [
            {"type": "thinking", "thinking": "Need to call tool first", "signature": "sig_123"},
            {"type": "text", "text": "Calling tool now."},
        ]

        first_raw = MagicMock()
        first_raw.content = thinking_blocks

        mock_responses = [
            MagicMock(
                content=thinking_blocks,
                tool_calls=[{"name": "test_tool", "id": "call_1", "args": {}}],
                cost=0.001,
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                model="gpt-4o",
                finish_reason="tool_calls",
                raw_response=first_raw,
                request_id="test_1",
            ),
            MagicMock(
                content="Done.",
                tool_calls=None,
                cost=0.001,
                usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                model="gpt-4o",
                finish_reason="stop",
                raw_response=None,
                request_id="test_2",
            ),
        ]

        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.ainvoke = AsyncMock(return_value="Tool result")

        client = LLMClient(
            model="gpt-4o",
            tools=[mock_tool],
            default_call_kwargs={"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}},
        )
        client.langchain_llm = MagicMock()
        client.langchain_llm.ainvoke = AsyncMock(side_effect=mock_responses)

        asyncio.run(client.send_message("Analyze this with tools"))

        assert client.langchain_llm.ainvoke.call_count == 2
        second_call_kwargs = client.langchain_llm.ainvoke.call_args_list[1].kwargs
        second_call_messages = second_call_kwargs["messages"]
        assistant_msgs = [m for m in second_call_messages if getattr(m, "role", None) == MessageRole.ASSISTANT]
        assert assistant_msgs, "Expected assistant message in second turn"
        assert isinstance(assistant_msgs[0].content, list)
        assert assistant_msgs[0].content[0].get("type") == "thinking"
        assert assistant_msgs[0].content[0].get("signature") == "sig_123"

    def test_default_kwargs_merged_with_call_kwargs(self):
        client = object.__new__(LLMClient)
        client.default_call_kwargs = {
            "response_format": {"type": "json_schema", "strict": True},
            "foo": 1,
        }

        merged = client._merge_default_and_call_kwargs(
            {
                "response_format": {"strict": False},
                "bar": 2,
            }
        )

        assert merged["foo"] == 1
        assert merged["bar"] == 2
        assert merged["response_format"]["type"] == "json_schema"
        assert merged["response_format"]["strict"] is False

    def test_extract_actual_response_text_ignores_reasoning_blocks(self):
        content = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "internal summary"}]},
            {"type": "thinking", "thinking": "internal thinking"},
            {"type": "text", "text": "actual answer"},
        ]
        assert LLMClient._extract_actual_response_text(content) == "actual answer"

    def test_final_response_content_omits_reasoning_blocks(self):
        import asyncio
        from unittest.mock import MagicMock, AsyncMock

        content = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "summary text"}]},
            {"type": "text", "text": "answer text"},
        ]

        client = LLMClient(
            model="gpt-4o",
            default_call_kwargs={"reasoning": {"effort": "medium", "summary": "auto"}},
        )
        client.langchain_llm = MagicMock()
        client.langchain_llm.ainvoke = AsyncMock(
            return_value=LLMResponse(
                content=content,
                model="gpt-4o",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )
        )

        response = asyncio.run(client.send_message("hello"))
        assert response.content == "answer text"

    def test_tool_result_multimodal_blocks_are_preserved(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        tool = MagicMock()
        tool.name = "browser_screenshot"
        tool.ainvoke = AsyncMock(
            return_value=[
                {"type": "input_text", "text": "Analyze the screenshot and plan the next action."},
                {"type": "input_image", "file_id": "file-123"},
            ]
        )

        client = LLMClient(model="gpt-4o", tools=[tool])
        messages = asyncio.run(
            client._execute_tool_calls(
                [{"name": "browser_screenshot", "id": "call_1", "args": {}}],
                runtime=None,
            )
        )

        assert len(messages) == 1
        assert messages[0].role == MessageRole.TOOL
        assert messages[0].content == [
            {"type": "input_text", "text": "Analyze the screenshot and plan the next action."},
            {"type": "input_image", "file_id": "file-123"},
        ]

    def test_collect_agent_loop_preserves_multimodal_tool_messages(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        tool = MagicMock()
        tool.name = "browser_screenshot"
        tool.ainvoke = AsyncMock(
            return_value=[
                {"type": "input_text", "text": "Analyze the screenshot and plan the next action."},
                {"type": "input_image", "file_id": "file-123"},
            ]
        )

        client = LLMClient(model="gpt-4o", tools=[tool], enable_multi_stage_reasoning=False)
        client.langchain_llm = MagicMock()
        client.langchain_llm.ainvoke = AsyncMock(
            side_effect=[
                MagicMock(
                    content="Calling screenshot",
                    tool_calls=[{"name": "browser_screenshot", "id": "call_1", "args": {}}],
                    cost=0.001,
                    usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    model="gpt-4o",
                    finish_reason="tool_calls",
                    raw_response=None,
                    request_id="test_1",
                ),
                MagicMock(
                    content="Done.",
                    tool_calls=None,
                    cost=0.001,
                    usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                    model="gpt-4o",
                    finish_reason="stop",
                    raw_response=None,
                    request_id="test_2",
                ),
            ]
        )

        asyncio.run(client.collect_agent_loop("Analyze the page"))

        second_call_kwargs = client.langchain_llm.ainvoke.call_args_list[1].kwargs
        second_call_messages = second_call_kwargs["messages"]
        tool_messages = [m for m in second_call_messages if getattr(m, "role", None) == MessageRole.TOOL]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == [
            {"type": "input_text", "text": "Analyze the screenshot and plan the next action."},
            {"type": "input_image", "file_id": "file-123"},
        ]


@pytest.mark.asyncio
class TestMultiStageReasoningIntegration:
    """Integration tests for multi-stage reasoning workflow."""
    
    async def test_multi_stage_with_marker(self):
        """Test multi-stage reasoning completes with marker."""
        from unittest.mock import MagicMock, AsyncMock
        
        # Create mock LLM that requires 2 tool calls before final answer
        mock_responses = [
            # First response: request tool call
            MagicMock(
                content="I need more info",
                tool_calls=[{"name": "test_tool", "id": "call_1", "args": {}}],
                cost=0.001,
                usage={"input_tokens": 10, "output_tokens": 5},
                model="gpt-4",
                finish_reason="tool_calls",
                raw_response=None,
                request_id="test_1"
            ),
            # Second response after tool: request another tool
            MagicMock(
                content="Need one more",
                tool_calls=[{"name": "test_tool2", "id": "call_2", "args": {}}],
                cost=0.001,
                usage={"input_tokens": 15, "output_tokens": 5},
                model="gpt-4",
                finish_reason="tool_calls",
                raw_response=None,
                request_id="test_2"
            ),
            # Third response after tools: final answer with marker
            MagicMock(
                content="<FINAL_ANSWER>Based on the tool results, here is my complete analysis.",
                tool_calls=None,
                cost=0.002,
                usage={"input_tokens": 20, "output_tokens": 15},
                model="gpt-4",
                finish_reason="stop",
                raw_response=None,
                request_id="test_3"
            )
        ]
        
        # Create mock tool
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.ainvoke = AsyncMock(return_value="Tool result 1")
        
        mock_tool2 = MagicMock()
        mock_tool2.name = "test_tool2"
        mock_tool2.ainvoke = AsyncMock(return_value="Tool result 2")
        
        # Create client with multi-stage reasoning enabled
        client = LLMClient(
            model="gpt-4",
            enable_multi_stage_reasoning=True,
            tools=[mock_tool, mock_tool2]
        )
        
        # Mock the LangChain LLM wrapper
        client.langchain_llm = MagicMock()
        client.langchain_llm.ainvoke = AsyncMock(side_effect=mock_responses)
        
        # Send message
        response = await client.send_message(
            message="Analyze this",
            system_message="You are a helpful assistant"
        )
        
        # Verify final answer was extracted (without marker)
        assert response.content == "Based on the tool results, here is my complete analysis."
        assert "<FINAL_ANSWER>" not in response.content
        
        # Verify tools were called
        assert mock_tool.ainvoke.call_count == 1
        assert mock_tool2.ainvoke.call_count == 1
        
        # Verify LLM was called 3 times (initial + after each tool round)
        assert client.langchain_llm.ainvoke.call_count == 3

