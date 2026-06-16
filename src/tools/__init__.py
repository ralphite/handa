"""Reusable, runtime-agnostic tool implementations.

These are plain Python functions with no agent-runtime coupling, so native
agents can call them directly. Tools that need session context are wrapped by
the native toolset.
"""

from __future__ import annotations

# Submodules are not imported eagerly: `from ..tools import skills` style
# imports load only what they name, so prompt-rendering consumers (and
# through them the Web process) don't pull in the command/file tool
# implementations.

__all__ = [
    "commands",
    "files",
    "skills",
]
