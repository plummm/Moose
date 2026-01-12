"""Command to start the Moose Web UI server."""

import os
from pathlib import Path

from moose.framework.logging import init_core_logger, get_core_logger, set_global_debug


class WebCommand:
    """Command to start the Moose Web UI."""

    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the web command."""
        parser = subparser.add_parser(cmd, help="Start the Moose Web UI server")
        parser.add_argument(
            "--port",
            type=int,
            default=None,
            help="Web UI port (overrides MOOSE_WEB_UI_PORT if set)",
        )
        parser.add_argument(
            "--projects-dir",
            type=str,
            default=None,
            help="Projects base directory (overrides MOOSE_PROJECTS_DIR if set)",
        )
        return parser

    def add_arguments(self, parser):
        """Add arguments to the web command parser."""
        # Arguments are handled in custom_subparser
        pass

    def run(self, args):
        """Start the web server (blocking)."""
        set_global_debug(getattr(args, "debug", False))
        init_core_logger()
        logger = get_core_logger()

        # Allow CLI override of projects directory by forcing env var for this process.
        projects_dir = getattr(args, "projects_dir", None)
        if projects_dir:
            os.environ["MOOSE_PROJECTS_DIR"] = str(Path(projects_dir).expanduser().resolve())
            logger.info(f"Using projects dir: {os.environ['MOOSE_PROJECTS_DIR']}")

        # Determine port: CLI > env > default
        port = getattr(args, "port", None)
        if port is None:
            web_port_str = os.environ.get("MOOSE_WEB_UI_PORT")
            if web_port_str:
                try:
                    port = int(web_port_str)
                except ValueError:
                    logger.warning(f"Invalid MOOSE_WEB_UI_PORT value: {web_port_str}; falling back to default 5000")
                    port = 5000
            else:
                port = 5000

        # Validate port
        try:
            port = int(port)
        except Exception:
            logger.error(f"Invalid port: {port}")
            return 1
        if port < 1 or port > 65535:
            logger.error(f"Port out of range (1-65535): {port}")
            return 1

        try:
            # Import lazily so `moose` still works without Flask unless `web` is invoked.
            from moose.web_ui.core_server import CoreWebServer

            server = CoreWebServer(port=port)
            logger.info(f"Starting Moose Web UI on http://localhost:{port}")
            server.start(blocking=True)
            return 0
        except ImportError as e:
            logger.error(f"Could not start web UI: {e}")
            logger.error("Install Flask with: pip install flask")
            return 1
        except KeyboardInterrupt:
            logger.info("Web UI stopped.")
            return 0
        except Exception as e:
            logger.error(f"Failed to start web UI: {e}")
            return 1

