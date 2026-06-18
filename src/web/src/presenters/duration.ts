export function formatWorkDuration(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds))
  if (total < 60) return `${total}s`

  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60

  if (hours > 0) {
    return minutes > 0 ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${hours}h`
  }
  return seconds > 0 ? `${minutes}m ${String(seconds).padStart(2, '0')}s` : `${minutes}m`
}

export function parseDurationToSeconds(text?: string): number {
  if (!text) return 0
  let seconds = 0
  const hours = text.match(/(\d+)\s*h/)
  if (hours) seconds += Number(hours[1]) * 3600
  const minutes = text.match(/(\d+)\s*m/)
  if (minutes) seconds += Number(minutes[1]) * 60
  const secs = text.match(/(\d+)\s*s/)
  if (secs) seconds += Number(secs[1])
  return seconds
}
