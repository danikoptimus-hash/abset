import { Checkbox, Typography, Space, Tag } from 'antd'

// Part 2: per-column "Categorical" checkbox. String/bool columns are always
// categorical (locked on); numeric columns are editable — checked means "each
// value is its own stratum/segment" (months_ago ∈ {1,2,3,5}), unchecked means
// "binned as a continuum". Used in both Create and Edit dataset flows.
export function ColumnTypeEditor({
  columns,
  numericColumns,
  value,
  onChange,
  disabled,
}: {
  columns: string[]
  // Columns detected as numeric (from preview values). Non-numeric columns are
  // locked categorical; only numeric columns can be toggled.
  numericColumns: Set<string>
  value: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
}) {
  const set = new Set(value)
  const toggle = (col: string, checked: boolean) => {
    const next = new Set(value)
    if (checked) next.add(col)
    else next.delete(col)
    onChange(columns.filter((c) => next.has(c)))
  }

  return (
    <div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
        Categorical columns are stratified/segmented per value; unchecked numeric columns are binned into ranges.
        Check a numeric column (e.g. an integer code with a few values) to keep each value as its own group.
      </Typography.Paragraph>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        {columns.map((col) => {
          const isNumeric = numericColumns.has(col)
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
            </div>
          )
        })}
      </Space>
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
