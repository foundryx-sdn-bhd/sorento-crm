"""Seed the REPORTS-STEP2 report wording into the prompt registry (11 Oct 2026).

Eight `chatbot_reply_report_*` copy rows, each with its `.ms` and `.zh` sibling (24 keys), seeded
at v1 from their hardcoded fallback with the `production` label pointing at it, so the owner can
edit the wording in Settings > AI Prompts with no deploy. The CODE change is what makes the bot
say them (`ai_prompt_registry` builds its key specs from `CHATBOT_REPLY_COPY`); this only puts
the rows in the registry. The bot answers from the same shipped text when a row is missing.

Insert-only by construction: it seeds just these 24 keys, makes a v1 only when the key has none
and a `production` label only when the key has none, so a row the owner edited (a later version
with the label moved onto it) is never touched. It deliberately does not call
`seed_prompt_registry`, whose other steps can move a label (the `agent_system` v2 preserve).

Revision ID: rep2_0001_wording_seed
Revises: scope_seeds_debtor_company
"""
from alembic import op
from sqlalchemy.orm import Session

revision = "rep2_0001_wording_seed"
down_revision = "scope_seeds_debtor_company"
branch_labels = None
depends_on = None

SHORT_NAMES = (
    "report_subject_ask",
    "report_refused",
    "report_period_missing",
    "report_period_give_up",
    "report_own_account",
    "report_dimension_denied",
    "report_not_enabled",
    "report_chat_not_enabled",
)


def upgrade() -> None:
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from app.services.ai_prompt_seed import _ensure_label, _ensure_version

    session = Session(bind=op.get_bind())
    try:
        for short in SHORT_NAMES:
            for suffix in ("", ".ms", ".zh"):
                name = f"chatbot_reply_{short}{suffix}"
                spec = PROMPT_KEYS[name]
                v1 = _ensure_version(session, name, 1, spec.fallback(), list(spec.variables))
                _ensure_label(session, name, "production", v1.id)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    # The seeded rows stay: dropping them would strip an edit the owner may have published on
    # top, and an unused key costs nothing. Same call 515_chatbot_offer_decline makes.
    pass
