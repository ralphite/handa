from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from google.genai import types

from ...agent_runtime import validate_agent_id
from ...config import AgentConfig
from ...config import agent_config_artifact_filename
from ...config import agent_config_warnings
from ...config import resolve_generated_agent_model_config_id
from ...model_configs import validate_model_config_id
from ...progress import PROGRESS_STATE_KEY
from ...progress import replace_progress_items
from ...runner import APP_NAME
from ...runtime import cancel_task_view
from ...runtime import list_task_events
from ...runtime import list_tasks
from ...runtime import read_task_log
from ...runtime import start_agent_run_task
from ...runtime import start_background_task
from ...runtime import start_run_agent_task
from ...runtime import task_log_file
from ...runtime import task_result_view
from ...runtime import task_status_view
from ...runtime import task_tool_view
from ...storage import HandaArtifactService
from ...storage import HandaSessionService
from ...storage.artifact_service import artifact_display_filename
from ...storage.artifact_service import artifact_stored_filename
from ...tools import browser as browser_tools
from ...tools import commands
from ...tools import files
from ...tools import skills
from ...tools.text_window import window_text
from ...tools.user_input import USER_INPUT_TOOL_NAME
from ...tools.user_input import UserInputQuestion
from ..tool_catalog import known_agent_tool_names


MAX_TOOL_RESULT_CHARS = 12000
MAX_RUN_AGENT_DEPTH = 8
RUN_AGENT_DELIVERY = (
    "The child agent result will be sent back as a system task notification when "
    "the task reaches completed, failed, or cancelled. Do not poll task status or "
    "logs to wait for completion."
)
_AGENT_TASK_KINDS = {"agent_run", "run_agent", "system_agent_run"}


@dataclass(frozen=True)
class SessionContext:
  session_id: str
  user_id: str
  app_name: str = APP_NAME
  model_config_id: str | None = None
  agent_run_depth: int = 0
  project_root: str | None = None


def build_session_context(
    *,
    session_id: str,
    user_id: str,
    model_config_id: str | None,
    project_root: str | None = None,
) -> SessionContext:
  """Build a SessionContext, reading the recursion depth from session state.

  The parent records `handa:agent_run_depth` on each child session it spawns, so
  a non-ADK child agent inherits the correct depth without an ADK ToolContext.
  """
  state = HandaSessionService().read_state_sync(session_id)
  return SessionContext(
      session_id=session_id,
      user_id=user_id,
      app_name=APP_NAME,
      model_config_id=model_config_id,
      agent_run_depth=_coerce_int(state.get("handa:agent_run_depth"), 0),
      project_root=project_root or _state_str(state.get("handa:project_root")),
  )


# Stateless repository tools reused verbatim from the shared tool layer. Paths
# resolve against the active `project_context` root, so the agent loop must run
# tool execution inside that context.
_SHARED_TOOLS: dict[str, Callable[..., Any]] = {
    "files_list": files.list,
    "files_search": files.search,
    "files_read": files.read,
    "files_write": files.write,
    "files_replace": files.replace,
    "commands_run": commands.run,
    "skills_list": skills.list,
    "skills_read": skills.read,
}


@dataclass(frozen=True)
class Toolset:
  callables: dict[str, Callable[..., Any]] = field(default_factory=dict)

  def as_genai_tool(self, client: Any) -> types.Tool:
    declarations = [
        types.FunctionDeclaration.from_callable(client=client, callable=function)
        for function in self.callables.values()
    ]
    return types.Tool(function_declarations=declarations)

  async def dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
    function = self.callables.get(name)
    if function is None:
      return _tool_error(name, ValueError(f"unknown tool: {name}"))
    try:
      result = function(**args)
      if inspect.isawaitable(result):
        result = await result
    except Exception as exc:  # noqa: BLE001 - tool errors feed back to the model.
      return _tool_error(name, exc)
    return _normalize_result(result)


def build_toolset(tool_names: list[str], ctx: SessionContext) -> Toolset:
  available = {**_SHARED_TOOLS, **_session_tools(ctx)}
  unknown = [name for name in tool_names if name not in available]
  if unknown:
    raise ValueError(f"Unknown agent tools: {', '.join(unknown)}")
  return Toolset(
      callables={name: _with_exposed_name(available[name], name) for name in tool_names}
  )


