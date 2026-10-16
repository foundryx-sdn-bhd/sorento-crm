"""Name the company "Sorento" in the email theme where System Settings still carries the
template's "Metronic" (PR 1622 owner hand test: every mail's footer read "You received this
email because you have an account on Metronic").

Narrow on purpose: only the email theme's own `company_name` override is written, and only
when System Settings still says "Metronic" and no admin has named a company on the Email
Theme screen. `system_settings.name` itself is left alone (other screens read it; the
reports engine already works around the same value). Idempotent: a second run finds the
override already set and writes nothing.

Revision ID: oirj_0002_theme_sorento
Revises: oirj_0001_seed_reject
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "oirj_0002_theme_sorento"
down_revision = "oirj_0001_seed_reject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE system_settings
            -- A NULL column and a JSON `null` (what an ORM None writes) both start empty.
            SET email_theme = (CASE WHEN jsonb_typeof(email_theme) = 'object'
                                    THEN email_theme ELSE '{}'::jsonb END)
                              || jsonb_build_object('company_name', 'Sorento')
            WHERE name = 'Metronic'
              AND COALESCE(email_theme ->> 'company_name', '') = ''
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE system_settings
            SET email_theme = email_theme - 'company_name'
            WHERE name = 'Metronic'
              AND email_theme ->> 'company_name' = 'Sorento'
            """
        )
    )
