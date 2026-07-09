import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Typography, Button, Descriptions, Alert, Progress, Space, Tag } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import { buildDesignConfig, groupsToApi, metricsToApi } from './types'
import type { WizardState } from './types'

interface Props {
  state: WizardState
}

type Phase = 'idle' | 'running' | 'requires_confirmation' | 'failed'

export function Step4Review({ state }: Props) {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('idle')
  const [stage, setStage] = useState<string | null>(null)
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
      if (data.status === 'completed') {
        const experimentName = (data.result as { experiment_name?: string } | null)?.experiment_name
        if (experimentName) {
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

  const submit = async (confirmed: boolean) => {
    if (!state.datasetId) return
    setPhase('running')
    setError(null)
    setConfirmation(null)
    try {
      const config = buildDesignConfig(state)
      if (state.sizeMode === 'mde_abs') {
        const mdeAbsMetric = state.metrics.find((m) => m.id === state.mdeAbsMetricId)
        if (!mdeAbsMetric) {
          throw new Error('Select a metric for the absolute MDE on the previous step')
        }
        const { data: baselineData } = await apiClient.POST('/api/v1/datasets/{dataset_id}/metric-baseline', {
          params: { path: { dataset_id: state.datasetId } },
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

      const { data, error } = await apiClient.POST('/api/v1/design', {
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
        <Descriptions.Item label="Name">{state.name || '—'}</Descriptions.Item>
        <Descriptions.Item label="Unit Column">{state.unitCol || '—'}</Descriptions.Item>
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
        <Descriptions.Item label="Strata">{state.strata.join(', ') || '—'}</Descriptions.Item>
        <Descriptions.Item label="Split Method">{state.splitMethod}</Descriptions.Item>
        <Descriptions.Item label="Isolation">{state.isolation}</Descriptions.Item>
      </Descriptions>

      {phase === 'idle' && (
        <Button type="primary" size="large" onClick={() => submit(false)}>
          Design
        </Button>
      )}

      {phase === 'running' && (
        <div>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Starting...'}</Typography.Text>
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
