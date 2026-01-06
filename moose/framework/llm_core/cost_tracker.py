"""Cost tracking for LLM calls."""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from moose.framework.logging import get_core_logger, get_project_log_dir, get_project_id


class CostTracker:
    """Tracks and logs LLM call costs to files.
    
    Cost logs are stored in the project log directory if available:
    projects/<project_id>/logs/llm_costs_<date>.log
    """
    
    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize the cost tracker.
        
        Args:
            log_dir: Directory to store cost logs. If None, uses project log directory
                     or falls back to current directory.
        """
        self.logger = get_core_logger()
        
        # Use project log directory if available
        if log_dir is None:
            project_log_dir = get_project_log_dir()
            if project_log_dir:
                log_dir = project_log_dir
            else:
                # Logging may not be initialized yet (set_project not called).
                # Prefer writing cost logs under projects/<project_id>/logs.
                project_id = get_project_id() or os.environ.get("MOOSE_PROJECT_ID") or "default"
                projects_base = os.environ.get("MOOSE_PROJECTS_DIR")
                if projects_base:
                    projects_base_dir = Path(projects_base)
                else:
                    projects_base_dir = Path.cwd() / "projects"
                log_dir = projects_base_dir / str(project_id) / "logs"
        
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Cost log file (daily rotation)
        today = datetime.now().strftime("%Y-%m-%d")
        self.log_file = self.log_dir / f"llm_costs_{today}.log"

        # If a root-level cost file was created before project logging was set up,
        # move/merge it into the project log directory.
        try:
            legacy_path = Path.cwd() / f"llm_costs_{today}.log"
            if legacy_path.exists() and legacy_path.resolve() != self.log_file.resolve():
                if self.log_file.exists():
                    # Merge: append legacy content then remove legacy file
                    with open(legacy_path, "r", encoding="utf-8") as src, open(self.log_file, "a", encoding="utf-8") as dst:
                        dst.write(src.read())
                    legacy_path.unlink(missing_ok=True)
                else:
                    legacy_path.replace(self.log_file)
        except Exception as e:
            # Non-fatal; proceed with current log_file
            try:
                self.logger.debug(f"Failed to move legacy cost log into project directory: {e}")
            except Exception:
                pass
        
        self.logger.debug(f"Cost tracker initialized: {self.log_file}")
    
    def log_cost(
        self,
        model: str,
        cost: float,
        tokens: Optional[Dict[str, int]] = None,
        request_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Log a cost entry.
        
        Args:
            model: Model name used
            cost: Cost in USD
            tokens: Token usage dictionary (input_tokens, output_tokens, total_tokens)
            request_id: Optional request identifier
            metadata: Optional additional metadata
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "cost": cost,
            "tokens": tokens or {},
            "request_id": request_id,
        }
        
        if metadata:
            entry["metadata"] = metadata
        
        # Write to log file (append mode)
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
            
            self.logger.debug(f"Logged cost: ${cost:.6f} for {model}")
        except Exception as e:
            self.logger.error(f"Failed to log cost: {e}")
    
    def get_daily_total(self, date: Optional[str] = None) -> float:
        """
        Get total cost for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format. If None, uses today.
        
        Returns:
            Total cost for the date
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        log_file = self.log_dir / f"llm_costs_{date}.log"
        
        if not log_file.exists():
            return 0.0
        
        total = 0.0
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        total += entry.get("cost", 0.0)
        except Exception as e:
            self.logger.error(f"Failed to read cost log: {e}")
        
        return total
    
    def get_model_total(self, model: str, date: Optional[str] = None) -> float:
        """
        Get total cost for a specific model on a date.
        
        Args:
            model: Model name
            date: Date in YYYY-MM-DD format. If None, uses today.
        
        Returns:
            Total cost for the model on the date
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        log_file = self.log_dir / f"llm_costs_{date}.log"
        
        if not log_file.exists():
            return 0.0
        
        total = 0.0
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get("model") == model:
                            total += entry.get("cost", 0.0)
        except Exception as e:
            self.logger.error(f"Failed to read cost log: {e}")
        
        return total

