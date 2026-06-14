from __future__ import annotations

from typing import Any

from ..runtime import list_files as list_files_runtime
from ..runtime import read_file as read_file_runtime
from ..runtime import replace_in_file as replace_in_file_runtime
from ..runtime import search_code as search_code_runtime
from ..runtime import write_file as write_file_runtime


def list(
    path: str = ".",
    max_depth: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
  """List repository files grouped by directory.

  By default this returns every matching file. Pass max_files for an explicit
  preview; a truncated listing keeps the shallowest files and ends with a
  per-directory note saying where the omitted files live.
  """
  return list_files_runtime(path=path, max_depth=max_depth, max_files=max_files)


def search(query: str, path: str = ".") -> dict[str, Any]:
  """Search code with ripgrep.

  `query` is a ripgrep regex: escape literal punctuation such as `(` with a
  backslash, or it fails as a parse error.
  """
  return search_code_runtime(query=query, path=path)


def read(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
  """Read part of a file with line numbers.

  The response includes total_lines, so compare it with end_line to tell
  whether the file continues past the excerpt.
  """
  return read_file_runtime(path=path, start_line=start_line, end_line=end_line)


def write(path: str, content: str) -> dict[str, Any]:
  """Create or overwrite a file in the repo."""
  return write_file_runtime(path=path, content=content)


def replace(
    path: str,
    old_text: str,
    new_text: str,
    expected_replacements: int | None = None,
) -> dict[str, Any]:
  """Replace every occurrence of old_text in one file.

  Pass expected_replacements to abort without writing when the match count
  differs — e.g. 1 to guarantee a unique edit.
  """
  return replace_in_file_runtime(
      path=path,
      old_text=old_text,
      new_text=new_text,
      expected_replacements=expected_replacements,
  )
