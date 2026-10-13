"""Publish the current chatbot parser prompt as the next `chatbot_semantic_parser` version.

The standalone twin of `alembic/versions/chatbot_po_spo_warehouse_vocab.py::publish` for a
database that will not run alembic data migrations (crew's hand-test copy, PR #1373 - see
`documentation/plans/chatbot/PLAN-po-spo-warehouse-29sep.md` M2). Idempotent: a version
already carrying the exact body publishes nothing. Never moves a label: promote by
selecting the printed version in the Chatbot Console.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_parser_prompt

Prints `published chatbot_semantic_parser v<N>` or `already published as v<N>`.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

PROMPT_NAME = "chatbot_semantic_parser"


def publish(session: Session) -> int | None:
    """Publish `SEMANTIC_PARSER_PROMPT` as the next version unless one already carries it.
    Returns the new version number, or None when already published."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services.ai_prompt_registry import PROMPT_KEYS
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    from app.services.chatbot_prompt_vars import wording_layer_exists

    if wording_layer_exists(session):
        # PLAN-prompt-dynamic-30sep R1: the owner's wording layer is the source now; a
        # version built from the code constant would drop his edits and his variables.
        raise SystemExit(
            "refused: the parser wording layer exists. Publish a wording change with "
            "chatbot_prompt_vars.publish_wording_edit instead of the code constant."
        )
    template = SEMANTIC_PARSER_PROMPT
    existing = (
        session.query(AIPromptVersion)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == template)
        .first()
    )
    if existing is not None:
        return None
    versions = session.query(AIPromptVersion.version).filter(AIPromptVersion.name == PROMPT_NAME).all()
    next_version = max((int(v[0]) for v in versions), default=0) + 1
    session.add(
        AIPromptVersion(
            name=PROMPT_NAME,
            version=next_version,
            type="text",
            template=template,
            variables=list(PROMPT_KEYS[PROMPT_NAME].variables),
        )
    )
    session.commit()
    return next_version


def _current_version(session: Session) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    row = (
        session.query(AIPromptVersion.version)
        .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == SEMANTIC_PARSER_PROMPT)
        .first()
    )
    return int(row[0]) if row else None


def main() -> None:
    from app.database import engine
    from app.services.ai_prompt_seed import seed_prompt_registry

    seed_prompt_registry(engine)
    with Session(bind=engine) as session:
        version = publish(session)
        if version is None:
            print(f"already published as v{_current_version(session)}")
        else:
            print(f"published {PROMPT_NAME} v{version}")


if __name__ == "__main__":
    main()
