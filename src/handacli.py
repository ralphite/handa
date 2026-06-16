"""Run Handa once from the command line.

handacli is a thin HTTP client of the Handa web API — the same contract the web
frontend uses. It looks up the named project, submits one turn, then polls the
turn until it settles; the agent itself runs in the web-spawned turn worker. A
session started here is therefore immediately visible in the browser, streams
its steps live there, and can be interacted with (user-input forms, terminate)
exactly as if it had been started from the web UI.

`--project` is a project name (the same name shown in the web sidebar), not a
filesystem path: the project must already be registered in Handa. Path-based
callers that legitimately start from a directory (e.g. reverse_spec_cli) use
resolve_project_name_for_path() to register-or-find by path, then pass the name.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from typing import Literal

import httpx
from pydantic import BaseModel
from pydantic import Field


HandaCliStatus = Literal["completed", "failed", "cancelled", "waiting_input"]

DEFAULT_API_URL = "http://127.0.0.1:5086"
DEFAULT_AGENT_ID = "orca"
POLL_INTERVAL_SEC = 1.0
# A transient API outage (e.g. a web restart) must not fail a run whose
# worker is still alive out of process; tolerate consecutive poll errors.
MAX_POLL_FAILURES = 30

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
# waiting_input parks the turn server-side: the worker exited and resumes
# only when the form is answered (e.g. from the browser session view), so
# the one-shot CLI returns instead of waiting on an unattended form.
_RETURNED_STATUSES = _TERMINAL_STATUSES | {"waiting_input"}


class HandaCliError(BaseModel):
  type: str
  message: str


class HandaCliTokenStats(BaseModel):
  input: int = 0
  output: int = 0
  total: int = 0


class HandaCliToolStats(BaseModel):
  total_calls: int = 0
  total_success: int = 0
  total_fail: int = 0
  total_duration_ms: int = 0


class HandaCliFileStats(BaseModel):
  lines_added: int = 0
  lines_removed: int = 0


class HandaCliStats(BaseModel):
  tokens: HandaCliTokenStats = Field(default_factory=HandaCliTokenStats)
  tools: HandaCliToolStats = Field(default_factory=HandaCliToolStats)
  files: HandaCliFileStats = Field(default_factory=HandaCliFileStats)
  active_seconds: float = 0.0


class HandaCliResult(BaseModel):
  ok: bool
  status: HandaCliStatus
  session_id: str | None = None
  turn_id: str | None = None
  response: str = ""
  stats: HandaCliStats = Field(default_factory=HandaCliStats)
  error: HandaCliError | None = None


class HandaCliApiError(Exception):
  def __init__(self, error: HandaCliError):
    super().__init__(error.message)
    self.error = error


def _base_url(api_url: str | None) -> str:
  return (api_url or os.getenv("HANDA_WEB_API_URL") or DEFAULT_API_URL).rstrip("/")


def _api_unavailable(base_url: str, exc: Exception) -> HandaCliApiError:
  return HandaCliApiError(
      HandaCliError(
          type="WebApiUnavailable",
          message=(
              f"Handa web API is not reachable at {base_url} ({exc}). "
              "Start it with `uv run python -m src.api.app` from the public repo "
              "or `scripts/restart-dev.sh` from the private parent checkout."
          ),
      )
  )


def _raise_for_api(response: httpx.Response) -> None:
  if response.status_code < 400:
    return
  try:
    detail = response.json().get("detail")
  except ValueError:
    detail = None
  message = str(detail or response.text or f"HTTP {response.status_code}")
  error_type = (
      "SessionNotFound" if message == "Session not found" else "WebApiError"
  )
  raise HandaCliApiError(HandaCliError(type=error_type, message=message))


async def _list_projects(client: httpx.AsyncClient) -> list[dict[str, Any]]:
  try:
    listed = await client.get("/api/projects")
  except httpx.TransportError as exc:
    raise _api_unavailable(str(client.base_url), exc) from exc
  _raise_for_api(listed)
  return listed.json()


async def _resolve_project_id_by_name(client: httpx.AsyncClient, name: str) -> str:
  """Resolve a project name to its id. Names are not unique, so reject dups."""
  rows = await _list_projects(client)
  matches = [row for row in rows if str(row.get("name")) == name]
  if len(matches) == 1:
    return str(matches[0]["id"])
  if not matches:
    available = sorted({str(row.get("name")) for row in rows if row.get("name")})
    shown = ", ".join(available[:20]) if available else "(none registered)"
    raise HandaCliApiError(
        HandaCliError(
            type="ProjectNotFound",
            message=(
                f"No Handa project named {name!r}. Register it in the Handa web UI "
                f"first. Available projects: {shown}."
            ),
        )
    )
  roots = ", ".join(sorted(str(row.get("root_path")) for row in matches))
  raise HandaCliApiError(
      HandaCliError(
          type="AmbiguousProject",
          message=(
              f"{len(matches)} projects are named {name!r} ({roots}). "
              "Rename one in the Handa web UI to disambiguate."
          ),
      )
  )


async def _resolve_project_id_by_path(
    client: httpx.AsyncClient, root_path: str
) -> tuple[str, str]:
  """Find a project by filesystem path, registering it if absent.

  Returns (project_id, project_name). Used by path-based callers that need the
  canonical name before delegating to the name-only CLI surface.
  """
  for row in await _list_projects(client):
    if str(row.get("root_path")) == root_path:
      return str(row["id"]), str(row.get("name") or "")
  created = await client.post("/api/projects", json={"root_path": root_path})
  if created.status_code == 409:
    for row in await _list_projects(client):
      if str(row.get("root_path")) == root_path:
        return str(row["id"]), str(row.get("name") or "")
  _raise_for_api(created)
  body = created.json()
  return str(body["id"]), str(body.get("name") or "")


async def _submit_turn(
    client: httpx.AsyncClient,
    *,
    project_id: str,
    prompt: str,
    session_id: str | None,
    agent_id: str,
    model_config_id: str | None,
) -> dict[str, Any]:
  data: dict[str, str] = {
      "input_text": prompt,
      "project_id": project_id,
      "agent_id": agent_id,
  }
  if session_id:
    data["session_id"] = session_id
  if model_config_id:
    data["model_config_id"] = model_config_id
  try:
    response = await client.post("/api/turns", data=data)
  except httpx.TransportError as exc:
    raise _api_unavailable(str(client.base_url), exc) from exc
  _raise_for_api(response)
  return response.json()


async def _poll_turn(
    client: httpx.AsyncClient,
    turn_id: str,
    *,
    poll_interval_sec: float,
) -> dict[str, Any]:
  failures = 0
  while True:
    try:
      response = await client.get(f"/api/turns/{turn_id}")
      _raise_for_api(response)
      turn = response.json()
      failures = 0
    except httpx.TransportError as exc:
      failures += 1
      if failures > MAX_POLL_FAILURES:
        raise _api_unavailable(str(client.base_url), exc) from exc
      await asyncio.sleep(poll_interval_sec)
      continue
    if str(turn.get("status")) in _RETURNED_STATUSES:
      return turn
    await asyncio.sleep(poll_interval_sec)


def _stats_from_turn(turn: dict[str, Any]) -> HandaCliStats:
  """Surface the token/tool/file/timing facts the web turn accumulates.

  Every sub-block is always present; fields default to 0 when the turn record
  carries no value (e.g. turns that ran before instrumentation, or that
  touched no tools/files). tool_duration_ms reflects the tool runtime the web
  API records — today that is command execution time.
  """
  return HandaCliStats(
      tokens=HandaCliTokenStats(
          input=int(turn.get("input_token_count") or 0),
          output=int(turn.get("output_token_count") or 0),
          total=int(turn.get("total_token_count") or 0),
      ),
      tools=HandaCliToolStats(
          total_calls=int(turn.get("tool_call_count") or 0),
          total_success=int(turn.get("tool_success_count") or 0),
          total_fail=int(turn.get("tool_fail_count") or 0),
          total_duration_ms=int(turn.get("tool_duration_ms") or 0),
      ),
      files=HandaCliFileStats(
          lines_added=int(turn.get("file_lines_added") or 0),
          lines_removed=int(turn.get("file_lines_removed") or 0),
      ),
      active_seconds=float(turn.get("active_seconds") or 0.0),
  )


def _result_from_turn(turn: dict[str, Any]) -> HandaCliResult:
  status = str(turn.get("status"))
  session_id = str(turn.get("session_id") or "") or None
  turn_id = str(turn.get("id") or "") or None
  stats = _stats_from_turn(turn)
  if status == "completed":
    return HandaCliResult(
        ok=True,
        status="completed",
        session_id=session_id,
        turn_id=turn_id,
        response=str(turn.get("final_text") or ""),
        stats=stats,
    )
  if status == "waiting_input":
    return HandaCliResult(
        ok=True,
        status="waiting_input",
        session_id=session_id,
        turn_id=turn_id,
        stats=stats,
    )
  if status == "cancelled":
    return HandaCliResult(
        ok=False,
        status="cancelled",
        session_id=session_id,
        turn_id=turn_id,
        stats=stats,
        error=HandaCliError(
            type=str(turn.get("error_type") or "Cancelled"),
            message=str(turn.get("error_message") or "Turn was cancelled."),
        ),
    )
  return HandaCliResult(
      ok=False,
      status="failed",
      session_id=session_id,
      turn_id=turn_id,
      stats=stats,
      error=HandaCliError(
          type=str(turn.get("error_type") or "TurnFailed"),
          message=str(turn.get("error_message") or "Turn failed."),
      ),
  )


async def run_handa_cli(
    *,
    project_name: str,
    prompt: str,
    session_id: str | None = None,
    agent_id: str = DEFAULT_AGENT_ID,
    model_config_id: str | None = None,
    api_url: str | None = None,
    poll_interval_sec: float = POLL_INTERVAL_SEC,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HandaCliResult:
  try:
    async with httpx.AsyncClient(
        base_url=_base_url(api_url),
        timeout=30.0,
        transport=transport,
    ) as client:
      project_id = await _resolve_project_id_by_name(client, project_name)
      turn = await _submit_turn(
          client,
          project_id=project_id,
          prompt=prompt,
          session_id=session_id,
          agent_id=agent_id,
          model_config_id=model_config_id,
      )
      turn = await _poll_turn(
          client,
          str(turn["id"]),
          poll_interval_sec=poll_interval_sec,
      )
  except HandaCliApiError as exc:
    error = exc.error
    if error.type == "SessionNotFound" and session_id:
      error = HandaCliError(
          type="SessionNotFound",
          message=f"Session not found: {session_id}",
      )
    return HandaCliResult(
        ok=False,
        status="failed",
        session_id=session_id,
        error=error,
    )
  result = _result_from_turn(turn)
  if result.session_id is None:
    result.session_id = session_id
  return result


async def _resolve_project_name_for_path_async(
    root_path: str,
    *,
    api_url: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> str:
  async with httpx.AsyncClient(
      base_url=_base_url(api_url),
      timeout=30.0,
      transport=transport,
  ) as client:
    _, name = await _resolve_project_id_by_path(client, root_path)
    return name


def resolve_project_name_for_path(
    path: str | Path,
    *,
    api_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
  """Register-or-find a project by filesystem path; return its Handa name.

  For path-based clients (reverse_spec_cli) that must convert a directory into
  the project name the name-only CLI surface expects. Raises HandaCliApiError if
  the web API is unreachable or returns an error.
  """
  root_path = str(Path(path).expanduser().resolve())
  return asyncio.run(
      _resolve_project_name_for_path_async(
          root_path, api_url=api_url, transport=transport
      )
  )


def _build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
      prog="handacli",
      description="Run Handa once from the command line via the Handa web API.",
  )
  parser.add_argument(
      "--project",
      required=True,
      help="Project name as shown in the Handa sidebar (must be registered).",
  )
  parser.add_argument(
      "--prompt",
      help="Prompt text to send. If omitted, handacli reads it from stdin.",
  )
  parser.add_argument("--session")
  parser.add_argument("--agent", default=DEFAULT_AGENT_ID)
  parser.add_argument("--model-config", dest="model_config_id")
  parser.add_argument(
      "--api-url",
      dest="api_url",
      help=f"Web API base URL (default $HANDA_WEB_API_URL or {DEFAULT_API_URL}).",
  )
  parser.add_argument(
      "--output-format",
      dest="output_format",
      choices=("text", "json"),
      default="text",
      help=(
          "Output format (default: text). 'json' emits a stable HandaCliResult "
          "object on stdout."
      ),
  )
  parser.add_argument(
      "--json",
      action="store_true",
      dest="json_output",
      help="Alias for --output-format json.",
  )
  return parser


def _read_prompt(prompt_arg: str | None) -> str | None:
  """Return the prompt from --prompt, falling back to piped stdin."""
  if prompt_arg is not None:
    return prompt_arg
  if not sys.stdin.isatty():
    piped = sys.stdin.read()
    if piped.strip():
      return piped
  return None


def _print_json(result: HandaCliResult) -> None:
  print(result.model_dump_json(indent=2))


def _print_text(result: HandaCliResult) -> None:
  """Human-facing rendering: response on stdout, errors on stderr."""
  if result.ok:
    if result.response:
      print(result.response)
    elif result.status == "waiting_input":
      print(
          f"Session {result.session_id} is waiting for input; answer it in the "
          "Handa web UI or resume with --session.",
          file=sys.stderr,
      )
    return
  error = result.error
  message = f"{error.type}: {error.message}" if error else "Turn failed."
  print(message, file=sys.stderr)


async def _main_async(argv: list[str] | None = None) -> int:
  parser = _build_parser()
  args = parser.parse_args(argv)
  output_format = "json" if args.json_output else args.output_format

  project_name = args.project.strip()
  session_id = args.session.strip() if args.session and args.session.strip() else None
  prompt = _read_prompt(args.prompt)
  if prompt is None:
    parser.error("provide a prompt via --prompt or stdin.")

  try:
    result = await run_handa_cli(
        project_name=project_name,
        prompt=prompt,
        session_id=session_id,
        agent_id=args.agent,
        model_config_id=args.model_config_id,
        api_url=args.api_url,
    )
  except Exception as exc:  # noqa: BLE001 - CLI must return a structured result.
    result = HandaCliResult(
        ok=False,
        status="failed",
        session_id=session_id,
        error=HandaCliError(type=type(exc).__name__, message=str(exc)),
    )

  if output_format == "json":
    _print_json(result)
  else:
    _print_text(result)
  return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
  return asyncio.run(_main_async(argv))


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
