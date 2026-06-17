from __future__ import annotations

from types import SimpleNamespace

from src.api.context_usage_breakdown import _llm_response_thought_tokens
from src.api.context_usage_breakdown import build_static_context_usage_breakdown
from src.api.context_usage_breakdown import estimate_context_usage_breakdown


def test_context_usage_breakdown_splits_messages_and_llm_responses():
  result = estimate_context_usage_breakdown(
      {
          "system_instruction": "abcd",
          "system_tools": "abcd",
          "skills": "abcd",
          "user_messages": "abcd",
          "tool_call_responses": "abcd",
          "llm_response_text": "abcd",
          "llm_response_tool_call_request": "abcd",
          "project_config": "abcd",
      },
      target_token_count=20,
  )

  assert [item["id"] for item in result] == [
      "instruction",
      "system_tools",
      "user_messages",
      "tool_call_responses",
      "llm_responses",
      "skills",
  ]
  assert [item["label"] for item in result] == [
      "Instruction",
      "System tools",
      "User Messages",
      "Tool Call Responses",
      "LLM Responses",
      "Skills",
  ]
  assert [item["token_count"] for item in result] == [2, 1, 3, 5, 8, 1]
  assert sum(item["token_count"] for item in result) == 20
  assert result[3]["percent"] == 25.0
  assert result[0]["children"] == [
      {"id": "system_instruction", "label": "System", "token_count": 1, "percent": 5.0},
      {"id": "project_config", "label": "Project", "token_count": 1, "percent": 5.0},
  ]
  assert result[4]["children"] == [
      {"id": "llm_response_text", "label": "Text", "token_count": 3, "percent": 15.0},
      {
          "id": "llm_response_tool_call_request",
          "label": "Tool Call Request",
          "token_count": 5,
          "percent": 25.0,
      },
  ]
  assert "MCP" not in repr(result)


def test_context_usage_static_sources_do_not_change_with_dynamic_messages():
  first = estimate_context_usage_breakdown(
      {
          "system_instruction": "abcdefgh",
          "system_tools": "abcd",
          "skills": "abcd",
          "user_messages": "short",
          "tool_call_responses": "short",
          "llm_response_text": "short",
          "project_config": "abcd",
      },
      target_token_count=30,
  )
  second = estimate_context_usage_breakdown(
      {
          "system_instruction": "abcdefgh",
          "system_tools": "abcd",
          "skills": "abcd",
          "user_messages": "long message" * 100,
          "tool_call_responses": "long tool result" * 100,
          "llm_response_text": "long response" * 100,
          "project_config": "abcd",
      },
      target_token_count=30,
  )

  dynamic_ids = {"user_messages", "tool_call_responses", "llm_responses"}
  assert [item["token_count"] for item in first if item["id"] not in dynamic_ids] == [
      item["token_count"] for item in second if item["id"] not in dynamic_ids
  ]
  assert sum(item["token_count"] for item in first) == 30
  assert sum(item["token_count"] for item in second) == 30


def test_context_usage_scales_static_sources_when_they_exceed_runtime_total():
  result = estimate_context_usage_breakdown(
      {
          "system_instruction": "x" * 400,
          "system_tools": "x" * 400,
          "skills": "x" * 400,
          "user_messages": "ignored residual",
          "tool_call_responses": "ignored residual",
          "llm_response_text": "ignored residual",
          "project_config": "x" * 400,
      },
      target_token_count=50,
  )

  assert sum(item["token_count"] for item in result) == 50
  assert next(item for item in result if item["id"] == "user_messages")["token_count"] == 0
  assert next(item for item in result if item["id"] == "tool_call_responses")["token_count"] == 0
  assert next(item for item in result if item["id"] == "llm_responses")["token_count"] == 0
  instruction = next(item for item in result if item["id"] == "instruction")
  assert sum(child["token_count"] for child in instruction["children"]) == instruction["token_count"]


def test_context_usage_breakdown_assigns_runtime_total_to_user_messages_when_sources_are_missing():
  result = estimate_context_usage_breakdown({}, target_token_count=50)

  assert [item["id"] for item in result] == [
      "instruction",
      "system_tools",
      "user_messages",
      "tool_call_responses",
      "llm_responses",
      "skills",
  ]
  assert [item["token_count"] for item in result] == [0, 0, 50, 0, 0, 0]


def test_context_usage_breakdown_keeps_llm_response_children_without_runtime_total():
  result = estimate_context_usage_breakdown(
      {
          "user_messages": "abcd",
          "llm_response_thought": 3,
          "llm_response_text": "abcd",
          "llm_response_tool_call_request": "abcd",
      },
      target_token_count=0,
  )

  llm_responses = next(item for item in result if item["id"] == "llm_responses")
  assert llm_responses["token_count"] == 6
  assert llm_responses["children"] == [
      {"id": "llm_response_thought", "label": "Thought", "token_count": 3, "percent": 42.9},
      {"id": "llm_response_text", "label": "Text", "token_count": 1, "percent": 14.3},
      {
          "id": "llm_response_tool_call_request",
          "label": "Tool Call Request",
          "token_count": 2,
          "percent": 28.6,
      },
  ]


