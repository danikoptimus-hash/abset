import { colors } from '../theme/tokens'

// Глобальная палитра ECharts (FRONTEND.md §5.1): значимый эффект — зеленый,
// незначимый — серый, сетка светло-серая. Ни одного оранжевого/красного для
// "обычных" состояний — красный (colors.error) используется ТОЛЬКО для
// значимого отрицательного эффекта, не для generic acccent.
export const chartColors = {
  significantPositive: colors.success,
  significantNegative: colors.error,
  notSignificant: '#999999',
  grid: '#EFEFEF',
  axisLine: colors.border,
  axisLabel: colors.tableHeaderText,
} as const

export const echartsBaseOption = {
  color: [chartColors.notSignificant, chartColors.significantPositive],
  textStyle: { fontFamily: 'Inter, -apple-system, Helvetica, Arial, sans-serif' },
  grid: { borderColor: chartColors.grid },
}

// Item 2: dataZoom slider styling — ECharts defaults to blue (handle,
// filler, selected-range background); this reskins it to the same green
// used for a significant effect everywhere else in the app, so it doesn't
// look like a leftover default control.
export const echartsZoomSliderStyle = {
  borderColor: chartColors.grid,
  fillerColor: 'rgba(46, 139, 109, 0.12)',
  handleStyle: { color: chartColors.significantPositive, borderColor: chartColors.significantPositive },
  moveHandleStyle: { color: chartColors.significantPositive },
  dataBackground: {
    lineStyle: { color: chartColors.axisLine },
    areaStyle: { color: chartColors.grid },
  },
  selectedDataBackground: {
    lineStyle: { color: chartColors.significantPositive },
    areaStyle: { color: chartColors.significantPositive, opacity: 0.15 },
  },
  textStyle: { color: chartColors.axisLabel },
}
