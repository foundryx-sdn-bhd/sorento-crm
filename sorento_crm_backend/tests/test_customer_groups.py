"""Lane CUSTOMER-GROUP, slice S1 red tests (AC-3 .. AC-9).

UAC ``documentation/plans/master-data/customer-group-acceptance-criteria.md``, PLAN
``documentation/plans/master-data/PLAN-customer-group-2oct.md``.

Written BEFORE any implementation exists. Each test is expected to fail because the route is
missing (domain 404 check), the ``customer_groups`` table / ``customers.customer_group_id``
column is missing, or the response field is absent. Groups and memberships are seeded with raw
SQL so no test imports a model that does not exist yet; the blank schema is built from the
models, so those statements fail on the missing table until the model lands.

Postgres only (``blank_session``); every row is seeded here, CI's database is empty.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.form_action_grace import WINDOW_DESTRUCTIVE, WINDOW_REVERSIBLE
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, seed_mocha
from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
BOTH = frozenset({SORENTO, MOCHA_ID})

VIEW = "order_management.customers.view"
EDIT = "order_management.customers.edit"
DELETE = "order_management.customers.delete"
ALL_PERMS = {VIEW, EDIT, DELETE}

GROUPS = "/api/v1/order-management/customer-groups"
CUSTOMERS = "/api/v1/order-management/customers"


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def db():
    with blank_session() as session:
        seed_mocha(session)
        set_company_scope(session, BOTH)
        yield session


@pytest.fixture
def state(db):
    return {"scope": frozenset({SORENTO}), "granted": set(ALL_PERMS)}


@pytest.fixture
def actor(db):
    from app.models.user import User

    row = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def client(db, state, actor, monkeypatch):
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        set_company_scope(db, state["scope"])
        return state["scope"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in state["granted"],
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------- seeding


def _customer(db, *, company_id=SORENTO, name=None, level=None, group_id=None, active=True) -> Customer:
    code = unique_code("ZZTC")
    row = Customer(
        customer_code=code,
        customer_name=name or f"ZZT customer {code}",
        company_id=company_id,
        account_level=level,
        is_active=active,
    )
    db.add(row)
    db.flush()
    if group_id:
        _put_in_group(db, row.id, group_id)
    return row


def _group(db, name: str, company_id=SORENTO) -> str:
    gid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
            "VALUES (:i, :c, :n, now(), now())"
        ),
        {"i": gid, "c": company_id, "n": name},
    )
    return gid


def _put_in_group(db, customer_id, group_id) -> None:
    db.execute(
        text("UPDATE customers SET customer_group_id = :g WHERE id = :c"),
        {"g": group_id, "c": str(customer_id)},
    )


def _group_of(db, customer_id):
    db.expire_all()
    value = db.execute(
        text("SELECT customer_group_id FROM customers WHERE id = :c"), {"c": str(customer_id)}
    ).scalar_one()
    return str(value) if value is not None else None


def _group_exists(db, group_id) -> bool:
    return bool(
        db.execute(text("SELECT count(*) FROM customer_groups WHERE id = :g"), {"g": group_id}).scalar_one()
    )


def _domain_404(response) -> bool:
    """A 404 raised by the handler, not FastAPI's 'no such route'."""
    return response.status_code == 404 and response.json() != {"detail": "Not Found"}


def _one(response):
    body = response.json()
    return body["data"] if isinstance(body, dict) and "data" in body else body


def _park(client, *, action_key, entity_type, entity_id, payload=None):
    return client.post(
        "/api/v1/pending-actions",
        json={
            "action_key": action_key,
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "payload": payload or {},
        },
    )


def _commit(client, db, *, entity_type, entity_id, action_id):
    from app.models.sla import SlaFormAction

    db.query(SlaFormAction).filter(SlaFormAction.id == action_id).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()
    return client.get(
        "/api/v1/pending-actions/current",
        params={"entity_type": entity_type, "entity_id": str(entity_id)},
    )


# ============================================================ AC-3 list


