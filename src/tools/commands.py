from __future__ import annotations

from typing import Any

from ..runtime import run_command


def run(command: str, cwd: str = ".", timeout_sec: int = 60) -> dict[str, Any]:
  """Run a short command inside the repository.

  Only the last 16k chars of each output stream are kept (flagged via
  stdout_truncated/stderr_truncated). Commands hitting timeout_sec (max 300)
  return timed_out=true with the partial output instead of raising.
  """
  return run_command(command=command, cwd=cwd, timeout_sec=timeout_sec)
