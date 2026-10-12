"""What an edit to a warehouse must actually persist, and answer with.

Three defects live here, and all three are invisible to the frontend tests, which mock the
mutation and so stop at the hook boundary:

* ``update_warehouse`` applied the payload and committed without touching ``updated_at``,
  and the column carries no ``onupdate``. So the detail header's "Last Updated" showed the
  creation date forever, no matter how many times the record was edited.
* Clearing "Draws stock from" has to write NULL. The only test proving that mocked the
  mutation, so a later "strip nulls from the payload" change on either side would silently
  restore the user-reported bug (a pool can be changed but never unset) with nothing red.
  Both transitions are covered here: set a pool, and clear one.
* The RESPONSE to that edit has to agree with itself. ``pool_warehouse_code`` is not a
  mapped column - it is a plain attribute that ``_attach_pool_codes`` hangs on the instance
  for display - so neither ``commit()`` nor ``refresh()`` touches it, and whatever the read
  at the top of ``update_warehouse`` attached survives the write. Clearing a pool therefore
  answered ``pool_warehouse_id: null`` beside the OLD code, and setting one answered the new
  id beside a stale null. One response contradicting itself, in the one field the screen
  actually renders (the id is a UUID and never shown). Both directions are pinned below.

Postgres only, against a blank copy of the real schema (``blank_session``). Nothing is
borrowed from an existing row: ``warehouse_code`` is globally unique, and CI's database is
empty, so every warehouse this file needs is seeded here with a marker-prefixed code.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.inventory import Warehouse
from app.schemas.inventory import WarehouseUpdate
from app.services.inventory_service import WarehouseService
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/inventory/warehouses"

# A stale timestamp no clock will ever produce, so "the edit advanced it" cannot pass by
# accident.
STALE = datetime(2020, 1, 1, 0, 0, 0)


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _warehouse(db, *, pool_id: str | None = None, updated_at: datetime | None = None) -> str:
    """One warehouse of this test's own making. Returns its id."""
    wid = str(uuid.uuid4())
    db.add(
        Warehouse(
            id=wid,
            warehouse_code=unique_code("WH")[:50],
            warehouse_name="Planning config test",
            location="Shah Alam",
            is_active=True,
            counts_as_available=True,
            pool_warehouse_id=pool_id,
            updated_at=updated_at,
        )
    )
    db.commit()
    return wid


def _row(db, wid: str) -> Warehouse:
    db.expire_all()
    return db.query(Warehouse).filter(Warehouse.id == wid).one()


def _code(db, wid: str) -> str:
    """The readable code of a seeded warehouse. Generated per test, so never hardcoded."""
    return _row(db, wid).warehouse_code


# --------------------------------------------------------------------------- #
# TestClient wiring
# --------------------------------------------------------------------------- #
_USER = {"id": str(uuid.uuid4()), "email": "warehouse-editor@example.com"}


@pytest.fixture()
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    # No auth header on the test request, so the router-level resolver would compute UNSET
    # (fail-closed) and 404 the seeded rows. No-op it and the session keeps the conftest
    # ``after_begin`` Sorento default that those rows were auto-stamped with; the Sorento
    # company row itself is seeded into the blank schema by the ``after_create`` hook.
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# =========================================================================== #
# updated_at
# =========================================================================== #
def test_update_advances_updated_at(db):
    """An edit must move ``updated_at``, or "Last Updated" is a lie."""
    wid = _warehouse(db, updated_at=STALE)
    before = datetime.utcnow()

    WarehouseService(db).update_warehouse(
        wid, WarehouseUpdate(warehouse_name="Renamed by the test")
    )

    row = _row(db, wid)
    assert row.updated_at is not None
    assert row.updated_at > STALE
    # Naive UTC, like every other datetime column in this codebase - not Malaysia local,
    # which would read 8 hours ahead of the clock the rest of the row is stamped against.
    assert row.updated_at >= before - timedelta(seconds=5)
    assert row.updated_at <= datetime.utcnow() + timedelta(seconds=5)


def test_first_edit_of_a_never_edited_warehouse_stamps_updated_at(db):
    """``updated_at`` starts NULL, so the very first edit has to fill it."""
    wid = _warehouse(db)
    assert _row(db, wid).updated_at is None

    WarehouseService(db).update_warehouse(wid, WarehouseUpdate(is_active=False))

    row = _row(db, wid)
    assert row.updated_at is not None
    assert row.updated_at >= row.created_at


