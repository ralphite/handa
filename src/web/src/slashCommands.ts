// Slash-command registry for the composer.
//
// Typing `/` that starts a word (at the input start, or right after whitespace)
// opens a command palette. This module owns the command catalog plus the pure
// token-detection / filter helpers so the matching logic stays unit-testable and
// decoupled from the Vue component.
//
// The shape is intentionally generic so more commands (agent, compact, fork,
// ...) can be added without touching the menu UI. `kind` tells the composer how
// a chosen command resolves.

export type SlashCommandKind = 'model' | 'goal' | 'optimize'

export interface SlashCommand {
  /** Stable identity used for keys and analytics. */
  id: string
  /** Token typed after the leading slash, e.g. `model` for `/model`. */
  name: string
  /** Human-facing label shown in the palette row. */
  title: string
  /** One-line description (registry metadata; not necessarily rendered). */
  description: string
  /** Extra tokens that should also match this command. */
  aliases?: string[]
  /**
   * Only offer the command when the draft already has non-whitespace text
   * before the slash — e.g. `optimize`, which rewrites the existing prompt and
   * is meaningless on an empty composer.
   */
  requiresLeadingText?: boolean
  /** How the composer resolves the command once chosen. */
  kind: SlashCommandKind
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {
    id: 'goal',
    name: 'goal',
    title: 'Goal',
    description: 'Set a goal and keep working towards it',
    aliases: ['goals', 'objective'],
    kind: 'goal',
  },
  {
    id: 'model',
    name: 'model',
    title: 'Model',
    description: 'Switch the active model',
    aliases: ['models'],
    kind: 'model',
  },
  {
    id: 'optimize',
    name: 'optimize',
    title: 'Optimize Prompt',
    description: 'Rewrite the current prompt to be clearer',
    aliases: ['optimise', 'improve', 'enhance'],
    requiresLeadingText: true,
    kind: 'optimize',
  },
]

/** The active slash-command token sitting at the caret. */
export interface SlashToken {
  /** Text after the slash up to the caret — the filter query. */
  query: string
  /** Index of the leading `/` in the source text. */
  start: number
  /** Exclusive end of the token (past any trailing word chars). */
  end: number
}

const WORD = /\w/
const SPACE = /\s/

/**
 * Detect the slash-command token at `caret`, or `null` when the caret is not
 * inside one. A token is a `/` that begins a word — preceded by the start of
 * input or whitespace — followed only by word characters. This triggers on
 * `/model`, `test /model`, ` /model`, and after a newline, but NOT on `1/2`,
 * `http://x`, or `a/b`, where the slash is not word-initial.
 *
 * `end` extends past the caret over trailing word chars so callers can excise
 * the whole token even when the caret sits mid-token.
 */
export function slashTokenAt(text: string, caret: number): SlashToken | null {
  const pos = caret < 0 || caret > text.length ? text.length : caret
  let start = pos
  while (start > 0 && WORD.test(text.charAt(start - 1))) start--
  const slashIndex = start - 1
  if (slashIndex < 0 || text.charAt(slashIndex) !== '/') return null
  if (slashIndex > 0 && !SPACE.test(text.charAt(slashIndex - 1))) return null
  let end = pos
  while (end < text.length && WORD.test(text.charAt(end))) end++
  return { query: text.slice(slashIndex + 1, pos), start: slashIndex, end }
}

/**
 * Commands whose name, title, or aliases contain `query` (case-insensitive),
 * ordered alphabetically by title.
 */
export function filterSlashCommands(
  query: string,
  commands: readonly SlashCommand[] = SLASH_COMMANDS,
): SlashCommand[] {
  const needle = query.trim().toLowerCase()
  const matches = !needle ? commands : commands.filter((command) => {
    const haystacks = [command.name, command.title, ...(command.aliases ?? [])]
    return haystacks.some((value) => value.toLowerCase().includes(needle))
  })
  return [...matches].sort((left, right) => {
    const byTitle = left.title.localeCompare(right.title, undefined, { sensitivity: 'base' })
    if (byTitle !== 0) return byTitle
    return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' })
  })
}
