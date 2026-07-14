import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Steps, Button, Space, Input, Select, Alert, Spin, Tooltip, Typography } from 'antd'
import { apiClient, errorMessage } from '../api/client'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { Step1Data } from './design-wizard/Step1Data'
import { Step2GroupsMetrics } from './design-wizard/Step2GroupsMetrics'
import { Step3Parameters } from './design-wizard/Step3Parameters'
import { Step4Review } from './design-wizard/Step4Review'
import { nextId, groupsSum, wizardStateFromConfig } from './design-wizard/types'
import type { WizardState, DesignConfig } from './design-wizard/types'

const INITIAL_STATE: WizardState = {
  splitMode: 'abkit',
  datasetId: null,
  columns: [],
  dtypes: {},
  previewRows: [],
  nRows: 0,
  name: '',
  hypothesis: '',
  unitCol: null,
  groups: [
    { id: nextId('group'), name: 'control', prop: 0.5, description: '' },
    { id: nextId('group'), name: 'treatment', prop: 0.5, description: '' },
  ],
  flowColumns: [],
  originalFlowGroupNames: [],
  metrics: [{ id: nextId('metric'), name: '', type: 'continuous', role: 'primary', preCol: null, num: null, den: null }],
  strata: [],
  nanStrategy: 'separate_stratum',
  sizeMode: 'all',
  alpha: 0.05,
  power: 0.8,
  mdeRel: 0.05,
  mdeAbsMetricId: null,
  mdeAbsValue: 0,
  sampleSize: 1000,
  splitMethod: 'stratified',
  isolation: 'exclude',
  isolationSelected: [],
}

function stepError(step: number, state: WizardState): string | null {
  const isExternal = state.splitMode === 'external'
  if (step === 0) {
    if (!isExternal && !state.datasetId) return 'Upload data or generate demo data'
  }
  if (step === 1) {
    if (!state.name.trim()) return 'Enter an experiment name'
    if (!isExternal && !state.unitCol) return 'Select the unit column'
    if (Math.abs(groupsSum(state) - 1) > 1e-6) return 'Group proportions must sum to 1'
    if (!state.metrics.some((m) => m.name.trim())) return 'Add at least one metric'
  }
  return null
}

function inferDtypes(previewRows: Record<string, unknown>[]): Record<string, string> {
  const dtypes: Record<string, string> = {}
  for (const [key, value] of Object.entries(previewRows[0] ?? {})) {
    dtypes[key] = typeof value === 'number' ? 'float64' : 'object'
  }
  return dtypes
}

// Redesign (5-part package pt.3.2): reached via /experiments/:name/redesign
// (route param below), loads the experiment's current config + its design
// dataset and pre-fills the wizard state before the user ever sees step 1 —
// "all steps editable, including the dataset."
function useRedesignPrefill(redesignName: string | undefined, setState: (updater: (prev: WizardState) => WizardState) => void) {
  const [loading, setLoading] = useState(!!redesignName)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!redesignName) return
    let cancelled = false
    const load = async () => {
      try {
        const { data: exp, error: expError } = await apiClient.GET('/api/v1/experiments/{name}', {
          params: { path: { name: redesignName } },
        })
        if (expError) throw new Error(errorMessage(expError))
        const { data: dsInfo, error: dsError } = await apiClient.GET('/api/v1/experiments/{name}/design-dataset', {
          params: { path: { name: redesignName } },
        })
        if (dsError || !dsInfo) throw new Error('No design dataset found for this experiment — cannot redesign')
        const { data: preview } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
          params: { path: { dataset_id: dsInfo.id }, query: { rows: 20 } },
        })
        const previewRows = preview?.rows ?? []

        // Stage 4: existing flow images (editable only via Redesign) —
        // grouped by group_name, one wizard column per group in config.groups'
        // order (default column i -> group i, per CLAUDE.md item 4.2), each
        // pre-populated with that group's images (kind='existing', already
        // ordered by position) and flow_title (denormalized per-row, so any
        // one row in the group carries it).
        const { data: existingImages } = await apiClient.GET('/api/v1/experiments/{name}/flow-images', {
          params: { path: { name: redesignName } },
        })
        const configGroups = wizardStateFromConfig(exp.config as unknown as DesignConfig).groups ?? []
        const imagesByGroup = new Map<string, typeof existingImages>()
        for (const img of existingImages ?? []) {
          const list = imagesByGroup.get(img.group_name) ?? []
          list.push(img)
          imagesByGroup.set(img.group_name, list)
        }
        const flowColumns = configGroups.map((g) => {
          const groupImages = (imagesByGroup.get(g.name) ?? []).slice().sort((a, b) => a.position - b.position)
          return {
            id: nextId('flowcol'),
            groupName: g.name,
            flowTitle: groupImages[0]?.flow_title ?? '',
            images: groupImages.map((img) => ({
              id: img.id,
              kind: 'existing' as const,
              previewUrl: `/api/v1/experiments/${redesignName}/flow-images/${img.id}/file`,
            })),
          }
        })

        if (cancelled) return
        setState((prev) => ({
          ...prev,
          datasetId: dsInfo.id,
          columns: dsInfo.columns,
          dtypes: inferDtypes(previewRows),
          nRows: dsInfo.n_rows,
          previewRows,
          ...wizardStateFromConfig(exp.config as unknown as DesignConfig),
          flowColumns,
          originalFlowGroupNames: [...imagesByGroup.keys()],
        }))
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load experiment for redesign')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [redesignName, setState])

  return { loading, error }
}

