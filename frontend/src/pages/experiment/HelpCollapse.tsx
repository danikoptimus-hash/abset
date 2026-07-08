import { Collapse, Alert } from 'antd'
import ReactMarkdown from 'react-markdown'
import { HELP_TEXTS, CHART_WARNINGS } from './helpTexts'

export function HelpCollapse({ chartType, table = false }: { chartType: string; table?: boolean }) {
  const text = HELP_TEXTS[chartType]
  const warning = CHART_WARNINGS[chartType]
  if (!text) return null

  return (
    <div style={{ marginTop: 8, marginBottom: 24 }}>
      {warning && <Alert type="warning" showIcon message={warning} style={{ marginBottom: 8 }} />}
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'help',
            label: table ? '❓ Как читать эту таблицу?' : '❓ Как читать этот график?',
            children: <ReactMarkdown>{text}</ReactMarkdown>,
          },
        ]}
      />
    </div>
  )
}
