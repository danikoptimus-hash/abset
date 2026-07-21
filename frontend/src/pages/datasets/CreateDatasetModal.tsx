import { useEffect, useRef, useState } from 'react'
import { Modal, Tabs, Upload, Button, Alert, Select, Input, Progress, Typography, Spin, Table } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { UploadProps } from 'antd'
import { apiClient, errorMessage, toFormData } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useJobPolling } from '../../api/useJobPolling'
import { useUnsavedGuard } from '../../hooks/useUnsavedGuard'
import { SchemaTableCascade } from '../../components/datasets/SchemaTableCascade'
import { QueryResultPreview } from '../../components/datasets/QueryResultPreview'
import { buildSelectAllSql } from '../../components/datasets/parseSchemaTableFromSql'
import { StopClickPropagation } from '../../components/StopClickPropagation'
import { ColumnTypeEditor, numericColumnsFromPreview } from '../../components/datasets/ColumnTypeEditor'

const { Dragger } = Upload
const { TextArea } = Input

function sameStringSet(a: string[], b: string[] | null | undefined): boolean {
  const bs = new Set(b ?? [])
  return a.length === bs.size && a.every((x) => bs.has(x))
}

// Item 1.3: mirrors the backend's forbidden-character check
// (abkit/jobs.py::run_update_dataset) — checked client-side too for
// immediate feedback, but the backend is the actual authority.
const FORBIDDEN_COLUMN_CHARS = /[,"'\\\n\r\t]/

function humanDtype(dtype: string | undefined): string {
  if (!dtype) return 'unknown'
  if (dtype.startsWith('int') || dtype.startsWith('float')) return 'number'
  if (dtype === 'bool') return 'boolean'
  if (dtype.startsWith('datetime')) return 'date/time'
  return 'text'
}

interface UploadedForRename {
  id: string
  originalFilename: string
  columns: string[]
  dtypes: Record<string, string> | null
  previewRows: Record<string, unknown>[]
  categorical: string[]
}

// Item 1.1 (upload confirmation step): shown right after a file finishes
// uploading (the dataset row already exists at this point, with default
// name/column names) — lets the user rename the dataset and/or individual
// columns before treating the upload as "done". Renames are applied via the
// same PATCH /datasets/{id} used by Edit (abkit/jobs.py::run_update_dataset,
// column_renames param) — skipping this step (closing the modal, or just
// not changing anything) leaves the dataset exactly as uploaded, which is a
// valid outcome, not an error state.
function RenameStep({ uploaded, onDone }: { uploaded: UploadedForRename; onDone: () => void }) {
  const [name, setName] = useState(uploaded.originalFilename)
  const [columnNames, setColumnNames] = useState<Record<string, string>>(
    Object.fromEntries(uploaded.columns.map((c) => [c, c])),
  )
  // Part 2: categorical flags, keyed by the ORIGINAL column name; mapped to the
  // (possibly renamed) final names on save.
  const [categorical, setCategorical] = useState<string[]>(uploaded.categorical)
  const numericCols = numericColumnsFromPreview(uploaded.columns, uploaded.previewRows)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const trimmedNames = uploaded.columns.map((c) => columnNames[c]?.trim() ?? '')
  const emptyName = trimmedNames.some((n) => !n)
  const forbiddenChar = trimmedNames.some((n) => FORBIDDEN_COLUMN_CHARS.test(n))
  const duplicateName = new Set(trimmedNames).size !== trimmedNames.length
  const nameEmpty = !name.trim()
  const canSave = !emptyName && !forbiddenChar && !duplicateName && !nameEmpty && !saving

  const runSave = async () => {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      const columnRenames = Object.fromEntries(
        uploaded.columns
          .filter((c) => columnNames[c].trim() !== c)
          .map((c) => [c, columnNames[c].trim()]),
      )
      const nameChanged = name.trim() !== uploaded.originalFilename
      // Map categorical flags onto the final (post-rename) column names.
      const finalCategorical = categorical.map((c) => columnNames[c]?.trim() ?? c)
      const categoricalChanged = !sameStringSet(finalCategorical, uploaded.categorical)
      if (nameChanged || Object.keys(columnRenames).length > 0 || categoricalChanged) {
        const { error: patchError } = await apiClient.PATCH('/api/v1/datasets/{dataset_id}', {
          params: { path: { dataset_id: uploaded.id } },
          body: {
            name: nameChanged ? name.trim() : undefined,
            column_renames: Object.keys(columnRenames).length > 0 ? columnRenames : undefined,
            categorical_columns: categoricalChanged ? finalCategorical : undefined,
          },
        })
        if (patchError) throw new Error(errorMessage(patchError))
      }
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ marginTop: -4 }}>
        Confirm the dataset and column names before finishing — or just click Finish to keep them as uploaded.
      </Typography.Paragraph>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Dataset name
      </Typography.Text>
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        status={nameEmpty ? 'error' : undefined}
        style={{ marginBottom: 16 }}
        aria-label="rename-dataset-name"
      />

      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Columns ({uploaded.columns.length})
      </Typography.Text>
      <Table
        size="small"
        rowKey={(c) => c}
        dataSource={uploaded.columns}
        pagination={false}
        scroll={{ y: 320 }}
        style={{ marginBottom: 12 }}
        columns={[
          {
            title: 'Column name',
            key: 'name',
            render: (_, col: string) => {
              const current = columnNames[col]
              const trimmed = current.trim()
              const isForbidden = FORBIDDEN_COLUMN_CHARS.test(trimmed)
              const isDupe = trimmed !== '' && trimmedNames.filter((n) => n === trimmed).length > 1
              return (
                <Input
                  size="small"
                  value={current}
                  status={!trimmed || isForbidden || isDupe ? 'error' : undefined}
                  onChange={(e) => setColumnNames((prev) => ({ ...prev, [col]: e.target.value }))}
                  aria-label={`rename-column-${col}`}
                />
              )
            },
          },
          {
            title: 'Detected type',
            key: 'dtype',
            width: 110,
            render: (_, col: string) => humanDtype(uploaded.dtypes?.[col]),
          },
          {
            title: 'First values',
            key: 'preview',
            render: (_, col: string) => (
              <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
                {uploaded.previewRows.map((r) => String(r[col] ?? '')).join(', ')}
              </Typography.Text>
            ),
          },
        ]}
      />

      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Column types
      </Typography.Text>
      <div style={{ marginBottom: 12 }}>
        <ColumnTypeEditor
          columns={uploaded.columns}
          numericColumns={numericCols}
          value={categorical}
          onChange={setCategorical}
        />
      </div>

      {emptyName && <Alert type="error" showIcon message="Column names cannot be empty" style={{ marginBottom: 8 }} />}
      {forbiddenChar && (
        <Alert
          type="error" showIcon
          message={'A column name contains a character that isn\'t allowed (, " \' \\ or a newline/tab)'}
          style={{ marginBottom: 8 }}
        />
      )}
      {duplicateName && <Alert type="error" showIcon message="Column names must be unique" style={{ marginBottom: 8 }} />}
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} closable onClose={() => setError(null)} />}

      <Button type="primary" onClick={runSave} disabled={!canSave} loading={saving}>
        Finish
      </Button>
    </div>
  )
}

