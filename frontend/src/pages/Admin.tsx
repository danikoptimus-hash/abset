import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Button, Modal, Form, Input, Select, Switch, message, Typography, Space, Tag } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { apiClient, errorMessage } from '../api/client'
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

  const { data: users, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/admin/users')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const openEdit = (user: UserAdminOut) => {
    setModalUser(user)
    form.setFieldsValue({
      email: user.email, first_name: user.first_name, last_name: user.last_name,
      role: user.role, is_active: user.is_active,
    })
  }

  const openCreate = () => {
    setModalUser('new')
    form.resetFields()
    form.setFieldsValue({ role: 'viewer', is_active: true })
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
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    } catch (e) {
      message.error(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

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

  return (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          Users
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          Create User
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={users ?? []}
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
        onCancel={() => setModalUser(null)}
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
}
