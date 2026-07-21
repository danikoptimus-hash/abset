import { useEffect, useRef, useState } from 'react'
import { Modal, Typography, Input, Select, Button, Alert, Progress, Space, Collapse, Tabs } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useJobPolling } from '../../api/useJobPolling'
import { useUnsavedGuard } from '../../hooks/useUnsavedGuard'
import type { components } from '../../api/schema'
import { SchemaTableCascade } from '../../components/datasets/SchemaTableCascade'
import { QueryResultPreview } from '../../components/datasets/QueryResultPreview'
import { DatasetSnapshotPreview } from '../../components/datasets/DatasetSnapshotPreview'
import { buildSelectAllSql, parseSchemaTableFromSql } from '../../components/datasets/parseSchemaTableFromSql'
import { StopClickPropagation } from '../../components/StopClickPropagation'
import {
  ColumnTypeEditor,
  numericColumnsFromPreview,
  defaultCategoricalFromPreview,
} from '../../components/datasets/ColumnTypeEditor'

type DatasetOut = components['schemas']['DatasetOut']

const { TextArea } = Input

function sameStringSet(a: string[], b: string[] | null | undefined): boolean {
  const bs = new Set(b ?? [])
  return a.length === bs.size && a.every((x) => bs.has(x))
}

// UX package, Datasets §2.3: opens the same shape of form as creation, with
// fields pre-filled. source=upload/demo only has `name` editable (the file
// itself can't change); source=sql also allows connection/SQL/schema-table
// picker, which re-fetches (same mechanism as Refresh) on save — with a
// warning naming how many experiments reference this dataset, since that
// re-fetch replaces the stored snapshot those experiments' "current data"
// points at.
export function EditDatasetModal({
  dataset, open, onClose,
}: {
  dataset: DatasetOut | null
  open: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [connectionId, setConnectionId] = useState<string | undefined>(undefined)
  const [schema, setSchema] = useState<string | undefined>(undefined)
  const [table, setTable] = useState<string | undefined>(undefined)
  const [sql, setSql] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Part 2: categorical column flags. null = untouched (fall back to the
  // stored list, or a preview heuristic for datasets predating the feature).
  const [categorical, setCategorical] = useState<string[] | null>(null)
  const { phase, stage, error: jobError, poll, reset } = useJobPolling<{ n_rows: number; truncated: boolean }>()

  const isSql = dataset?.source === 'sql'

  // Preview rows drive the "Column types" editor (numeric-vs-text detection +
  // the default flags for datasets with none stored yet).
  const { data: preview } = useQuery({
    queryKey: queryKeys.datasetColumnsPreview(dataset?.id),
    enabled: !!dataset && open,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
        params: { path: { dataset_id: dataset!.id }, query: { rows: 200 } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })
  const previewColumns = preview?.columns ?? dataset?.columns ?? []
  const previewRows = (preview?.rows ?? []) as Record<string, unknown>[]
  const numericCols = numericColumnsFromPreview(previewColumns, previewRows)
  const effectiveCategorical =
    categorical ?? dataset?.categorical_columns ?? (previewRows.length ? defaultCategoricalFromPreview(previewColumns, previewRows) : [])

  // Unlike Create, the editor here always opens holding the *saved* query —
  // which counts as a manual edit from the start — so picking a table
  // confirms before replacing it instead of silently filling an empty box.
  const lastAutoFilledSql = useRef<string | null>(null)

  useEffect(() => {
    if (dataset) {
      setName(dataset.filename)
      setConnectionId(dataset.connection_id ?? undefined)
      setSql(dataset.sql_text ?? '')
      // Datasets follow-up (persist source schema/table): the stored
      // columns are authoritative — only fall back to re-parsing sql_text
      // for older rows / hand-written queries that never had them set.
      if (dataset.source === 'sql' && (dataset.source_schema || dataset.source_table)) {
        setSchema(dataset.source_schema ?? undefined)
        setTable(dataset.source_table ?? undefined)
      } else {
        const parsed = dataset.source === 'sql' ? parseSchemaTableFromSql(dataset.sql_text ?? '') : {}
        setSchema(parsed.schema)
        setTable(parsed.table)
      }
      lastAutoFilledSql.current = null
      setCategorical(null)
      setError(null)
      reset()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset?.id])

  const { data: connections } = useQuery({
    queryKey: queryKeys.dbConnectionsForSqlDataset(),
    enabled: !!isSql && open,
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/admin/db-connections')
      return data ?? []
    },
  })

  const { data: usage } = useQuery({
    queryKey: queryKeys.datasetUsage(dataset?.id),
    enabled: !!isSql && !!dataset && open,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/usage', {
        params: { path: { dataset_id: dataset!.id } },
      })
      if (error) throw new Error(errorMessage(error))
      return data.experiments
    },
  })

  // Computed before the `!dataset` early return below (rules of hooks —
  // useUnsavedGuard must be called on every render, not skipped once
  // `dataset` goes null) with an explicit `!!dataset` guard so isDirty
  // correctly reads false once the modal has no dataset to edit, rather
  // than throwing or reading stale true.
  const sqlChanged =
    !!dataset && isSql && (connectionId !== (dataset.connection_id ?? undefined) || sql !== (dataset.sql_text ?? ''))
  const categoricalChanged =
    !!dataset && categorical !== null && !sameStringSet(categorical, dataset.categorical_columns)
  const isDirty = !!dataset && open && (name.trim() !== dataset.filename || sqlChanged || categoricalChanged)
  const { guard } = useUnsavedGuard(isDirty)

  if (!dataset) return null

  const handleTableChange = (value: string | undefined) => {
    setTable(value)
    if (!value || !schema) return
    const generated = buildSelectAllSql(schema, value)
    if (sql.trim() === '' || sql === lastAutoFilledSql.current) {
      setSql(generated)
      lastAutoFilledSql.current = generated
      return
    }
    Modal.confirm({
      title: 'Replace SQL query?',
      content: 'This will discard your manual edits in the SQL box and replace it with a SELECT * against the chosen table.',
      okText: 'Replace',
      onOk: () => {
        setSql(generated)
        lastAutoFilledSql.current = generated
      },
    })
  }

  const doSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const body: {
        name?: string
        connection_id?: string
        sql_text?: string
        source_schema?: string
        source_table?: string
        categorical_columns?: string[]
      } = {}
      if (name.trim() && name.trim() !== dataset.filename) body.name = name.trim()
      // Send the resolved categorical list whenever it differs from what's
      // stored (also backfills datasets that had none).
      if (!sameStringSet(effectiveCategorical, dataset.categorical_columns)) {
        body.categorical_columns = effectiveCategorical
      }
      if (sqlChanged) {
        body.connection_id = connectionId
        body.sql_text = sql
        // Datasets follow-up: only carry the schema/table selection along
        // if the SQL box still exactly matches what it generates — a
        // hand-edited query clears source_schema/source_table instead of
        // keeping a stale (and now false) pointer at the old table.
        const sourceMatches = !!schema && !!table && sql.trim() === buildSelectAllSql(schema, table)
        if (sourceMatches) {
          body.source_schema = schema
          body.source_table = table
        }
      }
      const { data, error } = await apiClient.PATCH('/api/v1/datasets/{dataset_id}', {
        params: { path: { dataset_id: dataset.id } },
        body,
      })
      if (error) throw new Error(errorMessage(error))
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
      if (data.job_id) {
        const result = await poll(data.job_id)
        queryClient.invalidateQueries({ queryKey: queryKeys.datasetPreview(dataset.id) })
        if (!result) return // job failed — error already shown via useJobPolling's `error`
      }
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const handleSave = () => {
    if (sqlChanged) {
      const n = usage?.length ?? 0
      Modal.confirm({
        title: 'Save changes?',
        content:
          `Saving will re-fetch data with the new query and replace the stored snapshot.` +
          (n > 0 ? ` ${n} experiment${n === 1 ? '' : 's'} reference${n === 1 ? 's' : ''} this dataset.` : ''),
        okText: 'Save & refresh',
        onOk: doSave,
      })
    } else {
      doSave()
    }
  }

  const running = phase === 'running'

  // Item 1.3: name change or (source=sql) a changed connection/query counts
  // as unsaved input — closing via the X/mask/Esc or the Cancel button all
  // go through AntD's onCancel, so guarding that one prop covers all three.
  const guardedClose = () => guard(onClose)

  return (
    <Modal
      title="Edit dataset"
      open={open}
      onCancel={guardedClose}
      footer={null}
      width={isSql ? 640 : 480}
      destroyOnHidden
    >
      <StopClickPropagation>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        Name
      </Typography.Text>
      <Input value={name} onChange={(e) => setName(e.target.value)} style={{ marginBottom: 12 }} />

      {isSql ? (
        <>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
            Connection
          </Typography.Text>
          <Select
            style={{ width: '100%', marginBottom: 12 }}
            value={connectionId}
            onChange={setConnectionId}
            options={(connections ?? []).map((c) => ({ value: c.id, label: `${c.display_name} (${c.engine})` }))}
          />

          <SchemaTableCascade
            connectionId={connectionId}
            schema={schema}
            table={table}
            onSchemaChange={setSchema}
            onTableChange={handleTableChange}
          />
          {!schema && !table && (
            <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
              Custom query — table picker not applicable.
            </Typography.Text>
          )}

          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
            SQL (SELECT only)
          </Typography.Text>
          <TextArea
            rows={6}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            style={{ marginBottom: 12, fontFamily: 'monospace' }}
          />
        </>
      ) : (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          To change data, upload a new dataset.
        </Typography.Paragraph>
      )}

      <Collapse
        style={{ marginBottom: 12 }}
        items={[
          {
            key: 'columns',
            label: 'Column types (categorical vs binned)',
            children: (
              <ColumnTypeEditor
                columns={previewColumns}
                numericColumns={numericCols}
                value={effectiveCategorical}
                onChange={setCategorical}
                disabled={running}
              />
            ),
          },
        ]}
      />

      <Collapse
        defaultActiveKey={['preview']}
        style={{ marginBottom: 12 }}
        items={[
          {
            key: 'preview',
            label: 'Data preview',
            children: isSql ? (
              <Tabs
                size="small"
                items={[
                  {
                    key: 'snapshot',
                    label: 'Stored snapshot',
                    children: (
                      <DatasetSnapshotPreview datasetId={dataset.id} nRows={dataset.n_rows} fetchedAt={dataset.fetched_at ?? null} />
                    ),
                  },
                  {
                    key: 'query',
                    label: 'Query result',
                    children: <QueryResultPreview connectionId={connectionId} sql={sql} buttonLabel="Preview query result" />,
                  },
                ]}
              />
            ) : (
              <DatasetSnapshotPreview datasetId={dataset.id} nRows={dataset.n_rows} fetchedAt={dataset.fetched_at ?? null} />
            ),
          },
        ]}
      />

      {running && (
        <div style={{ marginBottom: 12 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Refreshing...'}</Typography.Text>
        </div>
      )}
      {(error || jobError) && <Alert type="error" showIcon message={error ?? jobError} style={{ marginBottom: 12 }} />}

      <Space>
        <Button type="primary" onClick={handleSave} loading={saving || running} disabled={!name.trim()}>
          Save
        </Button>
        <Button onClick={guardedClose}>Cancel</Button>
      </Space>
      </StopClickPropagation>
    </Modal>
  )
}
