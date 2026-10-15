"""Chat fix PR-A: the class meanings are registry data.

The words each product class answers to ("sink" is a Kitchen Sink, "tandas" a Water Closet,
"whirlpool" a Jacuzzi) lived in the code list `product_class_signal.CLASS_SYNONYMS`, and its
Jacuzzi words never applied (no category is labelled Jacuzzi). They now live on the
registry's `class` row, the same `synonyms` column `SPEC_REGISTRY_SEED` ships them in, so the
parser's `{{specs}}` block, the class vocabulary and staff on Product Specifications read one
list (AC-A2, AC-A3; owner ruling 16 Oct 2026: "Jacuzzi is like bathtub with massage jet").

Idempotent and additive: the words are merged into the seed-owned `synonyms` of a
seed-sourced `class` row (`||`, keys this file does not name are kept), and the statement
writes nothing once they are there. A row staff took ownership of (`source != 'seed'`) is
left alone, as the seed repair leaves it. No downgrade body: removing words the catalogue
understands would only make an installed database understand less.

The same statements ship as the crew SQL at `crew/state/migrations/CHAT-ADVISOR-SPIKE.sql`.
Plan: documentation/plans/chatbot/PLAN-chat-fix-spec-16oct.md.

Revision ID: cfs_0001_spec_registry_seed
Revises: sds_0001_stock_debt_snapshots
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "cfs_0001_spec_registry_seed"
down_revision = "sds_0001_stock_debt_snapshots"
branch_labels = None
depends_on = None

#: `SPEC_REGISTRY_SEED`'s `class` synonyms as this revision ships them.
CLASS_MEANINGS: dict[str, list[str]] = {
    "Kitchen Sink": ["kitchen sink", "sink", "sinki", "dapur"],
    "Tap": ["tap", "faucet", "mixer", "paip", "kepala paip", "water tap"],
    "Wash Basin": ["wash basin", "basin", "washbasin", "sink basin", "besen", "water basin"],
    "Water Closet": ["water closet", "wc", "toilet", "toilet bowl", "closet", "tandas"],
    "Shower": ["shower", "shower head", "rain shower", "hand shower", "pancuran"],
    "Bathroom Furniture": ["cabinet", "mirror", "vanity", "bathroom cabinet", "kabinet"],
    "Bathtub": ["bathtub", "bath tub", "tub"],
    "Jacuzzi": ["jacuzzi", "bathtub with massage jets", "whirlpool", "spa bath", "按摩浴缸"],
    "Bathtub and Jacuzzi": ["bathtub", "bath tub", "jacuzzi", "tub", "shower screen"],
    "Bathroom Accessory": [
        "bathroom accessory", "towel rail", "towel bar", "soap holder", "paper holder", "robe hook",
    ],
    "Cloth Hanger": ["cloth hanger", "clothes hanger", "hanger", "penyidai"],
    "Urinal": ["urinal", "urinary"],
    "Flexible Hose": ["flexible hose", "hose", "shower tube", "hos"],
}


def _literal(value) -> str:
    """A JSON value as a SQL string literal (the crew SQL needs the statements whole)."""
    return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"


def statements() -> list[str]:
    """The seed, as idempotent SQL: one statement per registry change."""
    meanings = _literal(CLASS_MEANINGS)
    return [
        "UPDATE product_spec_registry"
        f" SET synonyms = synonyms || {meanings}, updated_at = now()"
        " WHERE spec_key = 'class' AND source = 'seed'"
        f" AND NOT (synonyms @> {meanings})",
    ]


def upgrade() -> None:
    bind = op.get_bind()
    for statement in statements():
        bind.execute(sa.text(statement))


def downgrade() -> None:
    """Nothing to undo: see the docstring."""
