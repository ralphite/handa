from __future__ import annotations

import asyncio
import sqlite3

from src.runner import APP_NAME
from src.storage.langgraph_checkpoints import copy_thread
from src.storage.langgraph_checkpoints import delete_thread
from src.storage.langgraph_checkpoints import thread_checkpoint_count
from src.storage.langgraph_checkpoints import truncate_thread_after
from src.storage.paths import langgraph_checkpoints_path
from src.storage.runtime_event_store import RuntimeEventStore
from src.storage.session_service import HandaSessionService


def _make_checkpoint_db(path, rows):
  path.parent.mkdir(parents=True, exist_ok=True)
  conn = sqlite3.connect(str(path))
  conn.executescript(
      """
      create table checkpoints (
        thread_id text, checkpoint_ns text default '', checkpoint_id text,
        parent_checkpoint_id text, type text, checkpoint blob, metadata blob,
        primary key (thread_id, checkpoint_ns, checkpoint_id)
      );
      create table writes (
        thread_id text, checkpoint_ns text default '', checkpoint_id text,
        task_id text, idx integer, channel text, type text, value blob,
        primary key (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
      );
      """
  )
  for thread_id, checkpoint_id in rows:
    conn.execute(
        "insert into checkpoints (thread_id, checkpoint_id, type, checkpoint, metadata) "
        "values (?, ?, 'msgpack', x'00', x'00')",
        (thread_id, checkpoint_id),
    )
    conn.execute(
        "insert into writes (thread_id, checkpoint_id, task_id, idx, channel, type, value) "
        "values (?, ?, 't', 0, 'history', 'msgpack', x'00')",
        (thread_id, checkpoint_id),
    )
  conn.commit()
  conn.close()


def test_checkpoint_thread_operations(tmp_path):
  db = tmp_path / "checkpoints.sqlite3"
  _make_checkpoint_db(
      db,
      [("s1", "1f00-aaaa"), ("s1", "1f00-bbbb"), ("s1", "1f00-cccc"), ("other", "1f00-aaaa")],
  )

  copied = copy_thread(db, source_thread_id="s1", target_thread_id="s2", up_to_checkpoint_id="1f00-bbbb")
  assert copied == 2
  assert thread_checkpoint_count(db, thread_id="s2") == 2

  removed = truncate_thread_after(db, thread_id="s1", checkpoint_id="1f00-aaaa")
  assert removed == 2
  assert thread_checkpoint_count(db, thread_id="s1") == 1

  assert delete_thread(db, thread_id="other") == 1
  assert thread_checkpoint_count(db, thread_id="other") == 0
  # writes pruned alongside
  conn = sqlite3.connect(str(db))
  assert conn.execute("select count(*) from writes where thread_id='other'").fetchone()[0] == 0
  assert conn.execute("select count(*) from writes where thread_id='s2'").fetchone()[0] == 2


def test_checkpoint_ops_tolerate_missing_store(tmp_path):
  db = tmp_path / "missing.sqlite3"
  assert copy_thread(db, source_thread_id="a", target_thread_id="b") == 0
  assert truncate_thread_after(db, thread_id="a", checkpoint_id="x") == 0
  assert delete_thread(db, thread_id="a") == 0


def _marker_event(checkpoint_id: str) -> dict:
  return {
      "kind": "langgraph.checkpoint",
      "summary": "checkpoint boundary",
      "payload": {"checkpoint_id": checkpoint_id},
  }


