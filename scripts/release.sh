#!/usr/bin/env bash
set -euo pipefail

# Сборка и публикация релиза ABSet в корпоративный Harbor. Запускается НА
# РАБОЧЕЙ МАШИНЕ разработчика (у нее есть и интернет, и доступ к Harbor), а не
# на прод-VM: у CLK2-ABSET-01 нет egress'а в интернет, собрать там образы
# невозможно в принципе — она только тянет готовые (docs/OPERATIONS.md §2-4).
#
# Использование:
#   bash scripts/release.sh v2.6.0
#
# Переменные окружения:
#   ABKIT_REGISTRY   куда пушить, напр. hb.intra.click.uz/abset (обязательна)
#   ABKIT_RELEASE_SKIP_GATE=1   пропустить быстрый гейт качества (только для
#                               отладки самого скрипта; в норме НЕ использовать)
#   ABKIT_RELEASE_SKIP_GIT=1    не трогать git-теги и не пушить (используется
#                               интеграционным тестом против локального
#                               registry:2 — ему нужны только образы)
#
# Что делает, по шагам:
#   1. проверяет формат версии и чистоту дерева на main
#   2. быстрый гейт качества (НЕ полный прогон — тот является гейтом приемки
#      пакета, здесь он занял бы 15+ минут на каждый релиз)
#   3. проверяет, что в Harbor вообще есть логин
#   4. собирает три образа
#   5. тегирует :vX.Y.Z и :latest, пушит оба
#   6. ставит git-тег и пушит его в оба remote (origin + gitlab, §3 ТЗ)
#   7. печатает сводку с digest'ами — то, что идет в заявку на деплой

usage() {
    echo "Usage: $0 vX.Y.Z" >&2
    echo "  e.g. $0 v2.6.0" >&2
    echo >&2
    echo "Requires ABKIT_REGISTRY (e.g. hb.intra.click.uz/abset) and a prior 'docker login'." >&2
}

# Имена образов внутри проекта Harbor. Совпадают с docker-compose.prod.yml —
# при добавлении сервиса править надо оба места, поэтому список один и явный.
IMAGES=(abset-backend abset-frontend abset-nginx)

dockerfile_for() {
    case "$1" in
        abset-backend)  echo "docker/Dockerfile" ;;
        abset-frontend) echo "frontend/Dockerfile" ;;
        abset-nginx)    echo "docker/nginx.Dockerfile" ;;
        *) return 1 ;;
    esac
}

# --- Валидация версии --------------------------------------------------------
# Вынесено в функцию, чтобы это можно было проверить юнит-тестом, не запуская
# сборку (tests/test_release_script.py). Строгий semver с обязательным "v":
# именно этот формат ждут scripts/update.sh и правило релизов из CLAUDE.md, а
# опечатка вида "2.6.0" или "v2.6" всплыла бы только на VM при pull'е.
validate_version() {
    local version="$1"
    if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
        echo "ERROR: '$version' is not a valid release version — expected vMAJOR.MINOR.PATCH (e.g. v2.6.0)." >&2
        return 1
    fi
    return 0
}

# --- Чистота рабочего дерева -------------------------------------------------
# Грязное дерево — самый дорогой из возможных провалов: собранный образ не
# соответствовал бы ни одному коммиту, и по тегу его было бы не воспроизвести.
# Плюс версия внутри образа берется из `git describe` (docker/Dockerfile),
# то есть на грязном дереве она просто соврет.
require_clean_tree() {
    if [ -n "$(git status --porcelain)" ]; then
        echo "ERROR: working tree is dirty — commit or stash before releasing." >&2
        echo "       An image built from a dirty tree matches no commit and cannot be reproduced from its tag." >&2
        git status --short >&2
        return 1
    fi
    return 0
}

require_main_branch() {
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [ "$branch" != "main" ]; then
        echo "ERROR: releases are cut from 'main', current branch is '$branch'." >&2
        return 1
    fi
    return 0
}

