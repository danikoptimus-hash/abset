import { useQuery } from '@tanstack/react-query'
import { Typography, Button, Alert } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { VerdictCards } from './AnalyzeResults'
import { DetailedResultsTable } from './DetailedResultsTable'
import { HelpCollapse } from './HelpCollapse'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'
import { experimentResultsQueryKey, fetchExperimentResults } from './resultsQuery'

interface Props {
  experimentName: string
  blocks: BlockDraft[]
  editing: boolean
  onChangeBlock: (id: string | null, patch: Partial<BlockDraft>) => void
  onAddBlock: () => void
  onRemoveBlock: (id: string | null) => void
}

export function ResultsSection({ experimentName, blocks, editing, onChangeBlock, onAddBlock, onRemoveBlock }: Props) {
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
          <VerdictCards results={results.results} />

          <Typography.Title level={4} style={{ marginTop: 8 }}>
            Detailed Results Table
          </Typography.Title>
          <DetailedResultsTable
            results={results.results}
            controlName={Object.values(results.chart_data.metrics)[0]?.control_name ?? 'control'}
            correction={results.correction ?? 'none'}
            experimentName={experimentName}
          />
          <HelpCollapse chartType="verdicts_table" table />

          <Button
            icon={<DownloadOutlined />}
            href={`/api/v1/experiments/${experimentName}/reports/report.html`}
            target="_blank"
            style={{ marginBottom: 24 }}
          >
            Download HTML Report
          </Button>
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
