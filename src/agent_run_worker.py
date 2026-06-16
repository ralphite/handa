from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from typing import Any

from dotenv import load_dotenv
from google.genai import types

from .agents.config_runner import run_config_agent
from .agents.native_loader import load_agent as load_native_agent
from .config import AgentConfig
from .config import agent_config_artifact_filename
from .config import resolve_agent_config_model_config_id
from .observability import setup_phoenix_tracing
from .run_outcome import RunOutcome
from .run_retry import run_with_retries
from .runtime import append_task_event
from .runtime import build_agent_run_report
from .runtime import list_tasks
from .runtime import LIVE_TASK_STATUSES
from .runtime import load_task
from .runtime import now_iso
from .runtime import project_context
from .runtime import save_task
from .runtime import task_log_file
from .runtime import task_result_file
from .storage import HandaArtifactService
from .storage import HandaSessionService
from .storage.native_only_migration import run_native_only_storage_migration
from .storage.paths import resolve_storage_root
from .storage.runtime_event_store import RuntimeEventStore
from .runner import APP_NAME
from .runner import DEFAULT_USER_ID


MAX_AGENT_RUN_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 2.0


def _configure_environment() -> None:
  load_dotenv()
  if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
  setup_phoenix_tracing()


def _log_runtime_event(log_handle, event: dict[str, Any]) -> None:
  log_handle.write(json.dumps(event, ensure_ascii=True) + "\n")
  log_handle.flush()


async def _load_agent_config(
    *,
    artifact_service: HandaArtifactService,
    parent_session_id: str,
    user_id: str,
    config_name: str,
    config_version: int | None,
) -> AgentConfig:
  filename = agent_config_artifact_filename(config_name)
  artifact = await artifact_service.load_artifact(
      app_name=APP_NAME,
      user_id=user_id,
      session_id=parent_session_id,
      filename=filename,
      version=config_version,
  )
  if artifact is None or artifact.text is None:
    raise ValueError(f"Agent Config artifact not found: {filename}")
  return AgentConfig.model_validate_json(artifact.text)


async def _save_parent_summary(
    *,
    artifact_service: HandaArtifactService,
    parent_session_id: str,
    user_id: str,
    task: dict[str, Any],
    final_text: str,
) -> str:
  label = _task_label(task)
  artifact_name = f"{task['kind']}_{label}.report.md"
  content = build_agent_run_report(task, final_text)
  await artifact_service.save_artifact(
      app_name=APP_NAME,
      user_id=user_id,
      session_id=parent_session_id,
      filename=artifact_name,
      artifact=types.Part.from_text(text=content),
  )
  return artifact_name


def _task_label(task: dict[str, Any]) -> str:
  return task.get("agent_id") or task.get("config_name") or task["id"]


async def _task_config(
    *,
    task: dict[str, Any],
    artifact_service: HandaArtifactService,
    parent_session_id: str,
    user_id: str,
) -> AgentConfig:
  if task["kind"] == "agent_run":
    config = await _load_agent_config(
        artifact_service=artifact_service,
        parent_session_id=parent_session_id,
        user_id=user_id,
        config_name=task["config_name"],
        config_version=task.get("config_version"),
    )
    resolved_model_config_id = resolve_agent_config_model_config_id(
        config,
        inherited_model_config_id=task.get("model_config_id"),
        allow_config_model=False,
    )
    normalized = config.model_dump(exclude_none=True)
    normalized["model_config_id"] = resolved_model_config_id
    return AgentConfig.model_validate(normalized)
  if task["kind"] == "system_agent_run":
    config = AgentConfig.model_validate(task["config"])
    resolved_model_config_id = resolve_agent_config_model_config_id(
        config,
        inherited_model_config_id=task.get("model_config_id"),
    )
    normalized = config.model_dump(exclude_none=True)
    normalized["model_config_id"] = resolved_model_config_id
    return AgentConfig.model_validate(normalized)
  raise ValueError(f"Unsupported Agent Config task kind: {task['kind']}")