def test_ac3_list_has_ledger_count_and_sorted_distinct_levels(client, db):
    g = _group(db, "ZZT ALPHA")
    for level in (2, 1, 2, None):
        _customer(db, level=level, group_id=g)
    empty = _group(db, "ZZT EMPTY")
    db.commit()

    response = client.get(GROUPS + "/", params={"query": "ZZT"})

    assert response.status_code == 200, response.text
    rows = {r["name"]: r for r in response.json()["data"]}
    assert rows["ZZT ALPHA"]["id"] == g
    assert rows["ZZT ALPHA"]["ledger_count"] == 4
    assert rows["ZZT ALPHA"]["account_levels"] == [1, 2]
    assert rows["ZZT EMPTY"]["id"] == empty
    assert rows["ZZT EMPTY"]["ledger_count"] == 0
    assert rows["ZZT EMPTY"]["account_levels"] == []


def test_ac3_list_searches_pages_sorts_and_is_company_scoped(client, db):
    for n in ("ZZT BETA", "ZZT GAMMA", "ZZT DELTA"):
        _group(db, n)
    _group(db, "ZZT MOCHA ONLY", company_id=MOCHA_ID)
    db.commit()

    searched = client.get(GROUPS + "/", params={"query": "gamma"})
    assert [r["name"] for r in searched.json()["data"]] == ["ZZT GAMMA"]

    page = client.get(GROUPS + "/", params={"query": "ZZT", "limit": 2, "page": 2, "sort": "name", "dir": "asc"})
    assert page.status_code == 200, page.text
    assert [r["name"] for r in page.json()["data"]] == ["ZZT GAMMA"]
    assert page.json()["pagination"]["total"] == 3

    names = [r["name"] for r in client.get(GROUPS + "/", params={"query": "ZZT"}).json()["data"]]
    assert "ZZT MOCHA ONLY" not in names


# ============================================================ AC-4 create / rename / get


def test_ac4_create_returns_201_and_get_one_reads_it_back(client, db):
    created = client.post(GROUPS + "/", json={"name": "  ZZT NEW GROUP  "})

    assert created.status_code == 201, created.text
    body = _one(created)
    assert body["name"] == "ZZT NEW GROUP"
    fetched = client.get(f"{GROUPS}/{body['id']}")
    assert fetched.status_code == 200, fetched.text
    got = _one(fetched)
    assert got["name"] == "ZZT NEW GROUP"
    assert got["ledger_count"] == 0
    assert got["account_levels"] == []


def test_ac4_duplicate_name_any_case_is_409_blank_is_422_other_company_is_fine(client, db, state):
    _group(db, "ZZT DUP")
    _group(db, "ZZT ELSEWHERE", company_id=MOCHA_ID)
    db.commit()

    dup = client.post(GROUPS + "/", json={"name": "zzt dup"})
    assert dup.status_code == 409, dup.text
    assert "A group with this name already exists" in dup.text

    blank = client.post(GROUPS + "/", json={"name": "   "})
    assert blank.status_code == 422, blank.text

    same_name_other_company = client.post(GROUPS + "/", json={"name": "ZZT ELSEWHERE"})
    assert same_name_other_company.status_code == 201, same_name_other_company.text


def test_ac4_patch_renames_under_the_same_rules(client, db):
    g = _group(db, "ZZT OLD")
    _group(db, "ZZT TAKEN")
    db.commit()

    renamed = client.patch(f"{GROUPS}/{g}", json={"name": "ZZT RENAMED"})
    assert renamed.status_code == 200, renamed.text
    assert _one(renamed)["name"] == "ZZT RENAMED"

    assert client.patch(f"{GROUPS}/{g}", json={"name": "zzt taken"}).status_code == 409
    assert client.patch(f"{GROUPS}/{g}", json={"name": ""}).status_code == 422


# ============================================================ AC-5 members


