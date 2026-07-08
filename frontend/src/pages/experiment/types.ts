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
}

export interface CheckResult {
  chi2: number
  p_value: number
  passed: boolean
}

export interface PrePeriodAA {
  metric: string
  treatment_group: string
  p_value: number
  passed: boolean
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
  strata_balance: CheckResult
  pre_period_aa: PrePeriodAA[]
  warnings: string[]
}

export function getComputed(config: Record<string, unknown>): ComputedDesignSummary | null {
  return (config.computed as ComputedDesignSummary | null | undefined) ?? null
}
