import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Table, Input, Typography, Space } from 'antd'
import { apiClient, errorMessage } from '../api/client'
import { RelativeTime } from '../components/RelativeTime'

export function AuditPage() {
  const [user, setUser] = useState('')
  const [action, setAction] = useState('')
  const [page, setPage] = useState(1)
  const pageSize = 50

  const { data, isLoading } = useQuery({
    queryKey: ['audit', { user, action, page }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/audit', {
        params: { query: { user: user || undefined, action: action || undefined, page, page_size: pageSize } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  return (
    <div>
      <Typography.Title level={4}>Action Log</Typography.Title>
      <Space style={{ marginBottom: 16 }}>
        <Input
          placeholder="User (email)"
          allowClear
          style={{ width: 220 }}
          onChange={(e) => {
            setUser(e.target.value)
            setPage(1)
          }}
        />
        <Input
          placeholder="Action"
          allowClear
          style={{ width: 220 }}
          onChange={(e) => {
            setAction(e.target.value)
            setPage(1)
          }}
        />
      </Space>
      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize, total: data?.total ?? 0, onChange: setPage, showSizeChanger: false }}
        columns={[
          { title: 'When', dataIndex: 'ts', render: (ts: string) => <RelativeTime iso={ts} /> },
          { title: 'User', dataIndex: 'user_email' },
          { title: 'Action', dataIndex: 'action' },
          { title: 'Object', dataIndex: 'object_name' },
        ]}
      />
    </div>
  )
}
