from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request

from ...contract.product import list_model_config_options
from ...contract.product import validate_model_config_id
from ..context import get_context
from ..schemas import WebSettingsSummary
from ..schemas import WebSettingsUpdateRequest


router = APIRouter(prefix="/api/settings")
MAX_FOLDED_PROJECT_IDS = 1000
DEFAULT_THEME_ID = "dark"
VALID_THEME_IDS = frozenset({"dark", "light", "system"})
LEGACY_THEME_IDS = {
    "github-dark": "dark",
    "github-light": "light",
}


@router.get("", response_model=WebSettingsSummary)
def read_settings(request: Request) -> dict:
  return _web_settings_summary(request)


@router.patch("", response_model=WebSettingsSummary)
def update_settings(
    payload: WebSettingsUpdateRequest,
    request: Request,
) -> dict:
  ctx = get_context(request)
  if payload.theme_id is not None:
    theme_id = _validate_theme_id(payload.theme_id)
    ctx.db.set_user_setting(
        user_id=ctx.settings.user_id,
        key="theme_id",
        value=theme_id,
    )
  if payload.model_config_id is not None:
    try:
      model_config_id = validate_model_config_id(payload.model_config_id)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    ctx.db.set_user_setting(
        user_id=ctx.settings.user_id,
        key="model_config_id",
        value=model_config_id,
    )
  if payload.streaming_mode_enabled is not None:
    ctx.db.set_user_setting(
        user_id=ctx.settings.user_id,
        key="streaming_mode_enabled",
        value="true" if payload.streaming_mode_enabled else "false",
    )
  if payload.folded_project_ids is not None:
    ctx.db.set_user_setting(
        user_id=ctx.settings.user_id,
        key="folded_project_ids",
        value=json.dumps(_normalize_folded_project_ids(payload.folded_project_ids)),
    )
  if payload.gemini_api_key is not None:
    ctx.db.set_user_setting(
        user_id=ctx.settings.user_id,
        key="gemini_api_key",
        value=payload.gemini_api_key.strip(),
    )
  return _web_settings_summary(request)


def _web_settings_summary(request: Request) -> dict:
  ctx = get_context(request)
  settings = ctx.db.get_web_settings(user_id=ctx.settings.user_id)
  settings["theme_id"] = _resolve_theme_id(settings.get("theme_id"))
  raw_gemini_api_key = (settings.pop("gemini_api_key", "") or "").strip()
  settings["gemini_api_key_set"] = bool(raw_gemini_api_key)
  settings["gemini_api_key_preview"] = (
      raw_gemini_api_key[-4:] if raw_gemini_api_key else ""
  )
  try:
    settings["model_config_id"] = validate_model_config_id(
        settings.get("model_config_id")
    )
  except ValueError:
    settings["model_config_id"] = validate_model_config_id(None)
  settings["model_configs"] = [
      {
          "id": option.id,
          "label": option.label,
          "description": option.description,
          "context_window": option.context_window,
      }
      for option in list_model_config_options()
  ]
  return settings


def _resolve_theme_id(theme_id: object) -> str:
  if isinstance(theme_id, str):
    if theme_id in VALID_THEME_IDS:
      return theme_id
    if theme_id in LEGACY_THEME_IDS:
      return LEGACY_THEME_IDS[theme_id]
  return DEFAULT_THEME_ID


def _validate_theme_id(theme_id: str) -> str:
  if theme_id in VALID_THEME_IDS:
    return theme_id
  raise HTTPException(status_code=400, detail="Unknown theme_id.")


def _normalize_folded_project_ids(project_ids: list[str]) -> list[str]:
  """Trim, drop blanks, dedupe (preserving order), and bound the stored list."""
  seen: set[str] = set()
  normalized: list[str] = []
  for project_id in project_ids:
    trimmed = project_id.strip()
    if not trimmed or trimmed in seen:
      continue
    seen.add(trimmed)
    normalized.append(trimmed)
    if len(normalized) >= MAX_FOLDED_PROJECT_IDS:
      break
  return normalized
