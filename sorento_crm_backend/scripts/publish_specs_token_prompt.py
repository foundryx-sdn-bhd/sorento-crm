"""Publish the SPEC-REGISTRY-UI parser version: a base version with its baked specification
lines replaced by the `{{specs}}` token.

Until the published parser text carries `{{specs}}`, a registry edit (a word, a value, a
chatbot meaning) never reaches the bot: the spec lines are baked text. The base is the
version under the `production` label, or `--from-version N` (run on top of the closed-lists
version so one publish carries both). The ONE contiguous run of `Specification ...` lines
(plus the `Product types (` line that closes it) becomes a single `{{specs}}` line; zero or
several runs are refused. The result is the NEXT `chatbot_semantic_parser` version,
UNLABELLED: runs pin it with `prompt_overrides`, and only the owner moves a label.
Idempotent: a version already carrying the exact body publishes nothing.

Usage, from `sorento_crm_backend/` with the backend env (`DATABASE_URL`) in place:

    venv/bin/python -m scripts.publish_specs_token_prompt [--from-version N]

Prints `published chatbot_semantic_parser v<N> (from v<M>)` or `already published as v<N>`.
"""
from __future__ import annotations

import argparse
import re

from sqlalchemy.orm import Session

PROMPT_NAME = "chatbot_semantic_parser"
TOKEN = "specs"

_SPEC_LINE = re.compile(r"^Specification [a-z0-9_]+ \(")
_TYPES_LINE = re.compile(r"^Product types \(")


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
        which = f"v{from_version}" if from_version is not None else "production"
        raise SystemExit(f"refused: no base version ({which})")
    return row


def _replace_run(template: str) -> str:
    """The template with its one baked specification run swapped for `{{specs}}`."""
    lines = template.split("\n")
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not _SPEC_LINE.match(lines[i]):
            i += 1
            continue
        start = i
        while i < len(lines) and _SPEC_LINE.match(lines[i]):
            i += 1
        if i < len(lines) and _TYPES_LINE.match(lines[i]):
            i += 1
        runs.append((start, i))
    if not runs:
        raise SystemExit("refused: no baked specification block")
    if len(runs) > 1:
        raise SystemExit(f"refused: {len(runs)} baked specification blocks")
    start, end = runs[0]
    return "\n".join([*lines[:start], "{{" + TOKEN + "}}", *lines[end:]])


def publish(session: Session, from_version: int | None = None) -> int | None:
    """The new version number, or None when a version already carries the body."""
    from app.models.ai_prompt import AIPromptVersion

    base = _base(session, from_version)
    if "{{" + TOKEN + "}}" in (base.template or ""):
        raise SystemExit(f"refused: v{base.version} already carries the {{{{{TOKEN}}}}} token")
    template = _replace_run(base.template or "")
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
            variables=list(dict.fromkeys([*(base.variables or []), TOKEN])),
            config_json=base.config_json,
            commit_message=f"{TOKEN} token replaces the baked specification lines (from v{base.version})",
        )
    )
    session.commit()
    return next_version


def main() -> None:
    from app.database import SessionLocal
    from app.models.ai_prompt import AIPromptVersion

    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--from-version", type=int, default=None)
    from_version = args.parse_args().from_version
    session = SessionLocal()
    try:
        base = _base(session, from_version)
        new = publish(session, from_version)
        if new is None:
            row = (
                session.query(AIPromptVersion)
                .filter(
                    AIPromptVersion.name == PROMPT_NAME,
                    AIPromptVersion.template == _replace_run(base.template or ""),
                )
                .first()
            )
            print(f"already published as v{row.version}")
        else:
            print(f"published {PROMPT_NAME} v{new} (from v{base.version})")
    finally:
        session.close()


if __name__ == "__main__":
    main()
