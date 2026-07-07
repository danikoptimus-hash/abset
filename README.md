# abkit

[![CI](https://github.com/danikoptimus-hash/abkit/actions/workflows/ci.yml/badge.svg)](https://github.com/danikoptimus-hash/abkit/actions/workflows/ci.yml)

Библиотека + веб-интерфейс на Streamlit (плюс минимальный CLI для автоматизации)
для дизайна и анализа A/B тестов: расчет мощности и MDE, (стратифицированное)
сплитование, проверки честности (SRM, баланс страт, потери данных, pre-period
A/A), пайплайн методов анализа (Welch, Z-тест пропорций, CUPED, бутстрап,
Mann-Whitney, дельта-метод для ratio-метрик), поправка на множественность,
HTML-отчеты и A/A/A/B-симуляции для валидации дизайна.

Streamlit — основной пользовательский интерфейс (кнопки, формы, графики); CLI —
тонкий слой поверх той же библиотеки для скриптов и автоматизации. Оба
работают с одними и теми же `Experiment`/`AnalysisResults` и одним
`experiments_dir`.

Полное техническое задание — в [`DESIGN.md`](DESIGN.md).

## Установка

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

## Быстрый старт: веб-интерфейс

```bash
.venv/Scripts/streamlit run app.py
```

Откроется приложение с четырьмя табами:

- **Design** — форма дизайна: загрузка исторических данных (`.csv`/`.parquet`),
  группы, метрики, страты, MDE/размер выборки. Кнопка **«Загрузить
  демо-данные»** сразу генерирует синтетику и заполняет форму — не нужно
  искать свои данные, чтобы попробовать интерфейс. После сабмита — сводка
  (размеры групп, MDE) и график, ссылка на `design_report.html`.
- **Analyze** — выбор эксперимента, загрузка фактических данных (для demo-
  экспериментов есть кнопка «Сгенерировать demo-данные (с эффектом)»),
  `compare_methods`, поправка на множественность. Результат — таблица,
  forest/distribution/segment/cumulative-графики, вердикты, скачивание
  `report.html`/`results.json`.
- **Experiments** — реестр экспериментов с фильтром по статусу, кнопки смены
  статуса, просмотр уже сохраненных отчетов прямо в приложении.
- **Validation** — A/A и A/B симуляции (`run_aa`/`run_ab`) с прогресс-баром.

Сайдбар показывает список экспериментов со статусами и текущий
`experiments_dir` (переопределяется переменной окружения
`ABKIT_EXPERIMENTS_DIR` или `settings.yaml`).

## Быстрый старт: CLI (`abkit demo`)

Тот же познакомительный сценарий без браузера — синтетика → design → analyze →
report одной командой:

```bash
.venv/Scripts/python cli.py demo
```

Команда создаст эксперимент `demo` в `experiments_dir`, покажет таблицы
размеров групп, MDE и результатов анализа, и напечатает пути к
`design_report.html` и `report.html`. Повторный запуск создаст `demo_2`,
`demo_3` и т.д., не конфликтуя с предыдущими.

## CLI: design → analyze → validate

CLI сохраняет все команды из предыдущих этапов разработки — полезно для
скриптов, автоматизации и быстрой проверки из терминала. Интерактивный опрос
при дизайне (когда-то через `questionary`) как основной способ работы больше
не развивается — эта роль перешла к табу Design в Streamlit; сам режим в CLI
не убран, но новых сценариев в нем не появится.

### 1. Дизайн эксперимента

Опишите эксперимент в `design.yaml`:

```yaml
name: checkout_button_color
unit_col: user_id
groups:
  control: 0.5
  treatment: 0.5
metrics:
  - name: revenue
    type: continuous
    pre_col: revenue_pre       # если есть pre-period колонка — включит CUPED
  - name: converted
    type: binary
    role: secondary
strata: [platform]
mde: 0.05                       # относительный лифт; альтернативы: sample_size, либо ни то ни другое ("все доступные")
split_method: stratified
isolation: exclude              # исключить юзеров из других активных экспериментов
```

Исторические данные (`historical.csv`) должны содержать `unit_col`, колонки
метрик (или `pre_col`) и колонки страт. Затем:

```bash
.venv/Scripts/python cli.py design --config design.yaml --data historical.csv
```

Команда посчитает мощность/MDE, исключит юзеров из других активных
экспериментов, сделает сплит, проверит SRM/баланс страт/pre-period A/A и
сохранит эксперимент в `<experiments_dir>/checkout_button_color/` вместе с
`design_report.html`.

Без `--config` команда переходит в интерактивный режим (questionary): вопросы
про `unit_col`, группы, метрики, страты, MDE/размер выборки. Ответы можно
сохранить флагом `--save-config out.yaml` для повторного использования.

### 2. Анализ по фактическим данным

```bash
.venv/Scripts/python cli.py analyze checkout_button_color --data post_period.csv --compare
```

- `--compare` — дополнительно считает альтернативные методы (Welch сырой,
  +trim 1%, +CUPED, Bootstrap BCa, Mann-Whitney) для проверки устойчивости
  выводов; они не влияют на вердикт.
- `--correction holm|bonferroni|bh` — поправка на множественность (по
  умолчанию `holm`).
- `--date-col event_date` — если в данных есть колонка даты, добавляет в отчет
  график кумулятивного лифта по дням.

Команда печатает таблицу результатов и всегда пишет `report.html` +
`results.json` в папку эксперимента.

### 3. Валидация симуляциями

Перед тем как доверять дизайну, стоит проверить его симуляциями на исторических
данных — честный ли FPR у выбранных методов и какая реальная мощность:

```bash
.venv/Scripts/python cli.py validate checkout_button_color --data historical.csv --n-sims 2000 --effect 0.05
```

Без `--effect` считается только A/A (эмпирический FPR с доверительным
интервалом); с `--effect` — дополнительно A/B (эмпирическая мощность против
аналитической).

### 4. Реестр экспериментов

```bash
.venv/Scripts/python cli.py list --active
.venv/Scripts/python cli.py status checkout_button_color running
```

## Python API

Тот же цикл доступен напрямую из кода:

```python
import pandas as pd
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment

config = DesignConfig(
    name="checkout_button_color",
    unit_col="user_id",
    groups={"control": 0.5, "treatment": 0.5},
    metrics=[MetricConfig(name="revenue", type="continuous")],
    mde=0.05,
    split_method="stratified",
    strata=["platform"],
)

historical_data = pd.read_csv("historical.csv")
experiment = Experiment.design(config, historical_data)
print(experiment.report.power_results["revenue"].sample_size_per_group)

# ... дожидаемся окончания эксперимента, собираем post_data ...

post_data = pd.read_csv("post_period.csv")
results = experiment.analyze(post_data, compare_methods=True)
results.summary()                       # таблица в консоль (rich)
print(results.verdict("revenue", treatment_group="treatment"))
report_path = results.report()          # report.html + results.json
```

Повторный `experiment.analyze()` на тех же данных с теми же аргументами дает
бит-в-бит тот же `results.json` — все случайности (сплит, бутстрап) идут от
`seed` из `config.yaml`.

## Серверный режим: учетки, роли, Postgres, Docker

Всё выше — «lite»-режим (`ABKIT_MODE=file`, дефолт): локальный
однопользовательский инструмент, без установки чего-либо кроме Python.

Для командной работы (общий доступ, роли Viewer/Editor/Admin, аудит-лог,
Postgres вместо файлов) есть серверный режим (`ABKIT_MODE=db`),
разворачиваемый в Docker одной командой:

```bash
git clone https://github.com/danikoptimus-hash/abkit.git && cd abkit
cp .env.example .env   # отредактировать ABKIT_SECRET_KEY, POSTGRES_PASSWORD
docker compose up -d
docker compose exec app abkit-admin create-admin --email admin@co.com
```

Подробности — [`docker/README.md`](docker/README.md) (деплой, бэкап, импорт
существующих файловых экспериментов) и [`DOCKER.md`](DOCKER.md) (полное
техническое задание серверного режима).

## Структура проекта

```
abkit/
├── config.py            # DesignConfig, MetricConfig (pydantic)
├── storage.py            # папки экспериментов, registry.json
├── experiment.py          # Experiment: design()/load()/analyze()
├── pipeline.py            # Step/Pipeline/MetricContext
├── checks.py              # SRM, баланс страт, дубли, потери, pre-A/A
├── design/                 # power.py, splitter.py, stratification.py, isolation.py
├── analysis/                # tests.py, variance_reduction.py, multiple_testing.py, results.py
├── preprocessing/          # outliers.py (RemoveOutliers, Winsorize, Log1p)
├── viz/                     # plots.py (plotly), report.py (jinja2)
├── validation/             # simulation.py (run_aa, run_ab)
└── demo_data.py            # синтетика для abkit demo / кнопки «Загрузить демо-данные»
app.py                      # Streamlit: табы Design / Analyze / Experiments / Validation
cli.py                      # typer: design, analyze, validate, status, list, demo
templates/                  # report.html.j2, design_report.html.j2
tests/                      # pytest: юнит + статистические/симуляционные тесты + AppTest
```

## Тесты

```bash
.venv/Scripts/python -m pytest
```

Статистические тесты (FPR методов в допуске, CUPED снижает дисперсию,
дельта-метод держит FPR на кластерных ratio-данных) — часть обязательного
прогона: любой рефакторинг, ломающий FPR или мощность, валит тесты.

Streamlit-приложение проверяется через `streamlit.testing.v1.AppTest`
(`tests/test_app.py`) — запускает `app.py` в headless-режиме, кликает по
кнопкам (демо-данные → дизайн → анализ, смена статуса, просмотр отчета) и
проверяет, что скрипт не падает и нужные элементы отрендерились.
