import { describe, expect, it } from 'vitest'
import type { BackendStep } from '../src/api/types'
import { stepHasArtifactDelta, useChatSessions } from '../src/composables/useChatSessions'
import type { AgentSession } from '../src/types'

function step(kind: string, payload: Record<string, unknown> = {}): Pick<BackendStep, 'kind' | 'payload'> {
  return { kind, payload }
}

describe('stepHasArtifactDelta', () => {
  it('detects direct artifact delta steps', () => {
    expect(stepHasArtifactDelta(step('artifact_delta'))).toBe(true)
  })

  it('detects artifact deltas folded into tool response projections', () => {
    expect(
      stepHasArtifactDelta(
        step('tool_response', {
          projections: [
            { kind: 'tool_response', summary: 'Saved artifact' },
            { kind: 'artifact_delta', payload: { filename: 'plan.md', version: 0 } },
          ],
        }),
      ),
    ).toBe(true)
  })

  it('ignores non-artifact steps and malformed projections', () => {
    expect(stepHasArtifactDelta(step('tool_response'))).toBe(false)
    expect(stepHasArtifactDelta(step('tool_response', { projections: { kind: 'artifact_delta' } }))).toBe(false)
    expect(stepHasArtifactDelta(step('tool_response', { projections: [{ kind: 'progress_delta' }] }))).toBe(false)
  })
})

describe('useChatSessions', () => {
  it('surfaces live-run edit precondition as an action error instead of a composer send error', async () => {
    const actionErrors: string[] = []
    const chat = useChatSessions({ onActionError: (message) => actionErrors.push(message) })
    const session: AgentSession = {
      id: 'session-1',
      title: 'Running session',
      createdAt: '2026-06-16T00:00:00.000Z',
      projectId: 'project-1',
      projectRoot: '/tmp/project',
      branch: 'main',
      status: 'running',
      elapsed: '12s',
      messages: [
        {
          id: 'turn-1-user',
          role: 'user',
          body: 'Initial prompt',
          createdAt: '2026-06-16T00:00:00.000Z',
          turnId: 'turn-1',
        },
      ],
      invocationSteps: [],
      artifacts: [],
      fileChanges: [],
    }

    chat.sessions.value = [session]
    chat.activeSessionId.value = session.id
    chat.sendError.value = 'Previous inline error'

    await chat.editUserMessage({
      sourceTurnId: 'turn-1',
      prompt: 'Edited prompt',
      files: [],
      existingAttachmentIds: [],
    })

    expect(actionErrors).toEqual(['Stop the current run before editing a message.'])
    expect(chat.sendError.value).toBe('')
  })
})
