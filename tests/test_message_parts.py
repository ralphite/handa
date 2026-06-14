from __future__ import annotations

from src.message_parts import build_message_parts


def test_image_attachment_becomes_inline_data(tmp_path):
  image_path = tmp_path / "image.png"
  image_path.write_bytes(b"\x89PNG\r\n\x1a\nPIX")

  parts = build_message_parts(
      "look at this",
      [
          {
              "storage_path": str(image_path),
              "mime_type": "image/png",
              "kind": "image",
              "filename": "image.png",
          }
      ],
  )

  assert parts[0].text == "look at this"
  assert parts[1].inline_data is not None
  assert parts[1].inline_data.mime_type == "image/png"
  assert parts[1].inline_data.data == b"\x89PNG\r\n\x1a\nPIX"


def test_text_attachment_is_inlined_as_text(tmp_path):
  notes = tmp_path / "notes.txt"
  notes.write_text("hello world", encoding="utf-8")

  parts = build_message_parts(
      "",
      [
          {
              "storage_path": str(notes),
              "mime_type": "text/plain",
              "kind": "text",
              "filename": "notes.txt",
          }
      ],
  )

  # No input text -> only the inlined text attachment part.
  assert len(parts) == 1
  assert "--- notes.txt ---" in parts[0].text
  assert "hello world" in parts[0].text


def test_missing_attachment_is_skipped(tmp_path):
  parts = build_message_parts(
      "hi",
      [
          {
              "storage_path": str(tmp_path / "gone.png"),
              "mime_type": "image/png",
              "kind": "image",
              "filename": "gone.png",
          }
      ],
  )

  assert len(parts) == 1
  assert parts[0].text == "hi"


def test_empty_input_yields_a_part():
  parts = build_message_parts("", None)
  assert len(parts) == 1
  assert parts[0].text == ""
