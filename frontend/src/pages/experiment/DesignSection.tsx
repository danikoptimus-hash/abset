import { Typography, Table, Tag, Space, Button, Alert, Descriptions, Collapse, Spin, Tooltip, Image } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
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

// Stage 3: renders name + proportion + (if set) description per group —
// a plain joined string (the old formatGroups) can't carry a multiline
// description, so this became its own small block instead of a one-liner.
function GroupsDisplay({ config }: { config: Record<string, unknown> }) {
  const groups = (config.groups as Record<string, number> | undefined) ?? {}
  const descriptions = (config.group_descriptions as Record<string, string> | undefined) ?? {}
  const entries = Object.entries(groups)
  if (entries.length === 0) return <>—</>
  return (
    <Space direction="vertical" size={4}>
      {entries.map(([name, prop]) => (
        <div key={name}>
          <Typography.Text>
            {name} {(prop * 100).toFixed(0)}%
          </Typography.Text>
          {descriptions[name] && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {descriptions[name]}
              </Typography.Text>
            </div>
          )}
        </div>
      ))}
    </Space>
  )
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

// 6-part package pt.10: explicit stratification fields — "Stratified by: X,
// Y (N strata after combination, min stratum size: Z)" / "No
// stratification" / "Hash-based split (salt stored)". n_strata comes from
// computed.strata_balance (null for legacy/imported experiments with no
// computed summary — the sentence still names the fields, just without the
// after-combination count).
function formatStratification(config: Record<string, unknown>, nStrata: number | null): string {
  const strata = (config.strata as string[] | undefined) ?? []
  if (strata.length > 0) {
    const minStratumSize = config.min_stratum_size as number | null | undefined
    const suffix = nStrata != null ? ` (${nStrata} strata after combination, min stratum size: ${minStratumSize ?? '—'})` : ''
    return `Stratified by: ${strata.join(', ')}${suffix}`
  }
  if (config.split_method === 'hash') return 'Hash-based split (salt stored)'
  return 'No stratification'
}

function ConfigSummary({ config, computed }: { config: Record<string, unknown>; computed: ComputedDesignSummary | null }) {
  const metrics = (config.metrics as RawMetric[] | undefined) ?? []
  const seed = config.seed as number | null | undefined

  return (
    <>
      <Descriptions bordered column={1} size="small" style={{ marginBottom: 24 }}>
        <Descriptions.Item label="Groups">
          <GroupsDisplay config={config} />
        </Descriptions.Item>
        <Descriptions.Item label="Metrics">
          <Space direction="vertical" size={2}>
            {metrics.length ? metrics.map((m, i) => <div key={i}>{formatMetric(m)}</div>) : '—'}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="Split method">{String(config.split_method ?? '—')}</Descriptions.Item>
        <Descriptions.Item label="Stratification">
          {formatStratification(config, computed?.strata_balance.n_strata ?? null)}
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

// Stage 4 (CLAUDE.md, variant flow images) — item 4.4: absent entirely when
// there are no images (not an empty section), grouped by group_name in the
// order returned (already group_name, position from the backend), with the
// group's description (Stage 3) shown alongside its flow title for context.
function VariantFlowsSection({ name, config }: { name: string; config: Record<string, unknown> }) {
  const { data: images } = useQuery({
    queryKey: queryKeys.flowImages(name),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/flow-images', {
        params: { path: { name } },
      })
      if (error) throw error
      return data
    },
  })

  if (!images || images.length === 0) return null

  const descriptions = (config.group_descriptions as Record<string, string> | undefined) ?? {}
  const byGroup = new Map<string, typeof images>()
  for (const img of images) {
    const list = byGroup.get(img.group_name) ?? []
    list.push(img)
    byGroup.set(img.group_name, list)
  }

  return (
    <div style={{ marginTop: 24, marginBottom: 24 }}>
      <Typography.Title level={5}>Variant flows</Typography.Title>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {[...byGroup.entries()].map(([groupName, groupImages]) => (
          <div key={groupName} style={{ minWidth: 220 }}>
            {groupImages[0]?.flow_title && (
              <Typography.Text strong style={{ display: 'block' }}>
                {groupImages[0].flow_title}
              </Typography.Text>
            )}
            <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, marginBottom: 8 }}>
              {groupName}
              {descriptions[groupName] ? ` — ${descriptions[groupName]}` : ''}
            </Typography.Text>
            <Image.PreviewGroup>
              <Space wrap size={8}>
                {groupImages.map((img) => (
                  <Image
                    key={img.id}
                    src={`/api/v1/experiments/${name}/flow-images/${img.id}/file`}
                    width={84}
                    height={84}
                    style={{ objectFit: 'cover', borderRadius: 4 }}
                  />
                ))}
              </Space>
            </Image.PreviewGroup>
          </div>
        ))}
      </div>
    </div>
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

// abs = rel × baseline; binary metrics read as percentage points (the
// baseline is itself a proportion, so raw units would be a tiny fraction
// like 0.0096 — "pp" is what a reader expects from a conversion-rate MDE).
// Item 4.1: 3 decimal places, matching the rest of the MDE table.
function formatAbs(value: number | null, metricType: string): string {
  if (value == null) return '—'
  return metricType === 'binary' ? `${(value * 100).toFixed(3)} pp` : value.toFixed(3)
}