def test_the_route_advances_updated_at(client, db):
    wid = _warehouse(db, updated_at=STALE)

    response = client.put(f"{BASE}/{wid}", json={"location": "Klang"})

    assert response.status_code == 200, response.text
    assert _row(db, wid).updated_at > STALE
    # And the response carries the fresh value, so the header updates without a refetch.
    assert response.json()["updated_at"] > STALE.isoformat()


# =========================================================================== #
# pool_warehouse_id - both transitions
# =========================================================================== #
def test_clearing_the_pool_writes_null(client, db):
    """The user-reported bug: a pool could be changed but never unset."""
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)
    assert _row(db, child).pool_warehouse_id == pool

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": None})

    assert response.status_code == 200, response.text
    assert response.json()["pool_warehouse_id"] is None
    assert _row(db, child).pool_warehouse_id is None


def test_setting_the_pool_writes_the_id(client, db):
    pool = _warehouse(db)
    child = _warehouse(db)
    assert _row(db, child).pool_warehouse_id is None

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": pool})

    assert response.status_code == 200, response.text
    assert response.json()["pool_warehouse_id"] == pool
    assert _row(db, child).pool_warehouse_id == pool


def test_clearing_the_pool_leaves_the_other_fields_alone(client, db):
    """A partial payload must not blank the fields it omits."""
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": None})

    assert response.status_code == 200, response.text
    row = _row(db, child)
    assert row.pool_warehouse_id is None
    assert row.warehouse_name == "Planning config test"
    assert row.location == "Shah Alam"
    assert row.is_active is True
    assert row.counts_as_available is True


# =========================================================================== #
# pool_warehouse_code - the response must agree with itself
#
# `pool_warehouse_code` is the only half of this pair the user ever sees: the id is a UUID
# and no UUID may reach the UI. So a correct id beside a stale code is, from the screen's
# side, simply the wrong answer.
# =========================================================================== #
def test_clearing_the_pool_also_clears_the_returned_pool_code(db):
    """Nothing expires a plain attribute, so the pre-write code has to be re-resolved."""
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)
    pool_code = _code(db, pool)

    updated = WarehouseService(db).update_warehouse(
        child, WarehouseUpdate(pool_warehouse_id=None)
    )

    assert updated.pool_warehouse_id is None
    assert updated.pool_warehouse_code is None, (
        f"cleared the pool but still answered with {pool_code!r}"
    )


def test_setting_the_pool_returns_the_new_pool_code(db):
    """The other direction: a warehouse that had no pool must not answer with a stale null."""
    pool = _warehouse(db)
    child = _warehouse(db)
    pool_code = _code(db, pool)

    updated = WarehouseService(db).update_warehouse(
        child, WarehouseUpdate(pool_warehouse_id=pool)
    )

    assert updated.pool_warehouse_id == pool
    assert updated.pool_warehouse_code == pool_code


def test_repointing_the_pool_returns_the_new_code_not_the_old_one(db):
    """Changing the pool is the case a "clear it on null" shortcut would still get wrong."""
    first = _warehouse(db)
    second = _warehouse(db)
    child = _warehouse(db, pool_id=first)

    updated = WarehouseService(db).update_warehouse(
        child, WarehouseUpdate(pool_warehouse_id=second)
    )

    assert updated.pool_warehouse_code == _code(db, second)
    assert updated.pool_warehouse_code != _code(db, first)


def test_the_route_answers_a_cleared_pool_with_a_null_code(client, db):
    """Through the real serializer: `WarehouseResponse` carries the code, so the body is
    what the detail page renders."""
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": None})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pool_warehouse_id"] is None
    assert body["pool_warehouse_code"] is None


def test_the_route_answers_a_set_pool_with_its_code(client, db):
    pool = _warehouse(db)
    child = _warehouse(db)

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": pool})

    assert response.status_code == 200, response.text
    assert response.json()["pool_warehouse_code"] == _code(db, pool)


