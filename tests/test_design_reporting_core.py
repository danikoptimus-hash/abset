"""Чистая логика пакета design & reporting fixes — без БД и без HTTP.

Здесь: подписи метрик (A1), формулировки исхода изоляции (C3), форматирование
абсолютного MDE по стратам (C4), границы авто-завершения (B3). Всё, что требует
Postgres/роутеров — backend/tests/test_design_reporting_package.py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from abkit.config import DesignConfig, MetricConfig, metric_label, metric_labels_by_name
from abkit.experiment import _build_isolation_decision, compute_strata_power_rows
from abkit.design.isolation import IsolationResult
from abkit.lifecycle import auto_completion_cutoff, planned_end_reached
from abkit.viz.report import format_stratum_mde_abs, isolation_disclosure


# ---------------------------------------------------------------------------
# A1 — подпись метрики
# ---------------------------------------------------------------------------


def test_a1_metric_label_falls_back_to_the_column_name():
    assert metric_label(MetricConfig(name="txn_sum", type="continuous")) == "txn_sum"
    assert (
        metric_label(MetricConfig(name="txn_sum", type="continuous", display_name="Revenue"))
        == "Revenue"
    )


def test_a1_blank_display_name_is_treated_as_unset():
    """Форма визарда отдает "" за неотредактированное поле — подпись "" была бы
    хуже любого имени колонки."""
    assert metric_label(MetricConfig(name="txn_sum", type="continuous", display_name="")) == "txn_sum"
    assert metric_label(MetricConfig(name="txn_sum", type="continuous", display_name="   ")) == "txn_sum"
    assert (
        metric_label(MetricConfig(name="txn_sum", type="continuous", display_name="  Revenue "))
        == "Revenue"
    )


def test_a1_labels_by_name_maps_keys_to_labels():
    labels = metric_labels_by_name(
        [
            MetricConfig(name="txn_sum", type="continuous", display_name="Revenue"),
            MetricConfig(name="clicks", type="binary"),
        ]
    )
    assert labels == {"txn_sum": "Revenue", "clicks": "clicks"}


def test_a1_display_name_does_not_have_to_be_unique():
    """В отличие от name (он же колонка и ключ) — две метрики вправе читаться
    одинаково, различаясь колонкой."""
    config = DesignConfig(
        name="dup",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[
            MetricConfig(name="rev_a", type="continuous", display_name="Revenue"),
            MetricConfig(name="rev_b", type="continuous", display_name="Revenue"),
        ],
    )
    assert [m.display_name for m in config.metrics] == ["Revenue", "Revenue"]


# ---------------------------------------------------------------------------
# C3 — формулировка исхода изоляции
# ---------------------------------------------------------------------------


def _isolation(n_excluded: int, by_experiment: dict[str, int], mode: str) -> dict:
    return _build_isolation_decision(
        IsolationResult(
            candidates=pd.DataFrame({"user_id": []}),
            excluded_by_experiment=by_experiment,
            n_before=1000,
            n_excluded=n_excluded,
            n_available=1000 - n_excluded,
            mode=mode,
        ),
        mode,
    )


def test_c3_decision_none_when_nothing_overlapped():
    decision = _isolation(0, {}, "exclude")
    assert decision["decision"] == "none"
    assert decision["n_overlap"] == 0
    assert decision["checked"] is True


def test_c3_isolation_off_is_recorded_as_not_checked():
    """mode="off" вообще не считает пересечение — сказать "его не было" было бы
    неправдой, поэтому есть отдельный флаг."""
    decision = _isolation(0, {}, "off")
    assert decision["decision"] == "none"
    assert decision["checked"] is False


def test_c3_decision_excluded_counts_actually_removed_users():
    decision = _isolation(120, {"other": 120}, "exclude")
    assert decision["decision"] == "excluded"
    assert decision["n_overlap"] == 120


def test_c3_decision_proceeded_counts_the_overlap_not_the_zero_removed():
    """У "продолжили несмотря на" исключено НОЛЬ — но отчету нужен размер
    пересечения, иначе строка была бы "proceeded despite 0"."""
    decision = _isolation(0, {"other": 37, "another": 5}, "warn")
    assert decision["decision"] == "proceeded"
    assert decision["n_overlap"] == 42


def test_c3_disclosure_wording_matches_the_three_required_outcomes():
    assert isolation_disclosure(
        {"isolation_decision": {"decision": "none", "n_overlap": 0, "by_experiment": {}}}
    )["text"] == "No overlap with other active experiments."

    excluded = isolation_disclosure(
        {"isolation_decision": {"decision": "excluded", "n_overlap": 120, "by_experiment": {"x": 120}}}
    )
    assert "Excluded 120 overlapping users" in excluded["text"]
    assert excluded["level"] == "ok"

    proceeded = isolation_disclosure(
        {"isolation_decision": {"decision": "proceeded", "n_overlap": 37, "by_experiment": {"x": 37}}}
    )
    assert "Proceeded despite 37 overlapping users" in proceeded["text"]
    assert proceeded["level"] == "warn"


def test_c3_disclosure_is_none_without_any_isolation_information():
    assert isolation_disclosure(None) is None
    assert isolation_disclosure({}) is None
    assert isolation_disclosure({"excluded_by_experiment": {}, "n_excluded_by_isolation": 0}) is None


def test_c3_disclosure_reconstructs_pre_feature_designs():
    """Старые дизайны решения не записывали, но числа хранили всегда."""
    excluded = isolation_disclosure(
        {"excluded_by_experiment": {"older": 50}, "n_excluded_by_isolation": 50}
    )
    assert excluded["decision"] == "excluded"
    proceeded = isolation_disclosure(
        {"excluded_by_experiment": {"older": 50}, "n_excluded_by_isolation": 0}
    )
    assert proceeded["decision"] == "proceeded"


# ---------------------------------------------------------------------------
# C4 — абсолютный MDE по стратам
# ---------------------------------------------------------------------------


def test_c4_absolute_mde_formats_binary_as_percentage_points():
    """Тот же контракт единиц, что у общей таблицы MDE: baseline binary-метрики
    сам по себе доля, поэтому сырые единицы читались бы как 0.0123."""
    assert format_stratum_mde_abs({"mde_abs": 0.01234, "metric_type": "binary"}) == "1.234 pp"


def test_c4_absolute_mde_formats_continuous_in_metric_units():
    assert format_stratum_mde_abs({"mde_abs": 12.3456, "metric_type": "continuous"}) == "12.346"


def test_c4_absolute_mde_is_a_dash_when_there_is_nothing_to_show():
    # Строка сохранена до item C4 / страта слишком мала / тип неизвестен.
    assert format_stratum_mde_abs({}) == "—"
    assert format_stratum_mde_abs({"mde_abs": None, "metric_type": "binary"}) == "—"
    # Неизвестный тип — не binary, значит единицы метрики (не выдумываем pp).
    assert format_stratum_mde_abs({"mde_abs": 1.5}) == "1.500"


def test_c4_strata_power_rows_carry_absolute_mde_consistent_with_relative():
    rng = pd.Series(range(400))
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(400)],
            "revenue": (rng % 50 + 10).astype(float),
            "country": ["ru" if i % 2 else "us" for i in range(400)],
        }
    )
    rows = compute_strata_power_rows(
        data,
        control_name="control",
        groups={"control": 0.5, "treatment": 0.5},
        primary_metrics=[MetricConfig(name="revenue", type="continuous")],
        strata_cols=["country"],
        overall_mde_rel={"revenue": 0.05},
        alpha=0.05,
        power_target=0.8,
    )
    flat = [r for rs in rows.values() for r in rs]
    assert flat
    priced = [r for r in flat if r.mde_abs is not None]
    assert priced, "expected at least one stratum with a computable MDE"
    for row in priced:
        assert row.metric_type == "continuous"
        assert row.baseline_mean is not None
        # abs = rel × baseline — то же соотношение, что в общей таблице MDE.
        assert abs(row.mde_abs - row.mde_rel * row.baseline_mean) < 1e-9


# ---------------------------------------------------------------------------
# B3 — когда именно "плановая дата наступила"
# ---------------------------------------------------------------------------


def test_b3_planned_end_includes_the_named_day_itself():
    """Пользователь, поставивший 20-е, имеет в виду "тест идет ПО 20-е
    включительно" — авто-завершение в 00:00 20-го отрезало бы целый день."""
    on_the_day = datetime(2030, 5, 20, 12, 0, tzinfo=timezone.utc)
    assert planned_end_reached(date(2030, 5, 20), now=on_the_day) is False
    # Наступила полночь 21-го — день закончился.
    assert planned_end_reached(date(2030, 5, 20), now=datetime(2030, 5, 21, 0, 5, tzinfo=timezone.utc)) is True


def test_b3_past_and_future_dates():
    now = datetime(2030, 5, 20, 12, 0, tzinfo=timezone.utc)
    assert planned_end_reached(date(2030, 5, 1), now=now) is True
    assert planned_end_reached(date(2030, 6, 1), now=now) is False


def test_b3_no_planned_end_date_never_triggers():
    assert planned_end_reached(None) is False


def test_b3_cutoff_is_yesterday_utc():
    assert auto_completion_cutoff(datetime(2030, 5, 20, 0, 1, tzinfo=timezone.utc)) == date(2030, 5, 19)


def test_b3_naive_datetime_is_treated_as_utc():
    """Защита от вызова с naive datetime — падать на tz-сравнении посреди
    фонового прохода было бы худшим из возможных вариантов."""
    assert auto_completion_cutoff(datetime(2030, 5, 20, 0, 1)) == date(2030, 5, 19)
