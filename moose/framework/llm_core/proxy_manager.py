"""Proxy manager for LiteLLM proxy server."""

import os
import subprocess
import time
import signal
import atexit
from pathlib import Path
from typing import Optional
from framework.logging import get_core_logger
from framework.llm_core.config import ProxyConfig

try:
    import litellm
    from litellm import proxy
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    litellm = None
    proxy = None


class ProxyManager:
    """Manages the lifecycle of LiteLLM proxy server."""
    
    _instance: Optional['ProxyManager'] = None
    _proxy_process: Optional[subprocess.Popen] = None
    _proxy_port: int = 4000
    _proxy_host: str = "0.0.0.0"
    _config_path: Optional[Path] = None
    
    def __new__(cls):
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize proxy manager."""
        if self._initialized:
            return
        
        self.logger = get_core_logger()
        self._initialized = True
        self._proxy_process = None
        
        # Register cleanup on exit
        atexit.register(self.stop)
        
        # Handle signals
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info("Received shutdown signal, stopping proxy...")
        self.stop()
    
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
    
    def start(self, config_path: Optional[Path] = None, port: Optional[int] = None, host: Optional[str] = None) -> bool:
        """
        Start the LiteLLM proxy server.
        
        Args:
            config_path: Path to config.yaml. If None, detects from project directory.
            port: Port to run proxy on. If None, reads from MOOSE_LITELLM_PROXY_PORT (default: 4000)
            host: Host to bind to. If None, reads from MOOSE_LITELLM_PROXY_HOST (default: 0.0.0.0)
        
        Returns:
            True if proxy started successfully, False otherwise
        """
        if not LITELLM_AVAILABLE:
            self.logger.error(
                "litellm[proxy] is required. Install with: pip install 'litellm[proxy]'"
            )
            return False
        
        if self.is_running():
            self.logger.debug("Proxy is already running")
            return True
        
        # Read port and host from environment variables with defaults
        self._proxy_port = port if port is not None else int(os.getenv("MOOSE_LITELLM_PROXY_PORT", "4000"))
        self._proxy_host = host if host is not None else os.getenv("MOOSE_LITELLM_PROXY_HOST", "0.0.0.0")
        
        # Determine config path
        if config_path is None:
            # Get config file name from environment variable
            config_name = os.getenv("MOOSE_LITELLM_CONFIG_NAME", "config.yaml")
            
            # Detect project directory
            project_dir = self._detect_project_dir()
            if project_dir:
                config_path = project_dir / config_name
            else:
                # Not in project context, use current directory
                config_path = Path.cwd() / config_name
        
        if not config_path.exists():
            raise FileNotFoundError(
                f"LiteLLM config file '{config_name}' not found in project directory: {project_dir} nor {Path.cwd()}. "
                f"Config file should have been created when the project was created."
            )
        
        self._config_path = config_path
        
        try:
            # Build command to start proxy
            cmd = ["litellm", "--port", str(self._proxy_port), "--host", self._proxy_host]
            
            if config_path and config_path.exists():
                cmd.extend(["--config", str(config_path)])
            
            self.logger.info(f"Starting LiteLLM proxy on {self._proxy_host}:{self._proxy_port}")
            
            # Start proxy as background process
            self._proxy_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )
            
            # Wait a bit and check if process is still alive
            time.sleep(2)
            
            if self._proxy_process.poll() is not None:
                # Process died, read error
                stdout, stderr = self._proxy_process.communicate()
                self.logger.error(f"Proxy failed to start: {stderr}")
                self._proxy_process = None
                return False
            
            # Wait for proxy to be ready
            if self._wait_for_proxy(timeout=10):
                self.logger.info(f"LiteLLM proxy started successfully on {host}:{port}")
                return True
            else:
                self.logger.error("Proxy started but not responding")
                self.stop()
                return False
                
        except Exception as e:
            self.logger.error(f"Failed to start proxy: {e}")
            self._proxy_process = None
            return False
    
    def stop(self):
        """Stop the proxy server."""
        if not self._proxy_process:
            return
        
        try:
            self.logger.info("Stopping LiteLLM proxy...")
            
            # Terminate process group (works on Unix)
            if os.name != 'nt':
                os.killpg(os.getpgid(self._proxy_process.pid), signal.SIGTERM)
            else:
                self._proxy_process.terminate()
            
            # Wait for process to terminate
            try:
                self._proxy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate
                if os.name != 'nt':
                    os.killpg(os.getpgid(self._proxy_process.pid), signal.SIGKILL)
                else:
                    self._proxy_process.kill()
                self._proxy_process.wait()
            
            self.logger.info("Proxy stopped")
        except Exception as e:
            self.logger.error(f"Error stopping proxy: {e}")
        finally:
            self._proxy_process = None
    
    def is_running(self) -> bool:
        """Check if proxy is running."""
        if not self._proxy_process:
            return False
        
        # Check if process is still alive
        if self._proxy_process.poll() is not None:
            self._proxy_process = None
            return False
        
        # Try to connect to proxy
        return self._check_proxy_health()
    
    def get_config(self) -> ProxyConfig:
        """Get the config."""
        return ProxyConfig(config_path=self._config_path)
    
    def _wait_for_proxy(self, timeout: int = 10) -> bool:
        """Wait for proxy to be ready."""
        try:
            import requests
        except ImportError:
            self.logger.warning("requests library not available, skipping health check")
            return True  # Assume proxy is running if we can't check
        
        start_time = time.time()
        proxy_url = f"http://localhost:{self._proxy_port}/health"
        
        # Get master_key for authentication
        master_key = self._get_master_key()
        headers = {}
        if master_key:
            headers["x-litellm-api-key"] = master_key
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(proxy_url, headers=headers, timeout=5)
                if response.status_code == 200:
                    return True
            except Exception as e:
                self.logger.warning(f"Failed to check proxy health: {e}")
                pass
            
            time.sleep(0.5)
        
        return False
    
    def _get_master_key(self) -> Optional[str]:
        """
        Get master_key from config.yaml for proxy authentication.
        
        Returns:
            Master key string if found, None otherwise (with warning logged)
        """
        if not self._config_path or not self._config_path.exists():
            self.logger.warning("Config path not set or doesn't exist, cannot read master_key")
            return None
        
        try:
            config = self.get_config()
            master_key = config.get_master_key()
            
            if not master_key:
                self.logger.debug("master_key not found in config.yaml general_settings")
                return None
            
            return master_key
        except Exception as e:
            self.logger.warning(f"Failed to read master_key from config: {e}")
            return None
    
    def _check_proxy_health(self) -> bool:
        """Check if proxy is healthy."""
        try:
            import requests
        except ImportError:
            return True  # Assume healthy if we can't check
        
        try:
            proxy_url = f"http://localhost:{self._proxy_port}/health"
            
            # Get master_key for authentication
            master_key = self._get_master_key()
            headers = {}
            if master_key:
                headers["x-litellm-api-key"] = master_key
            
            response = requests.get(proxy_url, headers=headers, timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def get_proxy_url(self) -> str:
        """Get the proxy URL."""
        return f"http://localhost:{self._proxy_port}"
    
    @classmethod
    def get_instance(cls) -> 'ProxyManager':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

