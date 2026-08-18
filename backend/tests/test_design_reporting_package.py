"""Пакет design & reporting fixes: A1-A5 / B1-B3 / C1-C4 — против реальной БД.

Разделение с tests/: здесь всё, что требует HTTP-слоя и Postgres (выгрузки,
права, авто-завершение, Edit Properties); чистая логика ядра (форматирование
абсолютного MDE, формулировки изоляции) — в tests/test_design_reporting_core.py.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

from abkit.auth.passwords import hash_password
from abkit.db.repositories import AuditRepo, ExperimentRepo, UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})


def _upload(app_client, csv_text: str, filename: str = "data.csv") -> str:
    resp = app_client.post(
        "/api/v1/datasets",
        data={"kind": "pre_design"},
        files={"file": (filename, csv_text, "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _poll(app_client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = app_client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in ("pending", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not finish within {timeout}s")


# ID-колонка НЕ называется unit_id — в этом весь смысл item C1.
def _csv(n: int = 200, id_col: str = "client_id") -> str:
    # `converted` — настоящая 0/1 колонка: binary-метрика считает по ней
    # среднее, и строковая колонка (country) на этом месте просто упала бы.
    lines = [f"{id_col},revenue,converted,country"] + [
        f"c{i},{100 + i % 10},{i % 2},{'ru' if i % 2 else 'us'}" for i in range(n)
    ]
    return "\n".join(lines)


def _config(name: str, **overrides) -> dict:
    config = {
        "name": name,
        "unit_col": "client_id",
        "groups": {"control": 0.5, "treatment": 0.5},
        "metrics": [{"name": "revenue", "type": "continuous", "role": "primary"}],
        "sample_size": 200,
        "split_method": "simple",
        "isolation": "off",
        "exclude_experiments": "all_active",
    }
    config.update(overrides)
    return config


def _design(app_client, name: str, dataset_id: str, *, config_overrides=None, **body_extra) -> dict:
    body = {"config": _config(name, **(config_overrides or {})), "dataset_id": dataset_id}
    body.update(body_extra)
    resp = app_client.post("/api/v1/design", json=body)
    assert resp.status_code == 202, resp.text
    return _poll(app_client, resp.json()["job_id"])


# ---------------------------------------------------------------------------
# C1 — никаких переименований и лишних колонок в пользовательских выгрузках
# ---------------------------------------------------------------------------


def test_c1_sample_download_keeps_the_original_id_column_name(app_client):
    """Воспроизведение зарепорченного бага: скачанные выборки приезжали с
    заголовком `unit_id`, которого в датасете не было никогда."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(app_client, "c1_names", dataset_id)["status"] == "completed"

    for group in ("control", "treatment"):
        resp = app_client.get(f"/api/v1/experiments/c1_names/samples/{group}.csv")
        assert resp.status_code == 200, resp.text
        header = resp.text.splitlines()[0]
        # Байт-в-байт: имя из датасета + назначение группы, больше ничего.
        assert header == "client_id,group"
        assert "unit_id" not in resp.text
        rows = list(csv.DictReader(io.StringIO(resp.text)))
        assert rows, "sample must not be empty"
        assert {r["group"] for r in rows} == {group}
        # ID действительно те, что были в исходных данных (c0, c1, ...) —
        # переименование колонки не должно было подменить и значения.
        assert all(r["client_id"].startswith("c") for r in rows)


