import { Typography, Input, InputNumber, Button, Select, Space, Alert, Card, Tag } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { GROUP_PRESETS } from './helpTexts'
import { numericColumns, nextId, groupsSum } from './types'
import type { WizardState, MetricFormRow } from './types'

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
}

export function Step2GroupsMetrics({ state, setState }: Props) {
  const numeric = numericColumns(state)
  const numericOptions = [{ value: '__none__', label: '(none)' }, ...numeric.map((c) => ({ value: c, label: c }))]
  const sum = groupsSum(state)
  const sumOk = Math.abs(sum - 1) < 1e-6

  const applyPreset = (preset: string) => {
    const groups = GROUP_PRESETS[preset]
    setState((prev) => ({
      ...prev,
      groups: Object.entries(groups).map(([name, prop]) => ({ id: nextId('group'), name, prop })),
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

  return (
    <div>
      <Typography.Title level={5}>Groups</Typography.Title>
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

      {state.groups.map((g) => (
        <Space key={g.id} style={{ display: 'flex', marginBottom: 8 }}>
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
          <Button
            icon={<DeleteOutlined />}
            onClick={() => setState((prev) => ({ ...prev, groups: prev.groups.filter((x) => x.id !== g.id) }))}
          />
        </Space>
      ))}
      <Button
        icon={<PlusOutlined />}
        onClick={() => setState((prev) => ({ ...prev, groups: [...prev.groups, { id: nextId('group'), name: '', prop: 0 }] }))}
        style={{ marginBottom: 12 }}
      >
        Add Group
      </Button>
      <Alert
        type={sumOk ? 'success' : 'warning'}
        showIcon
        message={`Sum of proportions: ${sum.toFixed(3)}${sumOk ? '' : ' — must equal 1'}`}
        style={{ marginBottom: 24 }}
      />

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
            {m.type === 'ratio' ? (
              <Input
                placeholder="Metric name (label), e.g. conv_rate"
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
            </Space>
          ) : (
            <div>
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
