#!/bin/sh
set -eu

# Handa one-line installer for macOS / Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/ralphite/handa/main/install.sh | sh
#
# Downloads the self-contained release for this OS/arch (no Python/Node needed),
# unpacks it under ~/.local/share/handa, and links `handa` into ~/.local/bin.
#
# Env overrides:
#   HANDA_REPO     GitHub repo (default: ralphite/handa)
#   HANDA_VERSION  release tag (default: latest)
#   HANDA_HOME     install root (default: ~/.local/share/handa)
#   HANDA_BIN_DIR  where to link the launcher (default: ~/.local/bin)

REPO="${HANDA_REPO:-ralphite/handa}"
VERSION="${HANDA_VERSION:-latest}"
HOME_DIR="${HANDA_HOME:-$HOME/.local/share/handa}"
BIN_DIR="${HANDA_BIN_DIR:-$HOME/.local/bin}"

os="$(uname -s)"
arch="$(uname -m)"
case "$os/$arch" in
  Darwin/arm64)          target="macos-arm64" ;;
  Linux/x86_64|Linux/amd64) target="linux-x86_64" ;;
  Darwin/x86_64)
    echo "No prebuilt binary for Intel Mac (macos-x86_64)." >&2
    echo "Build from source: https://github.com/$REPO#build-from-source" >&2
    exit 1
    ;;
  *)
    echo "Unsupported platform: $os/$arch" >&2
    echo "Handa ships prebuilt for macos-arm64 and linux-x86_64." >&2
    exit 1
    ;;
esac

asset="handa-$target.sh"
if [ "$VERSION" = "latest" ]; then
  url="https://github.com/$REPO/releases/latest/download/$asset"
else
  url="https://github.com/$REPO/releases/download/$VERSION/$asset"
fi

command -v curl >/dev/null 2>&1 || { echo "error: curl is required" >&2; exit 1; }

mkdir -p "$HOME_DIR/releases" "$BIN_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT INT TERM

echo "Downloading $url"
curl -fSL "$url" -o "$tmp"
chmod +x "$tmp"

echo "Unpacking..."
release_dir="$(sh "$tmp" --extract-only --install-dir "$HOME_DIR/releases")"
ln -sf "$release_dir/run" "$BIN_DIR/handa"

echo
echo "Handa installed."
echo "  launcher: $BIN_DIR/handa"
echo "  release:  $release_dir"
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "  Run: handa" ;;
  *) echo "  Add $BIN_DIR to your PATH, then run: handa" ;;
esac
