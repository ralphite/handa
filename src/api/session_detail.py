from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any
from typing import TYPE_CHECKING

from ..contract.browser import read_browser_summary
from ..contract.goals import present_goal_from_state
from ..contract.product import DEFAULT_WEB_AGENT_ID
from ..contract.product import PROGRESS_STATE_KEY
from ..contract.product import normalize_progress_items
from ..contract.services import APP_NAME
from ..contract.task_store import list_tasks
from ..contract.task_store import load_task
from ..contract.storage import RuntimeEventStore
from .context import WebApiContext
from .context_usage_breakdown import build_context_usage_breakdown
from .presenters.event_presenter import project_model_event
from .presenters.runtime_event_presenter import project_runtime_event
from .usage import summarize_token_usage

if TYPE_CHECKING:
  from ..storage.session_service import Session


AGENT_TASK_KINDS = {"agent_run", "run_agent", "system_agent_run"}
BACKGROUND_TASK_KINDS = {"command", "test", "index", "sync", "custom"}
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled"}
PROMPT_STATE_KEYS = (
    "handa:run_agent_prompt",
    "handa:agent_run_prompt",
    "handa:system_agent_run_prompt",
)


async def build_session_detail(
    ctx: WebApiContext,
    session_id: str,
    *,
    include_events: bool = True,
) -> dict[str, Any] | None:
  if ctx.db.is_session_deleted(session_id):
    return None
  session = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  if session is None:
    return None

  chain = await _session_chain(ctx, session)
  root = chain[0]
  meta = ctx.db.get_session_meta(session.id)
  root_meta = ctx.db.get_session_meta(root.id)
  root_turn = ctx.db.get_latest_turn_for_session(root.id)
  running_turn = ctx.db.get_active_turn_for_session(session.id)
  latest_turn = ctx.db.get_latest_turn_for_session(session.id)
  status_turn = running_turn or latest_turn
  project_id = _project_id_for(root, root_turn, root_meta)
  project = ctx.db.get_project(project_id) if project_id else None
  root_title = _root_session_title(root, root_meta, root_turn)
  task = _parent_task(session)
  agent_runtime = _agent_runtime_for(session, task, meta)
  agent_id = _agent_id_for(session, task, latest_turn, meta)
  prompt = _prompt_for(session)
  project_root = project["root_path"] if project else _state_str(root, "handa:project_root")
  status = _session_status(session, task, status_turn)
  events = (
      _project_events_for_runtime(ctx, session, agent_runtime)
      if include_events
      else []
  )
  usage = summarize_token_usage(_usage_events_for_runtime(ctx, session, agent_runtime))
  context_breakdown = await build_context_usage_breakdown(
      ctx,
      session,
      task=task,
      agent_id=agent_id,
      agent_runtime=agent_runtime,
      project_root=project_root,
      prompt=prompt,
      target_token_count=usage.context_token_count,
  )

  return {
      "id": session.id,
      "session_id": session.id,
      "title": root_title if session.id == root.id else _child_session_title(session, task),
      "agent_id": agent_id,
      "agent_runtime": agent_runtime,
      "automated_task_id": _state_str(root, "handa:automated_task_id"),
      "project_id": project_id,
      "project_root": project_root,
      "status": status,
      "created_at": _created_at_from_session_id(session.id),
      "updated_at": _updated_at(session),
      "parent_session_id": _parent_session_id(session),
      "parent_task_id": _state_str(session, "handa:parent_task_id"),
      "root_session_id": root.id,
      "forked_from_session_id": _fork_state(session, meta, "session_id"),
      "forked_from_turn_id": _fork_state(session, meta, "turn_id"),
      "forked_at": _fork_state(session, meta, "at"),
      "prompt": prompt,
      "goal": present_goal_from_state(session.state),
      "input_token_count": usage.context_token_count,
      "output_token_count": usage.output_token_count,
      "total_token_count": usage.total_token_count,
      "context_usage_breakdown": context_breakdown,
      "breadcrumbs": _breadcrumbs(chain, root_title, project),
      "progress_items": _progress_items(session, session_status=status),
      "browser_environment": _browser_environment(ctx, session),
      "background_runs": await _background_runs(ctx, session.id),
      "steps": events,
  }


async def _session_chain(ctx: WebApiContext, session: Session) -> list[Session]:
  chain = [session]
  seen = {session.id}
  current = session
  while True:
    parent_id = _parent_session_id(current)
    if not parent_id or parent_id in seen:
      break
    parent = await ctx.services.session_service.get_session(
        app_name=APP_NAME,
        user_id=ctx.settings.user_id,
        session_id=parent_id,
    )
    if parent is None:
      break
    chain.append(parent)
    seen.add(parent.id)
    current = parent
  return list(reversed(chain))


