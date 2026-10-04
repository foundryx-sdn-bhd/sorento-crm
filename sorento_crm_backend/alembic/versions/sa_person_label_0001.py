"""Fill sales_agents.person_label where empty, from the code minus its trailing level

CUSTOMER-SALES-AGENT (owner, 4 Oct 2026): `AGENT-A I` and `AGENT-A III` are one person, and the
customer group screen compares agents by that person. Additive data-only: only NULL or blank
labels are filled, a label staff typed is never touched. The regex is copied from
`sales_agent_service.derive_person_label` because a migration must not import app services.

Revision ID: sa_person_label_0001
Revises: cbs_0001_contact_brand_ids
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "sa_person_label_0001"
down_revision = "cbs_0001_contact_brand_ids"
branch_labels = None
depends_on = None

_LEVEL_SUFFIX = re.compile(r"[\s-]+(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, sales_agent FROM sales_agents "
            "WHERE person_label IS NULL OR btrim(person_label) = ''"
        )
    ).fetchall()
    for agent_id, code in rows:
        label = _LEVEL_SUFFIX.sub("", (code or "").strip().upper()).rstrip(" -")
        if label:
            bind.execute(
                sa.text("UPDATE sales_agents SET person_label = :l WHERE id = :i"),
                {"l": label, "i": agent_id},
            )


def downgrade() -> None:
    # No-op: a derived label cannot be told apart from one staff typed, so clearing would
    # destroy real data.
    pass
