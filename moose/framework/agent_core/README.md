# Agent Core - Docker Container Management

Docker-based container management system for running agents in isolated environments.

## Overview

The Agent Core module provides:
- **Container Lifecycle Management**: Build, start, stop agent containers
- **Agent Discovery**: Automatically discover agents from `moose/agents/`
- **Dockerfile Generation**: Auto-generate Dockerfiles from agent configuration
- **Project Isolation**: Each project gets its own Docker network
- **Resource Management**: CPU and memory limits per agent

## Agent Structure

Each agent must be in its own directory under `moose/agents/<agent_name>/`:

```
moose/agents/
  my_agent/
    agent.py              # Agent implementation (required)
    agent_config.json     # Agent configuration (required)
    Dockerfile            # Container definition (optional - auto-generated)
    requirements.txt      # Python dependencies (optional)
    setup.sh             # Initialization script (optional)
    python_version        # Python version file (optional)
```

### Required Files

- **agent.py**: Agent implementation code
- **agent_config.json**: Agent metadata and configuration

### Optional Files

- **Dockerfile**: Custom container definition (auto-generated if missing)
- **requirements.txt**: Python package dependencies
- **setup.sh**: Shell script for system package installation (apt-get, etc.)
- **python_version**: Text file containing Python version (e.g., "3.11")

## Agent Configuration

`agent_config.json` structure:

```json
{
  "name": "my_agent",
  "description": "Agent description",
  "version": "1.0.0",
  "python_version": "3.11",
  "system_packages": ["curl", "git", "build-essential"],
  "entry_point": "agent.py",
  "ports": [
    {"container": 8000, "host": 8000}
  ],
  "environment": {
    "CUSTOM_VAR": "value"
  },
  "volumes": [
    {
      "host": "/path/on/host",
      "container": "/path/in/container",
      "mode": "ro"
    }
  ],
  "resources": {
    "memory": "512m",
    "cpus": "1.0"
  }
}
```

### Configuration Fields

- **name**: Agent name (should match directory name)
- **description**: Human-readable description
- **version**: Agent version
- **python_version**: Python version for container (default: "3.11")
- **system_packages**: List of apt packages to install
- **entry_point**: Python file to run (default: "agent.py")
- **ports**: Port mappings (container -> host)
- **environment**: Environment variables for container
- **volumes**: Additional volume mounts
- **resources**: CPU and memory limits

## Usage

### Basic Example

```python
from framework.agent_core import ContainerManager

# Initialize container manager
manager = ContainerManager()

# Start agent container for a project
container_id = manager.start_agent_container(
    agent_name="my_agent",
    project_id="my_project",
    project_dir=Path("/path/to/project")
)

# Check container status
status = manager.get_container_status("my_agent", "my_project")
print(f"Status: {status}")

# Get logs
logs = manager.get_container_logs("my_agent", "my_project", tail=50)
print(logs)

# Stop container
manager.stop_agent_container("my_agent", "my_project")

# Cleanup all project containers
manager.cleanup_project_containers("my_project")
```

### Agent Discovery

```python
from framework.agent_core import AgentLoader

loader = AgentLoader()

# Discover all agents
agents = loader.discover_agents()
print(f"Available agents: {agents}")

# Load agent configuration
config = loader.load_agent_config("my_agent")
print(f"Agent config: {config}")

# Validate agent
loader.validate_agent("my_agent")
```

## Container Lifecycle

1. **Project Start**: When a project begins execution, required agent containers are started
2. **During Execution**: Containers remain running and can be reused across multiple calls
3. **Project End**: All project containers are stopped and cleaned up

## Docker Network

Each project gets its own Docker network:
- Network name: `moose-project-<project_id>`
- All project agents are on the same network
- Enables inter-agent communication

## Volume Mounts

By default, containers have:
- **Agent code**: Mounted at `/app` (read-write)
- **Project directory**: Mounted at `/project` (read-only)

Additional volumes can be specified in `agent_config.json`.

## Environment Variables

- `MOOSE_AGENTS_DIR`: Override agents directory (default: `moose/agents/`)
- `MOOSE_DOCKER_NETWORK_PREFIX`: Network prefix (default: `moose-project-`)
- `MOOSE_DOCKER_IMAGE_PREFIX`: Image prefix (default: `moose-agent-`)

## Requirements

- Docker installed and running
- `docker` Python library: `pip install docker`
- Docker daemon accessible

## Error Handling

The system handles:
- Docker daemon not running
- Image build failures
- Container start failures
- Missing agent files
- Invalid configurations

All errors are logged with appropriate error messages.

