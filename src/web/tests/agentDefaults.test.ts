import { describe, expect, it } from 'vitest'
import { COMPOSER_AGENT_IDS, DEFAULT_AGENT_ID } from '../src/agentDefaults'

describe('agent defaults', () => {
  it('keeps Orca as the default composer agent', () => {
    expect(DEFAULT_AGENT_ID).toBe('orca')
  })

  it('offers Ralph in the composer agent selector', () => {
    expect(COMPOSER_AGENT_IDS.has('ralph')).toBe(true)
  })
})
