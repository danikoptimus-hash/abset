import { useRef, useState } from 'react'
import { Modal, Tabs, Upload, Button, Alert, Select, Input, Progress, Typography, Spin } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage, toFormData } from '../../api/client'
import { useJobPolling } from '../../api/useJobPolling'
import { SchemaTableCascade } from '../../components/datasets/SchemaTableCascade'
import { QueryResultPreview } from '../../components/datasets/QueryResultPreview'
import { buildSelectAllSql } from '../../components/datasets/parseSchemaTableFromSql'

const { Dragger } = Upload
const { TextArea } = Input

function UploadTab({ onDone }: { onDone: () => void }) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const uploadProps: UploadProps = {
    accept: '.csv,.parquet',
    multiple: false,
    showUploadList: false,
    disabled: uploading,
    customRequest: async (options) => {
      const file = options.file as File
      setUploading(true)
      setError(null)
      try {
        const { data, error } = await apiClient.POST('/api/v1/datasets', {
          // kind: server defaults to 'pre_design' (DB3 dataset-centric
          // model — real kind is assigned per-use, not at creation) —
          // passed explicitly only to satisfy the generated request type.
          body: toFormData({ kind: 'pre_design', file }) as unknown as { kind: string; file: string },
        })
        if (error) throw new Error(errorMessage(error))
        options.onSuccess?.(data)
        onDone()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to upload file')
        options.onError?.(e as Error)
      } finally {
        setUploading(false)
      }
    },
  }

  return (
    <div>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}
      <Dragger {...uploadProps}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p>Drag a CSV or parquet file here, or click to choose one</p>
      </Dragger>
      {uploading && (
        <div style={{ marginTop: 16 }}>
          <Spin /> Uploading...
        </div>
      )}
    </div>
  )
}

function FromSqlTab({ onDone }: { onDone: () => void }) {
  const [connectionId, setConnectionId] = useState<string | undefined>(undefined)
  const [schema, setSchema] = useState<string | undefined>(undefined)
  const [table, setTable] = useState<string | undefined>(undefined)
  const [sql, setSql] = useState('')
  const [name, setName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const { phase, stage, error, poll, reset } = useJobPolling<{ dataset_id: string; n_rows: number; truncated: boolean }>()

  const { data: connections, isFetching: connectionsLoading } = useQuery({
    queryKey: ['db-connections-for-sql-dataset'],
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/admin/db-connections')
      return data ?? []
    },
  })

  // Schema/Table cascade (UX package, Datasets §1) — optional, purely a
  // convenience for filling in the SQL box. "SQL is the source of truth" —
  // selecting a table only overwrites `sql` when it's still exactly what a
  // previous selection generated (or empty); once the user edits it by
  // hand, further schema/table changes silently stop clobbering their edits
  // (unlike Edit, which confirms instead — see EditDatasetModal).
  const lastAutoFilledSql = useRef<string | null>(null)

  const handleTableChange = (value: string | undefined) => {
    setTable(value)
    if (value && schema) {
      const generated = buildSelectAllSql(schema, value)
      if (sql.trim() === '' || sql === lastAutoFilledSql.current) {
        setSql(generated)
        lastAutoFilledSql.current = generated
      }
    }
  }

  const runCreate = async () => {
    if (!connectionId || !sql.trim() || !name.trim()) return
    reset()
    // Datasets follow-up (persist source schema/table): only send the
    // cascade pick if the SQL box still exactly matches what it generates —
    // a hand-edited query has no business claiming to come from that table.
    const sourceMatches = !!schema && !!table && sql.trim() === buildSelectAllSql(schema, table)
    const { data, error } = await apiClient.POST('/api/v1/datasets/from-sql', {
      // kind: server defaults to 'pre_design' (DB3 dataset-centric model).
      body: {
        connection_id: connectionId, sql, name, kind: 'pre_design',
        source_schema: sourceMatches ? schema : undefined,
        source_table: sourceMatches ? table : undefined,
      },
    })
    if (error) {
      setCreateError(errorMessage(error))
      return
    }
    const result = await poll(data.job_id)
    if (result) onDone()
  }

  const running = phase === 'running'
  const canCreate = !!connectionId && !!sql.trim() && !!name.trim() && !running

  return (
    <div>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Connection
      </Typography.Text>
      <Select
        style={{ width: '100%', marginBottom: 12 }}
        aria-label="from-sql-connection-select"
        placeholder={connectionsLoading ? 'Loading...' : 'Select a database connection'}
        loading={connectionsLoading}
        value={connectionId}
        onChange={setConnectionId}
        options={(connections ?? []).map((c) => ({ value: c.id, label: `${c.display_name} (${c.engine})` }))}
        notFoundContent={
          connectionsLoading ? undefined : 'No database connections configured — ask an admin to add one in Settings'
        }
      />

      <SchemaTableCascade
        connectionId={connectionId}
        schema={schema}
        table={table}
        onSchemaChange={setSchema}
        onTableChange={handleTableChange}
      />

      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        SQL (SELECT only)
      </Typography.Text>
      <TextArea
        rows={6}
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        placeholder="SELECT user_id, revenue FROM events WHERE ..."
        style={{ marginBottom: 12, fontFamily: 'monospace' }}
      />

      <QueryResultPreview connectionId={connectionId} sql={sql} />

      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Dataset name
      </Typography.Text>
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. active_users_30d" style={{ marginBottom: 12 }} />

      {running && (
        <div style={{ marginBottom: 12 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Starting...'}</Typography.Text>
        </div>
      )}
      {createError && <Alert type="error" showIcon message={createError} style={{ marginBottom: 12 }} closable onClose={() => setCreateError(null)} />}
      {phase === 'failed' && error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}

      <Button type="primary" onClick={runCreate} disabled={!canCreate} loading={running}>
        {running ? 'Creating...' : 'Create dataset'}
      </Button>
    </div>
  )
}

export function CreateDatasetModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()

  const handleDone = () => {
    queryClient.invalidateQueries({ queryKey: ['datasets'] })
    queryClient.invalidateQueries({ queryKey: ['datasets-for-select'] })
    onClose()
  }

  return (
    <Modal title="New dataset" open={open} onCancel={onClose} footer={null} width={640} destroyOnHidden>
      <Tabs
        items={[
          { key: 'upload', label: 'Upload file', children: <UploadTab onDone={handleDone} /> },
          { key: 'sql', label: 'From SQL', children: <FromSqlTab onDone={handleDone} /> },
        ]}
      />
    </Modal>
  )
}
