import { Table, Button, Tag, Tooltip, Space } from 'antd'
import { DownloadOutlined, InfoCircleOutlined } from '@ant-design/icons'
import type { TestResultOut } from './analyzeTypes'
import { verdict } from './analyzeTypes'
import type { AnalyzeMetric } from './types'
import { isManuallySelected } from './methodOptions'

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
  // Item 3.2: outlier-removal count per group — surfaced as a tooltip on
  // the Variance reduction cell when this row's reduction came from
  // RemoveOutliers, referencing the same counts already shown implicitly
  // via n_control/n_test (post-trim n, not the raw upload count).
  n_removed: Record<string, number>
  cuped_rho: number | null
  verdictKey: string
  // Set only for a failed alternative method (compare_methods=True) — see
  // Experiment.analyze()'s extra_chains loop / _failed_method_result.
  failureReason: string | null
  // True when this is a Mann-Whitney/Hodges-Lehmann row whose estimate is
  // exactly 0 while some other method for the same metric/group found a
  // non-zero effect — legitimate on heavily zero-inflated data, but worth
  // an explanatory tooltip so it doesn't read as a bug.
  hlZeroOnSkewedData: boolean
  // 6-part package pt.3.2: primary-first ordering — see toRows()'s sort.
  role: TestResultOut['role']
  // Item 2.3/2.6: true when this designed row's method differs from the
  // type/config-based recommended default (methodOptions.ts::
  // isManuallySelected) — i.e. the user picked a method manually on the
  // Analysis tab instead of leaving the recommended one. Meaningless for
  // non-designed (compare_methods alternative) rows.
  manuallySelected: boolean
}

function failureReasonOf(r: TestResultOut): string | null {
  const failedWarning = r.warnings.find((w) => w.startsWith('failed: '))
  return failedWarning ? failedWarning.slice('failed: '.length) : null
}

const HL_WARNING = 'Hodges-Lehmann median shift'

// Compare_methods=True can recompute the exact same designed chain again as
// one of its "alternative" chains (e.g. a metric with no pre_col has
// designed=[Welch t-test] and the first alt chain is also plain Welch) —
// same (metric, method, treatment_group), same numbers except p-adj. Keep
// only the designed row (UX-package dedup); genuinely different alternative
// methods are untouched.
function dedupeDesignedDuplicates(results: TestResultOut[]): TestResultOut[] {
  const designedKeys = new Set(
    results.filter((r) => r.is_designed_method).map((r) => `${r.metric} ${r.method} ${r.treatment_group}`),
  )
  return results.filter(
    (r) => r.is_designed_method || !designedKeys.has(`${r.metric} ${r.method} ${r.treatment_group}`),
  )
}

