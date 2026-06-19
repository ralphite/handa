from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Iterator

try:
  import fcntl  # POSIX advisory locking
except ImportError:  # Windows has no fcntl; fall back to msvcrt below.
  fcntl = None
  import msvcrt


def _lock_exclusive(handle) -> None:
  if fcntl is not None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return
  # Windows: lock a single byte at offset 0 as an advisory mutex. LK_LOCK waits
  # ~10s per attempt, so loop to keep blocking like flock's LOCK_EX.
  handle.seek(0)
  while True:
    try:
      msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
      return
    except OSError:
      continue


def _unlock(handle) -> None:
  if fcntl is not None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return
  handle.seek(0)
  try:
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
  except OSError:
    pass


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8") as handle:
    _lock_exclusive(handle)
    try:
      yield
    finally:
      _unlock(handle)


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
