"""Agent core module for Docker container management."""

try:
    from moose.framework.agent_core.agent_loader import AgentLoader
    from moose.framework.agent_core.container_manager import ContainerManager
    from moose.framework.agent_core.dockerfile_generator import DockerfileGenerator
    from moose.framework.agent_core.agent_registry import AgentRegistry
    from moose.framework.agent_core.base_agent import BaseAgent
except ImportError:
    # Fallback for development mode
    from framework.agent_core.agent_loader import AgentLoader
    from framework.agent_core.container_manager import ContainerManager
    from framework.agent_core.dockerfile_generator import DockerfileGenerator
    from framework.agent_core.agent_registry import AgentRegistry
    from framework.agent_core.base_agent import BaseAgent

__all__ = [
    'AgentLoader',
    'ContainerManager',
    'DockerfileGenerator',
    'AgentRegistry',
    'BaseAgent',
]