def _session_tools(ctx: SessionContext) -> dict[str, Callable[..., Any]]:
  artifact_service = HandaArtifactService()
  session_service = HandaSessionService()
  return {
      **_artifact_tools(ctx, artifact_service),
      **_task_tools(ctx),
      **_agent_tools(ctx, artifact_service),
      **_notification_tools(ctx, session_service),
      **_progress_tools(ctx, session_service),
      **_note_tools(ctx, session_service),
      **_browser_tools(ctx),
      **_user_input_tools(),
  }


def _artifact_tools(
    ctx: SessionContext,
    service: HandaArtifactService,
) -> dict[str, Callable[..., Any]]:

  async def artifacts_save_text(filename: str, content: str) -> dict[str, Any]:
    """Save a text artifact in the current session.

    Use filenames like `testing_quality.plan.md`,
    `pytest_result.verification.md`, or `testing_quality.agent.json`. The storage
    service adds `.vN.` when writing.
    """
    version = await service.save_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        filename=filename,
        artifact=types.Part.from_text(text=content),
    )
    return {
        "success": True,
        "filename": artifact_display_filename(filename),
        "stored_filename": artifact_stored_filename(filename, version),
        "version": version,
        "display_version": version + 1,
    }

  async def artifacts_list() -> dict[str, Any]:
    """List artifact filenames saved in the current session."""
    keys = await service.list_artifact_keys(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
    )
    return {"artifacts": keys, "count": len(keys)}

  async def artifacts_read(
      filename: str,
      version: int | None = None,
      offset: int = 0,
      max_chars: int | None = None,
      metadata_only: bool = False,
  ) -> dict[str, Any]:
    """Read a text artifact from the current session.

    Large artifacts are bounded: the reply carries `char_count`/`line_count`
    plus a windowed `content` slice. Pass `metadata_only=True` for just the
    size, or page a big artifact with `offset` (start char) and `max_chars`.
    """
    artifact = await service.load_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        filename=filename,
        version=version,
    )
    if artifact is None:
      return {"found": False, "filename": filename, "version": version}
    if artifact.text is not None:
      return {
          "found": True,
          "filename": filename,
          "version": version,
          **window_text(
              artifact.text,
              offset=offset,
              max_chars=max_chars,
              metadata_only=metadata_only,
          ),
      }
    inline_data = artifact.inline_data
    return {
        "found": True,
        "filename": filename,
        "version": version,
        "mime_type": inline_data.mime_type if inline_data else None,
        "byte_count": len(inline_data.data or b"") if inline_data else 0,
    }

  return {
      "artifacts_save_text": artifacts_save_text,
      "artifacts_list": artifacts_list,
      "artifacts_read": artifacts_read,
  }


def _task_tools(ctx: SessionContext) -> dict[str, Callable[..., Any]]:

  def tasks_start_background(
      command: str,
      cwd: str = ".",
      summary: str | None = None,
  ) -> dict[str, Any]:
    """Start a long-running shell command in the background."""
    task = start_background_task(
        command=command,
        cwd=cwd,
        summary=summary,
        session_id=ctx.session_id,
    )
    return task_tool_view(task)

  def tasks_get_status(task_id: str) -> dict[str, Any]:
    """Get the current status for one background task."""
    return task_status_view(task_id, session_id=ctx.session_id)

  def tasks_list() -> dict[str, Any]:
    """List recent background tasks."""
    return {
        "tasks": [
            task_tool_view(task) for task in list_tasks(session_id=ctx.session_id)
        ]
    }

  def tasks_read_log(task_id: str, tail_lines: int = 200) -> dict[str, Any]:
    """Read recent log lines for a background task."""
    return read_task_log(
        task_id=task_id,
        tail_lines=tail_lines,
        session_id=ctx.session_id,
    )

  def tasks_cancel(task_id: str) -> dict[str, Any]:
    """Cancel a running background task."""
    return cancel_task_view(task_id, session_id=ctx.session_id)

  return {
      "tasks_start_background": tasks_start_background,
      "tasks_get_status": tasks_get_status,
      "tasks_list": tasks_list,
      "tasks_read_log": tasks_read_log,
      "tasks_cancel": tasks_cancel,
  }


