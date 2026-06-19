#!/usr/bin/env bash
set -euo pipefail

# Real-launch smoke test for a packaged Handa artifact (macOS/Linux).
#
# Unlike an import-only check, this extracts the self-contained bundle, starts
# the bundled server with its embedded Python (no system Python required), and
# verifies it actually serves /api/health and the frontend. With --browser it
# also installs Chromium and drives a real headless page against the live app,
# proving the Playwright stack works on this OS.
#
# Usage: scripts/smoke_artifact.sh [--browser] <artifact.sh>

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
WITH_BROWSER=0
ARTIFACT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --browser) WITH_BROWSER=1; shift ;;
    -h|--help)
      echo "Usage: scripts/smoke_artifact.sh [--browser] <artifact.sh>" >&2
      exit 0
      ;;
    *) ARTIFACT="$1"; shift ;;
  esac
done

[ -n "$ARTIFACT" ] || { echo "error: missing <artifact.sh>" >&2; exit 2; }
[ -f "$ARTIFACT" ] || { echo "error: artifact not found: $ARTIFACT" >&2; exit 2; }

PORT="${HANDA_SMOKE_PORT:-5099}"
WORK="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

echo "==> Extracting artifact"
RELEASE_DIR="$(sh "$ARTIFACT" --extract-only --install-dir "$WORK/install")"
[ -x "$RELEASE_DIR/run" ] || { echo "error: extract failed, run script missing" >&2; exit 1; }

echo "==> Starting server on 127.0.0.1:$PORT"
"$RELEASE_DIR/run" --host 127.0.0.1 --port "$PORT" > "$WORK/server.log" 2>&1 &
SERVER_PID=$!

echo "==> Waiting for /api/health"
if ! curl -fsS --retry 90 --retry-delay 1 --retry-connrefused --max-time 120 \
      "http://127.0.0.1:$PORT/api/health" | grep -q '"ok":true'; then
  echo "error: health check failed. Server log:" >&2
  cat "$WORK/server.log" >&2
  exit 1
fi
echo "    /api/health -> ok"

CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")"
[ "$CODE" = "200" ] || {
  echo "error: GET / returned HTTP $CODE" >&2
  cat "$WORK/server.log" >&2
  exit 1
}
echo "    GET / -> 200 (frontend served)"

PY="$RELEASE_DIR/runtime/python/bin/python3"
[ -x "$PY" ] || { echo "error: bundled python missing: $PY" >&2; exit 1; }

# Import every worker/agent-runtime entrypoint the app spawns, so a Unix-only
# import anywhere in that graph fails here instead of at runtime on Windows.
echo "==> Verifying worker/runtime imports (bundled Python)"
PYTHONPATH="$RELEASE_DIR/app:$RELEASE_DIR/app/vendor" "$PY" "$SCRIPT_DIR/import_check.py"

if [ "$WITH_BROWSER" = "1" ]; then
  echo "==> Installing Chromium (bundled Playwright)"
  # On Linux CI set PLAYWRIGHT_INSTALL_FLAGS=--with-deps to also pull OS libraries.
  PYTHONPATH="$RELEASE_DIR/app/vendor" "$PY" -m playwright install ${PLAYWRIGHT_INSTALL_FLAGS:-} chromium
  echo "==> Driving a real headless page against the live app"
  PYTHONPATH="$RELEASE_DIR/app/vendor:$RELEASE_DIR/app" \
    "$PY" "$SCRIPT_DIR/browser_smoke.py" "http://127.0.0.1:$PORT/"
fi

echo "SMOKE OK ($ARTIFACT)"