# --- Логин в реестр ----------------------------------------------------------
# Проверяем ДО сборки: узнать про отсутствующий логин после 10 минут сборки —
# худший момент из возможных. Пуш в несуществующий репозиторий и так упадет,
# но с невнятным "denied: requested access to the resource is denied".
require_registry_login() {
    local registry_host="${1%%/*}"
    if ! docker system info 2>/dev/null | grep -q .; then
        echo "ERROR: docker daemon is not reachable." >&2
        return 1
    fi
    # Локальный registry:2 (интеграционный тест) работает без аутентификации —
    # для него проверка логина неприменима и была бы ложным отказом.
    case "$registry_host" in
        localhost|localhost:*|127.0.0.1|127.0.0.1:*) return 0 ;;
    esac
    if ! grep -q "$registry_host" "${DOCKER_CONFIG:-$HOME/.docker}/config.json" 2>/dev/null; then
        echo "ERROR: no stored credentials for '$registry_host'." >&2
        echo "       Run: docker login $registry_host" >&2
        return 1
    fi
    return 0
}

# --- Быстрый гейт качества ---------------------------------------------------
# НЕ полный прогон: полный (pytest + весь e2e) — это гейт ПРИЕМКИ ПАКЕТА, он
# идет до коммита и занимает ~15 минут. Здесь задача другая: поймать очевидно
# сломанный релиз за минуту, до десятиминутной сборки трех образов.
run_quality_gate() {
    local python_bin=".venv/Scripts/python.exe"
    [ -x "$python_bin" ] || python_bin=".venv/bin/python"
    [ -x "$python_bin" ] || python_bin="python"

    echo "--- pyflakes"
    "$python_bin" -m pyflakes abkit backend tests migrations cli.py cli_admin.py conftest.py

    echo "--- frontend typecheck + lint"
    ( cd frontend && npm run typecheck && npm run lint )

    echo "--- backend unit subset (no containers)"
    # Подмножество, которое не требует Postgres/testcontainers: конфиг, чистая
    # статистика, OIDC-протокол, отчеты. Полный прогон против БД — гейт
    # приемки пакета, не релиза.
    "$python_bin" -m pytest -q --no-header \
        tests/test_oidc_core.py \
        tests/test_design_reporting_core.py \
        tests/test_config.py \
        tests/test_release_script.py
}

