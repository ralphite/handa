import type { Component } from 'vue'
import {
  ClipboardCheck,
  ClipboardList,
  File as FileIcon,
  FileCode2,
  FileText,
  Image as ImageIcon,
  NotebookText,
  ScrollText,
  TerminalSquare,
} from '@lucide/vue'

export interface ArtifactIconSource {
  kind: string
  meta?: string | null
  title: string
}

export function artifactIconFor(artifact: ArtifactIconSource): Component {
  const kind = artifact.kind.trim().toLowerCase().replace(/[\s_]+/g, '-')
  if (kind === 'plan') return ClipboardList
  if (kind === 'report' || kind === 'summary') return ScrollText
  if (kind === 'review' || kind === 'verification') return ClipboardCheck
  if (kind === 'patch' || kind === 'diff' || kind === 'code') return FileCode2
  if (kind === 'image') return ImageIcon
  if (kind === 'log' || kind === 'text') return TerminalSquare
  if (kind === 'note' || kind === 'notes' || kind === 'markdown') return NotebookText

  const descriptor = `${artifact.kind} ${artifact.meta ?? ''} ${artifact.title}`.toLowerCase()
  if (/\b(plan|task-breakdown)\b/.test(descriptor)) return ClipboardList
  if (/\b(report|summary)\b/.test(descriptor)) return ScrollText
  if (/\b(review|verification)\b/.test(descriptor)) return ClipboardCheck
  if (/\b(patch|diff|code|json|mermaid)\b/.test(descriptor)) return FileCode2
  if (/\b(image|png|jpg|jpeg|gif|webp|svg)\b/.test(descriptor)) return ImageIcon
  if (/\b(log|txt|text|output)\b/.test(descriptor)) return TerminalSquare
  if (/\b(note|notes|markdown|md)\b/.test(descriptor)) return NotebookText
  if (/\bartifact\b/.test(descriptor)) return FileText
  return FileIcon
}
