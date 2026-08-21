#!/usr/bin/env bash
set -euo pipefail

# Интеграционная проверка релизного конвейера БЕЗ настоящего Harbor: поднимает
# локальный registry:2, прогоняет через него scripts/release.sh с выбрасываемой
# версией и убеждается, что образы реально опубликованы, тянутся обратно и что
# прод-оверлей compose ссылается именно на них.
#
# Зачем: адрес Harbor (hb.intra.click.uz) и проект "abset" появятся позже, а
# конвейер должен быть проверяем уже сейчас. Локальный registry:2 говорит на том
# же Registry HTTP API V2, что и Harbor, поэтому push/pull/манифесты — ровно тот
# же путь кода docker'а; отличается только аутентификация (у Harbor она есть,
# у локального нет — release.sh это учитывает, см. require_registry_login).
#
# НЕ в e2e-наборе: там браузерные сценарии против поднятого приложения, а тут
# инфраструктура релиза (docker build/push), другой контур и другая длительность.
# Запускается руками перед первым настоящим релизом и после правок release.sh:
#
#   bash scripts/test_release_local_registry.sh
#
# Переменные: REGISTRY_PORT (default 5555) — если 5555 занят.

REGISTRY_PORT="${REGISTRY_PORT:-5555}"
REGISTRY="localhost:${REGISTRY_PORT}/abset"
CONTAINER="abset_release_test_registry"
# Заведомо невозможная версия: не должна пересечься с реальными тегами ни в
# git, ни в Harbor.
TEST_VERSION="v0.0.0-localtest"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

IMAGES=(abset-backend abset-frontend abset-nginx)

FAILURES=0
check() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  OK   $label"
    else
        echo "  FAIL $label"
        FAILURES=$((FAILURES + 1))
    fi
}

cleanup() {
    echo "==> Cleaning up"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    local name
    for name in "${IMAGES[@]}"; do
        docker rmi -f "$REGISTRY/$name:$TEST_VERSION" "$REGISTRY/$name:latest" >/dev/null 2>&1 || true
    done
}
trap cleanup EXIT

echo "==> Starting throwaway registry on :${REGISTRY_PORT}"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" -p "${REGISTRY_PORT}:5000" registry:2 >/dev/null

echo -n "    waiting for registry"
for _ in $(seq 1 30); do
    if curl -sf "http://localhost:${REGISTRY_PORT}/v2/" >/dev/null 2>&1; then
        echo " — up"
        break
    fi
    echo -n "."
    sleep 1
done
curl -sf "http://localhost:${REGISTRY_PORT}/v2/" >/dev/null || {
    echo >&2
    echo "ERROR: local registry did not come up on :${REGISTRY_PORT}" >&2
    exit 1
}

echo "==> Running release.sh against the local registry"
# SKIP_GIT: тест не должен ставить git-теги и уж тем более что-то пушить в
# origin/gitlab. SKIP_GATE: гейт качества проверяется своими тестами; здесь
# проверяется КОНВЕЙЕР (build/tag/push/pull), и десять минут гейта на каждый
# прогон этой проверки ничего бы не добавили.
ABKIT_REGISTRY="$REGISTRY" \
ABKIT_RELEASE_SKIP_GIT=1 \
ABKIT_RELEASE_SKIP_GATE=1 \
    bash scripts/release.sh "$TEST_VERSION"

echo
echo "==> Assertions"

# 1. Оба тега каждого образа реально лежат в реестре (спрашиваем сам реестр,
#    а не локальный docker-кэш: иначе тест прошел бы и без единого push'а).
for name in "${IMAGES[@]}"; do
    tags_url="http://localhost:${REGISTRY_PORT}/v2/abset/${name}/tags/list"
    check "$name: $TEST_VERSION present in registry" \
        bash -c "curl -sf '$tags_url' | grep -q '\"$TEST_VERSION\"'"
    check "$name: latest present in registry" \
        bash -c "curl -sf '$tags_url' | grep -q '\"latest\"'"
done

