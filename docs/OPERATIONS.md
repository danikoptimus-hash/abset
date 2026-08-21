# docs/OPERATIONS.md — регламент эксплуатации ABSet

Для инженера, который видит прод-развертывание ABSet впервые: как оно устроено,
как поднять с нуля, как обновить не потеряв данные, как откатиться, как бэкапить
и диагностировать проблемы. Технические ТЗ — [DOCKER.md](../DOCKER.md) (модель
ролей/БД), [FRONTEND.md](../FRONTEND.md) (React-UI/backend); практические
детали конкретных фич (Database Connections, TLS, импорт легаси-данных) —
[docker/README.md](../docker/README.md), этот документ его не дублирует, а
дает процессный регламент поверх (deploy/update/rollback/backup/diagnostics).

**Два контура развертывания, не перепутать:**

| | Корпоративный (основной) | Из исходников (запасной) |
|---|---|---|
| Где описан | §2-4 | Приложение А |
| Целевая машина | CLK2-ABSET-01, без интернета | любая с интернетом |
| Откуда образы | внутренний Harbor `hb.intra.click.uz` | собираются на месте |
| Откуда код | внутренний GitLab | GitHub |
| Что запущено определяет | `ABKIT_VERSION` в `.env` | `git checkout <тег>` |
| Команда запуска | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` | `docker compose up -d --build` |
| Обновление | правка `ABKIT_VERSION` + `pull` + `up` | `scripts/update.sh <тег>` |

Сборка и публикация релиза — всегда на рабочей машине разработчика
(`scripts/release.sh`, §3.1), никогда на прод-VM.

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

## 2. Первичное развертывание (корпоративный контур)

Целевая машина — **CLK2-ABSET-01** (Oracle Linux 9). У нее **нет выхода в
интернет**: образы приезжают из внутреннего Harbor (`hb.intra.click.uz`), код и
compose-файлы — из внутреннего GitLab. Собирать образы на ней невозможно и не
нужно; сборка живет на рабочей машине разработчика (§3.1, `scripts/release.sh`).

Развертывание из исходников (`docker compose up -d --build`) осталось как
**запасной путь для контуров без Harbor** — приложение А в конце документа.

### 2.1 Предварительные требования на VM

```bash
# Docker Engine + Compose v2. ИМЕННО docker, не podman: в OL9 из коробки
# ставится podman-docker (shim), который выдает себя за docker, но не
# поддерживает `docker compose` v2 и healthcheck-семантику, на которую
# опирается depends_on: condition: service_healthy в docker-compose.yml.
docker --version           # ожидается "Docker version 24+", НЕ "podman"
docker compose version     # ожидается "Docker Compose version v2.x"

# Если отвечает podman — снести shim и поставить настоящий Docker CE:
#   sudo dnf remove -y podman-docker podman
#   sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
#   sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
#   sudo systemctl enable --now docker
# (репозиторий docker-ce должен быть замирроren внутри — интернета нет)

# Порт приложения наружу. В OL9 firewalld включен по умолчанию, и без этого
# шага стек поднимется, но снаружи (в т.ч. с балансировщика) будет "connection
# refused" при полностью здоровых контейнерах — самая частая потеря времени
# на первом развертывании.
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports          # проверить, что 8080/tcp в списке
```

### 2.2 Логин в Harbor

```bash
docker login hb.intra.click.uz
# логин/пароль (или robot-аккаунт) выдает администратор Harbor.
# Учетка нужна ТОЛЬКО на чтение (pull) — публикует образы рабочая машина.
```

Учетные данные сохраняются в `~/.docker/config.json` того пользователя, от
которого потом будет выполняться `docker compose pull`. Если стек поднимается
через systemd от `root`, а `docker login` выполнен от обычного пользователя —
pull упадет с `unauthorized`; логиниться нужно тем же пользователем.

### 2.3 Код из GitLab

```bash
sudo mkdir -p /opt/abset && sudo chown "$USER" /opt/abset
git clone https://gitlab.intra.click.uz/<group>/abset.git /opt/abset
cd /opt/abset
git checkout v2.6.0        # тег релиза; main на прод не разворачивается
```

**GitHub на VM недоступен и не нужен.** Внутренний GitLab — источник правды для
развертывания: `scripts/release.sh` пушит туда `main` и теги при каждом релизе
(§3.1). Из репозитория на VM берутся только `docker-compose*.yml`, `.env` и
скрипты обслуживания — код приложения приезжает внутри образов.

### 2.4 .env

```bash
cp .env.example .env
```

Обязательно заполнить:

| Переменная | Значение |
|---|---|
| `ABKIT_SECRET_KEY` | `openssl rand -hex 32` — свой, не из примера |
| `POSTGRES_PASSWORD` | свой |
| `ABKIT_REGISTRY` | `hb.intra.click.uz/abset` |
| `ABKIT_VERSION` | разворачиваемый тег, напр. `v2.6.0` |
| `ABKIT_POSTGRES_IMAGE` | образ postgres, доступный из Harbor (см. ниже) |
| `ABKIT_PUBLIC_URL` | внешний адрес за балансировщиком, напр. `https://abset.intra.click.uz` |

Необязательные, но полезные при настройке под конкретный контур (полный
список с комментариями — `.env.example`):

