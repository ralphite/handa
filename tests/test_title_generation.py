from __future__ import annotations

from src.api.title_generation import fallback_title
from src.api.title_generation import normalise_generated_title


def test_fallback_title_strips_common_prefix_without_truncating():
  title = fallback_title(
      "feat: based on screenshot of codex figure out rule to generate session name"
  )

  assert title == "based on screenshot of codex figure out rule to generate session name"


def test_normalise_generated_title_returns_single_clean_line():
  title = normalise_generated_title('"设计 session 命名规则"\nextra text')

  assert title == "设计 session 命名规则"


def test_normalise_generated_title_preserves_long_title():
  title = normalise_generated_title(
      "Implement Codex-like browser feature parity across navigation and screenshots"
  )

  assert title == "Implement Codex-like browser feature parity across navigation and screenshots"


def test_normalise_generated_title_strips_unicode_punctuation_wrappers():
  title = normalise_generated_title("¿Revisar errores de API?")

  assert title == "Revisar errores de API"


def test_normalise_generated_title_preserves_technical_leading_punctuation():
  assert normalise_generated_title("#123 API failure") == "#123 API failure"
  assert normalise_generated_title(".NET upgrade") == ".NET upgrade"


def test_normalise_generated_title_rejects_empty_output():
  assert normalise_generated_title("``` \n```") is None
