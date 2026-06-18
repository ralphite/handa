import { describe, expect, it } from 'vitest'
import { automatedTasksPathFor, parseAutomatedTasksRoute } from '../src/presenters/automatedTasksRoute'

describe('automated tasks routes', () => {
  it('parses the list route', () => {
    expect(parseAutomatedTasksRoute('/automated-tasks')).toEqual({ kind: 'list' })
    expect(parseAutomatedTasksRoute('/automated-tasks/')).toEqual({ kind: 'list' })
  })

  it('parses the new task route', () => {
    expect(parseAutomatedTasksRoute('/automated-tasks/new')).toEqual({ kind: 'new' })
    expect(parseAutomatedTasksRoute('/automated-tasks/new/')).toEqual({ kind: 'new' })
  })

  it('parses encoded edit task ids', () => {
    expect(parseAutomatedTasksRoute('/automated-tasks/task%201%2F2')).toEqual({
      kind: 'edit',
      taskId: 'task 1/2',
    })
  })

  it('rejects unrelated or malformed paths', () => {
    expect(parseAutomatedTasksRoute('/')).toEqual({ kind: 'none' })
    expect(parseAutomatedTasksRoute('/automated-tasks/task/extra')).toEqual({ kind: 'none' })
    expect(parseAutomatedTasksRoute('/automated-tasks/%E0%A4%A')).toEqual({ kind: 'none' })
  })

  it('builds canonical paths', () => {
    expect(automatedTasksPathFor({ kind: 'list' })).toBe('/automated-tasks')
    expect(automatedTasksPathFor({ kind: 'new' })).toBe('/automated-tasks/new')
    expect(automatedTasksPathFor({ kind: 'edit', taskId: 'task 1/2' })).toBe('/automated-tasks/task%201%2F2')
  })
})
