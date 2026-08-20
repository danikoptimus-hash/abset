"""users.auth_provider: how this account authenticates ('password' | 'oidc')

Keycloak OIDC SSO package. Additive and backward-compatible:

  - NOT NULL with server_default 'password', so every existing row (and every
    row an older application version might insert without knowing about this
    column) is correctly labelled as a password account without a backfill
    step;
  - a CHECK constraint rather than a free-text column — the set of auth
    providers is closed, and a typo ('oauth', 'OIDC') silently disabling the
    password guard in abkit/auth/service.py::login would be a security bug,
    not a cosmetic one.

Why password_hash stays NOT NULL. An OIDC-provisioned user has no password at
all, and the obvious move would be to relax that column to nullable. It is
deliberately NOT relaxed: an older application tag rolled back onto this
schema would then read NULL into verify_password() and crash on a login
attempt. Instead such users get a sentinel hash
(abkit/auth/passwords.py::NO_PASSWORD_SENTINEL) that matches no password under
ANY version of verify_password() — old code included, since it returns False
for any string that isn't a recognised argon2/bcrypt hash. The result is the
same ("this account cannot log in with a password") but it degrades to a clean
"invalid email or password" instead of a 500.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "auth_provider",
            sa.Text(),
            nullable=False,
            server_default="password",
        ),
    )
    op.create_check_constraint(
        "ck_users_auth_provider",
        "users",
        "auth_provider IN ('password','oidc')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_auth_provider", "users", type_="check")
    op.drop_column("users", "auth_provider")