async def _run_with_child_event_log(
    *,
    task: dict[str, Any],
    log_handle,
    session_service: HandaSessionService,
    storage_root,
    runner,
) -> str:
  runtime_events = RuntimeEventStore(storage_root)
  child_session_id = str(task.get("child_session_id") or task.get("session_id") or "")
  turn_id = f"session:{child_session_id}" if child_session_id else None
  produced_output = False

  async def emit_event(event: dict[str, Any]) -> None:
    nonlocal produced_output
    _log_runtime_event(log_handle, event)
    if _event_counts_as_user_visible_output(event):
      produced_output = True
    if not child_session_id:
      return
    runtime_events.append(
        session_id=child_session_id,
        turn_id=turn_id,
        runtime="native",
        event_id=_optional_str(event.get("id")),
        created_at=_optional_str(event.get("created_at") or event.get("timestamp")),
        event=event,
    )
    session_service.merge_state_sync(child_session_id, {})

  async def _attempt() -> RunOutcome:
    return await runner(emit_event)

  outcome = await run_with_retries(
      _attempt,
      max_attempts=MAX_AGENT_RUN_ATTEMPTS,
      base_delay_sec=RETRY_BASE_DELAY_SEC,
      should_retry=lambda: not produced_output,
      on_retry=lambda attempt, delay_sec, exc: _log_runtime_event(
          log_handle,
          {
              "kind": "native.retry",
              "summary": "Retrying child agent after transient error",
              "payload": {
                  "attempt": attempt,
                  "next_attempt": attempt + 1,
                  "delay_sec": delay_sec,
                  "error": {
                      "type": type(exc).__name__,
                      "message": str(exc),
                      "code": getattr(exc, "code", None),
                  },
              },
          },
      ),
  )
  if outcome.pending_user_input is not None:
    raise RuntimeError("request_user_input is not supported in child agent runs")
  return outcome.final_text


async def _run_config_task(
    *,
    task: dict[str, Any],
    artifact_service: HandaArtifactService,
    parent_session_id: str,
    user_id: str,
    log_handle,
    session_service: HandaSessionService,
    storage_root,
) -> str:
  config = await _task_config(
      task=task,
      artifact_service=artifact_service,
      parent_session_id=parent_session_id,
      user_id=user_id,
  )

  async def runner(emit_event):
    return await run_config_agent(
        config=config,
        prompt=_task_prompt(task),
        context=task.get("context") or "",
        project_root=task["project_root"],
        emit_event=emit_event,
        model_config_id=task.get("model_config_id"),
        session_id=task["child_session_id"],
        user_id=user_id,
    )

  return await _run_with_child_event_log(
      task=task,
      log_handle=log_handle,
      session_service=session_service,
      storage_root=storage_root,
      runner=runner,
  )


async def _run_registered_agent_task(
    *,
    task: dict[str, Any],
    log_handle,
    session_service: HandaSessionService,
    storage_root,
) -> str:
  runner_fn = load_native_agent(task["agent_id"])

  async def runner(emit_event):
    return await runner_fn(
        prompt=task["prompt"],
        context=task.get("context") or "",
        project_root=task["project_root"],
        emit_event=emit_event,
        model_config_id=task.get("model_config_id"),
        session_id=task.get("child_session_id") or task.get("session_id"),
        user_id=task.get("user_id"),
    )

  return await _run_with_child_event_log(
      task=task,
      log_handle=log_handle,
      session_service=session_service,
      storage_root=storage_root,
      runner=runner,
  )


def _task_prompt(task: dict[str, Any]) -> str:
  prompt = task["prompt"]
  if task.get("context"):
    prompt = f"{prompt}\n\nContext:\n{task['context']}"
  return prompt


async def _run_agent(session_id: str, task_id: str) -> int:
  _configure_environment()
  storage_root = resolve_storage_root()
  run_native_only_storage_migration(storage_root)
  task = load_task(task_id, session_id=session_id)
  if task.get("project_root"):
    with project_context(task["project_root"]):
      return await _run_agent_in_project(session_id, task_id, task, storage_root)
  return await _run_agent_in_project(session_id, task_id, task, storage_root)