// Item 4.2: binary baseline shown as a percentage ("17.400%"), not a raw
// fraction (0.174) — continuous stays in the metric's own units, with
// thousands separators for readability on larger numbers.
function formatBaseline(value: number | null, metricType: string): string {
  if (value == null) return '—'
  if (metricType === 'binary') return `${(value * 100).toFixed(3)}%`
  return value.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })
}

// Item 1.2: ceil, not round — you need AT LEAST this many per group, so
// rounding down (or to nearest) would silently under-report the
// requirement. Comma-grouped since these are routinely 5-6 digit numbers.
function formatRequiredN(value: number | null): string {
  return value == null ? '—' : Math.ceil(value).toLocaleString('en-US')
}

function mdeTable(computed: ComputedDesignSummary) {
  const rows = Object.entries(computed.power)
    .map(([metricName, p]) => ({
      key: metricName,
      metric: metricName,
      metricType: p.metric_type,
      metricRole: p.metric_role,
      baseline: p.baseline_mean,
      n_per_group: p.sample_size_per_group,
      mde_rel: p.mde_rel,
      mde_abs: p.mde_abs,
      rho: p.rho,
      mde_rel_cuped: p.mde_rel_cuped,
      mde_abs_cuped: p.mde_abs_cuped,
      n_per_group_cuped: p.sample_size_per_group_cuped,
    }))
    // Item 3.1: primary metrics first — defense-in-depth stable sort (the
    // backend already emits computed.power in this order for designs run
    // after this fix, but experiments designed before it have this order
    // frozen into their persisted config; re-sorting here makes the Design
    // tab correct for those too, not just newly-designed experiments).
    .sort((a, b) => (a.metricRole === b.metricRole ? 0 : a.metricRole === 'primary' ? -1 : 1))
  const hasCuped = rows.some((r) => r.rho !== null && r.rho !== undefined)
  const hasSecondary = rows.some((r) => r.metricRole === 'secondary')

  const columns = [
    {
      title: 'Metric',
      dataIndex: 'metric',
      render: (v: string, record: (typeof rows)[number]) => (
        <Space size={4}>
          <Tag
            color={record.metricRole === 'primary' ? 'blue' : 'default'}
            style={{ fontSize: 11, lineHeight: '16px', marginInlineEnd: 0 }}
          >
            {record.metricRole}
          </Tag>
          {record.metricRole === 'secondary' ? (
            <Tooltip title="Secondary MDE is the minimal detectable effect at the chosen sample size (sample size is driven by primary metrics)">
              <span>
                {v} <sup>†</sup>
              </span>
            </Tooltip>
          ) : (
            v
          )}
        </Space>
      ),
    },
    {
      title: (
        <Tooltip title="Average value before the test — conversion rate, shown as %, for binary metrics">
          <span>Baseline</span>
        </Tooltip>
      ),
      dataIndex: 'baseline',
      render: (v: number | null, record: (typeof rows)[number]) => formatBaseline(v, record.metricType),
    },
    { title: 'MDE (rel.)', dataIndex: 'mde_rel', render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(3)}%`) },
    {
      title: (
        <Tooltip title="abs = rel × baseline">
          <span>MDE (abs.)</span>
        </Tooltip>
      ),
      dataIndex: 'mde_abs',
      render: (v: number | null, record: (typeof rows)[number]) => formatAbs(v, record.metricType),
    },
    {
      title: (
        <Tooltip title="Minimum group size to detect this metric's MDE at given α/power. Differs per metric (depends on its variance)">
          <span>Required n per group</span>
        </Tooltip>
      ),
      dataIndex: 'n_per_group',
      render: (v: number | null) => formatRequiredN(v),
    },
    ...(hasCuped
      ? [
          {
            title: 'MDE (rel., CUPED)',
            dataIndex: 'mde_rel_cuped',
            render: (v: number | null, record: (typeof rows)[number]) =>
              cupedCell(v, record.rho, (x) => `${(x * 100).toFixed(3)}%`),
          },
          {
            title: (
              <Tooltip title="abs = rel × baseline">
                <span>MDE (abs., CUPED)</span>
              </Tooltip>
            ),
            dataIndex: 'mde_abs_cuped',
            render: (v: number | null, record: (typeof rows)[number]) =>
              cupedCell(v, record.rho, (x) => formatAbs(x, record.metricType)),
          },
          {
            title: (
              <Tooltip title="Minimum group size to detect this metric's MDE with CUPED at given α/power. Differs per metric (depends on its variance)">
                <span>Required n per group (CUPED)</span>
              </Tooltip>
            ),
            dataIndex: 'n_per_group_cuped',
            render: (v: number | null, record: (typeof rows)[number]) => cupedCell(v, record.rho, formatRequiredN),
          },
        ]
      : []),
  ]

  // Item 1.3: the required-n column is per-metric — this line grounds it
  // against what the split actually produced, by real group name.
  const actualEntries = Object.entries(computed.group_sizes)

  return (
    <>
      <Table size="small" dataSource={rows} columns={columns} pagination={false} />
      {hasSecondary && (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
          † Secondary MDE is the minimal detectable effect at the chosen sample size (sample size is driven by
          primary metrics).
        </Typography.Text>
      )}
      {actualEntries.length > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          Actual group sizes: {actualEntries.map(([g, n]) => `${g} ${n.toLocaleString('en-US')}`).join(' · ')}
        </Typography.Text>
      )}
    </>
  )
}

// Item 1.4: per-metric power warnings (e.g. the implausible-sample-size
// units guard, or the pre-existing "not achievable" / "not enough data"
// ones) were already computed and sent to design_report.html — but never
// actually rendered on the Design tab itself, the page most people look at
// first. Grouped by metric so a reader can tell which row a warning is
// about without re-reading the MDE table above.
function powerWarnings(computed: ComputedDesignSummary) {
  const withWarnings = Object.entries(computed.power).filter(([, p]) => p.warnings.length > 0)
  if (withWarnings.length === 0) return null
  return (
    <Space direction="vertical" style={{ width: '100%', marginTop: 12, marginBottom: 16 }}>
      {withWarnings.flatMap(([metricName, p]) =>
        p.warnings.map((w, i) => (
          <Alert key={`${metricName}-${i}`} type="warning" showIcon message={`${metricName}: ${w}`} />
        )),
      )}
    </Space>
  )
}

function DesignDataSection({ name }: { name: string }) {
  const { data: dataset, isFetching: datasetLoading } = useQuery({
    queryKey: queryKeys.experimentDesignDataset(name),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/design-dataset', {
        params: { path: { name } },
      })
      if (error) return null
      return data
    },
  })

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: queryKeys.experimentDesignDatasetPreview(dataset?.id),
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
  const isExternal = config.split_source === 'external'

  // Item 6: real per-group download buttons (using actual group names, e.g.
  // "control.csv"/"treatment.csv") alongside the combined ZIP — the
  // motivation is that a product team only ever wants the treatment file
  // for rollout, and a combined ZIP risks the wrong group getting shipped.
  // Fetched from the same /samples list endpoint list_samples() already
  // uses to size the ZIP, so the buttons are exactly the files that exist
  // (not guessed from config.groups, which can differ from what was
  // actually split if the config changed after design).
  const { data: sampleFiles } = useQuery({
    queryKey: queryKeys.experimentSamples(name),
    enabled: !isExternal,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/samples', {
        params: { path: { name } },
      })
      if (error) return []
      return data
    },
  })

  return (
    <div>
      <Typography.Title level={5}>Configuration</Typography.Title>
      <ConfigSummary config={config} computed={computed} />

      <VariantFlowsSection name={name} config={config} />

      <DesignDataSection name={name} />

      {computed ? (
        <>
          <Typography.Title level={5}>MDE Table</Typography.Title>
          {mdeTable(computed)}
          {powerWarnings(computed)}

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
          {/* table/groups are absent on computed summaries persisted before
              this field existed (older designed experiments) — optional
              chaining so those don't crash, just skip the collapse. */}
          {computed.strata_balance.table?.length > 0 && (
            <Collapse
              size="small"
              style={{ marginBottom: 16 }}
              items={[
                {
                  key: 'strata-balance-table',
                  label: 'Strata balance table',
                  children: (
                    <Table
                      size="small"
                      dataSource={computed.strata_balance.table}
                      rowKey="stratum"
                      pagination={false}
                      columns={[
                        { title: 'Stratum', dataIndex: 'stratum' },
                        ...(computed.strata_balance.groups ?? []).map((g) => ({ title: g, dataIndex: g })),
                      ]}
                    />
                  ),
                },
              ]}
            />
          )}
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
      ) : isExternal ? (
        <Alert
          type="info"
          showIcon
          message="External design: power calculated by the external system"
          description="No dataset means no baseline to compute an MDE table from — the system that ran the split (e.g. Firebase A/B Testing) handles its own power calculation."
          style={{ marginBottom: 16, maxWidth: 640 }}
        />
      ) : (
        <Alert type="info" showIcon message="Design summary is not available for this experiment." style={{ marginBottom: 16 }} />
      )}

      <Space wrap>
        {!isExternal &&
          (sampleFiles ?? []).map((f) => (
            <Button
              key={f.filename}
              icon={<DownloadOutlined />}
              href={`/api/v1/experiments/${name}/samples/${encodeURIComponent(f.filename)}`}
            >
              Download {f.filename}
            </Button>
          ))}
        {!isExternal && (
          <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${name}/samples.zip`}>
            Download Samples (ZIP)
          </Button>
        )}
        {availableReports.includes('design_report.html') && (
          <>
            <Button icon={<EyeOutlined />} href={`/api/v1/experiments/${name}/reports/design_report.html`} target="_blank">
              View report
            </Button>
            <Button
              icon={<DownloadOutlined />}
              href={`/api/v1/experiments/${name}/reports/design_report.html?download=1`}
            >
              Download report
            </Button>
          </>
        )}
      </Space>
    </div>
  )
}
