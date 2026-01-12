"""Command to run a Moose project."""

import os
import sys
import json
import importlib.util
import threading
import traceback
from pathlib import Path

from moose.framework.logging import (
    init_core_logger, get_core_logger, set_global_debug,
    set_project, reinit_llm_logger, _current_log_suffix
)
from moose.framework.agent_core import AgentLoader, ContainerManager


class RunCommand:
    """Command to run a Moose project."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the run command."""
        parser = subparser.add_parser(cmd, help='Run a Moose project (starts enabled agents)')
        
        parser.add_argument(
            'project_id',
            type=str,
            nargs='?',
            default='default',
            help='Project ID to run (default: "default")'
        )
        
        parser.add_argument(
            '--no-agents',
            action='store_true',
            help='Do not start enabled agents (useful for validating config / logs)'
        )

        parser.add_argument(
            '--force-rebuild',
            action='store_true',
            help='Force rebuild Docker images for enabled agents before starting'
        )

        parser.add_argument(
            '--agent-debug',
            action='store_true',
            help='Run enabled agents locally in debug mode (loaded like `moose agent debug`, instead of Docker)'
        )
        
        return parser
    
    def add_arguments(self, parser):
        """Add arguments to the run command parser."""
        # Arguments are handled in custom_subparser
        pass
    
    def run(self, args):
        """Run the project."""
        # Set global debug flag before initializing logger
        set_global_debug(args.debug)
        init_core_logger()
        
        project_id = args.project_id

        # Resolve projects base dir (prefer env; fallback to ./projects)
        projects_base_dir = Path(os.getenv("MOOSE_PROJECTS_DIR") or (Path.cwd() / "projects"))
        project_dir = projects_base_dir / project_id
        if not project_dir.exists():
            logger = get_core_logger()
            logger.error(f"Project directory not found: {project_dir}")
            logger.error("Create it first with: moose create <project_id> --agents ...")
            return 1
        
        # Set up project for logging
        # All logs will go to projects/<project_id>/logs/
        set_project(project_id, projects_base_dir)
        
        # Reinitialize LLM logger to use project log directory
        reinit_llm_logger()
        
        logger = get_core_logger()
        logger.info(f"Starting project: {project_id}")

        # Load project_config.json (enabled_agents)
        enabled_agents: list[str] = []
        try:
            cfg_path = project_dir / "project_config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict) and isinstance(cfg.get("enabled_agents"), list):
                    enabled_agents = [str(x).strip() for x in cfg.get("enabled_agents") if str(x).strip()]
        except Exception as e:
            logger.warning(f"Failed to load project_config.json: {e}")

        if args.no_agents:
            logger.info("Agent startup disabled via --no-agents")
            enabled_agents = []

        manager = None
        debug_agents: list[object] = []
        debug_threads: list[threading.Thread] = []
        if enabled_agents:
            if getattr(args, "agent_debug", True):
                # Run enabled agents locally in debug mode, mirroring AgentCommand._run_debug
                # Set project context for BaseAgent init
                os.environ["MOOSE_PROJECT_ID"] = project_id
                os.environ["MOOSE_PROJECTS_DIR"] = str(projects_base_dir)
                if _current_log_suffix is not None:
                    os.environ["MOOSE_LOG_SUFFIX"] = str(_current_log_suffix)
                if getattr(args, "debug", False):
                    os.environ["MOOSE_AGENT_DEBUG"] = "true"

                loader = AgentLoader()
                discovered = set(loader.discover_agents())

                def _load_agent_instance(agent_name: str):
                    if agent_name not in discovered:
                        raise RuntimeError(f"Agent '{agent_name}' not found")
                    agent_path = loader.get_agent_path(agent_name)
                    config = loader.load_agent_config(agent_name)

                    agent_file = agent_path / config.get("entry_point", "agent.py")
                    if not agent_file.exists():
                        raise RuntimeError(f"Agent entry point not found: {agent_file}")

                    spec = importlib.util.spec_from_file_location(f"agent_module_{agent_name}", agent_file)
                    if spec is None or spec.loader is None:
                        raise RuntimeError(f"Failed to load module spec for agent '{agent_name}'")
                    agent_module = importlib.util.module_from_spec(spec)
                    # Ensure imports work in local debug mode.
                    # - `project_root` allows `import moose`
                    # - `agent_dir` allows sibling imports like `import department_router`
                    #
                    # IMPORTANT: We keep `agent_dir` on sys.path for the duration of the debug
                    # session so late imports inside handlers (e.g. inside `_node_start`) work.
                    # To reduce cross-agent name collisions, we only temporarily put `agent_dir`
                    # at the front during module load, then leave a copy appended afterwards.
                    agent_dir = str(agent_file.parent)
                    project_root = str(Path(__file__).resolve().parents[3])

                    if project_root and project_root not in sys.path:
                        sys.path.insert(0, project_root)

                    sys.path.insert(0, agent_dir)
                    try:
                        spec.loader.exec_module(agent_module)
                    finally:
                        # Remove the temporary front insertion (leave any existing/appended copy).
                        if sys.path and sys.path[0] == agent_dir:
                            sys.path.pop(0)

                    # Keep agent_dir available for late imports during runtime.
                    if agent_dir not in sys.path:
                        sys.path.append(agent_dir)

                    agent_class = None
                    entry_class = config.get("entry_class", None)
                    if entry_class is not None and entry_class != "":
                        if hasattr(agent_module, entry_class):
                            agent_class = getattr(agent_module, entry_class)
                    else:
                        for name in dir(agent_module):
                            obj = getattr(agent_module, name)
                            if (
                                isinstance(obj, type)
                                and hasattr(obj, "__bases__")
                                and any("BaseAgent" in str(base) for base in obj.__bases__)
                            ):
                                agent_class = obj
                                break

                    if not agent_class:
                        raise RuntimeError("Could not find agent class in agent module")

                    logger.info(f"Loading agent class for '{agent_name}': {agent_class.__name__}")
                    agent = agent_class(config_path=agent_path / "agent_config.json", debug=getattr(args, "debug", False))

                    # Get port from HTTP server config, ports config, or default (same logic as AgentCommand._run_debug)
                    port = 8000
                    interactive_mode = config.get("interactive_mode", {})
                    http_config = interactive_mode.get("http_server", {})
                    if http_config.get("port"):
                        port = http_config["port"]
                    else:
                        docker_config = config.get("docker", {})
                        if docker_config.get("ports") and len(docker_config["ports"]) > 0:
                            port = docker_config["ports"][0].get("host", docker_config["ports"][0].get("container", 8000))

                    return agent, port, http_config

                def _thread_target(agent_obj, agent_name: str, port: int):
                    try:
                        logger.info(f"Starting agent '{agent_name}' HTTP server on port {port} (debug mode)")
                        agent_obj.run(mode="http", port=port)
                    except Exception as e:
                        logger.error(f"Agent '{agent_name}' crashed: {e}")
                        logger.error(traceback.format_exc())

                if getattr(args, "force_rebuild", False):
                    logger.info("--force-rebuild ignored in --agent-debug mode (no Docker images are built).")

                for agent_name in enabled_agents:
                    try:
                        agent_obj, port, http_config = _load_agent_instance(agent_name)
                        debug_agents.append(agent_obj)

                        # List endpoints like _run_debug
                        endpoints_info = ["/health"]
                        if isinstance(http_config, dict) and http_config.get("endpoints"):
                            for ep in http_config["endpoints"]:
                                endpoints_info.append(f"{ep.get('path')}")
                        logger.info(f"Agent '{agent_name}' endpoints:")
                        for ep in endpoints_info:
                            logger.info(f"  - http://localhost:{port}{ep}")

                        t = threading.Thread(target=_thread_target, args=(agent_obj, agent_name, port), daemon=True)
                        t.start()
                        debug_threads.append(t)
                    except Exception as e:
                        logger.error(f"Failed to start agent '{agent_name}' in debug mode: {e}")
                        logger.error(traceback.format_exc())
            else:
                # Start enabled agents in Docker
                try:
                    manager = ContainerManager()
                except Exception as e:
                    logger.error(f"Failed to initialize Docker container manager: {e}")
                    logger.error("Make sure Docker is installed, running, and python 'docker' package is available.")
                    return 1

                # Propagate project context to containers
                common_env = {
                    "MOOSE_PROJECT_ID": project_id,
                    "MOOSE_PROJECTS_DIR": str(projects_base_dir),
                }
                # Pass log suffix so containers write to the same log files
                if _current_log_suffix is not None:
                    common_env["MOOSE_LOG_SUFFIX"] = _current_log_suffix
                if getattr(args, "debug", False):
                    common_env["MOOSE_AGENT_DEBUG"] = "true"

                for agent_name in enabled_agents:
                    try:
                        if getattr(args, "force_rebuild", False):
                            manager.build_agent_image(agent_name, force_rebuild=True)
                        cid = manager.start_agent_container(
                            agent_name=agent_name,
                            project_id=project_id,
                            project_dir=project_dir,
                            environment=common_env,
                        )
                        logger.info(f"Started agent '{agent_name}' in Docker: {cid[:12]}")
                    except Exception as e:
                        logger.error(f"Failed to start agent '{agent_name}': {e}")
        else:
            logger.warning("No enabled agents found for this project (project_config.json.enabled_agents is empty).")

        logger.info("Project is running. Press Ctrl+C to stop.")
        
        try:
            # Keep the main thread alive
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nStopping project...")
            if debug_agents:
                logger.info("Stopping debug agents...")
                for a in debug_agents:
                    try:
                        a.shutdown()
                    except Exception:
                        pass
            if manager is not None:
                try:
                    manager.cleanup_project_containers(project_id)
                except Exception as e:
                    logger.warning(f"Failed to cleanup project containers: {e}")
            logger.info("Project stopped.")
            sys.exit(0)