def _state_str(session: Session, key: str) -> str | None:
  value = (session.state or {}).get(key)
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _parent_session_id(session: Session) -> str | None:
  return (
      _state_str(session, "handa:parent_session_id")
      or _state_str(session, "handa:parent_thread_id")
  )


def _fork_state(
    session: Session,
    meta: dict[str, Any] | None,
    field: str,
) -> str | None:
  key = f"forked_from_{field}" if field != "at" else "forked_at"
  if field == "session_id":
    return (
        _state_str(session, "handa:forked_from_session_id")
        or _state_str(session, "handa:forked_from_thread_id")
        or (meta or {}).get("forked_from_session_id")
        or (meta or {}).get("forked_from_thread_id")
    )
  return _state_str(session, f"handa:{key}") or (meta or {}).get(key)


def _project_id_for(
    root: Session,
    turn: dict[str, Any] | None,
    meta: dict[str, Any] | None,
) -> str | None:
  if meta and meta.get("project_id"):
    return str(meta["project_id"])
  return _state_str(root, "handa:project_id") or (
      str(turn["project_id"]) if turn and turn.get("project_id") else None
  )


def _root_session_title(
    root: Session,
    meta: dict[str, Any] | None,
    turn: dict[str, Any] | None,
) -> str:
  if meta and str(meta.get("title") or "").strip():
    return str(meta["title"]).strip()
  if turn and turn.get("title"):
    return str(turn["title"])
  agent_id = _state_str(root, "handa:agent_id") or DEFAULT_WEB_AGENT_ID
  return f"{agent_id} · {root.id}"


def _child_session_title(session: Session, task: dict[str, Any] | None) -> str:
  label = _task_label(task) if task else _child_agent_label(session, task)
  return f"{label}: {_short_child_label(session.id)}"


def _agent_id_for(
    session: Session,
    task: dict[str, Any] | None,
    turn: dict[str, Any] | None,
    meta: dict[str, Any] | None,
) -> str:
  if meta and meta.get("agent_id"):
    return str(meta["agent_id"])
  if task and task.get("agent_id"):
    return str(task["agent_id"])
  if task and task.get("config_name"):
    return str(task["config_name"])
  if turn and turn.get("agent_id"):
    return str(turn["agent_id"])
  return (
      _state_str(session, "handa:target_agent_id")
      or _state_str(session, "handa:agent_id")
      or _state_str(session, "handa:agent_run_config_name")
      or _state_str(session, "handa:system_agent_config_name")
      or DEFAULT_WEB_AGENT_ID
  )


def _agent_runtime_for(
    session: Session,
    task: dict[str, Any] | None,
    meta: dict[str, Any] | None,
) -> str:
  if meta and meta.get("agent_runtime"):
    return str(meta["agent_runtime"])
  return str(
      (task or {}).get("agent_runtime")
      or _state_str(session, "handa:agent_runtime")
      or "native"
  )


def _session_status(
    session: Session,
    task: dict[str, Any] | None,
    turn: dict[str, Any] | None,
) -> str:
  if task:
    return _task_status(task)
  if turn:
    return _turn_status(turn.get("status"))
  return "done" if session.events else "idle"


def _task_status(task: dict[str, Any]) -> str:
  status = str(task.get("status") or "")
  if status == "succeeded":
    return "done"
  if status in {"queued", "running", "waiting", "failed", "cancelled"}:
    return status
  return "idle"


def _turn_status(status: Any) -> str:
  if status == "completed":
    return "done"
  if status in {"queued", "running", "waiting_input", "failed", "cancelled"}:
    return str(status)
  return "idle"


def _updated_at(session: Session) -> str:
  value = getattr(session, "last_update_time", None)
  if value is None:
    return _created_at_from_session_id(session.id)
  try:
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
  except (TypeError, ValueError, OSError):
    return _created_at_from_session_id(session.id)


def _created_at_from_session_id(session_id: str) -> str:
  try:
    return datetime.strptime(session_id[:15], "%Y%m%d-%H%M%S").astimezone(timezone.utc).isoformat()
  except ValueError:
    return datetime.now(tz=timezone.utc).isoformat()


def _parent_task(session: Session) -> dict[str, Any] | None:
  parent_session_id = _parent_session_id(session)
  parent_task_id = _state_str(session, "handa:parent_task_id")
  if not parent_session_id or not parent_task_id:
    return None
  try:
    return load_task(parent_task_id, session_id=parent_session_id)
  except (FileNotFoundError, KeyError, ValueError):
    return None


