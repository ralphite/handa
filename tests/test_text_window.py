from __future__ import annotations

from src.tools.text_window import DEFAULT_READ_MAX_CHARS
from src.tools.text_window import window_text


def test_window_text_returns_small_text_whole():
  out = window_text("# Plan\n\nRun pytest.")
  assert out["content"] == "# Plan\n\nRun pytest."
  assert out["char_count"] == len("# Plan\n\nRun pytest.")
  assert out["line_count"] == 3
  # A complete read advertises neither truncation nor a resume offset.
  assert "truncated" not in out
  assert "next_offset" not in out
  assert "offset" not in out


def test_window_text_bounds_large_text_and_reports_resume_offset():
  text = "x" * (DEFAULT_READ_MAX_CHARS + 500)
  out = window_text(text)
  assert len(out["content"]) == DEFAULT_READ_MAX_CHARS
  assert out["char_count"] == DEFAULT_READ_MAX_CHARS + 500
  assert out["truncated"] is True
  assert out["next_offset"] == DEFAULT_READ_MAX_CHARS


def test_window_text_metadata_only_omits_content():
  out = window_text("y" * 5000, metadata_only=True)
  assert "content" not in out
  assert out["char_count"] == 5000
  assert out["line_count"] == 1


def test_window_text_pages_with_offset_and_max_chars():
  text = "abcdefghij"
  out = window_text(text, offset=3, max_chars=4)
  assert out["content"] == "defg"
  assert out["offset"] == 3
  assert out["truncated"] is True
  assert out["next_offset"] == 7

  tail = window_text(text, offset=7, max_chars=4)
  assert tail["content"] == "hij"
  assert "truncated" not in tail  # reached the end
