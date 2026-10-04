"""CHATBOT-SELFREF-SCOPE B1: the n8n integration role may read the sales reports.

Revision ID: selfref_0001_n8n_sales_view
Revises: sotr_0001_so_is_transferable
Create Date: 2026-09-30

Production, 30 Sep 2026: "what is my sales this month" picked `crm_sales_analysis`, whose
route (`app/api/v1/sales/analysis.py`) is gated on `sales.reports.view` for the act-as
principal of the caller's API key. `sales_s1_reports_module` granted that slug to
`superadmin` and `admin` only, so the n8n key's principal (role `integration_n8n`) was
answered 403 and the customer read "Could not run the sales report right now."
`375_scm_proforma_invoice` is the precedent the other way round (an integration role
deliberately excluded from an OPERATOR permission); this one is a READ the chatbot makes on
the contact's behalf, and the contact's own reveal key stays the gate on the data.

Idempotent: the permission row is created if a create_all database lacks it, and the grant
is `ON CONFLICT DO NOTHING`. `scripts.bootstrap_env` seeds the same grant for a database
built without migration bodies (CI).
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "selfref_0001_n8n_sales_view"
down_revision = "sotr_0001_so_is_transferable"
branch_labels = None
depends_on = None

SLUG = "sales.reports.view"
ROLE = "integration_n8n"


def grant_sales_reports_view(bind) -> None:
    """Grant `sales.reports.view` to `integration_n8n`. Shared with `scripts.bootstrap_env`."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    bind.execute(
        sa.text(
            "INSERT INTO user_permissions (id, slug, name, description, created_at) "
            "VALUES (:id, :slug, 'View sales reports', "
            "'Open the sales reports (Yearly comparison, Sales report), export them and ask "
            "for them on WhatsApp.', :now) ON CONFLICT (slug) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "slug": SLUG, "now": now},
    )
    bind.execute(
        sa.text(
            "INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at) "
            "SELECT gen_random_uuid()::text, r.id, p.id, :now "
            "  FROM user_roles r CROSS JOIN user_permissions p "
            " WHERE r.slug = :role AND p.slug = :slug "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"role": ROLE, "slug": SLUG, "now": now},
    )


def upgrade() -> None:
    grant_sales_reports_view(op.get_bind())


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM user_role_permissions rp USING user_roles r, user_permissions p "
            " WHERE rp.role_id = r.id AND rp.permission_id = p.id "
            "   AND r.slug = :role AND p.slug = :slug"
        ),
        {"role": ROLE, "slug": SLUG},
    )