| Переменная | Дефолт | Что делает |
|---|---|---|
| `ABKIT_SQL_TIMEOUT_SEC` | `300` | таймаут выгрузки датасета из SQL (from-sql/refresh) — фоновая джоба |
| `ABKIT_SQL_LAB_TIMEOUT_SEC` | `60` | таймаут ИНТЕРАКТИВНОГО прогона в SQL Lab. Отдельная ручка: там ответа ждет человек, и 5 минут молчания в UI неприемлемы. Поднимать имеет смысл, только если аналитики штатно упираются в него на тяжелых источниках — предпочтительнее сузить запрос или собрать датасет |
| `ABKIT_SQL_MAX_ROWS` | `5000000` | обрезка датасета из SQL (в UI — флаг truncated) |

Размер интерактивного превью в SQL Lab (1000 строк) намеренно НЕ вынесен в
env: это предел читаемости грида и объема JSON-ответа, а не ресурсная
политика контура.

`ABKIT_POSTGRES_IMAGE` — два варианта, оба описаны в комментарии
`docker-compose.prod.yml`:

1. **Proxy cache Harbor** (предпочтительно): `hb.intra.click.uz/dockerhub/library/postgres:16-alpine`
   — Harbor сам подтянет и закэширует upstream-образ.
2. **Ручное зеркало**, если proxy cache не настроен. На машине С интернетом:
   ```bash
   docker pull postgres:16-alpine
   docker tag  postgres:16-alpine hb.intra.click.uz/abset/postgres:16-alpine
   docker push hb.intra.click.uz/abset/postgres:16-alpine
   ```

### 2.5 Запуск

```bash
cd /opt/abset
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**Порядок `-f` важен**: последний файл выигрывает, прод-оверлей обязан идти
вторым. Оверлей заменяет сборку из исходников на готовые образы из Harbor и
снимает секции `build:` — если случайно запустить только базовый файл, compose
попробует собрать образы на месте и упрется в отсутствие интернета.

Чтобы не набирать две длинные `-f` каждый раз, можно один раз задать в
профиле (или в `/etc/environment`):

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
# после этого достаточно: docker compose pull && docker compose up -d
```

Первый администратор:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    exec backend abkit-admin create-admin --email admin@click.uz
```

(Шаг можно пропустить, если `ABKIT_ADMIN_EMAIL`/`ABKIT_ADMIN_PASSWORD` заданы в
`.env` — тогда админ заводится автоматически при старте backend.)

### 2.6 Проверка

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -sf http://localhost:8080/api/v1/version     # должно совпасть с ABKIT_VERSION
curl -sf http://localhost:8080/login >/dev/null && echo OK
```

Полный чек-лист первого развертывания с тем, что именно проверять, — §2.7.

### 2.7 Чек-лист первого развертывания CLK2-ABSET-01

Проходить сверху вниз; каждый пункт либо зеленый, либо разбирается до конца —
пропущенный пункт всплывет в момент, когда система уже нужна людям.

**Подготовка машины**

- [ ] `docker --version` отвечает Docker CE 24+, **не** podman-shim (§2.1)
- [ ] `docker compose version` отвечает v2.x
- [ ] `sudo firewall-cmd --list-ports` содержит `8080/tcp`
- [ ] диск под `/var/lib/docker` и volume'ы: не меньше 50 ГБ свободно
      (`df -h /var/lib/docker`) — parquet-снимки датасетов живут в volume
      `abkit_data`
- [ ] системное время синхронизировано (`timedatectl status` → NTP active) —
      от него зависят и подписи сессий, и допуск часов при проверке SSO-токенов

**Доступы**

- [ ] `docker login hb.intra.click.uz` успешен от того пользователя, который
      будет поднимать стек
- [ ] `docker pull $ABKIT_REGISTRY/abset-backend:$ABKIT_VERSION` проходит
      вручную (проверка и доступа, и того, что релиз реально опубликован)
- [ ] `git clone`/`git pull` с внутреннего GitLab работает с VM
- [ ] GitHub с VM **не** требуется — убедиться, что в `git remote -v`
      рабочей копии на VM стоит GitLab

**Конфигурация**

- [ ] `.env` заполнен по таблице §2.4; `ABKIT_SECRET_KEY` сгенерирован свой
- [ ] `ABKIT_VERSION` — конкретный тег, не `latest`
- [ ] `ABKIT_PUBLIC_URL` — адрес, по которому ходят пользователи (за LB), а не
      `localhost`: из него строится redirect_uri для SSO (§6.1)
- [ ] `.env` не в git (`git status` чист)

**Запуск**

- [ ] `docker compose ... pull` вытянул все четыре образа без ошибок
- [ ] `docker compose ... ps` — backend/postgres `healthy`, frontend/nginx
      `running`
- [ ] `curl http://localhost:8080/api/v1/version` возвращает **тот же** тег,
      что в `ABKIT_VERSION` (несовпадение = развернуто не то, что думали)
- [ ] `docker compose ... logs backend | grep -i "alembic"` — миграции
      применились без ошибок
- [ ] страница логина открывается **через балансировщик**, по внешнему адресу,
      а не только с самой VM (это проверяет и firewalld, и маршрут LB)
- [ ] вход первым администратором работает

**Интеграции**

- [ ] Settings → Data → Database Connections: создать подключение к MSSQL,
      кнопка **Test connection** отвечает «Connection successful»
- [ ] то же для ClickHouse — **порт 8443** (HTTPS-интерфейс), не 9440/9000:
      драйвер `clickhouse-connect` говорит по HTTP-протоколу, на нативный порт
      он не подключится (CLAUDE.md, Database Connections)
- [ ] на каждом подключении — «Preview» на реальном запросе (проверяет не
      только сеть, но и права учетки на чтение)

**Эксплуатация**

- [ ] `bash scripts/backup.sh` отрабатывает вручную и создает каталог в
      `backups/`
