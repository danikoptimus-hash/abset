# abkit — фреймворк для дизайна и анализа A/B тестов

Техническое задание для реализации. Язык: Python 3.11+. Все идентификаторы кода — на английском, документация и сообщения CLI — на русском.

> **Примечание (после R8 FRONTEND.md):** Streamlit (`app.py`, упоминается ниже как исходный/основной UI) удален — актуальный интерфейс: React-UI + FastAPI backend, см. [FRONTEND.md](FRONTEND.md). Разделы ниже про Streamlit оставлены как есть — это техзадание с историей этапов реализации, а не описание текущего состояния интерфейса. Ядро (`abkit/`, разделы 2-6, 8) не менялось и остается актуальным.

## 1. Цель

Библиотека + веб-интерфейс на Streamlit (плюс минимальный CLI для автоматизации), которые закрывают полный цикл A/B теста:

1. **Дизайн**: загрузка кандидатов → изоляция от других тестов → расчет мощности/MDE → (стратифицированное) сплитование → проверки сплита → сохранение в папку эксперимента.
2. **Анализ**: загрузка фактических данных → джойн с назначениями → проверки честности → пайплайн методов по каждой метрике → поправка на множественность → HTML-отчет.

Ключевые свойства: воспроизводимость (seed везде, конфиг = единый источник правды), комбинируемость методов (пайплайн шагов), честность (SRM-проверки, изоляция, разделение primary/exploratory), валидируемость (A/A и A/B симуляции).

## 2. Структура проекта

```
abkit/
├── abkit/
│   ├── __init__.py            # экспорт: Experiment, DesignConfig, шаги пайплайна
│   ├── config.py              # DesignConfig, MetricConfig (pydantic-модели)
│   ├── experiment.py          # Experiment: design(), load(), analyze(), статусы
│   ├── design/
│   │   ├── power.py           # sample size, MDE, мощность (в т.ч. с учетом CUPED)
│   │   ├── splitter.py        # simple, stratified, hash-сплит
│   │   ├── stratification.py  # построение страт, бакетирование, склейка редких
│   │   └── isolation.py       # исключение пользователей активных экспериментов
│   ├── analysis/
│   │   ├── tests.py           # TTest, WelchTTest, MannWhitney, Bootstrap,
│   │   │                      # ZTestProportions, DeltaMethodTTest
│   │   ├── variance_reduction.py  # CUPED, PostStratification
│   │   ├── multiple_testing.py    # bonferroni, holm, benjamini-hochberg
│   │   └── results.py         # TestResult, MetricResult, AnalysisResults
│   ├── preprocessing/
│   │   └── outliers.py        # RemoveOutliers, Winsorize, Log1p
│   ├── pipeline.py            # Pipeline: валидация и исполнение цепочки шагов
│   ├── checks.py              # SRM, баланс страт, сверка потерь, дубли
│   ├── viz/
│   │   ├── plots.py           # plotly-графики (см. раздел 8)
│   │   └── report.py          # сборка HTML через jinja2
│   ├── validation/
│   │   └── simulation.py      # A/A и A/B симуляции
│   └── storage.py             # папки экспериментов, registry.json, чтение/запись
├── cli.py                     # typer: design, analyze (минимальный, для скриптов/автоматизации)
                                # UI — React + FastAPI backend (frontend/, backend/), см. FRONTEND.md
├── templates/report.html.j2   # jinja2-шаблон отчета
├── tests/                     # pytest: юнит + статистические симуляционные тесты
├── settings.yaml              # глобальные настройки (см. 2.1)
└── pyproject.toml
```

### 2.1 Глобальные настройки (settings.yaml)

```yaml
experiments_dir: ~/ab_experiments   # корень папки экспериментов, переопределяется env ABKIT_EXPERIMENTS_DIR
default_alpha: 0.05
default_power: 0.8
default_correction: holm
random_seed: null                   # null = генерировать и сохранять в конфиг эксперимента
```

