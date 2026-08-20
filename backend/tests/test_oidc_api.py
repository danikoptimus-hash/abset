"""SSO-эндпоинты и провижининг против РЕАЛЬНОЙ БД.

Keycloak тут не поднимается: сеть к IdP (discovery / обмен кода / проверка
подписи) подменяется, а проверяется всё остальное — редирект и его параметры,
state/nonce-транзакция, создание пользователя, обновление роли, отказы и
audit_log. Сквозной браузерный сценарий против настоящего Keycloak —
frontend/e2e/sso.spec.ts.
"""

from __future__ import annotations

import pytest

from abkit.auth import oidc as oidc_mod
from abkit.auth.passwords import hash_password
from abkit.db.repositories import AuditRepo, UserRepo

ISSUER = "https://kc.example.com/realms/abset"
PUBLIC_URL = "https://abset.intra.click.uz"
ROLE_MAP = '{"abset-admins":"admin","abset-editors":"editor","abset-viewers":"viewer"}'


@pytest.fixture
def oidc_env(monkeypatch):
    """OIDC включен и полностью настроен."""
    monkeypatch.setenv("ABKIT_OIDC_ENABLED", "true")
    monkeypatch.setenv("ABKIT_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("ABKIT_OIDC_CLIENT_ID", "abset")
    monkeypatch.setenv("ABKIT_OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("ABKIT_OIDC_ROLE_CLAIM", "groups")
    monkeypatch.setenv("ABKIT_OIDC_ROLE_MAP", ROLE_MAP)
    monkeypatch.setenv("ABKIT_OIDC_DEFAULT_ROLE", "")
    monkeypatch.setenv("ABKIT_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.delenv("ABKIT_OIDC_INTERNAL_BASE_URL", raising=False)
    oidc_mod.clear_caches()
    _stub_discovery(monkeypatch)
    yield
    oidc_mod.clear_caches()


def _stub_discovery(monkeypatch):
    document = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
        "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
    }
    monkeypatch.setattr(oidc_mod, "discover", lambda settings: document)


def _stub_identity(monkeypatch, *, email, groups, first_name="Test", last_name="User"):
    """Подменяет обмен кода и проверку подписи: к моменту oidc_login личность
    считается уже проверенной (это ровно то, что покрывают юнит-тесты
    tests/test_oidc_core.py)."""
    import backend.routers.oidc as router_mod

    identity = oidc_mod.OidcIdentity(
        subject="sub-" + email, email=email, email_verified=True,
        first_name=first_name, last_name=last_name, groups=groups, id_token="stub",
    )
    monkeypatch.setattr(router_mod, "exchange_code", lambda *a, **kw: {"id_token": "stub"})
    monkeypatch.setattr(router_mod, "verify_id_token", lambda *a, **kw: identity)
    return identity


def _start_login(app_client) -> tuple[str, str]:
    """GET /auth/oidc/login -> (state, tx-cookie). Клиент TestClient сам
    сохраняет cookie, но state нужен явно, чтобы собрать callback-URL."""
    import urllib.parse as up

    resp = app_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302, resp.text[:300]
    query = up.parse_qs(up.urlsplit(resp.headers["location"]).query)
    return query["state"][0], resp.cookies["abkit_oidc_tx"]


def _oidc_actions(action: str) -> list:
    return AuditRepo().list_recent(action=action, limit=50)


# ---------------------------------------------------------------------------
# Выключенный OIDC: ничего не меняется (ТЗ п.1)
# ---------------------------------------------------------------------------


def test_password_login_unchanged_when_oidc_disabled(app_client, monkeypatch):
    monkeypatch.delenv("ABKIT_OIDC_ENABLED", raising=False)
    UserRepo().create(
        email="pw@co.com", first_name="P", password_hash=hash_password("pw12345"), role="editor"
    )
    resp = app_client.post("/api/v1/auth/login", json={"email": "pw@co.com", "password": "pw12345"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"
    assert resp.json()["auth_provider"] == "password"


def test_config_reports_oidc_disabled_by_default(app_client, monkeypatch):
    monkeypatch.delenv("ABKIT_OIDC_ENABLED", raising=False)
    assert app_client.get("/api/v1/auth/config").json()["oidc_enabled"] is False


def test_sso_endpoints_refuse_politely_when_disabled(app_client, monkeypatch):
    monkeypatch.delenv("ABKIT_OIDC_ENABLED", raising=False)
    resp = app_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 404
    # Человеческая страница, не JSON и не пустой экран (ТЗ п.3).
    assert "text/html" in resp.headers["content-type"]
    assert "Sign in with your password" in resp.text


def test_broken_oidc_config_does_not_break_the_login_page(app_client, monkeypatch):
    """Опечатка в ABKIT_OIDC_ROLE_MAP не должна отрезать вход по паролю —
    /auth/config обязан ответить, просто без кнопки SSO."""
    monkeypatch.setenv("ABKIT_OIDC_ENABLED", "true")
    monkeypatch.setenv("ABKIT_OIDC_ROLE_MAP", "{broken json")
    resp = app_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.json()["oidc_enabled"] is False


# ---------------------------------------------------------------------------
# Старт авторизации
# ---------------------------------------------------------------------------


def test_config_reports_oidc_enabled(app_client, oidc_env):
    assert app_client.get("/api/v1/auth/config").json()["oidc_enabled"] is True


def test_login_redirects_with_state_nonce_and_pkce(app_client, oidc_env):
    import urllib.parse as up

    resp = app_client.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
    query = up.parse_qs(up.urlsplit(location).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["abset"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] and query["nonce"] and query["code_challenge"]

    tx = resp.cookies["abkit_oidc_tx"]
    assert tx
    raw = resp.headers["set-cookie"]
    assert "HttpOnly" in raw
    # Lax, НЕ Strict: Strict-cookie браузер не пришлет при возврате с домена
    # Keycloak, и вход ломался бы ровно на callback'е.
    assert "SameSite=lax" in raw.replace("samesite=lax", "SameSite=lax")


def test_redirect_uri_is_built_from_public_url_not_host_header(app_client, oidc_env):
    """ТЗ п.5 (security): подделанный Host не должен влиять на redirect_uri —
    иначе при слабой проверке на стороне IdP код улетел бы на чужой домен."""
    import urllib.parse as up

    resp = app_client.get(
        "/api/v1/auth/oidc/login",
        headers={"Host": "evil.example.com", "X-Forwarded-Host": "evil.example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    query = up.parse_qs(up.urlsplit(resp.headers["location"]).query)
    assert query["redirect_uri"] == [f"{PUBLIC_URL}/api/v1/auth/oidc/callback"]
    assert "evil.example.com" not in resp.headers["location"]


# ---------------------------------------------------------------------------
# Callback: провижининг и роли (ТЗ п.2)
# ---------------------------------------------------------------------------


def test_callback_provisions_new_user_and_signs_them_in(app_client, oidc_env, monkeypatch):
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="alice@corp.example", groups=["abset-admins"],
                   first_name="Alice", last_name="Admin")

    resp = app_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
    )
    assert resp.status_code == 302, resp.text[:400]
    assert resp.headers["location"] == "/experiments"

    me = app_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "alice@corp.example"
    assert body["role"] == "admin"
    assert body["auth_provider"] == "oidc"
    assert body["must_change_password"] is False

    created = UserRepo().get_by_email("alice@corp.example")
    assert created.auth_provider == "oidc"
    assert created.first_name == "Alice"

    assert any(e.user_email == "alice@corp.example" for e in _oidc_actions("auth.oidc_user_provisioned"))
    assert any(e.user_email == "alice@corp.example" for e in _oidc_actions("auth.oidc_login"))


def test_highest_privilege_group_wins(app_client, oidc_env, monkeypatch):
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="multi@corp.example",
                   groups=["abset-viewers", "abset-admins", "abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert UserRepo().get_by_email("multi@corp.example").role == "admin"


def test_role_is_refreshed_from_groups_on_every_login(app_client, oidc_env, monkeypatch):
    """ТЗ п.2: изменение групп доезжает при следующем входе, без действий с
    нашей стороны."""
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="bob@corp.example", groups=["abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert UserRepo().get_by_email("bob@corp.example").role == "editor"
    user_id = UserRepo().get_by_email("bob@corp.example").id

    state2, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="bob@corp.example", groups=["abset-admins"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state2}", follow_redirects=False)

    updated = UserRepo().get_by_email("bob@corp.example")
    assert updated.role == "admin"
    # Тот же аккаунт, а не второй с тем же email.
    assert updated.id == user_id

    changed = _oidc_actions("auth.oidc_role_changed")
    assert any(
        e.user_email == "bob@corp.example" and e.details["from"] == "editor" and e.details["to"] == "admin"
        for e in changed
    )


def test_user_with_no_mapped_group_is_rejected_when_no_default_role(app_client, oidc_env, monkeypatch):
    """Сценарий carol: аутентифицирован в Keycloak, но доступа к ABSet нет."""
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="carol@corp.example", groups=[])

    resp = app_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
    )
    assert resp.status_code == 403
    assert "text/html" in resp.headers["content-type"]
    assert "not a member of any group" in resp.text
    # Отклоненный вход не должен создавать аккаунт.
    assert UserRepo().get_by_email("carol@corp.example") is None
    assert app_client.get("/api/v1/auth/me").status_code == 401
    assert any(
        e.user_email == "carol@corp.example" and e.details["reason"] == "no_role_mapping"
        for e in _oidc_actions("auth.oidc_login_rejected")
    )


def test_user_with_no_mapped_group_gets_default_role_when_enabled(app_client, oidc_env, monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_DEFAULT_ROLE", "viewer")
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="carol2@corp.example", groups=[])
    resp = app_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False
    )
    assert resp.status_code == 302
    assert UserRepo().get_by_email("carol2@corp.example").role == "viewer"


def test_wildcard_mapping_grants_access_to_any_authenticated_user(app_client, oidc_env, monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_ROLE_MAP", '{"abset-admins":"admin","*":"viewer"}')
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="anyone@corp.example", groups=["unrelated"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert UserRepo().get_by_email("anyone@corp.example").role == "viewer"


def test_existing_password_user_matched_by_email_keeps_the_account(app_client, oidc_env, monkeypatch):
    """Пользователь, заведенный ДО SSO, при первом входе через SSO должен
    попасть в СВОЙ аккаунт (по email), а не получить второй — иначе он
    потерял бы свои эксперименты."""
    existing_id = UserRepo().create(
        email="veteran@corp.example", first_name="Vet",
        password_hash=hash_password("pw12345"), role="viewer",
    )
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="veteran@corp.example", groups=["abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)

    user = UserRepo().get_by_email("veteran@corp.example")
    assert user.id == existing_id
    assert user.role == "editor"  # роль подтянулась из групп
    # auth_provider у него остается 'password' — пароль-то никуда не делся,
    # и мы его не отнимаем (ТЗ: password users keep password login).
    assert user.auth_provider == "password"
    login = app_client.post(
        "/api/v1/auth/login", json={"email": "veteran@corp.example", "password": "pw12345"}
    )
    assert login.status_code == 200


def test_locally_deactivated_oidc_user_is_rejected(app_client, oidc_env, monkeypatch):
    """Деактивация в ABSet перевешивает: Keycloak про нее не знает и пустил бы."""
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="gone@corp.example", groups=["abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    user = UserRepo().get_by_email("gone@corp.example")
    UserRepo().set_active(user.id, False)
    app_client.post("/api/v1/auth/logout")

    state2, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="gone@corp.example", groups=["abset-editors"])
    resp = app_client.get(
        f"/api/v1/auth/oidc/callback?code=abc&state={state2}", follow_redirects=False
    )
    assert resp.status_code == 403
    assert "deactivated" in resp.text
    assert any(
        e.user_email == "gone@corp.example" and e.details["reason"] == "inactive"
        for e in _oidc_actions("auth.oidc_login_rejected")
    )


# ---------------------------------------------------------------------------
# Безопасность callback'а (ТЗ п.5)
# ---------------------------------------------------------------------------


def test_tampered_state_is_rejected_and_audited(app_client, oidc_env, monkeypatch):
    _start_login(app_client)
    _stub_identity(monkeypatch, email="attacker@corp.example", groups=["abset-admins"])

    resp = app_client.get(
        "/api/v1/auth/oidc/callback?code=abc&state=not-the-state-we-issued",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "text/html" in resp.headers["content-type"]
    assert "state mismatch" in resp.text
    assert app_client.get("/api/v1/auth/me").status_code == 401
    assert UserRepo().get_by_email("attacker@corp.example") is None
    assert any(
        e.details.get("reason") == "state_mismatch" for e in _oidc_actions("auth.oidc_login_rejected")
    )


def test_callback_without_transaction_cookie_is_rejected(app_client, oidc_env, monkeypatch):
    """Прямой заход на callback мимо /login (или протухшая вкладка)."""
    _stub_identity(monkeypatch, email="x@corp.example", groups=["abset-admins"])
    resp = app_client.get(
        "/api/v1/auth/oidc/callback?code=abc&state=whatever", follow_redirects=False
    )
    assert resp.status_code == 400
    assert "expired" in resp.text or "outside the login flow" in resp.text
    assert any(
        e.details.get("reason") == "missing_transaction"
        for e in _oidc_actions("auth.oidc_login_rejected")
    )


def test_forged_transaction_cookie_is_rejected(app_client, oidc_env, monkeypatch):
    """Cookie подписана нашим ABKIT_SECRET_KEY — подделать обе половины
    (state в URL и state в cookie) не зная секрета нельзя."""
    _stub_identity(monkeypatch, email="x2@corp.example", groups=["abset-admins"])
    app_client.cookies.set("abkit_oidc_tx", "not.a.valid.jwt", path="/api/v1/auth/oidc")
    resp = app_client.get(
        "/api/v1/auth/oidc/callback?code=abc&state=whatever", follow_redirects=False
    )
    assert resp.status_code == 400
    assert any(
        e.details.get("reason") == "invalid_transaction"
        for e in _oidc_actions("auth.oidc_login_rejected")
    )


def test_provider_error_renders_a_human_page_and_is_audited(app_client, oidc_env):
    """Keycloak отказал сам (аккаунт отключен, доступ к клиенту запрещен)."""
    state, _ = _start_login(app_client)
    resp = app_client.get(
        f"/api/v1/auth/oidc/callback?error=access_denied&error_description=Account+disabled&state={state}",
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "text/html" in resp.headers["content-type"]
    assert "Account disabled" in resp.text
    assert "recently disabled" in resp.text
    assert any(
        e.details.get("reason") == "provider_error" for e in _oidc_actions("auth.oidc_login_rejected")
    )


def test_error_page_offers_both_ways_out(app_client, oidc_env):
    """ТЗ п.3: "try again" и "use password instead", а не тупик."""
    resp = app_client.get(
        "/api/v1/auth/oidc/callback?code=abc&state=nope", follow_redirects=False
    )
    assert "/api/v1/auth/oidc/login" in resp.text
    assert "/login?sso=failed" in resp.text


# ---------------------------------------------------------------------------
# Сосуществование с паролем (ТЗ п.2)
# ---------------------------------------------------------------------------


def test_password_login_still_works_while_oidc_is_enabled(app_client, oidc_env):
    """Break-glass admin и все ранее заведенные аккаунты не должны терять
    парольный вход от включения SSO."""
    UserRepo().create(
        email="breakglass@co.com", first_name="BG",
        password_hash=hash_password("pw12345"), role="admin",
    )
    resp = app_client.post(
        "/api/v1/auth/login", json={"email": "breakglass@co.com", "password": "pw12345"}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_oidc_user_cannot_log_in_with_a_password(app_client, oidc_env, monkeypatch):
    """У SSO-аккаунта пароля нет вовсе — и подобрать его нельзя, потому что
    хранится не хеш, а заведомо не-хеш (NO_PASSWORD_SENTINEL)."""
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="ssoonly@corp.example", groups=["abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    app_client.post("/api/v1/auth/logout")

    for attempt in ("password", "", "!no-password:oidc"):
        resp = app_client.post(
            "/api/v1/auth/login", json={"email": "ssoonly@corp.example", "password": attempt}
        )
        assert resp.status_code == 401, f"password '{attempt}' must not be accepted"
    assert "corporate SSO" in resp.json()["error"]["message"]


def test_admin_user_list_exposes_auth_provider(app_client, oidc_env, monkeypatch):
    UserRepo().create(
        email="adm@co.com", first_name="A", password_hash=hash_password("pw12345"), role="admin"
    )
    state, _ = _start_login(app_client)
    _stub_identity(monkeypatch, email="ssouser@corp.example", groups=["abset-editors"])
    app_client.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    app_client.post("/api/v1/auth/logout")

    app_client.post("/api/v1/auth/login", json={"email": "adm@co.com", "password": "pw12345"})
    users = {u["email"]: u for u in app_client.get("/api/v1/admin/users").json()}
    assert users["ssouser@corp.example"]["auth_provider"] == "oidc"
    assert users["adm@co.com"]["auth_provider"] == "password"


def test_logout_reports_no_upstream_url_by_default(app_client, oidc_env):
    """ABKIT_OIDC_LOGOUT_UPSTREAM выключен по умолчанию: выход из ABSet не
    должен неожиданно выкидывать пользователя из всех корпоративных систем."""
    resp = app_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["upstream_logout_url"] == ""
