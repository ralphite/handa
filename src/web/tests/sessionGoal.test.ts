import { describe, expect, it } from 'vitest'
import { goalFromBackend } from '../src/presenters/sessionGoal'

describe('goalFromBackend', () => {
  it('keeps terminal goals so their source message can still show the Goal chip', () => {
    expect(
      goalFromBackend({
        goal_id: 'goal-1',
        text: 'Ship the flow',
        status: 'achieved',
        created_turn_id: 'turn-goal',
      }),
    ).toMatchObject({
      goalId: 'goal-1',
      text: 'Ship the flow',
      status: 'achieved',
      createdTurnId: 'turn-goal',
    })
  })

  it('keeps cleared goal markers when a source turn is present', () => {
    expect(
      goalFromBackend({
        text: '',
        status: 'cleared',
        created_turn_id: 'turn-goal',
      }),
    ).toMatchObject({
      text: '',
      status: 'cleared',
      createdTurnId: 'turn-goal',
    })
  })

  it('ignores empty cleared goals with no source turn', () => {
    expect(goalFromBackend({ text: '', status: 'cleared', created_turn_id: null })).toBeNull()
  })
})
