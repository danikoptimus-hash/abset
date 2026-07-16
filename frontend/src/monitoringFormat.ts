// Admin monitoring panel: every metric the collector stores is in MB
// (abkit/monitoring.py) — formatted here as MB below 1024, GB above, so a
// multi-gigabyte database size doesn't read as "14235.7 MB". Also used by
// the per-job peak-memory display (AnalyzeSection.tsx/Step4Review.tsx),
// hence living alongside dateFormat.ts at the src root rather than under
// pages/admin — it's a shared formatting utility, not admin-page-specific.
export function formatMb(mb: number | null | undefined): string {
  if (mb == null) return '—'
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} GB`
  return `${mb.toFixed(1)} MB`
}

export function formatBytes(bytes: number): string {
  return formatMb(bytes / (1024 * 1024))
}