- [ ] бэкап поставлен в cron и **проверен**:
      ```bash
      sudo crontab -e
      # ежедневно в 03:30
      30 3 * * * cd /opt/abset && /usr/bin/bash scripts/backup.sh >> /var/log/abset-backup.log 2>&1
      ```
      затем `sudo crontab -l` и разовый прогон строки руками
- [ ] Settings → Security → **Monitoring** показывает живые точки (память
      backend, размер БД, свободное место) — раздел наполняется фоновым
      потоком раз в минуту, сразу после старта график пуст, это нормально;
      кнопка «Snapshot now» даёт точку немедленно
- [ ] Admin → Action log показывает записи (как минимум вход администратора)
- [ ] задокументировано, кому уходят алерты по месту на диске (приложение само
      его только показывает, не рассылает)

## 3. Обновление версии

**Правило (CLAUDE.md): на серверы разворачиваются только теги `v*`.** `main` —
ветка разработки.

В корпоративном контуре обновление — это **смена одной строки в `.env` и pull**.
Ни сборки, ни `git checkout` кода приложения: что именно работает, определяет
`ABKIT_VERSION`, а не состояние рабочей копии.

```bash
cd /opt/abset

# 1. Бэкап — ПЕРЕД любым обновлением, без исключений.
bash scripts/backup.sh

# 2. Обновить compose-файлы/скрипты обслуживания (не код приложения).
git pull
git checkout v2.7.0

# 3. Указать новую версию образов.
sed -i 's/^ABKIT_VERSION=.*/ABKIT_VERSION=v2.7.0/' .env

# 4. Вытянуть образы и перезапустить (миграции применяются автоматически
#    при старте backend).
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 5. Smoke.
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -sf http://localhost:8080/api/v1/version   # должно совпасть с v2.7.0
curl -sf http://localhost:8080/login >/dev/null && echo OK
```

Даунтайм — время перезапуска контейнеров (postgres не перезапускается, если его
образ не менялся). Образы к этому моменту уже скачаны шагом 4, поэтому пауза
измеряется секундами, а не минутами сборки.

### 3.1 Как выпускается релиз (на рабочей машине разработчика)

Собирает и публикует `scripts/release.sh` — на машине, у которой есть и
интернет (для базовых образов и зависимостей), и доступ к Harbor:

```bash
export ABKIT_REGISTRY=hb.intra.click.uz/abset
docker login hb.intra.click.uz
bash scripts/release.sh v2.7.0
```

Что он делает: проверяет, что дерево чистое и ветка `main`; прогоняет быстрый
гейт качества (pyflakes + typecheck/lint фронта + юнит-подмножество backend —
**не** полный прогон, тот является гейтом приемки пакета и занимает ~15 минут);
собирает три образа; тегирует `:vX.Y.Z` и `:latest`; пушит; ставит git-тег и
пушит его **в оба remote** — `origin` (GitHub) и `gitlab` (внутренний); печатает
сводку с digest'ами и готовыми командами для VM.

Внутренний GitLab настраивается один раз:

```bash
bash scripts/setup_gitlab_remote.sh https://gitlab.intra.click.uz/<group>/abset.git
```

Пока remote `gitlab` не настроен, `release.sh` **предупреждает, но не падает**:
образы публикуются, а до GitLab релиз не доезжает — то есть развернуть его на VM
не получится, о чем скрипт и говорит прямым текстом.

Проверить весь конвейер, не трогая настоящий Harbor, можно локально: скрипт
`scripts/test_release_local_registry.sh` поднимает `registry:2`, прогоняет через
него `release.sh` с выбрасываемой версией и убеждается, что образы
опубликованы, тянутся обратно, а прод-оверлей на них ссылается — и что стек из
этих образов реально поднимается и отвечает.

## 4. Откат на предыдущий тег

Откат — то же обновление, только версия меньше. Безопасен, если миграции
откатываемого релиза были аддитивными (CLAUDE.md, правило релизов «б»:
удаление/переименование колонки — не раньше, чем через релиз после того, как код
перестал ее использовать). При соблюдении этого правила старый код просто не
видит новых колонок, и их наличие ему не мешает.

```bash
cd /opt/abset
sed -i 's/^ABKIT_VERSION=.*/ABKIT_VERSION=v2.6.0/' .env   # предыдущий рабочий тег
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
curl -sf http://localhost:8080/api/v1/version              # должно совпасть с v2.6.0
```

Образ предыдущей версии обычно еще лежит локально, так что откат — это
секунды. Именно поэтому `ABKIT_VERSION` держится в `.env`, а не выводится из
git-тега рабочей копии: откат не должен зависеть ни от сети, ни от состояния
репозитория.

Откатывать САМИ данные (restore из бэкапа) нужно, только если откатываемый релиз
действительно портил данные, а не просто содержал баг в UI/логике — в норме
откат кода без отката БД безопасен благодаря правилу аддитивных миграций. Если
восстановление БД все же необходимо — см. §5.

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

## 6.1 SSO (Keycloak OIDC)

Вход через корпоративный Keycloak: сотрудник, уже авторизованный в SSO,
жмет **Sign in with SSO** и попадает в ABSet без второго логина. Роль
берется из его групп. Уволенный/заблокированный в AD теряет доступ **без
единого действия с нашей стороны** — Keycloak просто перестает его пускать.

**Выключено по умолчанию.** Без `ABKIT_OIDC_ENABLED=true` ничего не
меняется: вход по паролю работает ровно как раньше, кнопки SSO на странице
логина нет. Парольный вход НЕ отключается и при включенном SSO — иначе
поломка Keycloak отрезала бы от системы всех, включая администраторов
(break-glass admin, заведенный `abkit-admin create-admin`, входит паролем
всегда).

