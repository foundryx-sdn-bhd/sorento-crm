"""Migration `chat_topics_0001_taxonomy`: the topic taxonomy table, the ask's language and an
item's confidence (chat analytics CA-2, tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca2-taxonomy-09oct.md, "Schema".

* static: the revision id fits `alembic_version.version_num` (<= 32 chars), it chains onto
  CA-1's tables, and `upgrade()` only adds;
* functional: `upgrade()` runs against the blank scratch schema inside a rolled-back
  transaction, seeds the 15 subjects (the 14 parser domains plus `general`) at version 1,
  and is idempotent.

Run: pytest tests/chatbot/test_migration_chat_topics_0001.py -q (under tests/chatbot/: it reads the
parser domains from `turn/policy_rows.py`, and only this folder may import the chatbot package)
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.services.chatbot.turn import policy_rows

from tests import _pg_fixture
from tests._pg_fixture import blank_session

STEM = "chat_topics_0001_taxonomy"
_MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / f"{STEM}.py"
DOMAINS = tuple(row["name"] for row in policy_rows.DEFAULT_DOMAIN_ROWS)


def _source() -> str:
    assert _MIGRATION.exists(), f"{_MIGRATION} does not exist"
    return _MIGRATION.read_text()


def _module():
    _source()
    spec = importlib.util.spec_from_file_location("zzt_migration_chat_topics_0001", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(db, direction: str = "upgrade") -> None:
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        getattr(_module(), direction)()


def _schema() -> str:
    return f"{_pg_fixture._BLANK['name']}_chatbot"


# --------------------------------------------------------------------------- static


def test_the_revision_fits_the_version_column_and_has_a_parent():
    module = _module()
    assert module.revision == STEM
    assert len(module.revision) <= 32
    assert isinstance(module.down_revision, str) and module.down_revision


def test_upgrade_only_adds():
    tree = ast.parse(_source())
    upgrade = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None
    )
    assert upgrade is not None, "the migration has no upgrade()"
    body = ast.get_source_segment(_source(), upgrade) or ""
    assert not re.search(r"\bdrop_\w+\(", body), "upgrade() must not drop anything"
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE\s+FROM)\b", body, re.I)
    assert "alter_column" not in body


# --------------------------------------------------------------------------- functional


@pytest.fixture
def db():
    with blank_session() as session:
        # The scratch schema is built from the models; take out what this migration adds so
        # the DDL under test is what puts it back.
        schema = _schema()
        session.execute(text(f'DROP TABLE IF EXISTS "{schema}".chat_topics CASCADE'))
        session.execute(text(f'ALTER TABLE "{schema}".chat_ask_facts DROP COLUMN IF EXISTS language'))
        session.execute(text(f'ALTER TABLE "{schema}".chat_ask_items DROP COLUMN IF EXISTS confidence'))
        yield session


def _columns(db, table: str) -> set:
    return {
        r[0]
        for r in db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": _schema(), "t": table},
        )
    }


def test_upgrade_creates_the_topics_table_and_the_two_columns(db):
    _run(db)
    assert {
        "code",
        "parent_code",
        "label",
        "definition",
        "version",
        "status",
        "sort_order",
    } <= _columns(db, "chat_topics")
    assert "language" in _columns(db, "chat_ask_facts")
    assert "confidence" in _columns(db, "chat_ask_items")


def test_the_subjects_are_the_parser_domains_plus_general_at_version_1(db):
    _run(db)
    rows = db.execute(
        text(f'SELECT code, parent_code, version, status, label FROM "{_schema()}".chat_topics')
    ).all()
    assert {r.code for r in rows} == set(DOMAINS) | {"general"}
    assert all(r.parent_code is None for r in rows), "a subject has no parent"
    assert {r.version for r in rows} == {1}
    assert {r.status for r in rows} == {"active"}
    assert all(r.label for r in rows), "every subject has words to print"


def test_a_leaf_nests_under_an_existing_subject_only(db):
    _run(db)
    schema = _schema()
    db.execute(
        text(
            f'INSERT INTO "{schema}".chat_topics (code, parent_code, label, definition, version, status) '
            "VALUES ('inventory.zzt_leaf', 'inventory', 'Leaf', 'A test leaf', 2, 'active')"
        )
    )
    with pytest.raises(Exception):
        with db.begin_nested():
            db.execute(
                text(
                    f'INSERT INTO "{schema}".chat_topics (code, parent_code, label, definition, version, status) '
                    "VALUES ('zzt_nowhere.leaf', 'zzt_nowhere', 'Leaf', 'Orphan', 2, 'active')"
                )
            )


def test_upgrade_is_idempotent(db):
    _run(db)
    _run(db)
    count = db.execute(text(f'SELECT count(*) FROM "{_schema()}".chat_topics')).scalar()
    assert count == len(DOMAINS) + 1
