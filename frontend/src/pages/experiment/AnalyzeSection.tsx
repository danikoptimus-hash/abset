import { useState } from 'react'
import { Upload, Button, Space, Select, Checkbox, Typography, Alert, Progress, Tooltip } from 'antd'
import { InboxOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage, toFormData } from '../../api/client'
import { useJobPolling } from '../../api/useJobPolling'
import { AnalyzeResults } from './AnalyzeResults'
import { experimentResultsQueryKey, fetchExperimentResults } from './resultsQuery'

const { Dragger } = Upload

const CORRECTION_OPTIONS = [
  { value: 'holm', label: 'holm' },
  { value: 'bonferroni', label: 'bonferroni' },
  { value: 'fdr_bh', label: 'fdr_bh (Benjamini-Hochberg)' },
  { value: 'none', label: 'no correction' },
]

export function AnalyzeSection({ experimentName, hasAssignments }: { experimentName: string; hasAssignments: boolean }) {
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [postColumns, setPostColumns] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const [correction, setCorrection] = useState('holm')
  const [compareMethods, setCompareMethods] = useState(false)
  const [dateCol, setDateCol] = useState<string | undefined>(undefined)

  const { phase, stage, error, poll, reset } = useJobPolling<{ experiment_name: string }>()

  // Same query key as ResultsSection (Results tab) — shares one cache entry,
  // so whichever tab mounts first fetches and invalidateQueries below
  // refreshes both at once (including one that isn't currently mounted).
  const { data: results } = useQuery({
    queryKey: experimentResultsQueryKey(experimentName),
    queryFn: () => fetchExperimentResults(experimentName),
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
          body: toFormData({ kind: 'post_analysis', experiment_name: experimentName, file }) as unknown as {
            kind: string
            file: string
          },
        })
        if (error) throw new Error(errorMessage(error))
        setDatasetId(data.id)
        setPostColumns(data.columns)
        options.onSuccess?.(data)
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : 'Failed to upload file')
        options.onError?.(e as Error)
      } finally {
        setUploading(false)
      }
    },
  }

  const runAnalyze = async () => {
    reset()
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/analyze', {
      params: { path: { name: experimentName } },
      body: { dataset_id: datasetId!, correction, compare_methods: compareMethods, date_col: dateCol ?? null },
    })
    if (error) {
      setUploadError(errorMessage(error))
      return
    }
    await poll(data.job_id)
    queryClient.invalidateQueries({ queryKey: experimentResultsQueryKey(experimentName) })
  }

  const runAnalyzeDemo = async () => {
    reset()
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/analyze/demo', {
      params: { path: { name: experimentName } },
      body: { effect: 0.03 },
    })
    if (error) {
      setUploadError(errorMessage(error))
      return
    }
    await poll(data.job_id)
    queryClient.invalidateQueries({ queryKey: experimentResultsQueryKey(experimentName) })
  }

  return (
    <div>
      {uploadError && <Alert type="error" showIcon message={uploadError} style={{ marginBottom: 16 }} closable onClose={() => setUploadError(null)} />}

      {phase !== 'running' && (
        <>
          <Space align="start" size={16} style={{ marginBottom: 16 }}>
            <Dragger {...uploadProps} style={{ width: 420 }} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p>Upload post-period data (CSV)</p>
            </Dragger>
            <Tooltip title={hasAssignments ? '' : 'No assignments for this experiment'}>
              <Button icon={<ThunderboltOutlined />} disabled={!hasAssignments} onClick={runAnalyzeDemo}>
                Generate demo post-period data (+3% effect)
              </Button>
            </Tooltip>
          </Space>

          <Space wrap style={{ marginBottom: 16 }}>
            <Select
              style={{ width: 220 }}
              value={correction}
              onChange={setCorrection}
              options={CORRECTION_OPTIONS}
              placeholder="Multiple-testing correction"
            />
            <Checkbox checked={compareMethods} onChange={(e) => setCompareMethods(e.target.checked)}>
              Compare alternative methods
            </Checkbox>
            {postColumns.length > 0 && (
              <Select
                style={{ width: 220 }}
                placeholder="Date column (for cumulative lift)"
                allowClear
                value={dateCol}
                onChange={setDateCol}
                options={postColumns.map((c) => ({ value: c, label: c }))}
              />
            )}
          </Space>
          {postColumns.length > 0 && (
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              If the data has multiple rows per user (broken down by day), specify the date column — the app
              will automatically aggregate them for the main analysis and build a daily cumulative lift chart.
            </Typography.Paragraph>
          )}

          {datasetId && (
            <Button type="primary" onClick={runAnalyze} style={{ marginBottom: 24 }}>
              Run Analysis
            </Button>
          )}
        </>
      )}

      {phase === 'running' && (
        <div style={{ marginBottom: 24 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Starting analysis...'}</Typography.Text>
        </div>
      )}

      {phase === 'failed' && error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 24 }} />
      )}

      {results && <AnalyzeResults data={results} />}
    </div>
  )
}