### Переменные окружения

| Переменная | Обязательна | Значение для прода |
|---|---|---|
| `ABKIT_OIDC_ENABLED` | — | `true` |
| `ABKIT_OIDC_ISSUER` | да, если enabled | `https://keycloak.intra.click.uz/realms/<realm>` |
| `ABKIT_OIDC_CLIENT_ID` | да, если enabled | `abset` |
| `ABKIT_OIDC_CLIENT_SECRET` | да, если enabled | выдает админ Keycloak |
| `ABKIT_PUBLIC_URL` | да, если enabled | `https://abset.intra.click.uz` |
| `ABKIT_OIDC_ROLE_CLAIM` | нет | `groups` (по умолчанию) |
| `ABKIT_OIDC_ROLE_MAP` | нет | `{"abset-admins":"admin","abset-editors":"editor","abset-viewers":"viewer"}` |
| `ABKIT_OIDC_DEFAULT_ROLE` | нет | `viewer` — или **пусто**, чтобы отклонять всех, кто не в группах |
| `ABKIT_OIDC_LOGOUT_UPSTREAM` | нет | `false` (по умолчанию) |
| `ABKIT_OIDC_INTERNAL_BASE_URL` | нет | **пусто в проде** (нужна только dev-окружению) |

Тонкости, которые стоит знать до включения:

- **`ABKIT_PUBLIC_URL` обязателен и используется буквально.** Из него
  строится `redirect_uri`; из заголовков запроса (`Host`,
  `X-Forwarded-Host`) он не строится НИКОГДА — заголовок подконтролен
  клиенту, и на нем redirect_uri был бы вектором увода кода авторизации на
  чужой домен. Значение должно побайтово совпадать с тем, что
  зарегистрировано у Keycloak.
- **Пустой `ABKIT_OIDC_DEFAULT_ROLE` — значащее значение**, а не «не
  задано»: «у кого нет ни одной сопоставленной группы — не пускать вовсе».
  Это осмысленный выбор для закрытого инструмента; `viewer` — для
  «пусть все сотрудники хотя бы смотрят».
- **Побеждает самая высокая роль.** Пользователь и в `abset-editors`, и в
  `abset-admins` получит `admin`. Ключ `"*"` в карте — общий fallback для
  аутентифицированных, участвует в том же выборе максимума.
- **Роль перечитывается из групп на КАЖДОМ входе.** Перевели человека из
  editors в admins — новая роль применится при следующем входе, руками у нас
  делать нечего. Обратное тоже верно: роль, выставленную вручную в Admin →
  Users, следующий SSO-вход перезапишет группами.
- **Смена `ABKIT_SECRET_KEY` рвет незавершенные входы** (им подписана
  короткоживущая cookie транзакции) — пользователю достаточно нажать «Try
  again». На уже выданные сессии это действует так же, как и раньше.

### Запрос администратору Keycloak (отправлять как есть)

> Просим завести в Keycloak (`https://keycloak.intra.click.uz`) клиент для
> внутреннего сервиса **ABSet** (A/B-тестирование, `https://abset.intra.click.uz`).
>
> **Realm:** существующий корпоративный (нужно его точное имя — оно войдет
> в issuer-URL вида `https://keycloak.intra.click.uz/realms/<realm>`).
>
> **Клиент:**
> - Client ID: `abset`
> - Client type: **OpenID Connect**, **confidential** (Client authentication:
>   **On**) — нужен client secret, его просим передать нам защищенным каналом
> - Standard flow (Authorization Code): **включен**
> - Direct access grants (password grant): **выключен**
> - Implicit flow: **выключен**
> - Service accounts: **не нужны**
> - PKCE: `S256` (Advanced → Proof Key for Code Exchange Code Challenge
>   Method) — мы отправляем PKCE всегда
> - **Valid redirect URI:** `https://abset.intra.click.uz/api/v1/auth/oidc/callback`
>   (ровно один, без вайлдкардов)
> - **Valid post logout redirect URI:** `https://abset.intra.click.uz/login`
> - Web origins: `https://abset.intra.click.uz`
>
> **Claims в ID-токене.** Нам нужен claim со списком групп пользователя
> **именно в ID-токене** (не только в access-токене и не только в userinfo):
> - добавить клиенту protocol mapper типа **Group Membership**
> - Token Claim Name: `groups`
> - Full group path: **Off** (или On — мы понимаем оба формата)
> - Add to ID token: **On**
>
> Также нужны стандартные claims `email`, `email_verified`, `given_name`,
> `family_name` (scope `email` + `profile`). **`email_verified` должен быть
> `true`** у сотрудников: ABSet сопоставляет учетные записи по
> подтвержденному email и отклоняет вход с неподтвержденным адресом.
>
> **Группы доступа** (создать, если их нет) — по ним ABSet выдает роль:
> - `abset-admins` → роль Admin в ABSet
> - `abset-editors` → роль Editor
> - `abset-viewers` → роль Viewer
>
> Пользователи добавляются в эти группы обычным порядком (в т.ч. через
> синхронизацию с AD). Отдельной заявки на каждого сотрудника не нужно:
> учетная запись в ABSet создается автоматически при первом входе, роль
> берется из групп и обновляется при каждом следующем входе.
>
> **Что нам вернуть:** имя realm, client secret.

### Чек-лист развертывания