function UploadTab({ onDone }: { onDone: () => void }) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Item 1.1: set once the file is actually persisted (dataset row exists,
  // with default name/columns) — switches this tab to the rename-confirm
  // step. Not reset on error, so a failed preview fetch still shows SOME
  // preview state (empty rows) rather than losing the just-created dataset.
  const [uploaded, setUploaded] = useState<UploadedForRename | null>(null)

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
        // Item 1.1: a few sample rows for the "First values" column — the
        // upload response itself only carries dtypes, not row data.
        const { data: previewData } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
          params: { path: { dataset_id: data.id }, query: { rows: 20 } },
        })
        setUploaded({
          id: data.id, originalFilename: data.filename, columns: data.columns,
          dtypes: data.dtypes ?? null, previewRows: previewData?.rows ?? [],
          // Part 2: the backend-computed heuristic default; the user can adjust
          // it in the rename/confirm step below.
          categorical: data.categorical_columns ?? [],
        })
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to upload file')
        options.onError?.(e as Error)
      } finally {
        setUploading(false)
      }
    },
  }

  if (uploaded) {
    return <RenameStep uploaded={uploaded} onDone={onDone} />
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

function FromSqlTab({ onDone, onDirtyChange }: { onDone: () => void; onDirtyChange: (dirty: boolean) => void }) {
  const [connectionId, setConnectionId] = useState<string | undefined>(undefined)
  const [schema, setSchema] = useState<string | undefined>(undefined)
  const [table, setTable] = useState<string | undefined>(undefined)
  const [sql, setSql] = useState('')
  const [name, setName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  const { phase, stage, error, poll, reset } = useJobPolling<{ dataset_id: string; n_rows: number; truncated: boolean }>()

  // UX contract, part A: this tab's state is local (not lifted), but the
  // parent modal's close guard needs to know about it — reported up via
  // callback rather than lifting connectionId/schema/table/sql/name
  // themselves, which would ripple through every handler in this file for
  // no benefit beyond the single boolean the parent actually needs. AntD
  // Tabs keeps inactive panes mounted (no destroyInactiveTabPane), so this
  // stays accurate even after switching to the Upload tab without closing.
  useEffect(() => {
    onDirtyChange(!!connectionId || !!sql.trim() || !!name.trim())
  }, [connectionId, sql, name, onDirtyChange])

  const { data: connections, isFetching: connectionsLoading } = useQuery({
    queryKey: queryKeys.dbConnectionsForSqlDataset(),
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
  const [sqlTabDirty, setSqlTabDirty] = useState(false)
  // open gate: this component unmounts on close (destroyOnHidden, and the
  // parent renders it conditionally too — see Datasets.tsx), so in practice
  // sqlTabDirty always resets to false on the next open; kept explicit
  // anyway to match the same defensive pattern used in the other guarded
  // modals (EditDatasetModal/ExperimentPropertiesModal).
  const isDirty = open && sqlTabDirty
  const { guard } = useUnsavedGuard(isDirty)
  const guardedClose = () => guard(onClose)

  const handleDone = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
    queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
    onClose()
  }

  return (
    <Modal title="New dataset" open={open} onCancel={guardedClose} footer={null} width={640} destroyOnHidden>
      <StopClickPropagation>
        <Tabs
          items={[
            { key: 'upload', label: 'Upload file', children: <UploadTab onDone={handleDone} /> },
            { key: 'sql', label: 'From SQL', children: <FromSqlTab onDone={handleDone} onDirtyChange={setSqlTabDirty} /> },
          ]}
        />
      </StopClickPropagation>
    </Modal>
  )
}
