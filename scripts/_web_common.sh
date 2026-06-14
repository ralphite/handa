#!/bin/bash

SCRIPT_DIR=$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PUBLIC_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
WEB_DIR="$PUBLIC_ROOT/src/web"
LOG_DIR="${HANDA_LOG_DIR:-$PUBLIC_ROOT/tmp}"
PID_DIR="$LOG_DIR/pids"
HOST="${HANDA_HOST:-127.0.0.1}"
API_PORT="${HANDA_WEB_API_PORT:-5086}"
WEB_PORT="${HANDA_WEB_PORT:-${HANDA_FE_PORT:-8086}}"
LEGACY_API_PORT="${HANDA_LEGACY_WEB_API_PORT:-8766}"
LEGACY_WEB_PORT="${HANDA_LEGACY_WEB_PORT:-${HANDA_LEGACY_FE_PORT:-5173}}"
UV_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$LOG_DIR/venv}"

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$PID_DIR"
}

use_node_24() {
  if [ -s "$HOME/.nvm/nvm.sh" ]; then
    unset npm_config_prefix
    unset NPM_CONFIG_PREFIX
    # shellcheck source=/dev/null
    source "$HOME/.nvm/nvm.sh"
    nvm use 24 >/dev/null
    return
  fi

  local node_major
  node_major=$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || true)
  if [ "$node_major" != "24" ]; then
    echo "Node 24 is required. Install nvm or put Node 24 on PATH." >&2
    exit 1
  fi
}

ensure_frontend_dependencies() {
  if [ -d "$WEB_DIR/node_modules" ]; then
    return
  fi

  echo "Installing frontend dependencies..."
  (cd "$WEB_DIR" && npm ci)
}

stop_process_tree() {
  local pid="$1"
  local current_pgid="$2"
  local signal="${3:-TERM}"

  if ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
  if [ -n "$pgid" ] && [ "$pgid" != "$current_pgid" ]; then
    kill "-$signal" "-$pgid" 2>/dev/null || kill "-$signal" "$pid" 2>/dev/null || true
    return
  fi

  kill "-$signal" "$pid" 2>/dev/null || true
}

stop_pid_files() {
  local current_pgid
  current_pgid=$(ps -o pgid= -p "$$" | tr -d ' ')

  local pid_file
  for pid_file in "$PID_DIR"/*.pid; do
    if [ ! -e "$pid_file" ]; then
      continue
    fi

    local pid
    pid=$(cat "$pid_file")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping previous service from $(basename "$pid_file"): $pid"
      stop_process_tree "$pid" "$current_pgid"
    fi
    rm -f "$pid_file"
  done
  sleep 1
}

stop_ports() {
  local collected_pids=()
  local port

  for port in "$@"; do
    local port_pids
    port_pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    if [ -n "$port_pids" ]; then
      echo "Stopping process on port $port: $port_pids"
      while IFS= read -r pid; do
        if [ -n "$pid" ]; then
          collected_pids+=("$pid")
        fi
      done <<< "$port_pids"
    fi
  done

  if [ "${#collected_pids[@]}" -eq 0 ]; then
    return
  fi

  local current_pgid
  current_pgid=$(ps -o pgid= -p "$$" | tr -d ' ')

  local pid
  for pid in $(printf "%s\n" "${collected_pids[@]}" | sort -u); do
    stop_process_tree "$pid" "$current_pgid"
  done
  sleep 1
}

start_in_dir() {
  local label="$1"
  local url="$2"
  local pid_name="$3"
  local log_name="$4"
  local cwd="$5"
  shift 5

  echo "Starting $label on $url"
  local pid
  pid=$(python3 - "$cwd" "$LOG_DIR/$log_name" "$@" <<'PY'
import subprocess
import sys

cwd, log_path, *command = sys.argv[1:]
log = open(log_path, "ab", buffering=0)
process = subprocess.Popen(
    command,
    cwd=cwd,
    stdin=subprocess.DEVNULL,
    stdout=log,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
print(process.pid)
PY
)
  echo "$pid" > "$PID_DIR/$pid_name.pid"
  echo "  PID $pid - logs: $LOG_DIR/$log_name"
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local attempts="${3:-40}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$label is ready."
      return 0
    fi
    sleep 1
  done

  echo "$label did not become ready at $url" >&2
  return 1
}

open_url() {
  local url="$1"

  if [ "${HANDA_OPEN_BROWSER:-1}" = "0" ]; then
    return
  fi

  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
    return
  fi

  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
    return
  fi

  python3 -m webbrowser "$url" >/dev/null 2>&1 || true
}

stop_existing_web_servers() {
  stop_pid_files
  stop_ports "$API_PORT" "$WEB_PORT" "$LEGACY_API_PORT" "$LEGACY_WEB_PORT"
}