def _agent_tools(
    ctx: SessionContext,
    service: HandaArtifactService,
) -> dict[str, Callable[..., Any]]:

  def run_agent(
      agent_id: str,
      prompt: str,
      context: str | None = None,
      summary: str | None = None,
      max_depth: int = 3,
  ) -> dict[str, Any]:
    """Run a registered Handa agent as a child session task.

    `agent_id` must be one of Handa's registered agent definitions, such as the
    ADK agents `main`/`ralph` or the LangGraph agent `orca`. The target
    agent runs in a child session and can be inspected through task
    status/result/artifacts.
    """
    normalized_agent_id = validate_agent_id(agent_id)
    bounded_depth = _bounded_depth(max_depth)
    if ctx.agent_run_depth >= bounded_depth:
      raise ValueError(
          f"run_agent max depth reached: depth={ctx.agent_run_depth}, "
          f"max_depth={bounded_depth}"
      )
    task = start_run_agent_task(
        agent_id=normalized_agent_id,
        prompt=prompt,
        context=context,
        summary=summary,
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        app_name=ctx.app_name,
        depth=ctx.agent_run_depth,
    )
    return {
        "success": True,
        "task_id": task["id"],
        "status": task["status"],
        "agent_id": task["agent_id"],
        "child_session_id": task["child_session_id"],
        "delivery": RUN_AGENT_DELIVERY,
    }

  async def agents_save_config(
      name: str,
      description: str,
      tools: list[str],
      skills: list[str],
      instruction_sections: list[str],
      custom_instruction: str | None = None,
      model_config_id: str | None = None,
  ) -> dict[str, Any]:
    """Save an agent config artifact.

    `name` must be a valid agent identifier: letters, digits, and underscores,
    starting with a letter or underscore. Valid `instruction_sections` options
    are: identity, task_execution, tool_usage, file_editing, testing, storage,
    agent_config, subagents, html_output, communication. Use `custom_instruction`
    for extra
    free-form instructions; it is appended after the selected built-in sections.
    `model_config_id` is optional: set it to a supported model config id to pin
    this agent's model; omit it to inherit the model selected in the session.
    """
    normalized_model_config_id = None
    if model_config_id and model_config_id.strip():
      normalized_model_config_id = validate_model_config_id(model_config_id)
    config = AgentConfig(
        name=name,
        description=description,
        tools=tools,
        skills=skills,
        instruction_sections=instruction_sections,
        custom_instruction=custom_instruction,
        model_config_id=normalized_model_config_id,
    )
    _reject_unknown_tools(config.tools)
    warnings = agent_config_warnings(config)
    payload = json.dumps(
        config.model_dump(exclude_none=True),
        indent=2,
        ensure_ascii=True,
    )
    filename = agent_config_artifact_filename(name)
    version = await service.save_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        filename=filename,
        artifact=types.Part.from_text(text=payload),
    )
    return {
        "success": True,
        "filename": artifact_display_filename(filename),
        "stored_filename": artifact_stored_filename(filename, version),
        "version": version,
        "display_version": version + 1,
        "model_config_id": normalized_model_config_id or ctx.model_config_id,
        "warnings": warnings,
    }

  async def agents_read_config(name: str, version: int | None = None) -> dict[str, Any]:
    """Read an agent config artifact."""
    filename = agent_config_artifact_filename(name)
    artifact = await service.load_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        filename=filename,
        version=version,
    )
    if artifact is None or artifact.text is None:
      return {"found": False, "filename": filename, "version": version}
    return {
        "found": True,
        "filename": filename,
        "version": version,
        "config": json.loads(artifact.text),
    }

  async def agents_list_configs() -> dict[str, Any]:
    """List agent config artifacts."""
    keys = await service.list_artifact_keys(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
    )
    configs = [name for name in keys if name.endswith(".agent.json")]
    return {"configs": configs, "count": len(configs)}

  async def agents_start_run(
      name: str,
      prompt: str,
      context: str | None = None,
      summary: str | None = None,
      version: int | None = None,
      max_depth: int = 3,
  ) -> dict[str, Any]:
    """Start an Agent Config run as a parent task with a child session."""
    bounded_depth = _bounded_depth(max_depth)
    if ctx.agent_run_depth >= bounded_depth:
      raise ValueError(
          f"Agent Config run max depth reached: depth={ctx.agent_run_depth}, "
          f"max_depth={bounded_depth}"
      )
    filename = agent_config_artifact_filename(name)
    artifact = await service.load_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        filename=filename,
        version=version,
    )
    if artifact is None or artifact.text is None:
      raise ValueError(f"Agent Config artifact not found: {filename}")
    config = AgentConfig.model_validate_json(artifact.text)
    task = start_agent_run_task(
        config_name=config.name,
        prompt=prompt,
        context=context,
        summary=summary,
        config_version=version,
        # The config's model wins when it names a supported model config;
        # otherwise the run inherits the session-selected model.
        model_config_id=resolve_generated_agent_model_config_id(
            config,
            inherited_model_config_id=ctx.model_config_id,
        ),
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        app_name=ctx.app_name,
        depth=ctx.agent_run_depth,
    )
    return {
        "success": True,
        "task_id": task["id"],
        "status": task["status"],
        "child_session_id": task["child_session_id"],
        "config_name": task["config_name"],
    }

  def _agent_task_view(task_id: str) -> dict[str, Any]:
    view = task_status_view(task_id, session_id=ctx.session_id)
    if not view.get("found"):
      return view
    if not _is_agent_task(view["task"]):
      return {"found": False, "task_id": task_id, "error": "task is not an agent task"}
    return view

  def agents_get_run_status(task_id: str) -> dict[str, Any]:
    """Get the parent task status for one Agent Config run.

    The reply separates `tool_status` (this read succeeded) from `task_status`
    (how the child run itself ended): reading a *failed* run is still a
    successful tool call, so a failed child must not read as a tool failure.
    """
    return _with_task_status(_agent_task_view(task_id))

  def agents_read_run_result(task_id: str) -> dict[str, Any]:
    """Read the final structured result for one Agent Config run.

    Like `agents_get_run_status`, the reply separates `tool_status` from
    `task_status` so a successful read of a failed run is not mistaken for a
    failed read.
    """
    result = task_result_view(task_id, session_id=ctx.session_id)
    task = result.get("task")
    if not isinstance(task, dict):
      return result  # unknown task id
    if not _is_agent_task(task):
      return {"found": False, "task_id": task_id, "error": "task is not an agent task"}
    return _with_task_status(result)

  def agents_read_run_log(
      task_id: str,
      tail_lines: int = 200,
      metadata_only: bool = False,
  ) -> dict[str, Any]:
    """Read recent parent task log lines for one Agent Config run.

    Returns the tail plus the total `line_count` and the on-disk `log_path`.
    Pass `metadata_only=True` for just the line count and path, no log text.
    """
    view = _agent_task_view(task_id)
    if not view.get("found"):
      return view
    result = read_task_log(
        task_id, tail_lines=tail_lines, session_id=ctx.session_id
    )
    result["log_path"] = str(task_log_file(task_id, session_id=ctx.session_id))
    if metadata_only:
      result.pop("log", None)
    return result

  async def agents_list_run_artifacts(task_id: str) -> dict[str, Any]:
    """List artifacts from the child session for an Agent Config run."""
    view = _agent_task_view(task_id)
    if not view.get("found"):
      return view
    task = view["task"]
    keys = await service.list_artifact_keys(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=task["child_session_id"],
    )
    return {
        "found": True,
        "task_id": task_id,
        "child_session_id": task["child_session_id"],
        "artifacts": keys,
        "count": len(keys),
    }

  async def agents_read_run_artifact(
      task_id: str,
      filename: str,
      version: int | None = None,
      offset: int = 0,
      max_chars: int | None = None,
      metadata_only: bool = False,
  ) -> dict[str, Any]:
    """Read a text artifact from the child session for an Agent Config run.

    Child reports can be large, so the reply is bounded: it carries
    `char_count`/`line_count` plus a windowed `content` slice. Pass
    `metadata_only=True` for just the size, or page a big artifact with `offset`
    (start char) and `max_chars`.
    """
    view = _agent_task_view(task_id)
    if not view.get("found"):
      return view
    task = view["task"]
    artifact = await service.load_artifact(
        app_name=ctx.app_name,
        user_id=ctx.user_id,
        session_id=task["child_session_id"],
        filename=filename,
        version=version,
    )
    if artifact is None:
      return {"found": False, "task_id": task_id, "filename": filename}
    if artifact.text is None:
      return {
          "found": True,
          "task_id": task_id,
          "filename": filename,
          "text": None,
          "mime_type": artifact.inline_data.mime_type if artifact.inline_data else None,
          "size": len(artifact.inline_data.data or b"") if artifact.inline_data else 0,
      }
    return {
        "found": True,
        "task_id": task_id,
        "filename": filename,
        **window_text(
            artifact.text,
            offset=offset,
            max_chars=max_chars,
            metadata_only=metadata_only,
        ),
    }

  return {
      "run_agent": run_agent,
      "agents_save_config": agents_save_config,
      "agents_read_config": agents_read_config,
      "agents_list_configs": agents_list_configs,
      "agents_start_run": agents_start_run,
      "agents_get_run_status": agents_get_run_status,
      "agents_read_run_result": agents_read_run_result,
      "agents_read_run_log": agents_read_run_log,
      "agents_list_run_artifacts": agents_list_run_artifacts,
      "agents_read_run_artifact": agents_read_run_artifact,
  }


