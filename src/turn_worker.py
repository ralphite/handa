from __future__ import annotations

import asyncio
import os
import signal
import sys
import traceback
from typing import Any

from dotenv import load_dotenv

from .observability import setup_phoenix_tracing
from .agent_runtime import DEFAULT_WEB_AGENT_ID
from .agent_runtime import get_agent_definition
from .agent_runtime import resolve_agent_id_for_runtime
from .run_manager import run_agent_invocation
from .runner import create_handa_services
from .runtime import append_task_event
from .runtime import load_task
from .runtime import now_iso
from .runtime import save_task
from .contract.parent_runs import finalize_parent_agent_task
from .contract.turn_trace import append_runtime_trace_event
from .contract.turn_trace import append_web_step_event


def _configure_environment() -> None:
  load_dotenv()
  if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
  setup_phoenix_tracing()


def _describe_exc(exc: BaseException) -> str:
  """A human-facing failure message; never empty.

  Some transport errors (notably httpx.ReadError from a dropped model
  connection) stringify to "", which the UI then renders as the generic
  "Turn worker failed." Fall back to the exception type so the real failure is
  at least named.
  """
  return str(exc) or type(exc).__name__


async def _run_turn(session_id: str, turn_id: str) -> int:
  task = load_task(turn_id, session_id=session_id)
  services = create_handa_services()
  storage_root = services.storage_root
  raw_agent_id = str(task.get("agent_id") or DEFAULT_WEB_AGENT_ID)
  agent_runtime = str(
      task.get("agent_runtime") or get_agent_definition(raw_agent_id).runtime
  )
  agent_id = resolve_agent_id_for_runtime(
      raw_agent_id,
      agent_runtime,
  )

  task["status"] = "running"
  task["started_at"] = task.get("started_at") or now_iso()
  save_task(task)
  append_task_event(
      "web_turn.started",
      f"Web turn {turn_id} started",
      session_id=session_id,
      task_id=turn_id,
  )
  _set_active_turn_id(services, session_id, turn_id)

  async def on_event(event: Any) -> None:
    append_runtime_trace_event(
        storage_root,
        session_id=session_id,
        turn_id=turn_id,
        runtime=agent_runtime,
        event=event,
    )

  try:
    outcome = await run_agent_invocation(
        session_id=session_id,
        user_id=str(task.get("user_id") or "user"),
        agent_id=agent_id,
        input_text=str(task.get("input_text") or ""),
        attachments=list(task.get("attachments") or []),
        on_event=on_event,
        project_root=task.get("project_root"),
        model_config_id=task.get("model_config_id"),
        resume_user_input=task.get("resume_user_input"),
    )

    task = load_task(turn_id, session_id=session_id)
    if task.get("cancel_requested_at"):
      # Terminate raced the run's completion; the cancel must stick.
      return 1
    if outcome.pending_user_input is not None:
      task["status"] = "waiting"
      task["pending_user_input"] = outcome.pending_user_input
      task["resume_user_input"] = None
      task["returncode"] = None
      save_task(task)
      append_task_event(
          "web_turn.waiting_input",
          f"Web turn {turn_id} waiting for user input",
          session_id=session_id,
          task_id=turn_id,
      )
      return 0

    task["status"] = "succeeded"
    task["final_text"] = outcome.final_text
    task["finished_at"] = now_iso()
    task["returncode"] = 0
    task["resume_user_input"] = None
    save_task(task)
    append_task_event(
        "web_turn.completed",
        f"Web turn {turn_id} completed",
        session_id=session_id,
        task_id=turn_id,
    )
    await finalize_parent_agent_task(
        services,
        user_id=str(task.get("user_id") or "user"),
        child_session_id=session_id,
        turn_status="completed",
        final_text=outcome.final_text,
    )
    return 0
  except asyncio.CancelledError:
    task = load_task(turn_id, session_id=session_id)
    if not task.get("cancel_requested_at"):
      # Cancelled from inside the run rather than via terminate (which already
      # emits the step before signalling); record the step ourselves.
      append_web_step_event(
          storage_root,
          session_id=session_id,
          turn_id=turn_id,
          kind="turn_cancelled",
          summary="Turn terminated",
          payload={"reason": "Turn run was cancelled."},
      )
    if task.get("status") not in {"cancelled", "failed", "succeeded"}:
      task["status"] = "cancelled"
      task["finished_at"] = task.get("finished_at") or now_iso()
      task["returncode"] = 1
      save_task(task)
    await finalize_parent_agent_task(
        services,
        user_id=str(task.get("user_id") or "user"),
        child_session_id=session_id,
        turn_status="cancelled",
        final_text=None,
    )
    return 1
  except Exception as exc:  # noqa: BLE001 - worker must persist failures.
    append_web_step_event(
        storage_root,
        session_id=session_id,
        turn_id=turn_id,
        kind="error",
        summary=_describe_exc(exc),
        payload={
            # `error_code` is carried (null here) so error steps share one field
            # shape with the runtime's error event, which reports `error_code`
            # and a null `error_type`.
            "error_type": type(exc).__name__,
            "error_code": None,
            "error_message": _describe_exc(exc),
            "traceback": traceback.format_exc(),
        },
    )
    task = load_task(turn_id, session_id=session_id)
    task["status"] = "failed"
    task["error_type"] = type(exc).__name__
    task["error_message"] = _describe_exc(exc)
    task["finished_at"] = now_iso()
    task["returncode"] = 1
    save_task(task)
    append_task_event(
        "web_turn.failed",
        f"Web turn {turn_id} failed",
        session_id=session_id,
        task_id=turn_id,
        payload={"error_type": type(exc).__name__},
    )
    await finalize_parent_agent_task(
        services,
        user_id=str(task.get("user_id") or "user"),
        child_session_id=session_id,
        turn_status="failed",
        final_text=None,
        error_type=type(exc).__name__,
        error_message=_describe_exc(exc),
    )
    return 1
  finally:
    _clear_active_turn_id(services, session_id, turn_id)


def _set_active_turn_id(services, session_id: str, turn_id: str) -> None:
  services.session_service.merge_state_sync(
      session_id,
      {"handa:active_turn_id": turn_id},
  )


def _clear_active_turn_id(services, session_id: str, turn_id: str) -> None:
  state = services.session_service.read_state_sync(session_id)
  if str(state.get("handa:active_turn_id") or "") != turn_id:
    return
  services.session_service.merge_state_sync(
      session_id,
      {"handa:active_turn_id": None},
  )


async def _main_async(session_id: str, turn_id: str) -> int:
  run_task = asyncio.ensure_future(_run_turn(session_id, turn_id))
  loop = asyncio.get_running_loop()
  for signum in (signal.SIGTERM, signal.SIGINT):
    loop.add_signal_handler(signum, run_task.cancel)
  try:
    return await run_task
  except asyncio.CancelledError:
    return 1


def main(session_id: str, turn_id: str) -> int:
  _configure_environment()
  return asyncio.run(_main_async(session_id, turn_id))


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1], sys.argv[2]))
