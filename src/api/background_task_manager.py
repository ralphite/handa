from __future__ import annotations

import asyncio
from typing import Any
from typing import TYPE_CHECKING

from ..contract.parent_runs import finalize_parent_agent_task
from ..contract.product import validate_model_config_id
from ..contract.services import APP_NAME
from ..contract.task_store import AGENT_RUN_TASK_KINDS
from ..contract.task_store import append_task_event
from ..contract.task_store import create_task_notification
from ..contract.task_store import list_task_events
from ..contract.task_store import list_task_notifications
from ..contract.task_store import list_tasks
from ..contract.task_store import load_task
from ..contract.task_store import now_iso
from ..contract.task_store import read_task_result
from ..contract.task_store import update_task_notification
from ..contract.storage import sessions_dir
from .automated_tasks.dispatcher import dispatch_due_time_triggers
from .automated_tasks.run_sync import sync_automated_task_runs
from .turn_queue import dispatch_queued_turns
from .turn_run_sync import sync_active_turns

if TYPE_CHECKING:
  from ..storage.session_service import Session

  from .context import WebApiContext


TERMINAL_TASK_EVENTS = {"task.completed", "task.failed", "task.cancelled"}
TASK_NOTIFICATION_TRIGGER = "task_notification"


class BackgroundTaskManager:
  """Consumes terminal task events and resumes parent sessions when possible."""

  def __init__(
      self,
      ctx: WebApiContext,
      *,
      poll_interval_sec: float = 1.0,
      start_invocation_run: bool = True,
  ) -> None:
    self.ctx = ctx
    self.poll_interval_sec = poll_interval_sec
    self.start_invocation_run = start_invocation_run
    self._loop_task: asyncio.Task[None] | None = None
    self._stop_event = asyncio.Event()

  def start(self) -> None:
    if self._loop_task is not None and not self._loop_task.done():
      return
    self._stop_event = asyncio.Event()
    self._loop_task = asyncio.create_task(self._run_loop())

  async def stop(self) -> None:
    self._stop_event.set()
    if self._loop_task is None:
      return
    await self._loop_task

  async def _run_loop(self) -> None:
    while not self._stop_event.is_set():
      await self.process_once()
      try:
        await asyncio.wait_for(
            self._stop_event.wait(),
            timeout=self.poll_interval_sec,
        )
      except TimeoutError:
        pass

  async def process_once(self) -> dict[str, int]:
    created = 0
    delivered = 0
    blocked = 0
    for turn in sync_active_turns(self.ctx):
      await self._finalize_waiting_parent_task_if_ready(turn)
    # Mirror automated-task run status off the (now-current) turn states.
    sync_automated_task_runs(self.ctx)
    # Fire scheduled (time-trigger) automated tasks that have come due.
    await dispatch_due_time_triggers(self.ctx)
    for session_id in self._task_session_ids():
      self._ensure_terminal_task_events(session_id)
      created += self._create_notifications_for_session(session_id)
      result = await self._deliver_pending_notifications(session_id)
      delivered += result["delivered"]
      blocked += result["blocked"]
    if self.start_invocation_run:
      dispatch_queued_turns(self.ctx)
    return {"created": created, "delivered": delivered, "blocked": blocked}

  def _task_session_ids(self) -> list[str]:
    root = sessions_dir(self.ctx.settings.storage_root)
    session_ids: list[str] = []
    for path in sorted(root.glob("*/tasks/task_events.jsonl")):
      session_ids.append(path.parent.parent.name)
    return session_ids

  def _ensure_terminal_task_events(self, session_id: str) -> int:
    existing = {
        (str(event.get("task_id") or ""), str(event.get("kind") or ""))
        for event in list_task_events(session_id=session_id, limit=200)
        if event.get("kind") in TERMINAL_TASK_EVENTS
    }
    created = 0
    for task in list_tasks(session_id=session_id):
      task_id = str(task.get("id") or "").strip()
      if str(task.get("kind") or "") == "web_turn":
        continue
      event_kind = _terminal_event_kind(task)
      if not task_id or event_kind is None:
        continue
      if (
          str(task.get("kind") or "") in AGENT_RUN_TASK_KINDS
          and not read_task_result(task_id, session_id=session_id).get("found")
      ):
        continue
      if (task_id, event_kind) in existing:
        continue
      append_task_event(
          event_kind,
          _terminal_event_summary(task, task_id, event_kind),
          session_id=session_id,
          task_id=task_id,
          payload={
              "child_session_id": task.get("child_session_id"),
              "returncode": task.get("returncode"),
          },
      )
      existing.add((task_id, event_kind))
      created += 1
    return created

  def _create_notifications_for_session(self, session_id: str) -> int:
    created = 0
    for event in list_task_events(session_id=session_id, limit=200):
      if event.get("kind") not in TERMINAL_TASK_EVENTS:
        continue
      if self._should_skip_task_notification(session_id, event.get("task_id")):
        continue
      task_id = str(event.get("task_id") or "").strip()
      source_event_id = str(event.get("id") or "").strip()
      if not task_id or not source_event_id:
        continue
      existing = _notification_for_event(session_id, source_event_id)
      notification = create_task_notification(
          session_id=session_id,
          task_id=task_id,
          source_event_id=source_event_id,
          source_event_kind=str(event["kind"]),
          payload=self._notification_payload(session_id, task_id, event),
      )
      if existing is None and notification.get("source_event_id") == source_event_id:
        created += 1
    return created

  def _notification_payload(
      self,
      session_id: str,
      task_id: str,
      event: dict[str, Any],
  ) -> dict[str, Any]:
    task = load_task(task_id, session_id=session_id)
    result = read_task_result(task_id, session_id=session_id)
    payload: dict[str, Any] = {
        "event_kind": event.get("kind"),
        "event_summary": event.get("summary"),
        "task_kind": task.get("kind"),
        "task_status": task.get("status"),
        "task_summary": task.get("summary"),
        "child_session_id": task.get("child_session_id"),
        "returncode": task.get("returncode"),
    }
    if result.get("found"):
      result_payload = result.get("result") or {}
      payload["result_success"] = result_payload.get("success")
      payload["final_text"] = _compact_text(result_payload.get("final_text"))
      payload["summary_artifact"] = result_payload.get("summary_artifact")
    return payload

  async def _deliver_pending_notifications(self, session_id: str) -> dict[str, int]:
    delivered = 0
    blocked = 0
    for notification in list_task_notifications(session_id=session_id):
      if notification.get("status") != "pending":
        continue
      if self._session_has_active_agent_loop(session_id):
        continue
      target = self._delivery_target(session_id)
      if target is None:
        update_task_notification(
            str(notification["id"]),
            session_id=session_id,
            status="blocked",
            blocked_at=now_iso(),
            error="Could not resolve project or agent for task notification delivery.",
        )
        blocked += 1
        continue
      turn = self.ctx.db.create_turn(
          session_id=session_id,
          model_config_id=target.get("model_config_id"),
          title="Task notification",
          input_text=_render_notification_message(notification),
          trigger_kind=TASK_NOTIFICATION_TRIGGER,
      )
      update_task_notification(
          str(notification["id"]),
          session_id=session_id,
          status="delivered",
          delivered_at=now_iso(),
          delivered_turn_id=turn["id"],
      )
      delivered += 1
    return {"delivered": delivered, "blocked": blocked}

  def _delivery_target(self, session_id: str) -> dict[str, str | None] | None:
    session = self.ctx.services.session_service._read_session(session_id)
    if session is None:
      return None
    root = self._root_session(session)
    root_meta = self.ctx.db.get_session_meta(root.id)
    project_id = _session_project_id(session) or _session_project_id(root)
    if not project_id:
      project_id = str(root_meta["project_id"]) if root_meta and root_meta.get("project_id") else None
    if not project_id or self.ctx.db.get_project(project_id) is None:
      return None
    return {
        "project_id": project_id,
        "model_config_id": self._model_config_id(session, root),
    }

  def _model_config_id(self, session: Session, root: Session) -> str:
    configured = (
        _session_model_config_id(session)
        or _session_model_config_id(root)
        or self.ctx.db.get_web_settings(user_id=self.ctx.settings.user_id).get(
            "model_config_id"
        )
    )
    return validate_model_config_id(configured)

  def _session_has_active_agent_loop(self, session_id: str) -> bool:
    if self.ctx.db.get_active_turn_for_session(session_id) is not None:
      return True
    session = self.ctx.services.session_service._read_session(session_id)
    if session is None:
      return False
    parent_session_id = _state_str(session, "handa:parent_session_id")
    parent_task_id = _state_str(session, "handa:parent_task_id")
    if not parent_session_id or not parent_task_id:
      return False
    try:
      parent_task = load_task(parent_task_id, session_id=parent_session_id)
    except (FileNotFoundError, KeyError, ValueError):
      return False
    return parent_task.get("status") in {"queued", "running"}

  def _root_session(self, session: Session) -> Session:
    current = session
    seen = {current.id}
    while True:
      parent_id = _state_str(current, "handa:parent_session_id")
      if not parent_id or parent_id in seen:
        return current
      parent = self.ctx.services.session_service._read_session(parent_id)
      if parent is None:
        return current
      seen.add(parent.id)
      current = parent

  def _should_skip_task_notification(self, session_id: str, task_id: Any) -> bool:
    resolved = str(task_id or "").strip()
    if not resolved:
      return False
    try:
      task = load_task(resolved, session_id=session_id)
    except (FileNotFoundError, KeyError, ValueError):
      return False
    return (
        str(task.get("kind") or "") == "web_turn"
        or bool(task.get("suppress_task_notification"))
    )

  async def _finalize_waiting_parent_task_if_ready(
      self,
      turn: dict[str, Any],
  ) -> None:
    """Fallback finalize for turns whose worker could not do it on exit
    (terminate, orphan cleanup). The normal path runs inside turn_worker."""
    await finalize_parent_agent_task(
        self.ctx.services,
        user_id=self.ctx.settings.user_id,
        child_session_id=str(turn["session_id"]),
        turn_status=str(turn.get("status") or ""),
        final_text=turn.get("final_text"),
        error_type=turn.get("error_type"),
        error_message=turn.get("error_message"),
    )

