import { describe, it, expect } from 'vitest'
import {
  describeRunSegments,
  runSegmentColumns,
  runUsedDefaultSegments,
  segmentSelectionDiffersFromRun,
} from './segmentRunIdentity'

const declared = ['gender', 'months_ago']

describe('runSegmentColumns / runUsedDefaultSegments', () => {
  it('falls back to declared strata when the run recorded no explicit request', () => {
    expect(runSegmentColumns({ segment_columns: null }, declared)).toEqual(declared)
    expect(runUsedDefaultSegments({ segment_columns: null })).toBe(true)
  })
  it('uses the run explicit request when present', () => {
    expect(runSegmentColumns({ segment_columns: ['channel'] }, declared)).toEqual(['channel'])
    expect(runUsedDefaultSegments({ segment_columns: ['channel'] })).toBe(false)
  })
})

describe('segmentSelectionDiffersFromRun', () => {
  it('is false when the form matches the run (order-insensitive)', () => {
    const run = { segment_columns: ['gender', 'channel'], segment_combinations: [['a', 'b']] }
    const form = { columns: ['channel', 'gender'], combinations: [['b', 'a']] }
    expect(segmentSelectionDiffersFromRun(form, run, declared)).toBe(false)
  })

  it('is true when the form adds a column the run did not have (the reported bug)', () => {
    // Run used the design default (declared strata); the form added 'channel'.
    const run = { segment_columns: null, segment_combinations: null }
    const form = { columns: [...declared, 'channel'], combinations: [] }
    expect(segmentSelectionDiffersFromRun(form, run, declared)).toBe(true)
  })

  it('is true when the combination differs (3-way form vs 4-way run)', () => {
    const run = { segment_columns: ['a'], segment_combinations: [['a', 'b', 'c', 'd']] }
    const form = { columns: ['a'], combinations: [['a', 'b', 'c']] }
    expect(segmentSelectionDiffersFromRun(form, run, declared)).toBe(true)
  })

  it('is false for a default run whose form still equals the declared strata', () => {
    const run = { segment_columns: null, segment_combinations: null }
    const form = { columns: [...declared], combinations: [] }
    expect(segmentSelectionDiffersFromRun(form, run, declared)).toBe(false)
  })
})

describe('describeRunSegments', () => {
  it('names the default case', () => {
    expect(describeRunSegments({ segment_columns: null })).toBe('design-declared strata')
  })
  it('lists explicit columns and combinations', () => {
    expect(
      describeRunSegments({ segment_columns: ['gender', 'channel'], segment_combinations: [['a', 'b']] }),
    ).toBe('gender, channel · combinations: a × b')
  })
  it('handles an explicitly empty column list', () => {
    expect(describeRunSegments({ segment_columns: [] })).toBe('none')
  })
})
