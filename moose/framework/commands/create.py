import argparse
import os
import json
import re
from pathlib import Path
try:
    from moose.framework.logging import init_core_logger, get_core_logger, set_global_debug, set_project, get_project_logger
    from moose.framework.agent_core import AgentLoader
except ImportError:
    # Fallback for development mode
    from framework.logging import init_core_logger, get_core_logger, set_global_debug, set_project, get_project_logger
    from framework.agent_core import AgentLoader


class CreateCommand:
    """Command to create a new Moose project."""
    
    def custom_subparser(self, subparser, cmd):
        """Create and return a subparser for the create command."""
        return subparser.add_parser(cmd, help='Create a new project')
    
    def add_arguments(self, parser):
        """Add arguments to the create command parser."""
        parser.add_argument(
            'project_name',
            type=str,
            help='Name of the project to create'
        )
        parser.add_argument(
            '--agents',
            nargs='+',
            default=[],
            help='List of agents to enable for this project (e.g., --agents news_scraper finance_office)'
        )
    
    def validate_project_name(self, name):
        """Validate project name format."""
        if not name:
            raise ValueError("Project name cannot be empty")
        
        # Allow alphanumeric, underscores, and hyphens
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise ValueError(
                "Project name can only contain alphanumeric characters, "
                "underscores, and hyphens"
            )
        
        # Check for reserved names
        reserved = ['framework', 'moose', 'commands']
        if name.lower() in reserved:
            raise ValueError(f"'{name}' is a reserved name and cannot be used")
        
        return True
    
    def create_project_config(self, project_dir: Path, enabled_agents: list[str]):
        """Create a project config file (including enabled agents)."""
        logger = get_core_logger()
        config_path = os.path.join(project_dir, 'project_config.json')
        try:
            with open(config_path, 'w') as f:
                json.dump(
                    {
                        "enabled_agents": enabled_agents,
                        "created_at": __import__("datetime").datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )
            logger.info(f"Created project config file: {config_path}")
            return config_path
        except Exception as e:
            logger.error(f"Failed to create project config: {e}")
            raise
    
    def create_workflow_file(self, project_dir):
        """Create a LangGraph workflow template file."""
        logger = get_core_logger()
        workflow_path = os.path.join(project_dir, 'workflow.py')
        
        workflow_content = '''"""LangGraph workflow definition for this project."""

from typing import TypedDict
from langgraph.graph import StateGraph, END


# Define the state structure for your workflow
class WorkflowState(TypedDict):
    """State that flows through the workflow."""
    # Add your state fields here
    # Example: messages: list
    pass


# Initialize the state graph
workflow = StateGraph(WorkflowState)

# Add nodes (agents) to the workflow
# Example:
# workflow.add_node("agent_name", agent_function)

# Define the workflow edges
# Example:
# workflow.set_entry_point("agent_name")
# workflow.add_edge("agent_name", END)

# Compile the graph
app = workflow.compile()

# Export for use
__all__ = ['app', 'workflow', 'WorkflowState']
'''
        
        try:
            with open(workflow_path, 'w') as f:
                f.write(workflow_content)
            logger.info(f"Created workflow file: {workflow_path}")
            return workflow_path
        except Exception as e:
            logger.error(f"Failed to create workflow file: {e}")
            raise
    
    def create_model_config(self, project_dir):
        """Create LLM config.yaml file in project directory."""
        logger = get_core_logger()
        
        # Get config file name from environment variable, default to config.yaml
        config_name = os.getenv("MOOSE_LLM_CONFIG_NAME", "model_config.yaml")
        config_path = project_dir / config_name
        
        try:
            # Read template from framework directory
            framework_dir = Path(__file__).parent.parent.parent
            template_path = framework_dir / "framework" / "llm_core" / "model_config.yaml.template"
            
            if template_path.exists():
                # Copy from template
                with open(template_path, 'r', encoding='utf-8') as f:
                    config_content = f.read()
            else:
                # Generate default config if template doesn't exist
                from framework.llm_core.config import ModelConfig
                config_obj = ModelConfig()
                config_obj.save_template(config_path)
                logger.info(f"Created LLM config file: {config_path}")
                return str(config_path)
            
            # Write config file
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            
            logger.info(f"Created LLM config file: {config_path}")
            return str(config_path)
        except Exception as e:
            logger.error(f"Failed to create LLM config file: {e}")
            raise
    
    def run(self, args):
        """Execute the create command."""
        project_name = args.project_name
        debug = getattr(args, 'debug', False)
        set_global_debug(debug)
        logger = init_core_logger()
        logger.info(f"Creating project: {project_name}")
        
        # Prefer projects directory from environment variable; fallback to ./projects
        projects_dir = os.getenv('MOOSE_PROJECTS_DIR') or str(Path.cwd() / "projects")
        
        projects_path = Path(projects_dir)
        
        # Validate project name
        try:
            self.validate_project_name(project_name)
        except ValueError as e:
            logger.error(f"Invalid project name: {e}")
            return 1
        
        # Check if project already exists
        project_dir = projects_path / project_name
        if project_dir.exists():
            logger.error(f"Project directory already exists: {project_dir}")
            return 1

        # Validate requested agents (if any)
        enabled_agents: list[str] = []
        try:
            requested = [str(a).strip() for a in (getattr(args, "agents", None) or []) if str(a).strip()]
            if requested:
                loader = AgentLoader()
                available = set(loader.discover_agents())
                missing = [a for a in requested if a not in available]
                if missing:
                    logger.error(f"Unknown agent(s): {', '.join(missing)}")
                    logger.info(f"Available agents: {', '.join(sorted(available))}")
                    return 1
                enabled_agents = requested
        except Exception as e:
            logger.error(f"Failed to validate agents: {e}")
            return 1
        
        # Create projects directory if it doesn't exist
        if not projects_path.exists():
            try:
                projects_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Could not create projects directory: {e}")
                return 1
        
        # Create project directory
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Could not create project directory: {e}")
            return 1
        
        # Setup project logger now that project directory exists
        set_project(project_name, projects_path)
        proj_logger = get_project_logger(project_name, debug=debug)
        proj_logger.info(f"Project directory: {project_dir}")
        proj_logger.debug(f"Debug mode: {debug}")
        
        # Create project files
        try:
            config_path = self.create_project_config(project_dir, enabled_agents=enabled_agents)
            proj_logger.info(f"Created project config: {config_path}")
            
            workflow_path = self.create_workflow_file(project_dir)
            proj_logger.info(f"Created workflow file: {workflow_path}")
            
            llm_config_path = self.create_model_config(project_dir)
            proj_logger.info(f"Created LLM config: {llm_config_path}")
            
            proj_logger.info(f"Project '{project_name}' created successfully!")
            proj_logger.info(f"Project location: {project_dir}")
            proj_logger.info(f"Log directory: {project_dir / 'logs'}")
            if enabled_agents:
                proj_logger.info(f"Enabled agents: {', '.join(enabled_agents)}")
            return 0
        except Exception as e:
            proj_logger.error(f"Failed to create project files: {e}")
            # Clean up project directory if file creation failed
            try:
                project_dir.rmdir()
                logger.debug("Cleaned up project directory after failure")
            except:
                pass
            return 1

