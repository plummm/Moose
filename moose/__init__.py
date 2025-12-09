"""Moose - A modular agent framework built on LangGraph."""

__version__ = "0.1.0"

# Import key components for easy access
try:
    from moose.framework import __version__ as framework_version, BaseAgent
except ImportError:
    # Fallback for development mode
    from framework import __version__ as framework_version, BaseAgent

__all__ = [
    "__version__",
    "framework_version",
    "BaseAgent",
]

