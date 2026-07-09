import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Tag, Select, Button, Space, Spin, Result, message, Input, Dropdown } from 'antd'
import { EditOutlined, SaveOutlined, CloseOutlined, DownloadOutlined, MoreOutlined, DeleteOutlined, SettingOutlined } from '@ant-design/icons'
import { apiClient, errorMessage } from '../../api/client'
import { DeleteExperimentModal } from '../../components/DeleteExperimentModal'
import { ExperimentPropertiesModal } from '../../components/ExperimentPropertiesModal'
import { DesignSection } from './DesignSection'
import { AnalyzeSection } from './AnalyzeSection'
import { HistorySection } from './HistorySection'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'

const STATUS_OPTIONS = ['designed', 'running', 'completed', 'archived']

export function ExperimentPage() {
  const { name } = useParams<{ name: string }>()
  const queryClient = useQueryClient()

  const [editing, setEditing] = useState(false)
  const [draftBlocks, setDraftBlocks] = useState<BlockDraft[]>([])
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [propertiesTarget, setPropertiesTarget] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['experiment', name],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}', { params: { path: { name: name! } } })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: blocks } = useQuery({
    queryKey: ['experiment-blocks', name],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/blocks', { params: { path: { name: name! } } })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  useEffect(() => {
    if (data) setDraftName(data.name)
  }, [data])

  const startEditing = () => {
    setDraftBlocks((blocks ?? []).map((b) => ({ id: b.id, kind: b.kind, title: b.title, content_md: b.content_md, position: b.position })))
    setDraftName(data?.name ?? '')
    setEditing(true)
  }

  const discardEditing = () => {
    setEditing(false)
  }

  const saveEditing = async () => {
    if (!name) return
    setSaving(true)
    try {
      if (draftName !== name) {
        const { error } = await apiClient.PATCH('/api/v1/experiments/{name}', {
          params: { path: { name } },
          body: { name: draftName },
        })
        if (error) throw new Error(errorMessage(error))
      }
      const { error: blocksError } = await apiClient.PUT('/api/v1/experiments/{name}/blocks', {
        params: { path: { name: draftName !== name ? draftName : name } },
        body: draftBlocks.map((b) => ({
          id: b.id ?? undefined,
          kind: b.kind,
          title: b.title,
          content_md: b.content_md,
          position: b.position,
        })),
      })
      if (blocksError) throw new Error(errorMessage(blocksError))
      message.success('Saved')
      setEditing(false)
      const finalName = draftName !== name ? draftName : name
      queryClient.invalidateQueries({ queryKey: ['experiment', finalName] })
      queryClient.invalidateQueries({ queryKey: ['experiment-blocks', finalName] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      if (finalName !== name) {
        window.location.href = `/experiments/${finalName}`
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const handleStatusChange = async (to: string) => {
    if (!name) return
    const { error } = await apiClient.POST('/api/v1/experiments/{name}/status', {
      params: { path: { name } },
      body: { to },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    queryClient.invalidateQueries({ queryKey: ['experiment', name] })
  }

  const handleTogglePublication = async () => {
    if (!name || !data) return
    const to = data.publication_status === 'published' ? 'draft' : 'published'
    const { error } = await apiClient.PATCH('/api/v1/experiments/{name}', {
      params: { path: { name } },
      body: { publication_status: to },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    queryClient.invalidateQueries({ queryKey: ['experiment', name] })
  }

  if (isLoading) return <Spin size="large" />
  if (error || !data || !name) return <Result status="404" title="Experiment not found" />

  const canEdit = data.can_edit

  const displayBlocks = editing ? draftBlocks : (blocks ?? []).map((b) => ({ ...b }))
  const hypothesisBlock = displayBlocks.find((b) => b.kind === 'hypothesis')
  const otherBlocks = displayBlocks.filter((b) => b.kind !== 'hypothesis')

  const updateDraftBlock = (id: string | null, patch: Partial<BlockDraft>) => {
    setDraftBlocks((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)))
  }

  return (
    <div>
      <Space align="center" style={{ marginBottom: 4 }}>
        {editing ? (
          <Input value={draftName} onChange={(e) => setDraftName(e.target.value)} style={{ width: 300 }} />
        ) : (
          <Typography.Title level={3} style={{ margin: 0 }}>
            {data.name}
          </Typography.Title>
        )}
        <Tag color={data.publication_status === 'published' ? 'success' : 'default'}>{data.publication_status}</Tag>
        <Tag>{data.status}</Tag>
        {canEdit && !editing && (
          <Dropdown
            menu={{
              items: [
                { key: 'properties', icon: <SettingOutlined />, label: 'Edit Properties', onClick: () => setPropertiesTarget(name) },
                { key: 'delete', icon: <DeleteOutlined />, label: 'Delete', danger: true, onClick: () => setDeleteTarget(name) },
              ],
            }}
            trigger={['click']}
          >
            <Button icon={<MoreOutlined />} aria-label="More actions" />
          </Dropdown>
        )}
      </Space>
      <Typography.Paragraph type="secondary">Owner: {data.owner_email}</Typography.Paragraph>

      <Space style={{ marginBottom: 24 }} wrap>
        {canEdit && !editing && (
          <Button icon={<EditOutlined />} onClick={startEditing}>
            Edit
          </Button>
        )}
        {editing && (
          <>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={saveEditing}>
              Save
            </Button>
            <Button icon={<CloseOutlined />} onClick={discardEditing} disabled={saving}>
              Discard
            </Button>
          </>
        )}
        {canEdit && !editing && (
          <Button onClick={handleTogglePublication}>
            {data.publication_status === 'published' ? 'Unpublish' : 'Publish'}
          </Button>
        )}
        {canEdit && !editing && (
          <Select value={data.status} style={{ width: 160 }} onChange={handleStatusChange} options={STATUS_OPTIONS.map((s) => ({ value: s, label: s }))} />
        )}
        <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${name}/samples.zip`}>
          Download Samples
        </Button>
      </Space>

      {hypothesisBlock && (
        <MarkdownBlockView
          block={hypothesisBlock}
          editing={editing}
          onChange={(patch) => updateDraftBlock(hypothesisBlock.id, patch)}
        />
      )}

      <DesignSection name={name} config={data.config} availableReports={data.available_reports} />

      <div style={{ marginTop: 32 }}>
        <AnalyzeSection experimentName={name} hasAssignments />
      </div>

      <Typography.Title level={4} style={{ marginTop: 32 }}>
        Conclusions and Decision
      </Typography.Title>
      {otherBlocks.map((b) => (
        <MarkdownBlockView
          key={b.id ?? `new-${b.position}`}
          block={b}
          editing={editing}
          onChange={(patch) => updateDraftBlock(b.id, patch)}
          onRemove={
            b.kind === 'custom'
              ? () => setDraftBlocks((prev) => prev.filter((x) => x.id !== b.id))
              : undefined
          }
        />
      ))}
      {editing && (
        <Button
          onClick={() =>
            setDraftBlocks((prev) => [
              ...prev,
              { id: null, kind: 'custom', title: '', content_md: '', position: prev.length + 1 },
            ])
          }
          style={{ marginBottom: 24 }}
        >
          + Add Block
        </Button>
      )}

      <HistorySection name={name} />

      <DeleteExperimentModal
        name={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onDeleted={() => {
          message.success(`Experiment "${deleteTarget}" deleted`)
          window.location.href = '/experiments'
        }}
      />

      <ExperimentPropertiesModal
        name={propertiesTarget}
        onCancel={() => setPropertiesTarget(null)}
        onSaved={(newName) => {
          setPropertiesTarget(null)
          queryClient.invalidateQueries({ queryKey: ['experiments'] })
          queryClient.invalidateQueries({ queryKey: ['experiment', name] })
          if (newName !== name) {
            window.location.href = `/experiments/${newName}`
          }
        }}
      />
    </div>
  )
}
