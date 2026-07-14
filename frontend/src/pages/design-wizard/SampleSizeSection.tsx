import { useState } from 'react'
import { Typography, Button, Alert, Space, InputNumber, Tooltip } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import { equalSplitGroups, groupsSum, metricsToApi, sampleSizeInputsKey } from './types'
import type { WizardState } from './types'

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

  const runCalculate = async () => {
    if (!canCalculate || !state.datasetId || !state.unitCol) return
    setCalculating(true)
    setCalcError(null)
    try {
      let mde: number | null = null
      if (state.sizeMode === 'mde_rel') {
        mde = state.mdeRel
      } else if (state.sizeMode === 'mde_abs') {
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

      const requiredNPerGroup = state.sizeMode === 'sample_size' ? state.sampleSize : data.required_n_per_group
      const key = sampleSizeInputsKey(state)
      setState((prev) => {
        // Only reset to an equal split the FIRST time a calculation lands
        // (prev.sampleSizeResult === null) — a recalculation after editing
        // MDE/alpha/power/metrics keeps whatever the user already entered
        // (item 3.2: "старые доли сохранить как введенные").
        const firstCalc = prev.sampleSizeResult === null
        return {
          ...prev,
          groups: firstCalc ? equalSplitGroups(prev.groups) : prev.groups,
          sampleSizeResult: {
            eligibleN: data.eligible_n,
            requiredNPerGroup,
            perMetric: data.per_metric.map((m) => ({
              metric: m.metric, baselineMean: m.baseline_mean, requiredN: m.required_n_per_group, warnings: m.warnings,
            })),
            inputsKey: key,
          },
        }
      })
    } catch (e) {
      setCalcError(e instanceof Error ? e.message : 'Failed to calculate sample size')
    } finally {
      setCalculating(false)
    }
  }

  const result = state.sampleSizeResult
  const stale = result !== null && result.inputsKey !== sampleSizeInputsKey(state)
  const showProportions = result !== null || isRedesign
  const sum = groupsSum(state)
  const sumOk = Math.abs(sum - 1) < 1e-6
  const nGroups = state.groups.length || 1
  const totalRequired = result?.requiredNPerGroup != null ? result.requiredNPerGroup * nGroups : null
  const notEnoughData =
    result?.eligibleN != null && totalRequired != null && result.eligibleN < totalRequired

  // Item 3.1e: control gets the minimum required for power; the rest is
  // split evenly among the remaining (treatment) group(s).
  const minimizeControl = () => {
    if (!result?.requiredNPerGroup || !result.eligibleN) return
    const controlId =
      state.groups.find((g) => g.name.trim().toLowerCase() === 'control')?.id ?? state.groups[0]?.id
    if (!controlId) return
    const controlShare = Math.min(0.95, Math.max(0.01, result.requiredNPerGroup / result.eligibleN))
    const others = state.groups.filter((g) => g.id !== controlId)
    const otherShare = others.length > 0 ? (1 - controlShare) / others.length : 0
    setState((prev) => ({
      ...prev,
      groups: prev.groups.map((g) => (g.id === controlId ? { ...g, prop: controlShare } : { ...g, prop: otherShare })),
    }))
  }

  return (
    <div style={{ marginTop: 24 }}>
      <Typography.Title level={5}>Sample Size</Typography.Title>
      <Tooltip title={!canCalculate ? 'Name at least 2 groups and 1 metric, and select the unit column, first' : ''}>
        <Button onClick={runCalculate} loading={calculating} disabled={!canCalculate}>
          Calculate sample size
        </Button>
      </Tooltip>
      {calcError && <Alert type="error" showIcon message={calcError} style={{ marginTop: 8, maxWidth: 560 }} />}

      {result && (
        <div style={{ marginTop: 12 }}>
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
              style={{ marginBottom: 12, maxWidth: 560 }}
              message="Not enough data for this target"
              description={`You need ~${totalRequired} users total (${result.requiredNPerGroup} per group × ${nGroups} groups), but only ${result.eligibleN} are eligible. Consider a larger MDE, lower power, or switching to "Use all available data".`}
            />
          )}
          {stale && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12, maxWidth: 560 }}
              message="Inputs changed since this was calculated — press Calculate again to refresh"
            />
          )}
        </div>
      )}

      {showProportions && (
        <div style={{ marginTop: 16 }}>
          <Typography.Title level={5}>Group Proportions</Typography.Title>
          <Tooltip title={!result?.requiredNPerGroup ? 'Calculate a required sample size first' : ''}>
            <Button size="small" disabled={!result?.requiredNPerGroup} onClick={minimizeControl} style={{ marginBottom: 12 }}>
              Minimize control group
            </Button>
          </Tooltip>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, maxWidth: 500, marginTop: -8 }}>
            Sets control to the minimum required for power, the rest goes to treatment.
          </Typography.Paragraph>

          {state.groups.map((g) => {
            const eligibleForCount = result?.eligibleN ?? null
            const groupN = eligibleForCount != null ? Math.floor(g.prop * eligibleForCount) : null
            const belowRequired = result?.requiredNPerGroup != null && groupN != null && groupN < result.requiredNPerGroup
            return (
              <div key={g.id} style={{ marginBottom: 8 }}>
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
                    onChange={(v) =>
                      setState((prev) => ({
                        ...prev,
                        groups: prev.groups.map((x) => (x.id === g.id ? { ...x, prop: v ?? 0 } : x)),
                      }))
                    }
                  />
                  <Typography.Text type="secondary">
                    {(g.prop * 100).toFixed(1)}%{groupN != null ? ` · ~${groupN} users` : ''}
                  </Typography.Text>
                </Space>
                {belowRequired && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginTop: 4, maxWidth: 480 }}
                    message={`Group '${g.name.trim()}' would get ${groupN} < required ${result!.requiredNPerGroup} users — power will be below target`}
                  />
                )}
              </div>
            )
          })}
          <Alert
            type={sumOk ? 'success' : 'warning'}
            showIcon
            message={`Sum of proportions: ${sum.toFixed(3)}${sumOk ? '' : ' — must equal 1'}`}
            style={{ marginTop: 8, maxWidth: 400 }}
          />
        </div>
      )}
    </div>
  )
}
