import { Typography, Input, InputNumber, Button, Select, Space, Alert, Card, Tag } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { GROUP_PRESETS } from './helpTexts'
import { FlowImagesSection } from './FlowImagesSection'
import { numericColumns, nextId, groupsSum, equalSplitGroups } from './types'
import type { WizardState, MetricFormRow } from './types'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
}

export function Step2GroupsMetrics({ state, setState }: Props) {
  const isExternal = state.splitMode === 'external'
  // External split rework: metric/pre-period/num/den become searchable column
  // pickers whenever a dataset's columns are known — always for the abkit
  // path, and for external ONLY when a reference dataset was selected on Step
  // 1 (otherwise external stays free-text, since there are no columns to
  // pick from). This replaces the old "external ⇒ always free-text" rule.
  const hasColumns = state.columns.length > 0
  const numeric = numericColumns(state)
  const numericOptions = [{ value: '__none__', label: '(none)' }, ...numeric.map((c) => ({ value: c, label: c }))]
  const sum = groupsSum(state)
  const sumOk = Math.abs(sum - 1) < 1e-6

  const applyPreset = (preset: string) => {
    const groups = GROUP_PRESETS[preset]
    setState((prev) => ({
      ...prev,
      groups: Object.entries(groups).map(([name, prop]) => ({ id: nextId('group'), name, prop, description: '' })),
    }))
  }

  const normalize = () => {
    setState((prev) => {
      const total = groupsSum(prev)
      if (total <= 0) return prev
      return { ...prev, groups: prev.groups.map((g) => ({ ...g, prop: g.prop / total })) }
    })
  }

  const binaryLikeColumns = state.columns.filter((c) => {
    const values = state.previewRows.map((r) => r[c]).filter((v) => v !== null && v !== undefined)
    return values.length > 0 && values.every((v) => v === 0 || v === 1 || v === true || v === false)
  })

  // Item 3 (sample-size-first flow): for the abkit-split path, proportions
  // move to the Parameters step (after "Calculate sample size") — this
  // step only collects group NAMES/descriptions now. Adding/removing a
  // group re-equalizes props immediately so state.groups stays numerically
  // valid (sums to 1) at all times even though nothing here edits `prop`
  // directly. External split is untouched (item 3.3): there's no dataset
  // to calculate a sample size from, so proportions are still entered
  // directly here, exactly as before this package.
  const addGroup = () =>
    setState((prev) => {
      const withNew = [...prev.groups, { id: nextId('group'), name: '', prop: 0, description: '' }]
      return { ...prev, groups: isExternal ? withNew : equalSplitGroups(withNew) }
    })
  const removeGroup = (id: string) =>
    setState((prev) => {
      const remaining = prev.groups.filter((x) => x.id !== id)
      return { ...prev, groups: isExternal ? remaining : equalSplitGroups(remaining) }
    })

  return (
    <div>
      <Typography.Title level={5}>Groups</Typography.Title>
      {isExternal && (
        <Space style={{ marginBottom: 12 }}>
          {Object.keys(GROUP_PRESETS).map((preset) => (
            <Button key={preset} size="small" onClick={() => applyPreset(preset)}>
              {preset}
            </Button>
          ))}
          <Button size="small" onClick={normalize}>
            Normalize
          </Button>
        </Space>
      )}

      {state.groups.map((g) => (
        <div key={g.id} style={{ marginBottom: 12 }}>
          <Space style={{ display: 'flex', marginBottom: 4 }}>
            <Input
              placeholder="Group name"
              value={g.name}
              style={{ width: 200 }}
              onChange={(e) =>
                setState((prev) => ({
                  ...prev,
                  groups: prev.groups.map((x) => (x.id === g.id ? { ...x, name: e.target.value } : x)),
                }))
              }
            />
            {isExternal && (
              <InputNumber
                min={0}
                max={1}
                step={0.05}
                value={g.prop}
                onChange={(v) =>
                  setState((prev) => ({
                    ...prev,
                    groups: prev.groups.map((x) => (x.id === g.id ? { ...x, prop: v ?? 0 } : x)),
                  }))
                }
              />
            )}
            <Button icon={<DeleteOutlined />} onClick={() => removeGroup(g.id)} />
          </Space>
          <Input.TextArea
            placeholder="What does this variant show/do? (optional)"
            value={g.description}
            rows={2}
            style={{ width: 460 }}
            onChange={(e) =>
              setState((prev) => ({
                ...prev,
                groups: prev.groups.map((x) => (x.id === g.id ? { ...x, description: e.target.value } : x)),
              }))
            }
          />
        </div>
      ))}
      <Button icon={<PlusOutlined />} onClick={addGroup} style={{ marginBottom: 12 }}>
        Add Group
      </Button>
      {isExternal ? (
        <Alert
          type={sumOk ? 'success' : 'warning'}
          showIcon
          message={`Sum of proportions: ${sum.toFixed(3)}${sumOk ? '' : ' — must equal 1'}`}
          style={{ marginBottom: 24 }}
        />
      ) : (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, maxWidth: 500, marginBottom: 24 }}>
          Group sizes (proportions) are set on the next step, after calculating the required sample size.
        </Typography.Paragraph>
      )}

      <FlowImagesSection state={state} setState={setState} />

      <Typography.Title level={5}>Metrics (at least one)</Typography.Title>
      {state.metrics.map((m) => (
        <Card key={m.id} size="small" style={{ marginBottom: 12 }}>
          <Space wrap style={{ marginBottom: 8 }}>
            <Select
              style={{ width: 140 }}
              value={m.type}
              onChange={(type) => updateMetric(setState, m.id, { type })}
              options={[
                { value: 'continuous', label: 'continuous' },
                { value: 'binary', label: 'binary' },
                { value: 'ratio', label: 'ratio' },
              ]}
            />
            {m.type === 'ratio' || !hasColumns ? (
              <Input
                placeholder={
                  m.type === 'ratio' ? 'Metric name (label), e.g. conv_rate' : 'Data column name, e.g. conversion'
                }
                style={{ width: 220 }}
                value={m.name}
                onChange={(e) => updateMetric(setState, m.id, { name: e.target.value })}
              />
            ) : (
              <Select
                placeholder="Dataframe column"
                style={{ width: 220 }}
                value={m.name || undefined}
                onChange={(name) => updateMetric(setState, m.id, { name })}
                options={state.columns.map((c) => ({ value: c, label: c }))}
              />
            )}
            <Select
              style={{ width: 140 }}
              value={m.role}
              onChange={(role) => updateMetric(setState, m.id, { role })}
              options={[
                { value: 'primary', label: 'primary' },
                { value: 'secondary', label: 'secondary' },
              ]}
            />
            <Button
              icon={<DeleteOutlined />}
              onClick={() => setState((prev) => ({ ...prev, metrics: prev.metrics.filter((x) => x.id !== m.id) }))}
            />
          </Space>

          {m.type === 'ratio' ? (
            <Space>
              {!hasColumns ? (
                <>
                  <Input
                    placeholder="Numerator column (num)"
                    style={{ width: 200 }}
                    value={m.num ?? ''}
                    onChange={(e) => updateMetric(setState, m.id, { num: e.target.value || null })}
                  />
                  <Input
                    placeholder="Denominator column (den)"
                    style={{ width: 200 }}
                    value={m.den ?? ''}
                    onChange={(e) => updateMetric(setState, m.id, { den: e.target.value || null })}
                  />
                </>
              ) : (
                <>
                  <Select
                    placeholder="Numerator (num)"
                    style={{ width: 200 }}
                    value={m.num ?? '__none__'}
                    onChange={(v) => updateMetric(setState, m.id, { num: v === '__none__' ? null : v })}
                    options={numericOptions}
                  />
                  <Select
                    placeholder="Denominator (den)"
                    style={{ width: 200 }}
                    value={m.den ?? '__none__'}
                    onChange={(v) => updateMetric(setState, m.id, { den: v === '__none__' ? null : v })}
                    options={numericOptions}
                  />
                </>
              )}
            </Space>
          ) : (
            <div>
              {!hasColumns ? (
                <Input
                  placeholder="Pre-period column (for CUPED, optional)"
                  style={{ width: 320 }}
                  value={m.preCol ?? ''}
                  onChange={(e) => updateMetric(setState, m.id, { preCol: e.target.value || null })}
                />
              ) : (
                <>
                  <Select
                    placeholder="pre-period column (for CUPED, optional)"
                    style={{ width: 320 }}
                    value={m.preCol ?? '__none__'}
                    onChange={(v) => updateMetric(setState, m.id, { preCol: v === '__none__' ? null : v })}
                    options={numericOptions}
                  />
                  {m.type === 'binary' && (
                    <div style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary">Suitable 0/1 columns: </Typography.Text>
                      {binaryLikeColumns.length ? (
                        binaryLikeColumns.map((c) => <Tag key={c}>{c}</Tag>)
                      ) : (
                        <Typography.Text type="secondary">none found</Typography.Text>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </Card>
      ))}
      <Button
        icon={<PlusOutlined />}
        onClick={() =>
          setState((prev) => ({
            ...prev,
            metrics: [
              ...prev.metrics,
              { id: nextId('metric'), name: '', type: 'continuous', role: 'primary', preCol: null, num: null, den: null },
            ],
          }))
        }
      >
        Add Metric
      </Button>
    </div>
  )
}

function updateMetric(
  setState: Props['setState'],
  id: string,
  patch: Partial<MetricFormRow>,
) {
  setState((prev) => ({
    ...prev,
    metrics: prev.metrics.map((x) => (x.id === id ? { ...x, ...patch } : x)),
  }))
}
