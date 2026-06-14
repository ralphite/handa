from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import List

from ..storage.paths import resolve_storage_root

SYSTEM_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILLS_DIR = resolve_storage_root() / "skills"
SKILL_DOC = "SKILL.md"


def list() -> dict[str, Any]:
  """List system skills and user skills stored under the Handa storage root."""
  skills = []
  for source, skill_dir in _iter_skill_dirs():
    skill_file = skill_dir / SKILL_DOC
    metadata, lines = _read_skill_metadata(skill_file)
    skills.append(
        {
            "name": skill_dir.name,
            "skill_name": _frontmatter_name(skill_dir.name, metadata),
            "title": _skill_title(skill_dir.name, metadata, lines),
            "description": metadata.get("description", ""),
            "source": source,
            "path": str(skill_file.resolve()),
        }
    )
  return {"skills": skills}


def describe(name: str) -> dict[str, Any]:
  """Return metadata for one skill without loading the full skill body."""
  skill_path = _resolve_skill_path(name)
  if skill_path is None:
    return {"success": False, "error": f"unknown skill: {name}"}
  metadata, lines = _read_skill_metadata(skill_path)
  return {
      "success": True,
      "name": skill_path.parent.name,
      "skill_name": _frontmatter_name(skill_path.parent.name, metadata),
      "title": _skill_title(skill_path.parent.name, metadata, lines),
      "description": metadata.get("description", ""),
      "source": _skill_source(skill_path),
      "path": str(skill_path.resolve()),
  }


def read(name: str) -> dict[str, Any]:
  """Read one skill by directory name or frontmatter name."""
  skill_path = _resolve_skill_path(name)
  if skill_path is None:
    return {"success": False, "error": f"unknown skill: {name}"}
  content = skill_path.read_text(encoding="utf-8")
  metadata, lines = _parse_skill_doc(content)
  return {
      "success": True,
      "name": skill_path.parent.name,
      "skill_name": _frontmatter_name(skill_path.parent.name, metadata),
      "title": _skill_title(skill_path.parent.name, metadata, lines),
      "description": metadata.get("description", ""),
      "source": _skill_source(skill_path),
      "path": str(skill_path.resolve()),
      "content": content,
  }


def _iter_skill_dirs() -> List[tuple[str, Path]]:
  skill_dirs: list[tuple[str, Path]] = []
  for source, root in _skill_roots():
    if not root.exists():
      continue
    skill_dirs.extend(
        (source, path)
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / SKILL_DOC).is_file()
    )
  return skill_dirs


def _resolve_skill_path(name: str) -> Path | None:
  normalized = name.strip()
  if not _is_safe_skill_name(normalized):
    return None

  for _, root in _skill_roots():
    direct_path = root / normalized / SKILL_DOC
    if direct_path.is_file():
      return direct_path

  for _, skill_dir in _iter_skill_dirs():
    skill_path = skill_dir / SKILL_DOC
    metadata, _ = _read_skill_metadata(skill_path)
    if metadata.get("name") == normalized:
      return skill_path
  return None


def _skill_roots() -> list[tuple[str, Path]]:
  return [
      ("system", SYSTEM_SKILLS_DIR),
      ("user", SKILLS_DIR),
  ]


def _skill_source(skill_path: Path) -> str:
  resolved = skill_path.resolve()
  for source, root in _skill_roots():
    try:
      resolved.relative_to(root.resolve())
      return source
    except ValueError:
      continue
  return "unknown"


def _is_safe_skill_name(name: str) -> bool:
  return bool(name) and name not in {".", ".."} and "/" not in name and "\\" not in name


def _read_skill_metadata(skill_file: Path) -> tuple[dict[str, str], List[str]]:
  return _parse_skill_doc(skill_file.read_text(encoding="utf-8"))


def _parse_skill_doc(content: str) -> tuple[dict[str, str], List[str]]:
  lines = content.splitlines()
  metadata: dict[str, str] = {}
  if not lines or lines[0].strip() != "---":
    return metadata, lines

  for line in lines[1:]:
    stripped = line.strip()
    if stripped == "---":
      break
    if not stripped or stripped.startswith("#") or ":" not in stripped:
      continue
    key, value = stripped.split(":", 1)
    metadata[key.strip()] = _strip_frontmatter_quotes(value.strip())
  return metadata, lines


def _strip_frontmatter_quotes(value: str) -> str:
  if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
    return value[1:-1]
  return value


def _skill_title(
    skill_id: str,
    metadata: dict[str, str],
    lines: List[str],
) -> str:
  if metadata.get("name"):
    return metadata["name"]
  for line in lines:
    stripped = line.strip()
    if stripped.startswith("# "):
      return stripped.lstrip("# ").strip()
  return skill_id


def _frontmatter_name(skill_id: str, metadata: dict[str, str]) -> str:
  return metadata.get("name") or skill_id
