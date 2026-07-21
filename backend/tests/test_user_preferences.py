"""PATCH /auth/me/preferences — первая per-user UI-настройка в приложении
(пакет share+folders): панель папок свернута по умолчанию, выбор запоминается.
"""

from __future__ import annotations

from abkit.auth.passwords import hash_password
from abkit.db.repositories import UserRepo


def _login(app_client, email="editor@co.com", role="editor"):
    UserRepo().create(email=email, first_name="E", password_hash=hash_password("pw12345"), role=role)
    resp = app_client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345"})
    assert resp.status_code == 200, resp.text
    return resp


def test_new_user_defaults_to_collapsed(app_client):
    """Продуктовое решение "свернута по умолчанию" — это server_default
    колонки, а не значение в UI: свежий пользователь получает его сразу и от
    /login, и от /me."""
    login = _login(app_client)
    assert login.json()["folders_panel_collapsed"] is True
    assert app_client.get("/api/v1/auth/me").json()["folders_panel_collapsed"] is True


def test_preference_survives_a_new_session(app_client):
    """Именно то, чего не умел useState: выбор переживает logout/login (а
    значит и перезагрузку, и навигацию)."""
    _login(app_client)

    patched = app_client.patch(
        "/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["folders_panel_collapsed"] is False

    app_client.post("/api/v1/auth/logout")
    relogin = app_client.post(
        "/api/v1/auth/login", json={"email": "editor@co.com", "password": "pw12345"}
    )
    assert relogin.json()["folders_panel_collapsed"] is False
    assert app_client.get("/api/v1/auth/me").json()["folders_panel_collapsed"] is False


def test_patch_returns_the_whole_user(app_client):
    """Фронт кладет ответ прямо в AuthContext — значит в нем должен быть весь
    пользователь, а не одно измененное поле."""
    _login(app_client)
    body = app_client.patch(
        "/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False}
    ).json()
    assert body["email"] == "editor@co.com"
    assert body["role"] == "editor"
    assert body["id"]


def test_omitted_field_is_left_alone(app_client):
    """None = "не трогать", а не "сбросить в дефолт" (паттерн частичного
    патча, как у admin'ского PatchUserRequest)."""
    _login(app_client)
    app_client.patch("/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False})

    patched = app_client.patch("/api/v1/auth/me/preferences", json={})
    assert patched.status_code == 200, patched.text
    assert patched.json()["folders_panel_collapsed"] is False


def test_preference_is_per_user_not_global(app_client):
    _login(app_client, email="first@co.com")
    app_client.patch("/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False})
    app_client.post("/api/v1/auth/logout")

    _login(app_client, email="second@co.com")
    assert app_client.get("/api/v1/auth/me").json()["folders_panel_collapsed"] is True


def test_viewer_can_set_own_preference(app_client):
    """Это про свой интерфейс, не про данные — гейта по роли быть не должно."""
    _login(app_client, email="viewer@co.com", role="viewer")
    patched = app_client.patch(
        "/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["folders_panel_collapsed"] is False


def test_anonymous_cannot_set_preferences(app_client):
    resp = app_client.patch(
        "/api/v1/auth/me/preferences", json={"folders_panel_collapsed": False}
    )
    assert resp.status_code == 401, resp.text


def test_strata_balance_expanded_defaults_false_and_persists(app_client):
    """Настройка №2 (§3 collapsible strata balance) — тот же типизированный
    паттерн: server_default=false (свернута), выбор переживает сессию, а
    частичный патч не трогает соседнюю настройку."""
    login = _login(app_client)
    assert login.json()["strata_balance_expanded"] is False

    patched = app_client.patch(
        "/api/v1/auth/me/preferences", json={"strata_balance_expanded": True}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["strata_balance_expanded"] is True
    # folders preference untouched by a strata-only patch.
    assert patched.json()["folders_panel_collapsed"] is True

    app_client.post("/api/v1/auth/logout")
    relogin = app_client.post(
        "/api/v1/auth/login", json={"email": "editor@co.com", "password": "pw12345"}
    )
    assert relogin.json()["strata_balance_expanded"] is True


def test_strata_power_expanded_defaults_false_and_persists(app_client):
    """Настройка №3 (visibility package: collapsible strata power check) —
    тот же паттерн, независима от balance."""
    login = _login(app_client)
    assert login.json()["strata_power_expanded"] is False

    patched = app_client.patch(
        "/api/v1/auth/me/preferences", json={"strata_power_expanded": True}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["strata_power_expanded"] is True
    # neighbouring prefs untouched by a power-only patch.
    assert patched.json()["strata_balance_expanded"] is False
    assert patched.json()["folders_panel_collapsed"] is True

    app_client.post("/api/v1/auth/logout")
    relogin = app_client.post(
        "/api/v1/auth/login", json={"email": "editor@co.com", "password": "pw12345"}
    )
    assert relogin.json()["strata_power_expanded"] is True
