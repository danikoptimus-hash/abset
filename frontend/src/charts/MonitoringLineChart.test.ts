import { describe, expect, it } from 'vitest'
import { BAND_HELPER_PREFIX, buildMonitoringChartOption, hexToRgba } from './MonitoringLineChart'

// Item B (memory chart redesign): pure option-builder tests, no DOM/canvas
// needed — see buildMonitoringChartOption's own comment for why this is
// split out. Assertions read the plain option object ECharts would render
// from, not pixels.

describe('buildMonitoringChartOption — reference line (item B2)', () => {
  it('omits markLine entirely when no referenceLine is given', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [{ name: 'Backend memory', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 100 }] }],
    })
    const mainSeries = option.series.find((s) => !s.name.startsWith(BAND_HELPER_PREFIX))
    expect(mainSeries?.markLine).toBeUndefined()
  })

  it('adds a dashed markLine at the limit value and label when referenceLine is given', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      referenceLine: { value: 4096, label: 'memory limit' },
      series: [{ name: 'Backend memory', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 100 }] }],
    })
    const mainSeries = option.series.find((s) => !s.name.startsWith(BAND_HELPER_PREFIX))
    expect(mainSeries?.markLine).toBeDefined()
    expect(mainSeries?.markLine?.data).toEqual([{ yAxis: 4096 }])
    expect(mainSeries?.markLine?.lineStyle.type).toBe('dashed')
    expect(mainSeries?.markLine?.label.formatter).toBe('memory limit')
  })

  it('Y-axis max accommodates a limit line higher than any data point', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      referenceLine: { value: 4096, label: 'memory limit' },
      series: [{ name: 'Backend memory', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 100 }] }],
    })
    expect(option.yAxis.max).toBeGreaterThan(4096)
  })

  it('Y-axis max is undefined (auto-scale) when there is no limit line', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [{ name: 'Backend memory', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 100 }] }],
    })
    expect(option.yAxis.max).toBeUndefined()
  })

  it('only the first series carries the markLine when multiple series are passed', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      referenceLine: { value: 500, label: 'memory limit' },
      series: [
        { name: 'Database', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 10 }] },
        { name: 'Data volume', color: '#C9A227', points: [{ ts: '2026-07-17T00:00:00Z', value: 5 }] },
      ],
    })
    const database = option.series.find((s) => s.name === 'Database')
    const dataVolume = option.series.find((s) => s.name === 'Data volume')
    expect(database?.markLine).toBeDefined()
    expect(dataVolume?.markLine).toBeUndefined()
  })
})

describe('buildMonitoringChartOption — min/max band (item B3)', () => {
  it('adds no band helper series for raw (24h) points with no min/max', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Backend memory',
          color: '#2E8B6D',
          points: [
            { ts: '2026-07-17T00:00:00Z', value: 100 },
            { ts: '2026-07-17T00:01:00Z', value: 110 },
          ],
        },
      ],
    })
    expect(option.series).toHaveLength(1)
    expect(option.series[0].name).toBe('Backend memory')
  })

  it('adds two band helper series for hourly points carrying min/max', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Backend memory',
          color: '#2E8B6D',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 200, min: 100, max: 300 }],
        },
      ],
    })
    expect(option.series).toHaveLength(3)
    const names = option.series.map((s) => s.name)
    expect(names.filter((n) => n.startsWith(BAND_HELPER_PREFIX))).toHaveLength(2)
    expect(names).toContain('Backend memory')
  })

  it('the band helper series encode min as the stacked base and (max-min) as the visible range', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Backend memory',
          color: '#2E8B6D',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 200, min: 100, max: 300 }],
        },
      ],
    })
    const baseSeries = option.series.find((s) => s.name === `${BAND_HELPER_PREFIX}min_0`)
    const rangeSeries = option.series.find((s) => s.name === `${BAND_HELPER_PREFIX}range_0`)
    expect(baseSeries?.data).toEqual([['2026-07-17T00:00:00Z', 100]])
    expect(rangeSeries?.data).toEqual([['2026-07-17T00:00:00Z', 200]]) // max - min = 300 - 100
  })

  it('band helper series share one stack id per source series so they compose into a band, not a stacked total', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Backend memory',
          color: '#2E8B6D',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 200, min: 100, max: 300 }],
        },
      ],
    })
    const baseSeries = option.series.find((s) => s.name === `${BAND_HELPER_PREFIX}min_0`)
    const rangeSeries = option.series.find((s) => s.name === `${BAND_HELPER_PREFIX}range_0`)
    expect(baseSeries?.stack).toBe(rangeSeries?.stack)
  })

  it('band helper series are invisible (opacity 0 line, hidden tooltip) — only the translucent area shows', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Backend memory',
          color: '#2E8B6D',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 200, min: 100, max: 300 }],
        },
      ],
    })
    const rangeSeries = option.series.find((s) => s.name === `${BAND_HELPER_PREFIX}range_0`)
    expect(rangeSeries?.lineStyle.opacity).toBe(0)
    expect(rangeSeries?.tooltip?.show).toBe(false)
    expect(rangeSeries?.areaStyle.opacity).toBe(0.15)
  })

  it('only series with band data get band helpers when mixed with a series that has none', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        {
          name: 'Database',
          color: '#2E8B6D',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 200, min: 100, max: 300 }],
        },
        {
          name: 'Data volume',
          color: '#C9A227',
          points: [{ ts: '2026-07-17T00:00:00Z', value: 50 }],
        },
      ],
    })
    // Database (index 0) gets 2 band helpers + itself = 3; Data volume
    // (index 1, no min/max) gets just itself = 1. Total 4.
    expect(option.series).toHaveLength(4)
    expect(option.series.filter((s) => s.name.includes('_0'))).toHaveLength(2)
    expect(option.series.filter((s) => s.name.includes('_1'))).toHaveLength(0)
  })
})

describe('buildMonitoringChartOption — area + step for every series (item B1/B4)', () => {
  it('every series uses step "end" (stepAfter) and has a gradient areaStyle', () => {
    const option = buildMonitoringChartOption({
      yAxisLabel: 'MB',
      series: [
        { name: 'Database', color: '#2E8B6D', points: [{ ts: '2026-07-17T00:00:00Z', value: 10 }] },
        { name: 'Data volume', color: '#C9A227', points: [{ ts: '2026-07-17T00:00:00Z', value: 5 }] },
      ],
    })
    for (const s of option.series) {
      expect(s.step).toBe('end')
      expect(s.areaStyle).toBeDefined()
    }
  })
})

describe('hexToRgba', () => {
  it('converts a hex color and alpha into an rgba() string', () => {
    expect(hexToRgba('#2E8B6D', 0.35)).toBe('rgba(46, 139, 109, 0.35)')
  })
})
