import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Col, Row, Segmented, Statistic, Table, Tooltip, Typography, Space } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { colors } from '../../theme/tokens'
import { chartColors } from '../../charts/theme'
import { MonitoringLineChart } from '../../charts/MonitoringLineChart'
import { formatBytes, formatMb } from '../../monitoringFormat'

// Item 6 (audit-details+ package): moved out of a tab on the Admin > Users
// page (pages/Admin.tsx) into its own route/page — Monitoring isn't related
// to user management, it just happened to share the admin-only gate. Same
// component, same behavior, just its own Settings menu entry instead of a
// second tab most admins never opened while looking at the user list.

// Disk-free stat card turns to a warning once less than this fraction of
// total disk space remains — matches the feature request's "warning
// highlight when disk free < 15%".
const DISK_FREE_WARNING_THRESHOLD = 0.15

const RANGE_OPTIONS = [
  { label: '24h', value: '24h' },
  { label: '7d', value: '7d' },
  { label: '30d', value: '30d' },
  { label: '90d', value: '90d' },
] as const

type RangeValue = (typeof RANGE_OPTIONS)[number]['value']

const RANGE_HOURS: Record<RangeValue, number> = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30, '90d': 24 * 90 }

// Raw (60s) points are only kept 24h — anything longer must read the
// downsampled hourly rows (abkit/monitoring.py's retention policy), or the
// query would just come back empty past the 24h mark.
function resolutionFor(range: RangeValue): 'raw' | 'hourly' {
  return range === '24h' ? 'raw' : 'hourly'
}

function StatCard({
  title,
  value,
  warning,
  tooltip,
}: {
  title: string
  value: string
  warning?: boolean
  tooltip?: string
}) {
  return (
    <Card size="small" style={warning ? { borderColor: colors.warning, background: '#FFFBE6' } : undefined}>
      <Statistic
        title={
          tooltip ? (
            <Tooltip title={tooltip}>
              <span>{title}</span>
            </Tooltip>
          ) : (
            title
          )
        }
        value={value}
        valueStyle={warning ? { color: colors.warning } : undefined}
        prefix={warning ? <WarningOutlined /> : undefined}
      />
    </Card>
  )
}

