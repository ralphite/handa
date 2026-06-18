import type { BackendSessionGoal } from '../api/types'
import type { SessionGoal } from '../types'

export function goalFromBackend(goal: BackendSessionGoal | null | undefined): SessionGoal | null {
  if (!goal) return null
  const text = goal.text.trim()
  const createdTurnId = goal.created_turn_id ?? null
  if (!text && !createdTurnId) return null
  return {
    goalId: goal.goal_id ?? null,
    text,
    status: goal.status,
    createdTurnId,
    createdAt: goal.created_at ?? null,
    updatedAt: goal.updated_at ?? null,
    maxAttempts: goal.max_attempts ?? null,
    reason: goal.reason ?? null,
  }
}
