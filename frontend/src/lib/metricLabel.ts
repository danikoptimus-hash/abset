// Item A1 — what to SHOW for a metric, everywhere the user sees one.
//
// A metric's `name` is its technical identity: the data column for
// continuous/binary metrics, and the key under which everything else is
// stored (analysis results, config.computed.power, strata power rows). It can
// never be swapped for a prettier string without breaking those lookups.
// `display_name` is the prettier string; this is the single rule that picks
// between them.
//
// Manual TS port of abkit/config.py::metric_label / metric_labels_by_name —
// same convention as branding.ts mirroring abkit.PRODUCT_NAME. Kept in
// frontend/src/lib (not next to a component) so the vitest suite, which runs
// with environment "node" and only picks up src/**/*.test.ts, can cover it —
// same reasoning as lib/share.ts.

export interface MetricLike {
  name: string
  display_name?: string | null
}

/** display_name when set and non-blank, otherwise the technical name. */
export function metricLabel(metric: MetricLike): string {
  const display = (metric.display_name ?? '').trim()
  return display || metric.name
}

/**
 * True when the technical column name is worth showing alongside the label —
 * i.e. only when a display name is actually overriding it. Callers render the
 * small grey "column: txn_sum" line off this, so a metric without a display
 * name doesn't get its own name printed twice.
 */
export function showsColumnSeparately(metric: MetricLike): boolean {
  return metricLabel(metric) !== metric.name
}

/** {technical name -> label}, for the many places holding only the key. */
export function metricLabelsByName(metrics: MetricLike[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const m of metrics) out[m.name] = metricLabel(m)
  return out
}

/** Label for a metric known only by key; falls back to the key itself. */
export function labelForMetricName(
  metricName: string,
  labels: Record<string, string> | undefined,
): string {
  return labels?.[metricName] ?? metricName
}
