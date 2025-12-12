"""Web UI for Moose Framework.

Provides a Flask-based web interface for monitoring agents, logs, and LLM conversations.

Components:
- CoreWebServer: Singleton Flask server handling multiple projects
- LogManager: Log buffering and SSE streaming  
- ChatManager: Chat history and real-time streaming
"""

from .log_manager import LogManager, get_log_manager
from .chat_manager import ChatManager, get_chat_manager
from .core_server import CoreWebServer, get_or_start_core_server, register_project

__all__ = [
    'LogManager',
    'get_log_manager',
    'ChatManager', 
    'get_chat_manager',
    'CoreWebServer',
    'get_or_start_core_server',
    'register_project',
]
