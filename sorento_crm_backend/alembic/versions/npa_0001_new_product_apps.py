"""NEW-PRODUCT-APP: new product applications, their lines and four permissions.

Additive only and idempotent (`IF NOT EXISTS`, `WHERE NOT EXISTS`) so the shared dev DB, which
converges through `create_all`, can take it in any order. Grants follow `sr_0001_sample_requests`:
all four go to the admin roles (a permission granted to nobody reads as a broken feature);
`create` is also copied to whoever holds `procurement.stock_inquiries.edit` (the people who
work stock inquiries today). An admin grants `approve` to the approvers and `open_code` to
purchasing; the role names are not in the repo.

Card: documentation/plans/procurement/CARD-new-product-application-17oct.md.

Revision ID: npa_0001_new_product_apps
Revises: sr_0001_sample_requests
"""
import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "npa_0001_new_product_apps"
down_revision = "sr_0001_sample_requests"
branch_labels = None
depends_on = None

_PERMS = (
    (
        "procurement.new_product_applications.view",
        "View New Product Applications",
        "See every new product application of your companies, not only your own.",
    ),
    (
        "procurement.new_product_applications.create",
        "Create New Product Applications",
        "Raise, submit, revise and cancel your own new product applications.",
    ),
    (
        "procurement.new_product_applications.approve",
        "Approve New Product Applications",
        "Approve or reject a submitted new product application, and revise one.",
    ),
    (
        "procurement.new_product_applications.open_code",
        "Open Codes for New Product Applications",
        "Link each line of an approved application to the product AutoCount sent, or close it.",
    ),
)
_COPY_CREATE_FROM = "procurement.stock_inquiries.edit"
_ADMIN_ROLES = ("superadmin", "admin")


def upgrade() -> None:
    bind = op.get_bind()
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS new_product_applications (
            id UUID PRIMARY KEY,
            company_id UUID NOT NULL REFERENCES companies(id),
            application_no VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            source VARCHAR(10) NOT NULL DEFAULT 'staff',
            applicant_id VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
            contact_id TEXT REFERENCES respond_contacts(id) ON DELETE SET NULL,
            space_id TEXT,
            stock_inquiry_id UUID REFERENCES stock_inquiries(id) ON DELETE SET NULL,
            customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
            customer_name VARCHAR(255),
            project_name VARCHAR(255),
            needed_by DATE,
            remark TEXT,
            submitted_at TIMESTAMP,
            decided_at TIMESTAMP,
            decided_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
            reject_reason TEXT,
            revise_reason TEXT,
            cancelled_at TIMESTAMP,
            cancel_reason TEXT,
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP NOT NULL DEFAULT now(),
            CONSTRAINT uq_new_product_application_no UNIQUE (company_id, application_no),
            CONSTRAINT ck_new_product_applications_status CHECK (
                status IN ('draft','submitted','approved','rejected','completed','cancelled')),
            CONSTRAINT ck_new_product_applications_source CHECK (source IN ('staff','portal'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_new_product_applications_company_id "
        "ON new_product_applications (company_id)",
        "CREATE INDEX IF NOT EXISTS ix_new_product_applications_status "
        "ON new_product_applications (status)",
        "CREATE INDEX IF NOT EXISTS ix_new_product_applications_applicant "
        "ON new_product_applications (applicant_id)",
        "CREATE INDEX IF NOT EXISTS ix_new_product_applications_contact "
        "ON new_product_applications (contact_id)",
        "CREATE INDEX IF NOT EXISTS ix_new_product_applications_inquiry "
        "ON new_product_applications (stock_inquiry_id)",
        """
        CREATE TABLE IF NOT EXISTS new_product_application_lines (
            id UUID PRIMARY KEY,
            application_id UUID NOT NULL REFERENCES new_product_applications(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL,
            proposed_code VARCHAR(255),
            description TEXT,
            further_description TEXT,
            category_id UUID REFERENCES product_categories(id) ON DELETE SET NULL,
            base_uom_id UUID REFERENCES units_of_measure(id) ON DELETE SET NULL,
            brand_id UUID REFERENCES brands(id) ON DELETE SET NULL,
            item_type_id UUID REFERENCES item_types(id) ON DELETE SET NULL,
            supplier_id UUID REFERENCES suppliers(id) ON DELETE SET NULL,
            supplier_item_code VARCHAR(100),
            cost_price NUMERIC(12,2),
            selling_price NUMERIC(12,2),
            expected_qty NUMERIC(12,3),
            remark TEXT,
            opened_product_id UUID REFERENCES products(id) ON DELETE RESTRICT,
            closed_reason TEXT,
            resolved_by VARCHAR(100) REFERENCES users(id) ON DELETE SET NULL,
            resolved_at TIMESTAMP,
            CONSTRAINT ck_new_product_application_lines_one_outcome
                CHECK (opened_product_id IS NULL OR closed_reason IS NULL),
            CONSTRAINT ck_new_product_application_lines_cost
                CHECK (cost_price IS NULL OR cost_price >= 0),
            CONSTRAINT ck_new_product_application_lines_selling
                CHECK (selling_price IS NULL OR selling_price >= 0),
            CONSTRAINT ck_new_product_application_lines_qty
                CHECK (expected_qty IS NULL OR expected_qty > 0)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_new_product_application_lines_application "
        "ON new_product_application_lines (application_id)",
        "CREATE INDEX IF NOT EXISTS ix_new_product_application_lines_product "
        "ON new_product_application_lines (opened_product_id)",
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

    # create: copied from the holders of procurement.stock_inquiries.edit.
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
            SELECT gen_random_uuid()::text, urp.role_id, tgt.id, now()
              FROM user_role_permissions urp
              JOIN user_permissions src ON src.id = urp.permission_id
             CROSS JOIN user_permissions tgt
             WHERE src.slug = :src
               AND tgt.slug = 'procurement.new_product_applications.create'
               AND NOT EXISTS (
                   SELECT 1 FROM user_role_permissions existing
                    WHERE existing.role_id = urp.role_id
                      AND existing.permission_id = tgt.id
               )
            """
        ),
        {"src": _COPY_CREATE_FROM},
    )

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
    slugs = [slug for slug, _n, _d in _PERMS]
    bind.execute(
        sa.text(
            "DELETE FROM user_role_permissions WHERE permission_id IN "
            "(SELECT id FROM user_permissions WHERE slug = ANY(:slugs))"
        ),
        {"slugs": slugs},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = ANY(:slugs)"), {"slugs": slugs})
    bind.execute(sa.text("DROP TABLE IF EXISTS new_product_application_lines"))
    bind.execute(sa.text("DROP TABLE IF EXISTS new_product_applications"))
