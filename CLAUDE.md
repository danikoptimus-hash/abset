# abkit — заметки для Claude Code

Инструмент для A/B-тестирования (дизайн выборки, анализ результатов, A/A-/A/B-валидация). Полная техническая спецификация — в отдельных документах, читать их, а не пересказ здесь:

- [DESIGN.md](DESIGN.md) — ядро (`abkit/`): дизайн эксперимента, статистика, отчеты.
- [DOCKER.md](DOCKER.md) — командный Docker-режим: Postgres, роли/аутентификация, аудит-лог.
- [FRONTEND.md](FRONTEND.md) — история миграции интерфейса со Streamlit на React+FastAPI (этапы R1-R8, все завершены), архитектура `backend/` и `frontend/`.
- [README.md](README.md) — пользовательская документация (файловый и Docker-режимы).
- [docker/README.md](docker/README.md) — развертывание.

## Текущее состояние (после R8, миграция завершена)

Интерфейс — **React-UI** (`frontend/`, за `backend/` на FastAPI) плюс минимальный CLI (`cli.py`/`cli_admin.py`). Streamlit (`app.py`) полностью удален вместе с сервисом `legacy` и маршрутом `/legacy` — DESIGN.md §7 и DOCKER.md описывают его только как историю (см. примечания в начале этих файлов), актуальная архитектура — FRONTEND.md + `docker-compose.yml`.

После R8 добавлена датасет-центричная модель данных (этапы DB1-DB5, ниже) — Database Connections + датасеты из SQL, по образцу Superset. Своего отдельного .md-документа у этой фичи нет (не было отдельного «большого» ТЗ-файла как DOCKER.md/FRONTEND.md) — архитектура описана прямо здесь.

## Тесты

- Backend/ядро: `python -m pytest -q` (из корня, venv `.venv`), lint — `python -m pyflakes abkit backend tests migrations cli.py cli_admin.py conftest.py`.
- Frontend: `cd frontend && npm run typecheck && npm run lint && npm run test:unit && npm run build`. `test:unit` (vitest, added for item B's memory-chart redesign) is the first frontend unit-test layer in the project — scoped to `src/**/*.test.ts` only (`frontend/vitest.config.ts`; without that scoping vitest also picks up `frontend/e2e/*.spec.ts` and fails on every one, since those use Playwright's own `test()`, not vitest's). Reserve it for PURE logic worth isolating from a DOM/canvas (e.g. `charts/MonitoringLineChart.tsx`'s option-builder) — everything else stays typecheck/lint/build + Playwright e2e, that split isn't changing wholesale.
- E2E (Playwright): **`bash scripts/e2e.sh`** из корня — ЕДИНСТВЕННЫЙ способ гонять e2e локально. Поднимает одноразовый стек под отдельным compose project name (свои volumes/сеть/порт :8090, `docker compose down -v` на выходе всегда), не трогает персистентный dev-стек на :8080. НИКОГДА не гонять `npx playwright test` вручную с `E2E_BASE_URL`, указывающим на dev-стек (:8080) — это и есть root cause накопления мусора в БД (см. `abkit/jobs.py::run_cleanup_dev` docstring, «Правило: гигиена dev-артефактов» ниже). CI (`.github/workflows/ci.yml` job `e2e`) достигает той же изоляции иначе — одноразовый раннер-VM + `docker compose down -v` в конце, поэтому там `npx playwright test` напрямую — нормально, это не тот контекст, где накапливается мусор.
- После правок backend-роутов — перегенерировать типы фронта: `cd frontend && npm run gen:api`.
- `scripts/*.sh` — `shellcheck scripts/*.sh` (локально `shellcheck` не стоит по умолчанию — ставится `pip install shellcheck-py`, либо `docker run --rm -v "$(pwd)/scripts:/scripts" koalaman/shellcheck:stable /scripts/*.sh`).

## Правило: пересборка локального стека после каждого пакета правок

После завершения КАЖДОГО пакета изменений, затрагивающего `frontend/` или `backend/` (не после каждого отдельного файла — после законченного пакета задачи), самостоятельно пересобрать и переподнять локальный Docker-стек, ПРЕЖДЕ чем отчитываться о завершении:

1. `docker compose up -d --build` — пересобирает измененные образы и поднимает стек.
2. Дождаться healthy всех сервисов (`docker compose ps`, все — `healthy`/`running`, не `starting`/`unhealthy`).
3. Smoke-проверка: страница логина отвечает (`curl -sf http://localhost:8080/login` или эквивалент) и API живо (`curl -sf http://localhost:8080/api/v1/version`).
4. В финальном отчете явно указать: "Стек пересобран и поднят, можно смотреть на localhost:8080 (Ctrl+Shift+R)" — либо, если пересборка не удалась, честно сообщить об ошибке вместо того чтобы промолчать про этот шаг.

Не применяется к пакетам, не трогающим `frontend/`/`backend/` (например, чисто `abkit/`-ядро без API-поверхности, только тесты, только документация) — в таких случаях пересборка ничего не меняет и не нужна.

## Правило: гигиена dev-артефактов на живом стеке (жесткий чек-лист)

