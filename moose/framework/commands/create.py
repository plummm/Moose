import argparse
import os
import json
import re
from pathlib import Path
from framework.logging import setup_project_logger, init_core_logger, get_core_logger


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
    
    def create_project_config(self, project_dir):
        """Create an empty project config file."""
        logger = get_core_logger()
        config_path = os.path.join(project_dir, 'project_config.json')
        try:
            with open(config_path, 'w') as f:
                json.dump({}, f, indent=2)
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
    
    def create_litellm_config(self, project_dir):
        """Create LiteLLM config.yaml file in project directory."""
        logger = get_core_logger()
        
        # Get config file name from environment variable, default to config.yaml
        config_name = os.getenv("MOOSE_LITELLM_CONFIG_NAME", "config.yaml")
        config_path = project_dir / config_name
        
        try:
            # Read template from framework directory
            framework_dir = Path(__file__).parent.parent.parent
            template_path = framework_dir / "framework" / "llm_core" / "config.yaml.template"
            
            if template_path.exists():
                # Copy from template
                with open(template_path, 'r', encoding='utf-8') as f:
                    config_content = f.read()
            else:
                # Generate default config if template doesn't exist
                from framework.llm_core.config import ProxyConfig
                config_obj = ProxyConfig()
                config_obj.save_template(config_path)
                logger.info(f"Created LiteLLM config file: {config_path}")
                return str(config_path)
            
            # Write config file
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            
            logger.info(f"Created LiteLLM config file: {config_path}")
            return str(config_path)
        except Exception as e:
            logger.error(f"Failed to create LiteLLM config file: {e}")
            raise
    
    def run(self, args):
        """Execute the create command."""
        project_name = args.project_name
        debug = getattr(args, 'debug', False)
        logger = init_core_logger(debug=debug)
        logger.info(f"Creating project: {project_name}")
        
        # Get projects directory from environment variable
        projects_dir = os.getenv('MOOSE_PROJECTS_DIR')
        if not projects_dir:
            # Use console logger before project is created
            logger.error("MOOSE_PROJECTS_DIR environment variable is not set")
            logger.info("Please set it to the directory where you want to store projects")
            logger.info("Example: export MOOSE_PROJECTS_DIR=/path/to/projects")
            return 1
        
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
        proj_logger = setup_project_logger(project_dir, debug=debug, project_name=project_name)
        logger.update_core_logger(log_file=project_dir / 'moose.log', debug=debug)
        proj_logger.info(f"Project directory: {project_dir}")
        proj_logger.debug(f"Debug mode: {debug}")
        
        # Create project files
        try:
            config_path = self.create_project_config(project_dir)
            proj_logger.info(f"Created project config: {config_path}")
            
            workflow_path = self.create_workflow_file(project_dir)
            proj_logger.info(f"Created workflow file: {workflow_path}")
            
            litellm_config_path = self.create_litellm_config(project_dir)
            proj_logger.info(f"Created LiteLLM config: {litellm_config_path}")
            
            proj_logger.info(f"Project '{project_name}' created successfully!")
            proj_logger.info(f"Project location: {project_dir}")
            proj_logger.info(f"Log file location: {project_dir / 'moose.log'}")
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

