import { useState } from 'react'
import { Button, Card, Form, Input, Typography, Alert, Collapse, Divider } from 'antd'
import { LoginOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthContext'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import logo from '../assets/logo.png'
import { PRODUCT_NAME } from '../branding'

interface LoginFormValues {
  email: string
  password: string
}

interface RegisterFormValues {
  email: string
  first_name: string
  last_name: string
  password: string
}

function SelfRegisterForm() {
  const [form] = Form.useForm<RegisterFormValues>()
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const onFinish = async (values: RegisterFormValues) => {
    setSubmitting(true)
    setError(null)
    setSuccess(false)
    try {
      const { error } = await apiClient.POST('/api/v1/auth/register', { body: values })
      if (error) throw new Error(errorMessage(error, 'Failed to register'))
      setSuccess(true)
      form.resetFields()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to register')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      {success && (
        <Alert
          type="success"
          message="Account created (Viewer role). Sign in above."
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}
      <Form form={form} layout="vertical" onFinish={onFinish} disabled={submitting}>
        <Form.Item name="email" label="Email" rules={[{ required: true, message: 'Enter your email' }]}>
          <Input autoComplete="username" />
        </Form.Item>
        <Form.Item name="first_name" label="First Name" rules={[{ required: true, message: 'Enter your first name' }]}>
          <Input autoComplete="given-name" />
        </Form.Item>
        <Form.Item name="last_name" label="Last Name">
          <Input autoComplete="family-name" />
        </Form.Item>
        <Form.Item name="password" label="Password" rules={[{ required: true, min: 8, message: 'At least 8 characters' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item>
          <Button htmlType="submit" block loading={submitting}>
            Create Account
          </Button>
        </Form.Item>
      </Form>
    </>
  )
}

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // Проставляется ссылкой "Use password instead" со страницы ошибки SSO
  // (backend/routers/oidc.py::_error_page) — тогда парольную форму сразу
  // разворачиваем: человек уже знает, что через SSO не вышло, и заставлять
  // его еще раз кликать "Sign in with password" незачем.
  const ssoFailed = searchParams.get('sso') === 'failed'

  const { data: config } = useQuery({
    queryKey: queryKeys.authConfig(),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/auth/config')
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const from = (location.state as { from?: Location })?.from?.pathname ?? '/experiments'

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true)
    setError(null)
    try {
      await login(values.email, values.password)
      navigate(from, { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to sign in')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh', background: '#F7F7F7' }}>
      <Card style={{ width: 360 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <img src={logo} alt={PRODUCT_NAME} style={{ maxWidth: 260, width: '100%', height: 'auto', display: 'block', margin: '0 auto' }} />
          <Typography.Text type="secondary">Sign in</Typography.Text>
        </div>
        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
        {ssoFailed && !error && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="Single sign-on didn't complete. You can try again or sign in with a password."
          />
        )}

        {/* SSO включено — кнопка становится ОСНОВНЫМ действием, парольная
            форма уезжает под спойлер (ТЗ п.3). Это обычная ссылка, а не
            fetch: весь обмен идет редиректами браузера, и XHR на
            /auth/oidc/login просто вернул бы 302 в никуда. */}
        {config?.oidc_enabled && (
          <>
            <Button
              type="primary"
              size="large"
              block
              icon={<LoginOutlined />}
              href="/api/v1/auth/oidc/login"
              // Не react-router Link: цель находится ВНЕ SPA (это редирект на
              // Keycloak), клиентская навигация тут неприменима.
            >
              Sign in with SSO
            </Button>
            <Divider plain style={{ marginTop: 20, marginBottom: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                or
              </Typography.Text>
            </Divider>
          </>
        )}

        <Collapse
          ghost
          // Без SSO — форма просто раскрыта, страница выглядит как раньше.
          // С SSO — свернута, кроме случая, когда пользователь пришел сюда
          // со страницы ошибки SSO.
          activeKey={!config?.oidc_enabled || ssoFailed ? ['password'] : undefined}
          defaultActiveKey={!config?.oidc_enabled || ssoFailed ? ['password'] : undefined}
          collapsible={config?.oidc_enabled ? undefined : 'disabled'}
          items={[
            {
              key: 'password',
              // При выключенном SSO заголовок скрываем: старая страница
              // логина не должна обрасти лишним аккордеоном.
              label: config?.oidc_enabled ? 'Sign in with password' : null,
              showArrow: !!config?.oidc_enabled,
              children: (
                <Form layout="vertical" onFinish={onFinish} disabled={submitting}>
                  <Form.Item name="email" label="Email" rules={[{ required: true, message: 'Enter your email' }]}>
                    <Input autoFocus={!config?.oidc_enabled} autoComplete="username" />
                  </Form.Item>
                  <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Enter your password' }]}>
                    <Input.Password autoComplete="current-password" />
                  </Form.Item>
                  <Form.Item style={{ marginBottom: 0 }}>
                    <Button type={config?.oidc_enabled ? 'default' : 'primary'} htmlType="submit" block loading={submitting}>
                      Sign In
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
        {config?.self_registration_enabled && (
          <Collapse
            ghost
            style={{ marginTop: 8 }}
            items={[{ key: 'register', label: 'Register', children: <SelfRegisterForm /> }]}
          />
        )}
      </Card>
    </div>
  )
}