Локальный docker-compose стек — персистентный (данные в volume переживают
рестарты, см. `docker/README.md`). Мягкая версия этого правила ("используй
префикс и убирай за собой") на практике не соблюдалась — на живом стеке
накопилось 171 тестовый эксперимент, 247 датасетов, 10 подключений к БД и 73
лишних пользователя за несколько сессий, прежде чем это заметили (разбор
причин и разовая уборка — см. историю; корень — п. (а) ниже). Действующая
версия — обязательный чек-лист, не рекомендация:

**(а) e2e ТОЛЬКО через одноразовое окружение.** `bash scripts/e2e.sh` —
единственный способ гонять Playwright локально: поднимает стек под отдельным
compose project name (свои volumes/сеть/порт :8090), тестирует, всегда
делает `docker compose down -v` на выходе. НИКОГДА не запускать
`npx playwright test`/`npm run test:e2e:raw` вручную с `E2E_BASE_URL`,
указывающим на персистентный dev-стек (:8080) — это и есть корень проблемы
(root cause), не гипотетический риск. CI (`e2e` job) изолирован иначе —
одноразовый раннер-VM + `down -v`, там прямой `npx playwright test` — ок,
это другой контекст.

**(б) Ручное создание сущностей — ТОЛЬКО через `abkit.dev_helpers.DevSession`**
(не pytest/Playwright — у них своя изоляция через (а)/conftest.py, это
правило их не касается). Прямой вызов `jobs.run_design`/`DatasetRepo().create()`/
`create_connection()`/т.п. в отладочном скрипте — считать ошибкой, не
шорткатом:

```python
from abkit.dev_helpers import DevSession
with DevSession() as dev:
    dev.design(current_user, config, data)         # name -> _dev_<name> принудительно
    dev.dataset(filename="probe.csv", ...)          # -> _dev_probe.csv
    dev.connection(current_user, display_name=...)  # -> _dev_<name>
# teardown() уже отработал на выходе из `with`
```

Префикс `_dev_` ставится автоматически (забыть — нельзя), все созданное
трекается, `teardown()` (или выход из `with`) убирает одним вызовом, не
полагаясь на память о том, что именно было создано.

**(в) Страховка — `abkit-admin cleanup-dev`** (`abkit/jobs.py::run_cleanup_dev`,
полный docstring там же): удаляет сущности с префиксом `_dev_` (любого
возраста) и все, что принадлежит аккаунтам `*@e2e.test`, старше
`--min-age-hours` (default 1 — не трогает то, что может быть еще в процессе).
Аккаунты `admin@e2e.test`/`viewer@e2e.test` (жестко зашиты в
`frontend/e2e/helpers.ts` как логин для e2e) не деактивируются никогда —
только то, что они создали. Ничего, принадлежащее email вне `@e2e.test` и не
с префиксом `_dev_`, не трогается — реальные данные невидимы для этой
команды по построению, не по списку исключений.

**(г) Обязательный вызов в конце КАЖДОГО пакета работ**, шагом перед
финальным отчетом:

```bash
docker compose exec backend abkit-admin cleanup-dev
```

И обязательная строка в отчете: **"cleanup: removed X dev artifacts"** или
**"cleanup: nothing to clean"** — не молчать про этот шаг ни в одном из двух
случаев.

## Правило: релизный процесс

Регламент эксплуатации (архитектура, деплой, обновление, откат, бэкап,
диагностика) — [docs/OPERATIONS.md](docs/OPERATIONS.md), скрипты —
`scripts/backup.sh`/`scripts/update.sh`. Постоянные правила (действуют во всех
будущих сессиях):

(а) **На серверы деплоятся ТОЛЬКО теги `v*`.** `main` — ветка разработки, на
    прод не катится напрямую (`scripts/update.sh` сам отказывается запускаться
    на чем-то, что не матчит `v*`). CI публикует образы в `ghcr.io` только на
    push тега `v*` (`.github/workflows/ci.yml`, джоба `build-and-push`).

(б) **Миграции БД — только аддитивные/обратно-совместимые.** Новые
    таблицы/колонки — можно всегда; удаление/переименование ИСПОЛЬЗУЕМОЙ
    колонки/таблицы — не раньше, чем через один релизный тег после того, как
    код перестал ее использовать (старый тег должен суметь безопасно
    откатиться на новый шаг схемы). Пример уже примененного паттерна —
    `analysis_results.dataset_id` (миграция 0009): `SET NULL` вместо жесткого
    удаления/CASCADE, старое значение не пропадает резко.

(в) **Перед тегом — полный прогон тестов и smoke на свежем стеке.** pytest +
    pyflakes + frontend typecheck/lint/build + Playwright e2e (`bash scripts/e2e.sh`,
    не `npx playwright test` вручную — см. «Правило: гигиена dev-артефактов»
    ниже) — все зеленые — и `docker compose up -d --build` с чистого
    состояния + smoke-чек (login, `/api/v1/version`) — до, не после,
    простановки тега.

(г) **Теги ставятся РЕГУЛЯРНО, не только на крупные вехи вроде v2.0.0.**
    Правило добавлено пост-фактум (item 8 итогового 8-пунктового пакета,
    2026-07-16): между v2.0.0 и следующим тегом накопилось 75 коммитов за 8
    дней без единого промежуточного тега, из-за чего отображаемая версия
    (About, шапки отчетов) все это время молча показывала устаревшее
    "2.0.0" — не баг конкретной фичи, а следствие того, что тегирование не
    было рутиной. Правило на будущее: **завершенный значимый пакет работ →
    бампнуть тег** (semver: новые фичи → minor, только фиксы → patch),
    прежде чем переходить к следующему пакету, а не откладывать до
    "накопится побольше". Версия, которую видит пользователь, теперь
    выводится ИЗ САМОГО ТЕГА, а не хранится отдельной строкой в коде и не
    зависит от build-arg'ов CI (item 8-Б, доводка после первого прохода
    item 8 — тот использовал `ABKIT_VERSION` env как основной источник и
    показывал версию БЕЗ префикса `v`, что разъезжалось с именем самого
    тега): единственный источник — `git describe --tags --always --long`
    против `.git`, скопированного в build context (`.dockerignore` его не
    исключает), посчитанный ОДИН РАЗ на этапе сборки отдельной Docker-стадией
    `version` в файл `/app/VERSION_DESCRIBE`, который `abkit/__init__.py::
    _read_version()`/`_format_version()` парсит при импорте: на самом теге
    (`distance=0`) → `vX.Y.Z`; после тега → `vX.Y.Z+N (sha)`; тегов в истории
    вообще нет → `dev (sha)`. Работает ОДИНАКОВО что на CI-билде из тега
    (`build-and-push`, `.github/workflows/ci.yml` — там чекаут с
    `fetch-depth: 0`, иначе тег может быть не виден при shallow-клоне), что
    на обычном локальном `docker compose up -d --build` без всякого тега —
    никакого ручного шага от разработчика не требуется ни в одном случае.
    Отдельный `ABKIT_VERSION` build-arg в Dockerfile остался, но только для
    OCI image-лейбла/имени тега образа в ghcr.io — соседнее, не то же самое
    назначение, что видимая пользователю версия (единый источник для
    backend/API/HTML-отчетов — CLAUDE.md «единый источник, не три»).

**CHANGELOG.md** — ведется вручную при подготовке каждого релизного тега:
секция `## vX.Y.Z — <дата>` с перечнем крупных изменений с прошлого тега (не
построчный git log — смысловая выжимка, как существующие записи).

## Правило: актуальность документации

После КАЖДОГО пакета изменений, затрагивающего функциональность, UI, API,
схему БД или процессы эксплуатации — перед коммитом проверять и
актуализировать три документа:

- **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** — если изменилось что-то, что
  видит или делает пользователь (новые поля, кнопки, вкладки, изменение
  воркфлоу, новые метрики/методы).
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** — если изменились: сервисы
  compose, env-переменные, схема БД (новые таблицы/миграции), процедуры
  бэкапа/обновления, зависимости.
- **[docs/PRESENTATION.md](docs/PRESENTATION.md)** — только при
  появлении/удалении КРУПНЫХ возможностей (уровня «подключения к БД»,
  «guardrail-метрики»), мелочи сюда не тащить.

Механика: в конце каждого пакета — явный шаг «docs check»: пройтись по diff'у
изменений и определить, какие из трех файлов затронуты; обновить затронутые В
ТОМ ЖЕ пуше (можно отдельным коммитом `docs: sync with <feature>`); если не
затронут ни один — явно писать в отчете «docs: no changes needed», а не
промалчивать про этот шаг. При подготовке каждого релизного тега — секция в
CHANGELOG.md (правило релизного процесса выше). Это же правило распространяется
и на существующие технические ТЗ (DESIGN.md/DOCKER.md/FRONTEND.md) и
`docker/README.md`, где применимо — три файла выше просто НОВЫЕ обязательные
пункты чек-листа, не единственные документы в проекте.

CI-подстраховка (не блокирующая, `.github/workflows/ci.yml`, шаг «docs
freshness reminder»): если push меняет файлы в `frontend/src`, `backend/` или
`abkit/`, а `docs/` не тронуты и в сообщении коммита нет маркера `[docs:none]`
— шаг выводит warning-аннотацию, не валит билд. `[docs:none]` — осознанная
пометка «в этом коммите действительно нечего актуализировать в docs/», не
способ обойти проверку не думая.

## Правило: unsaved-changes guard + свежесть кэша после мутаций (UX-контракт)

Два системных контракта фронта, оба обязательны для НОВОГО кода, не только
для мест, перечисленных ниже (те — снимок на момент введения правила, не
исчерпывающий список навсегда).

**(а) Unsaved-changes guard.** Единый механизм — `useUnsavedGuard(isDirty)`
(`frontend/src/hooks/useUnsavedGuard.tsx`), `isDirty` — сравнение живых
значений формы с pristine-снапшотом, снятым в момент открытия/загрузки формы
(`Form.useWatch` + объект, зафиксированный вместе с `form.setFieldsValue`, или
`JSON.stringify(state) !== JSON.stringify(pristine)` для немодальных
многошаговых форм вроде визарда). Хук покрывает три пути потери данных одним
вызовом: закрытие модала (X/маска/Esc/Cancel — все идут через один AntD
`onCancel`, гвардить только его), переход роута (react-router `useBlocker`) и
закрытие/перезагрузку вкладки браузера (`beforeunload`, только пока dirty).

