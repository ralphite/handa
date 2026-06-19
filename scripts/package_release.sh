#!/usr/bin/env bash
set -euo pipefail

# This script lives in the public repo (ralphite/handa) and is self-contained:
# the public checkout has everything needed to build a release (Python source,
# pyproject, and the web frontend under src/web). No parent repo is required.
SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
PUBLIC_ROOT="$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)"
FE_DIR="$PUBLIC_ROOT/src/web"
TMP_ROOT="$PUBLIC_ROOT/tmp/release"
BUILD_ROOT="$TMP_ROOT/build"
DIST_DIR="$TMP_ROOT/dist"
ARCH=""
PYTHON_STANDALONE_PLATFORM=""
PYTHON_MAJOR_MINOR="${PYTHON_MAJOR_MINOR:-3.12}"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/package_release.sh [version]

Builds a self-contained Handa release artifact for the current host:
  tmp/release/dist/handa-<version>-<target>.sh

Environment overrides:
  HANDA_RELEASE_TARGET       auto, macos-arm64, macos-x86_64, or linux-x86_64.
  PYTHON_STANDALONE_URL      Use a specific python-build-standalone tarball.
  PYTHON_STANDALONE_RELEASE  GitHub release tag to query instead of latest.
  PYTHON_MAJOR_MINOR         Python line to select, default: 3.12.
  SKIP_FRONTEND_BUILD=1      Reuse existing public/src/web/dist.
  SKIP_SMOKE_TEST=1          Do not run artifact smoke test.
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
    return
  fi
  die "missing checksum command: sha256sum or shasum"
}

require_checksum_cmd() {
  if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    die "missing checksum command: sha256sum or shasum"
  fi
}

configure_target() {
  local requested="${HANDA_RELEASE_TARGET:-auto}"
  local host_os
  local host_arch
  host_os="$(uname -s)"
  host_arch="$(uname -m)"

  if [ "$requested" = "auto" ] || [ -z "$requested" ]; then
    case "$host_os/$host_arch" in
      Darwin/arm64)
        requested="macos-arm64"
        ;;
      Darwin/x86_64)
        requested="macos-x86_64"
        ;;
      Linux/x86_64|Linux/amd64)
        requested="linux-x86_64"
        ;;
      *)
        die "unsupported host for auto target: $host_os/$host_arch"
        ;;
    esac
  fi

  case "$requested" in
    macos-arm64)
      [ "$host_os" = "Darwin" ] && [ "$host_arch" = "arm64" ] || die "macos-arm64 must be built on macOS arm64"
      ARCH="macos-arm64"
      PYTHON_STANDALONE_PLATFORM="aarch64-apple-darwin"
      ;;
    macos-x86_64)
      [ "$host_os" = "Darwin" ] && [ "$host_arch" = "x86_64" ] || die "macos-x86_64 must be built on macOS x86_64"
      ARCH="macos-x86_64"
      PYTHON_STANDALONE_PLATFORM="x86_64-apple-darwin"
      ;;
    linux-x86_64)
      [ "$host_os" = "Linux" ] || die "linux-x86_64 must be built on Linux because native Python wheels are platform-specific"
      case "$host_arch" in
        x86_64|amd64) ;;
        *) die "linux-x86_64 must be built on Linux x86_64" ;;
      esac
      ARCH="linux-x86_64"
      PYTHON_STANDALONE_PLATFORM="x86_64-unknown-linux-gnu"
      ;;
    *)
      die "unsupported HANDA_RELEASE_TARGET: $requested"
      ;;
  esac

  export PYTHON_STANDALONE_PLATFORM
}

version_from_pyproject() {
  python3 - "$PUBLIC_ROOT/pyproject.toml" <<'PY'
import sys
import tomllib
from pathlib import Path

data = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
}

