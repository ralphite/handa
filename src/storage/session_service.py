from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import secrets
import shutil
import time
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from ..progress import PROGRESS_STATE_KEY
from ..run_events import serialize_event
from .paths import resolve_storage_root
from .paths import session_dir
from .paths import sessions_dir
from .paths import migrate_legacy_session_storage
from .file_io import atomic_write_text
from .file_io import file_lock
from .runtime_event_store import RuntimeEventStore


SESSION_ID_RANDOM_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
SESSION_ID_RANDOM_LENGTH = 6
ORCA_HISTORY_STATE_KEY = "handa:orca_history"
ORCA_PENDING_ROUNDS_STATE_KEY = "handa:orca_pending_rounds"
BROWSER_HISTORY_STATE_KEY = "handa:browser_history"
BROWSER_PENDING_ROUNDS_STATE_KEY = "handa:browser_pending_rounds"
NATIVE_CONFIG_HISTORY_STATE_KEY = "handa:native_config_history"
NATIVE_CONFIG_PENDING_ROUNDS_STATE_KEY = "handa:native_config_pending_rounds"
PENDING_USER_INPUT_STATE_KEY = "handa:pending_user_input"
ORCA_HISTORY_BOUNDARY_EVENT_KIND = "orca.history_boundary"
BROWSER_HISTORY_BOUNDARY_EVENT_KIND = "browser.history_boundary"
NATIVE_CONFIG_HISTORY_BOUNDARY_EVENT_KIND = "native_config.history_boundary"
NATIVE_HISTORY_SPECS = (
    (
        ORCA_HISTORY_BOUNDARY_EVENT_KIND,
        ORCA_HISTORY_STATE_KEY,
        ORCA_PENDING_ROUNDS_STATE_KEY,
    ),
    (
        BROWSER_HISTORY_BOUNDARY_EVENT_KIND,
        BROWSER_HISTORY_STATE_KEY,
        BROWSER_PENDING_ROUNDS_STATE_KEY,
    ),
    (
        NATIVE_CONFIG_HISTORY_BOUNDARY_EVENT_KIND,
        NATIVE_CONFIG_HISTORY_STATE_KEY,
        NATIVE_CONFIG_PENDING_ROUNDS_STATE_KEY,
    ),
)


class Session(BaseModel):
  model_config = ConfigDict(populate_by_name=True, extra="ignore")

  id: str
  app_name: str = Field(alias="appName")
  user_id: str = Field(alias="userId")
  state: dict[str, Any] = Field(default_factory=dict)
  events: list[Any] = Field(default_factory=list)
  last_update_time: float = Field(default_factory=time.time, alias="lastUpdateTime")


class GetSessionConfig(BaseModel):
  num_recent_events: int | None = None
  after_timestamp: float | None = None


class ListSessionsResponse(BaseModel):
  sessions: list[Session]


def _random_session_segment() -> str:
  return "".join(
      secrets.choice(SESSION_ID_RANDOM_ALPHABET)
      for _ in range(SESSION_ID_RANDOM_LENGTH)
  )


def create_session_id() -> str:
  timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
  return f"{timestamp}-{_random_session_segment()}"


def create_child_session_id(parent_session_id: str) -> str:
  parent_session_id = parent_session_id.strip()
  return f"{parent_session_id}-{_random_session_segment()}"


