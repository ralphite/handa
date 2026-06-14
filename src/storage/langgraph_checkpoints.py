"""Session-lifecycle operations on the LangGraph checkpoint store.

The langgraph runtime keeps its real conversation context in
``langgraph/checkpoints.sqlite3`` keyed by ``thread_id`` (= Handa session id).
Session rewrite/fork must keep that store in step with the visible history,
otherwise truncated turns keep influencing answers and forked sessions start
amnesiac. Plain sqlite here — no langgraph import — so the storage layer stays
runtime-framework-free.

Checkpoint ids are UUIDv6 (time-prefixed), so lexicographic comparison orders
them chronologically; turn boundaries are the ``langgraph.checkpoint`` markers
the runner appends to the session's event trace.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


_TABLES = ("checkpoints", "writes")
_BUSY_TIMEOUT_MS = 5000


def copy_thread(
    db_path: Path | str,
    *,
    source_thread_id: str,
    target_thread_id: str,
    up_to_checkpoint_id: str | None = None,
) -> int:
  """Copy a thread's checkpoints to a new thread id (session fork).

  With ``up_to_checkpoint_id`` only checkpoints at or before the boundary are
  copied, so a fork from an earlier turn does not inherit later memory.
  Returns the number of copied checkpoint rows.
  """
  with _connect(db_path) as conn:
    if conn is None:
      return 0
    copied = 0
    for table in _TABLES:
      columns = _columns(conn, table)
      if "thread_id" not in columns or "checkpoint_id" not in columns:
        continue
      select_columns = ", ".join(
          "?" if column == "thread_id" else column for column in columns
      )
      where = "thread_id = ?"
      params: list[Any] = [target_thread_id, source_thread_id]
      if up_to_checkpoint_id:
        where += " and checkpoint_id <= ?"
        params.append(up_to_checkpoint_id)
      cursor = conn.execute(
          f"insert or ignore into {table} ({', '.join(columns)}) "
          f"select {select_columns} from {table} where {where}",
          params,
      )
      if table == "checkpoints":
        copied = cursor.rowcount
    conn.commit()
    return copied


def truncate_thread_after(
    db_path: Path | str,
    *,
    thread_id: str,
    checkpoint_id: str,
) -> int:
  """Drop checkpoints newer than the boundary (session rewrite).

  Returns the number of removed checkpoint rows.
  """
  with _connect(db_path) as conn:
    if conn is None:
      return 0
    removed = 0
    for table in _TABLES:
      cursor = conn.execute(
          f"delete from {table} where thread_id = ? and checkpoint_id > ?",
          (thread_id, checkpoint_id),
      )
      if table == "checkpoints":
        removed = cursor.rowcount
    conn.commit()
    return removed


def delete_thread(db_path: Path | str, *, thread_id: str) -> int:
  """Drop a thread entirely (no boundary marker, or session deletion)."""
  with _connect(db_path) as conn:
    if conn is None:
      return 0
    removed = 0
    for table in _TABLES:
      cursor = conn.execute(
          f"delete from {table} where thread_id = ?",
          (thread_id,),
      )
      if table == "checkpoints":
        removed = cursor.rowcount
    conn.commit()
    return removed


def thread_checkpoint_count(db_path: Path | str, *, thread_id: str) -> int:
  with _connect(db_path) as conn:
    if conn is None:
      return 0
    row = conn.execute(
        "select count(*) from checkpoints where thread_id = ?",
        (thread_id,),
    ).fetchone()
    return int(row[0]) if row else 0


class _connect:
  """Context manager yielding a connection, or None when the store is absent."""

  def __init__(self, db_path: Path | str):
    self._path = Path(db_path)
    self._conn: sqlite3.Connection | None = None

  def __enter__(self) -> sqlite3.Connection | None:
    if not self._path.is_file():
      return None
    self._conn = sqlite3.connect(str(self._path))
    self._conn.execute(f"pragma busy_timeout = {_BUSY_TIMEOUT_MS}")
    if not _table_exists(self._conn, "checkpoints"):
      self._conn.close()
      self._conn = None
      return None
    return self._conn

  def __exit__(self, *exc_info: Any) -> None:
    if self._conn is not None:
      self._conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
  row = conn.execute(
      "select 1 from sqlite_master where type = 'table' and name = ?",
      (table,),
  ).fetchone()
  return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
  return [row[1] for row in conn.execute(f"pragma table_info({table})")]
