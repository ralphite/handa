# Handa

Handa is a native AI coding agent. It ships as a single self-contained download —
the server, web UI, and an embedded Python runtime are bundled together, so there
is **nothing to install first**: no Python, no Node, no virtualenv.

## Install

**macOS / Linux**

```sh
curl -fsSL https://raw.githubusercontent.com/ralphite/handa/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/ralphite/handa/main/install.ps1 | iex
```

This downloads the release for your platform, unpacks it (macOS/Linux:
`~/.local/share/handa`, Windows: `%LOCALAPPDATA%\Handa`), and adds a `handa`
launcher. Then just run:

```sh
handa
```

Handa starts on <http://127.0.0.1:5086>. Pass `--host` / `--port` to change.

### Supported platforms

| OS | Architectures |
| --- | --- |
| macOS | arm64 (Apple Silicon) |
| Linux | x86_64 |
| Windows | x86_64 |

Intel Macs (x86_64) are not shipped as a prebuilt download — [build from
source](#build-from-source) instead.

Each release is built and smoke-tested on its native OS in CI (it is launched for
real and must serve `/api/health`, render the UI, and drive a real headless
browser before it is published).

## Build from source

You need Node 24 (frontend) and a `python3` on PATH (≥ 3.11, used only by the
build to read `pyproject.toml`); the bundled runtime is downloaded automatically.

```sh
# macOS / Linux — produces tmp/release/dist/handa-<version>-<target>.sh
scripts/package_macos_release.sh      # or scripts/package_linux_release.sh

# Windows — produces tmp/release/dist/handa-<version>-windows-x86_64.zip
scripts/package_windows_release.ps1
```

Verify an artifact end-to-end (launch + `/api/health` + real browser):

```sh
scripts/smoke_artifact.sh --browser tmp/release/dist/handa-<version>-<target>.sh
# Windows:
scripts/smoke_artifact.ps1 -Zip tmp/release/dist/handa-<version>-windows-x86_64.zip -Browser
```

## License

See [LICENSE](LICENSE).
