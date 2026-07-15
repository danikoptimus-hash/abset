import { useState } from 'react'
import { Collapse, Table, Typography, Tag, Button, Alert, Space, Spin } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import { groupsToApi, groupsSum, metricsToApi } from './types'
import type { WizardState } from './types'

interface StrataPowerRow {
  stratum: string
  treatment_group: string
  metric: string
  n_control: number
  n_treatment: number
  mde_rel: number | null
  mde_rel_cuped: number | null
  status: 'ok' | 'weak' | 'insufficient'
}

const STATUS_COLOR: Record<StrataPowerRow['status'], string> = { ok: 'success', weak: 'warning', insufficient: 'error' }

function fmtMde(v: number | null): string {
  return v === null ? '—' : `${(v * 100).toFixed(1)}%`
}

function DimensionTable({ rows, multiGroup, multiMetric }: { rows: StrataPowerRow[]; multiGroup: boolean; multiMetric: boolean }) {
  return (
    <Table
      size="small"
      rowKey={(r) => `${r.stratum}_${r.treatment_group}_${r.metric}`}
      pagination={false}
      dataSource={rows}
      style={{ marginBottom: 16 }}
      columns={[
        { title: 'Stratum', dataIndex: 'stratum' },
        ...(multiGroup ? [{ title: 'vs. group', dataIndex: 'treatment_group' }] : []),
        ...(multiMetric ? [{ title: 'Metric', dataIndex: 'metric' }] : []),
        { title: 'n (control)', dataIndex: 'n_control' },
        { title: 'n (test)', dataIndex: 'n_treatment' },
        { title: 'MDE (rel.)', dataIndex: 'mde_rel', render: (v: number | null) => fmtMde(v) },
        { title: 'MDE with CUPED', dataIndex: 'mde_rel_cuped', render: (v: number | null) => fmtMde(v) },
        {
          title: 'Status', dataIndex: 'status',
          render: (v: StrataPowerRow['status']) => <Tag color={STATUS_COLOR[v]}>{v}</Tag>,
        },
      ]}
    />
  )
}

function overallStatus(dimensions: Record<string, StrataPowerRow[]>): 'ok' | 'weak' | 'insufficient' {
  const rows = Object.values(dimensions).flat()
  if (rows.some((r) => r.status === 'insufficient')) return 'insufficient'
  if (rows.some((r) => r.status === 'weak')) return 'weak'
  return 'ok'
}

// Item 2.1: a plain-language summary above the tables — names the specific
// combined-stratum segments that aren't analyzable, rather than making the
// user scan every table to find them (the per-dimension ones are usually
// fine since they're never smaller than the combined ones).
function buildSummary(
  dimensions: Record<string, StrataPowerRow[]>, perDimensionLabels: string[], combinedLabel: string | undefined,
): string {
  const perDimensionRows = perDimensionLabels.flatMap((l) => dimensions[l] ?? [])
  const perDimensionBad = perDimensionRows.some((r) => r.status !== 'ok')
  const combinedRows = combinedLabel ? dimensions[combinedLabel] ?? [] : []
  const badCombinedStrata = [...new Set(combinedRows.filter((r) => r.status !== 'ok').map((r) => r.stratum))]

  const parts: string[] = []
  parts.push(
    perDimensionBad
      ? 'Some per-dimension segments are underpowered'
      : 'At the current split, per-dimension segments are analyzable',
  )
  if (combinedLabel) {
    parts.push(
      badCombinedStrata.length > 0
        ? `combined segments ${badCombinedStrata.join(', ')} are underpowered (increase total sample or the affected group's share)`
        : 'combined segments are analyzable too',
    )
  }
  return parts.join('; ') + '.'
}

// Item 2 (strata power check): a stratified split can be balanced overall
// while still leaving individual strata (and especially combined strata
// like gender × country) without enough data for a reliable segment-level
// analysis — this surfaces that at design time, using the group
// proportions ALREADY chosen (SampleSizeSection, above). Purely
// informational (item 2.3) — never blocks Next.
export function StrataPowerSection({ state }: { state: WizardState }) {
  const [dimensions, setDimensions] = useState<Record<string, StrataPowerRow[]> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [everOpened, setEverOpened] = useState(false)

  const canCheck =
    state.strata.length > 0 && !!state.datasetId && !!state.unitCol && Math.abs(groupsSum(state) - 1) < 1e-6

  const runCheck = async () => {
    if (!canCheck || !state.datasetId || !state.unitCol) return
    setLoading(true)
    setError(null)
    try {
      const { data, error: reqError } = await apiClient.POST('/api/v1/datasets/{dataset_id}/strata-power-preview', {
        params: { path: { dataset_id: state.datasetId } },
        body: {
          unit_col: state.unitCol,
          groups: groupsToApi(state),
          metrics: metricsToApi(state),
          strata: state.strata,
          alpha: state.alpha,
          power: state.power,
          isolation: state.isolation,
          exclude_experiments: 'all_active',
          isolation_selected_experiments: state.isolation === 'exclude_selected' ? state.isolationSelected : [],
          experiment_name: state.name.trim() || undefined,
        },
      })
      if (reqError) throw new Error(errorMessage(reqError))
      setDimensions(data.dimensions)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to check strata power')
    } finally {
      setLoading(false)
    }
  }

  if (!canCheck) return null

  const perDimensionLabels = state.strata.filter((s) => dimensions?.[s])
  const combinedLabel = dimensions ? Object.keys(dimensions).find((k) => !state.strata.includes(k)) : undefined
  const multiGroup = new Set(Object.values(dimensions ?? {}).flat().map((r) => r.treatment_group)).size > 1
  const multiMetric = new Set(Object.values(dimensions ?? {}).flat().map((r) => r.metric)).size > 1
  const status = dimensions ? overallStatus(dimensions) : null

  return (
    <Collapse
      style={{ marginTop: 16 }}
      onChange={(keys) => {
        if (keys.length > 0 && !everOpened) {
          setEverOpened(true)
          void runCheck()
        }
      }}
      items={[
        {
          key: 'strata-power',
          label: (
            <Space>
              Strata power check
              {status && <Tag color={STATUS_COLOR[status]}>{status}</Tag>}
            </Space>
          ),
          children: (
            <div>
              <Space style={{ marginBottom: 12 }}>
                <Button size="small" onClick={runCheck} loading={loading}>
                  {dimensions ? 'Refresh' : 'Check'}
                </Button>
              </Space>
              {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
              {loading && !dimensions && <Spin />}
              {dimensions && (
                <>
                  <Typography.Paragraph type="secondary">
                    {buildSummary(dimensions, perDimensionLabels, combinedLabel)}
                  </Typography.Paragraph>
                  {perDimensionLabels.map((label) => (
                    <div key={label}>
                      <Typography.Text strong>{label}</Typography.Text>
                      <DimensionTable rows={dimensions[label]} multiGroup={multiGroup} multiMetric={multiMetric} />
                    </div>
                  ))}
                  {combinedLabel && (
                    <Collapse
                      size="small"
                      items={[
                        {
                          key: 'combined',
                          label: `Combined strata (${combinedLabel})`,
                          children: (
                            <DimensionTable
                              rows={dimensions[combinedLabel]}
                              multiGroup={multiGroup}
                              multiMetric={multiMetric}
                            />
                          ),
                        },
                      ]}
                    />
                  )}
                </>
              )}
            </div>
          ),
        },
      ]}
    />
  )
}