select_python_standalone_url() {
  if [ -n "${PYTHON_STANDALONE_URL:-}" ]; then
    printf '%s\n' "$PYTHON_STANDALONE_URL"
    return
  fi

  PYTHON_MAJOR_MINOR="$PYTHON_MAJOR_MINOR" \
  PYTHON_STANDALONE_PLATFORM="$PYTHON_STANDALONE_PLATFORM" \
  PYTHON_STANDALONE_RELEASE="${PYTHON_STANDALONE_RELEASE:-latest}" \
  python3 - <<'PY'
import json
import os
import re
import sys
import urllib.request

release = os.environ["PYTHON_STANDALONE_RELEASE"]
line = os.environ["PYTHON_MAJOR_MINOR"]
platform = os.environ["PYTHON_STANDALONE_PLATFORM"]
if release == "latest":
  api_url = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
else:
  api_url = f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{release}"

# Authenticate when a token is available (e.g. GITHUB_TOKEN in CI). Unauthenticated
# api.github.com is rate-limited to 60/hr per IP, which shared CI runners blow past.
headers = {
    "User-Agent": "handa-release",
    "Accept": "application/vnd.github+json",
}
token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
if token:
  headers["Authorization"] = f"Bearer {token}"

request = urllib.request.Request(api_url, headers=headers)
with urllib.request.urlopen(request, timeout=30) as response:
  payload = json.load(response)

escaped_line = re.escape(line)
escaped_platform = re.escape(platform)
patterns = [
  re.compile(rf"cpython-{escaped_line}\.\d+\+.*-{escaped_platform}-install_only_stripped\.tar\.gz$"),
  re.compile(rf"cpython-{escaped_line}\.\d+\+.*-{escaped_platform}-install_only\.tar\.gz$"),
]

assets = payload.get("assets", [])
for pattern in patterns:
  matches = [
      asset["browser_download_url"]
      for asset in assets
      if pattern.search(asset.get("name", ""))
  ]
  if matches:
    print(sorted(matches)[-1])
    sys.exit(0)

names = "\n".join(asset.get("name", "") for asset in assets[:80])
raise SystemExit(
    "could not find python-build-standalone asset for "
    f"Python {line} {platform}. Set PYTHON_STANDALONE_URL.\n"
    f"Seen assets:\n{names}"
)
PY
}

build_frontend() {
  if [ "${SKIP_FRONTEND_BUILD:-0}" = "1" ]; then
    [ -f "$FE_DIR/dist/index.html" ] || die "SKIP_FRONTEND_BUILD=1 but $FE_DIR/dist/index.html is missing"
    return
  fi

  if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  fi
  if command -v nvm >/dev/null 2>&1; then
    nvm use 24
  else
    echo "warning: nvm not found; continuing with current node: $(command -v node || true)" >&2
  fi

  require_cmd npm
  (cd "$FE_DIR" && npm ci && npm run build)
}

install_python_runtime() {
  local python_url="$1"
  local archive="$BUILD_ROOT/python-standalone.tar.gz"

  mkdir -p "$BUILD_ROOT/python-runtime"
  echo "Downloading Python runtime:"
  echo "  $python_url"
  curl -fL "$python_url" -o "$archive"
  tar -xzf "$archive" -C "$BUILD_ROOT/python-runtime"
  [ -x "$BUILD_ROOT/python-runtime/python/bin/python3" ] || die "python runtime did not contain python/bin/python3"
}

install_python_dependencies() {
  local bundle_dir="$1"
  local python_bin="$BUILD_ROOT/python-runtime/python/bin/python3"
  local vendor_dir="$bundle_dir/app/vendor"
  local requirements_file="$BUILD_ROOT/requirements.txt"

  mkdir -p "$vendor_dir"
  "$python_bin" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$python_bin" -m pip --version >/dev/null
  "$python_bin" -m pip install --no-cache-dir --upgrade pip setuptools wheel
  "$python_bin" - "$PUBLIC_ROOT/pyproject.toml" > "$requirements_file" <<'PY'
import sys
import tomllib
from pathlib import Path

data = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for dependency in data["project"]["dependencies"]:
  print(dependency)
PY
  "$python_bin" -m pip install --no-cache-dir --target "$vendor_dir" -r "$requirements_file"
}

