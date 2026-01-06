#!/usr/bin/env bash
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/alpacahq/alpaca-mcp-server.git"
IMAGE_DEFAULT="mcp/alpaca-mcp-server:local"
NETWORK_DEFAULT="moose-autotrade-alpaca_trader"

usage() {
  cat <<'USAGE'
alpaca_mcp_manager.sh <command> [options]

Commands:
  clone            Clone repo if missing
  update           Git pull + refresh uv lock
  build            Build docker image
  create           Create (run) a new container for an account
  start            Start existing container
  stop             Stop existing container
  rm               Remove container
  restart          Restart container
  logs             Tail container logs
  status           Show containers + health basics
  list             List managed containers (by name prefix)

Common options:
  --repo-path PATH         Where to clone repo
  --repo-url URL           Repo URL (default: alpacahq/alpaca-mcp-server)
  --image NAME:TAG         Docker image name (default: mcp/alpaca-mcp-server:local)
  --network NAME           Docker network, need to be the same network as alpaca_trader (default: moose-autotrade-alpaca_trader)

create options:
  --account-id ID          Account identifier (required)
  --env-file PATH          Per-account env file (required)
  --port HOSTPORT          Host port to bind (required)
  --name NAME              Container name (default: alpaca-mcp-<account-id>)
  --host 0.0.0.0           Bind server host inside container (default: 0.0.0.0)
  --container-port 8100    Server port inside container (default: 8100)

Examples:
  ./alpaca_mcp_manager.sh clone --repo-path ~/repos/alpaca-mcp-server
  ./alpaca_mcp_manager.sh build --repo-path ~/repos/alpaca-mcp-server --image mcp/alpaca:latest
  ./alpaca_mcp_manager.sh create --repo-path ~/repos/alpaca-mcp-server --image mcp/alpaca:latest \
      --account-id paper_main --env-file ~/secrets/alpaca/paper_main.env --port 18001
  ./alpaca_mcp_manager.sh logs --name alpaca-mcp-paper_main
USAGE
}

die() { echo "ERROR: $*" >&2; exit 1; }

# Defaults
CMD="${1:-}"; shift || true
REPO_PATH=""
REPO_URL="$REPO_URL_DEFAULT"
IMAGE="$IMAGE_DEFAULT"
NETWORK="$NETWORK_DEFAULT"

ACCOUNT_ID=""
ENV_FILE=""
HOST_PORT=""
CONTAINER_NAME=""
HOST_BIND="0.0.0.0"
CONTAINER_PORT="8100"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path) REPO_PATH="${2:-}"; shift 2;;
    --repo-url) REPO_URL="${2:-}"; shift 2;;
    --image) IMAGE="${2:-}"; shift 2;;
    --network) NETWORK="${2:-}"; shift 2;;

    --account-id) ACCOUNT_ID="${2:-}"; shift 2;;
    --env-file) ENV_FILE="${2:-}"; shift 2;;
    --port) HOST_PORT="${2:-}"; shift 2;;
    --name) CONTAINER_NAME="${2:-}"; shift 2;;
    --host) HOST_BIND="${2:-}"; shift 2;;
    --container-port) CONTAINER_PORT="${2:-}"; shift 2;;

    -h|--help) usage; exit 0;;
    *) die "Unknown arg: $1";;
  esac
done

[[ -n "$CMD" ]] || { usage; exit 1; }

ensure_network() {
  if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network create "$NETWORK" >/dev/null
  fi
}

repo_clone() {
  [[ -n "$REPO_PATH" ]] || die "--repo-path is required"
  if [[ -d "$REPO_PATH/.git" ]]; then
    echo "Repo exists at $REPO_PATH"
    return
  fi
  mkdir -p "$(dirname "$REPO_PATH")"
  git clone "$REPO_URL" "$REPO_PATH"
}

