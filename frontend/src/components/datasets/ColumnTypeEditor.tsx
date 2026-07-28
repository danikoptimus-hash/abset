import { Checkbox, Typography, Space, Tag, Button, Tooltip, Modal } from 'antd'
import { DeleteOutlined, UndoOutlined } from '@ant-design/icons'

// Part 2 (removable columns): per-column usage across experiments — drives the
// remove guard. 'unit' = the ID column (hard-blocked); 'metric'/'pre'/'stratum'
// = removable, but with a confirming warning naming the experiments.
export type ColumnUsage = Record<string, { experiment: string; role: string }[]>

const ROLE_LABEL: Record<string, string> = {
  unit: 'ID', metric: 'metric', pre: 'pre-period', stratum: 'stratum',
}

// Part 2: per-column "Categorical" checkbox + a per-column Remove action, with
// a "Removed columns" restore section. String/bool columns are always
// categorical (locked on); numeric columns are editable. Removing a column
// excludes it from the dataset everywhere (the physical file is never changed —
// exclusion is applied on read), and it can be restored below. Used in both
// Create and Edit dataset flows.
export function ColumnTypeEditor({
  columns,
  numericColumns,
  value,
  onChange,
  excluded,
  onExcludedChange,
  columnUsage,
  disabled,
}: {
  // The FULL universe of columns (visible + already-excluded) — the editor
  // splits them by `excluded` itself.
  columns: string[]
  // Columns detected as numeric (from preview values). Non-numeric columns are
  // locked categorical; only numeric columns can be toggled.
  numericColumns: Set<string>
  value: string[]
  onChange: (next: string[]) => void
  // Part 2: the exclusion list and its setter. Omit both to hide removal
  // entirely (categorical-only mode).
  excluded?: string[]
  onExcludedChange?: (next: string[]) => void
  // Part 2 (Edit only): usage per column — omit at creation (nothing uses a
  // brand-new dataset yet).
  columnUsage?: ColumnUsage
  disabled?: boolean
}) {
  const set = new Set(value)
  const removable = !!onExcludedChange
  const excludedList = excluded ?? []
  const excludedSet = new Set(excludedList)
  const visible = columns.filter((c) => !excludedSet.has(c))

  const toggle = (col: string, checked: boolean) => {
    const next = new Set(value)
    if (checked) next.add(col)
    else next.delete(col)
    onChange(columns.filter((c) => next.has(c)))
  }

  const usageOf = (col: string) => columnUsage?.[col] ?? []
  const isIdColumn = (col: string) => usageOf(col).some((u) => u.role === 'unit')

  const remove = (col: string) => {
    if (!onExcludedChange || isIdColumn(col)) return
    const refs = usageOf(col).filter((u) => u.role !== 'unit')
    const apply = () => onExcludedChange([...excludedList, col])
    if (refs.length > 0) {
      Modal.confirm({
        title: `Remove column "${col}"?`,
        content: (
          <>
            <div style={{ marginBottom: 8 }}>This column is used by existing experiments:</div>
            <ul style={{ marginTop: 0 }}>
              {refs.map((u, i) => (
                <li key={i}>
                  used as {ROLE_LABEL[u.role] ?? u.role} in: <strong>{u.experiment}</strong>
                </li>
              ))}
            </ul>
            <div>Removing it will make re-analysis of those experiments fail until you restore it.</div>
          </>
        ),
        okText: 'Remove',
        okButtonProps: { danger: true },
        onOk: apply,
      })
      return
    }
    apply()
  }

  const restore = (col: string) => onExcludedChange?.(excludedList.filter((c) => c !== col))

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        Categorical columns are stratified/segmented per value; unchecked numeric columns are binned into ranges.
        {removable ? ' Remove a column to exclude it from this dataset everywhere — the file is not changed, and you can restore it below.' : ''}
      </Typography.Paragraph>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        {visible.map((col) => {
          const isNumeric = numericColumns.has(col)
          const idCol = isIdColumn(col)
          return (
            <div key={col} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Checkbox
                checked={set.has(col)}
                disabled={disabled || !isNumeric}
                onChange={(e) => toggle(col, e.target.checked)}
                aria-label={`categorical-${col}`}
              >
                {col}
              </Checkbox>
              {!isNumeric && <Tag color="default">text — always categorical</Tag>}
              {removable && (
                <Tooltip title={idCol ? 'ID column — used to join experiments, can\'t be removed' : 'Remove column'}>
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={disabled || idCol}
                    onClick={() => remove(col)}
                    aria-label={`remove-column-${col}`}
                    style={{ marginLeft: 'auto' }}
                  />
                </Tooltip>
              )}
            </div>
          )
        })}
      </Space>
      {removable && excludedList.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            Removed columns ({excludedList.length})
          </Typography.Text>
          <Space direction="vertical" size={4} style={{ width: '100%', marginTop: 4 }}>
            {excludedList.map((col) => (
              <div key={col} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Typography.Text delete type="secondary">{col}</Typography.Text>
                <Button
                  size="small"
                  type="link"
                  icon={<UndoOutlined />}
                  disabled={disabled}
                  onClick={() => restore(col)}
                  aria-label={`restore-column-${col}`}
                >
                  Restore
                </Button>
              </div>
            ))}
          </Space>
        </div>
      )}
    </div>
  )
}

// Infer which columns look numeric from preview rows (any non-null value is a
// JS number). Mirrors inferDtypes' numeric-vs-object split used elsewhere.
export function numericColumnsFromPreview(
  columns: string[],
  rows: Record<string, unknown>[],
): Set<string> {
  const out = new Set<string>()
  for (const col of columns) {
    const values = rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined)
    if (values.length > 0 && values.every((v) => typeof v === 'number')) out.add(col)
  }
  return out
}

// Client-side heuristic default for datasets with no stored flags yet (created
// before the feature): string/bool → categorical; numeric → categorical when
// the preview shows few distinct values. Backend is authoritative on save.
export function defaultCategoricalFromPreview(
  columns: string[],
  rows: Record<string, unknown>[],
  maxDistinct = 20,
): string[] {
  const numeric = numericColumnsFromPreview(columns, rows)
  return columns.filter((col) => {
    if (!numeric.has(col)) return true
    const distinct = new Set(rows.map((r) => r[col]).filter((v) => v !== null && v !== undefined))
    return distinct.size <= maxDistinct
  })
}