## 3. Ключевые абстракции

### 3.1 DesignConfig / MetricConfig (config.py)

Pydantic-модели. Сериализуются в `config.yaml` папки эксперимента вместе со всеми вычисленными на дизайне величинами (фактический seed, размеры групп, MDE, оценки дисперсий, ρ для CUPED).

```python
class MetricConfig(BaseModel):
    name: str
    type: Literal["continuous", "binary", "ratio"]
    role: Literal["primary", "secondary"] = "primary"
    pre_col: str | None = None        # колонка pre-period -> включает CUPED
    num: str | None = None            # для ratio
    den: str | None = None            # для ratio
    default_methods: list[str] | None = None  # переопределение цепочки анализа

class DesignConfig(BaseModel):
    name: str
    unit_col: str
    groups: dict[str, float]          # {"control": 0.5, "treatment": 0.5}, сумма == 1
    metrics: list[MetricConfig]
    alpha: float = 0.05
    power: float = 0.8
    mde: float | None = None          # ровно одно из mde / sample_size / use_all
    sample_size: int | None = None
    split_method: Literal["simple", "stratified", "hash"] = "stratified"
    strata: list[str] = []
    n_buckets_continuous: int = 4     # квантильных бакетов для непрерывных страт
    min_stratum_size: int = 20        # мельче -> склейка в "_other_"
    hash_salt: str | None = None
    isolation: Literal["exclude", "warn", "off", "exclude_selected"] = "exclude"
    exclude_experiments: Literal["all_active"] | list[str] = "all_active"
    isolation_selected_experiments: list[str] = []  # только для isolation="exclude_selected"
    seed: int | None = None
```

### 3.2 Pipeline (pipeline.py)

Каждый шаг — класс с интерфейсом:

```python
class Step(ABC):
    stage: ClassVar[Literal["preprocess", "variance_reduction", "test"]]
    def apply(self, ctx: MetricContext) -> MetricContext: ...
```

`MetricContext` несет: данные (значения метрики + group + strata + ковариаты), метаданные о примененных шагах, и (после шага stage="test") — `TestResult`. Пайплайн валидирует порядок стадий: preprocess → variance_reduction → test, ровно один test-шаг. Нарушение порядка — ошибка; методологически спорные комбинации (MannWhitney после CUPED) — warning в результат и в отчет.

Требование к preprocess-шагам: параметры (например, квантиль обрезки) вычисляются на объединенных данных обеих групп и применяются одинаково к обеим группам.

### 3.3 TestResult (analysis/results.py)

Единый формат результата любого критерия:

```python
@dataclass
class TestResult:
    metric: str
    method: str                      # человекочитаемое имя цепочки, напр. "Welch + CUPED"
    effect_abs: float
    effect_rel: float                # лифт в долях
    ci_abs: tuple[float, float]
    ci_rel: tuple[float, float]
    p_value: float
    p_value_adjusted: float | None
    n: dict[str, int]                # размер групп после препроцессинга
    n_removed: dict[str, int]        # сколько наблюдений отрезано
    variance_reduction: float | None # достигнутое снижение дисперсии (CUPED/пост-страт)
    warnings: list[str]
    is_designed_method: bool         # цепочка заявлена в дизайне (а не added в compare)
```

`AnalysisResults` агрегирует TestResult'ы, дает `.summary()` (консольная таблица, rich), `.report()` (HTML), `.to_json()`, доступ `results["revenue"]`, и вердикты по primary-метрикам: significant_positive / significant_negative / no_effect_detected.

## 4. Этап дизайна (Experiment.design)

Поток шагов — строго в этом порядке:

