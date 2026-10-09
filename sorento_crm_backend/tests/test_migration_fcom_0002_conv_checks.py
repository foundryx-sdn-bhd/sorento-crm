"""fcom_0002 tested by running it over the CHECK constraints migration 150 created.

The form comment thread shipped (fcom_0001) without widening the two CHECKs on
entity_conversation_messages, so every form comment 500'd on the dev database while every
test passed: the tests built the table from the model, which declared no CHECKs. These
tests rebuild the constraints exactly as 150 wrote them, prove a form comment is refused,
run the widening migration and prove every form kind and the form_comment scope are then
accepted, while the commercial threads keep working and junk is still refused.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests._pg_fixture import blank_session

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "fcom_0002_conv_check_widen.py"
)

FORM_KINDS = ("complaint", "stock_inquiry", "purchase_request", "sponsorship_form")
COMMERCIAL_KINDS = (
    "commercial_project",
    "commercial_master_quotation",
    "commercial_project_task",
    "commercial_master_quotation_task",
    "commercial_lead",
)


def _run(db, direction: str = "upgrade"):
    spec = importlib.util.spec_from_file_location("fcom_0002", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = MigrationContext.configure(db.connection())
    with Operations.context(ctx):
        getattr(module, direction)()


@pytest.fixture
def db():
    """The blank schema with the two CHECKs rewound to exactly what migration 150 created."""
    with blank_session() as session:
        session.execute(
            text(
                "ALTER TABLE entity_conversation_messages "
                "DROP CONSTRAINT IF EXISTS ck_entity_conversation_messages_entity_type, "
                "DROP CONSTRAINT IF EXISTS ck_entity_conversation_messages_scope"
            )
        )
        session.execute(
            text(
                "ALTER TABLE entity_conversation_messages ADD CONSTRAINT "
                "ck_entity_conversation_messages_entity_type CHECK (entity_type IN ("
                "'commercial_project','commercial_master_quotation','commercial_project_task',"
                "'commercial_master_quotation_task','commercial_lead'))"
            )
        )
        session.execute(
            text(
                "ALTER TABLE entity_conversation_messages ADD CONSTRAINT "
                "ck_entity_conversation_messages_scope CHECK (scope IN ('activity','internal_note'))"
            )
        )
        yield session


def _insert(db, entity_type: str, scope: str) -> None:
    db.execute(
        text(
            "INSERT INTO entity_conversation_messages (id, entity_type, entity_id, scope, body_html) "
            "VALUES (:i, :t, :e, :s, 'x')"
        ),
        {"i": str(uuid.uuid4()), "t": entity_type, "e": str(uuid.uuid4()), "s": scope},
    )


def _refused(db, entity_type: str, scope: str) -> bool:
    nested = db.begin_nested()
    try:
        _insert(db, entity_type, scope)
    except IntegrityError:
        nested.rollback()
        return True
    nested.rollback()
    return False


def test_before_the_migration_a_form_comment_is_refused(db):
    # The dev-database failure, reproduced: this is what every form comment hit.
    assert _refused(db, "stock_inquiry", "form_comment")


@pytest.mark.parametrize("kind", FORM_KINDS)
def test_after_the_migration_every_form_kind_takes_a_form_comment(db, kind):
    _run(db)
    _insert(db, kind, "form_comment")


@pytest.mark.parametrize("kind", COMMERCIAL_KINDS)
@pytest.mark.parametrize("scope", ["activity", "internal_note"])
def test_the_commercial_threads_still_work_after_the_migration(db, kind, scope):
    _run(db)
    _insert(db, kind, scope)


def test_unknown_types_and_scopes_are_still_refused(db):
    _run(db)
    assert _refused(db, "invoice", "form_comment")
    assert _refused(db, "stock_inquiry", "chat")


def test_downgrade_restores_the_original_constraints(db):
    _run(db)
    _run(db, "downgrade")
    assert _refused(db, "stock_inquiry", "form_comment")
    _insert(db, "commercial_lead", "activity")


def test_the_model_declares_the_same_constraints_as_the_migration():
    """The model is what create_all (tests, CI bootstrap) builds from; it must carry the CHECKs
    the migrations create, or a test passes against a schema production does not have."""
    from app.models.entity_conversation import EntityConversationMessage

    checks = {
        c.name: str(c.sqltext)
        for c in EntityConversationMessage.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert set(checks) == {
        "ck_entity_conversation_messages_entity_type",
        "ck_entity_conversation_messages_scope",
    }
    for kind in FORM_KINDS + COMMERCIAL_KINDS:
        assert f"'{kind}'" in checks["ck_entity_conversation_messages_entity_type"]
    for scope in ("activity", "internal_note", "form_comment"):
        assert f"'{scope}'" in checks["ck_entity_conversation_messages_scope"]
