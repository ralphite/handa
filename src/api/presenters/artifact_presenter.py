from __future__ import annotations

from pathlib import Path
import re

from ..schemas import ArtifactSummary


_VERSIONED_RE = re.compile(
    r"^(?P<name>.+)\.v(?P<display_version>\d+)\.(?P<kind>[^.]+)\.(?P<ext>[^.]+)$"
)


def present_artifact(filename: str, mime_type: str | None = None) -> ArtifactSummary:
  match = _VERSIONED_RE.match(Path(filename).name)
  if match:
    display_version = int(match.group("display_version"))
    title = _display_title(
        match.group("name"),
        match.group("kind"),
        match.group("ext"),
    )
    return ArtifactSummary(
        id=filename,
        filename=filename,
        title=title,
        kind=match.group("kind"),
        filetype=match.group("ext"),
        version=display_version - 1,
        display_version=display_version,
        mime_type=mime_type,
    )

  parts = Path(filename).name.split(".")
  kind = parts[-2] if len(parts) >= 3 else "artifact"
  filetype = parts[-1] if len(parts) >= 2 else "txt"
  title = ".".join(parts[:-2]) if len(parts) >= 3 else Path(filename).stem
  if len(parts) >= 3:
    title = _display_title(title, kind, filetype)
  return ArtifactSummary(
      id=filename,
      filename=filename,
      title=title,
      kind=kind,
      filetype=filetype,
      mime_type=mime_type,
  )


def _display_title(name: str, kind: str, ext: str) -> str:
  if kind == "artifact":
    return f"{name}.{ext}"
  return f"{name}.{kind}.{ext}"
