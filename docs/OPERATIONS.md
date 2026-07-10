# docs/OPERATIONS.md — регламент эксплуатации ABKit

Для инженера, который видит прод-развертывание ABKit впервые: как оно устроено,
как поднять с нуля, как обновить не потеряв данные, как откатиться, как бэкапить
и диагностировать проблемы. Технические ТЗ — [DOCKER.md](../DOCKER.md) (модель
ролей/БД), [FRONTEND.md](../FRONTEND.md) (React-UI/backend); практические
детали конкретных фич (Database Connections, TLS, импорт легаси-данных) —
[docker/README.md](../docker/README.md), этот документ его не дублирует, а
дает процессный регламент поверх (deploy/update/rollback/backup/diagnostics).

## 1. Архитектура

Четыре сервиса docker-compose за одним nginx (см. [docker-compose.yml](../docker-compose.yml)):

```
                        ┌──────────────────────┐
   браузер  ──── :8080 ─│        nginx          │  reverse proxy
                        │  /       -> frontend  │
                        │  /api/*  -> backend   │
                        └──────┬────────┬───────┘
                               │        │
                 ┌─────────────▼──┐   ┌─▼────────────────────┐
                 │    frontend     │   │      backend          │
                 │  React, статика│   │  FastAPI/uvicorn:      │
                 │  (nginx внутри) │   │  REST API, миграции   │
                 │                 │   │  Alembic при старте,   │
                 │                 │   │  bootstrap первого     │
                 │                 │   │  админа, job runner    │
                 │                 │   │  (ThreadPoolExecutor)  │
                 └─────────────────┘   └──────┬───────┬────────┘
                                               │       │
                                    ┌──────────▼──┐  ┌─▼─────────────────┐
                                    │  postgres:16 │  │ volume: abkit_data │
                                    │  users,      │  │ /data/experiments  │
                                    │  experiments,│  │ (parquet-датасеты, │
                                    │  datasets,   │  │ HTML-отчеты)       │
                                    │  audit_log   │  │                    │
                                    └──────────────┘  └────────────────────┘
```

- **nginx** — единственный порт наружу (`ABKIT_PORT`, дефолт 8080); маршрутизирует
  `/` на `frontend`, `/api/*` на `backend`. TLS опционален (см. docker/README.md §TLS).
- **frontend** — статическая сборка React (Vite), отдается nginx'ом внутри своего
  же контейнера; никакого рантайм-состояния.
- **backend** — FastAPI, вся бизнес-логика, REST API, применяет миграции Alembic
  и bootstrap первого администратора автоматически при старте контейнера
  (entrypoint, см. `docker/entrypoint-backend.sh`); джобы (design/analyze/
  validate/dataset-from-sql) выполняются в собственном ThreadPoolExecutor
  (`ABKIT_JOB_WORKERS`, дефолт 2) — отдельного воркер-сервиса/очереди нет,
  проект принципиально однопроцессный.
- **postgres** — единственное состояние в БД: пользователи, эксперименты,
  назначения групп (assignments), аудит-лог, метаданные датасетов/подключений.
  Не публикуется наружу — доступен только другим сервисам compose по имени
  `postgres`.
- **volume `abkit_data`** — тяжелые файлы вне БД: parquet-снимки датасетов,
  сгенерированные HTML-отчеты (`design_report.html`, `report.html`).
- **volume `abkit_pgdata`** — данные Postgres.

Оба volume — именованные Docker volumes, не bind-mount: контейнеры полностью
stateless и заменяемы, состояние живет в volumes (подробности — §5 ниже и
docker/README.md «Данные и перезапуски»).

## 2. Первичное развертывание

Требования: Docker Engine 24+, Docker Compose v2 (`docker compose version`),
открытый порт на хосте (дефолт 8080).

```bash
git clone https://github.com/<org>/abkit.git && cd abkit
git checkout v2.0.0            # см. CLAUDE.md: на серверы — только теги v*, не main
cp .env.example .env
# отредактировать .env — ОБЯЗАТЕЛЬНО сменить:
#   ABKIT_SECRET_KEY  (openssl rand -hex 32)
#   POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec backend abkit-admin create-admin --email admin@co.com
```

Через 1-2 минуты (сборка образов + старт Postgres) сервис доступен на
`http://<host>:${ABKIT_PORT:-8080}`. Миграции БД и bootstrap первого
администратора (если заданы `ABKIT_ADMIN_EMAIL`/`ABKIT_ADMIN_PASSWORD` в
`.env`) применяются автоматически при старте `backend` — шаг `create-admin`
можно пропустить в этом случае.

Проверка, что стек реально поднялся (smoke-чек, используется и в §3, и в
`scripts/update.sh`):

```bash
docker compose ps                              # все сервисы healthy/running
curl -sf http://localhost:${ABKIT_PORT:-8080}/login >/dev/null && echo OK
curl -sf http://localhost:${ABKIT_PORT:-8080}/api/v1/version
```

## 3. Обновление версии

