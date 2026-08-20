#!/usr/bin/env bash
set -euo pipefail

# The ONLY supported way to run Playwright e2e locally (CLAUDE.md, "Правило:
# гигиена dev-артефактов", root cause (а)): a raw `npx playwright test`
# against E2E_BASE_URL pointed at the persistent local dev stack (:8080) is
# what left ~170 experiments / ~250 datasets / ~70 stray user accounts behind
# across a handful of sessions before this was caught (see
# abkit/jobs.py::run_cleanup_dev's docstring for the full autopsy). This
# script instead brings up a throwaway stack under its own compose PROJECT
# NAME (-p) — separate containers, network, and (critically) separate named
# volumes from the dev stack — on its own port, runs the suite against it,
# and always tears it down (`docker compose down -v`) on exit, success or
# not. Mirrors .github/workflows/ci.yml's e2e job, which is already isolated
# by simply running on a fresh, single-use runner VM.
#
# Usage: scripts/e2e.sh [--keycloak] [any extra `playwright test` args]
#   --keycloak  also bring up the dev Keycloak (docker-compose.keycloak.yml)
#               and enable SSO on the backend — required by e2e/sso.spec.ts,
#               which is SKIPPED without it (so a plain `scripts/e2e.sh` stays
#               fast and needs no IdP).
# Env override: E2E_PORT (default 8090) — pick a free port if 8090 is taken
# (e.g. by a second concurrent e2e run); E2E_KEYCLOAK_PORT (default 8081).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PROJECT="abkit_e2e"
PORT="${E2E_PORT:-8090}"
KEYCLOAK_PORT="${E2E_KEYCLOAK_PORT:-8081}"

# --keycloak must be consumed here, not passed through to playwright.
WITH_KEYCLOAK=0
if [ "${1:-}" = "--keycloak" ]; then
    WITH_KEYCLOAK=1
    shift
fi

COMPOSE_FILES=(-f docker-compose.yml)
if [ "$WITH_KEYCLOAK" = "1" ]; then
    COMPOSE_FILES+=(-f docker-compose.keycloak.yml)
fi
ENV_FILE="$(mktemp)"
PG_PASSWORD="$(openssl rand -hex 16)"

cat > "$ENV_FILE" <<EOF
ABKIT_SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$PG_PASSWORD
ABKIT_PORT=$PORT
ABKIT_ADMIN_EMAIL=admin@e2e.test
ABKIT_ADMIN_PASSWORD=e2epass123
ABKIT_ADMIN_NAME=E2E Admin
EOF

if [ "$WITH_KEYCLOAK" = "1" ]; then
    # The issuer string must be identical for the browser and for the
    # backend (it is what lands in the token's `iss`), so both use the
    # published host port; only the backend's server-to-server calls are
    # redirected to the container via ABKIT_OIDC_INTERNAL_BASE_URL. See
    # abkit/auth/oidc.py::to_internal_url.
    cat >> "$ENV_FILE" <<EOF
ABKIT_KEYCLOAK_PORT=$KEYCLOAK_PORT
ABKIT_OIDC_ENABLED=true
ABKIT_OIDC_ISSUER=http://localhost:${KEYCLOAK_PORT}/realms/abset-dev
ABKIT_OIDC_INTERNAL_BASE_URL=http://keycloak:8081
ABKIT_OIDC_CLIENT_ID=abset
ABKIT_OIDC_CLIENT_SECRET=abset-dev-secret
ABKIT_OIDC_ROLE_CLAIM=groups
ABKIT_OIDC_ROLE_MAP={"abset-admins":"admin","abset-editors":"editor","abset-viewers":"viewer"}
ABKIT_OIDC_DEFAULT_ROLE=
ABKIT_PUBLIC_URL=http://localhost:${PORT}
EOF
fi

cleanup() {
    echo "==> Tearing down $PROJECT (docker compose down -v)"
    docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" -p "$PROJECT" down -v --remove-orphans || true
    rm -f "$ENV_FILE"
}
trap cleanup EXIT

echo "==> Starting isolated e2e stack (project=$PROJECT, port=$PORT, keycloak=$WITH_KEYCLOAK)"
docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" -p "$PROJECT" up -d --build --wait

echo "==> Creating viewer@e2e.test fixture (admin@e2e.test comes from ABKIT_ADMIN_EMAIL bootstrap)"
docker compose --env-file "$ENV_FILE" "${COMPOSE_FILES[@]}" -p "$PROJECT" exec -T backend abkit-admin create-user \
    --email viewer@e2e.test --first-name "E2E Viewer" --role viewer --password e2epass123

echo "==> Running Playwright against http://localhost:${PORT}"
cd frontend
E2E_KEYCLOAK_URL="$([ "$WITH_KEYCLOAK" = "1" ] && echo "http://localhost:${KEYCLOAK_PORT}" || echo "")" \
E2E_BASE_URL="http://localhost:${PORT}" \
E2E_API_BASE="http://localhost:${PORT}/api/v1" \
E2E_POSTGRES_HOST=postgres \
E2E_POSTGRES_PORT=5432 \
E2E_POSTGRES_USER=abkit \
E2E_POSTGRES_PASSWORD="$PG_PASSWORD" \
E2E_POSTGRES_DB=abkit \
npx playwright test "$@"
