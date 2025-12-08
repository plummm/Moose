"""Command to launch an interactive session with an agent."""

import sys
import json
import requests
from pathlib import Path
from framework.agent_core import AgentLoader, ContainerManager
from framework.logging import init_core_logger, get_core_logger


class AgentCommand:
    """Command to launch an interactive session with an agent."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the agent command."""
        return subparser.add_parser(cmd, help='Launch interactive session with an agent')
    
    def add_arguments(self, parser):
        """Add arguments to the agent command parser."""
        parser.add_argument(
            'agent_name',
            type=str,
            help='Name of the agent to interact with'
        )
        parser.add_argument(
            '--project-id',
            type=str,
            default='interactive',
            help='Project ID for the agent session (default: interactive)'
        )
        parser.add_argument(
            '--port',
            type=int,
            default=8000,
            help='Port for agent HTTP server (default: 8000)'
        )
        parser.add_argument(
            '--mode',
            type=str,
            choices=['http', 'stdin', 'local'],
            default='local',
            help='Interaction mode: http (connect to running container), stdin (pipe mode), or local (run locally)'
        )
    
    def run(self, args):
        """Run the agent command."""
        # Initialize logging
        init_core_logger(debug=args.debug)
        logger = get_core_logger()
        
        agent_name = args.agent_name
        project_id = args.project_id
        port = args.port
        mode = args.mode
        
        logger.info(f"Launching interactive session with agent: {agent_name}")
        
        if mode == 'local':
            # Run agent locally
            self._run_local(agent_name, args.debug)
        elif mode == 'http':
            # Connect to running container
            self._run_http_interactive(agent_name, project_id, port)
        elif mode == 'stdin':
            # Pipe mode
            self._run_stdin_mode(agent_name, project_id, args.debug)
        else:
            logger.error(f"Unknown mode: {mode}")
            sys.exit(1)
    
    def _run_local(self, agent_name: str, debug: bool):
        """Run agent locally in interactive mode."""
        logger = get_core_logger()
        
        try:
            # Load agent
            loader = AgentLoader()
            
            # Validate agent exists
            if agent_name not in loader.discover_agents():
                logger.error(f"Agent '{agent_name}' not found")
                sys.exit(1)
            
            # Get agent path
            agent_path = loader.get_agent_path(agent_name)
            config = loader.load_agent_config(agent_name)
            
            # Import and instantiate agent
            import importlib.util
            agent_file = agent_path / config.get("entry_point", "agent.py")
            
            if not agent_file.exists():
                logger.error(f"Agent entry point not found: {agent_file}")
                sys.exit(1)
            
            # Load agent module
            spec = importlib.util.spec_from_file_location("agent_module", agent_file)
            agent_module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(agent_path))
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
            
            # Instantiate agent
            agent = agent_class(config_path=agent_path / "agent_config.json", debug=debug)
            
            # Start interactive session
            logger.info(f"Agent '{agent_name}' loaded. Starting interactive session...")
            logger.info("Enter your input (JSON format). Type 'exit' or 'quit' to exit.")
            logger.info("=" * 60)
            
            while True:
                try:
                    # Read input
                    user_input = input("\n> ").strip()
                    
                    if user_input.lower() in ['exit', 'quit', 'q']:
                        logger.info("Exiting interactive session...")
                        break
                    
                    if not user_input:
                        continue
                    
                    # Parse input
                    try:
                        if user_input.startswith('{'):
                            input_data = json.loads(user_input)
                        else:
                            # Treat as plain text, wrap in standard format
                            input_data = {"input": user_input}
                    except json.JSONDecodeError:
                        # Treat as plain text
                        input_data = {"input": user_input}
                    
                    # Process
                    formatted_input = agent._format_input(input_data)
                    formatted_output = agent._process_with_formatting(formatted_input)
                    
                    # Display result
                    print("\n" + "=" * 60)
                    print("RESULT:")
                    print(json.dumps(formatted_output, indent=2))
                    print("=" * 60)
                    
                except KeyboardInterrupt:
                    logger.info("\nExiting interactive session...")
                    break
                except Exception as e:
                    logger.error(f"Error: {e}", exc_info=debug)
                    print(f"\nError: {e}")
        
        except Exception as e:
            logger.error(f"Failed to run agent locally: {e}", exc_info=debug)
            sys.exit(1)
    
    def _run_http_interactive(self, agent_name: str, project_id: str, port: int):
        """Connect to agent via HTTP and run interactive session."""
        logger = get_core_logger()
        
        # Check if container is running
        try:
            manager = ContainerManager()
            status = manager.get_container_status(agent_name, project_id)
            
            if status != "running":
                logger.error(f"Agent container is not running. Status: {status}")
                logger.info("Start the container first or use --mode local")
                sys.exit(1)
            
            # Get container info to find the port
            # For now, assume port is from config or use provided port
            base_url = f"http://localhost:{port}"
            
            logger.info(f"Connecting to agent at {base_url}")
            logger.info("Enter your input (JSON format). Type 'exit' or 'quit' to exit.")
            logger.info("=" * 60)
            
            while True:
                try:
                    # Read input
                    user_input = input("\n> ").strip()
                    
                    if user_input.lower() in ['exit', 'quit', 'q']:
                        logger.info("Exiting interactive session...")
                        break
                    
                    if not user_input:
                        continue
                    
                    # Parse input
                    try:
                        if user_input.startswith('{'):
                            input_data = json.loads(user_input)
                        else:
                            input_data = {"input": user_input}
                    except json.JSONDecodeError:
                        input_data = {"input": user_input}
                    
                    # Send request
                    response = requests.post(
                        f"{base_url}/process",
                        json=input_data,
                        timeout=60
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        print("\n" + "=" * 60)
                        print("RESULT:")
                        print(json.dumps(result, indent=2))
                        print("=" * 60)
                    else:
                        print(f"\nError: {response.status_code} - {response.text}")
                
                except KeyboardInterrupt:
                    logger.info("\nExiting interactive session...")
                    break
                except Exception as e:
                    logger.error(f"Error: {e}")
                    print(f"\nError: {e}")
        
        except Exception as e:
            logger.error(f"Failed to connect to agent: {e}")
            sys.exit(1)
    
    def _run_stdin_mode(self, agent_name: str, project_id: str, debug: bool):
        """Run in stdin mode (for piping)."""
        logger = get_core_logger()
        logger.info("Running in stdin mode (non-interactive)")
        
        # This mode reads from stdin and writes to stdout
        # Similar to local mode but non-interactive
        try:
            loader = AgentLoader()
            agent_path = loader.get_agent_path(agent_name)
            config = loader.load_agent_config(agent_name)
            
            # Import and instantiate agent (similar to _run_local)
            import importlib.util
            agent_file = agent_path / config.get("entry_point", "agent.py")
            spec = importlib.util.spec_from_file_location("agent_module", agent_file)
            agent_module = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(agent_path))
            spec.loader.exec_module(agent_module)
            
            # Find agent class
            agent_class = None
            for name in dir(agent_module):
                obj = getattr(agent_module, name)
                if (isinstance(obj, type) and 
                    hasattr(obj, '__bases__') and
                    any('BaseAgent' in str(base) for base in obj.__bases__)):
                    agent_class = obj
                    break
            
            if not agent_class:
                logger.error("Could not find agent class")
                sys.exit(1)
            
            agent = agent_class(config_path=agent_path / "agent_config.json", debug=debug)
            
            # Read from stdin and process
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    input_data = json.loads(line)
                except json.JSONDecodeError:
                    input_data = {"input": line}
                
                formatted_input = agent._format_input(input_data)
                formatted_output = agent._process_with_formatting(formatted_input)
                
                print(json.dumps(formatted_output))
        
        except Exception as e:
            logger.error(f"Error in stdin mode: {e}", exc_info=debug)
            sys.exit(1)