// Item 4.1 (consolidated package): 3 decimal places across every numeric
// column (effect/lift/CI/p-value/p-adj/rho — n stays a plain int), matching
// abkit/analysis/results.py::detailed_display_rows()'s CSV/report formatting.
function fmt3(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

// Same rounding, but blank (not '—') for null — matches the backend CSV's
// convention (csv.DictWriter renders a None cell as empty, not em-dash;
// '—' is a table-display-only placeholder).
function fmtCsv3(v: number | null): string {
  return v === null ? '' : v.toFixed(3)
}

function toRows(
  results: TestResultOut[],
  controlName: string,
  correction: string,
  alpha: number,
  metrics: AnalyzeMetric[],
): Row[] {
  const deduped = dedupeDesignedDuplicates(results)
  const metricsByName = new Map(metrics.map((m) => [m.name, m]))
  return deduped
    .map((r) => {
      const isHlRow = r.warnings.some((w) => w.includes(HL_WARNING))
      const hlZeroOnSkewedData =
        isHlRow &&
        r.effect_abs === 0 &&
        deduped.some(
          (other) =>
            other !== r &&
            other.metric === r.metric &&
            other.treatment_group === r.treatment_group &&
            other.effect_abs !== null &&
            other.effect_abs !== 0,
        )
      const metricInfo = metricsByName.get(r.metric)
      const manuallySelected =
        r.is_designed_method && !!metricInfo && isManuallySelected(r.method, metricInfo.type, metricInfo.hasPreCol)
      return {
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
        n_removed: r.n_removed,
        // ?? null: results.json persisted before this field existed (older
        // analyzed experiments, no backend schema/migration for this JSON
        // blob) won't have the key at all — undefined, not null.
        cuped_rho: r.cuped_rho ?? null,
        verdictKey: verdict(r, alpha),
        failureReason: failureReasonOf(r),
        hlZeroOnSkewedData,
        manuallySelected,
        role: r.role,
      }
    })
    // 6-part package pt.3.2: primary metrics before secondary — a stable
    // sort, so ties (same role) keep the backend's own order, which is
    // already metric-declaration-order with the designed row immediately
    // before its compare_methods alternatives (Experiment.analyze()'s
    // append order, AnalysisResults.__init__). Replaces the old flat
    // alphabetical (metric, method) sort, which had no notion of "designed
    // first" and could alphabetize an alternative ahead of its own metric's
    // designed row.
    .sort((a, b) => (a.role === b.role ? 0 : a.role === 'primary' ? -1 : 1))
}

function toCsv(rows: Row[]): string {
  const headers = [
    'Metric', 'Comparison group', 'Method', 'Effect (abs.)', 'Lift %',
    '95% CI of lift', 'p-value', 'p-value (adj.)', 'Correction', 'n (control)', 'n (test)',
    'Variance reduction', 'CUPED rho', 'Verdict', 'Failure reason',
  ]
  const lines = [headers.join(',')]
  for (const r of rows) {
    const ciRel = r.ci_rel[0] !== null && r.ci_rel[1] !== null
      ? `"[${(r.ci_rel[0] * 100).toFixed(3)}%, ${(r.ci_rel[1] * 100).toFixed(3)}%]"`
      : ''
    const cells = [
      r.metric, r.comparison, r.method,
      fmtCsv3(r.effect_abs),
      r.effect_rel !== null ? (r.effect_rel * 100).toFixed(3) : '',
      ciRel,
      fmtCsv3(r.p_value),
      fmtCsv3(r.p_value_adjusted),
      r.correction, r.n_control ?? '', r.n_test ?? '',
      fmtCsv3(r.variance_reduction),
      fmtCsv3(r.cuped_rho),
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
  results, controlName, correction, experimentName, showCorrection, alpha, metrics,
}: {
  results: TestResultOut[]
  controlName: string
  correction: string
  experimentName: string
  // false when the hypothesis family has only one member (one primary
  // metric × one treatment group) — p-value (adj.) then trivially equals
  // the raw p-value, so showing it would just duplicate the column
  // (5-part package pt.5.1).
  showCorrection: boolean
  // Item 2: the experiment's own configured significance level — the
  // Verdict column (and CSV export) must agree with the same threshold
  // Design/Analysis/the HTML report use (abkit/experiment.py's
  // config.alpha), not a hardcoded 0.05 independent of what the experiment
  // was actually designed with.
  alpha: number
  // Item 2.3/2.6: type/pre-col per metric, used to derive whether a
  // designed row's method was manually picked rather than the recommended
  // default (methodOptions.ts::isManuallySelected) — rendered as a tag in
  // the Method column.
  metrics: AnalyzeMetric[]
}) {
  const rows = toRows(results, controlName, correction, alpha, metrics)

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
            render: (v: string, record: Row) => {
              // Item 2.3/2.6: designed-and-manually-selected rows get an
              // explicit "manually selected" tag — with compare_methods on,
              // several rows share a metric and only the bolded designed row
              // should carry this label, so it's gated on record.designed too.
              const manualTag = record.designed && record.manuallySelected ? (
                <Tag color="blue">manually selected</Tag>
              ) : null
              if (record.failureReason) {
                return (
                  <Space size={4}>
                    <Tooltip title={record.failureReason}>
                      <span>{v}</span>
                    </Tooltip>
                    {manualTag}
                  </Space>
                )
              }
              if (record.hlZeroOnSkewedData) {
                return (
                  <Space size={4}>
                    {v}
                    <Tooltip title="Hodges-Lehmann estimates the median shift. With many zero values in the metric it is legitimately 0 — this reflects the skewed distribution, not an error.">
                      <InfoCircleOutlined style={{ color: '#8c8c8c' }} />
                    </Tooltip>
                    {manualTag}
                  </Space>
                )
              }
              if (manualTag) {
                return (
                  <Space size={4}>
                    {v}
                    {manualTag}
                  </Space>
                )
              }
              return v
            },
          },
          {
            title: <HeaderWithTooltip label="Effect (abs.)" tooltip="Absolute difference in metric units (test − control)" />,
            dataIndex: 'effect_abs', render: (v: number | null) => fmt3(v),
          },
          {
            title: <HeaderWithTooltip label="Lift %" tooltip="Relative effect: (test − control) / control" />,
            dataIndex: 'effect_rel', render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(3)}%`),
          },
          {
            title: <HeaderWithTooltip label="95% CI of lift" tooltip="Confidence interval of the relative effect (lift), not of the metric itself" />,
            dataIndex: 'ci_rel',
            render: (v: [number | null, number | null]) =>
              v[0] === null || v[1] === null ? '—' : `[${(v[0] * 100).toFixed(3)}%, ${(v[1] * 100).toFixed(3)}%]`,
          },
          { title: 'p-value', dataIndex: 'p_value', render: (v: number | null) => fmt3(v) },
          ...(showCorrection
            ? [
                {
                  title: (
                    <HeaderWithTooltip
                      label="p-value (adj.)"
                      tooltip="p-value adjusted for multiple comparisons (see Correction). Decision is made on this value."
                    />
                  ),
                  dataIndex: 'p_value_adjusted', render: (v: number | null) => fmt3(v),
                },
                { title: 'Correction', dataIndex: 'correction' },
              ]
            : []),
          { title: 'n (control)', dataIndex: 'n_control' },
          { title: 'n (test)', dataIndex: 'n_test' },
          {
            title: (
              <HeaderWithTooltip
                label="Variance reduction"
                tooltip="How much lower the effect estimate's variance is versus the raw (untreated) data — from CUPED, outlier removal, or post-stratification. Blank (—) when the method uses none of these techniques."
              />
            ),
            dataIndex: 'variance_reduction',
            render: (v: number | null, record: Row) => {
              if (v === null) {
                return (
                  <Tooltip title="No variance reduction technique applied">
                    <span>—</span>
                  </Tooltip>
                )
              }
              const pct = `${(v * 100).toFixed(3)}%`
              // Item 3.2: for a RemoveOutliers-driven row, the tooltip
              // references the same n_removed counts already used to compute
              // n (control)/n (test) (post-trim), instead of duplicating
              // them as their own columns.
              if (record.method.includes('RemoveOutliers')) {
                const removedEntries = Object.entries(record.n_removed)
                  .filter(([, n]) => n > 0)
                  .map(([group, n]) => `${group}: ${n}`)
                  .join(', ')
                return (
                  <Tooltip title={removedEntries ? `Outliers removed — ${removedEntries}` : 'Outliers removed'}>
                    <span>{pct}</span>
                  </Tooltip>
                )
              }
              return pct
            },
          },
          {
            title: (
              <HeaderWithTooltip
                label="CUPED ρ"
                tooltip="Correlation between metric and its pre-period covariate; variance reduction ≈ ρ²"
              />
            ),
            dataIndex: 'cuped_rho', render: (v: number | null) => fmt3(v),
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
