"""
Helpers for resolving cross-agent HTTP endpoints.

Design goal:
- In local debug mode (`moose agent debug` or `moose run --agent-debug`), agents run on the host.
  Inter-agent HTTP calls should target `https://localhost:<port>` (per project conventions).
- In Docker mode, agents run in a per-project Docker network created by ContainerManager.ensure_project_network(),
  and can be reached by their container name: `{image_prefix}{agent_name}-{project_id}`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _in_docker() -> bool:
    # Common, cheap checks.
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists() and ("docker" in cgroup.read_text(errors="ignore") or "containerd" in cgroup.read_text(errors="ignore")):
            return True
    except Exception:
        pass
    return False


def get_project_network_name(project_id: str) -> str:
    """
    Return the Docker network name for a project, matching ContainerManager.ensure_project_network().
    """
    network_prefix = os.getenv("MOOSE_DOCKER_NETWORK_PREFIX", "moose-project-")
    return f"{network_prefix}{project_id}"


def get_agent_container_host(agent_name: str, project_id: str) -> str:
    """
    Return the Docker container hostname used for service discovery within the project network.
    """
    image_prefix = os.getenv("MOOSE_DOCKER_IMAGE_PREFIX", "moose-agent-")
    return f"{image_prefix}{agent_name}-{project_id}"


def resolve_agent_base_url(
    *,
    agent_name: str,
    port: int,
    project_id: Optional[str] = None,
    local_scheme: str = "https",
    docker_scheme: str = "http",
) -> str:
    """
    Resolve the base URL for an agent.

    - Local debug: `<local_scheme>://localhost:<port>`
    - Docker: `<docker_scheme>://{image_prefix}{agent_name}-{project_id}:<port>`
    """
    pid = (project_id or os.getenv("MOOSE_PROJECT_ID") or "default").strip() or "default"
    if not _in_docker():
        return f"{local_scheme}://localhost:{int(port)}"
    host = get_agent_container_host(agent_name, pid)
    return f"{docker_scheme}://{host}:{int(port)}"