1. **ДО объявления SSO сотрудникам — добавить администраторов платформы в
   `abset-admins`.** Роль присваивается по группам при ПЕРВОМ SSO-входе:
   если админ войдет через SSO, не будучи в группе, он получит роль из
   `ABKIT_OIDC_DEFAULT_ROLE` (viewer) либо отказ — и чинить это придется
   break-glass админом через Admin → Users. Порядок «сначала группы, потом
   анонс» снимает вопрос целиком.
2. Прописать переменные из таблицы выше в `.env`, **сохранив** парольного
   break-glass админа (`abkit-admin create-admin`) — он единственный путь
   внутрь, если Keycloak недоступен.
3. `bash scripts/update.sh vX.Y.Z` (обычное обновление, миграция `0024`
   аддитивная — колонка `users.auth_provider` со значением `password` у всех
   существующих строк).
4. Проверить: страница `/login` показывает кнопку **Sign in with SSO**,
   парольная форма — под спойлером «Sign in with password».
5. Войти самому через SSO, убедиться в роли (Admin → Users, колонка
   **Auth** = `SSO`).

### Что происходит при увольнении

Отзыв доступа — операция на стороне Keycloak/AD, у нас руками ничего делать
не нужно. Есть два сценария, и они отличаются тем, где именно человек
получит отказ:

| Действие в Keycloak | Что видит пользователь | Что попадает в наш audit_log |
|---|---|---|
| Аккаунт **отключен** (disabled) | Ошибка на форме логина **Keycloak** — до ABSet он не доходит | ничего (наш callback не вызывался) |
| **Убран из групп** доступа | Наша страница «Could not sign you in» с объяснением | `auth.oidc_login_rejected` (`reason: no_role_mapping`) |
| Деактивирован в ABSet (Admin → Users) | Наша страница «Could not sign you in» | `auth.oidc_login_rejected` (`reason: inactive`) |

Во всех трех случаях **отступить на пароль он не может**: у аккаунта,
заведенного через SSO, пароля нет вовсе (хранится заведомо не-хеш, см.
`abkit/auth/passwords.py::NO_PASSWORD_SENTINEL`), и попытка входа паролем
дает внятное «This account signs in through corporate SSO».

**Важное ограничение по времени.** ABSet не проксирует токены Keycloak и не
опрашивает IdP на каждый запрос — после входа действует НАША сессия
(`ABKIT_SESSION_LIFETIME_HOURS`, по умолчанию 72 часа). Уже вошедший
сотрудник, которого отключили в AD, останется в системе до истечения своей
сессии. Если нужно отрезать немедленно — деактивировать его в Admin → Users
(это действует сразу, на каждом запросе) или уменьшить время жизни сессии.

### Аудит

Все события SSO попадают в **Admin → Action log** (и в `audit_log`):

| Действие | Когда |
|---|---|
| `auth.oidc_login` | успешный вход (в деталях — роль и группы) |
| `auth.oidc_user_provisioned` | учетная запись создана при первом входе |
| `auth.oidc_role_changed` | роль изменилась из-за смены групп (`from` → `to`) |
| `auth.oidc_login_rejected` | отказ: нет групп, деактивирован, невалидный state/nonce, отказ IdP |

### Диагностика

| Симптом | Причина / что делать |
|---|---|
| Кнопки SSO нет на `/login` | `ABKIT_OIDC_ENABLED` не `true`, ЛИБО конфиг битый (например невалидный JSON в `ABKIT_OIDC_ROLE_MAP`). Второе намеренно НЕ роняет страницу логина — смотреть лог backend, там будет причина |
| «Single sign-on is misconfigured» | не заданы обязательные переменные — в тексте ошибки перечислено, какие |
| «Discovery issuer mismatch» | `ABKIT_OIDC_ISSUER` не совпадает с тем, что realm сообщает о себе. Обычно опечатка в имени realm или лишний/недостающий слэш |
| «not a member of any group that grants access» | пользователя нет ни в одной группе из `ABKIT_OIDC_ROLE_MAP`, а `ABKIT_OIDC_DEFAULT_ROLE` пуст |
| «not marked as verified» | у пользователя в Keycloak `email_verified=false` — чинится на стороне IdP |
| «state mismatch» / «link has expired» | вход начали в одной вкладке, завершили в другой, либо форма логина Keycloak провисела больше 10 минут. «Try again» решает |
| Redirect URI mismatch (ошибка Keycloak) | `ABKIT_PUBLIC_URL` не совпадает с Valid redirect URI у клиента |

### Локальная разработка и тесты

Корпоративный realm для этого не нужен — есть одноразовый Keycloak в
`docker-compose.keycloak.yml` (**dev-only**, прод-compose он не трогает: в
проде Keycloak — внешняя инфраструктура SRE):

```bash
# dev-стек вместе с Keycloak (реалм abset-dev импортируется автоматически)
docker compose -f docker-compose.yml -f docker-compose.keycloak.yml up -d --build
# Keycloak: http://localhost:8081 (admin/admin), ABSet: http://localhost:8080

# e2e-сценарии SSO (одноразовый изолированный стек, как обычный scripts/e2e.sh)
bash scripts/e2e.sh --keycloak e2e/sso.spec.ts
```

Пользователи dev-реалма (пароль у всех `password`): `alice` →
`abset-admins`, `bob` → `abset-editors`, `carol` → без групп (отказ),
`dave` → неподтвержденный email (отказ).

Единственная переменная, которая нужна dev-окружению и не нужна проду —
`ABKIT_OIDC_INTERNAL_BASE_URL`: браузер видит Keycloak как
`localhost:8081`, а backend внутри контейнера по этому адресу увидел бы
себя. Проверка `iss` при этом остается на публичном issuer, переписываются
только серверные вызовы (discovery/token/JWKS).


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

