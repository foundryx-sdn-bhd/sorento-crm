"""The live production parser prompt, with registry variables where they reproduce it
exactly (owner, #1405, 1 Oct 2026).

Loads the owner-supplied production text (`alembic/data/chatbot_semantic_parser.prod-20261001.txt`,
verbatim, sha256 fdbf2ea1...) and swaps each hard-coded registry list for its
`{{variable}}` ONLY where the registry renders exactly that text from the tables as they
stand at migration time (`chatbot_prompt_vars.identical_wording_layer`). A registry that
differs keeps the owner's list literal; the report of every list (replaced or kept, and
what differs) is stored on the version (`config_json.identical_report`) and logged.

It locates prod's live text by the `production` label (the version that label points
at), never by search, and requires that text to equal the owner's file character for
character: no newline folding, no trimming. Any difference, one character, a trailing
newline or CRLF line ends, means nothing is written and the log names the first
difference (line, column, both characters). It never guesses (owner, 1 Oct 2026). No
`production` label: nothing written, logged.

The version also carries ACCOUNT_LEDGER_ADDENDUM (#1432, merged first), just before the
policy blocks (`chatbot_prompt_vars.with_account_block`). The result is proven before
insert: rendering it from the tables gives the file plus that block, byte for byte. If
that proof ever fails, nothing is written and the failure is logged.

Inserted as ONE new UNLABELLED version (version = max + 1 at run time). No label moves
and no existing version is touched; the owner publishes it himself. Idempotent: does
nothing when a version with the same template, or from the same snapshot, exists.
Downgrade deletes only its own row, and only while it is unlabelled.

Revision ID: pdyn_0003_prod_identical
Revises: pdyn_0002_wording_layer
Create Date: 2026-10-01
"""
from __future__ import annotations

import hashlib
import logging
import pathlib

from alembic import op
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

revision = "pdyn_0003_prod_identical"
down_revision = "pdyn_0002_wording_layer"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

PROMPT_NAME = "chatbot_semantic_parser"
SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "data" / "chatbot_semantic_parser.prod-20261001.txt"
MESSAGE = (
    "Production text of 1 Oct 2026 (owner-supplied), registry variables wherever the "
    "registry reproduces it exactly. Renders identical to that text plus the ACCOUNT-LEDGER block before the policy blocks. Unlabelled: publish "
    "from the Prompts page."
)


def _sha() -> str:
    return hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()


def _renders_identical(template: str, source: str, session: Session) -> bool:
    from app.services import ai_prompt_registry, chatbot_prompt_vars

    names = ai_prompt_registry.extract_tokens(template) & set(chatbot_prompt_vars.VARIABLE_NAMES)
    values = {name: chatbot_prompt_vars.render_value(session, name) for name in names}
    return ai_prompt_registry._substitute(template, values) == source


def _first_difference(a: str, b: str) -> str:
    i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    line = a[:i].count("\n") + 1
    col = i - (a.rfind("\n", 0, i) + 1) + 1
    return (
        f"first difference at line {line}, column {col}: live {a[i:i + 1]!r} vs file {b[i:i + 1]!r} "
        f"(live {len(a)} chars, file {len(b)} chars)"
    )


def apply(bind) -> int | None:
    from app.models.ai_prompt import AIPromptVersion
    from app.services import chatbot_prompt_vars

    source = SNAPSHOT.read_text(encoding="utf-8")
    sha = _sha()
    session = Session(bind=bind)
    try:
        live = session.execute(
            sql(
                "SELECT v.version, v.template FROM ai_prompt_versions v JOIN ai_prompt_labels l "
                "ON l.version_id = v.id WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_NAME},
        ).first()
        if live is None:
            logger.warning("prod snapshot: no production label on %s; nothing written", PROMPT_NAME)
            return None
        if live[1] != source:
            logger.warning(
                "prod snapshot: production v%s differs from the owner's file %s; %s; nothing written",
                live[0], SNAPSHOT.name, _first_difference(live[1], source),
            )
            return None
        chatbot_prompt_vars.clear_cache()
        template, report = chatbot_prompt_vars.identical_wording_layer(source, session)
        # #1432 ACCOUNT-LEDGER merged first: the version carries its `account` block, and
        # the proof is the owner's file plus that block.
        template = chatbot_prompt_vars.with_account_block(template)
        identical = _renders_identical(template, chatbot_prompt_vars.with_account_block(source), session)
        if not identical:
            logger.warning("prod snapshot: the swapped text did not render identical to the file; nothing written")
            return None
        exists = session.execute(
            sql(
                "SELECT version FROM ai_prompt_versions WHERE name = :n "
                "AND (template = :t OR config_json->>'prod_snapshot_sha256' = :s) LIMIT 1"
            ),
            {"n": PROMPT_NAME, "t": template, "s": sha},
        ).scalar()
        if exists is not None:
            logger.info("prod snapshot already published as v%s; nothing to do", exists)
            return None
        top = session.execute(
            sql("SELECT max(version) FROM ai_prompt_versions WHERE name = :n"), {"n": PROMPT_NAME}
        ).scalar()
        version = int(top or 0) + 1
        variables = session.execute(
            sql(
                "SELECT v.variables FROM ai_prompt_versions v JOIN ai_prompt_labels l ON l.version_id = v.id "
                "WHERE l.name = :n AND l.label = 'production'"
            ),
            {"n": PROMPT_NAME},
        ).scalar()
        session.add(
            AIPromptVersion(
                name=PROMPT_NAME,
                version=version,
                type="text",
                template=template,
                variables=list(variables or ["current_date"]),
                config_json={
                    "prod_snapshot": SNAPSHOT.name,
                    "prod_snapshot_sha256": sha,
                    "from_production_version": int(live[0]),
                    "rendered_identical": identical,
                    "identical_report": report,
                },
                commit_message=MESSAGE,
            )
        )
        session.commit()
        for row in report:
            logger.info("prod snapshot v%s: {{%s}} line %s %s", version, row["variable"], row["line"], row["action"])
        return version
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def remove(bind) -> None:
    """Delete this migration's own row, and only while nobody has labelled it."""
    bind.execute(
        sql(
            "DELETE FROM ai_prompt_versions v WHERE v.name = :n "
            "AND v.config_json->>'prod_snapshot_sha256' = :s "
            "AND NOT EXISTS (SELECT 1 FROM ai_prompt_labels l WHERE l.version_id = v.id)"
        ),
        {"n": PROMPT_NAME, "s": _sha()},
    )


def upgrade() -> None:
    apply(op.get_bind())


def downgrade() -> None:
    remove(op.get_bind())
