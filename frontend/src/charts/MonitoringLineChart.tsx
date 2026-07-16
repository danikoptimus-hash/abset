import ReactECharts from 'echarts-for-react'
import { chartColors } from './theme'
import { tooltipBaseStyle } from './tooltip'
import { formatMb } from '../monitoringFormat'

export interface MonitoringPoint {
  ts: string
  value: number | null
  // Populated only for aggregated (hourly) ranges (item B3) — the stored
  // hourly min/max alongside the avg in `value`, so a short spike doesn't
  // disappear once the chart is zoomed out to a week/month.
  min?: number | null
  max?: number | null
}

export interface MonitoringSeries {
  name: string
  color: string
  points: MonitoringPoint[]
}

export interface MonitoringReferenceLine {
  value: number
  label: string
}

export function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// Helper series (the invisible min-baseline + translucent max-min band,
// item B3) are prefixed so the tooltip formatter can filter them out —
// they're an implementation detail of the band, never meant to show up as
// their own tooltip row.
export const BAND_HELPER_PREFIX = '__band_'

interface TooltipParam {
  seriesName: string
  dataIndex: number
  marker: string
  data: [string, number | null]
}

// One shared shape for every series entry the builder can produce (main
// line or band helper) — every optional field covers the ones only SOME
// entries need, so property access stays type-safe everywhere (the
// component itself only ever reads `.data`/`.name` off these, but
// MonitoringLineChart.test.ts reads the rest directly to assert on them
// without needing a DOM/canvas).
interface EChartsLineSeriesEntry {
  name: string
  type: 'line'
  step: 'end'
  stack?: string
  symbol?: string
  showSymbol?: boolean
  silent?: boolean
  tooltip?: { show: boolean }
  lineStyle: { color?: string; width?: number; opacity?: number }
  itemStyle?: { color: string }
  areaStyle: { color?: string | Record<string, unknown>; opacity?: number }
  data: (string | number | null)[][]
  markLine?: {
    silent: boolean
    symbol: string
    lineStyle: { type: string; color: string }
    label: { formatter: string; position: string; color: string }
    data: { yAxis: number }[]
  }
}

