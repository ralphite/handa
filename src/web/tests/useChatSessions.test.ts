import { describe, expect, it } from 'vitest'
import type { BackendStep } from '../src/api/types'
import { stepHasArtifactDelta } from '../src/composables/useChatSessions'

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