1. **Загрузка и валидация данных.** Вход: parquet/csv/DataFrame, одна строка = один пользователь. Проверки: уникальность `unit_col`, отсутствие NaN в unit_col и strata, существование всех заявленных колонок (метрики pre-period, страты). Ошибки — сразу, с понятным сообщением.
2. **Изоляция (design/isolation.py).** Читает `registry.json`, для экспериментов в статусах `designed` и `running` (минус исключения по `exclude_experiments`) собирает set юзеров из их `assignments.parquet` и удаляет их из кандидатов. Режимы: `exclude` — молча исключить (из всех активных, кроме `exclude_experiments`) и отразить в отчете; `warn` — показать размер пересечения и спросить подтверждение (в CLI); `off` — пропустить; `exclude_selected` — молча исключить, но только из юнитов, занятых экспериментами, явно перечисленными в `isolation_selected_experiments` (INCLUDE-список, в отличие от `exclude_experiments`). Во всех режимах, кроме `off`, учитываются только статусы `designed`/`running` — `completed`/`archived` никогда не блокируют кандидатов. Вывод в лог/отчет: было N, занято K (с разбивкой по экспериментам), доступно N−K.
3. **Расчет мощности (design/power.py).** Оценка дисперсии каждой метрики по историческим данным (pre-period колонки; если pre-period нет — CLI просит либо колонку-прокси, либо дисперсию руками). Два режима: задан `mde` → требуемый sample size (сверка с доступным, при нехватке — сообщение с вариантами: достижимый MDE, требуемая длительность); задан `sample_size`/«все доступные» → достижимый MDE по каждой метрике. Если у метрики есть `pre_col`: посчитать ρ(metric, pre) и выдать MDE в двух вариантах — без CUPED и с CUPED (дисперсия × (1−ρ²)) — одинаково для continuous И binary (баг, где binary-метрики молча пропускали CUPED-ветку, исправлен: до фикса `mde_rel_cuped`/`sample_size_per_group_cuped` оставались `None`, хотя ρ уже считался). Формулы без CUPED: для continuous — стандартная двухвыборочная (z-приближение), для binary — точный тест по пропорциям (`sample_size_binary`/`mde_binary`), для ratio — дисперсия дельта-методом. С CUPED: для continuous — та же z-формула на дисперсии × (1−ρ²); для binary — CUPED-остаток уже не строго 0/1, поэтому вместо точного теста по пропорциям используют нормальное приближение на дисперсии Бернулли p(1−p) × (1−ρ²) (`abkit/design/power.py::mde_binary_cuped`/`sample_size_binary_cuped`, дисперсия — `binary_variance`). Учет числа групп (сравнений с контролем) и будущей поправки на множественность по primary-метрикам.
4. **Стратификация (design/stratification.py).** Непрерывные страты → квантильные бакеты (`n_buckets_continuous`), декартово произведение страт, страты размером < `min_stratum_size` → склейка в `_other_`. Результат — колонка `stratum` у каждого пользователя. Пропуски (NaN) в стратификационных колонках обрабатываются по `nan_strategy` из `DesignConfig` (не приводят к ошибке валидации по умолчанию): `separate_stratum` (по умолчанию) — NaN заменяется на строку `"unknown"`, юзер попадает в свою (под)страту (при декартовом произведении — `"unknown"` только в позиции пострадавшей колонки, например `gender=NaN, platform=ios` → `"unknown|ios"`); мелкая `"unknown"`-страта, как и любая другая, склеивается в `_other_`, если меньше `min_stratum_size`. `drop` — юзеры с пропусками в любой из страта-колонок удаляются из кандидатов до расчета мощности и сплита. `error` — старое поведение, падать с понятной ошибкой (для тех, кто хочет жестко валидировать входные данные). Независимо от стратегии, дизайн-отчет показывает число и долю пропусков по каждой колонке; при доле > 5% — отдельное предупреждение о качестве данных.
5. **Сплитование (design/splitter.py).** `simple`: перемешивание с seed, нарезка по долям. `stratified`: то же внутри каждой страты. `hash`: группа = по значению `sha256(salt + unit_id)`, соль генерируется если не задана и сохраняется в конфиг; страты при hash-сплите не гарантируют баланс — предупредить.
6. **Проверки сплита (checks.py).** SRM: chi-square факт. долей против заявленных, порог p < 0.001 = провал. Баланс страт: chi-square таблицы stratum × group. Pre-period A/A: t-test по каждой метрике с pre_col между группами; значимые различия — красный флаг. Все три — в design_report.
7. **Сохранение (storage.py).** Создать `experiments_dir/<name>/`, записать `config.yaml` (полный конфиг + вычисленное), `assignments.parquet` (unit_id, group, stratum, assigned_at), `design_report.html`, лог. Дополнительно — по одному CSV на каждую группу в подпапке `samples/` (`samples/<group>.csv`, колонки unit_id/stratum/assigned_at, UTF-8 без BOM, разделитель запятая, перевод строки `\n`) для передачи выборок в продуктовые системы; `assignments.parquet` остается основным рабочим форматом для джойна на этапе анализа. Зарегистрировать в `registry.json` со статусом `designed`. Имя эксперимента уникально; коллизия — ошибка с подсказкой.

