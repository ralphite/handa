from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from .file_io import atomic_write_text
from .paths import resolve_storage_root
from .paths import session_dir
from .paths import sessions_dir


OLD_RUNTIMES = {"adk", "langgraph"}
NATIVE_RUNTIME = "native"
LIVE_TURN_STATUSES = {"queued", "running", "waiting_input"}
LIVE_TASK_STATUSES = {"queued", "running", "waiting"}
MIGRATION_NAME = "native-only-20260616"


@dataclass(frozen=True)
class NativeOnlyMigrationResult:
  changed: bool
  backup_dir: Path | None
  sqlite_rows_changed: int
  session_files_changed: int
  task_files_changed: int
  runtime_dirs_moved: int
  checkpoints_removed: bool


def run_native_only_storage_migration(root: Path | str | None = None) -> NativeOnlyMigrationResult:
  storage_root = resolve_storage_root(root)
  storage_root.mkdir(parents=True, exist_ok=True)

  backup_dir: Path | None = None
  sqlite_rows_changed = 0
  session_files_changed = 0
  task_files_changed = 0
  runtime_dirs_moved = 0
  checkpoints_removed = False

  if not _has_legacy_state(storage_root):
    return NativeOnlyMigrationResult(False, None, 0, 0, 0, 0, False)

  backup_dir = _migration_backup_dir(storage_root)
  backup_dir.mkdir(parents=True, exist_ok=True)

  sqlite_rows_changed = _migrate_sqlite(storage_root, backup_dir)
  runtime_dirs_moved = _migrate_runtime_event_dirs(storage_root, backup_dir)
  session_files_changed = _migrate_session_json_files(storage_root, backup_dir)
  task_files_changed = _migrate_task_json_files(storage_root, backup_dir)
  checkpoints_removed = _remove_langgraph_checkpoints(storage_root, backup_dir)

  result = NativeOnlyMigrationResult(
      changed=True,
      backup_dir=backup_dir,
      sqlite_rows_changed=sqlite_rows_changed,
      session_files_changed=session_files_changed,
      task_files_changed=task_files_changed,
      runtime_dirs_moved=runtime_dirs_moved,
      checkpoints_removed=checkpoints_removed,
  )
  _write_marker(storage_root, result)
  return result


def _has_legacy_state(root: Path) -> bool:
  if (root / "langgraph").exists():
    return True
  for runtime in OLD_RUNTIMES:
    if any((path / "events.jsonl").is_file() for path in sessions_dir(root).glob(f"*/runtime/{runtime}")):
      return True
  db_path = root / "handa.sqlite3"
  if db_path.is_file() and _sqlite_has_legacy_state(db_path):
    return True
  for path in sessions_dir(root).glob("*/session.json"):
    if _json_file_has_legacy_state(path):
      return True
  for path in sessions_dir(root).glob("*/tasks/*/task.json"):
    if _json_file_has_legacy_state(path):
      return True
  return False


def _sqlite_has_legacy_state(db_path: Path) -> bool:
  try:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
      if _table_exists(connection, "web_sessions"):
        row = connection.execute(
            """
            select 1 from web_sessions
            where agent_runtime != 'native'
               or agent_id in ('main', 'orca_adk', 'orca_langgraph')
            limit 1
            """
        ).fetchone()
        if row:
          return True
      if _table_exists(connection, "web_automated_tasks"):
        row = connection.execute(
            """
            select 1 from web_automated_tasks
            where agent_id in ('main', 'orca_adk', 'orca_langgraph')
            limit 1
            """
        ).fetchone()
        if row:
          return True
    finally:
      connection.close()
  except sqlite3.Error:
    return False
  return False


def _json_file_has_legacy_state(path: Path) -> bool:
  data = _read_json(path)
  if not isinstance(data, dict):
    return False
  state = data.get("state") if path.name == "session.json" else data
  if not isinstance(state, dict):
    return False
  return _dict_has_legacy_runtime(state)


def _dict_has_legacy_runtime(data: dict[str, Any]) -> bool:
  runtime = str(data.get("agent_runtime") or data.get("handa:agent_runtime") or "")
  if runtime in OLD_RUNTIMES:
    return True
  has_agent_identity = any(
      str(data.get(key) or "").strip()
      for key in (
          "agent_id",
          "config_name",
          "handa:agent_id",
          "handa:target_agent_id",
          "handa:agent_run_config_name",
          "handa:system_agent_config_name",
      )
  )
  if has_agent_identity and runtime != NATIVE_RUNTIME:
    return True
  for key in ("agent_id", "handa:agent_id", "handa:target_agent_id"):
    if str(data.get(key) or "") in {"main", "orca_adk", "orca_langgraph"}:
      return True
  pending = data.get("handa:pending_user_input")
  return isinstance(pending, dict) and str(pending.get("runtime") or "") in OLD_RUNTIMES


