"""GET /users — lightweight user list (id/email/first_name/last_name/role)
for pickers (Properties modal Owners/Editors multiselects). Editor+ role, not
admin-only like /admin/users (which also exposes is_active/last_login and
supports mutation) — anyone who can open the Properties modal needs to be
able to search for people to add as owners/editors."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from abkit.auth.guards import CurrentUser
from abkit.db.repositories import UserRepo
from backend.deps import require_min_role
from backend.schemas.experiments import UserBrief

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserBrief])
def list_users(user: CurrentUser = Depends(require_min_role("editor"))) -> list[UserBrief]:
    return [
        UserBrief(id=str(u.id), email=u.email, first_name=u.first_name, last_name=u.last_name, role=u.role)
        for u in UserRepo().list_all()
    ]
