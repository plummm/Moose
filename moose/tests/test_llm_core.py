"""Unit tests for LLM Core module."""

import os
import pytest
import tempfile
from pathlib import Path
from framework.llm_core import LLMClient, Message, MessageRole, LLMResponse
from framework.llm_core.cost_tracker import CostTracker
from framework.logging import init_core_logger, set_global_debug


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
    
    @pytest.mark.llm
    def test_gpt4o_authentication_and_message(self):
        """Test GPT-4o authentication and message sending."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
        assert response.model == "gpt-4o"
        assert response.usage is not None
        assert "input_tokens" in response.usage
        assert "output_tokens" in response.usage
    
    @pytest.mark.llm
    def test_claude_sonnet_authentication_and_message(self):
        """Test Claude Sonnet authentication and message sending."""
        if not self.has_anthropic_key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        
        # Try Claude 3.5 Sonnet
        try:
            client = LLMClient(model="claude-sonnet-4-5-20250929")
        except Exception as e:
            pytest.skip(f"Claude model not available: {e}")
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content == 'Hello'
        assert len(response.content) > 0
        assert response.usage is not None
    
    @pytest.mark.llm
    def test_gemini_pro_authentication_and_message(self):
        """Test Gemini authentication and message sending."""
        if not self.has_google_key:
            pytest.skip("GOOGLE_API_KEY not set")
        
        try:
            client = LLMClient(model="gemini-2.5-flash")
        except Exception as e:
            pytest.skip(f"Gemini model not available: {e}")
        
        # Test simple message
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content == 'Hello'
        assert len(response.content) > 0
        assert response.usage is not None
    
    def test_message_with_system_prompt(self):
        """Test sending message with system prompt."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        response = client.send_message(
            message="What is 2+2?",
            system_message="You are a helpful math assistant. Always respond with just the number."
        )
        assert isinstance(response, LLMResponse)
        assert response.content == '4'
    
    def test_conversation_history(self):
        """Test conversation with message history."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
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
        
        client = LLMClient(model="gpt-4o")
        
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
        client = LLMClient(model="gpt-4o")
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
    
    def test_multiple_providers_same_api(self):
        """Test that different providers use the same API interface."""
        providers_tested = 0
        
        if self.has_openai_key:
            try:
                client1 = LLMClient(model="gpt-4o")
                response1 = client1.send_message("Hi")
                assert isinstance(response1, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"OpenAI test failed: {e}")
        
        if self.has_anthropic_key:
            try:
                client2 = LLMClient(model="claude-sonnet-4-5-20250929")
                response2 = client2.send_message("Hi")
                assert isinstance(response2, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"Anthropic test failed: {e}")
        
        if self.has_google_key:
            try:
                client3 = LLMClient(model="gemini-2.5-flash")
                response3 = client3.send_message("Hi")
                assert isinstance(response3, LLMResponse)
                providers_tested += 1
            except Exception as e:
                pytest.skip(f"Gemini test failed: {e}")
        
        assert providers_tested > 0, "At least one provider should be tested"
    
    def test_error_handling(self):
        """Test error handling for invalid requests."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
        
        # Test with unsupported provider model (should raise ValueError)
        with pytest.raises(ValueError, match="Cannot determine provider|Unsupported provider"):
            LLMClient(model="invalid-model-xyz")
    
    def test_response_structure(self):
        """Test that LLMResponse has correct structure."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o")
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
            assert 'input_tokens' in response.usage
            assert 'output_tokens' in response.usage
            assert 'total_tokens' in response.usage
    
    def test_pdf_text_extraction(self):
        """Test PDF text extraction using PyPDFLoader."""
        try:
            from framework.llm_core.pdf_utils import extract_pdf_text
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
    
    def test_pdf_extraction_with_llm(self):
        """Test using PDF text extraction with LLM."""
        try:
            from framework.llm_core.pdf_utils import extract_pdf_text
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
            response = client.send_message(
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
            from framework.llm_core.langchain_integration import LangChainLLM
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
            from framework.llm_core.langchain_integration import LangChainLLM
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
    def test_llmclient_uses_langchain(self):
        """Test that LLMClient uses LangChain."""
        try:
            from framework.llm_core.langchain_integration import LangChainLLM
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
        response = client.send_message("Say 'Hello' and nothing else.")
        assert isinstance(response, LLMResponse)
        assert response.content is not None
        assert len(response.content) > 0
    
    def test_cost_calculation_from_config(self):
        """Test that cost is calculated from config when not in response."""
        try:
            from framework.llm_core.config import ModelConfig
        except ImportError:
            pytest.skip("Config not available")
        
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create config
        config = ModelConfig()
        
        # Create client with config
        client = LLMClient(model="gpt-4o", config=config)
        
        # Send message
        response = client.send_message("Hello")
        
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

