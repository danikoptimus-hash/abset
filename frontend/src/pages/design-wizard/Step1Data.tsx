import { useState } from 'react'
import { Upload, Button, Collapse, Table, Typography, Alert, Space, Spin } from 'antd'
import { InboxOutlined, ThunderboltOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import {
  DESIGN_EXAMPLE_ROWS,
  DESIGN_SQL_EXAMPLE,
  WHAT_IS_THIS_DATA,
  EXAMPLE_EXPLANATION,
  SQL_EXPLANATION,
  NO_DATA_EXPLANATION,
} from './helpTexts'
import type { WizardState, GroupFormRow, MetricFormRow, MetricConfig, DesignConfig } from './types'
import { nextId } from './types'

const { Dragger } = Upload

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
}

function groupsFromApi(groups: Record<string, number>): GroupFormRow[] {
  return Object.entries(groups).map(([name, prop]) => ({ id: nextId('group'), name, prop }))
}

function metricsFromApi(metrics: MetricConfig[]): MetricFormRow[] {
  return metrics.map((m) => ({
    id: nextId('metric'),
    name: m.name,
    type: m.type as MetricFormRow['type'],
    role: m.role as MetricFormRow['role'],
    preCol: m.pre_col ?? null,
    num: m.num ?? null,
    den: m.den ?? null,
  }))
}

export function Step1Data({ state, setState }: Props) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const applyDatasetResult = (
    datasetId: string,
    columns: string[],
    dtypes: Record<string, string>,
    nRows: number,
    previewRows: Record<string, unknown>[],
  ) => {
    setState((prev) => ({ ...prev, datasetId, columns, dtypes, nRows, previewRows }))
  }

  const fetchPreview = async (datasetId: string) => {
    const { data } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
      params: { path: { dataset_id: datasetId }, query: { rows: 20 } },
    })
    return data?.rows ?? []
  }

  const uploadProps: UploadProps = {
    accept: '.csv',
    multiple: false,
    showUploadList: false,
    customRequest: async (options) => {
      const file = options.file as File
      setUploading(true)
      setError(null)
      try {
        // openapi-typescript типизирует multipart-файл как `string` (нет
        // отдельного бинарного типа) — openapi-fetch сам сериализует body в
        // FormData, когда видит File/Blob, отсюда единственный `as unknown as
        // string` на само значение (не на весь body).
        const { data, error } = await apiClient.POST('/api/v1/datasets', {
          body: { kind: 'pre_design', file: file as unknown as string },
        })
        if (error) throw new Error(errorMessage(error))
        const previewRows = await fetchPreview(data.id)
        applyDatasetResult(data.id, data.columns, data.dtypes ?? {}, data.n_rows, previewRows)
        options.onSuccess?.(data)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Не удалось загрузить файл')
        options.onError?.(e as Error)
      } finally {
        setUploading(false)
      }
    },
  }

  const handleDemoData = async () => {
    setUploading(true)
    setError(null)
    try {
      const { data, error } = await apiClient.POST('/api/v1/datasets/demo-design')
      if (error) throw new Error(errorMessage(error))
      const previewResp = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
        params: { path: { dataset_id: data.dataset_id }, query: { rows: 20 } },
      })
      const previewRows = previewResp.data?.rows ?? []
      // suggested_config приходит как dict[str, Any] (Record<string, unknown>
      // в сгенерированных типах) — форма гарантированно DesignConfig, кастуем.
      const config = data.suggested_config as unknown as DesignConfig
      // demo-design не отдает dtypes напрямую (только suggested_config) —
      // выводим численность колонок из JS-типов значений превью (для
      // Select числовых колонок в шаге 2/3: pre_col/num/den).
      const inferredDtypes: Record<string, string> = {}
      for (const [key, value] of Object.entries(previewRows[0] ?? {})) {
        inferredDtypes[key] = typeof value === 'number' ? 'float64' : 'object'
      }

      setState((prev) => ({
        ...prev,
        datasetId: data.dataset_id,
        columns: Object.keys(previewRows[0] ?? {}),
        dtypes: inferredDtypes,
        nRows: 5000,
        previewRows,
        name: config.name,
        unitCol: config.unit_col,
        groups: groupsFromApi(config.groups),
        metrics: metricsFromApi(config.metrics),
        strata: config.strata ?? [],
        splitMethod: config.split_method,
        sizeMode: config.sample_size ? 'sample_size' : 'all',
        sampleSize: config.sample_size ?? prev.sampleSize,
      }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось сгенерировать демо-данные')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <Typography.Title level={5}>Загрузите данные о ваших пользователях-кандидатах</Typography.Title>

      <Collapse
        ghost
        style={{ marginBottom: 16 }}
        items={[
          {
            key: 'what',
            label: '❓ Что это за данные и что в них должно быть',
            children: <Typography.Paragraph style={{ whiteSpace: 'pre-line' }}>{WHAT_IS_THIS_DATA}</Typography.Paragraph>,
          },
          {
            key: 'example',
            label: '📊 Пример: как должны выглядеть данные',
            children: (
              <>
                <Table
                  size="small"
                  dataSource={DESIGN_EXAMPLE_ROWS}
                  rowKey="user_id"
                  pagination={false}
                  columns={Object.keys(DESIGN_EXAMPLE_ROWS[0]).map((k) => ({ title: k, dataIndex: k }))}
                  style={{ marginBottom: 12 }}
                />
                <Typography.Paragraph style={{ whiteSpace: 'pre-line' }}>{EXAMPLE_EXPLANATION}</Typography.Paragraph>
              </>
            ),
          },
          {
            key: 'sql',
            label: '💡 Как выгрузить данные из БД (SQL-пример)',
            children: (
              <>
                <Typography.Paragraph code style={{ whiteSpace: 'pre' }}>
                  {DESIGN_SQL_EXAMPLE}
                </Typography.Paragraph>
                <Typography.Paragraph>{SQL_EXPLANATION}</Typography.Paragraph>
              </>
            ),
          },
          {
            key: 'nodata',
            label: '❓ Нет данных под рукой — хочу просто попробовать',
            children: <Typography.Paragraph style={{ whiteSpace: 'pre-line' }}>{NO_DATA_EXPLANATION}</Typography.Paragraph>,
          },
        ]}
      />

      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}

      <Space align="start" size={16} style={{ width: '100%' }}>
        <Dragger {...uploadProps} style={{ width: 480 }} disabled={uploading}>
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p>Перетащите CSV сюда или нажмите для выбора файла</p>
        </Dragger>
        <Button icon={<ThunderboltOutlined />} onClick={handleDemoData} loading={uploading}>
          Демо-данные
        </Button>
      </Space>

      {uploading && (
        <div style={{ marginTop: 16 }}>
          <Spin /> Обрабатываем данные...
        </div>
      )}

      {state.datasetId && state.previewRows.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <Alert
            type="success"
            showIcon
            message={`Данные загружены: ${state.nRows} строк, ${Object.keys(state.previewRows[0]).length} колонок`}
            style={{ marginBottom: 12 }}
          />
          <Table
            size="small"
            dataSource={state.previewRows}
            rowKey={(_, i) => String(i)}
            pagination={false}
            scroll={{ x: true }}
            columns={Object.keys(state.previewRows[0]).map((k) => ({ title: k, dataIndex: k }))}
          />
        </div>
      )}
    </div>
  )
}