def test_an_edit_that_never_mentions_the_pool_keeps_the_code(client, db):
    """Renaming a warehouse must not drop the code the screen shows beside it."""
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)

    response = client.put(f"{BASE}/{child}", json={"warehouse_name": "Renamed"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pool_warehouse_id"] == pool
    assert body["pool_warehouse_code"] == _code(db, pool)


def test_the_route_answers_a_repointed_pool_with_the_new_code(client, db):
    """Both codes non-null, through the serializer.

    The narrower fix of "blank the code whenever the incoming id is null" satisfies the two
    route tests above and still answers this one with the previous pool's code, which is the
    worst of the three cases: the screen shows a real, wrong warehouse rather than a blank.
    """
    first = _warehouse(db)
    second = _warehouse(db)
    child = _warehouse(db, pool_id=first)

    response = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": second})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pool_warehouse_id"] == second
    assert body["pool_warehouse_code"] == _code(db, second)
    assert body["pool_warehouse_code"] != _code(db, first)


# --------------------------------------------------------------------------- #
# The write and the read that follows it must agree.
#
# The detail page renders the PUT body and then refetches. If the two disagree the value
# the user just saved changes on its own a moment later, which reads as the save having
# been rejected. The GET has always been right, so this pair also pins WHICH of the two is
# the reference answer.
# --------------------------------------------------------------------------- #
def test_a_cleared_pool_reads_back_the_same_as_the_write_answered(client, db):
    pool = _warehouse(db)
    child = _warehouse(db, pool_id=pool)

    written = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": None})
    read = client.get(f"{BASE}/{child}")

    assert written.status_code == 200, written.text
    assert read.status_code == 200, read.text
    pair = ("pool_warehouse_id", "pool_warehouse_code")
    assert {k: written.json()[k] for k in pair} == {k: read.json()[k] for k in pair}
    assert read.json()["pool_warehouse_code"] is None


def test_a_set_pool_reads_back_the_same_as_the_write_answered(client, db):
    pool = _warehouse(db)
    child = _warehouse(db)

    written = client.put(f"{BASE}/{child}", json={"pool_warehouse_id": pool})
    read = client.get(f"{BASE}/{child}")

    assert written.status_code == 200, written.text
    assert read.status_code == 200, read.text
    pair = ("pool_warehouse_id", "pool_warehouse_code")
    assert {k: written.json()[k] for k in pair} == {k: read.json()[k] for k in pair}
    assert read.json()["pool_warehouse_code"] == _code(db, pool)


# =========================================================================== #
# A warehouse that is its own pool
#
# Site pools (BRW, HQ) point ``pool_warehouse_id`` at themselves (migration 311), and that
# is what puts them in the Stock Debt span (`procurement_service._planning_span_map`). The
# edit form used to show a self-pool as empty and send ``null`` on every save; it now sends
# only changed fields, so the guard that matters here is: an update that does not carry the
# key leaves the self-pool alone, and only an explicit ``null`` clears it.
# =========================================================================== #
def _self_pooled(db) -> str:
    wid = _warehouse(db)
    _row(db, wid).pool_warehouse_id = wid
    db.commit()
    return wid


def test_a_save_that_never_mentions_the_pool_keeps_a_self_pool(db):
    wid = _self_pooled(db)

    WarehouseService(db).update_warehouse(wid, WarehouseUpdate(warehouse_name="Renamed"))

    assert _row(db, wid).pool_warehouse_id == wid


def test_the_route_keeps_a_self_pool_on_an_edit_without_the_key(client, db):
    wid = _self_pooled(db)

    response = client.put(f"{BASE}/{wid}", json={"location": "Klang"})

    assert response.status_code == 200, response.text
    assert response.json()["pool_warehouse_id"] == wid
    assert _row(db, wid).pool_warehouse_id == wid


def test_a_warehouse_can_be_made_its_own_pool(client, db):
    wid = _warehouse(db)

    response = client.put(f"{BASE}/{wid}", json={"pool_warehouse_id": wid})

    assert response.status_code == 200, response.text
    assert _row(db, wid).pool_warehouse_id == wid
    assert response.json()["pool_warehouse_code"] == _code(db, wid)


def test_an_explicit_null_still_clears_a_self_pool(client, db):
    wid = _self_pooled(db)

    response = client.put(f"{BASE}/{wid}", json={"pool_warehouse_id": None})

    assert response.status_code == 200, response.text
    assert _row(db, wid).pool_warehouse_id is None
