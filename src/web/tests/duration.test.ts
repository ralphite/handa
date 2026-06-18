import { describe, expect, it } from 'vitest'
import { formatWorkDuration, parseDurationToSeconds } from '../src/presenters/duration'

describe('formatWorkDuration', () => {
  it('omits zero second and minute suffixes', () => {
    expect(formatWorkDuration(0)).toBe('0s')
    expect(formatWorkDuration(59)).toBe('59s')
    expect(formatWorkDuration(60)).toBe('1m')
    expect(formatWorkDuration(120)).toBe('2m')
    expect(formatWorkDuration(3600)).toBe('1h')
  })

  it('keeps useful precision for non-exact minute and hour durations', () => {
    expect(formatWorkDuration(61)).toBe('1m 01s')
    expect(formatWorkDuration(125)).toBe('2m 05s')
    expect(formatWorkDuration(3720)).toBe('1h 02m')
  })
})

describe('parseDurationToSeconds', () => {
  it('parses existing compact duration strings', () => {
    expect(parseDurationToSeconds('1m')).toBe(60)
    expect(parseDurationToSeconds('1m 01s')).toBe(61)
    expect(parseDurationToSeconds('1h 02m')).toBe(3720)
  })
})
