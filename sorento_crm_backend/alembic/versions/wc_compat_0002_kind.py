"""MIRROR-LINKS: `kind` on `product_compatibility` (seat_cover | mirror | shelf).

Additive. Existing rows stay `seat_cover`. The unique pair index becomes (accessory, host,
kind) so the same two products can be linked once per kind. Idempotent (`IF NOT EXISTS`,
guarded constraint) so the shared dev DB, which converges through `create_all`, can take
it in any order.
Plan: documentation/plans/products/PLAN-mirror-shelf-links-14oct.md.

Revision ID: wc_compat_0002_kind
Revises: frc_0001_debtor_type_no_cs
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "wc_compat_0002_kind"
down_revision = "frc_0001_debtor_type_no_cs"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    "ALTER TABLE product_compatibility "
    "ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'seat_cover'",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_product_compatibility_kind'
        ) THEN
            ALTER TABLE product_compatibility ADD CONSTRAINT ck_product_compatibility_kind
                CHECK (kind IN ('seat_cover', 'mirror', 'shelf'));
        END IF;
    END
    $$
    """,
    "DROP INDEX IF EXISTS uq_product_compatibility_pair",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair_kind "
    "ON product_compatibility (seat_cover_product_id, wc_product_id, kind)",
)

DOWNGRADE_SQL = (
    "DELETE FROM product_compatibility WHERE kind <> 'seat_cover'",
    "DROP INDEX IF EXISTS uq_product_compatibility_pair_kind",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_product_compatibility_pair "
    "ON product_compatibility (seat_cover_product_id, wc_product_id)",
    "ALTER TABLE product_compatibility DROP CONSTRAINT IF EXISTS ck_product_compatibility_kind",
    "ALTER TABLE product_compatibility DROP COLUMN IF EXISTS kind",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    bind = op.get_bind()
    for statement in DOWNGRADE_SQL:
        bind.execute(sa.text(statement))
