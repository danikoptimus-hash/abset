import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Typography, Button, Descriptions, Alert, Progress, Space, Tag, message } from 'antd'
import { apiClient, errorMessage, toFormData } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { getColumns } from './FlowImagesSection'
import { buildDesignConfig, buildExternalDesignConfig, groupsToApi, metricsToApi } from './types'
import type { WizardState } from './types'
import { PRODUCT_NAME } from '../../branding'
import { formatMb } from '../../monitoringFormat'

// Stage 4 (variant flow images): applies the wizard's staged flow-image
// state to the now-created/redesigned experiment. New images (kind='new',
// still a local File) are uploaded first; the resulting id, together with
// any kept 'existing' ids, becomes the final per-group order call — which
// also deletes (DB row + file) any of that group's images the user removed
// in the wizard, since it never reappears in that final id list (see
// abkit/db/repositories.py::FlowImageRepo.set_group_order). Groups that HAD
// images at wizard-open time but no longer have a column at all (the user
// deleted the whole column) still get an empty order call, so their images
// don't become permanently orphaned. Best-effort, same as saveHypothesis
// below — the design/redesign itself already succeeded by the time this
// runs, so a failure here shouldn't block navigation.
async function saveFlowImages(experimentName: string, state: WizardState): Promise<void> {
  try {
    const columns = getColumns(state).filter((c) => c.groupName.trim())
    const coveredGroupNames = new Set(columns.map((c) => c.groupName))

    for (const column of columns) {
      const orderedIds: string[] = []
      for (const image of column.images) {
        if (image.kind === 'existing') {
          orderedIds.push(image.id)
          continue
        }
        if (!image.file) continue
        const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/flow-images', {
          params: { path: { name: experimentName } },
          body: toFormData({
            group_name: column.groupName,
            flow_title: column.flowTitle,
            file: image.file,
          }) as unknown as { group_name: string; flow_title: string; file: string },
        })
        if (error) throw new Error(errorMessage(error))
        orderedIds.push(data.id)
      }
      const { error } = await apiClient.PUT('/api/v1/experiments/{name}/flow-images/order', {
        params: { path: { name: experimentName } },
        body: { group_name: column.groupName, flow_title: column.flowTitle, image_ids: orderedIds },
      })
      if (error) throw new Error(errorMessage(error))
    }

    for (const groupName of state.originalFlowGroupNames) {
      if (coveredGroupNames.has(groupName)) continue
      const { error } = await apiClient.PUT('/api/v1/experiments/{name}/flow-images/order', {
        params: { path: { name: experimentName } },
        body: { group_name: groupName, flow_title: '', image_ids: [] },
      })
      if (error) throw new Error(errorMessage(error))
    }
  } catch {
    message.warning('Variant flow images could not be saved automatically — retry from Redesign.')
  }
}

// The wizard's optional Hypothesis field (5-item follow-up п.14) saves into
// the experiment's existing Hypothesis markdown block — every experiment
// auto-creates one empty (abkit/db/repositories.py::ExperimentRepo.create),
// so this is always an UPDATE, never a new block; PUT .../blocks requires
// the block's real id or it would create a DUPLICATE hypothesis-kind block
// instead of filling in the existing one, hence the GET first. Best-effort:
// the design/redesign itself already succeeded by the time this runs, so a
// failure here shouldn't block navigation — the user can still fill it in
// from the experiment page.
async function saveHypothesis(experimentName: string, hypothesis: string): Promise<void> {
  try {
    const { data: blocks, error } = await apiClient.GET('/api/v1/experiments/{name}/blocks', {
      params: { path: { name: experimentName } },
    })
    if (error) throw new Error(errorMessage(error))
    const hypothesisBlock = blocks?.find((b) => b.kind === 'hypothesis')
    if (!hypothesisBlock) return
    const { error: putError } = await apiClient.PUT('/api/v1/experiments/{name}/blocks', {
      params: { path: { name: experimentName } },
      body: [
        {
          id: hypothesisBlock.id, kind: 'hypothesis', title: hypothesisBlock.title,
          content_md: hypothesis, position: hypothesisBlock.position,
        },
      ],
    })
    if (putError) throw new Error(errorMessage(putError))
  } catch {
    message.warning('The hypothesis could not be saved automatically — add it from the experiment page.')
  }
}

