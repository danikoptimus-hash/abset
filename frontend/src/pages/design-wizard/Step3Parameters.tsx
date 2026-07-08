import { useEffect, useState } from 'react'
import { Typography, Radio, InputNumber, Select, Alert, Space } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import { SIZE_MODE_LABELS, SPLIT_METHOD_LABELS, ISOLATION_LABELS, NAN_STRATEGY_LABELS } from './helpTexts'
import type { WizardState } from './types'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
}

function missingStats(state: WizardState, column: string): { count: number; pct: number } {
  const sampled = state.previewRows
  const missing = sampled.filter((r) => r[column] === null || r[column] === undefined || r[column] === '').length
  return { count: missing, pct: sampled.length ? (missing / sampled.length) * 100 : 0 }
}

export function Step3Parameters({ state, setState }: Props) {
  const [baselineMean, setBaselineMean] = useState<number | null | 'loading'>(null)

  const mdeAbsMetric = state.metrics.find((m) => m.id === state.mdeAbsMetricId)

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
    queryKey: ['active-experiments-for-isolation'],
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

  return (
    <div>
      <Typography.Title level={5}>Размер эксперимента</Typography.Title>
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
          addonBefore="Относительный MDE"
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
            placeholder="Метрика, для которой задается абсолютный MDE"
            style={{ width: 320, marginBottom: 8 }}
            value={state.mdeAbsMetricId ?? undefined}
            onChange={(mdeAbsMetricId) => setState((prev) => ({ ...prev, mdeAbsMetricId }))}
            options={state.metrics.filter((m) => m.name).map((m) => ({ value: m.id, label: m.name }))}
          />
          <br />
          <InputNumber
            addonBefore="Абсолютный MDE"
            step={0.01}
            value={state.mdeAbsValue}
            onChange={(v) => setState((prev) => ({ ...prev, mdeAbsValue: v ?? 0 }))}
            style={{ width: 320 }}
          />
          {baselineMean === 'loading' && <Typography.Text type="secondary"> считаем baseline...</Typography.Text>}
          {typeof baselineMean === 'number' && relFromAbs !== null && (
            <Typography.Paragraph type="secondary" style={{ marginTop: 4 }}>
              ≈ {(relFromAbs * 100).toFixed(1)}% относительного MDE при текущем среднем {baselineMean.toFixed(4)}
            </Typography.Paragraph>
          )}
          {baselineMean === null && mdeAbsMetric && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 8 }}
              message="Не удалось определить baseline для этой метрики — нужна pre-period колонка с реальными значениями"
            />
          )}
        </div>
      )}

      {state.sizeMode === 'sample_size' && (
        <InputNumber
          addonBefore="Размер выборки"
          min={1}
          step={100}
          value={state.sampleSize}
          onChange={(v) => setState((prev) => ({ ...prev, sampleSize: v ?? 1000 }))}
          style={{ marginBottom: 24, width: 320 }}
        />
      )}

      <Typography.Title level={5}>Страты</Typography.Title>
      <Select
        mode="multiple"
        placeholder="Страты (опционально)"
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
                message={`«${col}»: ~${pct.toFixed(1)}% пропусков (оценка по превью)`}
              />
            )
          })}
        </>
      )}

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        Метод сплита
      </Typography.Title>
      <Select
        style={{ width: '100%', marginBottom: 24 }}
        value={state.splitMethod}
        onChange={(splitMethod) => setState((prev) => ({ ...prev, splitMethod }))}
        options={Object.entries(SPLIT_METHOD_LABELS).map(([value, label]) => ({ value, label }))}
      />

      <Typography.Title level={5}>Изоляция от других активных экспериментов</Typography.Title>
      <Select
        style={{ width: '100%', marginBottom: 8 }}
        value={state.isolation}
        onChange={(isolation) => setState((prev) => ({ ...prev, isolation }))}
        options={Object.entries(ISOLATION_LABELS).map(([value, label]) => ({ value, label }))}
      />
      {state.isolation === 'exclude_selected' && (
        <Select
          mode="multiple"
          placeholder="Эксперименты, из которых исключить пересекающихся участников"
          style={{ width: '100%' }}
          value={state.isolationSelected}
          onChange={(isolationSelected) => setState((prev) => ({ ...prev, isolationSelected }))}
          options={(activeExperiments ?? []).map((n) => ({ value: n, label: n }))}
          notFoundContent={activeExperiments?.length === 0 ? 'Нет активных экспериментов для выбора' : undefined}
        />
      )}
    </div>
  )
}