class HandaSessionService:
  """Local JSON-backed SessionService stored under the Handa storage root."""

  def __init__(self, root: str | None = None):
    self.root = str(resolve_storage_root(root))
    migrate_legacy_session_storage(self.root)
    self._runtime_events = RuntimeEventStore(self.root)
    sessions_dir(self.root).mkdir(parents=True, exist_ok=True)

  async def create_session(
      self,
      *,
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]] = None,
      session_id: Optional[str] = None,
  ) -> Session:
    return await asyncio.to_thread(
        self._create_session_sync,
        app_name,
        user_id,
        state,
        session_id,
    )

  def _create_session_sync(
      self,
      app_name: str,
      user_id: str,
      state: Optional[dict[str, Any]],
      session_id: Optional[str],
  ) -> Session:
    if session_id and session_id.strip():
      session_id = session_id.strip()
      path = session_dir(self.root, session_id)
      with file_lock(_session_lock_path(path)):
        if (path / "session.json").exists():
          raise FileExistsError(f"Session with id {session_id} already exists.")
        session = _new_session(app_name, user_id, state, session_id)
        self._write_session_unlocked(session)
    else:
      for _ in range(100):
        session_id = create_session_id()
        path = session_dir(self.root, session_id)
        with file_lock(_session_lock_path(path)):
          if (path / "session.json").exists():
            continue
          session = _new_session(app_name, user_id, state, session_id)
          self._write_session_unlocked(session)
          break
      else:
        raise FileExistsError("Could not create a unique session id.")

    (path / "artifacts").mkdir(parents=True, exist_ok=True)
    return session

  async def get_session(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: str,
      config: Optional[GetSessionConfig] = None,
  ) -> Optional[Session]:
    session = await asyncio.to_thread(self._read_session, session_id)
    if session is None:
      return None
    if session.app_name != app_name or session.user_id != user_id:
      return None
    return self._filter_session_events(session, config)

  async def list_sessions(
      self,
      *,
      app_name: str,
      user_id: Optional[str] = None,
  ) -> ListSessionsResponse:
    sessions = await asyncio.to_thread(self._list_sessions_sync, app_name, user_id)
    return ListSessionsResponse(sessions=sessions)

  def _list_sessions_sync(
      self,
      app_name: str,
      user_id: Optional[str],
  ) -> list[Session]:
    result = []
    for path in sorted(sessions_dir(self.root).glob("*/session.json")):
      session = self._read_session_metadata(path.parent.name)
      if session is None or session.app_name != app_name:
        continue
      if user_id is not None and session.user_id != user_id:
        continue
      session.events = []
      result.append(session)
    return result

  async def delete_session(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: str,
  ) -> None:
    session = await self.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
      return
    await asyncio.to_thread(shutil.rmtree, session_dir(self.root, session_id))

  async def fork_session(
      self,
      *,
      app_name: str,
      user_id: str,
      source_session_id: str,
      target_session_id: str,
      state_updates: dict[str, Any] | None = None,
      source_turn_ids: set[str] | None = None,
      turn_id_map: dict[str, str] | None = None,
      artifact_refs: set[tuple[str, int | None]] | None = None,
  ) -> Session | None:
    return await asyncio.to_thread(
        self._fork_session_sync,
        app_name,
        user_id,
        source_session_id,
        target_session_id,
        state_updates,
        source_turn_ids,
        turn_id_map,
        artifact_refs,
    )

  def _fork_session_sync(
      self,
      app_name: str,
      user_id: str,
      source_session_id: str,
      target_session_id: str,
      state_updates: dict[str, Any] | None,
      source_turn_ids: set[str] | None,
      turn_id_map: dict[str, str] | None,
      artifact_refs: set[tuple[str, int | None]] | None,
  ) -> Session | None:
    source = self._read_session_metadata(source_session_id)
    if source is None or source.app_name != app_name or source.user_id != user_id:
      return None

    path = session_dir(self.root, target_session_id)
    with file_lock(_session_lock_path(path)):
      if (path / "session.json").exists():
        raise FileExistsError(f"Session with id {target_session_id} already exists.")
      state = dict(source.state or {})
      for key in (
          "handa:active_turn_id",
          "handa:parent_session_id",
          "handa:parent_thread_id",
          "handa:parent_task_id",
          "handa:automated_task_id",
          "handa:automated_task_run_id",
          "handa:trigger_kind",
      ):
        state.pop(key, None)
      if source_turn_ids is not None:
        state = self._state_after_truncate(state, source_turn_ids)
        state = self._state_after_native_truncate(
            state,
            _filtered_runtime_events(
                self._runtime_events,
                source_session_id=source_session_id,
                runtime="native",
                source_turn_ids=source_turn_ids,
            ),
        )
      state.update(state_updates or {})
      target = _new_session(app_name, user_id, state, target_session_id)
      self._write_session_unlocked(target)

    self._copy_runtime_events(
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        source_turn_ids=source_turn_ids,
        turn_id_map=turn_id_map or {},
    )
    self._copy_artifacts(source_session_id, target_session_id, artifact_refs=artifact_refs)
    return target

  async def truncate_session(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: str,
      kept_turn_ids: set[str],
      artifact_refs: set[tuple[str, int | None]],
  ) -> Session | None:
    return await asyncio.to_thread(
        self._truncate_session_sync,
        app_name,
        user_id,
        session_id,
        kept_turn_ids,
        artifact_refs,
    )

  def _truncate_session_sync(
      self,
      app_name: str,
      user_id: str,
      session_id: str,
      kept_turn_ids: set[str],
      artifact_refs: set[tuple[str, int | None]],
  ) -> Session | None:
    session = self._read_session_metadata(session_id)
    if session is None or session.app_name != app_name or session.user_id != user_id:
      return None

    path = session_dir(self.root, session_id)
    with file_lock(_session_lock_path(path)):
      stored = self._read_session_metadata(session_id) or session
      stored.state = self._state_after_truncate(stored.state or {}, kept_turn_ids)
      stored.last_update_time = time.time()
      self._write_session_unlocked(stored)

    for runtime in _runtime_names_for_session(self.root, session_id):
      events = self._runtime_events.list_events(session_id=session_id, runtime=runtime)
      filtered = [
          item
          for item in events
          if _event_turn_id(item) is None or _event_turn_id(item) in kept_turn_ids
      ]
      self._runtime_events.replace_events(
          session_id=session_id,
          runtime=runtime,
          events=filtered,
      )
      if runtime == "native":
        self._truncate_native_history(session_id, kept_events=filtered)
    self._prune_artifacts(session_id, artifact_refs)
    return stored

  def _truncate_native_history(
      self,
      session_id: str,
      *,
      kept_events: list[dict[str, Any]],
  ) -> None:
    """Roll framework-free agent histories back to the last kept turn."""
    path = session_dir(self.root, session_id)
    with file_lock(_session_lock_path(path)):
      stored = self._read_session_metadata(session_id)
      if stored is None:
        return
      stored.state = self._state_after_native_truncate(stored.state or {}, kept_events)
      stored.last_update_time = time.time()
      self._write_session_unlocked(stored)

  async def append_event(self, session: Session, event: Any) -> Any:
    return await asyncio.to_thread(self._append_event_sync, session, event)

  def _append_event_sync(self, session: Session, event: Any) -> Any:
    if bool(_event_get(event, "partial")):
      return event

    path = session_dir(self.root, session.id)
    with file_lock(_session_lock_path(path)):
      stored = self._read_session_metadata(session.id) or session
      if stored is not session:
        stored.state = {**(stored.state or {}), **(session.state or {})}
      stored.last_update_time = time.time()
      self._write_session_unlocked(stored)
      raw_event = serialize_event(event)
      self._runtime_events.append(
          session_id=session.id,
          turn_id=_state_str(stored, "handa:active_turn_id"),
          runtime="native",
          event_id=_optional_str(raw_event.get("id")),
          created_at=_optional_str(raw_event.get("timestamp")),
          event=raw_event,
      )

    session.state = stored.state
    session.events = [*(session.events or []), event]
    session.last_update_time = stored.last_update_time
    return event

  def read_state_sync(self, session_id: str) -> dict[str, Any]:
    """Return a copy of the session state, or empty dict if it does not exist."""
    session = self._read_session_metadata(session_id)
    return dict(session.state or {}) if session else {}

  def merge_state_sync(
      self,
      session_id: str,
      updates: dict[str, Any],
  ) -> dict[str, Any]:
    """Merge `updates` into the session state under the session lock.

    Returns the resulting state, or empty dict if the session does not exist.
    Used by native runners that persist scratch state such as notes and
    last-seen task event timestamps.
    """
    path = session_dir(self.root, session_id)
    with file_lock(_session_lock_path(path)):
      session = self._read_session_metadata(session_id)
      if session is None:
        return {}
      session.state = {**(session.state or {}), **updates}
      session.last_update_time = time.time()
      self._write_session_unlocked(session)
      return dict(session.state)

  def _read_session(self, session_id: str) -> Optional[Session]:
    session = self._read_session_metadata(session_id)
    if session is None:
      return None
    if session.events:
      self._migrate_embedded_events(session)
    session.events = []
    return session

  def _read_session_metadata(self, session_id: str) -> Optional[Session]:
    path = session_dir(self.root, session_id) / "session.json"
    if not path.exists():
      return None
    return Session.model_validate_json(path.read_text(encoding="utf-8"))

  def _write_session(self, session: Session) -> None:
    path = session_dir(self.root, session.id)
    with file_lock(_session_lock_path(path)):
      self._write_session_unlocked(session)

  def _write_session_unlocked(self, session: Session) -> None:
    path = session_dir(self.root, session.id)
    path.mkdir(parents=True, exist_ok=True)
    stored = session.model_copy(deep=True)
    stored.events = []
    atomic_write_text(
        path / "session.json",
        stored.model_dump_json(by_alias=True, indent=2) + "\n",
    )

  def _migrate_embedded_events(self, session: Session) -> None:
    path = session_dir(self.root, session.id)
    with file_lock(_session_lock_path(path)):
      stored = self._read_session_metadata(session.id)
      if stored is None or not stored.events:
        return
      existing = self._runtime_events.identity_index(
          session_id=session.id,
          runtime="native",
      )
      for event in stored.events:
        raw_event = serialize_event(event)
        key = _optional_str(raw_event.get("id"))
        if key and key in existing:
          continue
        envelope = self._runtime_events.append(
            session_id=session.id,
            turn_id=_state_str(stored, "handa:active_turn_id"),
            runtime="native",
            event_id=_optional_str(raw_event.get("id")),
            created_at=_optional_str(raw_event.get("timestamp")),
            event=raw_event,
        )
        if key:
          existing[key] = envelope
      stored.events = []
      self._write_session_unlocked(stored)

  def _copy_runtime_events(
      self,
      *,
      source_session_id: str,
      target_session_id: str,
      source_turn_ids: set[str] | None,
      turn_id_map: dict[str, str],
  ) -> None:
    for runtime in _runtime_names_for_session(self.root, source_session_id):
      for item in self._runtime_events.list_events(
          session_id=source_session_id,
          runtime=runtime,
      ):
        source_turn_id = _optional_str(item.get("turn_id"))
        if (
            source_turn_id
            and source_turn_ids is not None
            and source_turn_id not in source_turn_ids
        ):
          continue
        raw_event = item.get("event")
        if not isinstance(raw_event, dict):
          continue
        self._runtime_events.append(
            session_id=target_session_id,
            runtime=runtime,
            turn_id=turn_id_map.get(source_turn_id or ""),
            event=dict(raw_event),
            event_id=_optional_str(item.get("id") or raw_event.get("id")),
            created_at=_optional_str(item.get("created_at")),
        )
  def _copy_artifacts(
      self,
      source_session_id: str,
      target_session_id: str,
      *,
      artifact_refs: set[tuple[str, int | None]] | None = None,
  ) -> None:
    source = session_dir(self.root, source_session_id) / "artifacts"
    target = session_dir(self.root, target_session_id) / "artifacts"
    if not source.is_dir():
      target.mkdir(parents=True, exist_ok=True)
      return
    if artifact_refs is None:
      shutil.copytree(source, target, dirs_exist_ok=True)
      return
    target.mkdir(parents=True, exist_ok=True)
    if not artifact_refs:
      return
    for path in source.iterdir():
      if not path.is_file() or path.name.startswith(".") or path.name.endswith(".metadata.json"):
        continue
      if not _artifact_matches_refs(path, artifact_refs):
        continue
      shutil.copy2(path, target / path.name)
      metadata_path = path.parent / f"{path.name}.metadata.json"
      if metadata_path.is_file():
        shutil.copy2(metadata_path, target / metadata_path.name)

  def _prune_artifacts(
      self,
      session_id: str,
      artifact_refs: set[tuple[str, int | None]],
  ) -> None:
    directory = session_dir(self.root, session_id) / "artifacts"
    if not directory.is_dir():
      directory.mkdir(parents=True, exist_ok=True)
      return
    kept: set[str] = set()
    for path in directory.iterdir():
      if not path.is_file() or path.name.startswith(".") or path.name.endswith(".metadata.json"):
        continue
      if _artifact_matches_refs(path, artifact_refs):
        kept.add(path.name)
        continue
      path.unlink(missing_ok=True)
      (directory / f"{path.name}.metadata.json").unlink(missing_ok=True)
    for metadata_path in directory.glob("*.metadata.json"):
      payload_name = metadata_path.name[: -len(".metadata.json")]
      if payload_name not in kept and not (directory / payload_name).is_file():
        metadata_path.unlink(missing_ok=True)

  @staticmethod
  def _state_after_truncate(
      state: dict[str, Any],
      kept_turn_ids: set[str],
  ) -> dict[str, Any]:
    next_state = dict(state)
    for key in (
        "handa:active_turn_id",
        "handa:parent_session_id",
        "handa:parent_thread_id",
        "handa:parent_task_id",
    ):
      next_state.pop(key, None)
    progress = next_state.get(PROGRESS_STATE_KEY)
    if isinstance(progress, list):
      next_state[PROGRESS_STATE_KEY] = [
          item
          for item in progress
          if not isinstance(item, dict)
          or not item.get("source_turn_id")
          or str(item.get("source_turn_id")) in kept_turn_ids
      ]
    return next_state

  @staticmethod
  def _state_after_native_truncate(
      state: dict[str, Any],
      kept_events: list[dict[str, Any]],
  ) -> dict[str, Any]:
    next_state = dict(state)
    for boundary_kind, history_key, pending_rounds_key in NATIVE_HISTORY_SPECS:
      history_length = _last_native_history_length(kept_events, boundary_kind)
      history = next_state.get(history_key)
      if isinstance(history, list) and history_length is not None:
        next_state[history_key] = history[:history_length]
      else:
        next_state.pop(history_key, None)
      next_state.pop(pending_rounds_key, None)
    next_state.pop(PENDING_USER_INPUT_STATE_KEY, None)
    return next_state

  def _filter_session_events(
      self,
      session: Session,
      config: Optional[GetSessionConfig],
  ) -> Session:
    if config is None:
      return session
    session = session.model_copy(deep=True)
    if config.num_recent_events is not None:
      if config.num_recent_events == 0:
        session.events = []
      else:
        session.events = session.events[-config.num_recent_events :]
    if config.after_timestamp is not None:
      session.events = [
          event
          for event in session.events
          if event.timestamp >= config.after_timestamp
      ]
    return session