def test_ac5_members_list_has_code_name_level_and_active(client, db):
    g = _group(db, "ZZT MEMBERS")
    other = _group(db, "ZZT OTHER")
    a = _customer(db, name="ZZT M ONE", level=1, group_id=g)
    b = _customer(db, name="ZZT M TWO", level=2, group_id=g, active=False)
    _customer(db, name="ZZT STRANGER", group_id=other)
    db.commit()

    response = client.get(f"{GROUPS}/{g}/customers")

    assert response.status_code == 200, response.text
    rows = {r["id"]: r for r in response.json()["data"]}
    assert set(rows) == {a.id, b.id}
    assert rows[a.id]["customer_code"] == a.customer_code
    assert rows[a.id]["customer_name"] == "ZZT M ONE"
    assert rows[a.id]["account_level"] == 1
    assert rows[b.id]["is_active"] is False

    found = client.get(f"{GROUPS}/{g}/customers", params={"query": "TWO"})
    assert [r["id"] for r in found.json()["data"]] == [b.id]


def test_ac5_add_moves_customers_in_including_from_another_group(client, db):
    g = _group(db, "ZZT TARGET")
    old = _group(db, "ZZT OLD HOME")
    free = _customer(db)
    moved = _customer(db, group_id=old)
    db.commit()

    response = client.post(f"{GROUPS}/{g}/customers", json={"customer_ids": [free.id, moved.id]})

    assert response.status_code == 200, response.text
    assert _group_of(db, free.id) == g
    assert _group_of(db, moved.id) == g


def test_ac5_add_with_an_unknown_id_is_404_and_writes_nothing(client, db):
    g = _group(db, "ZZT ALLNONE")
    real = _customer(db)
    db.commit()

    response = client.post(
        f"{GROUPS}/{g}/customers", json={"customer_ids": [real.id, str(uuid.uuid4())]}
    )

    assert _domain_404(response), response.text
    assert _group_of(db, real.id) is None


def test_ac5_add_with_another_companys_customer_is_422_and_writes_nothing(client, db, state):
    state["scope"] = BOTH
    g = _group(db, "ZZT SORENTO GROUP")
    mine = _customer(db)
    theirs = _customer(db, company_id=MOCHA_ID)
    db.commit()

    response = client.post(f"{GROUPS}/{g}/customers", json={"customer_ids": [mine.id, theirs.id]})

    assert response.status_code == 422, response.text
    assert _group_of(db, mine.id) is None
    assert _group_of(db, theirs.id) is None


# ============================================================ AC-6 pending actions


def test_ac6_actions_are_registered_with_the_right_window_and_permission():
    from app.services.form_action_registry import get_action

    remove = get_action("customer.remove_from_group")
    delete = get_action("customer_group.delete")

    assert remove is not None, "customer.remove_from_group is not registered"
    assert remove.window == WINDOW_REVERSIBLE
    assert remove.permission == EDIT
    assert delete is not None, "customer_group.delete is not registered"
    assert delete.window == WINDOW_DESTRUCTIVE
    assert delete.permission == DELETE


def test_ac6_remove_from_group_clears_it_while_it_is_still_that_group(client, db):
    g = _group(db, "ZZT REMOVE")
    member = _customer(db, group_id=g)
    db.commit()

    parked = _park(
        client,
        action_key="customer.remove_from_group",
        entity_type="customer",
        entity_id=member.id,
        payload={"customer_group_id": g},
    )
    assert parked.status_code == 202, parked.text
    committed = _commit(client, db, entity_type="customer", entity_id=member.id, action_id=parked.json()["id"])

    assert committed.json()["last_outcome"]["status"] == "committed", committed.json()
    assert _group_of(db, member.id) is None


def test_ac6_remove_from_group_is_409_when_moved_to_another_group_meanwhile(client, db):
    g = _group(db, "ZZT FIRST")
    other = _group(db, "ZZT SECOND")
    member = _customer(db, group_id=g)
    db.commit()

    parked = _park(
        client,
        action_key="customer.remove_from_group",
        entity_type="customer",
        entity_id=member.id,
        payload={"customer_group_id": g},
    )
    assert parked.status_code == 202, parked.text
    _put_in_group(db, member.id, other)
    db.commit()
    committed = _commit(client, db, entity_type="customer", entity_id=member.id, action_id=parked.json()["id"])

    outcome = committed.json()["last_outcome"]
    assert outcome["status"] == "failed", committed.json()
    assert "409" in str(outcome) or "moved" in str(outcome).lower() or outcome["error_text"], outcome
    assert _group_of(db, member.id) == other


