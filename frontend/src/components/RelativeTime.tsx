import { Tooltip } from 'antd'
import { formatExactTime, formatRelativeTime } from '../dateFormat'

// Shared date display: relative text with an exact-time tooltip on hover
// (UX package, section 4) — used anywhere a raw ISO timestamp used to be
// shown directly (experiments list, datasets, audit log, experiment page).
export function RelativeTime({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span>—</span>
  return (
    <Tooltip title={formatExactTime(iso)}>
      <span>{formatRelativeTime(iso)}</span>
    </Tooltip>
  )
}
