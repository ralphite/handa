import type { AgentMessage, AgentSession } from '../types'

export function isSessionGoalMessage(
  session: Pick<AgentSession, 'goal'> | undefined,
  message: Pick<AgentMessage, 'role' | 'turnId'>,
) {
  const createdTurnId = session?.goal?.createdTurnId
  return message.role === 'user' && Boolean(createdTurnId && message.turnId === createdTurnId)
}
