import { useState } from 'react'
import { Typography, Tag, Space, Segmented, Button, Tooltip, Alert } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { ForestPlotChart } from '../../charts/ForestPlotChart'
import { HelpCollapse } from '../../pages/experiment/HelpCollapse'
import { isUnderpowered, UNDERPOWERED_MIN_N_PER_GROUP } from '../../pages/experiment/segmentGuard'
import type { MetricChartData, SegmentEffect } from '../../pages/experiment/analyzeTypes'

// Per-metric segment breakdown: a "Segment by" switch across dimensions
// (single columns and country × platform combinations), a forest plot of the
// POWERED cells, and the underpowered cells (< 100 users/group) listed greyed
// with a badge instead of a (noisy) lift. Shared by the Analysis tab
// (AnalyzeResults) and the Results tab (post-hoc). onRemoveDimension, when
// given, renders a remove control on post-hoc dimensions.
export function SegmentBreakdown({
  metricChart,
  adHocDimensions,
  combinationDimensions,
  postHocDimensions,
  segmentSkips,
  onRemoveDimension,
}: {
  metricChart: MetricChartData
  adHocDimensions: string[]
  combinationDimensions: string[]
  postHocDimensions: string[]
  // Bugfix (ad-hoc segment columns must not be silently dropped): requested
  // cuts that produced no breakdown, with a reason — shown as a per-cut notice
  // so a requested column never just disappears from the section.
  segmentSkips?: { label: string; reason: string }[]
  onRemoveDimension?: (label: string) => void
}) {
  const [segmentDimension, setSegmentDimension] = useState<string | null>(null)
  const dimensionLabels = Object.keys(metricChart.segments_by_dimension)
  const activeDimension =
    segmentDimension && dimensionLabels.includes(segmentDimension) ? segmentDimension : dimensionLabels[0]
  const skips = segmentSkips ?? []

  // Only bail entirely when there is genuinely nothing to say — neither a
  // produced breakdown NOR a skip notice to explain a vanished cut.
  if (dimensionLabels.length === 0 && skips.length === 0) return null

  const controlName = metricChart.control_name
  const isCombo = activeDimension ? combinationDimensions.includes(activeDimension) : false
  const isAdHoc = activeDimension ? adHocDimensions.includes(activeDimension) : false
  const isPostHoc = activeDimension ? postHocDimensions.includes(activeDimension) : false

  return (
    <div>
      {dimensionLabels.length > 1 && activeDimension && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Typography.Text type="secondary">Segment by:</Typography.Text>
          <Segmented
            options={dimensionLabels}
            value={activeDimension}
            onChange={(v) => setSegmentDimension(v as string)}
          />
        </Space>
      )}
      {activeDimension && Object.entries(metricChart.segments_by_dimension[activeDimension] ?? {}).map(([treatName, segs]) => {
        const powered = segs.filter((s) => !underpowered(s))
        const weak = segs.filter((s) => underpowered(s))
        return (
          <div key={treatName}>
            <Typography.Title level={5}>
              By {activeDimension}: {controlName} vs {treatName} <Tag>exploratory</Tag>
              {isCombo && <Tag color="geekblue">combination</Tag>}
              {isAdHoc && <Tag color="orange">ad-hoc (not declared at design)</Tag>}
              {isPostHoc && <Tag color="purple">added post-hoc</Tag>}
              {isPostHoc && onRemoveDimension && (
                <Tooltip title="Remove this segment cut">
                  <Button
                    size="small"
                    type="text"
                    icon={<DeleteOutlined />}
                    aria-label={`remove-segment-${activeDimension}`}
                    onClick={() => onRemoveDimension(activeDimension)}
                  />
                </Tooltip>
              )}
            </Typography.Title>
            {powered.length > 0 && (
              <ForestPlotChart
                rows={powered.map((s) => ({
                  label: s.stratum,
                  effectRelPct: s.effect_rel * 100,
                  ciLoPct: s.ci_rel[0] * 100,
                  ciHiPct: s.ci_rel[1] * 100,
                  highlighted: false,
                  extraTooltipLines: [
                    `n: ${controlName}=${s.n[controlName] ?? '—'}, ${treatName}=${s.n[treatName] ?? '—'}`,
                  ],
                }))}
              />
            )}
            {weak.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Underpowered (&lt; {UNDERPOWERED_MIN_N_PER_GROUP} users/group — no lift shown):{' '}
                </Typography.Text>
                <Space wrap size={4} style={{ marginTop: 4 }}>
                  {weak.map((s) => (
                    <Tag key={s.stratum} color="default" style={{ color: 'rgba(0,0,0,0.45)' }}>
                      {s.stratum} <Tag color="warning" style={{ marginInlineStart: 4 }}>underpowered</Tag>
                    </Tag>
                  ))}
                </Space>
              </div>
            )}
            <HelpCollapse chartType="segment_forest" />
          </div>
        )
      })}
      {skips.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 12 }}
          message="Requested segment cuts not shown"
          description={
            <ul style={{ margin: 0, paddingInlineStart: 20 }}>
              {skips.map((s) => (
                <li key={s.label}>
                  <strong>{s.label}</strong> — {s.reason}
                </li>
              ))}
            </ul>
          }
        />
      )}
    </div>
  )
}

function underpowered(s: SegmentEffect): boolean {
  // Prefer the backend flag; fall back to recomputing from n for older runs.
  return s.underpowered ?? isUnderpowered(s.n)
}