def test_ac6_group_delete_hard_deletes_and_leaves_members_ungrouped(client, db):
    g = _group(db, "ZZT DOOMED")
    member = _customer(db, group_id=g)
    db.commit()

    parked = _park(
        client, action_key="customer_group.delete", entity_type="customer_group", entity_id=g
    )
    assert parked.status_code == 202, parked.text
    committed = _commit(client, db, entity_type="customer_group", entity_id=g, action_id=parked.json()["id"])

    assert committed.json()["last_outcome"]["status"] == "committed", committed.json()
    db.expire_all()
    assert not _group_exists(db, g)
    assert db.get(Customer, member.id) is not None
    assert _group_of(db, member.id) is None


# ============================================================ AC-7 customers API


def test_ac7_customer_response_carries_group_id_and_name(client, db):
    g = _group(db, "ZZT SHOWN")
    member = _customer(db, group_id=g)
    loner = _customer(db)
    db.commit()

    got = client.get(f"{CUSTOMERS}/{member.id}")
    assert got.status_code == 200, got.text
    assert got.json()["customer_group_id"] == g
    assert got.json()["customer_group_name"] == "ZZT SHOWN"

    none = client.get(f"{CUSTOMERS}/{loner.id}").json()
    assert "customer_group_id" in none and none["customer_group_id"] is None
    assert none["customer_group_name"] is None


def test_ac7_put_sets_and_clears_the_group(client, db):
    g = _group(db, "ZZT SETTABLE")
    c = _customer(db)
    db.commit()

    setr = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": g})
    assert setr.status_code == 200, setr.text
    assert setr.json()["customer_group_id"] == g
    assert setr.json()["customer_group_name"] == "ZZT SETTABLE"
    assert _group_of(db, c.id) == g

    cleared = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["customer_group_id"] is None
    assert _group_of(db, c.id) is None


def test_ac7_put_with_another_companys_group_is_422(client, db, state):
    state["scope"] = BOTH
    theirs = _group(db, "ZZT MOCHA GROUP", company_id=MOCHA_ID)
    c = _customer(db)
    db.commit()

    response = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": theirs})

    assert response.status_code == 422, response.text
    assert _group_of(db, c.id) is None


def test_ac7_customer_list_filters_by_group(client, db):
    g = _group(db, "ZZT FILTER")
    inside = _customer(db, group_id=g)
    _customer(db)
    db.commit()

    response = client.get(CUSTOMERS + "/", params={"customer_group_id": g})

    assert response.status_code == 200, response.text
    assert [r["id"] for r in response.json()["data"]] == [inside.id]


# ============================================================ AC-8 permissions


def test_ac8_put_changing_the_group_needs_customers_edit_unchanged_does_not(client, db, state):
    g = _group(db, "ZZT PUT GUARD")
    other = _group(db, "ZZT PUT OTHER")
    c = _customer(db, group_id=g)
    db.commit()
    state["granted"] = {VIEW}

    changed = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": other})
    assert changed.status_code == 403, changed.text
    assert "Permission required: order_management.customers.edit" in changed.text
    assert _group_of(db, c.id) == g

    cleared = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": None})
    assert cleared.status_code == 403, cleared.text
    assert _group_of(db, c.id) == g

    unchanged = client.put(f"{CUSTOMERS}/{c.id}", json={"customer_group_id": g, "customer_name": "ZZT RENAMED"})
    assert unchanged.status_code == 200, unchanged.text
    assert _group_of(db, c.id) == g


def test_ac8_reads_need_customers_view(client, db, state):
    g = _group(db, "ZZT GUARDED")
    db.commit()
    state["granted"] = {EDIT, DELETE}

    # The route must exist: a missing route is a bare 404, never a 403.
    assert client.get(GROUPS + "/").status_code == 403
    assert client.get(f"{GROUPS}/{g}").status_code == 403
    assert client.get(f"{GROUPS}/{g}/customers").status_code == 403
    assert client.get(GROUPS + "/select").status_code == 403


