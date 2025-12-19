#!/usr/bin/env bash
set -euo pipefail

# Install Playwright browsers for this agent image.
# NOTE: This runs during Docker build (DockerfileGenerator calls ./setup.sh if present).

python -m playwright install --with-deps chromium


