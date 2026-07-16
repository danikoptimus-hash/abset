import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Button, Modal, Form, Input, Select, Switch, message, Typography, Space, Tag, Tabs } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { MonitoringPanel } from './admin/MonitoringPanel'
import type { components } from '../api/schema'

type UserAdminOut = components['schemas']['UserAdminOut']

interface UserFormValues {
  email: string
  first_name: string
  last_name: string
  role: string
  is_active: boolean
}

export function AdminPage() {
  const queryClient = useQueryClient()
  const [modalUser, setModalUser] = useState<UserAdminOut | 'new' | null>(null)
  const [form] = Form.useForm<UserFormValues>()
  const [saving, setSaving] = useState(false)
  // UX contract, part A: pristine snapshot captured at the same moment as
  // the form's own setFieldsValue (openEdit/openCreate below) — compared
  // against Form.useWatch's live values to know whether the modal has
  // anything worth warning about on close.
  const [pristine, setPristine] = useState<UserFormValues | null>(null)
  // Deactivated accounts (e.g. e2e/dev test users cleaned up per CLAUDE.md's
  // "_dev_ prefix + self-cleanup" rule) shouldn't clutter the list by
  // default — opt in with "Show inactive" instead.
  const [showInactive, setShowInactive] = useState(false)

  const { data: users, isLoading } = useQuery({
    queryKey: queryKeys.adminUsers(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/users')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const openEdit = (user: UserAdminOut) => {
    setModalUser(user)
    const values = {
      email: user.email, first_name: user.first_name, last_name: user.last_name,
      role: user.role, is_active: user.is_active,
    }
    form.setFieldsValue(values)
    setPristine(values)
  }

  const openCreate = () => {
    setModalUser('new')
    form.resetFields()
    const values = { email: '', first_name: '', last_name: '', role: 'viewer', is_active: true }
    form.setFieldsValue(values)
    setPristine(values)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      if (modalUser === 'new') {
        const { data, error } = await apiClient.POST('/api/v1/admin/users', {
          body: {
            email: values.email, first_name: values.first_name, last_name: values.last_name,
            role: values.role,
          },
        })
        if (error) throw new Error(errorMessage(error))
        Modal.info({
          title: 'User created',
          content: (
            <Typography.Paragraph>
              Temporary password (share it with the user — they will be asked to change it on first
              sign-in): <Typography.Text code copyable>{data.generated_password}</Typography.Text>
            </Typography.Paragraph>
          ),
        })
      } else if (modalUser) {
        const { error } = await apiClient.PATCH('/api/v1/admin/users/{user_id}', {
          params: { path: { user_id: modalUser.id } },
          body: {
            first_name: values.first_name, last_name: values.last_name,
            role: values.role, is_active: values.is_active,
          },
        })
        if (error) throw new Error(errorMessage(error))
      }
      message.success('Saved')
      setModalUser(null)
      queryClient.invalidateQueries({ queryKey: queryKeys.adminUsers() })
      // Audit gap fix (B.1): ExperimentPropertiesModal's owner/editor picker
      // reads users-picker, a separate cached query from admin-users — a
      // newly created/renamed/deactivated user must refresh both, or the
      // Properties modal keeps offering a stale name/list.
      queryClient.invalidateQueries({ queryKey: queryKeys.usersPicker() })
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const currentEmail = Form.useWatch('email', form)
  const currentFirstName = Form.useWatch('first_name', form)
  const currentLastName = Form.useWatch('last_name', form)
  const currentRole = Form.useWatch('role', form)
  const currentIsActive = Form.useWatch('is_active', form)
  const isDirty =
    modalUser !== null &&
    !!pristine &&
    (currentEmail !== pristine.email ||
      currentFirstName !== pristine.first_name ||
      currentLastName !== pristine.last_name ||
      currentRole !== pristine.role ||
      currentIsActive !== pristine.is_active)
  const { guard } = useUnsavedGuard(isDirty)
  const guardedCancel = () => guard(() => setModalUser(null))

  const handleResetPassword = async (user: UserAdminOut) => {
    const { data, error } = await apiClient.POST('/api/v1/admin/users/{user_id}/reset-password', {
      params: { path: { user_id: user.id } },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    Modal.info({
      title: `New password for ${user.email}`,
      content: (
        <Typography.Paragraph>
          <Typography.Text code copyable>{data.new_password}</Typography.Text>
        </Typography.Paragraph>
      ),
    })
  }

  const usersTab = (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          Users
        </Typography.Title>
        <Space>
          <Switch checked={showInactive} onChange={setShowInactive} aria-label="Show inactive" />
          <Typography.Text>Show inactive</Typography.Text>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            Create User
          </Button>
        </Space>
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={(users ?? []).filter((u) => showInactive || u.is_active)}
        columns={[
          { title: 'Email', dataIndex: 'email' },
          { title: 'First Name', dataIndex: 'first_name' },
          { title: 'Last Name', dataIndex: 'last_name' },
          { title: 'Role', dataIndex: 'role' },
          {
            title: 'Active',
            dataIndex: 'is_active',
            render: (active: boolean) => <Tag color={active ? 'success' : 'default'}>{active ? 'yes' : 'no'}</Tag>,
          },
          {
            title: 'Actions',
            key: 'actions',
            render: (_, record: UserAdminOut) => (
              <Space>
                <Button size="small" onClick={() => openEdit(record)}>
                  Edit
                </Button>
                <Button size="small" onClick={() => handleResetPassword(record)}>
                  Reset Password
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={modalUser === 'new' ? 'New User' : `Edit ${(modalUser as UserAdminOut)?.email ?? ''}`}
        open={modalUser !== null}
        onCancel={guardedCancel}
        onOk={handleSave}
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="email" label="Email" rules={[{ required: true }]}>
            <Input disabled={modalUser !== 'new'} />
          </Form.Item>
          <Form.Item name="first_name" label="First Name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="last_name" label="Last Name">
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Role" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'viewer', label: 'viewer' },
                { value: 'editor', label: 'editor' },
                { value: 'admin', label: 'admin' },
              ]}
            />
          </Form.Item>
          {modalUser !== 'new' && (
            <Form.Item
              name="is_active"
              label="Active"
              valuePropName="checked"
              extra="Prefer deactivating over deleting"
            >
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )

  return (
    <Tabs
      items={[
        { key: 'users', label: 'Users', children: usersTab },
        { key: 'monitoring', label: 'Monitoring', children: <MonitoringPanel /> },
      ]}
    />
  )
}
