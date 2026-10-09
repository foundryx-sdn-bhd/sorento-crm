"""Chat local first (lane CHAT-LOCAL-FIRST): media + sender on chat rows, the per-contact
sync state, and the reconcile task.

Revision ID: clf_0001_chat_local_first
Revises: fcom_0003_conv_thread
Create Date: 2026-09-30

`documentation/plans/sla/PLAN-chat-local-first-30sep.md`. Additive only:

1. `chat_histories` gains what the local lane could not render before (R5): the attachment
   (`media_url`, `media_type`, `media_file_name`) and who sent it (`sender_source`,
   `sender_user_id`). All nullable; old rows fill in as the delta / reconcile path re-reads
   them (fill-if-null), never by a one-shot script.
2. `chat_thread_sync_state`, one row per (channel, contact_id): what a `chat_histories` row
   cannot say for itself - the Respond-side watermark the delta read continues from
   (`newest_synced_message_id`, advanced ONLY by a Respond read, never by an n8n / webhook /
   CRM-send row, so a message one of those lanes missed is still behind the watermark and
   gets read), whether the oldest Respond page was reached, when the contact was last
   delta-synced, when it last had activity (what the reconcile selects on), and the last
   sync error. The oldest stored id is read from the rows (a scroll-back continues from
   whatever is oldest locally). Seeded from the last 7 days of `chat_histories` so the first
   reconcile tick after the deploy already covers everyone recently active.
3. A `chat_history_reconcile` row in `scheduled_tasks` (every 5 minutes), the existing
   DB-configured scheduler (`app/scheduler/task_scheduler.py`). Its knobs are the row's
   `metadata` (activity_days, concurrency, batch_limit), editable on the Scheduled Tasks page.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "clf_0001_chat_local_first"
down_revision = "fcom_0003_conv_thread"
branch_labels = None
depends_on = None


_CHAT_COLUMNS = (
    ("media_url", sa.Text()),
    ("media_type", sa.String(32)),
    ("media_file_name", sa.String(512)),
    ("sender_source", sa.String(32)),
    ("sender_user_id", sa.String(64)),
)

TASK_KEY = "chat_history_reconcile"


def upgrade() -> None:
    conn = op.get_bind()

    present = {
        r[0]
        for r in conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'chat_histories'"
            )
        )
    }
    for name, type_ in _CHAT_COLUMNS:
        if name not in present:
            op.add_column("chat_histories", sa.Column(name, type_, nullable=True))

    # A probe rather than `if_not_exists=` on create_table: the shared dev DB converges
    # through create_all and may already hold the table, and requirements.txt allows an
    # alembic older than that keyword.
    exists = conn.execute(sa.text("SELECT to_regclass('chat_thread_sync_state')")).scalar()
    if not exists:
        op.create_table(
            "chat_thread_sync_state",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("channel", sa.String(32), nullable=False),
            sa.Column("contact_id", sa.String(128), nullable=False),
            sa.Column("newest_synced_message_id", sa.String(64), nullable=True),
            sa.Column(
                "oldest_reached", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column("last_activity_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_error_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=False),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=False),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "channel", "contact_id", name="uq_chat_thread_sync_state_contact"
            ),
        )
        op.create_index(
            "ix_chat_thread_sync_state_activity",
            "chat_thread_sync_state",
            ["last_activity_at"],
        )

    # Bootstrap the reconcile's selection: everyone active in the last 7 days. One scan,
    # once, at deploy; afterwards every ingest lane keeps `last_activity_at` current.
    conn.execute(
        sa.text(
            """
            INSERT INTO chat_thread_sync_state (channel, contact_id, last_activity_at)
            SELECT channel, contact_id, MAX(sent_at)
            FROM chat_histories
            WHERE sent_at >= NOW() - INTERVAL '7 days'
            GROUP BY channel, contact_id
            ON CONFLICT (channel, contact_id) DO NOTHING
            """
        )
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO scheduled_tasks
                (id, key, name, description, enabled, interval_unit, interval_value,
                 timezone, metadata)
            VALUES
                (gen_random_uuid(), :key, :name, :description, true, 'minutes', 5, 'UTC',
                 CAST(:metadata AS jsonb))
            ON CONFLICT (key) DO NOTHING
            """
        ),
        {
            "key": TASK_KEY,
            "name": "Chat history reconcile",
            "description": (
                "Every 5 minutes: one Respond.io delta read per contact with chat activity in "
                "the last `activity_days` days (default 7), at most `concurrency` calls in "
                "flight per workspace key (default 2), backing off on 429 / Retry-After. "
                "Self-heals chat_histories when the n8n or webhook feed missed a message. "
                "Load scales with message activity, never with viewers."
            ),
            "metadata": '{"activity_days": 7, "concurrency": 2, "batch_limit": 500}',
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM scheduled_tasks WHERE key = :key"), {"key": TASK_KEY})
    op.drop_index("ix_chat_thread_sync_state_activity", table_name="chat_thread_sync_state")
    op.drop_table("chat_thread_sync_state")
    for name, _ in reversed(_CHAT_COLUMNS):
        op.drop_column("chat_histories", name)