Важная архитектурная деталь: react-router поддерживает **только один активный
`useBlocker` на все приложение** (не документировано явно, обнаружено
эмпирически по консольному "A router only supports one blocker at a time") —
поэтому `useUnsavedGuard` НЕ вызывает `useBlocker` сам, а регистрируется в
одном общем `UnsavedGuardProvider` (обёрнут вокруг `<Outlet/>` в
`AppLayout.tsx`), который держит единственный `useBlocker` и `Set` id'шников
компонентов, репортящих `dirty=true`. Если форма программно сохраняет данные
и сама же сразу переходит на другой роут (визард: submit → navigate), обычный
`setState`-сброс pristine не успевает примениться синхронно до `navigate()` —
для этого случая хук отдаёт `markSaved()`, мутирующий dirty-флаг через `ref`
напрямую (без ожидания рендера).

Подключено (на 2026-07): Edit-режим страницы эксперимента, Edit Properties
modal, Edit dataset modal, Create dataset modal (вкладка From SQL), визард
дизайна (весь флоу, не по шагам — между шагами уходить свободно), Admin →
create/edit user, Settings → Database Connections modal. Не подключено
осознанно: Profile.tsx — форма смены пароля (была отмечена как low-priority,
вне исходного A.1 списка).

**(б) Свежесть данных после мутаций.** Централизованный реестр ключей
`frontend/src/api/queryKeys.ts` — factory-функции (`queryKeys.experiment(name)`
и т.п.) вместо inline-литералов `['experiment', name]` на месте вызова.
Правило: **каждый `useQuery` берет ключ отсюда; каждая мутация, способная
повлиять на чужой закэшированный запрос, обязана инвалидировать его через ту
же factory-функцию** — новая мутация без инвалидации связанных ключей
считается дефектом, а не тем, что можно доделать потом. Инвалидация по
литералу-дубликату (руками набранный `['experiment', name]` вместо
`queryKeys.experiment(name)`) — тоже дефект: реестр существует именно затем,
чтобы разъезжающиеся вручную ключи между чтением и записью не проходили
незамеченными (ровно так родился баг-триггер этого правила — созданный тег не
появлялся в фильтре тегов без перезагрузки, потому что `tagsTypeaheadAll()` в
момент мутации нигде не инвалидировался). Там, где мгновенность важна для UX
(создание тега, publication-toggle) — optimistic update
(`queryClient.setQueryData`/`setQueriesData` до ответа сервера, откат к
снапшоту при ошибке) поверх обычной инвалидации, а не вместо неё.

Фоновые job'ы (design/redesign, refresh датасета, анализ): по завершении
(поллинг увидел `completed`) — явная инвалидация связанных ключей
(`experiment`, `experimentsAll`, `datasetsAll`/`datasetsForSelect`,
`experimentResults`), а не расчет на то, что переход `navigate()` в другую
страницу сам всё обновит через remount — тот путь работает только пока
`QueryClient`'s `staleTime` для соответствующего запроса равен 0 (текущий
дефолт в `main.tsx`, ничем не гарантирован на будущее) и только если старая
страница действительно размонтируется, а не остаётся во втором табе/фоне.

`AuthContext.tsx::logout()` вызывает `queryClient.clear()` — иначе второй
пользователь, вошедший в том же браузере, на миг (или неопределенно долго для
запросов с `staleTime: Infinity`, напр. `version`) видит кэш первого.

## Осознанные решения по скоупу

- Сырой список файлов эксперимента (легаси-таб «Файлы»: имя+размер каждого файла без действий) в React-UI не портирован — чисто отладочная информация, реально не используется. См. FRONTEND.md §8 «Вне скоупа».
- **Product name: ABSet (display). Package/internal name: abkit (legacy, intentional).** Ребрендинг ABKit → ABSet (пользовательский брендинг, видимое имя везде: навбар/логотип, логин, document title, favicon, Settings → About, шапки HTML-отчетов, CLI-тексты, документация) — технический идентификатор НЕ переименовывается: python-пакет `abkit`, импорты (`from abkit import ...`), пути `/data`, имена docker-сервисов (`backend`/`frontend`/`nginx`/`postgres` в `docker-compose.yml`, образы `abkit-backend`/`abkit-frontend`), env-префикс `ABKIT_*` (`ABKIT_SECRET_KEY`, `ABKIT_EXPERIMENTS_DIR`, `ABKIT_MODE` и т.п.), имя БД/пользователя postgres, CLI-команда `abkit-admin` — остаются как есть. Причина: переименование пакета/инфраструктурных идентификаторов — высокий blast radius (env на серверах, скрипты деплоя, существующие данные на диске) при нулевой пользе, поскольку пользователь ничего из этого не видит. Единый источник видимого имени: `abkit.PRODUCT_NAME` (Python) и `frontend/src/branding.ts` (TS, синхронизировать вручную). Репозиторий на GitHub переименован в `abset` (редирект со старого имени `abkit` работает); `git remote` обновлен на новый URL.
- Интерфейс (React-UI, HTML-отчеты, CLI `--help`) — полностью на английском. Внутренняя документация проекта (эти .md-файлы, комментарии в коде, docstrings, `git log`) остается на русском — так было и продолжает быть осознанным решением, не путать со scope перевода UI.

## Известный техдолг

- **Эксперименты адресуются по имени** (`experiments.name`, `Text unique`), не по `id`. Это работает корректно сегодня — уникальность имени обеспечена на уровне БД, и весь роутинг (фронт `encodeURIComponent` → nginx passthrough без URI-части в `proxy_pass` → FastAPI автоматически декодирует path-параметр → `ExperimentRepo.get_by_name`) проверен end-to-end на реальном кейсе с кириллицей/пробелом/двоеточием в имени (ref edb716f1, разбор — см. историю сессии) и работает без проблем. Известное ограничение: **переименование эксперимента (Properties) молча ломает существующие ссылки/закладки** — старый URL с прежним именем перестает резолвиться. Полная миграция адресации на `uuid` (роут `/experiments/{id}`, имя — только отображение) была спроектирована как системный фикс, но осознанно отложена — blast radius (~20 backend-роутов, вся фронтовая маршрутизация, ~60 e2e-спеков, десятки pytest-тестов, адресующих эксперимент по имени) сочтен непропорциональным при отсутствии живого бага в самом роутинге. Планировать эту миграцию отдельным пакетом в тихое окно, не смешивая с другими крупными изменениями.
- Две узкие проблемы из того же «класса» (адресация по имени) уже закрыты точечно, без миграции на uuid: скачивание samples с кириллицей в имени падало на `Content-Disposition`-заголовке (коммит `c0c8aea`, RFC 5987 `filename*=`); вкладка History путала события удаленного и заново созданного эксперимента с тем же именем из-за фильтрации `audit_log` по `object_name` вместо `object_id` (см. правки в `backend/routers/experiments.py`/`abkit/db/repositories.py::AuditRepo`).

## Permissions model

Роли: `viewer` < `editor` < `admin` (см. DOCKER.md §4.1) — базовая ролевая матрица не изменилась. Поверх нее — per-experiment права (UX-пакет, `abkit/access.py`): у эксперимента есть один изначальный `owner_id` (создатель) плюс необязательные дополнительные владельцы/редакторы в таблице `experiment_access` (Edit Properties modal, как в Superset), и опциональное `experiments.visible_roles` (ограничение видимости published-эксперимента конкретными ролями).

`access-editor` в таблице ниже = пользователь с строкой в `experiment_access` (access `owner` или `editor` — обе дают одинаковые права редактирования, различие между ними чисто информационное в Properties modal).

