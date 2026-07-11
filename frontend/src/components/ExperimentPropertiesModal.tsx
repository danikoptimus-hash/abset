import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, Spin, Alert } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../api/client'

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
  const [form] = Form.useForm<FormValues>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: properties, isLoading } = useQuery({
    queryKey: ['experiment-properties', name],
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
    queryKey: ['users-picker'],
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

  const [tagSearch, setTagSearch] = useState('')
  const { data: tagOptions } = useQuery({
    queryKey: ['tags-typeahead', tagSearch],
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
          return data.id
        }),
      )
      const { error: tagsError } = await apiClient.PUT('/api/v1/experiments/{name}/tags', {
        params: { path: { name: values.name } },
        body: { tag_ids: resolvedTags },
      })
      if (tagsError) throw new Error(errorMessage(tagsError))

      onSaved(values.name)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="Edit Properties"
      open={name !== null}
      onCancel={onCancel}
      onOk={handleSave}
      okText="Save"
      confirmLoading={saving}
      destroyOnHidden
    >
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
    </Modal>
  )
}
