// Item C3 — one sentence stating what the isolation check concluded and what
// was decided about it. Manual TS port of abkit/viz/report.py::
// isolation_disclosure, so the Design tab, design_report.html and report.html
// all say the same thing about the same experiment (same convention as
// lib/metricLabel.ts mirroring abkit/config.py::metric_label).

export interface IsolationDecision {
  decision?: string
  n_overlap?: number
  by_experiment?: Record<string, number>
}

export interface IsolationDisclosure {
  text: string
  level: 'ok' | 'warn'
  byExperiment: Record<string, number>
  decision: string
}

interface ComputedLike {
  isolation_decision?: IsolationDecision | null
  excluded_by_experiment?: Record<string, number> | null
  n_excluded_by_isolation?: number | null
}

/**
 * null when the experiment carries no isolation information at all (designed
 * before this feature, or an external split) — the caller then renders
 * nothing, rather than asserting "no overlap", which would be a claim we
 * can't back.
 */
export function isolationDisclosure(computed: ComputedLike | null | undefined): IsolationDisclosure | null {
  if (!computed) return null

  let decisionRecord = computed.isolation_decision
  if (!decisionRecord) {
    // Designed before item C3: the decision wasn't recorded, but the counts
    // always were — the outcome follows from them unambiguously, so older
    // experiments get an honest sentence too instead of a blank.
    const byExperiment = computed.excluded_by_experiment ?? {}
    const nExcluded = computed.n_excluded_by_isolation ?? 0
    if (Object.keys(byExperiment).length === 0) return null
    const total = Object.values(byExperiment).reduce((a, b) => a + b, 0)
    decisionRecord = {
      decision: nExcluded ? 'excluded' : 'proceeded',
      n_overlap: nExcluded || total,
      by_experiment: byExperiment,
    }
  }

  const n = decisionRecord.n_overlap ?? 0
  const byExperiment = decisionRecord.by_experiment ?? {}

  if (decisionRecord.decision === 'excluded') {
    return {
      text: `Excluded ${n} overlapping users from other active experiments.`,
      level: 'ok',
      byExperiment,
      decision: 'excluded',
    }
  }
  if (decisionRecord.decision === 'proceeded') {
    return {
      text:
        `Proceeded despite ${n} overlapping users also enrolled in other active experiments — ` +
        'their exposure to more than one test may confound the results.',
      level: 'warn',
      byExperiment,
      decision: 'proceeded',
    }
  }
  return {
    text: 'No overlap with other active experiments.',
    level: 'ok',
    byExperiment,
    decision: 'none',
  }
}
