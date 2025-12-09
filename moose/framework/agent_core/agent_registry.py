"""Registry for tracking running agent containers."""

from typing import Dict, Set, Optional
try:
    from moose.framework.logging import get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.logging import get_core_logger


class AgentRegistry:
    """Tracks running agent containers per project."""
    
    def __init__(self):
        """Initialize the agent registry."""
        self.logger = get_core_logger()
        # Map: project_id -> Set of container_ids
        self._project_containers: Dict[str, Set[str]] = {}
        # Map: container_id -> (agent_name, project_id)
        self._container_info: Dict[str, tuple] = {}
        # Map: (project_id, agent_name) -> container_id
        self._agent_containers: Dict[tuple, str] = {}
    
    def register_container(
        self,
        container_id: str,
        agent_name: str,
        project_id: str
    ):
        """
        Register a running container.
        
        Args:
            container_id: Docker container ID
            agent_name: Name of the agent
            project_id: Project identifier
        """
        if project_id not in self._project_containers:
            self._project_containers[project_id] = set()
        
        self._project_containers[project_id].add(container_id)
        self._container_info[container_id] = (agent_name, project_id)
        self._agent_containers[(project_id, agent_name)] = container_id
        
        self.logger.debug(
            f"Registered container {container_id[:12]} for agent '{agent_name}' "
            f"in project '{project_id}'"
        )
    
    def unregister_container(self, container_id: str):
        """
        Unregister a container.
        
        Args:
            container_id: Docker container ID
        """
        if container_id not in self._container_info:
            return
        
        agent_name, project_id = self._container_info[container_id]
        
        if project_id in self._project_containers:
            self._project_containers[project_id].discard(container_id)
            if not self._project_containers[project_id]:
                del self._project_containers[project_id]
        
        del self._container_info[container_id]
        self._agent_containers.pop((project_id, agent_name), None)
        
        self.logger.debug(
            f"Unregistered container {container_id[:12]} for agent '{agent_name}'"
        )
    
    def get_container_id(self, project_id: str, agent_name: str) -> Optional[str]:
        """
        Get container ID for an agent in a project.
        
        Args:
            project_id: Project identifier
            agent_name: Name of the agent
        
        Returns:
            Container ID if found, None otherwise
        """
        return self._agent_containers.get((project_id, agent_name))
    
    def get_project_containers(self, project_id: str) -> Set[str]:
        """
        Get all container IDs for a project.
        
        Args:
            project_id: Project identifier
        
        Returns:
            Set of container IDs
        """
        return self._project_containers.get(project_id, set())
    
    def get_container_info(self, container_id: str) -> Optional[tuple]:
        """
        Get agent name and project ID for a container.
        
        Args:
            container_id: Docker container ID
        
        Returns:
            Tuple of (agent_name, project_id) if found, None otherwise
        """
        return self._container_info.get(container_id)
    
    def clear_project(self, project_id: str):
        """
        Clear all containers for a project.
        
        Args:
            project_id: Project identifier
        """
        containers = self.get_project_containers(project_id)
        for container_id in list(containers):
            self.unregister_container(container_id)
        
        self.logger.debug(f"Cleared all containers for project '{project_id}'")
    
    def list_projects(self) -> Set[str]:
        """
        List all projects with running containers.
        
        Returns:
            Set of project IDs
        """
        return set(self._project_containers.keys())

