from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile
from typing import Iterator


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
      yield
    finally:
      fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(
      prefix=f".{path.name}.",
      suffix=".tmp",
      dir=str(path.parent),
      text=True,
  )
  try:
    with os.fdopen(fd, "w", encoding=encoding) as handle:
      handle.write(content)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp_name, path)
  except Exception:
    Path(tmp_name).unlink(missing_ok=True)
    raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(
      prefix=f".{path.name}.",
      suffix=".tmp",
      dir=str(path.parent),
  )
  try:
    with os.fdopen(fd, "wb") as handle:
      handle.write(content)
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp_name, path)
  except Exception:
    Path(tmp_name).unlink(missing_ok=True)
    raise
