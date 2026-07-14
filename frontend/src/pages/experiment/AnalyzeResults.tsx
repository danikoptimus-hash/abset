import { useEffect, useState } from 'react'
import { Typography, Tag, Space, Card, Alert, Row, Col } from 'antd'
import { ForestPlotChart } from '../../charts/ForestPlotChart'
import { DistributionChart } from '../../charts/DistributionChart'
import { CumulativeLiftChart } from '../../charts/CumulativeLiftChart'
import { formatPValue } from '../../charts/tooltip'
import { HelpCollapse } from './HelpCollapse'
import { colors } from '../../theme/tokens'
import type { AnalysisResultsOut, TestResultOut } from './analyzeTypes'
import { resultsByMetric, verdict } from './analyzeTypes'

export const VERDICT_LABELS: Record<string, string> = {
  significant_positive: 'significant positive',
  significant_negative: 'significant negative',
  no_effect_detected: 'no effect detected',
  failed: 'failed',
}
export const VERDICT_COLORS: Record<string, string> = {
  significant_positive: 'success',
  significant_negative: 'error',
  no_effect_detected: 'default',
  failed: 'warning',
}

const ROLE_LABELS: Record<TestResultOut['role'], string> = { primary: 'primary', secondary: 'secondary' }

// selectedMetric/onSelectMetric: Analysis tab (only) turns cards into
// clickable tabs that filter the analytics wall below to one metric — see
// AnalyzeResults. Results tab renders the same cards non-interactively
// (omits onSelectMetric) — CLAUDE.md UX-package: "клик по карточке — только
// на вкладке Analysis".
export function VerdictCards({
  results,
  selectedMetric,
  onSelectMetric,
  alpha = 0.05,
}: {
  results: TestResultOut[]
  selectedMetric?: string
  onSelectMetric?: (metric: string) => void
  // Defaults to 0.05 only for callers that genuinely have no experiment
  // config in scope (there are none left after item 2 — kept as a safety
  // net, not a real fallback path).
  alpha?: number
}) {
  const designed = results.filter((r) => r.is_designed_method)
  const byMetric = resultsByMetric(designed)
  const interactive = !!onSelectMetric
  return (
    <Row gutter={16} style={{ marginBottom: 24 }}>
      {Object.entries(byMetric).map(([metric, rows]) =>
        rows.map((r) => {
          const v = verdict(r, alpha)
          const active = interactive && selectedMetric === metric
          return (
            <Col key={`${metric}_${r.treatment_group}`}>
              <Card
                size="small"
                hoverable={interactive}
                role={interactive ? 'tab' : undefined}
                aria-selected={interactive ? active : undefined}
                tabIndex={interactive ? 0 : undefined}
                onClick={interactive ? () => onSelectMetric!(metric) : undefined}
                onKeyDown={
                  interactive
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onSelectMetric!(metric)
                        }
                      }
                    : undefined
                }
                style={{
                  minWidth: 220,
                  cursor: interactive ? 'pointer' : undefined,
                  borderColor: active ? colors.primary : undefined,
                  boxShadow: active ? `0 0 0 2px ${colors.primary}40` : undefined,
                }}
              >
                <Space align="center" size={6}>
                  <Typography.Text strong>{metric}</Typography.Text>
                  <Tag color={r.role === 'primary' ? 'blue' : 'default'} style={{ fontSize: 11, lineHeight: '16px', marginInlineEnd: 0 }}>
                    {ROLE_LABELS[r.role]}
                  </Tag>
                </Space>
                <br />
                <Typography.Text type="secondary">{r.treatment_group} vs control</Typography.Text>
                <br />
                <Tag color={VERDICT_COLORS[v]} style={{ marginTop: 8 }}>
                  {VERDICT_LABELS[v]}
                </Tag>
                <Typography.Paragraph style={{ marginTop: 4, marginBottom: 0 }}>
                  {r.effect_rel === null || r.ci_rel[0] === null || r.ci_rel[1] === null
                    ? '—'
                    : `${(r.effect_rel * 100).toFixed(1)}% [${(r.ci_rel[0] * 100).toFixed(1)}%, ${(r.ci_rel[1] * 100).toFixed(1)}%]`}
                </Typography.Paragraph>
              </Card>
            </Col>
          )
        }),
      )}
    </Row>
  )
}

