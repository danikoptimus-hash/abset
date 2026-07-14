import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Typography, Select, Button, InputNumber, Checkbox, Alert, Progress, Table, Tag, Collapse, Tooltip } from 'antd'
import { CheckCircleOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { apiClient, errorMessage } from '../api/client'
import { queryKeys } from '../api/queryKeys'
import { useJobPolling } from '../api/useJobPolling'
import { DatasetSelect } from '../components/DatasetSelect'
import { RelativeTime } from '../components/RelativeTime'

interface MethodFPR {
  method: string
  metric: string
  treatment_group: string
  n_sims: number
  fpr: number
  ci_low: number
  ci_high: number
  passed: boolean
}

interface MethodPower {
  method: string
  metric: string
  treatment_group: string
  n_sims: number
  empirical_power: number
  analytical_power: number | null
  discrepancy_warning: string | null
}

interface ValidateResult {
  aa: { methods: MethodFPR[] }
  ab: { methods: MethodPower[] }
  dataset_id: string
  dataset_filename: string | null
}

interface RawMetric {
  name: string
  type: string
  num?: string | null
  den?: string | null
}

// Columns the simulation reads from post-period data — unit_col + strata
// (needed to reproduce the split) + each metric's data column(s) (UX
// package, Validation п.C.3: compatibility check for a manually uploaded
// file, before simulations run rather than a cryptic failure mid-job).
function requiredColumns(config: Record<string, unknown>): string[] {
  const cols = new Set<string>()
  const unitCol = config.unit_col as string | undefined
  if (unitCol) cols.add(unitCol)
  ;((config.strata as string[] | undefined) ?? []).forEach((s) => cols.add(s))
  ;((config.metrics as RawMetric[] | undefined) ?? []).forEach((m) => {
    if (m.type === 'ratio') {
      if (m.num) cols.add(m.num)
      if (m.den) cols.add(m.den)
    } else {
      cols.add(m.name)
    }
  })
  return Array.from(cols)
}

interface DatasetInfo {
  id: string
  filename: string
  nRows: number
  columns: string[]
  uploadedAt: string | null
}

const WHAT_VALIDATION_RUNS = `Validation re-runs your experiment's design (its split method, strata, metrics and statistical tests) many times on this data: A/A — with no true effect, to verify the false positive rate stays at alpha; A/B — with a known injected effect, to verify the design detects it (empirical power).`

const WHAT_IS_THIS = `**What A/A simulations do**

Repeatedly split your historical data into two random groups with NO real difference between them (pure control vs control) and run the same statistical test many times. If the tool is honest, roughly 5% of these "fake" tests should come back significant just by chance — that's the false positive rate (FPR) matching alpha.

**What A/B simulations do**

The same repeated splitting, but with a known artificial effect injected into one group before testing. The empirical power is the share of runs that correctly detect that effect — it should roughly match the power the design predicted.

**When to run this**

Before an important/high-stakes test, when using a new metric type or measurement method for the first time, or when onboarding onto this tool with data patterns you haven't validated before.

**How to read the result**

The FPR's 95% confidence interval should cover 5% — well above means too many false positives (not honest); well below may mean overly conservative. p-values from A/A runs should be roughly uniformly distributed. For A/B, a gap of more than 5 percentage points between empirical and analytical power is flagged as a warning — it usually means the analytical power formula doesn't match how your real data behaves.`

function OptionLabel({ children }: { children: ReactNode }) {
  return (
    <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
      {children}
    </Typography.Text>
  )
}

function OptionHint({ children }: { children: ReactNode }) {
  return (
    <Typography.Text type="secondary" style={{ display: 'block', marginTop: 2, fontSize: 12 }}>
      {children}
    </Typography.Text>
  )
}

export function ValidationPage() {
  const [experimentName, setExperimentName] = useState<string | undefined>(undefined)
  const [manualDataset, setManualDataset] = useState<DatasetInfo | null>(null)
  const [showManualUpload, setShowManualUpload] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [nSims, setNSims] = useState<number | null>(2000)
  const [compareMethods, setCompareMethods] = useState(false)
  const [effect, setEffect] = useState(0.05)

  const { phase, stage, error, result, poll, reset } = useJobPolling<ValidateResult>()

  const { data: experiments } = useQuery({
    queryKey: queryKeys.experimentsForValidation(),
    queryFn: async () => {
      const { data } = await apiClient.GET('/api/v1/experiments', { params: { query: { page_size: 200 } } })
      return data?.items ?? []
    },
  })

  const { data: experimentDetail } = useQuery({
    queryKey: queryKeys.experimentForValidation(experimentName),
    enabled: !!experimentName,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}', { params: { path: { name: experimentName! } } })
      if (error) throw new Error(errorMessage(error))
      return data
    },
  })

  // Auto-datasource (UX package, Validation п.C.1): the pre-design dataset
  // already stored for this experiment, if any — 404 means none stored
  // (older/imported experiments, п.C.4), not an error to surface loudly.
  const { data: designDataset, isFetching: designDatasetLoading } = useQuery({
    queryKey: queryKeys.experimentDesignDataset(experimentName),
    enabled: !!experimentName,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/design-dataset', {
        params: { path: { name: experimentName! } },
      })
      if (error) return null
      return data
    },
  })

  useEffect(() => {
    setManualDataset(null)
    setShowManualUpload(false)
    reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentName])

  useEffect(() => {
    if (!designDatasetLoading && experimentName && designDataset === null) {
      setShowManualUpload(true)
    }
  }, [designDatasetLoading, experimentName, designDataset])

  const activeDataset: DatasetInfo | null = showManualUpload
    ? manualDataset
    : designDataset
      ? {
          id: designDataset.id, filename: designDataset.filename, nRows: designDataset.n_rows,
          columns: designDataset.columns, uploadedAt: designDataset.uploaded_at,
        }
      : null

  const handleSelectDataset = async (datasetId: string) => {
    setUploading(true)
    setUploadError(null)
    try {
      const { data, error } = await apiClient.GET('/api/v1/datasets', { params: { query: { page_size: 200 } } })
      if (error) throw new Error(errorMessage(error))
      const chosen = data.items.find((d) => d.id === datasetId)
      if (!chosen) throw new Error('Dataset not found')

      if (experimentDetail) {
        const missing = requiredColumns(experimentDetail.config).filter((c) => !chosen.columns.includes(c))
        if (missing.length > 0) {
          setUploadError(
            `This dataset is missing columns required by the experiment's design: ${missing.join(', ')}`,
          )
          return
        }
      }

      setManualDataset({
        id: chosen.id, filename: chosen.filename, nRows: chosen.n_rows, columns: chosen.columns,
        uploadedAt: chosen.uploaded_at,
      })
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Failed to load dataset')
    } finally {
      setUploading(false)
    }
  }

  // n_sims: min 100 — fewer simulations make FPR/power estimates too noisy
  // to interpret. Enforced as a visible validation error (not a silent
  // clamp to 100) — UX package, Validation п.3.4.
  const nSimsInvalid = nSims === null || nSims < 100

  const runValidate = async () => {
    if (!experimentName || !activeDataset || nSims === null || nSimsInvalid) return
    reset()
    const { data, error } = await apiClient.POST('/api/v1/experiments/{name}/validate', {
      params: { path: { name: experimentName } },
      body: { dataset_id: activeDataset.id, n_sims: nSims, compare_methods: compareMethods, effect },
    })
    if (error) {
      setUploadError(errorMessage(error))
      return
    }
    await poll(data.job_id)
  }

  const disabledReason = !experimentName
    ? 'Select an experiment first'
    : !activeDataset
      ? 'Select post-period data or use the experiment design data'
      : nSimsInvalid
        ? 'Enter at least 100 simulations'
        : ''
  const canSubmit = !!activeDataset && !nSimsInvalid && phase !== 'running'

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>Validation (A/A, A/B)</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 720 }}>
        Validation checks that the statistical engine is honest on your data before you trust real test results.
        A/A simulations verify the false positive rate stays at alpha
        {experimentDetail ? ` (${(Number(experimentDetail.config.alpha ?? 0.05) * 100).toFixed(1)}%, this experiment's configured significance level)` : ''}{' '}
        when there is no true effect; A/B
        simulations verify the engine detects an effect of a given size (empirical power).
      </Typography.Paragraph>
      <Collapse
        ghost
        size="small"
        style={{ marginBottom: 24, maxWidth: 720 }}
        items={[
          {
            key: 'what',
            label: '❓ What is this and when to use it',
            children: <Typography.Paragraph style={{ whiteSpace: 'pre-line' }}>{WHAT_IS_THIS}</Typography.Paragraph>,
          },
        ]}
      />

      {/* Experiment is the primary control (UX package, Validation п.3.1) —
          validation re-runs an EXPERIMENT's design, not an arbitrary
          dataset, so picking the experiment comes first and large,
          everything else (data, options) follows from that choice. */}
      <div style={{ maxWidth: 640, marginBottom: 24 }}>
        <Typography.Text strong style={{ fontSize: 16 }}>Experiment</Typography.Text>
        <Select
          size="large"
          placeholder="Search for an experiment by name"
          style={{ width: '100%', marginTop: 8 }}
          value={experimentName}
          onChange={setExperimentName}
          showSearch
          optionFilterProp="label"
          aria-label="validation-experiment-select"
          options={(experiments ?? []).map((e) => ({ value: e.name, label: e.name }))}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
          {WHAT_VALIDATION_RUNS}
        </Typography.Paragraph>
      </div>

      <div style={{ maxWidth: 480 }}>
        <Typography.Text strong>Data</Typography.Text>
        <div style={{ marginTop: 8, marginBottom: 24 }}>
          {!experimentName && (
            <Typography.Text type="secondary">Select an experiment above to choose its data.</Typography.Text>
          )}

          {experimentName && !showManualUpload && designDataset && (
            <Alert
              type="success"
              showIcon
              icon={<CheckCircleOutlined />}
              message={
                <>
                  <Tag color="blue" style={{ marginRight: 6 }}>From experiment design</Tag>
                  {designDataset.filename} — {designDataset.n_rows} rows, uploaded{' '}
                  <RelativeTime iso={designDataset.uploaded_at} />
                </>
              }
              action={
                <Button size="small" type="link" onClick={() => setShowManualUpload(true)}>
                  Use different data
                </Button>
              }
            />
          )}

          {experimentName && showManualUpload && (
            <>
              {designDataset === null && (
                <Alert
                  type="info"
                  showIcon
                  message="No stored design data for this experiment"
                  style={{ marginBottom: 12 }}
                />
              )}
              <DatasetSelect
                value={manualDataset?.id}
                onChange={handleSelectDataset}
                disabled={uploading}
                placeholder="Select simulation data"
                ariaLabel="validation-dataset-select"
                style={{ marginBottom: 4 }}
              />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4, marginBottom: 12 }}>
                Don't see your data? <Link to="/datasets" target="_blank">Create a new dataset on the Datasets page</Link>.
              </Typography.Paragraph>
              {manualDataset && (
                <Alert
                  type="success"
                  showIcon
                  icon={<CheckCircleOutlined />}
                  style={{ marginBottom: 12 }}
                  message={`Data ready: ${manualDataset.filename} — ${manualDataset.nRows} rows, ${manualDataset.columns.length} columns`}
                />
              )}
              {designDataset && (
                <Button size="small" onClick={() => { setShowManualUpload(false); setManualDataset(null) }}>
                  Reset to design data
                </Button>
              )}
            </>
          )}
        </div>

        <Typography.Text strong>Validation options</Typography.Text>
        <div style={{ marginTop: 8, marginBottom: 24 }}>
          <div style={{ marginBottom: 12 }}>
            <OptionLabel>Number of simulations</OptionLabel>
            <InputNumber
              // No `min` here on purpose: rc-input-number only fires
              // onChange for an out-of-[min,max] value once the field is
              // typed back in range — while it's out of range mid-typing,
              // onChange simply doesn't fire, and on blur it silently
              // snaps to `min` without ever calling onChange with the
              // rejected value. That's exactly the "молча заменять 10 на
              // 100" behavior the UX package forbids — so validation is
              // done ourselves (nSimsInvalid) against an unconstrained
              // input instead.
              step={100}
              value={nSims}
              onChange={setNSims}
              status={nSimsInvalid ? 'error' : undefined}
              style={{ width: '100%' }}
            />
            <OptionHint>
              Minimum 100 — fewer simulations make FPR/power estimates too noisy to interpret. 500 = quick
              check, 2000 = strict.
            </OptionHint>
            {nSimsInvalid && (
              <Typography.Text type="danger" style={{ display: 'block', fontSize: 12, marginTop: 2 }}>
                Enter at least 100 simulations.
              </Typography.Text>
            )}
          </div>

          <div style={{ marginBottom: 12 }}>
            <OptionLabel>Effect (A/B)</OptionLabel>
            <InputNumber min={0} step={0.01} value={effect} onChange={(v) => setEffect(v ?? 0.05)} style={{ width: '100%' }} />
            <OptionHint>Injected effect for the power check — 0.05 = +5% relative lift</OptionHint>
          </div>

          <Tooltip title="Also compute FPR/power for alternative methods to compare their honesty on your data">
            <Checkbox checked={compareMethods} onChange={(e) => setCompareMethods(e.target.checked)}>
              Compare alternative methods
            </Checkbox>
          </Tooltip>
        </div>

        <Tooltip title={disabledReason}>
          <Button type="primary" disabled={!canSubmit} loading={phase === 'running'} onClick={runValidate} style={{ marginBottom: 24 }}>
            {phase === 'running' ? 'Running validation...' : 'Run Validation'}
          </Button>
        </Tooltip>
      </div>

      {uploadError && <Alert type="error" showIcon message={uploadError} style={{ marginBottom: 16, maxWidth: 480 }} />}

      {phase === 'running' && (
        <div style={{ marginBottom: 24, maxWidth: 480 }}>
          <Progress percent={undefined} status="active" showInfo={false} />
          <Typography.Text>{stage ?? 'Running validation...'}</Typography.Text>
        </div>
      )}
      {phase === 'failed' && error && <Alert type="error" showIcon message={error} style={{ marginBottom: 24, maxWidth: 480 }} />}

      {phase === 'completed' && result && (
        <ValidationResults result={result} alpha={Number(experimentDetail?.config.alpha ?? 0.05)} />
      )}
    </div>
  )
}

