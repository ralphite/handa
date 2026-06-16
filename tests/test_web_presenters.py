from __future__ import annotations

from types import SimpleNamespace

from src.api.presenters.artifact_presenter import present_artifact
from src.api.presenters.event_presenter import project_adk_event
from src.api.presenters.tool_summary import summarize_tool_response


def test_project_adk_event_summarizes_tool_call():
  event = SimpleNamespace(
      id="event-1",
      invocation_id="inv-1",
      author="handa",
      timestamp=1,
      partial=False,
      turn_complete=True,
      interrupted=False,
      error_code=None,
      error_message=None,
      actions=SimpleNamespace(artifact_delta={}),
      content=SimpleNamespace(
          parts=[
              SimpleNamespace(
                  text=None,
                  function_call=SimpleNamespace(
                      id="call-1",
                      name="commands_run",
                      args={"command": "pytest -q"},
                      partial_args=None,
                      will_continue=None,
                  ),
                  function_response=None,
              )
          ]
      ),
      is_final_response=lambda: False,
  )

  projected = project_adk_event(event)

  assert projected == [
      {
          "kind": "tool_call",
          "summary": "Ran pytest -q",
          "payload": {
              "id": "call-1",
              "name": "commands_run",
              "args": {"command": "pytest -q"},
              "partial_args": None,
              "will_continue": None,
          },
      }
  ]


def test_present_artifact_uses_user_facing_title_for_versioned_names():
  generic = present_artifact("security_analysis.v1.artifact.md")
  typed = present_artifact("testing_quality.v2.plan.md")

  assert generic.title == "security_analysis.md"
  assert generic.filename == "security_analysis.v1.artifact.md"
  assert generic.display_version == 1
  assert typed.title == "testing_quality.plan.md"
  assert typed.display_version == 2


def test_project_adk_event_does_not_project_user_text_as_agent_text():
  event = SimpleNamespace(
      id="event-1",
      invocation_id="inv-1",
      author="user",
      timestamp=1,
      partial=False,
      turn_complete=True,
      interrupted=False,
      error_code=None,
      error_message=None,
      actions=SimpleNamespace(artifact_delta={}),
      content=SimpleNamespace(
          parts=[
              SimpleNamespace(
                  text="Research storage.",
                  function_call=None,
                  function_response=None,
              )
          ]
      ),
      is_final_response=lambda: False,
  )

  projected = project_adk_event(event)

  assert projected == [
      {
          "kind": "adk_event",
          "summary": "ADK event",
          "payload": {
              "author": "user",
              "partial": False,
              "final": False,
          },
      }
  ]


def test_summarize_tool_response_keeps_successful_read_of_failed_task_as_ok():
  # A status read that succeeds in reporting a *failed* child task is still a
  # successful tool call: the nested task.status must not mark the read failed.
  response = {
      "ok": True,
      "found": True,
      "tool_status": "ok",
      "task_status": "failed",
      "task": {
          "id": "task_123",
          "status": "failed",
          "returncode": 1,
      },
  }

  assert summarize_tool_response("agents_get_run_status", response) == "Finished agents_get_run_status"


def test_summarize_tool_response_marks_top_level_failure_as_failed():
  # A genuine tool-call failure (the read itself could not complete) still reads
  # as failed via the top-level envelope.
  assert (
      summarize_tool_response("agents_get_run_status", {"found": False, "error": "unknown task_id"})
      == "Failed agents_get_run_status"
  )


