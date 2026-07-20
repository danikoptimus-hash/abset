import { useEffect, useState } from 'react'
import { Typography, Radio, InputNumber, Select, Alert, Space, Tooltip } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { SIZE_MODE_LABELS, SPLIT_METHOD_LABELS, ISOLATION_LABELS, NAN_STRATEGY_LABELS } from './helpTexts'
import { SampleSizeSection } from './SampleSizeSection'
import { StrataPowerSection } from './StrataPowerSection'
import type { WizardState } from './types'
import { PRODUCT_NAME } from '../../branding'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
  isRedesign: boolean
}

function missingStats(state: WizardState, column: string): { count: number; pct: number } {
  const sampled = state.previewRows
  const missing = sampled.filter((r) => r[column] === null || r[column] === undefined || r[column] === '').length
  return { count: missing, pct: sampled.length ? (missing / sampled.length) * 100 : 0 }
}

export function Step3Parameters({ state, setState, isRedesign }: Props) {
  const isExternal = state.splitMode === 'external'
  const [baselineMean, setBaselineMean] = useState<number | null | 'loading'>(null)

  const mdeAbsMetric = state.metrics.find((m) => m.id === state.mdeAbsMetricId)
  const isBinaryAbsMde = mdeAbsMetric?.type === 'binary'

  useEffect(() => {
    if (state.sizeMode !== 'mde_abs' || !mdeAbsMetric || !state.datasetId) return
    let cancelled = false
    setBaselineMean('loading')
    apiClient
      .POST('/api/v1/datasets/{dataset_id}/metric-baseline', {
        params: { path: { dataset_id: state.datasetId } },
        body: {
          name: mdeAbsMetric.name,
          type: mdeAbsMetric.type,
          pre_col: mdeAbsMetric.preCol,
          num: mdeAbsMetric.num,
          den: mdeAbsMetric.den,
        },
      })
      .then(({ data }) => {
        if (!cancelled) setBaselineMean(data?.baseline_mean ?? null)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.sizeMode, state.mdeAbsMetricId, state.datasetId])

  const { data: activeExperiments } = useQuery({
    queryKey: queryKeys.activeExperimentsForIsolation(),
    enabled: state.isolation === 'exclude_selected',
    queryFn: async () => {
      const [designed, running] = await Promise.all([
        apiClient.GET('/api/v1/experiments', { params: { query: { status: 'designed', page_size: 200 } } }),
        apiClient.GET('/api/v1/experiments', { params: { query: { status: 'running', page_size: 200 } } }),
      ])
      const names = new Set<string>()
      for (const r of [designed.data, running.data]) {
        for (const item of r?.items ?? []) names.add(item.name)
      }
      return Array.from(names)
    },
  })

  const relFromAbs =
    typeof baselineMean === 'number' && baselineMean !== 0 ? state.mdeAbsValue / baselineMean : null

  if (isExternal) {
    // Item 12: no dataset means no baseline to compute an achievable MDE
    // from — an MDE table here would just be a guess dressed up as a
    // computation, so it's not offered at all. Split method and isolation
    // don't apply either (no split happening, no candidates to isolate) —
    // only an optional reference sample size. External split rework: strata
    // DO apply now (they drive the analysis balance check + segment
    // breakdown, not the split itself), and the sample size auto-fills from
    // a reference dataset's row count when one was selected.
    const hasColumns = state.columns.length > 0
    return (
      <div>
        <Typography.Title level={5}>Expected sample size (optional)</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ maxWidth: 560 }}>
          For reference only — the external system calculates its own power/MDE, so {PRODUCT_NAME} won't build an MDE
          table for this experiment.
          {hasColumns && ' Pre-filled from the reference dataset’s row count; edit it if you expect a different size.'}
        </Typography.Paragraph>
        <InputNumber
          addonBefore="Sample size"
          min={0}
          step={100}
          value={state.sampleSize}
          onChange={(v) => setState((prev) => ({ ...prev, sampleSize: v ?? 0 }))}
          style={{ width: 320 }}
        />

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          Strata / segment columns (optional)
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ maxWidth: 560 }}>
          {PRODUCT_NAME} can't stratify an external split (it already happened). These columns instead drive the
          analysis: a per-stratum balance check (was the outside split balanced across these attributes?) and the
          per-segment breakdown of the effect. {hasColumns
            ? 'Pick them from the reference dataset’s columns.'
            : 'Type the column names as they appear in the results you’ll analyze.'}
        </Typography.Paragraph>
        <Select
          mode={hasColumns ? 'multiple' : 'tags'}
          aria-label="external-strata-select"
          placeholder="Strata / segment columns (optional)"
          value={state.strata}
          onChange={(strata) => setState((prev) => ({ ...prev, strata }))}
          options={hasColumns ? state.columns.map((c) => ({ value: c, label: c })) : undefined}
          style={{ width: '100%', maxWidth: 560 }}
        />
      </div>
    )
  }

  return (
    <div>
      <Typography.Title level={5}>Experiment Size</Typography.Title>
      <Radio.Group
        value={state.sizeMode}
        onChange={(e) => setState((prev) => ({ ...prev, sizeMode: e.target.value }))}
        style={{ marginBottom: 16 }}
      >
        <Space direction="vertical">
          {Object.entries(SIZE_MODE_LABELS).map(([value, label]) => (
            <Radio key={value} value={value}>
              {label}
            </Radio>
          ))}
        </Space>
      </Radio.Group>

      {state.sizeMode === 'mde_rel' && (
        <InputNumber
          addonBefore="Relative MDE"
          min={0.0001}
          step={0.01}
          value={state.mdeRel}
          onChange={(v) => setState((prev) => ({ ...prev, mdeRel: v ?? 0.05 }))}
          style={{ marginBottom: 24, width: 320 }}
        />
      )}

      {state.sizeMode === 'mde_abs' && (
        <div style={{ marginBottom: 24 }}>
          <Select
            placeholder="Metric to set the absolute MDE for"
            style={{ width: 320, marginBottom: 8 }}
            value={state.mdeAbsMetricId ?? undefined}
            onChange={(mdeAbsMetricId) => setState((prev) => ({ ...prev, mdeAbsMetricId }))}
            options={state.metrics.filter((m) => m.name).map((m) => ({ value: m.id, label: m.name }))}
          />
          <br />
          {/* Item 1 bug fix: binary metrics store mde_abs everywhere else as
              a fraction (0.01 = 1 percentage point) — a bare number box with
              no unit cue is exactly how "1" (meant as "1%") gets typed and
              silently read as 1.0 (100 percentage points). For binary, this
              box shows/accepts PERCENTAGE POINTS and converts to the
              fraction state.mdeAbsValue on every keystroke — the rest of the
              app (relFromAbs below, Step4Review's submit) never sees pp,
              only the already-converted fraction, unchanged from before. */}
          <InputNumber
            addonBefore="Absolute MDE"
            addonAfter={isBinaryAbsMde ? 'pp' : undefined}
            min={0}
            step={isBinaryAbsMde ? 0.1 : 0.01}
            value={isBinaryAbsMde ? state.mdeAbsValue * 100 : state.mdeAbsValue}
            onChange={(v) =>
              setState((prev) => ({ ...prev, mdeAbsValue: isBinaryAbsMde ? (v ?? 0) / 100 : (v ?? 0) }))
            }
            style={{ width: 320 }}
          />
          {baselineMean === 'loading' && <Typography.Text type="secondary"> computing baseline...</Typography.Text>}
          {typeof baselineMean === 'number' && relFromAbs !== null && (
            <Typography.Paragraph type="secondary" style={{ marginTop: 4, marginBottom: 0 }}>
              ≈ {(relFromAbs * 100).toFixed(1)}% relative MDE at the current mean{' '}
              {isBinaryAbsMde ? `${(baselineMean * 100).toFixed(1)}%` : baselineMean.toFixed(4)}
            </Typography.Paragraph>
          )}
          {isBinaryAbsMde && typeof baselineMean === 'number' && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              1 pp = conversion {(baselineMean * 100).toFixed(1)}% → {((baselineMean + 0.01) * 100).toFixed(1)}%
            </Typography.Text>
          )}
          {baselineMean === null && mdeAbsMetric && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 8 }}
              message="Could not determine the baseline for this metric — a pre-period column with real values is needed"
            />
          )}
        </div>
      )}

      {state.sizeMode === 'sample_size' && (
        <InputNumber
          addonBefore="Sample size"
          min={1}
          step={100}
          value={state.sampleSize}
          onChange={(v) => setState((prev) => ({ ...prev, sampleSize: v ?? 1000 }))}
          style={{ marginBottom: 24, width: 320 }}
        />
      )}

      <Typography.Title level={5}>Statistical Parameters</Typography.Title>
      <Space style={{ marginBottom: 24 }}>
        <Tooltip title="Significance level (α) — the risk of a false positive: declaring an effect when there isn't one. Lower α = stricter evidence required, but needs more data.">
          <InputNumber
            addonBefore="Significance level (α)"
            min={0.001}
            max={0.2}
            step={0.01}
            value={state.alpha}
            onChange={(v) => setState((prev) => ({ ...prev, alpha: v ?? 0.05 }))}
            style={{ width: 260 }}
          />
        </Tooltip>
        <Tooltip title="Power — the chance of detecting a real effect of the target size, if one exists. Higher power = more confidence in a null result, but needs more data.">
          <InputNumber
            addonBefore="Power"
            min={0.5}
            max={0.99}
            step={0.01}
            value={state.power}
            onChange={(v) => setState((prev) => ({ ...prev, power: v ?? 0.8 }))}
            style={{ width: 200 }}
          />
        </Tooltip>
      </Space>

      <Typography.Title level={5}>Strata</Typography.Title>
      <Select
        mode="multiple"
        placeholder="Strata (optional)"
        style={{ width: '100%', marginBottom: 8 }}
        value={state.strata}
        onChange={(strata) => setState((prev) => ({ ...prev, strata }))}
        options={state.columns.map((c) => ({ value: c, label: c }))}
      />
      {state.strata.length > 0 && (
        <>
          <Select
            style={{ width: '100%', marginBottom: 8 }}
            value={state.nanStrategy}
            onChange={(nanStrategy) => setState((prev) => ({ ...prev, nanStrategy }))}
            options={Object.entries(NAN_STRATEGY_LABELS).map(([value, label]) => ({ value, label }))}
          />
          {state.strata.map((col) => {
            const { count, pct } = missingStats(state, col)
            if (count === 0) return null
            return (
              <Alert
                key={col}
                type="warning"
                showIcon
                style={{ marginBottom: 4 }}
                message={`"${col}": ~${pct.toFixed(1)}% missing (estimated from preview)`}
              />
            )
          })}
        </>
      )}

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        Split Method
      </Typography.Title>
      <Select
        style={{ width: '100%', marginBottom: 24 }}
        value={state.splitMethod}
        onChange={(splitMethod) => setState((prev) => ({ ...prev, splitMethod }))}
        options={Object.entries(SPLIT_METHOD_LABELS).map(([value, label]) => ({ value, label }))}
      />

      <Typography.Title level={5}>Isolation From Other Active Experiments</Typography.Title>
      <Select
        style={{ width: '100%', marginBottom: 8 }}
        value={state.isolation}
        onChange={(isolation) => setState((prev) => ({ ...prev, isolation }))}
        options={Object.entries(ISOLATION_LABELS).map(([value, label]) => ({ value, label }))}
      />
      {state.isolation === 'exclude_selected' && (
        <Select
          mode="multiple"
          placeholder="Experiments to exclude overlapping participants from"
          style={{ width: '100%' }}
          value={state.isolationSelected}
          onChange={(isolationSelected) => setState((prev) => ({ ...prev, isolationSelected }))}
          options={(activeExperiments ?? []).map((n) => ({ value: n, label: n }))}
          notFoundContent={activeExperiments?.length === 0 ? 'No active experiments to choose from' : undefined}
        />
      )}

      <SampleSizeSection state={state} setState={setState} isRedesign={isRedesign} />
      <StrataPowerSection state={state} />
    </div>
  )
}
