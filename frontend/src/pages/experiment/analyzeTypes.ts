// Форма GET /experiments/{name}/results (AnalysisResults.to_json(), ядро —
// abkit/analysis/results.py — + backend/chart_data.py "chart_data", см.
// routers/experiments.py::_save_analysis). Не пересчитывается на фронте,
// только рендерится.

export interface TestResultOut {
  metric: string
  method: string
  treatment_group: string
  effect_abs: number
  effect_rel: number
  ci_abs: [number, number]
  ci_rel: [number, number]
  p_value: number
  p_value_adjusted: number | null
  n: Record<string, number>
  n_removed: Record<string, number>
  variance_reduction: number | null
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
  control_counts: number[]
  treatment_counts: number[]
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
}

export type Distribution = BinaryDistribution | ContinuousDistribution

export interface SegmentEffect {
  stratum: string
  effect_rel: number
  ci_rel: [number, number]
}

export interface DailyLiftPoint {
  date: string
  effect_rel: number
  ci_lower: number
  ci_upper: number
}

export interface MetricChartData {
  metric_type: 'continuous' | 'binary' | 'ratio'
  control_name: string
  distributions: Record<string, Distribution>
  segments: Record<string, SegmentEffect[]>
  daily: Record<string, DailyLiftPoint[]>
}

export interface ChartData {
  checks: { srm: CheckResult | null; loss: CheckResult | null }
  metrics: Record<string, MetricChartData>
}

export interface AnalysisResultsOut {
  abkit_version: string
  seed: number | null
  correction: string | null
  global_warnings: string[]
  results: TestResultOut[]
  chart_data: ChartData
}

export function resultsByMetric(results: TestResultOut[]): Record<string, TestResultOut[]> {
  const out: Record<string, TestResultOut[]> = {}
  for (const r of results) {
    out[r.metric] = out[r.metric] ?? []
    out[r.metric].push(r)
  }
  return out
}

export function verdict(r: TestResultOut, alpha = 0.05): 'significant_positive' | 'significant_negative' | 'no_effect_detected' {
  const p = r.p_value_adjusted ?? r.p_value
  if (p < alpha && r.effect_abs > 0) return 'significant_positive'
  if (p < alpha && r.effect_abs < 0) return 'significant_negative'
  return 'no_effect_detected'
}
