"""Configuration management for model cost rates."""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List
try:
    from moose.framework.logging import get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.logging import get_core_logger

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None


class ModelConfig:
    """Manages model cost rate configuration from YAML file."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to config.yaml. If None, searches for it.
        """
        self.logger = get_core_logger()
        self.config_path = config_path
        
        if self.config_path is None:
            self.config_path = self._find_config()
        
        self.config: Dict[str, Any] = {}
        
        if self.config_path and self.config_path.exists():
            self.load()
        else:
            self.logger.warning(f"Config file not found: {self.config_path}. Using defaults.")
            self.config = self._get_default_config()
    
    def _find_config(self) -> Path:
        """Find config.yaml file."""
        # Get config file name from environment variable, default to config.yaml
        config_name = os.getenv("MOOSE_LLM_CONFIG_NAME", "config.yaml")
        
        # Check if explicit path is provided
        env_path = os.getenv("MOOSE_LLM_CONFIG_PATH")
        if env_path:
            return Path(env_path)
        
        # Check current directory
        current = Path.cwd() / config_name
        if current.exists():
            return current
        
        # Return default location
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
        """Get default configuration template with cost rates."""
        return {
            "models": [
                {
                    "model_name": "gpt-5",
                    "input_cost_per_token": 0.00000125,
                    "output_cost_per_token": 0.00001
                },
                {
                    "model_name": "gpt-4-turbo",
                    "input_cost_per_token": 0.00001,
                    "output_cost_per_token": 0.00003
                },
                {
                    "model_name": "gpt-4",
                    "input_cost_per_token": 0.00003,
                    "output_cost_per_token": 0.00006
                },
                {
                    "model_name": "gpt-4o",
                    "input_cost_per_token": 0.000005,
                    "output_cost_per_token": 0.000015
                },
                {
                    "model_name": "gpt-3.5-turbo",
                    "input_cost_per_token": 0.0000015,
                    "output_cost_per_token": 0.000002
                },
                {
                    "model_name": "claude-opus-4-5-20251101",
                    "input_cost_per_token": 0.000005,
                    "output_cost_per_token": 0.000025
                },
                {
                    "model_name": "claude-sonnet-4-5-20250929",
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000015
                },
                {
                    "model_name": "claude-sonnet-4-20250514",
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000015
                },
                {
                    "model_name": "claude-3-opus-20240229",
                    "input_cost_per_token": 0.000015,
                    "output_cost_per_token": 0.000075
                },
                {
                    "model_name": "claude-3-sonnet-20240229",
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000015
                },
                {
                    "model_name": "claude-3-5-sonnet-20241022",
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000015
                },
                {
                    "model_name": "gemini-pro",
                    "input_cost_per_token": 0.0000005,
                    "output_cost_per_token": 0.0000015
                },
                {
                    "model_name": "gemini-2.5-flash",
                    "input_cost_per_token": 0.000000125,
                    "output_cost_per_token": 0.0000005
                }
            ]
        }
    
    def get_model_list(self) -> List[Dict[str, Any]]:
        """Get model list from config."""
        return self.config.get("models", [])
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Get cost rate information for a specific model.
        
        Args:
            model_name: Name of the model (e.g., "gpt-4", "claude-3-opus-20240229")
        
        Returns:
            Dictionary with input_cost_per_token and output_cost_per_token, or None if not found
        """
        for model in self.get_model_list():
            if model.get("model_name") == model_name:
                return {
                    "input_cost_per_token": model.get("input_cost_per_token"),
                    "output_cost_per_token": model.get("output_cost_per_token")
                }
        return None
    
    def validate(self) -> bool:
        """Validate configuration."""
        if "models" not in self.config:
            self.logger.warning("Config missing 'models'")
            return False
        
        for model in self.config["models"]:
            if "model_name" not in model:
                self.logger.warning("Model missing 'model_name'")
                return False
            if "input_cost_per_token" not in model or "output_cost_per_token" not in model:
                self.logger.warning(f"Model {model.get('model_name')} missing cost rates")
                return False
        
        return True
