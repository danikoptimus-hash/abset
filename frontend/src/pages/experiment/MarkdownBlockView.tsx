import ReactMarkdown from 'react-markdown'
import { Typography, Input, Button, Card } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'

export interface BlockDraft {
  id: string | null
  kind: string
  title: string
  content_md: string
  position: number
}

const KIND_TITLES: Record<string, string> = {
  hypothesis: 'Гипотеза',
  conclusion: 'Выводы',
  decision: 'Решение',
}

interface Props {
  block: BlockDraft
  editing: boolean
  onChange: (patch: Partial<BlockDraft>) => void
  onRemove?: () => void
}

export function MarkdownBlockView({ block, editing, onChange, onRemove }: Props) {
  const title = block.kind === 'custom' ? block.title || 'Custom-блок' : KIND_TITLES[block.kind]

  if (!editing) {
    return (
      <div style={{ marginBottom: 24 }}>
        <Typography.Title level={5}>{title}</Typography.Title>
        {block.content_md.trim() ? (
          <ReactMarkdown>{block.content_md}</ReactMarkdown>
        ) : (
          <Typography.Text type="secondary">Не заполнено.</Typography.Text>
        )}
      </div>
    )
  }

  return (
    <Card
      size="small"
      style={{ marginBottom: 16 }}
      title={
        block.kind === 'custom' ? (
          <Input
            value={block.title}
            placeholder="Заголовок custom-блока"
            onChange={(e) => onChange({ title: e.target.value })}
          />
        ) : (
          title
        )
      }
      extra={
        block.kind === 'custom' && onRemove ? <Button icon={<DeleteOutlined />} size="small" onClick={onRemove} /> : null
      }
    >
      <div style={{ display: 'flex', gap: 16 }}>
        <Input.TextArea
          value={block.content_md}
          onChange={(e) => onChange({ content_md: e.target.value })}
          rows={6}
          style={{ flex: 1 }}
        />
        <div style={{ flex: 1, background: '#F7F7F7', padding: 8, borderRadius: 4, overflow: 'auto' }}>
          <ReactMarkdown>{block.content_md || '*Превью появится здесь*'}</ReactMarkdown>
        </div>
      </div>
    </Card>
  )
}