Любая неожиданная (не 4xx-валидационная) ошибка API отвечает пользователю
`{"error": {"code": "internal_error", "message": "Internal processing error
(ref: <8 hex-символов>)", "details": {"error_id": "<то же самое>"}}}` — этот
`error_id` пишется и в лог рядом со структурированным traceback'ом
(`"msg": "unhandled_exception"`), так что по сообщению из UI можно сразу
найти нужную строку без перебора логов по времени запроса:

```bash
docker compose logs backend | grep <error_id>
```

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
| Job завис / backend перезапустился посреди design/analyze | Вероятный OOM: job превысил `mem_limit` (§8.1), ядро/Docker убили процесс | `docker inspect backend --format '{{.State.OOMKilled}}'` (`true` — подтвержденный OOM) + `dmesg \| grep -i "killed process"` на хосте. При рестарте job, застрявшая в running/pending, сама помечается failed с понятным сообщением ("The backend restarted while this job was running") — см. §8.1; если backend НЕ поднялся сам, `docker compose up -d` вручную (см. §8.1 про ограничения `restart: unless-stopped` на некоторых хостах). Если OOM подтвержден — поднять `ABKIT_BACKEND_MEM_LIMIT` в `.env` либо уменьшить датасет/отключить неважные для этого прогона метрики |
| Database size (Monitoring) остается большим после удаления многих экспериментов | Post-DELETE bloat, не реальные данные: обычный `VACUUM` (который уже запускается автоматически после `abkit-admin cleanup-dev` и после каждого удаления эксперимента — item A2, DB bloat пакет) освобождает место только ЛОГИЧЕСКИ (для будущих INSERT), но не возвращает файл на диске ОС — это делает только `VACUUM FULL` (эксклюзивная блокировка таблицы). Именно так набралось 2+ ГБ на `assignments` от серии memory-стресс-тестов до появления этой автоматизации | Ничего делать вручную не нужно, ПОКА Monitoring-панель не показывает хинт "Table X has high bloat" (dead-tuple ratio > 30% И размер > 100 МБ — раз в неделю то же самое пишется в лог backend как `monitoring.table_bloat_detected`). Если хинт показался — `docker compose exec postgres psql -U "${POSTGRES_USER:-abkit}" "${POSTGRES_DB:-abkit}" -c 'VACUUM FULL VERBOSE ANALYZE <table>;'` в окно обслуживания (эксклюзивная блокировка — заблокирует чтение/запись этой таблицы на время операции); НЕ автоматизировано намеренно — это осознанное решение человека, не то, что должно происходить само |

Для более глубокой диагностики конкретных фич (Database Connections
test-connection категории ошибок, импорт легаси-данных, TLS) — см.
[docker/README.md](../docker/README.md), у него более узкий, но более
подробный фокус на настройке отдельных возможностей.

## 8. Мониторинг

Admin-страница (Settings → Security → **Monitoring**, `/settings/monitoring`,
роль Admin — своя страница, не таб внутри List Users, с итема 6 пакета
audit-details+) показывает, что ABSet потребляет прямо сейчас и в динамике —
собственный процесс backend, без Docker-сокета, без per-container метрик
хоста и без дополнительных сервисов (Prometheus/Grafana): вся история хранится
в той же Postgres, что и остальные данные, поэтому переживает рестарт
контейнера (в отличие от `docker stats`/OS task manager).

