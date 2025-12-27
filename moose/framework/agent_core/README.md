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

`agent_config.json` follows a structured format with four main sections:

### Configuration Structure

```json
{
  "name": "my_agent",
  "description": "Agent description",
  "version": "1.0.0",
  "python_version": "3.11",
  "entry_point": "agent.py",
  "entry_class": "MyAgent",
  "docker": {
    "container_suffix_name": "",
    "container_override": true,
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
  },
  "interactive_mode": {
    "mode": "http",
    "http_server": {
      "port": 8000,
      "auth_password": "",
      "endpoints": [
        {
          "path": "/process",
          "method": "POST",
          "handler": "process",
          "description": "Process input data",
          "auth_required": false
        }
      ]
    },
    "file": {
      "watch_dir": "/project/agent_io"
    }
  },
  "custom": {
    "agent_specific_config": "value"
  }
}
```

### Configuration Sections

#### Top-Level Fields (Basic Agent Info)

- **name** (required): Agent name (should match directory name)
- **description** (required): Human-readable description
- **version** (required): Agent version
- **python_version** (required): Python version for container (e.g., "3.11")
- **entry_point** (required): Python file to run (default: "agent.py")
- **entry_class** (optional): Class name to instantiate (auto-detected if not specified)

#### `docker` Object (Container Configuration)

All Docker-related settings:

- **network** (optional): Docker network name to attach the agent container to. If set, this overrides the default per-project network name (`moose-project-<project_id>` by default). This is useful for sharing a network across multiple Moose projects.
- **container_suffix_name** (optional): Suffix to append to container name
- **container_override** (optional, default: false): Whether to override existing containers
- **ports** (optional): Array of port mappings
  ```json
  [{"container": 8000, "host": 8000}]
  ```
- **environment** (optional): Environment variables for container
  ```json
  {"VAR_NAME": "value"}
  ```
- **volumes** (optional): Additional volume mounts
  ```json
  [{"host": "/host/path", "container": "/container/path", "mode": "ro"}]
  ```
- **resources** (optional): CPU and memory limits
  ```json
  {"memory": "512m", "cpus": "1.0"}
  ```

#### `interactive_mode` Object (Communication Mode)

Configuration for how the agent communicates:

- **mode** (required): Communication mode - `"http"`, `"stdin"`, or `"file"`
- **http_server** (required if mode is "http"): HTTP server configuration
  - **port** (required): Port to listen on
  - **auth_password** (optional): Password for HTTP authentication
  - **endpoints** (optional): Array of custom HTTP endpoints
    ```json
    [{
      "path": "/endpoint",
      "method": "POST",
      "handler": "method_name",
      "description": "Endpoint description",
      "auth_required": false
    }]
    ```
- **file** (required if mode is "file"): File watch configuration
  - **watch_dir** (required): Directory to watch for input files

#### `custom` Object (Agent-Specific Configuration)

All agent-specific configuration goes here. This section is free-form and can contain any keys specific to your agent's needs.

Examples:
- `scraper_config` for scraping agents
- `llm_config` for LLM-based agents
- `finance_office` for inter-agent communication configs
- Any other agent-specific settings

### Example: News Scraper Agent

```json
{
  "name": "news_scraper",
  "description": "Generic news scraper",
  "version": "1.0.0",
  "python_version": "3.11",
  "entry_point": "agent.py",
  "entry_class": "NewsScraper",
  "docker": {
    "container_suffix_name": "",
    "container_override": true,
    "ports": [{"container": 3500, "host": 3500}],
    "environment": {
      "SCRAPER_DATA_DIR": "/data/scraper"
    },
    "volumes": [
      {"host": "/data/scraper", "container": "/data/scraper"}
    ],
    "resources": {"memory": "512m", "cpus": "1.0"}
  },
  "interactive_mode": {
    "mode": "http",
    "http_server": {
      "port": 3500,
      "auth_password": "",
      "endpoints": [
        {
          "path": "/start",
          "method": "GET",
          "handler": "scrape",
          "description": "Start scraping",
          "auth_required": false
        }
      ]
    }
  },
  "custom": {
    "scraper_config": {
      "start_url": "https://example.com/news",
      "rate_limit": 60
    },
    "finance_office": {
      "endpoint": "http://localhost:3501/get_financial_news"
    }
  }
}
```

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