def _migrate_sqlite(root: Path, backup_dir: Path) -> int:
  db_path = root / "handa.sqlite3"
  if not db_path.is_file():
    return 0
  _backup_file(db_path, backup_dir / "handa.sqlite3")
  connection = sqlite3.connect(str(db_path))
  connection.row_factory = sqlite3.Row
  changed = 0
  now = _now_iso()
  try:
    if _table_exists(connection, "web_sessions"):
      old_session_ids = [
          str(row["id"])
          for row in connection.execute(
              """
              select id from web_sessions
              where agent_runtime != 'native'
                 or agent_id in ('main', 'orca_adk', 'orca_langgraph')
              """
          ).fetchall()
      ]
      if old_session_ids and _table_exists(connection, "web_turns"):
        placeholders = ",".join("?" for _ in old_session_ids)
        cursor = connection.execute(
            f"""
            update web_turns
               set status = 'cancelled',
                   updated_at = ?,
                   finished_at = coalesce(finished_at, ?),
                   cancel_requested_at = coalesce(cancel_requested_at, ?),
                   error_type = coalesce(error_type, 'LegacyRuntimeRemoved'),
                   error_message = coalesce(error_message, 'Legacy ADK/LangGraph runtime turns cannot be resumed after the native-only migration.')
             where session_id in ({placeholders})
               and status in ('queued', 'running', 'waiting_input')
            """,
            [now, now, now, *old_session_ids],
        )
        changed += cursor.rowcount if cursor.rowcount > 0 else 0
        connection.execute(
            f"delete from web_event_cursors where session_id in ({placeholders})",
            old_session_ids,
        )
      cursor = connection.execute(
          """
          update web_sessions
             set agent_id = case
                 when agent_id in ('main', 'orca_adk', 'orca_langgraph') then 'orca'
                 else agent_id
               end,
               agent_runtime = 'native'
           where agent_runtime != 'native'
              or agent_id in ('main', 'orca_adk', 'orca_langgraph')
          """
      )
      changed += cursor.rowcount if cursor.rowcount > 0 else 0
    if _table_exists(connection, "web_automated_tasks"):
      cursor = connection.execute(
          """
          update web_automated_tasks
             set agent_id = 'orca'
           where agent_id in ('main', 'orca_adk', 'orca_langgraph')
          """
      )
      changed += cursor.rowcount if cursor.rowcount > 0 else 0
    connection.commit()
  finally:
    connection.close()
  return changed


def _migrate_session_json_files(root: Path, backup_dir: Path) -> int:
  changed = 0
  for path in sessions_dir(root).glob("*/session.json"):
    data = _read_json(path)
    if not isinstance(data, dict):
      continue
    state = data.get("state")
    if not isinstance(state, dict):
      continue
    next_state = dict(state)
    modified = _migrate_state_dict(next_state)
    if not modified:
      continue
    _backup_file(path, backup_dir / "sessions" / path.parent.name / "session.json")
    data["state"] = next_state
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    changed += 1
  return changed


def _migrate_task_json_files(root: Path, backup_dir: Path) -> int:
  changed = 0
  now = _now_iso()
  for path in sessions_dir(root).glob("*/tasks/*/task.json"):
    task = _read_json(path)
    if not isinstance(task, dict):
      continue
    before = json.dumps(task, sort_keys=True, ensure_ascii=True, default=str)
    runtime = str(task.get("agent_runtime") or "")
    if task.get("kind") in {"agent_run", "system_agent_run", "run_agent", "web_turn"} and runtime != NATIVE_RUNTIME:
      task["agent_runtime"] = NATIVE_RUNTIME
    if str(task.get("agent_id") or "") in {"main", "orca_adk", "orca_langgraph"}:
      task["agent_id"] = "orca"
    if runtime in OLD_RUNTIMES and str(task.get("status") or "") in LIVE_TASK_STATUSES:
      task["status"] = "cancelled"
      task["returncode"] = 1
      task["cancel_requested_at"] = task.get("cancel_requested_at") or now
      task["finished_at"] = task.get("finished_at") or now
      task["error_type"] = task.get("error_type") or "LegacyRuntimeRemoved"
      task["error_message"] = task.get("error_message") or (
          "Legacy ADK/LangGraph runtime tasks cannot be resumed after the native-only migration."
      )
    after = json.dumps(task, sort_keys=True, ensure_ascii=True, default=str)
    if before == after:
      continue
    _backup_file(path, backup_dir / "sessions" / path.parents[2].name / "tasks" / path.parent.name / "task.json")
    atomic_write_text(path, json.dumps(task, indent=2, ensure_ascii=True) + "\n")
    changed += 1
  return changed


def _migrate_runtime_event_dirs(root: Path, backup_dir: Path) -> int:
  moved = 0
  for events_path in sessions_dir(root).glob("*/runtime/*/events.jsonl"):
    runtime = events_path.parent.name
    if runtime not in OLD_RUNTIMES:
      continue
    session_id = events_path.parents[2].name
    native_path = session_dir(root, session_id) / "runtime" / NATIVE_RUNTIME / "events.jsonl"
    _backup_tree(events_path.parent, backup_dir / "sessions" / session_id / "runtime" / runtime)
    existing_ids = _event_ids(native_path)
    native_path.parent.mkdir(parents=True, exist_ok=True)
    with native_path.open("a", encoding="utf-8") as target:
      for envelope in _read_jsonl(events_path):
        if not isinstance(envelope, dict):
          continue
        event_id = str(envelope.get("id") or "").strip()
        if event_id and event_id in existing_ids:
          continue
        migrated = _migrated_event_envelope(envelope, session_id=session_id, runtime=runtime)
        target.write(json.dumps(migrated, ensure_ascii=True, default=str) + "\n")
        if event_id:
          existing_ids.add(event_id)
    shutil.rmtree(events_path.parent)
    moved += 1
  return moved


