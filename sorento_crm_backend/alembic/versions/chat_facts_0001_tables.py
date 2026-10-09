"""Chat analytics CA-1: the three fact tables, plus the grant that opens the Chat overview.

`chatbot.chat_ask_facts` holds one row per customer ask, flattened from the turn trace at
close; `chatbot.chat_ask_items` the products and brands an ask named or was shown;
`chatbot.chat_episode_facts` one row per closed episode, snapshotted before retention trims
the frame (`episode_id` and `frame_id` carry no foreign key for that reason: the frame goes,
the analytic row stays).

No column can hold customer words, an episode summary or a contact's identity. That is the
privacy contract of the lane, and it is enforced by shape: there is nowhere to put them.

Additive only. Table and index creation is `IF NOT EXISTS` through Core, so a database that
converged through `create_all` (the shared local one) re-runs it harmlessly, and the test
that translates the `chatbot` schema to a scratch one sees the same DDL production runs.

The grant is copied rather than invented, as in 422: `chatbot.analytics.view` goes to every
role that already holds `system.chat_history.view`, because a permission granted to nobody is
indistinguishable from a broken feature. Idempotent, safe to re-run.

Revision ID: chat_facts_0001_tables
Revises: psl_0001_chatbot_file_links
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "chat_facts_0001_tables"
down_revision = "psl_0001_chatbot_file_links"
branch_labels = None
depends_on = None

_SCHEMA = "chatbot"
_SLUG = "chatbot.analytics.view"
_NAME = "View Chat Overview"
_DESCRIPTION = (
    "Permission to open the Chat overview report: how the chatbot answered, by outcome, week and product. "
    "Covers every company's customers."
)
_SOURCE_SLUG = "system.chat_history.view"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "chat_ask_facts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ask_seq", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("contact_respond_id", sa.Text(), nullable=False),
        sa.Column("contact_id", sa.Text(), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("company_id", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("contact_type", sa.Text(), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False, server_default=sa.text("'whatsapp'")),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("branch_kind", sa.Text(), nullable=True),
        sa.Column(
            "tools",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("rows_offered", sa.Integer(), nullable=True),
        sa.Column("reply_ms", sa.Integer(), nullable=True),
        sa.Column("is_media", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("phantom_dropped", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("episode_id", postgresql.UUID(as_uuid=False), nullable=True),
        # CA-2 fills the label columns; a trace row leaves them empty.
        sa.Column("topic_code", sa.Text(), nullable=True),
        sa.Column("taxonomy_version", sa.SmallInteger(), nullable=True),
        sa.Column("label_model", sa.Text(), nullable=True),
        sa.Column("label_prompt_version", sa.Text(), nullable=True),
        sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label_confidence", sa.REAL(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("turn_id", "ask_seq", name="uq_chat_ask_facts_turn_seq"),
        schema=_SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        "ix_chat_ask_facts_asked_at", "chat_ask_facts", ["asked_at"], schema=_SCHEMA, if_not_exists=True
    )
    op.create_index(
        "ix_chat_ask_facts_contact",
        "chat_ask_facts",
        ["contact_respond_id"],
        schema=_SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        "ix_chat_ask_facts_outcome", "chat_ask_facts", ["outcome"], schema=_SCHEMA, if_not_exists=True
    )

    # The writer matches a named code with `lower(code) IN (...)`; without these the match is
    # a scan of the catalogue per ask. Public-schema tables, so plain names.
    op.create_index(
        "ix_products_lower_product_code",
        "products",
        [sa.text("lower(product_code)")],
        if_not_exists=True,
    )
    op.create_index(
        "ix_brands_lower_brand_code",
        "brands",
        [sa.text("lower(brand_code)")],
        if_not_exists=True,
    )

    op.create_table(
        "chat_ask_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "ask_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(f"{_SCHEMA}.chat_ask_facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.Text(), nullable=False),
        sa.UniqueConstraint("ask_id", "kind", "ref_id", "role", name="uq_chat_ask_items_ask_kind_ref_role"),
        schema=_SCHEMA,
        if_not_exists=True,
    )
    op.create_index(
        "ix_chat_ask_items_ref_id", "chat_ask_items", ["ref_id"], schema=_SCHEMA, if_not_exists=True
    )

    op.create_table(
        "chat_episode_facts",
        sa.Column("frame_id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("contact_respond_id", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("ask_count", sa.Integer(), nullable=False),
        sa.Column("failed_turns", sa.Integer(), nullable=False),
        sa.Column("last_outcome", sa.Text(), nullable=True),
        sa.Column("handover", sa.Boolean(), nullable=False),
        sa.Column("close_reason", sa.Text(), nullable=True),
        # Null until CA-2 judges them.
        sa.Column("resolved", sa.Boolean(), nullable=True),
        sa.Column("asked_again", sa.Boolean(), nullable=True),
        schema=_SCHEMA,
        if_not_exists=True,
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO user_permissions (id, slug, name, description, created_at)
            SELECT gen_random_uuid()::text, :slug, :name, :descr, now()
            WHERE NOT EXISTS (SELECT 1 FROM user_permissions WHERE slug = :slug)
            """
        ),
        {"slug": _SLUG, "name": _NAME, "descr": _DESCRIPTION},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_permissions (id, role_id, permission_id, assigned_at)
            SELECT gen_random_uuid()::text, urp.role_id, tgt.id, now()
              FROM user_role_permissions urp
              JOIN user_permissions src ON src.id = urp.permission_id
             CROSS JOIN user_permissions tgt
             WHERE src.slug = :source_slug
               AND tgt.slug = :slug
               AND NOT EXISTS (
                   SELECT 1
                     FROM user_role_permissions existing
                    WHERE existing.role_id = urp.role_id
                      AND existing.permission_id = tgt.id
               )
            """
        ),
        {"source_slug": _SOURCE_SLUG, "slug": _SLUG},
    )


def downgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            DELETE FROM user_role_permissions
             WHERE permission_id IN (SELECT id FROM user_permissions WHERE slug = :slug)
            """
        ),
        {"slug": _SLUG},
    )
    bind.execute(sa.text("DELETE FROM user_permissions WHERE slug = :slug"), {"slug": _SLUG})

    op.drop_index("ix_brands_lower_brand_code", table_name="brands", if_exists=True)
    op.drop_index("ix_products_lower_product_code", table_name="products", if_exists=True)
    op.drop_table("chat_episode_facts", schema=_SCHEMA, if_exists=True)
    op.drop_table("chat_ask_items", schema=_SCHEMA, if_exists=True)
    op.drop_table("chat_ask_facts", schema=_SCHEMA, if_exists=True)
