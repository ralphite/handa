from __future__ import annotations

from src.agents.handa_adk.tools import get_tool_registry
from src.progress import normalize_progress_items


def test_progress_normalization_preserves_existing_timestamp_when_unchanged():
  existing = [
      {
          "id": "plan",
          "title": "Create plan",
          "status": "done",
          "updated_at": "2026-06-07T10:00:00+00:00",
          "source_turn_id": "turn-1",
      }
  ]

  result = normalize_progress_items(
      [
          {
              "id": "plan",
              "title": "Create plan",
              "status": "completed",
          }
      ],
      existing_items=existing,
      timestamp="2026-06-07T11:00:00+00:00",
      source_turn_id="turn-2",
  )

  assert result == [
      {
          "id": "plan",
          "title": "Create plan",
          "status": "done",
          "detail": None,
          "updated_at": "2026-06-07T10:00:00+00:00",
          "source_turn_id": "turn-1",
      }
  ]


def test_progress_tool_is_registered_for_adk_agents():
  registry = get_tool_registry()

  assert "progress_update" in registry
  assert registry["progress_update"].namespace == "progress"
