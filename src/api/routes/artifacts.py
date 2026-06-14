from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request

from ...contract.services import APP_NAME
from ..context import get_context
from ..presenters.artifact_presenter import present_artifact
from ..schemas import ArtifactContent
from ..schemas import ArtifactSummary


router = APIRouter(prefix="/api/sessions/{session_id}/artifacts")


@router.get("", response_model=list[ArtifactSummary])
async def list_artifacts(session_id: str, request: Request):
  ctx = get_context(request)
  filenames = await ctx.services.artifact_service.list_artifact_keys(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
  )
  return [present_artifact(filename) for filename in filenames]


@router.get("/{filename:path}", response_model=ArtifactContent)
async def read_artifact(session_id: str, filename: str, request: Request):
  ctx = get_context(request)
  artifact = await ctx.services.artifact_service.load_artifact(
      app_name=APP_NAME,
      user_id=ctx.settings.user_id,
      session_id=session_id,
      filename=filename,
  )
  if artifact is None:
    raise HTTPException(status_code=404, detail="Artifact not found")
  if artifact.text is not None:
    return ArtifactContent(filename=filename, found=True, text=artifact.text)
  inline_data = artifact.inline_data
  return ArtifactContent(
      filename=filename,
      found=True,
      mime_type=inline_data.mime_type if inline_data else None,
      byte_count=len(inline_data.data or b"") if inline_data else 0,
  )
