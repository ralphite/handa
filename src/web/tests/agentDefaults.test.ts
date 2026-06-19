import { describe, expect, it } from 'vitest'
import { COMPOSER_AGENT_IDS, COMPOSER_AGENT_LABELS, COMPOSER_AGENT_ORDER, DEFAULT_AGENT_ID } from '../src/agentDefaults'

describe('agent defaults', () => {
  it('keeps Orca as the default composer agent', () => {
    expect(DEFAULT_AGENT_ID).toBe('orca')
  })

  it('orders and labels composer agents', () => {
    expect(COMPOSER_AGENT_ORDER).toEqual(['orca', 'browser'])
    expect(COMPOSER_AGENT_IDS.has('ralph')).toBe(false)
    expect(COMPOSER_AGENT_LABELS).toMatchObject({
      orca: 'Orca',
      browser: 'Browser',
    })
  })
})
