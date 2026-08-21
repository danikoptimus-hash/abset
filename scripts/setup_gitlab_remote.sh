#!/usr/bin/env bash
set -euo pipefail

# Настраивает git-remote "gitlab" — внутренний GitLab, который является
# ИСТОЧНИКОМ ПРАВДЫ ДЛЯ ДЕПЛОЯ: прод-VM (CLK2-ABSET-01) клонирует и тянет
# только оттуда, к GitHub у нее доступа нет вовсе (docs/OPERATIONS.md §2).
#
# Отдельный скрипт, а не строчка в README: URL внутреннего GitLab на момент
# написания пакета еще не выдан, и подставить его в репозиторий заранее нельзя.
# Скрипт делает шаг воспроизводимым и идемпотентным — его же запустит любой,
# кто заведет себе рабочую копию.
#
# Использование:
#   bash scripts/setup_gitlab_remote.sh https://gitlab.intra.click.uz/analytics/abset.git
#   bash scripts/setup_gitlab_remote.sh git@gitlab.intra.click.uz:analytics/abset.git
#
# После этого scripts/release.sh пушит main и теги в origin И в gitlab.

usage() {
    echo "Usage: $0 <gitlab-remote-url>" >&2
    echo "  e.g. $0 https://gitlab.intra.click.uz/analytics/abset.git" >&2
}

if [ $# -ne 1 ]; then
    usage
    exit 1
fi

case "$1" in
    -h|--help) usage; exit 0 ;;
esac

URL="$1"

# Минимальная проверка формы: опечатка в URL обнаружилась бы иначе только при
# первом push'е релиза — то есть в самый неудачный момент.
case "$URL" in
    https://*|http://*|git@*:*|ssh://*) ;;
    *)
        echo "ERROR: '$URL' does not look like a git remote URL (expected https://, ssh:// or git@host:path)." >&2
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

if git remote get-url gitlab >/dev/null 2>&1; then
    CURRENT="$(git remote get-url gitlab)"
    if [ "$CURRENT" = "$URL" ]; then
        echo "remote 'gitlab' already points at $URL — nothing to do."
        exit 0
    fi
    echo "==> updating remote 'gitlab': $CURRENT -> $URL"
    git remote set-url gitlab "$URL"
else
    echo "==> adding remote 'gitlab': $URL"
    git remote add gitlab "$URL"
fi

echo
git remote -v | grep gitlab
echo
# Не падаем, если GitLab недоступен прямо сейчас (VPN не поднят, доступ еще не
# выдан): remote настроен корректно в любом случае, а проверка связи — отдельный
# вопрос, из-за которого не должно ломаться конфигурирование.
if git ls-remote --exit-code gitlab >/dev/null 2>&1; then
    echo "OK: remote is reachable."
else
    echo "NOTE: remote is configured but not reachable right now (VPN? access not granted yet?)."
    echo "      Verify later with: git ls-remote gitlab"
fi
