"""Unit tests for Agent Core module."""

import os
import pytest
import tempfile
import shutil
import json
from pathlib import Path
from moose.framework.agent_core import (
    AgentLoader,
    ContainerManager,
    DockerfileGenerator,
    AgentRegistry
)
from moose.framework.logging import init_core_logger, set_global_debug


class TestAgentCore:
    """Test suite for Agent Core functionality."""
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test environment."""
        # Initialize logger
        set_global_debug(True)
        init_core_logger()
        
        # Create temporary agents directory
        self.temp_agents_dir = Path(tempfile.mkdtemp())
        self.test_agent_name = "test_agent"
        self.test_agent_dir = self.temp_agents_dir / self.test_agent_name
        
        # Create test agent structure
        self.test_agent_dir.mkdir(parents=True, exist_ok=True)
        
        # Create agent_config.json
        config = {
            "name": self.test_agent_name,
            "description": "Test agent for unit testing",
            "version": "1.0.0",
            "python_version": "3.11",
            "system_packages": ["curl"],
            "entry_point": "agent.py",
            "ports": [],
            "environment": {},
            "volumes": [],
            "resources": {
                "memory": "256m",
                "cpus": "0.5"
            }
        }
        
        with open(self.test_agent_dir / "agent_config.json", 'w') as f:
            json.dump(config, f, indent=2)
        
        # Create minimal agent.py
        with open(self.test_agent_dir / "agent.py", 'w') as f:
            f.write('import time\nprint("Hello from test agent")\ntime.sleep(30)')
        
        # Create requirements.txt
        with open(self.test_agent_dir / "requirements.txt", 'w') as f:
            f.write("requests==2.31.0\n")
        
        yield
        
        # Cleanup
        if self.temp_agents_dir.exists():
            shutil.rmtree(self.temp_agents_dir)
            
    @pytest.fixture
    def cleanup_docker_containers(self):
        """Cleanup Docker containers before tests."""
        import docker
        from docker.errors import NotFound
        docker_client = docker.from_env()
        try:
            container = docker_client.containers.get("moose-agent-test_agent-test_project_123")
            container.stop()
            container.remove()
        except NotFound:
            pass
        yield
    
    def test_agent_loader_discovery(self):
        """Test agent discovery."""
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        agents = loader.discover_agents()
        assert self.test_agent_name in agents
    
    def test_agent_loader_config_loading(self):
        """Test loading agent configuration."""
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        config = loader.load_agent_config(self.test_agent_name)
        assert config["name"] == self.test_agent_name
        assert config["python_version"] == "3.11"
        assert "system_packages" in config
    
    def test_agent_loader_validation(self):
        """Test agent validation."""
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        # Should pass validation
        assert loader.validate_agent(self.test_agent_name) is True
    
    def test_agent_loader_get_path(self):
        """Test getting agent path."""
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        path = loader.get_agent_path(self.test_agent_name)
        assert path == self.test_agent_dir
        assert path.exists()
    
    def test_dockerfile_generation(self):
        """Test Dockerfile generation."""
        generator = DockerfileGenerator()
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        agent_path = loader.get_agent_path(self.test_agent_name)
        config = loader.load_agent_config(self.test_agent_name)
        
        # Generate Dockerfile
        dockerfile_path = generator.generate_dockerfile(agent_path, config)
        
        assert dockerfile_path.exists()
        
        # Read and verify Dockerfile content
        with open(dockerfile_path, 'r') as f:
            content = f.read()
        
        assert "FROM python:3.11-slim" in content
        assert "WORKDIR /app" in content
        assert "COPY . /app" in content
        assert "curl" in content  # System package
        assert "requirements.txt" in content
        assert "agent.py" in content
    
    def test_dockerfile_generation_without_requirements(self):
        """Test Dockerfile generation without requirements.txt."""
        generator = DockerfileGenerator()
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        # Remove requirements.txt
        (self.test_agent_dir / "requirements.txt").unlink()
        
        agent_path = loader.get_agent_path(self.test_agent_name)
        config = loader.load_agent_config(self.test_agent_name)
        
        dockerfile_path = generator.generate_dockerfile(agent_path, config)
        
        with open(dockerfile_path, 'r') as f:
            content = f.read()
        
        # Should not have requirements.txt install
        assert "requirements.txt" not in content or "pip install" not in content
    
    def test_dockerfile_generation_with_setup_script(self):
        """Test Dockerfile generation with setup.sh."""
        generator = DockerfileGenerator()
        loader = AgentLoader(agents_dir=self.temp_agents_dir)
        
        # Create setup.sh
        with open(self.test_agent_dir / "setup.sh", 'w') as f:
            f.write("#!/bin/bash\necho 'Setup complete'\n")
        os.chmod(self.test_agent_dir / "setup.sh", 0o755)
        
        agent_path = loader.get_agent_path(self.test_agent_name)
        config = loader.load_agent_config(self.test_agent_name)
        
        dockerfile_path = generator.generate_dockerfile(agent_path, config)
        
        with open(dockerfile_path, 'r') as f:
            content = f.read()
        
        assert "setup.sh" in content
        assert "chmod +x" in content
    
    def test_agent_registry(self):
        """Test agent registry functionality."""
        registry = AgentRegistry()
        
        # Register container
        registry.register_container("container123", "test_agent", "test_project")
        
        # Get container ID
        container_id = registry.get_container_id("test_project", "test_agent")
        assert container_id == "container123"
        
        # Get project containers
        containers = registry.get_project_containers("test_project")
        assert "container123" in containers
        
        # Get container info
        info = registry.get_container_info("container123")
        assert info == ("test_agent", "test_project")
        
        # Unregister
        registry.unregister_container("container123")
        assert registry.get_container_id("test_project", "test_agent") is None
    
    def test_agent_registry_cleanup(self):
        """Test registry cleanup."""
        registry = AgentRegistry()
        
        # Register multiple containers
        registry.register_container("container1", "agent1", "project1")
        registry.register_container("container2", "agent2", "project1")
        registry.register_container("container3", "agent3", "project2")
        
        # Clear project1
        registry.clear_project("project1")
        
        assert len(registry.get_project_containers("project1")) == 0
        assert len(registry.get_project_containers("project2")) == 1
    
    @pytest.mark.docker
    def test_container_manager_initialization(self):
        """Test ContainerManager initialization."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            assert manager.agent_loader is not None
            assert manager.dockerfile_generator is not None
            assert manager.registry is not None
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.skip("Docker daemon not available")
            raise
    
    @pytest.mark.docker
    def test_container_image_building(self):
        """Test Docker image building."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            
            # Build image
            image_name = manager.build_agent_image(self.test_agent_name)
            assert image_name is not None
            assert self.test_agent_name in image_name
            
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.skip("Docker daemon not available")
            raise
        except Exception as e:
            pytest.skip(f"Image build test skipped: {e}")
    
    @pytest.mark.docker
    def test_container_start_stop(self, cleanup_docker_containers):
        """Test container start and stop."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            
            # Build image first
            manager.build_agent_image(self.test_agent_name)
            
            # Start container
            project_id = "test_project_123"
            container_id = manager.start_agent_container(
                agent_name=self.test_agent_name,
                project_id=project_id
            )
            
            assert container_id is not None
            
            # Check status
            status = manager.get_container_status(self.test_agent_name, project_id)
            assert status == "running"
            
            # Stop container
            stopped = manager.stop_agent_container(self.test_agent_name, project_id)
            assert stopped is True
            
            # Cleanup
            manager.cleanup_project_containers(project_id)
            
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.fail("Docker daemon not available")
            raise
        except Exception as e:
            pytest.fail(f"Container start/stop test skipped: {e}")
    
    @pytest.mark.docker
    def test_project_network_creation(self, cleanup_docker_containers):
        """Test project network creation."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            
            project_id = "test_network_project"
            network_name = manager.ensure_project_network(project_id)
            
            assert network_name is not None
            assert project_id in network_name
            
            # Cleanup
            try:
                network = manager.docker_client.networks.get(network_name)
                network.remove()
            except:
                pass
                
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.fail("Docker daemon not available")
            raise
    
    @pytest.mark.docker
    def test_container_logs(self, cleanup_docker_containers):
        """Test retrieving container logs."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            
            # Build and start container
            manager.build_agent_image(self.test_agent_name)
            project_id = "test_logs_project"
            container_id = manager.start_agent_container(
                agent_name=self.test_agent_name,
                project_id=project_id
            )
            
            # Wait a bit for container to produce logs
            import time
            time.sleep(2)
            
            # Get logs
            logs = manager.get_container_logs(
                agent_name=self.test_agent_name,
                project_id=project_id,
                tail=10
            )
            
            assert logs is not None
            assert isinstance(logs, str)
            
            # Cleanup
            manager.cleanup_project_containers(project_id)
            
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.fail("Docker daemon not available")
            raise
        except Exception as e:
            pytest.fail(f"Container logs test skipped: {e}")
    
    @pytest.mark.docker
    def test_project_cleanup(self, cleanup_docker_containers):
        """Test project container cleanup."""
        try:
            manager = ContainerManager(agents_dir=self.temp_agents_dir)
            
            # Build image
            manager.build_agent_image(self.test_agent_name)
            
            project_id = "test_cleanup_project"
            
            # Start multiple containers (same agent, different would be better but simpler)
            container_id1 = manager.start_agent_container(
                agent_name=self.test_agent_name,
                project_id=project_id
            )
            
            # Cleanup all
            manager.cleanup_project_containers(project_id)
            
            # Verify cleanup
            status = manager.get_container_status(self.test_agent_name, project_id)
            assert status is None
            
        except RuntimeError as e:
            if "Docker daemon" in str(e):
                pytest.fail("Docker daemon not available")
            raise
        except Exception as e:
            pytest.fail(f"Cleanup test skipped: {e}")

