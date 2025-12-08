"""Base agent class with common functionality for all Moose agents."""

import json
import sys
import signal
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union
from abc import abstractmethod
from framework.logging import get_logger

try:
    from flask import Flask, request, jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    Flask = None


class BaseAgent():
    """
    Base class for all Moose agents.
    
    Provides common functionality:
    - Configuration loading
    - Multiple communication modes (HTTP, stdin/stdout, file-based)
    - Polling loop to keep container alive
    - Graceful shutdown handling
    - Standardized input/output formatting with token cost tracking
    """
    
    def __init__(self, config_path: Optional[Path] = None, debug: Optional[bool] = False):
        """
        Initialize the base agent.
        
        Args:
            config_path: Path to agent_config.json. If None, looks for it in current directory.
            debug: Enable debug logging
        """
        self.config_path = config_path or Path("agent_config.json")
        self.config = self.load_config()
        
        # Agent metadata from config
        self.name = self.config.get("name", "unknown_agent")
        self.description = self.config.get("description", "")
        
        self.logger = get_logger(name=self.name, label=f"[agent:{self.name}]", debug=debug)
        
        # Communication mode
        self.running = False
        self.shutdown_requested = False
        
        # HTTP server (if using Flask)
        self.app = None
        self.http_server = None
        
        # Token cost tracking
        self._current_token_cost = None
        self._current_model_used = None
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        self.logger.info(f"Initialized agent: {self.name} - {self.description}")
    
    def load_config(self) -> Dict[str, Any]:
        """
        Load agent configuration from agent_config.json.
        
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Agent config not found: {self.config_path}. "
                f"Each agent must have an agent_config.json file."
            )
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            self.logger.debug(f"Loaded config from {self.config_path}")
            return config
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in agent config {self.config_path}: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load agent config: {e}")
    
    def _format_input(self, raw_input: Any) -> Dict[str, Any]:
        """
        Format and validate input according to standard format.
        
        Args:
            raw_input: Raw input data (can be dict, string, or other)
        
        Returns:
            Formatted input dictionary
        """
        # If already in standard format, validate and return
        if isinstance(raw_input, dict):
            # Check if it's already in standard format
            if "input" in raw_input or "request_id" in raw_input:
                # Validate required fields
                request_id = raw_input.get("request_id") or str(uuid.uuid4())
                input_data = raw_input.get("input", raw_input)
                metadata = raw_input.get("metadata", {})
                
                return {
                    "request_id": request_id,
                    "agent_name": raw_input.get("agent_name", self.name),
                    "input": input_data,
                    "metadata": {
                        "timestamp": metadata.get("timestamp", datetime.now().isoformat()),
                        "source": metadata.get("source", "unknown"),
                        "user_id": metadata.get("user_id"),
                        **{k: v for k, v in metadata.items() if k not in ["timestamp", "source", "user_id"]}
                    }
                }
            else:
                # Not in standard format, wrap it
                return {
                    "request_id": str(uuid.uuid4()),
                    "agent_name": self.name,
                    "input": raw_input,
                    "metadata": {
                        "timestamp": datetime.now().isoformat(),
                        "source": "direct"
                    }
                }
        else:
            # Not a dict, wrap it
            return {
                "request_id": str(uuid.uuid4()),
                "agent_name": self.name,
                "input": raw_input,
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "source": "direct"
                }
            }
    
    def _format_output(
        self,
        result: Any,
        request_id: str,
        status: str = "success",
        error: Optional[str] = None,
        token_cost: Optional[Dict[str, Any]] = None,
        model_used: Optional[str] = None,
        processing_time_ms: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Format output according to standard format.
        
        Args:
            result: Agent's processing result
            request_id: Request ID from input
            status: Status ("success" or "error")
            error: Error message if status is "error"
            token_cost: Token cost information (from LLM calls)
            model_used: Model name used (if LLM was called)
            processing_time_ms: Processing time in milliseconds
        
        Returns:
            Formatted output dictionary
        """
        output = {
            "request_id": request_id,
            "agent_name": self.name,
            "status": status,
            "timestamp": datetime.now().isoformat()
        }
        
        if status == "error":
            output["error"] = error or "Unknown error"
            if result:
                output["partial_result"] = result
        else:
            output["result"] = result
        
        # Add token cost information
        if token_cost:
            output["token_cost"] = {
                "prompt_tokens": token_cost.get("prompt_tokens", 0),
                "completion_tokens": token_cost.get("completion_tokens", 0),
                "total_tokens": token_cost.get("total_tokens", 0),
                "cost_usd": token_cost.get("cost", 0.0) or token_cost.get("cost_usd", 0.0)
            }
        else:
            output["token_cost"] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0
            }
        
        # Add model information
        if model_used:
            output["model_used"] = model_used
        
        # Add processing time
        if processing_time_ms is not None:
            output["processing_time_ms"] = processing_time_ms
        
        # Add metadata
        output["metadata"] = {}
        
        return output
    
    def _extract_token_cost_from_result(self, result: Any):
        """
        Extract token cost and model information from agent result.
        
        This method looks for common patterns in agent results that contain
        LLM usage information.
        
        Args:
            result: Agent's result (may contain usage/cost info)
        
        Returns:
            Tuple of (token_cost_dict, model_name) or (None, None)
        """
        if not isinstance(result, dict):
            return None, None
        
        # Check for usage information in various formats
        usage = result.get("usage") or result.get("token_usage") or result.get("tokens")
        cost = result.get("cost") or result.get("cost_usd")
        model = result.get("model") or result.get("model_used")
        
        # Also check nested structures
        if "analysis" in result and isinstance(result["analysis"], dict):
            usage = usage or result["analysis"].get("usage")
            cost = cost or result["analysis"].get("cost")
            model = model or result["analysis"].get("model")
        
        if usage or cost:
            token_cost = {
                "prompt_tokens": usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0,
                "completion_tokens": usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0,
                "total_tokens": usage.get("total_tokens", 0) if isinstance(usage, dict) else 0,
                "cost": cost or 0.0,
                "cost_usd": cost or 0.0
            }
            return token_cost, model
        
        return None, None
    
    @abstractmethod
    def process(self, input_data: Any) -> Any:
        """
        Process input data and return result.
        
        This is the main method that each agent must implement.
        
        Args:
            input_data: Input data (format depends on agent implementation)
            
        Returns:
            Processing result (format depends on agent implementation)
        """
        pass
    
    def _process_with_formatting(self, formatted_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Internal method that processes formatted input and returns formatted output.
        
        Args:
            formatted_input: Input in standard format
        
        Returns:
            Output in standard format
        """
        request_id = formatted_input["request_id"]
        input_data = formatted_input["input"]
        start_time = time.time()
        
        # Reset token tracking
        self._current_token_cost = None
        self._current_model_used = None
        
        try:
            # Process the input
            result = self.process(input_data)
            
            # Calculate processing time
            processing_time_ms = (time.time() - start_time) * 1000
            
            # Try to extract token cost from result
            token_cost, model_used = self._extract_token_cost_from_result(result)
            
            # Use tracked values if available
            if self._current_token_cost:
                token_cost = self._current_token_cost
            if self._current_model_used:
                model_used = self._current_model_used
            
            # Format output
            return self._format_output(
                result=result,
                request_id=request_id,
                status="success",
                token_cost=token_cost,
                model_used=model_used,
                processing_time_ms=processing_time_ms
            )
            
        except Exception as e:
            self.logger.error(f"Error processing request {request_id}: {e}", exc_info=True)
            processing_time_ms = (time.time() - start_time) * 1000
            
            return self._format_output(
                result=None,
                request_id=request_id,
                status="error",
                error=str(e),
                processing_time_ms=processing_time_ms
            )
    
    def run_http_server(self, port: int = 8000, host: str = "0.0.0.0"):
        """
        Run agent as HTTP server.
        
        Args:
            port: Port to listen on
            host: Host to bind to
        """
        if not FLASK_AVAILABLE:
            raise ImportError(
                "Flask is required for HTTP mode. Install with: pip install flask"
            )
        
        self.app = Flask(__name__)
        self.running = True
        
        @self.app.route('/health', methods=['GET'])
        def health():
            """Health check endpoint."""
            return jsonify({
                "status": "healthy",
                "agent": self.name,
                "description": self.description
            })
        
        @self.app.route('/process', methods=['POST'])
        def process_endpoint():
            """Process input data."""
            try:
                raw_data = request.get_json()
                if raw_data is None:
                    return jsonify({
                        "status": "error",
                        "error": "No JSON data provided"
                    }), 400
                
                # Format input and process
                formatted_input = self._format_input(raw_data)
                formatted_output = self._process_with_formatting(formatted_input)
                
                return jsonify(formatted_output)
            except Exception as e:
                self.logger.error(f"Error processing request: {e}", exc_info=True)
                return jsonify({
                    "status": "error",
                    "error": str(e)
                }), 500
        
        @self.app.route('/process', methods=['GET'])
        def process_get():
            """Process input from query parameters or body."""
            raw_data = request.args.to_dict()
            if not raw_data:
                raw_data = request.get_json() or {}
            
            try:
                # Format input and process
                formatted_input = self._format_input(raw_data)
                formatted_output = self._process_with_formatting(formatted_input)
                
                return jsonify(formatted_output)
            except Exception as e:
                self.logger.error(f"Error processing request: {e}", exc_info=True)
                return jsonify({
                    "status": "error",
                    "error": str(e)
                }), 500
        
        self.logger.info(f"Starting HTTP server on {host}:{port}")
        try:
            self.app.run(host=host, port=port, debug=False, use_reloader=False)
        except KeyboardInterrupt:
            self.logger.info("HTTP server stopped by user")
        finally:
            self.running = False
    
    def run_stdin_mode(self):
        """
        Run agent in stdin/stdout mode.
        
        Reads JSON from stdin, processes it, and writes JSON result to stdout.
        """
        self.running = True
        self.logger.info("Starting stdin/stdout mode")
        
        try:
            while self.running and not self.shutdown_requested:
                # Read input from stdin
                line = sys.stdin.readline()
                if not line:
                    # EOF or empty line - wait a bit and continue
                    time.sleep(0.1)
                    continue
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    # Parse JSON input
                    raw_input = json.loads(line)
                    
                    # Format input and process
                    formatted_input = self._format_input(raw_input)
                    formatted_output = self._process_with_formatting(formatted_input)
                    
                    # Write JSON result to stdout
                    output = json.dumps(formatted_output)
                    print(output, flush=True)
                    
                except json.JSONDecodeError as e:
                    error_output = self._format_output(
                        result=None,
                        request_id=str(uuid.uuid4()),
                        status="error",
                        error=f"Invalid JSON: {e}"
                    )
                    print(json.dumps(error_output), flush=True)
                except Exception as e:
                    self.logger.error(f"Error processing input: {e}", exc_info=True)
                    error_output = self._format_output(
                        result=None,
                        request_id=str(uuid.uuid4()),
                        status="error",
                        error=str(e)
                    )
                    print(json.dumps(error_output), flush=True)
                    
        except KeyboardInterrupt:
            self.logger.info("Stdin mode stopped by user")
        finally:
            self.running = False
    
    def run_file_watch_mode(self, watch_dir: Path, poll_interval: float = 1.0):
        """
        Run agent in file-watch mode.
        
        Watches a directory for input files, processes them, and writes results.
        
        Args:
            watch_dir: Directory to watch for input files
            poll_interval: How often to check for new files (seconds)
        """
        self.running = True
        watch_dir = Path(watch_dir)
        watch_dir.mkdir(parents=True, exist_ok=True)
        
        input_dir = watch_dir / "input"
        output_dir = watch_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger.info(f"Starting file-watch mode, watching {input_dir}")
        
        processed_files = set()
        
        try:
            while self.running and not self.shutdown_requested:
                # Check for new files in input directory
                for input_file in input_dir.glob("*"):
                    if input_file.is_file() and input_file.name not in processed_files:
                        try:
                            # Read input file
                            with open(input_file, 'r', encoding='utf-8') as f:
                                if input_file.suffix == '.json':
                                    raw_input = json.load(f)
                                else:
                                    raw_input = f.read()
                            
                            # Format input and process
                            formatted_input = self._format_input(raw_input)
                            formatted_output = self._process_with_formatting(formatted_input)
                            
                            # Write result to output directory
                            output_file = output_dir / f"{input_file.stem}_result.json"
                            with open(output_file, 'w', encoding='utf-8') as f:
                                json.dump(formatted_output, f, indent=2)
                            
                            # Mark as processed
                            processed_files.add(input_file.name)
                            self.logger.debug(f"Processed {input_file.name}")
                            
                        except Exception as e:
                            self.logger.error(
                                f"Error processing file {input_file.name}: {e}",
                                exc_info=True
                            )
                            # Write error to output
                            error_file = output_dir / f"{input_file.stem}_error.json"
                            error_output = self._format_output(
                                result=None,
                                request_id=str(uuid.uuid4()),
                                status="error",
                                error=str(e)
                            )
                            with open(error_file, 'w', encoding='utf-8') as f:
                                json.dump(error_output, f, indent=2)
                
                # Sleep before next poll
                time.sleep(poll_interval)
                
        except KeyboardInterrupt:
            self.logger.info("File-watch mode stopped by user")
        finally:
            self.running = False
    
    def run(self, mode: str = "http", **kwargs):
        """
        Main entry point to run the agent.
        
        Args:
            mode: Communication mode - "http", "stdin", or "file"
            **kwargs: Additional arguments for the specific mode
                - For "http": port (default 8000), host (default "0.0.0.0")
                - For "file": watch_dir (required), poll_interval (default 1.0)
        """
        self.logger.info(f"Starting agent in {mode} mode")
        
        if mode == "http":
            port = kwargs.get("port", 8000)
            host = kwargs.get("host", "0.0.0.0")
            self.run_http_server(port=port, host=host)
        elif mode == "stdin":
            self.run_stdin_mode()
        elif mode == "file":
            watch_dir = kwargs.get("watch_dir")
            if watch_dir is None:
                raise ValueError("watch_dir is required for file mode")
            poll_interval = kwargs.get("poll_interval", 1.0)
            self.run_file_watch_mode(Path(watch_dir), poll_interval=poll_interval)
        else:
            raise ValueError(f"Unknown mode: {mode}. Must be 'http', 'stdin', or 'file'")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.shutdown_requested = True
        self.running = False

