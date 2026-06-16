from __future__ import annotations

import pytest

from src.tools.user_input import build_pending_request
from src.tools.user_input import validate_answers
from src.tools.user_input import validate_questions


def _question(**overrides):
  question = {
      "id": "approach",
      "prompt": "Which approach?",
      "options": [
          {"label": "A (recommended)", "description": "fast"},
          {"label": "B"},
      ],
  }
  question.update(overrides)
  return question


def test_validate_questions_normalizes_defaults():
  normalized = validate_questions([_question()])
  assert normalized == [
      {
          "id": "approach",
          "prompt": "Which approach?",
          "options": [
              {"label": "A (recommended)", "description": "fast"},
              {"label": "B"},
          ],
          "multi_select": False,
          "allow_free_text": True,
      }
  ]


def test_validate_questions_accepts_string_options_and_multi_select():
  normalized = validate_questions(
      [_question(options=["A", "B", "C"], multi_select=True)]
  )
  assert normalized[0]["options"] == [
      {"label": "A"},
      {"label": "B"},
      {"label": "C"},
  ]
  assert normalized[0]["multi_select"] is True


@pytest.mark.parametrize(
    "questions, message",
    [
        ([], "non-empty"),
        ([_question()] * 5, "at most 4"),
        ([_question(id="")], "id is required"),
        ([_question(), _question()], "duplicate question id"),
        ([_question(prompt=" ")], "prompt is required"),
        ([_question(options=["A"])], "2 to 4"),
        ([_question(options=["A", "B", "C", "D", "E"])], "2 to 4"),
        ([_question(options=["A", "A"])], "duplicate option label"),
    ],
)
def test_validate_questions_rejects_invalid_payloads(questions, message):
  with pytest.raises(ValueError, match=message):
    validate_questions(questions)


def test_validate_answers_single_select_and_free_text():
  questions = validate_questions([_question()])
  response = validate_answers(
      questions,
      {"answers": [{"id": "approach", "selected": ["B"], "free_text": "use C"}]},
  )
  assert response == {
      "answers": [{"id": "approach", "selected": ["B"], "free_text": "use C"}]
  }


def test_validate_answers_multi_select():
  questions = validate_questions(
      [_question(options=["A", "B", "C"], multi_select=True)]
  )
  response = validate_answers(
      questions,
      {"answers": [{"id": "approach", "selected": ["A", "C", "A"]}]},
  )
  assert response["answers"][0]["selected"] == ["A", "C"]


def test_validate_answers_cancelled():
  questions = validate_questions([_question()])
  assert validate_answers(questions, {"cancelled": True}) == {"cancelled": True}


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"answers": [{"id": "other", "selected": ["B"]}]}, "does not match"),
        ({"answers": []}, "missing answers"),
        ({"answers": [{"id": "approach", "selected": ["Z"]}]}, "unknown option"),
        (
            {"answers": [{"id": "approach", "selected": ["A (recommended)", "B"]}]},
            "single-select",
        ),
        ({"answers": [{"id": "approach", "selected": []}]}, "select an option"),
    ],
)
def test_validate_answers_rejects_invalid_payloads(payload, message):
  questions = validate_questions([_question()])
  with pytest.raises(ValueError, match=message):
    validate_answers(questions, payload)


def test_validate_answers_rejects_free_text_when_disallowed():
  questions = validate_questions([_question(allow_free_text=False)])
  with pytest.raises(ValueError, match="free_text is not allowed"):
    validate_answers(
        questions,
        {"answers": [{"id": "approach", "selected": ["B"], "free_text": "x"}]},
    )


def test_build_pending_request_includes_native_runtime():
  questions = validate_questions([_question()])
  pending = build_pending_request(
      runtime="native",
      questions=questions,
  )
  assert pending["runtime"] == "native"
  assert pending["tool_name"] == "request_user_input"
  assert "function_call_id" not in pending
  assert pending["request_id"].startswith("uireq_")
  assert pending["questions"] == questions


def test_build_pending_request_accepts_explicit_request_id():
  pending = build_pending_request(
      runtime="native",
      questions=validate_questions([_question()]),
      request_id="uireq_fixed",
  )
  assert pending["request_id"] == "uireq_fixed"
