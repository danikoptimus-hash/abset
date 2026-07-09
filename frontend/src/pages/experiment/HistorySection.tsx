import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Typography, Table } from 'antd'
import { apiClient, errorMessage } from '../../api/client'
import { RelativeTime } from '../../components/RelativeTime'

export function HistorySection({ name }: { name: string }) {
  const [page, setPage] = useState(1)
  const pageSize = 50

  const { data, isLoading } = useQuery({
    queryKey: ['experiment-audit', name, page],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/audit', {
        params: { path: { name }, query: { page, page_size: pageSize } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  return (
    <div>
      <Typography.Title level={4}>History</Typography.Title>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize, total: data?.total ?? 0, onChange: setPage, showSizeChanger: false }}
        columns={[
          { title: 'When', dataIndex: 'ts', render: (ts: string) => <RelativeTime iso={ts} /> },
          { title: 'User', dataIndex: 'user_email' },
          { title: 'Action', dataIndex: 'action' },
        ]}
      />
    </div>
  )
}