def _notification_for_event(
    session_id: str,
    source_event_id: str,
) -> dict[str, Any] | None:
  for notification in list_task_notifications(session_id=session_id):
    if notification.get("source_event_id") == source_event_id:
      return notification
  return None


def _render_notification_message(notification: dict[str, Any]) -> str:
  payload = notification.get("payload") or {}
  lines = [
      "System notification:",
      "",
      "A background task reached a terminal state.",
      "",
      f"task_id: {notification.get('task_id')}",
      f"task_kind: {payload.get('task_kind')}",
      f"status: {payload.get('task_status')}",
  ]
  child_session_id = payload.get("child_session_id")
  if child_session_id:
    lines.append(f"child_session_id: {child_session_id}")
  final_text = str(payload.get("final_text") or "").strip()
  if final_text:
    lines.extend(["", f"result_summary: {final_text}"])
  lines.extend(["", "Use task/result/artifact tools if you need details."])
  return "\n".join(lines)


def _terminal_event_kind(task: dict[str, Any]) -> str | None:
  status = str(task.get("status") or "")
  if status == "succeeded":
    return "task.completed"
  return None


def _terminal_event_summary(
    task: dict[str, Any],
    task_id: str,
    event_kind: str,
) -> str:
  kind = str(task.get("kind") or "task")
  if event_kind == "task.completed":
    return f"{kind} {task_id} completed"
  return f"{kind} {task_id} reached a terminal state"


def _compact_text(value: Any, *, max_chars: int = 2000) -> str | None:
  text = str(value or "").strip()
  if not text:
    return None
  if len(text) <= max_chars:
    return text
  return f"{text[: max_chars - 3]}..."


def _state_str(session: Session, key: str) -> str | None:
  value = (session.state or {}).get(key)
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _session_project_id(session: Session) -> str | None:
  return _state_str(session, "handa:project_id")


def _session_model_config_id(session: Session) -> str | None:
  return _state_str(session, "handa:model_config_id")
