"""Command to run a Moose project with web UI."""

import os
import sys
from pathlib import Path

try:
    from moose.framework.logging import (
        init_core_logger, get_core_logger, set_global_debug,
        set_project, reinit_llm_logger, enable_webui_logging
    )
    from moose.web_ui import register_project
except ImportError:
    # Fallback for development mode
    from framework.logging import (
        init_core_logger, get_core_logger, set_global_debug,
        set_project, reinit_llm_logger, enable_webui_logging
    )
    from web_ui import register_project


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
        
        # Set up project for logging
        # All logs will go to projects/<project_id>/logs/
        set_project(project_id, Path.cwd() / "projects")
        
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
        
        # Placeholder for project execution logic
        # This is where you would start agents, workflows, etc.
        logger.info("Project is running. Press Ctrl+C to stop.")
        
        try:
            # Keep the main thread alive
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nStopping project...")
            logger.info("Project stopped.")
            sys.exit(0)
