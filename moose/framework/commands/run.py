"""Command to run a Moose project with web UI."""

import os
import sys
import json
from pathlib import Path

try:
    from moose.framework.logging import (
        init_core_logger, get_core_logger, set_global_debug,
        set_project, reinit_llm_logger, enable_webui_logging
    )
    from moose.web_ui import register_project
    from moose.framework.agent_core import ContainerManager
except ImportError:
    # Fallback for development mode
    from framework.logging import (
        init_core_logger, get_core_logger, set_global_debug,
        set_project, reinit_llm_logger, enable_webui_logging
    )
    from web_ui import register_project
    from framework.agent_core import ContainerManager


class RunCommand:
    """Command to run a Moose project."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the run command."""
        parser = subparser.add_parser(cmd, help='Run a Moose project with web UI')
        
        parser.add_argument(
            'project_id',
            type=str,
            nargs='?',
            default='default',
            help='Project ID to run (default: "default")'
        )
        
        parser.add_argument(
            '--no-web',
            action='store_true',
            help='Disable the web UI server'
        )

        parser.add_argument(
            '--no-agents',
            action='store_true',
            help='Do not start enabled agents; run only the web UI/logging loop'
        )

        parser.add_argument(
            '--force-rebuild',
            action='store_true',
            help='Force rebuild Docker images for enabled agents before starting'
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
        
        # Start web UI server unless disabled
        web_port_str = os.environ.get('MOOSE_WEB_UI_PORT')
        
        if args.no_web:
            logger.info("Web UI disabled via --no-web flag")
        elif not web_port_str:
            logger.info("MOOSE_WEB_UI_PORT not set, web UI will be disabled")
        else:
            try:
                web_port = int(web_port_str)
                server = register_project(project_id, port=web_port)
                enable_webui_logging(project_id)
                logger.info(f"Web UI available at http://localhost:{web_port}")
            except ValueError:
                logger.warning(f"Invalid MOOSE_WEB_UI_PORT value: {web_port_str}, web UI disabled")
            except ImportError as e:
                logger.warning(f"Could not start web UI: {e}")
                logger.warning("Install Flask with: pip install flask")
            except Exception as e:
                logger.warning(f"Could not start web UI: {e}")

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
        if enabled_agents:
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
            if manager is not None:
                try:
                    manager.cleanup_project_containers(project_id)
                except Exception as e:
                    logger.warning(f"Failed to cleanup project containers: {e}")
            logger.info("Project stopped.")
            sys.exit(0)