// Визард дизайна A/B теста (FRONTEND.md §5.2, 4 шага). Состояние — один
// объект конфига (WizardState), поднятый в этом компоненте и передаваемый
// шагам как props. Also serves redesign (5-part package pt.3) at
// /experiments/:name/redesign — same steps, pre-filled state, name locked,
// and Step4Review submits to a different endpoint (see redesignName prop).
export function DesignWizardPage() {
  const { name: redesignName } = useParams<{ name?: string }>()
  const [current, setCurrent] = useState(0)
  const [state, setState] = useState<WizardState>(INITIAL_STATE)
  const { loading: redesignLoading, error: redesignError } = useRedesignPrefill(redesignName, setState)

  // UX contract, part A: the wizard has no per-field pristine tracking of
  // its own (unlike the Form-based modals) — the whole WizardState object
  // is the form, so a plain snapshot comparison works. Baseline is
  // INITIAL_STATE for a fresh design; for Redesign, it's captured once the
  // async prefill (useRedesignPrefill above) has actually landed, not
  // INITIAL_STATE (which would make an unmodified redesign look dirty). A
  // plain useState (not a ref) here — its value feeds isDirty, which drives
  // render output, and refs aren't meant to be read during render.
  const [pristine, setPristine] = useState<WizardState>(INITIAL_STATE)
  const capturedRef = useRef(false)
  useEffect(() => {
    if (redesignName && !redesignLoading && !capturedRef.current) {
      setPristine(state)
      capturedRef.current = true
    }
  }, [redesignLoading, redesignName, state])
  const isDirty = JSON.stringify(state) !== JSON.stringify(pristine)
  const { markSaved } = useUnsavedGuard(isDirty)

  const error = stepError(current, state)

  if (redesignLoading) return <Spin size="large" />
  if (redesignError) return <Alert type="error" showIcon message={redesignError} />

  return (
    <div>
      <Steps
        current={current}
        items={[{ title: 'Data' }, { title: 'Groups & Metrics' }, { title: 'Parameters' }, { title: 'Run' }]}
        style={{ marginBottom: 32, maxWidth: 800 }}
      />

      {redesignName && (
        <Alert
          type="info"
          showIcon
          message={`Redesigning "${redesignName}"`}
          description="The current split, MDE table, and split checks will be discarded once you submit; analyses run against the old split will be deleted."
          style={{ marginBottom: 24, maxWidth: 600 }}
        />
      )}

      {current === 1 && (
        <Space style={{ marginBottom: 24 }}>
          <Tooltip title={redesignName ? 'Renaming is not part of redesign — use Edit Properties afterwards' : ''}>
            <Input
              placeholder="Experiment name"
              value={state.name}
              style={{ width: 260 }}
              disabled={!!redesignName}
              onChange={(e) => setState((prev) => ({ ...prev, name: e.target.value }))}
            />
          </Tooltip>
          {state.splitMode === 'abkit' && (
            <Select
              placeholder="Unit column (unit_col)"
              style={{ width: 220 }}
              value={state.unitCol ?? undefined}
              onChange={(unitCol) => setState((prev) => ({ ...prev, unitCol }))}
              options={state.columns.map((c) => ({ value: c, label: c }))}
            />
          )}
        </Space>
      )}

      {current === 1 && (
        <div style={{ marginBottom: 24, maxWidth: 600 }}>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
            Hypothesis (optional)
          </Typography.Text>
          <Input.TextArea
            value={state.hypothesis}
            onChange={(e) => setState((prev) => ({ ...prev, hypothesis: e.target.value }))}
            rows={3}
            aria-label="Hypothesis"
          />
          <Typography.Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
            A well-formed hypothesis: If we change X, it will affect Y, which we will observe as a change in
            metric Z.
          </Typography.Text>
        </div>
      )}

      <div style={{ minHeight: 300 }}>
        {current === 0 && <Step1Data state={state} setState={setState} lockSplitMode={!!redesignName} />}
        {current === 1 && <Step2GroupsMetrics state={state} setState={setState} />}
        {current === 2 && <Step3Parameters state={state} setState={setState} />}
        {current === 3 && <Step4Review state={state} redesignName={redesignName} onSubmitted={markSaved} />}
      </div>

      {current < 3 && (
        <div style={{ marginTop: 24 }}>
          {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 12, maxWidth: 500 }} />}
          <Space>
            {current > 0 && <Button onClick={() => setCurrent((c) => c - 1)}>Back</Button>}
            <Button type="primary" disabled={!!error} onClick={() => setCurrent((c) => c + 1)}>
              Next
            </Button>
          </Space>
        </div>
      )}
      {current === 3 && (
        <div style={{ marginTop: 24 }}>
          <Button onClick={() => setCurrent((c) => c - 1)}>Back</Button>
        </div>
      )}
    </div>
  )
}
