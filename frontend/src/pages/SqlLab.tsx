import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Collapse, Input, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { PlayCircleOutlined, HistoryOutlined, DeleteOutlined, DatabaseOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { SchemaTableCascade } from '../components/datasets/SchemaTableCascade'
import { buildSelectAllSql } from '../components/datasets/parseSchemaTableFromSql'
import { CreateDatasetModal } from './datasets/CreateDatasetModal'
import { RelativeTime } from '../components/RelativeTime'
import { loadSqlLabState, saveSqlLabState } from './sqlLabState'

type Row = Record<string, unknown>

interface RunResult {
  columns: string[]
  rows: Row[]
  n_rows: number
  elapsed_ms: number
  truncated: boolean
  row_limit: number
  warnings: string[]
}

// SQL Lab — интерактивный редактор запросов (Superset-подобный: подключение
// слева, редактор сверху, результат снизу, история рядом).
//
// Обычный Input.TextArea, а НЕ редактор с подсветкой: библиотека
// react-simple-code-editor уже пробовалась в этом проекте для SQL-поля
// создания датасета и стабильно роняла приложение (React error #130, белый
// экран) после нескольких переходов между страницами — см. CLAUDE.md,
// "Database Connections". Стабильность важнее подсветки; решение
// распространяется и сюда.
export function SqlLabPage() {
  const queryClient = useQueryClient()
  // Ленивый инициализатор useState, а не useRef(...).current: sessionStorage
  // читается ровно один раз при монтировании, и при этом ничего не читается
  // из ref'а во время рендера (react-hooks/refs).
  const [restored] = useState(loadSqlLabState)

  const [connectionId, setConnectionId] = useState<string | undefined>(restored.connectionId)
  const [schema, setSchema] = useState<string | undefined>(restored.schema)
  const [table, setTable] = useState<string | undefined>(restored.table)
  const [sql, setSql] = useState(restored.sql)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  // Последний текст, который каскад подставил сам — чтобы не затирать
  // запрос, который пользователь уже правил руками (тот же контракт, что в
  // CreateDatasetModal).
  const [lastAutoSql, setLastAutoSql] = useState<string | null>(null)

  // Состояние вкладки переживает переход по приложению (ТЗ п.1): страница
  // размонтируется при уходе, поэтому храним снаружи компонента — в
  // sessionStorage, а не в React-состоянии родителя, чтобы пережить и
  // случайный F5 в рамках той же сессии.
  useEffect(() => {
    saveSqlLabState({ connectionId, schema, table, sql })
  }, [connectionId, schema, table, sql])

  const { data: connections } = useQuery({
    queryKey: queryKeys.dbConnectionsForSelect(),
    queryFn: async () => {
      // Тот же эндпоинт, что использует создание датасета из SQL: GET на нём
      // разрешён editor+, несмотря на префикс /admin (управление
      // подключениями — admin-only, чтение списка — нет, см.
      // backend/routers/db_connections.py).
      const { data, error } = await apiClient.GET('/api/v1/admin/db-connections')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: history } = useQuery({
    queryKey: queryKeys.sqlLabHistory(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/sql-lab/history')
      if (error) throw new Error(errorMessage(error))
      return data.items
    },
  })

  const run = async () => {
    if (!connectionId || !sql.trim()) return
    setRunning(true)
    setError(null)
    try {
      const { data, error } = await apiClient.POST('/api/v1/sql-lab/run', {
        body: { connection_id: connectionId, sql },
      })
      if (error) throw new Error(errorMessage(error, 'Query failed'))
      setResult(data as RunResult)
    } catch (e) {
      setResult(null)
      setError(e instanceof Error ? e.message : 'Query failed')
    } finally {
      setRunning(false)
      // История пополняется и успехом, и ошибкой — обновляем в обоих случаях.
      queryClient.invalidateQueries({ queryKey: queryKeys.sqlLabHistory() })
    }
  }

  // Ctrl/Cmd+Enter — то же сочетание, что в SQL Lab Superset и в большинстве
  // консолей БД; ожидание у людей уже сформировано.
  const onEditorKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      void run()
    }
  }

  // Обработчики — ОТДЕЛЬНЫЕ на схему и на таблицу, ровно как в
  // CreateDatasetModal. Один общий обработчик, восстанавливающий вторую
  // половину состояния из замыкания, здесь и был багом: выбор схемы
  // приводит к двум вызовам подряд — onSchemaChange(value) и
  // onTableChange(undefined), — и второй записывал в схему УСТАРЕВШЕЕ (еще
  // undefined) значение из того же рендера, отменяя первый. Схема
  // сбрасывалась в тот же тик, запрос таблиц не уходил вовсе, и пикер
  // таблиц оставался пустым навсегда.
  const handleTableChange = (next: string | undefined) => {
    setTable(next)
    if (!next || !schema) return
    const generated = buildSelectAllSql(schema, next)
    // Подставляем молча, только если поле пустое или содержит ровно то, что
    // мы же и сгенерировали в прошлый раз: затирать написанный руками запрос
    // выбором таблицы в дереве — верный способ потерять работу.
    if (!sql.trim() || sql === lastAutoSql) {
      setSql(generated)
      setLastAutoSql(generated)
    }
  }

  const restoreFromHistory = (item: { sql_text: string; connection_id: string | null }) => {
    setSql(item.sql_text)
    if (item.connection_id) setConnectionId(item.connection_id)
    setLastAutoSql(null)
    message.success('Query restored to the editor')
  }

  const clearHistory = async () => {
    await apiClient.DELETE('/api/v1/sql-lab/history')
    queryClient.invalidateQueries({ queryKey: queryKeys.sqlLabHistory() })
  }

  const resultColumns = (result?.columns ?? []).map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
    // Сортировка на клиенте: результат уже ограничен 1000 строками, тянуть
    // ради сортировки повторный запрос в БД незачем.
    sorter: (a: Row, b: Row) => {
      const av = a[col]
      const bv = b[col]
      if (av === null || av === undefined) return -1
      if (bv === null || bv === undefined) return 1
      if (typeof av === 'number' && typeof bv === 'number') return av - bv
      return String(av).localeCompare(String(bv))
    },
    render: (v: unknown) => (v === null || v === undefined ? <Typography.Text type="secondary">NULL</Typography.Text> : String(v)),
  }))

  return (
    <div>
      <Typography.Title level={4}>SQL Lab</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 760 }}>
        Explore a connected database and turn a query into a dataset. Interactive runs are
        capped at a preview size — the full result is materialized when you create a dataset.
      </Typography.Paragraph>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <Card size="small" style={{ width: 320, flexShrink: 0 }} title={<><DatabaseOutlined /> Source</>}>
          <Select
            aria-label="sql-lab-connection"
            placeholder="Database connection"
            style={{ width: '100%', marginBottom: 12 }}
            value={connectionId}
            onChange={(id) => {
              setConnectionId(id)
              setSchema(undefined)
              setTable(undefined)
            }}
            options={(connections ?? []).map((c) => ({
              value: c.id,
              label: `${c.display_name} (${c.engine})`,
            }))}
          />
          {connectionId && (
            <SchemaTableCascade
              connectionId={connectionId}
              schema={schema}
              table={table}
              onSchemaChange={setSchema}
              onTableChange={handleTableChange}
              autoSelectDefaultSchema
            />
          )}
        </Card>

        <div style={{ flex: 1, minWidth: 420 }}>
          <Input.TextArea
            aria-label="sql-lab-editor"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={onEditorKeyDown}
            rows={10}
            placeholder="SELECT user_id, revenue FROM events WHERE ..."
            style={{ fontFamily: 'monospace', fontSize: 13 }}
          />
          <Space style={{ marginTop: 12 }} wrap>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={running}
              disabled={!connectionId || !sql.trim()}
              onClick={run}
            >
              Run
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Ctrl+Enter
            </Typography.Text>
            <Button
              disabled={!connectionId || !sql.trim()}
              onClick={() => setCreateOpen(true)}
            >
              Create dataset from query
            </Button>
          </Space>

          {error && (
            <Alert type="error" showIcon message={error} style={{ marginTop: 16 }} closable onClose={() => setError(null)} />
          )}

          {result && (
            <div style={{ marginTop: 16 }}>
              <Space wrap style={{ marginBottom: 8 }}>
                <Tag color="blue">{result.n_rows} rows</Tag>
                <Tag>{result.elapsed_ms} ms</Tag>
                {result.truncated && <Tag color="warning">preview limited to {result.row_limit} rows</Tag>}
              </Space>
              {result.warnings.map((w, i) => (
                <Alert key={i} type="warning" showIcon message={w} style={{ marginBottom: 8 }} />
              ))}
              <Table
                size="small"
                bordered
                dataSource={result.rows.map((r, i) => ({ ...r, __key: i }))}
                rowKey="__key"
                columns={resultColumns}
                pagination={{ pageSize: 50, showSizeChanger: false }}
                scroll={{ x: true }}
              />
            </div>
          )}
        </div>
      </div>

      <Collapse
        ghost
        style={{ marginTop: 24 }}
        items={[
          {
            key: 'history',
            label: (
              <span>
                <HistoryOutlined /> Query history ({history?.length ?? 0})
              </span>
            ),
            children: (
              <>
                <Button
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={clearHistory}
                  disabled={!history?.length}
                  style={{ marginBottom: 12 }}
                >
                  Clear history
                </Button>
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={history ?? []}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  onRow={(item) => ({ onClick: () => restoreFromHistory(item), style: { cursor: 'pointer' } })}
                  columns={[
                    {
                      title: 'When',
                      dataIndex: 'started_at',
                      width: 140,
                      render: (ts: string) => <RelativeTime iso={ts} />,
                    },
                    { title: 'Connection', dataIndex: 'connection_name', width: 160 },
                    {
                      title: 'Query',
                      dataIndex: 'sql_text',
                      ellipsis: true,
                      render: (t: string) => <code style={{ fontSize: 12 }}>{t}</code>,
                    },
                    {
                      title: 'Result',
                      key: 'result',
                      width: 160,
                      render: (_, item) =>
                        item.error ? (
                          <Tag color="error">failed</Tag>
                        ) : (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {item.n_rows} rows · {item.duration_ms} ms
                          </Typography.Text>
                        ),
                    },
                  ]}
                />
              </>
            ),
          },
        ]}
      />

      {/* Передача в существующий флоу создания датасета: полная
          материализация происходит там, БЕЗ интерактивного лимита — то есть
          датасет получает весь результат, а не первую тысячу строк. */}
      <CreateDatasetModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        initialSql={sql}
        initialConnectionId={connectionId}
      />
    </div>
  )
}
