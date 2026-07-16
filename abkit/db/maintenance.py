"""VACUUM + bloat-detection helpers (DB bloat package, item A2).

Plain `VACUUM (ANALYZE)` only — never `VACUUM FULL` (that needs an
ACCESS EXCLUSIVE lock on the table; staying a deliberate human decision,
run manually during a maintenance window, is the whole point of this
module's design — automation's job is making the NEED impossible to miss,
not taking the disruptive action itself). See CLAUDE.md/docs/OPERATIONS.md
troubleshooting row "Database size stays high after deleting many
experiments".

VACUUM cannot run inside a transaction block — Postgres rejects it. Every
other place in this codebase goes through `abkit.db.engine.session_scope()`,
which always wraps work in a transaction, so this module opens its own
AUTOCOMMIT connection instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from abkit.db.engine import get_engine
from abkit.logging_config import get_logger

log = get_logger("abkit.db.maintenance")

# Table names passed in here are always hardcoded call-site constants
# (never user input), but VACUUM doesn't support parameterized identifiers
# either way — validated before string interpolation as a defensive habit,
# not because untrusted input is actually expected to reach this function.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# Weekly bloat check thresholds (item A2): a table only gets flagged once
# it's BOTH proportionally bloated (dead_pct) AND big enough in absolute
# terms to matter (size_mb) — a 100%-dead 40kB table isn't worth a human's
# attention, matching what real-world bloat incidents look like (see the
# `assignments` table that motivated this whole package: 2+ GB from a
# handful of dev sessions).
BLOAT_DEAD_PCT_THRESHOLD = 30.0
BLOAT_SIZE_MB_THRESHOLD = 100.0


def vacuum_tables(table_names: list[str]) -> None:
    """Best-effort — logs and swallows errors per table. A lock-contention
    blip or transient connectivity issue while vacuuming must never fail
    the caller's actual deletion job (cleanup-dev / experiment delete),
    which has already committed its own work by the time this runs."""
    engine = get_engine()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name in table_names:
            if not _IDENTIFIER_RE.match(name):
                log.warning("vacuum_skipped_invalid_identifier", table=name)
                continue
            try:
                conn.exec_driver_sql(f'VACUUM (ANALYZE) "{name}"')
            except Exception:
                log.warning("vacuum_failed", table=name, exc_info=True)


@dataclass
class TableBloatInfo:
    table_name: str
    dead_pct: float
    size_mb: float


def _classify_bloat(rows: list[tuple]) -> list[TableBloatInfo]:
    """Pure decision logic (no DB access), separated from find_bloated_tables
    the same way abkit.monitoring.plan_retention is separated from the
    collector's own DB calls — lets tests exercise the threshold boundary
    (29% vs 31%, a small-but-100%-dead table vs a large-but-barely-over
    table) with plain tuples, no Postgres needed. rows: (relname, n_live_tup,
    n_dead_tup, size_bytes) — exactly pg_stat_user_tables' shape."""
    result: list[TableBloatInfo] = []
    for relname, n_live, n_dead, size_bytes in rows:
        total = (n_live or 0) + (n_dead or 0)
        dead_pct = (100.0 * (n_dead or 0) / total) if total > 0 else 0.0
        size_mb = (size_bytes or 0) / (1024 * 1024)
        if dead_pct > BLOAT_DEAD_PCT_THRESHOLD and size_mb > BLOAT_SIZE_MB_THRESHOLD:
            result.append(TableBloatInfo(table_name=relname, dead_pct=dead_pct, size_mb=size_mb))
    return result


def find_bloated_tables() -> list[TableBloatInfo]:
    """Read-only (safe inside a normal transaction, unlike vacuum_tables) —
    dead-tuple ratio > BLOAT_DEAD_PCT_THRESHOLD AND size >
    BLOAT_SIZE_MB_THRESHOLD (see _classify_bloat). Used both by the weekly
    log-warning check (MonitoringCollector.run_bloat_check) and live by the
    Monitoring API's /current endpoint (cheap system-catalog read, safe to
    run on every request — no reason to make an admin looking at the panel
    wait for the next weekly tick to see accurate bloat state)."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            """
            SELECT relname, n_live_tup, n_dead_tup, pg_total_relation_size(relid) AS size_bytes
            FROM pg_stat_user_tables
            """
        ).fetchall()
    return _classify_bloat(list(rows))