**Правило (CLAUDE.md): деплой только по тегам `v*`.** `main` — ветка разработки,
на прод не катится напрямую.

```bash
# 1. Бэкап — ПЕРЕД любым обновлением, без исключений.
bash scripts/backup.sh

# 2. Checkout нового тега.
git fetch --tags
git checkout v2.1.0

# 3. Пересборка и рестарт (миграции применяются автоматически при старте backend).
docker compose up -d --build

# 4. Smoke-чек (см. §2) — все сервисы healthy, login и /api/v1/version отвечают.
docker compose ps
curl -sf http://localhost:${ABKIT_PORT:-8080}/login >/dev/null && echo OK
curl -sf http://localhost:${ABKIT_PORT:-8080}/api/v1/version
```

Все четыре шага делает одной командой `scripts/update.sh <тег>` (§ниже).

Даунтайм — время пересборки образов + перезапуска контейнеров (Postgres не
перезапускается, если его образ/конфиг не менялись). Если вместо локальной
сборки используются готовые образы из `ghcr.io/<org>/abkit-backend:<version>` /
`abkit-frontend:<version>` (публикуются CI на каждый тег `v*` —
`.github/workflows/ci.yml`, джоба `build-and-push`), шаг 3 — `docker compose pull && docker compose up -d`
вместо `--build` (потребует переключить `image:` вместо `build:` в
`docker-compose.yml` или отдельный `docker-compose.prod.yml`).

**Перед крупными обновлениями** (смена мажорной версии, миграции схемы БД,
правки `docker-compose.yml`/`entrypoint-*.sh`) — дополнительно прогнать
`bash docker/test_persistence.sh` (см. docker/README.md) до переключения
прод-трафика.

## 4. Откат на предыдущий тег

Если после обновления что-то не так — откат на предыдущий тег БЕЗОПАСЕН только
если миграции этого релиза были аддитивными (CLAUDE.md, правило релизов «б»:
удаление/переименование колонок — не раньше, чем через один релиз после
прекращения использования). При соблюдении этого правила старый код просто не
видит новых колонок/таблиц — их наличие ему не мешает.

```bash
git checkout v2.0.0             # предыдущий известный рабочий тег
docker compose up -d --build
docker compose ps
```

Откатывать САМИ данные (restore из бэкапа) нужно, только если откатываемый
релиз действительно ломал данные (а не просто содержал баг в UI/логике) — в
норме откат кода без отката БД безопасен именно благодаря правилу аддитивных
миграций. Если восстановление БД все же необходимо — см. §5.

## 5. Бэкап и восстановление

### Бэкап

```bash
bash scripts/backup.sh                # см. scripts/backup.sh — pg_dump + tar /data, дата в имени, ротация N=14
```

Делает то же самое, что было раньше в docker/README.md вручную:

```bash
docker compose exec postgres pg_dump -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}" > backup.sql
docker run --rm -v abkit_abkit_data:/data -v "$(pwd)":/backup alpine tar -czf /backup/data.tgz -C /data .
```

— но с датой в имени файлов и автоматической ротацией (не более 14 последних
наборов бэкапов, старые удаляются).

**Рекомендация: cron.** Ежедневный бэкап по ночам:

```cron
0 3 * * * cd /opt/abkit && bash scripts/backup.sh >> /var/log/abkit-backup.log 2>&1
```

Бэкапы кладутся в `backups/` внутри репозитория по умолчанию (настраивается
переменной `BACKUP_DIR` в `scripts/backup.sh`) — на проде эту директорию стоит
держать на отдельном диске/выгружать во внешнее хранилище (S3/аналог), сам
скрипт этим не занимается (см. комментарий в начале файла).

### Восстановление (на новом/пустом окружении)

```bash
docker compose up -d postgres
cat backups/<дата>/backup.sql | docker compose exec -T postgres psql -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}"
docker run --rm -v abkit_abkit_data:/data -v "$(pwd)/backups/<дата>":/backup alpine \
    sh -c "cd /data && tar -xzf /backup/data.tgz"
docker compose up -d
```

`docker compose down` (без `-v`) НЕ удаляет volumes — данные переживают
остановку/пересоздание контейнеров. `docker compose down -v` volumes удаляет
безвозвратно — использовать только осознанно (см. docker/README.md «Данные и
перезапуски» — полная таблица «команда → данные целы/нет»).

## 6. Управление пользователями

Через `docker compose exec backend abkit-admin <command>` ИЛИ через веб —
раздел **Admin** (виден только роли Admin):

```bash
docker compose exec backend abkit-admin create-admin --email admin@co.com
docker compose exec backend abkit-admin create-user  --email u@co.com --role editor
docker compose exec backend abkit-admin reset-password --email u@co.com
docker compose exec backend abkit-admin list-users
```

Если `--password` не передан — пароль генерируется и печатается в stdout один
раз (сохранить сразу). Роли: Viewer/Editor/Admin — матрица прав в
docker/README.md §«Роли» и CLAUDE.md §«Permissions model» (там же —
per-experiment права владения/доступа поверх базовой ролевой матрицы).

