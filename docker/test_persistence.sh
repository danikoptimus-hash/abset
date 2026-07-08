#!/usr/bin/env bash
# docker/test_persistence.sh — чек-лист DOCKER.md: данные (пользователи,
# эксперименты, assignments, отчеты) должны переживать restart/down-up и
# пересборку образа, пока не используется `docker compose down -v`.
#
# НЕ входит в обязательный CI-прогон (медленный, требует реального docker
# compose стека) — запускается вручную:
#   - перед развертыванием на новом сервере,
#   - перед крупными обновлениями (смена версии образа, миграции схемы),
#   - либо через опциональную CI-джобу "persistence" (workflow_dispatch,
#     .github/workflows/ci.yml).
#
# Требует: docker compose (с уже настроенным .env — см. docker/README.md),
# запускается из любой директории, сам находит корень репозитория.
#
# Выход 0 — все проверки прошли, 1 — хотя бы одна не прошла (сообщения см. в
# выводе с префиксом [test_persistence] FAIL:).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

TEST_EMAIL="persistence-test-$$@abkit.local"
TEST_EXP_NAME="persistence_check_$$"
FAILED=0

log() { echo "[test_persistence] $*"; }
fail() { echo "[test_persistence] FAIL: $*" >&2; FAILED=1; }

wait_healthy() {
    local service="$1" tries=0 cid status
    while [ "$tries" -lt 60 ]; do
        cid=$(docker compose ps -q "$service" 2>/dev/null)
        if [ -n "$cid" ]; then
            status=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null)
            if [ "$status" = "healthy" ]; then
                return 0
            fi
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
}

# Возвращает "user_exists|exp_exists|n_assignments" одной строкой — используется
# и сразу после создания тестовых данных, и после каждого рестарта, чтобы
# сравнить "было" со "стало".
check_state() {
    docker compose exec -T \
        -e ABKIT_TEST_EMAIL="$TEST_EMAIL" -e ABKIT_TEST_EXP="$TEST_EXP_NAME" \
        backend python <<'PYEOF'
import os
from abkit.db.repositories import AssignmentRepo, ExperimentRepo, UserRepo

user = UserRepo().get_by_email(os.environ["ABKIT_TEST_EMAIL"])
exp = ExperimentRepo().get_by_name(os.environ["ABKIT_TEST_EXP"])
n_assignments = len(AssignmentRepo().load(exp.id)) if exp is not None else 0
print(f"{user is not None}|{exp is not None}|{n_assignments}")
PYEOF
}

check_report_exists() {
    docker compose exec -T -e ABKIT_TEST_EXP="$TEST_EXP_NAME" backend python <<'PYEOF'
import os
from abkit.db.store import get_data_dir

path = get_data_dir() / os.environ["ABKIT_TEST_EXP"] / "design_report.html"
print(path.exists())
PYEOF
}

cleanup() {
    log "Очистка тестовых сущностей (деактивация тестового пользователя)..."
    docker compose exec -T -e ABKIT_TEST_EMAIL="$TEST_EMAIL" backend python <<'PYEOF' >/dev/null 2>&1
import os
from abkit.db.repositories import UserRepo

repo = UserRepo()
user = repo.get_by_email(os.environ["ABKIT_TEST_EMAIL"])
if user is not None:
    repo.set_active(user.id, False)
PYEOF
    # Эксперимент намеренно не удаляется — публичного delete-experiment в
    # ExperimentRepo нет (удаление экспериментов — Admin-only действие через
    # jobs.run_delete_experiment, требует полноценного CurrentUser, что тут
    # избыточно); тестовый эксперимент маленький (200 юзеров) и не мешает.
}
trap cleanup EXIT

log "(а) docker compose up -d, ждем healthy..."
docker compose up -d || { fail "docker compose up -d не выполнился"; exit 1; }
wait_healthy postgres || { fail "postgres не стал healthy"; exit 1; }
wait_healthy backend || { fail "backend не стал healthy"; exit 1; }

log "(б) создаем тестового пользователя и эксперимент с assignments..."
docker compose exec -T backend abkit-admin create-user \
    --email "$TEST_EMAIL" --name "PersistenceTest" --role admin --password "PersistTest123456" \
    || { fail "не удалось создать тестового пользователя"; exit 1; }