def test_c1_stratum_kept_only_for_a_stratified_split(app_client):
    """stratum — служебная склейка значений страт: у стратифицированного
    сплита это реальная информация о дизайне, у простого — вырожденное "all",
    то есть ровно та техническая колонка, которой в выгрузке быть не должно."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())

    assert _design(app_client, "c1_simple", dataset_id)["status"] == "completed"
    simple_header = app_client.get("/api/v1/experiments/c1_simple/samples/control.csv").text.splitlines()[0]
    assert simple_header == "client_id,group"

    dataset_id2 = _upload(app_client, _csv(), filename="strat.csv")
    assert _design(
        app_client, "c1_strat", dataset_id2,
        config_overrides={"split_method": "stratified", "strata": ["country"]},
    )["status"] == "completed"
    strat_header = app_client.get("/api/v1/experiments/c1_strat/samples/control.csv").text.splitlines()[0]
    assert strat_header == "client_id,group,stratum"


def test_c1_samples_zip_matches_the_per_group_downloads(app_client):
    """Второй путь выгрузки (ZIP) не должен расходиться с первым — иначе
    "починили" ровно половину."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(app_client, "c1_zip", dataset_id)["status"] == "completed"

    resp = app_client.get("/api/v1/experiments/c1_zip/samples.zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert sorted(zf.namelist()) == ["control.csv", "treatment.csv"]
        for member in zf.namelist():
            header = zf.read(member).decode("utf-8").splitlines()[0]
            assert header == "client_id,group"


def test_c1_analyze_still_joins_after_the_rename(app_client):
    """Переименование живет ТОЛЬКО на границе сериализации: внутренний join
    анализа по-прежнему идет через unit_id и обязан работать."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(app_client, "c1_join", dataset_id)["status"] == "completed"

    post_id = _upload(app_client, _csv(), filename="post.csv")
    resp = app_client.post(
        "/api/v1/experiments/c1_join/analyze",
        json={"dataset_id": post_id, "correction": "holm"},
    )
    assert resp.status_code == 202, resp.text
    job = _poll(app_client, resp.json()["job_id"], timeout=60.0)
    assert job["status"] == "completed", job.get("error")

    results = app_client.get("/api/v1/experiments/c1_join/results").json()
    # Джойн реально нашел пользователей — иначе анализ был бы пустым.
    assert results["results"], "analysis produced no rows — the join broke"
    assert all(r["n"]["control"] > 0 for r in results["results"])


def test_c1_export_archive_round_trips_column_and_metric_names(app_client):
    """Экспорт/импорт не должен терять ни имя ID-колонки, ни подписи метрик."""
    _login(app_client, email="admin@co.com", role="admin")
    dataset_id = _upload(app_client, _csv())
    assert _design(
        app_client, "c1_export", dataset_id,
        config_overrides={
            "metrics": [
                {"name": "revenue", "display_name": "Revenue per user",
                 "type": "continuous", "role": "primary"},
            ],
        },
    )["status"] == "completed"

    export = app_client.get("/api/v1/experiments/c1_export/export")
    assert export.status_code == 200, export.text

    imported = app_client.post(
        "/api/v1/experiments/import",
        files={"file": ("exp.zip", export.content, "application/zip")},
    )
    assert imported.status_code == 201, imported.text
    new_name = imported.json()["experiment_name"]

    detail = app_client.get(f"/api/v1/experiments/{new_name}").json()
    assert detail["config"]["unit_col"] == "client_id"
    assert detail["config"]["metrics"][0]["name"] == "revenue"
    assert detail["config"]["metrics"][0]["display_name"] == "Revenue per user"


# ---------------------------------------------------------------------------
# A3 / C3 — исключение пересечения и фиксация принятого решения
# ---------------------------------------------------------------------------


def _occupy(app_client, name: str = "overlap_first", n: int = 200) -> None:
    """Эксперимент, занимающий юзеров, — чтобы следующему было с чем
    пересечься. Создается ОДИН раз на тест."""
    dataset_id = _upload(app_client, _csv(n=n), filename=f"{name}.csv")
    assert _design(app_client, name, dataset_id)["status"] == "completed"


def _design_overlapping(app_client, second_name: str, **body_extra) -> dict:
    """Второй эксперимент по тем же юзерам с isolation=warn — пересечение
    гарантировано."""
    second_ds = _upload(app_client, _csv(), filename=f"{second_name}.csv")
    return _design(
        app_client, second_name, second_ds,
        config_overrides={"isolation": "warn"}, **body_extra,
    )


def test_a3_overlap_still_asks_before_doing_anything(app_client):
    _login(app_client)
    _occupy(app_client)
    job = _design_overlapping(app_client, "overlap_ask")
    assert job["status"] == "requires_confirmation"
    assert job["result"]["overlap"] == 200


def test_a3_exclude_overlapping_removes_them_and_records_the_decision(app_client):
    """Новая кнопка: исключить пересекшихся и продолжить.

    Пересечение ЧАСТИЧНОЕ (120 из 200) — как в жизни. Полное пересечение
    здесь было бы вырожденным случаем: после исключения не осталось бы ни
    одного кандидата, и дизайн честно падает с "No candidates left" — это
    существующее и правильное поведение, не то, что item A3 просит менять
    (он про "пул стал меньше требуемого", а не "пул стал пустым")."""
    _login(app_client)
    _occupy(app_client, n=120)
    assert _design_overlapping(app_client, "overlap_excl")["status"] == "requires_confirmation"

    job = _design_overlapping(
        app_client, "overlap_excl", confirmed=True, overlap_action="exclude",
    )
    assert job["status"] == "completed", job.get("error")

    computed = app_client.get("/api/v1/experiments/overlap_excl").json()["config"]["computed"]
    decision = computed["isolation_decision"]
    assert decision["decision"] == "excluded"
    assert decision["n_overlap"] == 120
    assert decision["by_experiment"] == {"overlap_first": 120}
    # Пул реально уменьшился — исключение не косметическое.
    assert computed["n_excluded_by_isolation"] == 120
    assert computed["n_available"] == 80
    assert sum(computed["group_sizes"].values()) == 80


def test_a3_proceed_records_the_decision_without_filtering(app_client):
    """Старая кнопка продолжает работать И теперь оставляет след."""
    _login(app_client)
    _occupy(app_client)
    assert _design_overlapping(app_client, "overlap_proceed")["status"] == "requires_confirmation"

    job = _design_overlapping(
        app_client, "overlap_proceed", confirmed=True, overlap_action="proceed",
    )
    assert job["status"] == "completed", job.get("error")

    computed = app_client.get("/api/v1/experiments/overlap_proceed").json()["config"]["computed"]
    decision = computed["isolation_decision"]
    assert decision["decision"] == "proceeded"
    assert decision["n_overlap"] == 200
    # Никого не исключили — в этом и был смысл выбора.
    assert computed["n_excluded_by_isolation"] == 0


def test_a3_bare_confirmed_true_keeps_its_old_meaning(app_client):
    """Обратная совместимость: клиент, не знающий про overlap_action, шлет
    только confirmed=true и должен получить прежнее поведение (продолжить)."""
    _login(app_client)
    _occupy(app_client)
    assert _design_overlapping(app_client, "overlap_legacy")["status"] == "requires_confirmation"
    job = _design_overlapping(app_client, "overlap_legacy", confirmed=True)
    assert job["status"] == "completed", job.get("error")
    computed = app_client.get("/api/v1/experiments/overlap_legacy").json()["config"]["computed"]
    assert computed["isolation_decision"]["decision"] == "proceeded"


def test_a3_exclusion_below_required_sample_size_does_not_block(app_client):
    """Явное требование ТЗ: если после исключения пула не хватает на
    рассчитанный размер выборки — НЕ блокировать, таблица MDE честно покажет
    что достижимо на уменьшившемся пуле."""
    _login(app_client)
    # Первый занимает 150 из 200 юзеров второго -> останется 50 при
    # запрошенных sample_size=200.
    _occupy(app_client, name="shrink_first", n=150)

    second_ds = _upload(app_client, _csv(n=200), filename="big_second.csv")
    body = {
        "config": _config("shrink_second", isolation="warn", sample_size=200),
        "dataset_id": second_ds,
        "confirmed": True,
        "overlap_action": "exclude",
    }
    resp = app_client.post("/api/v1/design", json=body)
    assert resp.status_code == 202
    job = _poll(app_client, resp.json()["job_id"])
    assert job["status"] == "completed", job.get("error")

    computed = app_client.get("/api/v1/experiments/shrink_second").json()["config"]["computed"]
    assert computed["isolation_decision"]["decision"] == "excluded"
    # Осталось меньше запрошенного, и это не ошибка.
    assert computed["n_available"] == 50
    assert sum(computed["group_sizes"].values()) == 50


def test_c3_no_overlap_is_recorded_as_such(app_client):
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(
        app_client, "c3_clean", dataset_id, config_overrides={"isolation": "exclude"},
    )["status"] == "completed"
    computed = app_client.get("/api/v1/experiments/c3_clean").json()["config"]["computed"]
    assert computed["isolation_decision"]["decision"] == "none"
    assert computed["isolation_decision"]["n_overlap"] == 0


def test_c3_disclosure_appears_in_the_design_report(app_client):
    _login(app_client)
    _occupy(app_client)
    assert _design_overlapping(app_client, "c3_report")["status"] == "requires_confirmation"
    assert _design_overlapping(
        app_client, "c3_report", confirmed=True, overlap_action="proceed",
    )["status"] == "completed"

    html = app_client.get("/api/v1/experiments/c3_report/reports/design_report.html").text
    assert "Proceeded despite 200 overlapping users" in html


# ---------------------------------------------------------------------------
# B1 / B2 — редактируемые даты
# ---------------------------------------------------------------------------


def test_b2_planned_end_date_is_stored_at_design_time(app_client):
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    resp = app_client.post(
        "/api/v1/design",
        json={"config": _config("b2_design"), "dataset_id": dataset_id, "planned_end_date": "2030-01-15"},
    )
    assert resp.status_code == 202
    assert _poll(app_client, resp.json()["job_id"])["status"] == "completed"

    assert app_client.get("/api/v1/experiments/b2_design").json()["planned_end_date"] == "2030-01-15"


def test_b1_b2_dates_are_editable_and_audited(app_client):
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(app_client, "b1_edit", dataset_id)["status"] == "completed"

    props = app_client.get("/api/v1/experiments/b1_edit/properties").json()
    resp = app_client.put(
        "/api/v1/experiments/b1_edit/properties",
        json={
            "name": "b1_edit", "owner_ids": [], "editor_ids": [], "visible_roles": None,
            "set_lifecycle_dates": True,
            "started_at": "2029-03-01T10:00:00+00:00",
            "planned_end_date": "2029-03-20",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["planned_end_date"] == "2029-03-20"
    assert resp.json()["started_at"].startswith("2029-03-01")
    assert props["started_at"] is None  # до правки старта не было

    entries = AuditRepo().list_recent(action="experiment.properties_change", object_name="b1_edit")
    details = entries[0].details
    assert details["started_at"]["from"] is None
    assert details["started_at"]["to"].startswith("2029-03-01")
    assert details["planned_end_date"] == {"from": None, "to": "2029-03-20"}


def test_b2_clearing_the_planned_end_date_turns_auto_completion_off(app_client):
    """None здесь — значащее значение ("даты нет"), а не "не трогать"."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    resp = app_client.post(
        "/api/v1/design",
        json={"config": _config("b2_clear"), "dataset_id": dataset_id, "planned_end_date": "2030-01-15"},
    )
    assert _poll(app_client, resp.json()["job_id"])["status"] == "completed"

    app_client.put(
        "/api/v1/experiments/b2_clear/properties",
        json={
            "name": "b2_clear", "owner_ids": [], "editor_ids": [], "visible_roles": None,
            "set_lifecycle_dates": True, "started_at": None, "planned_end_date": None,
        },
    )
    assert app_client.get("/api/v1/experiments/b2_clear").json()["planned_end_date"] is None


