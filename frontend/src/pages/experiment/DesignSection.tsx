import { Typography, Table, Tag, Space, Button, Alert, Descriptions, Collapse, Spin, Tooltip } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import { RelativeTime } from '../../components/RelativeTime'
import { getComputed } from './types'
import type { ComputedDesignSummary } from './types'

interface Props {
  name: string
  config: Record<string, unknown>
  availableReports: string[]
}

interface RawMetric {
  name: string
  type: string
  role: string
  pre_col?: string | null
  num?: string | null
  den?: string | null
}

function CheckBadge({ label, passed, detail }: { label: string; passed: boolean; detail: string }) {
  return (
    <Space direction="vertical" size={0} style={{ marginRight: 24 }}>
      <Tag color={passed ? 'success' : 'error'}>{label}: {passed ? 'OK' : 'failed'}</Tag>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {detail}
      </Typography.Text>
    </Space>
  )
}

function formatGroups(config: Record<string, unknown>): string {
  const groups = config.groups as Record<string, number> | undefined
  if (!groups || Object.keys(groups).length === 0) return '—'
  return Object.entries(groups)
    .map(([n, p]) => `${n} ${(p * 100).toFixed(0)}%`)
    .join(' / ')
}

function formatMetric(m: RawMetric): string {
  if (m.type === 'ratio') {
    return `${m.name} — ${m.type}, ${m.role}, ${m.num ?? '?'}/${m.den ?? '?'}`
  }
  return `${m.name} — ${m.type}, ${m.role}${m.pre_col ? `, pre-period: ${m.pre_col}` : ''}`
}

function formatSizeMode(config: Record<string, unknown>): string {
  const mdeAbsInput = config.mde_abs_input as number | null | undefined
  const mdeSourceMetric = config.mde_source_metric as string | null | undefined
  const mde = config.mde as number | null | undefined
  const sampleSize = config.sample_size as number | null | undefined
  if (mdeAbsInput != null) {
    return `Target absolute MDE ${mdeAbsInput}${mdeSourceMetric ? ` (on ${mdeSourceMetric})` : ''}`
  }
  if (mde != null) return `Target relative MDE ${(mde * 100).toFixed(1)}%`
  if (sampleSize != null) return `Sample size ${sampleSize}`
  return 'All available data'
}

function formatIsolation(config: Record<string, unknown>): string {
  const isolation = config.isolation as string | undefined
  if (isolation === 'exclude') return 'exclude (all active experiments)'
  if (isolation === 'exclude_selected') {
    const selected = (config.isolation_selected_experiments as string[] | undefined) ?? []
    return `exclude selected: ${selected.join(', ') || '—'}`
  }
  if (isolation === 'warn') return 'warn (show overlap, ask for confirmation)'
  if (isolation === 'off') return 'off (no exclusion)'
  return isolation ?? '—'
}

function ConfigSummary({ config }: { config: Record<string, unknown> }) {
  const strata = (config.strata as string[] | undefined) ?? []
  const metrics = (config.metrics as RawMetric[] | undefined) ?? []
  const seed = config.seed as number | null | undefined

  return (
    <>
      <Descriptions bordered column={1} size="small" style={{ marginBottom: 24 }}>
        <Descriptions.Item label="Groups">{formatGroups(config)}</Descriptions.Item>
        <Descriptions.Item label="Metrics">
          <Space direction="vertical" size={2}>
            {metrics.length ? metrics.map((m, i) => <div key={i}>{formatMetric(m)}</div>) : '—'}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="Split method">
          {String(config.split_method ?? '—')}
          {strata.length > 0 ? ` (strata: ${strata.join(', ')})` : ''}
        </Descriptions.Item>
        <Descriptions.Item label="Sample size mode">{formatSizeMode(config)}</Descriptions.Item>
        <Descriptions.Item label="Isolation">{formatIsolation(config)}</Descriptions.Item>
        <Descriptions.Item label="Parameters">
          Missing values: {String(config.nan_strategy ?? '—')} · α={String(config.alpha ?? '—')} · power={String(config.power ?? '—')}
        </Descriptions.Item>
      </Descriptions>
      {seed != null && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Seed: {seed}
        </Typography.Text>
      )}
    </>
  )
}

// Below this |rho| threshold CUPED's variance reduction is negligible
// (~1% or less) — still a real, computed number, not omitted, just flagged
// so it isn't mistaken for a meaningful gain.
const CUPED_NEGLIGIBLE_RHO = 0.1

// A CUPED cell is never a bare, unexplained dash: null means no pre-period
// column was given at all (nothing to compute from), which is a different
// situation from "computed, but the correlation is too weak to matter" —
// each gets its own tooltip so the distinction is visible, not implied.
function cupedCell(value: number | null, rho: number | null, format: (v: number) => string) {
  if (value == null) {
    return (
      <Tooltip title="no pre-period column specified">
        <span>—</span>
      </Tooltip>
    )
  }
  if (rho != null && Math.abs(rho) < CUPED_NEGLIGIBLE_RHO) {
    return (
      <Tooltip title="low correlation, negligible gain">
        <span>{format(value)}</span>
      </Tooltip>
    )
  }
  return format(value)
}

