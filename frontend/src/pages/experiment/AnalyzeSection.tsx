import { useState } from 'react'
import { Upload, Button, Space, Select, Checkbox, Typography, Alert, Progress, Tooltip } from 'antd'
import { InboxOutlined, ThunderboltOutlined, DownloadOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage, toFormData } from '../../api/client'
import { useJobPolling } from '../../api/useJobPolling'
import { AnalyzeResults } from './AnalyzeResults'
import type { AnalysisResultsOut } from './analyzeTypes'

const { Dragger } = Upload

const CORRECTION_OPTIONS = [
  { value: 'holm', label: 'holm' },
  { value: 'bonferroni', label: 'bonferroni' },
  { value: 'fdr_bh', label: 'fdr_bh (Benjamini-Hochberg)' },
  { value: 'none', label: 'без поправки' },
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

  const { data: results, refetch: refetchResults } = useQuery({
    queryKey: ['experiment-results', experimentName],
    enabled: false,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/results', {
        params: { path: { name: experimentName } },
      })
      if (error) throw new Error(errorMessage(error))
      return data as unknown as AnalysisResultsOut
    },
  })

  // Существующие результаты (например, после перезагрузки страницы) — если
  // анализ уже проводился раньше, показываем их сразу без повторного запуска.
  useQuery({
    queryKey: ['experiment-results-initial', experimentName],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/results', {
        params: { path: { name: experimentName } },
      })
      if (error) return null
      queryClient.setQueryData(['experiment-results', experimentName], data)
      return data
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
        setUploadError(e instanceof Error ? e.message : 'Не удалось загрузить файл')
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
    refetchResults()
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
    refetchResults()
  }

  return (
    <div>
      <Typography.Title level={4}>Анализ</Typography.Title>

      {uploadError && <Alert type="error" showIcon message={uploadError} style={{ marginBottom: 16 }} closable onClose={() => setUploadError(null)} />}

      {phase !== 'running' && (
        <>
          <Space align="start" size={16} style={{ marginBottom: 16 }}>
            <Dragger {...uploadProps} style={{ width: 420 }} disabled={uploading}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p>Загрузить пост-данные (CSV)</p>
            </Dragger>
            <Tooltip title={hasAssignments ? '' : 'Нет назначений (assignments) для этого эксперимента'}>
              <Button icon={<ThunderboltOutlined />} disabled={!hasAssignments} onClick={runAnalyzeDemo}>
                Сгенерировать демо пост-данные (+3% эффект)
              </Button>
            </Tooltip>
          </Space>

          <Space wrap style={{ marginBottom: 16 }}>
            <Select
              style={{ width: 220 }}
              value={correction}
              onChange={setCorrection}
              options={CORRECTION_OPTIONS}
              placeholder="Поправка на множественность"
            />
            <Checkbox checked={compareMethods} onChange={(e) => setCompareMethods(e.target.checked)}>
              Сравнить альтернативные методы
            </Checkbox>
            {postColumns.length > 0 && (
              <Select
                style={{ width: 220 }}
                placeholder="Колонка даты (для кумулятивного лифта)"
                allowClear
                value={dateCol}
                onChange={setDateCol}
                options={postColumns.map((c) => ({ value: c, label: c }))}
              />
            )}
          </Space>
          {postColumns.length > 0 && (
            <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
              Если в данных несколько строк на юзера (разбивка по дням), укажите колонку даты — программа
              автоматически агрегирует их для основного анализа и построит кумулятивный лифт по дням.
            </Typography.Paragraph>
          )}

          {datasetId && (
            <Button type="primary" onClick={runAnalyze} style={{ marginBottom: 24 }}>
              Запустить анализ
            </Button>
          )}
        </>
      )}

      {phase === 'running' && (
        <div style={{ marginBottom: 24 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Запускаем анализ...'}</Typography.Text>
        </div>
      )}

      {phase === 'failed' && error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 24 }} />
      )}

      {results && (
        <>
          <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${experimentName}/reports/report.html`} target="_blank" style={{ marginBottom: 24 }}>
            Скачать HTML-отчет
          </Button>
          <AnalyzeResults data={results} experimentName={experimentName} />
        </>
      )}
    </div>
  )
}
