import { Table, Button, Tag } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import type { TestResultOut } from './analyzeTypes'
import { verdict } from './analyzeTypes'

const VERDICT_LABELS: Record<string, string> = {
  significant_positive: 'significant positive',
  significant_negative: 'significant negative',
  no_effect_detected: 'no effect detected',
}
const VERDICT_COLORS: Record<string, string> = {
  significant_positive: 'success',
  significant_negative: 'error',
  no_effect_detected: 'default',
}

interface Row {
  key: string
  metric: string
  comparison: string
  method: string
  designed: boolean
  effect_abs: number
  effect_rel: number
  ci_rel: [number, number]
  p_value: number
  p_value_adjusted: number | null
  correction: string
  n_control: number | undefined
  n_test: number | undefined
  variance_reduction: number | null
  verdictKey: string
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
    }))
    .sort((a, b) => a.metric.localeCompare(b.metric) || a.method.localeCompare(b.method))
}

function toCsv(rows: Row[]): string {
  const headers = [
    'Metric', 'Comparison group', 'Method', 'Designed', 'Effect (abs)', 'Effect (rel, %)',
    '95% CI (rel., %)', 'p-value', 'p-adj', 'Correction', 'n (control)', 'n (test)',
    'Variance reduction', 'Verdict',
  ]
  const lines = [headers.join(',')]
  for (const r of rows) {
    const cells = [
      r.metric, r.comparison, r.method, r.designed ? '1' : '0',
      String(r.effect_abs), String(r.effect_rel * 100),
      `"[${(r.ci_rel[0] * 100).toFixed(2)}%, ${(r.ci_rel[1] * 100).toFixed(2)}%]"`,
      String(r.p_value), r.p_value_adjusted !== null ? String(r.p_value_adjusted) : '',
      r.correction, r.n_control ?? '', r.n_test ?? '',
      r.variance_reduction !== null ? String(r.variance_reduction) : '',
      VERDICT_LABELS[r.verdictKey],
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
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Comparison group', dataIndex: 'comparison' },
          { title: 'Method', dataIndex: 'method' },
          { title: 'Designed', dataIndex: 'designed', render: (v: boolean) => (v ? '✓' : '') },
          { title: 'Effect (abs)', dataIndex: 'effect_abs', render: (v: number) => v.toFixed(4) },
          { title: 'Effect (rel, %)', dataIndex: 'effect_rel', render: (v: number) => `${(v * 100).toFixed(2)}%` },
          {
            title: '95% CI (rel.)', dataIndex: 'ci_rel',
            render: (v: [number, number]) => `[${(v[0] * 100).toFixed(2)}%, ${(v[1] * 100).toFixed(2)}%]`,
          },
          { title: 'p-value', dataIndex: 'p_value', render: (v: number) => v.toFixed(4) },
          { title: 'p-adj', dataIndex: 'p_value_adjusted', render: (v: number | null) => (v === null ? '—' : v.toFixed(4)) },
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
