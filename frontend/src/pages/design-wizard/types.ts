import type { components } from '../../api/schema'

export type MetricConfig = components['schemas']['MetricConfig']
export type DesignConfig = components['schemas']['DesignConfig']

export interface MetricFormRow {
  id: string
  name: string
  type: 'continuous' | 'binary' | 'ratio'
  role: 'primary' | 'secondary'
  preCol: string | null
  num: string | null
  den: string | null
}

export interface GroupFormRow {
  id: string
  name: string
  prop: number
}

export type SizeMode = 'mde_rel' | 'mde_abs' | 'sample_size' | 'all'

export interface WizardState {
  datasetId: string | null
  columns: string[]
  dtypes: Record<string, string>
  previewRows: Record<string, unknown>[]
  nRows: number

  name: string
  hypothesis: string
  unitCol: string | null
  groups: GroupFormRow[]
  metrics: MetricFormRow[]
  strata: string[]
  nanStrategy: 'separate_stratum' | 'drop' | 'error'
  sizeMode: SizeMode
  mdeRel: number
  mdeAbsMetricId: string | null
  mdeAbsValue: number
  sampleSize: number
  splitMethod: 'simple' | 'stratified' | 'hash'
  isolation: 'exclude' | 'warn' | 'off' | 'exclude_selected'
  isolationSelected: string[]
}

export function numericColumns(state: WizardState): string[] {
  return state.columns.filter((c) => {
    const dt = state.dtypes[c] ?? ''
    return dt.startsWith('int') || dt.startsWith('float')
  })
}

export function metricsToApi(state: WizardState): MetricConfig[] {
  return state.metrics
    .filter((m) => m.name.trim())
    .map((m) => ({
      name: m.name.trim(),
      type: m.type,
      role: m.role,
      pre_col: m.type === 'ratio' ? undefined : m.preCol ?? undefined,
      num: m.type === 'ratio' ? m.num ?? undefined : undefined,
      den: m.type === 'ratio' ? m.den ?? undefined : undefined,
    }))
}

export function groupsToApi(state: WizardState): Record<string, number> {
  const out: Record<string, number> = {}
  for (const g of state.groups) {
    if (g.name.trim()) out[g.name.trim()] = g.prop
  }
  return out
}

export function groupsSum(state: WizardState): number {
  return state.groups.reduce((acc, g) => acc + (g.prop || 0), 0)
}

export function buildDesignConfig(state: WizardState): DesignConfig {
  const config: DesignConfig = {
    name: state.name.trim(),
    unit_col: state.unitCol ?? '',
    groups: groupsToApi(state),
    metrics: metricsToApi(state),
    alpha: 0.05,
    power: 0.8,
    split_method: state.splitMethod,
    strata: state.strata,
    n_buckets_continuous: 4,
    min_stratum_size: 20,
    nan_strategy: state.nanStrategy,
    isolation: state.isolation,
    exclude_experiments: 'all_active',
    isolation_selected_experiments: state.isolation === 'exclude_selected' ? state.isolationSelected : [],
  }
  if (state.sizeMode === 'mde_rel') {
    config.mde = state.mdeRel
  } else if (state.sizeMode === 'sample_size') {
    config.sample_size = state.sampleSize
  }
  // mde_abs подставляется отдельно (buildDesignConfigWithAbsMde) — там нужен
  // baseline_mean, полученный асинхронно с сервера.
  return config
}

let idCounter = 0
export function nextId(prefix: string): string {
  idCounter += 1
  return `${prefix}${idCounter}`
}

export function groupsFromApi(groups: Record<string, number>): GroupFormRow[] {
  return Object.entries(groups).map(([name, prop]) => ({ id: nextId('group'), name, prop }))
}

export function metricsFromApi(metrics: MetricConfig[]): MetricFormRow[] {
  return metrics.map((m) => ({
    id: nextId('metric'),
    name: m.name,
    type: m.type as MetricFormRow['type'],
    role: m.role as MetricFormRow['role'],
    preCol: m.pre_col ?? null,
    num: m.num ?? null,
    den: m.den ?? null,
  }))
}

// Redesign (5-part package pt.3.2): "the wizard opens PRE-FILLED with the
// current config" — the inverse of buildDesignConfig, sourced from a real
// saved DesignConfig instead of the demo-data suggested_config (see
// Step1Data.tsx's handleDemoData for the precedent this mirrors). Dataset
// fields (datasetId/columns/dtypes/previewRows/nRows) aren't part of
// DesignConfig — the caller fetches and merges those separately.
export function wizardStateFromConfig(config: DesignConfig): Partial<WizardState> {
  const metrics = metricsFromApi(config.metrics)
  const mdeSourceMetric = metrics.find((m) => m.name === config.mde_source_metric)
  let sizeMode: SizeMode = 'all'
  if (config.mde_abs_input != null) sizeMode = 'mde_abs'
  else if (config.mde != null) sizeMode = 'mde_rel'
  else if (config.sample_size != null) sizeMode = 'sample_size'
  return {
    name: config.name,
    unitCol: config.unit_col,
    groups: groupsFromApi(config.groups),
    metrics,
    strata: config.strata ?? [],
    nanStrategy: config.nan_strategy ?? 'separate_stratum',
    sizeMode,
    mdeRel: config.mde ?? 0.05,
    mdeAbsMetricId: mdeSourceMetric?.id ?? null,
    mdeAbsValue: config.mde_abs_input ?? 0,
    sampleSize: config.sample_size ?? 1000,
    splitMethod: config.split_method,
    isolation: config.isolation,
    isolationSelected: config.isolation_selected_experiments ?? [],
  }
}
