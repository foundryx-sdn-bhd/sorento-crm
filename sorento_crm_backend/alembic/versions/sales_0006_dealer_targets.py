"""Sales module: dealer targets on the one target engine (lane SALES-TARGET-DEALER).

Owner rulings 16 Oct 00:10 (one engine for every subject; Q1 rounding; Q4 points; Q5 stacking
additional lines; a payout account apart from the counted accounts) and crew A1/A2.

1. `sales.targets`: `subject_kind` may be `dealer` (no agent, no team); `payout_customer_id`
   (customers, SET NULL, dealers only); `threshold_unit` (`pct` default | `amount` | `points`);
   `rounding` (`sen` default | `ringgit`); `tiers_per_period` (false default). Existing rows
   take the defaults, so every agent and team target reads exactly as before.
2. `sales.target_commission_tiers`: `from_pct` nullable, `threshold` numeric(15,2), exactly one
   of the two (existing rows all carry `from_pct`); `is_additional` (false); `period_start`
   date. The unique `(target_id, from_pct)` index becomes a plain `target_id` index: per-period
   and additional tiers repeat a figure, and `target_service` checks duplicates per period.
3. New `sales.target_accounts`, `sales.target_tier_rates`, `sales.point_ratios`.

Downgrade drops the dealer targets (and their accounts, rates) and the new tiers, then the
columns and tables; the original tier index comes back.

Revision ID: sales_0006_dealer_targets
Revises: sds_0001_stock_debt_snapshots
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.sales import translated_schema

revision = "sales_0006_dealer_targets"
down_revision = "sds_0001_stock_debt_snapshots"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=False)


def upgrade() -> None:
    # ALTER TABLE ignores `schema_translate_map`, so the schema is named explicitly (sales_0004).
    schema = translated_schema(op.get_bind())

    # 1. targets
    op.add_column(
        "targets",
        sa.Column(
            "payout_customer_id",
            _UUID,
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=schema,
    )
    op.add_column(
        "targets",
        sa.Column("threshold_unit", sa.String(8), nullable=False, server_default=sa.text("'pct'")),
        schema=schema,
    )
    op.add_column(
        "targets",
        sa.Column("rounding", sa.String(8), nullable=False, server_default=sa.text("'sen'")),
        schema=schema,
    )
    op.add_column(
        "targets",
        sa.Column(
            "tiers_per_period", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema=schema,
    )
    # Crew ruling 16 Oct (owner ruling 4): an account's own target is replaced by its
    # dealer's target once that one reaches a tier in the same period.
    op.add_column(
        "targets",
        sa.Column(
            "replaced_by_target_id",
            _UUID,
            sa.ForeignKey(f"{schema}.targets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=schema,
    )
    op.drop_constraint("ck_sales_targets_subject_kind", "targets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_sales_targets_subject_kind",
        "targets",
        "subject_kind IN ('agent', 'team', 'dealer')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_sales_targets_replaced_by_dealer",
        "targets",
        "replaced_by_target_id IS NULL OR subject_kind = 'dealer'",
        schema=schema,
    )
    op.drop_constraint("ck_sales_targets_subject", "targets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_sales_targets_subject",
        "targets",
        "(subject_kind = 'agent' AND sales_agent_id IS NOT NULL AND sales_team_id IS NULL) "
        "OR (subject_kind = 'team' AND sales_team_id IS NOT NULL AND sales_agent_id IS NULL) "
        "OR (subject_kind = 'dealer' AND sales_agent_id IS NULL AND sales_team_id IS NULL)",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_sales_targets_threshold_unit",
        "targets",
        "threshold_unit IN ('pct', 'amount', 'points')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_sales_targets_rounding", "targets", "rounding IN ('sen', 'ringgit')", schema=schema
    )
    op.create_check_constraint(
        "ck_sales_targets_payout_dealer",
        "targets",
        "payout_customer_id IS NULL OR subject_kind = 'dealer'",
        schema=schema,
    )

    # 2. tiers
    op.alter_column("target_commission_tiers", "from_pct", nullable=True, schema=schema)
    op.add_column(
        "target_commission_tiers",
        sa.Column("threshold", sa.Numeric(15, 2), nullable=True),
        schema=schema,
    )
    op.add_column(
        "target_commission_tiers",
        sa.Column("is_additional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=schema,
    )
    op.add_column(
        "target_commission_tiers", sa.Column("period_start", sa.Date(), nullable=True), schema=schema
    )
    op.drop_constraint(
        "ck_sales_target_commission_tiers_from_pct",
        "target_commission_tiers",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_sales_target_commission_tiers_from_pct",
        "target_commission_tiers",
        "from_pct IS NULL OR from_pct >= 0",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_sales_target_commission_tiers_threshold",
        "target_commission_tiers",
        "threshold IS NULL OR threshold >= 0",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_sales_target_commission_tiers_one_threshold",
        "target_commission_tiers",
        "num_nonnulls(from_pct, threshold) = 1",
        schema=schema,
    )
    op.drop_index(
        "uq_sales_target_commission_tiers_target_from", "target_commission_tiers", schema=schema
    )
    op.create_index(
        "ix_sales_target_commission_tiers_target_id",
        "target_commission_tiers",
        ["target_id"],
        schema=schema,
    )

    # 3. new tables
    op.create_table(
        "target_accounts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "target_id", _UUID, sa.ForeignKey("sales.targets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "customer_id", _UUID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        schema="sales",
    )
    op.create_index(
        "ix_sales_target_accounts_company_id", "target_accounts", ["company_id"], schema="sales"
    )
    op.create_index(
        "ix_sales_target_accounts_customer_id", "target_accounts", ["customer_id"], schema="sales"
    )
    op.create_index(
        "uq_sales_target_accounts_target_customer",
        "target_accounts",
        ["target_id", "customer_id"],
        unique=True,
        schema="sales",
    )

    op.create_table(
        "target_tier_rates",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column(
            "tier_id",
            _UUID,
            sa.ForeignKey("sales.target_commission_tiers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id", _UUID, sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rate", sa.Numeric(9, 4), nullable=False),
        sa.CheckConstraint("rate >= 0", name="ck_sales_target_tier_rates_rate"),
        schema="sales",
    )
    op.create_index(
        "ix_sales_target_tier_rates_company_id", "target_tier_rates", ["company_id"], schema="sales"
    )
    op.create_index(
        "uq_sales_target_tier_rates_tier_customer",
        "target_tier_rates",
        ["tier_id", "customer_id"],
        unique=True,
        schema="sales",
    )

    op.create_table(
        "point_ratios",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("company_id", _UUID, sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("rm_per_point", sa.Numeric(12, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rm_per_point > 0", name="ck_sales_point_ratios_positive"),
        schema="sales",
    )
    op.create_index(
        "ix_sales_point_ratios_company_id", "point_ratios", ["company_id"], schema="sales"
    )
    op.create_index(
        "uq_sales_point_ratios_company_from",
        "point_ratios",
        ["company_id", "effective_from"],
        unique=True,
        schema="sales",
    )


def downgrade() -> None:
    schema = translated_schema(op.get_bind())
    op.drop_table("point_ratios", schema="sales")
    op.drop_table("target_tier_rates", schema="sales")
    op.drop_table("target_accounts", schema="sales")

    targets = sa.table("targets", sa.column("subject_kind"), schema=schema)
    tiers = sa.table(
        "target_commission_tiers",
        sa.column("from_pct"),
        sa.column("period_start"),
        sa.column("is_additional"),
        schema=schema,
    )
    op.execute(targets.delete().where(targets.c.subject_kind == "dealer"))
    op.execute(
        tiers.delete().where(
            sa.or_(
                tiers.c.from_pct.is_(None),
                tiers.c.period_start.isnot(None),
                tiers.c.is_additional.is_(True),
            )
        )
    )
    op.drop_index(
        "ix_sales_target_commission_tiers_target_id", "target_commission_tiers", schema=schema
    )
    op.create_index(
        "uq_sales_target_commission_tiers_target_from",
        "target_commission_tiers",
        ["target_id", "from_pct"],
        unique=True,
        schema=schema,
    )
    for name in (
        "ck_sales_target_commission_tiers_one_threshold",
        "ck_sales_target_commission_tiers_threshold",
        "ck_sales_target_commission_tiers_from_pct",
    ):
        op.drop_constraint(name, "target_commission_tiers", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_sales_target_commission_tiers_from_pct",
        "target_commission_tiers",
        "from_pct >= 0",
        schema=schema,
    )
    op.drop_column("target_commission_tiers", "period_start", schema=schema)
    op.drop_column("target_commission_tiers", "is_additional", schema=schema)
    op.drop_column("target_commission_tiers", "threshold", schema=schema)
    op.alter_column("target_commission_tiers", "from_pct", nullable=False, schema=schema)

    for name in (
        "ck_sales_targets_replaced_by_dealer",
        "ck_sales_targets_payout_dealer",
        "ck_sales_targets_rounding",
        "ck_sales_targets_threshold_unit",
        "ck_sales_targets_subject",
        "ck_sales_targets_subject_kind",
    ):
        op.drop_constraint(name, "targets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_sales_targets_subject_kind", "targets", "subject_kind IN ('agent', 'team')", schema=schema
    )
    op.create_check_constraint(
        "ck_sales_targets_subject",
        "targets",
        "(subject_kind = 'agent' AND sales_agent_id IS NOT NULL AND sales_team_id IS NULL) "
        "OR (subject_kind = 'team' AND sales_team_id IS NOT NULL AND sales_agent_id IS NULL)",
        schema=schema,
    )
    op.drop_column("targets", "tiers_per_period", schema=schema)
    op.drop_column("targets", "rounding", schema=schema)
    op.drop_column("targets", "threshold_unit", schema=schema)
    op.drop_column("targets", "payout_customer_id", schema=schema)
    op.drop_column("targets", "replaced_by_target_id", schema=schema)