def test_project_adk_event_keeps_successful_status_read_as_ok():
  event = SimpleNamespace(
      id="event-2",
      invocation_id="inv-1",
      author="handa",
      timestamp=2,
      partial=False,
      turn_complete=True,
      interrupted=False,
      error_code=None,
      error_message=None,
      actions=SimpleNamespace(artifact_delta={}),
      content=SimpleNamespace(
          parts=[
              SimpleNamespace(
                  text=None,
                  function_call=None,
                  function_response=SimpleNamespace(
                      id="call-1",
                      name="agents_get_run_status",
                      response={
                          "ok": True,
                          "found": True,
                          "task": {
                              "id": "task_123",
                              "status": "failed",
                              "returncode": 1,
                          },
                      },
                      will_continue=None,
                      scheduling=None,
                  ),
              )
          ]
      ),
      is_final_response=lambda: False,
  )

  projected = project_adk_event(event)

  assert projected[0]["kind"] == "tool_response"
  assert projected[0]["summary"] == "Finished agents_get_run_status"


def test_project_adk_event_drops_empty_partial_markers():
  event = SimpleNamespace(
      id="evt-empty",
      author="orca_adk",
      partial=True,
      content=SimpleNamespace(parts=[]),
      actions=SimpleNamespace(artifact_delta={}),
      is_final_response=lambda: False,
  )

  assert project_adk_event(event) == []


def test_project_runtime_event_drops_lifecycle_kinds():
  from src.api.presenters.runtime_event_presenter import project_runtime_event

  for runtime, kind in (
      ("langgraph", "langgraph.started"),
      ("langgraph", "langgraph.checkpoint"),
      ("native", "orca.started"),
      ("native", "browser.started"),
      ("native", "browser.history_boundary"),
      ("native", "ralph.started"),
  ):
    event = {"id": "evt-1", "kind": kind, "summary": "noise", "payload": {}}
    assert project_runtime_event(event, runtime=runtime) == []


_QUOTA_ERROR_MESSAGE = """
On how to mitigate this issue, please refer to:

https://google.github.io/adk-docs/agents/models/google-gemini/#error-code-429-resource_exhausted


429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details.', 'status': 'RESOURCE_EXHAUSTED'}}
"""


def test_summarize_error_prefers_status_line_and_drops_json_blob():
  from src.api.presenters.tool_summary import summarize_error

  assert (
      summarize_error("_ResourceExhaustedError", _QUOTA_ERROR_MESSAGE)
      == "429 RESOURCE_EXHAUSTED."
  )


def test_summarize_error_falls_back_to_code_then_fallback():
  from src.api.presenters.tool_summary import summarize_error

  assert summarize_error("_ServerError", None) == "_ServerError"
  assert summarize_error(None, None, fallback="Interrupted") == "Interrupted"
  long_line = "x" * 200
  assert summarize_error(None, long_line) == f"{'x' * 137}..."


def test_project_adk_event_uses_short_error_summary_with_full_payload():
  event = SimpleNamespace(
      id="evt-err",
      author="orca_adk",
      partial=False,
      interrupted=False,
      error_code="_ResourceExhaustedError",
      error_message=_QUOTA_ERROR_MESSAGE,
      content=SimpleNamespace(parts=[]),
      actions=SimpleNamespace(artifact_delta={}),
      is_final_response=lambda: True,
  )

  projected = project_adk_event(event)

  assert projected[-1]["kind"] == "error"
  assert projected[-1]["summary"] == "429 RESOURCE_EXHAUSTED."
  assert projected[-1]["payload"]["error_message"] == _QUOTA_ERROR_MESSAGE
  # Error steps share one field shape with the worker's exception step: both
  # `error_type` and `error_code` keys are always present (one may be null).
  assert projected[-1]["payload"]["error_code"] == "_ResourceExhaustedError"
  assert projected[-1]["payload"]["error_type"] is None


def test_project_runtime_event_uses_short_error_summary():
  from src.api.presenters.runtime_event_presenter import project_runtime_event

  event = {
      "id": "evt-err",
      "kind": "langgraph.error",
      "summary": _QUOTA_ERROR_MESSAGE,
      "payload": {"message": _QUOTA_ERROR_MESSAGE},
  }

  projected = project_runtime_event(event, runtime="langgraph")

  assert projected[0]["kind"] == "error"
  assert projected[0]["summary"] == "429 RESOURCE_EXHAUSTED."