copy_app_files() {
  local bundle_dir="$1"
  mkdir -p "$bundle_dir/app"

  cp "$PUBLIC_ROOT/pyproject.toml" "$bundle_dir/app/"
  mkdir -p "$bundle_dir/app/src"
  (
    cd "$PUBLIC_ROOT/src"
    tar \
      --exclude="./web/node_modules" \
      --exclude="./web/dist" \
      --exclude="./web/test-results" \
      --exclude="*/__pycache__" \
      --exclude="*.pyc" \
      -cf - .
  ) | (cd "$bundle_dir/app/src" && tar -xf -)
  cp -R "$FE_DIR/dist" "$bundle_dir/app/web_dist"

  # Built-in skills live under src/skills and are already included in the src
  # tarball above, so no separate skills copy is needed here.
}

write_bundle_runner() {
  local bundle_dir="$1"
  cat > "$bundle_dir/run" <<'EOF'
#!/bin/sh
set -eu

SELF_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
PYTHON_BIN="$SELF_DIR/runtime/python/bin/python3"
APP_DIR="$SELF_DIR/app"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Handa runtime is missing: $PYTHON_BIN" >&2
  exit 1
fi

export PYTHONNOUSERSITE=1
export PYTHONPATH="$APP_DIR:$APP_DIR/vendor${PYTHONPATH:+:$PYTHONPATH}"
export HANDA_FRONTEND_DIST="$APP_DIR/web_dist"

echo "Starting Handa on http://127.0.0.1:5086"
exec "$PYTHON_BIN" -m src.api.app "$@"
EOF
  chmod +x "$bundle_dir/run"
}

write_self_extracting_artifact() {
  local bundle_name="$1"
  local payload="$2"
  local output="$3"
  local payload_sha

  payload_sha="$(sha256_file "$payload")"
  cat > "$output" <<EOF
#!/bin/sh
set -eu

HANDA_VERSION="$VERSION"
HANDA_BUNDLE_NAME="$bundle_name"
PAYLOAD_SHA256="$payload_sha"

usage() {
  cat >&2 <<'USAGE'
Usage: sh ./handa [--extract-only] [--install-dir DIR] [handa server args...]

Downloads should use:
  curl -fsSL "<url>" -o handa
  sh ./handa

This self-extracting artifact needs to read its own file, so piping it via
"curl ... | sh" is not supported.
USAGE
}

if [ -n "\${XDG_CACHE_HOME:-}" ]; then
  INSTALL_BASE="\$XDG_CACHE_HOME/handa/releases"
elif [ -n "\${HOME:-}" ]; then
  INSTALL_BASE="\$HOME/.cache/handa/releases"
else
  INSTALL_BASE=""
fi
EXTRACT_ONLY=0

while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --extract-only)
      EXTRACT_ONLY=1
      shift
      ;;
    --install-dir)
      [ "\$#" -ge 2 ] || {
        echo "missing value for --install-dir" >&2
        exit 2
      }
      INSTALL_BASE="\$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

[ -n "\$INSTALL_BASE" ] || {
  echo "HOME is not set; pass --install-dir DIR" >&2
  exit 2
}

