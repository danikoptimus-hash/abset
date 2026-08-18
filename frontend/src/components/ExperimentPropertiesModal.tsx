import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Spin, Alert, DatePicker } from 'antd'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { StopClickPropagation } from './StopClickPropagation'

interface Props {
  name: string | null
  onCancel: () => void
  onSaved: (newName: string) => void
}

interface FormValues {
  name: string
  owner_ids: string[]
  editor_ids: string[]
  visible_roles: string[] | null
  tags: string[]
  // Items B1/B2 — lifecycle dates, held as Dayjs (what AntD's DatePicker
  // speaks) and serialized at save time: started_at as a full ISO instant
  // (it IS one), planned_end_date as a bare calendar day.
  started_at: Dayjs | null
  planned_end_date: Dayjs | null
}

const ROLE_OPTIONS = [
  { value: 'viewer', label: 'viewer' },
  { value: 'editor', label: 'editor' },
  { value: 'admin', label: 'admin' },
]

// Edit Properties modal (UX package, section 3) — like Superset's dashboard
// Properties: name, additional owners/editors, visibility restricted by
// role. Opened from the "..." menu on the experiment page and from the
// hover Edit button in the experiments list.
export function ExperimentPropertiesModal({ name, onCancel, onSaved }: Props) {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<FormValues>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: properties, isLoading } = useQuery({
    queryKey: queryKeys.experimentProperties(name),
    enabled: name !== null,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/properties', {
        params: { path: { name: name! } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const { data: users } = useQuery({
    queryKey: queryKeys.usersPicker(),
    enabled: name !== null,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/users')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  // Tags typeahead (UX package, Tags §3.3) — options refresh as the user
  // types; mode="tags" lets them also just type a brand-new name and hit
  // Enter, which becomes a plain string in the form value either way. What
  // "is this new or existing" resolves to is decided at Save time (below),
  // not here — this Select only ever deals in tag NAMES.
  const currentNameValue = Form.useWatch('name', form)
  const currentOwnerIds = Form.useWatch('owner_ids', form)
  const currentEditorIds = Form.useWatch('editor_ids', form)
  const currentVisibleRoles = Form.useWatch('visible_roles', form)
  const currentTags = Form.useWatch('tags', form)
  const currentStartedAt = Form.useWatch('started_at', form)
  const currentPlannedEnd = Form.useWatch('planned_end_date', form)

  const [tagSearch, setTagSearch] = useState('')
  const { data: tagOptions } = useQuery({
    queryKey: queryKeys.tagsTypeahead(tagSearch),
    enabled: name !== null,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/tags', { params: { query: { q: tagSearch || undefined } } })
      if (error) throw new Error(errorMessage(error))
      return data.items
    },
  })

  useEffect(() => {
    if (!properties) return
    form.setFieldsValue({
      name: properties.name,
      owner_ids: properties.owners.map((u) => u.id),
      editor_ids: properties.editors.map((u) => u.id),
      visible_roles: properties.visible_roles,
      tags: properties.tags.map((t) => t.name),
      started_at: properties.started_at ? dayjs(properties.started_at) : null,
      planned_end_date: properties.planned_end_date ? dayjs(properties.planned_end_date) : null,
    })
  }, [properties, form])

  const userOptions = (users ?? [])
    .filter((u) => u.id !== properties?.owner?.id)
    .map((u) => ({
      value: u.id,
      label: `${u.first_name} ${u.last_name}`.trim() || u.email,
    }))

  const handleSave = async () => {
    if (!name) return
    const values = await form.validateFields()
    setSaving(true)
    setError(null)
    try {
      const { error } = await apiClient.PUT('/api/v1/experiments/{name}/properties', {
        params: { path: { name } },
        body: {
          name: values.name,
          owner_ids: values.owner_ids ?? [],
          editor_ids: values.editor_ids ?? [],
          visible_roles: values.visible_roles ?? null,
          // Items B1/B2. set_lifecycle_dates tells the backend to actually
          // read the two fields below — without it a null would be
          // indistinguishable from "this client doesn't know about dates",
          // and clearing a planned end date (the way auto-completion is
          // turned back off) has to stay expressible.
          set_lifecycle_dates: true,
          started_at: values.started_at ? values.started_at.toISOString() : null,
          planned_end_date: values.planned_end_date ? values.planned_end_date.format('YYYY-MM-DD') : null,
        },
      })
      if (error) throw new Error(errorMessage(error))

      // Tags are a separate PUT (backend/routers/experiments.py) — resolve
      // every name in the field to a real tag id first (POST /tags is
      // get-or-create, so this is safe to call even for names that already
      // exist), THEN send the full id list. Runs against values.name, the
      // possibly-just-renamed name, not the original `name` prop.
      const tagNames = values.tags ?? []
      const resolvedTags = await Promise.all(
        tagNames.map(async (tagName) => {
          const { data, error } = await apiClient.POST('/api/v1/tags', { body: { name: tagName } })
          if (error) throw new Error(errorMessage(error))
          return data
        }),
      )
      const { error: tagsError } = await apiClient.PUT('/api/v1/experiments/{name}/tags', {
        params: { path: { name: values.name } },
        body: { tag_ids: resolvedTags.map((t) => t.id) },
      })
      if (tagsError) throw new Error(errorMessage(tagsError))

      // UX contract, part B: this is the exact bug that motivated the
      // cache-invalidation audit — a brand-new tag typed here previously
      // never showed up in the experiments list's tag filter without a full
      // reload, because nothing ever invalidated (or updated)
      // tagsTypeahead's cache. Optimistically splice any newly-seen tags
      // into every cached typeahead result NOW (immediate, no flicker),
      // then invalidate too (covers search strings not currently cached, and
      // reconciles if the optimistic guess above turns out stale).
      queryClient.setQueriesData<{ id: string; name: string }[]>(
        { queryKey: queryKeys.tagsTypeaheadAll() },
        (old) => {
          const known = new Set((old ?? []).map((t) => t.id))
          const additions = resolvedTags.filter((t) => !known.has(t.id))
          return additions.length ? [...(old ?? []), ...additions] : old
        },
      )
      queryClient.invalidateQueries({ queryKey: queryKeys.tagsTypeaheadAll() })

      onSaved(values.name)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  // Item 1.3: closing via the X icon, mask click, Esc, or the default
  // footer's Cancel button all route through this one onCancel prop in
  // AntD's Modal — guarding it here covers all four paths at once.
  const arraysEqual = (a?: string[] | null, b?: string[] | null) => {
    const aa = [...(a ?? [])].sort()
    const bb = [...(b ?? [])].sort()
    return aa.length === bb.length && aa.every((v, i) => v === bb[i])
  }
  // name !== null gate: this component stays mounted across open/close
  // (both callers render it unconditionally, toggling only the `name`
  // prop) — Form.useForm()'s instance (and its useWatch values) outlives
  // the Modal's own destroyOnHidden unmount, so without this gate isDirty
  // could still read "true" from a just-discarded edit after the modal
  // visually closes, keeping the shared hook's route-blocker wrongly armed.
  const isDirty =
    name !== null &&
    !!properties &&
    (currentNameValue !== properties.name ||
      !arraysEqual(currentOwnerIds, properties.owners.map((u) => u.id)) ||
      !arraysEqual(currentEditorIds, properties.editors.map((u) => u.id)) ||
      !arraysEqual(currentVisibleRoles, properties.visible_roles) ||
      !arraysEqual(currentTags, properties.tags.map((t) => t.name)) ||
      (currentStartedAt?.toISOString() ?? null) !== (properties.started_at ?? null) ||
      (currentPlannedEnd?.format('YYYY-MM-DD') ?? null) !== (properties.planned_end_date ?? null))
  const { guard } = useUnsavedGuard(isDirty)
  const guardedCancel = () => guard(onCancel)

  return (
    <Modal
      title="Edit Properties"
      open={name !== null}
      onCancel={guardedCancel}
      onOk={handleSave}
      okText="Save"
      confirmLoading={saving}
      destroyOnHidden
    >
      <StopClickPropagation>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      {isLoading || !properties ? (
        <Spin size="small" />
      ) : (
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {currentNameValue && currentNameValue !== properties.name && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16, marginTop: -8 }}
              message="Renaming changes the experiment's URL — existing links and bookmarks will stop working."
            />
          )}
          <Form.Item label="Owner">
            <Input
              disabled
              value={
                properties.owner
                  ? `${properties.owner.first_name} ${properties.owner.last_name}`.trim() || properties.owner.email
                  : '—'
              }
            />
          </Form.Item>
          <Form.Item name="owner_ids" label="Additional owners">
            <Select mode="multiple" allowClear options={userOptions} placeholder="No additional owners" />
          </Form.Item>
          <Form.Item name="editor_ids" label="Editors">
            <Select mode="multiple" allowClear options={userOptions} placeholder="No additional editors" />
          </Form.Item>
          <Form.Item
            name="visible_roles"
            label="Visible to roles"
            extra="Empty = default visibility rules (draft: owners/editors/admin only; published: everyone)"
          >
            <Select mode="multiple" allowClear options={ROLE_OPTIONS} placeholder="Everyone (default)" />
          </Form.Item>
          {/* Items B1/B2: the two editable lifecycle dates. Start date is
              normally set automatically when the test is moved to "running" —
              editable here because the button is often pressed a day or two
              after the test actually started. */}
          <Form.Item
            name="started_at"
            label="Start date"
            extra="Set automatically when the test starts running; correct it here if it began on a different day"
          >
            <DatePicker showTime style={{ width: '100%' }} aria-label="Start date" />
          </Form.Item>
          <Form.Item
            name="planned_end_date"
            label="Planned end date"
            extra="A running test is completed automatically once this day has passed. Clear it to turn that off"
          >
            <DatePicker style={{ width: '100%' }} aria-label="Planned end date" />
          </Form.Item>
          <Form.Item name="tags" label="Tags" extra="Pick an existing tag or type a new name and press Enter">
            <Select
              mode="tags"
              allowClear
              aria-label="Tags"
              placeholder="No tags"
              filterOption={false}
              onSearch={setTagSearch}
              options={(tagOptions ?? []).map((t) => ({ value: t.name, label: t.name }))}
            />
          </Form.Item>
        </Form>
      )}
      </StopClickPropagation>
    </Modal>
  )
}
