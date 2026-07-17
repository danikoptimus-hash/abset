import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Input, Select, Button, Tag, Space, message, Tooltip, Modal, Typography } from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckSquareOutlined,
  CloseOutlined,
  FolderOutlined,
  DownloadOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { useAuth, hasMinRole } from '../auth/AuthContext'
import { DeleteExperimentModal } from '../components/DeleteExperimentModal'
import { ExperimentPropertiesModal } from '../components/ExperimentPropertiesModal'
import { BulkDeleteModal } from '../components/BulkDeleteModal'
import type { BulkDeleteResult } from '../components/BulkDeleteModal'
import { UserAvatarGroup } from '../components/UserAvatar'
import { RelativeTime } from '../components/RelativeTime'
import { TagList } from '../components/TagBadge'
import type { TagLike } from '../components/TagBadge'
import { useDebouncedValue } from '../hooks/useDebouncedValue'
import { FolderPanel } from '../components/folders/FolderPanel'
import { MoveToFolderModal } from '../components/folders/MoveToFolderModal'
import { ExportExperimentModal } from '../components/ExportExperimentModal'
import { ImportExperimentModal } from '../components/ImportExperimentModal'

const STATUS_COLORS: Record<string, string> = {
  designed: 'default',
  running: 'success',
  completed: 'blue',
  archived: 'default',
}

function StatusBadge({ status }: { status: string }) {
  return <Tag color={STATUS_COLORS[status] ?? 'default'}>{status}</Tag>
}

function PublicationBadge({ status }: { status: string }) {
  return <Tag color={status === 'published' ? 'success' : 'default'}>{status === 'published' ? 'published' : 'draft'}</Tag>
}

