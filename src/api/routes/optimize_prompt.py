"""Prompt optimization endpoint.

Takes the draft prompt from the composer's optimize button, asks Gemini to
rewrite it into a clearer instruction for the coding agent, and returns the
rewritten text intended to replace the draft. The model is conditioned on
lightweight session + project context so vague references ("fix it", "the
login page") can be resolved into concrete, self-contained instructions.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..context import get_context
from ..prompt_context import (
    format_context as _format_context,
    gemini_api_key as _api_key,
    history_context as _history_context,
    normalise_model_text as _normalise,
    project_context as _project_context,
)
from ..schemas import OptimizePromptRequest, OptimizePromptResponse

router = APIRouter(prefix="/api/optimize_prompt")
logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3-flash-preview"
# Keep the draft well inside a single flash context window.
MAX_PROMPT_CHARS = 20_000
SYSTEM_PROMPT = (
    "You are a prompt-refinement assistant for an AI coding agent's chat "
    "input box. The user drafted an instruction for the agent and asked to "
    "optimize it. Rewrite the draft into a clearer, more effective prompt.\n"
    "Rules:\n"
    "- Output ONLY the rewritten prompt. No preamble, no quotes, no "
    "  commentary, no markdown fences.\n"
    "- Stay faithful to the user's intent. Never invent requirements, "
    "  scope, or constraints the user did not state or clearly imply.\n"
    "- Match the effort to the task: keep simple instructions short (a "
    "  sentence or two, lightly clarified); expand into structure (goal, "
    "  key constraints, acceptance criteria) only when the draft genuinely "
    "  describes a complex task.\n"
    "- Use the provided project files and recent chat history to make "
    "  vague references concrete (exact file paths, symbol names, agent "
    "  names), but only when the context makes the mapping unambiguous.\n"
    "- If the draft leans on the conversation ('fix it', 'same for the "
    "  other file'), rewrite it as a self-contained instruction using the "
    "  history.\n"
    "- Keep the user's language. If the draft mixes English and Mandarin "
    "  Chinese (Simplified), keep the mix; do not translate.\n"
    "- Keep technical identifiers (paths, symbols, commands, error "
    "  messages) exactly as written.\n"
    "- If the draft is already clear and well-scoped, return it unchanged "
    "  or with minimal touch-ups."
)


@router.post("", response_model=OptimizePromptResponse)
async def optimize_prompt(
    request: Request,
    payload: OptimizePromptRequest,
) -> OptimizePromptResponse:
  api_key = _api_key()
  if not api_key:
    raise HTTPException(
        status_code=503,
        detail="Prompt optimization unavailable: set GEMINI_API_KEY or GOOGLE_API_KEY",
    )

  prompt = payload.prompt.strip()
  if not prompt:
    raise HTTPException(status_code=400, detail="Empty prompt")
  if len(prompt) > MAX_PROMPT_CHARS:
    raise HTTPException(
        status_code=413,
        detail=f"Prompt too long ({len(prompt)} chars, max {MAX_PROMPT_CHARS})",
    )

  ctx = get_context(request)
  project_context = (
      _project_context(ctx, payload.project_id) if payload.project_id else ""
  )
  history_context = ""
  if payload.session_id:
    try:
      history_context = await _history_context(ctx, payload.session_id)
    except Exception as exc:  # pragma: no cover - defensive
      logger.warning(
          "Failed to load session history for prompt optimization: %s", exc
      )

  context_block = _format_context(project_context, history_context)

  try:
    optimized = await _optimize(api_key, prompt, context_block)
  except HTTPException:
    raise
  except Exception as exc:
    logger.exception("Gemini prompt optimization failed")
    raise HTTPException(
        status_code=502, detail=f"Prompt optimization failed: {exc}"
    ) from exc

  optimized = _normalise(optimized)
  # An empty rewrite would wipe the user's draft — fall back to the original.
  return OptimizePromptResponse(optimized=optimized or prompt)


async def _optimize(api_key: str, prompt: str, context_block: str) -> str:
  # Import lazily so import-time failures don't break the rest of the API.
  from google import genai
  from google.genai import types

  from ...contract.product import with_default_model_retry_options

  client = genai.Client(api_key=api_key)
  user_parts: list = []
  if context_block:
    user_parts.append(
        "Use the following context to resolve project-specific terms and "
        "references. Do NOT echo this context in your output.\n\n" + context_block
    )
  user_parts.append("User's draft prompt:\n\n" + prompt)
  user_parts.append("Rewrite the draft above as instructed.")

  response = await client.aio.models.generate_content(
      model=MODEL_NAME,
      contents=user_parts,
      config=with_default_model_retry_options(
          types.GenerateContentConfig(
              system_instruction=SYSTEM_PROMPT,
              temperature=0.3,
          )
      ),
  )
  return response.text or ""
