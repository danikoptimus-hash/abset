import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Table, Drawer, Table as PreviewTable, Typography, Button, Space, message, Modal, Alert, Tooltip, Input, Select, Tag,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, EditOutlined, DeleteOutlined, CheckSquareOutlined, CloseOutlined,
} from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { RelativeTime } from '../components/RelativeTime'
import { SourceTag } from '../components/DatasetSelect'
import { StopClickPropagation } from '../components/StopClickPropagation'
import { CreateDatasetModal } from './datasets/CreateDatasetModal'
import { EditDatasetModal } from './datasets/EditDatasetModal'
import { BulkDeleteDatasetsModal } from '../components/datasets/BulkDeleteDatasetsModal'
import type { BulkDeleteDatasetsResult } from '../components/datasets/BulkDeleteDatasetsModal'
import { useAuth, hasMinRole } from '../auth/AuthContext'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import type { components } from '../api/schema'
import { PRODUCT_NAME } from '../branding'

type DatasetOut = components['schemas']['DatasetOut']

// Shared by the table row's icon action and the preview drawer's labeled
// button (UX-package, Datasets п.1.1a/1.1b) — same confirm+run+poll flow,
// different presentation.
function useRefreshDataset(datasetId: string) {
  const queryClient = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const run = async () => {
    setRefreshing(true)
    try {
      const { data, error } = await apiClient.POST('/api/v1/datasets/{dataset_id}/refresh', {
        params: { path: { dataset_id: datasetId } },
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
        queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
        queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
        // The drawer's preview rows/columns (a separate query, keyed by
        // dataset id) also need to reflect the fresh data — UX package,
        // Datasets п.1.3: "обновленные fetched_at и структура колонок
        // видны в drawer" after Refresh.
        queryClient.invalidateQueries({ queryKey: queryKeys.datasetPreview(datasetId) })
      } else {
        message.error(job?.error ?? 'Refresh failed')
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Refresh failed')
    } finally {
      setRefreshing(false)
    }
  }

  const confirmRefresh = () => {
    Modal.confirm({
      title: 'Refresh dataset from source?',
      content:
        'This will replace the stored snapshot with fresh data from the source. Experiments already analyzed keep their results.',
      okText: 'Refresh',
      onOk: run,
    })
  }

  return { refreshing, confirmRefresh }
}

// Datasets table's Actions column, source=sql only — hover-reveal icon like
// the rest of the app's row actions (ExperimentsList).
function RefreshRowAction({ dataset }: { dataset: DatasetOut }) {
  const { refreshing, confirmRefresh } = useRefreshDataset(dataset.id)
  return (
    <Tooltip title="Re-fetch data from source">
      <Button
        className="hover-actions"
        size="small"
        aria-label="Refresh"
        icon={<ReloadOutlined />}
        loading={refreshing}
        onClick={(e) => {
          e.stopPropagation() // don't also trigger the row's preview-drawer click
          confirmRefresh()
        }}
      />
    </Tooltip>
  )
}

// Preview drawer's labeled button, next to the source info.
function RefreshDrawerButton({ dataset }: { dataset: DatasetOut }) {
  const { refreshing, confirmRefresh } = useRefreshDataset(dataset.id)
  return (
    <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={confirmRefresh}>
      Refresh from source
    </Button>
  )
}

// UX package, Datasets §2.2, strengthened (5-part package pt.1): checks usage
// first (GET .../usage), then ALWAYS shows the same strict typed-DELETE
// Modal — the icons for Edit/Delete sit close together in the Actions
// column, so a plain confirm on unused datasets was too easy to trigger by
// accident. Used datasets additionally show the referencing-experiments
// list. The backend independently re-enforces this (defense in depth — see
// abkit/jobs.py::run_delete_dataset), so a stale usage check here can't
// bypass it.
function DeleteDatasetAction({ dataset, onDeleted }: { dataset: DatasetOut; onDeleted: () => void }) {
  const [deleting, setDeleting] = useState(false)
  const [confirmModal, setConfirmModal] = useState<{ experiments: string[] } | null>(null)
  const [typedConfirm, setTypedConfirm] = useState('')

  const doDelete = async () => {
    setDeleting(true)
    try {
      const { error } = await apiClient.DELETE('/api/v1/datasets/{dataset_id}', {
        params: { path: { dataset_id: dataset.id } },
        body: { confirm: 'DELETE' },
      })
      if (error) throw new Error(errorMessage(error))
      message.success(`Deleted ${dataset.filename}`)
      setConfirmModal(null)
      setTypedConfirm('')
      onDeleted()
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to delete dataset')
    } finally {
      setDeleting(false)
    }
  }

  const startDelete = async () => {
    setDeleting(true)
    try {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/usage', {
        params: { path: { dataset_id: dataset.id } },
      })
      if (error) throw new Error(errorMessage(error))
      setConfirmModal({ experiments: data.experiments })
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to check dataset usage')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <Tooltip title="Delete">
        <Button
          className="hover-actions"
          danger
          size="small"
          aria-label="Delete"
          icon={<DeleteOutlined />}
          loading={deleting}
          onClick={(e) => {
            e.stopPropagation()
            startDelete()
          }}
        />
      </Tooltip>
      <Modal
        title={`Delete dataset ${dataset.filename}?`}
        open={confirmModal !== null}
        // Modal renders via a React portal, but click events still bubble
        // through the REACT tree (not the DOM tree) to the table row's
        // onClick — without stopPropagation, clicking Cancel/OK here also
        // "clicks" the row underneath and opens its preview drawer.
        onCancel={(e) => {
          e.stopPropagation()
          setConfirmModal(null)
          setTypedConfirm('')
        }}
        onOk={(e) => {
          e.stopPropagation()
          doDelete()
        }}
        okText="Delete"
        okButtonProps={{ danger: true, disabled: typedConfirm !== 'DELETE', loading: deleting }}
        destroyOnHidden
      >
        <StopClickPropagation>
          {confirmModal && confirmModal.experiments.length > 0 && (
            <Typography.Paragraph>
              Used by experiments: <strong>{confirmModal.experiments.join(', ')}</strong>. Deleting this dataset
              will not affect their existing analysis results, but their data source will show as deleted.
            </Typography.Paragraph>
          )}
          <Typography.Paragraph>This cannot be undone. Type <Typography.Text code>DELETE</Typography.Text> to confirm.</Typography.Paragraph>
          <Input value={typedConfirm} onChange={(e) => setTypedConfirm(e.target.value)} placeholder="DELETE" />
        </StopClickPropagation>
      </Modal>
    </>
  )
}

// Item 1 bug fix: a dataset can be used by more than one experiment (or by
// the same experiment for more than one purpose — design AND later
// analyze) — experiment_datasets is a many-to-many table, so the column
// shows every use, not just one. Collapses beyond 2 into a "+N" tag with a
// tooltip listing the rest, same pattern as TagBadge/TagList's overflow.
const KIND_LABELS: Record<string, string> = {
  pre_design: 'design',
  post_analysis: 'analysis',
  validation: 'validation',
}

function ExperimentUsageCell({ experiments }: { experiments: DatasetOut['experiments'] }) {
  if (!experiments || experiments.length === 0) return <>—</>
  const MAX_VISIBLE = 2
  const visible = experiments.slice(0, MAX_VISIBLE)
  const overflow = experiments.slice(MAX_VISIBLE)
  return (
    <Space size={4} wrap>
      {visible.map((use, i) => (
        <Link key={`${use.experiment_id}-${use.kind}-${i}`} to={`/experiments/${use.experiment_name}`}>
          {use.experiment_name} <Tag style={{ marginInlineEnd: 0 }}>{KIND_LABELS[use.kind] ?? use.kind}</Tag>
        </Link>
      ))}
      {overflow.length > 0 && (
        <Tooltip
          title={overflow.map((use, i) => (
            <div key={`${use.experiment_id}-${use.kind}-${i}`}>
              {use.experiment_name} ({KIND_LABELS[use.kind] ?? use.kind})
            </div>
          ))}
        >
          <Tag>+{overflow.length}</Tag>
        </Tooltip>
      )}
    </Space>
  )
}

export function DatasetsPage() {
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('')
  const [source, setSource] = useState<string | undefined>(undefined)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<DatasetOut | null>(null)
  const pageSize = 20
  const debouncedQ = useDebouncedValue(q, 300)
  const queryClient = useQueryClient()

  // Reset to page 1 whenever the search/filter changes — otherwise a
  // narrowed result set can leave the user stranded on a page that no
  // longer exists.
  useEffect(() => {
    setPage(1)
  }, [debouncedQ, source])

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.datasets(page, debouncedQ, source),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets', {
        params: { query: { page, page_size: pageSize, q: debouncedQ || undefined, source } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: queryKeys.datasetPreview(previewId),
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
  const { user } = useAuth()
  const canRefresh = hasMinRole(user, 'editor')
  const isAdmin = hasMinRole(user, 'admin')
  const canEditDataset = (record: DatasetOut) => isAdmin || (!!user && record.uploaded_by === user.id)

  const invalidateAfterDelete = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
    queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
    if (previewId) setPreviewId(null)
  }

  // Bulk select (mirrors ExperimentsList.tsx's pattern, reused not duplicated
  // in spirit — same toggle + checkbox-column + action-bar shape, adapted for
  // dataset ids instead of experiment names).
  const [bulkMode, setBulkMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [bulkDeleteTargets, setBulkDeleteTargets] = useState<DatasetOut[] | null>(null)

  const exitBulkMode = () => {
    setBulkMode(false)
    setSelectedIds([])
  }

  const handleBulkDeleteDone = (result: BulkDeleteDatasetsResult) => {
    setBulkDeleteTargets(null)
    exitBulkMode()
    invalidateAfterDelete()
    if (result.skipped.length === 0) {
      message.success(`Deleted ${result.deleted.length} dataset${result.deleted.length === 1 ? '' : 's'}`)
    } else {
      const skippedNames = result.skipped
        .map((s) => data?.items.find((d) => d.id === s.dataset_id)?.filename ?? s.dataset_id)
        .join(', ')
      Modal.info({
        title: 'Bulk delete finished',
        content: (
          <p>
            Deleted {result.deleted.length}, skipped {result.skipped.length} (no permission): {skippedNames}
          </p>
        ),
      })
    }
  }

  return (
    <div>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Datasets</Typography.Title>
        <Space>
          {canRefresh && (
            // Icon-only (пакет UI-фиксов, item 1): подпись/aria-label
            // динамические — в bulk-режиме кнопка становится "Cancel".
            // aria-label держит getByRole('button', {name:'Bulk select'}) в
            // e2e рабочим ровно когда режим не активен.
            <Tooltip title={bulkMode ? 'Cancel' : 'Bulk select'}>
              <Button
                aria-label={bulkMode ? 'Cancel' : 'Bulk select'}
                icon={bulkMode ? <CloseOutlined /> : <CheckSquareOutlined />}
                onClick={() => (bulkMode ? exitBulkMode() : setBulkMode(true))}
              />
            </Tooltip>
          )}
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            Dataset
          </Button>
        </Space>
      </Space>
      <Space style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="Search datasets..."
          style={{ width: 280 }}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select
          allowClear
          aria-label="Source"
          placeholder="Source"
          style={{ width: 140 }}
          value={source}
          onChange={setSource}
          options={[
            { value: 'upload', label: 'Upload' },
            { value: 'sql', label: 'SQL' },
            { value: 'demo', label: 'Demo' },
          ]}
        />
      </Space>

      {bulkMode && selectedIds.length > 0 && (
        <Space style={{ marginBottom: 12, padding: '8px 12px', background: '#F0F5F3', borderRadius: 6 }}>
          <span>{selectedIds.length} selected</span>
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            aria-label="Delete selected"
            onClick={() => setBulkDeleteTargets((data?.items ?? []).filter((d) => selectedIds.includes(d.id)))}
          >
            Delete
          </Button>
          <Button size="small" onClick={exitBulkMode}>
            Deselect all
          </Button>
        </Space>
      )}

      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items ?? []}
        rowSelection={
          bulkMode
            ? {
                selectedRowKeys: selectedIds,
                onChange: (keys) => setSelectedIds(keys as string[]),
              }
            : undefined
        }
        pagination={{ current: page, pageSize, total: data?.total ?? 0, onChange: setPage, showSizeChanger: false }}
        onRow={(record) => ({ onClick: () => setPreviewId(record.id), style: { cursor: 'pointer' } })}
        columns={[
          { title: 'File', dataIndex: 'filename' },
          { title: 'Source', dataIndex: 'source', render: (source: string) => <SourceTag source={source} /> },
          {
            title: 'Experiment',
            key: 'experiments',
            render: (_, record: DatasetOut) => <ExperimentUsageCell experiments={record.experiments} />,
          },
          { title: 'Rows', dataIndex: 'n_rows' },
          { title: 'Uploaded By', dataIndex: 'uploaded_by_email' },
          { title: 'When', dataIndex: 'uploaded_at', render: (ts: string) => <RelativeTime iso={ts} /> },
          {
            title: 'Actions',
            key: 'actions',
            render: (_, record: DatasetOut) => (
              <Space size={4}>
                {record.source === 'sql' && canRefresh && <RefreshRowAction dataset={record} />}
                {canEditDataset(record) && (
                  <Tooltip title="Edit">
                    <Button
                      className="hover-actions"
                      size="small"
                      aria-label="Edit"
                      icon={<EditOutlined />}
                      onClick={(e) => {
                        e.stopPropagation()
                        setEditTarget(record)
                      }}
                    />
                  </Tooltip>
                )}
                {canEditDataset(record) && <DeleteDatasetAction dataset={record} onDeleted={invalidateAfterDelete} />}
              </Space>
            ),
          },
        ]}
      />

      <CreateDatasetModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <EditDatasetModal dataset={editTarget} open={editTarget !== null} onClose={() => setEditTarget(null)} />

      <BulkDeleteDatasetsModal
        datasets={bulkDeleteTargets}
        onCancel={() => setBulkDeleteTargets(null)}
        onDone={handleBulkDeleteDone}
      />

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
              message={`Snapshot stored in ${PRODUCT_NAME}. Deleting the source table in the external database does NOT affect this dataset. Use Refresh to re-fetch current data (columns are updated automatically).`}
            />
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
              Source: {previewedDataset.connection_name ?? 'Unknown connection'} ·{' '}
              {previewedDataset.source_schema && previewedDataset.source_table
                ? `${previewedDataset.source_schema}.${previewedDataset.source_table}`
                : 'custom query'}
            </Typography.Text>
            <Space style={{ marginBottom: 4, justifyContent: 'space-between', width: '100%' }}>
              <Space align="center">
                <Typography.Text strong>SQL</Typography.Text>
                {previewedDataset.fetched_at && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Last fetched <RelativeTime iso={previewedDataset.fetched_at} />
                    {previewedDataset.connection_name ? ` from ${previewedDataset.connection_name}` : ''}
                  </Typography.Text>
                )}
              </Space>
              {canRefresh && <RefreshDrawerButton dataset={previewedDataset} />}
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
            columns={preview.columns.map((col) => {
              // Item 1.2: a renamed column shows where it came from — the
              // mapping is {new_name: original_name}, keyed by the CURRENT
              // (displayed) name, which is exactly `col` here.
              const originalName = previewedDataset?.renamed_columns?.[col]
              return {
                title: originalName ? (
                  <Tooltip title={`renamed from ${originalName}`}>
                    <span>
                      {col} <Typography.Text type="secondary" style={{ fontSize: 11 }}>*</Typography.Text>
                    </span>
                  </Tooltip>
                ) : (
                  col
                ),
                dataIndex: col,
              }
            })}
          />
        )}
      </Drawer>
    </div>
  )
}