def test_truncate_session_rolls_back_langgraph_thread(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  service = HandaSessionService(root=str(storage_root))
  session = asyncio.run(
      service.create_session(app_name=APP_NAME, user_id="user", session_id="lg-sess")
  )
  store = RuntimeEventStore(storage_root)
  for turn_id, checkpoint_id in (("turn1", "1f00-aaaa"), ("turn2", "1f00-bbbb")):
    store.append(
        session_id=session.id,
        turn_id=turn_id,
        runtime="langgraph",
        event={"kind": "agent_text", "payload": {"text": turn_id}},
    )
    store.append(
        session_id=session.id,
        turn_id=turn_id,
        runtime="langgraph",
        event=_marker_event(checkpoint_id),
    )
  _make_checkpoint_db(
      langgraph_checkpoints_path(storage_root),
      [(session.id, "1f00-aaaa"), (session.id, "1f00-bbbb"), (session.id, "1f00-cccc")],
  )

  asyncio.run(
      service.truncate_session(
          app_name=APP_NAME,
          user_id="user",
          session_id=session.id,
          kept_turn_ids=["turn1"],
          artifact_refs=set(),
      )
  )

  db = langgraph_checkpoints_path(storage_root)
  # rolled back to turn1's boundary: 1f00-aaaa stays, later checkpoints gone
  assert thread_checkpoint_count(db, thread_id=session.id) == 1


def test_truncate_session_without_marker_drops_thread(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  service = HandaSessionService(root=str(storage_root))
  session = asyncio.run(
      service.create_session(app_name=APP_NAME, user_id="user", session_id="lg-old")
  )
  store = RuntimeEventStore(storage_root)
  store.append(
      session_id=session.id,
      turn_id="turn1",
      runtime="langgraph",
      event={"kind": "agent_text", "payload": {"text": "no marker"}},
  )
  _make_checkpoint_db(
      langgraph_checkpoints_path(storage_root),
      [(session.id, "1f00-aaaa"), (session.id, "1f00-bbbb")],
  )

  asyncio.run(
      service.truncate_session(
          app_name=APP_NAME,
          user_id="user",
          session_id=session.id,
          kept_turn_ids=["turn1"],
          artifact_refs=set(),
      )
  )

  # leaked memory is worse than amnesia: without a boundary the thread goes
  assert thread_checkpoint_count(
      langgraph_checkpoints_path(storage_root), thread_id=session.id
  ) == 0


def test_delete_session_drops_langgraph_thread(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  service = HandaSessionService(root=str(storage_root))
  session = asyncio.run(
      service.create_session(app_name=APP_NAME, user_id="user", session_id="lg-del")
  )
  _make_checkpoint_db(
      langgraph_checkpoints_path(storage_root),
      [(session.id, "1f00-aaaa"), (session.id, "1f00-bbbb"), ("other", "1f00-aaaa")],
  )

  asyncio.run(
      service.delete_session(app_name=APP_NAME, user_id="user", session_id=session.id)
  )

  db = langgraph_checkpoints_path(storage_root)
  assert thread_checkpoint_count(db, thread_id=session.id) == 0
  # unrelated threads stay
  assert thread_checkpoint_count(db, thread_id="other") == 1
  conn = sqlite3.connect(str(db))
  assert conn.execute(
      "select count(*) from writes where thread_id = ?", (session.id,)
  ).fetchone()[0] == 0


def test_fork_copies_langgraph_thread_up_to_boundary(tmp_path, monkeypatch):
  storage_root = tmp_path / ".handa"
  monkeypatch.setenv("HANDA_STORAGE_ROOT", str(storage_root))
  service = HandaSessionService(root=str(storage_root))
  session = asyncio.run(
      service.create_session(app_name=APP_NAME, user_id="user", session_id="lg-src")
  )
  store = RuntimeEventStore(storage_root)
  for turn_id, checkpoint_id in (("turn1", "1f00-aaaa"), ("turn2", "1f00-cccc")):
    store.append(
        session_id=session.id,
        turn_id=turn_id,
        runtime="langgraph",
        event=_marker_event(checkpoint_id),
    )
  _make_checkpoint_db(
      langgraph_checkpoints_path(storage_root),
      [(session.id, "1f00-aaaa"), (session.id, "1f00-bbbb"), (session.id, "1f00-cccc")],
  )

  forked = asyncio.run(
      service.fork_session(
          app_name=APP_NAME,
          user_id="user",
          source_session_id=session.id,
          target_session_id="lg-fork",
          state_updates={},
          source_turn_ids={"turn1"},
          turn_id_map={"turn1": "new-turn1"},
      )
  )
  assert forked is not None

  db = langgraph_checkpoints_path(storage_root)
  # only checkpoints up to turn1's boundary travel to the fork
  assert thread_checkpoint_count(db, thread_id="lg-fork") == 1
  # source thread untouched
  assert thread_checkpoint_count(db, thread_id=session.id) == 3