### 4.1 Реестр и статусы (storage.py)

`registry.json`: `{name: {status, created_at, started_at, completed_at, path}}`. Статусы: `designed → running → completed → archived`. Переходы — командой CLI `abkit status <name> <new_status>`. Изоляция учитывает `designed` и `running`. Запись в реестр — атомарная (запись во временный файл + rename), на случай параллельных запусков — файловая блокировка (filelock).

## 5. Этап анализа (Experiment.load(...).analyze(...))

Поток:

1. **Загрузка.** `Experiment.load(name)` читает config.yaml и assignments.parquet. `analyze(data=...)` принимает фактические данные: unit_col + колонки метрик (+ pre-period колонки для CUPED, если их нет в assignments).
2. **Дедупликация/агрегация, джойн и проверки честности (checks.py).** `analyze()` принимает опциональный `date_col`. Если по `unit_col` нет дублей — данные используются как есть (одна строка = один юзер). Если дубли есть и `date_col` не передан — ошибка с инструкцией (агрегировать заранее либо передать `date_col`). Если дубли есть и `date_col` передан — исходные данные (`data`, разбивка юзер × день) сохраняются для кумулятивного лифта, а для основного анализа строится агрегат `main_data` через `aggregate_post_data()`: по каждой метрике сворачиваем к одной строке на юзера — continuous по умолчанию суммой, binary — максимумом (был ли хоть раз положительный исход), ratio — сумма num и сумма den раздельно по колонкам с последующим делением на уровне юзера (не среднее подневных отношений). Способ агрегации переопределяем per-metric через `agg_methods` (`sum`/`max`/`last`/`first`; `last`/`first` — по последней/первой дате юзера в тесте, для метрик-снэпшотов). Далее inner join `main_data` с назначениями по unit_col. Проверки: SRM и сверка потерь (доля назначенных, но отсутствующих в данных, по группам + chi-square на симметричность потерь) считаются на `main_data`, то есть на юзерах после агрегации, а не на сырых строках.
3. **Пайплайн по метрикам.** Для каждой метрики цепочка шагов: из аргумента `methods`, иначе из `MetricConfig.default_methods`, иначе дефолт по типу: continuous → [WelchTTest] (+CUPED если есть pre_col), binary → [ZTestProportions], ratio → [DeltaMethodTTest]. Если сплит был stratified или в данных есть страты — PostStratification доступна как шаг. При >2 групп: каждая тритмент-группа против контроля, это учитывается в множественности.
4. **compare_methods=True.** Для каждой continuous-метрики дополнительно посчитать стандартный набор альтернатив: Welch сырой, Welch+trim 1%, Welch+CUPED (если есть pre_col), Bootstrap BCa, Mann-Whitney. Дизайн-цепочка помечается `is_designed_method=True` — решение принимается только по ней, остальные для устойчивости выводов.
5. **Множественность (analysis/multiple_testing.py).** Поправка (`holm` по умолчанию) применяется к семейству: primary-метрики × сравнения с контролем, только по designed-цепочкам. Secondary и любые метрики, отсутствовавшие в дизайне, — exploratory: показываются с сырыми и скорректированными p-value и пометкой, в вердикт не входят.
6. **Выход.** `AnalysisResults`; `results.report()` пишет `report.html` и `results.json` в папку эксперимента.

