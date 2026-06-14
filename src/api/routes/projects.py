from __future__ import annotations

from pathlib import Path
import subprocess
import sqlite3
import sys

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse

from ..context import get_context
from ..schemas import ProjectCreateRequest
from ..schemas import ProjectDeleteSummary
from ..schemas import ProjectLauncherRequest
from ..schemas import ProjectLauncherSummary
from ..schemas import ProjectSummary
from ..schemas import ProjectUpdateRequest


router = APIRouter(prefix="/api/projects")


@router.get("", response_model=list[ProjectSummary])
def list_projects(request: Request) -> list[dict]:
  ctx = get_context(request)
  return ctx.db.list_projects()


@router.post("", response_model=ProjectSummary)
def create_project(payload: ProjectCreateRequest, request: Request) -> dict:
  ctx = get_context(request)
  root_path = Path(payload.root_path).expanduser()
  if not root_path.is_absolute():
    root_path = root_path.absolute()
  if not root_path.exists() or not root_path.is_dir():
    raise HTTPException(status_code=400, detail="Project root must be an existing directory")

  try:
    return ctx.db.create_project(
        name=payload.name or root_path.name,
        root_path=str(root_path),
    )
  except sqlite3.IntegrityError as exc:
    raise HTTPException(status_code=409, detail="Project root already exists") from exc


@router.get("/launcher-icons/{target}")
def read_launcher_icon(target: str, request: Request) -> FileResponse:
  ctx = get_context(request)
  icon_path = _launcher_icon_png(target, ctx.settings.storage_root)
  return FileResponse(icon_path, media_type="image/png")


@router.post("/{project_id}/open", response_model=ProjectSummary)
def open_project(project_id: str, request: Request) -> dict:
  ctx = get_context(request)
  try:
    return ctx.db.touch_project(project_id)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail="Project not found") from exc


@router.patch("/{project_id}", response_model=ProjectSummary)
def update_project(project_id: str, payload: ProjectUpdateRequest, request: Request) -> dict:
  ctx = get_context(request)
  name = payload.name.strip()
  if not name:
    raise HTTPException(status_code=400, detail="Project name is required")
  try:
    return ctx.db.update_project_name(project_id, name=name)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail="Project not found") from exc
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{project_id}", response_model=ProjectDeleteSummary)
def delete_project(project_id: str, request: Request) -> dict:
  ctx = get_context(request)
  try:
    project = ctx.db.delete_project(project_id)
  except KeyError as exc:
    raise HTTPException(status_code=404, detail="Project not found") from exc
  return {
      "project_id": project_id,
      "root_path": project["root_path"],
      "removed": True,
  }


@router.post("/{project_id}/launcher", response_model=ProjectLauncherSummary)
def launch_project_app(
    project_id: str,
    payload: ProjectLauncherRequest,
    request: Request,
) -> dict:
  ctx = get_context(request)
  project = ctx.db.get_project(project_id)
  if project is None:
    raise HTTPException(status_code=404, detail="Project not found")

  root_path = Path(project["root_path"]).expanduser()
  if not root_path.exists() or not root_path.is_dir():
    raise HTTPException(status_code=400, detail="Project root does not exist")

  command = _launcher_command(payload.target, root_path)
  try:
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
  except FileNotFoundError as exc:
    raise HTTPException(
        status_code=500,
        detail="System opener is unavailable",
    ) from exc
  except subprocess.CalledProcessError as exc:
    detail = _launcher_error(payload.target, exc)
    raise HTTPException(status_code=500, detail=detail) from exc

  return {
      "project_id": project_id,
      "target": payload.target,
      "opened": True,
  }


def _launcher_command(target: str, root_path: Path) -> list[str]:
  if sys.platform != "darwin":
    raise HTTPException(
        status_code=400,
        detail="Project app launcher is only supported on macOS",
    )
  if target == "finder":
    return ["open", str(root_path)]
  if target == "vscode":
    return ["open", "-a", "Visual Studio Code", str(root_path)]
  raise HTTPException(status_code=400, detail="Unknown launcher target")


def _launcher_icon_png(target: str, storage_root: Path) -> Path:
  source = _launcher_icon_source(target)
  if source is None or not source.is_file():
    raise HTTPException(status_code=404, detail="Launcher icon not found")

  icon_dir = storage_root / "web-launcher-icons"
  icon_dir.mkdir(parents=True, exist_ok=True)
  output = icon_dir / f"{target}.png"
  if output.is_file():
    return output

  try:
    subprocess.run(
        [
            "sips",
            "-s",
            "format",
            "png",
            "--resampleHeightWidth",
            "64",
            "64",
            str(source),
            "--out",
            str(output),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
  except (FileNotFoundError, subprocess.CalledProcessError) as exc:
    output.unlink(missing_ok=True)
    raise HTTPException(status_code=500, detail="Unable to read launcher icon") from exc

  return output


def _launcher_icon_source(target: str) -> Path | None:
  if target == "finder":
    return Path("/System/Library/CoreServices/Finder.app/Contents/Resources/Finder.icns")
  if target == "vscode":
    candidates = [
        Path("/Applications/Visual Studio Code.app/Contents/Resources/Code.icns"),
        Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/Code.icns",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
  raise HTTPException(status_code=404, detail="Launcher icon not found")


def _launcher_error(target: str, exc: subprocess.CalledProcessError) -> str:
  label = "Finder" if target == "finder" else "VS Code"
  output = (exc.stderr or exc.stdout or "").strip()
  if output:
    return f"Unable to open {label}: {output}"
  return f"Unable to open {label}"
