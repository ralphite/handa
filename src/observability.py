from __future__ import annotations


def setup_phoenix_tracing() -> bool:
  """Native agents do not currently install a Phoenix instrumentation hook."""
  return False
