"""Configuration management for LiteLLM proxy."""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from framework.logging import get_core_logger

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


class ProxyConfig:
    """Manages LiteLLM proxy configuration from YAML file."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to config.yaml. If None, searches for it.
        """
        self.logger = get_core_logger()
        self.config_path = config_path
        
        if self.config_path is None:
            try:
                self.config_path = self._find_config()
            except FileNotFoundError as e:
                # In project context but config doesn't exist - raise error
                self.logger.error(str(e))
                raise
        
        self.config: Dict[str, Any] = {}
        
        if self.config_path and self.config_path.exists():
            self.load()
        else:
            # Only use defaults if not in project context
            project_dir = self._detect_project_dir()
            if project_dir:
                # In project context but config doesn't exist - should have been caught above
                self.logger.warning(f"Config file not found: {self.config_path}. Using defaults.")
            self.config = self._get_default_config()
    
    def _detect_project_dir(self) -> Optional[Path]:
        """Detect project directory from current working directory."""
        projects_dir = os.getenv("MOOSE_PROJECTS_DIR")
        if not projects_dir:
            return None
        
        cwd = Path.cwd()
        projects_path = Path(projects_dir)
        
        # Check if cwd is within projects directory
        try:
            cwd.relative_to(projects_path)
            # cwd is within projects_dir, find the project root
            # If cwd is projects_dir/project_name, return that
            if cwd.parent == projects_path:
                return cwd
            # If deeper, find the project root (first directory under projects_dir)
            for parent in cwd.parents:
                if parent.parent == projects_path:
                    return parent
        except ValueError:
            # cwd is not within projects_dir
            return None
        
        return None
    
    def _find_config(self) -> Path:
        """Find config.yaml file."""
        # Get config file name from environment variable, default to config.yaml
        config_name = os.getenv("MOOSE_LITELLM_CONFIG_NAME", "config.yaml")
        
        # Check if explicit path is provided (legacy support)
        env_path = os.getenv("LITELLM_CONFIG_PATH")
        if env_path:
            return Path(env_path)
        
        # Detect project directory from cwd
        project_dir = self._detect_project_dir()
        
        if project_dir:
            # We're in a project context, config file must exist in project directory
            config_path = project_dir / config_name
            if not config_path.exists():
                raise FileNotFoundError(
                    f"LiteLLM config file '{config_name}' not found in project directory: {project_dir}. "
                    f"Config file should have been created when the project was created."
                )
            return config_path
        
        # Not in project context, check current directory
        current = Path.cwd() / config_name
        if current.exists():
            return current
        
        # Return default location (current directory) - will use defaults if not found
        return Path.cwd() / config_name
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if not self.config_path or not self.config_path.exists():
            self.logger.warning("Config file does not exist, using defaults")
            self.config = self._get_default_config()
            return self.config
        
        if not YAML_AVAILABLE:
            self.logger.warning("PyYAML not available, using defaults. Install with: pip install pyyaml")
            self.config = self._get_default_config()
            return self.config
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f) or {}
            
            self.logger.info(f"Loaded config from: {self.config_path}")
            return self.config
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            self.config = self._get_default_config()
            return self.config
    
    def save_template(self, path: Optional[Path] = None):
        """Save a default config template to file."""
        if path is None:
            path = self.config_path or Path.cwd() / "config.yaml"
        
        if not YAML_AVAILABLE:
            self.logger.error("PyYAML not available. Install with: pip install pyyaml")
            return
        
        template = self._get_default_config()
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(template, f, default_flow_style=False, sort_keys=False)
            
            self.logger.info(f"Saved config template to: {path}")
        except Exception as e:
            self.logger.error(f"Failed to save config template: {e}")
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration template."""
        return {
            "model_list": [
                {
                    "model_name": "gpt-4",
                    "litellm_params": {
                        "model": "openai/gpt-4"
                    },
                    "model_info": {
                        "input_cost_per_token": 0.00003,
                        "output_cost_per_token": 0.00006
                    }
                },
                {
                    "model_name": "gpt-3.5-turbo",
                    "litellm_params": {
                        "model": "openai/gpt-3.5-turbo"
                    },
                    "model_info": {
                        "input_cost_per_token": 0.0000015,
                        "output_cost_per_token": 0.000002
                    }
                },
                {
                    "model_name": "claude-3-opus",
                    "litellm_params": {
                        "model": "anthropic/claude-3-opus-20240229"
                    },
                    "model_info": {
                        "input_cost_per_token": 0.000015,
                        "output_cost_per_token": 0.000075
                    }
                },
                {
                    "model_name": "claude-3-sonnet",
                    "litellm_params": {
                        "model": "anthropic/claude-3-sonnet-20240229"
                    },
                    "model_info": {
                        "input_cost_per_token": 0.000003,
                        "output_cost_per_token": 0.000015
                    }
                },
                {
                    "model_name": "gemini-pro",
                    "litellm_params": {
                        "model": "google/gemini-pro"
                    },
                    "model_info": {
                        "input_cost_per_token": 0.0000005,
                        "output_cost_per_token": 0.0000015
                    }
                }
            ],
            "general_settings": {
                "master_key": os.getenv("LITELLM_MASTER_KEY", "sk-1234"),
                "database_url": os.getenv("LITELLM_DATABASE_URL", ""),
            },
            "litellm_settings": {
                "success_callback": [],
                "failure_callback": [],
            }
        }
    
    def get_model_list(self) -> List[Dict[str, Any]]:
        """Get model list from config."""
        return self.config.get("model_list", [])
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get information for a specific model."""
        for model in self.get_model_list():
            if model.get("model_name") == model_name:
                return model
        return None
    
    def get_master_key(self) -> None | str:
        """Get general settings."""
        general_settings = self.config.get("general_settings", {})
        if general_settings:
            return general_settings.get("master_key", None)
        return None
    
    def get_database_url(self) -> None | str:
        """Get general settings."""
        general_settings = self.config.get("general_settings", {})
        if general_settings:
            return general_settings.get("database_url", None)
        return None
    
    def validate(self) -> bool:
        """Validate configuration."""
        if "model_list" not in self.config:
            self.logger.warning("Config missing 'model_list'")
            return False
        
        for model in self.config["model_list"]:
            if "model_name" not in model:
                self.logger.warning("Model missing 'model_name'")
                return False
            if "litellm_params" not in model:
                self.logger.warning(f"Model {model.get('model_name')} missing 'litellm_params'")
                return False
        
        return True