def test_b1_omitting_the_flag_leaves_dates_untouched(app_client):
    """Клиент, не знающий про даты, не должен их обнулять простым сохранением
    остальных свойств."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    resp = app_client.post(
        "/api/v1/design",
        json={"config": _config("b1_keep"), "dataset_id": dataset_id, "planned_end_date": "2030-06-01"},
    )
    assert _poll(app_client, resp.json()["job_id"])["status"] == "completed"

    app_client.put(
        "/api/v1/experiments/b1_keep/properties",
        json={"name": "b1_keep", "owner_ids": [], "editor_ids": [], "visible_roles": None},
    )
    assert app_client.get("/api/v1/experiments/b1_keep").json()["planned_end_date"] == "2030-06-01"


# ---------------------------------------------------------------------------
# B3 — авто-завершение
# ---------------------------------------------------------------------------


def _make_running_with_end_date(app_client, name: str, end: date) -> None:
    dataset_id = _upload(app_client, _csv(), filename=f"{name}.csv")
    assert _design(app_client, name, dataset_id)["status"] == "completed"
    resp = app_client.post(f"/api/v1/experiments/{name}/status", json={"to": "running"})
    assert resp.status_code == 200, resp.text
    ExperimentRepo().update_planned_end_date(name, end)


def test_b3_sweep_completes_a_running_experiment_past_its_end_date(app_client):
    from abkit.lifecycle import auto_complete_due_experiments

    _login(app_client)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    _make_running_with_end_date(app_client, "b3_due", yesterday)

    assert auto_complete_due_experiments() == ["b3_due"]
    assert app_client.get("/api/v1/experiments/b3_due").json()["status"] == "completed"


def test_b3_transition_happens_exactly_once(app_client):
    """Гейт по status == 'running' и есть гарантия "ровно один раз" — второй
    проход не должен ни переводить снова, ни писать второй audit-entry."""
    from abkit.lifecycle import auto_complete_due_experiments

    _login(app_client)
    _make_running_with_end_date(
        app_client, "b3_once", (datetime.now(timezone.utc) - timedelta(days=2)).date()
    )

    assert auto_complete_due_experiments() == ["b3_once"]
    assert auto_complete_due_experiments() == []

    entries = AuditRepo().list_recent(action="experiment.auto_completed", object_name="b3_once")
    assert len(entries) == 1
    assert entries[0].details["from"] == "running"
    assert entries[0].details["to"] == "completed"
    assert "planned end date" in entries[0].details["reason"]
    # Система, не человек — строка в History рисуется как "system".
    assert entries[0].user_email is None


def test_b3_leaves_alone_what_is_not_due(app_client):
    from abkit.lifecycle import auto_complete_due_experiments

    _login(app_client)
    # Плановая дата в будущем.
    _make_running_with_end_date(
        app_client, "b3_future", (datetime.now(timezone.utc) + timedelta(days=5)).date()
    )
    # Плановая дата — СЕГОДНЯ: день еще не закончился, тест идет по нее
    # включительно (abkit/lifecycle.py::auto_completion_cutoff).
    _make_running_with_end_date(app_client, "b3_today", datetime.now(timezone.utc).date())
    # Дата прошла, но эксперимент не running.
    dataset_id = _upload(app_client, _csv(), filename="b3_designed.csv")
    assert _design(app_client, "b3_designed", dataset_id)["status"] == "completed"
    ExperimentRepo().update_planned_end_date(
        "b3_designed", (datetime.now(timezone.utc) - timedelta(days=10)).date()
    )

    assert auto_complete_due_experiments() == []
    for name in ("b3_future", "b3_today"):
        assert app_client.get(f"/api/v1/experiments/{name}").json()["status"] == "running"
    assert app_client.get("/api/v1/experiments/b3_designed").json()["status"] == "designed"


def test_b3_lazy_check_on_page_load(app_client):
    """UI не должен показывать "running" на тесте, чья дата уже прошла, даже
    если тик планировщика еще не случился — GET страницы сам догоняет."""
    _login(app_client)
    _make_running_with_end_date(
        app_client, "b3_lazy", (datetime.now(timezone.utc) - timedelta(days=2)).date()
    )

    # Никакого sweep — только открытие страницы.
    detail = app_client.get("/api/v1/experiments/b3_lazy").json()
    assert detail["status"] == "completed"
    assert detail["completed_at"] is not None

    entries = AuditRepo().list_recent(action="experiment.auto_completed", object_name="b3_lazy")
    assert len(entries) == 1
    # Повторное открытие не плодит записей.
    app_client.get("/api/v1/experiments/b3_lazy")
    assert len(AuditRepo().list_recent(action="experiment.auto_completed", object_name="b3_lazy")) == 1


def test_b3_auto_completed_entry_shows_up_in_experiment_history(app_client):
    from abkit.lifecycle import auto_complete_due_experiments

    _login(app_client)
    _make_running_with_end_date(
        app_client, "b3_history", (datetime.now(timezone.utc) - timedelta(days=2)).date()
    )
    auto_complete_due_experiments()

    audit = app_client.get("/api/v1/experiments/b3_history/audit").json()
    actions = [e["action"] for e in audit["items"]]
    assert "experiment.auto_completed" in actions


# ---------------------------------------------------------------------------
# A1 / C2 — подписи метрик и полный дизайн-контекст в отчете анализа
# ---------------------------------------------------------------------------


def test_c2_analysis_report_carries_the_full_design_context(app_client):
    """Фикстура "заполнено всё" — и каждое из полей обязано отрисоваться.
    Гипотеза до этого фикса в отчете анализа отсутствовала вовсе."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(
        app_client, "c2_full", dataset_id,
        config_overrides={
            "metrics": [
                {"name": "revenue", "display_name": "Revenue per user", "type": "continuous",
                 "role": "primary", "description": "Sum of paid orders"},
                {"name": "converted", "display_name": "Conversion", "type": "binary",
                 "role": "secondary", "description": "Any purchase"},
            ],
            "groups": {"control": 0.5, "treatment": 0.5},
            "group_descriptions": {"treatment": "New checkout"},
            "split_method": "stratified",
            "strata": ["country"],
            "sample_size": 200,
        },
    )["status"] == "completed"

    # Гипотеза (отдельный markdown-блок) + плановая дата (колонка строки).
    blocks = app_client.get("/api/v1/experiments/c2_full/blocks").json()
    hypothesis_block = next(b for b in blocks if b["kind"] == "hypothesis")
    app_client.put(
        "/api/v1/experiments/c2_full/blocks",
        json=[{**hypothesis_block, "content_md": "Checkout redesign lifts revenue"}],
    )
    ExperimentRepo().update_planned_end_date("c2_full", date(2030, 5, 5))

    post_id = _upload(app_client, _csv(), filename="c2_post.csv")
    resp = app_client.post(
        "/api/v1/experiments/c2_full/analyze",
        json={"dataset_id": post_id, "correction": "holm"},
    )
    assert resp.status_code == 202
    assert _poll(app_client, resp.json()["job_id"], timeout=60.0)["status"] == "completed"

    html = app_client.get("/api/v1/experiments/c2_full/reports/report.html").text

    assert "Checkout redesign lifts revenue" in html          # гипотеза (был баг)
    assert "Revenue per user" in html                          # display_name метрики
    assert "column: revenue" in html                           # техническое имя рядом
    assert "Sum of paid orders" in html                        # описание метрики
    assert "New checkout" in html                              # описание группы
    assert "stratified" in html                                # метод сплита
    # _format_report_date снимает ведущий ноль дня ("May 05" -> "May 5").
    assert "Planned end" in html and "May 5, 2030" in html      # плановая дата
    assert "Target sample size 200" in html                    # как задавался размер
    assert "primary" in html and "secondary" in html            # роли метрик
    assert "50%" in html                                       # доли групп


