from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import re
from typing import Any
from typing import Optional
from typing import Union

from google.adk.artifacts.base_artifact_service import ArtifactVersion
from google.adk.artifacts.base_artifact_service import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ensure_part
from google.genai import types

from .file_io import atomic_write_bytes
from .file_io import atomic_write_text
from .file_io import file_lock
from .paths import artifacts_dir
from .paths import migrate_legacy_session_storage
from .paths import resolve_storage_root


_VERSIONED_RE = re.compile(
    r"^(?P<name>.+)\.v(?P<version>\d+)\.(?P<type>[^.]+)\.(?P<ext>[^.]+)$"
)
_UNTYPED_VERSIONED_RE = re.compile(
    r"^(?P<name>.+)\.v(?P<version>\d+)\.(?P<ext>[^.]+)$"
)


class HandaArtifactService(BaseArtifactService):
  """Flat file ArtifactService stored in each session's `artifacts/` folder.

  Must subclass ADK's `BaseArtifactService`: `InvocationContext` is a pydantic
  model whose `artifact_service` field is validated as `BaseArtifactService`, so
  ADK rejects a non-subclass at runtime on every agent run.
  """

  def __init__(self, root: str | None = None):
    self.root = str(resolve_storage_root(root))
    migrate_legacy_session_storage(self.root)

  async def save_text_artifact(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      text: str,
      session_id: Optional[str] = None,
  ) -> int:
    """Save a plain-text artifact without the caller touching genai types."""
    return await self.save_artifact(
        app_name=app_name,
        user_id=user_id,
        filename=filename,
        artifact=types.Part.from_text(text=text),
        session_id=session_id,
    )

  async def save_artifact(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      artifact: Union[types.Part, dict[str, Any]],
      session_id: Optional[str] = None,
      custom_metadata: Optional[dict[str, Any]] = None,
  ) -> int:
    if session_id is None:
      raise ValueError("Handa artifacts must be scoped to a session.")
    return await asyncio.to_thread(
        self._save_artifact_sync,
        filename,
        artifact,
        session_id,
        custom_metadata,
    )

  def _save_artifact_sync(
      self,
      filename: str,
      artifact: Union[types.Part, dict[str, Any]],
      session_id: str,
      custom_metadata: Optional[dict[str, Any]],
  ) -> int:
    artifact = ensure_part(artifact)
    directory = artifacts_dir(self.root, session_id)
    directory.mkdir(parents=True, exist_ok=True)

    parsed = _parse_artifact_filename(filename)
    with file_lock(_artifact_lock_path(directory, parsed)):
      next_number = _next_version_number(
          directory,
          parsed.name,
          parsed.kind,
          parsed.ext,
      )
      stored_name = parsed.format(next_number)
      payload_path = directory / stored_name

      metadata = {
          "source_filename": filename,
          "stored_filename": stored_name,
          "name": parsed.name,
          "type": parsed.kind,
          "filetype": parsed.ext,
          "version": next_number - 1,
          "display_version": next_number,
          "custom_metadata": custom_metadata or {},
      }

      if artifact.inline_data and artifact.inline_data.data is not None:
        atomic_write_bytes(payload_path, artifact.inline_data.data)
        metadata["mime_type"] = artifact.inline_data.mime_type
      elif artifact.text is not None:
        atomic_write_text(payload_path, artifact.text)
        metadata["mime_type"] = None
      else:
        raise ValueError("Artifact must contain text or inline_data.")

      atomic_write_text(
          directory / f"{stored_name}.metadata.json",
          json.dumps(metadata, indent=2, ensure_ascii=True) + "\n",
      )
      return next_number - 1

  async def load_artifact(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      session_id: Optional[str] = None,
      version: Optional[int] = None,
  ) -> Optional[types.Part]:
    if session_id is None:
      return None
    return await asyncio.to_thread(
        self._load_artifact_sync,
        filename,
        session_id,
        version,
    )

  def _load_artifact_sync(
      self,
      filename: str,
      session_id: str,
      version: Optional[int],
  ) -> Optional[types.Part]:
    path = _resolve_artifact_path(artifacts_dir(self.root, session_id), filename, version)
    if path is None or not path.exists():
      return None
    metadata = _read_metadata(path)
    mime_type = metadata.get("mime_type")
    if mime_type:
      return types.Part(
          inline_data=types.Blob(mime_type=mime_type, data=path.read_bytes())
      )
    return types.Part(text=path.read_text(encoding="utf-8"))

  async def list_artifact_keys(
      self,
      *,
      app_name: str,
      user_id: str,
      session_id: Optional[str] = None,
  ) -> list[str]:
    if session_id is None:
      return []
    directory = artifacts_dir(self.root, session_id)
    if not directory.exists():
      return []
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and not path.name.endswith(".metadata.json")
    )

  async def delete_artifact(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      session_id: Optional[str] = None,
  ) -> None:
    if session_id is None:
      return
    directory = artifacts_dir(self.root, session_id)
    parsed = _parse_artifact_filename(filename)
    for path in directory.glob(f"{parsed.name}.v*.{parsed.kind}.{parsed.ext}"):
      path.unlink(missing_ok=True)
      (directory / f"{path.name}.metadata.json").unlink(missing_ok=True)

  async def list_versions(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      session_id: Optional[str] = None,
  ) -> list[int]:
    if session_id is None:
      return []
    parsed = _parse_artifact_filename(filename)
    return [
        number - 1
        for number in _existing_version_numbers(
            artifacts_dir(self.root, session_id),
            parsed.name,
            parsed.kind,
            parsed.ext,
        )
    ]

  async def list_artifact_versions(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      session_id: Optional[str] = None,
  ) -> list[ArtifactVersion]:
    versions = await self.list_versions(
        app_name=app_name,
        user_id=user_id,
        filename=filename,
        session_id=session_id,
    )
    result = []
    for version in versions:
      artifact_version = await self.get_artifact_version(
          app_name=app_name,
          user_id=user_id,
          filename=filename,
          session_id=session_id,
          version=version,
      )
      if artifact_version is not None:
        result.append(artifact_version)
    return result

  async def get_artifact_version(
      self,
      *,
      app_name: str,
      user_id: str,
      filename: str,
      session_id: Optional[str] = None,
      version: Optional[int] = None,
  ) -> Optional[ArtifactVersion]:
    if session_id is None:
      return None
    directory = artifacts_dir(self.root, session_id)
    path = _resolve_artifact_path(directory, filename, version)
    if path is None or not path.exists():
      return None
    metadata = _read_metadata(path)
    return ArtifactVersion(
        version=int(metadata.get("version", 0)),
        canonical_uri=path.resolve().as_uri(),
        custom_metadata=metadata.get("custom_metadata", {}),
        mime_type=metadata.get("mime_type"),
    )


