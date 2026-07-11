#!/usr/bin/env bash
set -euo pipefail

# Обновление ABSet на сервере: бэкап -> checkout тега -> build -> up ->
# smoke-чек -> вывод статуса. См. docs/OPERATIONS.md §3.
#
# Только теги вида v* деплоятся на серверы (CLAUDE.md, правило релизов) —
# скрипт отказывается запускаться на чем-то другом.
#
# Использование: scripts/update.sh v2.1.0

if [ $# -ne 1 ]; then
    echo "Usage: $0 <tag>   (e.g. $0 v2.1.0)" >&2
    exit 1
fi

TAG="$1"

case "$TAG" in
    v*) ;;
    *)
        echo "ERROR: refusing to deploy '$TAG' — only tags matching 'v*' are deployed to servers (CLAUDE.md release rules)." >&2
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "==> 1/4 Backup"
bash "$SCRIPT_DIR/backup.sh"

echo "==> 2/4 git fetch --tags && git checkout $TAG"
git fetch --tags
git checkout "$TAG"

echo "==> 3/4 docker compose up -d --build"
docker compose up -d --build

echo "==> 4/4 Smoke check"
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a && source .env && set +a
fi
PORT="${ABKIT_PORT:-8080}"
OK=0
for _ in $(seq 1 30); do
    if curl -sf "http://localhost:${PORT}/api/v1/version" >/dev/null 2>&1; then
        OK=1
        break
    fi
    sleep 2
done
if [ "$OK" -ne 1 ]; then
    echo "ERROR: backend did not respond after update — check 'docker compose logs backend'." >&2
    exit 1
fi
echo "    backend responds on :${PORT}"

echo
echo "==> Status:"
docker compose ps

echo
echo "==> Updated to $TAG. Verify manually: http://localhost:${PORT}/login"