def _new_session(
    app_name: str,
    user_id: str,
    state: Optional[dict[str, Any]],
    session_id: str,
) -> Session:
  return Session(
      id=session_id,
      app_name=app_name,
      user_id=user_id,
      state=state or {},
      events=[],
      last_update_time=time.time(),
  )


def _session_lock_path(path: str | Path) -> Path:
  return Path(path) / ".session.json.lock"


def _event_turn_id(item: dict[str, Any]) -> str | None:
  value = item.get("turn_id")
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _last_native_history_length(
    events: list[dict[str, Any]],
    boundary_kind: str,
) -> int | None:
  for item in reversed(events):
    raw_event = item.get("event")
    if not isinstance(raw_event, dict):
      continue
    if str(raw_event.get("kind") or "") != boundary_kind:
      continue
    payload = raw_event.get("payload")
    value = payload.get("history_length") if isinstance(payload, dict) else None
    try:
      return max(0, int(value))
    except (TypeError, ValueError):
      return None
  return None


def _filtered_runtime_events(
    store: RuntimeEventStore,
    *,
    source_session_id: str,
    runtime: str,
    source_turn_ids: set[str],
) -> list[dict[str, Any]]:
  return [
      item
      for item in store.list_events(session_id=source_session_id, runtime=runtime)
      if _event_turn_id(item) is None or _event_turn_id(item) in source_turn_ids
  ]