**Что собирается** (`abkit/monitoring.py::MonitoringCollector`, демон-поток,
стартует вместе с job runner'ом в `backend/main.py`, тик раз в 60с):
память backend-процесса (RSS, `psutil`), полный размер БД
(`pg_database_size`), размер каталога данных (`ABKIT_DATA_DIR` — обход через
`os.walk`, кэшируется на 5 минут, не пересчитывается на каждый тик), свободное
место на диске (`shutil.disk_usage`), число job'ов в статусе `running`. Плюс
per-job пиковая память (`jobs.peak_memory_mb`, backend/jobs/runner.py — сэмпл
RSS раз в 2с, пока job выполняется) — видна прямо в UI прогресса
design/analyze job'а, пока он бежит.

**Хранение и retention** (таблица `monitoring_snapshots`, миграция 0016):
сырые точки (`resolution='raw'`, раз в 60с) хранятся 24 часа; тот же
демон-поток раз в 5 минут понижает точки старше 24ч до часовых
min/avg/max-агрегатов (`resolution='hourly'`) и удаляет всё старше 90 дней.
Ручной снапшот прямо сейчас (не дожидаясь очередного тика) —
`POST /api/v1/admin/monitoring/snapshot-now` (тот же admin-only гейт, что и
остальной раздел Admin).

**Авто-завершение тестов по плановой дате** (item B3, `abkit/lifecycle.py`):
тот же демон-поток раз в 10 минут переводит в `completed` все эксперименты в
статусе `running`, у которых `experiments.planned_end_date` (миграция 0023,
аддитивная, nullable) уже прошла. Отдельного сервиса/треда/cron'а под это НЕ
заводится — это ровно тот же `MonitoringCollector`, что снимает метрики выше
(`run_auto_complete()`), и никаких новых env-переменных фича не добавляет:
интервал зашит константой `AUTO_COMPLETE_INTERVAL_SECONDS`, потому что
крутить его незачем — ленивая проверка при открытии страницы эксперимента
(`GET /api/v1/experiments/{name}`) и так закрывает случай "надо прямо
сейчас". Дата трактуется как «по этот день включительно»: тест с
`planned_end_date = 20-е` завершится первым проходом после полуночи 21-го по
UTC.

Диагностика: переход пишется в `audit_log` как `experiment.auto_completed`
БЕЗ `user_id`/`user_email` (это система, а не человек — в History-вкладке
такая строка рисуется как "system") и в лог backend как
`lifecycle.auto_completed` со ссылкой на имя эксперимента. Ничего, кроме
самого перехода и этой записи, не происходит — уведомлений нет. Чтобы
выключить авто-завершение для конкретного теста, достаточно очистить его
плановую дату (Edit Properties).

**Область ответственности**: эта панель — только про сам процесс ABSet
(что регулируется приложением) и специально не пытается заменить
host-level мониторинг (CPU хоста, сеть, метрики самого Postgres-контейнера
глубже размера БД, алертинг) — это домен инфраструктурной команды со своими
инструментами. Docker-сокет намеренно не монтируется в backend — панель не
видит и не может видеть другие контейнеры/хост целиком, только собственный
процесс, свою БД и свой каталог данных.

### 8.1 Память backend-процесса: ожидаемая ватерлиния

После тяжелого design/analyze job'а RSS backend-процесса заметно выше, чем
на старте (~250МБ сразу после рестарта), и НЕ возвращается к этому уровню
сам по себе — это ожидаемое поведение аллокатора памяти (glibc не отдает
освобожденную кучу процесса обратно ОС сама по себе), не утечка ссылок.
Диагностика (8 design job'ов подряд на синтетике 1.5М строк, с
стратификацией и CUPED) подтвердила: RSS растет один раз после первого
тяжелого job'а, дальше держится на плато ±5-10% от run к run, БЕЗ
монотонного роста — то есть ватерлиния, не рост. Код-трейс (jobs-реестр,
ThreadPoolExecutor, кэши, `AnalysisResults`) не нашел ни одного места,
которое держит DataFrame дольше времени жизни самого job'а — `job.result_ref`
(JSONB) хранит только скаляры/пути, futures нигде не накапливаются,
единственные кэши в коде (`introspection.py`, `monitoring.py`) хранят
строки/числа, не датафреймы.

Гигиена, снижающая саму ватерлинию (не устраняющая её — она встроена
в то, как работает аллокатор памяти Python/glibc с большими датафреймами),
`backend/jobs/runner.py::JobRunner._run` + `abkit/experiment.py::design()`:

- `del data` сразу после применения isolation (пока `candidates` уже
  посчитаны, а сырой `data` для остальной части `design()` больше не
  нужен) — снижает пиковое потребление, когда isolation реально
  отфильтровала часть строк (в остальных случаях `candidates is data`,
  `del` просто убирает одну из двух ссылок, безвредно).
- `gc.collect()` + `ctypes`-вызов `malloc_trim(0)` (Linux/glibc — деплой;
  безвредный no-op на других платформах) в `finally`-блоке ПОСЛЕ КАЖДОГО
  job'а — просит glibc реально отдать освобожденную кучу ОС, а не просто
  подождать, пока Python освободит ссылки (это и так происходит само по
  завершении job'а).

Эффект измерен на той же синтетике (8 design job'ов подряд, settled RSS):
до фикса — 1.9-2.13 ГБ (плато); после фикса — 0.70-0.86 ГБ (тоже плато, но
почти втрое ниже). Абсолютные цифры зависят от размера реальных датасетов
и числа метрик/страт — ориентир для админа: если RSS растет МОНОТОННО от
job'а к job'у без выхода на плато (а не просто держится на новом,
повышенном уровне после первого тяжелого job'а) — это признак реальной
проблемы, стоит завести issue с логами `docker compose logs backend` и
графиком со страницы Monitoring за этот период.

**Страховка**: `mem_limit` у сервиса `backend` в `docker-compose.yml`
(`ABKIT_BACKEND_MEM_LIMIT`, default `4g`) — не признак известной утечки, а
подушка безопасности выше наблюдавшегося плато с запасом, чтобы аномальный
рост (реальная утечка, аномально большой датасет) убивал контейнер вместо
того, чтобы забирать память соседних сервисов/хоста целиком. Поднять через
`.env`, если реальные датасеты заметно крупнее тестовых (1.5М строк).

**Что происходит с job'ой, которую убило посреди выполнения** (OOM или
любой другой SIGKILL контейнера): проверено намеренным экспериментом —
запущена реальная тяжелая design-job на синтетике 1.5М строк, контейнер
`backend` убит SIGKILL прямо посреди выполнения (`docker kill -s SIGKILL`,
job была в статусе `running`), затем поднят заново. Job, застрявшая в
`pending`/`running`, при СЛЕДУЮЩЕМ старте backend'а автоматически
помечается `failed` с понятным сообщением ("The backend restarted while
this job was running — please run it again") —
`backend/jobs/runner.py::JobRunner.mark_unfinished_jobs_failed_on_startup`,
вызывается безусловно в `backend/main.py`'s lifespan при каждом старте
процесса. UI показывает конкретную причину, а не вечный спиннер —
починка подтверждена, дополнительных правок не потребовалось (уже было
реализовано на более ранней стадии, FRONTEND.md §4).

**Проверка `restart: unless-stopped` для НАСТОЯЩЕГО OOM**: временно занизил
лимит (`docker update --memory=250m` на живом `backend`) и запустил
операцию, реально упирающуюся в потолок (генерация датафрейма на 1.5М
строк) — ядро убило процесс по OOM (`docker events` показал
`oom → die (exitCode=137) → start`), Docker **автоматически поднял
контейнер обратно** в течение секунды: `docker inspect --format
'{{.RestartCount}}'` стал 1, `{{.State.OOMKilled}}` — `true` (именно этот
флаг отличает настоящий cgroup-OOM от произвольного SIGKILL — используется
в таблице «Типовые проблемы» выше), контейнер снова healthy без ручного
вмешательства. **Для реального сценария, который производит `mem_limit`
(job выедает память сверх лимита), `restart: unless-stopped` отрабатывает
штатно** — менять конфигурацию не потребовалось.

Отдельно проверил другой (не-OOM) вид убийства контейнера — вручную
`docker kill -s SIGKILL` (как имитация внешнего сбоя, не связанного с
памятью) на том же `backend`, а также изолированно на обычном alpine
контейнере с `--restart unless-stopped` вне docker-compose: в ЭТОМ случае
на **Docker Desktop для Windows** автоматический рестарт **не сработал** —
контейнер оставался в `Exited (137)` минимум полминуты,
`RestartCount` = 0. Не ошибка конфигурации (`unless-stopped` — корректная
директива; `always` ведет себя идентично для этого сценария) и не баг
самого ABSet — похоже на особенность конкретно Docker Desktop for Windows
в том, как `docker kill` (внешняя команда) размечает причину смерти
контейнера, в отличие от того, как cgroup помечает настоящий OOM. На
реальном продакшн-хосте (Linux, нативный Docker Engine — целевая
платформа деплоя по этому документу) это может отличаться в лучшую
сторону — но раз поведение зависит от ПРИЧИНЫ смерти контейнера, а не
только от самой директивы `restart`, стоит не полагаться на неё вслепую:
если backend не поднялся сам после инцидента — `docker compose up -d`
руками поднимает его без потери данных (job, попавшая под рестарт, к
этому моменту уже корректно помечена `failed` процедурой выше).

## 9. Гигиена dev/e2e-окружения

Актуально при разработке/отладке ABSet самого по себе (не для конечных
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

## Приложение А. Развертывание из исходников (контуры без Harbor)

**Запасной путь.** Основной способ для корпоративного контура — §2-4 выше
(готовые образы из Harbor). Этот раздел нужен там, где внутреннего реестра
нет вовсе: демо-стенд, машина разработчика, разовая установка «на посмотреть».
Требует интернет на самой машине (базовые образы, pip, npm) — на
CLK2-ABSET-01 он неприменим.

Отличие ровно одно: образы собираются на месте из базового
`docker-compose.yml`, без прод-оверлея и без `ABKIT_REGISTRY`/`ABKIT_VERSION`.
Всё остальное (бэкап §5, пользователи §6, диагностика §7, мониторинг §8)
работает одинаково.

### А.1 Первичное развертывание

Требования: Docker Engine 24+, Docker Compose v2 (`docker compose version`),
открытый порт на хосте (дефолт 8080).

```bash
git clone https://github.com/<org>/abset.git && cd abset
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

### А.2 Обновление версии

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

# 4. Smoke-чек (см. §2) — все сервисы healthy, login и /api/v1/version отвечают
#    С ПРАВИЛЬНЫМ значением version (см. ниже — до item 8 итогового пакета
#    это поле молча показывало 2.0.0 независимо от реально задеплоенного
#    тега, так что раньше этот curl проверял только "отвечает", не "совпадает").
docker compose ps
curl -sf http://localhost:${ABKIT_PORT:-8080}/login >/dev/null && echo OK
curl -sf http://localhost:${ABKIT_PORT:-8080}/api/v1/version  # должно совпасть с тегом из шага 2
```

Все четыре шага делает одной командой `scripts/update.sh <тег>`.
**`scripts/update.sh` относится ТОЛЬКО к этому запасному пути** — он
делает `git checkout` + `docker compose up -d --build`, то есть собирает
образы на месте. Для корпоративного контура обновление выглядит иначе
(смена `ABKIT_VERSION` + `pull`), см. §3.

**Откуда берется `version` в ответе** (item 8 + 8-Б, CLAUDE.md «Правило:
релизный процесс», пункт г): не хардкод в коде — `git describe --tags
--always --long` против `.git`, посчитанный ОДИН РАЗ внутри Docker-сборки
(стадия `version`), результат — файл `/app/VERSION_DESCRIBE`, который
`abkit/__init__.py` парсит при импорте. На самом теге → `vX.Y.Z`
(например `v2.5.0`); собранный локально образ ПОСЛЕ тега, без нового тега
(`docker compose up -d --build` из §2 выше, обычный ход разработки) —
`vX.Y.Z+N (<short sha>)`, где N — число коммитов поверх последнего тега;
тегов в истории вообще нет — `dev (<short sha>)`. Всё вычисляется во время
сборки, без ручных шагов и без зависимости от build-arg'ов CI.

Даунтайм — время пересборки образов + перезапуска контейнеров (Postgres не
перезапускается, если его образ/конфиг не менялись). Если вместо локальной
сборки используются готовые образы из `ghcr.io/<org>/abset-backend:<version>` /
`abset-frontend:<version>` (публикуются CI на каждый тег `v*` —
`.github/workflows/ci.yml`, джоба `build-and-push`), шаг 3 — `docker compose pull && docker compose up -d`
вместо `--build` (потребует переключить `image:` вместо `build:` в
`docker-compose.yml` или отдельный `docker-compose.prod.yml`).

**Перед крупными обновлениями** (смена мажорной версии, миграции схемы БД,
правки `docker-compose.yml`/`entrypoint-*.sh`) — дополнительно прогнать
`bash docker/test_persistence.sh` (см. docker/README.md) до переключения
прод-трафика.

### А.3 Откат на предыдущий тег

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
