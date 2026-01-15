"""LLM provider definitions and utilities."""

from enum import Enum


class LLMProvider(str, Enum):
    """Supported LLM providers (OpenAI, Anthropic, Gemini, Azure AI)."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    AZURE_AI = "azure_ai"
    
    @classmethod
    def from_string(cls, provider_str: str) -> "LLMProvider":
        """Convert string to LLMProvider enum."""
        provider_str = provider_str.lower().strip()
        # Handle aliases
        if provider_str in ["google", "gemini"]:
            return cls.GEMINI
        if provider_str in ["azure_openai", "azure-openai", "azureai", "azure-ai", "azure_ai"]:
            return cls.AZURE_AI
        try:
            return cls(provider_str)
        except ValueError:
            raise ValueError(
                f"Unsupported provider: {provider_str}. Supported: openai, anthropic, gemini, azure_ai"
            )


def get_provider(model_name: str) -> LLMProvider:
    """
    Infer provider from model name.
    
    Supports OpenAI, Anthropic, Gemini, and Azure AI models.
    
    Args:
        model_name: Name of the model (e.g., "gpt-4", "claude-3-opus", "gemini-pro")
    
    Returns:
        LLMProvider enum value
    
    Raises:
        ValueError: If provider cannot be determined or is not supported
    """
    model_lower = model_name.lower()
    
    if model_lower.startswith("azure:"):
        return LLMProvider.AZURE_AI
    if model_lower.startswith("gpt-") or model_lower.startswith("o1-") or "openai" in model_lower:
        return LLMProvider.OPENAI
    elif model_lower.startswith("claude-") or "anthropic" in model_lower:
        return LLMProvider.ANTHROPIC
    elif model_lower.startswith("gemini-") or "google" in model_lower:
        return LLMProvider.GEMINI
    else:
        raise ValueError(
            f"Cannot determine provider for model '{model_name}'. "
            f"Supported providers: OpenAI (gpt-*, o1-*), Anthropic (claude-*), "
            f"Gemini (gemini-*), Azure AI (azure:*)"
        )

