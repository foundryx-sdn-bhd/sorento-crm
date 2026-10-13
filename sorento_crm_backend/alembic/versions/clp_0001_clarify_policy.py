"""CLARIFY-POLICY S2: `chatbot_clarify_policy`, one row per question kind the chatbot asks.

Plan: documentation/plans/chatbot/PLAN-clarify-policy-11oct.md (AC-110..AC-116). The owner sets,
per kind, whether the bot asks or defaults (R1, 11 Oct 2026). `mode` must be one of the row's own
`allowed_modes` (CHECK). Seeded with today's behaviour, `carry_unclear` at `carry` until the
parser version with `continuation` is promoted (crew ruling 11 Oct 2026).

Idempotent and additive: `CREATE TABLE IF NOT EXISTS` and `INSERT ... ON CONFLICT DO NOTHING`,
so a database that converged through `create_all` (the shared dev copy) upgrades cleanly.

Revision ID: clp_0001_clarify_policy
Revises: maskp_0002_measure_words
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Kept <=32 chars: alembic_version.version_num is varchar(32).
revision = "clp_0001_clarify_policy"
down_revision = "maskp_0002_measure_words"
branch_labels = None
depends_on = None


# The rows this migration seeds, FROZEN here as literal data (the `chatbot_rearch_s0` /
# `_chatbot_policy_seed.py` precedent): a migration must write the same rows whenever it runs,
# so it never imports the app's list. At this revision these rows equal
# `app.services.chatbot.clarify_policy.CLARIFY_POLICY_ROWS`, which the loader falls back to; a
# later policy change is a NEW migration against the table, never an edit here.
# (ask_kind, label, mode, allowed_modes, default_note); repeat_limit is 2 for every row.
_SEED_ROWS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    ("promo_tier", "Promotion access level", "ask", ("ask",), ""),
    ("tier_pick", "Generic access level pick", "ask", ("ask",), ""),
    ("promo_subject", "Promotion subject", "ask", ("ask",), ""),
    ("attachment_type", "Attachment type", "ask", ("ask",), ""),
    ("attachment_kind", "Attachment kind", "ask", ("ask",), ""),
    ("cost_product", "Purchase cost product", "ask", ("ask",), ""),
    ("do_period", "Which period, for delivery orders", "ask", ("ask", "default"), "newest 20, no date window"),
    ("so_period", "Which period, for sales orders", "ask", ("ask", "default"), "newest 20, no date window"),
    ("ranking_fields", "Ranking period, metric, basis, top N", "ask", ("ask",), ""),
    ("ranking_drill", "Ranking drill scope", "ask", ("ask",), ""),
    ("top_selling", "Top selling grain, metric, who", "ask", ("ask",), ""),
    ("low_stock", "Low stock category, supplier, grouping", "ask", ("ask",), ""),
    ("idea_text", "Ideation idea", "ask", ("ask",), ""),
    ("outstanding_scope", "Outstanding for which document", "ask", ("ask", "default"), "both SO and DO"),
    ("outstanding_customer", "Outstanding report customer", "ask", ("ask",), ""),
    ("product_pick", "Product picker", "collapse", ("collapse",), ""),
    ("customer_pick", "Customer picker", "ask", ("ask",), ""),
    ("roster_pick", "Roster pick", "ask", ("ask",), ""),
    ("kind_pick", "Kind pick", "ask", ("ask",), ""),
    ("account_customer", "Account N customer", "ask", ("ask",), ""),
    ("stock_qty", "Stock quantity", "ask", ("ask",), ""),
    ("stock_family", "Stock family pick", "ask", ("ask",), ""),
    ("qty_scope", "Quantity for all or one", "ask", ("ask",), ""),
    ("demand_qty", "Stock denial demand quantity", "ask", ("ask",), ""),
    ("did_you_mean", "Did you mean", "ask", ("ask",), ""),
    ("generic_menu", "Domain menu", "default", ("default", "ask"), "answer the subject's default domain"),
    ("llm_clarify", "LLM clarifier", "ask", ("ask",), ""),
    ("escalation_pick", "Escalation team, company, member", "ask", ("ask",), ""),
    ("entities_only", "What would you like me to know", "default", ("default", "ask"), "answer the subject's default domain"),
    ("carry_combine", "Carried subject with a new subject", "ask", ("ask", "carry", "drop"), "ask, keep or drop"),
    ("carry_unclear", "Carried subject, no reference", "carry", ("carry", "ask", "drop"), "ask, keep or drop"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chatbot_clarify_policy (
            ask_kind text PRIMARY KEY,
            label text NOT NULL,
            mode text NOT NULL,
            allowed_modes text[] NOT NULL,
            default_note text NOT NULL DEFAULT '',
            repeat_limit integer NOT NULL DEFAULT 2,
            updated_at timestamp without time zone NOT NULL DEFAULT now(),
            updated_by text,
            CONSTRAINT ck_chatbot_clarify_policy_mode_allowed CHECK (mode = ANY(allowed_modes))
        )
        """
    )
    insert = sa.text(
        "INSERT INTO chatbot_clarify_policy "
        "(ask_kind, label, mode, allowed_modes, default_note, repeat_limit, updated_by) "
        "VALUES (:ask_kind, :label, :mode, :allowed_modes, :default_note, :repeat_limit, 'migration') "
        "ON CONFLICT (ask_kind) DO NOTHING"
    )
    bind = op.get_bind()
    for ask_kind, label, mode, allowed_modes, default_note in _SEED_ROWS:
        bind.execute(
            insert,
            {
                "ask_kind": ask_kind,
                "label": label,
                "mode": mode,
                "allowed_modes": list(allowed_modes),
                "default_note": default_note,
                "repeat_limit": 2,
            },
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chatbot_clarify_policy")