def _breadcrumbs(
    chain: list[Session],
    root_title: str,
    project: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
  if not chain:
    return []
  root = chain[0]
  project_id = _state_str(root, "handa:project_id")
  project_label = str(project.get("name") or project_id or "project") if project else project_id or "project"
  crumbs: list[dict[str, str | None]] = [
      {
          "id": f"project:{project_id or project_label}",
          "label": project_label,
          "title": str(project.get("root_path")) if project else _state_str(root, "handa:project_root"),
      },
      {"id": root.id, "label": root_title, "title": root.id},
  ]
  for session in chain[1:]:
    task = _parent_task(session)
    label = f"{_child_agent_label(session, task)}: {_short_child_label(session.id)}"
    crumbs.append({"id": session.id, "label": label, "title": session.id})
  return crumbs


# Session statuses with no live or queued invocation: nothing can still be
# executing a progress item, so a stored `running` entry is a leftover from an
# interrupted invocation (stop, crash, quota error).
_INACTIVE_SESSION_STATUSES = {"done", "failed", "cancelled", "idle"}


def _progress_items(
    session: Session,
    *,
    session_status: str | None = None,
) -> list[dict[str, Any]]:
  items = normalize_progress_items((session.state or {}).get(PROGRESS_STATE_KEY))
  if session_status not in _INACTIVE_SESSION_STATUSES:
    return items
  return [
      {**item, "status": "pending"} if item.get("status") == "running" else item
      for item in items
  ]


def _browser_environment(ctx: WebApiContext, session: Session) -> dict[str, Any] | None:
  own = read_browser_summary(ctx.services.storage_root, session.id)
  if own is not None:
    return own
  return _child_browser_environment(ctx, session.id)


def _child_browser_environment(
    ctx: WebApiContext,
    session_id: str,
) -> dict[str, Any] | None:
  """Surface a Browser Environment owned by a delegated sub-agent session.

  Browser work is delegated to the `browser` sub-agent, which runs in a child
  session, so its Browser Environment lives under the child session id. When the
  viewed session has none of its own, surface the most recently updated child
  environment (unless closed) so the live viewer still appears in this panel.
  The summary carries the owning child session id and its own screenshot/stream
  URLs, so viewing and interaction target the right session automatically.
  """
  best: dict[str, Any] | None = None
  for task in list_tasks(session_id=session_id):
    if _background_run_kind(task) != "sub-agent":
      continue
    child_session_id = str(task.get("child_session_id") or "")
    if not child_session_id:
      continue
    summary = read_browser_summary(ctx.services.storage_root, child_session_id)
    if summary is None or summary.get("status") == "closed":
      continue
    if best is None or str(summary.get("updated_at") or "") > str(best.get("updated_at") or ""):
      best = summary
  return best


async def _background_runs(ctx: WebApiContext, session_id: str) -> list[dict[str, Any]]:
  runs: list[dict[str, Any]] = []
  for task in list_tasks(session_id=session_id):
    kind = _background_run_kind(task)
    if kind is None:
      continue
    child_session_id = str(task.get("child_session_id") or "")
    artifact_count = await _task_artifact_count(
        ctx,
        parent_session_id=session_id,
        task=task,
        child_session_id=child_session_id,
    )
    current_step = await _current_step(ctx, child_session_id, task) if child_session_id else _task_current_step(task)
    runs.append(
        {
            "id": task["id"],
            "kind": kind,
            "title": _task_label(task),
            "status": _task_status(task),
            "child_session_id": child_session_id or None,
            "current_step": current_step,
            "artifact_count": artifact_count,
        }
    )
  return runs


def _background_run_kind(task: dict[str, Any]) -> str | None:
  raw_kind = str(task.get("kind") or "").strip()
  if raw_kind in AGENT_TASK_KINDS:
    return "sub-agent"
  normalized = raw_kind.replace("_", "-")
  if normalized in BACKGROUND_TASK_KINDS:
    return normalized
  return None


async def _artifact_count(ctx: WebApiContext, session_id: str) -> int:
  if not session_id:
    return 0
  artifacts = await ctx.services.artifact_service.list_artifact_keys(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  return len(artifacts)


async def _task_artifact_count(
    ctx: WebApiContext,
    *,
    parent_session_id: str,
    task: dict[str, Any],
    child_session_id: str,
) -> int:
  count = await _artifact_count(ctx, child_session_id) if child_session_id else 0
  summary_artifact = str(task.get("summary_artifact") or "").strip()
  if not summary_artifact:
    return count
  artifact = await ctx.services.artifact_service.load_artifact(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=parent_session_id,
      filename=summary_artifact,
  )
  return count + (1 if artifact is not None else 0)


async def _current_step(
    ctx: WebApiContext,
    child_session_id: str,
    task: dict[str, Any],
) -> str | None:
  if _task_status(task) != "running":
    return None
  child = await ctx.services.session_service.get_session(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=child_session_id,
  )
  if child is None:
    return None
  runtime = str(task.get("agent_runtime") or _state_str(child, "handa:agent_runtime") or "native")
  for item in reversed(
      RuntimeEventStore(ctx.settings.storage_root).list_events(
          session_id=child_session_id,
          runtime=runtime,
      )
  ):
    raw_event = item.get("event")
    if not isinstance(raw_event, dict):
      continue
    for projected in reversed(project_runtime_event(raw_event, runtime=runtime)):
      if projected["kind"] in {"tool_call", "tool_response", "artifact_delta"}:
        return str(projected["summary"])
  for event in reversed(child.events):
    for projected in reversed(project_model_event(event)):
      if projected["kind"] in {"tool_call", "tool_response", "artifact_delta"}:
        return str(projected["summary"])
  return None


def _task_label(task: dict[str, Any] | None) -> str:
  if not task:
    return "agent"
  summary = str(task.get("summary") or "").strip()
  if summary:
    return summary
  return (
      str(task.get("agent_id") or "").strip()
      or str(task.get("config_name") or "").strip()
      or str(task.get("id") or "agent")
  )


def _task_current_step(task: dict[str, Any]) -> str | None:
  command = str(task.get("command") or "").strip()
  if command and command != _task_label(task):
    return command
  return None


def _child_agent_label(session: Session, task: dict[str, Any] | None) -> str:
  if task:
    return _task_label(task)
  return (
      _state_str(session, "handa:target_agent_id")
      or _state_str(session, "handa:agent_run_config_name")
      or _state_str(session, "handa:system_agent_config_name")
      or "agent"
  )


def _short_child_label(session_id: str) -> str:
  return session_id.rsplit("-", 1)[-1] if "-" in session_id else session_id


def _prompt_for(session: Session) -> str | None:
  for key in PROMPT_STATE_KEYS:
    value = _state_str(session, key)
    if value:
      return value
  return None


def _project_events_for_runtime(
    ctx: WebApiContext,
    session: Session,
    runtime: str,
) -> list[dict[str, Any]]:
  projected_events: list[dict[str, Any]] = []
  seq = 0
  synthetic_turn_id = f"session:{session.id}"
  for item in RuntimeEventStore(ctx.settings.storage_root).list_events(
      session_id=session.id,
      runtime=runtime,
  ):
    raw_event = item.get("event")
    if not isinstance(raw_event, dict):
      continue
    projections = project_runtime_event(raw_event, runtime=runtime)
    if not projections:
      continue
    base_id = str(item.get("id") or raw_event.get("id") or f"{synthetic_turn_id}:{seq + 1}")
    turn_id = str(item.get("turn_id") or synthetic_turn_id)
    created_at = _runtime_event_created_at(item, raw_event)
    for index, projected in enumerate(projections):
      seq += 1
      projected_events.append(
          {
              "id": base_id if index == 0 else f"{base_id}#{index}",
              "turn_id": turn_id,
              "seq": seq,
              "session_seq": seq,
              "kind": projected["kind"],
              "summary": projected["summary"],
              "payload": projected["payload"],
              "created_at": created_at,
          }
      )
  return projected_events


def _usage_events_for_runtime(
    ctx: WebApiContext,
    session: Session,
    runtime: str,
) -> list[dict[str, Any]]:
  events: list[dict[str, Any]] = []
  for item in RuntimeEventStore(ctx.settings.storage_root).list_events(
      session_id=session.id,
      runtime=runtime,
  ):
    raw_event = item.get("event")
    if not isinstance(raw_event, dict):
      continue
    events.append(
        {
            "id": item.get("id") or raw_event.get("id"),
            "raw_event": raw_event,
        }
    )
  return events


def _event_created_at(raw_event: dict[str, Any]) -> str:
  timestamp = raw_event.get("timestamp")
  if timestamp is None:
    return datetime.now(tz=timezone.utc).isoformat()
  try:
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
  except (TypeError, ValueError, OSError):
    return datetime.now(tz=timezone.utc).isoformat()


def _runtime_event_created_at(
    item: dict[str, Any],
    raw_event: dict[str, Any],
) -> str:
  created_at = item.get("created_at") or raw_event.get("created_at")
  if isinstance(created_at, str) and created_at.strip():
    return created_at
  return _event_created_at(raw_event)
