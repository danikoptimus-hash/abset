import { Typography, Table, Tag, Space, Button, Alert } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { getComputed } from './types'
import type { ComputedDesignSummary } from './types'

interface Props {
  name: string
  config: Record<string, unknown>
  availableReports: string[]
}

function CheckBadge({ label, passed, detail }: { label: string; passed: boolean; detail: string }) {
  return (
    <Space direction="vertical" size={0} style={{ marginRight: 24 }}>
      <Tag color={passed ? 'success' : 'error'}>{label}: {passed ? 'OK' : 'провалена'}</Tag>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {detail}
      </Typography.Text>
    </Space>
  )
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
    { title: 'Метрика', dataIndex: 'metric' },
    { title: 'Baseline', dataIndex: 'baseline', render: (v: number | null) => (v == null ? '—' : v.toFixed(4)) },
    { title: 'MDE (отн.)', dataIndex: 'mde_rel', render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`) },
    { title: 'n на группу', dataIndex: 'n_per_group', render: (v: number | null) => v ?? '—' },
    ...(hasCuped
      ? [
          { title: 'MDE (отн., CUPED)', dataIndex: 'mde_rel_cuped', render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`) },
          { title: 'n на группу (CUPED)', dataIndex: 'n_per_group_cuped', render: (v: number | null) => v ?? '—' },
        ]
      : []),
  ]

  return <Table size="small" dataSource={rows} columns={columns} pagination={false} />
}

export function DesignSection({ name, config, availableReports }: Props) {
  const computed = getComputed(config)

  return (
    <div>
      <Typography.Title level={4}>Дизайн</Typography.Title>

      <Typography.Title level={5}>Конфигурация</Typography.Title>
      <pre style={{ background: '#F7F7F7', padding: 12, borderRadius: 4, overflow: 'auto', fontSize: 12, marginBottom: 24 }}>
        {JSON.stringify(config, (k, v) => (k === 'computed' ? undefined : v), 2)}
      </pre>

      {computed ? (
        <>
          <Typography.Title level={5}>MDE-таблица</Typography.Title>
          {mdeTable(computed)}

          <Typography.Title level={5} style={{ marginTop: 24 }}>
            Проверки честности сплита
          </Typography.Title>
          <Space wrap style={{ marginBottom: 16 }}>
            <CheckBadge
              label="SRM"
              passed={computed.srm.passed}
              detail={`p-value=${computed.srm.p_value.toExponential(2)}`}
            />
            <CheckBadge
              label="Баланс страт"
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
        <Alert type="info" showIcon message="Сводка дизайна недоступна для этого эксперимента." style={{ marginBottom: 16 }} />
      )}

      <Space>
        <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${name}/samples.zip`}>
          Скачать выборки (ZIP)
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
