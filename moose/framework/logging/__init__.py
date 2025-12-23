"""Logging system for Moose Framework.

Provides hierarchical logging with proper caller file path tracking:
- moose (core logger)
  - moose.project.<project_id> (project logger)
    - moose.agent.<agent_name> (agent logger)
- moose.llm (LLM invocation logger)

All logs are written to:
- Console (streamed)
- projects/<project_id>/logs/moose.log (all execution logs)
- projects/<project_id>/logs/llm.log (LLM invocation logs only)
- projects/<project_id>/logs/agents/<agent_name>.log (agent-specific logs)

Environment Variables:
- MOOSE_PROJECTS_DIR: Override the base directory for projects (higher priority than local arguments)
"""

import logging
import sys
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from datetime import datetime


# Global state
_current_project_id: Optional[str] = None
_project_log_dir: Optional[Path] = None
_debug: bool = False
_initialized_loggers: Dict[str, 'MooseLogger'] = {}
_current_log_suffix: Optional[str] = None  # Suffix for log files in current run (e.g., ".1", ".2")


def _get_projects_base_dir(fallback_dir: Optional[Path] = None) -> Path:
    """Get the base directory for projects.
    
    Priority:
    1. MOOSE_PROJECTS_DIR environment variable
    2. fallback_dir argument
    3. Current working directory
    
    Args:
        fallback_dir: Fallback directory if env var is not set
        
    Returns:
        Base directory path for projects
    """
    env_dir = os.environ.get('MOOSE_PROJECTS_DIR')
    if env_dir:
        return Path(env_dir)
    if fallback_dir:
        return fallback_dir
    return Path.cwd()


def _get_unique_log_file(log_dir: Path, base_name: str) -> Path:
    """Get a unique log file path, creating incremented suffix if file exists.
    
    If the base log file exists (e.g., moose.log), finds the next available
    incremented name (moose.log.1, moose.log.2, etc.).
    
    Uses the global _current_log_suffix to ensure all log files from the same
    run use the same suffix.
    
    Args:
        log_dir: Directory for the log file
        base_name: Base name of the log file (e.g., "moose.log")
        
    Returns:
        Path to the unique log file
    """
    global _current_log_suffix
    
    base_path = log_dir / base_name
    
    if 'MOOSE_LOG_SUFFIX' in os.environ:
        _current_log_suffix = os.environ['MOOSE_LOG_SUFFIX']
    
    # If we already have a suffix for this run, use it
    if _current_log_suffix is not None:
        if _current_log_suffix == "":
            return base_path
        return log_dir / f"{base_name}{_current_log_suffix}"
    
    # First call - determine the suffix
    if not base_path.exists():
        # Base file doesn't exist, use it directly
        _current_log_suffix = ""
        return base_path
    
    # Find next available incremented name
    counter = 1
    while True:
        suffix = f".{counter}"
        candidate = log_dir / f"{base_name}{suffix}"
        if not candidate.exists():
            _current_log_suffix = suffix
            return candidate
        counter += 1


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
        self.datefmt = datefmt or '%Y-%m-%d %H:%M:%S'
        super().__init__(datefmt=self.datefmt)
    
    def format(self, record):
        """Format the log record with the label prefix."""
        # Create format with label before pathname
        fmt = f'%(asctime)s | {self.label} %(pathname)s:%(lineno)d | %(levelname)-8s | %(message)s'
        formatter = logging.Formatter(fmt=fmt, datefmt=self.datefmt)
        return formatter.format(record)


