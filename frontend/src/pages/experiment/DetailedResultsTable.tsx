import { Table, Button, Tag, Tooltip, Space } from 'antd'
import { DownloadOutlined, InfoCircleOutlined } from '@ant-design/icons'
import type { TestResultOut } from './analyzeTypes'
import { verdict } from './analyzeTypes'

const VERDICT_LABELS: Record<string, string> = {
  significant_positive: 'significant positive',
  significant_negative: 'significant negative',
  no_effect_detected: 'no effect detected',
  failed: 'failed',
}
const VERDICT_COLORS: Record<string, string> = {
  significant_positive: 'success',
  significant_negative: 'error',
  no_effect_detected: 'default',
  failed: 'warning',
}

interface Row {
  key: string
  metric: string
  comparison: string
  method: string
  designed: boolean
  effect_abs: number | null
  effect_rel: number | null
  ci_rel: [number | null, number | null]
  p_value: number | null
  p_value_adjusted: number | null
  correction: string
  n_control: number | undefined
  n_test: number | undefined
  variance_reduction: number | null
  verdictKey: string
  // Set only for a failed alternative method (compare_methods=True) — see
  // Experiment.analyze()'s extra_chains loop / _failed_method_result.
  failureReason: string | null
}

function failureReasonOf(r: TestResultOut): string | null {
  const failedWarning = r.warnings.find((w) => w.startsWith('failed: '))
  return failedWarning ? failedWarning.slice('failed: '.length) : null
}

function toRows(results: TestResultOut[], controlName: string, correction: string): Row[] {
  return results
    .map((r) => ({
      key: `${r.metric}_${r.method}_${r.treatment_group}`,
      metric: r.metric,
      comparison: `${r.treatment_group} vs ${controlName}`,
      method: r.method,
      designed: r.is_designed_method,
      effect_abs: r.effect_abs,
      effect_rel: r.effect_rel,
      ci_rel: r.ci_rel,
      p_value: r.p_value,
      p_value_adjusted: r.p_value_adjusted,
      correction: r.p_value_adjusted !== null ? correction : 'none',
      n_control: r.n[controlName],
      n_test: r.n[r.treatment_group],
      variance_reduction: r.variance_reduction,
      verdictKey: verdict(r),
      failureReason: failureReasonOf(r),
    }))
    .sort((a, b) => a.metric.localeCompare(b.metric) || a.method.localeCompare(b.method))
}

function toCsv(rows: Row[]): string {
  const headers = [
    'Metric', 'Comparison group', 'Method', 'Effect (abs.)', 'Lift %',
    '95% CI of lift', 'p-value', 'p-value (adj.)', 'Correction', 'n (control)', 'n (test)',
    'Variance reduction', 'Verdict', 'Failure reason',
  ]
  const lines = [headers.join(',')]
  for (const r of rows) {
    const ciRel = r.ci_rel[0] !== null && r.ci_rel[1] !== null
      ? `"[${(r.ci_rel[0] * 100).toFixed(2)}%, ${(r.ci_rel[1] * 100).toFixed(2)}%]"`
      : ''
    const cells = [
      r.metric, r.comparison, r.method,
      r.effect_abs !== null ? String(r.effect_abs) : '',
      r.effect_rel !== null ? String(r.effect_rel * 100) : '',
      ciRel,
      r.p_value !== null ? String(r.p_value) : '',
      r.p_value_adjusted !== null ? String(r.p_value_adjusted) : '',
      r.correction, r.n_control ?? '', r.n_test ?? '',
      r.variance_reduction !== null ? String(r.variance_reduction) : '',
      VERDICT_LABELS[r.verdictKey],
      r.failureReason ? `"${r.failureReason.replace(/"/g, '""')}"` : '',
    ]
    lines.push(cells.join(','))
  }
  return lines.join('\n')
}

function downloadCsv(csv: string, filename: string) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function HeaderWithTooltip({ label, tooltip }: { label: string; tooltip: string }) {
  return (
    <Space size={4}>
      {label}
      <Tooltip title={tooltip}>
        <InfoCircleOutlined style={{ color: '#8c8c8c' }} />
      </Tooltip>
    </Space>
  )
}

export function DetailedResultsTable({
  results, controlName, correction, experimentName,
}: {
  results: TestResultOut[]
  controlName: string
  correction: string
  experimentName: string
}) {
  const rows = toRows(results, controlName, correction)

  return (
    <div>
      <Button
        icon={<DownloadOutlined />}
        onClick={() => downloadCsv(toCsv(rows), `${experimentName}_detailed_results.csv`)}
        style={{ marginBottom: 12 }}
      >
        Export CSV
      </Button>
      <Table
        size="small"
        rowKey="key"
        dataSource={rows}
        pagination={false}
        scroll={{ x: true }}
        // Designed method is the one the decision is based on — with no
        // "Designed" column anymore (UX package, 5.1), bolding the row is
        // the only remaining signal when compare_methods shows several rows
        // per metric.
        rowClassName={(record) => (record.designed ? 'detailed-results-designed-row' : '')}
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Comparison group', dataIndex: 'comparison' },
          {
            title: 'Method', dataIndex: 'method',
            render: (v: string, record: Row) =>
              record.failureReason ? (
                <Tooltip title={record.failureReason}>
                  <span>{v}</span>
                </Tooltip>
              ) : (
                v
              ),
          },
          {
            title: <HeaderWithTooltip label="Effect (abs.)" tooltip="Absolute difference in metric units (test − control)" />,
            dataIndex: 'effect_abs', render: (v: number | null) => (v === null ? '—' : v.toFixed(4)),
          },
          {
            title: <HeaderWithTooltip label="Lift %" tooltip="Relative effect: (test − control) / control" />,
            dataIndex: 'effect_rel', render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(2)}%`),
          },
          {
            title: <HeaderWithTooltip label="95% CI of lift" tooltip="Confidence interval of the relative effect (lift), not of the metric itself" />,
            dataIndex: 'ci_rel',
            render: (v: [number | null, number | null]) =>
              v[0] === null || v[1] === null ? '—' : `[${(v[0] * 100).toFixed(2)}%, ${(v[1] * 100).toFixed(2)}%]`,
          },
          { title: 'p-value', dataIndex: 'p_value', render: (v: number | null) => (v === null ? '—' : v.toFixed(4)) },
          {
            title: (
              <HeaderWithTooltip
                label="p-value (adj.)"
                tooltip="p-value adjusted for multiple comparisons (see Correction). Decision is made on this value. Equals raw p-value when there is only one primary hypothesis"
              />
            ),
            dataIndex: 'p_value_adjusted', render: (v: number | null) => (v === null ? '—' : v.toFixed(4)),
          },
          { title: 'Correction', dataIndex: 'correction' },
          { title: 'n (control)', dataIndex: 'n_control' },
          { title: 'n (test)', dataIndex: 'n_test' },
          {
            title: 'Variance reduction', dataIndex: 'variance_reduction',
            render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`),
          },
          {
            title: 'Verdict', dataIndex: 'verdictKey',
            render: (v: string) => <Tag color={VERDICT_COLORS[v]}>{VERDICT_LABELS[v]}</Tag>,
          },
        ]}
      />
    </div>
  )
}
