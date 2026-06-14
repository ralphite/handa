import { describe, expect, it } from 'vitest'
import { SLASH_COMMANDS, filterSlashCommands, slashTokenAt } from '../src/slashCommands'

describe('slashTokenAt', () => {
  it('detects a lone leading slash with an empty query', () => {
    expect(slashTokenAt('/', 1)).toEqual({ query: '', start: 0, end: 1 })
  })

  it('detects a slash command at the start of the input', () => {
    expect(slashTokenAt('/model', 6)).toEqual({ query: 'model', start: 0, end: 6 })
  })

  it('detects a slash command after preceding text and a space', () => {
    expect(slashTokenAt('test /model', 11)).toEqual({ query: 'model', start: 5, end: 11 })
  })

  it('detects a slash command after a leading space or newline', () => {
    expect(slashTokenAt(' /model', 7)).toEqual({ query: 'model', start: 1, end: 7 })
    expect(slashTokenAt('a\n/model', 8)).toEqual({ query: 'model', start: 2, end: 8 })
  })

  it('extends end past the caret over trailing word chars', () => {
    // caret sits mid-token: query is up to the caret, end covers the whole token.
    expect(slashTokenAt('/model', 3)).toEqual({ query: 'mo', start: 0, end: 6 })
  })

  it('does not trigger when the slash is not word-initial', () => {
    expect(slashTokenAt('1/2', 3)).toBeNull()
    expect(slashTokenAt('http://x', 8)).toBeNull()
    expect(slashTokenAt('a/b', 3)).toBeNull()
  })

  it('does not trigger without a slash, or once a space ends the token', () => {
    expect(slashTokenAt('hello', 5)).toBeNull()
    expect(slashTokenAt('', 0)).toBeNull()
    expect(slashTokenAt('/mod ', 5)).toBeNull()
  })
})

describe('filterSlashCommands', () => {
  it('returns every command for an empty query, preserving order', () => {
    expect(filterSlashCommands('')).toEqual([...SLASH_COMMANDS])
  })

  it('matches by name prefix case-insensitively', () => {
    expect(filterSlashCommands('MOD').map((command) => command.id)).toContain('model')
  })

  it('matches by alias', () => {
    expect(filterSlashCommands('models').map((command) => command.id)).toContain('model')
  })

  it('returns nothing for an unknown token', () => {
    expect(filterSlashCommands('zzz')).toEqual([])
  })
})
