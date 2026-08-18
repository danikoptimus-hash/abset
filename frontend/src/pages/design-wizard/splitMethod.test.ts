import { describe, it, expect } from 'vitest'
import { splitMethodForStrataChange } from './types'

// Item A2 — the auto-switch, in BOTH directions plus the cases that must not
// move. The wizard now starts on "simple"; picking strata is what makes a
// stratified split meaningful, and clearing them makes it meaningless again.
describe('splitMethodForStrataChange', () => {
  it('switches simple -> stratified when the first stratum is picked', () => {
    expect(splitMethodForStrataChange('simple', [], ['country'])).toBe('stratified')
  })

  it('switches stratified -> simple when the last stratum is removed', () => {
    expect(splitMethodForStrataChange('stratified', ['country'], [])).toBe('simple')
  })

  it('leaves the method alone while strata merely change but stay non-empty', () => {
    // Adding a second stratum is not a new decision about the method.
    expect(splitMethodForStrataChange('stratified', ['country'], ['country', 'platform'])).toBe('stratified')
    // ...and neither is a manual override that the user already made.
    expect(splitMethodForStrataChange('simple', ['country'], ['country', 'platform'])).toBe('simple')
  })

  it('keeps a manual "simple" override when strata are still selected', () => {
    // This is what makes the switch overridable rather than a lock: the user
    // set simple deliberately, and nothing about the strata changed here.
    expect(splitMethodForStrataChange('simple', ['country'], ['country'])).toBe('simple')
  })

  it('never touches "hash"', () => {
    // A hash split is an orthogonal, deliberate choice (deterministic split
    // by id hash) — not a point on the simple/stratified axis.
    expect(splitMethodForStrataChange('hash', [], ['country'])).toBe('hash')
    expect(splitMethodForStrataChange('hash', ['country'], [])).toBe('hash')
  })

  it('is a no-op when there were and are no strata', () => {
    expect(splitMethodForStrataChange('simple', [], [])).toBe('simple')
  })
})
