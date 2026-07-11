import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Tag, Button, Spin, Result, message, Input, Dropdown, Tooltip, Tabs, Space, Modal } from 'antd'
import {
  EditOutlined, SaveOutlined, CloseOutlined, MoreOutlined, DeleteOutlined, SettingOutlined, ExperimentOutlined,
} from '@ant-design/icons'
import { apiClient, errorMessage } from '../../api/client'
import { DeleteExperimentModal } from '../../components/DeleteExperimentModal'
import { ExperimentPropertiesModal } from '../../components/ExperimentPropertiesModal'
import { RelativeTime } from '../../components/RelativeTime'
import { TagList } from '../../components/TagBadge'
import { PRODUCT_NAME } from '../../branding'
import { DesignSection } from './DesignSection'
import { AnalyzeSection } from './AnalyzeSection'
import { ResultsSection } from './ResultsSection'
import { HistorySection } from './HistorySection'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'
import { hypothesisFamily } from './types'

// The backend doesn't enforce a state machine (any status can be set to any
// other, abkit/jobs.py::run_update_status) — this is what the status-badge
// dropdown offers as sensible next steps (UX package, 1.2; backward
// transitions added by the 6-part package pt.8). Forward transitions
// (designed->running->completed, anything->archived) are frictionless;
// backward ones (running->designed, completed->running, any unarchive) go
// through backwardTransitionWarning's confirm modal below.
const STATUS_TRANSITIONS: Record<string, string[]> = {
  designed: ['running', 'archived'],
  running: ['completed', 'designed', 'archived'],
  completed: ['running', 'archived'],
  archived: ['designed', 'running', 'completed'],
}

// null = forward transition, no friction. Non-null = backward — the
// StatusBadge dropdown shows this as a confirm-modal body before calling
// onChange (6-part package pt.8.2).
function backwardTransitionWarning(from: string, to: string): string | null {
  if (to === 'designed' && from !== 'designed') {
    return "Returning to 'designed' implies the test has not started. Existing analyses will be KEPT; if you intend to change the design, use Redesign instead (it properly resets split and analyses)."
  }
  if (from === 'completed' && to === 'running') {
    return 'Reopening a completed test. Note: extending a test after looking at results inflates false positive rates (peeking). Proceed only if the test was closed by mistake.'
  }
  if (from === 'archived') {
    return `Unarchiving this experiment and moving it to '${to}'. Make sure this reflects its actual state — it will show up as active again wherever '${to}' experiments are surfaced.`
  }
  return null
}

function PublicationBadge({
  status, canEdit, onToggle,
}: {
  status: string
  canEdit: boolean
  onToggle: () => void
}) {
  const isPublished = status === 'published'
  const tag = (
    <Tag
      color={isPublished ? 'success' : 'default'}
      style={canEdit ? { cursor: 'pointer' } : undefined}
      onClick={canEdit ? onToggle : undefined}
    >
      {status}
    </Tag>
  )
  if (!canEdit) return tag
  return <Tooltip title={isPublished ? 'Click to unpublish' : 'Click to publish'}>{tag}</Tooltip>
}

function LastModifiedText({
  at, firstName, lastName, email,
}: {
  at: string | null
  firstName: string | null
  lastName: string | null
  email: string | null
}) {
  if (!at) return null
  const name = `${firstName ?? ''} ${lastName ?? ''}`.trim() || email || 'Unknown'
  return (
    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
      Last modified by {name} <RelativeTime iso={at} />
    </Typography.Text>
  )
}

function StatusBadge({
  status, canEdit, onChange,
}: {
  status: string
  canEdit: boolean
  onChange: (to: string) => void
}) {
  const tag = <Tag style={canEdit ? { cursor: 'pointer' } : undefined}>{status}</Tag>
  if (!canEdit) return tag
  const transitions = STATUS_TRANSITIONS[status] ?? []
  return (
    <Dropdown
      trigger={['click']}
      menu={{
        items: transitions.length
          ? transitions.map((s) => ({ key: s, label: `Move to ${s}` }))
          : [{ key: 'none', label: 'No transitions available', disabled: true }],
        onClick: ({ key }) => {
          if (!transitions.includes(key)) return
          const warning = backwardTransitionWarning(status, key)
          if (!warning) {
            onChange(key)
            return
          }
          Modal.confirm({
            title: `Move to '${key}'?`,
            content: warning,
            okText: 'Continue',
            onOk: () => onChange(key),
          })
        },
      }}
    >
      {tag}
    </Dropdown>
  )
}

const TAB_KEYS = ['design', 'analysis', 'results', 'history'] as const
type TabKey = (typeof TAB_KEYS)[number]

