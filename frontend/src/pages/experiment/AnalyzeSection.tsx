import { useEffect, useState } from 'react'
import { Button, Select, Radio, Typography, Alert, Progress, Tooltip, Table } from 'antd'
import { ThunderboltOutlined, ReloadOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { apiClient, errorMessage } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useJobPolling } from '../../api/useJobPolling'
import { DatasetSelect } from '../../components/DatasetSelect'
import { AnalyzeResults } from './AnalyzeResults'
import { experimentResultsQueryKey, fetchExperimentResults } from './resultsQuery'
import { methodOptions, recommendedMethodId } from './methodOptions'
import type { HypothesisFamily, AnalyzeMetric } from './types'
import { PRODUCT_NAME } from '../../branding'

const CORRECTION_OPTIONS = [
  { value: 'holm', label: 'holm' },
  { value: 'bonferroni', label: 'bonferroni' },
  { value: 'fdr_bh', label: 'fdr_bh (Benjamini-Hochberg)' },
  { value: 'none', label: 'no correction' },
]

const EXCLUDE_VALUE = '__exclude__'

interface PreparedDataset {
  id: string
  filename: string
  nRows: number
  columns: string[]
  isDemo: boolean
}

export function AnalyzeSection({
  experimentName, hasAssignments, family, splitSource, declaredGroups, unitCol, alpha, metrics,
}: {
  experimentName: string
  hasAssignments: boolean
  // Primary metrics × treatment groups (see hypothesisFamily) — a family
  // of 1 means correction is a no-op, so the control is hidden rather than
  // offered (5-part package pt.5.1).
  family: HypothesisFamily
  // Item 12 (external split): "external" means the split happened outside
  // ABSet — there's no assignments join, the group comes from a column in
  // the uploaded post-data that the user maps to declaredGroups (the
  // experiment's declared group names, control first) right here, before
  // "Run analysis" is even enabled.
  splitSource: string
  declaredGroups: string[]
  // Item 2: the experiment's unit_col — checked for duplicates in the
  // prepared dataset to decide whether Date column is required. Not used
  // for external-split experiments (no unit_col-based assignments join).
  unitCol: string
  // The experiment's configured significance level (config.alpha) — drives
  // the Verdict cards/table here the same way it drives the HTML report.
  alpha: number
  // Item 2 (explicit method selection): type + pre-period-column presence
  // per metric — drives which methods the selector offers, and is passed
  // through to AnalyzeResults for the "manually selected" derivation.
  metrics: AnalyzeMetric[]
}) {
  const queryClient = useQueryClient()
  const [prepared, setPrepared] = useState<PreparedDataset | null>(null)
  const [selecting, setSelecting] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const isExternal = splitSource === 'external'
  const [groupColumn, setGroupColumn] = useState<string | undefined>(undefined)
  const [groupMapping, setGroupMapping] = useState<Record<string, string>>({})

  const [correction, setCorrection] = useState('holm')
  const [dateCol, setDateCol] = useState<string | undefined>(undefined)
  const showCorrection = family.familySize > 1
  const effectiveCorrection = showCorrection ? correction : 'none'

  // Item 3 (consolidated package, multi-select methods): replaces both the
  // old single-method override AND the separate "Compare alternative
  // methods" checkbox — a metric's comparison set is now exactly whichever
  // extra methods the user multi-selects (2+ selected = comparison, 1 =
  // pure calculation), instead of a fixed standard set toggled on/off.
  // methodSelections absent for a metric == just the recommended one; a
  // fresh Analysis tab (nothing touched) still submits a single method per
  // metric, matching what resolve_steps() would have picked anyway.
  const [methodSelections, setMethodSelections] = useState<Record<string, string[]>>({})
  // Which of the currently-selected ids is primary (drives the verdict).
  // Absent == the recommended one (or the first selected, if the
  // recommended one isn't in the current selection).
  const [primaryMethod, setPrimaryMethod] = useState<Record<string, string>>({})
  const namedMetrics = metrics.filter((m) => m.name.trim())

  const selectedMethodIds = (m: AnalyzeMetric): string[] =>
    methodSelections[m.name] ?? [recommendedMethodId(m.type, m.hasPreCol)]
  const primaryMethodId = (m: AnalyzeMetric): string => {
    const selected = selectedMethodIds(m)
    const p = primaryMethod[m.name]
    return p && selected.includes(p) ? p : selected[0]
  }
  // Item 3.1: "нельзя снять [recommended], не выбрав другой основной" — at
  // least one method must always stay selected (an empty multiselect is
  // rejected outright), and if the CURRENT primary gets deselected, the
  // first still-selected method is auto-promoted to primary rather than
  // leaving a dangling reference — this guarantees a valid primary exists
  // at all times without a separate "confirm removal" step.
  const setSelectedMethodIds = (m: AnalyzeMetric, ids: string[]) => {
    if (ids.length === 0) return
    setMethodSelections((prev) => ({ ...prev, [m.name]: ids }))
    if (!ids.includes(primaryMethodId(m))) {
      setPrimaryMethod((prev) => ({ ...prev, [m.name]: ids[0] }))
    }
  }
  const setPrimaryMethodId = (m: AnalyzeMetric, id: string) =>
    setPrimaryMethod((prev) => ({ ...prev, [m.name]: id }))

  // null = follow the default (open until the first result exists, then
  // collapsed behind "Re-run analysis" — UX package, п.3).
  const [panelOverride, setPanelOverride] = useState<boolean | null>(null)

  const { phase, stage, error, poll, reset } = useJobPolling<{ experiment_name: string }>()

  // Same query key as ResultsSection (Results tab) — shares one cache entry,
  // so whichever tab mounts first fetches and invalidateQueries below
  // refreshes both at once (including one that isn't currently mounted).
  const { data: results } = useQuery({
    queryKey: experimentResultsQueryKey(experimentName),
    queryFn: () => fetchExperimentResults(experimentName),
  })

  const panelOpen = panelOverride ?? !results
  const running = phase === 'running'

  const openRerunPanel = () => {
    setPrepared(null)
    setDateCol(undefined)
    setGroupColumn(undefined)
    setGroupMapping({})
    reset()
    setPanelOverride(true)
  }

  // Group assignment mapping (item 12, external split only) — distinct
  // values of the chosen column, most frequent first, fetched fresh each
  // time groupColumn changes so the mapping selects below always reflect
  // the CURRENTLY selected dataset+column, not a stale one.
  const { data: columnValues, isFetching: columnValuesLoading } = useQuery({
    queryKey: queryKeys.datasetColumnValues(prepared?.id, groupColumn),
    enabled: isExternal && !!prepared && !!groupColumn,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/column-values', {
        params: { path: { dataset_id: prepared!.id }, query: { column: groupColumn! } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  useEffect(() => {
    setGroupMapping({})
  }, [groupColumn])

  // Item 2: does the prepared dataset have duplicate unit_col values (day-
  // by-day/multi-row-per-user data)? If so, Date column stops being
  // optional — analyze() can't aggregate without knowing which column is
  // the date (abkit/experiment.py already refuses this combination
  // server-side; this surfaces it before submission instead of after a
  // failed job). Not applicable to external-split experiments (no unit_col
  // join at all in that flow).
  const { data: duplicateCheck } = useQuery({
    queryKey: queryKeys.datasetDuplicateCheck(prepared?.id, unitCol),
    enabled: !isExternal && !!prepared && !!unitCol,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/datasets/{dataset_id}/duplicate-check', {
        params: { path: { dataset_id: prepared!.id }, query: { column: unitCol } },
      })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })
  const dateColRequired = !isExternal && !!duplicateCheck?.has_duplicates

  const mappedGroups = new Set(Object.values(groupMapping).filter((g) => g !== EXCLUDE_VALUE))
  const groupMappingComplete = declaredGroups.every((g) => mappedGroups.has(g))

  const handleSelectDataset = async (datasetId: string) => {
    setSelecting(true)
    setUploadError(null)
    try {
      const { data, error } = await apiClient.GET('/api/v1/datasets', { params: { query: { page_size: 200 } } })
      if (error) throw new Error(errorMessage(error))
      const chosen = data.items.find((d) => d.id === datasetId)
      if (!chosen) throw new Error('Dataset not found')
      setPrepared({ id: chosen.id, filename: chosen.filename, nRows: chosen.n_rows, columns: chosen.columns, isDemo: false })
      setGroupColumn(undefined)
      setGroupMapping({})
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Failed to load dataset')
    } finally {
      setSelecting(false)
    }
  }

  const generateDemoData = async () => {
    setSelecting(true)
    setUploadError(null)
    try {
      const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/demo-post-data', {
        params: { path: { name: experimentName } },
        body: { effect: 0.03 },
      })
      if (error) throw new Error(errorMessage(error))
      setPrepared({ id: data.id, filename: data.filename, nRows: data.n_rows, columns: data.columns, isDemo: true })
      // UX contract, part B: this persists a real dataset row (source=demo)
      // — it should show up in the Datasets list/select without a reload,
      // same as any other dataset creation path.
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Failed to generate demo data')
    } finally {
      setSelecting(false)
    }
  }

  const runAnalyze = async () => {
    if (!prepared) return
    if (isExternal && (!groupColumn || !groupMappingComplete)) return
    reset()
    // Item 3: always send the CURRENTLY EFFECTIVE selection for every named
    // metric, not just the ones the user actually touched — an untouched
    // metric sends [recommended] (single method, harmless — identical to
    // what resolve_steps() would have picked anyway), so there's one code
    // path instead of two. Primary id goes first; the backend reads
    // list[0] as designed, the rest as the comparison set.
    const methods = Object.fromEntries(
      namedMetrics.map((m) => {
        const selected = selectedMethodIds(m)
        const primary = primaryMethodId(m)
        return [m.name, [primary, ...selected.filter((id) => id !== primary)]]
      }),
    )
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/analyze', {
      params: { path: { name: experimentName } },
      body: {
        dataset_id: prepared.id, correction: effectiveCorrection,
        date_col: dateCol ?? null, methods,
        ...(isExternal ? { group_column: groupColumn, group_mapping: groupMapping } : {}),
      },
    })
    if (error) {
      setUploadError(errorMessage(error))
      return
    }
    await poll(data.job_id)
    await queryClient.invalidateQueries({ queryKey: experimentResultsQueryKey(experimentName) })
    // UX contract, part B: a completed analysis also changes fields the
    // experiment detail query exposes (lifecycle dates, "Last modified") and
    // the list's "Last Modified" column — this used to only invalidate the
    // results query, leaving those stale if their pages/components stay
    // mounted.
    queryClient.invalidateQueries({ queryKey: queryKeys.experiment(experimentName) })
    queryClient.invalidateQueries({ queryKey: queryKeys.experimentsAll() })
    setPanelOverride(false)
  }

  return (
    <div>
      {uploadError && <Alert type="error" showIcon message={uploadError} style={{ marginBottom: 16, maxWidth: 480 }} closable onClose={() => setUploadError(null)} />}

      {panelOpen && (
        <div style={{ maxWidth: 480 }}>
          {/* Analysis options — read at the moment "Run analysis" is
              clicked, so they need to be set BEFORE data is uploaded/run,
              not after (UX package, item A). */}
          <Typography.Text strong>Analysis options</Typography.Text>
          <div style={{ marginTop: 8, marginBottom: 24 }}>
            {namedMetrics.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
                  Analysis methods
                </Typography.Text>
                {namedMetrics.map((m) => {
                  const options = methodOptions(m.type, m.hasPreCol)
                  const selected = selectedMethodIds(m)
                  const primary = primaryMethodId(m)
                  return (
                    <div key={m.name} style={{ marginBottom: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Typography.Text style={{ width: 140, flexShrink: 0 }} ellipsis={{ tooltip: m.name }}>
                          {m.name}
                        </Typography.Text>
                        {/* Item 3.1: multi-select replaces both the single
                            method dropdown and the "Compare alternative
                            methods" checkbox — 2+ selected = comparison,
                            1 = pure calculation. */}
                        <Select
                          mode="multiple"
                          style={{ flex: 1 }}
                          size="small"
                          value={selected}
                          disabled={running}
                          onChange={(ids) => setSelectedMethodIds(m, ids)}
                          options={options.map((o) => ({
                            value: o.id,
                            label: o.recommended ? `${o.label} (recommended)` : o.label,
                          }))}
                          aria-label={`method-select-${m.name}`}
                        />
                      </div>
                      {selected.length > 1 && (
                        <div style={{ marginLeft: 148, marginTop: 4 }}>
                          <Typography.Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>
                            Primary (drives verdict):
                          </Typography.Text>
                          <Radio.Group
                            size="small"
                            value={primary}
                            disabled={running}
                            onChange={(e) => setPrimaryMethodId(m, e.target.value)}
                            aria-label={`primary-method-${m.name}`}
                          >
                            {selected.map((id) => (
                              <Radio key={id} value={id}>
                                {options.find((o) => o.id === id)?.label ?? id}
                              </Radio>
                            ))}
                          </Radio.Group>
                        </div>
                      )}
                    </div>
                  )
                })}
                {namedMetrics.some((m) => primaryMethodId(m) !== recommendedMethodId(m.type, m.hasPreCol)) && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginTop: 4 }}
                    message={
                      <>
                        {namedMetrics
                          .filter((m) => primaryMethodId(m) !== recommendedMethodId(m.type, m.hasPreCol))
                          .map((m) => (
                            <div key={m.name}>
                              <strong>{m.name}</strong>: differs from the designed method — power was calculated
                              for {methodOptions(m.type, m.hasPreCol).find((o) => o.recommended)?.label}.
                            </div>
                          ))}
                      </>
                    }
                  />
                )}
              </div>
            )}
            {/* Item 3.2: correction now lives in the main options flow (it
                already had its own "only when family > 1" gate — no longer
                tucked inside a removed "Advanced options" collapse). */}
            {showCorrection && (
              <div style={{ marginBottom: 12 }}>
                <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
                  Multiple testing correction
                </Typography.Text>
                <Select
                  style={{ width: '100%' }}
                  value={correction}
                  onChange={setCorrection}
                  options={CORRECTION_OPTIONS}
                  disabled={running}
                />
                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
                  Your design tests {family.familySize} hypotheses ({family.primaryCount} primary metric
                  {family.primaryCount === 1 ? '' : 's'} × {family.treatmentGroupCount} treatment group
                  {family.treatmentGroupCount === 1 ? '' : 's'}) — correction controls the family-wise
                  error rate.
                </Typography.Paragraph>
              </div>
            )}
            {prepared && prepared.columns.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
                  {dateColRequired ? <><span style={{ color: '#ff4d4f' }}>*</span> Date column</> : 'Date column (optional)'}
                </Typography.Text>
                <Select
                  style={{ width: '100%' }}
                  placeholder="For cumulative lift, if the data has multiple rows per user"
                  allowClear
                  value={dateCol}
                  onChange={setDateCol}
                  options={prepared.columns.map((c) => ({ value: c, label: c }))}
                  disabled={running}
                  status={dateColRequired && !dateCol ? 'error' : undefined}
                  aria-label="date-column-select"
                />
                {dateColRequired && (
                  <Typography.Paragraph type={dateCol ? 'secondary' : 'danger'} style={{ fontSize: 12, marginTop: 4, marginBottom: 0 }}>
                    Dataset contains {duplicateCheck?.n_duplicated_units} duplicated unit ids (daily/multi-row
                    data). Select the date column so rows can be aggregated per user.
                  </Typography.Paragraph>
                )}
              </div>
            )}
          </div>

          <Typography.Text strong>Data</Typography.Text>
          <div style={{ marginTop: 8, marginBottom: 16 }}>
            <DatasetSelect
              value={prepared && !prepared.isDemo ? prepared.id : undefined}
              onChange={handleSelectDataset}
              disabled={selecting || running}
              placeholder="Select post-period dataset"
              ariaLabel="post-period-dataset-select"
            />
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 12 }}>
              Don't see your data? <Link to="/datasets" target="_blank">Create a new dataset on the Datasets page</Link>.
            </Typography.Paragraph>
            {!isExternal && (
              <Tooltip title={hasAssignments ? '' : 'No assignments for this experiment'}>
                <Button
                  icon={<ThunderboltOutlined />}
                  disabled={!hasAssignments || selecting || running}
                  loading={selecting}
                  onClick={generateDemoData}
                  block
                >
                  Generate demo post-period data (+3% effect)
                </Button>
              </Tooltip>
            )}

            {prepared && (
              <Alert
                type="success"
                showIcon
                icon={<CheckCircleOutlined />}
                style={{ marginTop: 12 }}
                message={
                  prepared.isDemo
                    ? `Demo data generated: ${prepared.nRows} users, +3% injected effect`
                    : `Data ready: ${prepared.filename} — ${prepared.nRows} rows, ${prepared.columns.length} columns`
                }
              />
            )}
          </div>

          {isExternal && prepared && (
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong>Group assignment</Typography.Text>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 8 }}>
                The split happened outside {PRODUCT_NAME} — pick the column that carries it, then map each value to a
                declared group (or exclude it).
              </Typography.Paragraph>
              <Select
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="Group column"
                value={groupColumn}
                onChange={setGroupColumn}
                options={prepared.columns.map((c) => ({ value: c, label: c }))}
                disabled={running}
                aria-label="group-column-select"
              />
              {groupColumn && (
                <Table
                  size="small"
                  loading={columnValuesLoading}
                  dataSource={columnValues?.values ?? []}
                  rowKey="value"
                  pagination={false}
                  columns={[
                    { title: 'Value', dataIndex: 'value' },
                    { title: 'Rows', dataIndex: 'count' },
                    {
                      title: 'Maps to',
                      key: 'mapsTo',
                      render: (_, row: { value: string }) => (
                        <Select
                          size="small"
                          style={{ width: 160 }}
                          placeholder="Map to..."
                          value={groupMapping[row.value]}
                          onChange={(v) => setGroupMapping((prev) => ({ ...prev, [row.value]: v }))}
                          disabled={running}
                          options={[
                            ...declaredGroups.map((g) => ({ value: g, label: g })),
                            { value: EXCLUDE_VALUE, label: 'Exclude' },
                          ]}
                          aria-label={`map-${row.value}`}
                        />
                      ),
                    },
                  ]}
                />
              )}
              {columnValues?.truncated && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Showing the {columnValues.values.length} most frequent values — less common ones are excluded
                  by default (leave them unmapped).
                </Typography.Text>
              )}
              {groupColumn && !groupMappingComplete && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 8 }}
                  message={`Map at least one value to each declared group: ${declaredGroups.join(', ')}`}
                />
              )}
            </div>
          )}

          <Tooltip
            title={
              !prepared
                ? 'Select a dataset or generate demo data first'
                : isExternal && (!groupColumn || !groupMappingComplete)
                  ? 'Finish the group assignment mapping first'
                  : dateColRequired && !dateCol
                    ? 'This dataset has duplicate unit ids — select the date column first'
                    : ''
            }
          >
            <Button
              type="primary"
              onClick={runAnalyze}
              disabled={
                !prepared || running ||
                (isExternal && (!groupColumn || !groupMappingComplete)) ||
                (dateColRequired && !dateCol)
              }
              loading={running}
              style={{ marginBottom: 24 }}
            >
              {running ? 'Running analysis...' : 'Run analysis'}
            </Button>
          </Tooltip>
        </div>
      )}

      {phase === 'running' && (
        <div style={{ marginBottom: 24, maxWidth: 480 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Starting analysis...'}</Typography.Text>
        </div>
      )}

      {phase === 'failed' && error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 24, maxWidth: 480 }} />
      )}

      {results && !panelOpen && (
        <Button icon={<ReloadOutlined />} onClick={openRerunPanel} style={{ marginBottom: 16 }}>
          Re-run analysis
        </Button>
      )}

      {results && <AnalyzeResults data={results} alpha={alpha} />}
    </div>
  )
}
