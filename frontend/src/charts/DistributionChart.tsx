import { useState } from 'react'
import ReactECharts from 'echarts-for-react'
import { Segmented, Typography } from 'antd'
import { chartColors } from './theme'
import type { Distribution, Histogram } from '../pages/experiment/analyzeTypes'

const CONTROL_COLOR = '#8C9AA6'
const TREATMENT_COLOR = chartColors.significantPositive

function binLabels(edges: number[]): string[] {
  const labels: string[] = []
  for (let i = 0; i < edges.length - 1; i++) {
    labels.push(`${edges[i].toFixed(1)}–${edges[i + 1].toFixed(1)}`)
  }
  return labels
}

function ContinuousDistributionChart({
  distribution, controlName, treatName,
}: {
  distribution: Extract<Distribution, { kind: 'continuous' }>
  controlName: string
  treatName: string
}) {
  const [range, setRange] = useState<'clipped' | 'full_range'>('clipped')
  const hist: Histogram = distribution[range]

  const option = {
    grid: [
      { left: 60, right: 20, top: 30, height: '38%' },
      { left: 60, right: 20, top: '58%', height: '32%' },
    ],
    tooltip: { trigger: 'axis' },
    legend: { data: [controlName, treatName], top: 0 },
    xAxis: [
      { type: 'category', data: binLabels(hist.bin_edges), gridIndex: 0, axisLabel: { show: false } },
      {
        type: 'value', gridIndex: 1, name: 'Value', scale: true,
        axisLine: { lineStyle: { color: chartColors.axisLine } },
      },
    ],
    yAxis: [
      { type: 'value', name: 'Density', gridIndex: 0, axisLine: { lineStyle: { color: chartColors.axisLine } } },
      { type: 'value', name: 'ECDF', gridIndex: 1, min: 0, max: 1, axisLine: { lineStyle: { color: chartColors.axisLine } } },
    ],
    series: [
      {
        name: controlName, type: 'bar', data: hist.control_counts, xAxisIndex: 0, yAxisIndex: 0,
        barGap: '-100%', itemStyle: { color: CONTROL_COLOR, opacity: 0.55 },
      },
      {
        name: treatName, type: 'bar', data: hist.treatment_counts, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: TREATMENT_COLOR, opacity: 0.55 },
      },
      {
        name: controlName, type: 'line', data: distribution.control_ecdf, xAxisIndex: 1, yAxisIndex: 1,
        showSymbol: false, lineStyle: { color: CONTROL_COLOR },
      },
      {
        name: treatName, type: 'line', data: distribution.treatment_ecdf, xAxisIndex: 1, yAxisIndex: 1,
        showSymbol: false, lineStyle: { color: TREATMENT_COLOR },
      },
    ],
  }

  return (
    <div>
      {distribution.n_above_p99 > 0 && (
        <Segmented
          size="small"
          value={range}
          onChange={(v) => setRange(v as 'clipped' | 'full_range')}
          options={[
            { label: 'Clipped at P99', value: 'clipped' },
            { label: 'Full range', value: 'full_range' },
          ]}
          style={{ marginBottom: 8 }}
        />
      )}
      <ReactECharts option={option} style={{ height: 420 }} />
      {range === 'clipped' && distribution.n_above_p99 > 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          For clarity, the axis is clipped at the 99th percentile ({distribution.p99_threshold?.toFixed(4)}).{' '}
          {distribution.n_above_p99} observations ({distribution.pct_above_p99.toFixed(1)}%) are above the threshold.
        </Typography.Text>
      )}
    </div>
  )
}

function renderVerticalErrorBar(
  categoryIndex: number,
  lo: number,
  hi: number,
  point: number,
  color: string,
  api: { coord: (v: [number, number]) => [number, number] },
) {
  const loCoord = api.coord([categoryIndex, lo])
  const hiCoord = api.coord([categoryIndex, hi])
  const midCoord = api.coord([categoryIndex, point])
  const capHalf = 6
  return {
    type: 'group' as const,
    children: [
      { type: 'line' as const, shape: { x1: loCoord[0], y1: loCoord[1], x2: hiCoord[0], y2: hiCoord[1] }, style: { stroke: color, lineWidth: 2 } },
      { type: 'line' as const, shape: { x1: loCoord[0] - capHalf, y1: loCoord[1], x2: loCoord[0] + capHalf, y2: loCoord[1] }, style: { stroke: color, lineWidth: 2 } },
      { type: 'line' as const, shape: { x1: hiCoord[0] - capHalf, y1: hiCoord[1], x2: hiCoord[0] + capHalf, y2: hiCoord[1] }, style: { stroke: color, lineWidth: 2 } },
      { type: 'circle' as const, shape: { cx: midCoord[0], cy: midCoord[1], r: 0 }, style: { fill: color } },
    ],
  }
}

function BinaryDistributionChart({
  distribution, controlName, treatName,
}: {
  distribution: Extract<Distribution, { kind: 'binary' }>
  controlName: string
  treatName: string
}) {
  const categories = [controlName, treatName]
  const props = [distribution.control.prop * 100, distribution.treatment.prop * 100]
  const data = [
    [0, distribution.control.ci_lo * 100, distribution.control.ci_hi * 100, props[0]],
    [1, distribution.treatment.ci_lo * 100, distribution.treatment.ci_hi * 100, props[1]],
  ]

  const option = {
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: categories, axisLine: { lineStyle: { color: chartColors.axisLine } } },
    yAxis: { type: 'value', name: 'Rate, %', axisLine: { lineStyle: { color: chartColors.axisLine } } },
    series: [
      {
        type: 'bar',
        data: props,
        itemStyle: {
          color: (params: { dataIndex: number }) => (params.dataIndex === 0 ? CONTROL_COLOR : TREATMENT_COLOR),
          opacity: 0.6,
        },
        barWidth: '50%',
      },
      {
        type: 'custom',
        renderItem: (_params: unknown, api: { value: (i: number) => number; coord: (v: [number, number]) => [number, number] }) =>
          renderVerticalErrorBar(
            api.value(0), api.value(1), api.value(2), api.value(3),
            api.value(0) === 0 ? CONTROL_COLOR : TREATMENT_COLOR, api,
          ),
        encode: { x: 0, y: [1, 2] },
        data,
      },
    ],
  }

  return <ReactECharts option={option} style={{ height: 350 }} />
}

export function DistributionChart({
  distribution, controlName, treatName,
}: {
  distribution: Distribution
  controlName: string
  treatName: string
}) {
  if (distribution.kind === 'binary') {
    return <BinaryDistributionChart distribution={distribution} controlName={controlName} treatName={treatName} />
  }
  return <ContinuousDistributionChart distribution={distribution} controlName={controlName} treatName={treatName} />
}
