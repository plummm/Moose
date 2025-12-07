"""LLM provider definitions and utilities."""

from enum import Enum
from typing import Optional, Dict, Any


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"  # Alias for GEMINI
    GEMINI = "gemini" 
    COHERE = "cohere"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"  # AWS Bedrock
    
    @classmethod
    def from_string(cls, provider_str: str) -> "LLMProvider":
        """Convert string to LLMProvider enum."""
        provider_str = provider_str.lower().strip()
        # Handle aliases
        if provider_str in ["google", "gemini"]:
            return cls.GEMINI
        return cls(provider_str)


def get_provider(model_name: str) -> LLMProvider:
    """
    Infer provider from model name.
    
    Args:
        model_name: Name of the model (e.g., "gpt-4", "claude-3-opus", "gemini-pro")
    
    Returns:
        LLMProvider enum value
    """
    model_lower = model_name.lower()
    
    if model_lower.startswith("gpt-") or model_lower.startswith("o1-") or "openai" in model_lower:
        return LLMProvider.OPENAI
    elif model_lower.startswith("claude-") or "anthropic" in model_lower:
        return LLMProvider.ANTHROPIC
    elif model_lower.startswith("gemini-") or "google" in model_lower:
        return LLMProvider.GEMINI
    elif model_lower.startswith("cohere") or "command" in model_lower:
        return LLMProvider.COHERE
    elif model_lower.startswith("mistral-") or "mistral" in model_lower:
        return LLMProvider.MISTRAL
    elif model_lower.startswith("ollama") or model_lower.startswith("llama"):
        return LLMProvider.OLLAMA
    elif "bedrock" in model_lower or "aws" in model_lower:
        return LLMProvider.BEDROCK
    else:
        # Default to OpenAI for unknown models
        return LLMProvider.OPENAI


def get_provider_model_string(provider: LLMProvider, model_name: str) -> str:
    """
    Convert model name to provider-specific format for LiteLLM.
    
    Args:
        provider: The LLM provider
        model_name: The model name
    
    Returns:
        Provider-prefixed model string (e.g., "openai/gpt-4", "anthropic/claude-3-opus")
    """
    provider_map = {
        LLMProvider.OPENAI: "openai",
        LLMProvider.ANTHROPIC: "anthropic",
        LLMProvider.GOOGLE: "gemini",
        LLMProvider.GEMINI: "gemini",
        LLMProvider.COHERE: "cohere",
        LLMProvider.MISTRAL: "mistral",
        LLMProvider.OLLAMA: "ollama",
        LLMProvider.AZURE_OPENAI: "azure",
        LLMProvider.BEDROCK: "bedrock",
    }
    
    provider_prefix = provider_map.get(provider, "openai")
    return f"{provider_prefix}/{model_name}"

