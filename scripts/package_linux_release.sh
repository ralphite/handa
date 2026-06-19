#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
export HANDA_RELEASE_TARGET="${HANDA_RELEASE_TARGET:-linux-x86_64}"

exec "$SCRIPT_DIR/package_release.sh" "$@"
