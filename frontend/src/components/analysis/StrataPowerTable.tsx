import { Typography, Tag, Table, Collapse } from 'antd'
import { useAuth } from '../../auth/AuthContext'
import type { StrataPowerRow } from '../../pages/experiment/types'

// Visibility package: the strata power check (per-stratum achievable MDE at the
// design's split). Mirrors StrataBalanceTable — collapsed by default when it
// has many strata (> 12 rows), summary always visible, expand state persisted
// per-user (users.strata_power_expanded). Same threshold + summary text as the
// HTML reports (templates/_strata_power_section.html.j2) so app and file read
// identically. One block per metric, each with a table per dimension.
const COLLAPSE_THRESHOLD = 12

const STATUS_COLOR: Record<StrataPowerRow['status'], string> = {
  ok: 'success', weak: 'warning', insufficient: 'error',
}

const fmtMde = (v: number | null): string => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)

// Mirror of abkit/viz/report.py::strata_power_view — group the stored
// {dimension: [row]} into per-metric blocks + collapse metadata.
function toView(strataPower: Record<string, StrataPowerRow[]>) {
  const allRows = Object.values(strataPower).flat()
  const nRows = allRows.length
  const nWeak = allRows.filter((r) => r.status !== 'ok').length
  const multiGroup = new Set(allRows.map((r) => r.treatment_group)).size > 1
  const metrics: string[] = []
  for (const r of allRows) if (!metrics.includes(r.metric)) metrics.push(r.metric)
  const blocks = metrics.map((metric) => ({
    metric,
    dimensions: Object.entries(strataPower)
      .map(([label, rows]) => ({ label, rows: rows.filter((r) => r.metric === metric) }))
      .filter((d) => d.rows.length > 0),
  }))
  return { blocks, nRows, nWeak, multiGroup }
}

function DimensionTable({ rows, multiGroup }: { rows: StrataPowerRow[]; multiGroup: boolean }) {
  return (
    <Table
      size="small"
      pagination={false}
      rowKey={(r) => `${r.stratum}_${r.treatment_group}`}
      dataSource={rows}
      style={{ marginBottom: 12 }}
      columns={[
        { title: 'Stratum', dataIndex: 'stratum' },
        ...(multiGroup ? [{ title: 'vs. group', dataIndex: 'treatment_group' }] : []),
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

export function StrataPowerTable({ strataPower }: { strataPower: Record<string, StrataPowerRow[]> }) {
  const { user, updatePreferences } = useAuth()
  const view = toView(strataPower)
  if (view.nRows === 0) return null

  const summary = (
    <>
      Strata power check: {view.nRows} strata ·{' '}
      {view.nWeak > 0 ? <strong>{view.nWeak} weak</strong> : `${view.nWeak} weak`}
    </>
  )

  const body = (
    <>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        Achievable MDE inside each stratum at the chosen split — a balanced split can still leave individual
        strata underpowered for a segment-level analysis.
      </Typography.Paragraph>
      {view.blocks.map((block) => (
        <div key={block.metric}>
          <Typography.Text strong>{block.metric}</Typography.Text>
          {block.dimensions.map((dim) => (
            <div key={dim.label}>
              <Typography.Text type="secondary" style={{ display: 'block', margin: '4px 0' }}>
                By {dim.label}
              </Typography.Text>
              <DimensionTable rows={dim.rows} multiGroup={view.multiGroup} />
            </div>
          ))}
        </div>
      ))}
    </>
  )

  const header = (
    <Typography.Text strong>
      {summary}
    </Typography.Text>
  )

  if (view.nRows <= COLLAPSE_THRESHOLD) {
    return (
      <div style={{ marginTop: 16, marginBottom: 8 }}>
        <div style={{ marginBottom: 8 }}>{header}</div>
        {body}
      </div>
    )
  }

  const expanded = user?.strata_power_expanded ?? false
  const onChange = (keys: string | string[]) => {
    const isOpen = (Array.isArray(keys) ? keys : [keys]).includes('power')
    void updatePreferences({ strata_power_expanded: isOpen }).catch(() => {})
  }

  return (
    <div style={{ marginTop: 16, marginBottom: 8 }}>
      <Collapse
        activeKey={expanded ? ['power'] : []}
        onChange={onChange}
        items={[{ key: 'power', label: header, children: body }]}
      />
    </div>
  )
}