function mdeTable(computed: ComputedDesignSummary) {
  const rows = Object.entries(computed.power).map(([metricName, p]) => ({
    key: metricName,
    metric: metricName,
    baseline: p.baseline_mean,
    n_per_group: p.sample_size_per_group,
    mde_rel: p.mde_rel,
    rho: p.rho,
    mde_rel_cuped: p.mde_rel_cuped,
    n_per_group_cuped: p.sample_size_per_group_cuped,
  }))
  const hasCuped = rows.some((r) => r.rho !== null && r.rho !== undefined)

  const columns = [
    { title: 'Metric', dataIndex: 'metric' },
    { title: 'Baseline', dataIndex: 'baseline', render: (v: number | null) => (v == null ? '—' : v.toFixed(4)) },
    { title: 'MDE (rel.)', dataIndex: 'mde_rel', render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`) },
    { title: 'n per group', dataIndex: 'n_per_group', render: (v: number | null) => v ?? '—' },
    ...(hasCuped
      ? [
          {
            title: 'MDE (rel., CUPED)',
            dataIndex: 'mde_rel_cuped',
            render: (v: number | null, record: (typeof rows)[number]) =>
              cupedCell(v, record.rho, (x) => `${(x * 100).toFixed(1)}%`),
          },
          {
            title: 'n per group (CUPED)',
            dataIndex: 'n_per_group_cuped',
            render: (v: number | null, record: (typeof rows)[number]) =>
              cupedCell(v, record.rho, (x) => String(x)),
          },
        ]
      : []),
  ]

  return <Table size="small" dataSource={rows} columns={columns} pagination={false} />
}

function DesignDataSection({ name }: { name: string }) {
  const { data: dataset, isFetching: datasetLoading } = useQuery({
    queryKey: ['experiment-design-dataset', name],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/design-dataset', {
        params: { path: { name } },
      })
      if (error) return null
      return data
    },
  })

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: ['experiment-design-dataset-preview', dataset?.id],
    enabled: !!dataset,
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
        params: { path: { dataset_id: dataset!.id }, query: { rows: 10 } },
      })
      return data?.rows ?? []
    },
  })

  return (
    <>
      <Typography.Title level={5} style={{ marginTop: 24 }}>Design Data</Typography.Title>
      {datasetLoading ? (
        <Spin size="small" />
      ) : dataset ? (
        <>
          <Descriptions bordered column={1} size="small" style={{ marginBottom: 16, maxWidth: 640 }}>
            <Descriptions.Item label="File">{dataset.filename}</Descriptions.Item>
            <Descriptions.Item label="Rows">{dataset.n_rows}</Descriptions.Item>
            <Descriptions.Item label="Columns">{dataset.columns.length}</Descriptions.Item>
            <Descriptions.Item label="Uploaded">
              <RelativeTime iso={dataset.uploaded_at} />
            </Descriptions.Item>
          </Descriptions>
          <Collapse
            size="small"
            style={{ marginBottom: 16 }}
            items={[
              {
                key: 'preview',
                label: 'Preview data',
                children: previewLoading ? (
                  <Spin size="small" />
                ) : preview && preview.length > 0 ? (
                  <Table
                    size="small"
                    dataSource={preview}
                    rowKey={(_, i) => String(i)}
                    pagination={false}
                    scroll={{ x: true }}
                    columns={Object.keys(preview[0]).map((k) => ({ title: k, dataIndex: k }))}
                  />
                ) : (
                  <Typography.Text type="secondary">No rows to preview.</Typography.Text>
                ),
              },
            ]}
          />
        </>
      ) : (
        <Alert type="info" showIcon message="No stored design data" style={{ marginBottom: 16 }} />
      )}
    </>
  )
}

export function DesignSection({ name, config, availableReports }: Props) {
  const computed = getComputed(config)

  return (
    <div>
      <Typography.Title level={5}>Configuration</Typography.Title>
      <ConfigSummary config={config} />

      <DesignDataSection name={name} />

      {computed ? (
        <>
          <Typography.Title level={5}>MDE Table</Typography.Title>
          {mdeTable(computed)}

          <Typography.Title level={5} style={{ marginTop: 24 }}>
            Split Sanity Checks
          </Typography.Title>
          <Space wrap style={{ marginBottom: 16 }}>
            <CheckBadge
              label="SRM"
              passed={computed.srm.passed}
              detail={`p-value=${computed.srm.p_value.toExponential(2)}`}
            />
            <CheckBadge
              label="Strata balance"
              passed={computed.strata_balance.passed}
              detail={`p-value=${computed.strata_balance.p_value.toFixed(4)}`}
            />
          </Space>
          {computed.pre_period_aa.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong>Pre-period A/A: </Typography.Text>
              <Space wrap>
                {computed.pre_period_aa.map((aa) => (
                  <Tag key={`${aa.metric}_${aa.treatment_group}`} color={aa.passed ? 'success' : 'error'}>
                    {aa.metric} vs {aa.treatment_group}: p={aa.p_value.toFixed(4)}
                  </Tag>
                ))}
              </Space>
            </div>
          )}
          {computed.warnings.length > 0 && (
            <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
              {computed.warnings.map((w, i) => (
                <Alert key={i} type="warning" showIcon message={w} />
              ))}
            </Space>
          )}
        </>
      ) : (
        <Alert type="info" showIcon message="Design summary is not available for this experiment." style={{ marginBottom: 16 }} />
      )}

      <Space>
        <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${name}/samples.zip`}>
          Download Samples (ZIP)
        </Button>
        {availableReports.includes('design_report.html') && (
          <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${name}/reports/design_report.html`} target="_blank">
            design_report.html
          </Button>
        )}
      </Space>
    </div>
  )
}
