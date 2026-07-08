from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserAdminOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None


class CreateUserRequest(BaseModel):
    email: str
    name: str
    role: str
    password: str | None = None


class CreateUserResponse(BaseModel):
    user: UserAdminOut
    generated_password: str


class PatchUserRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordResponse(BaseModel):
    new_password: str
