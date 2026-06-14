from __future__ import annotations

from types import SimpleNamespace

from src.turn_worker import _DeltaAggregator


def _delta(text: str, *, event_id: str = "evt", calls: bool = False):
  part = SimpleNamespace(
      text=text or None,
      function_call=SimpleNamespace(id="c1", name="t", args={}, partial_args=None, will_continue=None) if calls else None,
      function_response=None,
  )
  return SimpleNamespace(
      id=event_id,
      author="orca_adk",
      partial=True,
      timestamp=1.0,
      content=SimpleNamespace(parts=[part]),
      actions=SimpleNamespace(artifact_delta={}),
      is_final_response=lambda: False,
  )


def _collect():
  written = []
  return written, _DeltaAggregator(written.append, flush_interval_seconds=999, flush_min_chars=10)


def test_aggregator_merges_chunks_without_losing_text():
  written, agg = _collect()
  agg.add(_delta("Hel", event_id="e1"))
  agg.add(_delta("lo ", event_id="e2"))
  agg.add(_delta("world!", event_id="e3"))  # crosses the 10-char threshold
  agg.flush()

  texts = ["".join(p["text"] for p in ev["content"]["parts"]) for ev in written]
  assert "".join(texts) == "Hello world!"
  assert written[0]["partial"] is True
  assert written[0]["id"] == "e3"


def test_aggregator_flush_on_final_keeps_remainder():
  written, agg = _collect()
  agg.add(_delta("tail", event_id="e9"))
  agg.flush()  # what on_event does when the final (non-partial) event arrives

  assert len(written) == 1
  assert written[0]["content"]["parts"][0]["text"] == "tail"


def test_aggregator_passes_structured_partials_through():
  written, agg = _collect()
  agg.add(_delta("buffered", event_id="e1"))
  structured = _delta("", event_id="e2", calls=True)
  agg.add(structured)

  # buffered text flushed first, then the structured event verbatim
  assert written[0]["content"]["parts"][0]["text"] == "buffered"
  assert written[1] is structured


def test_aggregator_ignores_empty_markers():
  written, agg = _collect()
  agg.add(_delta("", event_id="e0"))
  agg.flush()
  assert written == []