def _remove_langgraph_checkpoints(root: Path, backup_dir: Path) -> bool:
  path = root / "langgraph"
  if not path.exists():
    return False
  _backup_tree(path, backup_dir / "langgraph")
  if path.is_dir():
    shutil.rmtree(path)
  else:
    path.unlink(missing_ok=True)
  return True


def _migrate_state_dict(state: dict[str, Any]) -> bool:
  before = json.dumps(state, sort_keys=True, ensure_ascii=True, default=str)
  if str(state.get("handa:agent_runtime") or "") in OLD_RUNTIMES:
    state["handa:agent_runtime"] = NATIVE_RUNTIME
  if not str(state.get("handa:agent_runtime") or "").strip() and any(
      str(state.get(key) or "").strip()
      for key in (
          "handa:agent_id",
          "handa:target_agent_id",
          "handa:agent_run_config_name",
          "handa:system_agent_config_name",
      )
  ):
    state["handa:agent_runtime"] = NATIVE_RUNTIME
  for key in ("handa:agent_id", "handa:target_agent_id"):
    value = str(state.get(key) or "")
    if value in {"main", "orca_adk", "orca_langgraph"}:
      state[key] = "orca"
  state.pop("handa:agent_definition_id", None)
  state.pop("handa:agent_entrypoint", None)
  pending = state.get("handa:pending_user_input")
  if isinstance(pending, dict) and str(pending.get("runtime") or "") in OLD_RUNTIMES:
    state.pop("handa:pending_user_input", None)
  after = json.dumps(state, sort_keys=True, ensure_ascii=True, default=str)
  return before != after


def _migrated_event_envelope(
    envelope: dict[str, Any],
    *,
    session_id: str,
    runtime: str,
) -> dict[str, Any]:
  migrated = dict(envelope)
  migrated["session_id"] = session_id
  event = migrated.get("event")
  if isinstance(event, dict):
    event = dict(event)
    author = str(event.get("author") or "")
    if author in {"main", "orca_adk", "orca_langgraph"}:
      event["author"] = "orca"
    if runtime == "langgraph":
      kind = str(event.get("kind") or "")
      if kind.startswith("langgraph."):
        event["kind"] = f"orca.{kind.split('.', 1)[1]}"
    migrated["event"] = event
  return migrated


def _event_ids(path: Path) -> set[str]:
  ids: set[str] = set()
  for envelope in _read_jsonl(path):
    if not isinstance(envelope, dict):
      continue
    value = str(envelope.get("id") or "").strip()
    if value:
      ids.add(value)
  return ids


def _read_jsonl(path: Path) -> list[Any]:
  if not path.is_file():
    return []
  rows: list[Any] = []
  with path.open("r", encoding="utf-8") as handle:
    for line in handle:
      line = line.strip()
      if not line:
        continue
      try:
        rows.append(json.loads(line))
      except json.JSONDecodeError:
        continue
  return rows


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
  row = connection.execute(
      "select 1 from sqlite_master where type = 'table' and name = ?",
      (table,),
  ).fetchone()
  return row is not None


def _read_json(path: Path) -> Any:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return None


def _backup_file(source: Path, target: Path) -> None:
  if not source.exists() or target.exists():
    return
  target.parent.mkdir(parents=True, exist_ok=True)
  shutil.copy2(source, target)


def _backup_tree(source: Path, target: Path) -> None:
  if not source.exists() or target.exists():
    return
  target.parent.mkdir(parents=True, exist_ok=True)
  if source.is_dir():
    shutil.copytree(source, target)
  else:
    shutil.copy2(source, target)


def _migration_backup_dir(root: Path) -> Path:
  stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
  return root / "backups" / f"{MIGRATION_NAME}-{stamp}"


def _write_marker(root: Path, result: NativeOnlyMigrationResult) -> None:
  marker = root / "migrations" / f"{MIGRATION_NAME}.json"
  marker.parent.mkdir(parents=True, exist_ok=True)
  payload = {
      "migration": MIGRATION_NAME,
      "ran_at": _now_iso(),
      "backup_dir": str(result.backup_dir) if result.backup_dir else None,
      "sqlite_rows_changed": result.sqlite_rows_changed,
      "session_files_changed": result.session_files_changed,
      "task_files_changed": result.task_files_changed,
      "runtime_dirs_moved": result.runtime_dirs_moved,
      "checkpoints_removed": result.checkpoints_removed,
  }
  atomic_write_text(marker, json.dumps(payload, indent=2, ensure_ascii=True) + "\n")


def _now_iso() -> str:
  return datetime.now(tz=timezone.utc).isoformat()