### 5.1 Реализация методов (analysis/)

- **WelchTTest / TTest**: scipy, ДИ через t-распределение. Относительный лифт и его ДИ — дельта-методом (Fieller как опция v2).
- **ZTestProportions**: statsmodels `proportions_ztest`, ДИ разности пропорций.
- **MannWhitney**: scipy; оценка эффекта — Hodges-Lehmann сдвиг + ДИ; в отчете пометить, что HL-оценка не равна разности средних.
- **Bootstrap(n=10000, method="bca"|"percentile")**: векторизованный numpy-ресэмплинг по пользователям, seed из конфига.
- **DeltaMethodTTest**: для ratio-метрик (num/den по группе) — дисперсия отношения дельта-методом; обязателен, когда единица анализа ≠ единице рандомизации; наивный t-test по строкам для ratio запрещен на уровне валидации пайплайна.
- **CUPED**: θ = cov(Y, X_pre)/var(X_pre) на объединенных данных; Y' = Y − θ(X_pre − mean(X_pre)). NaN в ковариате → импутация средним + warning с долей пропусков. В TestResult — фактическое снижение дисперсии.
- **PostStratification**: оценка эффекта как взвешенная по стратам разность средних, дисперсия — по формуле стратифицированной оценки.
- **RemoveOutliers(q)/Winsorize(q)**: порог по квантилю объединенных данных, применяется к обеим группам; в TestResult — n_removed по группам.
- **multiple_testing**: собственные реализации bonferroni/holm/BH (простые формулы) либо statsmodels `multipletests`.

## 6. Хранение: форматы файлов

Это описание — **lite-режим** (`ABKIT_MODE=file`, дефолт): локальный
однопользовательский инструмент без Docker/БД, тот, что реализован этапами
1-7 этого документа. Начиная с `DOCKER.md` есть второй, серверный режим
(`ABKIT_MODE=db`) — та же библиотека и Streamlit-приложение, но данные
(users/experiments/assignments/datasets/analysis_results/audit_log) живут в
Postgres, а не в файлах; выбор режима — через `abkit/experiment_store.py`
(`get_experiment_store()`, диспетчер по `ABKIT_MODE`), само хранилище — через
`abkit/db/` (репозитории, модели, Alembic-миграции). Оба режима используют
один и тот же `Experiment.design()/.load()/.analyze()` — статистическая
логика и форматы отчетов (report.html/results.json) не зависят от режима
хранения. Подробности серверного режима (роли, аутентификация, Docker,
аудит-лог) — см. `DOCKER.md`, этот раздел ниже описывает только lite-режим.

```
experiments/
├── registry.json
└── <experiment_name>/
    ├── config.yaml           # DesignConfig + computed: seed, соль, размеры, MDE, дисперсии, ρ
    ├── assignments.parquet   # unit_id, group, stratum, assigned_at — рабочий формат для джойна на analyze
    ├── samples/              # по одному CSV на группу — для передачи в продуктовые системы
    │   ├── control.csv        # unit_id, stratum, assigned_at; UTF-8 без BOM, разделитель ",", \n
    │   └── treatment.csv       # имя файла = имя группы + .csv
    ├── design_report.html
    ├── report.html           # появляется после analyze
    ├── results.json          # все TestResult + вердикты, машиночитаемо
    └── logs/                 # design.log, analyze.log
```

Все случайности (сплит, бутстрап, симуляции) — от seed из config.yaml. Повторный запуск analyze на тех же данных дает бит-в-бит тот же results.json.

