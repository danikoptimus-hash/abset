# abkit

[![CI](https://github.com/danikoptimus-hash/abkit/actions/workflows/ci.yml/badge.svg)](https://github.com/danikoptimus-hash/abkit/actions/workflows/ci.yml)

Библиотека + веб-интерфейс на React (плюс минимальный CLI для автоматизации)
для дизайна и анализа A/B тестов: расчет мощности и MDE, (стратифицированное)
сплитование, проверки честности (SRM, баланс страт, потери данных, pre-period
A/A), пайплайн методов анализа (Welch, Z-тест пропорций, CUPED, бутстрап,
Mann-Whitney, дельта-метод для ratio-метрик), поправка на множественность,
HTML-отчеты и A/A/A/B-симуляции для валидации дизайна.

Два режима: **lite** (файловый, локально, без установки чего-либо кроме
Python — CLI и Python API ниже) и **серверный** (React-UI + FastAPI backend +
Postgres, роли/аудит-лог, разворачивается в Docker — раздел «Серверный режим»
ниже). Оба работают с одними и теми же `Experiment`/`AnalysisResults`.

Полное техническое задание — в [`DESIGN.md`](DESIGN.md) (ядро) и
[`FRONTEND.md`](FRONTEND.md) (React-UI/backend).

## Установка

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

Эта установка дает CLI и Python API (файловый режим, ниже) — работает локально
без Docker. Веб-интерфейс (React-UI) доступен только в серверном режиме
(`docker compose up`, раздел «Серверный режим» ниже) — отдельного
no-Docker веб-UI (как раньше был Streamlit) не существует.

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
не развивается — эта роль перешла к мастеру дизайна в React-UI (серверный
режим, см. ниже); сам режим в CLI не убран, но новых сценариев в нем не
появится.

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

## Серверный режим: React-UI, учетки, роли, Postgres, Docker

Всё выше (CLI, Python API) — «lite»-режим (`ABKIT_MODE=file`, дефолт):
локальный однопользовательский инструмент, без установки чего-либо кроме
Python, без веб-интерфейса.

Для командной работы (веб-интерфейс, общий доступ, роли Viewer/Editor/Admin,
аудит-лог, Postgres вместо файлов) есть серверный режим (`ABKIT_MODE=db`),
разворачиваемый в Docker одной командой:

```bash
git clone https://github.com/danikoptimus-hash/abkit.git && cd abkit
cp .env.example .env   # отредактировать ABKIT_SECRET_KEY, POSTGRES_PASSWORD
docker compose up -d
docker compose exec backend abkit-admin create-admin --email admin@co.com
# React-UI: http://localhost:8080
```

Веб-интерфейс: реестр экспериментов с поиском, мастер дизайна (выбор
датасета, группы/метрики/страты, MDE/размер выборки, демо-данные в один
клик), анализ по фактическим данным (таблица, forest/distribution/
segment/cumulative-графики, вердикты), валидация A/A и A/B симуляциями,
управление пользователями и аудит-лог (роль Admin).

Данные для дизайна/анализа/валидации — **датасеты** (страница `/datasets`,
единственное место загрузки файлов): либо загруженный CSV/parquet, либо
результат SQL-запроса к подключенной внешней БД (PostgreSQL/ClickHouse/MSSQL,
настраивается в Settings → Data → Database Connections, только Admin, пароли
шифруются). Подробности — [`docker/README.md`](docker/README.md) §«Подключения
к базам данных».

Подробности — [`docker/README.md`](docker/README.md) (деплой, бэкап, импорт
существующих файловых экспериментов), [`DOCKER.md`](DOCKER.md) (техническое
задание модели ролей/аутентификации/аудита/БД) и [`FRONTEND.md`](FRONTEND.md)
(архитектура React-UI/backend).

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
└── demo_data.py            # синтетика для abkit demo / демо-данных в React-UI
cli.py                      # typer: design, analyze, validate, status, list, demo
backend/                    # FastAPI: REST API поверх abkit (см. FRONTEND.md)
frontend/                   # React-UI (см. FRONTEND.md)
templates/                  # report.html.j2, design_report.html.j2
tests/                      # pytest: юнит + статистические/симуляционные тесты
```

## Тесты

```bash
.venv/Scripts/python -m pytest
```

Статистические тесты (FPR методов в допуске, CUPED снижает дисперсию,
дельта-метод держит FPR на кластерных ratio-данных) — часть обязательного
прогона: любой рефакторинг, ломающий FPR или мощность, валит тесты.

React-UI и backend — отдельные наборы тестов (`backend/tests/`, pytest;
`frontend/` — typecheck/lint/build + Playwright e2e против реального docker
compose стека), см. [`CLAUDE.md`](CLAUDE.md) и `.github/workflows/ci.yml`.