docker compose exec -T -e ABKIT_TEST_EXP="$TEST_EXP_NAME" backend python <<'PYEOF' || { echo "[test_persistence] FAIL: не удалось спроектировать тестовый эксперимент" >&2; exit 1; }
import os
import numpy as np
import pandas as pd
from abkit.config import DesignConfig, MetricConfig
from abkit.experiment import Experiment

n = 200
rng = np.random.default_rng(0)
data = pd.DataFrame(
    {"user_id": [f"u{i}" for i in range(n)], "revenue": rng.normal(100, 20, size=n)}
)
config = DesignConfig(
    name=os.environ["ABKIT_TEST_EXP"],
    unit_col="user_id",
    groups={"control": 0.5, "treatment": 0.5},
    metrics=[MetricConfig(name="revenue", type="continuous")],
    sample_size=n,
    split_method="simple",
    seed=1,
    # off: этот тест про персистентность, не про изоляцию — unit_id "u0".."uN"
    # пересекается с другими экспериментами, которые уже могут быть в БД
    # (реальные/демо), с дефолтной изоляцией "exclude" кандидаты для сплита
    # обнулились бы и design() упал бы с "После изоляции не осталось
    # кандидатов".
    isolation="off",
)
exp = Experiment.design(config, data)
print(f"created experiment '{exp.name}' with {len(exp.assignments)} assignments")
PYEOF

STATE_BEFORE=$(check_state)
IFS='|' read -r USER_OK EXP_OK N_BEFORE <<< "$STATE_BEFORE"
if [ "$USER_OK" != "True" ] || [ "$EXP_OK" != "True" ] || [ "$N_BEFORE" -eq 0 ]; then
    fail "тестовые данные не создались как ожидалось: $STATE_BEFORE"
    exit 1
fi
log "создано: assignments=$N_BEFORE"

REPORT_BEFORE=$(check_report_exists)
[ "$REPORT_BEFORE" = "True" ] || fail "design_report.html не найден сразу после дизайна (до любого рестарта)"

log "(в) docker compose down (БЕЗ -v)..."
docker compose down || { fail "docker compose down не выполнился"; exit 1; }

log "(г) docker compose up -d, ждем healthy..."
docker compose up -d || { fail "docker compose up -d (после down) не выполнился"; exit 1; }
wait_healthy postgres || { fail "postgres не стал healthy после down/up"; exit 1; }
wait_healthy backend || { fail "backend не стал healthy после down/up"; exit 1; }

log "(д) проверяем целостность данных после down/up..."
STATE_AFTER=$(check_state)
IFS='|' read -r USER_OK2 EXP_OK2 N_AFTER <<< "$STATE_AFTER"
[ "$USER_OK2" = "True" ] || fail "пользователь не найден после down/up"
[ "$EXP_OK2" = "True" ] || fail "эксперимент не найден после down/up"
[ "$N_AFTER" = "$N_BEFORE" ] || fail "число assignments изменилось после down/up: было $N_BEFORE, стало $N_AFTER"

REPORT_AFTER=$(check_report_exists)
[ "$REPORT_AFTER" = "True" ] || fail "design_report.html недоступен после down/up"

log "(е) docker compose build && up -d --force-recreate — проверяем то же самое..."
docker compose build backend || { fail "docker compose build backend не выполнился"; exit 1; }
docker compose up -d --force-recreate backend || { fail "up -d --force-recreate не выполнился"; exit 1; }
wait_healthy backend || { fail "backend не стал healthy после force-recreate"; exit 1; }

STATE_RECREATE=$(check_state)
IFS='|' read -r USER_OK3 EXP_OK3 N_RECREATE <<< "$STATE_RECREATE"
[ "$USER_OK3" = "True" ] || fail "пользователь не найден после force-recreate"
[ "$EXP_OK3" = "True" ] || fail "эксперимент не найден после force-recreate"
[ "$N_RECREATE" = "$N_BEFORE" ] || fail "число assignments изменилось после force-recreate: было $N_BEFORE, стало $N_RECREATE"

REPORT_RECREATE=$(check_report_exists)
[ "$REPORT_RECREATE" = "True" ] || fail "design_report.html недоступен после force-recreate"

if [ "$FAILED" -eq 0 ]; then
    log "OK — пользователь, эксперимент ($N_BEFORE assignments) и отчет пережили down/up и build+force-recreate."
    exit 0
else
    log "FAIL — см. сообщения выше. Данные НЕ переживают перезапуск как ожидается."
    exit 1
fi