CSV в `samples/` — производный, вспомогательный формат (генерируется из assignments.parquet сразу после сплита, только для выгрузки во внешние системы); источником истины остается `assignments.parquet`.

## 7. Веб-интерфейс (Streamlit, исторический раздел — см. примечание в начале файла)

> Актуальный UI — React, см. [FRONTEND.md](FRONTEND.md). Раздел ниже описывает Streamlit-реализацию, которая была основным интерфейсом до этапа R8 FRONTEND.md и с тех пор удалена из репозитория.

Streamlit — основной пользовательский интерфейс (кнопки, формы, графики); CLI — минимальный слой поверх той же библиотеки для скриптов, автоматизации и быстрой проверки из терминала. Оба работали с одними и теми же `Experiment`/`AnalysisResults` и одним `experiments_dir`.

`app.py` — основной интерфейс пользователя, четыре таба:

- **Design.** Перед загрузкой данных — онбординг-блок для пользователей без опыта работы с инструментом: заголовок «Шаг 1. Загрузите данные о ваших пользователях-кандидатах», пояснение формата (snapshot ПЕРЕД тестом, одна строка = юзер, что должно быть в файле), три `st.expander`: «Пример: как должны выглядеть данные» (пример-таблица интернет-магазина с разными типами pre-period метрик), «Как выгрузить данные из БД» (готовый SQL-пример), «Нет данных под рукой» (указывает на кнопку демо-данных). После загрузки — `st.success` с числом строк/колонок и чеклистом следующих шагов. Затем форма дизайна: загрузка исторических данных через `st.file_uploader` (csv/parquet); поля `unit_col`, группы (динамический список имя/доля), метрики (динамический список: имя, тип, роль, pre_col/num/den), страты (мультиселект по колонкам данных), режим размера (mde / sample_size / "все доступные"), split_method, изоляция. Кнопка **«Загрузить демо-данные»** — генерирует ту же синтетику, что и `abkit demo` в CLI (5000 юзеров), и сразу заполняет форму для знакомства с интерфейсом без своих данных под рукой. По сабмиту — вызов `Experiment.design()`, показ сводки (таблицы размеров групп и MDE), графиков через `st.plotly_chart`, кнопка скачать `design_report.html`. Секция «Выборки для передачи»: список CSV из `samples/` с размерами (строки/КБ), кнопка «Скачать» на каждый файл и отдельная кнопка «Скачать все выборки (ZIP)». Ошибки валидации конфига/данных показываются как `st.error` с понятным текстом, без трейсбека.
- **Analyze.** Аналогичный онбординг-блок перед загрузкой: заголовок «Шаг 1. Выберите эксперимент и загрузите данные теста», пояснение (данные ЗА период теста, назначения групп подтягиваются автоматически из assignments), `st.expander` с примером-таблицей и SQL-запросом (фильтр по периоду теста + `user_id IN (... assignments)`). Селектбокс существующего эксперимента (из реестра), загрузка фактических данных через `file_uploader`, чекбокс `compare_methods`, выбор поправки на множественность, опциональная колонка даты. По сабмиту — вызов `Experiment.analyze()`, таблица результатов, forest/distribution/segment/cumulative-графики через `st.plotly_chart`, карточки вердиктов по primary-метрикам (secondary — с пометкой exploratory), кнопки скачать `report.html`/`results.json`.
- **Experiments.** Таблица реестра (имя, статус, даты, путь) с фильтром по статусу; кнопки смены статуса (`running`/`completed`/`archived`); просмотр уже сохраненных `design_report.html`/`report.html` прямо в приложении (`st.iframe`); та же секция «Выборки для передачи» (список CSV из `samples/`, кнопки скачать/ZIP), что и в табе Design — доступна для любого ранее спроектированного эксперимента, а не только сразу после дизайна в текущей сессии.
- **Validation.** Выбор эксперимента, загрузка исторических данных, поля `n_sims`/`effect`/`compare_methods`, запуск `run_aa`/`run_ab` с прогресс-баром (`st.progress`), таблицы FPR/мощности по методам.

