"""Cost tracking for LLM calls."""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from framework.logging import get_core_logger


class CostTracker:
    """Tracks and logs LLM call costs to files."""
    
    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize the cost tracker.
        
        Args:
            log_dir: Directory to store cost logs. If None, uses current directory.
        """
        self.logger = get_core_logger()
        self.log_dir = log_dir or Path.cwd()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Cost log file (daily rotation)
        today = datetime.now().strftime("%Y-%m-%d")
        self.log_file = self.log_dir / f"llm_costs_{today}.log"
        
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
            tokens: Token usage dictionary (prompt_tokens, completion_tokens, total_tokens)
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