interface Props {
  state: WizardState
  // Set when reached via /experiments/:name/redesign (5-part package pt.3)
  // — submits to POST .../redesign (in-place replace) instead of POST
  // /design (always-create). Job result shape is identical either way.
  redesignName?: string
  // UX contract, part A: DesignWizardPage's route-blocker (useUnsavedGuard)
  // treats any WizardState different from its pristine snapshot as unsaved
  // work worth confirming before leaving — including the navigate() below,
  // which otherwise looks identical to the user clicking a nav link mid-
  // wizard. Calling this right before navigate() tells the parent "this
  // work is now saved", so its own isDirty flips false in time for the
  // blocker to let this specific navigation through unprompted.
  onSubmitted: () => void
}

type Phase = 'idle' | 'running' | 'requires_confirmation' | 'failed'

export function Step4Review({ state, redesignName, onSubmitted }: Props) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [phase, setPhase] = useState<Phase>('idle')
  const [stage, setStage] = useState<string | null>(null)
  // Admin monitoring panel (per-job peak memory) — same live-during-the-run
  // display as Analyze (AnalyzeSection.tsx/useJobPolling.ts); this page has
  // its own hand-rolled poll loop instead of the shared hook (extra states
  // like requires_confirmation with a design-specific payload), so it's
  // tracked separately here rather than through useJobPolling.
  const [peakMemoryMb, setPeakMemoryMb] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<{ overlap: number; by_experiment: Record<string, number> } | null>(null)

  // A single failed poll can be a transient blip (or the backend container
  // restarting after a crash) — give it a few retries before giving up, so
  // a real job.error (surfaced once the backend recovers) wins over a
  // generic message. See frontend/src/api/useJobPolling.ts for the same
  // pattern used by Analyze/Validate.
  const MAX_CONSECUTIVE_FAILURES = 5

  const pollJob = async (jobId: string): Promise<void> => {
    let consecutiveFailures = 0
    for (;;) {
      const { data } = await apiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } })
      if (!data) {
        consecutiveFailures += 1
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          throw new Error('Analysis worker stopped unexpectedly. Check server logs or try again.')
        }
        await new Promise((r) => setTimeout(r, 1000))
        continue
      }
      consecutiveFailures = 0
      setStage(data.progress?.stage ?? null)
      setPeakMemoryMb(data.peak_memory_mb ?? null)
      if (data.status === 'completed') {
        const experimentName = (data.result as { experiment_name?: string } | null)?.experiment_name
        if (experimentName) {
          if (state.hypothesis.trim()) {
            await saveHypothesis(experimentName, state.hypothesis.trim())
          }
          const hasFlowImageWork =
            getColumns(state).some((c) => c.images.length > 0) || state.originalFlowGroupNames.length > 0
          if (hasFlowImageWork) {
            await saveFlowImages(experimentName, state)
          }
          // B.3: explicit invalidation rather than relying solely on the
          // navigate()-triggered remount below — the list this experiment
          // now belongs to (new row, or redesign changing its config) must
          // not show stale data if anything stays mounted across this nav
          // (e.g. a background tab keeping ExperimentsList alive).
          queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
          if (redesignName) {
            queryClient.invalidateQueries({ queryKey: queryKeys.experiment(redesignName) })
            queryClient.invalidateQueries({ queryKey: queryKeys.experimentBlocks(redesignName) })
            queryClient.invalidateQueries({ queryKey: queryKeys.experimentDesignDataset(redesignName) })
            // Item 6: redesign regenerates the split -> a stale per-group
            // download list (old group names/row counts) would otherwise
            // survive if the Design tab stays mounted across the redesign.
            queryClient.invalidateQueries({ queryKey: queryKeys.experimentSamples(redesignName) })
          }
          onSubmitted()
          navigate(`/experiments/${experimentName}`)
        }
        return
      }
      if (data.status === 'failed') {
        setPhase('failed')
        setError(data.error ?? 'Design failed')
        return
      }
      if (data.status === 'requires_confirmation') {
        setPhase('requires_confirmation')
        setConfirmation(data.result as { overlap: number; by_experiment: Record<string, number> })
        return
      }
      await new Promise((r) => setTimeout(r, 1000))
    }
  }

  const isExternal = state.splitMode === 'external'

  const submit = async (confirmed: boolean) => {
    if (!isExternal && !state.datasetId) return
    setPhase('running')
    setError(null)
    setConfirmation(null)
    setPeakMemoryMb(null)
    try {
      if (isExternal) {
        // Item 12: no dataset, no isolation overlap to confirm, redesign
        // isn't offered for external experiments — always a plain create.
        const config = buildExternalDesignConfig(state)
        const { data, error } = await apiClient.POST('/api/v1/design', {
          body: { config, confirmed: false },
        })
        if (error) throw new Error(errorMessage(error))
        await pollJob(data.job_id)
        return
      }

      const config = buildDesignConfig(state)
      if (state.sizeMode === 'mde_abs') {
        const mdeAbsMetric = state.metrics.find((m) => m.id === state.mdeAbsMetricId)
        if (!mdeAbsMetric) {
          throw new Error('Select a metric for the absolute MDE on the previous step')
        }
        const { data: baselineData } = await apiClient.POST('/api/v1/datasets/{dataset_id}/metric-baseline', {
          params: { path: { dataset_id: state.datasetId! } },
          body: {
            name: mdeAbsMetric.name,
            type: mdeAbsMetric.type,
            pre_col: mdeAbsMetric.preCol,
            num: mdeAbsMetric.num,
            den: mdeAbsMetric.den,
          },
        })
        const baseline = baselineData?.baseline_mean
        if (!baseline) {
          throw new Error('Could not determine the baseline for the absolute MDE')
        }
        config.mde = state.mdeAbsValue / baseline
        config.mde_abs_input = state.mdeAbsValue
        config.mde_source_metric = mdeAbsMetric.name
      }

      const { data, error } = redesignName
        ? await apiClient.POST('/api/v1/experiments/{name}/redesign', {
            params: { path: { name: redesignName } },
            body: { config, dataset_id: state.datasetId, confirmed },
          })
        : await apiClient.POST('/api/v1/design', {
            body: { config, dataset_id: state.datasetId, confirmed },
          })
      if (error) throw new Error(errorMessage(error))
      await pollJob(data.job_id)
    } catch (e) {
      setPhase('failed')
      setError(e instanceof Error ? e.message : 'Failed to start the design')
    }
  }

  return (
    <div>
      <Typography.Title level={5}>Summary</Typography.Title>
      <Descriptions bordered column={1} size="small" style={{ marginBottom: 24 }}>
        <Descriptions.Item label="Split mode">
          {isExternal ? 'External split (e.g. Firebase)' : `${PRODUCT_NAME} split`}
        </Descriptions.Item>
        <Descriptions.Item label="Name">{state.name || '—'}</Descriptions.Item>
        <Descriptions.Item label="Hypothesis">{state.hypothesis.trim() || '—'}</Descriptions.Item>
        {!isExternal && <Descriptions.Item label="Unit Column">{state.unitCol || '—'}</Descriptions.Item>}
        <Descriptions.Item label="Groups">
          {Object.entries(groupsToApi(state))
            .map(([name, prop]) => `${name}: ${(prop * 100).toFixed(0)}%`)
            .join(', ')}
        </Descriptions.Item>
        <Descriptions.Item label="Metrics">
          {metricsToApi(state)
            .map((m) => `${m.name} (${m.type})`)
            .join(', ')}
        </Descriptions.Item>
        {isExternal ? (
          <Descriptions.Item label="Expected sample size">{state.sampleSize || '—'}</Descriptions.Item>
        ) : (
          <>
            <Descriptions.Item label="Strata">{state.strata.join(', ') || '—'}</Descriptions.Item>
            <Descriptions.Item label="Split Method">{state.splitMethod}</Descriptions.Item>
            <Descriptions.Item label="Isolation">{state.isolation}</Descriptions.Item>
          </>
        )}
      </Descriptions>

      {phase === 'idle' && (
        <Button type="primary" size="large" onClick={() => submit(false)}>
          {redesignName ? 'Redesign' : 'Design'}
        </Button>
      )}

      {phase === 'running' && (
        <div>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Starting...'}</Typography.Text>
          {peakMemoryMb != null && (
            <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
              Peak memory: {formatMb(peakMemoryMb)}
            </Typography.Text>
          )}
        </div>
      )}

      {phase === 'requires_confirmation' && confirmation && (
        <Alert
          type="warning"
          showIcon
          message="Overlap detected with other active experiments"
          description={
            <div>
              <Typography.Paragraph>
                Total overlapping units: <b>{confirmation.overlap}</b>
              </Typography.Paragraph>
              <Space direction="vertical" style={{ marginBottom: 12 }}>
                {Object.entries(confirmation.by_experiment).map(([name, n]) => (
                  <Tag key={name}>
                    {name}: {n}
                  </Tag>
                ))}
              </Space>
              <br />
              <Button type="primary" onClick={() => submit(true)}>
                Continue despite the overlap
              </Button>
            </div>
          }
        />
      )}

      {phase === 'failed' && error && (
        <div>
          <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />
          <Button onClick={() => submit(false)}>Retry</Button>
        </div>
      )}
    </div>
  )
}