Сайдбар: список экспериментов со статусами (перерисовывается при каждом действии), текущий `experiments_dir`, быстрый переход между табами.

CLI (`cli.py`, typer) — минимальный слой для скриптов и автоматизации, без опросников в терминале; все команды, реализованные к моменту пивота на Streamlit, сохраняются как есть:

- `abkit design [--config design.yaml] --data historical.csv` — по готовому yaml (интерактивный опрос как основной UX перестал развиваться в терминале — эта роль перешла к Streamlit).
- `abkit analyze <name> --data post.parquet [--compare] [--correction holm]`
- `abkit validate <name> [--n-sims 2000] [--effect 0.02]` — A/A (и опционально A/B) симуляции на данных дизайна.
- `abkit status <name> <running|completed|archived>`; `abkit list [--active]` — таблица реестра.
- `abkit demo` — синтетика + полный прогон design → analyze → report для знакомства через терминал; в Streamlit ему соответствует кнопка «Загрузить демо-данные» в табе Design.

## 8. Визуализация и отчет (viz/)

Стек: plotly (self-contained HTML, include_plotlyjs="cdn" c fallback inline) + jinja2. Структура report.html:

1. Шапка: имя, даты, группы и размеры, бейджи проверок (SRM, потери, поправка, метод сплита).
2. Вердикты: карточки по primary-метрикам (эффект %, ДИ, p, статус цветом), secondary — отдельным рядом с пометкой exploratory.
3. Forest plot по каждой метрике: все посчитанные цепочки методов, designed-цепочка выделена; вертикаль на нуле.
4. Распределения: наложенные гистограммы + ECDF по группам, отметка порога обрезки.
5. Эффект по сегментам: forest plot в разрезе страт, с пометкой exploratory.
6. Динамика: кумулятивный лифт с ДИ по дням (если в данных есть колонка даты) + предупреждение про peeking.
7. Диагностика: таблица SRM, потери по группам, n_removed, variance_reduction факт vs ожидание из дизайна.
8. Аппендикс: полный config.yaml, версия abkit, seed.

design_report.html — упрощенный вариант: доступная выборка и изоляция, таблица MDE (без/с CUPED), баланс страт, SRM, pre-period A/A.

Под каждым графиком и под ключевыми таблицами (вердикты, SRM/потери, MDE) — свернутый по умолчанию блок «❓ Как читать этот график/эту таблицу?» (структура: «Что показано» / «Как читать» / «Когда что-то не так»). Тексты живут в одном месте — `abkit/viz/help_texts.py` (`get_help_text(chart_type)`), чтобы не разъезжаться между Streamlit (`st.expander`) и HTML-отчетом (`<details><summary>`, рендерится `render_help_html()` через jinja2-глобалы `help_details`/`chart_warning`). Для кумулятивного лифта и сегментного forest plot предупреждение (peeking, exploratory) дублируется НАД графиком постоянно видимым блоком — не только внутри expander.

## 8.1 Обязательные предупреждения над графиками

- Кумулятивный лифт: график только для post-hoc диагностики, решение — по последнему дню из дизайна; остановка теста по промежуточному дню (peeking) завышает FPR.
- Сегментный forest plot: разрезы по стратам — exploratory, поправка на множественность на них не применяется, использовать как основание для решения нельзя.

## 9. Валидация симуляциями (validation/simulation.py)

- `run_aa(data, config, n_sims, pipeline)` — n_sims раз: фейковый сплит по конфигу → полный пайплайн → сбор p-value. Выход: эмпирический FPR с биномиальным ДИ, распределение p-value (должно быть равномерным), для каждой цепочки методов из compare-набора. Критерий провала: ДИ FPR не накрывает alpha.
- `run_ab(data, config, n_sims, effect)` — то же + инъекция эффекта (аддитивный сдвиг/мультипликативный для continuous, флип для binary) → эмпирическая мощность; сверка с аналитической из power.py (расхождение > 5 п.п. — warning).
- Параллелизация joblib, прогресс-бар, результат — короткий HTML/консольная сводка.