repo_update() {
  [[ -n "$REPO_PATH" ]] || die "--repo-path is required"
  [[ -d "$REPO_PATH/.git" ]] || die "Repo not found; run clone first"
  ( cd "$REPO_PATH" && git pull )
  # Fix stale lock issues: refresh uv lock (requires uv in build stage later too)
  # We do it outside Docker to keep diffs visible; you can skip if you want fully-containerized build.
  if command -v uv >/dev/null 2>&1; then
    ( cd "$REPO_PATH" && uv lock --upgrade )
  else
    echo "Note: 'uv' not found locally; skipping local uv lock refresh."
    echo "If your Dockerfile uses '--frozen', consider refreshing uv.lock inside repo or adjusting Dockerfile."
  fi
}

repo_build() {
  [[ -n "$REPO_PATH" ]] || die "--repo-path is required"
  [[ -d "$REPO_PATH" ]] || die "Repo not found; run clone first"
  ( cd "$REPO_PATH" && docker build -t "$IMAGE" . )
}

container_name() {
  if [[ -n "$CONTAINER_NAME" ]]; then
    echo "$CONTAINER_NAME"
  else
    echo "alpaca-mcp-$ACCOUNT_ID"
  fi
}

create_container() {
  [[ -n "$ACCOUNT_ID" ]] || die "--account-id is required"
  [[ -n "$ENV_FILE" ]] || die "--env-file is required"
  [[ -n "$HOST_PORT" ]] || die "--port is required"
  [[ -f "$ENV_FILE" ]] || die "env-file not found: $ENV_FILE"
  ensure_network

  local name; name="$(container_name)"

  # Remove any existing container with same name to allow recreate.
  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    docker rm -f "$name" >/dev/null || true
  fi

  docker run -d \
    --name "$name" \
    --restart unless-stopped \
    --network "$NETWORK" \
    -p "${HOST_PORT}:${CONTAINER_PORT}" \
    --env-file "$ENV_FILE" \
    "$IMAGE" \
    alpaca-mcp-server serve --transport streamable-http --host "$HOST_BIND" --port "$CONTAINER_PORT"

  echo "Created: $name"
  echo "mcp_url (from other containers on same network): http://$name:$CONTAINER_PORT/mcp"
  echo "mcp_url (from host): http://localhost:$HOST_PORT/mcp"
}

start_container() {
  local name="${CONTAINER_NAME:-}"
  [[ -n "$name" ]] || [[ -n "$ACCOUNT_ID" ]] || die "need --name or --account-id"
  [[ -n "$name" ]] || name="alpaca-mcp-$ACCOUNT_ID"
  docker start "$name"
}

stop_container() {
  local name="${CONTAINER_NAME:-}"
  [[ -n "$name" ]] || [[ -n "$ACCOUNT_ID" ]] || die "need --name or --account-id"
  [[ -n "$name" ]] || name="alpaca-mcp-$ACCOUNT_ID"
  docker stop "$name"
}

rm_container() {
  local name="${CONTAINER_NAME:-}"
  [[ -n "$name" ]] || [[ -n "$ACCOUNT_ID" ]] || die "need --name or --account-id"
  [[ -n "$name" ]] || name="alpaca-mcp-$ACCOUNT_ID"
  docker rm -f "$name"
}

restart_container() {
  local name="${CONTAINER_NAME:-}"
  [[ -n "$name" ]] || [[ -n "$ACCOUNT_ID" ]] || die "need --name or --account-id"
  [[ -n "$name" ]] || name="alpaca-mcp-$ACCOUNT_ID"
  docker restart "$name"
}

logs_container() {
  local name="${CONTAINER_NAME:-}"
  [[ -n "$name" ]] || [[ -n "$ACCOUNT_ID" ]] || die "need --name or --account-id"
  [[ -n "$name" ]] || name="alpaca-mcp-$ACCOUNT_ID"
  docker logs -f "$name"
}

status() {
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | sed -n '1p;/alpaca-mcp-/p'
}

list_managed() {
  docker ps -a --format 'table {{.Names}}\t{{.Status}}' | sed -n '1p;/alpaca-mcp-/p'
}

case "$CMD" in
  clone) repo_clone;;
  update) repo_update;;
  build) repo_build;;
  create) repo_clone; repo_update || true; repo_build; create_container;;
  start) start_container;;
  stop) stop_container;;
  rm) rm_container;;
  restart) restart_container;;
  logs) logs_container;;
  status) status;;
  list) list_managed;;
  *) usage; exit 1;;
esac