class MooseLogger:
    """Logger for Moose Framework with console and optional file output.
    
    Provides wrapper methods with proper stacklevel so that logged file paths
    show the actual caller instead of this logging module.
    """
    
    def __init__(
        self, 
        name: str = "moose", 
        log_file: Optional[Path] = None, 
        debug: bool = False, 
        label: str = "[core]",
        format: str = None,
        propagate: bool = True
    ):
        """
        Initialize the Moose logger.
        
        Args:
            name: Logger name (supports hierarchy like "moose.project.default")
            log_file: Optional path to log file. If provided, logs will be written to file.
            debug: If True, set log level to DEBUG. Otherwise, use INFO.
            label: Label prefix for logs (e.g., "[core]", "[project:XXX]", "[agent:XXX]")
            propagate: Whether to propagate logs to parent logger (default True)
        """
        self.label = label
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.propagate = propagate
        
        # Set logger level based on debug flag
        logger_level = logging.DEBUG if debug else logging.INFO
        self.logger.setLevel(logger_level)
        
        # Store formatter for later use
        if format:
            self.formatter = logging.Formatter(format)
        else:
            self.formatter = LabeledFormatter(label=label, datefmt='%Y-%m-%d %H:%M:%S')
        
        # Track our own handlers to avoid duplicates
        self._console_handler: Optional[logging.StreamHandler] = None
        self._file_handlers: List[logging.FileHandler] = []
        
        # Check if handlers already exist
        if not self._has_console_handler():
            self._add_console_handler(debug)
        
        # Add file handler if specified
        if log_file:
            self.add_file_handler(log_file)
    
    def _has_console_handler(self) -> bool:
        """Check if a console handler already exists."""
        for handler in self.logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                return True
        return False
    
    def _add_console_handler(self, debug: bool):
        """Add console handler to the logger."""
        console_handler = logging.StreamHandler(sys.stdout)
        console_level = logging.DEBUG if debug else logging.INFO
        console_handler.setLevel(console_level)
        console_handler.setFormatter(self.formatter)
        self.logger.addHandler(console_handler)
        self._console_handler = console_handler
    
    def add_file_handler(self, log_file: Path, formatter: Optional[logging.Formatter] = None):
        """Add a file handler to the logger.
        
        Args:
            log_file: Path to the log file
            formatter: Optional custom formatter (uses default if not provided)
        """
        # Check if this file handler already exists
        for handler in self._file_handlers:
            if hasattr(handler, 'baseFilename') and Path(handler.baseFilename) == log_file.resolve():
                return  # Already exists
        
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # File gets all logs
        file_handler.setFormatter(formatter or self.formatter)
        self.logger.addHandler(file_handler)
        self._file_handlers.append(file_handler)
    
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
    
    def update_label(self, label: str):
        """Update the label for all formatters."""
        self.label = label
        self.formatter = LabeledFormatter(label=label, datefmt='%Y-%m-%d %H:%M:%S')
        for handler in self.logger.handlers:
            if isinstance(handler.formatter, LabeledFormatter):
                handler.setFormatter(self.formatter)
    
    # Wrapper methods with proper stacklevel to show actual caller file path
    def debug(self, msg, *args, **kwargs):
        """Log debug message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        """Log info message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.info(msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        """Log warning message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        """Log error message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        """Log critical message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.critical(msg, *args, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        """Log exception message with correct caller file path."""
        kwargs.setdefault('stacklevel', 2)
        self.logger.exception(msg, *args, **kwargs)


class LLMLogger:
    """Specialized logger for LLM invocations.
    
    Logs complete message objects (SystemMessage, AIMessage, HumanMessage, ToolMessage)
    to both moose.log and a dedicated llm.log file.
    """
    
    def __init__(self, project_id: Optional[str] = None, debug: bool = False):
        """
        Initialize the LLM logger.
        
        Args:
            project_id: Project ID for log file location
            debug: Enable debug logging
        """
        self.project_id = project_id or _current_project_id or "default"
        self.debug = debug
        
        # Create the underlying logger
        self.logger = MooseLogger(
            name="moose.llm",
            debug=debug,
            label="[llm]",
            format='%(message)s',
            propagate=True  # Propagate to parent (moose) for moose.log
        )
        
        # Add dedicated LLM log file if project is set (using unique file name)
        if _project_log_dir:
            llm_log_file = _get_unique_log_file(_project_log_dir, "llm.log")
            self.logger.add_file_handler(llm_log_file)
            # Store the log file path for _write_to_llm_log
            self._llm_log_file = llm_log_file
        else:
            self._llm_log_file = None
    
    def _serialize_message(self, msg: Any) -> Dict[str, Any]:
        """Serialize a LangChain message object to a dictionary.
        
        Args:
            msg: A LangChain message object (SystemMessage, HumanMessage, AIMessage, ToolMessage)
                 or a dict representation
            
        Returns:
            Dictionary representation of the message
        """
        # If already a dict, return it directly (e.g., for streamed responses)
        if isinstance(msg, dict):
            return msg
        
        # Get the class name for type identification
        msg_type = type(msg).__name__
        
        result = {
            "type": msg_type,
        }
        
        # Extract common fields
        if hasattr(msg, 'content'):
            result["content"] = msg.content

        # Include name when present (useful for ToolMessage display)
        if hasattr(msg, 'name') and getattr(msg, 'name', None):
            result["name"] = getattr(msg, 'name', None)
        
        # Handle tool calls for AIMessage
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            result["tool_calls"] = []
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    result["tool_calls"].append(tc)
                else:
                    result["tool_calls"].append({
                        'id': getattr(tc, 'id', None),
                        'name': getattr(tc, 'name', None),
                        'args': dict(getattr(tc, 'args', {})) if hasattr(tc, 'args') else {}
                    })
        
        # Handle tool_call_id for ToolMessage
        if hasattr(msg, 'tool_call_id') and msg.tool_call_id:
            result["tool_call_id"] = msg.tool_call_id
        
        # Include additional_kwargs if present
        if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
            result["additional_kwargs"] = msg.additional_kwargs
        
        # Include response_metadata if present (for AIMessage responses)
        if hasattr(msg, 'response_metadata') and msg.response_metadata:
            result["response_metadata"] = msg.response_metadata
        
        # Include usage_metadata if present
        if hasattr(msg, 'usage_metadata') and msg.usage_metadata:
            result["usage_metadata"] = dict(msg.usage_metadata) if hasattr(msg.usage_metadata, '__dict__') else msg.usage_metadata
        
        # Include cost if present
        if hasattr(msg, 'cost') and msg.cost:
            result["cost"] = msg.cost
        
        return result
    
    def _serialize_messages(self, messages: List[Any]) -> List[Dict[str, Any]]:
        """Serialize a list of messages."""
        return [self._serialize_message(msg) for msg in messages]
    
    def log_message(
        self,
        message: Any,
        direction: str,
        request_id: str,
        model: str,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[float] = None,
        **kwargs
    ):
        """Log a single message (request or response).
        
        Args:
            message: Single LangChain message object
            direction: 'request' or 'response'
            request_id: Unique request identifier
            model: Model name being used
            usage: Token usage dictionary (for responses)
            cost: Cost in USD (for responses)
            **kwargs: Additional metadata
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "direction": direction,
            "model": model,
            "message": self._serialize_message(message),
        }
        
        if usage:
            log_entry["usage"] = usage
        
        if cost is not None:
            log_entry["cost"] = cost
        
        if kwargs:
            log_entry["metadata"] = kwargs
        
        # Log as JSON for structured logging
        self.logger.debug(json.dumps(log_entry, default=str))
        
        # Also write to dedicated LLM log file in JSON format
        self._write_to_llm_log(log_entry)
    
    def log_tool_result(
        self,
        tool_message: Any,
        request_id: str,
        model: str,
        tool_name: Optional[str] = None,
        **kwargs
    ):
        """Log a tool call result (ToolMessage).
        
        Args:
            tool_message: ToolMessage object with the result
            request_id: Unique request identifier
            model: Model name being used
            tool_name: Name of the tool that was called
            **kwargs: Additional metadata
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id,
            "direction": "tool_result",
            "model": model,
            "message": self._serialize_message(tool_message),
        }
        
        if tool_name:
            log_entry["tool_name"] = tool_name
        
        if kwargs:
            log_entry["metadata"] = kwargs
        
        # Log as JSON for structured logging
        self.logger.debug(json.dumps(log_entry, default=str))
        
        # Also write to dedicated LLM log file in JSON format
        self._write_to_llm_log(log_entry)
    
    # Keep old methods for backward compatibility
    def log_request(
        self, 
        messages: List[Any], 
        request_id: str,
        model: str,
        **kwargs
    ):
        """Log an LLM request - only logs NEW messages, not historical ones.
        
        This method intelligently detects what's new:
        - If last message is HumanMessage: log it as a request
        - If last message is ToolMessage: log each trailing ToolMessage as tool_result
        - If last message is HumanMessage and it is preceded by ToolMessages (common in multi-stage tool loops),
          log those ToolMessages as tool_result first, then log the HumanMessage.
        - SystemMessage at start is NOT logged (assumed to be static)
        """
        if not messages:
            return

        def _infer_tool_name(tool_call_id: str) -> Optional[str]:
            """Infer tool name by scanning previous AIMessage tool calls.

            Different providers / LangChain versions may represent tool call IDs with different keys:
            - dict: {"id": "..."} (common), or {"tool_use_id": "..."} (Anthropic-ish), etc.
            """
            if not tool_call_id:
                return None

            def _tc_id(tc: Any) -> Optional[str]:
                if isinstance(tc, dict):
                    return tc.get("id") or tc.get("tool_use_id") or tc.get("tool_call_id")
                return (
                    getattr(tc, "id", None)
                    or getattr(tc, "tool_use_id", None)
                    or getattr(tc, "tool_call_id", None)
                )

            def _tc_name(tc: Any) -> Optional[str]:
                if isinstance(tc, dict):
                    return tc.get("name") or tc.get("tool_name")
                return getattr(tc, "name", None) or getattr(tc, "tool_name", None)

            # Walk backwards; first match wins.
            for msg in reversed(messages):
                if type(msg).__name__ != 'AIMessage':
                    continue

                tool_calls = getattr(msg, 'tool_calls', None) or []

                # Some providers tuck tool call info into additional_kwargs
                if not tool_calls:
                    ak = getattr(msg, "additional_kwargs", None) or {}
                    tool_calls = ak.get("tool_calls") or ak.get("tool_uses") or []

                for tc in tool_calls:
                    if _tc_id(tc) == tool_call_id:
                        return _tc_name(tc)
            return None
        
        last_msg = messages[-1]
        msg_type = type(last_msg).__name__
        
        # Common in multi-stage tool loops: ... ToolMessage(s) then a HumanMessage continuation prompt.
        # In that case, the tool results are "new" too and should be logged.
        if msg_type == 'HumanMessage' and len(messages) >= 2 and type(messages[-2]).__name__ == 'ToolMessage':
            tool_messages = []
            for msg in reversed(messages[:-1]):  # exclude the last HumanMessage
                if type(msg).__name__ == 'ToolMessage':
                    tool_messages.insert(0, msg)
                else:
                    break
            for tool_msg in tool_messages:
                tool_name = None
                if hasattr(tool_msg, 'name'):
                    tool_name = tool_msg.name
                elif hasattr(tool_msg, 'tool_call_id'):
                    # Never treat a tool_call_id as a tool name; only infer if possible.
                    tool_name = _infer_tool_name(tool_msg.tool_call_id)
                self.log_tool_result(
                    tool_message=tool_msg,
                    request_id=request_id,
                    model=model,
                    tool_name=tool_name,
                    **kwargs
                )
            # Finally log the user's continuation prompt as the request
            self.log_message(
                message=last_msg,
                direction="request",
                request_id=request_id,
                model=model,
                **kwargs
            )
            return

        if msg_type == 'ToolMessage':
            # Find all consecutive ToolMessages at the end
            tool_messages = []
            for msg in reversed(messages):
                if type(msg).__name__ == 'ToolMessage':
                    tool_messages.insert(0, msg)
                else:
                    break
            
            # Log each tool result
            for tool_msg in tool_messages:
                tool_name = None
                if hasattr(tool_msg, 'name'):
                    tool_name = tool_msg.name
                elif hasattr(tool_msg, 'tool_call_id'):
                    # Never treat a tool_call_id as a tool name; only infer if possible.
                    tool_name = _infer_tool_name(tool_msg.tool_call_id)
                
                self.log_tool_result(
                    tool_message=tool_msg,
                    request_id=request_id,
                    model=model,
                    tool_name=tool_name,
                    **kwargs
                )
        elif msg_type in ('HumanMessage', 'SystemMessage'):
            # Log the user's message as request
            self.log_message(
                message=last_msg,
                direction="request",
                request_id=request_id,
                model=model,
                **kwargs
            )
        # AIMessage as last message is unusual for a request - skip it
        # (it would be from history)
    
    def log_response(
        self,
        response: Any,
        request_id: str,
        model: str,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[float] = None,
        **kwargs
    ):
        """Log an LLM response.
        
        Args:
            response: LangChain response object (AIMessage or similar)
            request_id: Unique request identifier
            model: Model name used
            usage: Token usage dictionary
            cost: Cost in USD
            **kwargs: Additional metadata
        """
        self.log_message(
            message=response,
            direction="response",
            request_id=request_id,
            model=model,
            usage=usage,
            cost=cost,
            **kwargs
        )
    
    def _write_to_llm_log(self, entry: Dict[str, Any]):
        """Write a structured entry directly to the LLM log file."""
        # Use stored log file path, or get unique one if not set
        llm_log_file = self._llm_log_file
        if llm_log_file is None and _project_log_dir:
            llm_log_file = _get_unique_log_file(_project_log_dir, "llm.log")
            self._llm_log_file = llm_log_file
        
        if llm_log_file:
            try:
                llm_log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(llm_log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, default=str) + '\n')
            except Exception as e:
                self.logger.error(f"Failed to write to LLM log: {e}")

        # Note: Real-time streaming is handled by ChatManager's file tailing
        # No need to forward here - the log file is the single source of truth

    def _legacy_forward_to_chat_manager(self, entry: Dict[str, Any]):
        """Forward LLM log entry to ChatManager for real-time web UI streaming.
        
        Args:
            entry: LLM log entry dict
        """
        try:
            # Lazy import to avoid circular imports
            try:
                from moose.web_ui import get_chat_manager
            except ImportError:
                try:
                    from web_ui import get_chat_manager
                except ImportError:
                    return  # Web UI not available
            
            project_id = self.project_id or _current_project_id
            if not project_id:
                return
            
            chat_manager = get_chat_manager()
            
            # Convert entry to chat message
            direction = entry.get('direction', '')
            timestamp = entry.get('timestamp', datetime.now().isoformat())
            request_id = entry.get('request_id', '')
            
            # New format: single 'message' field
            message = entry.get('message')
            if message:
                chat_msg = self._convert_to_chat_message(message, timestamp, request_id)
                if chat_msg:
                    # Add direction info
                    chat_msg['direction'] = direction
                    
                    # Add usage and cost info for responses
                    if entry.get('usage'):
                        chat_msg['usage'] = entry['usage']
                    if entry.get('cost'):
                        chat_msg['cost'] = entry['cost']
                    
                    # Add tool name for tool results
                    if entry.get('tool_name'):
                        chat_msg['tool_name'] = entry['tool_name']
                    
                    chat_manager.add_message(project_id, chat_msg)
        
        except Exception:
            # Don't let chat forwarding errors break logging
            pass
    
    def _convert_to_chat_message(self, msg: Dict[str, Any], timestamp: str, request_id: str) -> Optional[Dict[str, Any]]:
        """Convert a serialized message to chat format.
        
        Args:
            msg: Serialized message dict
            timestamp: Timestamp string
            request_id: Request ID
            
        Returns:
            Chat message dict or None
        """
        if not msg:
            return None
        
        msg_type = msg.get('type', 'unknown')
        content = msg.get('content', '')
        
        # Map message types
        type_map = {
            'SystemMessage': 'system',
            'HumanMessage': 'human',
            'AIMessage': 'ai',
            'ToolMessage': 'tool'
        }
        
        chat_type = type_map.get(msg_type, msg_type.lower() if isinstance(msg_type, str) else 'unknown')
        
        result = {
            'id': f"{request_id}_{timestamp}_{chat_type}_{id(msg)}",
            'type': chat_type,
            'content': content,
            'timestamp': timestamp,
            'request_id': request_id
        }
        
        # Include tool calls if present (for AI messages)
        if msg.get('tool_calls'):
            result['tool_calls'] = msg['tool_calls']
        
        # Include tool_call_id if present (for Tool messages)
        if msg.get('tool_call_id'):
            result['tool_call_id'] = msg['tool_call_id']
        
        return result


# =============================================================================
# Global State Management
# =============================================================================

def set_global_debug(debug: bool):
    """Set the global debug flag.
    
    Args:
        debug: Debug flag value
    """
    global _debug
    _debug = debug
    
    # Update all initialized loggers
    for logger in _initialized_loggers.values():
        logger.update_log_level(debug)


def get_global_debug() -> bool:
    """Get the current global debug flag value."""
    return _debug


def set_project(project_id: str, base_dir: Optional[Path] = None):
    """Set the current project and initialize logging directories.
    
    The base directory is determined by:
    1. MOOSE_PROJECTS_DIR environment variable (highest priority)
    2. base_dir argument
    3. Current working directory (fallback)
    
    Args:
        project_id: Project identifier
        base_dir: Base directory for projects (overridden by MOOSE_PROJECTS_DIR env var)
    """
    global _current_project_id, _project_log_dir, _current_log_suffix
    
    _current_project_id = project_id
    
    # Reset log suffix for new project setup
    _current_log_suffix = None
    
    # Get base directory (env var takes priority)
    effective_base_dir = _get_projects_base_dir(base_dir)
    
    _project_log_dir = effective_base_dir / project_id / "logs"
    _project_log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create agents log subdirectory
    agents_log_dir = _project_log_dir / "agents"
    agents_log_dir.mkdir(parents=True, exist_ok=True)
    
    # Update core logger with file handler (using unique file name)
    core_logger = get_core_logger()
    moose_log_file = _get_unique_log_file(_project_log_dir, "moose.log")
    core_logger.add_file_handler(moose_log_file)


def get_project_id() -> Optional[str]:
    """Get the current project ID."""
    return _current_project_id


def get_project_log_dir() -> Optional[Path]:
    """Get the current project log directory."""
    return _project_log_dir


# =============================================================================
# Core Logger (moose)
# =============================================================================

_core_logger: Optional[MooseLogger] = None


def init_core_logger():
    """Initialize the core logger."""
    global _core_logger
    
    _core_logger = MooseLogger(
        name="moose",
        debug=_debug,
        label="[core]",
        propagate=False  # Root logger doesn't propagate
    )
    _initialized_loggers["moose"] = _core_logger
    
    return _core_logger


def get_core_logger() -> MooseLogger:
    """Get the core logger instance."""
    global _core_logger
    
    if _core_logger is None:
        init_core_logger()
    
    return _core_logger


# =============================================================================
# Project Logger (moose.project.<project_id>)
# =============================================================================

def get_project_logger(project_id: Optional[str] = None, debug: Optional[bool] = None) -> MooseLogger:
    """Get or create a project logger.
    
    Args:
        project_id: Project identifier (uses current project if not specified)
        debug: Debug flag (uses global if not specified)
        
    Returns:
        Project logger instance
    """
    project_id = project_id or _current_project_id or "default"
    logger_name = f"moose.project.{project_id}"
    
    # Return existing logger if available
    if logger_name in _initialized_loggers:
        return _initialized_loggers[logger_name]
    
    # Ensure core logger exists (parent)
    get_core_logger()
    
    # Create project logger
    project_logger = MooseLogger(
        name=logger_name,
        debug=debug if debug is not None else _debug,
        label=f"[project:{project_id}]",
        propagate=True  # Propagate to core logger
    )
    
    _initialized_loggers[logger_name] = project_logger
    return project_logger


# =============================================================================
# Agent Logger (moose.project.<project_id>.agent.<agent_name>)
# =============================================================================

def get_agent_logger(
    agent_name: str,
    project_id: Optional[str] = None,
    debug: Optional[bool] = None
) -> MooseLogger:
    """Get or create an agent logger.
    
    Agent logs are written to:
    1. Console (streamed)
    2. moose.log (via propagation)
    3. agents/<agent_name>.log (dedicated agent log)
    
    Args:
        agent_name: Name of the agent
        project_id: Project identifier (uses current project if not specified)
        debug: Debug flag (uses global if not specified)
        
    Returns:
        Agent logger instance
    """
    project_id = project_id or _current_project_id or "default"
    logger_name = f"moose.project.{project_id}.agent.{agent_name}"
    
    # Return existing logger if available
    if logger_name in _initialized_loggers:
        return _initialized_loggers[logger_name]
    
    # Ensure project logger exists (parent)
    get_project_logger(project_id, debug)
    
    # Create agent logger
    agent_logger = MooseLogger(
        name=logger_name,
        debug=debug if debug is not None else _debug,
        label=f"[agent:{agent_name}]",
        propagate=True  # Propagate to project logger -> core logger
    )
    
    # Add dedicated agent log file (using unique file name based on global suffix)
    if _project_log_dir:
        agents_dir = _project_log_dir / "agents"
        agent_log_file = _get_unique_log_file(agents_dir, f"{agent_name}.log")
        agent_logger.add_file_handler(agent_log_file)
    
    _initialized_loggers[logger_name] = agent_logger
    return agent_logger


# =============================================================================
# LLM Logger
# =============================================================================

_llm_logger: Optional[LLMLogger] = None


def get_llm_logger(project_id: Optional[str] = None, debug: Optional[bool] = None) -> LLMLogger:
    """Get or create the LLM logger.
    
    Args:
        project_id: Project identifier (uses current project if not specified)
        debug: Debug flag (uses global if not specified)
        
    Returns:
        LLM logger instance
    """
    global _llm_logger
    
    if _llm_logger is None:
        _llm_logger = LLMLogger(
            project_id=project_id or _current_project_id,
            debug=debug if debug is not None else _debug
        )
    
    return _llm_logger


def reinit_llm_logger():
    """Reinitialize the LLM logger (call after setting project)."""
    global _llm_logger
    _llm_logger = None
    return get_llm_logger()


# =============================================================================
# Web UI Integration
# =============================================================================

class WebUILogHandler(logging.Handler):
    """Handler that forwards log records to the Web UI LogManager.

    Note: This handler is kept for backward compatibility but is now mostly a no-op.
    Real-time log streaming is handled by LogManager's file tailing mechanism,
    which reads from the moose.log file directly. This works across process
    boundaries (e.g., when agents run in Docker containers).
    """
    
    def __init__(self, project_id: Optional[str] = None):
        """Initialize the handler.
        
        Args:
            project_id: Project ID for log routing (uses current project if not specified)
        """
        super().__init__()
        self._project_id = project_id
    
    def emit(self, record):
        """Forward log record to LogManager.
        
        Args:
            record: The log record to forward
        """
        try:
            # Lazy import to avoid circular imports
            from moose.web_ui import get_log_manager
        except ImportError:
            try:
                from web_ui import get_log_manager
            except ImportError:
                return  # Web UI not available
        
        project_id = self._project_id or _current_project_id
        if not project_id:
            return
        
        try:
            log_manager = get_log_manager()
            
            # Format the log entry
            entry = {
                'time': self.format_time(record),
                'level': record.levelname,
                'message': record.getMessage(),
                'path': record.pathname,
                'line': record.lineno,
                'label': getattr(record, 'label', '[core]')
            }
            
            log_manager.add_log(project_id, entry)
        except Exception:
            # Don't let logging errors break the application
            pass
    
    def format_time(self, record) -> str:
        """Format the timestamp for the record.
        
        Args:
            record: The log record
            
        Returns:
            Formatted timestamp string
        """
        from datetime import datetime
        return datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')


# Global WebUI handler
_webui_handler: Optional[WebUILogHandler] = None


def enable_webui_logging(project_id: Optional[str] = None):
    """Enable log forwarding to the Web UI.
    
    This adds a WebUILogHandler to the core logger, enabling real-time
    log streaming to the web dashboard.
    
    Args:
        project_id: Project ID for log routing (uses current project if not specified)
    """
    global _webui_handler
    
    if _webui_handler is not None:
        return  # Already enabled
    
    _webui_handler = WebUILogHandler(project_id)
    _webui_handler.setLevel(logging.DEBUG)  # Capture all logs
    
    # Add to core logger
    core_logger = get_core_logger()
    core_logger.logger.addHandler(_webui_handler)


def disable_webui_logging():
    """Disable log forwarding to the Web UI."""
    global _webui_handler
    
    if _webui_handler is None:
        return
    
    # Remove from core logger
    core_logger = get_core_logger()
    core_logger.logger.removeHandler(_webui_handler)
    
    _webui_handler = None


