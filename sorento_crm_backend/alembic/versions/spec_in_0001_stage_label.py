"""Call the project funnel's "Specified" rung "Spec In", and let it step back to Registered.

Owner hand-test, 13 Oct: the stage is "Spec In" (spec = specification, the trade's own
phrase; migration 324 already renamed the edge into it to "Spec in"), and a project moved
to Spec In by mistake had no way back - the only backward edge into Registered's
neighbourhood was Quoted -> Spec In. The seed writes labels and edges only when it creates
the funnel, so existing installs need this.

Matched on the seeded wording, so an admin who already renamed the rung or the edge keeps
their own words. The back edge is added only on the default graph (scope_id NULL), only
where both rungs exist, and only once, so re-running changes nothing.

Revision ID: spec_in_0001_stage_label
Revises: mig_idx_chunks_document_id
"""
from alembic import op
import sqlalchemy as sa


revision = "spec_in_0001_stage_label"
down_revision = "mig_idx_chunks_document_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "update statuses set label = 'Spec In' "
            "where entity_type = 'project' and key = 'specified' and label = 'Specified'"
        )
    )
    op.execute(
        sa.text(
            "update status_transitions set label = 'Spec In' "
            "where entity_type = 'project' and label = 'Spec in'"
        )
    )
    op.execute(
        sa.text(
            "insert into status_transitions "
            "(id, entity_type, scope_id, from_status_id, to_status_id, label, sort_order, "
            " trigger_mode, created_at, updated_at) "
            "select gen_random_uuid(), 'project', null, s.id, r.id, 'Back to registered', 0, "
            "       'manual', now(), now() "
            "from statuses s join statuses r "
            "  on r.entity_type = 'project' and r.scope_id is null and r.key = 'registered' "
            "where s.entity_type = 'project' and s.scope_id is null and s.key = 'specified' "
            "  and not exists (select 1 from status_transitions t "
            "                  where t.entity_type = 'project' and t.scope_id is null "
            "                    and t.from_status_id = s.id and t.to_status_id = r.id)"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "delete from status_transitions "
            "where entity_type = 'project' and scope_id is null and label = 'Back to registered'"
        )
    )
    op.execute(
        sa.text(
            "update status_transitions set label = 'Spec in' "
            "where entity_type = 'project' and label = 'Spec In'"
        )
    )
    op.execute(
        sa.text(
            "update statuses set label = 'Specified' "
            "where entity_type = 'project' and key = 'specified' and label = 'Spec In'"
        )
    )
