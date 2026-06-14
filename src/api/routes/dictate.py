"""Voice dictation endpoint.

Accepts a short audio clip from the composer's mic button, asks Gemini to
transcribe it, and returns a refined text intended to be inserted into the
input box. The model is conditioned on lightweight session + project context
so transcription accuracy improves for project-specific terms (file paths,
agent names, custom jargon).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..context import get_context
from ..prompt_context import (
    format_context as _format_context,
    gemini_api_key as _api_key,
    history_context as _history_context,
    normalise_model_text as _normalise,
    project_context as _project_context,
)
from ..schemas import DictateResponse

router = APIRouter(prefix="/api/dictate")
logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3-flash-preview"
# Cap the audio at ~10MB to keep us well inside the inline-bytes limit.
MAX_AUDIO_BYTES = 10 * 1024 * 1024
SYSTEM_PROMPT = (
    "You are a transcription assistant for an AI coding agent's chat input "
    "box. The user is dictating an instruction they want to send to the "
    "agent. Transcribe their speech into a single coherent prompt.\n"
    "Rules:\n"
    "- Output ONLY the transcribed prompt. No preamble, no quotes, no "
    "  commentary, no markdown fences.\n"
    "- Preserve the user's intent verbatim. Do not add information they did "
    "  not say.\n"
    "- Remove filler words (um, uh, like, you know), false starts, and "
    "  stutters. Lightly fix grammar.\n"
    "- The user mixes English and Mandarin Chinese (Simplified). Keep each "
    "  language as spoken; do not translate.\n"
    "- Treat the provided project files and recent chat history as context "
    "  to disambiguate project-specific terms (file paths, symbol names, "
    "  agent names). Spell those terms exactly as they appear in the "
    "  context.\n"
    "- If the audio is silent or unintelligible, return an empty string."
)


@router.post("", response_model=DictateResponse)
async def dictate(
    request: Request,
    audio: UploadFile = File(...),
    session_id: str | None = Form(default=None),
    project_id: str | None = Form(default=None),
) -> DictateResponse:
    api_key = _api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Dictation unavailable: set GEMINI_API_KEY or GOOGLE_API_KEY",
        )

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio too large ({len(audio_bytes)} bytes, max {MAX_AUDIO_BYTES})",
        )

    mime_type = audio.content_type or "audio/webm"

    ctx = get_context(request)
    project_context = _project_context(ctx, project_id) if project_id else ""
    history_context = ""
    if session_id:
        try:
            history_context = await _history_context(ctx, session_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to load session history for dictation: %s", exc)

    context_block = _format_context(project_context, history_context)

    try:
        transcript = await _transcribe(api_key, audio_bytes, mime_type, context_block)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Gemini dictation failed")
        raise HTTPException(status_code=502, detail=f"Dictation failed: {exc}") from exc

    return DictateResponse(transcript=_normalise(transcript))


async def _transcribe(
    api_key: str,
    audio_bytes: bytes,
    mime_type: str,
    context_block: str,
) -> str:
    # Import lazily so import-time failures don't break the rest of the API.
    from google import genai
    from google.genai import types

    from ...contract.product import with_default_model_retry_options

    client = genai.Client(api_key=api_key)
    system_instruction = SYSTEM_PROMPT
    user_parts: list = [
        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
    ]
    if context_block:
        user_parts.append(
            "Use the following context to disambiguate project-specific terms. "
            "Do NOT echo this context in your output.\n\n" + context_block
        )
    user_parts.append("Transcribe the audio above as instructed.")

    response = await client.aio.models.generate_content(
        model=MODEL_NAME,
        contents=user_parts,
        config=with_default_model_retry_options(
            types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
            )
        ),
    )
    return response.text or ""