export function AnalyzeResults({ data, alpha }: { data: AnalysisResultsOut; alpha: number }) {
  const byMetric = resultsByMetric(data.results)
  const { checks } = data.chart_data
  const metricNames = Object.keys(byMetric)

  // Metric cards double as tabs here (UX-package: "аналитика раскрывается по
  // клику на карточку метрики") — the wall of plots below renders ONLY the
  // selected metric instead of every metric back to back. Defaults to the
  // first primary metric; falls back to the first metric at all (e.g. an
  // experiment with only secondary metrics) or resets if the previously
  // selected metric disappears (a re-run with a different metric set).
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null)
  useEffect(() => {
    if (metricNames.length === 0) return
    if (selectedMetric && metricNames.includes(selectedMetric)) return
    const firstPrimary = data.results.find((r) => r.is_designed_method && r.role === 'primary')
    setSelectedMetric(firstPrimary?.metric ?? metricNames[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.results])

  const activeMetric = selectedMetric && metricNames.includes(selectedMetric) ? selectedMetric : metricNames[0]
  const metricResults = activeMetric ? byMetric[activeMetric] : undefined
  const metricChart = activeMetric ? data.chart_data.metrics[activeMetric] : undefined

  return (
    <div>
      {data.global_warnings.length > 0 && (
        <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
          {data.global_warnings.map((w, i) => (
            <Alert key={i} type="warning" showIcon message={w} />
          ))}
        </Space>
      )}

      <VerdictCards results={data.results} selectedMetric={activeMetric} onSelectMetric={setSelectedMetric} alpha={alpha} />

      <Typography.Title level={5}>Sanity checks (on post-period data)</Typography.Title>
      <Space wrap style={{ marginBottom: 8 }}>
        {checks.srm && (
          <Tag color={checks.srm.passed ? 'success' : 'error'}>
            SRM: {checks.srm.passed ? 'OK' : 'failed'} (p={checks.srm.p_value.toExponential(2)})
          </Tag>
        )}
        {checks.loss && (
          <Tag color={checks.loss.symmetric ? 'success' : 'error'}>
            Data loss: {checks.loss.symmetric ? 'symmetric' : 'asymmetric'}
          </Tag>
        )}
      </Space>
      <HelpCollapse chartType="srm_table" table />

      {activeMetric && metricResults && (
        <div style={{ marginTop: 32 }}>
          <Typography.Title level={4}>{activeMetric}</Typography.Title>

          <Typography.Title level={5}>Forest plot</Typography.Title>
          <ForestPlotChart
            // Stage 1 e2e coverage (item 1.4): only the main forest plot
            // needs to be addressable from a test, not the per-segment ones.
            onChartReady={(instance) => {
              ;(window as unknown as { __abkitForestChart?: unknown }).__abkitForestChart = instance
            }}
            rows={metricResults
              // A failed alternative method (compare_methods=True) has no
              // usable effect/CI to plot — it's shown as a "failed" row in
              // the detailed table below instead.
              .filter((r) => r.effect_rel !== null && r.ci_rel[0] !== null && r.ci_rel[1] !== null)
              .map((r) => ({
                label: `${r.method} (${r.treatment_group})`,
                effectRelPct: r.effect_rel! * 100,
                ciLoPct: r.ci_rel[0]! * 100,
                ciHiPct: r.ci_rel[1]! * 100,
                highlighted: r.is_designed_method,
                extraTooltipLines: [`p-value: ${r.p_value === null ? '—' : formatPValue(r.p_value)}`],
              }))}
          />
          <HelpCollapse chartType="forest" />

          {metricChart &&
            Object.entries(metricChart.distributions).map(([treatName, dist]) => (
              <div key={treatName}>
                <Typography.Title level={5}>
                  Distribution: {metricChart.control_name} vs {treatName}
                </Typography.Title>
                <DistributionChart distribution={dist} controlName={metricChart.control_name} treatName={treatName} />
                <HelpCollapse chartType={dist.kind === 'binary' ? 'distribution_binary' : 'distribution_continuous'} />
              </div>
            ))}

          {metricChart &&
            Object.entries(metricChart.daily).map(([treatName, points]) => (
              <div key={treatName}>
                <Typography.Title level={5}>
                  Cumulative lift: {metricChart.control_name} vs {treatName}
                </Typography.Title>
                <CumulativeLiftChart points={points} />
                <HelpCollapse chartType="cumulative_lift" />
              </div>
            ))}

          {metricChart &&
            Object.entries(metricChart.segments).map(([treatName, segs]) => (
              <div key={treatName}>
                <Typography.Title level={5}>
                  By segment: {metricChart.control_name} vs {treatName} <Tag>exploratory</Tag>
                </Typography.Title>
                <ForestPlotChart
                  rows={segs.map((s) => ({
                    label: s.stratum,
                    effectRelPct: s.effect_rel * 100,
                    ciLoPct: s.ci_rel[0] * 100,
                    ciHiPct: s.ci_rel[1] * 100,
                    highlighted: false,
                    extraTooltipLines: [
                      `n: ${metricChart.control_name}=${s.n[metricChart.control_name] ?? '—'}, ` +
                        `${treatName}=${s.n[treatName] ?? '—'}`,
                    ],
                  }))}
                />
                <HelpCollapse chartType="segment_forest" />
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