case "\$0" in
  */*|.*) SELF="\$0" ;;
  *) SELF="./\$0" ;;
esac

[ -f "\$SELF" ] || {
  echo "Cannot read self-extracting file: \$SELF" >&2
  echo "Use: curl -fsSL <url> -o handa && sh ./handa" >&2
  exit 2
}

ARCHIVE_LINE=\$(awk '/^__HANDA_ARCHIVE_BELOW__\$/ {print NR + 1; exit 0;}' "\$SELF")
[ -n "\$ARCHIVE_LINE" ] || {
  echo "Archive marker not found." >&2
  exit 1
}

RELEASE_DIR="\$INSTALL_BASE/\$HANDA_BUNDLE_NAME"
mkdir -p "\$INSTALL_BASE"

if [ ! -x "\$RELEASE_DIR/run" ]; then
  TMP_DIR="\$INSTALL_BASE/.extract-\$HANDA_BUNDLE_NAME.\$\$"
  rm -rf "\$TMP_DIR"
  mkdir -p "\$TMP_DIR"
  trap 'rm -rf "\$TMP_DIR"' EXIT INT TERM

  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA=\$(tail -n +"\$ARCHIVE_LINE" "\$SELF" | sha256sum | awk '{print \$1}')
  elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA=\$(tail -n +"\$ARCHIVE_LINE" "\$SELF" | shasum -a 256 | awk '{print \$1}')
  else
    ACTUAL_SHA=""
  fi
  if [ -n "\$ACTUAL_SHA" ]; then
    if [ "\$ACTUAL_SHA" != "\$PAYLOAD_SHA256" ]; then
      echo "Payload checksum mismatch." >&2
      exit 1
    fi
  fi

  tail -n +"\$ARCHIVE_LINE" "\$SELF" | gzip -dc | tar -xf - -C "\$TMP_DIR"
  rm -rf "\$RELEASE_DIR"
  mv "\$TMP_DIR/\$HANDA_BUNDLE_NAME" "\$RELEASE_DIR"
  rm -rf "\$TMP_DIR"
  trap - EXIT INT TERM
fi

if [ "\$EXTRACT_ONLY" = "1" ]; then
  echo "\$RELEASE_DIR"
  exit 0
fi

exec "\$RELEASE_DIR/run" "\$@"

__HANDA_ARCHIVE_BELOW__
EOF
  cat "$payload" >> "$output"
  chmod +x "$output"
}

run_smoke_test() {
  local artifact="$1"
  # Real-launch smoke: start the bundled server and verify it actually serves
  # /api/health and the frontend (the embedded interpreter, not system Python).
  # Pass --browser via the CI step for the heavier Chromium check.
  HANDA_SMOKE_PORT="${HANDA_SMOKE_PORT:-5099}" \
    bash "$SCRIPT_DIR/smoke_artifact.sh" "$artifact" || die "smoke test failed"
}

main() {
  if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
  fi

  configure_target

  require_cmd awk
  require_cmd curl
  require_cmd gzip
  require_cmd python3
  require_checksum_cmd
  require_cmd tar

  VERSION="${1:-$(version_from_pyproject)}"
  export VERSION
  local bundle_name="handa-$VERSION-$ARCH"
  local bundle_dir="$BUILD_ROOT/$bundle_name"
  local payload="$BUILD_ROOT/$bundle_name.tar.gz"
  local artifact="$DIST_DIR/$bundle_name.sh"
  local python_url

  rm -rf "$BUILD_ROOT"
  mkdir -p "$BUILD_ROOT" "$DIST_DIR"

  build_frontend
  python_url="$(select_python_standalone_url)"
  install_python_runtime "$python_url"

  mkdir -p "$bundle_dir/runtime"
  cp -R "$BUILD_ROOT/python-runtime/python" "$bundle_dir/runtime/python"
  copy_app_files "$bundle_dir"
  install_python_dependencies "$bundle_dir"
  write_bundle_runner "$bundle_dir"

  printf '%s\n' "$VERSION" > "$bundle_dir/VERSION"
  tar -C "$BUILD_ROOT" -czf "$payload" "$bundle_name"
  write_self_extracting_artifact "$bundle_name" "$payload" "$artifact"
  printf '%s  %s\n' "$(sha256_file "$artifact")" "$(basename "$artifact")" > "$artifact.sha256"

  if [ "${SKIP_SMOKE_TEST:-0}" != "1" ]; then
    run_smoke_test "$artifact"
  fi

  echo "Built:"
  echo "  $artifact"
  echo "  $artifact.sha256"

  if [ "${KEEP_RELEASE_BUILD:-0}" != "1" ]; then
    rm -rf "$BUILD_ROOT"
  fi
}

main "$@"
