"""Command to debug and deploy agents."""

import sys
import os
import importlib.util
import traceback
from pathlib import Path
from typing import Optional
try:
    from moose.framework.agent_core import AgentLoader, ContainerManager
    from moose.framework.logging import init_core_logger, get_core_logger
except ImportError:
    # Fallback for development mode
    from framework.agent_core import AgentLoader, ContainerManager
    from framework.logging import init_core_logger, get_core_logger


class AgentCommand:
    """Command to debug and deploy agents."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the agent command."""
        parser = subparser.add_parser(cmd, help='Debug and deploy agents')
        
        # Create subcommands
        subparsers = parser.add_subparsers(dest='subcommand', help='Subcommands')
        
        # Debug subcommand
        debug_parser = subparsers.add_parser('debug', help='Run agent locally for debugging')
        debug_parser.add_argument(
            '--name',
            type=str,
            required=True,
            help='Name of the agent to debug'
        )
        debug_parser.add_argument(
            '--debug',
            action='store_true',
            help='Enable debug logging (default: INFO level)'
        )
        
        # Deploy subcommand
        deploy_parser = subparsers.add_parser('deploy', help='Deploy agent in Docker container')
        deploy_parser.add_argument(
            '--name',
            type=str,
            required=True,
            help='Name of the agent to deploy'
        )
        deploy_parser.add_argument(
            '--port',
            type=int,
            default=None,
            help='Host port for agent HTTP server (default: from agent config)'
        )
        deploy_parser.add_argument(
            '--debug',
            action='store_true',
            help='Enable debug logging (default: INFO level)'
        )
        
        return parser
    
    def add_arguments(self, parser):
        """Add arguments to the agent command parser."""
        # Arguments are handled in custom_subparser
        pass
    
    def run(self, args):
        """Run the agent command."""
        # Initialize logging
        init_core_logger(debug=args.debug)
        logger = get_core_logger()
        
        if not hasattr(args, 'subcommand') or args.subcommand is None:
            logger.error("Subcommand required. Use 'debug' or 'deploy'")
            sys.exit(1)
        
        if args.subcommand == 'debug':
            self._run_debug(args.name, args.debug)
        elif args.subcommand == 'deploy':
            self._run_deploy(args.name, args.port, args.debug)
        else:
            logger.error(f"Unknown subcommand: {args.subcommand}")
            sys.exit(1)
    
    def _run_debug(self, agent_name: str, debug: bool):
        """Run agent locally for debugging."""
        logger = get_core_logger()
        
        try:
            logger.info(f"Starting agent '{agent_name}' in debug mode...")
            
            # Load agent
            loader = AgentLoader()
            
            # Validate agent exists
            if agent_name not in loader.discover_agents():
                logger.error(f"Agent '{agent_name}' not found")
                logger.info(f"Available agents: {', '.join(loader.discover_agents())}")
                sys.exit(1)
            
            # Get agent path and config
            agent_path = loader.get_agent_path(agent_name)
            config = loader.load_agent_config(agent_name)
            
            # Import and instantiate agent
            agent_file = agent_path / config.get("entry_point", "agent.py")
            
            if not agent_file.exists():
                logger.error(f"Agent entry point not found: {agent_file}")
                sys.exit(1)
            
            # Load agent module
            spec = importlib.util.spec_from_file_location("agent_module", agent_file)
            agent_module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(os.getcwd()))
            spec.loader.exec_module(agent_module)
            
            # Find agent class (look for class that extends BaseAgent)
            agent_class = None
            for name in dir(agent_module):
                obj = getattr(agent_module, name)
                if (isinstance(obj, type) and 
                    hasattr(obj, '__bases__') and
                    any('BaseAgent' in str(base) for base in obj.__bases__)):
                    agent_class = obj
                    break
            
            if not agent_class:
                logger.error("Could not find agent class in agent module")
                sys.exit(1)
            
            # Instantiate agent with debug flag
            logger.info(f"Loading agent class: {agent_class.__name__}")
            agent = agent_class(config_path=agent_path / "agent_config.json", debug=debug)
            
            # Get port from HTTP server config, ports config, or default
            port = 8000
            http_config = config.get("http_server", {})
            if http_config.get("port"):
                port = http_config["port"]
            elif config.get("ports") and len(config["ports"]) > 0:
                port = config["ports"][0].get("host", config["ports"][0].get("container", 8000))
            
            # List endpoints
            endpoints_info = ["/health", "/process"]
            if http_config.get("endpoints"):
                for ep in http_config["endpoints"]:
                    endpoints_info.append(f"{ep.get('method', 'POST')} {ep.get('path')}")
            
            # Run agent in HTTP mode
            logger.info(f"Starting agent HTTP server on port {port}...")
            logger.info(f"Agent '{agent_name}' is running. Press Ctrl+C to stop.")
            logger.info(f"Available endpoints:")
            for ep in endpoints_info:
                logger.info(f"  - http://localhost:{port}{ep}")
            
            try:
                agent.run(mode="http", port=port)
            except KeyboardInterrupt:
                logger.info("\nStopping agent...")
                agent.shutdown()
                logger.info("Agent stopped.")
        
        except Exception as e:
            logger.error(f"Failed to run agent in debug mode: {e}")
            logger.error(traceback.format_exc())
            sys.exit(1)
    
    def _run_deploy(self, agent_name: str, port: Optional[int], debug: bool):
        """Deploy agent in Docker container."""
        logger = get_core_logger()
        
        try:
            logger.info(f"Deploying agent '{agent_name}' in Docker container...")
            
            # Initialize container manager
            try:
                manager = ContainerManager()
            except Exception as e:
                logger.error(f"Failed to initialize container manager: {e}")
                logger.error("Make sure Docker is installed and running")
                sys.exit(1)
            
            # Validate agent exists
            loader = AgentLoader()
            if agent_name not in loader.discover_agents():
                logger.error(f"Agent '{agent_name}' not found")
                logger.info(f"Available agents: {', '.join(loader.discover_agents())}")
                sys.exit(1)
            
            # Get agent config
            config = loader.load_agent_config(agent_name)
            
            # Use default project_id for standalone deployment
            project_id = "default"
            
            # Check if container already running
            try:
                status = manager.get_container_status(agent_name, project_id)
                if status == "running":
                    logger.warning(f"Container for agent '{agent_name}' is already running")
                    container_id = manager.registry.get_container_id(project_id, agent_name)
                    logger.info(f"Container ID: {container_id[:12] if container_id else 'unknown'}")
                    
                    # Get port from config
                    container_port = 8000
                    if config.get("ports") and len(config["ports"]) > 0:
                        container_port = config["ports"][0].get("host", config["ports"][0].get("container", 8000))
                    
                    logger.info(f"Agent is accessible at: http://localhost:{container_port}")
                    logger.info("Use 'docker stop <container_id>' to stop the container")
                    return
            except Exception:
                # Container not running, continue
                pass
            
            # Build image
            logger.info("Building Docker image...")
            try:
                image_name = manager.build_agent_image(agent_name, force_rebuild=False)
                logger.info(f"Image built: {image_name}")
            except Exception as e:
                logger.error(f"Failed to build image: {e}")
                sys.exit(1)
            
            # Prepare environment variables
            environment = config.get("environment", {}).copy()
            if debug:
                environment["MOOSE_AGENT_DEBUG"] = "true"
            
            # Start container
            logger.info("Starting container...")
            try:
                container_id = manager.start_agent_container(
                    agent_name=agent_name,
                    project_id=project_id,
                    environment=environment
                )
                logger.info(f"Container started: {container_id[:12]}")
            except Exception as e:
                logger.error(f"Failed to start container: {e}")
                sys.exit(1)
            
            # Get port from config
            container_port = 8000
            if config.get("ports") and len(config["ports"]) > 0:
                container_port = config["ports"][0].get("host", config["ports"][0].get("container", 8000))
            
            logger.info("=" * 60)
            logger.info(f"Agent '{agent_name}' deployed successfully!")
            logger.info(f"Container ID: {container_id[:12]}")
            logger.info(f"Health check: http://localhost:{container_port}/health")
            logger.info(f"Process endpoint: http://localhost:{container_port}/process")
            logger.info("=" * 60)
            logger.info("To view logs: docker logs <container_id>")
            logger.info("To stop: docker stop <container_id>")
        
        except Exception as e:
            logger.error(f"Failed to deploy agent: {e}")
            logger.error(traceback.format_exc())
            sys.exit(1)
