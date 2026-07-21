from __future__ import annotations

from pydantic import BaseModel

from abkit.auth.guards import CurrentUser


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RegisterRequest(BaseModel):
    email: str
    first_name: str
    last_name: str = ""
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    must_change_password: bool
    # Per-user UI-настройки едут вместе со всем остальным про текущего
    # пользователя: и /login, и /me отдают UserOut, так что отдельного
    # запроса за настройками на старте приложения не нужно.
    folders_panel_collapsed: bool = True
    strata_balance_expanded: bool = False
    strata_power_expanded: bool = False

    @classmethod
    def from_current_user(cls, user: CurrentUser) -> "UserOut":
        return cls(
            id=user.id, email=user.email, name=user.name, role=user.role,
            must_change_password=user.must_change_password,
            folders_panel_collapsed=user.folders_panel_collapsed,
            strata_balance_expanded=user.strata_balance_expanded,
            strata_power_expanded=user.strata_power_expanded,
        )


class UpdatePreferencesRequest(BaseModel):
    """PATCH /auth/me/preferences — частичный патч (тот же паттерн, что у
    admin'ского PatchUserRequest): None означает "не трогать", а не "сбросить".
    Новые настройки добавляются сюда новым опциональным полем."""

    folders_panel_collapsed: bool | None = None
    strata_balance_expanded: bool | None = None
    strata_power_expanded: bool | None = None
