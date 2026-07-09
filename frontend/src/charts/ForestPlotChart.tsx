import ReactECharts from 'echarts-for-react'
import type { CustomSeriesRenderItemAPI, CustomSeriesRenderItemParams, CustomSeriesRenderItemReturn } from 'echarts'
import { chartColors } from './theme'

export interface ForestRow {
  label: string
  effectRelPct: number
  ciLoPct: number
  ciHiPct: number
  highlighted: boolean
}

function renderErrorBar(
  _params: CustomSeriesRenderItemParams,
  api: CustomSeriesRenderItemAPI,
): CustomSeriesRenderItemReturn {
  const categoryIndex = api.value(0) as number
  const lo = api.value(1) as number
  const hi = api.value(2) as number
  const point = api.value(3) as number
  const highlighted = api.value(4) === 1
  const color = highlighted ? chartColors.significantPositive : chartColors.notSignificant

  const loCoord = api.coord([lo, categoryIndex])
  const hiCoord = api.coord([hi, categoryIndex])
  const midCoord = api.coord([point, categoryIndex])
  const capHalf = 5

  return {
    type: 'group',
    children: [
      { type: 'line', shape: { x1: loCoord[0], y1: loCoord[1], x2: hiCoord[0], y2: hiCoord[1] }, style: { stroke: color, lineWidth: 2 } },
      { type: 'line', shape: { x1: loCoord[0], y1: loCoord[1] - capHalf, x2: loCoord[0], y2: loCoord[1] + capHalf }, style: { stroke: color, lineWidth: 2 } },
      { type: 'line', shape: { x1: hiCoord[0], y1: hiCoord[1] - capHalf, x2: hiCoord[0], y2: hiCoord[1] + capHalf }, style: { stroke: color, lineWidth: 2 } },
      { type: 'circle', shape: { cx: midCoord[0], cy: midCoord[1], r: 5 }, style: { fill: color } },
    ],
  }
}

export function ForestPlotChart({ rows, title }: { rows: ForestRow[]; title?: string }) {
  const labels = rows.map((r) => r.label)
  const data = rows.map((r, i) => [i, r.ciLoPct, r.ciHiPct, r.effectRelPct, r.highlighted ? 1 : 0])
  const height = Math.max(200, 60 * rows.length + 80)

  const option = {
    title: title ? { text: title, textStyle: { fontSize: 13 } } : undefined,
    grid: { left: 220, right: 40, top: title ? 48 : 20, bottom: 32 },
    xAxis: {
      type: 'value',
      name: 'Effect, %',
      axisLine: { lineStyle: { color: chartColors.axisLine } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    yAxis: { type: 'category', data: labels, inverse: true, axisLine: { lineStyle: { color: chartColors.axisLine } } },
    series: [
      {
        type: 'custom',
        renderItem: renderErrorBar,
        encode: { x: [1, 2, 3], y: 0 },
        data,
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { type: 'dashed', color: chartColors.axisLine },
          data: [{ xAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  }

  return <ReactECharts option={option} style={{ height }} />
}
