from __future__ import annotations

import re

# Simple, deliberately-limited "FROM [schema.]table" parse — mirrors
# frontend/src/components/datasets/parseSchemaTableFromSql.ts (kept in sync
# manually; there is no shared implementation across the Python/TS boundary).
# Used only by migration 0010's one-time backfill of datasets.source_schema/
# source_table for pre-existing rows created before those columns existed —
# JOIN/CTE/unqualified-table queries are left unparsed rather than guessed
# at, same as the frontend twin.
_IDENT = r'(?:"([^"]+)"|(\w+))'
_FROM_RE = re.compile(rf"\bFROM\s+{_IDENT}\.{_IDENT}", re.IGNORECASE)
_FROM_TOKEN_RE = re.compile(r"\bFROM\b", re.IGNORECASE)


def parse_schema_table_from_sql(sql: str) -> tuple[str | None, str | None]:
    if not sql or not sql.strip():
        return None, None
    # CTE (WITH) and subqueries (more than one FROM) are exactly the cases
    # this simple parse is meant to bail out of, not guess at — a regex
    # match against the first FROM it finds would otherwise happily grab the
    # table from inside a CTE/subquery instead of the actual outer query.
    if re.search(r"\bWITH\b", sql, re.IGNORECASE) or re.search(r"\bJOIN\b", sql, re.IGNORECASE):
        return None, None
    if len(_FROM_TOKEN_RE.findall(sql)) != 1:
        return None, None
    m = _FROM_RE.search(sql)
    if not m:
        return None, None
    schema = m.group(1) or m.group(2)
    table = m.group(3) or m.group(4)
    if not schema or not table:
        return None, None
    return schema, table
