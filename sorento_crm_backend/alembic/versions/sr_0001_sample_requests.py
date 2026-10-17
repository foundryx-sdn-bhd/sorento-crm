"""SR-CORE: sample requests (consignment requisition), their lines, two settings switches and
three permissions.

Additive only and idempotent (`IF NOT EXISTS`, `WHERE NOT EXISTS`) so the shared dev DB, which
converges through `create_all`, can take it in any order. Permission grants follow
`362_promotion_type_permissions`: only `create` goes to whoever already holds
`projects.projects.edit` (every salesperson sees own requests only); `view` and `process` go to
the admin roles, and an admin grants them to the coordinator role (the role name is not in the
repo). A permission granted to nobody would read as a broken feature, which is why admins hold
them.

Plan: documentation/plans/projects/PLAN-sample-request-core-15oct.md.

Revision ID: sr_0001_sample_requests
Revises: ctg_0001_contact_tags
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "sr_0001_sample_requests"
down_revision = "ctg_0001_contact_tags"
branch_labels = None
depends_on = None

_PERMS = (
    ("projects.sample_requests.view", "View Sample Requests", "See every sample request."),
    (
        "projects.sample_requests.create",
        "Create Sample Requests",
        "Raise and submit own sample requests.",
    ),
    (
        "projects.sample_requests.process",
        "Process Sample Requests",
        "Move sample requests through Processing to Delivered and fill the internal fields.",
    ),
)
_COPY_FROM_EDIT = ("projects.sample_requests.create",)
_ADMIN_ROLES = ("superadmin", "admin")


def _schema(bind, module: str) -> str:
    """Where a module's tables live, for RAW SQL, under BOTH substrates (see
    `sds_0001_stock_debt_snapshots`)."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return module if current in (None, "public") else f"{current}_{module}"


