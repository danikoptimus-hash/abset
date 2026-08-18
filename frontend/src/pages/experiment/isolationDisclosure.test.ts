import { describe, it, expect } from 'vitest'
import { isolationDisclosure } from './isolationDisclosure'

// Item C3 — the three outcomes the reports and the Design tab must be able to
// state, plus the two "say nothing" cases. Kept in step with
// abkit/viz/report.py::isolation_disclosure (manual port, same wording).
describe('isolationDisclosure', () => {
  it('returns null when the experiment carries no isolation info at all', () => {
    // External splits and designs predating this feature: we don't know
    // whether there was overlap, so claiming "no overlap" would be a lie.
    expect(isolationDisclosure(null)).toBeNull()
    expect(isolationDisclosure(undefined)).toBeNull()
    expect(isolationDisclosure({})).toBeNull()
  })

  it('states "no overlap" when the check ran and found nothing', () => {
    const d = isolationDisclosure({
      isolation_decision: { decision: 'none', n_overlap: 0, by_experiment: {} },
    })
    expect(d?.text).toBe('No overlap with other active experiments.')
    expect(d?.level).toBe('ok')
  })

  it('states how many users were excluded', () => {
    const d = isolationDisclosure({
      isolation_decision: { decision: 'excluded', n_overlap: 412, by_experiment: { other_test: 412 } },
    })
    expect(d?.text).toContain('Excluded 412 overlapping users')
    expect(d?.level).toBe('ok')
    expect(d?.byExperiment).toEqual({ other_test: 412 })
  })

  it('warns when the design proceeded despite the overlap', () => {
    const d = isolationDisclosure({
      isolation_decision: { decision: 'proceeded', n_overlap: 37, by_experiment: { other_test: 37 } },
    })
    expect(d?.text).toContain('Proceeded despite 37 overlapping users')
    expect(d?.text).toContain('confound')
    // This one is a caveat on the result, not a clean outcome — it has to
    // read as a warning, not as reassurance.
    expect(d?.level).toBe('warn')
  })

  it('reconstructs the outcome for designs predating the recorded decision', () => {
    // Older experiments never stored isolation_decision, but always stored
    // the counts — an honest sentence still follows from them.
    const excluded = isolationDisclosure({
      excluded_by_experiment: { older: 50 },
      n_excluded_by_isolation: 50,
    })
    expect(excluded?.decision).toBe('excluded')
    expect(excluded?.text).toContain('Excluded 50')

    const proceeded = isolationDisclosure({
      excluded_by_experiment: { older: 50 },
      n_excluded_by_isolation: 0,
    })
    expect(proceeded?.decision).toBe('proceeded')
    expect(proceeded?.text).toContain('Proceeded despite 50')
  })

  it('says nothing for an old design that recorded no overlap either way', () => {
    // No decision AND no counts: indistinguishable from "isolation was off",
    // so there is nothing truthful to state.
    expect(isolationDisclosure({ excluded_by_experiment: {}, n_excluded_by_isolation: 0 })).toBeNull()
  })
})
