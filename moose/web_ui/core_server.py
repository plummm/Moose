"""Core Web Server for Moose Framework.

Singleton Flask server that handles multiple projects with:
- Main dashboard page
- API endpoints for projects, agents, logs, and chat
- SSE streaming for real-time updates
"""

import json
import threading
import errno
from typing import Dict, List, Optional, Set
from pathlib import Path

try:
    from flask import Flask, Response, jsonify, request
except ImportError:
    raise ImportError("Flask is required for the web UI. Install it with: pip install flask")

from .log_manager import get_log_manager
from .chat_manager import get_chat_manager
from .core_web_ui import get_dashboard_html


class CoreWebServer:
    """Singleton Flask web server for Moose core.
    
    Handles multiple projects with a single server instance.
    """
    
    _instance: Optional['CoreWebServer'] = None
    _lock = threading.Lock()
    
    def __new__(cls, port: int = 5000):
        """Ensure only one instance exists (singleton pattern)."""
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cls._instance
    
    def __init__(self, port: int = 5000):
        """Initialize the web server.
        
        Args:
            port: Port to run the server on
        """
        if self._initialized:
            return
        
        self.port = port
        self._projects: Set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.config['JSON_SORT_KEYS'] = False
        
        # Register routes
        self._register_routes()
        
        self._initialized = True
    
    def _register_routes(self):
        """Register all Flask routes."""
        
        @self.app.route('/')
        def dashboard():
            """Serve the main dashboard page."""
            return get_dashboard_html()
        
        @self.app.route('/api/projects')
        def list_projects():
            """List all registered projects."""
            return jsonify(sorted(list(self._projects)))
        
        @self.app.route('/api/projects/<project_id>/agents')
        def list_agents(project_id: str):
            """List agents for a project."""
            agents = self._get_agents(project_id)
            return jsonify(agents)
        
        @self.app.route('/api/projects/<project_id>/logs/files')
        def list_log_files(project_id: str):
            """List available log files for a project."""
            log_manager = get_log_manager()
            files = log_manager.list_log_files(project_id)
            return jsonify(files)
        
        @self.app.route('/api/projects/<project_id>/logs')
        def get_logs(project_id: str):
            """Get logs for a project.
            
            Query params:
                file: Optional historical log file name
                limit: Optional max entries to return
            """
            log_manager = get_log_manager()
            file = request.args.get('file')
            limit = request.args.get('limit', type=int)
            
            if file:
                # Load historical log file
                entries = log_manager.read_log_file(project_id, file, limit=limit)
            else:
                # Get buffered logs
                entries = log_manager.get_buffer(project_id, limit=limit)
            
            return jsonify(entries)
        
        @self.app.route('/api/projects/<project_id>/logs/stream')
        def stream_logs(project_id: str):
            """SSE stream for real-time logs."""
            log_manager = get_log_manager()
            
            def generate():
                yield from log_manager.generate_sse_stream(project_id)
            
            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )
        
        @self.app.route('/api/projects/<project_id>/chat/files')
        def list_chat_files(project_id: str):
            """List available chat files for a project (agent log files containing LLM JSONL entries)."""
            chat_manager = get_chat_manager()
            files = chat_manager.list_chat_files(project_id)
            return jsonify(files)
        
        @self.app.route('/api/projects/<project_id>/chat')
        def get_chat(project_id: str):
            """Get chat messages for a project.
            
            Query params:
                file: Optional historical agent log file name (e.g., agents/<agent>.log.<n>)
            """
            chat_manager = get_chat_manager()
            file = request.args.get('file')
            
            if file:
                # Load historical chat file
                messages = chat_manager.read_chat_file(project_id, file)
            else:
                # Get buffered messages
                messages = chat_manager.get_buffer(project_id)
            
            return jsonify(messages)
        
        @self.app.route('/api/projects/<project_id>/chat/stream')
        def stream_chat(project_id: str):
            """SSE stream for real-time chat messages."""
            chat_manager = get_chat_manager()
            
            def generate():
                yield from chat_manager.generate_sse_stream(project_id)
            
            return Response(
                generate(),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no'
                }
            )

        @self.app.route('/api/projects/<project_id>/llm/usage_summary')
        def get_llm_usage_summary(project_id: str):
            """
            Aggregate cost + token usage across agent log files for the project.

            Groups by metadata.agent_name (main agent attribution) and by day.
            """
            chat_manager = get_chat_manager()

            def _empty():
                return {
                    "project_id": project_id,
                    "totals": {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}},
                    "by_agent": {},
                    "per_day": [],
                }

            # Resolve log directory (uses ChatManager's logic for MOOSE_PROJECTS_DIR / cwd projects)
            try:
                log_dir = chat_manager._get_log_dir(project_id)  # type: ignore[attr-defined]
            except Exception:
                log_dir = None
            if not log_dir or not Path(log_dir).exists():
                return jsonify(_empty())
            log_dir = Path(log_dir)

            try:
                # e.g. ["agents/finance_office.log.40", "agents/truthsocial_agent.log.40", ...]
                files = chat_manager.list_chat_files(project_id)
            except Exception:
                files = []
            if not files:
                return jsonify(_empty())

            totals_cost = 0.0
            totals_tokens = {"input": 0, "output": 0, "total": 0}
            by_agent: Dict[str, Dict[str, object]] = {}
            per_day_map: Dict[str, Dict[str, Dict[str, object]]] = {}

            def _bump(bucket: Dict[str, object], *, cost: float, it: int, ot: int, tt: int):
                bucket["cost"] = float(bucket.get("cost", 0.0) or 0.0) + float(cost or 0.0)
                toks = bucket.get("tokens") if isinstance(bucket.get("tokens"), dict) else {"input": 0, "output": 0, "total": 0}
                toks["input"] = int(toks.get("input", 0) or 0) + int(it or 0)
                toks["output"] = int(toks.get("output", 0) or 0) + int(ot or 0)
                toks["total"] = int(toks.get("total", 0) or 0) + int(tt or 0)
                bucket["tokens"] = toks

            for filename in files:
                fp = log_dir / filename
                if not fp.exists() or not fp.is_file():
                    continue
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        for line in f:
                            line = (line or "").strip()
                            if not line:
                                continue
                            try:
                                if not line.startswith("{"):
                                    continue
                                entry = json.loads(line)
                            except Exception:
                                continue
                            if not isinstance(entry, dict):
                                continue
                            if "direction" not in entry or "timestamp" not in entry:
                                continue
                            if entry.get("direction") != "response":
                                continue

                            meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                            agent = str(meta.get("agent_name") or entry.get("agent_name") or "unknown")
                            ts = str(entry.get("timestamp") or "")
                            day = ts[:10] if len(ts) >= 10 else "unknown"

                            usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
                            try:
                                it = int(usage.get("input_tokens", 0) or 0)
                                ot = int(usage.get("output_tokens", 0) or 0)
                                tt = int(usage.get("total_tokens", it + ot) or (it + ot))
                            except Exception:
                                it, ot, tt = 0, 0, 0

                            try:
                                cost = float(entry.get("cost") or 0.0)
                            except Exception:
                                cost = 0.0

                            totals_cost += cost
                            totals_tokens["input"] += it
                            totals_tokens["output"] += ot
                            totals_tokens["total"] += tt

                            if agent not in by_agent:
                                by_agent[agent] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                            _bump(by_agent[agent], cost=cost, it=it, ot=ot, tt=tt)

                            if day not in per_day_map:
                                per_day_map[day] = {}
                            if agent not in per_day_map[day]:
                                per_day_map[day][agent] = {"cost": 0.0, "tokens": {"input": 0, "output": 0, "total": 0}}
                            _bump(per_day_map[day][agent], cost=cost, it=it, ot=ot, tt=tt)
                except Exception:
                    continue

            per_day = [{"date": d, "by_agent": per_day_map[d]} for d in sorted(per_day_map.keys())]
            return jsonify(
                {
                    "project_id": project_id,
                    "totals": {"cost": float(totals_cost), "tokens": totals_tokens},
                    "by_agent": by_agent,
                    "per_day": per_day,
                }
            )
    
    def _check_agent_health(self, url: str, timeout: float = 2.0) -> str:
        """Check agent health via HTTP /health endpoint.
        
        Args:
            url: Base URL of the agent (e.g., http://localhost:8000)
            timeout: Request timeout in seconds
            
        Returns:
            'running' if healthy, 'error' if unhealthy, 'stopped' if unreachable
        """
        import urllib.request
        import urllib.error
        
        try:
            health_url = f"{url}/health"
            req = urllib.request.Request(health_url, method='GET')
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status == 200:
                    return 'running'
                else:
                    return 'error'
        except urllib.error.URLError:
            return 'stopped'
        except Exception:
            return 'stopped'
    
    def _get_agents(self, project_id: str) -> List[Dict]:
        """Get agent information for a project.
        
        Args:
            project_id: Project identifier
            
        Returns:
            List of agent info dicts
        """
        agents = []
        
        try:
            # Import AgentLoader and AgentRegistry
            try:
                from moose.framework.agent_core import AgentLoader, AgentRegistry, ContainerManager
            except ImportError:
                from framework.agent_core import AgentLoader, AgentRegistry, ContainerManager
            
            loader = AgentLoader()
            registry = AgentRegistry()
            
            # Discover all agents
            agent_names = loader.discover_agents()
            
            for agent_name in agent_names:
                status = 'stopped'
                url = None
                container_name = ''
                interactive_mode = ''
                mode = 'http'
                
                # Load agent config for interactive_mode info
                try:
                    config = loader.load_agent_config(agent_name)
                    interactive_config = config.get('interactive_mode', {})
                    mode = interactive_config.get('mode', 'http')
                    
                    if mode == 'http':
                        http_config = interactive_config.get('http_server', {})
                        port = http_config.get('port', 8000)
                        interactive_mode = f"http (localhost:{port})"
                        url = f"http://localhost:{port}"
                    elif mode == 'file':
                        file_config = interactive_config.get('file', {})
                        watch_dir = file_config.get('watch_dir', '/project/agent_io')
                        interactive_mode = f"file ({watch_dir})"
                    else:
                        interactive_mode = mode
                except Exception:
                    interactive_mode = 'unknown'
                
                # Check agent status
                if mode == 'http' and url:
                    # For HTTP mode, check health endpoint
                    status = self._check_agent_health(url)
                else:
                    # For other modes, check container status
                    container_id = registry.get_container_id(project_id, agent_name)
                    if container_id:
                        container_name = container_id[:12]
                        
                        # Try to get container status
                        try:
                            manager = ContainerManager()
                            container_status = manager.get_container_status(agent_name, project_id)
                            status = container_status if container_status else 'stopped'
                        except Exception:
                            status = 'unknown'
                
                # Still get container name even for HTTP mode
                if not container_name:
                    container_id = registry.get_container_id(project_id, agent_name)
                    if container_id:
                        container_name = container_id[:12]
                
                agents.append({
                    'name': agent_name,
                    'status': status,
                    'container': container_name,
                    'interactive_mode': interactive_mode,
                    'url': url
                })
        
        except Exception as e:
            # If loader not available, return empty list
            pass
        
        return agents
    
    def add_project(self, project_id: str):
        """Register a project with the server.
        
        Args:
            project_id: Project identifier
        """
        self._projects.add(project_id)
    
    def remove_project(self, project_id: str):
        """Unregister a project from the server.
        
        Args:
            project_id: Project identifier
        """
        self._projects.discard(project_id)
    
    def start(self, blocking: bool = True):
        """Start the web server.
        
        Args:
            blocking: If True, block the calling thread. If False, run in background.
        """
        if self._running:
            return
        
        self._running = True
        
        if blocking:
            self._run_server()
        else:
            self._thread = threading.Thread(target=self._run_server, daemon=True)
            self._thread.start()
    
    def start_background(self):
        """Start the web server in a background thread."""
        self.start(blocking=False)
    
    def _run_server(self):
        """Run the Flask server."""
        try:
            # Disable Flask's default logging for cleaner output
            import logging
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.WARNING)
            
            print(f"\n🦌 Moose Web UI running at http://localhost:{self.port}\n")
            
            self.app.run(
                host='0.0.0.0',
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        except OSError as e:
            # Handle "Address already in use" error gracefully
            # This happens when another moose server is already running on the same port
            error_msg = str(e).lower()
            if "address already in use" in error_msg or "address is already in use" in error_msg or e.errno == errno.EADDRINUSE:
                print(f"\n⚠️  Port {self.port} is already in use (moose server may already be running)")
                print(f"   Skipping moose server launch. Existing server will auto-discover agents.\n")
            else:
                print(f"Failed to start web server: {e}")
            self._running = False
        except Exception as e:
            print(f"Failed to start web server: {e}")
            self._running = False
    
    def stop(self):
        """Stop the web server."""
        self._running = False
        # Note: Flask doesn't have a clean way to stop from another thread
        # The server will stop when the process exits
    
    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running
    
    @classmethod
    def get_instance(cls) -> Optional['CoreWebServer']:
        """Get the singleton instance if it exists."""
        return cls._instance


# Module-level functions for convenience

_server_instance: Optional[CoreWebServer] = None


def get_or_start_core_server(port: int = 5000) -> CoreWebServer:
    """Get or create and start the core web server.
    
    Args:
        port: Port for the server
        
    Returns:
        CoreWebServer instance
    """
    global _server_instance
    
    if _server_instance is None:
        _server_instance = CoreWebServer(port)
    
    if not _server_instance.is_running:
        _server_instance.start_background()
    
    return _server_instance


def register_project(project_id: str, port: int = 5000) -> CoreWebServer:
    """Register a project and ensure the web server is running.
    
    Args:
        project_id: Project identifier
        port: Port for the server
        
    Returns:
        CoreWebServer instance
    """
    server = get_or_start_core_server(port)
    server.add_project(project_id)
    return server
