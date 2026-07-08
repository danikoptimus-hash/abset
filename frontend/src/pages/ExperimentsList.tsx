import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Input, Select, Button, Tag, Space, message } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { useAuth, hasMinRole } from '../auth/AuthContext'
import { DeleteExperimentModal } from '../components/DeleteExperimentModal'

const STATUS_COLORS: Record<string, string> = {
  designed: 'default',
  running: 'success',
  completed: 'blue',
  archived: 'default',
}

function StatusBadge({ status }: { status: string }) {
  return <Tag color={STATUS_COLORS[status] ?? 'default'}>{status}</Tag>
}

function PublicationBadge({ status }: { status: string }) {
  return <Tag color={status === 'published' ? 'success' : 'default'}>{status === 'published' ? 'published' : 'draft'}</Tag>
}

export function ExperimentsListPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const pageSize = 20

  const { data, isLoading } = useQuery({
    queryKey: ['experiments', { q, status, page }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments', {
        params: { query: { q: q || undefined, status, page, page_size: pageSize } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const canCreate = hasMinRole(user, 'editor')

  return (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Input.Search
            placeholder="Поиск по названию"
            allowClear
            style={{ width: 260 }}
            onSearch={(value) => {
              setQ(value)
              setPage(1)
            }}
          />
          <Select
            placeholder="Статус"
            allowClear
            style={{ width: 160 }}
            options={[
              { value: 'designed', label: 'designed' },
              { value: 'running', label: 'running' },
              { value: 'completed', label: 'completed' },
              { value: 'archived', label: 'archived' },
            ]}
            onChange={(value) => {
              setStatus(value)
              setPage(1)
            }}
          />
        </Space>
        {canCreate && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/experiments/new')}>
            Создать A/B тест
          </Button>
        )}
      </Space>

      <Table
        rowKey="name"
        loading={isLoading}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          onChange: setPage,
          showSizeChanger: false,
        }}
        columns={[
          {
            title: 'Название',
            dataIndex: 'name',
            render: (name: string) => <Link to={`/experiments/${name}`}>{name}</Link>,
          },
          { title: 'Владелец', dataIndex: 'owner_email' },
          { title: 'Статус', dataIndex: 'status', render: (s: string) => <StatusBadge status={s} /> },
          {
            title: 'Публикация',
            dataIndex: 'publication_status',
            render: (s: string) => <PublicationBadge status={s} />,
          },
          {
            title: 'Изменен',
            key: 'updated',
            render: (_, record) =>
              record.archived_at ?? record.completed_at ?? record.started_at ?? record.created_at,
          },
          {
            title: 'Действия',
            key: 'actions',
            render: (_, record) => (
              <Button
                danger
                size="small"
                disabled={!hasMinRole(user, 'editor')}
                onClick={() => setDeleteTarget(record.name)}
              >
                Удалить
              </Button>
            ),
          },
        ]}
      />

      <DeleteExperimentModal
        name={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onDeleted={() => {
          message.success(`Эксперимент «${deleteTarget}» удален`)
          setDeleteTarget(null)
          queryClient.invalidateQueries({ queryKey: ['experiments'] })
        }}
      />
    </div>
  )
}
