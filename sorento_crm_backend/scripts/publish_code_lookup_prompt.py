"""Publish the CHAT-CODE-LOOKUP parser version: a CLOSED LISTS base version plus the CODE LOOKUP section.

The base is the version under the `production` label, or `--from-version N` (a lane's own
version, e.g. CHAT-STATE's). The result is the NEXT `chatbot_semantic_parser` version,
UNLABELLED: runs pin it with `prompt_overrides`, and only the owner moves a label.
Idempotent: a version already carrying the exact body publishes nothing.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_code_lookup_prompt [--from-version N]

Prints `published chatbot_semantic_parser v<N> (from v<M>)` or `already published as v<N>`.
"""
from __future__ import annotations

import argparse

from sqlalchemy.orm import Session

PROMPT_NAME = "chatbot_semantic_parser"

#: The base bullet (production lineage v63 / v158 / v163) the when-deliver class is added to, exactly once.
OUTSTANDING_ANCHOR = (
    '  - "outstanding" -> orders NOT yet delivered: "outstanding", "pending", "not delivered",\n'
    '    "undelivered", "not yet delivered", "still pending", "open orders", "belum hantar",\n'
    '    "belum sampai", "tak hantar lagi".\n'
)
OUTSTANDING_ADDITION = (
    "    A question asking WHEN goods will be delivered to, or reach, a customer or a shop is also such an\n"
    '    explicit word, in any wording or language: "when deliver", "when will ... be delivered",\n'
    '    "bila hantar", "几时送" and their misspellings.\n'
)


def _base(session: Session, from_version: int | None):
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

    query = session.query(AIPromptVersion).filter(AIPromptVersion.name == PROMPT_NAME)
    if from_version is not None:
        row = query.filter(AIPromptVersion.version == from_version).first()
    else:
        row = (
            query.join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.name == PROMPT_NAME, AIPromptLabel.label == "production")
            .first()
        )
    if row is None:
        raise SystemExit(f"refused: no base version ({'v' + str(from_version) if from_version else 'production'})")
    return row


def _build(base_template: str, version: int) -> str:
    """The base with the when-deliver class inserted after the outstanding bullet, then the section."""
    from app.services.chatbot.head.code_lookup_prompt import SECTION

    if base_template.count(OUTSTANDING_ANCHOR) != 1:
        raise SystemExit(f"refused: v{version} does not carry the outstanding bullet exactly once")
    at = base_template.index(OUTSTANDING_ANCHOR) + len(OUTSTANDING_ANCHOR)
    return base_template[:at] + OUTSTANDING_ADDITION + base_template[at:] + SECTION


def publish(session: Session, from_version: int | None = None) -> int | None:
    """The new version number, or None when a version already carries the body."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services.chatbot.head import closed_lists_prompt
    from app.services.chatbot.head.code_lookup_prompt import MARKER

    base = _base(session, from_version)
    if closed_lists_prompt.MARKER not in (base.template or ""):
        raise SystemExit(f"refused: v{base.version} does not carry the CLOSED LISTS section")
    if MARKER in (base.template or ""):
        raise SystemExit(f"refused: v{base.version} already carries the CODE LOOKUP section")
    template = _build(base.template, base.version)
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
            type=base.type or "text",
            template=template,
            variables=list(base.variables or []),
        )
    )
    session.commit()
    return next_version


def main() -> None:
    from app.database import SessionLocal

    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--from-version", type=int, default=None)
    from_version = args.parse_args().from_version
    session = SessionLocal()
    try:
        base = _base(session, from_version)
        new = publish(session, from_version)
        if new is None:
            from app.models.ai_prompt import AIPromptVersion
            row = (
                session.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == _build(base.template, base.version))
                .first()
            )
            print(f"already published as v{row.version}")
        else:
            print(f"published {PROMPT_NAME} v{new} (from v{base.version})")
    finally:
        session.close()


if __name__ == "__main__":
    main()
