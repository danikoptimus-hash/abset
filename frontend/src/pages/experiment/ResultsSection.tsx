import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Typography, Button, Alert, Space, Modal, Select, Tag, Tooltip, message } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import { VerdictCards } from './AnalyzeResults'
import { DetailedResultsTable } from './DetailedResultsTable'
import { HelpCollapse } from './HelpCollapse'
import { MarkdownBlockView } from './MarkdownBlockView'
import type { BlockDraft } from './MarkdownBlockView'
import { experimentResultsQueryKey, fetchExperimentResults } from './resultsQuery'
import { LifecycleDates } from '../../components/LifecycleDates'
import { RelativeTime } from '../../components/RelativeTime'
import { StrataBalanceTable } from '../../components/analysis/StrataBalanceTable'
import { SegmentBreakdown } from '../../components/analysis/SegmentBreakdown'
import { SegmentCutPicker, type SegmentCuts } from '../../components/analysis/SegmentCutPicker'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useJobPolling } from '../../api/useJobPolling'
import type { AnalysisResultsOut } from './analyzeTypes'
import type { AnalyzeMetric } from './types'

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
  // Item 2: type/pre-col per metric — DetailedResultsTable uses this to
  // derive "designed vs manually selected" (no reload/session state
  // needed — reconstructed purely from the metric config + the result's
  // own method string, so it works identically right after a run and on a
  // cold Results-tab page load).
  metrics: AnalyzeMetric[]
  // Feature (dataset name in downloads): design-dataset segment for the
  // detailed-results CSV filename.
  datasetSegment?: string | null
}

export function ResultsSection({
  experimentName, familySize, createdAt, startedAt, completedAt, blocks, editing, onChangeBlock, onAddBlock, onRemoveBlock, alpha, metrics, datasetSegment,
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

          <VerdictCards results={results.results} alpha={alpha} metrics={metrics} />

          <Typography.Title level={4} style={{ marginTop: 8 }}>
            Detailed Results Table
          </Typography.Title>
          <DetailedResultsTable
            results={results.results}
            controlName={Object.values(results.chart_data.metrics)[0]?.control_name ?? 'control'}
            correction={results.correction ?? 'none'}
            experimentName={experimentName}
            datasetSegment={datasetSegment}
            showCorrection={familySize > 1}
            alpha={alpha}
            metrics={metrics}
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

          <ResultsSegmentsPanel results={results} experimentName={experimentName} />
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

// §2: segments on the Results tab — the strata balance table + per-metric
// breakdown, plus "Analyze segments" to compute an ADDITIONAL cut post-hoc
// against this run's stored dataset (verdict/metrics untouched), and remove
// controls on post-hoc cuts. The exploratory caveat is pinned here.
function ResultsSegmentsPanel({
  results,
  experimentName,
}: {
  results: AnalysisResultsOut
  experimentName: string
}) {
  const queryClient = useQueryClient()
  const chart = results.chart_data
  const metricNames = Object.keys(chart.metrics)
  const [metric, setMetric] = useState<string | null>(null)
  const activeMetric = metric && metricNames.includes(metric) ? metric : metricNames[0]
  const metricChart = activeMetric ? chart.metrics[activeMetric] : undefined
  const hasSegments = !!metricChart && Object.keys(metricChart.segments_by_dimension).length > 0
  // Bugfix (ad-hoc segment columns must not be silently dropped): a requested
  // cut that produced nothing still gets a notice — so render the breakdown
  // (which carries the notice) even when there are no produced segments.
  const segmentSkips = chart.segment_skips ?? []
  const hasSkipNotices = segmentSkips.length > 0

  const datasetId = results.run_meta.dataset_id ?? undefined
  const [modalOpen, setModalOpen] = useState(false)
  const [cuts, setCuts] = useState<SegmentCuts>({ columns: [], combinations: [] })
  const { phase, poll } = useJobPolling<{ experiment_name: string }>()
  const running = phase === 'running'

  const { data: dsColumns } = useQuery({
    queryKey: queryKeys.datasetsForSelect(),
    enabled: modalOpen && !!datasetId,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets', { params: { query: { page_size: 200 } } })
      if (error) throw new Error(errorMessage(error))
      return data.items
    },
    select: (items) => items.find((d) => d.id === datasetId)?.columns ?? [],
  })

  const refetch = () => queryClient.invalidateQueries({ queryKey: experimentResultsQueryKey(experimentName) })

  const submit = async () => {
    if (!datasetId) return
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/results/segments', {
      params: { path: { name: experimentName } },
      body: {
        segment_columns: cuts.columns.length ? cuts.columns : null,
        segment_combinations: cuts.combinations.length ? cuts.combinations : null,
      },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    await poll(data.job_id)
    await refetch()
    setModalOpen(false)
    setCuts({ columns: [], combinations: [] })
  }

  const removeDimension = async (label: string) => {
    const { error } = await apiClient.DELETE('/api/v1/experiments/{name}/results/segments', {
      params: { path: { name: experimentName }, query: { label } },
    })
    if (error) {
      message.error(errorMessage(error))
      return
    }
    await refetch()
  }

  const cutsEmpty = cuts.columns.length === 0 && cuts.combinations.length === 0

  return (
    <div style={{ marginTop: 24 }}>
      <Space style={{ marginBottom: 8 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>
          Segments <Tag>exploratory</Tag>
        </Typography.Title>
        <Tooltip title={datasetId ? '' : 'The dataset this run was computed on is no longer available.'}>
          <Button size="small" onClick={() => setModalOpen(true)} disabled={!datasetId}>
            Analyze segments
          </Button>
        </Tooltip>
      </Space>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="Segment results are hypothesis-generating only — no multiple-testing correction is applied, and they are not decision inputs. Validate any segment finding with a fresh, targeted test."
      />

      {chart.strata_balance && <StrataBalanceTable balance={chart.strata_balance} />}

      {metricNames.length > 1 && hasSegments && (
        <Space style={{ margin: '8px 0' }}>
          <Typography.Text type="secondary">Metric:</Typography.Text>
          <Select
            size="small"
            value={activeMetric}
            onChange={setMetric}
            options={metricNames.map((m) => ({ value: m, label: m }))}
            style={{ minWidth: 160 }}
          />
        </Space>
      )}
      {metricChart && (hasSegments || hasSkipNotices) ? (
        <SegmentBreakdown
          metricChart={metricChart}
          adHocDimensions={chart.ad_hoc_dimensions ?? []}
          combinationDimensions={chart.combination_dimensions ?? []}
          postHocDimensions={chart.post_hoc_dimensions ?? []}
          segmentSkips={segmentSkips}
          onRemoveDimension={removeDimension}
        />
      ) : (
        <Typography.Paragraph type="secondary">
          No segment breakdown yet — use "Analyze segments" to add one.
        </Typography.Paragraph>
      )}

      <Modal
        title="Analyze segments"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={submit}
        okText="Add segments"
        confirmLoading={running}
        okButtonProps={{ disabled: cutsEmpty || !datasetId }}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
          Computed against this run's dataset and appended to the segments above — the metrics and verdict are
          untouched. Exploratory only.
        </Typography.Paragraph>
        {datasetId && dsColumns && (
          <SegmentCutPicker
            datasetId={datasetId}
            columns={dsColumns}
            value={cuts}
            onChange={setCuts}
            disabled={running}
          />
        )}
      </Modal>
    </div>
  )
}