# 2. Образы тянутся обратно — то есть манифест и слои целы, а не только
#    «push вернул 0». Сначала убираем локальные копии, чтобы pull был настоящим.
for name in "${IMAGES[@]}"; do
    docker rmi -f "$REGISTRY/$name:$TEST_VERSION" >/dev/null 2>&1 || true
    check "$name: pullable from registry" docker pull "$REGISTRY/$name:$TEST_VERSION"
done

# 3. Прод-оверлей compose подставляет ИМЕННО эти ссылки.
resolved="$(ABKIT_REGISTRY="$REGISTRY" ABKIT_VERSION="$TEST_VERSION" \
    POSTGRES_PASSWORD=placeholder ABKIT_SECRET_KEY=placeholder \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml config 2>/dev/null)"

for name in "${IMAGES[@]}"; do
    check "prod compose references $name:$TEST_VERSION" \
        bash -c "printf '%s' \"\$1\" | grep -q 'image: $REGISTRY/$name:$TEST_VERSION'" _ "$resolved"
done

# 4. В прод-конфиге не осталось ни одной секции build: — иначе VM без интернета
#    попыталась бы собирать вместо честного отказа на недоступном реестре.
check "prod compose has no build: sections" \
    bash -c "! printf '%s' \"\$1\" | grep -qE '^[[:space:]]+build:'" _ "$resolved"

# 5. Dev-режим при этом НЕ сломан: без оверлея сборка из контекстов на месте.
dev_resolved="$(POSTGRES_PASSWORD=placeholder ABKIT_SECRET_KEY=placeholder \
    docker compose -f docker-compose.yml config 2>/dev/null)"
check "dev compose still builds from context" \
    bash -c "printf '%s' \"\$1\" | grep -qE '^[[:space:]]+build:'" _ "$dev_resolved"

# 6. Самая содержательная проверка: прод-оверлей РЕАЛЬНО ПОДНИМАЕТСЯ из
#    вытянутых образов. Пункты выше доказывают, что образы опубликованы и что
#    ссылки разрешаются; этот — что образ не пустой, конфиг nginx запекся
#    внутрь, миграции проходят и приложение отвечает. Ближайшая доступная
#    репетиция первого развертывания на CLK2-ABSET-01.
echo
echo "==> Smoke: bringing the prod overlay up from the local registry"
SMOKE_PROJECT="abset_release_smoke"
SMOKE_PORT="${SMOKE_PORT:-8093}"
SMOKE_ENV="$(mktemp)"
cat > "$SMOKE_ENV" <<EOF
ABKIT_REGISTRY=$REGISTRY
ABKIT_VERSION=$TEST_VERSION
ABKIT_POSTGRES_IMAGE=postgres:16-alpine
ABKIT_SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
ABKIT_PORT=$SMOKE_PORT
ABKIT_ADMIN_EMAIL=admin@release-test.local
ABKIT_ADMIN_PASSWORD=releasetest123
EOF

smoke_down() {
    docker compose --env-file "$SMOKE_ENV" \
        -f docker-compose.yml -f docker-compose.prod.yml \
        -p "$SMOKE_PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -f "$SMOKE_ENV"
}

if docker compose --env-file "$SMOKE_ENV" \
        -f docker-compose.yml -f docker-compose.prod.yml \
        -p "$SMOKE_PROJECT" up -d --wait >/dev/null 2>&1; then
    version_json="$(curl -sf "http://localhost:${SMOKE_PORT}/api/v1/version" || true)"
    login_code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${SMOKE_PORT}/login" || true)"
    check "prod stack: /api/v1/version responds" bash -c "[ -n \"\$1\" ]" _ "$version_json"
    check "prod stack: login page served through the baked nginx config" \
        bash -c "[ \"\$1\" = 200 ]" _ "$login_code"
    echo "       version endpoint said: ${version_json:-<no response>}"
else
    echo "  FAIL prod stack did not become healthy"
    docker compose --env-file "$SMOKE_ENV" \
        -f docker-compose.yml -f docker-compose.prod.yml \
        -p "$SMOKE_PROJECT" logs --tail 30 backend 2>&1 | tail -30
    FAILURES=$((FAILURES + 1))
fi
smoke_down

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "==> Release pipeline OK ($REGISTRY, $TEST_VERSION)"
else
    echo "==> $FAILURES assertion(s) FAILED" >&2
    exit 1
fi
