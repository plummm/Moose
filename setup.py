"""Setup script for Moose framework."""

from setuptools import setup, find_packages
from pathlib import Path
import re

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

# Read requirements
requirements_file = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_file.exists():
    requirements = [
        line.strip()
        for line in requirements_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

docker_requirements_file = Path(__file__).parent / "requirements-docker.txt"
docker_requirements = []
if docker_requirements_file.exists():
    docker_requirements = [
        line.strip()
        for line in docker_requirements_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _req_name(requirement: str) -> str:
    base = requirement.split(";", 1)[0].strip()
    base = base.split("[", 1)[0].strip()
    return re.split(r"[<>=!~ ]", base, maxsplit=1)[0].strip().lower()


dev_deps = {"pytest", "pytest-cov"}
install_requires = [req for req in requirements if _req_name(req) not in dev_deps]
dev_requires = [req for req in requirements if _req_name(req) in dev_deps]

setup(
    name="moose",
    version="0.1.3",
    description="A modular agent framework built on LangGraph",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Xiaochen Zou",
    # Agents are deployed separately (e.g., mounted into /app in containers) and should NOT be part of the
    # installed `moose` package. Exclude them by package name patterns (find_packages does not accept paths).
    packages=find_packages(
        exclude=(
            "moose.tests",
            "moose.tests.*",
            "moose.agents",
            "moose.agents.*",
        )
    ),
    python_requires=">=3.10",
    install_requires=install_requires,
    extras_require={
        "dev": dev_requires,
        "docker": docker_requirements,
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)

