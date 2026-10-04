"""Owner answer 6 (2 Oct 2026), access levels, APPROVED: the active contact access types take
the order the owner's parser prompt lists them in, so `{{access_levels}}` renders his text.

Only `sort_order` of ACTIVE rows with these exact names changes; nothing is inserted,
renamed or deactivated, and an inactive row keeps its order. Re-running is a no-op.
Only a row whose sort_order IS NULL is filled, and an order someone already set is never overwritten.
Chained before the wording-layer migrations (pdyn_0002, pdyn_0003) so they render the
aligned order. Agents and the master_products narrowing (also answer 6) are on hold.

Revision ID: pdyn_0005_access_level_order
Revises: pdyn_0004_prompt_lists
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pdyn_0005_access_level_order"
down_revision = "pdyn_0004_prompt_lists"
branch_labels = None
depends_on = None

#: The owner's order (production text of 1 Oct 2026, line 352).
ACCESS_LEVEL_ORDER: tuple[str, ...] = (
    "Sorento Dealer", "Mocha Dealer", "Mocha Office", "Cabana Dealer", "Cabana Office", "End User",
    "Sorento Office",
)


def apply(bind) -> None:
    for i, name in enumerate(ACCESS_LEVEL_ORDER, start=1):
        bind.execute(
            sa.text(
                "UPDATE contact_access_types SET sort_order = :s "
                "WHERE is_active AND name = :n AND sort_order IS NULL"
            ),
            {"s": i, "n": name},
        )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    """The previous order was not recorded; the reorder is left in place."""
