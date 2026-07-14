import { useQuery } from '@tanstack/react-query'
import { Typography, Button, Alert, Space } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import { VerdictCards } from './AnalyzeResults'
import { DetailedResultsTable } from './DetailedResultsTable'
import { HelpCollapse } from './HelpCollapse'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'
import { experimentResultsQueryKey, fetchExperimentResults } from './resultsQuery'
import { LifecycleDates } from '../../components/LifecycleDates'
import { RelativeTime } from '../../components/RelativeTime'

interface Props {
  experimentName: string
  familySize: number
  createdAt: string
  startedAt: string | null
  completedAt: string | null
  blocks: BlockDraft[]
  editing: boolean
  onChangeBlock: (id: string | null, patch: Partial<BlockDraft>) => void
  onAddBlock: () => void
  onRemoveBlock: (id: string | null) => void
  // Item 2: the experiment's configured significance level, driving the
  // Verdict column/cards here the same way it drives Design/Analysis/the
  // HTML report — see DetailedResultsTable's alpha prop.
  alpha: number
}

export function ResultsSection({
  experimentName, familySize, createdAt, startedAt, completedAt, blocks, editing, onChangeBlock, onAddBlock, onRemoveBlock, alpha,
}: Props) {
  // Same query key as AnalyzeSection (Analysis tab) — shares the react-query
  // cache entry, so opening the Results tab directly (deep link/reload)
  // still gets the latest results without needing the Analysis tab to have
  // mounted first.
  const { data: results } = useQuery({
    queryKey: experimentResultsQueryKey(experimentName),
    queryFn: () => fetchExperimentResults(experimentName),
  })

  return (
    <div>
      {results ? (
        <>
          <Typography.Paragraph type="secondary" style={{ marginTop: -4, marginBottom: 4, fontSize: 13 }}>
            Analyzed <RelativeTime iso={results.run_meta.created_at} /> with{' '}
            {results.run_meta.dataset_filename ?? 'unknown dataset'} (run #{results.run_meta.run_number})
          </Typography.Paragraph>
          <div style={{ marginBottom: 16 }}>
            <LifecycleDates createdAt={createdAt} startedAt={startedAt} completedAt={completedAt} />
          </div>

          <VerdictCards results={results.results} alpha={alpha} />

          <Typography.Title level={4} style={{ marginTop: 8 }}>
            Detailed Results Table
          </Typography.Title>
          <DetailedResultsTable
            results={results.results}
            controlName={Object.values(results.chart_data.metrics)[0]?.control_name ?? 'control'}
            correction={results.correction ?? 'none'}
            experimentName={experimentName}
            showCorrection={familySize > 1}
            alpha={alpha}
          />
          <HelpCollapse chartType="verdicts_table" table />

          <Space style={{ marginBottom: 24 }}>
            <Button icon={<EyeOutlined />} href={`/api/v1/experiments/${experimentName}/reports/report.html`} target="_blank">
              View report
            </Button>
            <Button icon={<DownloadOutlined />} href={`/api/v1/experiments/${experimentName}/reports/report.html?download=1`}>
              Download report
            </Button>
          </Space>
        </>
      ) : (
        <Alert
          type="info"
          showIcon
          message="No analysis results yet — run analysis on the Analysis tab."
          style={{ marginBottom: 24 }}
        />
      )}

      <Typography.Title level={4} style={{ marginTop: 32 }}>
        Conclusions and Decision
      </Typography.Title>
      {blocks.map((b) => (
        <MarkdownBlockView
          key={b.id ?? `new-${b.position}`}
          block={b}
          editing={editing}
          onChange={(patch) => onChangeBlock(b.id, patch)}
          onRemove={b.kind === 'custom' ? () => onRemoveBlock(b.id) : undefined}
        />
      ))}
      {editing && <Button onClick={onAddBlock}>+ Add Block</Button>}
    </div>
  )
}
