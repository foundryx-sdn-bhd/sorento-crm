"""Migration `chat_facts_0001_tables`: the three chat-facts tables and the analytics grant
(tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md, "Tables". The
file `alembic/versions/chat_facts_0001_tables.py` does not exist yet, so every test fails on
that.

Two halves, as in `tests/test_migration_422_report_views.py`:
* static: the revision id fits `alembic_version.version_num` (<= 32 chars) and `upgrade()`
  only adds (the lane is additive; nothing in it may drop or rewrite existing data);
* functional: `upgrade()` runs against the blank scratch schema inside a rolled-back
  transaction (the connection already translates the `chatbot` schema to the scratch one),
  and the grant sweep gives `chatbot.analytics.view` to exactly the roles that hold
  `system.chat_history.view`. A permission granted to nobody is indistinguishable from a
  broken feature (PRINCIPLES.md DoD gate 3).

Run: pytest tests/test_migration_chat_facts_0001.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.user import UserPermission, UserRole, UserRolePermission

from tests import _pg_fixture
from tests._pg_fixture import blank_session

SLUG = "chatbot.analytics.view"
SOURCE = "system.chat_history.view"
STEM = "chat_facts_0001_tables"
_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{STEM}.py"
TABLES = ("chat_ask_items", "chat_ask_facts", "chat_episode_facts")


def _source() -> str:
    assert _MIGRATION.exists(), f"{_MIGRATION} does not exist"
    return _MIGRATION.read_text()


def _module():
    _source()
    spec = importlib.util.spec_from_file_location("zzt_migration_chat_facts_0001", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(db, direction: str = "upgrade") -> None:
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        getattr(_module(), direction)()


# --------------------------------------------------------------------------- static


def test_the_migration_file_exists_and_its_revision_fits_the_version_column():
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
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE\s+\S+\s+(DROP|ALTER))\b", body, re.I), (
        "upgrade() must not drop, truncate, delete or alter existing objects"
    )
    assert "alter_column" not in body


def test_the_migration_names_the_three_tables_the_schema_and_the_grant():
    source = _source()
    for table in TABLES:
        assert table in source, table
    assert "chatbot" in source
    assert SLUG in source and SOURCE in source


# --------------------------------------------------------------------------- functional


def _permission(db, slug: str) -> str:
    row = db.query(UserPermission).filter_by(slug=slug).first()
    if row is None:
        row = UserPermission(id=str(uuid.uuid4()), slug=slug, name=slug, description="")
        db.add(row)
        db.flush()
    return row.id


def _role(db, slug: str) -> str:
    row = UserRole(id=str(uuid.uuid4()), slug=slug, name=slug, description="", is_protected=False)
    db.add(row)
    db.flush()
    return row.id


def _grant(db, role_id: str, slug: str) -> None:
    db.add(UserRolePermission(id=str(uuid.uuid4()), role_id=role_id, permission_id=_permission(db, slug)))
    db.flush()


def _slugs_for(db, role_id: str) -> set:
    rows = db.execute(
        text(
            "SELECT p.slug FROM user_role_permissions rp "
            "JOIN user_permissions p ON p.id = rp.permission_id WHERE rp.role_id = :role"
        ),
        {"role": role_id},
    ).all()
    return {row[0] for row in rows}


@pytest.fixture
def db():
    with blank_session() as session:
        # The scratch schema is built from the models (once they exist); drop the tables so
        # the DDL under test is what puts them back.
        schema = f"{_pg_fixture._BLANK['name']}_chatbot"
        for table in TABLES:
            session.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE'))
        _permission(session, SOURCE)
        yield session


def _chatbot_tables(db) -> set:
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    return {
        r[0]
        for r in db.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": schema}
        )
    }


def test_upgrade_creates_the_three_tables_in_the_chatbot_schema(db):
    _run(db)
    assert set(TABLES) <= _chatbot_tables(db)


def _insert_ask(db, *, turn_id=None, seq=1) -> str:
    ask_id = str(uuid.uuid4())
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    db.execute(
        text(
            f'INSERT INTO "{schema}".chat_ask_facts '
            "(id, source, turn_id, ask_seq, contact_respond_id, audience, asked_at, outcome) "
            "VALUES (:id, 'trace', :t, :s, 'ZZT-mig', 'dealer', now(), 'answered')"
        ),
        {"id": ask_id, "t": turn_id or str(uuid.uuid4()), "s": seq},
    )
    return ask_id


def test_a_turn_has_at_most_one_row_per_ask_seq(db):
    _run(db)
    turn = str(uuid.uuid4())
    _insert_ask(db, turn_id=turn, seq=1)
    _insert_ask(db, turn_id=turn, seq=2)  # a second ask of the same turn is allowed
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            _insert_ask(db, turn_id=turn, seq=1)


def test_the_column_defaults_hold(db):
    _run(db)
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    ask_id = _insert_ask(db)
    row = db.execute(
        text(
            f'SELECT ask_seq, channel, tools, is_media, phantom_dropped, created_at '
            f'FROM "{schema}".chat_ask_facts WHERE id = :id'
        ),
        {"id": ask_id},
    ).one()
    assert row.channel == "whatsapp"
    assert list(row.tools) == []
    assert row.is_media is False and row.phantom_dropped is False
    assert row.created_at is not None


def test_items_are_unique_per_ask_kind_ref_and_role_and_follow_their_ask(db):
    _run(db)
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    ask_id = _insert_ask(db)

    def item(role: str = "named") -> None:
        db.execute(
            text(
                f'INSERT INTO "{schema}".chat_ask_items (id, ask_id, kind, ref_id, role, resolved_by) '
                "VALUES (:id, :a, 'product', 'ZZT-ref', :r, 'parser')"
            ),
            {"id": str(uuid.uuid4()), "a": ask_id, "r": role},
        )

    item("named")
    item("shown")  # same product in the other role is a different row
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            item("named")

    db.execute(text(f'DELETE FROM "{schema}".chat_ask_facts WHERE id = :id'), {"id": ask_id})
    assert db.execute(text(f'SELECT count(*) FROM "{schema}".chat_ask_items')).scalar() == 0, "ON DELETE CASCADE"


def test_the_episode_row_survives_with_no_frame_behind_it(db):
    _run(db)
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    db.execute(
        text(
            f'INSERT INTO "{schema}".chat_episode_facts '
            "(frame_id, contact_respond_id, opened_at, closed_at, turn_count, ask_count, failed_turns, handover) "
            "VALUES (:f, 'ZZT-mig', now(), now(), 3, 3, 0, false)"
        ),
        {"f": str(uuid.uuid4())},
    )
    assert db.execute(text(f'SELECT count(*) FROM "{schema}".chat_episode_facts')).scalar() == 1


def test_the_grant_goes_to_every_holder_of_chat_history_view_and_nobody_else(db):
    holder = _role(db, "zzt-cf-holder")
    other_holder = _role(db, "zzt-cf-holder-2")
    bystander = _role(db, "zzt-cf-bystander")
    _grant(db, holder, SOURCE)
    _grant(db, other_holder, SOURCE)
    _grant(db, other_holder, "zzt.unrelated.view")
    _grant(db, bystander, "zzt.unrelated.view")

    _run(db)

    assert SLUG in _slugs_for(db, holder)
    assert SLUG in _slugs_for(db, other_holder)
    assert SLUG not in _slugs_for(db, bystander)
    assert db.query(UserPermission).filter_by(slug=SLUG).count() == 1


def test_the_grant_is_idempotent(db):
    holder = _role(db, "zzt-cf-idem")
    _grant(db, holder, SOURCE)

    _run(db)
    # The tables exist now; a second run must not trip over them or double-grant.
    schema = f"{_pg_fixture._BLANK['name']}_chatbot"
    for table in TABLES:
        db.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE'))
    _run(db)

    assert db.query(UserPermission).filter_by(slug=SLUG).count() == 1
    count = db.execute(
        text(
            "SELECT count(*) FROM user_role_permissions rp JOIN user_permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r AND p.slug = :s"
        ),
        {"r": holder, "s": SLUG},
    ).scalar()
    assert count == 1