def test_c2_design_report_gets_the_hypothesis_after_the_fact(app_client):
    """design_report.html пишется ДО того, как визард успевает сохранить
    гипотезу — секция впечатывается в уже сохраненный файл."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(app_client, "c2_design", dataset_id)["status"] == "completed"

    before = app_client.get("/api/v1/experiments/c2_design/reports/design_report.html").text
    assert "design-context-section:start" in before

    blocks = app_client.get("/api/v1/experiments/c2_design/blocks").json()
    hypothesis_block = next(b for b in blocks if b["kind"] == "hypothesis")
    app_client.put(
        "/api/v1/experiments/c2_design/blocks",
        json=[{**hypothesis_block, "content_md": "A better onboarding raises D7 retention"}],
    )

    after = app_client.get("/api/v1/experiments/c2_design/reports/design_report.html").text
    assert "A better onboarding raises D7 retention" in after
    assert "<h2>Hypothesis</h2>" in after


def test_a1_display_name_flows_into_reports_and_falls_back(app_client):
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(
        app_client, "a1_labels", dataset_id,
        config_overrides={
            "metrics": [
                {"name": "revenue", "display_name": "Revenue per user",
                 "type": "continuous", "role": "primary"},
                # Без display_name — должно остаться техническое имя.
                {"name": "converted", "type": "binary", "role": "secondary"},
            ],
        },
    )["status"] == "completed"

    html = app_client.get("/api/v1/experiments/a1_labels/reports/design_report.html").text
    assert "Revenue per user" in html
    assert "column: revenue" in html
    assert "converted" in html
    # У метрики без подписи лишней строки "column:" быть не должно.
    assert "column: converted" not in html


def test_a5_no_dagger_anywhere_in_the_reports(app_client):
    """Item A5 — это была сноска-маркер для secondary-метрик; роль и так
    написана словом, значок убран из UI и отчетов."""
    _login(app_client)
    dataset_id = _upload(app_client, _csv())
    assert _design(
        app_client, "a5_dagger", dataset_id,
        config_overrides={
            "metrics": [
                {"name": "revenue", "type": "continuous", "role": "primary"},
                {"name": "converted", "type": "binary", "role": "secondary"},
            ],
        },
    )["status"] == "completed"

    html = app_client.get("/api/v1/experiments/a5_dagger/reports/design_report.html").text
    assert "†" not in html
    assert "†" not in html
    # Смысл сноски никуда не делся, просто выражен словами.
    assert "secondary" in html


# ---------------------------------------------------------------------------
# C4 — абсолютный MDE по стратам
# ---------------------------------------------------------------------------


def test_c4_strata_power_carries_absolute_mde(app_client):
    _login(app_client)
    dataset_id = _upload(app_client, _csv(n=400))
    assert _design(
        app_client, "c4_strata", dataset_id,
        config_overrides={"split_method": "stratified", "strata": ["country"], "sample_size": 400},
    )["status"] == "completed"

    computed = app_client.get("/api/v1/experiments/c4_strata").json()["config"]["computed"]
    rows = [r for rows in computed["strata_power"].values() for r in rows]
    assert rows, "expected strata power rows"
    for row in rows:
        assert "mde_abs" in row and "metric_type" in row
    priced = [r for r in rows if r["mde_abs"] is not None]
    assert priced, "at least one stratum should have a computable absolute MDE"
    for row in priced:
        # abs = rel × baseline, с точностью до плавающей запятой.
        assert row["baseline_mean"] is not None
        assert abs(row["mde_abs"] - row["mde_rel"] * row["baseline_mean"]) < 1e-6

    html = app_client.get("/api/v1/experiments/c4_strata/reports/design_report.html").text
    assert "MDE (abs.)" in html
