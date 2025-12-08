"""Docker container lifecycle management for agents."""

import os
from pathlib import Path
from typing import Optional, Dict, Any
from framework.logging import get_core_logger
from framework.agent_core.agent_loader import AgentLoader
from framework.agent_core.dockerfile_generator import DockerfileGenerator
from framework.agent_core.agent_registry import AgentRegistry

try:
    import docker
    from docker.errors import DockerException, ImageNotFound, ContainerError, NotFound
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None
    DockerException = Exception
    ImageNotFound = Exception
    ContainerError = Exception
    NotFound = Exception


class ContainerManager:
    """Manages Docker containers for agents."""
    
    def __init__(
        self,
        agents_dir: Optional[Path] = None,
        network_prefix: Optional[str] = None,
        image_prefix: Optional[str] = None
    ):
        """
        Initialize the container manager.
        
        Args:
            agents_dir: Directory containing agents
            network_prefix: Prefix for Docker networks (default: moose-project-)
            image_prefix: Prefix for Docker images (default: moose-agent-)
        """
        if not DOCKER_AVAILABLE:
            raise ImportError(
                "docker library is required. Install with: pip install docker"
            )
        
        self.logger = get_core_logger()
        self.agent_loader = AgentLoader(agents_dir)
        self.dockerfile_generator = DockerfileGenerator()
        self.registry = AgentRegistry()
        
        # Docker client
        try:
            self.docker_client = docker.from_env()
            # Test connection
            self.docker_client.ping()
        except (DockerException, Exception) as e:
            self.logger.error(f"Failed to connect to Docker daemon: {e}")
            raise RuntimeError(f"Docker daemon not available: {e}") from e
        
        # Configuration
        self.network_prefix = network_prefix or os.getenv(
            "MOOSE_DOCKER_NETWORK_PREFIX", "moose-project-"
        )
        self.image_prefix = image_prefix or os.getenv(
            "MOOSE_DOCKER_IMAGE_PREFIX", "moose-agent-"
        )
        
        self.logger.info("Container manager initialized")
    
    def build_agent_image(self, agent_name: str, force_rebuild: bool = False) -> str:
        """
        Build Docker image for an agent.
        
        Args:
            agent_name: Name of the agent
            force_rebuild: Force rebuild even if image exists
        
        Returns:
            Image name/tag
        """
        
        # Image name
        image_name = f"{self.image_prefix}{agent_name}"
        
        # Check if image exists
        if not force_rebuild:
            try:
                self.docker_client.images.get(image_name)
                self.logger.debug(f"Image {image_name} already exists, skipping build")
                return image_name
            except ImageNotFound:
                pass
            
        self.logger.info(f"Building image for agent: {agent_name}")
        
        # Validate agent
        self.agent_loader.validate_agent(agent_name)
        
        # Get agent path and config
        agent_path = self.agent_loader.get_agent_path(agent_name)
        config = self.agent_loader.load_agent_config(agent_name)
        
        # Generate Dockerfile if needed
        dockerfile_path = agent_path / "Dockerfile"
        if not dockerfile_path.exists():
            self.logger.info(f"Generating Dockerfile for agent: {agent_name}")
            self.dockerfile_generator.generate_dockerfile(agent_path, config)
        
        # Build image
        # Use moose directory as build context so we can access framework
        # The agent_path is relative to moose/agents/<agent_name>
        moose_dir = agent_path.parent.parent  # Go up from agents/<agent_name> to moose/
        
        try:
            self.logger.info(f"Building Docker image: {image_name}")
            image, build_logs = self.docker_client.images.build(
                path=str(moose_dir),
                tag=image_name,
                dockerfile=str(agent_path.relative_to(moose_dir) / "Dockerfile"),
                rm=True,
                forcerm=True
            )
            
            self.logger.info(f"Successfully built image: {image_name}")
            return image_name
        except DockerException as e:
            self.logger.error(f"Failed to build image for {agent_name}: {e}")
            raise
    
    def ensure_project_network(self, project_id: str) -> str:
        """
        Ensure Docker network exists for a project.
        
        Args:
            project_id: Project identifier
        
        Returns:
            Network name
        """
        network_name = f"{self.network_prefix}{project_id}"
        
        try:
            network = self.docker_client.networks.get(network_name)
            self.logger.debug(f"Network {network_name} already exists")
            return network_name
        except NotFound:
            pass
        
        # Create network
        try:
            self.logger.info(f"Creating Docker network: {network_name}")
            network = self.docker_client.networks.create(
                network_name,
                driver="bridge"
            )
            self.logger.info(f"Created network: {network.name}")
            return network.name
        except DockerException as e:
            self.logger.error(f"Failed to create network: {e}")
            raise
    
    def start_agent_container(
        self,
        agent_name: str,
        project_id: str,
        project_dir: Optional[Path] = None,
        environment: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Start a container for an agent.
        
        Args:
            agent_name: Name of the agent
            project_id: Project identifier
            project_dir: Project directory to mount (optional)
            environment: Additional environment variables
        
        Returns:
            Container ID
        """
        # Check if container already running
        existing_container_id = self.registry.get_container_id(project_id, agent_name)
        if existing_container_id:
            try:
                container = self.docker_client.containers.get(existing_container_id)
                if container.status == "running":
                    self.logger.debug(
                        f"Container for agent '{agent_name}' already running: {existing_container_id[:12]}"
                    )
                    return existing_container_id
            except NotFound:
                # Container doesn't exist, continue
                pass
        
        self.logger.info(f"Starting container for agent: {agent_name}")
        
        # Build image if needed
        image_name = self.build_agent_image(agent_name)
        
        # Get agent config
        config = self.agent_loader.load_agent_config(agent_name)
        agent_path = self.agent_loader.get_agent_path(agent_name)
        
        # Ensure network exists
        network_name = self.ensure_project_network(project_id)
        
        # Prepare container configuration
        container_name = f"{self.image_prefix}{agent_name}-{project_id}"
        
        # Environment variables
        env_vars = config.get("environment", {}).copy()
        if environment:
            env_vars.update(environment)
        
        # Volume mounts
        volumes = {}
        # Mount agent code
        volumes[str(agent_path)] = {
            "bind": "/app",
            "mode": "rw"
        }
        # Mount project directory if provided
        if project_dir:
            volumes[str(project_dir)] = {
                "bind": "/project",
                "mode": "ro"  # Read-only for security
            }
        # Add custom volumes from config
        for vol_config in config.get("volumes", []):
            if isinstance(vol_config, dict):
                host_path = vol_config.get("host")
                container_path = vol_config.get("container", host_path)
                mode = vol_config.get("mode", "rw")
                if host_path:
                    volumes[host_path] = {"bind": container_path, "mode": mode}
        
        # Resource limits
        resources = config.get("resources", {})
        mem_limit = resources.get("memory")
        cpu_limit = resources.get("cpus")
        
        # Port mappings
        ports = {}
        for port_config in config.get("ports", []):
            if isinstance(port_config, dict):
                container_port = port_config.get("container")
                host_port = port_config.get("host", container_port)
                if container_port:
                    ports[container_port] = host_port
            elif isinstance(port_config, (int, str)):
                ports[str(port_config)] = port_config
        
        # Create and start container
        try:
            container = self.docker_client.containers.create(
                image=image_name,
                name=container_name,
                environment=env_vars,
                volumes=volumes,
                network=network_name,
                ports=ports if ports else None,
                mem_limit=mem_limit,
                nano_cpus=int(float(cpu_limit) * 1e9) if cpu_limit else None,
                detach=True,
                auto_remove=False
            )
            
            container.start()
            
            # Register container
            self.registry.register_container(container.id, agent_name, project_id)
            
            self.logger.info(
                f"Started container {container.id[:12]} for agent '{agent_name}' "
                f"in project '{project_id}'"
            )
            
            return container.id
        except DockerException as e:
            self.logger.error(f"Failed to start container for {agent_name}: {e}")
            raise
    
    def stop_agent_container(self, agent_name: str, project_id: str) -> bool:
        """
        Stop a container for an agent.
        
        Args:
            agent_name: Name of the agent
            project_id: Project identifier
        
        Returns:
            True if container was stopped, False if not found
        """
        container_id = self.registry.get_container_id(project_id, agent_name)
        if not container_id:
            self.logger.debug(f"No running container found for agent '{agent_name}'")
            return False
        
        try:
            container = self.docker_client.containers.get(container_id)
            container.stop(timeout=10)
            self.logger.info(f"Stopped container {container_id[:12]} for agent '{agent_name}'")
            
            # Unregister
            self.registry.unregister_container(container_id)
            
            return True
        except NotFound:
            self.logger.warning(f"Container {container_id[:12]} not found")
            self.registry.unregister_container(container_id)
            return False
        except DockerException as e:
            self.logger.error(f"Failed to stop container: {e}")
            return False
    
    def get_container_status(self, agent_name: str, project_id: str) -> Optional[str]:
        """
        Get status of an agent container.
        
        Args:
            agent_name: Name of the agent
            project_id: Project identifier
        
        Returns:
            Container status ("running", "stopped", etc.) or None if not found
        """
        container_id = self.registry.get_container_id(project_id, agent_name)
        if not container_id:
            return None
        
        try:
            container = self.docker_client.containers.get(container_id)
            container.reload()
            return container.status
        except docker.errors.NotFound:
            self.registry.unregister_container(container_id)
            return None
    
    def get_container_logs(
        self,
        agent_name: str,
        project_id: str,
        tail: int = 100,
        follow: bool = False
    ) -> str:
        """
        Get logs from an agent container.
        
        Args:
            agent_name: Name of the agent
            project_id: Project identifier
            tail: Number of lines to retrieve
            follow: Follow log output (streaming)
        
        Returns:
            Log output
        """
        container_id = self.registry.get_container_id(project_id, agent_name)
        if not container_id:
            raise ValueError(f"No container found for agent '{agent_name}'")
        
        try:
            container = self.docker_client.containers.get(container_id)
            logs = container.logs(tail=tail, follow=follow, stream=follow)
            
            if follow:
                # Return generator for streaming
                return logs
            else:
                # Return decoded string
                return logs.decode('utf-8')
        except NotFound:
            raise ValueError(f"Container {container_id[:12]} not found")
    
    def cleanup_project_containers(self, project_id: str):
        """
        Stop and remove all containers for a project.
        
        Args:
            project_id: Project identifier
        """
        self.logger.info(f"Cleaning up containers for project: {project_id}")
        
        containers = list(self.registry.get_project_containers(project_id))
        
        for container_id in containers:
            try:
                container = self.docker_client.containers.get(container_id)
                container.stop(timeout=60)
                container.remove()
                self.logger.debug(f"Removed container {container_id[:12]}")
            except docker.errors.NotFound:
                pass
            except DockerException as e:
                self.logger.warning(f"Failed to remove container {container_id[:12]}: {e}")
            finally:
                self.registry.unregister_container(container_id)
        
        # Clean up network
        network_name = f"{self.network_prefix}{project_id}"
        try:
            network = self.docker_client.networks.get(network_name)
            network.remove()
            self.logger.debug(f"Removed network {network_name}")
        except NotFound:
            pass
        except DockerException as e:
            self.logger.warning(f"Failed to remove network {network_name}: {e}")
        
        self.registry.clear_project(project_id)
        self.logger.info(f"Cleanup complete for project: {project_id}")

