import { useState } from 'react'
import { Button, Collapse, Table, Typography, Alert, Space, Spin, Radio } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router-dom'
import { apiClient, errorMessage } from '../../api/client'
import { DatasetSelect } from '../../components/DatasetSelect'
import {
  DESIGN_EXAMPLE_ROWS,
  DESIGN_SQL_EXAMPLE,
  WHAT_IS_THIS_DATA,
  EXAMPLE_EXPLANATION,
  SQL_EXPLANATION,
  NO_DATA_EXPLANATION,
} from './helpTexts'
import type { WizardState, DesignConfig } from './types'
import { groupsFromApi, metricsFromApi } from './types'
import { PRODUCT_NAME } from '../../branding'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
  // Redesign always targets an existing ABSet-split experiment (external
  // ones don't offer a Redesign action at all) — locked so a redesign
  // can't accidentally switch modes mid-flow.
  lockSplitMode?: boolean
}

// Preview rows carry only JSON-primitive values — dtypes aren't persisted
// for existing datasets (only computed once, at upload time), so we infer a
// light "numeric vs. not" split from the preview's JS value types, same
// trick already used for demo data below. Good enough for step 2/3's
// numeric-column selects (pre_col/num/den).
function inferDtypes(previewRows: Record<string, unknown>[]): Record<string, string> {
  const dtypes: Record<string, string> = {}
  for (const [key, value] of Object.entries(previewRows[0] ?? {})) {
    dtypes[key] = typeof value === 'number' ? 'float64' : 'object'
  }
  return dtypes
}