class _ParsedArtifactName:
  def __init__(self, name: str, kind: str, ext: str):
    self.name = _safe_name(name)
    self.kind = _safe_name(kind)
    self.ext = _safe_name(ext)

  def format(self, version_number: int) -> str:
    return f"{self.name}.v{version_number}.{self.kind}.{self.ext}"

  def display(self) -> str:
    if self.kind == "artifact":
      return f"{self.name}.{self.ext}"
    return f"{self.name}.{self.kind}.{self.ext}"


def _parse_artifact_filename(filename: str) -> _ParsedArtifactName:
  name = Path(filename).name
  match = _VERSIONED_RE.match(name)
  if match:
    return _ParsedArtifactName(
        match.group("name"),
        match.group("type"),
        match.group("ext"),
    )
  match = _UNTYPED_VERSIONED_RE.match(name)
  if match:
    return _ParsedArtifactName(
        match.group("name"),
        "artifact",
        match.group("ext"),
    )

  parts = name.split(".")
  if len(parts) >= 3:
    return _ParsedArtifactName(
        ".".join(parts[:-2]),
        parts[-2],
        parts[-1],
    )
  if len(parts) == 2:
    return _ParsedArtifactName(parts[0], "artifact", parts[1])
  return _ParsedArtifactName(name, "artifact", "txt")


def artifact_display_filename(filename: str) -> str:
  return _parse_artifact_filename(filename).display()


def artifact_stored_filename(filename: str, version: int) -> str:
  return _parse_artifact_filename(filename).format(version + 1)


def _safe_name(value: str) -> str:
  cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_")
  if not cleaned:
    raise ValueError("artifact name segment must not be empty")
  return cleaned


def _existing_version_numbers(
    directory: Path,
    name: str,
    kind: str,
    ext: str,
) -> list[int]:
  if not directory.exists():
    return []
  numbers = []
  pattern = re.compile(
      rf"^{re.escape(name)}\.v(?P<number>\d+)\.{re.escape(kind)}\.{re.escape(ext)}$"
  )
  for path in directory.iterdir():
    match = pattern.match(path.name)
    if match:
      numbers.append(int(match.group("number")))
  return sorted(numbers)


def _next_version_number(directory: Path, name: str, kind: str, ext: str) -> int:
  versions = _existing_version_numbers(directory, name, kind, ext)
  return 1 if not versions else versions[-1] + 1


def _resolve_artifact_path(
    directory: Path,
    filename: str,
    version: Optional[int],
) -> Optional[Path]:
  if not directory.exists():
    return None
  parsed = _parse_artifact_filename(filename)
  if version is not None:
    candidate = directory / parsed.format(version + 1)
    return candidate if candidate.exists() else None

  exact = directory / Path(filename).name
  if exact.exists():
    return exact

  versions = _existing_version_numbers(directory, parsed.name, parsed.kind, parsed.ext)
  if not versions:
    return None
  return directory / parsed.format(versions[-1])


def _read_metadata(path: Path) -> dict[str, Any]:
  metadata_path = path.parent / f"{path.name}.metadata.json"
  if not metadata_path.exists():
    return {}
  return json.loads(metadata_path.read_text(encoding="utf-8"))


def _artifact_lock_path(directory: Path, parsed: _ParsedArtifactName) -> Path:
  return directory / f".{parsed.name}.{parsed.kind}.{parsed.ext}.lock"
