// Форма Experiment.config["computed"] (abkit/experiment.py::Experiment.design)
// — структурированная сводка дизайна, сохраненная в JSONB config вместе с
// остальным DesignConfig. Не пересчитывается на фронте, только рендерится.
export interface PowerResult {
  metric_type: string
  baseline_mean: number | null
  baseline_std: number | null
  mde_abs: number | null
  mde_rel: number | null
  sample_size_per_group: number | null
  rho: number | null
  mde_abs_cuped: number | null
  mde_rel_cuped: number | null
  sample_size_per_group_cuped: number | null
  warnings: string[]
  // Item 5: secondary metrics always carry their own achievable MDE (never
  // a copy of the primary metric's target) — the Design tab uses this to
  // show a footnote next to those rows.
  metric_role: 'primary' | 'secondary'
}

export interface CheckResult {
  chi2: number
  p_value: number
  passed: boolean
}

// 6-part package pt.10: per-stratum-per-group counts (a flat dict per
// stratum — group names are dynamic keys alongside "stratum") plus the
// column order and distinct stratum count, computed alongside the chi2
// balance test (abkit/checks.py::strata_balance_rows/strata_balance_groups).
export interface StrataBalanceResult extends CheckResult {
  // Each row: { stratum: "<name>", "<group>": count, ... } — "stratum" is
  // always a string, group columns are always counts; kept loose (not a
  // strict intersection type) since the group keys are dynamic.
  table: Array<Record<string, string | number>>
  groups: string[]
  n_strata: number
}

export interface PrePeriodAA {
  metric: string
  treatment_group: string
  p_value: number
  passed: boolean
}

// Visibility package: one (dimension, stratum, group, metric) row of the strata
// power check — mirrors abkit/experiment.py::StratumPowerRow / the wizard's
// StrataPowerSection row.
export interface StrataPowerRow {
  stratum: string
  treatment_group: string
  metric: string
  n_control: number
  n_treatment: number
  mde_rel: number | null
  mde_rel_cuped: number | null
  status: 'ok' | 'weak' | 'insufficient'
}

export interface ComputedDesignSummary {
  n_candidates_total: number
  n_excluded_by_isolation: number
  n_available: number
  excluded_by_experiment: Record<string, number>
  group_sizes: Record<string, number>
  strata_nan_counts: Record<string, number>
  n_dropped_for_nan_strata: number
  power: Record<string, PowerResult>
  srm: CheckResult
  strata_balance: StrataBalanceResult
  // Visibility package: per-dimension per-stratum achievable MDE at the design
  // split ({dimension: [row]}). Absent on designs with no strata or persisted
  // before this feature — optional.
  strata_power?: Record<string, StrataPowerRow[]>
  pre_period_aa: PrePeriodAA[]
  warnings: string[]
}

export function getComputed(config: Record<string, unknown>): ComputedDesignSummary | null {
  return (config.computed as ComputedDesignSummary | null | undefined) ?? null
}

export interface HypothesisFamily {
  primaryCount: number
  treatmentGroupCount: number
  familySize: number
}

// Hypothesis family (5-part package pt.5.1): primary metrics × treatment
// groups (control excluded). Secondary/exploratory metrics don't count —
// they're informative only, never part of the multiple-testing family.
// familySize == 1 means there's exactly one hypothesis being tested, so any
// correction method is a no-op — the control is hidden rather than shown
// with a choice that changes nothing.
export function hypothesisFamily(config: Record<string, unknown>): HypothesisFamily {
  const metrics = (config.metrics as { role: string }[] | undefined) ?? []
  const groups = (config.groups as Record<string, number> | undefined) ?? {}
  const primaryCount = metrics.filter((m) => m.role === 'primary').length
  const treatmentGroupCount = Math.max(Object.keys(groups).length - 1, 0)
  return { primaryCount, treatmentGroupCount, familySize: primaryCount * treatmentGroupCount }
}

// Item 2 (explicit method selection): the subset of a metric's config the
// method selector (Analysis tab) and the "manually selected" derivation
// (Results tab, methodOptions.ts::isManuallySelected) both need — type and
// whether a pre-period column is set, per metric.
export interface AnalyzeMetric {
  name: string
  type: 'continuous' | 'binary' | 'ratio'
  hasPreCol: boolean
  // Optional free-text description — shown in an info popover next to the
  // metric name on the Results tab (not inlined).
  description?: string | null
}

export function analyzeMetricsFromConfig(config: Record<string, unknown>): AnalyzeMetric[] {
  const metrics =
    (config.metrics as {
      name: string
      type: 'continuous' | 'binary' | 'ratio'
      pre_col?: string | null
      description?: string | null
    }[] | undefined) ?? []
  return metrics.map((m) => ({ name: m.name, type: m.type, hasPreCol: !!m.pre_col, description: m.description }))
}
