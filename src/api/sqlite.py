from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
import uuid

from ..contract.product import DEFAULT_MODEL_CONFIG_ID
from ..contract.product import DEFAULT_WEB_AGENT_ID
from ..contract.product import get_agent_definition
from ..contract.task_store import now_iso
from ..contract.storage import RuntimeEventStore


def _fallback_agent_id(row: dict[str, Any]) -> str:
  return str(row.get("agent_id") or DEFAULT_WEB_AGENT_ID)


def _fallback_agent_runtime(row: dict[str, Any]) -> str:
  value = row.get("agent_runtime")
  if value:
    return str(value)
  try:
    return get_agent_definition(_fallback_agent_id(row)).runtime
  except ValueError:
    return "native"


_WEB_STEPS_SCHEMA_SQL = """
create table if not exists web_steps (
  id text primary key,
  turn_id text not null,
  seq integer not null,
  session_seq integer,
  kind text not null,
  summary text not null,
  payload_json text not null,
  created_at text not null
)
"""

_WEB_TURN_ATTACHMENTS_SCHEMA_SQL = """
create table if not exists web_turn_attachments (
  id text primary key,
  turn_id text not null,
  ordinal integer not null default 0,
  filename text not null,
  mime_type text not null,
  kind text not null,
  byte_count integer not null default 0,
  storage_path text not null,
  created_at text not null
)
"""

_WEB_TURNS_SCHEMA_SQL = """
create table if not exists web_turns (
  id text primary key,
  session_id text not null,
  model_config_id text,
  title text,
  input_text text not null,
  trigger_kind text not null default 'user_message',
  status text not null,
  created_at text not null,
  updated_at text not null,
  started_at text,
  finished_at text,
  cancel_requested_at text,
  input_token_count integer not null default 0,
  output_token_count integer not null default 0,
  total_token_count integer not null default 0,
  tool_call_count integer not null default 0,
  tool_success_count integer not null default 0,
  tool_fail_count integer not null default 0,
  tool_duration_ms integer not null default 0,
  file_lines_added integer not null default 0,
  file_lines_removed integer not null default 0,
  final_text text,
  error_type text,
  error_message text
)
"""

_WEB_EVENT_CURSORS_SCHEMA_SQL = """
create table if not exists web_event_cursors (
  session_id text not null,
  runtime text not null,
  byte_offset integer not null default 0,
  updated_at text not null,
  primary key (session_id, runtime)
)
"""

_WEB_SESSIONS_SCHEMA_SQL = """
create table if not exists web_sessions (
  id text primary key,
  project_id text,
  agent_id text not null,
  agent_runtime text not null default 'native',
  title text,
  title_source text not null default 'auto',
  parent_session_id text,
  parent_task_id text,
  forked_from_session_id text,
  forked_from_turn_id text,
  forked_at text,
  starred_at text,
  archived_at text,
  deleted_at text,
  unread_at text,
  created_at text not null
)
"""

_WEB_AUTOMATED_TASKS_SCHEMA_SQL = """
create table if not exists web_automated_tasks (
  id text primary key,
  project_id text not null,
  name text not null,
  description text,
  enabled integer not null default 0,
  agent_id text not null,
  model_config_id text,
  prompt text not null,
  last_triggered_at text,
  last_run_session_id text,
  created_at text not null,
  updated_at text not null,
  deleted_at text
)
"""

_WEB_AUTOMATED_TASK_TRIGGERS_SCHEMA_SQL = """
create table if not exists web_automated_task_triggers (
  id text primary key,
  automated_task_id text not null,
  type text not null,
  enabled integer not null default 1,
  config_json text not null,
  next_fire_at text,
  last_fired_at text,
  created_at text not null,
  updated_at text not null
)
"""

_WEB_AUTOMATED_TASK_RUNS_SCHEMA_SQL = """
create table if not exists web_automated_task_runs (
  id text primary key,
  automated_task_id text not null,
  trigger_id text,
  trigger_kind text not null,
  dedup_key text,
  trigger_context_json text,
  session_id text,
  turn_id text,
  status text not null,
  error_message text,
  created_at text not null,
  updated_at text not null
)
"""


def _setting_bool(value: str | None) -> bool:
  return str(value).lower() in {"1", "true", "yes", "on"}


def _setting_str_list(value: str | None) -> list[str]:
  if not value:
    return []
  try:
    parsed = json.loads(value)
  except (TypeError, ValueError):
    return []
  if not isinstance(parsed, list):
    return []
  return [item for item in parsed if isinstance(item, str)]


