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

  useEffect(() => {
    if (!properties) return
    form.setFieldsValue({
      name: properties.name,
      owner_ids: properties.owners.map((u) => u.id),
      editor_ids: properties.editors.map((u) => u.id),
      visible_roles: properties.visible_roles,
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
        </Form>
      )}
    </Modal>
  )
}