## 7. Диагностика

### Логи

```bash
docker compose logs -f backend    # структурированные JSON-логи (ABKIT_LOG_FORMAT=json по умолчанию)
docker compose logs -f frontend
docker compose logs -f postgres
docker compose logs -f nginx
```

`ABKIT_LOG_FORMAT=text` в `.env` переключает `backend` на человекочитаемый
формат для отладки на живую руку.

### Healthchecks

```bash
docker compose ps                                            # STATUS столбец: healthy/unhealthy/starting
curl -sf http://localhost:${ABKIT_PORT:-8080}/api/v1/version  # backend жив и отвечает через nginx
docker compose exec postgres pg_isready -U "${POSTGRES_USER:-abkit}"
```

`backend` считается healthy по `GET /api/health` (интервал 10с, 5 попыток,
`start_period` 30с — см. `docker-compose.yml`); `nginx` стартует только после
`backend: condition: service_healthy`, так что зависший backend виден сразу по
`docker compose ps`, а не только по 502 в браузере.

### Типовые проблемы

| Симптом | Вероятная причина | Что делать |
|---|---|---|
| `backend` не становится healthy | Приложение не стартует само по себе, отказался стартовать (проверка `ABKIT_SECRET_KEY` при дефолтном `change-me...`) — либо БД недоступна | `docker compose logs backend` — обычно явное сообщение об ошибке в первых строках |
| 502 / пустая страница на `:8080` | `backend`/`frontend` еще не healthy (первый старт после `--build`) | Подождать `start_period` (30с) + пару healthcheck-интервалов, затем `docker compose ps` |
| Login не проходит с верным паролем | Rate-limit по попыткам логина на email (защита от брутфорса) | Подождать, либо `docker compose exec backend abkit-admin reset-password` для сброса |
| Пароли Database Connections «сломались» после смены `ABKIT_SECRET_KEY` | Ключ шифрования Fernet выводится из `ABKIT_SECRET_KEY` — ротация ключа обесценивает сохраненные пароли подключений | Пересоздать подключения к БД в Settings → Data → Database Connections после смены секрета |
| `docker compose up -d --build` зависает на пересборке backend | Транзиентная сетевая ошибка pip-загрузки зависимостей (`IncompleteRead` и т.п.) | Повторить `docker compose up -d --build` — обычно проходит со второго раза |
| Датасет из SQL падает с ошибкой на `SELECT` | Guard `sql_guard.py` разрешает только `SELECT`/CTE — попытка мутации отклоняется намеренно | Проверить, что запрос действительно read-only; см. docker/README.md §«Подключения к базам данных» |
| Нужно посмотреть, что реально творится в БД | — | `docker compose exec postgres psql -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}"` |

Для более глубокой диагностики конкретных фич (Database Connections
test-connection категории ошибок, импорт легаси-данных, TLS) — см.
[docker/README.md](../docker/README.md), у него более узкий, но более
подробный фокус на настройке отдельных возможностей.

## 8. Гигиена dev/e2e-окружения

Актуально при разработке/отладке ABKit самого по себе (не для конечных
пользователей продукта) — полный чек-лист и обоснование в CLAUDE.md,
«Правило: гигиена dev-артефактов». Кратко:

- **`bash scripts/e2e.sh`** — единственный способ гонять Playwright e2e
  локально: поднимает одноразовый docker-compose стек под отдельным project
  name (свои volumes/сеть, порт `:8090` по умолчанию — не мешает
  персистентному dev-стеку на `:8080`), прогоняет `npx playwright test`,
  затем ВСЕГДА (успех/провал/Ctrl-C, через `trap ... EXIT`) делает
  `docker compose down -v`. Никогда не гонять `npx playwright test` вручную
  с `E2E_BASE_URL`, указывающим на постоянный dev-стек — так на живом стеке
  накопилось 171 тестовый эксперимент/247 датасетов/73 лишних пользователя
  за несколько сессий разработки, прежде чем это заметили.
- **`docker compose exec backend abkit-admin cleanup-dev [--dry-run] [--min-age-hours N]`**
  — сметает сущности с префиксом `_dev_` (любого возраста) и всё, что
  принадлежит аккаунтам `*@e2e.test`, старше `--min-age-hours` (default 1).
  `--dry-run` только печатает список кандидатов, без удаления. Аккаунты
  `admin@e2e.test`/`viewer@e2e.test` (жестко зашиты как логин в e2e-хелперах)
  никогда не деактивируются — только то, что они создали. Ничего, что не
  подходит под эти два признака (не `_dev_`-префикс и не `@e2e.test`), не
  трогается — реальные пользовательские данные невидимы для этой команды по
  построению. Полный docstring с матрицей "что именно matches" —
  `abkit/jobs.py::run_cleanup_dev`.
- Вызывайте `cleanup-dev` после КАЖДОГО пакета ручной отладки на живом
  стеке, не только когда стек выглядит захламленным — это дешевая,
  идемпотентная команда (пустой результат = "Nothing to clean.").
