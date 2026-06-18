import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BackendStep } from '../src/api/types'
import { hiddenStepDebugPayload, stepHasArtifactDelta, useChatSessions } from '../src/composables/useChatSessions'
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

describe('hiddenStepDebugPayload', () => {
  it('prints nested goal judge messages for debugging', () => {
    const debug = hiddenStepDebugPayload('session-1', {
      id: 'step-1',
      turn_id: 'turn-1',
      seq: 3,
      kind: 'runtime_step',
      summary: 'Goal judge: continue',
      payload: {
        kind: 'goal_judge_verdict',
        goal_id: 'goal-1',
        goal_attempt_id: 'attempt-1',
        attempt_number: 1,
        verdict: {
          status: 'continue',
          reason: 'The second attempt has not written the file yet.',
          next_request: 'Write the file and run the browser verification.',
        },
      },
      raw_event: {
        kind: 'goal_judge_verdict',
        payload: {
          verdict: {
            reason: 'The second attempt has not written the file yet.',
            next_request: 'Write the file and run the browser verification.',
          },
        },
      },
      created_at: '2026-06-18T00:00:00.000Z',
    })

    expect(debug?.hiddenReason).toBe('goal step')
    expect(debug?.judgeReason).toBe('The second attempt has not written the file yet.')
    expect(debug?.hiddenMessage).toBe('Write the file and run the browser verification.')
    expect(debug?.hiddenMessages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'payload',
          path: 'payload.verdict.reason',
          value: 'The second attempt has not written the file yet.',
        }),
        expect.objectContaining({
          source: 'payload',
          path: 'payload.verdict.next_request',
          value: 'Write the file and run the browser verification.',
        }),
      ]),
    )
  })

  it('does not treat normal visible steps as hidden debug messages', () => {
    const debug = hiddenStepDebugPayload('session-1', {
      id: 'step-2',
      turn_id: 'turn-1',
      seq: 4,
      kind: 'tool_response',
      summary: 'Command finished',
      payload: { name: 'command', response: { ok: true } },
      created_at: '2026-06-18T00:00:00.000Z',
    })

    expect(debug).toBeNull()
  })
})

describe('useChatSessions', () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window

  afterEach(() => {
    vi.useRealTimers()
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    })
  })

  it('stops retrying initial load and shows a backend unavailable error', async () => {
    vi.useFakeTimers()
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: {
        setTimeout: globalThis.setTimeout.bind(globalThis),
        clearTimeout: globalThis.clearTimeout.bind(globalThis),
      },
    })
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })

    const chat = useChatSessions()
    await chat.loadInitial()

    while (vi.getTimerCount() > 0) {
      await vi.runOnlyPendingTimersAsync()
    }

    expect(chat.loading.value).toBe(false)
    expect(chat.projectsLoading.value).toBe(false)
    expect(chat.error.value).toBe('Backend unavailable. Start or restart the Handa backend server, then retry.')
    expect(chat.activeSession.value.title).toBe('Backend unavailable')
    expect(chat.activeSession.value.messages[0]?.body).toContain('could not reach the backend')
  })

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