| Действие                                   | viewer | editor (не owner/access) | access-editor | owner (`owner_id`) | admin |
|---------------------------------------------|:------:|:-------------------------:|:--------------:|:-------------------:|:-----:|
| View (список/детали, с учетом видимости)     |   ✓¹   |            ✓¹              |       ✓        |          ✓           |   ✓   |
| Analyze / Validate                          |        |            ✓¹              |       ✓¹        |          ✓¹           |   ✓   |
| Edit blocks (Hypothesis/Conclusions/Decision)|        |                            |       ✓        |          ✓           |   ✓   |
| Rename                                       |        |                            |       ✓        |          ✓           |   ✓   |
| Operational status (running/completed/...)   |        |                            |       ✓        |          ✓           |   ✓   |
| Publication status (draft/published)         |        |                            |       ✓        |          ✓           |   ✓   |
| Edit Properties (owners/editors/visible_roles)|        |                            |       ✓        |          ✓           |   ✓   |
| Delete                                       |        |                            |       ✓        |          ✓           |   ✓   |
| Admin-функции (users, action log)            |        |                            |                |                      |   ✓   |

¹ Analyze/Validate — намеренно НЕ ограничены владением/грантом (см. `tests/test_jobs_permission_matrix.py::test_run_analyze_editor_allowed_on_others_experiment`): любой editor+ может анализировать/валидировать ЛЮБОЙ эксперимент, который он ВИДИТ (колонка View) — гейт по видимости применяется на уровне HTTP-роутера (`backend/routers/experiments.py`, `_visible_or_404` перед постановкой job в очередь), не на уровне `abkit/jobs.py`. Draft-эксперимент без гранта editor'у не видим (View = ✗ для чужого editor) → следовательно и Analyze/Validate для него недоступны, хотя формально это два разных, независимо применяемых правила, а не одна проверка. Тест на саму видимость: `backend/tests/test_analyze_validate_jobs.py::test_analyze_blocked_on_experiment_editor_cannot_see`.

Видимость (`can_view_experiment`, `abkit/access.py`): owner/access-editor/admin видят всегда; published без `visible_roles` видят все; published с `visible_roles` — только перечисленные роли (плюс owner/access-editor/admin); draft — только owner/access-editor/admin.

Тесты на всю эту модель разом (GET/PUT `/properties`, грант через `experiment_access`, фильтрация списка по `visible_roles`): `backend/tests/test_experiment_properties.py`. Regression-guard на английский UI (0.8): `tests/test_no_cyrillic_in_ui.py` — AST-скан Python (без docstring/attribute-docstring) + regex-скан `frontend/src/**/*.ts*` (без комментариев, `schema.ts` исключен как генерируемый) на кириллицу.

## Database Connections + датасет-центричная модель (DB1-DB5)

По образцу Superset (Database Connections + Datasets как единственный источник данных).

**Подключения к БД** (`abkit/db_connections/`, таблица `database_connections`, `backend/routers/db_connections.py`): движки v1 — PostgreSQL (`psycopg`, уже основная зависимость), ClickHouse (`clickhouse-connect`, HTTP-диалект `clickhousedb://`, порты 8123/8443 — не «родной» TCP 9000/9440), MSSQL (`pymssql`/FreeTDS — выбран вместо `pyodbc`, чтобы не тянуть системный ODBC-драйвер в образ). Зависимости — extras `[db-connectors]` в `pyproject.toml`, ставятся в `docker/Dockerfile`. Пароль шифруется Fernet (`abkit/db_connections/crypto.py`), ключ выводится из `ABKIT_SECRET_KEY` (SHA256 → urlsafe base64) — отдельного `ABKIT_DB_ENCRYPTION_KEY` нет (см. `.env.example`: ротация `ABKIT_SECRET_KEY` делает сохраненные пароли подключений нерасшифровываемыми). API никогда не возвращает пароль (write-only поле). Управление — только admin, чтение списка (без пароля) — editor+. `POST .../{id}/test` и черновой `POST .../test-draft` (форма еще не сохранена) — `SELECT 1` с человеческим результатом (`abkit/db_connections/testing.py::_classify`, категории: ok/dns_error/tcp_timeout/auth_failed/db_not_found/error), таймаут 5-10с. dns_error и tcp_timeout — раньше были одной категорией `host_unreachable` (сообщение "Host unreachable or connection timed out" смешивало "хост не резолвится" с "хост резолвится, но порт не отвечает" — два разных поля формы нужно чинить в каждом случае); разделены после разбора жалобы на ложную диагностику. `test_connection()` логирует каждую попытку (`abkit.db_connections.testing`, engine/host/port/database/username/outcome — БЕЗ пароля).

**Датасет из SQL** (`abkit/db_connections/{sql_guard,sql_dataset}.py`, `POST /datasets/from-sql`): `sql_guard.py` на `sqlglot` — только SELECT/CTE (обходит попытки протащить мутацию через CTE типа `WITH t AS (INSERT ... RETURNING *) SELECT * FROM t`, проверяя ВСЕ узлы AST, не только корень), запрет multi-statement и `FOR UPDATE`. `sql_dataset.py::execute_select_to_parquet` — чанкинг через `pd.read_sql(..., chunksize=...)` (честный server-side stream курсор гарантирован только для Postgres/psycopg — у ClickHouse/MSSQL best-effort), пустой-но-типизированный `WHERE 1=0`-проб для схемы при 0 строках, обрезка по `ABKIT_SQL_MAX_ROWS` (default 5_000_000) с флагом `truncated`, таймаут `ABKIT_SQL_TIMEOUT_SEC` (default 300). **Известная ловушка**: psycopg возвращает postgres-колонку `uuid` как Python `uuid.UUID`, а `pa.Table.from_pandas` молча превращает такие объекты в сырые 16 байт вместо ошибки (данные тихо портятся) — `execute_select_to_parquet` явно стрингифицирует UUID-колонки (`_stringify_uuid_columns`) перед конвертацией в pyarrow; при добавлении новых типов из внешних БД проверяй тем же способом (`pd.read_parquet(...).dtypes` + `type(df[col].iloc[0])`), не доверяй молчаливому успеху записи. `POST /db-connections/{id}/preview` — первые 100 строк без материализации, тот же SELECT-only контроль. `POST /datasets/{id}/refresh` (Editor+, `run_refresh_sql_dataset`) — перевыполняет сохраненный `sql_text`, обновляет parquet и `fetched_at`; фетчит в отдельный временный файл и подменяет им живой `storage_path` только при полном успехе (`Path.replace`, атомарно) — обрыв соединения/исчезнувшая таблица посреди чанкинга не должны портить уже существующий снапшот. UI: hover-иконка в Actions списка датасетов + кнопка «Refresh from source» в drawer превью, обе за подтверждающим Modal, обе видны только Editor+.

**Датасет-центричная модель**: страница `/datasets` — единственное место загрузки файлов/создания SQL-датасетов (`frontend/src/pages/datasets/CreateDatasetModal.tsx`, две вкладки Upload/From SQL). Дизайн-визард (Step1Data), Analyze (AnalyzeSection), Validation — файлы напрямую больше не принимают, только `DatasetSelect` (`frontend/src/components/DatasetSelect.tsx`, поиск по существующим датасетам + ссылка «Create new dataset») или генерация демо-данных. `datasets.source` (upload|sql|demo), для source=sql — `connection_id`/`sql_text`/`fetched_at`/`source_schema`/`source_table` (последние два — см. «Schema/table browser» ниже). Таблица `experiment_datasets(experiment_id, dataset_id, kind)` — связь many-to-many, заполняется при ФАКТИЧЕСКОМ использовании датасета (design/analyze/validate), а не при простом выборе в UI; старые поля `datasets.experiment_id`/`kind` остаются как «primary/first-use» для обратной совместимости чтения.

