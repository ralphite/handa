"""Handa package root.

`build_agent` is the ADK entrypoint convention (`adk run src`); resolve it
lazily so that merely importing src.* submodules — the Web API in particular —
never loads the agent implementations and their ADK/tool stack.
"""
from __future__ import annotations

__all__ = ["build_agent"]


def __getattr__(name: str):
  if name == "build_agent":
    from .agents.handa_adk.orca_adk import build_agent

    return build_agent
  raise AttributeError(f"module 'src' has no attribute {name!r}")
