"""Publish a parser version carrying the live `{{find_filters}}` block (CHAT-FIND).

The block is the generic find rule plus one vocabulary line per registered filter
(`product_find.parser_vocabulary`, rendered live by `chatbot_prompt_vars.VARIABLES
["find_filters"]`). Publishing it ONCE is the only prompt edit generic find ever needs: a
filter registered later reaches the parser through the variable, with no new version.

`publish_find_block` copies the source version word for word and adds the variable inside the
policy markers (before the end marker), or appends a marker pair holding it when the text has
none. A new UNLABELLED version; idempotent; never moves a label (the owner promotes it on the
Prompts page). Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md section 2.4.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.services.chatbot_prompt_vars import PROMPT_KEY

VARIABLE = "find_filters"
TOKEN = "{{" + VARIABLE + "}}"
_TOKEN_RE = re.compile(r"\{\{\s*" + VARIABLE + r"\s*\}\}")


def with_find_block(text: str) -> str:
    """`text` carrying `{{find_filters}}`, every other byte kept."""
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    text = text or ""
    if _TOKEN_RE.search(text):
        return text
    if BLOCKS_BEGIN in text and BLOCKS_END in text and text.index(BLOCKS_BEGIN) < text.index(BLOCKS_END):
        # Inserted only: every byte of the source stays where it was.
        head, end, tail = text.rpartition(BLOCKS_END)
        sep = "" if head.endswith("\n") else "\n"
        return f"{head}{sep}{TOKEN}\n{end}{tail}"
    sep = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{sep}\n{BLOCKS_BEGIN}\n{TOKEN}\n{BLOCKS_END}\n"


def publish_find_block(session: Session, *, from_version: int | None = None) -> int | None:
    """The source version (`from_version`, else the one labelled production) with the find
    block, as a new unlabelled version. Returns its number; None when the source is missing,
    already carries the block, or a version with exactly the new text exists."""
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
    new_text = with_find_block(row[0] or "")
    if new_text == row[0]:
        return None
    exists = session.execute(
        sql("SELECT 1 FROM ai_prompt_versions WHERE name = :n AND template = :t"),
        {"n": PROMPT_KEY, "t": new_text},
    ).first()
    if exists is not None:
        return None
    from app.models.ai_prompt import AIPromptVersion

    top = session.execute(
        sql("SELECT coalesce(max(version), 0) FROM ai_prompt_versions WHERE name = :n"),
        {"n": PROMPT_KEY},
    ).scalar()
    version = int(top) + 1
    variables = list(row[1] or [])
    session.add(
        AIPromptVersion(
            id=str(uuid.uuid4()),
            name=PROMPT_KEY,
            version=version,
            type="text",
            template=new_text,
            variables=variables if VARIABLE in variables else [*variables, VARIABLE],
            config_json={"wording_edit": True},
            commit_message=(
                f"CHAT-FIND: v{from_version if from_version is not None else 'production'}"
                " with the live {{find_filters}} block"
            ),
        )
    )
    session.flush()
    return version
