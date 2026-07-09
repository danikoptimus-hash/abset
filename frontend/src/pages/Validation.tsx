import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Typography, Select, Upload, Button, InputNumber, Checkbox, Space, Alert, Progress, Table, Tag } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage, toFormData } from '../api/client'
import { useJobPolling } from '../api/useJobPolling'

const { Dragger } = Upload

interface MethodFPR {
  method: string
  metric: string
  treatment_group: string
  n_sims: number
  fpr: number
  ci_low: number
  ci_high: number
  passed: boolean
}

interface MethodPower {
  method: string
  metric: string
  treatment_group: string
  n_sims: number
  empirical_power: number
  analytical_power: number | null
  discrepancy_warning: string | null
}

interface ValidateResult {
  aa: { methods: MethodFPR[] }
  ab: { methods: MethodPower[] }
}

export function ValidationPage() {
  const [experimentName, setExperimentName] = useState<string | undefined>(undefined)
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [nSims, setNSims] = useState(2000)
  const [compareMethods, setCompareMethods] = useState(false)
  const [effect, setEffect] = useState(0.05)

  const { phase, stage, error, result, poll, reset } = useJobPolling<ValidateResult>()

  const { data: experiments } = useQuery({
    queryKey: ['experiments-for-validation'],
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/experiments', { params: { query: { page_size: 200 } } })
      return data?.items ?? []
    },
  })

  const uploadProps: UploadProps = {
    accept: '.csv',
    multiple: false,
    showUploadList: false,
    customRequest: async (options) => {
      const file = options.file as File
      setUploading(true)
      setUploadError(null)
      try {
        const { data, error } = await apiClient.POST('/api/v1/datasets', {
          body: toFormData({ kind: 'validation', file }) as unknown as { kind: string; file: string },
        })
        if (error) throw new Error(errorMessage(error))
        setDatasetId(data.id)
        options.onSuccess?.(data)
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Failed to upload file')
        options.onError?.(e as Error)
      } finally {
        setUploading(false)
      }
    },
  }

  const runValidate = async () => {
    if (!experimentName || !datasetId) return
    reset()
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/validate', {
      params: { path: { name: experimentName } },
      body: { dataset_id: datasetId, n_sims: nSims, compare_methods: compareMethods, effect },
    })
    if (error) {
      setUploadError(errorMessage(error))
      return
    }
    await poll(data.job_id)
  }

  const canSubmit = experimentName && datasetId && phase !== 'running'

  return (
    <div>
      <Typography.Title level={4}>Validation (A/A, A/B)</Typography.Title>

      <Space direction="vertical" size={16} style={{ width: '100%', maxWidth: 480, marginBottom: 24 }}>
        <Select
          placeholder="Experiment (design config)"
          style={{ width: '100%' }}
          value={experimentName}
          onChange={setExperimentName}
          showSearch
          optionFilterProp="label"
          options={(experiments ?? []).map((e) => ({ value: e.name, label: e.name }))}
        />
        <Dragger {...uploadProps} disabled={uploading}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p>Simulation data (CSV)</p>
        </Dragger>
        <InputNumber addonBefore="n_sims" min={100} step={100} value={nSims} onChange={(v) => setNSims(v ?? 2000)} style={{ width: '100%' }} />
        <InputNumber addonBefore="Effect (A/B)" min={0} step={0.01} value={effect} onChange={(v) => setEffect(v ?? 0.05)} style={{ width: '100%' }} />
        <Checkbox checked={compareMethods} onChange={(e) => setCompareMethods(e.target.checked)}>
          Compare alternative methods
        </Checkbox>
        <Button type="primary" disabled={!canSubmit} onClick={runValidate}>
          Run Validation
        </Button>
      </Space>

      {uploadError && <Alert type="error" showIcon message={uploadError} style={{ marginBottom: 16 }} />}

      {phase === 'running' && (
        <div style={{ marginBottom: 24 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Running validation...'}</Typography.Text>
        </div>
      )}
      {phase === 'failed' && error && <Alert type="error" showIcon message={error} style={{ marginBottom: 24 }} />}

      {phase === 'completed' && result && <ValidationResults result={result} />}
    </div>
  )
}

function ValidationResults({ result }: { result: ValidateResult }) {
  return (
    <div>
      <Typography.Title level={5}>A/A: empirical FPR (false-positive rate)</Typography.Title>
      <Table
        size="small"
        rowKey={(r: MethodFPR) => `${r.metric}_${r.method}_${r.treatment_group}`}
        dataSource={result.aa.methods}
        pagination={false}
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Group', dataIndex: 'treatment_group' },
          { title: 'Method', dataIndex: 'method' },
          { title: 'n_sims', dataIndex: 'n_sims' },
          { title: 'FPR', dataIndex: 'fpr', render: (v: number) => `${(v * 100).toFixed(2)}%` },
          {
            title: '95% CI', key: 'ci',
            render: (_: unknown, r: MethodFPR) => `[${(r.ci_low * 100).toFixed(2)}%, ${(r.ci_high * 100).toFixed(2)}%]`,
          },
          {
            title: 'Verdict', dataIndex: 'passed',
            render: (v: boolean) => <Tag color={v ? 'success' : 'error'}>{v ? 'honest' : 'lying'}</Tag>,
          },
        ]}
      />

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        A/B: empirical vs analytical power
      </Typography.Title>
      <Table
        size="small"
        rowKey={(r: MethodPower) => `${r.metric}_${r.method}_${r.treatment_group}`}
        dataSource={result.ab.methods}
        pagination={false}
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Group', dataIndex: 'treatment_group' },
          { title: 'Method', dataIndex: 'method' },
          { title: 'n_sims', dataIndex: 'n_sims' },
          { title: 'Power (empirical)', dataIndex: 'empirical_power', render: (v: number) => `${(v * 100).toFixed(1)}%` },
          {
            title: 'Power (analytical)', dataIndex: 'analytical_power',
            render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`),
          },
          {
            title: 'Discrepancy', dataIndex: 'discrepancy_warning',
            render: (v: string | null) => (v ? <Tag color="warning">{v}</Tag> : '—'),
          },
        ]}
      />
    </div>
  )
}
