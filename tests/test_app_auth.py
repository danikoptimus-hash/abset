"""AppTest-сценарий логин -> design -> logout (ABKIT_MODE=db) — критерий
готовности этапа D2 (DOCKER.md §12). Файловый режим (ABKIT_MODE не задан)
по-прежнему не показывает логин-экран вообще — см. tests/test_app.py, там
current_user всегда None и поведение не изменилось."""

import pandas as pd
from streamlit.testing.v1 import AppTest

from abkit.auth.passwords import hash_password


def _fresh_db_app(db_url, tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-apptest-scenario")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    # _next_demo_name()/storage.get_experiments_dir() читают файловый registry.json
    # независимо от ABKIT_MODE (это file-mode-only хелпер) — без изоляции тест
    # словил бы коллизию имени "demo" с реальным ~/ab_experiments на машине.
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    return at


def test_unauthenticated_user_sees_only_login_form(db_url, tmp_path, monkeypatch):
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)
    assert not at.exception
    assert any("вход" in t.value.lower() for t in at.title)
    assert any(ti.label == "Email" for ti in at.text_input)
    assert any(ti.label == "Пароль" for ti in at.text_input)
    # никаких табов/данных экспериментов не рендерится до входа
    assert len(at.tabs) == 0


def test_login_with_wrong_password_shows_error_and_stays_on_login(db_url, tmp_path, monkeypatch):
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="viewer@co.com", name="Viewer", password_hash=hash_password("realpw123"), role="viewer"
    )
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("viewer@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("wrongpw")
    at.button[0].click().run(timeout=30)

    assert not at.exception
    assert any("Неверный" in e.value for e in at.error)
    assert len(at.tabs) == 0


def test_login_design_logout_scenario(db_url, tmp_path, monkeypatch):
    """Полный сценарий: Editor логинится, дизайнит demo-эксперимент через UI,
    видит его сводку, разлогинивается — снова видит только форму входа."""
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="editor@co.com", name="Editor", password_hash=hash_password("pw12345"), role="editor"
    )

    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("editor@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)

    assert not at.exception
    assert len(at.tabs) == 4  # Design/Analyze/Experiments/Validation, без Admin (editor)
    assert any("editor@co.com" in c.value for c in at.sidebar.caption)

    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    assert not at.exception

    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    design_tab = at.tabs[0]
    assert any("Сводка: demo" in s.value for s in design_tab.subheader)

    from abkit.db.repositories import ExperimentRepo

    exp_row = ExperimentRepo().get_by_name("demo")
    assert exp_row is not None

    at.sidebar.button[0].click().run(timeout=30)  # "Выйти"
    assert not at.exception
    assert any(ti.label == "Email" for ti in at.text_input)
    assert len(at.tabs) == 0


def test_viewer_does_not_see_design_form_after_login(db_url, tmp_path, monkeypatch):
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="viewer2@co.com", name="Viewer2", password_hash=hash_password("pw12345"), role="viewer"
    )
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("viewer2@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    assert any("Недостаточно прав" in i.value for i in design_tab.info)
    assert not any("демо-данные" in b.label for b in design_tab.button)


def test_admin_sees_admin_tab_editor_does_not(db_url, tmp_path, monkeypatch):
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="admin2@co.com", name="Admin2", password_hash=hash_password("pw12345"), role="admin"
    )
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("admin2@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)

    assert not at.exception
    # 5 верхнеуровневых (+ Admin) + 2 подтаба внутри Admin ("Пользователи"/"Аудит") —
    # at.tabs плоско считает все Tab-элементы дерева, включая вложенные
    assert len(at.tabs) == 7
    admin_tab = at.tabs[4]
    assert any("Администрирование" in h.value for h in admin_tab.header)


def test_tab_content_does_not_leak_across_tabs_db_mode(db_url, tmp_path, monkeypatch):
    """UX0 (regression): каждый раздел должен жить строго внутри своего таба —
    ни один заголовок одной вкладки не должен просачиваться в дерево другой
    (в частности, "Администрирование" не должен быть виден вне Admin-таба,
    и наоборот). Admin-роль -> все 5 вкладок присутствуют."""
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="admin4@co.com", name="Admin4", password_hash=hash_password("pw12345"), role="admin"
    )
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("admin4@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)
    assert not at.exception

    own_headers = [
        "Дизайн эксперимента",
        "Анализ по фактическим данным",
        "Реестр экспериментов",
        "Валидация симуляциями",
        "Администрирование",
    ]
    top_level_tabs = at.tabs[:5]
    for i, tab in enumerate(top_level_tabs):
        tab_headers = [h.value for h in tab.header]
        assert tab_headers == [own_headers[i]], (
            f"Таб {i} ({own_headers[i]}) содержит посторонние заголовки: {tab_headers}"
        )
        for j, other_header in enumerate(own_headers):
            if j == i:
                continue
            assert other_header not in tab_headers


def test_experiments_tab_history_and_admin_audit_show_design_event(db_url, tmp_path, monkeypatch):
    """DOCKER.md §6.2: «История» — события эксперимента видны любой роли;
    «Аудит» у Admin — общий список событий."""
    from abkit.db.repositories import UserRepo

    UserRepo().create(
        email="admin3@co.com", name="Admin3", password_hash=hash_password("pw12345"), role="admin"
    )
    at = _fresh_db_app(db_url, tmp_path, monkeypatch)

    next(ti for ti in at.text_input if ti.label == "Email").set_value("admin3@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)

    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    next(b for b in experiments_tab.button if b.key == "exp_toggle_demo").click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    history_expanders = [e for e in experiments_tab.expander if e.label == "История"]
    assert len(history_expanders) == 1
    history_dfs = history_expanders[0].dataframe
    assert len(history_dfs) == 1
    assert "experiment.create" in history_dfs[0].value["действие"].values

    admin_tab = at.tabs[4]
    assert any("Аудит" in s.value for s in admin_tab.subheader)
    audit_dfs = admin_tab.dataframe
    all_actions = pd.concat([df.value["действие"] for df in audit_dfs if "действие" in df.value.columns])
    assert "experiment.create" in all_actions.values


def test_imported_legacy_experiment_visible_in_ui_with_status_and_report(db_url, tmp_path, monkeypatch):
    """Критерий готовности этапа D5 (DOCKER.md §12): "эксперименты видны в UI
    со статусами и отчетами" после import-legacy."""
    import numpy as np

    from abkit.config import DesignConfig, MetricConfig
    from abkit.db.import_legacy import import_legacy_dir
    from abkit.db.repositories import UserRepo
    from abkit.experiment import Experiment

    # строим настоящий файловый (легаси) эксперимент с анализом и отчетом
    legacy_dir = tmp_path / "legacy_source"
    rng = np.random.default_rng(0)
    n = 200
    data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": rng.normal(100, 20, size=n),
        }
    )
    config = DesignConfig(
        name="imported_exp",
        unit_col="user_id",
        groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=n,
        split_method="simple",
        seed=1,
    )
    legacy_experiment = Experiment.design(config, data, experiments_dir=legacy_dir)
    post_data = pd.DataFrame(
        {
            "user_id": legacy_experiment.assignments["unit_id"],
            "revenue": rng.normal(100, 20, size=n),
        }
    )
    legacy_experiment.analyze(post_data).report()

    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-import-ui-test")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "server_data"))

    UserRepo().create(
        email="importadmin@co.com", name="ImportAdmin", password_hash=hash_password("pw12345"), role="admin"
    )
    result = import_legacy_dir(legacy_dir, "importadmin@co.com")
    assert result.imported == ["imported_exp"]

    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    next(ti for ti in at.text_input if ti.label == "Email").set_value("importadmin@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    row_texts = " ".join(m.value for m in experiments_tab.markdown)
    assert "imported_exp" in row_texts
    assert "designed" in row_texts  # цветной бейдж статуса

    next(b for b in experiments_tab.button if b.key == "exp_toggle_imported_exp").click().run(timeout=30)
    experiments_tab = at.tabs[2]
    assert not at.exception

    report_radio = next(r for r in experiments_tab.radio if r.key == "exp_detail_imported_exp_report_choice")
    assert "report.html" in report_radio.options
    report_radio.set_value("report.html").run(timeout=30)
    experiments_tab = at.tabs[2]
    assert not at.exception
    assert not any("еще не создан" in i.value for i in experiments_tab.info)
    assert len(experiments_tab.get("iframe")) == 1


def _prep_db_env(db_url, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABKIT_MODE", "db")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ABKIT_SECRET_KEY", "a-real-generated-secret-for-apptest-scenario")
    monkeypatch.setenv("ABKIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path / "file_side"))
    monkeypatch.setenv("ABKIT_FLASH_SECONDS", "0")


def _design_via_jobs(owner_user, name: str, n: int = 200, seed: int = 0):
    """Дизайнит эксперимент напрямую через jobs.run_design (минуя UI) — нужен
    известный owner_id для тестов видимости кнопок мутаций по ролям. unit_id
    с префиксом имени эксперимента — не пересекается с другими экспериментами
    в том же тесте (иначе дефолтная изоляция "exclude" обнулит кандидатов)."""
    import numpy as np

    from abkit import jobs
    from abkit.config import DesignConfig, MetricConfig

    rng = np.random.default_rng(seed)
    data = pd.DataFrame(
        {"user_id": [f"{name}_u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
    )
    config = DesignConfig(
        name=name, unit_col="user_id", groups={"control": 0.5, "treatment": 0.5},
        metrics=[MetricConfig(name="revenue", type="continuous")],
        sample_size=n, split_method="simple", seed=seed,
    )
    return jobs.run_design(owner_user, config, data)


def test_experiments_tab_viewer_sees_no_mutation_buttons(db_url, tmp_path, monkeypatch):
    _prep_db_env(db_url, tmp_path, monkeypatch)
    from abkit.auth.guards import CurrentUser
    from abkit.db.repositories import UserRepo

    admin_id = UserRepo().create(
        email="admin_pv@co.com", name="A", password_hash=hash_password("pw12345"), role="admin"
    )
    UserRepo().create(
        email="viewer_pv@co.com", name="V", password_hash=hash_password("pw12345"), role="viewer"
    )
    admin_user = CurrentUser(id=str(admin_id), email="admin_pv@co.com", name="A", role="admin")
    _design_via_jobs(admin_user, "viewer_check_exp")

    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    next(ti for ti in at.text_input if ti.label == "Email").set_value("viewer_pv@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    mutation_prefixes = ("exp_forward_", "exp_archive_", "exp_delete_")
    mutation_keys = [
        b.key for b in experiments_tab.button if b.key and b.key.startswith(mutation_prefixes)
    ]
    assert mutation_keys == []
    # но сама панель списка (view-only "подробнее") видна
    assert any(b.key == "exp_toggle_viewer_check_exp" for b in experiments_tab.button)


def test_experiments_tab_editor_sees_mutations_only_on_own_experiments(db_url, tmp_path, monkeypatch):
    _prep_db_env(db_url, tmp_path, monkeypatch)
    from abkit.auth.guards import CurrentUser
    from abkit.db.repositories import UserRepo

    admin_id = UserRepo().create(
        email="admin_ed@co.com", name="A", password_hash=hash_password("pw12345"), role="admin"
    )
    editor_id = UserRepo().create(
        email="editor_ed@co.com", name="E", password_hash=hash_password("pw12345"), role="editor"
    )
    admin_user = CurrentUser(id=str(admin_id), email="admin_ed@co.com", name="A", role="admin")
    editor_user = CurrentUser(id=str(editor_id), email="editor_ed@co.com", name="E", role="editor")
    _design_via_jobs(editor_user, "editor_own_exp", seed=1)
    _design_via_jobs(admin_user, "admin_other_exp", seed=2)

    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    next(ti for ti in at.text_input if ti.label == "Email").set_value("editor_ed@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    assert any(b.key == "exp_forward_editor_own_exp" for b in experiments_tab.button)
    assert any(b.key == "exp_archive_editor_own_exp" for b in experiments_tab.button)
    assert any(b.key == "exp_delete_open_editor_own_exp" for b in experiments_tab.button)

    assert not any(b.key == "exp_forward_admin_other_exp" for b in experiments_tab.button)
    assert not any(b.key == "exp_archive_admin_other_exp" for b in experiments_tab.button)
    assert not any(b.key == "exp_delete_open_admin_other_exp" for b in experiments_tab.button)
    # подробности на чужом эксперименте видны всем ролям (view-only)
    assert any(b.key == "exp_toggle_admin_other_exp" for b in experiments_tab.button)


def test_experiments_tab_delete_requires_exact_DELETE_confirmation(db_url, tmp_path, monkeypatch):
    _prep_db_env(db_url, tmp_path, monkeypatch)
    from abkit.auth.guards import CurrentUser
    from abkit.db.repositories import UserRepo

    admin_id = UserRepo().create(
        email="admin_del@co.com", name="A", password_hash=hash_password("pw12345"), role="admin"
    )
    admin_user = CurrentUser(id=str(admin_id), email="admin_del@co.com", name="A", role="admin")
    _design_via_jobs(admin_user, "delete_confirm_exp", n=150)

    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    next(ti for ti in at.text_input if ti.label == "Email").set_value("admin_del@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)
    assert not at.exception

    experiments_tab = at.tabs[2]
    next(b for b in experiments_tab.button if b.key == "exp_delete_open_delete_confirm_exp").click().run(
        timeout=30
    )
    experiments_tab = at.tabs[2]
    assert not at.exception

    error_texts = " ".join(e.value for e in experiments_tab.error)
    assert "delete_confirm_exp" in error_texts
    assert "150 строк" in error_texts

    confirm_key = "exp_delete_confirm_btn_delete_confirm_exp"
    input_key = "exp_delete_confirm_input_delete_confirm_exp"

    confirm_btn = next(b for b in experiments_tab.button if b.key == confirm_key)
    assert confirm_btn.disabled

    for bad_value in ["delete", "Delete", "", "DELETE "]:
        input_widget = next(t for t in experiments_tab.text_input if t.key == input_key)
        input_widget.set_value(bad_value).run(timeout=30)
        experiments_tab = at.tabs[2]
        confirm_btn = next(b for b in experiments_tab.button if b.key == confirm_key)
        assert confirm_btn.disabled, f"кнопка не должна активироваться для {bad_value!r}"

    input_widget = next(t for t in experiments_tab.text_input if t.key == input_key)
    input_widget.set_value("DELETE").run(timeout=30)
    experiments_tab = at.tabs[2]
    confirm_btn = next(b for b in experiments_tab.button if b.key == confirm_key)
    assert not confirm_btn.disabled

    # Удаление убирает всю "строку" (st.columns с ~15 виджетами) из цикла
    # рендера — без row_placeholder=st.empty()+.empty() перед st.rerun() в
    # app.py это ломало и AppTest (внутренний AssertionError при разборе
    # дерева), и настоящий браузер ("'setIn' cannot be called on an
    # ElementNode", воспроизведено вручную и исправлено).
    confirm_btn.click().run(timeout=30)
    assert not at.exception

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("delete_confirm_exp") is None


def test_experiments_tab_delete_cancel_clears_confirmation_state(db_url, tmp_path, monkeypatch):
    _prep_db_env(db_url, tmp_path, monkeypatch)
    from abkit.auth.guards import CurrentUser
    from abkit.db.repositories import UserRepo

    admin_id = UserRepo().create(
        email="admin_cancel@co.com", name="A", password_hash=hash_password("pw12345"), role="admin"
    )
    admin_user = CurrentUser(id=str(admin_id), email="admin_cancel@co.com", name="A", role="admin")
    _design_via_jobs(admin_user, "cancel_delete_exp")

    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    next(ti for ti in at.text_input if ti.label == "Email").set_value("admin_cancel@co.com")
    next(ti for ti in at.text_input if ti.label == "Пароль").set_value("pw12345")
    at.button[0].click().run(timeout=30)

    experiments_tab = at.tabs[2]
    next(b for b in experiments_tab.button if b.key == "exp_delete_open_cancel_delete_exp").click().run(
        timeout=30
    )
    experiments_tab = at.tabs[2]
    input_widget = next(
        t for t in experiments_tab.text_input if t.key == "exp_delete_confirm_input_cancel_delete_exp"
    )
    input_widget.set_value("DELETE").run(timeout=30)

    experiments_tab = at.tabs[2]
    next(b for b in experiments_tab.button if b.key == "exp_delete_cancel_btn_cancel_delete_exp").click().run(
        timeout=30
    )
    experiments_tab = at.tabs[2]
    assert not at.exception
    assert not any(e.key == "exp_delete_confirm_btn_cancel_delete_exp" for e in experiments_tab.button)

    from abkit.db.repositories import ExperimentRepo

    assert ExperimentRepo().get_by_name("cancel_delete_exp") is not None

