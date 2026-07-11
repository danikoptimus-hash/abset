import { Tooltip, Typography } from 'antd'
import { formatExactTime, formatShortDate } from '../dateFormat'

// Stage 2: compact "Created Jul 2 · Started Jul 5 · Completed Jul 19" line —
// shows only the dates that are actually set (a "designed" experiment has
// no startedAt/completedAt yet), each with an exact-timestamp tooltip on
// hover. Used in both the experiment page header (after "Last modified by
// ...") and the Results tab (next to "Analyzed ... (run #N)") — CLAUDE.md
// Stage 2 spec deliberately keeps this to those two spots plus report
// headers, nowhere else (e.g. not the tests list).
export function LifecycleDates({
  createdAt, startedAt, completedAt,
}: {
  createdAt: string | null
  startedAt: string | null
  completedAt: string | null
}) {
  const entries: [string, string][] = (
    [
      ['Created', createdAt],
      ['Started', startedAt],
      ['Completed', completedAt],
    ] as [string, string | null][]
  ).filter((e): e is [string, string] => !!e[1])

  if (entries.length === 0) return null

  return (
    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
      {entries.map(([label, iso], i) => (
        <span key={label}>
          {i > 0 && ' · '}
          <Tooltip title={formatExactTime(iso)}>
            <span>
              {label} {formatShortDate(iso)}
            </span>
          </Tooltip>
        </span>
      ))}
    </Typography.Text>
  )
}
