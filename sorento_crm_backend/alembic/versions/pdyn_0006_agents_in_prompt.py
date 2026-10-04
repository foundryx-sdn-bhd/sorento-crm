"""Owner Q-B (2 Oct 2026) = (b): which agents the parser prompt names.

`access_agents.in_parser_prompt` (boolean, default false), seeded true for the owner's five
agents; `{{agents}}` renders the flagged ACTIVE rows only, so his line 774 becomes a variable
without deactivating any agent (`is_active` also gates MCP tool access, field access and
user assignment). Kept simple on purpose: lane ACCESS-MODEL will later turn agents into
role presets.

Additive and idempotent: the seed runs only while no agent is flagged, so an owner edit
stands. Chained before the wording-layer migrations (pdyn_0002, pdyn_0003), which render
`{{agents}}`.

Revision ID: pdyn_0006_agents_in_prompt
Revises: pdyn_0005_access_level_order
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "pdyn_0006_agents_in_prompt"
down_revision = "pdyn_0005_access_level_order"
branch_labels = None
depends_on = None

#: The owner's agents, in his order (production text of 1 Oct 2026, line 774).
PROMPT_AGENTS: tuple[str, ...] = (
    "general_enquiries", "order_enquiries", "incoming_stock_enquiries", "marketing_form", "it_support",
)


def apply(bind) -> None:
    bind.execute(
        sa.text("ALTER TABLE access_agents ADD COLUMN IF NOT EXISTS in_parser_prompt boolean NOT NULL DEFAULT false")
    )
    bind.execute(
        sa.text(
            "UPDATE access_agents SET in_parser_prompt = true WHERE code = ANY(:codes) "
            "AND NOT EXISTS (SELECT 1 FROM access_agents WHERE in_parser_prompt)"
        ),
        {"codes": list(PROMPT_AGENTS)},
    )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    op.get_bind().execute(sa.text("ALTER TABLE access_agents DROP COLUMN IF EXISTS in_parser_prompt"))