class WebDatabase:
  def __init__(self, path: Path):
    self.path = path
    self._lock = threading.Lock()
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self._connection = sqlite3.connect(str(path), check_same_thread=False)
    self._connection.row_factory = sqlite3.Row

  def init_schema(self) -> None:
    with self._lock, self._connection:
      self._create_schema()
      self._migrate_legacy_web_tables()
      self._migrate_legacy_turn_tables()
      self._backfill_web_sessions()
      self._strip_legacy_fork_title_prefix()
      self._backfill_turn_event_session_seq()

  def _create_schema(self) -> None:
    self._connection.executescript(
        f"""
        {_WEB_TURNS_SCHEMA_SQL};

        create table if not exists web_projects (
          id text primary key,
          name text not null,
          root_path text not null unique,
          created_at text not null,
          updated_at text not null,
          last_opened_at text not null
        );

        {_WEB_STEPS_SCHEMA_SQL};

        {_WEB_TURN_ATTACHMENTS_SCHEMA_SQL};

        create table if not exists web_user_settings (
          user_id text not null,
          key text not null,
          value text not null,
          updated_at text not null,
          primary key (user_id, key)
        );

        {_WEB_SESSIONS_SCHEMA_SQL};

        {_WEB_EVENT_CURSORS_SCHEMA_SQL};

        {_WEB_AUTOMATED_TASKS_SCHEMA_SQL};

        {_WEB_AUTOMATED_TASK_TRIGGERS_SCHEMA_SQL};

        {_WEB_AUTOMATED_TASK_RUNS_SCHEMA_SQL};
        """
    )
    self._ensure_column("web_turns", "cancel_requested_at", "text")
    self._ensure_column("web_turns", "model_config_id", "text")
    self._ensure_column("web_turns", "input_token_count", "integer not null default 0")
    self._ensure_column("web_turns", "output_token_count", "integer not null default 0")
    self._ensure_column("web_turns", "total_token_count", "integer not null default 0")
    self._ensure_column("web_turns", "tool_call_count", "integer not null default 0")
    self._ensure_column("web_turns", "tool_success_count", "integer not null default 0")
    self._ensure_column("web_turns", "tool_fail_count", "integer not null default 0")
    self._ensure_column("web_turns", "tool_duration_ms", "integer not null default 0")
    self._ensure_column("web_turns", "file_lines_added", "integer not null default 0")
    self._ensure_column("web_turns", "file_lines_removed", "integer not null default 0")
    self._ensure_column("web_steps", "id", "text")
    self._ensure_column("web_steps", "session_seq", "integer")
    self._ensure_column("web_sessions", "agent_runtime", "text not null default 'native'")
    self._ensure_column("web_sessions", "archived_at", "text")
    self._ensure_column("web_sessions", "deleted_at", "text")
    self._ensure_column("web_sessions", "unread_at", "text")
    self._ensure_column("web_sessions", "forked_from_session_id", "text")
    self._ensure_column("web_sessions", "forked_from_turn_id", "text")
    self._ensure_column("web_sessions", "forked_at", "text")
    self._normalize_web_sessions_schema()
    self._normalize_web_turns_schema()
    self._normalize_web_steps_schema()
    self._normalize_web_turn_attachments_schema()
    self._create_session_indexes()
    self._create_turn_indexes()
    self._create_step_indexes()
    self._create_attachment_indexes()
    self._create_automated_task_indexes()

  def _create_automated_task_indexes(self) -> None:
    self._connection.executescript(
        """
        create index if not exists idx_web_automated_tasks_project
          on web_automated_tasks(project_id);
        create index if not exists idx_web_automated_task_triggers_task
          on web_automated_task_triggers(automated_task_id);
        create index if not exists idx_web_automated_task_triggers_next_fire
          on web_automated_task_triggers(next_fire_at);
        create unique index if not exists idx_web_automated_task_runs_dedup
          on web_automated_task_runs(dedup_key);
        create index if not exists idx_web_automated_task_runs_task
          on web_automated_task_runs(automated_task_id, created_at);
        """
    )

  def _migrate_legacy_web_tables(self) -> None:
    if self._table_exists("web_threads"):
      rows = [dict(row) for row in self._connection.execute("select * from web_threads").fetchall()]
      self._connection.executemany(
          """
          insert or ignore into web_sessions (
            id, project_id, agent_id, agent_runtime, title, title_source,
            parent_session_id, parent_task_id,
            forked_from_session_id, forked_from_turn_id, forked_at, starred_at,
            archived_at, deleted_at, unread_at, created_at
          ) values (
            :id, :project_id, :agent_id, :agent_runtime, :title, :title_source,
            :parent_session_id, :parent_task_id,
            :forked_from_session_id, :forked_from_turn_id, :forked_at, :starred_at,
            :archived_at, :deleted_at, :unread_at, :created_at
          )
          """,
          [
              {
                  "id": row["id"],
                  "project_id": row.get("project_id"),
                  "agent_id": _fallback_agent_id(row),
                  "agent_runtime": _fallback_agent_runtime(row),
                  "title": row.get("title"),
                  "title_source": row.get("title_source") or "auto",
                  "parent_session_id": row.get("parent_session_id") or row.get("parent_thread_id"),
                  "parent_task_id": row.get("parent_task_id"),
                  "forked_from_session_id": row.get("forked_from_session_id") or row.get("forked_from_thread_id"),
                  "forked_from_turn_id": row.get("forked_from_turn_id"),
                  "forked_at": row.get("forked_at"),
                  "starred_at": row.get("starred_at"),
                  "archived_at": row.get("archived_at"),
                  "deleted_at": row.get("deleted_at"),
                  "unread_at": row.get("unread_at"),
                  "created_at": row.get("created_at") or now_iso(),
              }
              for row in rows
          ],
      )
      self._connection.execute("drop table if exists web_threads")
      self._connection.execute("drop index if exists idx_web_threads_project_created")
      self._connection.execute("drop index if exists idx_web_threads_starred")
      self._connection.execute("drop index if exists idx_web_threads_archived")
      self._connection.execute("drop index if exists idx_web_threads_deleted")

    if self._table_exists("web_invocations"):
      rows = [dict(row) for row in self._connection.execute("select * from web_invocations").fetchall()]
      self._seed_sessions_from_turn_rows(rows)
      self._connection.executemany(
          """
          insert or ignore into web_turns (
            id, session_id, model_config_id, title, input_text,
            trigger_kind, status, created_at, updated_at, started_at,
            finished_at, cancel_requested_at, input_token_count,
            output_token_count, total_token_count,
            final_text, error_type, error_message
          ) values (
            :id, :session_id, :model_config_id, :title, :input_text,
            :trigger_kind, :status, :created_at, :updated_at, :started_at,
            :finished_at, :cancel_requested_at, :input_token_count,
            :output_token_count, :total_token_count,
            :final_text, :error_type, :error_message
          )
          """,
          [
              {
                  "id": row["id"],
                  "session_id": row.get("session_id") or row.get("thread_id"),
                  "model_config_id": row.get("model_config_id") or DEFAULT_MODEL_CONFIG_ID,
                  "title": row.get("title"),
                  "input_text": row.get("input_text") or "",
                  "trigger_kind": row.get("trigger_kind") or "user_message",
                  "status": row.get("status") or "completed",
                  "created_at": row.get("created_at") or now_iso(),
                  "updated_at": row.get("updated_at") or row.get("created_at") or now_iso(),
                  "started_at": row.get("started_at"),
                  "finished_at": row.get("finished_at"),
                  "cancel_requested_at": row.get("cancel_requested_at"),
                  "input_token_count": int(row.get("input_token_count") or 0),
                  "output_token_count": int(row.get("output_token_count") or 0),
                  "total_token_count": int(row.get("total_token_count") or 0),
                  "final_text": row.get("final_text"),
                  "error_type": row.get("error_type"),
                  "error_message": row.get("error_message"),
              }
              for row in rows
              if row.get("session_id") or row.get("thread_id")
          ],
      )
      self._connection.execute("drop table if exists web_invocations")
      self._connection.execute("drop index if exists idx_web_invocations_session")
      self._connection.execute("drop index if exists idx_web_invocations_project_created")

    if self._table_exists("web_invocation_events"):
      rows = [dict(row) for row in self._connection.execute("select * from web_invocation_events").fetchall()]
      event_store = RuntimeEventStore(self.path.parent)
      event_indexes: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
      for row in rows:
        if not (row.get("invocation_id") or row.get("turn_id")):
          continue
        if not (row.get("session_id") or row.get("thread_id")):
          continue
        self._insert_step_row(
            self._normalized_step_row(
                {
                    "turn_id": row.get("invocation_id") or row.get("turn_id"),
                    "seq": int(row.get("seq") or 0),
                    "session_seq": row.get("session_seq") or row.get("thread_seq"),
                    "session_id": row.get("session_id") or row.get("thread_id"),
                    "adk_event_id": row.get("adk_event_id"),
                    "id": row.get("runtime_event_id") or row.get("adk_event_id"),
                    "kind": row.get("kind") or "runtime_step",
                    "summary": row.get("summary") or "",
                    "payload_json": row.get("payload_json") or "{}",
                    "raw_event_json": row.get("raw_event_json") or "{}",
                    "created_at": row.get("created_at") or now_iso(),
                },
                event_store,
                event_indexes,
            )
        )
      self._connection.execute("drop table if exists web_invocation_events")
      self._connection.execute("drop index if exists idx_web_invocation_events_invocation_seq")
      self._connection.execute("drop index if exists idx_web_invocation_events_session_thread_seq")
      self._connection.execute("drop index if exists idx_web_invocation_events_session_session_seq")

      if self._table_exists("web_invocation_attachments"):
        rows = [dict(row) for row in self._connection.execute("select * from web_invocation_attachments").fetchall()]
        self._connection.executemany(
            """
            insert or ignore into web_turn_attachments (
              id, turn_id, ordinal, filename, mime_type,
              kind, byte_count, storage_path, created_at
            ) values (
              :id, :turn_id, :ordinal, :filename, :mime_type,
              :kind, :byte_count, :storage_path, :created_at
            )
            """,
            [
                {
                    "id": row["id"],
                    "turn_id": row.get("invocation_id") or row.get("turn_id"),
                    "ordinal": int(row.get("ordinal") or 0),
                    "filename": row.get("filename") or "",
                    "mime_type": row.get("mime_type") or "application/octet-stream",
                    "kind": row.get("kind") or "file",
                    "byte_count": int(row.get("byte_count") or 0),
                    "storage_path": row.get("storage_path") or "",
                    "created_at": row.get("created_at") or now_iso(),
                }
                for row in rows
                if row.get("invocation_id") or row.get("turn_id")
            ],
        )
      self._connection.execute("drop table if exists web_invocation_attachments")
      self._connection.execute("drop index if exists idx_web_invocation_attachments_invocation")

  def _migrate_legacy_turn_tables(self) -> None:
    if self._table_exists("web_runs"):
      rows = self._connection.execute(
          """
          select id, session_id as session_id, project_id, agent_id, title, prompt, status,
                 created_at, updated_at, started_at, finished_at, final_text,
                 error_type, error_message
          from web_runs
          order by rowid asc
          """
      ).fetchall()
      self._seed_sessions_from_turn_rows([dict(row) for row in rows])
      self._connection.executemany(
          """
          insert or ignore into web_turns (
            id, session_id, model_config_id, title, input_text,
            trigger_kind, status, created_at, updated_at, started_at,
            finished_at, final_text, error_type, error_message
          ) values (?, ?, ?, ?, ?, 'user_message', ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          [
              (
                  row["id"],
                  row["session_id"],
                  DEFAULT_MODEL_CONFIG_ID,
                  row["title"],
                  row["prompt"],
                  row["status"],
                  row["created_at"],
                  row["updated_at"],
                  row["started_at"],
                  row["finished_at"],
                  row["final_text"],
                  row["error_type"],
                  row["error_message"],
              )
              for row in rows
          ],
      )

    if self._table_exists("web_run_events"):
      rows = self._connection.execute(
          """
          select run_id, seq, session_id as session_id, adk_event_id, kind, summary,
                 payload_json, raw_event_json, created_at
          from web_run_events
          order by rowid asc
          """
      ).fetchall()
      event_store = RuntimeEventStore(self.path.parent)
      event_indexes: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
      for raw_row in rows:
        row = dict(raw_row)
        self._insert_step_row(
            self._normalized_step_row(
                {
                    "turn_id": row["run_id"],
                    "seq": row["seq"],
                    "session_id": row["session_id"],
                    "runtime": "native",
                    "adk_event_id": row["adk_event_id"],
                    "id": row["adk_event_id"],
                    "kind": row["kind"],
                    "summary": row["summary"],
                    "payload_json": row["payload_json"],
                    "raw_event_json": row["raw_event_json"],
                    "created_at": row["created_at"],
                },
                event_store,
                event_indexes,
            )
        )

    self._connection.execute("drop table if exists web_run_events")
    self._connection.execute("drop table if exists web_runs")
    self._connection.execute("drop index if exists idx_web_runs_session")
    self._connection.execute("drop index if exists idx_web_runs_project_created")
    self._connection.execute("drop index if exists idx_web_run_events_run_seq")

  def _backfill_web_sessions(self) -> None:
    # Fold legacy per-(project, session) stars into web_sessions.
    if self._table_exists("web_thread_stars"):
      self._connection.execute(
          """
          update web_sessions
          set starred_at = (
            select s.starred_at from web_thread_stars s
            where s.thread_id = web_sessions.id
            limit 1
          )
          where exists (
            select 1 from web_thread_stars s where s.thread_id = web_sessions.id
          )
          """
      )
      self._connection.execute("drop table if exists web_thread_stars")
      self._connection.execute("drop index if exists idx_web_thread_stars_project")
    if self._table_exists("web_session_stars"):
      self._connection.execute(
          """
          update web_sessions
          set starred_at = (
            select s.starred_at from web_session_stars s
            where s.session_id = web_sessions.id
            limit 1
          )
          where exists (
            select 1 from web_session_stars s where s.session_id = web_sessions.id
          )
          """
      )
      self._connection.execute("drop table if exists web_session_stars")
      self._connection.execute("drop index if exists idx_web_session_stars_project")
    self._backfill_web_session_project_ids()

  def _strip_legacy_fork_title_prefix(self) -> None:
    # One-time cleanup: forked sessions used to carry a redundant "Fork: " title
    # prefix. The sidebar fork icon (forked_from_session_id) now conveys that, so
    # strip the prefix from existing auto-generated fork titles. Manually renamed
    # titles (title_source='manual') keep whatever the user typed. Idempotent:
    # once stripped the title no longer matches the LIKE clause.
    self._connection.execute(
        """
        update web_sessions
        set title = substr(title, 7)
        where title_source = 'fork' and title like 'Fork: %'
        """
    )

  def _backfill_web_session_project_ids(self) -> None:
    rows = self._connection.execute(
        """
        select id
        from web_sessions
        where project_id is null or trim(project_id) = ''
        """
    ).fetchall()
    for row in rows:
      project_id = self._project_id_from_session_json(str(row["id"]))
      if not project_id:
        continue
      self._connection.execute(
          "update web_sessions set project_id = ? where id = ?",
          (project_id, row["id"]),
      )

  def _project_id_from_session_json(self, session_id: str) -> str | None:
    path = self.path.parent / "sessions" / session_id / "session.json"
    if not path.is_file():
      return None
    try:
      payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      return None
    state = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(state, dict):
      return None
    project_id = _optional_str(state.get("handa:project_id"))
    if project_id:
      return project_id
    project_root = _optional_str(state.get("handa:project_root"))
    if not project_root:
      return None
    match = self._connection.execute(
        "select id from web_projects where root_path = ?",
        (project_root,),
    ).fetchone()
    return str(match["id"]) if match and match["id"] else None

  def _backfill_turn_event_session_seq(self) -> None:
    rows = self._connection.execute(
        """
        select e.turn_id, e.seq, i.session_id
        from web_steps e
        join web_turns i on i.id = e.turn_id
        where e.session_seq is null
        order by i.session_id asc, i.created_at asc, i.rowid asc, e.seq asc
        """
    ).fetchall()
    next_by_session: dict[str, int] = {}
    for row in rows:
      session_id = str(row["session_id"])
      next_seq = next_by_session.get(session_id)
      if next_seq is None:
        existing = self._connection.execute(
            """
            select coalesce(max(session_seq), 0) + 1 as next_seq
            from web_steps e
            join web_turns i on i.id = e.turn_id
            where i.session_id = ? and e.session_seq is not null
            """,
            (session_id,),
        ).fetchone()
        next_seq = int(existing["next_seq"])
      self._connection.execute(
          """
          update web_steps
          set session_seq = ?
          where turn_id = ? and seq = ?
          """,
          (next_seq, row["turn_id"], row["seq"]),
      )
      next_by_session[session_id] = next_seq + 1

  def _table_exists(self, table: str) -> bool:
    row = self._connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None

  def _ensure_column(self, table: str, column: str, column_type: str) -> None:
    columns = {
        row["name"]
        for row in self._connection.execute(f"pragma table_info({table})").fetchall()
    }
    if column not in columns:
      self._connection.execute(
          f"alter table {table} add column {column} {column_type}"
      )

  def _columns(self, table: str) -> set[str]:
    return {
        row["name"]
        for row in self._connection.execute(f"pragma table_info({table})").fetchall()
    }

  def _normalize_web_sessions_schema(self) -> None:
    if not self._table_exists("web_sessions"):
      return
    canonical_columns = {
        "id",
        "project_id",
        "agent_id",
        "agent_runtime",
        "title",
        "title_source",
        "parent_session_id",
        "parent_task_id",
        "forked_from_session_id",
        "forked_from_turn_id",
        "forked_at",
        "starred_at",
        "archived_at",
        "deleted_at",
        "unread_at",
        "created_at",
    }
    if self._columns("web_sessions") == canonical_columns:
      return

    rows = [
        dict(row)
        for row in self._connection.execute(
            "select * from web_sessions order by rowid asc"
        ).fetchall()
    ]
    self._connection.execute("alter table web_sessions rename to web_sessions_legacy_normalize")
    self._connection.execute(_WEB_SESSIONS_SCHEMA_SQL)
    self._connection.executemany(
        """
        insert or ignore into web_sessions (
          id, project_id, agent_id, agent_runtime, title, title_source,
          parent_session_id, parent_task_id,
          forked_from_session_id, forked_from_turn_id, forked_at, starred_at,
          archived_at, deleted_at, unread_at, created_at
        ) values (
          :id, :project_id, :agent_id, :agent_runtime, :title, :title_source,
          :parent_session_id, :parent_task_id,
          :forked_from_session_id, :forked_from_turn_id, :forked_at, :starred_at,
          :archived_at, :deleted_at, :unread_at, :created_at
        )
        """,
        [
            {
                "id": str(row["id"]),
                "project_id": row.get("project_id"),
                "agent_id": _fallback_agent_id(row),
                "agent_runtime": _fallback_agent_runtime(row),
                "title": row.get("title"),
                "title_source": row.get("title_source") or "auto",
                "parent_session_id": row.get("parent_session_id") or row.get("parent_thread_id"),
                "parent_task_id": row.get("parent_task_id"),
                "forked_from_session_id": row.get("forked_from_session_id") or row.get("forked_from_thread_id"),
                "forked_from_turn_id": row.get("forked_from_turn_id"),
                "forked_at": row.get("forked_at"),
                "starred_at": row.get("starred_at"),
                "archived_at": row.get("archived_at"),
                "deleted_at": row.get("deleted_at"),
                "unread_at": row.get("unread_at"),
                "created_at": row.get("created_at") or now_iso(),
            }
            for row in rows
            if row.get("id")
        ],
    )
    self._connection.execute("drop table web_sessions_legacy_normalize")
    self._create_session_indexes()

  def _normalize_web_turns_schema(self) -> None:
    if not self._table_exists("web_turns"):
      return
    canonical_columns = {
        "id",
        "session_id",
        "model_config_id",
        "title",
        "input_text",
        "trigger_kind",
        "status",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "cancel_requested_at",
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "tool_call_count",
        "tool_success_count",
        "tool_fail_count",
        "tool_duration_ms",
        "file_lines_added",
        "file_lines_removed",
        "final_text",
        "error_type",
        "error_message",
    }
    if self._columns("web_turns") == canonical_columns:
      return

    rows = [
        dict(row)
        for row in self._connection.execute(
            "select * from web_turns order by rowid asc"
        ).fetchall()
    ]
    self._seed_sessions_from_turn_rows(rows)
    self._connection.execute("alter table web_turns rename to web_turns_legacy_normalize")
    self._connection.execute(_WEB_TURNS_SCHEMA_SQL)
    self._connection.executemany(
        """
        insert or ignore into web_turns (
          id, session_id, model_config_id, title, input_text, trigger_kind,
          status, created_at, updated_at, started_at, finished_at,
          cancel_requested_at, input_token_count, output_token_count,
          total_token_count, tool_call_count, tool_success_count,
          tool_fail_count, tool_duration_ms, file_lines_added,
          file_lines_removed, final_text, error_type, error_message
        ) values (
          :id, :session_id, :model_config_id, :title, :input_text, :trigger_kind,
          :status, :created_at, :updated_at, :started_at, :finished_at,
          :cancel_requested_at, :input_token_count, :output_token_count,
          :total_token_count, :tool_call_count, :tool_success_count,
          :tool_fail_count, :tool_duration_ms, :file_lines_added,
          :file_lines_removed, :final_text, :error_type, :error_message
        )
        """,
        [
            {
                "id": str(row["id"]),
                "session_id": row.get("session_id") or row.get("thread_id"),
                "model_config_id": row.get("model_config_id") or DEFAULT_MODEL_CONFIG_ID,
                "title": row.get("title"),
                "input_text": row.get("input_text") or row.get("prompt") or "",
                "trigger_kind": row.get("trigger_kind") or "user_message",
                "status": row.get("status") or "completed",
                "created_at": row.get("created_at") or now_iso(),
                "updated_at": row.get("updated_at") or row.get("created_at") or now_iso(),
                "started_at": row.get("started_at"),
                "finished_at": row.get("finished_at"),
                "cancel_requested_at": row.get("cancel_requested_at"),
                "input_token_count": int(row.get("input_token_count") or 0),
                "output_token_count": int(row.get("output_token_count") or 0),
                "total_token_count": int(row.get("total_token_count") or 0),
                "tool_call_count": int(row.get("tool_call_count") or 0),
                "tool_success_count": int(row.get("tool_success_count") or 0),
                "tool_fail_count": int(row.get("tool_fail_count") or 0),
                "tool_duration_ms": int(row.get("tool_duration_ms") or 0),
                "file_lines_added": int(row.get("file_lines_added") or 0),
                "file_lines_removed": int(row.get("file_lines_removed") or 0),
                "final_text": row.get("final_text"),
                "error_type": row.get("error_type"),
                "error_message": row.get("error_message"),
            }
            for row in rows
            if row.get("id") and (row.get("session_id") or row.get("thread_id"))
        ],
    )
    self._connection.execute("drop table web_turns_legacy_normalize")
    self._create_turn_indexes()

  def _seed_sessions_from_turn_rows(self, rows: list[dict[str, Any]]) -> None:
    now = now_iso()
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for row in rows:
      session_id = str(row.get("session_id") or row.get("thread_id") or "").strip()
      if not session_id or session_id in seen:
        continue
      seen.add(session_id)
      records.append(
          {
              "id": session_id,
              "project_id": row.get("project_id"),
              "agent_id": _fallback_agent_id(row),
              "agent_runtime": _fallback_agent_runtime(row),
              "title": row.get("title"),
              "title_source": row.get("title_source") or "auto",
              "parent_session_id": row.get("parent_session_id") or row.get("parent_thread_id"),
              "parent_task_id": row.get("parent_task_id"),
              "forked_from_session_id": row.get("forked_from_session_id") or row.get("forked_from_thread_id"),
              "forked_from_turn_id": row.get("forked_from_turn_id"),
              "forked_at": row.get("forked_at"),
              "starred_at": row.get("starred_at"),
              "archived_at": row.get("archived_at"),
              "deleted_at": row.get("deleted_at"),
              "unread_at": row.get("unread_at"),
              "created_at": row.get("created_at") or now,
          }
      )
    self._connection.executemany(
        """
        insert or ignore into web_sessions (
          id, project_id, agent_id, agent_runtime, title, title_source,
          parent_session_id, parent_task_id,
          forked_from_session_id, forked_from_turn_id, forked_at, starred_at,
          archived_at, deleted_at, unread_at, created_at
        ) values (
          :id, :project_id, :agent_id, :agent_runtime, :title, :title_source,
          :parent_session_id, :parent_task_id,
          :forked_from_session_id, :forked_from_turn_id, :forked_at, :starred_at,
          :archived_at, :deleted_at, :unread_at, :created_at
        )
        """,
        records,
    )

  def _normalize_web_steps_schema(self) -> None:
    if not self._table_exists("web_steps"):
      return
    canonical_columns = {
        "id",
        "turn_id",
        "seq",
        "session_seq",
        "kind",
        "summary",
        "payload_json",
        "created_at",
    }
    if self._columns("web_steps") == canonical_columns:
      return

    rows = [
        dict(row)
        for row in self._connection.execute(
            """
            select e.*
            from web_steps e
            left join web_turns i on i.id = e.turn_id
            order by i.session_id asc, coalesce(e.session_seq, e.seq) asc, e.turn_id asc, e.seq asc
            """
        ).fetchall()
    ]
    self._connection.execute("alter table web_steps rename to web_steps_legacy_normalize")
    self._connection.execute(_WEB_STEPS_SCHEMA_SQL)
    event_store = RuntimeEventStore(self.path.parent)
    event_indexes: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
      self._insert_step_row(self._normalized_step_row(row, event_store, event_indexes))
    self._connection.execute("drop table web_steps_legacy_normalize")
    self._create_step_indexes()

  def _normalize_web_turn_attachments_schema(self) -> None:
    if not self._table_exists("web_turn_attachments"):
      return
    canonical_columns = {
        "id",
        "turn_id",
        "ordinal",
        "filename",
        "mime_type",
        "kind",
        "byte_count",
        "storage_path",
        "created_at",
    }
    if self._columns("web_turn_attachments") == canonical_columns:
      return

    rows = [
        dict(row)
        for row in self._connection.execute(
            "select * from web_turn_attachments order by turn_id asc, ordinal asc, rowid asc"
        ).fetchall()
    ]
    self._connection.execute(
        "alter table web_turn_attachments rename to web_turn_attachments_legacy_normalize"
    )
    self._connection.execute(_WEB_TURN_ATTACHMENTS_SCHEMA_SQL)
    self._connection.executemany(
        """
        insert or ignore into web_turn_attachments (
          id, turn_id, ordinal, filename, mime_type,
          kind, byte_count, storage_path, created_at
        ) values (
          :id, :turn_id, :ordinal, :filename, :mime_type,
          :kind, :byte_count, :storage_path, :created_at
        )
        """,
        [
            {
                "id": str(row["id"]),
                "turn_id": row.get("turn_id") or row.get("invocation_id"),
                "ordinal": int(row.get("ordinal") or 0),
                "filename": row.get("filename") or "",
                "mime_type": row.get("mime_type") or "application/octet-stream",
                "kind": row.get("kind") or "file",
                "byte_count": int(row.get("byte_count") or 0),
                "storage_path": row.get("storage_path") or "",
                "created_at": row.get("created_at") or now_iso(),
            }
            for row in rows
            if row.get("id") and (row.get("turn_id") or row.get("invocation_id"))
        ],
    )
    self._connection.execute("drop table web_turn_attachments_legacy_normalize")
    self._create_attachment_indexes()

  def _create_turn_indexes(self) -> None:
    self._connection.executescript(
        """
        create index if not exists idx_web_turns_session
          on web_turns(session_id);
        """
    )

  def _create_session_indexes(self) -> None:
    self._connection.executescript(
        """
        create index if not exists idx_web_sessions_project_created
          on web_sessions(project_id, created_at desc);
        create index if not exists idx_web_sessions_starred
          on web_sessions(starred_at desc);
        create index if not exists idx_web_sessions_archived
          on web_sessions(archived_at desc);
        create index if not exists idx_web_sessions_deleted
          on web_sessions(deleted_at);
        """
    )

  def _create_step_indexes(self) -> None:
    self._connection.executescript(
        """
        create index if not exists idx_web_steps_turn_seq
          on web_steps(turn_id, seq);
        """
    )

  def _create_attachment_indexes(self) -> None:
    self._connection.executescript(
        """
        create index if not exists idx_web_turn_attachments_turn
          on web_turn_attachments(turn_id, ordinal);
        """
    )

  def _normalized_step_row(
      self,
      row: dict[str, Any],
      event_store: RuntimeEventStore,
      event_indexes: dict[tuple[str, str], dict[str, dict[str, Any]]],
  ) -> dict[str, Any]:
    turn_id = str(row.get("turn_id") or "")
    session_id = str(row.get("session_id") or row.get("thread_id") or self._session_id_for_turn(turn_id) or "")
    raw_event = _json_dict(row.get("raw_event_json")) if row.get("raw_event_json") is not None else {}
    runtime = str(row.get("runtime") or self._runtime_for_session(session_id) or "native")
    step_id = _optional_str(
        row.get("id")
        or row.get("runtime_event_id")
        or row.get("adk_event_id")
        or raw_event.get("id")
        or raw_event.get("event_id")
        or raw_event.get("eventId")
    )
    if raw_event and step_id:
      self._persist_legacy_runtime_event(
          event_store=event_store,
          event_indexes=event_indexes,
          session_id=session_id,
          turn_id=turn_id,
          runtime=runtime,
          event_id=step_id,
          created_at=_optional_str(row.get("created_at")),
          raw_event=raw_event,
      )
    return {
        "id": step_id or f"step_{uuid.uuid4().hex[:12]}",
        "turn_id": turn_id,
        "seq": int(row.get("seq") or 0),
        "session_seq": _optional_int(row.get("session_seq") or row.get("thread_seq")),
        "kind": str(row.get("kind") or "runtime_step"),
        "summary": str(row.get("summary") or ""),
        "payload_json": row.get("payload_json") or "{}",
        "created_at": row.get("created_at") or now_iso(),
    }

  def _persist_legacy_runtime_event(
      self,
      *,
      event_store: RuntimeEventStore,
      event_indexes: dict[tuple[str, str], dict[str, dict[str, Any]]],
      session_id: str,
      turn_id: str,
      runtime: str,
      event_id: str,
      created_at: str | None,
      raw_event: dict[str, Any],
  ) -> dict[str, Any] | None:
    if not session_id or not event_id:
      return None
    index_key = (session_id, runtime)
    index = event_indexes.get(index_key)
    if index is None:
      index = event_store.identity_index(session_id=session_id, runtime=runtime)
      event_indexes[index_key] = index
    existing = index.get(event_id)
    if existing is not None:
      return existing
    envelope = event_store.append(
        session_id=session_id,
        turn_id=turn_id or None,
        runtime=runtime,
        event_id=event_id,
        created_at=created_at,
        event=raw_event,
    )
    index[event_id] = envelope
    return envelope

  def _runtime_for_session(self, session_id: str) -> str:
    if session_id:
      row = self._connection.execute(
          "select agent_runtime from web_sessions where id = ?",
          (session_id,),
      ).fetchone()
      if row and row["agent_runtime"]:
        return str(row["agent_runtime"])
    return "native"

  def _session_id_for_turn(self, turn_id: str) -> str | None:
    if not turn_id:
      return None
    row = self._connection.execute(
        "select session_id from web_turns where id = ?",
        (turn_id,),
    ).fetchone()
    if row and row["session_id"]:
      return str(row["session_id"])
    return None

  def _insert_step_row(self, row: dict[str, Any]) -> None:
    self._connection.execute(
        """
        insert or ignore into web_steps (
          id, turn_id, seq, session_seq,
          kind, summary, payload_json, created_at
        ) values (
          :id, :turn_id, :seq, :session_seq,
          :kind, :summary, :payload_json, :created_at
        )
        """,
        row,
    )

  def create_project(self, *, name: str, root_path: str) -> dict[str, Any]:
    project_id = f"proj_{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    display_name = name.strip() or Path(root_path).name or root_path
    row = {
        "id": project_id,
        "name": display_name,
        "root_path": root_path,
        "created_at": created_at,
        "updated_at": created_at,
        "last_opened_at": created_at,
    }
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_projects (
            id, name, root_path, created_at, updated_at, last_opened_at
          ) values (
            :id, :name, :root_path, :created_at, :updated_at, :last_opened_at
          )
          """,
          row,
      )
    return row

  def get_project(self, project_id: str) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          "select * from web_projects where id = ?",
          (project_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_projects(self) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_projects
          order by last_opened_at desc, created_at desc
          """
      ).fetchall()
    return [dict(row) for row in rows]

  def touch_project(self, project_id: str) -> dict[str, Any]:
    updated_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_projects
          set updated_at = ?, last_opened_at = ?
          where id = ?
          """,
          (updated_at, updated_at, project_id),
      )
    project = self.get_project(project_id)
    if project is None:
      raise KeyError(f"Project not found: {project_id}")
    return project

  def update_project_name(self, project_id: str, *, name: str) -> dict[str, Any]:
    display_name = name.strip()
    if not display_name:
      raise ValueError("Project name is required")
    updated_at = now_iso()
    with self._lock, self._connection:
      cursor = self._connection.execute(
          """
          update web_projects
          set name = ?, updated_at = ?
          where id = ?
          """,
          (display_name, updated_at, project_id),
      )
      if cursor.rowcount == 0:
        raise KeyError(f"Project not found: {project_id}")
      row = self._connection.execute(
          "select * from web_projects where id = ?",
          (project_id,),
      ).fetchone()
    if row is None:
      raise KeyError(f"Project not found: {project_id}")
    return dict(row)

  def delete_project(self, project_id: str) -> dict[str, Any]:
    with self._lock, self._connection:
      row = self._connection.execute(
          "select * from web_projects where id = ?",
          (project_id,),
      ).fetchone()
      if row is None:
        raise KeyError(f"Project not found: {project_id}")
      self._connection.execute(
          "delete from web_projects where id = ?",
          (project_id,),
      )
    return dict(row)

  # ---- Automated tasks -------------------------------------------------

  def create_automated_task(
      self,
      *,
      project_id: str,
      name: str,
      prompt: str,
      agent_id: str,
      model_config_id: str | None = None,
      description: str | None = None,
      enabled: bool = False,
  ) -> dict[str, Any]:
    task_id = f"atask_{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    row = {
        "id": task_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "enabled": 1 if enabled else 0,
        "agent_id": agent_id,
        "model_config_id": model_config_id,
        "prompt": prompt,
        "last_triggered_at": None,
        "last_run_session_id": None,
        "created_at": created_at,
        "updated_at": created_at,
        "deleted_at": None,
    }
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_automated_tasks (
            id, project_id, name, description, enabled, agent_id,
            model_config_id, prompt, last_triggered_at, last_run_session_id,
            created_at, updated_at, deleted_at
          ) values (
            :id, :project_id, :name, :description, :enabled, :agent_id,
            :model_config_id, :prompt, :last_triggered_at, :last_run_session_id,
            :created_at, :updated_at, :deleted_at
          )
          """,
          row,
      )
    return row

  _LAST_RUN_STATUS_SUBQUERY = """
    (select r.status from web_automated_task_runs r
       where r.automated_task_id = t.id
       order by r.created_at desc, r.rowid desc limit 1) as last_run_status
  """

  def get_automated_task(
      self,
      task_id: str,
      *,
      include_deleted: bool = False,
  ) -> dict[str, Any] | None:
    clause = "" if include_deleted else " and t.deleted_at is null"
    with self._lock:
      row = self._connection.execute(
          f"""
          select t.*, {self._LAST_RUN_STATUS_SUBQUERY}
          from web_automated_tasks t
          where t.id = ?{clause}
          """,
          (task_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_automated_tasks(
      self,
      *,
      project_id: str | None = None,
  ) -> list[dict[str, Any]]:
    clauses = ["t.deleted_at is null"]
    params: list[Any] = []
    if project_id:
      clauses.append("t.project_id = ?")
      params.append(project_id)
    where = " and ".join(clauses)
    with self._lock:
      rows = self._connection.execute(
          f"""
          select t.*, {self._LAST_RUN_STATUS_SUBQUERY}
          from web_automated_tasks t
          where {where}
          order by t.created_at desc, t.rowid desc
          """,
          params,
      ).fetchall()
    return [dict(row) for row in rows]

  def update_automated_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
    fields = {key: value for key, value in fields.items() if value is not None}
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    payload = {"id": task_id, **fields}
    with self._lock, self._connection:
      self._connection.execute(
          f"update web_automated_tasks set {assignments} where id = :id",
          payload,
      )
    return self.get_automated_task(task_id, include_deleted=True)

  def set_automated_task_enabled(
      self,
      task_id: str,
      *,
      enabled: bool,
  ) -> dict[str, Any] | None:
    return self.update_automated_task(task_id, enabled=1 if enabled else 0)

  def delete_automated_task(self, task_id: str) -> dict[str, Any] | None:
    deleted_at = now_iso()
    with self._lock, self._connection:
      cursor = self._connection.execute(
          """
          update web_automated_tasks
          set deleted_at = ?, enabled = 0, updated_at = ?
          where id = ? and deleted_at is null
          """,
          (deleted_at, deleted_at, task_id),
      )
      if cursor.rowcount == 0:
        return None
    return self.get_automated_task(task_id, include_deleted=True)

  def mark_automated_task_triggered(
      self,
      task_id: str,
      *,
      session_id: str | None,
  ) -> None:
    triggered_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_automated_tasks
          set last_triggered_at = ?, last_run_session_id = ?, updated_at = ?
          where id = ?
          """,
          (triggered_at, session_id, triggered_at, task_id),
      )

  def replace_automated_task_triggers(
      self,
      task_id: str,
      triggers: list[dict[str, Any]],
  ) -> list[dict[str, Any]]:
    now = now_iso()
    rows = [
        {
            "id": f"atrig_{uuid.uuid4().hex[:12]}",
            "automated_task_id": task_id,
            "type": str(trig.get("type") or ""),
            "enabled": 1 if trig.get("enabled", True) else 0,
            "config_json": json.dumps(trig.get("config") or {}),
            "next_fire_at": trig.get("next_fire_at"),
            "last_fired_at": None,
            "created_at": now,
            "updated_at": now,
        }
        for trig in triggers
    ]
    with self._lock, self._connection:
      self._connection.execute(
          "delete from web_automated_task_triggers where automated_task_id = ?",
          (task_id,),
      )
      if rows:
        self._connection.executemany(
            """
            insert into web_automated_task_triggers (
              id, automated_task_id, type, enabled, config_json,
              next_fire_at, last_fired_at, created_at, updated_at
            ) values (
              :id, :automated_task_id, :type, :enabled, :config_json,
              :next_fire_at, :last_fired_at, :created_at, :updated_at
            )
            """,
            rows,
        )
    return self.list_automated_task_triggers(task_id)

  def list_automated_task_triggers(self, task_id: str) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_automated_task_triggers
          where automated_task_id = ?
          order by created_at asc, rowid asc
          """,
          (task_id,),
      ).fetchall()
    return [dict(row) for row in rows]

  def list_due_time_triggers(self, *, now: str) -> list[dict[str, Any]]:
    """Enabled time triggers (on enabled, live tasks) whose next_fire_at has
    arrived. next_fire_at is stored in the same '...Z' format as `now`, so the
    `<=` is a correct chronological compare. Indexed on next_fire_at."""
    with self._lock:
      rows = self._connection.execute(
          """
          select trig.id as trigger_id,
                 trig.automated_task_id as task_id,
                 trig.config_json as config_json,
                 trig.next_fire_at as next_fire_at
          from web_automated_task_triggers trig
          join web_automated_tasks t on t.id = trig.automated_task_id
          where trig.type = 'time'
            and trig.enabled = 1
            and t.enabled = 1
            and t.deleted_at is null
            and trig.next_fire_at is not null
            and trig.next_fire_at <= ?
          order by trig.next_fire_at asc, trig.rowid asc
          """,
          (now,),
      ).fetchall()
    return [dict(row) for row in rows]

  def list_time_triggers_missing_next_fire(self) -> list[dict[str, Any]]:
    """Time triggers on live tasks with no computed next_fire_at — used to
    backfill schedules at startup (rows predating the scheduler)."""
    with self._lock:
      rows = self._connection.execute(
          """
          select trig.id as trigger_id,
                 trig.automated_task_id as task_id,
                 trig.config_json as config_json
          from web_automated_task_triggers trig
          join web_automated_tasks t on t.id = trig.automated_task_id
          where trig.type = 'time'
            and trig.next_fire_at is null
            and t.deleted_at is null
          """,
      ).fetchall()
    return [dict(row) for row in rows]

  def set_automated_task_trigger_next_fire(
      self,
      trigger_id: str,
      *,
      next_fire_at: str | None,
  ) -> None:
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_automated_task_triggers
          set next_fire_at = ?, updated_at = ?
          where id = ?
          """,
          (next_fire_at, now_iso(), trigger_id),
      )

  def advance_automated_task_trigger(
      self,
      trigger_id: str,
      *,
      next_fire_at: str | None,
      last_fired_at: str,
  ) -> None:
    """Record a fire: stamp last_fired_at and move next_fire_at to the next
    slot (None if the cron became unparseable, leaving the trigger inert)."""
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_automated_task_triggers
          set next_fire_at = ?, last_fired_at = ?, updated_at = ?
          where id = ?
          """,
          (next_fire_at, last_fired_at, now_iso(), trigger_id),
      )

  def create_automated_task_run(
      self,
      *,
      automated_task_id: str,
      trigger_kind: str,
      trigger_id: str | None = None,
      dedup_key: str | None = None,
      trigger_context: dict[str, Any] | None = None,
  ) -> dict[str, Any] | None:
    run_id = f"arun_{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    row = {
        "id": run_id,
        "automated_task_id": automated_task_id,
        "trigger_id": trigger_id,
        "trigger_kind": trigger_kind,
        "dedup_key": dedup_key,
        "trigger_context_json": json.dumps(trigger_context) if trigger_context else None,
        "session_id": None,
        "turn_id": None,
        "status": "launched",
        "error_message": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
    with self._lock, self._connection:
      cursor = self._connection.execute(
          """
          insert or ignore into web_automated_task_runs (
            id, automated_task_id, trigger_id, trigger_kind, dedup_key,
            trigger_context_json, session_id, turn_id, status, error_message,
            created_at, updated_at
          ) values (
            :id, :automated_task_id, :trigger_id, :trigger_kind, :dedup_key,
            :trigger_context_json, :session_id, :turn_id, :status, :error_message,
            :created_at, :updated_at
          )
          """,
          row,
      )
      # A non-null dedup_key that already exists is ignored (insert or ignore);
      # rowcount 0 means this fire was already launched. NULL keys (manual runs)
      # are distinct in SQLite and always insert.
      if cursor.rowcount == 0:
        return None
    return row

  def update_automated_task_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
    fields = {key: value for key, value in fields.items() if value is not None}
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    payload = {"id": run_id, **fields}
    with self._lock, self._connection:
      self._connection.execute(
          f"update web_automated_task_runs set {assignments} where id = :id",
          payload,
      )
    return self.get_automated_task_run(run_id)

  def attach_automated_task_run_session(
      self,
      run_id: str,
      *,
      session_id: str,
      turn_id: str,
  ) -> dict[str, Any] | None:
    return self.update_automated_task_run(
        run_id,
        session_id=session_id,
        turn_id=turn_id,
    )

  def get_automated_task_run(self, run_id: str) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          "select * from web_automated_task_runs where id = ?",
          (run_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_automated_task_runs(
      self,
      task_id: str,
      *,
      limit: int = 50,
  ) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_automated_task_runs
          where automated_task_id = ?
          order by created_at desc, rowid desc
          limit ?
          """,
          (task_id, max(1, min(limit, 200))),
      ).fetchall()
    return [dict(row) for row in rows]

  def list_automated_task_runs_with_status(
      self,
      status: str,
  ) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_automated_task_runs
          where status = ?
          order by created_at asc, rowid asc
          """,
          (status,),
      ).fetchall()
    return [dict(row) for row in rows]

  def get_web_settings(self, *, user_id: str) -> dict[str, Any]:
    return {
        "theme_id": self.get_user_setting(
            user_id=user_id,
            key="theme_id",
            default="dark",
        ),
        "model_config_id": self.get_user_setting(
            user_id=user_id,
            key="model_config_id",
            default=DEFAULT_MODEL_CONFIG_ID,
        ),
        "streaming_mode_enabled": _setting_bool(
            self.get_user_setting(
                user_id=user_id,
                key="streaming_mode_enabled",
                default="true",
            )
        ),
        "folded_project_ids": _setting_str_list(
            self.get_user_setting(
                user_id=user_id,
                key="folded_project_ids",
                default="[]",
            )
        ),
        "gemini_api_key": self.get_user_setting(
            user_id=user_id,
            key="gemini_api_key",
            default="",
        ),
    }

  def get_user_setting(
      self,
      *,
      user_id: str,
      key: str,
      default: str | None = None,
  ) -> str | None:
    with self._lock:
      row = self._connection.execute(
          """
          select value from web_user_settings
          where user_id = ? and key = ?
          """,
          (user_id, key),
      ).fetchone()
    return str(row["value"]) if row else default

  def set_user_setting(self, *, user_id: str, key: str, value: str) -> dict[str, Any]:
    updated_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_user_settings (user_id, key, value, updated_at)
          values (?, ?, ?, ?)
          on conflict(user_id, key) do update set
            value = excluded.value,
            updated_at = excluded.updated_at
          """,
          (user_id, key, value, updated_at),
      )
    return {
        "user_id": user_id,
        "key": key,
        "value": value,
        "updated_at": updated_at,
    }

  def create_session(
      self,
      *,
      session_id: str,
      project_id: str | None,
      agent_id: str,
      agent_runtime: str = "native",
      title: str | None = None,
      title_source: str = "auto",
      parent_session_id: str | None = None,
      parent_task_id: str | None = None,
      forked_from_session_id: str | None = None,
      forked_from_turn_id: str | None = None,
      forked_at: str | None = None,
  ) -> dict[str, Any]:
    row = {
        "id": session_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "agent_runtime": agent_runtime,
        "title": title,
        "title_source": title_source,
        "parent_session_id": parent_session_id,
        "parent_task_id": parent_task_id,
        "forked_from_session_id": forked_from_session_id,
        "forked_from_turn_id": forked_from_turn_id,
        "forked_at": forked_at,
        "starred_at": None,
        "archived_at": None,
        "deleted_at": None,
        "unread_at": None,
        "created_at": now_iso(),
    }
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert or ignore into web_sessions (
            id, project_id, agent_id, agent_runtime, title, title_source,
            parent_session_id, parent_task_id,
            forked_from_session_id, forked_from_turn_id, forked_at, starred_at,
            archived_at, deleted_at, unread_at, created_at
          ) values (
            :id, :project_id, :agent_id, :agent_runtime, :title, :title_source,
            :parent_session_id, :parent_task_id,
            :forked_from_session_id, :forked_from_turn_id, :forked_at, :starred_at,
            :archived_at, :deleted_at, :unread_at, :created_at
          )
          """,
          row,
      )
    return self.get_session_meta(session_id) or row

  def get_session_meta(
      self,
      session_id: str,
      *,
      include_deleted: bool = False,
  ) -> dict[str, Any] | None:
    deleted_clause = "" if include_deleted else " and deleted_at is null"
    with self._lock:
      row = self._connection.execute(
          f"select * from web_sessions where id = ?{deleted_clause}",
          (session_id,),
      ).fetchone()
    return dict(row) if row else None

  def is_session_deleted(self, session_id: str) -> bool:
    with self._lock:
      row = self._connection.execute(
          """
          select deleted_at from web_sessions
          where id = ? and deleted_at is not null
          """,
          (session_id,),
      ).fetchone()
    return row is not None

  def list_session_metas(
      self,
      *,
      project_id: str | None = None,
      include_children: bool = False,
      include_archived: bool = False,
      archived: bool | None = None,
  ) -> list[dict[str, Any]]:
    clauses: list[str] = ["deleted_at is null"]
    params: list[Any] = []
    if project_id is not None:
      clauses.append("project_id = ?")
      params.append(project_id)
    if not include_children:
      clauses.append("parent_session_id is null")
    if archived is not None:
      clauses.append("archived_at is not null" if archived else "archived_at is null")
    elif not include_archived:
      clauses.append("archived_at is null")
    where = (" where " + " and ".join(clauses)) if clauses else ""
    with self._lock:
      rows = self._connection.execute(
          f"select * from web_sessions{where} order by created_at desc, rowid desc",
          params,
      ).fetchall()
    return [dict(row) for row in rows]

  def update_session_title(
      self,
      session_id: str,
      title: str,
      *,
      source: str = "manual",
  ) -> dict[str, Any] | None:
    with self._lock, self._connection:
      if source == "auto":
        # Never clobber a user-set name with an auto-generated one.
        self._connection.execute(
            """
            update web_sessions set title = ?, title_source = 'auto'
            where id = ? and title_source = 'auto'
            """,
            (title, session_id),
        )
      else:
        self._connection.execute(
            "update web_sessions set title = ?, title_source = 'manual' where id = ?",
            (title, session_id),
        )
    return self.get_session_meta(session_id)

  def set_session_archive(
      self,
      *,
      session_id: str,
      archived: bool,
  ) -> dict[str, Any] | None:
    archived_at = now_iso() if archived else None
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_sessions
          set archived_at = ?
          where id = ? and deleted_at is null
          """,
          (archived_at, session_id),
      )
    return self.get_session_meta(session_id)

  def set_session_unread(
      self,
      *,
      session_id: str,
      unread: bool,
  ) -> dict[str, Any] | None:
    unread_at = now_iso() if unread else None
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_sessions
          set unread_at = ?
          where id = ? and deleted_at is null
          """,
          (unread_at, session_id),
      )
    return self.get_session_meta(session_id)

  def soft_delete_session(self, session_id: str) -> dict[str, Any] | None:
    deleted_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_sessions
          set deleted_at = ?, archived_at = null, unread_at = null
          where id = ? and deleted_at is null
          """,
          (deleted_at, session_id),
      )
    return self.get_session_meta(session_id, include_deleted=True)

  def set_session_star(
      self,
      *,
      session_id: str,
      starred: bool,
  ) -> dict[str, Any]:
    starred_at = now_iso() if starred else None
    with self._lock, self._connection:
      self._connection.execute(
          "update web_sessions set starred_at = ? where id = ?",
          (starred_at, session_id),
      )
    return {
        "session_id": session_id,
        "starred": starred,
        "starred_at": starred_at,
    }

  def create_turn(
      self,
      *,
      session_id: str,
      input_text: str,
      title: str,
      trigger_kind: str = "user_message",
      model_config_id: str = DEFAULT_MODEL_CONFIG_ID,
  ) -> dict[str, Any]:
    turn_id = f"inv_{uuid.uuid4().hex[:12]}"
    created_at = now_iso()
    row = {
        "id": turn_id,
        "session_id": session_id,
        "model_config_id": model_config_id,
        "title": title,
        "input_text": input_text,
        "trigger_kind": trigger_kind,
        "status": "queued",
        "created_at": created_at,
        "updated_at": created_at,
        "started_at": None,
        "finished_at": None,
        "cancel_requested_at": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "tool_call_count": 0,
        "tool_success_count": 0,
        "tool_fail_count": 0,
        "tool_duration_ms": 0,
        "file_lines_added": 0,
        "file_lines_removed": 0,
        "final_text": None,
        "error_type": None,
        "error_message": None,
    }
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_turns (
            id, session_id, model_config_id, title, input_text,
            trigger_kind, status, created_at, updated_at, started_at,
            finished_at, cancel_requested_at, input_token_count,
            output_token_count, total_token_count, tool_call_count,
            tool_success_count, tool_fail_count, tool_duration_ms,
            file_lines_added, file_lines_removed,
            final_text, error_type, error_message
          ) values (
            :id, :session_id, :model_config_id, :title, :input_text,
            :trigger_kind, :status, :created_at, :updated_at, :started_at,
            :finished_at, :cancel_requested_at, :input_token_count,
            :output_token_count, :total_token_count, :tool_call_count,
            :tool_success_count, :tool_fail_count, :tool_duration_ms,
            :file_lines_added, :file_lines_removed,
            :final_text, :error_type, :error_message
          )
          """,
          row,
      )
    return row

  def create_attachment(
      self,
      *,
      turn_id: str,
      ordinal: int,
      filename: str,
      mime_type: str,
      kind: str,
      byte_count: int,
      storage_path: str,
  ) -> dict[str, Any]:
    attachment_id = f"att_{uuid.uuid4().hex[:12]}"
    row = {
        "id": attachment_id,
        "turn_id": turn_id,
        "ordinal": ordinal,
        "filename": filename,
        "mime_type": mime_type,
        "kind": kind,
        "byte_count": byte_count,
        "storage_path": storage_path,
        "created_at": now_iso(),
    }
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_turn_attachments (
            id, turn_id, ordinal, filename, mime_type,
            kind, byte_count, storage_path, created_at
          ) values (
            :id, :turn_id, :ordinal, :filename, :mime_type,
            :kind, :byte_count, :storage_path, :created_at
          )
          """,
          row,
      )
    return row

  def list_attachments_for_turn(
      self,
      turn_id: str,
  ) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_turn_attachments
          where turn_id = ?
          order by ordinal asc, rowid asc
          """,
          (turn_id,),
      ).fetchall()
    return [dict(row) for row in rows]

  def list_attachments_for_turns(
      self,
      turn_ids: list[str],
  ) -> dict[str, list[dict[str, Any]]]:
    ids = [item for item in turn_ids if item]
    if not ids:
      return {}
    placeholders = ", ".join("?" for _ in ids)
    with self._lock:
      rows = self._connection.execute(
          f"""
          select * from web_turn_attachments
          where turn_id in ({placeholders})
          order by turn_id asc, ordinal asc, rowid asc
          """,
          ids,
      ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {turn_id: [] for turn_id in ids}
    for row in rows:
      item = dict(row)
      result.setdefault(str(item["turn_id"]), []).append(item)
    return result

  def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          "select * from web_turn_attachments where id = ?",
          (attachment_id,),
      ).fetchone()
    return dict(row) if row else None

  def clone_attachments_to_turn(
      self,
      *,
      attachment_ids: list[str],
      target_turn_id: str,
      starting_ordinal: int = 0,
  ) -> list[dict[str, Any]]:
    if not attachment_ids:
      return []
    deduped: list[str] = []
    seen: set[str] = set()
    for attachment_id in attachment_ids:
      attachment_id = str(attachment_id or "").strip()
      if not attachment_id or attachment_id in seen:
        continue
      seen.add(attachment_id)
      deduped.append(attachment_id)
    if not deduped:
      return []

    placeholders = ", ".join("?" for _ in deduped)
    with self._lock, self._connection:
      target_session_id = self._session_id_for_turn(target_turn_id)
      if target_session_id is None:
        raise KeyError(f"Turn not found: {target_turn_id}")
      rows = self._connection.execute(
          f"""
          select * from web_turn_attachments
          where id in ({placeholders})
          """,
          deduped,
      ).fetchall()
      by_id = {str(row["id"]): dict(row) for row in rows}
      missing = [attachment_id for attachment_id in deduped if attachment_id not in by_id]
      if missing:
        raise KeyError(f"Attachment not found: {missing[0]}")

      cloned: list[dict[str, Any]] = []
      now = now_iso()
      for offset, attachment_id in enumerate(deduped):
        row = by_id[attachment_id]
        cloned_row = {
            **row,
            "id": f"att_{uuid.uuid4().hex[:12]}",
            "turn_id": target_turn_id,
            "ordinal": starting_ordinal + offset,
            "created_at": now,
        }
        self._connection.execute(
            """
            insert into web_turn_attachments (
              id, turn_id, ordinal, filename, mime_type,
              kind, byte_count, storage_path, created_at
            ) values (
              :id, :turn_id, :ordinal, :filename, :mime_type,
              :kind, :byte_count, :storage_path, :created_at
            )
            """,
            cloned_row,
        )
        cloned.append(cloned_row)
    return cloned

  def get_turn(self, turn_id: str) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          "select * from web_turns where id = ?",
          (turn_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_turns(self, limit: int = 50) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_turns
          order by created_at desc, rowid desc
          limit ?
          """,
          (max(1, min(limit, 200)),),
      ).fetchall()
    return [dict(row) for row in rows]

  def list_turns_for_session(self, session_id: str) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_turns
          where session_id = ?
          order by created_at asc, rowid asc
          """,
          (session_id,),
      ).fetchall()
    return [dict(row) for row in rows]

  def get_latest_turn_for_session(
      self,
      session_id: str,
  ) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          """
          select * from web_turns
          where session_id = ?
          order by created_at desc, rowid desc
          limit 1
          """,
          (session_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_turns_with_statuses(
      self,
      statuses: tuple[str, ...] | list[str],
  ) -> list[dict[str, Any]]:
    values = [str(status) for status in statuses if str(status)]
    if not values:
      return []
    placeholders = ", ".join("?" for _ in values)
    with self._lock:
      rows = self._connection.execute(
          f"""
          select * from web_turns
          where status in ({placeholders})
          order by created_at asc, rowid asc
          """,
          values,
      ).fetchall()
    return [dict(row) for row in rows]

  def get_active_turn_for_session(
      self,
      session_id: str,
  ) -> dict[str, Any] | None:
    with self._lock:
      row = self._connection.execute(
          """
          select * from web_turns
          where session_id = ? and status = 'running'
          order by created_at desc, rowid desc
          limit 1
          """,
          (session_id,),
      ).fetchone()
    return dict(row) if row else None

  def fork_session_history(
      self,
      *,
      source_session_id: str,
      target_session_id: str,
      source_turn_id: str | None = None,
      include_source_turn: bool = True,
      title: str | None = None,
  ) -> dict[str, Any]:
    forked_at = now_iso()
    with self._lock, self._connection:
      source = self._connection.execute(
          "select * from web_sessions where id = ? and deleted_at is null",
          (source_session_id,),
      ).fetchone()
      if source is None:
        raise KeyError(f"Session not found: {source_session_id}")

      turns = [
          dict(row)
          for row in self._connection.execute(
              """
              select * from web_turns
              where session_id = ?
              order by created_at asc, rowid asc
              """,
              (source_session_id,),
          ).fetchall()
      ]
      selected = _fork_turn_prefix(
          turns,
          source_turn_id,
          include_source_turn=include_source_turn,
      )
      live = [turn for turn in selected if _is_live_turn_status(turn.get("status"))]
      if live:
        raise ValueError("Cannot fork a running turn")
      if source_turn_id and not include_source_turn:
        source_turn = next(
            (turn for turn in turns if str(turn.get("id")) == source_turn_id),
            None,
        )
        if source_turn and _is_live_turn_status(source_turn.get("status")):
          raise ValueError("Cannot fork a running turn")

      source_dict = dict(source)
      fork_title = title or _fork_session_title(source_dict.get("title"))
      self._connection.execute(
          """
          insert into web_sessions (
            id, project_id, agent_id, agent_runtime, title, title_source,
            parent_session_id, parent_task_id,
            forked_from_session_id, forked_from_turn_id, forked_at, starred_at,
            archived_at, deleted_at, unread_at, created_at
          ) values (
            :id, :project_id, :agent_id, :agent_runtime, :title, 'fork',
            null, null,
            :forked_from_session_id, :forked_from_turn_id, :forked_at, null,
            null, null, null, :created_at
          )
          """,
          {
              "id": target_session_id,
              "project_id": source_dict.get("project_id"),
              "agent_id": _fallback_agent_id(source_dict),
              "agent_runtime": _fallback_agent_runtime(source_dict),
              "title": fork_title,
              "forked_from_session_id": source_session_id,
              "forked_from_turn_id": source_turn_id,
              "forked_at": forked_at,
              "created_at": forked_at,
          },
      )

      turn_id_map: dict[str, str] = {}
      next_session_seq = 1
      for turn in selected:
        target_turn_id = f"inv_{uuid.uuid4().hex[:12]}"
        turn_id_map[str(turn["id"])] = target_turn_id
        cloned_turn = {
            **turn,
            "id": target_turn_id,
            "session_id": target_session_id,
        }
        self._connection.execute(
            """
            insert into web_turns (
              id, session_id, model_config_id, title, input_text,
              trigger_kind, status, created_at, updated_at, started_at,
              finished_at, cancel_requested_at, input_token_count,
              output_token_count, total_token_count, tool_call_count,
              tool_success_count, tool_fail_count, tool_duration_ms,
              file_lines_added, file_lines_removed,
              final_text, error_type, error_message
            ) values (
              :id, :session_id, :model_config_id, :title, :input_text,
              :trigger_kind, :status, :created_at, :updated_at, :started_at,
              :finished_at, :cancel_requested_at, :input_token_count,
              :output_token_count, :total_token_count, :tool_call_count,
              :tool_success_count, :tool_fail_count, :tool_duration_ms,
              :file_lines_added, :file_lines_removed,
              :final_text, :error_type, :error_message
            )
            """,
            cloned_turn,
        )
        self._clone_turn_attachments_unlocked(str(turn["id"]), target_turn_id)
        next_session_seq = self._clone_turn_steps_unlocked(
            source_turn_id=str(turn["id"]),
            target_turn_id=target_turn_id,
            next_session_seq=next_session_seq,
        )
      artifact_refs = (
          self._artifact_refs_for_turn_ids_unlocked({str(turn["id"]) for turn in selected})
          if not include_source_turn
          else None
      )

    meta = self.get_session_meta(target_session_id)
    if meta is None:
      raise KeyError(f"Session not found: {target_session_id}")
    return {
        "meta": meta,
        "source_turn_ids": set(turn_id_map),
        "turn_id_map": turn_id_map,
        "artifact_refs": artifact_refs,
    }

  def truncate_session_before_turn(
      self,
      *,
      session_id: str,
      source_turn_id: str,
  ) -> dict[str, Any]:
    with self._lock, self._connection:
      turns = [
          dict(row)
          for row in self._connection.execute(
              """
              select * from web_turns
              where session_id = ?
              order by created_at asc, rowid asc
              """,
              (session_id,),
          ).fetchall()
      ]
      source_index = next(
          (
              index
              for index, turn in enumerate(turns)
              if str(turn.get("id")) == source_turn_id
          ),
          None,
      )
      if source_index is None:
        raise KeyError(f"Turn not found: {source_turn_id}")
      live = [turn for turn in turns if _is_live_turn_status(turn.get("status"))]
      if live:
        raise ValueError("Cannot rewrite a session with a running turn")

      kept_turns = turns[:source_index]
      removed_turns = turns[source_index:]
      kept_turn_ids = {str(turn["id"]) for turn in kept_turns}
      removed_turn_ids = [str(turn["id"]) for turn in removed_turns]
      artifact_refs = self._artifact_refs_for_turn_ids_unlocked(kept_turn_ids)
      if removed_turn_ids:
        placeholders = ", ".join("?" for _ in removed_turn_ids)
        self._connection.execute(
            f"delete from web_steps where turn_id in ({placeholders})",
            removed_turn_ids,
        )
        self._connection.execute(
            f"delete from web_turn_attachments where turn_id in ({placeholders})",
            removed_turn_ids,
        )
        self._connection.execute(
            f"delete from web_turns where id in ({placeholders})",
            removed_turn_ids,
        )
    return {
        "kept_turn_ids": kept_turn_ids,
        "removed_turn_ids": removed_turn_ids,
        "artifact_refs": artifact_refs,
    }

  def _clone_turn_attachments_unlocked(
      self,
      source_turn_id: str,
      target_turn_id: str,
  ) -> None:
    rows = self._connection.execute(
        """
        select * from web_turn_attachments
        where turn_id = ?
        order by ordinal asc, rowid asc
        """,
        (source_turn_id,),
    ).fetchall()
    self._connection.executemany(
        """
        insert into web_turn_attachments (
          id, turn_id, ordinal, filename, mime_type,
          kind, byte_count, storage_path, created_at
        ) values (
          :id, :turn_id, :ordinal, :filename, :mime_type,
          :kind, :byte_count, :storage_path, :created_at
        )
        """,
        [
            {
                **dict(row),
                "id": f"att_{uuid.uuid4().hex[:12]}",
                "turn_id": target_turn_id,
            }
            for row in rows
        ],
    )

  def _clone_turn_steps_unlocked(
      self,
      *,
      source_turn_id: str,
      target_turn_id: str,
      next_session_seq: int,
  ) -> int:
    rows = self._connection.execute(
        """
        select * from web_steps
        where turn_id = ?
        order by seq asc, rowid asc
        """,
        (source_turn_id,),
    ).fetchall()
    for row in rows:
      cloned = dict(row)
      cloned["id"] = f"step_{uuid.uuid4().hex[:12]}"
      cloned["turn_id"] = target_turn_id
      cloned["session_seq"] = next_session_seq
      self._insert_step_row(cloned)
      next_session_seq += 1
    return next_session_seq

  def _artifact_refs_for_turn_ids_unlocked(
      self,
      turn_ids: set[str],
  ) -> set[tuple[str, int | None]]:
    if not turn_ids:
      return set()
    placeholders = ", ".join("?" for _ in turn_ids)
    rows = self._connection.execute(
        f"""
        select payload_json from web_steps
        where turn_id in ({placeholders}) and kind = 'artifact_delta'
        """,
        list(turn_ids),
    ).fetchall()
    refs: set[tuple[str, int | None]] = set()
    for row in rows:
      try:
        payload = json.loads(row["payload_json"])
      except (TypeError, json.JSONDecodeError):
        continue
      if not isinstance(payload, dict):
        continue
      for key in ("filename", "stored_filename"):
        filename = str(payload.get(key) or "").strip()
        if filename:
          refs.add((filename, _optional_int(payload.get("version"))))
    return refs

  def list_queued_turn_session_ids(self) -> list[str]:
    with self._lock:
      rows = self._connection.execute(
          """
          select distinct session_id
          from web_turns
          where status = 'queued'
          order by session_id asc
          """
      ).fetchall()
    return [str(row["session_id"]) for row in rows]

  def claim_next_queued_turn_for_session(
      self,
      session_id: str,
  ) -> dict[str, Any] | None:
    claimed_at = now_iso()
    with self._lock, self._connection:
      running = self._connection.execute(
          """
          select 1 from web_turns
          where session_id = ? and status in ('running', 'waiting_input')
          limit 1
          """,
          (session_id,),
      ).fetchone()
      if running is not None:
        return None

      row = self._connection.execute(
          """
          select * from web_turns
          where session_id = ? and status = 'queued'
          order by
            case trigger_kind when 'task_notification' then 0 else 1 end,
            created_at asc,
            rowid asc
          limit 1
          """,
          (session_id,),
      ).fetchone()
      if row is None:
        return None

      self._connection.execute(
          """
          update web_turns
          set status = 'running',
              started_at = coalesce(started_at, ?),
              updated_at = ?
          where id = ? and status = 'queued'
          """,
          (claimed_at, claimed_at, row["id"]),
      )
    return self.get_turn(str(row["id"]))

  def cancel_stale_active_turns(self) -> int:
    finished_at = now_iso()
    with self._lock, self._connection:
      cursor = self._connection.execute(
          """
          update web_turns
          set status = 'cancelled',
              updated_at = ?,
              finished_at = ?,
              error_type = 'ServerRestarted',
              error_message = 'Turn was active when the Web API started.'
          where status = 'running'
          """,
          (finished_at, finished_at),
      )
      return cursor.rowcount

  def update_turn(self, turn_id: str, **fields: Any) -> dict[str, Any]:
    fields = {key: value for key, value in fields.items() if value is not None}
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    payload = {"id": turn_id, **fields}
    with self._lock, self._connection:
      self._connection.execute(
          f"update web_turns set {assignments} where id = :id",
          payload,
      )
    turn = self.get_turn(turn_id)
    if turn is None:
      raise KeyError(f"Turn not found: {turn_id}")
    return turn

  def add_turn_token_usage(
      self,
      turn_id: str,
      *,
      input_token_count: int,
      output_token_count: int,
      total_token_count: int = 0,
  ) -> dict[str, Any]:
    input_val = max(0, int(input_token_count))
    output_val = max(0, int(output_token_count))
    total_val = max(0, int(total_token_count)) or input_val + output_val
    if input_val == 0 and output_val == 0 and total_val == 0:
      turn = self.get_turn(turn_id)
      if turn is None:
        raise KeyError(f"Turn not found: {turn_id}")
      return turn

    updated_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_turns
          set input_token_count = case
                when ? > 0 then ?
                else coalesce(input_token_count, 0)
              end,
              output_token_count = coalesce(output_token_count, 0) + ?,
              total_token_count = coalesce(total_token_count, 0) + ?,
              updated_at = ?
          where id = ?
          """,
          (input_val, input_val, output_val, total_val, updated_at, turn_id),
      )
    turn = self.get_turn(turn_id)
    if turn is None:
      raise KeyError(f"Turn not found: {turn_id}")
    return turn

  def add_turn_activity_stats(
      self,
      turn_id: str,
      *,
      tool_call_count: int = 0,
      tool_success_count: int = 0,
      tool_fail_count: int = 0,
      tool_duration_ms: int = 0,
      file_lines_added: int = 0,
      file_lines_removed: int = 0,
  ) -> dict[str, Any]:
    """Accumulate tool-call and file-change counters onto a turn.

    Mirrors add_turn_token_usage: every counter is added to its column so the
    web turn carries running totals across the events ingested for it. Callers
    invoke this only when a step is newly inserted, keeping overlapping ingests
    idempotent.
    """
    calls = max(0, int(tool_call_count))
    success = max(0, int(tool_success_count))
    fail = max(0, int(tool_fail_count))
    duration = max(0, int(tool_duration_ms))
    added = max(0, int(file_lines_added))
    removed = max(0, int(file_lines_removed))
    if not (calls or success or fail or duration or added or removed):
      turn = self.get_turn(turn_id)
      if turn is None:
        raise KeyError(f"Turn not found: {turn_id}")
      return turn

    updated_at = now_iso()
    with self._lock, self._connection:
      self._connection.execute(
          """
          update web_turns
          set tool_call_count = coalesce(tool_call_count, 0) + ?,
              tool_success_count = coalesce(tool_success_count, 0) + ?,
              tool_fail_count = coalesce(tool_fail_count, 0) + ?,
              tool_duration_ms = coalesce(tool_duration_ms, 0) + ?,
              file_lines_added = coalesce(file_lines_added, 0) + ?,
              file_lines_removed = coalesce(file_lines_removed, 0) + ?,
              updated_at = ?
          where id = ?
          """,
          (calls, success, fail, duration, added, removed, updated_at, turn_id),
      )
    turn = self.get_turn(turn_id)
    if turn is None:
      raise KeyError(f"Turn not found: {turn_id}")
    return turn

  def append_turn_event(
      self,
      *,
      turn_id: str,
      id: str | None = None,
      kind: str,
      summary: str,
      payload: dict[str, Any],
  ) -> dict[str, Any]:
    created_at = now_iso()
    step_id = id or f"step_{uuid.uuid4().hex[:12]}"
    with self._lock, self._connection:
      session_id = self._session_id_for_turn(turn_id)
      if session_id is None:
        raise KeyError(f"Turn not found: {turn_id}")
      row = self._connection.execute(
          "select coalesce(max(seq), 0) + 1 as next_seq "
          "from web_steps where turn_id = ?",
          (turn_id,),
      ).fetchone()
      seq = int(row["next_seq"])
      session_row = self._connection.execute(
          "select coalesce(max(session_seq), 0) + 1 as next_session_seq "
          "from web_steps e join web_turns i on i.id = e.turn_id "
          "where i.session_id = ?",
          (session_id,),
      ).fetchone()
      session_seq = int(session_row["next_session_seq"])
      self._insert_step_row(
          {
              "turn_id": turn_id,
              "seq": seq,
              "session_seq": session_seq,
              "id": step_id,
              "kind": kind,
              "summary": summary,
              "payload_json": json.dumps(payload, ensure_ascii=True),
              "created_at": created_at,
          }
      )
    return {
        "id": step_id,
        "turn_id": turn_id,
        "seq": seq,
        "session_seq": session_seq,
        "kind": kind,
        "summary": summary,
        "payload": payload,
        "created_at": created_at,
    }

  def ingest_step(
      self,
      *,
      turn_id: str,
      id: str,
      kind: str,
      summary: str,
      payload: dict[str, Any],
      created_at: str | None = None,
  ) -> dict[str, Any] | None:
    """Materialize one projected runtime event into web_steps.

    Returns None when the step id already exists, so re-ingesting the same
    event slice (cursor rewind, concurrent polls) stays idempotent and callers
    can skip non-idempotent side effects like token-usage accumulation.
    """
    with self._lock, self._connection:
      existing = self._connection.execute(
          "select 1 from web_steps where id = ?",
          (id,),
      ).fetchone()
      if existing is not None:
        return None
      session_id = self._session_id_for_turn(turn_id)
      if session_id is None:
        raise KeyError(f"Turn not found: {turn_id}")
      row = self._connection.execute(
          "select coalesce(max(seq), 0) + 1 as next_seq "
          "from web_steps where turn_id = ?",
          (turn_id,),
      ).fetchone()
      seq = int(row["next_seq"])
      session_row = self._connection.execute(
          "select coalesce(max(session_seq), 0) + 1 as next_session_seq "
          "from web_steps e join web_turns i on i.id = e.turn_id "
          "where i.session_id = ?",
          (session_id,),
      ).fetchone()
      session_seq = int(session_row["next_session_seq"])
      resolved_created_at = created_at or now_iso()
      self._insert_step_row(
          {
              "turn_id": turn_id,
              "seq": seq,
              "session_seq": session_seq,
              "id": id,
              "kind": kind,
              "summary": summary,
              "payload_json": json.dumps(payload, ensure_ascii=True),
              "created_at": resolved_created_at,
          }
      )
    return {
        "id": id,
        "turn_id": turn_id,
        "seq": seq,
        "session_seq": session_seq,
        "kind": kind,
        "summary": summary,
        "payload": payload,
        "created_at": resolved_created_at,
    }

  def delete_turn_steps_of_kind(self, *, turn_id: str, kind: str) -> int:
    """Drop materialized steps of one kind for a finished turn.

    Streaming deltas only matter while the turn is live (the final agent_text
    carries the full response); pruning them keeps web_steps from accumulating
    one row per flushed chunk.
    """
    with self._lock, self._connection:
      cursor = self._connection.execute(
          "delete from web_steps where turn_id = ? and kind = ?",
          (turn_id, kind),
      )
      return cursor.rowcount

  def has_step(self, step_id: str) -> bool:
    with self._lock:
      row = self._connection.execute(
          "select 1 from web_steps where id = ?",
          (step_id,),
      ).fetchone()
    return row is not None

  def turn_has_step_kind(self, turn_id: str, kind: str) -> bool:
    """Whether the turn already materialized a step of this kind.

    Used to keep a single canonical error step per turn: a failure surfaces both
    the runtime's error event and the worker's exception step, and projecting
    both would double-count failures.
    """
    with self._lock:
      row = self._connection.execute(
          "select 1 from web_steps where turn_id = ? and kind = ? limit 1",
          (turn_id, kind),
      ).fetchone()
    return row is not None

  def get_event_cursor(self, *, session_id: str, runtime: str) -> int:
    with self._lock:
      row = self._connection.execute(
          "select byte_offset from web_event_cursors "
          "where session_id = ? and runtime = ?",
          (session_id, runtime),
      ).fetchone()
    return int(row["byte_offset"]) if row else 0

  def advance_event_cursor(
      self,
      *,
      session_id: str,
      runtime: str,
      byte_offset: int,
  ) -> None:
    """Move the ingest cursor forward; concurrent ingests can never rewind it."""
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_event_cursors (session_id, runtime, byte_offset, updated_at)
          values (?, ?, ?, ?)
          on conflict (session_id, runtime) do update set
            byte_offset = max(web_event_cursors.byte_offset, excluded.byte_offset),
            updated_at = excluded.updated_at
          """,
          (session_id, runtime, max(0, int(byte_offset)), now_iso()),
      )

  def reset_event_cursor(self, *, session_id: str, runtime: str) -> None:
    """Rewind to zero after the event log shrank (session truncate/fork rewrite)."""
    with self._lock, self._connection:
      self._connection.execute(
          """
          insert into web_event_cursors (session_id, runtime, byte_offset, updated_at)
          values (?, ?, 0, ?)
          on conflict (session_id, runtime) do update set
            byte_offset = 0,
            updated_at = excluded.updated_at
          """,
          (session_id, runtime, now_iso()),
      )

  def list_turn_events(
      self,
      *,
      turn_id: str,
      after_seq: int = 0,
  ) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select * from web_steps
          where turn_id = ? and seq > ?
          order by seq asc
          """,
          (turn_id, max(0, after_seq)),
      ).fetchall()
    return [self._event_row_to_turn_event(row) for row in rows]

  def list_turn_events_for_session(
      self,
      *,
      session_id: str,
      after_seq: int = 0,
  ) -> list[dict[str, Any]]:
    with self._lock:
      rows = self._connection.execute(
          """
          select e.*
          from web_steps e
          join web_turns i on i.id = e.turn_id
          where i.session_id = ? and coalesce(e.session_seq, e.seq) > ?
          order by coalesce(e.session_seq, e.seq) asc, i.created_at asc, i.rowid asc, e.seq asc
          """,
          (session_id, max(0, after_seq)),
      ).fetchall()
    return [self._event_row_to_turn_event(row) for row in rows]

  def get_session(self, session_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
    return self.get_session_meta(session_id, include_deleted=include_deleted)

  def list_sessions(self, **kwargs) -> list[dict[str, Any]]:
    return self.list_session_metas(**kwargs)

  def append_step(
      self,
      *,
      turn_id: str,
      id: str | None = None,
      kind: str,
      summary: str,
      payload: dict[str, Any],
  ) -> dict[str, Any]:
    """Framework-agnostic alias for a runtime step inside a turn."""
    return self.append_turn_event(
        turn_id=turn_id,
        id=id,
        kind=kind,
        summary=summary,
        payload=payload,
    )

  def list_steps_for_turn(
      self,
      *,
      turn_id: str,
      after_seq: int = 0,
  ) -> list[dict[str, Any]]:
    return self.list_turn_events(turn_id=turn_id, after_seq=after_seq)

  def list_steps_for_session(
      self,
      *,
      session_id: str,
      after_seq: int = 0,
  ) -> list[dict[str, Any]]:
    return self.list_turn_events_for_session(
        session_id=session_id,
        after_seq=after_seq,
    )

  def waiting_seconds_for_turns(
      self,
      turn_ids: list[str],
  ) -> dict[str, float]:
    """Total seconds each turn spent paused on `waiting_input`.

    A turn that asks the user one or more clarifying questions keeps its
    original ``started_at`` across resumes, so wall-clock elapsed includes all
    the time the user spent thinking. Each ``user_input_requested`` step pairs
    with the following ``user_input_submitted`` step to bound one paused span;
    summing those spans lets callers subtract idle time from elapsed and report
    actual working time. A trailing unmatched request (the turn is still
    waiting) contributes nothing.
    """
    ids = [tid for tid in dict.fromkeys(turn_ids) if tid]
    if not ids:
      return {}
    placeholders = ", ".join("?" for _ in ids)
    with self._lock:
      rows = self._connection.execute(
          f"""
          select turn_id, kind, created_at
          from web_steps
          where turn_id in ({placeholders})
            and kind in ('user_input_requested', 'user_input_submitted')
          order by turn_id asc, seq asc
          """,
          ids,
      ).fetchall()
    waiting: dict[str, float] = {tid: 0.0 for tid in ids}
    pending: dict[str, float | None] = {}
    for row in rows:
      turn_id = str(row["turn_id"])
      moment = _parse_iso_seconds(row["created_at"])
      if moment is None:
        continue
      if row["kind"] == "user_input_requested":
        pending[turn_id] = moment
      elif row["kind"] == "user_input_submitted":
        started = pending.pop(turn_id, None)
        if started is not None and moment > started:
          waiting[turn_id] += moment - started
    return waiting

  def _event_row_to_turn_event(self, row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    if result.get("session_seq") is None:
      result["session_seq"] = result.get("seq")
    result["turn_id"] = result.get("turn_id")
    return result


def _parse_iso_seconds(value: Any) -> float | None:
  """Parse an ISO-8601 timestamp (e.g. ``2026-06-10T12:34:56Z``) to epoch seconds."""
  if not value:
    return None
  text = str(value).strip()
  if text.endswith("Z"):
    text = f"{text[:-1]}+00:00"
  try:
    return datetime.fromisoformat(text).timestamp()
  except ValueError:
    return None


def _json_dict(value: Any) -> dict[str, Any]:
  if isinstance(value, dict):
    return value
  if value is None:
    return {}
  try:
    loaded = json.loads(str(value))
  except json.JSONDecodeError:
    return {}
  return loaded if isinstance(loaded, dict) else {}


def _optional_int(value: Any) -> int | None:
  try:
    return None if value is None else int(value)
  except (TypeError, ValueError):
    return None


def _fork_turn_prefix(
    turns: list[dict[str, Any]],
    source_turn_id: str | None,
    *,
    include_source_turn: bool = True,
) -> list[dict[str, Any]]:
  if source_turn_id:
    for index, turn in enumerate(turns):
      if str(turn.get("id")) == source_turn_id:
        return turns[:index + 1] if include_source_turn else turns[:index]
    raise KeyError(f"Turn not found: {source_turn_id}")

  selected: list[dict[str, Any]] = []
  for turn in turns:
    if _is_live_turn_status(turn.get("status")):
      break
    selected.append(turn)
  return selected


def _is_live_turn_status(status: Any) -> bool:
  # waiting_input turns hold a paused agent run (pending request_user_input),
  # so they block forks and rewrites like running turns do.
  return str(status or "") in {"queued", "running", "waiting_input"}


def _fork_session_title(source_title: Any) -> str:
  # Forked sessions are flagged visually by the fork icon in the sidebar
  # (forked_from_session_id), so the title is copied verbatim — no "Fork: " prefix.
  return (str(source_title or "").strip() or "New session")[:200]


def _optional_str(value: Any) -> str | None:
  return None if value is None else str(value)