## 10. Зависимости

pandas, numpy, scipy, statsmodels, pydantic v2, typer, rich, questionary, streamlit, plotly, jinja2, pyarrow, filelock, joblib, pytest.

## 11. План реализации (этапы для Claude Code)

Каждый этап заканчивается зелеными тестами; не переходить дальше, пока критерий готовности не выполнен.

**Этап 1 — скелет и хранение.** Структура пакета, config.py, storage.py (папки, registry, атомарность, filelock), заглушка CLI. Готовность: тесты на сериализацию конфига, создание/чтение папки эксперимента, конкурентную запись реестра.

**Этап 2 — дизайн.** stratification, splitter (все 3 метода), isolation, power, checks (SRM, баланс, pre-A/A), сборка Experiment.design. Готовность: юнит-тесты + статистические тесты (доли групп при simple/stratified сплите в допуске; hash-сплит детерминирован; изоляция реально исключает; sample size для известных кейсов совпадает с эталоном statsmodels в пределах 1%).

**Этап 3 — анализ, ядро.** pipeline, tests.py (Welch, Z-proportions), results.py, multiple_testing, checks на анализе, Experiment.analyze без отчета (summary в консоль). Готовность: p-value Welch совпадает со scipy на эталонах; A/A смоук-тест на синтетике: FPR в [3.5%, 6.5%] при 2000 симуляций.

**Этап 4 — продвинутые методы.** CUPED, PostStratification, Bootstrap, MannWhitney(HL), DeltaMethodTTest, preprocessing, compare_methods. Готовность: симуляционные тесты — CUPED снижает дисперсию на синтетике с известной ρ в пределах 10% от (1−ρ²); FPR каждого метода в допуске; дельта-метод на ratio с кластерной структурой держит FPR (а наивный t-test на тех же данных — нет, негативный тест).

**Этап 5 — визуализация и отчет.** plots.py, report.py, шаблон, results.json. Готовность: отчет генерируется на демо-данных, открывается офлайн, все секции на месте (снапшот-тест структуры HTML).

**Этап 6 — Streamlit-приложение и симуляции.** `validation/simulation.py` (run_aa/run_ab), `app.py` с табами Design / Analyze / Experiments / Validation (см. раздел 7); CLI (design/analyze/validate/status/list, без опросника) сохраняется как минимальный слой для автоматизации. Готовность: сквозной e2e-тест на уровне `Experiment`/`AnalysisResults` API — синтетический датасет → design → фейковые пост-данные с подсаженным эффектом → analyze → эффект задетектирован, отчет создан; отдельно — smoke-тест, что `app.py` запускается и рендерит все табы без ошибок (`streamlit.testing.v1.AppTest`, если версия Streamlit это поддерживает, иначе — ручная проверка `streamlit run app.py`).

**Этап 7 — полировка.** Сообщения об ошибках, README с примерами, `abkit demo` остается CLI-командой (генерация синтетики и полный прогон для знакомства через терминал); в Streamlit (этап 6) для той же цели добавляется кнопка «Загрузить демо-данные» в табе Design.

## 12. Нефункциональные требования

- Воспроизводимость: все случайности от seed конфига; версии в results.json.
- Производительность: датасеты до ~10 млн строк; сплит и критерии — векторизованно, без питоновских циклов по строкам; бутстрап — батчами.
- Ошибки: пользовательские ошибки (нет колонки, дубли, не хватает мощности) — понятный текст без трейсбека; трейсбек только на багах.
- Тесты статистики обязаны быть частью CI: любой рефакторинг, ломающий FPR или мощность, валит сборку.
