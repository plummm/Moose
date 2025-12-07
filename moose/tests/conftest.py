"""Pytest configuration and fixtures."""

import os
import pytest
from pathlib import Path


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "docker: marks tests as requiring Docker daemon"
    )
    config.addinivalue_line(
        "markers", "llm: marks tests as requiring LLM API keys"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow running"
    )


@pytest.fixture(scope="session")
def check_docker():
    """Check if Docker is available."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def check_api_keys():
    """Check which API keys are available."""
    return {
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "google": bool(os.getenv("GOOGLE_API_KEY")),
    }


@pytest.fixture(autouse=True)
def skip_if_no_docker(request, check_docker):
    """Skip docker-marked tests if Docker is not available."""
    if request.node.get_closest_marker("docker") and not check_docker:
        pytest.skip("Docker daemon not available")


@pytest.fixture(autouse=True)
def skip_if_no_api_keys(request, check_api_keys):
    """Skip LLM tests if no API keys are available."""
    if request.node.get_closest_marker("llm"):
        has_any_key = any(check_api_keys.values())
        if not has_any_key:
            pytest.skip("No LLM API keys available")