export function MonitoringPage() {
  const [range, setRange] = useState<RangeValue>('24h')

  const { data: current, isLoading: currentLoading } = useQuery({
    queryKey: queryKeys.monitoringCurrent(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/monitoring/current')
      if (error) throw new Error(errorMessage(error))
      return data
    },
    // Feels "live" while the page is open, without hammering the collector
    // — matches the collector's own 60s snapshot cadence.
    refetchInterval: 60_000,
  })

  // Memoized on `range` only — NOT recomputed on every render. dayjs() is
  // "now", so computing from/to inline in the component body would mint a
  // brand new query key (and abandon the in-flight fetch) on every
  // unrelated re-render this component gets (e.g. the /current query above
  // resolving), never letting the history query settle.
  const { from, to, resolution } = useMemo(() => {
    const now = dayjs()
    return {
      from: now.subtract(RANGE_HOURS[range], 'hour').toISOString(),
      to: now.toISOString(),
      resolution: resolutionFor(range),
    }
  }, [range])

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: queryKeys.monitoringHistory(from, to, resolution),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/monitoring/history', {
        params: { query: { from, to, resolution } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const points = history?.points ?? []
  const diskFreePct =
    current?.disk_free_mb != null && current?.disk_total_mb ? current.disk_free_mb / current.disk_total_mb : null
  const diskWarning = diskFreePct != null && diskFreePct < DISK_FREE_WARNING_THRESHOLD

  // Item B2/B3: aggregated ranges (7d/30d/90d) carry stored hourly min/max
  // alongside the avg — 24h stays raw points, with no min/max to show (the
  // schema already returns them as null there, nothing to branch on here).
  const memoryReferenceLine =
    current?.backend_mem_limit_mb != null
      ? { value: current.backend_mem_limit_mb, label: 'memory limit' }
      : undefined

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 16 }}>
        Monitoring
      </Typography.Title>

      {/* Item A2 (DB bloat package): live per-request check (see
          backend/routers/admin.py::_current_payload), not the collector's
          own weekly-cadence LOG warning — an admin looking at this page
          shouldn't have to wait up to a week to see accurate bloat state. */}
      {current?.bloated_tables && current.bloated_tables.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="High table bloat detected"
          description={
            <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
              {current.bloated_tables.map((t) => (
                <li key={t.table_name}>
                  <Typography.Text code>{t.table_name}</Typography.Text> — {t.dead_pct.toFixed(1)}% dead rows,{' '}
                  {t.size_mb.toFixed(0)} MB. VACUUM FULL recommended during a maintenance window (see
                  docs/OPERATIONS.md).
                </li>
              ))}
            </ul>
          }
        />
      )}

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <StatCard
            title="Backend memory"
            value={currentLoading ? '…' : formatMb(current?.backend_rss_mb)}
            tooltip="Resident memory (RSS) of the backend process right now"
          />
        </Col>
        <Col span={6}>
          <StatCard
            title="Database size"
            value={currentLoading ? '…' : formatMb(current?.db_total_mb)}
            tooltip="Total Postgres database size (pg_database_size)"
          />
        </Col>
        <Col span={6}>
          <StatCard
            title="Data volume"
            value={currentLoading ? '…' : formatMb(current?.data_volume_mb)}
            tooltip="Total size of the data directory on disk (experiment reports, uploaded/materialized datasets) — recomputed at most every 5 minutes, not on every snapshot"
          />
        </Col>
        <Col span={6}>
          <StatCard
            title="Disk free"
            value={
              currentLoading
                ? '…'
                : `${formatMb(current?.disk_free_mb)}${
                    current?.disk_total_mb ? ` / ${formatMb(current.disk_total_mb)}` : ''
                  }`
            }
            warning={diskWarning}
            tooltip={
              diskWarning
                ? 'Less than 15% of disk space is free'
                : 'Free space on the volume backing the data directory'
            }
          />
        </Col>
      </Row>

      <Space style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">Range:</Typography.Text>
        <Segmented options={[...RANGE_OPTIONS]} value={range} onChange={(v) => setRange(v as RangeValue)} />
      </Space>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={12}>
          <Typography.Text strong>Backend memory</Typography.Text>
          {historyLoading ? (
            <div style={{ height: 300 }} />
          ) : (
            <MonitoringLineChart
              yAxisLabel="MB"
              referenceLine={memoryReferenceLine}
              series={[
                {
                  name: 'Backend memory',
                  color: chartColors.significantPositive,
                  points: points.map((p) => ({
                    ts: p.ts, value: p.backend_rss_mb,
                    min: p.backend_rss_mb_min, max: p.backend_rss_mb_max,
                  })),
                },
              ]}
            />
          )}
        </Col>
        <Col span={12}>
          <Typography.Text strong>Database + data volume size</Typography.Text>
          {historyLoading ? (
            <div style={{ height: 300 }} />
          ) : (
            <MonitoringLineChart
              yAxisLabel="MB"
              series={[
                {
                  name: 'Database',
                  color: chartColors.significantPositive,
                  points: points.map((p) => ({
                    ts: p.ts, value: p.db_total_mb, min: p.db_total_mb_min, max: p.db_total_mb_max,
                  })),
                },
                {
                  name: 'Data volume',
                  color: colors.warning,
                  points: points.map((p) => ({
                    ts: p.ts, value: p.data_volume_mb, min: p.data_volume_mb_min, max: p.data_volume_mb_max,
                  })),
                },
              ]}
            />
          )}
        </Col>
      </Row>

      <Typography.Title level={5}>Top 10 tables by size</Typography.Title>
      <Table
        size="small"
        loading={currentLoading}
        rowKey="table_name"
        dataSource={current?.top_tables ?? []}
        pagination={false}
        columns={[
          { title: 'Table', dataIndex: 'table_name' },
          {
            title: 'Size',
            dataIndex: 'size_bytes',
            render: (bytes: number) => formatBytes(bytes),
          },
        ]}
      />
    </div>
  )
}
