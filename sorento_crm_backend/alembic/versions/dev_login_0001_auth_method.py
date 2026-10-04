"""user_sessions.auth_method accepts 'dev_login'

DEV-LOGIN-BYPASS (owner, 3 Oct 2026; crew answer Q1 (a)): a passwordless sign-in on a local
test copy records its own auth_method so a dev session stays distinguishable in audit and the
session list. Additive: the CHECK constraint is widened, no row changes.

Revision ID: dev_login_0001
Revises: merge_03oct_join6
"""
from __future__ import annotations

from alembic import op

revision = "dev_login_0001"
down_revision = "merge_03oct_join6"
branch_labels = None
depends_on = None

_OLD = "('password', 'phone_otp', 'portal_link', 'impersonation')"
_NEW = "('password', 'phone_otp', 'portal_link', 'impersonation', 'dev_login')"


def upgrade() -> None:
    op.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS ck_user_sessions_auth_method")
    op.execute(
        "ALTER TABLE user_sessions ADD CONSTRAINT ck_user_sessions_auth_method "
        f"CHECK (auth_method IN {_NEW})"
    )


def downgrade() -> None:
    # A dev session cannot satisfy the narrower check, so those rows go first.
    op.execute("DELETE FROM user_sessions WHERE auth_method = 'dev_login'")
    op.execute("ALTER TABLE user_sessions DROP CONSTRAINT IF EXISTS ck_user_sessions_auth_method")
    op.execute(
        "ALTER TABLE user_sessions ADD CONSTRAINT ck_user_sessions_auth_method "
        f"CHECK (auth_method IN {_OLD})"
    )
