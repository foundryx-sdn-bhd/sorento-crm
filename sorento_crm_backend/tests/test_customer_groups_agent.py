"""Customer group agent column and mixed filter (CUSTOMER-SALES-AGENT, AC-8, AC-9 backend half).

Per group: distinct persons (`lower(btrim(coalesce(person_label, derived)))`) over ledgers that
carry an agent. Response gains `sales_agent_label` (str|None) and `sales_agent_mixed` (bool);
the list takes `agent_mixed=true`. Seeded with raw SQL on a blank Postgres schema.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
GROUPS = "/api/v1/order-management/customer-groups"
VIEW = "order_management.customers.view"


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({SORENTO}))
        yield session


@pytest.fixture
def client(db, monkeypatch):
    from app.models.user import User

    actor = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(actor)
    db.flush()
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        set_company_scope(db, frozenset({SORENTO}))
        return frozenset({SORENTO})

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, uid, slug: slug == VIEW
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _agent(db, code: str, label=None) -> str:
    aid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO sales_agents (id, sales_agent, source, is_active, person_label, company_id) "
            "VALUES (:i, :c, 'manual', true, :l, NULL)"
        ),
        {"i": aid, "c": code, "l": label},
    )
    return aid


def _group(db, name: str) -> str:
    gid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
            "VALUES (:i, :c, :n, now(), now())"
        ),
        {"i": gid, "c": SORENTO, "n": name},
    )
    return gid


def _ledger(db, group_id, agent_id=None) -> str:
    cid = str(uuid.uuid4())
    code = unique_code("ZZTC")
    db.execute(
        text(
            "INSERT INTO customers (id, customer_code, customer_name, company_id, is_active, "
            "customer_group_id, sales_agent_id, created_at, updated_at) "
            "VALUES (:i, :c, :n, :co, true, :g, :a, now(), now())"
        ),
        {"i": cid, "c": code, "n": f"ZZT {code}", "co": SORENTO, "g": group_id, "a": agent_id},
    )
    return cid


def _row(client, group_id):
    res = client.get(f"{GROUPS}/{group_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    return body["data"] if "data" in body else body


def _listed(client, name):
    res = client.get(GROUPS + "/", params={"query": name})
    assert res.status_code == 200, res.text
    return {r["name"]: r for r in res.json()["data"]}[name]


def test_ledgers_of_one_derived_person_show_that_person_unassigned_ignored(client, db):
    g = _group(db, "GRP-1")
    a1 = _agent(db, "ZZCSA AGENT-B - I")
    a2 = _agent(db, "ZZCSA AGENT-B III")
    _ledger(db, g, a1)
    _ledger(db, g, a2)
    _ledger(db, g, None)
    db.commit()

    for row in (_listed(client, "GRP-1"), _row(client, g)):
        assert row["sales_agent_label"] == "ZZCSA AGENT-B"
        assert row["sales_agent_mixed"] is False


def test_same_typed_label_in_any_case_and_spacing_is_one_person(client, db):
    g = _group(db, "GRP-2")
    _ledger(db, g, _agent(db, "A1 I", "Same Person"))
    _ledger(db, g, _agent(db, "B2 I", " same person "))
    db.commit()

    for row in (_listed(client, "GRP-2"), _row(client, g)):
        assert row["sales_agent_label"].lower().strip() == "same person"
        assert row["sales_agent_mixed"] is False


def test_two_different_persons_are_mixed_with_no_label(client, db):
    g = _group(db, "GRP-3")
    _ledger(db, g, _agent(db, "ZZCSA AGENT-A I"))
    _ledger(db, g, _agent(db, "ZZCSA AGENT-D II"))
    db.commit()

    for row in (_listed(client, "GRP-3"), _row(client, g)):
        assert row["sales_agent_label"] is None
        assert row["sales_agent_mixed"] is True


def test_group_with_no_assigned_ledger_is_blank_and_not_mixed(client, db):
    g = _group(db, "GRP-4")
    _ledger(db, g, None)
    _group(db, "GRP-5")
    db.commit()

    for row in (_listed(client, "GRP-4"), _listed(client, "GRP-5"), _row(client, g)):
        assert row["sales_agent_label"] is None
        assert row["sales_agent_mixed"] is False


def test_agent_mixed_filter_lists_only_mixed_groups_and_counts_them(client, db):
    one = _agent(db, "ZZCSA AGENT-A I")
    two = _agent(db, "ZZCSA AGENT-D II")
    for name in ("GRP-M1", "GRP-M2"):
        g = _group(db, name)
        _ledger(db, g, one)
        _ledger(db, g, two)
    solo = _group(db, "GRP-S1")
    _ledger(db, solo, one)
    _ledger(db, solo, one)
    _group(db, "GRP-E1")
    db.commit()

    res = client.get(GROUPS + "/", params={"agent_mixed": "true", "limit": 1, "page": 2})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pagination"]["total"] == 2
    assert len(body["data"]) == 1

    full = client.get(GROUPS + "/", params={"agent_mixed": "true"}).json()
    assert sorted(r["name"] for r in full["data"]) == ["GRP-M1", "GRP-M2"]
    assert all(r["sales_agent_mixed"] is True for r in full["data"])


def test_agent_mixed_filter_ignores_one_derived_person_and_one_typed_label(client, db):
    f1 = _agent(db, "ZZCSA AGENT-F I")
    f3 = _agent(db, "ZZCSA AGENT-F III")
    t1 = _agent(db, "ZZCSA T1 I", "Same Typed")
    t2 = _agent(db, "ZZCSA T2 I", " same typed ")
    one_person = _group(db, "GRP-P1")
    _ledger(db, one_person, f1)
    _ledger(db, one_person, f3)
    typed = _group(db, "GRP-P2")
    _ledger(db, typed, t1)
    _ledger(db, typed, t2)
    mixed = _group(db, "GRP-P3")
    _ledger(db, mixed, f1)
    _ledger(db, mixed, t1)
    db.commit()

    full = client.get(GROUPS + "/", params={"agent_mixed": "true"}).json()

    assert [r["name"] for r in full["data"]] == ["GRP-P3"]
    assert full["pagination"]["total"] == 1
