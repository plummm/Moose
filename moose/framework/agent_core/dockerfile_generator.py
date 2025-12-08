"""Dockerfile generation from templates and agent configuration."""

from pathlib import Path
from typing import Dict, Any, Optional
from framework.logging import get_core_logger


class DockerfileGenerator:
    """Generates Dockerfiles for agents from templates and configuration."""
    
    def __init__(self):
        """Initialize the Dockerfile generator."""
        self.logger = get_core_logger()
    
    def generate_dockerfile(
        self,
        agent_path: Path,
        config: Dict[str, Any],
        output_path: Optional[Path] = None
    ) -> Path:
        """
        Generate a Dockerfile for an agent.
        
        Args:
            agent_path: Path to agent directory
            config: Agent configuration dictionary
            output_path: Where to write Dockerfile. If None, writes to agent_path/Dockerfile
        
        Returns:
            Path to generated Dockerfile
        """
        if output_path is None:
            output_path = agent_path / "Dockerfile"
        
        # Get template
        template = self._get_template()
        
        # Get configuration values
        python_version = config.get("python_version", "3.11")
        system_packages = config.get("system_packages", [])
        entry_point = config.get("entry_point", "agent.py")
        has_setup_script = (agent_path / "setup.sh").exists()
        has_requirements = (agent_path / "requirements.txt").exists()
        
        # Get agent name from config or path
        agent_name = config.get("name") or agent_path.name
        
        # Generate Dockerfile content
        dockerfile_content = self._render_template(
            template=template,
            python_version=python_version,
            system_packages=system_packages,
            entry_point=entry_point,
            has_setup_script=has_setup_script,
            has_requirements=has_requirements,
            agent_name=agent_name
        )
        
        # Write Dockerfile
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(dockerfile_content)
            
            self.logger.info(f"Generated Dockerfile: {output_path}")
            return output_path
        except Exception as e:
            self.logger.error(f"Failed to generate Dockerfile: {e}")
            raise
    
    def _get_template(self) -> str:
        """Get the Dockerfile template."""
        # Try to load from framework directory
        framework_dir = Path(__file__).parent
        template_path = framework_dir / "Dockerfile.template"
        
        if template_path.exists():
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        
        # Return default template
        return self._get_default_template()
    
    def _get_default_template(self) -> str:
        """Get default Dockerfile template."""
        return """# Auto-generated Dockerfile for Moose Agent
FROM python:{python_version}-slim

# Set working directory
WORKDIR /app

# Install system packages if specified
{% if system_packages %}
RUN apt-get update && apt-get install -y \\
    {system_packages_str} \\
    && rm -rf /var/lib/apt/lists/*
{% endif %}

# Install Moose framework
# The build context should be the moose/ directory, so framework is at ./framework
COPY framework /tmp/moose/framework
COPY setup.py /tmp/moose/
RUN pip install --no-cache-dir /tmp/moose && rm -rf /tmp/moose

# Copy agent files (from agents/<agent_name>/)
COPY agents/{agent_name} /app

# Run setup script if it exists
{% if has_setup_script %}
RUN chmod +x setup.sh && ./setup.sh
{% endif %}

# Install Python dependencies if requirements.txt exists
{% if has_requirements %}
RUN pip install --no-cache-dir -r requirements.txt
{% endif %}

# Set entry point
CMD ["python", "{entry_point}"]
"""
    
    def _render_template(
        self,
        template: str,
        python_version: str,
        system_packages: bool,
        entry_point: str,
        has_setup_script: bool,
        has_requirements: bool,
        agent_name: str
    ) -> str:
        """Render template with values."""
        import re
        
        # Replace Python version
        content = template.replace("{python_version}", python_version)
        
        # Replace system packages
        if system_packages:
            packages_str = " \\\n    ".join(system_packages)
            # Replace the conditional block with actual content
            content = re.sub(
                r'{% if system_packages %}\s*RUN apt-get update && apt-get install -y \\.*{system_packages_str} \\.*&& rm -rf /var/lib/apt/lists/\*\s*{% endif %}',
                f'RUN apt-get update && apt-get install -y \\\n    {packages_str} \\\n    && rm -rf /var/lib/apt/lists/*',
                content,
                flags=re.DOTALL
            )
        else:
            # Remove entire system packages block
            content = re.sub(
                r'{% if system_packages %}.*?{% endif %}',
                '',
                content,
                flags=re.DOTALL
            )
        
        # Replace setup script conditional
        if has_setup_script:
            content = re.sub(
                r'{% if has_setup_script %}.*RUN chmod \+x setup\.sh && \./setup\.sh.*{% endif %}',
                'RUN chmod +x setup.sh && ./setup.sh',
                content,
                flags=re.DOTALL
            )
        else:
            content = re.sub(
                r'{% if has_setup_script %}.*?{% endif %}',
                '',
                content,
                flags=re.DOTALL
            )
        
        # Replace requirements conditional
        if has_requirements:
            content = re.sub(
                r'{% if has_requirements %}.*RUN pip install --no-cache-dir -r requirements\.txt.*{% endif %}',
                'RUN pip install --no-cache-dir -r requirements.txt',
                content,
                flags=re.DOTALL
            )
        else:
            content = re.sub(
                r'{% if has_requirements %}.*?{% endif %}',
                '',
                content,
                flags=re.DOTALL
            )
        
        # Replace entry point
        content = content.replace("{entry_point}", entry_point)
        
        # Replace agent name
        content = content.replace("{agent_name}", agent_name)
        
        return content

