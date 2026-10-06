"""Contact regions: respond_contacts.regions (CONTACT-REGION).

Additive. Existing contacts take the column default (West only) as the column is added.

Revision ID: creg_0001_contact_regions
Revises: pdyn_0003_prod_identical
"""
from alembic import op

revision = "creg_0001_contact_regions"
down_revision = "pdyn_0003_prod_identical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE respond_contacts ADD COLUMN IF NOT EXISTS regions text[] NOT NULL DEFAULT '{west}'")
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_respond_contacts_regions') THEN
            ALTER TABLE respond_contacts ADD CONSTRAINT ck_respond_contacts_regions
              CHECK (cardinality(regions) >= 1 AND regions <@ ARRAY['west','east']::text[]);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE respond_contacts DROP CONSTRAINT IF EXISTS ck_respond_contacts_regions")
    op.execute("ALTER TABLE respond_contacts DROP COLUMN IF EXISTS regions")
