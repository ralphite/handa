import { describe, expect, it } from 'vitest'
import { sidebarSessionPagination } from '../src/presenters/sidebarSessions'

describe('sidebarSessionPagination', () => {
  it('shows only Show more before a long multi-project list is expanded', () => {
    expect(sidebarSessionPagination({ hasMultipleProjects: true, sessionCount: 80 })).toEqual({
      limit: 10,
      canShowMore: true,
      canShowLess: false,
    })
  })

  it('shows both controls when a long list is partially expanded', () => {
    expect(
      sidebarSessionPagination({
        hasMultipleProjects: true,
        sessionCount: 80,
        visibleSessionCount: 60,
      }),
    ).toEqual({
      limit: 60,
      canShowMore: true,
      canShowLess: true,
    })
  })

  it('shows only Show less once a long list is fully expanded', () => {
    expect(
      sidebarSessionPagination({
        hasMultipleProjects: true,
        sessionCount: 80,
        visibleSessionCount: 80,
      }),
    ).toEqual({
      limit: 80,
      canShowMore: false,
      canShowLess: true,
    })
  })

  it('does not paginate single-project sidebars', () => {
    expect(sidebarSessionPagination({ hasMultipleProjects: false, sessionCount: 80 })).toEqual({
      limit: 80,
      canShowMore: false,
      canShowLess: false,
    })
  })
})
