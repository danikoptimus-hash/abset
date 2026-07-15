// Форма GET /experiments/{name}/results (AnalysisResults.to_json(), ядро —
// abkit/analysis/results.py — + backend/chart_data.py "chart_data", см.
// routers/experiments.py::_save_analysis). Не пересчитывается на фронте,
// только рендерится.

export interface TestResultOut {
  metric: string
  method: string
  treatment_group: string
  // null: a NaN result — a legitimate degenerate-segment outcome (zero
  // variance in a stratum), or an alternative comparison method
  // (compare_methods=True) that raised an exception (see
  // Experiment.analyze()'s extra_chains loop / _failed_method_result) —
  // the designed method and other alternatives still complete normally.
  effect_abs: number | null
  effect_rel: number | null
  ci_abs: [number | null, number | null]
  ci_rel: [number | null, number | null]
  p_value: number | null
  p_value_adjusted: number | null
  n: Record<string, number>
  n_removed: Record<string, number>
  variance_reduction: number | null
  // Correlation between the metric and its pre-period covariate — set only
  // when CUPED was applied (variance_reduction ~= cuped_rho^2); null
  // otherwise.
  cuped_rho: number | null
  warnings: string[]
  is_designed_method: boolean
  role: 'primary' | 'secondary'
}

export interface CheckResult {
  chi2: number
  p_value: number
  passed?: boolean
  symmetric?: boolean
  missing_rate?: Record<string, number>
}

export interface BinaryDistribution {
  kind: 'binary'
  control: { prop: number; ci_lo: number; ci_hi: number; n: number }
  treatment: { prop: number; ci_lo: number; ci_hi: number; n: number }
}

export interface Histogram {
  bin_edges: number[]
  // Density (bar heights) — NOT a count, despite the name (histnorm=density
  // upstream). Kept as-is for backward compat with existing series data;
  // control_n/treatment_n below are the actual per-bin counts, added for
  // Stage 1's tooltip (count + % share), which density can't reconstruct
  // exactly.
  control_counts: number[]
  treatment_counts: number[]
  control_n: number[]
  treatment_n: number[]
}

export interface PositiveOnlyDistribution {
  histogram: Histogram
  control_ecdf: [number, number][]
  treatment_ecdf: [number, number][]
  pct_zero_control: number
  pct_zero_treatment: number
}

export interface ContinuousDistribution {
  kind: 'continuous'
  clipped: Histogram
  full_range: Histogram
  control_ecdf: [number, number][]
  treatment_ecdf: [number, number][]
  p99_threshold: number | null
  n_above_p99: number
  pct_above_p99: number
  // "Positive only" display mode (report feature) — exact zeros excluded
  // from both charts, display-only; has_zeros gates whether the toggle
  // option even makes sense to offer (no zeros = identical to Full range).
  positive_only: PositiveOnlyDistribution
  has_zeros: boolean
}

export type Distribution = BinaryDistribution | ContinuousDistribution

export interface SegmentEffect {
  stratum: string
  effect_rel: number
  ci_rel: [number, number]
  n: Record<string, number>
}

export interface DailyLiftPoint {
  date: string
  // Raw fractions (0.02 = 2%), same convention as the main results'
  // effect_rel/ci_rel above — CumulativeLiftChart converts to percent with
  // its own *100. Regression: these used to arrive pre-multiplied by 100
  // from the backend, doubling up with the chart's own conversion.
  effect_rel: number
  ci_lower: number
  ci_upper: number
}

export interface MetricChartData {
  metric_type: 'continuous' | 'binary' | 'ratio'
  control_name: string
  distributions: Record<string, Distribution>
  // Item 3 (per-dimension segment analysis): {dimension_label: {treat_name:
  // [...]}} — one entry per stratification dimension alone (e.g. "gender"),
  // plus (when there's more than one) their combination under a
  // " × "-joined label (e.g. "gender × country"). A single-dimension design
  // has just that one column's own name as the only key — no separate
  // "combined" entry duplicating it.
  segments_by_dimension: Record<string, Record<string, SegmentEffect[]>>
  daily: Record<string, DailyLiftPoint[]>
}

export interface ChartData {
  checks: { srm: CheckResult | null; loss: CheckResult | null }
  metrics: Record<string, MetricChartData>
}

export interface RunMeta {
  created_at: string
  dataset_filename: string | null
  run_number: number
}

export interface AnalysisResultsOut {
  abkit_version: string
  seed: number | null
  correction: string | null
  global_warnings: string[]
  results: TestResultOut[]
  chart_data: ChartData
  run_meta: RunMeta
}

export function resultsByMetric(results: TestResultOut[]): Record<string, TestResultOut[]> {
  const out: Record<string, TestResultOut[]> = {}
  for (const r of results) {
    out[r.metric] = out[r.metric] ?? []
    out[r.metric].push(r)
  }
  return out
}

export function verdict(
  r: TestResultOut,
  alpha = 0.05,
): 'significant_positive' | 'significant_negative' | 'no_effect_detected' | 'failed' {
  const p = r.p_value_adjusted ?? r.p_value
  if (p === null || r.effect_abs === null) return 'failed'
  if (p < alpha && r.effect_abs > 0) return 'significant_positive'
  if (p < alpha && r.effect_abs < 0) return 'significant_negative'
  return 'no_effect_detected'
}
