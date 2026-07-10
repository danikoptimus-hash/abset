import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Drawer, Table as PreviewTable, Typography, Button, Space, message, Modal, Alert } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { RelativeTime } from '../components/RelativeTime'
import { SourceTag } from '../components/DatasetSelect'
import { CreateDatasetModal } from './datasets/CreateDatasetModal'
import type { components } from '../api/schema'

type DatasetOut = components['schemas']['DatasetOut']

function RefreshButton({ dataset }: { dataset: DatasetOut }) {
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const run = async () => {
    setRefreshing(true)
    try {
      const { data, error } = await apiClient.POST('/api/v1/datasets/{dataset_id}/refresh', {
        params: { path: { dataset_id: dataset.id } },
      })
      if (error) throw new Error(errorMessage(error))
      const deadline = Date.now() + 30_000
      let job = null
      while (Date.now() < deadline) {
        const resp = await apiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: data.job_id } } })
        job = resp.data
        if (job && job.status !== 'pending' && job.status !== 'running') break
        await new Promise((r) => setTimeout(r, 500))
      }
      if (job?.status === 'completed') {
        message.success(`Refreshed: ${(job.result as { n_rows: number } | null)?.n_rows ?? '?'} rows`)
        queryClient.invalidateQueries({ queryKey: ['datasets'] })
        queryClient.invalidateQueries({ queryKey: ['datasets-for-select'] })
        // The drawer's preview rows/columns (a separate query, keyed by
        // dataset id) also need to reflect the fresh data — UX package,
        // Datasets п.4.2: "обновленные fetched_at и структура колонок
        // видны в drawer" after Refresh.
        queryClient.invalidateQueries({ queryKey: ['dataset-preview', dataset.id] })
      } else {
        message.error(job?.error ?? 'Refresh failed')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const confirmRefresh = (e: React.MouseEvent) => {
    e.stopPropagation() // don't also trigger the row's preview-drawer click
    Modal.confirm({
      title: 'Refresh dataset from source?',
      content:
        'This will replace the stored snapshot with fresh data from the source. Experiments already analyzed keep their results.',
      okText: 'Refresh',
      onOk: run,
    })
  }

  return (
    <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={confirmRefresh}>
      Refresh
    </Button>
  )
}

export function DatasetsPage() {
  const [page, setPage] = useState(1)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const pageSize = 20

  const { data, isLoading } = useQuery({
    queryKey: ['datasets', page],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets', {
        params: { query: { page, page_size: pageSize } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: ['dataset-preview', previewId],
    enabled: previewId !== null,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
        params: { path: { dataset_id: previewId! }, query: { rows: 20 } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const previewedDataset = data?.items.find((d) => d.id === previewId)

  return (
    <div>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Datasets</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          Dataset
        </Button>
      </Space>
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize, total: data?.total ?? 0, onChange: setPage, showSizeChanger: false }}
        onRow={(record) => ({ onClick: () => setPreviewId(record.id), style: { cursor: 'pointer' } })}
        columns={[
          { title: 'File', dataIndex: 'filename' },
          { title: 'Source', dataIndex: 'source', render: (source: string) => <SourceTag source={source} /> },
          {
            title: 'Experiment',
            dataIndex: 'experiment_name',
            render: (name: string | null) => (name ? <Link to={`/experiments/${name}`}>{name}</Link> : '—'),
          },
          { title: 'Rows', dataIndex: 'n_rows' },
          { title: 'Uploaded By', dataIndex: 'uploaded_by_email' },
          { title: 'When', dataIndex: 'uploaded_at', render: (ts: string) => <RelativeTime iso={ts} /> },
          {
            title: 'Actions',
            key: 'actions',
            render: (_, record: DatasetOut) => (record.source === 'sql' ? <RefreshButton dataset={record} /> : null),
          },
        ]}
      />

      <CreateDatasetModal open={createOpen} onClose={() => setCreateOpen(false)} />

      <Drawer
        title={preview?.filename ?? 'Preview'}
        open={previewId !== null}
        onClose={() => setPreviewId(null)}
        width={720}
      >
        {previewedDataset?.source === 'sql' && previewedDataset.sql_text && (
          <div style={{ marginBottom: 16 }}>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="Snapshot stored in ABKit. Deleting the source table in the external database does NOT affect this dataset. Use Refresh to re-fetch current data (columns are updated automatically)."
            />
            <Space style={{ marginBottom: 4, justifyContent: 'space-between', width: '100%' }}>
              <Typography.Text strong>SQL</Typography.Text>
              {previewedDataset.fetched_at && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Last fetched <RelativeTime iso={previewedDataset.fetched_at} />
                  {previewedDataset.connection_name ? ` from ${previewedDataset.connection_name}` : ''}
                </Typography.Text>
              )}
            </Space>
            <Typography.Paragraph
              code
              style={{ whiteSpace: 'pre-wrap', background: '#f5f5f5', padding: 8, borderRadius: 4 }}
            >
              {previewedDataset.sql_text}
            </Typography.Paragraph>
          </div>
        )}
        {preview && (
          <PreviewTable
            loading={previewLoading}
            rowKey={(_, index) => String(index)}
            dataSource={preview.rows}
            pagination={false}
            size="small"
            scroll={{ x: true }}
            columns={preview.columns.map((col) => ({ title: col, dataIndex: col }))}
          />
        )}
      </Drawer>
    </div>
  )
}
