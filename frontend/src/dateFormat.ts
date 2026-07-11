import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

const RELATIVE_CUTOFF_DAYS = 30

// "5 minutes ago" / "17 hours ago" / "2 days ago" up to RELATIVE_CUTOFF_DAYS,
// then an absolute short date ("Jul 8, 2026") — Superset-style list dates
// (UX package, section 4). dayjs parses the ISO timestamp (backend always
// sends timezone-aware UTC) and formats in the browser's local timezone.
export function formatRelativeTime(iso: string): string {
  const d = dayjs(iso)
  if (dayjs().diff(d, 'day') > RELATIVE_CUTOFF_DAYS) {
    return d.format('MMM D, YYYY')
  }
  return d.fromNow()
}

// Exact local tooltip text: "Jul 8, 2026, 21:38" (no seconds/T/Z/ms).
export function formatExactTime(iso: string): string {
  return dayjs(iso).format('MMM D, YYYY, HH:mm')
}

// Compact lifecycle-date label text: "Jul 8" (no year — used alongside a
// "Created/Started/Completed" label where the exact timestamp is already one
// hover away via formatExactTime, so the year would just be visual noise).
export function formatShortDate(iso: string): string {
  return dayjs(iso).format('MMM D')
}