def _artifact_matches_refs(
    path: Path,
    refs: set[tuple[str, int | None]],
) -> bool:
  if not refs:
    return False
  metadata = _artifact_metadata(path)
  names = {
      path.name,
      str(metadata.get("stored_filename") or "").strip(),
      str(metadata.get("source_filename") or "").strip(),
  }
  names.discard("")
  metadata_version = _int_or_none(metadata.get("version"))
  display_version = _int_or_none(metadata.get("display_version"))
  for filename, version in refs:
    if filename not in names:
      continue
    if version is None:
      return True
    if version == metadata_version or version == display_version:
      return True
  return False


def _artifact_metadata(path: Path) -> dict[str, Any]:
  metadata_path = path.parent / f"{path.name}.metadata.json"
  if not metadata_path.is_file():
    return {}
  try:
    loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    return {}
  return loaded if isinstance(loaded, dict) else {}


def _int_or_none(value: Any) -> int | None:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _runtime_names_for_session(root: str | Path, session_id: str) -> list[str]:
  runtime_root = session_dir(root, session_id) / "runtime"
  if not runtime_root.is_dir():
    return ["native"]
  names = [
      path.name
      for path in runtime_root.iterdir()
      if path.is_dir() and (path / "events.jsonl").is_file()
  ]
  return names or ["native"]


def _state_str(session: Session, key: str) -> str | None:
  value = (session.state or {}).get(key)
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _event_get(event: Any, key: str) -> Any:
  if isinstance(event, dict):
    return event.get(key)
  return getattr(event, key, None)


def _optional_str(value: Any) -> str | None:
  return None if value is None else str(value)