def _notification_tools(
    ctx: SessionContext,
    session_service: HandaSessionService,
) -> dict[str, Callable[..., Any]]:

  def notifications_get(
      unread_only: bool = True,
      limit: int = 20,
      mark_read: bool = True,
  ) -> dict[str, Any]:
    """Return recent structured task events for the current session."""
    state_key = "handa:last_seen_task_event_ts"
    after_ts = None
    if unread_only:
      raw = session_service.read_state_sync(ctx.session_id).get(state_key)
      after_ts = _coerce_float(raw)
    events = list_task_events(
        session_id=ctx.session_id,
        after_ts=after_ts,
        limit=limit,
    )
    if mark_read and events:
      session_service.merge_state_sync(
          ctx.session_id,
          {state_key: max(event["created_ts"] for event in events)},
      )
    return {
        "events": events,
        "count": len(events),
        "unread_only": unread_only,
    }

  return {"notifications_get": notifications_get}


def _note_tools(
    ctx: SessionContext,
    session_service: HandaSessionService,
) -> dict[str, Callable[..., Any]]:

  def notes_add(summary: str) -> dict[str, Any]:
    """Create a lightweight note in the current session state."""
    notes = list(session_service.read_state_sync(ctx.session_id).get("handa:notes", []))
    note = {"summary": summary, "session_id": ctx.session_id}
    notes.append(note)
    session_service.merge_state_sync(ctx.session_id, {"handa:notes": notes})
    return {"success": True, "note": note}

  return {"notes_add": notes_add}


