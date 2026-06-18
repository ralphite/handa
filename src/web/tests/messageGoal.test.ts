import { describe, expect, it } from 'vitest'
import { isSessionGoalMessage } from '../src/presenters/messageGoal'

describe('isSessionGoalMessage', () => {
  it('marks only the user message that created the goal', () => {
    const session = { goal: { text: 'Ship the flow', status: 'active', createdTurnId: 'turn-goal' } }

    expect(isSessionGoalMessage(session, { role: 'user', turnId: 'turn-goal' })).toBe(true)
    expect(isSessionGoalMessage(session, { role: 'user', turnId: 'turn-other' })).toBe(false)
    expect(isSessionGoalMessage(session, { role: 'assistant', turnId: 'turn-goal' })).toBe(false)
  })

  it('keeps marking the goal message after the goal reaches a terminal status', () => {
    const session = { goal: { text: 'Ship the flow', status: 'achieved', createdTurnId: 'turn-goal' } }

    expect(isSessionGoalMessage(session, { role: 'user', turnId: 'turn-goal' })).toBe(true)
  })

  it('does not mark messages when the active goal has no source turn', () => {
    const session = { goal: { text: 'Ship the flow', status: 'active', createdTurnId: null } }

    expect(isSessionGoalMessage(session, { role: 'user', turnId: 'turn-goal' })).toBe(false)
  })
})
