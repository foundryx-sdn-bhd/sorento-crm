"""Widen three `customers` columns to the lengths the model has always declared.

The model was always the intent; the database had drifted below it. `app/models/order.py`
declares `phone_number` String(50), `industry` String(120) and `website` String(500), and the
live column was `varchar(20)`, `varchar(100)` and `varchar(255)`. Nothing in the codebase
asked for the narrower widths - they are the residue of an earlier shape that a later model
edit never carried into a migration.

Found by a real 4,196-row AutoCount debtor export: 58 rows carry a `Phone 1` longer than 20
characters (`016-000 0503 (MR.CHAEH)`, `95-0000558/013-0000504`). The importer's over-length
pre-check read the MODEL's lengths, so every one of those rows passed the preview and was then
rejected by Postgres at apply time with `StringDataRightTruncation` - a preview promising 0
failures followed by 58 `upsert_error` rows. The importer now reads its limits from the
database (see `customer_import_service.column_limits`) and `tests/test_customers_schema_drift.py`
fails on any future divergence, so this revision resets the clock and the guard keeps it.

Widening only, so it is non-destructive: every value that fits `varchar(20)` fits
`varchar(50)`, and Postgres does not rewrite the table for a varchar length increase.

**The downgrade can truncate.** It narrows the same three columns back, which Postgres refuses
outright if any stored value is longer (and would silently mean data loss if it did not). Run
it only on a database where those columns are known to be within the old widths.

Revision ID: 355_customers_length_drift
Revises: 354_customer_import_permission
"""
import sqlalchemy as sa
from alembic import op

revision = "355_customers_length_drift"
down_revision = "354_customer_import_permission"
branch_labels = None
depends_on = None


#: column -> (drifted length in the database, length the model declares)
_COLUMNS = (
    ("phone_number", 20, 50),
    ("industry", 100, 120),
    ("website", 255, 500),
)


def upgrade() -> None:
    for name, was, now in _COLUMNS:
        op.alter_column(
            "customers",
            name,
            existing_type=sa.String(length=was),
            type_=sa.String(length=now),
            existing_nullable=True,
        )


def downgrade() -> None:
    for name, was, now in _COLUMNS:
        op.alter_column(
            "customers",
            name,
            existing_type=sa.String(length=now),
            type_=sa.String(length=was),
            existing_nullable=True,
        )
