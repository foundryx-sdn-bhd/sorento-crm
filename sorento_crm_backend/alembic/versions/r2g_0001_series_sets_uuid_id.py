"""RELEASE2-GUARDS: give `projects.series_sets` a uuid `id`.

`series_sets` (sql_0001_set_quote_line) carries prices of its own, so it is not a pure
junction and tests/test_schema_uuid_id_principle.py requires a uuid `id` primary key. The
(series_id, product_set_id) pair keeps one price per series and set through a unique index
in place of the old composite primary key.

Idempotent (`IF NOT EXISTS`, guarded key swap) so the shared dev DB, which converges
through `create_all` and may already hold the new column, can take it in any order.

Revision ID: r2g_0001_series_sets_uuid_id
Revises: wc_compat_0002_kind
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "r2g_0001_series_sets_uuid_id"
down_revision = "wc_compat_0002_kind"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    "ALTER TABLE projects.series_sets "
    "ADD COLUMN IF NOT EXISTS id uuid NOT NULL DEFAULT gen_random_uuid()",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_series_sets_pair "
    "ON projects.series_sets (series_id, product_set_id)",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = 'projects.series_sets'::regclass
              AND i.indisprimary
              AND a.attname = 'id'
              AND i.indnatts = 1
        ) THEN
            ALTER TABLE projects.series_sets DROP CONSTRAINT IF EXISTS series_sets_pkey;
            ALTER TABLE projects.series_sets ADD CONSTRAINT series_sets_pkey PRIMARY KEY (id);
        END IF;
    END
    $$
    """,
)

DOWNGRADE_SQL = (
    "ALTER TABLE projects.series_sets DROP CONSTRAINT IF EXISTS series_sets_pkey",
    "ALTER TABLE projects.series_sets "
    "ADD CONSTRAINT series_sets_pkey PRIMARY KEY (series_id, product_set_id)",
    "DROP INDEX IF EXISTS projects.uq_project_series_sets_pair",
    "ALTER TABLE projects.series_sets DROP COLUMN IF EXISTS id",
)


def upgrade() -> None:
    bind = op.get_bind()
    for statement in UPGRADE_SQL:
        bind.execute(sa.text(statement))


def downgrade() -> None:
    bind = op.get_bind()
    for statement in DOWNGRADE_SQL:
        bind.execute(sa.text(statement))
