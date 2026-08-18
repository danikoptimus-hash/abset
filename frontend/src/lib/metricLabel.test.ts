import { describe, it, expect } from 'vitest'
import {
  labelForMetricName,
  metricLabel,
  metricLabelsByName,
  showsColumnSeparately,
} from './metricLabel'

// Item A1 — the fallback rule. The whole feature rests on this: a metric's
// `name` is a data column and a storage key, `display_name` is only a label,
// and every screen picks between them here.
describe('metricLabel', () => {
  it('falls back to the technical name when no display name is set', () => {
    expect(metricLabel({ name: 'txn_sum' })).toBe('txn_sum')
    expect(metricLabel({ name: 'txn_sum', display_name: null })).toBe('txn_sum')
  })

  it('uses the display name when set', () => {
    expect(metricLabel({ name: 'txn_sum', display_name: 'Revenue per user' })).toBe('Revenue per user')
  })

  it('treats a blank display name as unset, not as a blank label', () => {
    // The wizard's form field yields "" for a never-touched input, and a
    // whitespace-only value is a typo, not a deliberate empty caption.
    expect(metricLabel({ name: 'txn_sum', display_name: '' })).toBe('txn_sum')
    expect(metricLabel({ name: 'txn_sum', display_name: '   ' })).toBe('txn_sum')
  })

  it('trims a display name rather than rendering stray whitespace', () => {
    expect(metricLabel({ name: 'txn_sum', display_name: '  Revenue  ' })).toBe('Revenue')
  })
})

describe('showsColumnSeparately', () => {
  it('is false without a display name — the column would be printed twice', () => {
    expect(showsColumnSeparately({ name: 'txn_sum' })).toBe(false)
    expect(showsColumnSeparately({ name: 'txn_sum', display_name: '' })).toBe(false)
  })

  it('is false when the display name merely repeats the column', () => {
    expect(showsColumnSeparately({ name: 'txn_sum', display_name: 'txn_sum' })).toBe(false)
  })

  it('is true when a display name actually overrides the column', () => {
    expect(showsColumnSeparately({ name: 'txn_sum', display_name: 'Revenue' })).toBe(true)
  })
})

describe('metricLabelsByName / labelForMetricName', () => {
  it('maps technical names to labels', () => {
    const labels = metricLabelsByName([
      { name: 'txn_sum', display_name: 'Revenue' },
      { name: 'clicks' },
    ])
    expect(labels).toEqual({ txn_sum: 'Revenue', clicks: 'clicks' })
  })

  it('falls back to the key itself for an unknown metric', () => {
    // Results can carry a metric the current config no longer declares (an
    // older run kept after a redesign) — it must still render as something.
    expect(labelForMetricName('gone', { txn_sum: 'Revenue' })).toBe('gone')
    expect(labelForMetricName('gone', undefined)).toBe('gone')
  })
})
