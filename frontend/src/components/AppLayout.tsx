import { Layout, Menu, Dropdown, Space } from 'antd'
import { SettingOutlined, DownOutlined } from '@ant-design/icons'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthContext'
import { apiClient } from '../api/client'
import { PRODUCT_NAME } from '../branding'

const { Header, Content } = Layout

// Верхняя навигация как Dashboards/Charts/Datasets в Superset (FRONTEND.md §1).
const NAV_ITEMS = [
  { key: '/experiments', label: <Link to="/experiments">A/B Tests</Link> },
  { key: '/datasets', label: <Link to="/datasets">Datasets</Link> },
  { key: '/validation', label: <Link to="/validation">Validation</Link> },
]

export function AppLayout() {
  const { user, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()

  const selectedKey = NAV_ITEMS.find((item) => location.pathname.startsWith(item.key))?.key ?? ''

  const { data: version } = useQuery({
    queryKey: ['version'],
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/version')
      return data
    },
    staleTime: Infinity,
  })

  // Settings menu (Superset-style, UX package section 6): Security
  // (admin-only) / User / About, grouped with dividers. Replaces the old
  // avatar+name dropdown that mixed admin links into a generic user menu.
  // "List Roles" is intentionally not included — roles are fixed
  // (viewer/editor/admin), there's nothing to manage.
  const settingsItems = [
    ...(user?.role === 'admin'
      ? [
          { key: 'security-label', label: 'Security', type: 'group' as const },
          { key: 'admin', label: <Link to="/admin">List Users</Link> },
          { key: 'audit', label: <Link to="/audit">Action Log</Link> },
          { type: 'divider' as const },
        ]
      : []),
    { key: 'user-label', label: 'User', type: 'group' as const },
    { key: 'profile', label: <Link to="/profile">Info</Link> },
    { key: 'logout', label: 'Logout' },
    { type: 'divider' as const },
    { key: 'about-label', label: 'About', type: 'group' as const },
    {
      key: 'about',
      label: `${PRODUCT_NAME} · Version ${version?.version ?? '…'}`,
      disabled: true,
    },
  ]

  const handleSettingsMenuClick = ({ key }: { key: string }) => {
    if (key === 'logout') {
      logout().then(() => navigate('/login'))
    }
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', background: '#fff', borderBottom: '1px solid #E0E0E0' }}>
        <div style={{ fontWeight: 700, fontSize: 18, color: '#2E8B6D', marginRight: 32 }}>{PRODUCT_NAME}</div>
        <Menu mode="horizontal" selectedKeys={[selectedKey]} items={NAV_ITEMS} style={{ flex: 1, borderBottom: 'none' }} />
        {user && (
          <Dropdown menu={{ items: settingsItems, onClick: handleSettingsMenuClick }} trigger={['click']}>
            <Space style={{ cursor: 'pointer' }} data-testid="user-menu-trigger">
              <SettingOutlined />
              Settings
              <DownOutlined style={{ fontSize: 10 }} />
            </Space>
          </Dropdown>
        )}
      </Header>
      <Content style={{ padding: 24 }}>
        <Outlet />
      </Content>
    </Layout>
  )
}
