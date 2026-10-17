"""FORMS-PROJECT: link stock inquiries, price tag requests and sales opportunities to a project.

The same nullable-FK shape complaints (318) and purchase requests (315) carry:

- **Nullable.** Most of these forms are not about a registered project, and the link stays
  optional (owner, 17 Oct).
- **ON DELETE SET NULL.** Deleting a project unlinks its forms, it never deletes them.
- **No backfill.** Stock inquiries keep their free-text `project_name` / `project_customer`;
  the owner ruled out matching by name, so only a person ever writes a link.

Additive and idempotent (`IF NOT EXISTS`) so the shared dev DB, which converges through
`create_all`, can take it in any order.

Plan: documentation/plans/forms/PLAN-forms-project-17oct.md.

Revision ID: fp_0001_form_project_links
Revises: sr_0001_sample_requests
"""
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "fp_0001_form_project_links"
down_revision = "sr_0001_sample_requests"
branch_labels = None
depends_on = None

# (module schema or None for the default schema, table, constraint/index stem)
_TABLES = (
    (None, "stock_inquiries", "stock_inquiries"),
    (None, "price_tag_requests", "price_tag_requests"),
    ("sales", "opportunities", "sales_opportunities"),
)


def _schema(bind, module):
    """Where a module's tables live, for RAW SQL, under BOTH substrates (see
    `sr_0001_sample_requests`)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    if module is None:
        return current or "public"
    return module if current in (None, "public") else f"{current}_{module}"


def upgrade() -> None:
    bind = op.get_bind()
    projects = _schema(bind, "projects")
    for module, table, stem in _TABLES:
        schema = _schema(bind, module)
        op.execute(
            f"""
            ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS project_id UUID;

            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_namespace n ON n.oid = c.connamespace
                    WHERE c.conname = 'fk_{stem}_project_id' AND n.nspname = '{schema}'
                ) THEN
                    ALTER TABLE "{schema}".{table}
                        ADD CONSTRAINT fk_{stem}_project_id
                        FOREIGN KEY (project_id) REFERENCES "{projects}".projects (id)
                        ON DELETE SET NULL;
                END IF;
            END $$;

            CREATE INDEX IF NOT EXISTS ix_{stem}_project_id
                ON "{schema}".{table} (project_id);
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    for module, table, stem in _TABLES:
        schema = _schema(bind, module)
        op.execute(
            f"""
            DROP INDEX IF EXISTS "{schema}".ix_{stem}_project_id;
            ALTER TABLE "{schema}".{table} DROP CONSTRAINT IF EXISTS fk_{stem}_project_id;
            ALTER TABLE "{schema}".{table} DROP COLUMN IF EXISTS project_id;
            """
        )
