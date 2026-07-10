// Edit dataset modal (UX package, Datasets §1.2) — best-effort prefill for
// the schema/table selects from a saved `sql_text`, used only as a fallback
// when the dataset has no persisted source_schema/source_table (older rows,
// or ones created from genuinely hand-written SQL — see CLAUDE.md's
// "Edit/Delete датасета" section). Deliberately only handles the simple
// "FROM schema.table" shape (bare or double-quoted identifiers); a CTE
// (WITH), a JOIN, or a subquery (more than one FROM) is left unparsed rather
// than guessed at — a naive regex would otherwise happily grab the table
// from inside a CTE/subquery instead of the actual outer query.
const IDENT = '(?:"([^"]+)"|(\\w+))'
// No trailing \b: after a quoted identifier the last char is `"`, a
// non-word char, so \b would never match there (word boundary requires a
// word/non-word transition) — the alternation's own delimiters (quotes, or
// \w+'s own greediness) already bound the match without it.
const FROM_SCHEMA_TABLE_RE = new RegExp(`\\bFROM\\s+${IDENT}\\.${IDENT}`, 'i')
const FROM_TOKEN_RE = /\bFROM\b/gi

export function parseSchemaTableFromSql(sql: string): { schema?: string; table?: string } {
  if (!sql.trim() || /\bWITH\b/i.test(sql) || /\bJOIN\b/i.test(sql)) return {}
  if ((sql.match(FROM_TOKEN_RE) ?? []).length !== 1) return {}
  const match = sql.match(FROM_SCHEMA_TABLE_RE)
  if (!match) return {}
  const schema = match[1] ?? match[2]
  const table = match[3] ?? match[4]
  if (!schema || !table) return {}
  return { schema, table }
}

// Inverse of the above — the exact SQL a schema/table selection would
// generate. Used both to fill the SQL box on selection and (Datasets
// follow-up: persist source schema/table) to decide, at submit time,
// whether the current SQL box content still exactly matches the current
// cascade selection — if it doesn't, the selection is stale/hand-edited and
// source_schema/source_table must NOT be sent as if it still applied.
export function buildSelectAllSql(schema: string, table: string): string {
  return `SELECT * FROM "${schema}"."${table}"`
}
