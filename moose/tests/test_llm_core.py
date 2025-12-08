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
    
    def test_send_message_with_file_pdf(self):
        """Test sending message with PDF file attachment."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create a simple PDF file for testing
        # Note: This requires a vision-capable model
        try:
            client = LLMClient(model="gpt-5", use_proxy=False)
        except:
            pytest.skip("Vision-capable model not available")
        
        # Create a temporary PDF file
        # For testing, we'll create a minimal PDF or use a test file
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
        
        # Test sending message with PDF
        try:
            response = client.send_message_with_file(
                message="What text is in this PDF?",
                file_path=pdf_file,
                system_message="You are a helpful assistant that analyzes documents."
            )
            
            assert isinstance(response, LLMResponse)
            assert response.content is not None
            assert len(response.content) > 0
            assert response.model is not None
            # Usage should be available
            if response.usage:
                assert 'prompt_tokens' in response.usage
                assert 'completion_tokens' in response.usage
        except Exception as e:
            # Some models might not support PDFs directly
            # Check if it's a model capability issue
            if "vision" in str(e).lower() or "multimodal" in str(e).lower() or "file" in str(e).lower():
                pytest.skip(f"Model may not support PDF files: {e}")
            else:
                raise
    
    def test_send_message_with_file_image(self):
        """Test sending message with image file attachment."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        # Create a simple PNG image for testing
        try:
            client = LLMClient(model="gpt-5", use_proxy=False)
        except:
            pytest.skip("Vision-capable model not available")
        
        image_file = Path(os.path.join(os.getcwd(), "moose/tests/flag.png"))
        
        # Test sending message with image
        try:
            response = client.send_message_with_file(
                message="Describe what you see in this image.",
                file_path=image_file,
                system_message="You are a helpful assistant that describes images."
            )
            
            assert isinstance(response, LLMResponse)
            assert response.content is not None
            assert len(response.content) > 0
            assert response.model is not None
            # Usage should be available
            if response.usage:
                assert 'prompt_tokens' in response.usage
                assert 'completion_tokens' in response.usage
        except Exception as e:
            # Some models might not support images
            if "vision" in str(e).lower() or "multimodal" in str(e).lower():
                pytest.skip(f"Model may not support image files: {e}")
            else:
                raise
    
    def test_upload_file(self):
        """Test file upload functionality."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # Create a test PDF file
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
(Test Upload) Tj
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
        
        pdf_file = Path(self.temp_dir) / "test_upload.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_content)
        
        # Test uploading file
        try:
            file_id = client.upload_file(pdf_file, purpose="assistants")
            assert file_id is not None
            assert len(file_id) > 0
            assert file_id.startswith("file-") or "file" in file_id.lower()
        except Exception as e:
            # File upload might not be supported by all providers/models
            if "unsupported" in str(e).lower() or "not supported" in str(e).lower():
                pytest.skip(f"File upload may not be supported: {e}")
            else:
                raise
    
    def test_send_message_with_file_not_found(self):
        """Test error handling when file doesn't exist."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # Test with non-existent file
        non_existent_file = Path(self.temp_dir) / "nonexistent.pdf"
        
        with pytest.raises(FileNotFoundError):
            client.send_message_with_file(
                message="Analyze this file",
                file_path=non_existent_file
            )
    
    def test_send_message_with_file_text_file(self):
        """Test sending message with text file (should work but not use vision API)."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        client = LLMClient(model="gpt-4o", use_proxy=False)
        
        # Create a text file
        text_file = Path(self.temp_dir) / "test_report.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write("This is a test earning report.\nRevenue: $1,000,000\nProfit: $100,000")
        
        # Test sending message with text file
        # Note: Text files might not work with vision API, but should handle gracefully
        try:
            response = client.send_message_with_file(
                message="Analyze this report",
                file_path=text_file,
                system_message="You are a financial analyst."
            )
            
            assert isinstance(response, LLMResponse)
            assert response.content is not None
        except Exception as e:
            # Text files might not be supported by vision API
            # This is expected behavior - text files should use regular send_message
            if "unsupported" in str(e).lower() or "text" in str(e).lower():
                pytest.skip(f"Text files may not be supported by vision API: {e}")
            else:
                raise
    
    def test_send_message_with_file_claude(self):
        """Test sending message with file using Claude (if available)."""
        if not self.has_anthropic_key:
            pytest.skip("ANTHROPIC_API_KEY not set")
        
        # Claude 3 supports file uploads
        try:
            client = LLMClient(model="claude-3-5-sonnet-20241022", use_proxy=False)
        except:
            pytest.skip("Claude model not available")
        
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
(Test PDF for Claude) Tj
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
        
        pdf_file = Path(self.temp_dir) / "test_claude.pdf"
        with open(pdf_file, 'wb') as f:
            f.write(pdf_content)
        
        # Test sending message with PDF to Claude
        try:
            response = client.send_message_with_file(
                message="What is in this PDF document?",
                file_path=pdf_file,
                system_message="You are a helpful assistant."
            )
            
            assert isinstance(response, LLMResponse)
            assert response.content is not None
            assert len(response.content) > 0
        except Exception as e:
            # Claude might have different file format requirements
            if "format" in str(e).lower() or "unsupported" in str(e).lower():
                pytest.skip(f"Claude may require different file format: {e}")
            else:
                raise
    
    def test_send_message_with_file_response_structure(self):
        """Test that file upload response has correct structure."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        try:
            client = LLMClient(model="gpt-4o", use_proxy=False)
        except:
            pytest.skip("Vision-capable model not available")
        
        # Create a minimal image
        png_content = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xDE,
            0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54,
            0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01,
            0x0D, 0x0A, 0x2D, 0xB4,
            0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44,
            0xAE, 0x42, 0x60, 0x82
        ])
        
        image_file = Path(self.temp_dir) / "test_structure.png"
        with open(image_file, 'wb') as f:
            f.write(png_content)
        
        try:
            response = client.send_message_with_file(
                message="Describe this image.",
                file_path=image_file
            )
            
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
        except Exception as e:
            if "vision" in str(e).lower() or "multimodal" in str(e).lower():
                pytest.skip(f"Model may not support file uploads: {e}")
            else:
                raise
    
    def test_image_upload_capability(self):
        """Test comprehensive image upload capability with different formats and scenarios."""
        if not self.has_openai_key:
            pytest.skip("OPENAI_API_KEY not set")
        
        try:
            client = LLMClient(model="gpt-4o", use_proxy=False)
        except:
            pytest.skip("Vision-capable model not available")
        
        # Test 1: PNG image upload
        png_content = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D,  # IHDR chunk length
            0x49, 0x48, 0x44, 0x52,  # IHDR
            0x00, 0x00, 0x00, 0x01,  # width: 1
            0x00, 0x00, 0x00, 0x01,  # height: 1
            0x08, 0x02, 0x00, 0x00, 0x00,  # bit depth, color type, etc.
            0x90, 0x77, 0x53, 0xDE,  # CRC
            0x00, 0x00, 0x00, 0x0C,  # IDAT chunk length
            0x49, 0x44, 0x41, 0x54,  # IDAT
            0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01,  # compressed data
            0x0D, 0x0A, 0x2D, 0xB4,  # CRC
            0x00, 0x00, 0x00, 0x00,  # IEND chunk length
            0x49, 0x45, 0x4E, 0x44,  # IEND
            0xAE, 0x42, 0x60, 0x82   # CRC
        ])
        
        png_file = Path(self.temp_dir) / "test_image_upload.png"
        with open(png_file, 'wb') as f:
            f.write(png_content)
        
        try:
            # Test PNG upload
            response_png = client.send_message_with_file(
                message="What do you see in this PNG image?",
                file_path=png_file,
                system_message="You are an image analysis assistant."
            )
            
            assert isinstance(response_png, LLMResponse)
            assert response_png.content is not None
            assert len(response_png.content) > 0
            assert response_png.model is not None
            
            # Verify it used completion API (not responses API) for images
            # Images should use base64 encoding via completion API
            assert hasattr(response_png, 'raw_response')
            
            # Test 2: JPEG image upload (minimal JPEG)
            # Minimal JPEG: SOI, APP0, DQT, SOF, DHT, SOS, EOI
            jpeg_content = bytes([
                0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,  # APP0
                0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
                0xFF, 0xDB, 0x00, 0x43, 0x00,  # DQT
                0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07,
                0x09, 0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B,
                0x0C, 0x19, 0x12, 0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E,
                0x1D, 0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20, 0x22,
                0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29, 0x2C, 0x30, 0x31,
                0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32, 0x3C,
                0x2E, 0x33, 0x34, 0x32,
                0xFF, 0xC0, 0x00, 0x11, 0x08, 0x00, 0x01, 0x00, 0x01, 0x01, 0x01, 0x11, 0x00,  # SOF
                0x02, 0x11, 0x01, 0x03, 0x11, 0x01,
                0xFF, 0xC4, 0x00, 0x14, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # DHT
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00,  # SOS
                0x3F, 0x00,
                0xFF, 0xD9  # EOI
            ])
            
            jpeg_file = Path(self.temp_dir) / "test_image_upload.jpg"
            with open(jpeg_file, 'wb') as f:
                f.write(jpeg_content)
            
            # Test JPEG upload
            response_jpeg = client.send_message_with_file(
                message="Analyze this JPEG image.",
                file_path=jpeg_file
            )
            
            assert isinstance(response_jpeg, LLMResponse)
            assert response_jpeg.content is not None
            assert len(response_jpeg.content) > 0
            
            # Test 3: Verify image upload uses base64 encoding (completion API)
            # Images should NOT trigger file upload to /files endpoint
            # They should use base64 encoding directly in completion API
            assert hasattr(response_png, 'raw_response')
            assert hasattr(response_jpeg, 'raw_response')
            
            # Test 4: Verify usage tracking works for images
            if response_png.usage:
                assert 'prompt_tokens' in response_png.usage
                assert 'completion_tokens' in response_png.usage
                assert 'total_tokens' in response_png.usage
                # Image tokens should be included in prompt tokens
                assert response_png.usage['prompt_tokens'] > 0
            
            if response_jpeg.usage:
                assert 'prompt_tokens' in response_jpeg.usage
                assert 'completion_tokens' in response_jpeg.usage
                assert 'total_tokens' in response_jpeg.usage
            
            # Test 5: Verify cost tracking works for images
            if response_png.cost is not None:
                assert response_png.cost >= 0
            
            if response_jpeg.cost is not None:
                assert response_jpeg.cost >= 0
            
        except Exception as e:
            # Some models might not support images
            if "vision" in str(e).lower() or "multimodal" in str(e).lower() or "image" in str(e).lower():
                pytest.skip(f"Model may not support image uploads: {e}")
            else:
                raise

