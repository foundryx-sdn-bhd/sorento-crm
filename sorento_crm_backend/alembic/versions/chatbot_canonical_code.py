"""Publish the chatbot parser prompt with CANONICAL_CODE_ADDENDUM as ONE new version,
label unmoved (RESOLVE-SEMANTIC-FALLBACK, PR #1511, owner ruling 6 Oct 2026).

Owner report (6 Oct 2026 20:02 MYT, production): the stock-check line "TAP SRTWT5608
x 5" came back "I could not find TAP SRTWT5608" while SRTWT5608 exists. The parser had
emitted ONE product entity, raw "TAP SRTWT5608", canonical_code null, and the resolver
matches a product token only as an exact code, a prefix of a code or a substring of one.
Owner ruling (card Q1 = a): a type / colour / brand word beside a code is still ONE
product entity, `raw` unchanged, `canonical_code` the bare code exactly as typed; the
chatbot reads `canonical_code` first (`resolve_gate._token_of`, and the sites #1511
unified). `CANONICAL_CODE_ADDENDUM` (`app/services/chatbot_parser_prompt.py`) teaches
that shape; it is graded live by `tests/chatbot/test_canonical_code_live_parser.py` over
`tests/chatbot/console_cases/2026-10-06-canonical-code-type-word.yaml` (red on the
current text, green with the addendum).

Same reason `chatbot_known_brands` publishes: `ai_prompt_registry.render()` reads the
PUBLISHED row, not the Python constant (the fallback is used only when no DB row exists
at all), so a session must have a published version carrying the addendum before it can
reach a live turn. Only the FULL body is published (the SLIM body retired, AC-1506).

Same immutable-versions-plus-movable-labels split as migration 475: the text lands as
the next `chatbot_semantic_parser` version with NO label, so promoting is one label move
in the admin UI and rolling back is the reverse move. Nothing a customer sees changes
until the owner promotes.

Idempotent, and safe on a fresh database: ``seed_prompt_registry`` runs first so v1 and
the ``production`` label exist even on an install that never saw an earlier chatbot
migration, and the publish is skipped when a version already carries that template.

The insert logic lives in module-level ``publish(session)`` so it can be called outside
alembic (e.g. to publish against the shared dev database without an ``alembic upgrade``,
which is how the console check gets a version id to pass to ``--prompt-version``).

Revision ID: chatbot_canonical_code
Revises: aig_0001_chatbot_allowed
"""
import logging

from alembic import op
from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
from app.services.ai_prompt_registry import PROMPT_KEYS

revision = "chatbot_canonical_code"
down_revision = "aig_0001_chatbot_allowed"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"


def _full_text() -> str:
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


def _publish_one(session: Session, template: str, tag: str) -> int | None:
    """Publish ``template`` as the next `chatbot_semantic_parser` version, unless a
    version already carries it. Returns the new version number, or None when already
    published. ``tag`` is only for the log line."""
    spec = PROMPT_KEYS[PROMPT_NAME]
    existing = (
        session.query(AIPromptVersion)
        .filter(
            AIPromptVersion.name == PROMPT_NAME,
            AIPromptVersion.template == template,
        )
        .first()
    )
    if existing is not None:
        logger.info(
            "chatbot parser canonical-code %s prompt already published as v%s; nothing to do",
            tag,
            existing.version,
        )
        return None
    versions = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME)
        .all()
    )
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(spec.variables),
        )
    )
    session.commit()
    logger.info(
        "published chatbot parser canonical-code %s prompt as v%s (%s chars); "
        "production label left on the previous version, promote by moving it",
        tag,
        next_version,
        len(template),
    )
    return next_version


def publish(session: Session) -> dict[str, int | None]:
    """Publish the FULL text as a new version, idempotent on template equality. Returns
    ``{"full": v|None}``; a second call on an already-published database returns None."""
    return {"full": _publish_one(session, _full_text(), "full")}


def upgrade() -> None:
    from app.services.ai_prompt_seed import seed_prompt_registry

    bind = op.get_bind()
    # v1 from the fallback plus the production label, if absent.
    seed_prompt_registry(bind)

    session = Session(bind=bind)
    try:
        publish(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    """Drop the unlabelled version this migration published. Its FULL text carries the
    canonical-code addendum, so no other migration's row matches it (a later identical
    publish is skipped by `_publish_one`), which is what makes a template match safe here.

    A version this migration published can stop being unlabelled: the owner may have
    since moved a label (e.g. `production`) onto it in the admin UI.
    `AIPromptLabel.version_id` is `ondelete="CASCADE"` (`app/models/ai_prompt.py`), so
    deleting a labelled version would silently delete the label row with it - a
    downgrade must never do that. Any version this migration published that now carries
    a label is therefore excluded from the delete; the label keeps pointing at it.
    """
    bind = op.get_bind()
    templates = [_full_text()]
    session = Session(bind=bind)
    try:
        labelled_version_ids = {
            row[0]
            for row in (
                session.query(AIPromptLabel.version_id)
                .join(AIPromptVersion, AIPromptVersion.id == AIPromptLabel.version_id)
                .filter(AIPromptVersion.name == PROMPT_NAME)
                .all()
            )
        }
        query = session.query(AIPromptVersion).filter(
            AIPromptVersion.name == PROMPT_NAME,
            AIPromptVersion.template.in_(templates),
        )
        if labelled_version_ids:
            query = query.filter(AIPromptVersion.id.notin_(labelled_version_ids))
        query.delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
