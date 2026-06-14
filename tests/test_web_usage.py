from __future__ import annotations

from src.api.usage import summarize_token_usage


def test_summarize_token_usage_deduplicates_projected_adk_events():
  raw_event = {
      "id": "event-1",
      "usageMetadata": {
          "promptTokenCount": 100,
          "candidatesTokenCount": 10,
          "thoughtsTokenCount": 5,
          "totalTokenCount": 115,
      },
  }

  usage = summarize_token_usage(
      [
          {
              "id": "event-0",
              "raw_event": {
                  "id": "event-0",
                  "partial": True,
                  "usageMetadata": {
                      "promptTokenCount": 90,
                      "candidatesTokenCount": 5,
                      "totalTokenCount": 95,
                  },
              },
          },
          {"id": "event-1", "raw_event": raw_event},
          {"id": "event-1", "raw_event": raw_event},
          {
              "id": "event-2",
              "raw_event": {
                  "id": "event-2",
                  "usageMetadata": {
                      "promptTokenCount": 120,
                      "candidatesTokenCount": 20,
                      "totalTokenCount": 140,
                  },
              },
          },
      ]
  )

  assert usage.context_token_count == 120
  # Output includes thinking tokens (5), matching Gemini's billing convention
  # and Phoenix's completion = candidates + reasoning.
  assert usage.output_token_count == 35
  assert usage.total_token_count == 255
