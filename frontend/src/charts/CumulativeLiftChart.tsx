import ReactECharts from 'echarts-for-react'
import { chartColors } from './theme'
import type { DailyLiftPoint } from '../pages/experiment/analyzeTypes'

export function CumulativeLiftChart({ points }: { points: DailyLiftPoint[] }) {
  const dates = points.map((p) => p.date)
  const lift = points.map((p) => p.effect_rel * 100)
  const ciLower = points.map((p) => p.ci_lower * 100)
  const ciUpper = points.map((p) => p.ci_upper * 100)
  const band = ciUpper.map((hi, i) => hi - ciLower[i])

  const option = {
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: chartColors.axisLine } } },
    yAxis: {
      type: 'value', name: 'Лифт, %',
      axisLine: { lineStyle: { color: chartColors.axisLine } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    series: [
      {
        name: 'ДИ (нижняя граница)', type: 'line', data: ciLower, showSymbol: false,
        lineStyle: { opacity: 0 }, stack: 'ci', silent: true,
      },
      {
        name: 'ДИ', type: 'line', data: band, showSymbol: false,
        lineStyle: { opacity: 0 }, areaStyle: { color: chartColors.significantPositive, opacity: 0.15 },
        stack: 'ci', silent: true,
      },
      {
        name: 'Кумулятивный лифт, %', type: 'line', data: lift, showSymbol: true,
        lineStyle: { color: chartColors.significantPositive }, itemStyle: { color: chartColors.significantPositive },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { type: 'dashed', color: chartColors.axisLine },
          data: [{ yAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  }

  return <ReactECharts option={option} style={{ height: 320 }} />
}