export function ExperimentsListPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // Initial tag/folder filter can arrive via URL (?tag=<id>, ?folder=<id>)
  // — how a tag badge / folder click on a different route hands off "filter
  // the list by this" (UX package, Tags §3.5; item 5 for folder).
  const [searchParams] = useSearchParams()
  const [q, setQ] = useState('')
  const debouncedQ = useDebouncedValue(q, 300)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [tagIds, setTagIds] = useState<string[]>(() => searchParams.getAll('tag'))
  const [folderFilter, setFolderFilter] = useState<string | undefined>(() => searchParams.get('folder') ?? undefined)
  const [page, setPage] = useState(1)
  const pageSize = 20

  // Reset to page 1 whenever a filter narrows the result set — matches the
  // same rule Datasets.tsx follows for its own live search.
  useEffect(() => {
    setPage(1)
  }, [debouncedQ, status, tagIds, folderFilter])

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.experiments({ q: debouncedQ, status, tagIds, folderFilter, page }),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments', {
        params: {
          query: {
            q: debouncedQ || undefined, status, tag: tagIds.length > 0 ? tagIds : undefined,
            folder: folderFilter, page, page_size: pageSize,
          },
        },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  // Tag filter (UX package, Tags §3.5) — typeahead options, AND logic when
  // more than one is selected (enforced server-side, GET /experiments?tag=).
  const [tagFilterSearch, setTagFilterSearch] = useState('')
  const { data: tagFilterOptions } = useQuery({
    queryKey: queryKeys.tagsTypeahead(tagFilterSearch),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tags', {
        params: { query: { q: tagFilterSearch || undefined } },
      })
      if (error) throw new Error(errorMessage(error))
      return data.items
    },
  })

  // Click a tag badge anywhere in the list -> filter to just that tag.
  const filterByTag = (tag: TagLike) => {
    setTagIds([tag.id])
    setPage(1)
  }

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [editTarget, setEditTarget] = useState<string | null>(null)
  const [moveTarget, setMoveTarget] = useState<string[] | null>(null)
  const [exportTarget, setExportTarget] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const canCreate = hasMinRole(user, 'editor')
  // Экспорт — Editor+ на любой ВИДИМЫЙ тест, владения не требует (пакет
  // export/import): намеренно НЕ record.can_edit, в отличие от остальных
  // действий строки — экспорт это чтение, а прочитать этот тест пользователь
  // и так может, раз видит его в списке.
  const canExport = hasMinRole(user, 'editor')

  // Bulk select (UX package, list п.E) — Superset-style: a toggle reveals a
  // checkbox column, selecting rows shows an action bar above the table.
  const [bulkMode, setBulkMode] = useState(false)
  const [selectedNames, setSelectedNames] = useState<string[]>([])
  const [bulkDeleteTarget, setBulkDeleteTarget] = useState<string[] | null>(null)

  const refreshList = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
    // Item 5 (folders package) — a move changes per-folder counts shown in
    // the FolderPanel, not just the table rows.
    queryClient.invalidateQueries({ queryKey: queryKeys.folders() })
  }

  const exitBulkMode = () => {
    setBulkMode(false)
    setSelectedNames([])
  }

  const handleBulkDeleteDone = (result: BulkDeleteResult) => {
    setBulkDeleteTarget(null)
    exitBulkMode()
    refreshList()
    if (result.skipped.length === 0) {
      message.success(`Deleted ${result.deleted.length} experiment${result.deleted.length === 1 ? '' : 's'}`)
    } else {
      Modal.info({
        title: 'Bulk delete finished',
        content: (
          <div>
            <p>
              Deleted {result.deleted.length}, skipped {result.skipped.length} (no permission):{' '}
              {result.skipped.map((s) => s.name).join(', ')}
            </p>
          </div>
        ),
      })
    }
  }

  // Extendable on purpose (UX package, list п.E.6) — bulk archive/export
  // can join this array later without restructuring the action bar.
  const bulkActions = [
    {
      key: 'move',
      label: 'Move to folder',
      ariaLabel: 'Move selected to folder',
      icon: <FolderOutlined />,
      onClick: () => setMoveTarget(selectedNames),
    },
    {
      key: 'delete',
      label: 'Delete',
      ariaLabel: 'Delete selected',
      danger: true,
      icon: <DeleteOutlined />,
      onClick: () => setBulkDeleteTarget(selectedNames),
    },
  ]

  return (
    <div style={{ display: 'flex' }}>
      <FolderPanel selected={folderFilter} onSelect={setFolderFilter} />
      <div style={{ flex: 1, minWidth: 0 }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Input
            allowClear
            placeholder="Search by name or tag..."
            style={{ width: 260 }}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Select
            placeholder="Status"
            allowClear
            style={{ width: 160 }}
            options={[
              { value: 'designed', label: 'designed' },
              { value: 'running', label: 'running' },
              { value: 'completed', label: 'completed' },
              { value: 'archived', label: 'archived' },
            ]}
            onChange={setStatus}
          />
          <Select
            mode="multiple"
            aria-label="Tags filter"
            placeholder="Tags"
            allowClear
            style={{ minWidth: 160, maxWidth: 320 }}
            value={tagIds}
            onSearch={setTagFilterSearch}
            filterOption={false}
            options={(tagFilterOptions ?? []).map((t) => ({ value: t.id, label: t.name }))}
            onChange={setTagIds}
          />
        </Space>
        <Space>
          {/* Bulk select и Import — icon-only (пакет UI-фиксов, item 1):
              tooltip + aria-label вместо подписи (aria-label держит e2e
              getByRole по имени рабочим). Bulk select тот же именованный
              кнопок-контрол, что и на Datasets, поэтому icon-only и тут —
              иначе он остался бы подписанным рядом с icon-only Import.
              "Create A/B Test" — НЕ трогаем (не из трёх названных). */}
          {canCreate && (
            <Tooltip title={bulkMode ? 'Cancel' : 'Bulk select'}>
              <Button
                aria-label={bulkMode ? 'Cancel' : 'Bulk select'}
                icon={bulkMode ? <CloseOutlined /> : <CheckSquareOutlined />}
                onClick={() => (bulkMode ? exitBulkMode() : setBulkMode(true))}
              />
            </Tooltip>
          )}
          {canCreate && (
            <Tooltip title="Import">
              <Button
                aria-label="Import"
                icon={<UploadOutlined />}
                onClick={() => setImportOpen(true)}
              />
            </Tooltip>
          )}
          {canCreate && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/experiments/new')}>
              Create A/B Test
            </Button>
          )}
        </Space>
      </Space>

      {bulkMode && selectedNames.length > 0 && (
        <Space style={{ marginBottom: 12, padding: '8px 12px', background: '#F0F5F3', borderRadius: 6 }}>
          <span>{selectedNames.length} selected</span>
          {bulkActions.map((action) => (
            <Button
              key={action.key}
              size="small"
              danger={action.danger}
              icon={action.icon}
              onClick={action.onClick}
              aria-label={action.ariaLabel}
            >
              {action.label}
            </Button>
          ))}
          <Button size="small" onClick={exitBulkMode}>
            Deselect all
          </Button>
        </Space>
      )}

      <Table
        rowKey="name"
        loading={isLoading}
        dataSource={data?.items ?? []}
        rowSelection={
          bulkMode
            ? {
                selectedRowKeys: selectedNames,
                onChange: (keys) => setSelectedNames(keys as string[]),
              }
            : undefined
        }
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          onChange: setPage,
          showSizeChanger: false,
        }}
        columns={[
          {
            title: 'Name',
            dataIndex: 'name',
            render: (name: string) => <Link to={`/experiments/${name}`}>{name}</Link>,
          },
          {
            title: 'Owner',
            key: 'owner',
            render: (_, record) =>
              record.owner_id ? (
                <UserAvatarGroup
                  users={[
                    {
                      id: record.owner_id,
                      firstName: record.owner_first_name ?? '',
                      lastName: record.owner_last_name ?? '',
                      email: record.owner_email ?? '',
                    },
                  ]}
                />
              ) : null,
          },
          { title: 'Status', dataIndex: 'status', render: (s: string) => <StatusBadge status={s} /> },
          {
            title: 'Publication',
            dataIndex: 'publication_status',
            render: (s: string) => <PublicationBadge status={s} />,
          },
          {
            title: 'Tags',
            key: 'tags',
            render: (_, record) => <TagList tags={record.tags} onTagClick={filterByTag} />,
          },
          {
            title: 'Folder',
            key: 'folder',
            render: (_, record) =>
              record.folder_name ? (
                <Tag
                  style={{ cursor: 'pointer' }}
                  onClick={() => setFolderFilter(record.folder_id ?? undefined)}
                >
                  {record.folder_name}
                </Tag>
              ) : (
                <Typography.Text type="secondary">—</Typography.Text>
              ),
          },
          {
            title: 'Last Modified',
            key: 'updated',
            render: (_, record) => (
              <RelativeTime
                iso={record.archived_at ?? record.completed_at ?? record.started_at ?? record.created_at}
              />
            ),
          },
          {
            title: 'Actions',
            key: 'actions',
            // Два РАЗНЫХ гейта в одной колонке: Export — по роли (canExport),
            // остальное — по правам на конкретный тест (record.can_edit).
            // Поэтому колонка рендерится, если доступно хоть что-то, а не
            // "если can_edit", как было до пакета export/import.
            render: (_, record) =>
              (record.can_edit || canExport) && (
                <Space className="hover-actions">
                  {canExport && (
                    <Tooltip title="Export">
                      <Button
                        size="small"
                        aria-label="Export"
                        icon={<DownloadOutlined />}
                        onClick={() => setExportTarget(record.name)}
                      />
                    </Tooltip>
                  )}
                  {record.can_edit && (
                    <Tooltip title="Edit">
                      <Button
                        size="small"
                        aria-label="Edit"
                        icon={<EditOutlined />}
                        onClick={() => setEditTarget(record.name)}
                      />
                    </Tooltip>
                  )}
                  {record.can_edit && (
                    <Tooltip title="Move to folder">
                      <Button
                        size="small"
                        aria-label="Move to folder"
                        icon={<FolderOutlined />}
                        onClick={() => setMoveTarget([record.name])}
                      />
                    </Tooltip>
                  )}
                  {record.can_edit && (
                    <Tooltip title="Delete">
                      <Button
                        danger
                        size="small"
                        aria-label="Delete"
                        icon={<DeleteOutlined />}
                        onClick={() => setDeleteTarget(record.name)}
                      />
                    </Tooltip>
                  )}
                </Space>
              ),
          },
        ]}
      />

      <DeleteExperimentModal
        name={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onDeleted={() => {
          message.success(`Experiment "${deleteTarget}" deleted`)
          setDeleteTarget(null)
          refreshList()
        }}
      />

      <ExperimentPropertiesModal
        name={editTarget}
        onCancel={() => setEditTarget(null)}
        onSaved={() => {
          message.success('Saved')
          setEditTarget(null)
          refreshList()
        }}
      />

      <BulkDeleteModal
        names={bulkDeleteTarget}
        onCancel={() => setBulkDeleteTarget(null)}
        onDone={handleBulkDeleteDone}
      />

      <MoveToFolderModal
        names={moveTarget}
        onCancel={() => setMoveTarget(null)}
        onDone={() => {
          const moved = moveTarget
          setMoveTarget(null)
          if (moved && moved.length > 1) exitBulkMode()
          refreshList()
        }}
      />

      <ExportExperimentModal name={exportTarget} onCancel={() => setExportTarget(null)} />

      <ImportExperimentModal open={importOpen} onClose={() => setImportOpen(false)} />
      </div>
    </div>
  )
}
