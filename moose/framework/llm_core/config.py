"""Configuration management for model cost rates."""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List
try:
    from moose.framework.logging import get_core_logger, get_project_id
except ImportError:
    # Fallback for development mode
    from framework.logging import get_core_logger, get_project_id

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
        config_name = os.getenv("MOOSE_LLM_CONFIG_NAME", "model_config.yaml")
        
        # Check if explicit path is provided
        env_path = os.getenv("MOOSE_LLM_CONFIG_PATH")
        if env_path:
            return Path(env_path)

        candidates: List[Path] = []

        # 1) If project is set, check project directory first
        project_id = get_project_id() or os.getenv("MOOSE_PROJECT_ID") or "default"
        projects_base_dir = Path(os.getenv("MOOSE_PROJECTS_DIR") or (Path.cwd() / "projects"))
        project_dir = projects_base_dir / str(project_id)
        candidates.append(project_dir / config_name)

        # 2) Also check common project-root patterns (for backward compatibility)
        candidates.append(project_dir / "config.yaml")
        candidates.append(project_dir / "model_config.yaml")

        # 3) Current working directory fallback
        candidates.append(Path.cwd() / config_name)

        for p in candidates:
            try:
                if p.exists():
                    return p
            except Exception:
                continue

        # Default: return the project candidate path (best guess) so logs mention the intended location
        return candidates[0]
    
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
            
            self.logger.debug(f"Loaded config from: {self.config_path}")
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
        """
        Load the built-in default configuration from `model_config.yaml.template`.

        This keeps the template as the single source of truth, so defaults don't drift.
        """
        # If yaml isn't available, fall back to an empty config (cost calculation will be disabled).
        if not YAML_AVAILABLE or yaml is None:  # pragma: no cover
            return {"models": []}

        template_path = Path(__file__).with_name("model_config.yaml.template")
        try:
            if template_path.exists():
                with open(template_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return data if isinstance(data, dict) else {"models": []}
        except Exception as e:  # pragma: no cover
            try:
                self.logger.warning(f"Failed to load model_config.yaml.template: {e}")
            except Exception:
                pass
        return {"models": []}
    
    def get_model_list(self) -> List[Dict[str, Any]]:
        """Get model list from config."""
        return self.config.get("models", [])
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        Get cost rate information for a specific model.
        
        Args:
            model_name: Name of the model (e.g., "gpt-4", "claude-3-opus-20240229")
        
        Returns:
            Dictionary with pricing + token limits, or None if not found.
            Pricing fields are USD per 1,000,000 tokens.
        """
        for model in self.get_model_list():
            if model.get("model_name") == model_name:
                return {
                    "input_cost_per_million_token": model.get("input_cost_per_million_token"),
                    "output_cost_per_million_token": model.get("output_cost_per_million_token"),
                    "max_input_tokens": model.get("max_input_tokens"),
                    "max_output_tokens": model.get("max_output_tokens"),
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
            has_new = ("input_cost_per_million_token" in model and "output_cost_per_million_token" in model)
            if not has_new:
                self.logger.warning(f"Model {model.get('model_name')} missing cost rates")
                return False
        
        return True
