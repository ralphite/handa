export const DEFAULT_VISIBLE_SESSIONS = 10
export const SHOW_MORE_SESSION_INCREMENT = 50

export interface SidebarSessionPaginationInput {
  hasMultipleProjects: boolean
  sessionCount: number
  visibleSessionCount?: number
}

export interface SidebarSessionPagination {
  limit: number
  canShowMore: boolean
  canShowLess: boolean
}

export function sidebarSessionPagination({
  hasMultipleProjects,
  sessionCount,
  visibleSessionCount,
}: SidebarSessionPaginationInput): SidebarSessionPagination {
  if (!hasMultipleProjects) {
    return { limit: sessionCount, canShowMore: false, canShowLess: false }
  }

  const requestedLimit = visibleSessionCount ?? DEFAULT_VISIBLE_SESSIONS
  const limit = Math.min(requestedLimit, sessionCount)
  const hasOverflow = sessionCount > DEFAULT_VISIBLE_SESSIONS
  return {
    limit,
    canShowMore: limit < sessionCount,
    canShowLess: hasOverflow && limit > DEFAULT_VISIBLE_SESSIONS,
  }
}