async def _run_agent_in_project(
    session_id: str,
    task_id: str,
    task: dict[str, Any],
    storage_root,
) -> int:
  user_id = task.get("user_id") or DEFAULT_USER_ID

  task["status"] = "running"
  task["started_at"] = now_iso()
  save_task(task)
  append_task_event(
      "task.started",
      f"{task['kind']} {task_id} started",
      session_id=session_id,
      task_id=task_id,
      payload={"child_session_id": task["child_session_id"]},
  )

  session_service = HandaSessionService(root=str(storage_root))
  artifact_service = HandaArtifactService(root=str(storage_root))
  log_path = task_log_file(task_id, session_id=session_id)
  log_path.parent.mkdir(parents=True, exist_ok=True)
  final_text = ""

  try:
    with log_path.open("a", encoding="utf-8") as log_handle:
      if str(task.get("agent_runtime") or "native") != "native":
        raise ValueError(
            f"Unsupported agent runtime: {task.get('agent_runtime')!r}. "
            "Handa only supports native agents."
        )
      if task["kind"] in {"agent_run", "system_agent_run"}:
        final_text = await _run_config_task(
            task=task,
            artifact_service=artifact_service,
            parent_session_id=session_id,
            user_id=user_id,
            log_handle=log_handle,
            session_service=session_service,
            storage_root=storage_root,
        )
      elif task["kind"] == "run_agent":
        final_text = await _run_registered_agent_task(
            task=task,
            log_handle=log_handle,
            session_service=session_service,
            storage_root=storage_root,
        )
      else:
        raise ValueError(f"Unsupported agent task kind: {task['kind']}")

    task = load_task(task_id, session_id=session_id)
    if _has_child_tasks(task["child_session_id"]):
      task["status"] = "waiting"
      task["returncode"] = None
      save_task(task)
      append_task_event(
          "task.waiting",
          f"{task['kind']} {task_id} waiting on child tasks",
          session_id=session_id,
          task_id=task_id,
          payload={"child_session_id": task["child_session_id"]},
      )
      return 0

    task["status"] = "succeeded"
    task["returncode"] = 0
    task["finished_at"] = now_iso()
    summary_artifact = None
    if task.get("save_parent_summary", True):
      summary_artifact = await _save_parent_summary(
          artifact_service=artifact_service,
          parent_session_id=session_id,
          user_id=user_id,
          task=task,
          final_text=final_text,
      )
    task["summary_artifact"] = summary_artifact
    result = {
        "success": True,
        "task_id": task_id,
        "kind": task["kind"],
        "agent_id": task.get("agent_id"),
        "config_name": task.get("config_name"),
        "agent_runtime": "native",
        "child_session_id": task["child_session_id"],
        "final_text": final_text,
        "summary_artifact": summary_artifact,
    }
    task_result_file(task_id, session_id=session_id).write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    append_task_event(
        "task.completed",
        f"{task['kind']} {task_id} completed",
        session_id=session_id,
        task_id=task_id,
        payload={"child_session_id": task["child_session_id"]},
    )
    save_task(task)
    return 0
  except Exception as exc:  # noqa: BLE001 - worker must persist failures.
    error = {
        "success": False,
        "task_id": task_id,
        "child_session_id": task.get("child_session_id"),
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
    }
    with log_path.open("a", encoding="utf-8") as log_handle:
      log_handle.write(json.dumps(error, ensure_ascii=True) + "\n")
    task = load_task(task_id, session_id=session_id)
    task["status"] = "failed"
    task["returncode"] = 1
    task["finished_at"] = now_iso()
    task_result_file(task_id, session_id=session_id).write_text(
        json.dumps(error, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    append_task_event(
        "task.failed",
        f"Agent run {task_id} failed: {exc}",
        session_id=session_id,
        task_id=task_id,
        payload={"child_session_id": task.get("child_session_id")},
    )
    save_task(task)
    return 1


def _has_child_tasks(session_id: str) -> bool:
  return any(
      task.get("status") in LIVE_TASK_STATUSES
      for task in list_tasks(session_id=session_id)
  )


def _event_counts_as_user_visible_output(event: Any) -> bool:
  if not isinstance(event, dict):
    return True
  kind = str(event.get("kind") or "")
  if not kind:
    return True
  if kind.endswith(".started") or kind.endswith(".history_boundary"):
    return False
  return True


def _optional_str(value: Any) -> str | None:
  return None if value is None else str(value)


def main(session_id: str, task_id: str) -> int:
  return asyncio.run(_run_agent(session_id, task_id))


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1], sys.argv[2]))
