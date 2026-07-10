#!/usr/bin/env bash
set -euo pipefail

# Бэкап ABKit: pg_dump (users/experiments/assignments/audit_log — структурные
# данные) + tar volume abkit_data (parquet-датасеты, HTML-отчеты) в
# BACKUP_DIR/<timestamp>/, ротация — хранит последние KEEP_LAST наборов
# (default 14), старые удаляет. Требует запущенный docker compose стек
# (сервис postgres) — см. docs/OPERATIONS.md §5.
#
# Этот скрипт НЕ выгружает бэкапы во внешнее хранилище (S3/rclone/etc.) — на
# проде BACKUP_DIR стоит держать на отдельном диске или синкать отдельно;
# рекомендация по cron — docs/OPERATIONS.md §5.
#
# Использование: scripts/backup.sh
# Переменные окружения: BACKUP_DIR (default: <repo>/backups), KEEP_LAST (default: 14)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP_LAST="${KEEP_LAST:-14}"

if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a && source .env && set +a
fi

POSTGRES_USER="${POSTGRES_USER:-abkit}"
POSTGRES_DB="${POSTGRES_DB:-abkit}"

if ! docker compose ps postgres 2>/dev/null | grep -q postgres; then
    echo "ERROR: docker compose service 'postgres' is not running — start the stack first (docker compose up -d)." >&2
    exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_DIR/$TIMESTAMP"
mkdir -p "$DEST"

echo "==> Backing up Postgres ($POSTGRES_DB) to $DEST/backup.sql"
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$DEST/backup.sql"

echo "==> Backing up abkit_data volume to $DEST/data.tgz"
VOLUME_NAME="$(docker volume ls --filter label=com.docker.compose.volume=abkit_data --format '{{.Name}}' | head -n1)"
if [ -z "$VOLUME_NAME" ]; then
    echo "ERROR: could not find the abkit_data docker volume — is the stack running from this directory?" >&2
    rm -rf "$DEST"
    exit 1
fi
# Reuses the postgres:16-alpine image (already required/pulled for the
# postgres service itself, docker-compose.yml) as the tar helper instead of
# pulling a separate alpine image — one less thing that can fail to pull at
# backup time, which is exactly when registry hiccups hurt most.
docker run --rm -v "${VOLUME_NAME}:/data" -v "$DEST":/backup postgres:16-alpine \
    tar -czf /backup/data.tgz -C /data .

echo "==> Backup complete: $DEST"

echo "==> Rotating: keeping last $KEEP_LAST backups in $BACKUP_DIR"
mapfile -t ALL_BACKUPS < <(find "$BACKUP_DIR" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*_[0-9]*' | sort)
COUNT=${#ALL_BACKUPS[@]}
if [ "$COUNT" -gt "$KEEP_LAST" ]; then
    TO_REMOVE=$((COUNT - KEEP_LAST))
    for ((i = 0; i < TO_REMOVE; i++)); do
        echo "    removing old backup: ${ALL_BACKUPS[$i]}"
        rm -rf "${ALL_BACKUPS[$i]}"
    done
fi

echo "==> Done."
