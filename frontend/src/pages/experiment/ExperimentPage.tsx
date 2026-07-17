import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Tag, Button, Spin, Result, message, Input, Dropdown, Tooltip, Tabs, Space, Modal } from 'antd'
import {
  EditOutlined, SaveOutlined, CloseOutlined, MoreOutlined, DeleteOutlined, SettingOutlined, ExperimentOutlined,
  DownloadOutlined, ShareAltOutlined,
} from '@ant-design/icons'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useAuth, hasMinRole } from '../../auth/AuthContext'
import { DeleteExperimentModal } from '../../components/DeleteExperimentModal'
import { ExperimentPropertiesModal } from '../../components/ExperimentPropertiesModal'
import { ExportExperimentModal } from '../../components/ExportExperimentModal'
import { buildExperimentPermalink, copyText, shareToastMessage } from '../../lib/share'
import { LifecycleDates } from '../../components/LifecycleDates'
import { RelativeTime } from '../../components/RelativeTime'
import { TagList } from '../../components/TagBadge'
import { PRODUCT_NAME } from '../../branding'
import { useUnsavedGuard } from '../../hooks/useUnsavedGuard'
import { DesignSection } from './DesignSection'
import { AnalyzeSection } from './AnalyzeSection'
import { ResultsSection } from './ResultsSection'
import { HistorySection } from './HistorySection'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'
import { hypothesisFamily, analyzeMetricsFromConfig } from './types'

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
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  const [editing, setEditing] = useState(false)
  const [draftBlocks, setDraftBlocks] = useState<BlockDraft[]>([])
  const [draftName, setDraftName] = useState('')
  // Item 1: snapshot taken the moment Edit mode starts — compared against
  // the live draft to decide whether there's actually something to lose.
  // Not the query cache directly: that can change under us (refetch) while
  // editing, which must NOT retroactively mark an untouched draft "dirty".
  const [pristineName, setPristineName] = useState('')
  const [pristineBlocks, setPristineBlocks] = useState<BlockDraft[]>([])
  const [saving, setSaving] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [propertiesTarget, setPropertiesTarget] = useState<string | null>(null)
  const [exportTarget, setExportTarget] = useState<string | null>(null)

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
    queryKey: queryKeys.experiment(name!),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}', { params: { path: { name: name! } } })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: blocks } = useQuery({
    queryKey: queryKeys.experimentBlocks(name!),
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
    const snapshot = (blocks ?? []).map((b) => ({ id: b.id, kind: b.kind, title: b.title, content_md: b.content_md, position: b.position }))
    setDraftBlocks(snapshot)
    setPristineBlocks(snapshot)
    setDraftName(data?.name ?? '')
    setPristineName(data?.name ?? '')
    setEditing(true)
  }

  const discardEditing = () => {
    setEditing(false)
  }

  const isDirty =
    editing && (draftName !== pristineName || JSON.stringify(draftBlocks) !== JSON.stringify(pristineBlocks))

  // UX contract, part A: beforeunload + route-navigation (nav links, browser
  // back, programmatic navigate) blocking both come from the shared hook now
  // — this used to be a hand-rolled beforeunload effect with no route
  // protection at all (frontend/src/hooks/useUnsavedGuard.ts).
  const { guard } = useUnsavedGuard(isDirty)

  // Item 1.1: internal navigation away from a dirty edit (tabs, tag-badge
  // links, the Discard button itself) all funnel through here instead of
  // running `action` directly — the hook's guard() is a no-op passthrough
  // when there's nothing to lose, so callers don't need their own dirty
  // check. Preserves the pre-refactor behavior of only resetting `editing`
  // on the CONFIRMED-discard path, not the passthrough one (the passthrough
  // path only runs when isDirty is already false, i.e. nothing to reset).
  const confirmDiscardIfDirty = (action: () => void) => {
    if (!isDirty) {
      action()
      return
    }
    guard(() => {
      setEditing(false)
      action()
    })
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
      queryClient.invalidateQueries({ queryKey: queryKeys.experiment(finalName) })
      queryClient.invalidateQueries({ queryKey: queryKeys.experimentBlocks(finalName) })
      queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
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
    queryClient.invalidateQueries({ queryKey: queryKeys.experiment(name) })
    // UX contract, part B: the list's own Status column reads a separately
    // cached ['experiments', ...] query — this used to invalidate only the
    // single-experiment detail query, leaving the list showing a stale
    // status badge if it's still mounted (e.g. this page was reached
    // without unmounting the list, or the user has both open).
    queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
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
    // UX contract, part B: optimistic update — this is a one-click toggle
    // (PublicationBadge is both indicator and control), so it should flip
    // instantly rather than wait a round trip; rolled back on failure.
    const previous = queryClient.getQueryData(queryKeys.experiment(name))
    queryClient.setQueryData(queryKeys.experiment(name), (old: typeof data | undefined) =>
      old ? { ...old, publication_status: to } : old,
    )
    const { error } = await apiClient.PATCH('/api/v1/experiments/{name}', {
      params: { path: { name } },
      body: { publication_status: to },
    })
    if (error) {
      queryClient.setQueryData(queryKeys.experiment(name), previous)
      message.error(errorMessage(error))
      return
    }
    queryClient.invalidateQueries({ queryKey: queryKeys.experiment(name) })
    queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
  }

  if (isLoading) return <Spin size="large" />
  if (error || !data || !name) return <Result status="404" title="Experiment not found" />

  const canEdit = data.can_edit
  // Экспорт не требует прав на правку (пакет export/import): Editor+ может
  // выгрузить любой тест, который видит, а видимость этой страницы уже
  // проверена сервером — до сюда невидимый тест не доезжает (404 выше).
  const canExport = hasMinRole(user, 'editor')

  const handleShare = async () => {
    const link = buildExperimentPermalink(window.location.origin, data.id)
    if (await copyText(link)) {
      // Про draft предупреждаем в момент копирования, а не потом: у ссылки на
      // черновик получатель почти наверняка увидит "not found", и узнать об
      // этом лучше до того, как ее отправили.
      message.success(shareToastMessage(data.publication_status))
      return
    }
    message.error('Could not copy the link — copy it from the address bar instead')
  }

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
          {!editing && (
            <Dropdown
              menu={{
                items: [
                  // Share доступен ЛЮБОЙ роли, которая видит тест (viewer
                  // включительно — поделиться значит прочитать), а видимость
                  // уже проверена сервером: невидимый тест сюда не доезжает
                  // (404 выше). Поэтому "⋯" больше не гейтится вообще — до
                  // этого пакета он показывался только при canEdit||canExport
                  // и viewer не увидел бы Share никогда.
                  { key: 'share', icon: <ShareAltOutlined />, label: 'Share', onClick: handleShare },
                  // Export — по роли (Editor+ на видимый тест), а не по
                  // canEdit: экспорт это чтение (пакет export/import).
                  ...(canExport
                    ? [{ key: 'export', icon: <DownloadOutlined />, label: 'Export', onClick: () => setExportTarget(name) }]
                    : []),
                  ...(canEdit
                    ? [{ key: 'properties', icon: <SettingOutlined />, label: 'Edit Properties', onClick: () => setPropertiesTarget(name) }]
                    : []),
                  // Redesigning a running (or later) experiment is a
                  // methodological disaster (5-part package pt.3.4) — the
                  // item is absent, not just disabled, once past 'designed'.
                  // Also absent for external-split experiments (item 12) —
                  // Redesign's wizard flow assumes a dataset, which
                  // external experiments never have.
                  ...(canEdit && data.status === 'designed' && data.config.split_source !== 'external'
                    ? [{ key: 'redesign', icon: <ExperimentOutlined />, label: 'Redesign', onClick: startRedesign }]
                    : []),
                  ...(canEdit
                    ? [{ key: 'delete', icon: <DeleteOutlined />, label: 'Delete', danger: true, onClick: () => setDeleteTarget(name) }]
                    : []),
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
              <Button icon={<CloseOutlined />} onClick={() => confirmDiscardIfDirty(discardEditing)} disabled={saving}>
                Discard
              </Button>
            </>
          )}
        </div>
      </div>

      <div style={{ marginTop: -16, marginBottom: 24 }}>
        <LifecycleDates createdAt={data.created_at} startedAt={data.started_at} completedAt={data.completed_at} />
      </div>

      {data.tags.length > 0 && (
        <Space size={4} style={{ marginBottom: 24 }}>
          <TagList
            tags={data.tags}
            maxVisible={data.tags.length}
            onTagClick={(tag) => confirmDiscardIfDirty(() => navigate(`/experiments?tag=${tag.id}`))}
          />
        </Space>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={(key) => confirmDiscardIfDirty(() => setActiveTab(key))}
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
                unitCol={String(data.config.unit_col ?? '')}
                alpha={Number(data.config.alpha ?? 0.05)}
                metrics={analyzeMetricsFromConfig(data.config)}
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
                createdAt={data.created_at}
                startedAt={data.started_at}
                completedAt={data.completed_at}
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
                alpha={Number(data.config.alpha ?? 0.05)}
                metrics={analyzeMetricsFromConfig(data.config)}
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
          queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
          queryClient.invalidateQueries({ queryKey: queryKeys.experiment(name) })
          if (newName !== name) {
            window.location.href = `/experiments/${newName}`
          }
        }}
      />

      <ExportExperimentModal name={exportTarget} onCancel={() => setExportTarget(null)} />
    </div>
  )
}