// Item 2.2/2.4: expected FPR is the EXPERIMENT'S OWN configured alpha, not
// a hardcoded 5% — matches abkit/validation/simulation.py's
// passed=bool(ci_low <= config.alpha <= ci_high), which already used
// config.alpha throughout (the display text just used to lag behind it).
function ValidationResults({ result, alpha }: { result: ValidateResult; alpha: number }) {
  const alphaPct = (alpha * 100).toFixed(2)
  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 13 }}>
        Validated with {result.dataset_filename ?? 'dataset'} (id {result.dataset_id.slice(0, 8)}…) — target alpha: {alphaPct}%
      </Typography.Paragraph>

      <Typography.Title level={5}>A/A: empirical FPR (false-positive rate)</Typography.Title>
      <Table
        size="small"
        rowKey={(r: MethodFPR) => `${r.metric}_${r.method}_${r.treatment_group}`}
        dataSource={result.aa.methods}
        pagination={false}
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Group', dataIndex: 'treatment_group' },
          { title: 'Method', dataIndex: 'method' },
          { title: 'n_sims', dataIndex: 'n_sims' },
          { title: 'FPR', dataIndex: 'fpr', render: (v: number) => `${(v * 100).toFixed(2)}%` },
          {
            title: '95% CI', key: 'ci',
            render: (_: unknown, r: MethodFPR) => `[${(r.ci_low * 100).toFixed(2)}%, ${(r.ci_high * 100).toFixed(2)}%]`,
          },
          {
            title: 'Verdict', dataIndex: 'passed',
            render: (v: boolean) => <Tag color={v ? 'success' : 'error'}>{v ? 'honest' : 'lying'}</Tag>,
          },
        ]}
      />
      <Collapse
        ghost
        size="small"
        style={{ marginBottom: 24 }}
        items={[
          {
            key: 'aa-help',
            label: '❓ How do I read this table?',
            children: (
              <Typography.Paragraph>
                Each row is one metric × method × comparison group. FPR is the share of the {result.aa.methods[0]?.n_sims ?? 'N'} A/A
                simulations that came back significant despite no real difference — it should sit close to {alphaPct}%
                (this experiment&apos;s configured significance level). The verdict is &quot;honest&quot; when the 95% CI covers{' '}
                {alphaPct}%; &quot;lying&quot; means the method is producing significantly more (or fewer) false positives than it
                claims to.
              </Typography.Paragraph>
            ),
          },
        ]}
      />

      <Typography.Title level={5}>A/B: empirical vs analytical power</Typography.Title>
      <Table
        size="small"
        rowKey={(r: MethodPower) => `${r.metric}_${r.method}_${r.treatment_group}`}
        dataSource={result.ab.methods}
        pagination={false}
        columns={[
          { title: 'Metric', dataIndex: 'metric' },
          { title: 'Group', dataIndex: 'treatment_group' },
          { title: 'Method', dataIndex: 'method' },
          { title: 'n_sims', dataIndex: 'n_sims' },
          { title: 'Power (empirical)', dataIndex: 'empirical_power', render: (v: number) => `${(v * 100).toFixed(1)}%` },
          {
            title: 'Power (analytical)', dataIndex: 'analytical_power',
            render: (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`),
          },
          {
            title: 'Discrepancy', dataIndex: 'discrepancy_warning',
            render: (v: string | null) => (v ? <Tag color="warning">{v}</Tag> : '—'),
          },
        ]}
      />
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'ab-help',
            label: '❓ How do I read this table?',
            children: (
              <Typography.Paragraph>
                Empirical power is the share of A/B simulations (with the configured effect injected) that the method correctly
                flagged as significant. Analytical power is what the design-time formula predicted for the same effect and sample
                size. A gap larger than 5 percentage points between the two is flagged in the Discrepancy column — it usually means
                the analytical formula&apos;s assumptions don&apos;t hold for this data.
              </Typography.Paragraph>
            ),
          },
        ]}
      />
    </div>
  )
}
