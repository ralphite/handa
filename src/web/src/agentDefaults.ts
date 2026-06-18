export const DEFAULT_AGENT_ID = 'orca'
export const COMPOSER_AGENT_ORDER = ['orca', 'browser', 'ralph'] as const
export const COMPOSER_AGENT_IDS = new Set<string>(COMPOSER_AGENT_ORDER)
export const COMPOSER_AGENT_LABELS: Record<string, string> = {
  orca: 'Orca',
  browser: 'Browser',
  ralph: 'Ralph',
}