def test_ac8_writes_need_customers_edit_and_delete_needs_delete(client, db, state):
    g = _group(db, "ZZT NOEDIT")
    c = _customer(db)
    db.commit()
    state["granted"] = {VIEW}

    assert client.post(GROUPS + "/", json={"name": "ZZT X"}).status_code == 403
    assert client.patch(f"{GROUPS}/{g}", json={"name": "ZZT Y"}).status_code == 403
    assert client.post(f"{GROUPS}/{g}/customers", json={"customer_ids": [c.id]}).status_code == 403
    assert _group_of(db, c.id) is None

    state["granted"] = {VIEW, EDIT}
    parked = _park(client, action_key="customer_group.delete", entity_type="customer_group", entity_id=g)
    assert parked.status_code == 403, parked.text


def test_ac8_a_group_of_another_company_is_404(client, db):
    theirs = _group(db, "ZZT HIDDEN", company_id=MOCHA_ID)
    db.commit()

    assert _domain_404(client.get(f"{GROUPS}/{theirs}"))
    assert _domain_404(client.patch(f"{GROUPS}/{theirs}", json={"name": "ZZT STOLEN"}))
    assert _domain_404(client.get(f"{GROUPS}/{theirs}/customers"))


# ============================================================ AC-9 select


def test_ac9_select_returns_id_name_ledger_count_searched_on_the_server(client, db):
    g = _group(db, "ZZT SELECT ME")
    _group(db, "ZZT NOT THIS ONE")
    _customer(db, group_id=g)
    _customer(db, group_id=g)
    db.commit()

    response = client.get(GROUPS + "/select", params={"query": "select me"})

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [(r["id"], r["name"], r["ledger_count"]) for r in rows] == [(g, "ZZT SELECT ME", 2)]


# ============================================ mock section 5: picker shows current group


def test_mock5_customers_select_carries_group_id_and_name_null_when_none(client, db):
    g = _group(db, "ZZT PICKER GROUP")
    member = _customer(db, group_id=g)
    loner = _customer(db)
    db.commit()

    response = client.get(f"{CUSTOMERS}/select", params={"query": "ZZT", "limit": 50})

    assert response.status_code == 200, response.text
    rows = {r["id"]: r for r in response.json()["data"]}
    assert rows[str(member.id)]["customer_group_id"] == g
    assert rows[str(member.id)]["customer_group_name"] == "ZZT PICKER GROUP"
    assert "customer_group_name" in rows[str(loner.id)]
    assert rows[str(loner.id)]["customer_group_name"] is None
    assert rows[str(loner.id)]["customer_group_id"] is None


# ============================================================ security-review round


def test_ac8_create_with_a_group_needs_customers_edit(client, db, state):
    g = _group(db, "ZZT CREATE GUARD")
    db.commit()
    code = unique_code("ZZTNEW")
    body = {"customer_code": code, "customer_name": "ZZT Created", "customer_group_id": g}
    state["granted"] = {VIEW}

    denied = client.post(CUSTOMERS + "/", json=body)
    assert denied.status_code == 403, denied.text
    assert "Permission required: order_management.customers.edit" in denied.text
    assert db.query(Customer).filter(Customer.customer_code == code).count() == 0

    state["granted"] = {VIEW, EDIT}
    allowed = client.post(CUSTOMERS + "/", json=body)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["customer_group_id"] == g


def test_a_malformed_group_filter_is_422_not_a_leaking_500(client, db):
    response = client.get(CUSTOMERS + "/", params={"customer_group_id": "not-a-uuid"})

    assert response.status_code == 422, response.text
    assert "InvalidTextRepresentation" not in response.text
    assert "SELECT" not in response.text


def test_ac5_add_with_more_than_500_ids_is_422_and_writes_nothing(client, db):
    g = _group(db, "ZZT CAP")
    real = _customer(db)
    db.commit()
    ids = [real.id] + [str(uuid.uuid4()) for _ in range(500)]

    response = client.post(f"{GROUPS}/{g}/customers", json={"customer_ids": ids})

    assert response.status_code == 422, response.text
    assert _group_of(db, real.id) is None
