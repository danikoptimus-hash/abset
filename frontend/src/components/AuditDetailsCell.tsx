import { Typography } from 'antd'
import { summarizeAuditDetails } from '../utils/auditDetails'

// Shared History tab / Audit page cell (item 4, audit-details package):
// AntD's Paragraph ellipsis+expandable gives a free "more" expand-on-click
// for the multi-line summaries (properties/dataset changes touching several
// fields at once) without a separate modal/popover.
export function AuditDetailsCell({
  action,
  details,
}: {
  action: string
  details: Record<string, unknown> | null
}) {
  const summary = summarizeAuditDetails(action, details)
  if (!summary) return <span>—</span>
  return (
    <Typography.Paragraph
      style={{ marginBottom: 0, whiteSpace: 'pre-line', maxWidth: 360 }}
      ellipsis={{ rows: 1, expandable: 'collapsible', symbol: 'more' }}
    >
      {summary}
    </Typography.Paragraph>
  )
}
