"""Чистая часть SSO (abkit/auth/oidc.py) — без БД, без сети, без Keycloak.

Здесь проверяется всё, что можно проверить на голых функциях: карта ролей,
конфиг, PKCE, discovery+кэш, проверка ID-токена (подписываем свои токены
самодельным ключом и подсовываем JWKS через monkeypatch). Сквозной браузерный
сценарий против настоящего Keycloak — frontend/e2e/sso.spec.ts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from abkit.auth import oidc


@pytest.fixture(autouse=True)
def _clear_oidc_caches():
    oidc.clear_caches()
    yield
    oidc.clear_caches()


def _settings(**overrides) -> oidc.OidcSettings:
    base = dict(
        enabled=True,
        issuer="https://kc.example.com/realms/abset",
        client_id="abset",
        client_secret="s3cret",
        role_claim="groups",
        role_map={},
        default_role="viewer",
        public_url="https://abset.intra.click.uz",
        logout_upstream=False,
    )
    base.update(overrides)
    return oidc.OidcSettings(**base)


# ---------------------------------------------------------------------------
# Карта ролей (ТЗ п.2)
# ---------------------------------------------------------------------------

ROLE_MAP = {"abset-admins": "admin", "abset-editors": "editor", "abset-viewers": "viewer"}


def test_role_map_single_group():
    s = _settings(role_map=ROLE_MAP)
    assert oidc.resolve_role(["abset-editors"], s) == "editor"


def test_role_map_highest_privilege_wins():
    """ТЗ: "highest-privilege match wins (admin > editor > viewer)". Иначе
    результат зависел бы от порядка ключей в JSON, то есть был бы
    непредсказуемым для одного и того же пользователя."""
    s = _settings(role_map=ROLE_MAP)
    assert oidc.resolve_role(["abset-viewers", "abset-admins", "abset-editors"], s) == "admin"
    # Порядок групп не влияет.
    assert oidc.resolve_role(["abset-admins", "abset-viewers"], s) == "admin"
    assert oidc.resolve_role(["abset-viewers", "abset-editors"], s) == "editor"


def test_role_map_wildcard_is_a_fallback_for_authenticated_users():
    s = _settings(role_map={**ROLE_MAP, "*": "viewer"})
    assert oidc.resolve_role(["some-unrelated-group"], s) == "viewer"
    assert oidc.resolve_role([], s) == "viewer"


def test_wildcard_participates_in_the_same_max_not_as_last_word():
    """"*" — такое же правило, а не «перебивает всё»: явная группа editor +
    "*"->viewer обязана дать editor, иначе wildcard молча понижал бы права."""
    s = _settings(role_map={"abset-editors": "editor", "*": "viewer"})
    assert oidc.resolve_role(["abset-editors"], s) == "editor"
    # И наоборот: "*" может ПОВЫСИТЬ, если так настроили.
    s2 = _settings(role_map={"abset-viewers": "viewer", "*": "editor"})
    assert oidc.resolve_role(["abset-viewers"], s2) == "editor"


def test_no_match_falls_back_to_default_role():
    s = _settings(role_map=ROLE_MAP, default_role="viewer")
    assert oidc.resolve_role(["random"], s) == "viewer"


def test_no_match_and_no_default_rejects():
    """Пустой ABKIT_OIDC_DEFAULT_ROLE — «отклонять тех, кто не попал ни в одну
    группу» (сценарий carol)."""
    s = _settings(role_map=ROLE_MAP, default_role=None)
    assert oidc.resolve_role(["random"], s) is None
    assert oidc.resolve_role([], s) is None


# ---------------------------------------------------------------------------
# Нормализация групп
# ---------------------------------------------------------------------------


def test_groups_claim_strips_keycloak_leading_slash():
    """Keycloak отдает ПУТЬ группы ("/abset-admins"), а в ABKIT_OIDC_ROLE_MAP
    админ пишет имя — иначе пришлось бы угадывать формат."""
    assert oidc.normalize_groups({"groups": ["/abset-admins"]}, "groups") == ["abset-admins"]


def test_groups_claim_nested_group_matches_by_tail_too():
    groups = oidc.normalize_groups({"groups": ["/departments/analytics/abset-admins"]}, "groups")
    assert "departments/analytics/abset-admins" in groups
    assert "abset-admins" in groups
    s = _settings(role_map=ROLE_MAP)
    assert oidc.resolve_role(groups, s) == "admin"


def test_groups_claim_accepts_a_bare_string():
    assert oidc.normalize_groups({"groups": "/abset-editors"}, "groups") == ["abset-editors"]


def test_groups_claim_absent_or_empty():
    assert oidc.normalize_groups({}, "groups") == []
    assert oidc.normalize_groups({"groups": []}, "groups") == []


def test_role_claim_name_is_configurable():
    claims = {"roles": ["/abset-admins"]}
    assert oidc.normalize_groups(claims, "roles") == ["abset-admins"]
    # ...и под дефолтным именем ничего не находится.
    assert oidc.normalize_groups(claims, "groups") == []


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    """Без ABKIT_OIDC_ENABLED всё выключено и НИЧЕГО не требуется — вход по
    паролю продолжает работать ровно как раньше."""
    for name in (
        "ABKIT_OIDC_ENABLED", "ABKIT_OIDC_ISSUER", "ABKIT_OIDC_CLIENT_ID",
        "ABKIT_OIDC_CLIENT_SECRET", "ABKIT_OIDC_ROLE_MAP", "ABKIT_PUBLIC_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = oidc.load_settings()
    assert settings.enabled is False


def test_enabled_without_required_settings_is_an_error(monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_ENABLED", "true")
    for name in ("ABKIT_OIDC_ISSUER", "ABKIT_OIDC_CLIENT_ID", "ABKIT_OIDC_CLIENT_SECRET", "ABKIT_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(oidc.OidcError) as e:
        oidc.load_settings()
    assert "ABKIT_OIDC_ISSUER" in str(e.value)


def test_role_map_broken_json_is_an_error_not_an_empty_map(monkeypatch):
    """Пустая карта означала бы, что ВСЕ проваливаются в default_role — то
    есть тихую раздачу не тех прав. Лучше отказ на старте."""
    monkeypatch.setenv("ABKIT_OIDC_ROLE_MAP", "{not json")
    with pytest.raises(oidc.OidcError):
        oidc.load_settings()


def test_role_map_unknown_role_is_an_error(monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_ROLE_MAP", json.dumps({"g": "superuser"}))
    with pytest.raises(oidc.OidcError) as e:
        oidc.load_settings()
    assert "superuser" in str(e.value)


def test_empty_default_role_means_reject(monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_DEFAULT_ROLE", "")
    assert oidc.load_settings().default_role is None


def test_unknown_default_role_is_an_error(monkeypatch):
    monkeypatch.setenv("ABKIT_OIDC_DEFAULT_ROLE", "root")
    with pytest.raises(oidc.OidcError):
        oidc.load_settings()


def test_redirect_uri_comes_from_public_url_only():
    """ТЗ п.1: redirect_uri строится из ABKIT_PUBLIC_URL и НИКОГДА из
    заголовков запроса. Здесь фиксируется сама форма значения; что подделанный
    Host на него не влияет — backend/tests/test_oidc_api.py."""
    s = _settings(public_url="https://abset.intra.click.uz")
    assert s.redirect_uri == "https://abset.intra.click.uz/api/v1/auth/oidc/callback"
    # Хвостовой слэш не должен давать двойной.
    assert _settings(public_url="https://abset.intra.click.uz/").redirect_uri == (
        "https://abset.intra.click.uz/api/v1/auth/oidc/callback"
    )


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = oidc.generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge  # base64url без паддинга, как требует RFC 7636


def test_pkce_pair_is_fresh_every_time():
    assert oidc.generate_pkce_pair()[0] != oidc.generate_pkce_pair()[0]


# ---------------------------------------------------------------------------
# Discovery + кэш
# ---------------------------------------------------------------------------


_DISCOVERY = {
    "issuer": "https://kc.example.com/realms/abset",
    "authorization_endpoint": "https://kc.example.com/realms/abset/protocol/openid-connect/auth",
    "token_endpoint": "https://kc.example.com/realms/abset/protocol/openid-connect/token",
    "jwks_uri": "https://kc.example.com/realms/abset/protocol/openid-connect/certs",
    "end_session_endpoint": "https://kc.example.com/realms/abset/protocol/openid-connect/logout",
}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_discovery_is_cached_and_not_refetched_per_login(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(_DISCOVERY)

    monkeypatch.setattr(oidc.httpx, "get", fake_get)
    s = _settings()
    oidc.discover(s)
    oidc.discover(s)
    oidc.discover(s)
    assert len(calls) == 1, "discovery must be cached, not fetched on every login"
    assert calls[0].endswith("/.well-known/openid-configuration")


def test_discovery_issuer_mismatch_is_rejected(monkeypatch):
    """Несовпадение issuer — либо опечатка в конфиге, либо нас увели на чужой
    реалм. И то, и другое должно падать громко."""
    monkeypatch.setattr(
        oidc.httpx, "get",
        lambda url, **kw: _FakeResponse({**_DISCOVERY, "issuer": "https://evil.example.com/realms/x"}),
    )
    with pytest.raises(oidc.OidcError) as e:
        oidc.discover(_settings())
    assert "mismatch" in str(e.value).lower()


def test_discovery_missing_endpoint_is_rejected(monkeypatch):
    broken = {k: v for k, v in _DISCOVERY.items() if k != "token_endpoint"}
    monkeypatch.setattr(oidc.httpx, "get", lambda url, **kw: _FakeResponse(broken))
    with pytest.raises(oidc.OidcError) as e:
        oidc.discover(_settings())
    assert "token_endpoint" in str(e.value)


def test_endpoints_are_never_hardcoded(monkeypatch):
    """ТЗ п.1: пути берутся ТОЛЬКО из discovery. Проверяем тем, что подсунули
    нестандартные — и authorization-URL собрался именно по ним."""
    weird = {
        **_DISCOVERY,
        "authorization_endpoint": "https://kc.example.com/auth/realms/abset/custom-authorize",
    }
    monkeypatch.setattr(oidc.httpx, "get", lambda url, **kw: _FakeResponse(weird))
    request = oidc.build_authorization_request(_settings())
    assert request.url.startswith("https://kc.example.com/auth/realms/abset/custom-authorize?")


def test_authorization_request_carries_state_nonce_and_pkce(monkeypatch):
    monkeypatch.setattr(oidc.httpx, "get", lambda url, **kw: _FakeResponse(_DISCOVERY))
    request = oidc.build_authorization_request(_settings())
    assert f"state={request.state}" in request.url
    assert f"nonce={request.nonce}" in request.url
    assert "code_challenge_method=S256" in request.url
    assert "response_type=code" in request.url
    # verifier наружу не уходит — в URL только challenge.
    assert request.code_verifier not in request.url


def test_internal_base_url_rewrites_only_server_side_calls(monkeypatch):
    """Dev-режим: браузер ходит на публичный адрес, backend — на внутренний.
    authorization_endpoint переписывать НЕЛЬЗЯ (по нему идет браузер)."""
    s = _settings(internal_base_url="http://keycloak:8081")
    assert oidc.to_internal_url(s, _DISCOVERY["token_endpoint"]).startswith("http://keycloak:8081/")
    # Путь сохраняется целиком.
    assert oidc.to_internal_url(s, _DISCOVERY["token_endpoint"]).endswith(
        "/realms/abset/protocol/openid-connect/token"
    )
    # В проде настройка пуста — ни одного отличия в поведении.
    assert oidc.to_internal_url(_settings(), _DISCOVERY["token_endpoint"]) == _DISCOVERY["token_endpoint"]


def test_logout_url_only_when_enabled(monkeypatch):
    monkeypatch.setattr(oidc.httpx, "get", lambda url, **kw: _FakeResponse(_DISCOVERY))
    assert oidc.build_logout_url(_settings(logout_upstream=False)) is None
    url = oidc.build_logout_url(_settings(logout_upstream=True))
    assert url is not None and url.startswith(_DISCOVERY["end_session_endpoint"])
    assert "post_logout_redirect_uri=https%3A%2F%2Fabset.intra.click.uz%2Flogin" in url


# ---------------------------------------------------------------------------
# Проверка ID-токена
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_id_token(rsa_key, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://kc.example.com/realms/abset",
        "aud": "abset",
        "sub": "user-uuid",
        "iat": now,
        "exp": now + 300,
        "nonce": "the-nonce",
        "email": "alice@corp.example",
        "email_verified": True,
        "given_name": "Alice",
        "family_name": "Admin",
        "groups": ["/abset-admins"],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, rsa_key, algorithm="RS256")


@pytest.fixture
def verifying(monkeypatch, rsa_key):
    """discover() отдает фиктивный документ, а JWKS-клиент — наш публичный ключ."""
    monkeypatch.setattr(oidc.httpx, "get", lambda url, **kw: _FakeResponse(_DISCOVERY))

    class _Key:
        key = rsa_key.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(oidc, "_jwk_client", lambda uri: _Client())
    return _settings(role_map=ROLE_MAP)


def test_valid_id_token_yields_identity(verifying, rsa_key):
    identity = oidc.verify_id_token(
        verifying, _make_id_token(rsa_key), expected_nonce="the-nonce"
    )
    assert identity.email == "alice@corp.example"
    assert identity.email_verified is True
    assert identity.first_name == "Alice"
    assert identity.groups == ["abset-admins"]
    assert oidc.resolve_role(identity.groups, verifying) == "admin"


def test_nonce_mismatch_is_rejected(verifying, rsa_key):
    """Без проверки nonce валидный, но переигранный ID-токен прошел бы."""
    with pytest.raises(oidc.OidcError) as e:
        oidc.verify_id_token(verifying, _make_id_token(rsa_key), expected_nonce="different-nonce")
    assert "nonce" in str(e.value).lower()


def test_unverified_email_is_rejected(verifying, rsa_key):
    """ТЗ п.2 — сопоставление ТОЛЬКО по проверенному email: иначе чужой адрес
    в своем профиле IdP давал бы доступ к чужому аккаунту ABSet."""
    token = _make_id_token(rsa_key, email_verified=False)
    with pytest.raises(oidc.OidcError) as e:
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")
    assert "verified" in str(e.value).lower()


def test_missing_email_is_rejected(verifying, rsa_key):
    token = _make_id_token(rsa_key, email="")
    with pytest.raises(oidc.OidcError) as e:
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")
    assert "email" in str(e.value).lower()


def test_expired_token_is_rejected(verifying, rsa_key):
    now = int(time.time())
    token = _make_id_token(rsa_key, iat=now - 3600, exp=now - 1800)
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")


def test_small_clock_skew_is_tolerated(verifying, rsa_key):
    """Истек 30 секунд назад — в пределах CLOCK_SKEW_SECONDS, принимаем:
    обычный дрейф часов не должен ломать вход."""
    now = int(time.time())
    token = _make_id_token(rsa_key, iat=now - 300, exp=now - 30)
    identity = oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")
    assert identity.email == "alice@corp.example"


def test_wrong_audience_is_rejected(verifying, rsa_key):
    token = _make_id_token(rsa_key, aud="some-other-client")
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")


def test_wrong_issuer_is_rejected(verifying, rsa_key):
    token = _make_id_token(rsa_key, iss="https://evil.example.com/realms/x")
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")


def test_unsigned_token_is_rejected(verifying):
    """alg=none — классическая атака на JWT. Список алгоритмов задаем МЫ, а не
    заголовок токена, поэтому такой токен не рассматривается в принципе."""
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://kc.example.com/realms/abset", "aud": "abset", "sub": "x",
            "iat": now, "exp": now + 300, "nonce": "the-nonce",
            "email": "attacker@evil.example", "email_verified": True,
        },
        key="", algorithm="none",
    )
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")


def test_token_signed_by_the_wrong_key_is_rejected(verifying):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "https://kc.example.com/realms/abset", "aud": "abset", "sub": "x",
            "iat": now, "exp": now + 300, "nonce": "the-nonce",
            "email": "attacker@evil.example", "email_verified": True,
        },
        other_key, algorithm="RS256",
    )
    with pytest.raises(oidc.OidcError):
        oidc.verify_id_token(verifying, token, expected_nonce="the-nonce")


def test_jwks_client_is_reused_per_uri(monkeypatch):
    """Ходить за JWKS на каждый логин не нужно — клиент (и его кэш ключей)
    переиспользуется."""
    oidc.clear_caches()
    created = []

    class _Client:
        def __init__(self, uri, **kwargs):
            created.append(uri)

    monkeypatch.setattr(oidc, "PyJWKClient", _Client)
    uri = _DISCOVERY["jwks_uri"]
    first = oidc._jwk_client(uri)
    second = oidc._jwk_client(uri)
    assert first is second
    assert len(created) == 1
