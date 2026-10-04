"""chatbot pickers no longer capped at 10: roster_cap starts at the S3 ceiling, 50

PICKER-NO-CAP (owner, 2 Oct 2026: "the chatbot picker is capped at 10 options, notably
in the incoming search. Remove that cap"; crew answer (c), PR #1436). The setting stays
(`chatbot_entity_kinds.roster_cap`, owner ruling 20 Sep 2026) and so does its ceiling of
50 (security review S3, `api/v1/system/chatbot_config.py`). Only the starting value
moves: the server default goes from 10 to 50, and every row still at the old default 10
moves to 50. A row the owner set to any other number keeps it. The resolver returns at
most 20 to 25 matches per typed word, so in practice nothing is cut.

Row content, but no `seed_chatbot_policy` replay is needed: `policy_rows.py`'s seed
rows carry 50 themselves, and the model's `server_default` carries it for `create_all`.

Revision ID: picker_no_cap_0001
Revises: grn_pull_0001_perm
"""
from __future__ import annotations

from alembic import op

revision = "picker_no_cap_0001"
down_revision = "grn_pull_0001_perm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chatbot_entity_kinds ALTER COLUMN roster_cap SET DEFAULT 50")
    op.execute("UPDATE chatbot_entity_kinds SET roster_cap = 50 WHERE roster_cap = 10")


def downgrade() -> None:
    # A row set to 50 by hand after the upgrade is not told apart from one this
    # migration moved, so both go back to 10.
    op.execute("ALTER TABLE chatbot_entity_kinds ALTER COLUMN roster_cap SET DEFAULT 10")
    op.execute("UPDATE chatbot_entity_kinds SET roster_cap = 10 WHERE roster_cap = 50")
