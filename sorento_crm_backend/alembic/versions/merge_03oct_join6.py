"""Join the heads left on main by the #1450 and #1442 merges.

#1450 landed `item_type_0001` and #1442 landed `oipf_0001_prev_item_code`,
both hung off `merge_03oct_join5`, so main carried two heads. Schema-free
merge point.

Revision ID: merge_03oct_join6
Revises: item_type_0001, oipf_0001_prev_item_code
"""
from __future__ import annotations

revision = "merge_03oct_join6"
down_revision = ("item_type_0001", "oipf_0001_prev_item_code")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
