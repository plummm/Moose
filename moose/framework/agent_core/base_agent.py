"""Base agent class with common functionality for all Moose agents."""

import asyncio
import json
import os
import sys
import signal
import time
import uuid
import logging
import inspect
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
from abc import abstractmethod
from moose.framework.logging import get_agent_logger, set_project, set_global_debug
from moose.framework.agent_core.config_loader import load_agent_config

try:
    from flask import Flask, request, jsonify, Response, stream_with_context
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    Flask = None

try:
    from moose.framework.agent_core.agent_web_ui import generate_homepage_html, get_endpoints_list
except ImportError:
    generate_homepage_html = None
    get_endpoints_list = None

from moose.framework.logging.tracing import ensure_trace, span as trace_span

from moose.framework.logging import get_project_id


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
        set_global_debug(debug)
        
        if type(config_path) == str:
            config_path = Path(config_path)
        self.config_path = config_path or Path("agent_config.json")
        self.config = self._load_config_early()
        
        # Agent metadata from config (set before logger initialization)
        self.name = self.config.get("name", "unknown_agent")
        self.description = self.config.get("description", "")
        
        # Use hierarchical agent logger (moose.project.<id>.agent.<name>)
        # This ensures logs go to:
        # 1. Console (streamed)
        # 2. moose.log (via propagation)
        # 3. agents/<agent_name>.log (dedicated agent log)
        projects_base_dir = Path(os.getenv("MOOSE_PROJECTS_DIR"))
        project_id = os.getenv("MOOSE_PROJECT_ID", "default")
        set_project(project_id, projects_base_dir)
        self.logger = get_agent_logger(
            agent_name=self.name,
            project_id=project_id,
            debug=debug
        )
        
        # Communication mode
        self.running = False
        self.shutdown_requested = False
        
        # HTTP server (if using Flask)
        self.app = None
        self.http_server = None
        
        # HTTP server configuration
        interactive_mode = self.config.get("interactive_mode", {})
        http_config = interactive_mode.get("http_server", {})
        self.http_port = http_config.get("port") or self._get_default_http_port()
        self.http_auth_password = http_config.get("auth_password", "")
        self.http_endpoints = http_config.get("endpoints", [])
        
        # Logging storage for web UI
        self._log_buffer: List[Dict[str, Any]] = []
        self._max_log_entries = 1000
        
        # Add custom log handler to capture logs for web UI
        self._setup_log_capture()
        
        # Token cost tracking
        self._current_token_cost = None
        self._current_model_used = None
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        self.logger.info(f"Initialized agent: {self.name} - {self.description}")
    
    def _setup_log_capture(self):
        """Setup custom log handler to capture logs for web UI."""
        class LogBufferHandler(logging.Handler):
            def __init__(self, buffer, max_entries):
                super().__init__()
                self.buffer = buffer
                self.max_entries = max_entries
            
            def emit(self, record):
                try:
                    log_entry = {
                        'time': datetime.now().strftime('%H:%M:%S'),
                        'level': record.levelname.lower(),
                        'message': self.format(record)
                    }
                    self.buffer.append(log_entry)
                    # Keep only last N entries
                    if len(self.buffer) > self.max_entries:
                        self.buffer.pop(0)
                except Exception:
                    pass  # Don't let logging errors break the app
        
        # Add handler to the underlying logger
        if hasattr(self.logger, 'logger'):
            handler = LogBufferHandler(self._log_buffer, self._max_log_entries)
            handler.setLevel(logging.DEBUG)
            # Use simple format for web UI
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.logger.addHandler(handler)
    
    def _load_config_early(self) -> Dict[str, Any]:
        """
        Load agent configuration early (before logger is initialized).
        
        This is called during __init__ to get agent name for logger setup.
        
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        return load_agent_config(self.config_path)
    
    def load_config(self) -> Dict[str, Any]:
        """
        Load agent configuration from agent_config.json.
        
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        config = load_agent_config(self.config_path)
        self.logger.debug(f"Loaded config from {self.config_path}")
        return config
    
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
                "input_tokens": token_cost.get("input_tokens", 0),
                "output_tokens": token_cost.get("output_tokens", 0),
                "total_tokens": token_cost.get("total_tokens", 0),
                "cost_usd": token_cost.get("cost", 0.0) or token_cost.get("cost_usd", 0.0)
            }
        else:
            output["token_cost"] = {
                "input_tokens": 0,
                "output_tokens": 0,
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
                "input_tokens": usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
                "output_tokens": usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
                "total_tokens": usage.get("total_tokens", 0) if isinstance(usage, dict) else 0,
                "cost": cost or 0.0,
                "cost_usd": cost or 0.0
            }
            return token_cost, model
        
        return None, None
    
    @abstractmethod
    async def process(self, input_data: Any) -> Any:
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
            # Process the input (supports both async and sync implementations)
            maybe_result = self.process(input_data)
            if inspect.isawaitable(maybe_result):
                try:
                    result = asyncio.run(maybe_result)
                except RuntimeError as run_error:
                    # asyncio.run cannot be called if a loop is already running in this thread.
                    # This path is primarily used by stdin/file modes, where no loop should exist.
                    raise RuntimeError(
                        "Async process() cannot run because an event loop is already running. "
                        "Use an async-capable entrypoint (e.g. HTTP async handler) or provide a sync wrapper."
                    ) from run_error
            else:
                result = maybe_result
            
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
            self.logger.error(f"Error processing request {request_id}: {e}")
            processing_time_ms = (time.time() - start_time) * 1000
            
            return self._format_output(
                result=None,
                request_id=request_id,
                status="error",
                error=str(e),
                processing_time_ms=processing_time_ms
            )
    
    def _get_default_http_port(self) -> int:
        """Get default HTTP port from config or return 8000."""
        docker_config = self.config.get("docker", {})
        if docker_config.get("ports") and len(docker_config["ports"]) > 0:
            return docker_config["ports"][0].get("host", docker_config["ports"][0].get("container", 8000))
        return 8000
    
    def _check_auth(self, request) -> bool:
        """
        Check authentication for request.
        
        Args:
            request: Flask request object
            
        Returns:
            True if authenticated or no auth required, False otherwise
        """
        if not self.http_auth_password:
            return True  # No auth required
        
        # Check X-API-Key header
        api_code = request.headers.get('X-auth-password')
        if api_code == self.http_auth_password:
            return True
        
        return False
    
    def _register_custom_endpoints(self):
        """Register custom endpoints from config."""
        if not self.http_endpoints:
            return
        
        # Check if endpoints are already registered
        if hasattr(self, '_endpoints_registered') and self._endpoints_registered:
            return
        
        for endpoint_config in self.http_endpoints:
            path = endpoint_config.get('path')
            method = endpoint_config.get('method', 'POST').upper()
            handler_name = endpoint_config.get('handler')
            auth_required = endpoint_config.get('auth_required', bool(self.http_auth_password))
            
            if not path or not handler_name:
                self.logger.warning(f"Invalid endpoint config: missing path or handler")
                continue
            
            # Get handler method
            if not hasattr(self, handler_name):
                self.logger.warning(f"Handler method '{handler_name}' not found, skipping endpoint {path}")
                continue
            
            handler_func = getattr(self, handler_name)
            if not callable(handler_func):
                self.logger.warning(f"'{handler_name}' is not callable, skipping endpoint {path}")
                continue
            
            # Create route with proper closure
            # Use default parameters to capture values in closure
            def create_endpoint_wrapper(ep_path, ep_method, handler_func, requires_auth):
                # Create unique function name based on path and method
                # Replace special chars in path to make valid function name
                safe_name = ep_path.replace('/', '_').replace('-', '_').replace('.', '_').strip('_')
                unique_name = f"endpoint_{safe_name}_{ep_method.lower()}"
                
                def endpoint_wrapper():
                    inbound_request_id = (request.headers.get("X-Moose-Request-Id") or "").strip()
                    inbound_parent_span_id = (request.headers.get("X-Moose-Parent-Span-Id") or "").strip() or None
                    body_rid = None
                    try:
                        j = request.get_json(silent=True) or {}
                        if isinstance(j, dict):
                            body_rid = (j.get("request_id") or j.get("requestId") or "").strip() or None
                    except Exception:
                        body_rid = None

                    ctx = ensure_trace(
                        request_id=inbound_request_id or body_rid,
                        project_id=get_project_id(),
                        agent_name=self.name,
                    )

                    with trace_span(
                        kind="ingress.http",
                        name=f"{ep_method} {ep_path}",
                        parent_span_id=inbound_parent_span_id,
                        attrs={
                            "http.method": ep_method,
                            "http.path": ep_path,
                            "agent.name": self.name,
                        },
                        request_id=ctx.request_id,
                        project_id=ctx.project_id,
                        agent_name=ctx.agent_name,
                    ):
                    # Check auth if required
                        if requires_auth and not self._check_auth(request):
                            return jsonify({
                                "status": "error",
                                "error": "Unauthorized"
                            }), 401
                    
                    # Call handler with request data
                    try:
                        if ep_method in ['POST', 'PUT', 'PATCH']:
                            data = request.get_json() or {}
                        else:
                            data = request.args.to_dict()
                        
                        # Call handler function
                        result = handler_func(data)

                        # Allow handlers to return raw Flask Response/tuples (e.g., HTML pages)
                        try:
                            if isinstance(result, Response):
                                return result
                            if isinstance(result, tuple):
                                # Common Flask patterns: (body, status), (body, status, headers), (Response, status)
                                if len(result) >= 1 and isinstance(result[0], Response):
                                    return result
                        except Exception:
                            pass
                        
                        # Ensure result is a dict
                        if not isinstance(result, dict):
                            result = {"status": "success", "result": result}
                        
                        return jsonify(result)
                    except Exception as e:
                        self.logger.error(f"Error in endpoint {ep_path}: {e}")
                        return jsonify({
                            "status": "error",
                            "error": str(e)
                        }), 500
                
                async def endpoint_wrapper_async():
                    inbound_request_id = (request.headers.get("X-Moose-Request-Id") or "").strip()
                    inbound_parent_span_id = (request.headers.get("X-Moose-Parent-Span-Id") or "").strip() or None
                    body_rid = None
                    try:
                        j = request.get_json(silent=True) or {}
                        if isinstance(j, dict):
                            body_rid = (j.get("request_id") or j.get("requestId") or "").strip() or None
                    except Exception:
                        body_rid = None

                    ctx = ensure_trace(
                        request_id=inbound_request_id or body_rid,
                        project_id=get_project_id(),
                        agent_name=self.name,
                    )

                    with trace_span(
                        kind="ingress.http",
                        name=f"{ep_method} {ep_path}",
                        parent_span_id=inbound_parent_span_id,
                        attrs={
                            "http.method": ep_method,
                            "http.path": ep_path,
                            "agent.name": self.name,
                        },
                        request_id=ctx.request_id,
                        project_id=ctx.project_id,
                        agent_name=ctx.agent_name,
                    ):
                    # Check auth if required
                        if requires_auth and not self._check_auth(request):
                            return jsonify({
                                "status": "error",
                                "error": "Unauthorized"
                            }), 401
                    
                    # Call handler with request data
                    try:
                        if ep_method in ['POST', 'PUT', 'PATCH']:
                            data = request.get_json() or {}
                        else:
                            data = request.args.to_dict()
                        
                        # Call handler function
                        result = await handler_func(data)

                        # Allow handlers to return raw Flask Response/tuples (e.g., HTML pages)
                        try:
                            if isinstance(result, Response):
                                return result
                            if isinstance(result, tuple):
                                # Common Flask patterns: (body, status), (body, status, headers), (Response, status)
                                if len(result) >= 1 and isinstance(result[0], Response):
                                    return result
                        except Exception:
                            pass
                        
                        # Ensure result is a dict
                        if not isinstance(result, dict):
                            result = {"status": "success", "result": result}
                        
                        return jsonify(result)
                    except Exception as e:
                        self.logger.error(f"Error in endpoint {ep_path}: {e}")
                        return jsonify({
                            "status": "error",
                            "error": str(e)
                        }), 500
                
                # Select the appropriate wrapper
                if inspect.iscoroutinefunction(handler_func):
                    wrapper = endpoint_wrapper_async
                else:
                    wrapper = endpoint_wrapper
                
                # Set unique function name to avoid Flask endpoint conflicts
                wrapper.__name__ = unique_name
                
                return wrapper
                   
            # Register the route
            wrapper = create_endpoint_wrapper(path, method, handler_func, auth_required)
            self.app.route(path, methods=[method])(wrapper)
            
            self.logger.info(f"Registered endpoint: {method} {path} -> {handler_name}")
        
        # Mark endpoints as registered
        self._endpoints_registered = True
    
    def run_http_server(self, port: Optional[int] = None, host: str = "0.0.0.0"):
        """
        Run agent as HTTP server.
        
        Args:
            port: Port to listen on (overrides config if provided)
            host: Host to bind to
        """
        if not FLASK_AVAILABLE:
            raise ImportError(
                "Flask is required for HTTP mode. Install with: pip install flask"
            )
        
        # Check if Flask app already exists and is configured
        if self.app is not None:
            self.logger.warning("HTTP server already initialized, skipping re-initialization")
            return
        
        # Use provided port or config port
        server_port = port or self.http_port
        
        self.app = Flask(self.name)
        self.running = True
        
        # Health check endpoint (no auth required)
        @self.app.route('/health', methods=['GET'])
        def health():
            """Health check endpoint."""
            return jsonify({
                "status": "healthy",
                "agent": self.name,
                "description": self.description
            })
        
        # Homepage with web UI (no auth required)
        @self.app.route('/', methods=['GET'])
        def homepage():
            """Agent dashboard homepage."""
            if generate_homepage_html is None:
                return jsonify({
                    "status": "error",
                    "error": "Web UI not available"
                }), 500
            
            endpoints = get_endpoints_list(self) if get_endpoints_list else []
            html = generate_homepage_html(
                agent_name=self.name,
                agent_description=self.description,
                agent_version=self.config.get("version", "N/A"),
                endpoints=endpoints,
                http_port=server_port,
                auth_enabled=bool(self.http_auth_password)
            )
            return html
        
        # Logs endpoint (no auth required)
        @self.app.route('/logs', methods=['GET'])
        def logs():
            """Get agent logs for web UI."""
            return jsonify({
                "logs": self._log_buffer[-100:]  # Return last 100 entries
            })

        # Logs streaming endpoint (SSE, no auth required)
        @self.app.route('/logs/stream', methods=['GET'])
        def logs_stream():
            """Stream agent logs for web UI via Server-Sent Events (SSE)."""
            def event_stream():
                # Start streaming from the current end of the buffer to avoid replaying history.
                last_idx = len(self._log_buffer)
                keepalive_every_s = 10.0
                last_keepalive = time.time()

                while self.running:
                    # Send new entries if any
                    if last_idx < len(self._log_buffer):
                        new_entries = self._log_buffer[last_idx:]
                        last_idx = len(self._log_buffer)
                        for entry in new_entries:
                            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

                    # Keepalive comment to prevent proxies from closing the connection
                    now = time.time()
                    if now - last_keepalive >= keepalive_every_s:
                        last_keepalive = now
                        yield ": ping\n\n"

                    time.sleep(0.5)

            return Response(
                stream_with_context(event_stream()),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        
        # Register custom endpoints from config
        self._register_custom_endpoints()
        
        auth_info = f" (auth: {'enabled' if self.http_auth_password else 'disabled'})"
        self.logger.info(f"Starting HTTP server on {host}:{server_port}{auth_info}")
        try:
            self.app.run(host=host, port=server_port, debug=False, use_reloader=False)
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
                    self.logger.error(f"Error processing input: {e}")
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
