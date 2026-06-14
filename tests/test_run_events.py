from __future__ import annotations

from types import SimpleNamespace

from src.run_events import extract_event_facts
from src.run_events import serialize_adk_event


def test_extract_event_facts_reads_text_tool_and_artifact_delta():
  event = SimpleNamespace(
      id="event-1",
      invocation_id="inv-1",
      author="handa",
      timestamp=1.5,
      partial=True,
      turn_complete=False,
      interrupted=False,
      error_code=None,
      error_message=None,
      actions=SimpleNamespace(artifact_delta={"plan.md": 0}),
      content=SimpleNamespace(
          parts=[
              SimpleNamespace(text="hello", function_call=None, function_response=None),
              SimpleNamespace(
                  text=None,
                  function_call=SimpleNamespace(
                      id="call-1",
                      name="files_read",
                      args={"path": "QA.md"},
                      partial_args=None,
                      will_continue=None,
                  ),
                  function_response=None,
              ),
          ]
      ),
      is_final_response=lambda: False,
  )

  facts = extract_event_facts(event)

  assert facts.text == "hello"
  assert facts.partial is True
  assert facts.function_calls[0].name == "files_read"
  assert facts.function_calls[0].args == {"path": "QA.md"}
  assert facts.artifact_delta == {"plan.md": 0}


def test_extract_event_facts_reads_usage_metadata_token_counts():
  event = SimpleNamespace(
      id="event-1",
      invocation_id="inv-1",
      content=SimpleNamespace(parts=[]),
      actions=SimpleNamespace(artifact_delta={}),
      usageMetadata={
          "promptTokenCount": 1234,
          "candidatesTokenCount": 56,
          "totalTokenCount": 2000,
      },
      is_final_response=lambda: False,
  )

  facts = extract_event_facts(event)

  assert facts.input_token_count == 1234
  assert facts.output_token_count == 56


def test_serialize_adk_event_handles_plain_objects():
  event = SimpleNamespace(id="event-1", nested=SimpleNamespace(value=1))

  assert serialize_adk_event(event) == {"id": "event-1", "nested": {"value": 1}}


def _real_adk_event():
  from google.adk.events.event import Event
  from google.adk.events.event_actions import EventActions
  from google.genai import types

  return Event(
      invocation_id="inv-1",
      author="handa",
      content=types.Content(
          role="model",
          parts=[
              types.Part(text="hello"),
              types.Part.from_function_call(name="files_read", args={"path": "QA.md"}),
              types.Part.from_function_response(name="files_read", response={"ok": True}),
          ],
      ),
      usage_metadata=types.GenerateContentResponseUsageMetadata(
          prompt_token_count=10,
          candidates_token_count=5,
          total_token_count=15,
      ),
      actions=EventActions(artifact_delta={"plan.md": 1}),
  )


def test_extract_event_facts_matches_between_object_and_serialized_dict():
  event = _real_adk_event()

  assert extract_event_facts(serialize_adk_event(event)) == extract_event_facts(event)


def test_serialize_adk_event_persists_finality_for_dict_facts():
  from google.adk.events.event import Event
  from google.genai import types

  event = Event(
      invocation_id="inv-1",
      author="handa",
      content=types.Content(role="model", parts=[types.Part(text="done")]),
  )
  assert event.is_final_response() is True

  raw = serialize_adk_event(event)

  assert raw["is_final_response"] is True
  assert extract_event_facts(raw).final is True


def test_serialize_adk_event_returns_dict_input_unchanged():
  raw = {"id": "event-1", "kind": "web.turn_cancelled"}

  assert serialize_adk_event(raw) is raw
