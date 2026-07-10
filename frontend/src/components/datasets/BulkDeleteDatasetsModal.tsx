import { useEffect, useState } from 'react'
import { Modal, Typography, Form, Input, List, Alert, Spin } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../../api/client'
import type { components } from '../../api/schema'

type DatasetOut = components['schemas']['DatasetOut']

export interface BulkDeleteDatasetsResult {
  deleted: string[]
  skipped: { dataset_id: string; reason: string }[]
}

interface Props {
  datasets: DatasetOut[] | null
  onCancel: () => void
  onDone: (result: BulkDeleteDatasetsResult) => void
}

// Datasets follow-up (Bulk select): mirrors components/BulkDeleteModal.tsx
// (experiments) — one typed-DELETE confirmation for the whole batch, but
// ALSO fetches usage per dataset first (like the single-item delete flow,
// DeleteDatasetAction in Datasets.tsx) so "used by: ..." shows per row
// instead of the user discovering it only after confirming.
export function BulkDeleteDatasetsModal({ datasets, onCancel, onDone }: Props) {
  const [confirmText, setConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setConfirmText('')
    setError(null)
  }, [datasets])

  const ids = datasets?.map((d) => d.id) ?? []
  const { data: usageData, isFetching: usageLoading } = useQuery({
    queryKey: ['datasets-bulk-usage', ids],
    enabled: datasets !== null && ids.length > 0,
    queryFn: async () => {
      const entries = await Promise.all(
        ids.map(async (id) => {
          const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/usage', {
            params: { path: { dataset_id: id } },
          })
          if (error) throw new Error(errorMessage(error))
          return [id, data.experiments] as const
        }),
      )
      return Object.fromEntries(entries) as Record<string, string[]>
    },
  })

  const usageById = usageData ?? {}
  const usedCount = ids.filter((id) => (usageById[id] ?? []).length > 0).length

  const handleDelete = async () => {
    if (!datasets) return
    setDeleting(true)
    setError(null)
    try {
      const { data, error } = await apiClient.POST('/api/v1/datasets/bulk-delete', {
        body: { dataset_ids: ids, confirm: confirmText },
      })
      if (error) throw new Error(errorMessage(error))
      onDone(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal
      title={`Delete ${datasets?.length ?? 0} datasets?`}
      open={datasets !== null}
      onCancel={onCancel}
      onOk={handleDelete}
      okButtonProps={{ danger: true, disabled: confirmText !== 'DELETE', loading: deleting }}
      okText="Delete"
      destroyOnHidden
    >
      {error && <Typography.Paragraph type="danger">{error}</Typography.Paragraph>}
      <Typography.Paragraph type="danger">
        This will permanently delete {datasets?.length ?? 0} datasets. This action cannot be undone.
        {usedCount > 0 && (
          <>
            {' '}
            {usedCount} of them {usedCount === 1 ? 'is' : 'are'} used by experiments — their existing analysis
            results are unaffected, but the data source will show as deleted.
          </>
        )}
      </Typography.Paragraph>
      {usageLoading ? (
        <Spin size="small" />
      ) : (
        <List
          size="small"
          bordered
          dataSource={datasets ?? []}
          renderItem={(ds) => {
            const usedBy = usageById[ds.id] ?? []
            return (
              <List.Item>
                <div>
                  <div>{ds.filename}</div>
                  {usedBy.length > 0 && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      used by: {usedBy.join(', ')}
                    </Typography.Text>
                  )}
                </div>
              </List.Item>
            )
          }}
          style={{ maxHeight: 240, overflow: 'auto', marginBottom: 16 }}
        />
      )}
      {usedCount > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`${usedCount} dataset${usedCount === 1 ? '' : 's'} in use — deleting anyway is allowed but not reversible.`}
        />
      )}
      <Form layout="vertical">
        <Form.Item label='Type "DELETE" to confirm'>
          <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoFocus />
        </Form.Item>
      </Form>
    </Modal>
  )
}
