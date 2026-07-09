import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Table, Input, Select, Button, Tag, Space, message, Tooltip } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { useAuth, hasMinRole } from '../auth/AuthContext'
import { DeleteExperimentModal } from '../components/DeleteExperimentModal'
import { ExperimentPropertiesModal } from '../components/ExperimentPropertiesModal'
import { UserAvatarGroup } from '../components/UserAvatar'
import { RelativeTime } from '../components/RelativeTime'

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
  const [editTarget, setEditTarget] = useState<string | null>(null)
  const canCreate = hasMinRole(user, 'editor')

  const refreshList = () => queryClient.invalidateQueries({ queryKey: ['experiments'] })

  return (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Input.Search
            placeholder="Search by name"
            allowClear
            style={{ width: 260 }}
            onSearch={(value) => {
              setQ(value)
              setPage(1)
            }}
          />
          <Select
            placeholder="Status"
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
            Create A/B Test
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
            title: 'Name',
            dataIndex: 'name',
            render: (name: string) => <Link to={`/experiments/${name}`}>{name}</Link>,
          },
          {
            title: 'Owner',
            key: 'owner',
            render: (_, record) =>
              record.owner_id ? (
                <UserAvatarGroup
                  users={[
                    {
                      id: record.owner_id,
                      firstName: record.owner_first_name ?? '',
                      lastName: record.owner_last_name ?? '',
                      email: record.owner_email ?? '',
                    },
                  ]}
                />
              ) : null,
          },
          { title: 'Status', dataIndex: 'status', render: (s: string) => <StatusBadge status={s} /> },
          {
            title: 'Publication',
            dataIndex: 'publication_status',
            render: (s: string) => <PublicationBadge status={s} />,
          },
          {
            title: 'Last Modified',
            key: 'updated',
            render: (_, record) => (
              <RelativeTime
                iso={record.archived_at ?? record.completed_at ?? record.started_at ?? record.created_at}
              />
            ),
          },
          {
            title: 'Actions',
            key: 'actions',
            render: (_, record) =>
              record.can_edit && (
                <Space className="hover-actions">
                  <Tooltip title="Edit">
                    <Button
                      size="small"
                      aria-label="Edit"
                      icon={<EditOutlined />}
                      onClick={() => setEditTarget(record.name)}
                    />
                  </Tooltip>
                  <Tooltip title="Delete">
                    <Button
                      danger
                      size="small"
                      aria-label="Delete"
                      icon={<DeleteOutlined />}
                      onClick={() => setDeleteTarget(record.name)}
                    />
                  </Tooltip>
                </Space>
              ),
          },
        ]}
      />

      <DeleteExperimentModal
        name={deleteTarget}
        onCancel={() => setDeleteTarget(null)}
        onDeleted={() => {
          message.success(`Experiment "${deleteTarget}" deleted`)
          setDeleteTarget(null)
          refreshList()
        }}
      />

      <ExperimentPropertiesModal
        name={editTarget}
        onCancel={() => setEditTarget(null)}
        onSaved={() => {
          message.success('Saved')
          setEditTarget(null)
          refreshList()
        }}
      />
    </div>
  )
}
