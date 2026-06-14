from __future__ import annotations

import logging
import os
import re
import unicodedata

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash-lite"
TITLE_SYSTEM_PROMPT = (
    "You generate concise session titles for an AI coding agent sidebar.\n"
    "Rules:\n"
    "- Output only one title line. No quotes, markdown, punctuation wrapper, or explanation.\n"
    "- Detect the user's primary natural language and write the title in that same language.\n"
    "- Do not translate the title to English unless the user wrote in English or explicitly asked for English.\n"
    "- For multilingual prompts, use the language that best represents the task while preserving key product and technical terms as written.\n"
    "- Prefer short verb + object phrasing when it is natural for that language; otherwise use the most concise natural title form.\n"
    "- Remove conversational filler, politeness, hesitation, and ticket prefixes in any language, such as please, can you, help me, let's, um, feat:, fix:, chore:.\n"
    "- Avoid process details like based on screenshot, think about it, summarize, unless they are the actual task.\n"
    "- Keep the title compact enough for a sidebar, roughly 3-7 words or the equivalent length in the user's language.\n"
    "Examples:\n"
    "Input: can you record video when you use browser\n"
    "Output: Record browser QA video\n"
    "Input: feat: make queued tasks visible in the sidebar\n"
    "Output: Show queued tasks in sidebar\n"
    "Input: please summarize the API error handling changes\n"
    "Output: Summarize API error handling"
)


def fallback_title(prompt: str) -> str:
  text = _first_meaningful_line(prompt)
  text = re.sub(r"^\s*(feat|fix|chore|docs|refactor|test|style)\s*:\s*", "", text, flags=re.I)
  text = _strip_outer_punctuation(re.sub(r"\s+", " ", text))
  if not text:
    return "New task"
  return text


async def generate_session_title(prompt: str) -> str | None:
  api_key = _api_key()
  if not api_key:
    return None

  try:
    from google import genai
    from google.genai import types

    from ..contract.product import with_default_model_retry_options

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=MODEL_NAME,
        contents="Generate a sidebar title for this user task:\n\n" + prompt.strip()[:4000],
        config=with_default_model_retry_options(
            types.GenerateContentConfig(
                system_instruction=TITLE_SYSTEM_PROMPT,
                temperature=0.2,
            )
        ),
    )
  except Exception:  # noqa: BLE001 - title generation must not block invocations.
    logger.exception("Session title generation failed")
    return None

  return normalise_generated_title(response.text or "")


def normalise_generated_title(text: str) -> str | None:
  title = (text or "").strip()
  if title.startswith("```") and title.endswith("```"):
    title = title.strip("`").strip()
    if "\n" in title:
      title = title.split("\n", 1)[1].strip()
  title = title.splitlines()[0].strip() if title else ""
  if len(title) >= 2 and title[0] == title[-1] and title[0] in ('"', "'", "`"):
    title = title[1:-1].strip()
  title = _strip_outer_punctuation(re.sub(r"\s+", " ", title))
  if not title:
    return None
  return title


def _api_key() -> str | None:
  return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _first_meaningful_line(prompt: str) -> str:
  for line in prompt.splitlines():
    stripped = line.strip()
    if stripped:
      return stripped
  return ""


def _strip_outer_punctuation(text: str) -> str:
  title = text.strip()
  start = 0
  end = len(title)
  while start < end and _is_leading_title_wrapper(title[start]):
    start += 1
  while end > start and _is_trailing_title_wrapper(title[end - 1]):
    end -= 1
  return title[start:end].strip()


def _is_leading_title_wrapper(char: str) -> bool:
  if char.isspace() or char in "-:\"'`([{":
    return True
  category = unicodedata.category(char)
  if category in {"Ps", "Pi"}:
    return True
  name = unicodedata.name(char, "")
  return name.startswith("INVERTED ") and (
      "QUESTION MARK" in name or "EXCLAMATION MARK" in name
  )


def _is_trailing_title_wrapper(char: str) -> bool:
  if char.isspace() or char in "-:.,;!?\"'`)]}":
    return True
  category = unicodedata.category(char)
  if category in {"Pe", "Pf"}:
    return True
  if not category.startswith("P"):
    return False
  name = unicodedata.name(char, "")
  return any(
      marker in name
      for marker in ("FULL STOP", "QUESTION MARK", "EXCLAMATION MARK", "COLON", "COMMA", "SEMICOLON")
  )
