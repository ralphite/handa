from __future__ import annotations

import re
from pathlib import Path


THEMES_TS = Path("src/web/src/themes.ts")

EXPECTED_THEME_IDS = (
    "dark",
    "light",
)

EXPECTED_THEME_OPTIONS = (
    "system",
    "dark",
    "light",
)

REQUIRED_TOKENS = {
    "--background",
    "--foreground",
    "--panel",
    "--sidebar",
    "--right-panel",
    "--surface",
    "--surface-muted",
    "--surface-hover",
    "--surface-active",
    "--border-muted",
    "--border-layout",
    "--border",
    "--input",
    "--ring",
    "--muted-foreground",
    "--subtle-foreground",
    "--faint-foreground",
    "--accent",
    "--accent-soft",
    "--accent-foreground",
    "--destructive",
    "--destructive-foreground",
    "--destructive-soft",
    "--success",
    "--success-foreground",
    "--success-soft",
    "--warning",
    "--warning-foreground",
    "--warning-soft",
    "--info",
    "--info-foreground",
    "--info-soft",
    "--overlay",
    "--scrollbar-track",
    "--scrollbar-thumb",
    "--scrollbar-thumb-hover",
    "--code-bg",
    "--code-fg",
    "--code-border",
    "--markdown-fg",
    "--user-message-bg",
    "--user-message-fg",
    "--user-message-border",
    "--shadow-color",
}

SEMANTIC_SOFT_TOKENS = (
    "--destructive-soft",
    "--success-soft",
    "--warning-soft",
    "--info-soft",
)

EXPECTED_SOFT_TOKENS = {
    "dark": {
        "--accent-soft": "#388bfd1a",
        "--destructive-soft": "#f851491a",
        "--success-soft": "#2ea04326",
        "--warning-soft": "#bb800926",
        "--info-soft": "#388bfd1a",
    },
    "light": {
        "--accent-soft": "#ddf4ff",
        "--destructive-soft": "#ffebe9",
        "--success-soft": "#dafbe1",
        "--warning-soft": "#fff8c5",
        "--info-soft": "#ddf4ff",
    },
}


def _object_at(text: str, start: int) -> str:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError("unterminated object in themes.ts")


def _theme_presets() -> dict[str, dict[str, str]]:
    text = THEMES_TS.read_text()
    presets: dict[str, dict[str, str]] = {}
    cursor = 0
    marker = "createThemePreset({"
    while True:
        start = text.find(marker, cursor)
        if start == -1:
            break
        block = _object_at(text, start + len("createThemePreset("))
        theme_id_match = re.search(r"id:\s*['\"]([^'\"]+)['\"]", block)
        assert theme_id_match, block
        variables_start = block.find("variables:")
        assert variables_start != -1, block
        variables_open = block.find("{", variables_start)
        variables = _object_at(block, variables_open)
        presets[theme_id_match.group(1)] = dict(
            re.findall(r"""["'](--[^"']+)["']:\s*["']([^"']+)["']""", variables),
        )
        cursor = start + len(marker)
    return presets


def _theme_options() -> tuple[str, ...]:
    text = THEMES_TS.read_text()
    start = text.find("export const THEME_OPTIONS")
    assert start != -1
    end = text.find("];", start)
    assert end != -1
    block = text[start:end]
    return tuple(re.findall(r"id:\s*['\"]([^'\"]+)['\"]", block))


def test_theme_options_include_system_preference_without_extra_preset():
    assert _theme_options() == EXPECTED_THEME_OPTIONS


def test_theme_presets_cover_expected_themes_and_tokens():
    presets = _theme_presets()

    assert tuple(presets) == EXPECTED_THEME_IDS
    for theme_id, variables in presets.items():
        assert set(variables) == REQUIRED_TOKENS, theme_id


def test_theme_soft_variants_are_tier_uniform():
    presets = _theme_presets()

    for theme_id, variables in presets.items():
        assert variables["--accent-soft"] == EXPECTED_SOFT_TOKENS[theme_id]["--accent-soft"]
        for token in SEMANTIC_SOFT_TOKENS:
            assert variables[token] == EXPECTED_SOFT_TOKENS[theme_id][token], (
                theme_id,
                token,
            )


def test_theme_palette_decisions_remain_intentional():
    presets = _theme_presets()

    assert presets["dark"]["--accent"] == "#4493f8"
    assert presets["dark"]["--background"] == "#101010"
    assert presets["dark"]["--foreground"] == "#eeeeee"
    assert presets["dark"]["--border"] == "#444444"
    assert presets["light"]["--accent"] == "#0969da"
    assert presets["dark"]["--user-message-bg"] == "#ffffff"
    assert presets["dark"]["--user-message-fg"] == "#1f2328"
    assert presets["dark"]["--user-message-border"] == "#ffffff"
    assert presets["light"]["--user-message-bg"] == "#101010"
    assert presets["light"]["--user-message-fg"] == "#ffffff"
    assert presets["light"]["--user-message-border"] == "#101010"
