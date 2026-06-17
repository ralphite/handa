from __future__ import annotations

from src.goal_judge import parse_goal_judge_response


def test_parse_goal_judge_response_accepts_json_object():
  verdict = parse_goal_judge_response(
      '{"status":"achieved","reason":"Tests passed.","citations":["evt_1"]}'
  )

  assert verdict.status == "achieved"
  assert verdict.reason == "Tests passed."
  assert verdict.citations == ["evt_1"]


def test_parse_goal_judge_response_falls_back_to_continue_for_invalid_json():
  verdict = parse_goal_judge_response("not json")

  assert verdict.status == "continue"
  assert "invalid JSON" in verdict.reason
  assert verdict.next_request


def test_parse_goal_judge_response_extracts_embedded_json():
  verdict = parse_goal_judge_response(
      'Verdict:\n{"status":"continue","reason":"Missing proof.","next_request":"Run QA."}'
  )

  assert verdict.status == "continue"
  assert verdict.reason == "Missing proof."
  assert verdict.next_request == "Run QA."
