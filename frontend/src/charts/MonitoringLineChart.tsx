import ReactECharts from 'echarts-for-react'
import { chartColors } from './theme'
import { tooltipBaseStyle } from './tooltip'
import { formatMb } from '../monitoringFormat'

export interface MonitoringSeries {
  name: string
  color: string
  points: { ts: string; value: number | null }[]
}

// Admin monitoring panel: generic multi-series time chart, reused for both
// the memory-over-time chart (one series) and the DB+volume-size-over-time
// chart (two series) — same stack (echarts-for-react) as the rest of the
// app's charts (ForestPlotChart/DistributionChart/CumulativeLiftChart).
export function MonitoringLineChart({ series, yAxisLabel }: { series: MonitoringSeries[]; yAxisLabel: string }) {
  const option = {
    grid: { left: 70, right: 20, top: series.length > 1 ? 36 : 20, bottom: 40 },
    legend: series.length > 1 ? { top: 0, textStyle: { color: chartColors.axisLabel } } : undefined,
    tooltip: {
      trigger: 'axis',
      ...tooltipBaseStyle,
      formatter: (params: { marker: string; seriesName: string; data: [string, number | null] }[]) =>
        params
          .map((p) => `${p.marker} ${p.seriesName}: ${p.data[1] == null ? '—' : formatMb(p.data[1])}`)
          .join('<br/>'),
    },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: chartColors.axisLine } } },
    yAxis: {
      type: 'value',
      name: yAxisLabel,
      axisLine: { lineStyle: { color: chartColors.axisLine } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      showSymbol: false,
      lineStyle: { color: s.color },
      itemStyle: { color: s.color },
      data: s.points.map((p) => [p.ts, p.value]),
    })),
  }

  return <ReactECharts option={option} style={{ height: 300 }} notMerge />
}
