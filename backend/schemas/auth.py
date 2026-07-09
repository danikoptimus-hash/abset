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

    @classmethod
    def from_current_user(cls, user: CurrentUser) -> "UserOut":
        return cls(
            id=user.id, email=user.email, name=user.name, role=user.role,
            must_change_password=user.must_change_password,
        )
