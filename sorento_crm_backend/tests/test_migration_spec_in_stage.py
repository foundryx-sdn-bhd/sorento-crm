"""The "Spec In" stage migration, exercised rather than assumed (owner hand-test, 13 Oct).

Three things: the default funnel's "Specified" rung reads "Spec In", the edge into it reads
"Spec In" too, and a project on Spec In can be moved back to Registered. An admin's own
wording is left alone, and running the upgrade twice changes nothing.

The migration's own ``upgrade()`` runs against a blank schema inside a rolled-back
transaction, so nothing here depends on what the local database happens to hold.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.status import Status, StatusTransition
from app.services import project_seed_service

from ._pg_fixture import blank_session

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "spec_in_0001_stage_label.py"
)


def _run_upgrade(db) -> None:
    spec = importlib.util.spec_from_file_location("migration_spec_in", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = MigrationContext.configure(db.connection())
    with Operations.context(context):
        module.upgrade()


def _status(db, key: str, label: str, order: int) -> Status:
    row = Status(
        id=str(uuid.uuid4()),
        entity_type="project",
        scope_id=None,
        key=key,
        label=label,
        sort_order=order,
    )
    db.add(row)
    db.flush()
    return row


def _edge(db, source: Status, target: Status, label: str) -> None:
    db.add(
        StatusTransition(
            id=str(uuid.uuid4()),
            entity_type="project",
            scope_id=None,
            from_status_id=source.id,
            to_status_id=target.id,
            label=label,
            trigger_mode="manual",
        )
    )
    db.flush()


def _edges(db, source: Status, target: Status) -> list:
    return db.execute(
        text(
            "SELECT label FROM status_transitions "
            "WHERE entity_type = 'project' AND from_status_id = :f AND to_status_id = :t"
        ),
        {"f": source.id, "t": target.id},
    ).scalars().all()


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def test_renames_the_seeded_rung_and_its_edge_and_adds_the_way_back(db):
    registered = _status(db, "registered", "Registered", 1)
    specified = _status(db, "specified", "Specified", 2)
    _edge(db, registered, specified, "Spec in")

    _run_upgrade(db)

    db.expire_all()
    assert db.get(Status, specified.id).label == "Spec In"
    assert _edges(db, registered, specified) == ["Spec In"]
    assert _edges(db, specified, registered) == ["Back to registered"]


def test_leaves_an_admins_own_wording_alone_and_is_idempotent(db):
    registered = _status(db, "registered", "Registered", 1)
    specified = _status(db, "specified", "Specification", 2)
    _edge(db, registered, specified, "Specify")

    _run_upgrade(db)
    _run_upgrade(db)

    db.expire_all()
    assert db.get(Status, specified.id).label == "Specification"
    assert _edges(db, registered, specified) == ["Specify"]
    # The way back is added once, however many times the upgrade runs.
    assert _edges(db, specified, registered) == ["Back to registered"]


def test_a_fresh_install_seeds_spec_in_and_the_way_back(db):
    project_seed_service.seed_default_funnel(db)

    rows = {
        row.key: row
        for row in db.query(Status)
        .filter(Status.entity_type == "project", Status.scope_id.is_(None))
        .all()
    }
    assert rows["specified"].label == "Spec In"
    assert _edges(db, rows["registered"], rows["specified"]) == ["Spec In"]
    assert _edges(db, rows["specified"], rows["registered"]) == ["Back to registered"]