def _progress_tools(
    ctx: SessionContext,
    session_service: HandaSessionService,
) -> dict[str, Callable[..., Any]]:

  def progress_update(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace the current session-level progress checklist shown in the Web UI.

    Each item should include a stable `id`, a short `title`, and a `status` of
    pending, running, done, or failed. The checklist represents the current
    session progress, so send the full list whenever it changes.
    """
    state = session_service.read_state_sync(ctx.session_id)
    progress_items = replace_progress_items(
        items,
        existing_items=state.get(PROGRESS_STATE_KEY),
        source_turn_id=_state_str(state.get("handa:active_turn_id")),
    )
    session_service.merge_state_sync(ctx.session_id, {PROGRESS_STATE_KEY: progress_items})
    # Do not echo the full `progress_items` back: the model already supplied them
    # in the call args, the canonical copy is persisted to session state for the
    # Web UI, and replaying the normalized list (with timestamps / source turn
    # ids) every call bloats history as the session deepens.
    return {
        "success": True,
        "count": len(progress_items),
    }

  return {"progress_update": progress_update}


def _browser_tools(ctx: SessionContext) -> dict[str, Callable[..., Any]]:

  async def browser_open(
      url: str,
      wait_until: str = "domcontentloaded",
  ) -> dict[str, Any]:
    """Open a URL in this session's Browser Environment."""
    return await browser_tools.open(
        session_id=ctx.session_id,
        url=url,
        wait_until=wait_until,
        project_root=ctx.project_root,
    )

  async def browser_snapshot(max_elements: int = 80) -> dict[str, Any]:
    """Capture visible clickable/input elements from the current browser page."""
    return await browser_tools.snapshot(
        session_id=ctx.session_id,
        max_elements=max_elements,
    )

  async def browser_click(target: str) -> dict[str, Any]:
    """Click a browser element by snapshot id such as e12, or by CSS selector."""
    return await browser_tools.click(session_id=ctx.session_id, target=target)

  async def browser_type(
      target: str,
      text: str,
      clear: bool = True,
  ) -> dict[str, Any]:
    """Type into a browser element by snapshot id such as e12, or by CSS selector."""
    return await browser_tools.type(
        session_id=ctx.session_id,
        target=target,
        text=text,
        clear=clear,
    )

  async def browser_keys(keys: str) -> dict[str, Any]:
    """Press keyboard keys in the current browser page."""
    return await browser_tools.keys(session_id=ctx.session_id, keys=keys)

  async def browser_scroll(
      direction: str = "down",
      amount: int = 600,
  ) -> dict[str, Any]:
    """Scroll the current browser page up or down."""
    return await browser_tools.scroll(
        session_id=ctx.session_id,
        direction=direction,
        amount=amount,
    )

  async def browser_wait(
      selector: str | None = None,
      text: str | None = None,
      timeout_ms: int = 5000,
  ) -> dict[str, Any]:
    """Wait for a selector, text, or a fixed timeout in the current browser page."""
    return await browser_tools.wait(
        session_id=ctx.session_id,
        selector=selector,
        text=text,
        timeout_ms=timeout_ms,
    )

  async def browser_screenshot(full_page: bool = False) -> dict[str, Any]:
    """Save the current browser screenshot for Web UI preview."""
    return await browser_tools.screenshot(
        session_id=ctx.session_id,
        full_page=full_page,
    )

  async def browser_close() -> dict[str, Any]:
    """Close this session's Browser Environment."""
    return await browser_tools.close(session_id=ctx.session_id)

  return {
      "browser_open": browser_open,
      "browser_snapshot": browser_snapshot,
      "browser_click": browser_click,
      "browser_type": browser_type,
      "browser_keys": browser_keys,
      "browser_scroll": browser_scroll,
      "browser_wait": browser_wait,
      "browser_screenshot": browser_screenshot,
      "browser_close": browser_close,
  }


def _user_input_tools() -> dict[str, Callable[..., Any]]:
  # Declaration-only control-flow tool: the agent loop intercepts this call
  # and pauses the run instead of dispatching it, so the body must never run.

  def request_user_input(questions: list[UserInputQuestion]) -> dict[str, Any]:
    """Ask the user structured questions and pause this turn until they answer.

    Use this when ambiguity about intent, requirements, approach, or the next
    step would change your plan, and when asking the user to confirm a plan.
    Provide at most 4 questions per call; each question needs 2-4 concrete,
    mutually exclusive options (put the recommended option first and mark it).
    Set multi_select=true when several answers can apply. The turn pauses
    after this call; the answers arrive as the tool response, either
    {"answers": [{"id", "selected", "free_text"?}]} or {"cancelled": true}
    when the user skipped the form. Call it at most once per turn.
    """
    raise RuntimeError(
        "request_user_input is a control-flow tool handled by the agent runtime"
    )

  return {USER_INPUT_TOOL_NAME: request_user_input}


def _bounded_depth(max_depth: int) -> int:
  return max(1, min(int(max_depth), MAX_RUN_AGENT_DEPTH))


def _is_agent_task(task: dict[str, Any]) -> bool:
  return task.get("kind") in _AGENT_TASK_KINDS


def _reject_unknown_tools(tools: list[str]) -> None:
  """Fail fast on tool names that no runtime can resolve (typos).

  Validated against the full cross-runtime catalog so a tool that is valid for
  whichever runtime runs the config is never rejected.
  """
  unknown = [name for name in tools if name not in known_agent_tool_names()]
  if unknown:
    raise ValueError(f"Unknown agent tools: {', '.join(unknown)}")


def _with_task_status(view: dict[str, Any]) -> dict[str, Any]:
  """Tag a successful agent-task read with explicit tool/task status fields."""
  if not view.get("found"):
    return view
  task = view.get("task")
  task_status = task.get("status") if isinstance(task, dict) else None
  return {**view, "tool_status": "ok", "task_status": task_status}


def _coerce_int(value: Any, default: int) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return default


def _coerce_float(value: Any) -> float | None:
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _state_str(value: Any) -> str | None:
  if value is None:
    return None
  text = str(value).strip()
  return text or None


def _with_exposed_name(function: Callable[..., Any], exposed_name: str) -> Callable[..., Any]:
  if getattr(function, "__name__", None) == exposed_name:
    return function

  def wrapper(*args: Any, **kwargs: Any) -> Any:
    return function(*args, **kwargs)

  wrapper.__name__ = exposed_name
  wrapper.__qualname__ = exposed_name
  wrapper.__doc__ = function.__doc__
  wrapper.__annotations__ = dict(getattr(function, "__annotations__", {}))
  wrapper.__signature__ = inspect.signature(function)  # type: ignore[attr-defined]
  return wrapper


def _normalize_result(result: Any) -> dict[str, Any]:
  if isinstance(result, dict):
    payload = result if "ok" in result else {"ok": True, **result}
  else:
    payload = {"ok": True, "result": result}
  return _truncate_value(payload, MAX_TOOL_RESULT_CHARS)


def _tool_error(name: str, exc: Exception) -> dict[str, Any]:
  return {
      "ok": False,
      "error": {
          "type": type(exc).__name__,
          "message": str(exc),
          "tool": name,
      },
  }


# Floor for any single field's truncation budget: even when a sibling field has
# consumed the whole payload budget, a field still keeps enough room to say
# something useful (an exit code, a short error, a filename).
MIN_FIELD_CHARS = 200


def _serialized_size(value: Any) -> int:
  return len(json.dumps(value, ensure_ascii=True, default=str))


def _truncate_value(value: Any, max_chars: int) -> Any:
  if _serialized_size(value) <= max_chars:
    return value
  if isinstance(value, str):
    return _truncate(value, max_chars)
  if isinstance(value, list):
    result: list[Any] = []
    total = 0
    for item in value:
      normalized = _truncate_value(item, max_chars)
      total += _serialized_size(normalized)
      if total > max_chars:
        result.append("... truncated ...")
        break
      result.append(normalized)
    return result
  if isinstance(value, dict):
    # Never drop keys: a failing command must keep `stderr` and `returncode`
    # even when `stdout` alone exceeds the budget. Fields that fit pass
    # through; oversized ones share the remaining budget, smallest first so
    # compact fields keep their full content.
    sizes = {key: _serialized_size(item) for key, item in value.items()}
    overhead = sum(len(str(key)) + 6 for key in value)
    remaining = max(max_chars - overhead, MIN_FIELD_CHARS)
    truncated: dict[Any, Any] = {}
    pending = len(value)
    for key, item in sorted(value.items(), key=lambda entry: sizes[entry[0]]):
      share = max(remaining // pending, MIN_FIELD_CHARS)
      if sizes[key] <= share:
        truncated[key] = item
        remaining -= sizes[key]
      else:
        truncated[key] = _truncate_value(item, share)
        remaining -= _serialized_size(truncated[key])
      remaining = max(remaining, 0)
      pending -= 1
    return {key: truncated[key] for key in value}
  return value


def _truncate(text: str, max_chars: int) -> str:
  if len(text) <= max_chars:
    return text
  return f"{text[:max_chars]}... truncated ..."
