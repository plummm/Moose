"""Registry for tracking running agent containers."""

from pathlib import Path
from typing import Dict, Set, Optional
from moose.framework.logging import get_core_logger


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

    def generate_entry_py(self, agent_path: Path, entry_point: str, entry_class: str = None) -> None:
        """Generate entry.py file for the agent."""
        if entry_point.endswith(".py"):
            entry_point = entry_point[:-3]
        
        code = f"""import os
import json
import sys
import os
import {entry_point}

if __name__ == "__main__":
    debug = os.getenv("MOOSE_AGENT_DEBUG", "false").lower() in ("true", "1", "yes", "on")
    projects_base_dir = os.getenv("MOOSE_PROJECTS_DIR")
    projects_parent_dir = os.path.dirname(projects_base_dir)
    if not os.path.exists(projects_parent_dir):
        os.makedirs(projects_parent_dir)
    
    os.symlink("/app/projects", projects_base_dir)
    
    with open("./agent_config.json", 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    interactive_mode = config.get("interactive_mode", {{}})
    mode = interactive_mode.get("mode", "http")
    if mode == "http":
        http_server = interactive_mode.get("http_server", {{}})
        port = http_server.get("port", 8000)
    if mode == "file":
        file_config = interactive_mode.get("file", {{}})
        watch_dir = file_config.get("watch_dir", "/project/agent_io")
"""

        if entry_class is not None and entry_class != "":
            code += f"""
    from {entry_point} import {entry_class}
    agent = {entry_class}(config_path="./agent_config.json", debug=debug)
    
    if mode == "http":
        agent.run(mode="http", port=port)
    elif mode == "stdin":
        agent.run(mode="stdin")
    elif mode == "file":
        agent.run(mode="file", watch_dir=watch_dir)
    else:
        print("Unknown mode: " + mode, file=sys.stderr)
        sys.exit(1)"""
        else:
            code += f"""
    for name in dir({entry_point}):
        obj = getattr({entry_point}, name)
        if (isinstance(obj, type) and 
            hasattr(obj, '__bases__') and
            any('BaseAgent' in str(base) for base in obj.__bases__)):
            agent_class = obj
            break

    if not agent_class:
        print("Could not find agent class in agent module")
        sys.exit(1)

    agent = agent_class(config_path="./agent_config.json", debug=debug)
    
    if mode == "http":
        agent.run(mode="http", port=port)
    elif mode == "stdin":
        agent.run(mode="stdin")
    elif mode == "file":
        agent.run(mode="file", watch_dir=watch_dir)
    else:
        print("Unknown mode: " + mode, file=sys.stderr)
        sys.exit(1)"""

        with open(agent_path / "entry.py", 'w') as f:
            f.write(code)

        return
    

