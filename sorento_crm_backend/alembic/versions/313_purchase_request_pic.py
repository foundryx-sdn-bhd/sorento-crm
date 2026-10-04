"""Add PIC (person in charge) to purchase requests / sponsorship forms.

There was nowhere on the form to record the person receiving the delivery, so
staff typed them onto the end of the delivery address:

    2, Lebuh Cecil, Ghaut, 10300 George Town, Pulau Pinang Contact: CONTACT Q (012-000 0502)

which makes the address two facts in one column - unreadable programmatically and
easy to miss when scanning the printed form.

Deliberately ONE free-text column rather than pic_name + pic_phone: real values
are messy ("CONTACT Q (012-000 0502)", "CONTACT Q / CONTACT R 012-0000523", a name with no
number at all), nothing downstream parses it, and the field is explicitly
optional - two required-shaped columns would just invite empty halves. The
structured path already exists as ``requested_by_contact_id``.

No backfill: historical rows keep their contact embedded in the address. Parsing
it back out is guesswork and would silently corrupt addresses it got wrong.

Revision ID: 313_purchase_request_pic
Revises: 312_certificate_register
"""
from alembic import op

revision = "313_purchase_request_pic"
down_revision = "312_certificate_register"
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS: this database is shared by several worktrees, so the column
    # may already be present from another branch's run.
    op.execute("ALTER TABLE purchase_requests ADD COLUMN IF NOT EXISTS pic TEXT")


def downgrade():
    op.execute("ALTER TABLE purchase_requests DROP COLUMN IF EXISTS pic")
