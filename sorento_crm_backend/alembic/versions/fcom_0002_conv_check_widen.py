"""Form comments: widen entity_conversation_messages CHECKs for the form thread

Revision ID: fcom_0002_conv_check_widen
Revises: fcom_0001_conv_author_contact
Create Date: 2026-10-09 04:10:00.000000

`PLAN-form-comments-09oct.md`. Migration 150 limited the table to the commercial entity
types and the activity / internal_note scopes. The form comment thread writes
entity_type = the form kind and scope = form_comment, so every comment failed the CHECK on
a migrated database (owner hand test, 9 Oct). Additive in effect: every value allowed
before is still allowed. Downgrade restores 150's constraints and fails if form comments
exist, which is the honest outcome (they would violate them).
"""
from alembic import op

revision = "fcom_0002_conv_check_widen"
down_revision = "fcom_0001_conv_author_contact"
branch_labels = None
depends_on = None

TABLE = "entity_conversation_messages"
CK_TYPE = "ck_entity_conversation_messages_entity_type"
CK_SCOPE = "ck_entity_conversation_messages_scope"

_COMMERCIAL = (
    "'commercial_project','commercial_master_quotation','commercial_project_task',"
    "'commercial_master_quotation_task','commercial_lead'"
)
_FORMS = "'complaint','stock_inquiry','purchase_request','sponsorship_form'"


def _replace(type_values: str, scope_values: str) -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CK_TYPE}")
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CK_SCOPE}")
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CK_TYPE} CHECK (entity_type IN ({type_values}))")
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT {CK_SCOPE} CHECK (scope IN ({scope_values}))")


def upgrade() -> None:
    _replace(f"{_COMMERCIAL},{_FORMS}", "'activity','internal_note','form_comment'")


def downgrade() -> None:
    _replace(_COMMERCIAL, "'activity','internal_note'")