**Schema/table browser** (`abkit/db_connections/introspection.py`, `GET /db-connections/{id}/schemas` и `.../schemas/{schema}/tables`, public_router, editor+): `sqlalchemy.inspect(engine)` для postgres/mssql; ClickHouse — прямые запросы к `system.databases`/`system.tables` (инспектор диалекта `clickhousedb` не поддерживает это надежно). In-process TTL-кэш 60с, ключ `(connection_id, schema)` — без Redis (проект принципиально однопроцессный, ThreadPoolExecutor jobs), `?refresh=true` (кнопка 🗘) обходит кэш. Frontend — общий компонент `frontend/src/components/datasets/SchemaTableCascade.tsx` (контролируемый: schema/table-состояние и связка с SQL-полем — у родителя), используется И в создании (`CreateDatasetModal.tsx::FromSqlTab`), И в редактировании (`EditDatasetModal.tsx`), чтобы правки не расходились между формами (см. ниже). Выбор Schema→Table автозаполняет `SELECT * FROM "schema"."table"` в SQL-поле (`buildSelectAllSql` в `parseSchemaTableFromSql.ts`) — при создании молча, если запрос еще не редактировался вручную (сравнение с последним автозаполненным значением, не просто "было пусто"); при редактировании — через `Modal.confirm`, потому что редактор при открытии Edit всегда содержит уже сохраненный (то есть заведомо "ручной") запрос. Там же — общий `frontend/src/components/datasets/QueryResultPreview.tsx` (кнопка + таблица результата, выполняет ТЕКУЩИЙ текст SQL-поля через `/db-connections/{id}/preview`).

**Persisted source schema/table** (баг-фикс поверх исходной Schema/table browser фичи — Edit открывался с пустым каскадом, потому что единственным механизмом было разобрать `sql_text` заново, а парсинг молча падал на кавычках, которые сам же каскад и генерирует, `"schema"."table"` — regex с финальным `\b` не матчится после закрывающей кавычки, не word-char): `datasets.source_schema`/`source_table` (миграция `0010_dataset_source_schema_table.py`, nullable, бэкофилл существующих `source='sql'` строк тем же парсингом) записываются ЯВНО при создании/правке — `DatasetFromSqlRequest`/`PatchDatasetRequest` получают эти два поля, фронт шлет их, только если текущий текст SQL байт-в-байт совпадает с `buildSelectAllSql(schema, table)` (иначе — `undefined`, то есть "не было осознанного выбора / запрос теперь другой"). `abkit/jobs.py::run_update_dataset` → `DatasetRepo.update_sql_source()` ВСЕГДА перезаписывает `source_schema`/`source_table` вместе с `sql_text` (в null, если не переданы) — не оставляет устаревшее значение при правке SQL руками, "не врать" про источник данных важнее, чем сохранить когда-то верную подсказку. `EditDatasetModal.tsx` при открытии предзаполняет каскад ИЗ ЭТИХ КОЛОНОК напрямую (не парсит `sql_text` заново); `parseSchemaTableFromSql.ts`/`abkit/db_connections/sql_parsing.py` (два независимых, вручную синхронизируемых порта одной логики — TS для live-фолбэка в Edit, Python только для бэкофилла в миграции) остаются резервным вариантом ТОЛЬКО для строк без этих колонок (старые датасеты, либо в принципе никогда не парсящийся сложный запрос) — если и это не сработало (JOIN/CTE/подзапрос/несколько `FROM`), каскад пустой с подсказкой "Custom query — table picker not applicable" вместо угадывания. Превью-drawer списка датасетов показывает те же поля строкой "Source: `<connection>` · `<schema>.<table>`" (или "· custom query").

**Edit/Delete датасета** (`abkit/jobs.py::run_update_dataset`/`run_delete_dataset`, право — владелец `uploaded_by` или Admin, не просто Admin как было в DB4): Delete проверяет использование (`experiment_datasets` + legacy `datasets.experiment_id`) — если ни один эксперимент не использует, обычный confirm; если использует — `DatasetInUseError` (маппится в 400 `confirmation_required` в `backend/errors.py`, как и остальные глобальные хендлеры), фронт показывает список экспериментов и требует ввести `DELETE`; повторный вызов с `confirm="DELETE"` проходит. Удаление датасета, на который ссылаются `analysis_results`, больше не падает с FK-violation — `analysis_results.dataset_id` теперь `ON DELETE SET NULL` (миграция 0009); поскольку это обнуляет сам dataset_id, отдельная колонка `analysis_results.dataset_filename` замораживает имя файла В МОМЕНТ АНАЛИЗА (`_save_analysis` в `backend/routers/experiments.py`) — `GET /experiments/{name}/results` читает именно её, не делает live-lookup, так что "какие данные анализировались" переживает удаление датасета. Edit для source=sql: смена `connection_id`/`sql_text` обновляет строку синхронно и запускает тот же джоб, что Refresh (переиспользует `run_refresh_sql_dataset`, которая читает поля СВЕЖИМИ из БД — отдельного кода "применить отредактированный SQL" нет). `EditDatasetModal.tsx` внизу показывает Collapse "Data preview" (развернут по умолчанию): для `source=sql` — Tabs с вкладками "Stored snapshot" (`components/datasets/DatasetSnapshotPreview.tsx`, первые 10 строк текущего сохраненного снапшота через `/datasets/{id}/preview`, тот же query-key `['dataset-preview', id]`, что и превью-drawer в списке) и "Query result" (тот же `QueryResultPreview`, кнопка "Preview query result" — можно сравнить сохраненный снапшот с результатом еще не сохраненной правки запроса); для `source=upload`/`demo` — только "Stored snapshot" (без Tabs, без Connection/SQL).

