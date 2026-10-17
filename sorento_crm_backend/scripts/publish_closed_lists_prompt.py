"""Publish the CHAT-CLOSED-LISTS parser version: a base version plus the CLOSED LISTS section.

The base is the version under the `production` label, or `--from-version N` (a lane's own
version, e.g. CHAT-STATE's). The result is the NEXT `chatbot_semantic_parser` version,
UNLABELLED: runs pin it with `prompt_overrides`, and only the owner moves a label.
Idempotent: a version already carrying the exact body publishes nothing.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_closed_lists_prompt [--from-version N]

Prints `published chatbot_semantic_parser v<N> (from v<M>)` or `already published as v<N>`.
"""
from __future__ import annotations

import argparse
import re

from sqlalchemy.orm import Session

PROMPT_NAME = "chatbot_semantic_parser"

#: The lines `chatbot_parser_prompt.specification_lines` renders (one per registry key, then
#: the product types), as a base published before `{{specs}}` existed carries them baked in.
_SPEC_LINE = re.compile(r'^(?:Specification [^\s(]+ \(".*"\): .*|Product types \(the category entity keeps these.*)$')


def with_specs_token(template: str) -> str:
    """The template with its baked specification lines replaced by `{{specs}}`, so an owner
    edit on Product Specifications reaches the parser at the next read (SPEC-REGISTRY-UI
    #1765). A template already carrying the token, or no such lines, is returned as it is;
    two separate runs of them are refused rather than guessed at."""
    if "{{specs}}" in template:
        return template
    lines = template.split("\n")
    runs: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if _SPEC_LINE.match(line):
            if runs and runs[-1][1] == i:
                runs[-1] = (runs[-1][0], i + 1)
            else:
                runs.append((i, i + 1))
    if not runs:
        return template
    if len(runs) > 1:
        raise SystemExit(f"refused: {len(runs)} separate runs of specification lines; expected one")
    start, end = runs[0]
    return "\n".join([*lines[:start], "{{specs}}", *lines[end:]])


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


def publish(session: Session, from_version: int | None = None) -> int | None:
    """The new version number, or None when a version already carries the body."""
    from app.models.ai_prompt import AIPromptVersion
    from app.services.chatbot.head.closed_lists_prompt import SECTION, gate_on

    base = _base(session, from_version)
    if gate_on(base.template):
        raise SystemExit(f"refused: v{base.version} already carries the CLOSED LISTS section")
    template = with_specs_token(base.template) + SECTION
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
            from app.services.chatbot.head.closed_lists_prompt import SECTION

            row = (
                session.query(AIPromptVersion)
                .filter(AIPromptVersion.name == PROMPT_NAME, AIPromptVersion.template == with_specs_token(base.template) + SECTION)
                .first()
            )
            print(f"already published as v{row.version}")
        else:
            print(f"published {PROMPT_NAME} v{new} (from v{base.version})")
    finally:
        session.close()


if __name__ == "__main__":
    main()
