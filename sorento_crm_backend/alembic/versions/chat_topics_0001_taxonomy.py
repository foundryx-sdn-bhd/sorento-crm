"""Chat analytics CA-2: the topic taxonomy, the ask's language and an item's confidence.

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Schema".

`chatbot.chat_topics` holds the taxonomy. A subject (no parent) is one of the parser's own
domains, plus `general` for an ask with none (owner ruling HYBRID, 9 Oct 2026: the top level
of a topic IS the parser's domain). A leaf nests under a subject through `parent_code`, a
foreign key, so a leaf under a subject that does not exist cannot be written. `version` is
the taxonomy version that introduced the row; the current version is the highest one among
active rows. Leaves arrive in a later migration, once the owner has approved taxonomy v1.

The subject list is frozen here rather than read from `turn/policy_rows.py`: a migration
must replay the same rows forever, whatever the code says later. A domain added to the
parser later arrives with its own taxonomy bump.

`chat_ask_facts.language` (Q8 ruling: the labeller names each ask's language) and
`chat_ask_items.confidence` (the labeller's own score for an item it resolved) are
nullable additions. Additive and idempotent: `IF NOT EXISTS` throughout and
`ON CONFLICT DO NOTHING` on the seed.

Revision ID: chat_topics_0001_taxonomy
Revises: fcom_0003_conv_thread
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "chat_topics_0001_taxonomy"
down_revision = "fcom_0003_conv_thread"
branch_labels = None
depends_on = None

_SCHEMA = "chatbot"

#: (code, label, definition), in the order a reader meets them. Codes are the parser's
#: domain names (`turn/policy_rows.py` DEFAULT_DOMAIN_ROWS, 9 Oct 2026) plus `general`.
_SUBJECTS = (
    ("inventory", "Stock", "Whether a product is in stock, how many, and where."),
    ("incoming", "Incoming stock", "When stock that is on its way will arrive."),
    ("order", "Orders", "The status, delivery and documents of the customer's own orders."),
    ("master_products", "Product information", "What a product is: price, specification, alternatives."),
    ("product_attachment", "Product files", "Photos, drawings and catalogues of a product."),
    ("promotion", "Promotions", "Current promotions and their terms."),
    ("sales", "Sales", "Sales figures and rankings."),
    ("purchase_order", "Purchase orders", "Outstanding purchase orders to suppliers."),
    ("purchase_cost", "Purchase cost", "The last price the company paid for a product."),
    ("spo_allocation", "Last in", "The last shipment a product came in on."),
    ("forms", "Forms", "Getting a form to fill in."),
    ("portal_link", "Portal link", "A link to the dealer portal."),
    ("resource_attachment", "Resources", "Company documents that are not about one product."),
    ("ideate", "Ideas", "An idea or suggestion the customer wants to leave."),
    ("goods_receive", "Goods receive", "Goods received into the warehouse."),
    ("general", "General", "Anything the parser gave no business domain: greetings, asks for a person, the rest."),
)


def _schema(bind) -> str:
    """Where the chatbot tables live, for raw SQL: the scratch copy under the test suite
    (`<default>_chatbot`, `tests/_pg_fixture.py`), `chatbot` everywhere else."""
    current = bind.exec_driver_sql("SELECT current_schema()").scalar()
    return _SCHEMA if current in (None, "public") else f"{current}_{_SCHEMA}"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "chat_topics",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column(
            "parent_code",
            sa.Text(),
            sa.ForeignKey(f"{_SCHEMA}.chat_topics.code"),
            nullable=True,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=_SCHEMA,
        if_not_exists=True,
    )

    # Raw and schema-aware rather than `op.add_column(..., schema="chatbot")`: alembic
    # renders an ALTER TABLE's schema literally, so under the test suite's
    # `schema_translate_map` it would reach the REAL `chatbot` schema (migration 421's
    # `_schema`, same trap). `IF NOT EXISTS` keeps it a no-op where `create_all` converged.
    chatbot = _schema(bind)
    op.execute(f'ALTER TABLE "{chatbot}".chat_ask_facts ADD COLUMN IF NOT EXISTS language TEXT')
    op.execute(f'ALTER TABLE "{chatbot}".chat_ask_items ADD COLUMN IF NOT EXISTS confidence REAL')

    # A real `Table`, not `sa.table()`: only a `Table` takes part in the connection's
    # schema translation, which is how the migration test lands it in a scratch schema.
    topics = sa.Table(
        "chat_topics",
        sa.MetaData(),
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text()),
        sa.Column("definition", sa.Text()),
        sa.Column("version", sa.SmallInteger()),
        sa.Column("status", sa.Text()),
        sa.Column("sort_order", sa.Integer()),
        schema=_SCHEMA,
    )
    for order, (code, label, definition) in enumerate(_SUBJECTS):
        bind.execute(
            postgresql.insert(topics)
            .values(code=code, label=label, definition=definition, version=1, status="active", sort_order=order)
            .on_conflict_do_nothing(index_elements=["code"])
        )


def downgrade() -> None:
    chatbot = _schema(op.get_bind())
    op.execute(f'ALTER TABLE "{chatbot}".chat_ask_items DROP COLUMN IF EXISTS confidence')
    op.execute(f'ALTER TABLE "{chatbot}".chat_ask_facts DROP COLUMN IF EXISTS language')
    op.drop_table("chat_topics", schema=_SCHEMA, if_exists=True)
