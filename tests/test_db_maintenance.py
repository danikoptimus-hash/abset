"""abkit.db.maintenance (item A2, DB bloat package) — pure threshold logic
in _classify_bloat (no DB needed, same separation-of-concerns as
abkit.monitoring.plan_retention). vacuum_tables' actual VACUUM execution is
covered indirectly via tests/test_dev_cleanup.py and tests/test_audit_log.py
(mocked at the call site, exactly this module's public function)."""

from __future__ import annotations

from abkit.db.maintenance import (
    BLOAT_DEAD_PCT_THRESHOLD,
    BLOAT_SIZE_MB_THRESHOLD,
    _classify_bloat,
)

_MB = 1024 * 1024


def test_table_just_under_dead_pct_threshold_is_not_flagged():
    # 29% dead, comfortably over the size threshold.
    rows = [("assignments", 71, 29, 200 * _MB)]
    assert _classify_bloat(rows) == []


def test_table_just_over_dead_pct_threshold_is_flagged():
    # 31% dead, comfortably over the size threshold.
    rows = [("assignments", 69, 31, 200 * _MB)]
    result = _classify_bloat(rows)
    assert len(result) == 1
    assert result[0].table_name == "assignments"
    assert result[0].dead_pct == 31.0


def test_exactly_at_dead_pct_threshold_is_not_flagged():
    # Boundary is exclusive (`>`, not `>=`) — exactly 30% doesn't count.
    rows = [("assignments", 70, 30, 200 * _MB)]
    assert _classify_bloat(rows) == []


def test_small_table_fully_dead_is_not_flagged_despite_100_percent():
    # 100% dead but tiny (40kB) — not worth a human's attention, matches
    # the tiny administrative tables (database_connections, datasets, etc.)
    # observed in the real incident that motivated this package.
    rows = [("database_connections", 0, 26, 40 * 1024)]
    assert _classify_bloat(rows) == []


def test_large_table_with_low_dead_pct_is_not_flagged():
    # A big table (500MB) that's mostly live rows (5% dead) is healthy,
    # not bloated — size alone doesn't trigger the hint.
    rows = [("assignments", 950_000, 50_000, 500 * _MB)]
    assert _classify_bloat(rows) == []


def test_large_bloated_table_is_flagged_with_correct_values():
    # The actual incident shape: assignments at ~2GB with heavy churn.
    rows = [("assignments", 63_673, 24_000_000, 2183 * _MB)]
    result = _classify_bloat(rows)
    assert len(result) == 1
    info = result[0]
    assert info.table_name == "assignments"
    assert info.size_mb == 2183.0
    assert info.dead_pct > BLOAT_DEAD_PCT_THRESHOLD


def test_zero_rows_table_does_not_divide_by_zero():
    rows = [("empty_table", 0, 0, 500 * _MB)]
    assert _classify_bloat(rows) == []


def test_multiple_tables_only_bloated_ones_returned():
    rows = [
        ("healthy_big", 900_000, 10_000, 300 * _MB),
        ("bloated_big", 10_000, 900_000, 400 * _MB),
        ("bloated_but_small", 10, 990, 1 * _MB),
    ]
    result = _classify_bloat(rows)
    assert [r.table_name for r in result] == ["bloated_big"]


def test_thresholds_are_the_documented_values():
    # Guards against silent threshold drift — the spec (item A2) is exact.
    assert BLOAT_DEAD_PCT_THRESHOLD == 30.0
    assert BLOAT_SIZE_MB_THRESHOLD == 100.0
