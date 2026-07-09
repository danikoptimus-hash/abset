import { useState } from 'react'
import { Steps, Button, Space, Input, Select, Alert } from 'antd'
import { Step1Data } from './design-wizard/Step1Data'
import { Step2GroupsMetrics } from './design-wizard/Step2GroupsMetrics'
import { Step3Parameters } from './design-wizard/Step3Parameters'
import { Step4Review } from './design-wizard/Step4Review'
import { nextId, groupsSum } from './design-wizard/types'
import type { WizardState } from './design-wizard/types'

const INITIAL_STATE: WizardState = {
  datasetId: null,
  columns: [],
  dtypes: {},
  previewRows: [],
  nRows: 0,
  name: '',
  unitCol: null,
  groups: [
    { id: nextId('group'), name: 'control', prop: 0.5 },
    { id: nextId('group'), name: 'treatment', prop: 0.5 },
  ],
  metrics: [{ id: nextId('metric'), name: '', type: 'continuous', role: 'primary', preCol: null, num: null, den: null }],
  strata: [],
  nanStrategy: 'separate_stratum',
  sizeMode: 'all',
  mdeRel: 0.05,
  mdeAbsMetricId: null,
  mdeAbsValue: 0,
  sampleSize: 1000,
  splitMethod: 'stratified',
  isolation: 'exclude',
  isolationSelected: [],
}

function stepError(step: number, state: WizardState): string | null {
  if (step === 0) {
    if (!state.datasetId) return 'Upload data or generate demo data'
  }
  if (step === 1) {
    if (!state.name.trim()) return 'Enter an experiment name'
    if (!state.unitCol) return 'Select the unit column'
    if (Math.abs(groupsSum(state) - 1) > 1e-6) return 'Group proportions must sum to 1'
    if (!state.metrics.some((m) => m.name.trim())) return 'Add at least one metric'
  }
  return null
}

// Визард дизайна A/B теста (FRONTEND.md §5.2, 4 шага). Состояние — один
// объект конфига (WizardState), поднятый в этом компоненте и передаваемый
// шагам как props.
export function DesignWizardPage() {
  const [current, setCurrent] = useState(0)
  const [state, setState] = useState<WizardState>(INITIAL_STATE)

  const error = stepError(current, state)

  return (
    <div>
      <Steps
        current={current}
        items={[{ title: 'Data' }, { title: 'Groups & Metrics' }, { title: 'Parameters' }, { title: 'Run' }]}
        style={{ marginBottom: 32, maxWidth: 800 }}
      />

      {current === 1 && (
        <Space style={{ marginBottom: 24 }}>
          <Input
            placeholder="Experiment name"
            value={state.name}
            style={{ width: 260 }}
            onChange={(e) => setState((prev) => ({ ...prev, name: e.target.value }))}
          />
          <Select
            placeholder="Unit column (unit_col)"
            style={{ width: 220 }}
            value={state.unitCol ?? undefined}
            onChange={(unitCol) => setState((prev) => ({ ...prev, unitCol }))}
            options={state.columns.map((c) => ({ value: c, label: c }))}
          />
        </Space>
      )}

      <div style={{ minHeight: 300 }}>
        {current === 0 && <Step1Data state={state} setState={setState} />}
        {current === 1 && <Step2GroupsMetrics state={state} setState={setState} />}
        {current === 2 && <Step3Parameters state={state} setState={setState} />}
        {current === 3 && <Step4Review state={state} />}
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
