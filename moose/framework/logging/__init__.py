"""Logging system for Moose Framework core."""

import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime


class LabeledFormatter(logging.Formatter):
    """Custom formatter that includes a label prefix."""
    
    def __init__(self, label: str = "[core]", datefmt: Optional[str] = None):
        """
        Initialize the labeled formatter.
        
        Args:
            label: Label to prefix in the log format (e.g., "[core]", "[project:XXX]", "[agent:XXX]")
            datefmt: Date format string (optional)
        """
        self.label = label
        self.datefmt = datefmt
        # Base format without label - we'll add label dynamically
        super().__init__(datefmt=datefmt)
    
    def format(self, record):
        """Format the log record with the label prefix."""
        # Create format with label before pathname
        fmt = f'%(asctime)s | {self.label} %(pathname)s:%(lineno)d | %(levelname)-8s | %(message)s'
        formatter = logging.Formatter(fmt=fmt, datefmt=self.datefmt)
        return formatter.format(record)


class MooseLogger:
    """Logger for Moose Framework with console and optional file output."""
    
    def __init__(self, name: str = "moose", log_file: Optional[Path] = None, debug: bool = False, label: str = "[core]"):
        """
        Initialize the Moose logger.
        
        Args:
            name: Logger name
            log_file: Optional path to log file. If provided, logs will be written to file.
            debug: If True, set log level to DEBUG. Otherwise, use INFO.
            label: Label prefix for logs (e.g., "[core]", "[project:XXX]", "[agent:XXX]")
        """
        self.label = label
        self.logger = logging.getLogger(name)
        # Set logger level based on debug flag (INFO by default, DEBUG when debug=True)
        logger_level = logging.DEBUG if debug else logging.INFO
        self.logger.setLevel(logger_level)
        
        # Prevent duplicate handlers if logger already exists
        if self.logger.handlers:
            for handler in self.logger.handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                    handler.setLevel(logger_level)
                elif isinstance(handler, logging.FileHandler):
                    handler.setLevel(logging.DEBUG)  # File always gets all logs
            # Update formatter labels if they exist
            for handler in self.logger.handlers:
                if isinstance(handler.formatter, LabeledFormatter):
                    handler.formatter.label = label
            return
        
        # Create formatter with label, timestamp, file:line, level, and message
        formatter = LabeledFormatter(
            label=label,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console handler (always present)
        console_handler = logging.StreamHandler(sys.stdout)
        console_level = logging.DEBUG if debug else logging.INFO
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (if log_file is provided)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)  # File gets all logs
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def update_log_level(self, debug: bool):
        """
        Update the log level for logger and console handler.
        
        Args:
            debug: If True, set log level to DEBUG. Otherwise, use INFO.
        """
        log_level = logging.DEBUG if debug else logging.INFO
        self.logger.setLevel(log_level)
        for handler in self.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(log_level)
    
    def add_file_handler(self, log_file: Path):
        """Add a file handler to the logger."""
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)
    
    def debug(self, message: str):
        """Log debug message."""
        self.logger.debug(message)
    
    def info(self, message: str):
        """Log info message."""
        self.logger.info(message)
    
    def warning(self, message: str):
        """Log warning message."""
        self.logger.warning(message)
    
    def error(self, message: str):
        """Log error message."""
        self.logger.error(message)
    
    def critical(self, message: str):
        """Log critical message."""
        self.logger.critical(message)


# Global logger instance (console only by default)
_logger_instance: Optional[MooseLogger] = None
_debug: bool = False

def set_global_debug(debug: bool):
    """
    Set the global debug flag.
    
    Args:
        debug: Debug flag value
    """
    global _debug
    _debug = debug
    # If logger is already initialized, update its log level
    if _logger_instance is not None:
        _logger_instance.update_log_level(debug)

def get_global_debug() -> bool:
    """
    Get the current global debug flag value.
    
    Returns:
        Current debug flag value
    """
    return _debug

def init_core_logger():
    """
    Initialize the core logger.
    """
    global _logger_instance
    _logger_instance = MooseLogger(name="moose", log_file=None, debug=_debug, label="[core]")
    return _logger_instance

def get_core_logger():
    """
    Get the core logger instance.
    """
    global _logger_instance
    if _logger_instance is None:
        init_core_logger()
    return _logger_instance

def update_core_logger(log_file=None, debug=None):
    """
    Update the core logger instance.
    """
    global _logger_instance
    if log_file:
        _logger_instance.add_file_handler(log_file)
    if debug:
        _logger_instance.update_log_level(debug)
    return _logger_instance

def get_logger(name: str, log_file: Optional[Path] = None, debug: bool = False, label: str = "[core]") -> MooseLogger:
    """
    Get or create the global Moose logger instance.
    
    Args:
        log_file: Optional path to log file. If provided, configures file logging.
                  This only takes effect on first call.
        debug: If True, set log level to DEBUG. Otherwise, use INFO.
        label: Label prefix for logs (e.g., "[core]", "[project:XXX]", "[agent:XXX]")
    
    Returns:
        MooseLogger instance
    """
    
    logger = MooseLogger(name, log_file, debug, label)
    return logger


def setup_project_logger(project_dir: Path, debug: bool = False, project_name: Optional[str] = None) -> MooseLogger:
    """
    Setup logger with file output for a project.
    
    Args:
        project_dir: Path to the project directory where log file will be created.
        debug: If True, set log level to DEBUG. Otherwise, use INFO.
        project_name: Name of the project. If not provided, will use the directory name.
    
    Returns:
        Configured MooseLogger instance
    """
    if project_name is None:
        project_name = project_dir.name
    label = f"[project:{project_name}]"
    log_file = project_dir / "moose.log"
    return get_logger(name="project", log_file=log_file, debug=debug, label=label)