export function Step1Data({ state, setState, lockSplitMode }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isExternal = state.splitMode === 'external'

  const applyDatasetResult = (
    datasetId: string,
    columns: string[],
    dtypes: Record<string, string>,
    nRows: number,
    previewRows: Record<string, unknown>[],
  ) => {
    setState((prev) => ({ ...prev, datasetId, columns, dtypes, nRows, previewRows }))
  }

  const fetchPreview = async (datasetId: string) => {
    const { data } = await apiClient.GET('/api/v1/datasets/{dataset_id}/preview', {
      params: { path: { dataset_id: datasetId }, query: { rows: 20 } },
    })
    return data?.rows ?? []
  }

  const handleSelectDataset = async (datasetId: string) => {
    setLoading(true)
    setError(null)
    try {
      const { data, error } = await apiClient.GET('/api/v1/datasets', { params: { query: { page_size: 200 } } })
      if (error) throw new Error(errorMessage(error))
      const chosen = data.items.find((d) => d.id === datasetId)
      if (!chosen) throw new Error('Dataset not found')
      const previewRows = await fetchPreview(datasetId)
      applyDatasetResult(datasetId, chosen.columns, inferDtypes(previewRows), chosen.n_rows, previewRows)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dataset')
    } finally {
      setLoading(false)
    }
  }

  const handleDemoData = async () => {
    setLoading(true)
    setError(null)
    try {
      const { data, error } = await apiClient.POST('/api/v1/datasets/demo-design')
      if (error) throw new Error(errorMessage(error))
      const previewRows = await fetchPreview(data.dataset_id)
      // suggested_config приходит как dict[str, Any] (Record<string, unknown>
      // в сгенерированных типах) — форма гарантированно DesignConfig, кастуем.
      const config = data.suggested_config as unknown as DesignConfig

      setState((prev) => ({
        ...prev,
        datasetId: data.dataset_id,
        columns: Object.keys(previewRows[0] ?? {}),
        dtypes: inferDtypes(previewRows),
        nRows: 5000,
        previewRows,
        name: config.name,
        unitCol: config.unit_col,
        groups: groupsFromApi(config.groups),
        metrics: metricsFromApi(config.metrics),
        strata: config.strata ?? [],
        splitMethod: config.split_method,
        sizeMode: config.sample_size ? 'sample_size' : 'all',
        sampleSize: config.sample_size ?? prev.sampleSize,
      }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate demo data')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <Typography.Title level={5}>Split mode</Typography.Title>
      <Radio.Group
        value={state.splitMode}
        disabled={lockSplitMode}
        onChange={(e) => setState((prev) => ({ ...prev, splitMode: e.target.value }))}
        style={{ marginBottom: 24 }}
      >
        <Radio.Button value="abkit">{PRODUCT_NAME} split</Radio.Button>
        <Radio.Button value="external">External split (e.g. Firebase)</Radio.Button>
      </Radio.Group>

      {isExternal ? (
        <Alert
          type="info"
          showIcon
          message="No dataset needed for an external split"
          description={`The split already happens in an outside system (Firebase A/B Testing and similar) — ${PRODUCT_NAME} is only used to analyze the results. Declare your groups, metrics, and hypothesis on the next steps; you'll map the actual split to real data when you run the analysis.`}
          style={{ maxWidth: 640 }}
        />
      ) : (
        <>
          <Typography.Title level={5}>Select data about your candidate users</Typography.Title>

          <Collapse
        ghost
        style={{ marginBottom: 16 }}
        items={[
          {
            key: 'what',
            label: '❓ What is this data and what should it contain',
            // Тексты — markdown (**bold**, списки, `code`); рендерим через тот
            // же react-markdown, что и HelpCollapse/MarkdownBlockView (пакет
            // UI-фиксов, item 3). Раньше был <Paragraph pre-line> — markdown
            // показывался сырым (литеральные ** и дефисы-как-списки).
            children: <ReactMarkdown>{WHAT_IS_THIS_DATA}</ReactMarkdown>,
          },
          {
            key: 'example',
            label: '📊 Example: what the data should look like',
            children: (
              <>
                <Table
                  size="small"
                  dataSource={DESIGN_EXAMPLE_ROWS}
                  rowKey="user_id"
                  pagination={false}
                  columns={Object.keys(DESIGN_EXAMPLE_ROWS[0]).map((k) => ({ title: k, dataIndex: k }))}
                  style={{ marginBottom: 12 }}
                />
                <ReactMarkdown>{EXAMPLE_EXPLANATION}</ReactMarkdown>
              </>
            ),
          },
          {
            key: 'sql',
            label: '💡 How to export data from a DB (SQL example)',
            children: (
              <>
                <Typography.Paragraph code style={{ whiteSpace: 'pre' }}>
                  {DESIGN_SQL_EXAMPLE}
                </Typography.Paragraph>
                {/* DESIGN_SQL_EXAMPLE выше — намеренный код-блок (не markdown).
                    SQL_EXPLANATION — markdown с inline `code`, поэтому через
                    react-markdown. */}
                <ReactMarkdown>{SQL_EXPLANATION}</ReactMarkdown>
              </>
            ),
          },
          {
            key: 'nodata',
            label: "❓ No data on hand — I just want to try it out",
            children: <ReactMarkdown>{NO_DATA_EXPLANATION}</ReactMarkdown>,
          },
        ]}
      />

      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}

      <Space align="start" size={16} style={{ width: '100%' }}>
        <DatasetSelect
          value={state.datasetId ?? undefined}
          onChange={handleSelectDataset}
          disabled={loading}
          style={{ width: 420 }}
          ariaLabel="design-dataset-select"
        />
        <Button icon={<ThunderboltOutlined />} onClick={handleDemoData} loading={loading}>
          Demo Data
        </Button>
      </Space>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
        Don't see your data? <Link to="/datasets" target="_blank">Create a new dataset on the Datasets page</Link> (upload a
        file or pull it from a database connection), then come back and select it here.
      </Typography.Paragraph>

      {loading && (
        <div style={{ marginTop: 16 }}>
          <Spin /> Processing data...
        </div>
      )}

      {state.datasetId && state.previewRows.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <Alert
            type="success"
            showIcon
            message={`Data loaded: ${state.nRows} rows, ${Object.keys(state.previewRows[0]).length} columns`}
            style={{ marginBottom: 12 }}
          />
          <Table
            size="small"
            dataSource={state.previewRows}
            rowKey={(_, i) => String(i)}
            pagination={false}
            scroll={{ x: true }}
            columns={Object.keys(state.previewRows[0]).map((k) => ({ title: k, dataIndex: k }))}
          />
        </div>
      )}
        </>
      )}
    </div>
  )
}