**Bulk select/delete** (`frontend/src/pages/Datasets.tsx`, паттерн переиспользован из `ExperimentsList.tsx`'s bulk select — `POST /datasets/bulk-delete`, `backend/routers/datasets.py`): чекбокс-колонка + панель действий, как у экспериментов, но подтверждение ОДНО на весь батч (не два уровня, как у одиночного удаления) — модалка сразу подтягивает usage (`GET /datasets/{id}/usage`) на каждый выбранный id и показывает "used by: ..." построчно, ввод `DELETE` разрешает удалить всё сразу, включая используемые (сервер вызывает `run_delete_dataset(..., confirm="DELETE")` безусловно). Права — per-item (owner/admin) на сервере, несовпадающие пропускаются с "no permission" в итоговом отчете, а не роняют весь запрос.

**Frontend**: `/admin/db-connections` (Settings → Data → Database Connections, только admin) — CRUD-страница в стиле Superset (`frontend/src/pages/admin/DatabaseConnections.tsx`). SQL-редактор в "From SQL" — обычный `Input.TextArea` (моно-шрифт), НЕ подсвеченный редактор: пробовали `react-simple-code-editor`, но библиотека стабильно роняла приложение (React error #130, чистый белый экран) после 2+ жестких переходов между страницами в одной сессии браузера — баг внутри самого `<Editor>` (воспроизводился даже с тривиальным `highlight={(code) => code}`, без Prism), библиотека полностью удалена (`npm uninstall`). Стабильность важнее подсветки синтаксиса.

## Теги для A/B тестов

По образцу тегов дашбордов Superset — свободная группировка/поиск, не контролируемый словарь.

**Модель** (миграция `0011_tags.py`, аддитивная): `tags` (`id`, `name` — `CITEXT unique`, тот же паттерн, что `users.email`, регистронезависимая уникальность без ручного `lower()`; `color` nullable, `created_by`, `created_at`) + `experiment_tags` — голая composite-PK связка (`experiment_id`, `tag_id`, оба `ON DELETE CASCADE`), без суррогатного `id`, в отличие от `experiment_datasets` (у той есть `kind` — колонка, оправдывающая отдельный `id`; здесь оправдывать нечем).

**API** (`abkit/jobs.py`: `run_create_tag`/`search_tags`/`run_set_experiment_tags`/`get_tag_usage_count`/`run_delete_tag`; `backend/routers/tags.py` + `PUT /experiments/{name}/tags` в `backend/routers/experiments.py`, не `{id}` — весь остальной API адресует эксперимент по имени, R2-решение, тег-эндпоинт не исключение): `GET /tags?q=` (typeahead, viewer+), `POST /tags` (editor+, **get-or-create** — `TagRepo.get_or_create()` возвращает существующий тег при регистронезависимом совпадении имени вместо ошибки, специально ради «ввод нового имени = тег уже существует» на UI не будучи отдельным кейсом), `PUT /experiments/{name}/tags` (owner/access-editor/admin — тот же `require_experiment_edit_access`, что у остальных Properties, ВСЕГДА полная замена списка, не дельта), `GET /tags/{id}/usage` (сколько экспериментов используют — для текста подтверждения перед удалением), `DELETE /tags/{id}` (только admin, снимает тег со всех экспериментов через `ON DELETE CASCADE`, не отдельным шагом). Все мутации — `audit_log` (`tag.create`, `experiment.tags_change`, `tag.delete`).

**Список экспериментов**: `ExperimentSummary`/`ExperimentDetail`/`ExperimentPropertiesOut` несут `tags: list[TagOut]`, `ExperimentTagRepo.list_for_experiments()` — один запрос на страницу списка, не N+1. `GET /experiments` получил `tag` (repeatable query param, AND-логика: эксперимент должен нести ВСЕ выбранные теги, не любой) и живой поиск `q` теперь матчит и по имени тега, не только по имени эксперимента.

**Frontend**: `components/TagBadge.tsx` (`TagBadge`/`TagList`, `+N`-сворачивание с tooltip) + `components/hashColor.ts` (детерминированный приглушенный цвет по hash строки — тот же палитра/алгоритм, что у аватарок владельцев в `UserAvatar.tsx`, вынесен в общий модуль вместо копии; цвет теговой бейджи ВСЕГДА считается по имени на клиенте, `tags.color` в БД не читается нигде — задел под ручной пикер, вне скоупа v1). `ExperimentPropertiesModal.tsx`: поле Tags — AntD `Select mode="tags"`, значения формы — ИМЕНА тегов (не id); при Save каждое имя резолвится в id через `POST /tags` (безопасно вызывать и для уже существующих имен — get-or-create), затем один `PUT .../tags` с полным списком id — Select ничего не знает про "новый или существующий тег", это решается только на сохранении. `ExperimentsList.tsx`: колонка Tags, мультиселект-фильтр по тегам (typeahead, AND), клик по бейджу — фильтр по этому тегу; поиск переведен с `Input.Search` (по Enter) на живой debounced `Input` (`useDebouncedValue`, вынесен в `hooks/useDebouncedValue.ts` — тот же хук, что уже был в `Datasets.tsx`, не копия), т.к. поиск теперь ищет и по тегам. `ExperimentPage.tsx`: бейджи тегов — ОТДЕЛЬНОЙ строкой под шапкой (не втиснуты в строку с статус-бейджами/Last modified — там и так `flexWrap`, но осознанно вынесено ниже, чтобы не грозило переносом строки шапки), клик — переход на `/experiments?tag=<id>` (список читает `tag` из URL при монтировании как начальное состояние фильтра — единственный способ передать фильтр между страницами, отдельного глобального стора фильтров нет).

Тесты: `tests/test_db_connections_core.py`, `backend/tests/test_db_connections.py`, `tests/test_sql_guard.py`, `tests/test_sql_dataset_core.py`, `tests/test_sql_parsing.py` (Python-порт `parseSchemaTableFromSql`, используемый миграцией 0010), `backend/tests/test_dataset_from_sql.py`, `tests/test_experiment_dataset_repo.py`, `backend/tests/test_experiment_datasets_link.py` — все против `testcontainers-postgres` (реальная БД, не моки), см. `feedback` в общем описании тестов выше. E2E: `frontend/e2e/database-connections.spec.ts` (полный цикл: создать подключение → test → preview SQL → создать датасет → дизайн на нем; тест на Edit — каскад предзаполнен из сохраненного SQL, обе вкладки превью, "Preview query result" отражает несохраненную правку, плюс проверка JOIN-запроса → пустой каскад с подсказкой; тест на создание через каскад → `source_schema`/`source_table` реально сохранены в БД и предзаполнены в Edit БЕЗ повторного парсинга), `frontend/e2e/datasets-page.spec.ts`, `frontend/e2e/dataset-management.spec.ts` (Edit/Delete/поиск на source=upload, включая снапшот-превью без Connection/SQL).

## Экспорт/импорт эксперимента (zip-архив)

Перенос теста между инстансами (и клонирование внутри одного). Никаких новых
таблиц/миграций — фича целиком поверх существующей модели.

**Раздел ответственности**: `abkit/exchange.py` — ТОЛЬКО чтение/запись архива
(bytes <-> структуры), без единого обращения к БД (тестируется без
testcontainers — `tests/test_exchange.py`); `abkit/jobs.py::run_export_experiment`/
`run_import_experiment` — оркестрация (репозитории, права, audit_log), тесты
против реальной БД через API — `backend/tests/test_experiment_export_import.py`.
Тот же раздел, что у `abkit/flow_images.py` (чистая валидация) и jobs.py.

**Формат** (`<experiment-name>_export.zip`): `manifest.json` (`format_version`,
версия приложения, `exported_at`), `experiment.json` (config, блоки, теги,
статусы, ссылки на датасеты), `assignments.parquet` (только ABSet-сплит —
у external его нет по построению, `config.split_source`; назначения лежат в
ТАБЛИЦЕ `assignments`, не в parquet на диске, см. `abkit/db/store.py` —
экспорт читает `AssignmentRepo.load()` и сериализует, импорт делает
`bulk_insert`), `analysis_results.json` (ВСЕ прогоны — ради этого добавлен
`ResultRepo.list_for_experiment()`, до того была только
`latest_for_experiment`), `reports/`, `datasets/<sha256>.parquet` (только с
галочкой «Include dataset snapshots»).

`EXPORT_FORMAT_VERSION` растет только при НЕСОВМЕСТИМОМ изменении; архив
новее поддерживаемого — осознанный отказ (400 `unsupported_format_version`),
старее — читается как есть (новые поля всегда опциональны).

**Безопасность**: архив приходит от пользователя, а `reports/` пишутся на
диск. `zipfile` НЕ нормализует имена (`namelist()` отдает
`reports/../../../etc/passwd` дословно — проверено), поэтому
`_safe_member()` явно ОТКЛОНЯЕТ traversal (а не молча берет basename — честный
экспорт таких имен не производит, значит архив битый или враждебный), плюс
белый список `REPORT_FILENAMES`. Неизвестные записи (README архиватора и т.п.)
— игнорируются, не фатальны.

**Права**: экспорт — Editor+ на ЛЮБОЙ ВИДИМЫЙ тест, владения/гранта не нужно
(экспорт — чтение). Гейт двухчастный, ровно как у Analyze/Validate: роль —
`require_min_role("editor")` депендой + `require_role` в jobs, видимость —
`_visible_or_404` в роутере. Следствие для UI: в колонке Actions списка
Export гейтится РОЛЬЮ, а остальные кнопки — `record.can_edit`; поэтому колонка
рендерится при `record.can_edit || canExport`, а «⋯» на странице теста — при
`canEdit || canExport`.

**Импорт**: всегда НОВЫЙ тест (никогда не перезапись), `publication=draft`
принудительно, владелец — импортирующий, конфликт имени → `<name> (imported)`
→ `(imported 2)`... `created_at` восстанавливается ИЗ АРХИВА, а не `now()`:
`design_report.html` копируется побайтово и уже содержит исходную дату в
шапке — иначе UI и отчет разъехались бы. Блоки сопоставляются ПО `kind` с
дефолтными (их создает `ExperimentRepo.create()`), иначе `upsert_many` без id
создал бы по второму hypothesis/conclusion/decision.

**Разрешение ссылок на датасеты** (`_plan_dataset_links`, порядок из ТЗ):
sha256 → имя (с подтверждением) → снапшот из архива → предупреждение
(«re-analysis unavailable until relinked» — импорт при этом УДАЕТСЯ). План
считается ДО создания эксперимента: `DatasetNameMatchConfirmationRequired`
(400 `confirmation_required`, тот же паттерн, что `DatasetInUseError`) не
должен оставлять за собой полусозданный тест. **Ловушка**: снапшот пишется на
диск ВСЕГДА с расширением `.parquet`, даже когда `datasets.filename` —
`data.csv`: `read_dataset_file` выбирает парсер ПО РАСШИРЕНИЮ `storage_path`,
так что parquet-байты под именем `.csv` молча уехали бы в `pd.read_csv`.

**Frontend**: `components/ExportExperimentModal.tsx` (галочка снапшотов;
скачивание через fetch+blob, а НЕ `<Button href>` как у отчетов/samples —
экспорт умеет отказать 403/404, а у href-навигации отказ выглядит как «ничего
не произошло»), `components/ImportExperimentModal.tsx` (файл копится в
состоянии — повтор с `confirm=true` должен слать ТОТ ЖЕ файл, а не просить
перевыбрать; успех с warnings — баннер, а не ошибка). E2E:
`frontend/e2e/export-import.spec.ts`.

## Глобальная кнопка «+» в шапке

Superset-style, `components/AppLayout.tsx`, справа перед Settings, только
Editor+ (`hasMinRole`). Существующие кнопки создания на страницах остаются —
это ДОПОЛНИТЕЛЬНЫЙ вход, не замена.

Два пункта открываются принципиально по-разному: «A/B test» —
`navigate('/experiments/new')` (роут есть), «Dataset» — модалка
(`/datasets/new` не существует, состояние живет внутри `DatasetsPage`, куда
шапке не дотянуться). Поэтому `CreateDatasetModal` рендерится в `AppLayout`
своим экземпляром — пропсы у нее самодостаточные, `datasetsAll` она
инвалидирует сама. **Важно**: рендерится ВНУТРИ `UnsavedGuardProvider`, а не
рядом — `useUnsavedGuard` регистрирует dirty-флаг через контекст и вне
провайдера сделал бы это в `null` (`ctx?.setDirty`) молча, без ошибки,
потеряв блокировку ухода с роута (UX-контракт (а)).

E2E: `frontend/e2e/global-create.spec.ts`.

## Папки для A/B тестов

Простая одноуровневая группировка тестов в список — сознательно НЕ дерево (подпапок в v1 нет, решение зафиксировано, не забытая фича). У Superset нет собственного понятия "папка" для дашбордов/чартов (только теги, см. выше) — ближайшая существующая в проекте аналогия не оттуда, а левая filter-панель, уже применяемая в этом приложении для одиночного (не AND-комбинируемого) сужения списка; папки взяли этот же паттерн, а не паттерн чипов у тегов, потому что членство в папке — одно-к-одному (тест лежит ровно в одной папке или нигде), а не пересекающиеся ярлыки.

**Модель** (миграция `0017_folders.py`, аддитивная): `folders` (`id`, `name` — **plain `Text` unique**, НЕ `CITEXT` как у `tags.name` — папка это контейнер, который пользователь осознанно создает и организует, а не свободная метка, где регистро-независимое слипание "Checkout"/"checkout" было бы удобством; здесь коллизия имени — ошибка пользователя, которую стоит показать, не скрывать молча; `position`, `created_by`, `created_at`) + `experiments.folder_id` — nullable FK, `ON DELETE SET NULL` (как `datasets.experiment_id`, НЕ как `experiment_flow_images.experiment_id`) — удаление папки НЕ удаляет тесты, они возвращаются в Uncategorized (`folder_id IS NULL`).

**API** (`abkit/jobs.py`: `run_create_folder`/`list_folders`/`run_rename_folder`/`run_delete_folder`/`run_move_experiment_to_folder`; `backend/routers/folders.py` + `PUT /experiments/{name}/folder` и `POST /experiments/bulk-move-folder` в `backend/routers/experiments.py`, тот же паттерн, что у тегов — назначение живет в роутере эксперимента, не в роутере сущности): `GET /folders` (viewer+, отдает список с `count` — посчитан ТОЛЬКО по видимым текущему пользователю экспериментам, `can_view_experiment`, тот же принцип, что у списка экспериментов — черновик, скрытый от этого пользователя, не должен раздувать чужой счетчик числом, которое нечем объяснить), `POST /folders` (editor+, точное совпадение имени — ошибка, не get-or-create как у тегов, `FolderNameConflictError` → 400 `folder_name_conflict`, без предложения слияния — папки не сливаются), `PATCH /folders/{id}` и `DELETE /folders/{id}` (создатель ИЛИ admin — `_require_folder_owner_or_admin`, уже, чем editor+, нужный для создания), `PUT /experiments/{name}/folder` (owner/access-editor/admin ЭТОГО эксперимента — `require_experiment_edit_access`, то же правило, что у тегов/блоков/переименования, не связано с тем, кто создал папку), `POST /experiments/bulk-move-folder` (тот же per-item skip-паттерн, что `bulk-delete`). Все мутации — `audit_log` (`folder.create`, `folder.rename` `{from,to}`, `folder.delete` `{affected_experiments}`, `experiment.folder_change` `{from,to}` — именами папок, не id).

**Frontend**: `components/folders/FolderPanel.tsx` — коллапсируемая (стрелка сворачивания) левая панель на `/experiments` (`<nav aria-label="Folders">`): "All tests" → список папок со счетчиками → **"Uncategorized" В КОНЦЕ, только если `uncategorized_count > 0`** (п.5.7 — это не папка, а вид `folder_id IS NULL`; приглушенный стиль — `type="secondary"`+italic+серая иконка ВСЕГДА, даже когда выбрана — и никогда не получает `menu` prop, т.е. rename/delete физически недоступны, не просто скрыты правами). Клик — фильтр (`folder` composes AND-ом с `status`/`tag`/`q`, читается из URL при монтировании как и `tag`); "+ New folder" (editor+), "⋯" per-папка (creator или admin — сравнение `user.email === folder.created_by_email` на клиенте, реальная проверка на сервере) → Rename/Delete, Delete подтверждает через `Modal` с текстом "N tests will move to Uncategorized" (без typed-DELETE — не такой разрушительный шаг, как удаление самого теста). `components/folders/MoveToFolderModal.tsx` — один компонент на оба сценария (`names.length === 1` бьет в одиночный `PUT`, иначе в bulk-эндпоинт), используется и hover-кнопкой строки, и bulk-панелью `ExperimentsList.tsx` (третий пункт в `bulkActions`, рядом с Delete). `ExperimentsList.tsx` получил колонку Folder (кликабельный `Tag`, фильтрует так же, как клик по папке в панели) — таблица экспериментов НЕ имеет `onRow`, так что `MoveToFolderModal` (page-level sibling, не вложен в рендер строки) не подвержен классу бага из п.3 (React-portal click bubbling через `onRow`), `StopClickPropagation` не нужен.

Drag&drop строки на папку (п.5.3.в опционально) — НЕ реализован: решение задокументировано здесь, не забытый пункт. Обоснование: dnd-kit уже в проекте (миниатюры флоу-картинок), но перетаскивание СТРОКИ ТАБЛИЦЫ на элемент ДРУГОГО компонента (панели) — принципиально другая, более дорогая интеграция (drop-zone в другом React-дереве, авто-скролл при перетаскивании через границу таблицы), чем переупорядочивание миниатюр внутри одной колонки; row action + bulk action уже покрывают тот же результат за один клик.

Тесты: `backend/tests/test_folders.py` (create/list-with-counts/rename/delete permissions/move single+bulk/filter composition), `tests/test_audit_log.py`-стиль для audit-деталей покрыт внутри `test_folders.py` через прямые ассерты `AuditRepo`. E2E: `frontend/e2e/folders.spec.ts` (создание папки → move по одной строке → фильтр по клику → bulk-move второго теста → delete папки → оба теста пережили удаление, вернулись в Uncategorized; отдельный тест — viewer не видит "New folder", All tests/Uncategorized видны всем).

## Варианты флоу-картинки (Stage 4)

Опциональные скриншоты «что видит/делает вариант» по группам — чисто для отображения (Design tab, `design_report.html`), на сплит/анализ не влияют. Редактируются ТОЛЬКО через Redesign (тот же визард, что и Groups/Metrics) — отдельного edit-флоу нет.

**Модель** (миграция `0014_experiment_flow_images.py`, аддитивная): таблица `experiment_flow_images` (`id`, `experiment_id`, `group_name`, `flow_title`, `file_path`, `position`, `uploaded_by`, `uploaded_at`). **Важное отличие от датасетов**: `experiment_id` — `ON DELETE CASCADE`, не `SET NULL` — эти картинки ЧАСТЬ теста, а не самостоятельная сущность (в отличие от `datasets.experiment_id`, см. выше «Edit/Delete датасета»). Удаление эксперимента (`abkit/jobs.py::run_delete_experiment`) уже делает `shutil.rmtree()` на всю папку эксперимента — файлы флоу-картинок лежат внутри нее же (`<data_dir>/<experiment_name>/flow_images/`), поэтому для каскадного удаления файлов не нужен отдельный код, только `ON DELETE CASCADE` для строк.

**Загрузка/санитизация** (`abkit/flow_images.py`): тип файла проверяется ПО СОДЕРЖИМОМУ через Pillow (`Image.open().verify()`), не по расширению/заявленному Content-Type — тот же принцип, что у `sql_guard.py` для SQL. Разрешены PNG/JPEG/WEBP, лимиты — 5MB/файл, 10/группу (`MAX_FILE_BYTES`/`MAX_IMAGES_PER_GROUP`). Сохраняется САНИТИЗИРОВАННАЯ копия — пересохранение через Pillow (`img.save(...)`) само по себе роняет все, что не является пиксельными данными; downscale до 1600px по большей стороне (`_MAX_DIMENSION`), формат-источник сохраняется как есть (PNG остается PNG, и т.д.) — конвертация в RGB только когда реально нужно (JPEG без альфа-канала).

**API** (`abkit/jobs.py`: `run_upload_flow_image`/`run_delete_flow_image`/`run_set_flow_image_group_order`; `backend/routers/experiments.py`, эндпоинты `/experiments/{name}/flow-images*`): права — `require_experiment_edit_access` (owner/access-editor/admin, тот же гейт, что у Redesign/blocks/tags). `POST` — один файл за раз (multipart), позиция — в конец группы. `PUT .../flow-images/order` — финальная реконсиляция ОДНОЙ группы/колонки визарда за раз (`FlowImageRepo.set_group_order`): задает `flow_title` всем оставшимся картинкам, `position` из порядка присланного списка id, УДАЛЯЕТ (строка + файл) все существующие картинки группы, которых нет в списке — так реализованы удаление/перетасовка миниатюр в визарде: они копятся в состоянии визарда и применяются одним вызовом на submit, а не вживую по каждому клику. `GET /flow-images/{id}/file` отдает байты картинки (auth-gated, как и весь остальной доступ к файлам в проекте — публичного static-маунта на `/data` нет и не появится).

**Визард** (`frontend/src/pages/design-wizard/FlowImagesSection.tsx`, встроен в Step2GroupsMetrics.tsx после списка групп): колонка на каждую группу (`flowColumns`, синхронизируется по длине с `groups`, но `groupName` каждой колонки — отдельный редактируемый select с default = группа с тем же индексом), drag&drop через `Upload.Dragger` (`beforeUpload` возвращает `false` — файлы копятся в состоянии, реальная загрузка на сервер откладывается до `Step4Review`'s submit), лайтбокс — `antd`'s `Image`/`Image.PreviewGroup` (уже установлен, отдельная библиотека не нужна), перетасовка миниатюр — **dnd-kit** (`@dnd-kit/core`+`@dnd-kit/sortable`+`@dnd-kit/utilities`, новая зависимость — выбран за отсутствием HTML5-drag-специфичных проблем touch/React 19-совместимость; в проекте до этого drag-библиотек не было). Новые картинки — `File` в памяти (`kind: 'new'`, `previewUrl` — `blob:` object URL) до подтверждения дизайна; существующие (Redesign prefill) — `kind: 'existing'`, `previewUrl` указывает на реальный `GET .../flow-images/{id}/file`.

**Submit** (`Step4Review.tsx::saveFlowImages`, вызывается ПОСЛЕ успешного design/redesign, тем же best-effort паттерном, что и `saveHypothesis`): для каждой колонки — сначала `POST` каждой новой (`kind: 'new'`) картинки, затем ОДИН `PUT .../order` с полным желаемым списком id (существующие + только что созданные) в финальном порядке пользователя. Группы, у которых были картинки при открытии визарда (Redesign), но колонка целиком удалена пользователем — тоже получают `PUT .../order` с пустым списком (`state.originalFlowGroupNames`), иначе их картинки осиротели бы без явного удаления.

**design_report.html**: картинки встраиваются как base64 (`data:image/jpeg;base64,...`, `abkit/viz/report.py::_flow_image_data_uri`) — те же соображения self-contained-отчета, что и у `logo_data_uri`, но пересжимаются под ширину ~900px (меньше, чем хранимая копия 1600px — специально для отчета, не переписывается на диск). **Ловушка**: сохраненная копия может быть в любом Pillow-режиме (например `LA` — grayscale+alpha), а JPEG пишет напрямую только `RGB`/`L` — конвертация в RGB нужна для ЛЮБОГО режима кроме этих двух, не только `RGBA`/`P` (более узкая проверка тихо роняла `Image.save` и — так как ошибка молча проглатывалась — вся группа пропадала из отчета без единой строки в логах видимой пользователю; проверено e2e). Технически интересная часть — `design_report.html` пишется ОДИН РАЗ в момент дизайна (`Experiment.design()`), ДО того как картинки вообще существуют (они грузятся отдельным шагом сразу после) — секция с флоу поэтому не рендерится через обычный `render_design_report()` повторно (для этого нужен объект `DesignReport`, который не восстановить после факта через `Experiment.load()`), а ПАТЧИТСЯ на месте: `abkit/viz/report.py::render_flow_images_section()` перерендеривает только партиал `templates/_flow_images_section.html.j2` и вставляет его между HTML-комментариями `<!-- flow-images-section:start/:end -->`, которые партиал сам всегда содержит (даже когда картинок нет — секция пустая, но якоря есть). Вызывается автоматически в конце `run_set_flow_image_group_order` (`abkit/jobs.py::_regenerate_design_report`, best-effort, ошибка только логируется).

**Design tab**: секция «Variant flows» (`frontend/src/pages/experiment/DesignSection.tsx::VariantFlowsSection`) — читает `GET /flow-images` отдельным запросом (картинки не часть `config`/JSONB, в отличие от group descriptions), группирует по `group_name`, показывает описание группы (Stage 3) рядом; секции нет вообще, если картинок нет (не пустая секция).

Тесты: `tests/test_flow_images.py` (валидация/санитизация, включая LA-режим), `backend/tests/test_flow_images_api.py` (upload/list/serve/delete/reorder, права, каскадное удаление файлов при удалении эксперимента, регенерация отчета), `tests/test_viz_report.py` (сплайсинг секции в уже сохраненный HTML). E2E: `frontend/e2e/design-wizard.spec.ts` (визард с 2 группами → 2 картинки → drag-перестановка (dnd-kit, реальные mouse move/down/up) → лайтбокс на Design tab (не в визарде — там перетасовка тем же кликом иногда конфликтует с drag-сенсором dnd-kit при вызове через сам компонент миниатюры, поэтому лайтбокс-e2e осознанно проверяется на read-only отображении Design tab, где dnd-kit вообще не участвует) → отчет с картинками; отдельный тест — эксперимент без картинок не показывает секцию нигде).
