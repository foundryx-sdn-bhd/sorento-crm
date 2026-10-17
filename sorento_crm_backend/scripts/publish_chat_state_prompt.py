"""Publish the CHAT-STATE lane prompt as ONE new UNLABELLED `chatbot_semantic_parser` version:
the source version (`--from-version N`, else the one labelled production) with the state section
appended (`head/chat_state_prompt.build_lane_prompt`). Never moves a label; a rerun adds nothing.

Usage, from `sorento_crm_backend/` with the backend env in place (dev copy only):

    venv/bin/python -m scripts.publish_chat_state_prompt [--from-version N]
"""
from __future__ import annotations

import uuid

from sqlalchemy import text as sql

PROMPT_KEY = "chatbot_semantic_parser"


def publish(session, *, from_version: int | None = None, with_closed_lists: bool = False) -> int | None:
    """Returns the new version number, the existing version when one already holds exactly
    the new text, or None when the source version is missing."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services.chatbot.head.chat_state_prompt import build_lane_prompt

    if from_version is not None:
        row = session.execute(
            sql("SELECT template, variables FROM ai_prompt_versions WHERE name = :n AND version = :v"),
            {"n": PROMPT_KEY, "v": int(from_version)},
        ).first()
    else:
        row = session.execute(
            sql(
                "SELECT v.template, v.variables FROM ai_prompt_versions v "
                "JOIN ai_prompt_labels l ON l.version_id = v.id "
                "WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_KEY},
        ).first()
    if row is None:
        return None
    base_text = row[0] or ""
    if with_closed_lists:
        # One version with both gated sections, each once: #1755's builder, then the state section.
        from app.services.chatbot.head.closed_lists_prompt import SECTION, gate_on
        from scripts.publish_closed_lists_prompt import with_specs_token

        if not gate_on(base_text):
            base_text = with_specs_token(base_text) + SECTION
    new_text = build_lane_prompt(base_text)
    existing = session.execute(
        sql("SELECT version FROM ai_prompt_versions WHERE name = :n AND template = :t"),
        {"n": PROMPT_KEY, "t": new_text},
    ).first()
    if existing is not None:
        return int(existing[0])
    top = session.execute(
        sql("SELECT coalesce(max(version), 0) FROM ai_prompt_versions WHERE name = :n"),
        {"n": PROMPT_KEY},
    ).scalar()
    version = int(top) + 1
    session.add(
        AIPromptVersion(
            id=str(uuid.uuid4()),
            name=PROMPT_KEY,
            version=version,
            type="text",
            template=new_text,
            variables=list(row[1] or []),
            commit_message=(
                f"CHAT-STATE: v{from_version if from_version is not None else 'production'}"
                + (" with the CLOSED LISTS and state_changes sections" if with_closed_lists else " with the state_changes section")
            ),
        )
    )
    session.flush()
    return version


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-version", type=int, default=None)
    parser.add_argument("--with-closed-lists", action="store_true")
    args = parser.parse_args()
    from_version = args.from_version

    import app.main  # noqa: F401  registers every model
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptVersion

    session = SessionLocal()
    try:
        version = publish(session, from_version=from_version, with_closed_lists=args.with_closed_lists)
        session.commit()
        row = (
            session.query(AIPromptVersion)
            .filter(AIPromptVersion.name == PROMPT_KEY, AIPromptVersion.version == version)
            .first()
            if version is not None
            else None
        )
        row_id = str(row.id) if row is not None else None
    finally:
        session.close()
    if version is None:
        print("the source version is missing; nothing done")
    else:
        print(f"chatbot_semantic_parser v{version} id {row_id} (unlabelled); the production label is untouched")


if __name__ == "__main__":
    main()
