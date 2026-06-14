#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
# shellcheck source=public/scripts/_web_common.sh
source "$SCRIPT_DIR/_web_common.sh"

ensure_dirs
stop_existing_web_servers

: > "$LOG_DIR/api.log"
: > "$LOG_DIR/web.log"

start_in_dir "Web API" "http://$HOST:$API_PORT" "api" "api.log" "$PUBLIC_ROOT" \
  env UV_PROJECT_ENVIRONMENT="$UV_ENVIRONMENT" \
  uv run python -m uvicorn src.api.app:create_app \
  --factory \
  --host "$HOST" \
  --port "$API_PORT" \
  --reload \
  --reload-dir "$PUBLIC_ROOT/src" \
  --no-access-log
wait_for_url "Web API" "http://$HOST:$API_PORT/api/health"

use_node_24
ensure_frontend_dependencies

start_in_dir "Web UI" "http://$HOST:$WEB_PORT" "web" "web.log" "$WEB_DIR" \
  env BROWSER=none npm run dev -- --host "$HOST" --port "$WEB_PORT" --strictPort
wait_for_url "Web UI" "http://$HOST:$WEB_PORT/"

open_url "http://$HOST:$WEB_PORT/"

echo "Done. Web: http://$HOST:$WEB_PORT  API: http://$HOST:$API_PORT"
