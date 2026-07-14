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
  // Stage 3: optional free-text "what does this variant show/do?" — stored
  // additively in DesignConfig.group_descriptions (sibling dict to groups,
  // same keys), editable only via Redesign (this same wizard, prefilled).
  description: string
}

// Stage 4 (variant flow images): 'existing' images already live on the
// server (id is the real FlowImageOut.id, previewUrl points at
// GET /flow-images/{id}/file — same-origin, cookies ride along same as any
// other <img>/href in this app); 'new' ones are staged client-side only
// (id is a throwaway local key, previewUrl is a blob: object URL over
// `file`) until Step4Review's submit uploads them.
export interface FlowImageState {
  id: string
  kind: 'existing' | 'new'
  file?: File
  previewUrl: string
}

export interface FlowColumnState {
  id: string
  groupName: string
  flowTitle: string
  images: FlowImageState[]
}

export type SizeMode = 'mde_rel' | 'mde_abs' | 'sample_size' | 'all'

export interface WizardState {
  datasetId: string | null
  columns: string[]
  dtypes: Record<string, string>
  previewRows: Record<string, unknown>[]
  nRows: number

  // Item 12: "abkit" is the usual flow (this file, as before) — "external"
  // means the split already happened outside ABSet (Firebase A/B Testing
  // and similar); no dataset, no split, no isolation, only a manually
  // declared config. See buildExternalDesignConfig below for what that
  // flow actually submits.
  splitMode: 'abkit' | 'external'

  name: string
  hypothesis: string
  unitCol: string | null
  groups: GroupFormRow[]
  flowColumns: FlowColumnState[]
  // Stage 4: group names that already had flow images when the wizard
  // opened (Redesign prefill only, always [] on fresh design) — a whole
  // column can be removed by the user without individually deleting its
  // images first, so submit-time cleanup needs to know about groups that
  // HAD images even if no column for them survives to submit.
  originalFlowGroupNames: string[]
  metrics: MetricFormRow[]
  strata: string[]
  nanStrategy: 'separate_stratum' | 'drop' | 'error'
  sizeMode: SizeMode
  alpha: number
  power: number
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

// Only non-empty descriptions are sent — an empty string is "no
// description", same as the key being absent entirely (old configs).
export function groupDescriptionsToApi(state: WizardState): Record<string, string> {
  const out: Record<string, string> = {}
  for (const g of state.groups) {
    if (g.name.trim() && g.description.trim()) out[g.name.trim()] = g.description.trim()
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
    group_descriptions: groupDescriptionsToApi(state),
    metrics: metricsToApi(state),
    alpha: state.alpha,
    power: state.power,
    split_source: 'abkit',
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

// Item 12 (external split): no dataset, so none of the ABSet-split-only
// fields (split_method/strata/isolation/nan_strategy) mean anything — sent
// as harmless fixed defaults rather than left to whatever they happened to
// be in state, so a persisted external config never LOOKS like it made an
// isolation/split-method decision it didn't actually make. sample_size is
// the one field that IS meaningful here (expected size, for reference) —
// carried over as-is when the user filled it in.
export function buildExternalDesignConfig(state: WizardState): DesignConfig {
  return {
    name: state.name.trim(),
    unit_col: '',
    groups: groupsToApi(state),
    group_descriptions: groupDescriptionsToApi(state),
    metrics: metricsToApi(state),
    alpha: state.alpha,
    power: state.power,
    split_source: 'external',
    split_method: 'simple',
    strata: [],
    n_buckets_continuous: 4,
    min_stratum_size: 20,
    nan_strategy: 'separate_stratum',
    isolation: 'off',
    exclude_experiments: 'all_active',
    isolation_selected_experiments: [],
    sample_size: state.sampleSize > 0 ? state.sampleSize : undefined,
  }
}

let idCounter = 0
export function nextId(prefix: string): string {
  idCounter += 1
  return `${prefix}${idCounter}`
}

export function groupsFromApi(
  groups: Record<string, number>,
  descriptions?: Record<string, string>,
): GroupFormRow[] {
  return Object.entries(groups).map(([name, prop]) => ({
    id: nextId('group'),
    name,
    prop,
    description: descriptions?.[name] ?? '',
  }))
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
    splitMode: config.split_source === 'external' ? 'external' : 'abkit',
    name: config.name,
    unitCol: config.unit_col,
    groups: groupsFromApi(config.groups, config.group_descriptions),
    metrics,
    strata: config.strata ?? [],
    nanStrategy: config.nan_strategy ?? 'separate_stratum',
    sizeMode,
    alpha: config.alpha ?? 0.05,
    power: config.power ?? 0.8,
    mdeRel: config.mde ?? 0.05,
    mdeAbsMetricId: mdeSourceMetric?.id ?? null,
    mdeAbsValue: config.mde_abs_input ?? 0,
    sampleSize: config.sample_size ?? 1000,
    splitMethod: config.split_method,
    isolation: config.isolation,
    isolationSelected: config.isolation_selected_experiments ?? [],
  }
}
