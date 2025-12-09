"""Moose Framework - A modular agent framework built on LangGraph."""

__version__ = "0.1.0"

try:
    from moose.framework.agent_core import *
    from moose.framework.llm_core import *
except ImportError:
    # Fallback for development mode
    from framework.agent_core import *
    from framework.llm_core import *

__all__ = [
    '__version__',
    'AgentLoader',
    'ContainerManager',
    'DockerfileGenerator',
    'AgentRegistry',
    'BaseAgent',
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