def upgrade() -> None:
    bind = op.get_bind()
    p = _schema(bind, "projects")
    for statement in (
        f'CREATE SCHEMA IF NOT EXISTS "{p}"',
        f"""
        CREATE TABLE IF NOT EXISTS "{p}".sample_requests (
            id UUID PRIMARY KEY,
            company_id UUID NOT NULL REFERENCES public.companies(id),
            request_no VARCHAR(20) NOT NULL,
            status_id UUID REFERENCES public.statuses(id) ON DELETE SET NULL,
            project_id UUID REFERENCES "{p}".projects(id) ON DELETE SET NULL,
            project_title VARCHAR(255),
            customer_id UUID REFERENCES public.customers(id) ON DELETE SET NULL,
            customer_name VARCHAR(255),
            salesperson_id VARCHAR(100) REFERENCES public.users(id) ON DELETE SET NULL,
            source VARCHAR(10) NOT NULL DEFAULT 'staff',
            contact_id TEXT REFERENCES public.respond_contacts(id) ON DELETE SET NULL,
            space_id TEXT,
            request_date DATE NOT NULL DEFAULT CURRENT_DATE,
            delivery_date DATE,
            scope_note VARCHAR(255),
            ship_to_name VARCHAR(255),
            ship_to_address TEXT,
            site_contact_name VARCHAR(255),
            site_contact_phone VARCHAR(60),
            site_contact_note VARCHAR(255),
            delivery_mode VARCHAR(20),
            loan_value INTEGER NOT NULL DEFAULT 2,
            loan_unit VARCHAR(10) NOT NULL DEFAULT 'months',
            goods_from VARCHAR(10) NOT NULL DEFAULT 'either',
            purpose TEXT,
            board_mounting BOOLEAN NOT NULL DEFAULT false,
            board_label VARCHAR(255),
            consignment_no VARCHAR(80),
            po_no VARCHAR(80),
            submitted_at TIMESTAMP,
            confirmed_at DATE,
            delivered_at DATE,
            return_due DATE,
            cancelled_at TIMESTAMP,
            cancel_reason TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT uq_sample_request_no UNIQUE (company_id, request_no),
            CONSTRAINT ck_sample_requests_delivery_mode
                CHECK (delivery_mode IS NULL OR delivery_mode IN ('driver','self_deliver','self_collect')),
            CONSTRAINT ck_sample_requests_loan_unit CHECK (loan_unit IN ('days','months')),
            CONSTRAINT ck_sample_requests_goods_from CHECK (goods_from IN ('demo','new','either')),
            CONSTRAINT ck_sample_requests_source CHECK (source IN ('staff','portal')),
            CONSTRAINT ck_sample_requests_loan_value CHECK (loan_value >= 1)
        )
        """,
        f'CREATE INDEX IF NOT EXISTS ix_projects_sample_requests_company_id ON "{p}".sample_requests (company_id)',
        f'CREATE INDEX IF NOT EXISTS ix_sample_requests_status ON "{p}".sample_requests (status_id)',
        f'CREATE INDEX IF NOT EXISTS ix_sample_requests_project ON "{p}".sample_requests (project_id)',
        f'CREATE INDEX IF NOT EXISTS ix_sample_requests_salesperson ON "{p}".sample_requests (salesperson_id)',
        f'CREATE INDEX IF NOT EXISTS ix_sample_requests_contact ON "{p}".sample_requests (contact_id)',
        f"""
        CREATE TABLE IF NOT EXISTS "{p}".sample_request_lines (
            id UUID PRIMARY KEY,
            request_id UUID NOT NULL REFERENCES "{p}".sample_requests(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL,
            product_id UUID NOT NULL REFERENCES public.products(id) ON DELETE RESTRICT,
            qty INTEGER NOT NULL DEFAULT 1,
            remark VARCHAR(255),
            CONSTRAINT ck_sample_request_lines_qty CHECK (qty >= 1)
        )
        """,
        f'CREATE INDEX IF NOT EXISTS ix_sample_request_lines_request ON "{p}".sample_request_lines (request_id)',
        "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS "
        "sample_request_require_project BOOLEAN NOT NULL DEFAULT false",
    ):
        bind.execute(sa.text(statement))

    for slug, name, descr in _PERMS:
        bind.execute(
            sa.text(
                """
                INSERT INTO user_permissions (id, slug, name, description, created_at)
                SELECT gen_random_uuid()::text, :slug, :name, :descr, now()
                WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = :slug)
                """
            ),
            {"slug": slug, "name": name, "descr": descr},
        )

    # create: copied from the holders of projects.projects.edit.
    for slug in _COPY_FROM_EDIT:
        bind.execute(
            sa.text(
                """
                INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
                SELECT gen_random_uuid()::text, urp.role_id, tgt.id, now()
                  FROM user_role_permissions urp
                  JOIN user_permissions src ON src.id = urp.permission_id
                 CROSS JOIN user_permissions tgt
                 WHERE src.slug = 'projects.projects.edit'
                   AND tgt.slug = :slug
                   AND NOT EXISTS (
                       SELECT 1 FROM user_role_permissions existing
                        WHERE existing.role_id = urp.role_id
                          AND existing.permission_id = tgt.id
                   )
                """
            ),
            {"slug": slug},
        )

    # All three go to the admin roles (so `process` is never granted to nobody); an admin
    # grants `process` to the coordinator role.
    for slug, _name, _descr in _PERMS:
        bind.execute(
            sa.text(
                """
                INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
                SELECT gen_random_uuid()::text, r.id, tgt.id, now()
                  FROM user_roles r
                 CROSS JOIN user_permissions tgt
                 WHERE r.slug = ANY(:roles)
                   AND tgt.slug = :slug
                   AND NOT EXISTS (
                       SELECT 1 FROM user_role_permissions existing
                        WHERE existing.role_id = r.id
                          AND existing.permission_id = tgt.id
                   )
                """
            ),
            {"slug": slug, "roles": list(_ADMIN_ROLES)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    p = _schema(bind, "projects")
    slugs = [slug for slug, _n, _d in _PERMS]
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = ANY(:slugs))"
        ),
        {"slugs": slugs},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = ANY(:slugs)"), {"slugs": slugs})
    bind.execute(
        sa.text("ALTER TABLE system_settings DROP COLUMN IF EXISTS sample_request_require_project")
    )
    bind.execute(sa.text(f'DROP TABLE IF EXISTS "{p}".sample_request_lines'))
    bind.execute(sa.text(f'DROP TABLE IF EXISTS "{p}".sample_requests'))
