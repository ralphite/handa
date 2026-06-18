export type AutomatedTasksRoute =
  | { kind: 'none' }
  | { kind: 'list' }
  | { kind: 'new' }
  | { kind: 'edit'; taskId: string }

const AUTOMATED_TASKS_BASE_PATH = '/automated-tasks'

export function parseAutomatedTasksRoute(pathname: string): AutomatedTasksRoute {
  const normalized = normalizePathname(pathname)
  if (normalized === AUTOMATED_TASKS_BASE_PATH) return { kind: 'list' }
  if (normalized === `${AUTOMATED_TASKS_BASE_PATH}/new`) return { kind: 'new' }
  if (!normalized.startsWith(`${AUTOMATED_TASKS_BASE_PATH}/`)) return { kind: 'none' }

  const encodedTaskId = normalized.slice(AUTOMATED_TASKS_BASE_PATH.length + 1)
  if (!encodedTaskId || encodedTaskId.includes('/')) return { kind: 'none' }

  try {
    const taskId = decodeURIComponent(encodedTaskId)
    return taskId ? { kind: 'edit', taskId } : { kind: 'none' }
  } catch {
    return { kind: 'none' }
  }
}

export function automatedTasksPathFor(route: Exclude<AutomatedTasksRoute, { kind: 'none' }>): string {
  if (route.kind === 'list') return AUTOMATED_TASKS_BASE_PATH
  if (route.kind === 'new') return `${AUTOMATED_TASKS_BASE_PATH}/new`
  return `${AUTOMATED_TASKS_BASE_PATH}/${encodeURIComponent(route.taskId)}`
}

function normalizePathname(pathname: string): string {
  const path = pathname || '/'
  if (path === '/') return path
  return path.replace(/\/+$/, '') || '/'
}
