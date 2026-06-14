import type { AgentMessage, InvocationTimelineItem } from '../types'

export function timelineItemsWithoutDuplicateFinalText(
  items: InvocationTimelineItem[] | undefined,
  finalText: string | undefined,
): InvocationTimelineItem[] {
  if (!items?.length || !finalText?.trim()) return items ?? []

  // Trim trailing process_text items that duplicate the final text. This covers
  // cumulative streaming snapshots where the last segment grows into the body.
  let end = items.length
  while (end > 0) {
    const item = items[end - 1]
    if (!item) break
    if (item.kind !== 'process_text') break
    if (!textDuplicatesFinalText(item.text ?? '', finalText)) break
    end -= 1
  }

  // Also drop any earlier process_text item that duplicates the body. This
  // happens when the agent posts a message and then keeps the turn going (e.g.
  // request_user_input), so the duplicate is stranded before later timeline
  // items and the trailing trim above can't reach it.
  const trimmed = end === items.length ? items : items.slice(0, end)
  const deduped = trimmed.filter(
    (item) => !(item.kind === 'process_text' && textDuplicatesFinalText(item.text ?? '', finalText)),
  )

  return deduped.length === items.length ? items : deduped
}

export function removeDuplicateFinalProcessText(message: AgentMessage, finalText = message.body) {
  if (!message.timelineItems?.length || !finalText?.trim()) return
  const next = timelineItemsWithoutDuplicateFinalText(message.timelineItems, finalText)
  if (next !== message.timelineItems) message.timelineItems = next
}

function textDuplicatesFinalText(processText: string, finalText: string) {
  const process = normalizedText(processText)
  const final = normalizedText(finalText)
  if (!process || !final) return false
  if (process === final) return true
  if (process.length < 32 || final.length < 32) return false
  // process_text items are streaming snapshots of the same message that lands in
  // the body. A snapshot frozen mid-stream (e.g. the agent paused for
  // request_user_input) is a prefix of the body; cumulative snapshots can also
  // share the suffix. Anchored prefix/suffix containment in either direction
  // catches these without matching unrelated quoted fragments.
  return (
    final.startsWith(process) ||
    process.startsWith(final) ||
    final.endsWith(process) ||
    process.endsWith(final)
  )
}

function normalizedText(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}