def test_context_usage_residual_attributes_most_space_to_tool_call_responses():
  # 4000 chars of tool results vs a short question and a short answer: the
  # residual must land mostly on tool results, not on LLM text.
  result = estimate_context_usage_breakdown(
      {
          "system_instruction": "s" * 40,
          "user_messages": "how do I fix the login bug?",
          "tool_call_responses": "x" * 4000,
          "llm_response_text": "Fixed by updating the session check.",
      },
      target_token_count=1500,
  )

  by_id = {item["id"]: item["token_count"] for item in result}
  assert by_id["tool_call_responses"] > by_id["llm_responses"] * 10
  assert by_id["tool_call_responses"] > by_id["user_messages"] * 10
  assert sum(by_id.values()) == 1500


def test_thought_tokens_count_all_replayed_turns_except_final_response(tmp_path):
  # Thought signatures replay thinking into every later prompt; only the final response's
  # thoughts are not part of the latest request's prompt yet.
  def usage_event(invocation_id: str, thoughts: int) -> dict:
    return {
        "invocationId": invocation_id,
        "usageMetadata": {"thoughtsTokenCount": thoughts, "promptTokenCount": 100},
    }

  from src.storage.runtime_event_store import RuntimeEventStore

  session = SimpleNamespace(id="s1")
  storage_root = tmp_path / ".handa"
  store = RuntimeEventStore(storage_root)
  for event in (
      usage_event("inv-1", 4000),
      usage_event("inv-1", 2000),
      usage_event("inv-2", 300),
      usage_event("inv-2", 200),
  ):
    store.append(session_id=session.id, runtime="native", event=event)
  ctx = SimpleNamespace(settings=SimpleNamespace(storage_root=storage_root))

  assert _llm_response_thought_tokens(ctx, session, "native") == 6300


def test_exact_thought_tokens_survive_residual_scaling():
  # Thought counts come straight from usage metadata; scaling must stretch
  # only the character-estimated sources around them.
  result = estimate_context_usage_breakdown(
      {
          "system_instruction": "s" * 400,  # 100 static tokens
          "user_messages": "u" * 400,  # 100 estimated
          "tool_call_responses": "t" * 700,  # 200 estimated
          "llm_response_thought": 500,  # exact
          "llm_response_text": "x" * 400,  # 100 estimated
      },
      target_token_count=1400,
  )

  by_id = {item["id"]: item for item in result}
  llm_children = {child["id"]: child["token_count"] for child in by_id["llm_responses"]["children"]}
  assert llm_children["llm_response_thought"] == 500
  # static 100 + exact 500 leave 800 for the 400 estimated tokens: 2x scale.
  assert by_id["user_messages"]["token_count"] == 200
  assert by_id["tool_call_responses"]["token_count"] == 400
  assert llm_children["llm_response_text"] == 200
  assert by_id["llm_responses"]["token_count"] == 700
  assert sum(item["token_count"] for item in result) == 1400


def test_static_context_usage_breakdown_previews_orca():
  result = build_static_context_usage_breakdown(
      agent_id="orca",
      agent_runtime="native",
      project_root=None,
  )

  by_id = {item["id"]: item for item in result}
  assert by_id["instruction"]["token_count"] > 0
  assert by_id["skills"]["token_count"] > 0
  assert by_id["user_messages"]["token_count"] == 0
  assert by_id["tool_call_responses"]["token_count"] == 0
  assert by_id["llm_responses"]["token_count"] == 0
  total = sum(item["token_count"] for item in result)
  assert sum(item["percent"] for item in result) > 99.0
  assert total > 0


def test_static_context_usage_breakdown_previews_browser():
  result = build_static_context_usage_breakdown(
      agent_id="browser",
      agent_runtime="native",
      project_root=None,
  )

  by_id = {item["id"]: item for item in result}
  assert by_id["instruction"]["token_count"] > 0
  assert by_id["system_tools"]["token_count"] > 0
  assert by_id["skills"]["token_count"] == 0
  assert by_id["user_messages"]["token_count"] == 0
  assert by_id["tool_call_responses"]["token_count"] == 0
  assert by_id["llm_responses"]["token_count"] == 0


def test_static_context_usage_breakdown_previews_ralph():
  result = build_static_context_usage_breakdown(
      agent_id="ralph",
      agent_runtime="native",
      project_root=None,
  )

  by_id = {item["id"]: item for item in result}
  assert by_id["instruction"]["token_count"] > 0
  assert by_id["system_tools"]["token_count"] == 0
  assert by_id["skills"]["token_count"] == 0
  assert by_id["user_messages"]["token_count"] == 0
  assert by_id["tool_call_responses"]["token_count"] == 0
  assert by_id["llm_responses"]["token_count"] == 0
