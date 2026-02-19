"""Agent discovery and configuration loading."""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from moose.framework.logging import get_core_logger
from moose.framework.agent_core.config_loader import load_agent_config


class AgentLoader:
    """Discovers and loads agent configurations."""
    
    def __init__(self, agents_dir: Optional[Path] = None):
        """
        Initialize the agent loader.
        
        Args:
            agents_dir: Directory containing agents. If None, uses default location.
        """
        self.logger = get_core_logger()
        
        if agents_dir is None:
            # Default to moose/agents/ relative to framework
            framework_dir = Path(__file__).parent.parent.parent
            agents_dir = framework_dir / "agents"
        
        self.agents_dir = Path(agents_dir)
        
        self.logger.debug(f"Agent loader initialized with directory: {self.agents_dir}")
    
    def discover_agents(self) -> List[str]:
        """
        Discover all available agents.
        
        Returns:
            List of agent names
        """
        if not self.agents_dir.exists():
            self.logger.warning(f"Agents directory does not exist: {self.agents_dir}")
            return []
        
        agents = []
        for item in self.agents_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check if it has agent.py or agent_config.json
                if (item / "agent.py").exists() or (item / "agent_config.json").exists():
                    agents.append(item.name)
        
        self.logger.debug(f"Discovered {len(agents)} agents: {agents}")
        return agents
    
    def get_agent_path(self, agent_name: str) -> Path:
        """
        Get the path to an agent directory.
        
        Args:
            agent_name: Name of the agent
        
        Returns:
            Path to agent directory
        
        Raises:
            ValueError: If agent doesn't exist
        """
        agent_path = self.agents_dir / agent_name
        
        if not agent_path.exists():
            raise ValueError(f"Agent '{agent_name}' not found at {agent_path}")
        
        return agent_path
    
    def load_agent_config(self, agent_name: str) -> Dict[str, Any]:
        """
        Load agent configuration from agent_config.json.
        
        Args:
            agent_name: Name of the agent
        
        Returns:
            Agent configuration dictionary
        
        Raises:
            ValueError: If agent doesn't exist or config is invalid
            FileNotFoundError: If agent_config.json doesn't exist
        """
        agent_path = self.get_agent_path(agent_name)
        config_path = agent_path / "agent_config.json"
        
        if not config_path.exists():
            raise FileNotFoundError(
                f"Agent config not found: {config_path}. "
                f"Each agent must have an agent_config.json file."
            )
        
        try:
            config = load_agent_config(config_path)
            # Validate required fields
            if "name" not in config:
                config["name"] = agent_name
            self.logger.debug(f"Loaded config for agent: {agent_name}")
            return config
        except Exception as e:
            raise ValueError(f"Failed to load agent config: {e}")
    
    def validate_agent(self, agent_name: str) -> bool:
        """
        Validate that an agent has the required structure.
        
        Args:
            agent_name: Name of the agent
        
        Returns:
            True if agent is valid
        
        Raises:
            ValueError: If agent structure is invalid
        """
        agent_path = self.get_agent_path(agent_name)
        
        # Check for required files
        required_files = ["agent_config.json"]
        missing_files = []
        
        for file in required_files:
            if not (agent_path / file).exists():
                missing_files.append(file)
        
        if missing_files:
            raise ValueError(
                f"Agent '{agent_name}' is missing required files: {missing_files}"
            )
        
        # Validate config
        try:
            config = self.load_agent_config(agent_name)
        except Exception as e:
            raise ValueError(f"Agent '{agent_name}' has invalid config: {e}")
        
        # Check for agent.py or entry_point
        entry_point = config.get("entry_point", "agent.py")
        if not (agent_path / entry_point).exists():
            self.logger.warning(
                f"Agent '{agent_name}' entry point '{entry_point}' not found. "
                f"Agent may not be executable."
            )
        
        self.logger.debug(f"Agent '{agent_name}' validation passed")
        return True
    
    def get_agent_file_path(self, agent_name: str, filename: str) -> Optional[Path]:
        """
        Get path to a specific file in agent directory.
        
        Args:
            agent_name: Name of the agent
            filename: Name of the file
        
        Returns:
            Path to file if exists, None otherwise
        """
        try:
            agent_path = self.get_agent_path(agent_name)
            file_path = agent_path / filename
            return file_path if file_path.exists() else None
        except ValueError:
            return None
