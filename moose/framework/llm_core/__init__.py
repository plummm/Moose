"""Universal LLM interaction layer for Moose Framework."""

from framework.llm_core.client import LLMClient
from framework.llm_core.models import Message, MessageRole, LLMResponse
from framework.llm_core.providers import LLMProvider, get_provider
from framework.llm_core.cost_tracker import CostTracker
from framework.llm_core.config import ModelConfig

# LangChain integration
try:
    from framework.llm_core.langchain_integration import LangChainLLM
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LangChainLLM = None
    LANGCHAIN_AVAILABLE = False

# PDF utilities
try:
    from framework.llm_core.pdf_utils import extract_pdf_text
    PDF_UTILS_AVAILABLE = True
except ImportError:
    extract_pdf_text = None
    PDF_UTILS_AVAILABLE = False

__all__ = [
    'LLMClient',
    'Message',
    'MessageRole',
    'LLMResponse',
    'LLMProvider',
    'get_provider',
    'ModelConfig',
    'CostTracker',
    'LangChainLLM',
    'extract_pdf_text',
    'LANGCHAIN_AVAILABLE',
    'PDF_UTILS_AVAILABLE',
]