main() {
    if [ $# -ne 1 ]; then
        usage
        exit 1
    fi
    case "$1" in
        -h|--help) usage; exit 0 ;;
    esac

    local version="$1"
    validate_version "$version"

    local script_dir project_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    project_dir="$(cd "$script_dir/.." && pwd)"
    cd "$project_dir"

    local registry="${ABKIT_REGISTRY:-}"
    if [ -z "$registry" ]; then
        echo "ERROR: ABKIT_REGISTRY is not set (e.g. hb.intra.click.uz/abset)." >&2
        exit 1
    fi
    registry="${registry%/}"

    local skip_git="${ABKIT_RELEASE_SKIP_GIT:-0}"

    echo "==> 1/7 Preflight"
    if [ "$skip_git" != "1" ]; then
        require_main_branch
        require_clean_tree
        if git rev-parse -q --verify "refs/tags/$version" >/dev/null; then
            echo "ERROR: tag '$version' already exists — pick a new version or delete the tag." >&2
            exit 1
        fi
    else
        echo "    (ABKIT_RELEASE_SKIP_GIT=1 — git checks and tagging skipped)"
    fi
    require_registry_login "$registry"
    echo "    ok: registry=$registry version=$version"

    echo "==> 2/7 Quality gate"
    if [ "${ABKIT_RELEASE_SKIP_GATE:-0}" = "1" ]; then
        echo "    (ABKIT_RELEASE_SKIP_GATE=1 — skipped)"
    else
        run_quality_gate
    fi

    echo "==> 3/7 Build images"
    local name dockerfile
    for name in "${IMAGES[@]}"; do
        dockerfile="$(dockerfile_for "$name")"
        echo "--- $name ($dockerfile)"
        # ABKIT_VERSION build-arg — только OCI-лейбл образа; версия, которую
        # видит пользователь, считается из git describe внутри сборки
        # (docker/Dockerfile, стадия version).
        docker build \
            -f "$dockerfile" \
            --build-arg "ABKIT_VERSION=$version" \
            -t "$registry/$name:$version" \
            -t "$registry/$name:latest" \
            .
    done

    echo "==> 4/7 Push images"
    for name in "${IMAGES[@]}"; do
        docker push "$registry/$name:$version"
        # :latest — удобство для «просто дай мне свежее» (диагностика, dev-стенд).
        # Прод по :latest НЕ разворачивается никогда: docker-compose.prod.yml
        # требует явный ABKIT_VERSION, иначе непонятно, что именно запущено.
        docker push "$registry/$name:latest"
    done

    echo "==> 5/7 Verify prod compose resolves these images"
    ABKIT_REGISTRY="$registry" ABKIT_VERSION="$version" \
    POSTGRES_PASSWORD=placeholder ABKIT_SECRET_KEY=placeholder \
        docker compose -f docker-compose.yml -f docker-compose.prod.yml config >/dev/null
    echo "    ok"

    echo "==> 6/7 Git tag"
    if [ "$skip_git" = "1" ]; then
        echo "    (skipped)"
    else
        git tag -a "$version" -m "Release $version"
        push_to_remotes "$version"
    fi

    echo "==> 7/7 Summary"
    print_summary "$registry" "$version"
}

# Пуш в ОБА remote: origin (GitHub, разработка) и gitlab (внутренний, источник
# правды для деплоя — с него клонирует VM). Отсутствие gitlab-remote — не
# ошибка: URL внутреннего GitLab на момент написания еще не известен, и до его
# появления релизы должны собираться и публиковаться как обычно.
push_to_remotes() {
    local version="$1" remote
    for remote in origin gitlab; do
        if ! git remote get-url "$remote" >/dev/null 2>&1; then
            if [ "$remote" = "gitlab" ]; then
                echo "    WARNING: remote 'gitlab' is not configured — the deployment VM clones from GitLab," >&2
                echo "             so this release is NOT yet reachable for deployment." >&2
                echo "             Configure it with: bash scripts/setup_gitlab_remote.sh <url>" >&2
            else
                echo "    WARNING: remote '$remote' is not configured — skipped." >&2
            fi
            continue
        fi
        echo "--- push main + $version -> $remote"
        git push "$remote" main
        git push "$remote" "$version"
    done
}

print_summary() {
    local registry="$1" version="$2" name digest
    echo
    echo "============================================================"
    echo " ABSet release $version"
    echo "============================================================"
    echo " Registry: $registry"
    echo
    printf " %-22s %s\n" "IMAGE" "DIGEST"
    for name in "${IMAGES[@]}"; do
        digest="$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}(not pushed){{end}}' \
            "$registry/$name:$version" 2>/dev/null || echo '(unknown)')"
        printf " %-22s %s\n" "$name:$version" "${digest##*@}"
    done
    echo
    echo " Deploy on CLK2-ABSET-01:"
    echo "   cd /opt/abset && git pull"
    echo "   sed -i 's/^ABKIT_VERSION=.*/ABKIT_VERSION=$version/' .env"
    echo "   docker compose -f docker-compose.yml -f docker-compose.prod.yml pull"
    echo "   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
    echo " Full procedure: docs/OPERATIONS.md §3"
    echo "============================================================"
}

# Позволяет sourcing'у подтянуть функции без запуска main — так их проверяет
# tests/test_release_script.py, не собирая ни одного образа.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
