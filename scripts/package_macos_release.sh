#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"

case "$(uname -m)" in
  arm64)
    export HANDA_RELEASE_TARGET="${HANDA_RELEASE_TARGET:-macos-arm64}"
    ;;
  x86_64)
    export HANDA_RELEASE_TARGET="${HANDA_RELEASE_TARGET:-macos-x86_64}"
    ;;
  *)
    echo "unsupported macOS architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

exec "$SCRIPT_DIR/package_release.sh" "$@"
