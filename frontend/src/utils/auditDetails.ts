// Item 4 (audit-details package): turns the raw `details` JSONB payload
// written by abkit/jobs.py/abkit/auth/service.py into a short human-readable
// line for the History tab / global Audit page. Unknown actions or actions
// with no useful details fall back to null (renders as "—" — see callers).
type AuditDetails = Record<string, unknown> | null | undefined

function joinList(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return '(none)'
  return value.join(', ')
}

function fromTo(details: Record<string, unknown>, label: string): string {
  return `${label}: ${String(details.from)} → ${String(details.to)}`
}

export function summarizeAuditDetails(action: string, details: AuditDetails): string | null {
  if (!details || Object.keys(details).length === 0) return null

  switch (action) {
    case 'experiment.status_change':
      return fromTo(details, 'status')
    case 'experiment.publication_status_change':
      return fromTo(details, 'publication')
    case 'experiment.rename':
      return `renamed: '${details.from}' → '${details.to}'`
    case 'experiment.tags_change': {
      const added = Array.isArray(details.added) ? (details.added as string[]) : []
      const removed = Array.isArray(details.removed) ? (details.removed as string[]) : []
      const parts = [...added.map((t) => `+${t}`), ...removed.map((t) => `−${t}`)]
      return parts.length ? `tags: ${parts.join(', ')}` : null
    }
    case 'experiment.blocks_change': {
      const kinds = Array.isArray(details.kinds) ? (details.kinds as string[]) : []
      return kinds.length ? `block${kinds.length > 1 ? 's' : ''} edited: ${kinds.join(', ')}` : null
    }
    case 'experiment.properties_change': {
      const lines: string[] = []
      if (details.owners) {
        const { from, to } = details.owners as { from: unknown; to: unknown }
        lines.push(`owners: ${joinList(from)} → ${joinList(to)}`)
      }
      if (details.editors) {
        const { from, to } = details.editors as { from: unknown; to: unknown }
        lines.push(`editors: ${joinList(from)} → ${joinList(to)}`)
      }
      if (details.visible_roles) {
        const { from, to } = details.visible_roles as { from: unknown; to: unknown }
        lines.push(`visible to: ${joinList(from)} → ${joinList(to)}`)
      }
      return lines.length ? lines.join('\n') : null
    }
    case 'dataset.update': {
      const lines: string[] = []
      if (details.filename) {
        const { old, new: next } = details.filename as { old: unknown; new: unknown }
        lines.push(`renamed: '${old}' → '${next}'`)
      }
      if (details.sql_text) lines.push('SQL query changed')
      if (details.columns) {
        const { old, new: next } = details.columns as { old: unknown; new: unknown }
        lines.push(`columns: ${joinList(old)} → ${joinList(next)}`)
      }
      return lines.length ? lines.join('\n') : null
    }
    case 'user.role_change':
      return fromTo(details, 'role')
    case 'user.active_change':
      return fromTo(details, 'active')
    case 'user.name_change':
      return `name: '${details.from}' → '${details.to}'`
    default:
      if ('from' in details && 'to' in details) return fromTo(details, 'value')
      return null
  }
}
