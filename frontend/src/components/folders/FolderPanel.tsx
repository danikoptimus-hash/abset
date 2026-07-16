import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Dropdown, Form, Input, Modal, Typography, message } from 'antd'
import type { MenuProps } from 'antd'
import {
  FolderOutlined,
  LeftOutlined,
  MoreOutlined,
  PlusOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useAuth, hasMinRole } from '../../auth/AuthContext'
import type { components } from '../../api/schema'

type FolderOut = components['schemas']['FolderOut']

// Item 5 (folders package): a collapsible left-side list, not a top row of
// chip filters like Tags (frontend/src/pages/ExperimentsList.tsx's tag
// filter). Folders are single-membership containers a user organizes tests
// INTO — closer to a mailbox/project sidebar than a set of overlapping
// labels, so a vertical nav list with per-row counts fits the mental model
// better than chips (which read naturally for tags' AND-composable,
// multi-select filtering). Superset itself has no folder concept for
// dashboards to borrow from directly (only tags) — the closest existing
// convention in THIS app is the left-hand filter rail already used
// elsewhere for single-choice narrowing, which this mirrors. The panel
// itself collapses (chevron button) to reclaim table width, independent of
// which entry is selected.
export function FolderPanel({
  selected,
  onSelect,
}: {
  // undefined = "All tests", 'none' = Uncategorized, else a folder id.
  selected: string | undefined
  onSelect: (value: string | undefined) => void
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [collapsed, setCollapsed] = useState(false)
  const canCreate = hasMinRole(user, 'editor')

  const { data } = useQuery({
    queryKey: queryKeys.folders(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/folders')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.folders() })

  const [createOpen, setCreateOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState<FolderOut | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<FolderOut | null>(null)

  const canManage = (folder: FolderOut) =>
    user?.role === 'admin' || (!!user?.email && user.email === folder.created_by_email)

  if (collapsed) {
    return (
      <Button
        icon={<RightOutlined />}
        aria-label="Show folders"
        onClick={() => setCollapsed(false)}
        style={{ marginRight: 12 }}
      />
    )
  }

  return (
    <nav aria-label="Folders" style={{ width: 220, flexShrink: 0, marginRight: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Typography.Text strong>Folders</Typography.Text>
        <div>
          {canCreate && (
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              aria-label="New folder"
              onClick={() => setCreateOpen(true)}
            />
          )}
          <Button
            type="text"
            size="small"
            icon={<LeftOutlined />}
            aria-label="Hide folders"
            onClick={() => setCollapsed(true)}
          />
        </div>
      </div>

      <FolderRow
        label="All tests"
        count={data?.all_count ?? 0}
        active={selected === undefined}
        onClick={() => onSelect(undefined)}
      />
      {(data?.items ?? []).map((folder) => (
        <FolderRow
          key={folder.id}
          label={folder.name}
          count={folder.count}
          active={selected === folder.id}
          onClick={() => onSelect(folder.id)}
          menu={
            canManage(folder)
              ? {
                  items: [
                    { key: 'rename', label: 'Rename' },
                    { key: 'delete', label: 'Delete', danger: true },
                  ],
                  onClick: ({ key, domEvent }) => {
                    domEvent.stopPropagation()
                    if (key === 'rename') setRenameTarget(folder)
                    if (key === 'delete') setDeleteTarget(folder)
                  },
                }
              : undefined
          }
        />
      ))}
      {/* Item 5.7: not a folder, just a view over folder_id IS NULL — kept
          visually apart (after the real folders, muted styling) and only
          shown at all once something is actually uncategorized; never
          rename/delete-able (no menu prop, ever). */}
      {(data?.uncategorized_count ?? 0) > 0 && (
        <FolderRow
          label="Uncategorized"
          count={data!.uncategorized_count}
          active={selected === 'none'}
          onClick={() => onSelect('none')}
          muted
        />
      )}

      <CreateFolderModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false)
          refresh()
        }}
      />
      <RenameFolderModal
        folder={renameTarget}
        onClose={() => setRenameTarget(null)}
        onRenamed={() => {
          setRenameTarget(null)
          refresh()
        }}
      />
      <DeleteFolderModal
        folder={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDeleted={(folderId) => {
          setDeleteTarget(null)
          refresh()
          if (selected === folderId) onSelect(undefined)
        }}
      />
    </nav>
  )
}

function FolderRow({
  label,
  count,
  active,
  onClick,
  menu,
  muted,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
  menu?: MenuProps
  // Item 5.7: "Uncategorized" isn't a folder (no rename/delete, ever) — kept
  // visually apart from real user-created folders regardless of selection.
  muted?: boolean
}) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '6px 8px',
        borderRadius: 6,
        cursor: 'pointer',
        background: active ? '#F0F5F3' : undefined,
        fontWeight: active && !muted ? 600 : 400,
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden' }}>
        <FolderOutlined style={{ color: muted ? '#bfbfbf' : active ? undefined : '#8c8c8c' }} />
        <Typography.Text ellipsis type={muted ? 'secondary' : undefined} italic={muted} style={{ maxWidth: 130 }}>
          {label}
        </Typography.Text>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {count}
        </Typography.Text>
        {menu && (
          <Dropdown menu={menu} trigger={['click']}>
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined />}
              aria-label="Folder actions"
              onClick={(e) => e.stopPropagation()}
            />
          </Dropdown>
        )}
      </span>
    </div>
  )
}

function CreateFolderModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setName('')
      setError(null)
    }
  }, [open])

  const handleCreate = async () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setSaving(true)
    setError(null)
    try {
      const { error } = await apiClient.POST('/api/v1/folders', { body: { name: trimmed } })
      if (error) throw new Error(errorMessage(error))
      message.success('Folder created')
      onCreated()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create folder')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="New folder"
      open={open}
      onCancel={onClose}
      onOk={handleCreate}
      okText="Create"
      confirmLoading={saving}
      destroyOnHidden
    >
      {error && <Typography.Paragraph type="danger">{error}</Typography.Paragraph>}
      <Form layout="vertical">
        <Form.Item label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus onPressEnter={handleCreate} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function RenameFolderModal({
  folder, onClose, onRenamed,
}: {
  folder: FolderOut | null
  onClose: () => void
  onRenamed: () => void
}) {
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Keyed on folder?.id, not Modal's afterOpenChange — fires synchronously
  // with the prop change instead of racing the open animation (same
  // reasoning as Tags.tsx::RenameTagModal).
  useEffect(() => {
    if (folder) {
      setName(folder.name)
      setError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder?.id])

  const handleRename = async () => {
    if (!folder) return
    const trimmed = name.trim()
    if (!trimmed) return
    setSaving(true)
    setError(null)
    try {
      const { error } = await apiClient.PATCH('/api/v1/folders/{folder_id}', {
        params: { path: { folder_id: folder.id } },
        body: { name: trimmed },
      })
      if (error) throw new Error(errorMessage(error))
      message.success('Renamed')
      onRenamed()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to rename')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={`Rename "${folder?.name ?? ''}"`}
      open={folder !== null}
      onCancel={onClose}
      onOk={handleRename}
      okText="Rename"
      confirmLoading={saving}
      destroyOnHidden
    >
      {error && <Typography.Paragraph type="danger">{error}</Typography.Paragraph>}
      <Form layout="vertical">
        <Form.Item label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus onPressEnter={handleRename} />
        </Form.Item>
      </Form>
    </Modal>
  )
}

function DeleteFolderModal({
  folder, onClose, onDeleted,
}: {
  folder: FolderOut | null
  onClose: () => void
  onDeleted: (folderId: string) => void
}) {
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (!folder) return
    setDeleting(true)
    try {
      const { data, error } = await apiClient.DELETE('/api/v1/folders/{folder_id}', {
        params: { path: { folder_id: folder.id } },
      })
      if (error) throw new Error(errorMessage(error))
      message.success(
        data.affected_experiments > 0
          ? `Folder deleted — ${data.affected_experiments} test${data.affected_experiments === 1 ? '' : 's'} moved to Uncategorized`
          : 'Folder deleted',
      )
      onDeleted(folder.id)
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to delete folder')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal
      title={`Delete "${folder?.name ?? ''}"?`}
      open={folder !== null}
      onCancel={onClose}
      onOk={handleDelete}
      okText="Delete"
      okButtonProps={{ danger: true, loading: deleting }}
      destroyOnHidden
    >
      <Typography.Paragraph>
        {folder && folder.count > 0
          ? `${folder.count} test${folder.count === 1 ? '' : 's'} in this folder will move to Uncategorized. The tests themselves are not deleted.`
          : 'This folder is empty.'}
      </Typography.Paragraph>
    </Modal>
  )
}