// Pure option-builder, separated from the component so it's unit-testable
// without a DOM/canvas (frontend/src/charts/MonitoringLineChart.test.ts) —
// same "pure decision logic split from rendering/IO" pattern already used
// elsewhere in this codebase (abkit.monitoring.plan_retention,
// abkit.db.maintenance._classify_bloat).
export function buildMonitoringChartOption({
  series,
  yAxisLabel,
  referenceLine,
}: {
  series: MonitoringSeries[]
  yAxisLabel: string
  referenceLine?: MonitoringReferenceLine
}) {
  const allValues = series
    .flatMap((s) => s.points.flatMap((p) => [p.value, p.max ?? null]))
    .filter((v): v is number => v != null)
  const dataMax = allValues.length ? Math.max(...allValues) : 0
  // Headroom so the reference line never sits flush against the plot's top
  // edge (item B2: "Y-axis max should accommodate the limit line so it's
  // always visible").
  const yAxisMax = referenceLine ? Math.max(dataMax, referenceLine.value) * 1.1 : undefined

  const echartsSeries: EChartsLineSeriesEntry[] = series.flatMap((s, index) => {
    const hasBand = s.points.some((p) => p.min != null && p.max != null)
    const stackId = `${BAND_HELPER_PREFIX}${index}`

    const bandSeries: EChartsLineSeriesEntry[] = hasBand
      ? [
          {
            name: `${BAND_HELPER_PREFIX}min_${index}`,
            type: 'line',
            stack: stackId,
            step: 'end',
            symbol: 'none',
            silent: true,
            tooltip: { show: false },
            lineStyle: { opacity: 0 },
            areaStyle: { opacity: 0 },
            data: s.points.map((p) => [p.ts, p.min ?? p.value]),
          },
          {
            name: `${BAND_HELPER_PREFIX}range_${index}`,
            type: 'line',
            stack: stackId,
            step: 'end',
            symbol: 'none',
            silent: true,
            tooltip: { show: false },
            lineStyle: { opacity: 0 },
            areaStyle: { color: s.color, opacity: 0.15 },
            data: s.points.map((p) => (p.min != null && p.max != null ? [p.ts, p.max - p.min] : [p.ts, null])),
          },
        ]
      : []

    const mainSeries: EChartsLineSeriesEntry = {
      name: s.name,
      type: 'line',
      step: 'end',
      showSymbol: false,
      lineStyle: { color: s.color, width: 2 },
      itemStyle: { color: s.color },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: hexToRgba(s.color, 0.35) },
            { offset: 1, color: hexToRgba(s.color, 0.02) },
          ],
        },
      },
      data: s.points.map((p) => [p.ts, p.value]),
      // Only the first series carries the markLine — with today's callers
      // (the memory chart has exactly one series when a limit applies) this
      // never duplicates, and it keeps the option from drawing the same
      // dashed line once per series if a future caller passes several.
      markLine:
        referenceLine && index === 0
          ? {
              silent: true,
              symbol: 'none',
              lineStyle: { type: 'dashed', color: chartColors.axisLabel },
              label: { formatter: referenceLine.label, position: 'insideEndTop', color: chartColors.axisLabel },
              data: [{ yAxis: referenceLine.value }],
            }
          : undefined,
    }

    return [...bandSeries, mainSeries]
  })

  return {
    grid: { left: 70, right: 20, top: series.length > 1 ? 36 : 20, bottom: 40 },
    legend: series.length > 1
      ? { top: 0, textStyle: { color: chartColors.axisLabel }, data: series.map((s) => s.name) }
      : undefined,
    tooltip: {
      trigger: 'axis',
      ...tooltipBaseStyle,
      formatter: (params: TooltipParam[]) => {
        const visible = params.filter((p) => !p.seriesName.startsWith(BAND_HELPER_PREFIX))
        const rows = visible.map((p) => {
          const point = series.find((s) => s.name === p.seriesName)?.points[p.dataIndex]
          const valueText = p.data[1] == null ? '—' : formatMb(p.data[1])
          const rangeText =
            point?.min != null && point?.max != null
              ? ` (min ${formatMb(point.min)} / max ${formatMb(point.max)})`
              : ''
          return `${p.marker} ${p.seriesName}: ${valueText}${rangeText}`
        })
        if (rows.length === 0) return ''
        const ts = visible[0]?.data?.[0]
        return [...(ts ? [`<div>${new Date(ts).toLocaleString()}</div>`] : []), ...rows].join('<br/>')
      },
    },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: chartColors.axisLine } } },
    yAxis: {
      type: 'value',
      name: yAxisLabel,
      max: yAxisMax,
      axisLine: { lineStyle: { color: chartColors.axisLine } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    series: echartsSeries,
  }
}

// Admin monitoring panel (item B, memory chart redesign): generic
// multi-series time chart. Every series renders as a gradient-filled area
// with step ('end' — a.k.a. stepAfter: the value holds flat from its own
// timestamp until the NEXT sample arrives, then jumps) rather than a
// smoothed/interpolated line — these are discrete 60s/hourly snapshots, not
// a continuous signal, and smoothing would hide real spikes. An optional
// dashed `referenceLine` (the effective container memory limit) and, per
// series, a translucent min-max band when its points carry min/max
// (aggregated 7d/30d/90d ranges — 24h raw points never do).
export function MonitoringLineChart({
  series,
  yAxisLabel,
  referenceLine,
}: {
  series: MonitoringSeries[]
  yAxisLabel: string
  referenceLine?: MonitoringReferenceLine
}) {
  const option = buildMonitoringChartOption({ series, yAxisLabel, referenceLine })
  return <ReactECharts option={option} style={{ height: 300 }} notMerge />
}