export function ExperimentPage() {
  const { name } = useParams<{ name: string }>()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [editing, setEditing] = useState(false)
  const [draftBlocks, setDraftBlocks] = useState<BlockDraft[]>([])
  const [draftName, setDraftName] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [propertiesTarget, setPropertiesTarget] = useState<string | null>(null)

  const rawTab = searchParams.get('tab')
  const activeTab: TabKey = TAB_KEYS.includes(rawTab as TabKey) ? (rawTab as TabKey) : 'design'
  const setActiveTab = (key: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (key === 'design') next.delete('tab')
        else next.set('tab', key)
        return next
      },
      { replace: true },
    )
  }

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

  const startRedesign = async () => {
    if (!name) return
    // Reuses the same counts the Delete confirmation modal shows (FRONTEND.md
    // §5.2) — "analyses already run against the old split" is exactly
    // results, no new endpoint needed.
    const { data: summary, error } = await apiClient.GET('/api/v1/experiments/{name}/deletion-summary', {
      params: { path: { name } },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    Modal.confirm({
      title: 'Redesign this experiment?',
      content: (
        <Typography.Paragraph>
          Redesign will discard the current split (assignments), MDE table, and split checks. The experiment
          config will be loaded into the wizard for editing. Analyses already run against the old split will be
          deleted ({summary.results} found).
        </Typography.Paragraph>
      ),
      okText: 'Continue',
      onOk: () => navigate(`/experiments/${name}/redesign`),
    })
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24, flexWrap: 'wrap' }}>
        {editing ? (
          <Input value={draftName} onChange={(e) => setDraftName(e.target.value)} style={{ width: 260 }} />
        ) : (
          <Typography.Title level={3} style={{ margin: 0 }}>
            {data.name}
          </Typography.Title>
        )}
        <PublicationBadge
          status={data.publication_status}
          canEdit={canEdit && !editing}
          onToggle={handleTogglePublication}
        />
        <StatusBadge status={data.status} canEdit={canEdit && !editing} onChange={handleStatusChange} />
        {data.config.split_source === 'external' && (
          <Tooltip title={`The split happened in an outside system (e.g. Firebase A/B Testing) — ${PRODUCT_NAME} is used for analysis only`}>
            <Tag color="purple">External split</Tag>
          </Tooltip>
        )}
        <LastModifiedText
          at={data.last_modified_at}
          firstName={data.last_modified_by_first_name}
          lastName={data.last_modified_by_last_name}
          email={data.last_modified_by_email}
        />

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {canEdit && !editing && (
            <Dropdown
              menu={{
                items: [
                  { key: 'properties', icon: <SettingOutlined />, label: 'Edit Properties', onClick: () => setPropertiesTarget(name) },
                  // Redesigning a running (or later) experiment is a
                  // methodological disaster (5-part package pt.3.4) — the
                  // item is absent, not just disabled, once past 'designed'.
                  // Also absent for external-split experiments (item 12) —
                  // Redesign's wizard flow assumes a dataset, which
                  // external experiments never have.
                  ...(data.status === 'designed' && data.config.split_source !== 'external'
                    ? [{ key: 'redesign', icon: <ExperimentOutlined />, label: 'Redesign', onClick: startRedesign }]
                    : []),
                  { key: 'delete', icon: <DeleteOutlined />, label: 'Delete', danger: true, onClick: () => setDeleteTarget(name) },
                ],
              }}
              trigger={['click']}
            >
              <Button icon={<MoreOutlined />} aria-label="More actions" />
            </Dropdown>
          )}
          {canEdit && !editing && (
            <Button type="primary" icon={<EditOutlined />} onClick={startEditing}>
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
        </div>
      </div>

      {data.tags.length > 0 && (
        <Space size={4} style={{ marginBottom: 24 }}>
          <TagList
            tags={data.tags}
            maxVisible={data.tags.length}
            onTagClick={(tag) => navigate(`/experiments?tag=${tag.id}`)}
          />
        </Space>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'design',
            label: 'Design',
            children: (
              <div>
                {hypothesisBlock && (
                  <MarkdownBlockView
                    block={hypothesisBlock}
                    editing={editing}
                    onChange={(patch) => updateDraftBlock(hypothesisBlock.id, patch)}
                  />
                )}
                <DesignSection name={name} config={data.config} availableReports={data.available_reports} />
              </div>
            ),
          },
          {
            key: 'analysis',
            label: 'Analysis',
            children: (
              <AnalyzeSection
                experimentName={name}
                hasAssignments
                family={hypothesisFamily(data.config)}
                splitSource={String(data.config.split_source ?? 'abkit')}
                declaredGroups={Object.keys((data.config.groups as Record<string, number>) ?? {})}
              />
            ),
          },
          {
            key: 'results',
            label: 'Results',
            children: (
              <ResultsSection
                experimentName={name}
                familySize={hypothesisFamily(data.config).familySize}
                blocks={otherBlocks}
                editing={editing}
                onChangeBlock={updateDraftBlock}
                onAddBlock={() =>
                  setDraftBlocks((prev) => [
                    ...prev,
                    { id: null, kind: 'custom', title: '', content_md: '', position: prev.length + 1 },
                  ])
                }
                onRemoveBlock={(id) => setDraftBlocks((prev) => prev.filter((x) => x.id !== id))}
              />
            ),
          },
          {
            key: 'history',
            label: 'History',
            children: <HistorySection name={name} />,
          },
        ]}
      />

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
