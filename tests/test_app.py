"""Smoke- и сценарные тесты Streamlit-приложения (app.py) через streamlit.testing.v1.AppTest."""

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from abkit import storage
from abkit.demo_data import generate_demo_design_data, make_demo_design_config
from abkit.experiment import Experiment


def _fresh_app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    # успех после design/analyze теперь показывается ненадолго и сам скрывается
    # (st.empty + time.sleep + .empty()) — в тестах отключаем задержку, иначе
    # каждый прогон submit-кнопки тормозил бы весь набор на несколько секунд.
    monkeypatch.setenv("ABKIT_FLASH_SECONDS", "0")
    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    return at


def test_app_boots_without_exceptions_and_has_four_tabs(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    assert not at.exception
    assert len(at.tabs) == 4


def test_tab_content_does_not_leak_across_tabs_file_mode(tmp_path, monkeypatch):
    """UX0 (regression): содержимое одного таба не должно просачиваться в
    другой — например, заголовок "Реестр экспериментов" не должен быть виден
    в дереве Design-таба, и наоборот. Файловый режим -> 4 таба, без Admin."""
    at = _fresh_app(tmp_path, monkeypatch)
    assert not at.exception

    own_headers = [
        "Дизайн эксперимента",
        "Анализ по фактическим данным",
        "Реестр экспериментов",
        "Валидация симуляциями",
    ]
    assert len(at.tabs) == len(own_headers)
    for i, tab in enumerate(at.tabs):
        tab_headers = [h.value for h in tab.header]
        assert tab_headers == [own_headers[i]], (
            f"Таб {i} ({own_headers[i]}) содержит посторонние заголовки: {tab_headers}"
        )


def test_design_tab_has_onboarding_explanation(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]

    subheaders = [s.value for s in design_tab.subheader]
    assert any("Загрузите данные о ваших пользователях-кандидатах" in s for s in subheaders)
    assert not any("Шаг 1" in s for s in subheaders)

    expander_labels = [e.label for e in design_tab.expander]
    assert any("данные и что в них должно быть" in label for label in expander_labels)
    assert any("Пример" in label for label in expander_labels)
    assert any("SQL" in label for label in expander_labels)
    assert any("просто попробовать" in label for label in expander_labels)

    example_dfs = design_tab.dataframe
    assert len(example_dfs) == 1
    assert list(example_dfs[0].value.columns) == [
        "user_id", "platform", "country", "segment",
        "converted_pre_30d", "revenue_pre_30d", "sessions_pre_30d",
    ]

    code_blocks = design_tab.code
    assert len(code_blocks) == 1
    assert "SELECT" in code_blocks[0].value
    assert "converted_pre_30d" in code_blocks[0].value


def test_design_tab_shows_next_steps_after_data_loaded(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    success_messages = [s.value for s in design_tab.success]
    assert any("Файл загружен" in s and "5000 строк" in s for s in success_messages)
    assert any("Что нужно сделать дальше" in s for s in success_messages)


def test_analyze_tab_has_onboarding_explanation(tmp_path, monkeypatch):
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    analyze_tab = at.tabs[1]

    subheaders = [s.value for s in analyze_tab.subheader]
    assert not any("Шаг 1" in s for s in subheaders)

    expander_labels = [e.label for e in analyze_tab.expander]
    assert any("данные и что в них должно быть" in label for label in expander_labels)
    assert any("Пример" in label for label in expander_labels)
    assert any("БД" in label for label in expander_labels)

    example_dfs = analyze_tab.dataframe
    assert len(example_dfs) == 1
    assert list(example_dfs[0].value.columns) == ["user_id", "converted", "revenue", "sessions"]

    code_blocks = analyze_tab.code
    assert len(code_blocks) == 1
    assert "SELECT" in code_blocks[0].value


def test_design_tab_demo_button_fills_form(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    demo_button = next(b for b in design_tab.button if "демо-данные" in b.label)
    demo_button.click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    names = {ti.key: ti.value for ti in design_tab.text_input}
    assert names["design_name"] == "demo"
    assert "control" in {names.get(k) for k in names if k.startswith("group_name_")}
    assert "treatment" in {names.get(k) for k in names if k.startswith("group_name_")}
    # имена continuous/binary метрик — теперь selectbox (колонка датафрейма), не text_input;
    # ratio-метрика остается text_input (это просто ярлык, а не колонка)
    metric_ids = at.session_state["design_metric_ids"]
    metric_names = {at.session_state[f"metric_name_{mid}"] for mid in metric_ids}
    assert {"revenue", "clicks", "conv_rate"} <= metric_names


def test_metric_precol_num_den_are_selectboxes_restricted_to_numeric_columns(tmp_path, monkeypatch):
    """Регрессия/UX: pre_col/num/den должны быть выпадающими списками из числовых
    колонок загруженного датафрейма, а не текстовым вводом (опечатки)."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    design_tab = at.tabs[0]
    precol_selects = [s for s in design_tab.selectbox if s.key and s.key.startswith("metric_precol_")]
    assert precol_selects
    for select in precol_selects:
        assert select.options[0] == "(нет)"
        assert "platform" not in select.options  # категориальная колонка не числовая
        assert "revenue" in select.options

    num_selects = [s for s in design_tab.selectbox if s.key and s.key.startswith("metric_num_")]
    den_selects = [s for s in design_tab.selectbox if s.key and s.key.startswith("metric_den_")]
    assert num_selects and den_selects
    assert "orders" in num_selects[0].options
    assert "sessions" in den_selects[0].options


def test_binary_metric_shows_01_column_hint(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    design_tab = at.tabs[0]
    captions = [c.value for c in design_tab.caption]
    hint_captions = [c for c in captions if "Подходящие 0/1 колонки" in c]
    assert hint_captions
    assert "clicks" in hint_captions[0]


def test_stale_column_selection_reset_with_warning_on_new_data(tmp_path, monkeypatch):
    """При загрузке нового датафрейма без ранее выбранных колонок (unit_col,
    страта, num/den/pre_col) — сбросить выбор на дефолт/«(нет)» и предупредить,
    а не упасть с ошибкой selectbox."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    mid = at.session_state["design_metric_ids"][0]
    assert at.session_state[f"metric_precol_{mid}"] == "revenue_pre"

    new_data = pd.DataFrame(
        {
            "totally_different_id": [f"x{i}" for i in range(500)],
            "some_metric": np.random.default_rng(0).normal(0, 1, size=500),
        }
    )
    at.session_state["design_data"] = new_data
    at.run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    warning_texts = " ".join(w.value for w in design_tab.warning)
    assert "unit_col" in warning_texts and "не найдена" in warning_texts
    assert "revenue_pre" in warning_texts
    assert at.session_state[f"metric_precol_{mid}"] == "(нет)"


def test_design_data_persists_across_unrelated_rerun(tmp_path, monkeypatch):
    """Регрессия: session_state.pop() однократно съедал данные демо на первом же
    неcвязанном rerun'е (например, при правке любого поля формы)."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    demo_button = next(b for b in design_tab.button if "демо-данные" in b.label)
    demo_button.click().run(timeout=30)

    at.run(timeout=30)  # имитация еще одного rerun'а без клика по demo-кнопке
    assert not at.exception
    design_tab = at.tabs[0]
    assert any(ti.key == "design_name" and ti.value == "demo" for ti in design_tab.text_input)
    assert any("Спроектировать" in b.label for b in design_tab.button)


def test_design_submit_creates_experiment(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    # success теперь короткий и сам исчезает (см. _flash_success), а st.status
    # рендерится только пока design_running=True — после финального st.rerun()
    # оба уже не видны; персистентный признак завершения — сводка эксперимента.
    assert any("Сводка: demo" in s.value for s in design_tab.subheader)
    registry = storage.read_registry(tmp_path)
    assert "demo" in registry


def test_design_submit_button_disabled_flag_wired_to_running_state(tmp_path, monkeypatch):
    """Кнопка должна ссылаться на design_running, чтобы быть недоступной во
    время расчета (защита от повторного клика) — проверяем саму разметку виджета
    (сам процесс расчета в AppTest происходит синхронно и промежуточное состояние
    поймать нельзя, но связь disabled<->session_state можно проверить косвенно:
    после ошибки конфигурации кнопка должна снова стать активной)."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    # ломаем конфиг: доли групп не суммируются в 1 -> ValidationError до вызова design()
    design_tab = at.tabs[0]
    prop_inputs = [ni for ni in design_tab.number_input if ni.key.startswith("group_prop_")]
    prop_inputs[0].set_value(0.9).run(timeout=30)

    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    submit_button = next(b for b in design_tab.button if "Спроектировать" in b.label)
    assert submit_button.disabled is False  # снова активна после ошибки, не "зависла"
    assert any("Ошибка в конфиге дизайна" in e.value for e in design_tab.error)


def test_design_summary_shows_samples_section(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    assert any("Выборки для передачи" in m.value for m in design_tab.markdown)
    assert any("control.csv" in md.value for md in design_tab.markdown)
    assert any("treatment.csv" in md.value for md in design_tab.markdown)

    assert (tmp_path / "demo" / "samples" / "control.csv").exists()
    assert (tmp_path / "demo" / "samples" / "treatment.csv").exists()


def test_design_summary_shows_mde_table_help_expander(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)

    assert not at.exception
    design_tab = at.tabs[0]
    expander_labels = [e.label for e in design_tab.expander]
    assert "❓ Как читать эту таблицу?" in expander_labels


def test_design_strata_nan_shows_warning_and_nan_strategy_selectbox(tmp_path, monkeypatch):
    """Регрессия: пропуски в стратификационной колонке раньше валили дизайн с
    блокирующей ошибкой. Теперь — предупреждение при загрузке + селектбокс
    стратегии, а сам дизайн проходит успешно."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    data = at.session_state["design_data"].copy()
    data.loc[:200, "platform"] = None  # ~4% пропусков в колонке страты
    at.session_state["design_data"] = data
    at.run(timeout=30)

    design_tab = at.tabs[0]
    assert not at.exception
    nan_strategy_selects = [s for s in design_tab.selectbox if s.key == "design_nan_strategy"]
    assert len(nan_strategy_selects) == 1
    assert nan_strategy_selects[0].value == "separate_stratum"
    assert any(
        "пропусков" in w.value and "unknown" in w.value for w in design_tab.warning
    )

    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception
    design_tab = at.tabs[0]
    assert any("Сводка: demo" in s.value for s in design_tab.subheader)
    assert any("Пропуски в стратах" in m.value for m in design_tab.markdown)


def test_analyze_tab_demo_flow_detects_effect(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    demo_post_button = next(b for b in analyze_tab.button if "Сгенерировать" in b.label)
    demo_post_button.click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Проанализировать" in b.label).click().run(timeout=60)
    assert not at.exception

    analyze_tab = at.tabs[1]
    # success короткий и сам исчезает (см. _flash_success) — персистентный
    # признак завершения: секция "Вердикты" из _render_analysis_results
    assert any("Вердикты" in m.value for m in analyze_tab.markdown)

    exp_path = tmp_path / "demo"
    assert (exp_path / "report.html").exists()
    assert (exp_path / "results.json").exists()


def test_analyze_tab_demo_button_works_for_non_demo_named_experiment(tmp_path, monkeypatch):
    """UX-регрессия: раньше кнопка была скрыта, если имя эксперимента не
    начиналось буквально с "demo" (exp_name.startswith("demo")) — т.е. для
    любого реального эксперимента пользователя кнопка не показывалась вообще.
    Теперь она должна быть доступна для ЛЮБОГО выбранного эксперимента."""
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("customer_checkout_test", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    analyze_tab = at.tabs[1]

    exp_select = next(s for s in analyze_tab.selectbox if s.key == "analyze_exp_select")
    assert exp_select.value == "customer_checkout_test"

    demo_button = next(b for b in analyze_tab.button if "Сгенерировать" in b.label)
    assert not demo_button.disabled
    demo_button.click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    captions = [c.value for c in analyze_tab.caption]
    assert any("строк" in c for c in captions)
    info_messages = [i.value for i in analyze_tab.info]
    assert any("Сгенерированы демо пост-данные" in i and "+3%" in i for i in info_messages)


def test_analyze_tab_demo_button_disabled_without_assignments(tmp_path, monkeypatch):
    n = 300
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("broken_exp", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)
    (tmp_path / "broken_exp" / "assignments.parquet").unlink()

    at = _fresh_app(tmp_path, monkeypatch)
    analyze_tab = at.tabs[1]

    demo_button = next(b for b in analyze_tab.button if "Сгенерировать" in b.label)
    assert demo_button.disabled
    assert demo_button.help is not None and "assignments" in demo_button.help


def test_analyze_tab_shows_help_expanders_under_charts_and_tables(tmp_path, monkeypatch):
    """Демо-эксперимент содержит continuous+CUPED, binary и ratio метрики, а также
    страту platform — значит forest/distribution(both types)/segment-forest все
    должны отрендериться, и под каждым — свернутый expander "Как читать"."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Сгенерировать" in b.label).click().run(timeout=30)
    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Проанализировать" in b.label).click().run(timeout=60)
    assert not at.exception

    analyze_tab = at.tabs[1]
    expander_labels = [e.label for e in analyze_tab.expander]
    chart_help_count = expander_labels.count("❓ Как читать этот график?")
    table_help_count = expander_labels.count("❓ Как читать эту таблицу?")

    # 3 метрики: forest (x3) + распределения (revenue continuous, clicks binary,
    # conv_rate распределение недоступно т.к. ratio не попадает в raw_values... но
    # сегменты (platform) должны быть хотя бы у части метрик
    assert chart_help_count >= 3  # как минимум по одному forest на метрику
    assert table_help_count >= 1  # хотя бы "Таблица результатов"

    # сегментный warning показан НАД графиком, не только в expander
    assert any("Сегментные разрезы" in w.value for w in analyze_tab.warning)


def test_analyze_tab_distribution_chart_has_p99_clip_toggle_and_caption(tmp_path, monkeypatch):
    """UX10: гистограммы continuous-метрик по умолчанию обрезаны по P99 (с
    подписью-caption), а st.toggle "Показать полный диапазон" (выключен по
    умолчанию) снимает обрезку."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Сгенерировать" in b.label).click().run(timeout=30)
    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Проанализировать" in b.label).click().run(timeout=60)
    assert not at.exception

    analyze_tab = at.tabs[1]
    toggles = [t for t in analyze_tab.toggle if t.key and t.key.startswith("dist_full_range_")]
    assert toggles
    assert all(t.value is False for t in toggles)
    captions = [c.value for c in analyze_tab.caption]
    assert any("99-м перцентилем" in c and "последний столбец" in c for c in captions)

    n_clip_captions_before = sum("99-м перцентилем" in c for c in captions)

    toggles[0].set_value(True).run(timeout=30)
    assert not at.exception
    analyze_tab = at.tabs[1]
    captions_after = [c.value for c in analyze_tab.caption]
    n_clip_captions_after = sum("99-м перцентилем" in c for c in captions_after)
    # включили "полный диапазон" для одной метрики -> хотя бы одной P99-подписью меньше
    assert n_clip_captions_after < n_clip_captions_before


def test_analyze_tab_shows_detailed_results_table_and_csv_download(tmp_path, monkeypatch):
    """UX11: "Детальная таблица результатов" со всеми колонками (эффекты, ДИ,
    p-value/p-adj, n control/test, снижение дисперсии, вердикт) + кнопка
    скачивания CSV."""
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "Спроектировать" in b.label).click().run(timeout=30)
    assert not at.exception

    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Сгенерировать" in b.label).click().run(timeout=30)
    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Проанализировать" in b.label).click().run(timeout=60)
    assert not at.exception

    analyze_tab = at.tabs[1]
    assert any("Детальная таблица результатов" in m.value for m in analyze_tab.markdown)

    dfs = analyze_tab.dataframe
    detailed_df = next(
        d.value for d in dfs
        if {"Метрика", "Группа сравнения", "Метод", "Designed", "Вердикт"} <= set(d.value.columns)
    )
    assert len(detailed_df) > 0
    expected_columns = {
        "Метрика", "Группа сравнения", "Метод", "Designed", "Эффект (абс)",
        "Эффект (отн, %)", "95% ДИ (отн.)", "p-value", "p-adj", "Коррекция",
        "n (control)", "n (test)", "Снижение дисперсии", "Вердикт",
    }
    assert expected_columns <= set(detailed_df.columns)
    assert "✓" in detailed_df["Designed"].values
    # отсортировано по (метрика, метод)
    keys = list(zip(detailed_df["Метрика"], detailed_df["Метод"]))
    assert keys == sorted(keys)

    # st.download_button — отдельный тип элемента в AppTest (не st.button),
    # доступен только через Block.get("download_button") (не через .button)
    download_buttons = [b for b in analyze_tab.get("download_button") if b.label == "Скачать таблицу CSV"]
    assert len(download_buttons) == 1


def test_analyze_tab_shows_cumulative_lift_warning_and_help(tmp_path, monkeypatch):
    n = 300
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    experiment = Experiment.design(config, data, experiments_dir=tmp_path)

    rng = np.random.default_rng(50)
    rows = []
    for _, r in experiment.assignments.iterrows():
        for day in range(3):
            rows.append(
                {
                    "user_id": r["unit_id"],
                    "event_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                    "revenue": rng.normal(10, 3),
                    "revenue_pre": rng.normal(10, 3),
                    "clicks": rng.binomial(1, 0.1),
                    "orders": rng.integers(0, 2),
                    "sessions": rng.integers(1, 3),
                }
            )
    daily_data = pd.DataFrame(rows)

    monkeypatch.setenv("ABKIT_EXPERIMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ABKIT_FLASH_SECONDS", "0")
    at = AppTest.from_file("app.py")
    at.run(timeout=30)
    at.session_state["analyze_data"] = daily_data
    at.run(timeout=30)

    analyze_tab = at.tabs[1]
    date_select = next(s for s in analyze_tab.selectbox if s.key == "analyze_date_col")
    date_select.set_value("event_date").run(timeout=30)

    analyze_tab = at.tabs[1]
    next(b for b in analyze_tab.button if "Проанализировать" in b.label).click().run(timeout=60)
    assert not at.exception

    analyze_tab = at.tabs[1]
    assert any("post-hoc диагностики" in w.value for w in analyze_tab.warning)
    expander_labels = [e.label for e in analyze_tab.expander]
    assert expander_labels.count("❓ Как читать этот график?") >= 1


def test_analyze_date_col_reset_with_warning_on_new_data(tmp_path, monkeypatch):
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    post_data = pd.DataFrame(
        {
            "user_id": [f"u{i}" for i in range(n)],
            "revenue": np.random.default_rng(1).normal(100, 20, size=n),
            "clicks": np.random.default_rng(1).binomial(1, 0.1, size=n),
            "orders": np.random.default_rng(1).integers(0, 5, size=n),
            "sessions": np.random.default_rng(1).integers(1, 10, size=n),
            "event_date": pd.date_range("2024-01-01", periods=n, freq="h"),
        }
    )
    at.session_state["analyze_data"] = post_data
    at.run(timeout=30)

    analyze_tab = at.tabs[1]
    date_select = next(s for s in analyze_tab.selectbox if s.key == "analyze_date_col")
    date_select.set_value("event_date").run(timeout=30)

    new_post = post_data.drop(columns=["event_date"])
    at.session_state["analyze_data"] = new_post
    at.run(timeout=30)

    assert not at.exception
    analyze_tab = at.tabs[1]
    warning_texts = " ".join(w.value for w in analyze_tab.warning)
    assert "event_date" in warning_texts and "не найдена" in warning_texts
    date_select_after = next(s for s in analyze_tab.selectbox if s.key == "analyze_date_col")
    assert date_select_after.value == "(нет)"


def test_experiments_tab_shows_registry_and_status_transition(tmp_path, monkeypatch):
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    assert not at.exception

    row_texts = [m.value for m in exp_tab.markdown]
    assert any("demo" in t for t in row_texts)
    assert any("designed" in t for t in row_texts)  # цветной бейдж статуса

    forward_button = next(b for b in exp_tab.button if b.key == "exp_forward_demo")
    forward_button.click().run(timeout=30)
    assert not at.exception

    registry = storage.read_registry(tmp_path)
    assert registry["demo"]["status"] == "running"
    exp_tab = at.tabs[2]
    assert any("running" in m.value for m in exp_tab.markdown)


def test_experiments_tab_row_shows_owner_and_status_columns(tmp_path, monkeypatch):
    """Superset-стиль реестра (файловый режим — владелец всегда "-", своего
    понятия владельца в файловом режиме нет)."""
    n = 300
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    assert not at.exception
    exp_tab = at.tabs[2]
    header_labels = [m.value for m in exp_tab.markdown]
    assert any("Название" in h for h in header_labels)
    assert any("Владелец" in h for h in header_labels)
    assert any("Статус" in h for h in header_labels)


def test_experiments_tab_detail_panel_shows_report_iframe_after_expand(tmp_path, monkeypatch):
    """Отчет теперь внутри детальной панели (клик по ▸), не на верхнем уровне таба."""
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    assert not exp_tab.get("iframe")  # свернуто по умолчанию

    toggle_button = next(b for b in exp_tab.button if b.key == "exp_toggle_demo")
    toggle_button.click().run(timeout=30)
    assert not at.exception

    exp_tab = at.tabs[2]
    assert len(exp_tab.get("iframe")) == 1


def test_experiments_tab_detail_panel_shows_samples_section(tmp_path, monkeypatch):
    """Регрессия: секция скачивания выборок должна быть доступна для ЛЮБОГО
    ранее спроектированного эксперимента через таб Experiments (детальная
    панель), а не только сразу после дизайна в табе Design в текущей сессии."""
    n = 500
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    next(b for b in exp_tab.button if b.key == "exp_toggle_demo").click().run(timeout=30)
    assert not at.exception

    exp_tab = at.tabs[2]
    assert any("Выборки для передачи" in m.value for m in exp_tab.markdown)
    assert any("control.csv" in m.value for m in exp_tab.markdown)
    assert any("treatment.csv" in m.value for m in exp_tab.markdown)


def test_experiments_tab_detail_panel_samples_missing_shows_info(tmp_path, monkeypatch):
    """Обратная совместимость: эксперимент, спроектированный до появления
    samples/ (например, старой версией кода), не должен ронять таб — только
    информативное сообщение."""
    n = 300
    data = generate_demo_design_data(n, seed=0)
    config = make_demo_design_config("demo", n, seed=0)
    Experiment.design(config, data, experiments_dir=tmp_path)
    import shutil

    shutil.rmtree(tmp_path / "demo" / "samples")

    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    next(b for b in exp_tab.button if b.key == "exp_toggle_demo").click().run(timeout=30)
    assert not at.exception

    exp_tab = at.tabs[2]
    assert any("не найдены" in i.value for i in exp_tab.info)


def test_experiments_tab_search_filters_by_name(tmp_path, monkeypatch):
    n = 200
    for name in ("alpha_checkout", "beta_signup"):
        data = generate_demo_design_data(n, seed=0)
        config = make_demo_design_config(name, n, seed=0)
        Experiment.design(config, data, experiments_dir=tmp_path)

    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    search = next(t for t in exp_tab.text_input if t.key == "exp_search")
    search.set_value("alpha").run(timeout=30)
    assert not at.exception

    exp_tab = at.tabs[2]
    row_texts = " ".join(m.value for m in exp_tab.markdown)
    assert "alpha_checkout" in row_texts
    assert "beta_signup" not in row_texts


def test_validation_tab_renders_without_error_when_no_experiments(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    val_tab = at.tabs[3]
    assert not at.exception
    assert any("Нет ни одного" in i.value for i in val_tab.info)


def test_experiments_tab_empty_registry_shows_info(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    exp_tab = at.tabs[2]
    assert not at.exception
    assert any("нет" in i.value.lower() for i in exp_tab.info)


def test_isolation_selectbox_has_human_readable_labels_and_four_modes(tmp_path, monkeypatch):
    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    design_tab = at.tabs[0]
    isolation_selects = [s for s in design_tab.selectbox if s.key == "design_isolation"]
    assert len(isolation_selects) == 1
    select = isolation_selects[0]
    assert select.value == "exclude"
    assert select.options == [
        "exclude — исключить участников всех активных тестов (рекомендуется)",
        "warn — показать пересечение и спросить подтверждение",
        "off — не исключать никого (осознанный риск пересечения)",
        "exclude_selected — исключить участников только выбранных тестов",
    ]
    # без выбора exclude_selected мультиселект экспериментов не показывается
    assert not [ms for ms in design_tab.multiselect if ms.key == "design_isolation_selected"]


def test_isolation_exclude_selected_multiselect_lists_only_active_experiments(tmp_path, monkeypatch):
    """exclude_selected должен предлагать на выбор только designed/running
    эксперименты — completed/archived не должны быть доступны для выбора
    (и, отдельно, не должны блокировать кандидатов — см. test_isolation*.py)."""
    n = 200
    running_data = generate_demo_design_data(n, seed=1)
    running_config = make_demo_design_config("running_exp", n, seed=1)
    Experiment.design(running_config, running_data, experiments_dir=tmp_path)

    archived_data = generate_demo_design_data(n, seed=2)
    archived_config = make_demo_design_config("archived_exp", n, seed=2)
    Experiment.design(archived_config, archived_data, experiments_dir=tmp_path)
    storage.update_status(tmp_path, "archived_exp", "archived")

    at = _fresh_app(tmp_path, monkeypatch)
    design_tab = at.tabs[0]
    next(b for b in design_tab.button if "демо-данные" in b.label).click().run(timeout=30)

    design_tab = at.tabs[0]
    select = next(s for s in design_tab.selectbox if s.key == "design_isolation")
    select.set_value("exclude_selected").run(timeout=30)

    design_tab = at.tabs[0]
    assert not at.exception
    multiselects = [ms for ms in design_tab.multiselect if ms.key == "design_isolation_selected"]
    assert len(multiselects) == 1
    assert multiselects[0].options == ["running_exp"]
