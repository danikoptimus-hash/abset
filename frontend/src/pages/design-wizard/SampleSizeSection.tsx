import { useState } from 'react'
import { Typography, Button, Alert, Space, InputNumber, Tooltip, Checkbox } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import {
  equalSplitGroups, groupsSum, metricsToApi, sampleSizeInputsKey,
  aggregateShortfall, groupBelowRequired, anyGroupBelowRequired,
} from './types'
import type { WizardState, SizeMode } from './types'
import { mb, mt } from './spacing'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
  // Redesign prefill (wizardStateFromConfig) seeds sampleSizeResult with a
  // sentinel so the block below shows immediately with the experiment's
  // real saved proportions — no fresh Calculate click required just to see
  // them, though they still read as "stale" (nudge, not a block).
  isRedesign: boolean
}

// Item 3 (sample-size-first wizard flow): "Calculate sample size" runs
// against the real dataset + isolation, THEN the proportions block appears
// — the inverse of the old order, where proportions were guessed before
// anyone knew how much data the design actually needed.
export function SampleSizeSection({ state, setState, isRedesign }: Props) {
  const [calculating, setCalculating] = useState(false)
  const [calcError, setCalcError] = useState<string | null>(null)

  const groupNames = state.groups.map((g) => g.name.trim()).filter(Boolean)
  const canCalculate =
    !!state.datasetId && !!state.unitCol && groupNames.length >= 2 && state.metrics.some((m) => m.name.trim())

  // Item 2.2 (hard Next gate): runCalculate always uses state.sizeMode;
  // useAllDataAnyway (below) calls this with an explicit override instead —
  // switching to 'all' is the one and only way to resolve an AGGREGATE
  // shortfall (not enough total data for the target) without actually
  // fixing the target, so it needs to run against 'all' immediately, before
  // the setState that would otherwise apply it has landed.
  const runCalculateWithSizeMode = async (sizeModeOverride: SizeMode) => {
    if (!canCalculate || !state.datasetId || !state.unitCol) return
    setCalculating(true)
    setCalcError(null)
    try {
      let mde: number | null = null
      if (sizeModeOverride === 'mde_rel') {
        mde = state.mdeRel
      } else if (sizeModeOverride === 'mde_abs') {
        const metric = state.metrics.find((m) => m.id === state.mdeAbsMetricId)
        if (!metric) throw new Error('Select a metric for the absolute MDE on this step first')
        const { data: baselineData, error: baselineError } = await apiClient.POST(
          '/api/v1/datasets/{dataset_id}/metric-baseline',
          {
            params: { path: { dataset_id: state.datasetId } },
            body: { name: metric.name, type: metric.type, pre_col: metric.preCol, num: metric.num, den: metric.den },
          },
        )
        if (baselineError) throw new Error(errorMessage(baselineError))
        const baseline = baselineData?.baseline_mean
        if (!baseline) throw new Error('Could not determine the baseline for the absolute MDE')
        mde = state.mdeAbsValue / baseline
      }
      // sizeMode 'sample_size'/'all': mde stays null — the preview still
      // reports eligible_n, just no MDE-driven required_n_per_group.

      const { data, error } = await apiClient.POST('/api/v1/datasets/{dataset_id}/sample-size-preview', {
        params: { path: { dataset_id: state.datasetId } },
        body: {
          unit_col: state.unitCol,
          group_names: groupNames,
          metrics: metricsToApi(state),
          alpha: state.alpha,
          power: state.power,
          mde: mde ?? undefined,
          isolation: state.isolation,
          exclude_experiments: 'all_active',
          isolation_selected_experiments: state.isolation === 'exclude_selected' ? state.isolationSelected : [],
          experiment_name: state.name.trim() || undefined,
        },
      })
      if (error) throw new Error(errorMessage(error))

      // Item 2 (hard Next gate) bugfix: config.sample_size is a TOTAL
      // design size (abkit/experiment.py: `n_control = config.sample_size *
      // control_prop` — it's multiplied by a PROPORTION, so it can't
      // itself be a per-group count). state.sampleSize inherits that same
      // total-not-per-group meaning — dividing by the group count gives the
      // equal-split per-group figure, consistent with what the MDE-driven
      // modes' required_n_per_group already represents (the preview always
      // assumes an equal split, per abkit/jobs.py::preview_sample_size).
      // Before this gate existed the bug was harmless (display-only + the
      // "Minimize control" convenience math); now it would otherwise make
      // the aggregate-shortfall gate spuriously trip for every
      // sizeMode='sample_size' design (demo data's default mode) at 2+
      // groups.
      const requiredNPerGroup =
        sizeModeOverride === 'sample_size'
          ? Math.round(state.sampleSize / (groupNames.length || 1))
          : data.required_n_per_group
      const key = sampleSizeInputsKey({ ...state, sizeMode: sizeModeOverride })
      setState((prev) => {
        // Only reset to an equal split the FIRST time a calculation lands
        // (prev.sampleSizeResult === null) — a recalculation after editing
        // MDE/alpha/power/metrics keeps whatever the user already entered
        // (item 3.2: "старые доли сохранить как введенные").
        const firstCalc = prev.sampleSizeResult === null
        return {
          ...prev,
          sizeMode: sizeModeOverride,
          groups: firstCalc ? equalSplitGroups(prev.groups) : prev.groups,
          sampleSizeResult: {
            eligibleN: data.eligible_n,
            requiredNPerGroup,
            perMetric: data.per_metric.map((m) => ({
              metric: m.metric, baselineMean: m.baseline_mean, requiredN: m.required_n_per_group, warnings: m.warnings,
            })),
            inputsKey: key,
          },
          // Item 2.3: a fresh calculation invalidates any prior "proceed
          // anyway" acknowledgment — it may no longer apply to the new
          // result (different requiredNPerGroup/eligibleN).
          acceptedGroupShortfall: false,
        }
      })
    } catch (e) {
      setCalcError(e instanceof Error ? e.message : 'Failed to calculate sample size')
    } finally {
      setCalculating(false)
    }
  }
  const runCalculate = () => runCalculateWithSizeMode(state.sizeMode)
  const useAllDataAnyway = () => runCalculateWithSizeMode('all')

  const result = state.sampleSizeResult
  const stale = result !== null && result.inputsKey !== sampleSizeInputsKey(state)
  const showProportions = result !== null || isRedesign
  const sum = groupsSum(state)
  const sumOk = Math.abs(sum - 1) < 1e-6
  const notEnoughData = aggregateShortfall(state)
  const anyBelowRequired = anyGroupBelowRequired(state)
  const nGroups = state.groups.length || 1
  const totalRequired = result?.requiredNPerGroup != null ? result.requiredNPerGroup * nGroups : null

  // Item 2.3: reset the group-shortfall acknowledgment on every proportion
  // edit (share, count, or Minimize control) — an old "I accept this" can
  // never silently cover a NEW split the user hasn't actually looked at.
  const updateGroups = (updater: (groups: WizardState['groups']) => WizardState['groups']) =>
    setState((prev) => ({ ...prev, groups: updater(prev.groups), acceptedGroupShortfall: false }))

  const updateShare = (id: string, prop: number) =>
    updateGroups((groups) => groups.map((g) => (g.id === id ? { ...g, prop } : g)))

  // Item 1.5 (shares <-> headcount): editing the count field recomputes the
  // proportion from it (count / eligibleN) — the two representations are
  // always kept in sync through `prop`, the single source of truth; count
  // is purely derived for display/editing, never stored separately.
  const updateCount = (id: string, count: number, eligibleN: number) =>
    updateGroups((groups) => groups.map((g) => (g.id === id ? { ...g, prop: eligibleN > 0 ? count / eligibleN : 0 } : g)))

  // Item 1.3: control gets the minimum required for power; the rest is
  // split evenly among the remaining (treatment) group(s).
  const controlId =
    state.groups.find((g) => g.name.trim().toLowerCase() === 'control')?.id ?? state.groups[0]?.id
  const minimizeControlShare =
    result?.requiredNPerGroup && result.eligibleN
      ? Math.min(0.95, Math.max(0.01, result.requiredNPerGroup / result.eligibleN))
      : null
  const minimizeControl = () => {
    if (minimizeControlShare == null || !controlId) return
    const others = state.groups.filter((g) => g.id !== controlId)
    const otherShare = others.length > 0 ? (1 - minimizeControlShare) / others.length : 0
    updateGroups((groups) =>
      groups.map((g) => (g.id === controlId ? { ...g, prop: minimizeControlShare } : { ...g, prop: otherShare })),
    )
  }

  return (
    <div style={{ ...mt('SECTION') }}>
      <Typography.Title level={5}>Sample Size</Typography.Title>
      <Tooltip title={!canCalculate ? 'Name at least 2 groups and 1 metric, and select the unit column, first' : ''}>
        <Button onClick={runCalculate} loading={calculating} disabled={!canCalculate}>
          Calculate sample size
        </Button>
      </Tooltip>
      {calcError && <Alert type="error" showIcon message={calcError} style={{ ...mt('FIELD'), maxWidth: 560 }} />}

      {result && (
        <div style={{ ...mt('BLOCK') }}>
          {result.requiredNPerGroup != null ? (
            <Typography.Paragraph>
              Required per group: <strong>{result.requiredNPerGroup}</strong>.{' '}
              {result.eligibleN != null ? (
                <>
                  Your dataset: <strong>{result.eligibleN}</strong> eligible users (after isolation).
                </>
              ) : (
                'Press Calculate to see how many users are eligible after isolation.'
              )}
            </Typography.Paragraph>
          ) : result.eligibleN != null ? (
            <Typography.Paragraph type="secondary">
              No MDE target set — all {result.eligibleN} eligible users (after isolation) will be used.
            </Typography.Paragraph>
          ) : null}
          {notEnoughData && (
            <Alert
              type="warning"
              showIcon
              style={{ ...mb('BLOCK'), maxWidth: 560 }}
              message="Not enough data for this target"
              description={
                <>
                  <div>
                    You need ~{totalRequired} users total ({result.requiredNPerGroup} per group × {nGroups} groups),
                    but only {result.eligibleN} are eligible. Pick one:
                  </div>
                  <ul style={{ ...mb('HINT'), paddingLeft: 20 }}>
                    <li>Increase the MDE above (a bigger effect needs fewer users)</li>
                    <li>Lower the power target above</li>
                    <li>
                      Or{' '}
                      <Button size="small" loading={calculating} onClick={useAllDataAnyway}>
                        use all available data anyway
                      </Button>{' '}
                      — no fixed target, power reflects what {result.eligibleN} users actually give you
                    </li>
                  </ul>
                  {/* Item 2.2: no checkbox bypass here on purpose — this can only be
                      resolved by actually changing the target above (recalculate)
                      or the explicit "use all available data" action, never by
                      just acknowledging the warning. */}
                </>
              }
            />
          )}
          {stale && (
            <Alert
              type="info"
              showIcon
              style={{ ...mb('BLOCK'), maxWidth: 560 }}
              message="Inputs changed since this was calculated — press Calculate again to refresh"
            />
          )}
        </div>
      )}

      {showProportions && (
        <div style={{ ...mt('BLOCK') }}>
          <Typography.Title level={5}>Group Proportions</Typography.Title>
          <Tooltip title={!result?.requiredNPerGroup ? 'Calculate a required sample size first' : ''}>
            <Button size="small" disabled={!result?.requiredNPerGroup} onClick={minimizeControl} style={{ ...mb('BLOCK') }}>
              Minimize control group
            </Button>
          </Tooltip>
          {/* Item 1.3: caption states the ACTUAL numbers this button would set
              (computed the same way minimizeControl() itself does), not just
              a generic description — visible before clicking, not just after. */}
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, maxWidth: 500, marginTop: -8 }}>
            {minimizeControlShare != null && result?.eligibleN != null
              ? `Control gets the minimum required for power (${Math.round(minimizeControlShare * result.eligibleN)} users, ${(minimizeControlShare * 100).toFixed(1)}%), the rest is split equally between treatment groups.`
              : 'Sets control to the minimum required for power, the rest is split equally between treatment groups.'}
          </Typography.Paragraph>

          {state.groups.map((g) => {
            const eligibleForCount = result?.eligibleN ?? null
            // Item 1.5: count is DERIVED from prop (not a second stored
            // field) — editing either field writes back to the same
            // `prop`, so the two representations can never disagree.
            const groupN = eligibleForCount != null ? Math.round(g.prop * eligibleForCount) : null
            const belowRequired = groupBelowRequired(state, g)
            return (
              <div key={g.id} style={{ ...mb('FIELD') }}>
                <Space>
                  <Typography.Text style={{ width: 160, display: 'inline-block' }}>
                    {g.name.trim() || '(unnamed)'}
                  </Typography.Text>
                  <InputNumber
                    min={0}
                    max={1}
                    step={0.01}
                    value={g.prop}
                    aria-label={`group-share-${g.name.trim() || g.id}`}
                    onChange={(v) => updateShare(g.id, v ?? 0)}
                  />
                  <Typography.Text type="secondary">{(g.prop * 100).toFixed(1)}%</Typography.Text>
                  {eligibleForCount != null && (
                    <>
                      <InputNumber
                        min={0}
                        max={eligibleForCount}
                        step={1}
                        value={groupN}
                        aria-label={`group-count-${g.name.trim() || g.id}`}
                        onChange={(v) => updateCount(g.id, v ?? 0, eligibleForCount)}
                      />
                      <Typography.Text type="secondary">users (of {eligibleForCount})</Typography.Text>
                    </>
                  )}
                </Space>
                {belowRequired && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ ...mt('HINT'), maxWidth: 480 }}
                    message={`Group '${g.name.trim()}' would get ${groupN} < required ${result!.requiredNPerGroup} users — power will be below target`}
                  />
                )}
              </div>
            )
          })}
          <Alert
            type={sumOk ? 'success' : 'warning'}
            showIcon
            message={`Sum of proportions: ${sum.toFixed(3)} (~${Math.round(sum * (result?.eligibleN ?? 0))} of ${result?.eligibleN ?? 0} users)${sumOk ? '' : ' — must equal 1'}`}
            style={{ ...mt('FIELD'), maxWidth: 480 }}
          />
          {/* Item 2.3: the per-group shortfall gate has an explicit bypass
              (unlike the aggregate one above) — a lopsided split is a
              deliberate allocation choice the user can knowingly accept,
              not a hard data-availability wall. */}
          {anyBelowRequired && (
            <Checkbox
              checked={state.acceptedGroupShortfall}
              onChange={(e) => setState((prev) => ({ ...prev, acceptedGroupShortfall: e.target.checked }))}
              style={{ ...mt('FIELD') }}
            >
              I understand the group(s) above will get less than the required minimum — proceed anyway
            </Checkbox>
          )}
        </div>
      )}
    </div>
  )
}
