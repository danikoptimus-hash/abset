import { useState } from 'react'
import { Card, Form, Input, Button, Typography, Alert } from 'antd'
import { useNavigate } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { useAuth } from '../auth/AuthContext'

interface ChangePasswordValues {
  old_password: string
  new_password: string
}

export function ProfilePage() {
  const { user, refresh } = useAuth()
  const navigate = useNavigate()
  const [form] = Form.useForm<ChangePasswordValues>()
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const forced = user?.must_change_password ?? false

  const onFinish = async (values: ChangePasswordValues) => {
    setSubmitting(true)
    setError(null)
    setSuccess(false)
    try {
      const { error } = await apiClient.POST('/api/v1/auth/change-password', { body: values })
      if (error) throw new Error(errorMessage(error, 'Failed to change password'))
      setSuccess(true)
      form.resetFields()
      // Обновляем контекст (must_change_password -> false), чтобы гейт в
      // RequireAuth перестал заворачивать на /profile, и уходим на главную —
      // как в legacy (_render_force_password_change -> st.rerun после смены).
      await refresh()
      if (forced) navigate('/', { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to change password')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card style={{ maxWidth: 420 }}>
      <Typography.Title level={4}>{forced ? 'Change Password' : 'Profile'}</Typography.Title>
      {forced && (
        <Alert
          type="warning"
          showIcon
          message="You must change your password before continuing"
          style={{ marginBottom: 16 }}
        />
      )}
      <Typography.Paragraph type="secondary">
        {user?.email} · role {user?.role}
      </Typography.Paragraph>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      {success && !forced && (
        <Alert type="success" message="Password changed" showIcon style={{ marginBottom: 16 }} />
      )}
      <Form form={form} layout="vertical" onFinish={onFinish} disabled={submitting}>
        <Form.Item name="old_password" label="Current Password" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="new_password" label="New Password" rules={[{ required: true, min: 8 }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting}>
            Change Password
          </Button>
        </Form.Item>
      </Form>
    </Card>
  )
}
