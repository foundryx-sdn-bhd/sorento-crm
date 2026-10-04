"""Lane CONTACT-BULK-ACCESS, slice S1 (A1): POST /user-management/contacts/bulk-copy-access.

UAC ``documentation/plans/contacts/contact-bulk-access-3oct-acceptance-criteria.md`` A1.1-A1.13.
Red tests written before the implementation. Postgres only (``blank_session``); every row is
seeded here. Assertions go through the HTTP response (``response_model`` drops undeclared
fields), then through direct DB reads of the whole access set.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import (
    AccessAgent,
    ContactAccessType,
    ContactAgentAccess,
    ContactFieldReveal,
    RespondContact,
    RespondContactCustomer,
)
from app.models.base import set_company_scope
from app.models.order import Customer
from app.services import contact_field_reveal_service
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS
from app.services.user_service import UserPermissionService

from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
BULK = "/api/v1/user-management/contacts/bulk-copy-access"
VIEW = "user_management.contacts.view"
EDIT = "user_management.contacts.edit"
LABELS = dict(FIELD_REVEAL_KEYS)
COST = "purchase_orders.cost"
SELLABLE = "inventory.sellable"
PLACED = "purchase_orders.placed"
#: DO-ASK-SIMPLIFY (owner, 4 Oct 2026): every DO field reveal defaults ON for every contact, so a
#: contact's EFFECTIVE reveals (`granted_keys`) include these unless a granted=False row hides one.
DO_KEYS = sorted(k for k, _label in FIELD_REVEAL_KEYS if k.startswith("delivery_orders."))
WAREHOUSE = "delivery_orders.warehouse"

FACET_ORDER = [
    "access_types",
    "tier",
    "chatbot_stock_allowed",
    "notify_salesman",
    "packing_list_allowed",
    "chatbot_eta_offset_applied",
    "escalation_allowed",
    "field_reveals",
    "agent_access",
]
D1 = datetime(2026, 1, 1)
D2 = datetime(2026, 12, 31)


@pytest.fixture
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({SORENTO}))
        yield session


@pytest.fixture
def perms():
    """Mutable permission set the monkeypatched check reads on every call."""
    return {"granted": {VIEW, EDIT}}


@pytest.fixture
def client(db, perms, monkeypatch):
    from app.models.user import User

    actor = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(actor)
    db.flush()
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({SORENTO})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug in perms["granted"],
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- seeding


def _access_type(db, tag: str) -> ContactAccessType:
    code = unique_code(f"zat{tag}")
    row = ContactAccessType(code=code, name=f"ZZT type {code}", is_active=True, sort_order=1)
    db.add(row)
    db.flush()
    return row


def _agent(db, tag: str) -> AccessAgent:
    code = unique_code(f"zag{tag}")
    row = AccessAgent(code=code, name=f"ZZT agent {code}")
    db.add(row)
    db.flush()
    return row


def _contact(
    db,
    tag: str,
    *,
    types=(),
    tier=None,
    reveals=(),
    stock=True,
    notify=False,
    packing=False,
    eta=True,
    escalation=True,
    agents=(),
    memory=None,
    language="en",
) -> RespondContact:
    """agents = [(agent, is_allowed, valid_from, valid_to)]."""
    profile = {"language": language}
    if tier is not None:
        profile["tier"] = tier
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT {tag} {unique_code('c')}",
        chatbot_profile=profile,
        chatbot_memory_level=memory,
        chatbot_stock_allowed=stock,
        notify_salesman=notify,
        packing_list_allowed=packing,
        chatbot_eta_offset_applied=eta,
        escalation_allowed=escalation,
    )
    db.add(row)
    db.flush()
    row.access_types = list(types)
    db.flush()
    for key in reveals:
        db.add(ContactFieldReveal(respond_contact_id=row.id, field_key=key, granted=True))
    for agent, allowed, vfrom, vto in agents:
        db.add(
            ContactAgentAccess(
                respond_contact_id=row.id,
                respond_contact_phone=row.phone_number,
                agent_id=agent.id,
                is_allowed=allowed,
                valid_from=vfrom,
                valid_to=vto,
            )
        )
    db.flush()
    return row


def _iso(value):
    return value.isoformat() if value is not None else None


def _snap(db, contact_id: str) -> dict:
    """The whole access set plus the untouched-by-copy fields, read fresh from the DB."""
    db.expire_all()
    c = db.get(RespondContact, contact_id)
    # The effective set (DO keys default ON), the same read the chatbot and the Field reveals card use.
    revealed = contact_field_reveal_service.granted_keys(db, contact_id)
    agent_rows = (
        db.query(ContactAgentAccess, AccessAgent)
        .join(AccessAgent, AccessAgent.id == ContactAgentAccess.agent_id)
        .filter(
            (ContactAgentAccess.respond_contact_id == contact_id)
            | (
                ContactAgentAccess.respond_contact_id.is_(None)
                & (ContactAgentAccess.respond_contact_phone == c.phone_number)
            )
        )
        .all()
    )
    agents = sorted(
        (a.code, r.is_allowed, _iso(r.valid_from), _iso(r.valid_to)) for r, a in agent_rows
    )
    return {
        "types": sorted(t.code for t in c.access_types),
        "tier": (c.chatbot_profile or {}).get("tier"),
        "stock": c.chatbot_stock_allowed,
        "notify": c.notify_salesman,
        "packing": c.packing_list_allowed,
        "eta": c.chatbot_eta_offset_applied,
        "escalation": c.escalation_allowed,
        "reveals": revealed,
        "agents": agents,
        "agent_row_count": len(agent_rows),
    }


def _untouched(db, contact_id: str) -> dict:
    db.expire_all()
    c = db.get(RespondContact, contact_id)
    links = sorted(
        (r.customer_id, str(r.company_id))
        for r in db.query(RespondContactCustomer).filter(RespondContactCustomer.contact_id == contact_id)
    )
    return {
        "name": c.name,
        "phone": c.phone_number,
        "memory": c.chatbot_memory_level,
        "language": (c.chatbot_profile or {}).get("language"),
        "links": links,
    }


def _post(client, source, targets, dry_run=False):
    ids = [t if isinstance(t, str) else t.id for t in targets]
    return client.post(
        BULK,
        json={"source_contact_id": source if isinstance(source, str) else source.id, "target_contact_ids": ids, "dry_run": dry_run},
    )


def _by_id(body):
    return {r["contact_id"]: r for r in body["results"]}


@pytest.fixture
def world(db):
    """A rich source and a target that differs on EVERY facet, with extras to be removed."""
    ta, tb, tc = _access_type(db, "a"), _access_type(db, "b"), _access_type(db, "c")
    ag_a, ag_b, ag_c = _agent(db, "a"), _agent(db, "b"), _agent(db, "c")
    source = _contact(
        db,
        "source",
        types=[ta, tb],
        tier="dealer",
        reveals=[COST, SELLABLE],
        stock=False,
        notify=True,
        packing=True,
        eta=False,
        escalation=False,
        agents=[(ag_a, True, D1, D2), (ag_b, True, None, None)],
        memory="full",
        language="en",
    )
    target = _contact(
        db,
        "target",
        types=[tc],
        tier="office",
        reveals=[SELLABLE, PLACED],
        stock=True,
        notify=False,
        packing=False,
        eta=True,
        escalation=True,
        agents=[(ag_a, False, None, None), (ag_c, True, None, None)],
        memory="off",
        language="ms",
    )
    customer = Customer(customer_code=unique_code("ZZT-C"), customer_name="ZZT cust", company_id=SORENTO)
    db.add(customer)
    db.flush()
    db.add(
        RespondContactCustomer(
            contact_id=target.id, customer_id=customer.id, company_id=SORENTO, source="manual"
        )
    )
    db.flush()
    return dict(
        source=source, target=target, ta=ta, tb=tb, tc=tc, ag_a=ag_a, ag_b=ag_b, ag_c=ag_c, customer=customer
    )


# ---------------------------------------------------------------- tests


def test_a1_2_dry_run_writes_nothing_and_returns_change_rows(client, db, world):
    source, target = world["source"], world["target"]
    before = (_snap(db, source.id), _snap(db, target.id), _untouched(db, target.id))
    resp = _post(client, source, [target], dry_run=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["source"]["id"] == source.id
    assert body["source"]["label"] == source.name
    (row,) = body["results"]
    assert row["contact_id"] == target.id
    assert row["label"] == target.name
    assert row["status"] == "changed"
    assert row["error"] is None
    assert row["changes"], "a dry run still reports what would change"
    assert (_snap(db, source.id), _snap(db, target.id), _untouched(db, target.id)) == before


def test_a1_3_apply_makes_target_equal_to_source_on_every_facet(client, db, world):
    source, target = world["source"], world["target"]
    resp = _post(client, source, [target], dry_run=False)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["results"][0]["status"] == "changed"
    assert _snap(db, target.id) == _snap(db, source.id)
    # replace semantics, spelled out
    after = _snap(db, target.id)
    assert PLACED not in after["reveals"]
    assert world["ag_c"].code not in [a[0] for a in after["agents"]]
    assert world["tc"].code not in after["types"]


def test_a1_10_change_rows_carry_facet_label_before_after_and_human_added_removed(client, db, world):
    source, target = world["source"], world["target"]
    body = _post(client, source, [target], dry_run=True).json()
    changes = body["results"][0]["changes"]
    by_facet = {c["facet"]: c for c in changes}
    assert [c["facet"] for c in changes] == FACET_ORDER

    at = by_facet["access_types"]
    assert at["label"] == "Access types"
    assert at["before"] == [world["tc"].code]
    assert at["after"] == sorted([world["ta"].code, world["tb"].code])
    assert sorted(at["added"]) == sorted([world["ta"].name, world["tb"].name])
    assert at["removed"] == [world["tc"].name]

    tier = by_facet["tier"]
    assert tier["label"] == "Tier"
    assert (tier["before"], tier["after"], tier["added"], tier["removed"]) == ("office", "dealer", [], [])

    scalars = {
        "chatbot_stock_allowed": ("Stock checks", True, False),
        "notify_salesman": ("Notify salesman", False, True),
        "packing_list_allowed": ("Packing list", False, True),
        "chatbot_eta_offset_applied": ("ETA buffer days", True, False),
        "escalation_allowed": ("Escalation", True, False),
    }
    for facet, (label, before, after) in scalars.items():
        c = by_facet[facet]
        assert (c["label"], c["before"], c["after"], c["added"], c["removed"]) == (label, before, after, [], [])

    fr = by_facet["field_reveals"]
    assert fr["label"] == "Field reveals"
    assert fr["before"] == sorted([SELLABLE, PLACED, *DO_KEYS])
    assert fr["after"] == sorted([COST, SELLABLE, *DO_KEYS])
    assert fr["added"] == ["Last purchase cost"]
    assert fr["removed"] == [LABELS[PLACED]]

    aa = by_facet["agent_access"]
    assert aa["label"] == "Agent access"
    key = lambda d: d["agent_code"]  # noqa: E731
    assert sorted(aa["before"], key=key) == sorted(
        [
            {"agent_code": world["ag_a"].code, "is_allowed": False, "valid_from": None, "valid_to": None},
            {"agent_code": world["ag_c"].code, "is_allowed": True, "valid_from": None, "valid_to": None},
        ],
        key=key,
    )
    assert sorted(aa["after"], key=key) == sorted(
        [
            {"agent_code": world["ag_a"].code, "is_allowed": True, "valid_from": _iso(D1), "valid_to": _iso(D2)},
            {"agent_code": world["ag_b"].code, "is_allowed": True, "valid_from": None, "valid_to": None},
        ],
        key=key,
    )
    # agent A differs on flags/dates so it is in both lists; B only added; C only removed
    assert sorted(aa["added"]) == sorted([world["ag_a"].name, world["ag_b"].name])
    assert sorted(aa["removed"]) == sorted([world["ag_a"].name, world["ag_c"].name])


def test_a1_4_customers_memory_name_other_profile_keys_and_source_untouched(client, db, world):
    source, target = world["source"], world["target"]
    target_before = _untouched(db, target.id)
    source_before = (_snap(db, source.id), _untouched(db, source.id))
    assert target_before["links"], "seed sanity: the target has a customer link"
    assert _post(client, source, [target], dry_run=False).status_code == 200
    assert _untouched(db, target.id) == target_before
    assert target_before["language"] == "ms" and target_before["memory"] == "off"
    assert (_snap(db, source.id), _untouched(db, source.id)) == source_before


def test_a1_5_identical_target_is_unchanged_with_no_write(client, db, world, monkeypatch):
    source, target = world["source"], world["target"]
    assert _post(client, source, [target], dry_run=False).status_code == 200  # make it equal

    calls = []
    original = contact_field_reveal_service.set_granted_keys

    def counting(db_, respond_contact_id, keys, **kw):
        calls.append(respond_contact_id)
        return original(db_, respond_contact_id, keys, **kw)

    monkeypatch.setattr(contact_field_reveal_service, "set_granted_keys", counting)
    before = _snap(db, target.id)
    body = _post(client, source, [target], dry_run=False).json()
    (row,) = body["results"]
    assert row["status"] == "unchanged"
    assert row["changes"] == []
    assert row["error"] is None
    assert calls == []
    assert _snap(db, target.id) == before


def test_a1_6_source_among_targets_is_skipped(client, db, world):
    source, target = world["source"], world["target"]
    before = _snap(db, source.id)
    body = _post(client, source, [source, target], dry_run=False).json()
    rows = _by_id(body)
    assert rows[source.id]["status"] == "skipped"
    assert rows[source.id]["error"] == "This is the source contact."
    assert rows[source.id]["changes"] == []
    assert rows[target.id]["status"] == "changed"
    assert _snap(db, source.id) == before


def test_a1_7_unknown_target_fails_others_still_apply(client, db, world):
    source, target = world["source"], world["target"]
    ghost = str(uuid.uuid4())
    body = _post(client, source, [ghost, target], dry_run=False).json()
    rows = _by_id(body)
    assert rows[ghost]["status"] == "failed"
    assert rows[ghost]["error"] == "Contact not found."
    assert rows[ghost]["label"] is None
    assert rows[ghost]["changes"] == []
    assert rows[target.id]["status"] == "changed"
    assert _snap(db, target.id) == _snap(db, source.id)


def test_a1_8_failing_write_isolated_to_one_target(client, db, world, monkeypatch):
    source = world["source"]
    first = world["target"]
    doomed = _contact(db, "doomed", types=[world["tc"]], tier="office", reveals=[PLACED], agents=[(world["ag_c"], True, None, None)])
    last = _contact(db, "last", types=[world["tc"]], tier="office", reveals=[PLACED])
    doomed_before = _snap(db, doomed.id)

    original = contact_field_reveal_service.set_granted_keys

    def boom(db_, respond_contact_id, keys, **kw):
        if respond_contact_id == doomed.id:
            raise RuntimeError("simulated write failure")
        return original(db_, respond_contact_id, keys, **kw)

    monkeypatch.setattr(contact_field_reveal_service, "set_granted_keys", boom)
    resp = _post(client, source, [first, doomed, last], dry_run=False)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = _by_id(body)
    assert rows[doomed.id]["status"] == "failed"
    assert rows[doomed.id]["error"]
    assert rows[first.id]["status"] == "changed"
    assert rows[last.id]["status"] == "changed"
    assert _snap(db, doomed.id) == doomed_before, "the doomed target keeps its previous access on EVERY facet"
    assert _snap(db, first.id) == _snap(db, source.id)
    assert _snap(db, last.id) == _snap(db, source.id)
    assert body["counts"] == {"changed": 2, "unchanged": 0, "skipped": 0, "failed": 1}


def test_a1_9_unknown_source_404_and_writes_nothing(client, db, world):
    target = world["target"]
    before = _snap(db, target.id)
    resp = _post(client, str(uuid.uuid4()), [target], dry_run=False)
    assert resp.status_code == 404
    assert _snap(db, target.id) == before


def test_a1_9_empty_target_list_is_422(client, world):
    assert _post(client, world["source"], [], dry_run=True).status_code == 422


def test_a1_9_more_than_500_targets_is_422(client, world):
    ids = [str(uuid.uuid4()) for _ in range(501)]
    assert _post(client, world["source"], ids, dry_run=True).status_code == 422


def test_a1_9_exactly_500_targets_is_accepted(client, world):
    ids = [str(uuid.uuid4()) for _ in range(500)]
    resp = _post(client, world["source"], ids, dry_run=True)
    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"]["failed"] == 500


def test_a1_11_view_only_permission_is_403_and_writes_nothing(client, db, world, perms):
    perms["granted"] = {VIEW}
    target = world["target"]
    before = _snap(db, target.id)
    resp = _post(client, world["source"], [target], dry_run=False)
    assert resp.status_code == 403
    assert _snap(db, target.id) == before


def _legacy_pair(db, *, target_allowed=True):
    """Source holds agent X; the target holds the SAME agent only as a phone-keyed legacy row."""
    ag = _agent(db, "legacy")
    source = _contact(db, "lsrc", agents=[(ag, True, D1, D2)])
    target = _contact(db, "ltgt")
    db.add(
        ContactAgentAccess(
            respond_contact_id=None,
            respond_contact_phone=target.phone_number,
            agent_id=ag.id,
            is_allowed=target_allowed,
            valid_from=D1,
            valid_to=D2,
        )
    )
    db.flush()
    return ag, source, target


def _rows_for(db, agent, phone) -> int:
    db.expire_all()
    return (
        db.query(ContactAgentAccess)
        .filter(ContactAgentAccess.agent_id == agent.id, ContactAgentAccess.respond_contact_phone == phone)
        .count()
    )


def test_a1_12_legacy_phone_only_row_counts_as_held_no_duplicate(client, db):
    ag, source, target = _legacy_pair(db)
    body = _post(client, source, [target], dry_run=False).json()
    (row,) = body["results"]
    assert row["status"] == "unchanged"
    assert [c for c in row["changes"] if c["facet"] == "agent_access"] == []
    assert _rows_for(db, ag, target.phone_number) == 1


def test_a1_12_legacy_row_with_different_flags_is_updated_in_place_not_duplicated(client, db):
    ag, source, target = _legacy_pair(db, target_allowed=False)
    body = _post(client, source, [target], dry_run=False).json()
    assert body["results"][0]["status"] == "changed"
    assert _rows_for(db, ag, target.phone_number) == 1
    assert _snap(db, target.id)["agents"] == _snap(db, source.id)["agents"]


def test_a1_13_dry_run_rows_equal_apply_rows_for_the_same_request(client, db, world):
    source, target = world["source"], world["target"]
    same = _contact(db, "same", types=[world["ta"], world["tb"]], tier="dealer", reveals=[COST, SELLABLE],
                    stock=False, notify=True, packing=True, eta=False, escalation=False,
                    agents=[(world["ag_a"], True, D1, D2), (world["ag_b"], True, None, None)])
    ghost = str(uuid.uuid4())
    targets = [target, same, source, ghost]
    preview = _post(client, source, targets, dry_run=True).json()
    applied = _post(client, source, targets, dry_run=False).json()
    assert preview["dry_run"] is True and applied["dry_run"] is False
    strip = lambda body: [(r["contact_id"], r["status"], r["changes"], r["error"]) for r in body["results"]]  # noqa: E731
    assert strip(preview) == strip(applied)
    assert preview["counts"] == applied["counts"]


def test_a1_1_results_in_request_order_with_counts_and_duplicates_collapsed(client, db, world):
    source, target = world["source"], world["target"]
    same = _contact(db, "same2", types=[world["ta"], world["tb"]], tier="dealer", reveals=[COST, SELLABLE],
                    stock=False, notify=True, packing=True, eta=False, escalation=False,
                    agents=[(world["ag_a"], True, D1, D2), (world["ag_b"], True, None, None)])
    ghost = str(uuid.uuid4())
    order = [ghost, target.id, source.id, same.id, target.id]  # target repeated
    body = _post(client, source, order, dry_run=True).json()
    assert [r["contact_id"] for r in body["results"]] == [ghost, target.id, source.id, same.id]
    assert [r["status"] for r in body["results"]] == ["failed", "changed", "skipped", "unchanged"]
    assert body["counts"] == {"changed": 1, "unchanged": 1, "skipped": 1, "failed": 1}


# ---------------------------------------------------------------- security review S1 / N1


def _copy_audit_rows(db, contact_id: str):
    from app.models.audit import AuditLog

    db.expire_all()
    return (
        db.query(AuditLog)
        .filter(AuditLog.entity_id == contact_id, AuditLog.event == "contact.access_copied", AuditLog.action == "EVENT")
        .all()
    )


def test_s1_one_audit_row_per_copied_contact_naming_the_source_none_on_dry_run(client, db, world):
    source, target = world["source"], world["target"]
    assert _post(client, source, [target], dry_run=True).status_code == 200
    assert _copy_audit_rows(db, target.id) == []

    assert _post(client, source, [target], dry_run=False).status_code == 200
    (row,) = _copy_audit_rows(db, target.id)
    assert row.new_values["copied_from_contact_id"] == source.id
    assert source.name in (row.description or "") and source.id not in (row.description or "")
    assert row.old_values["tier"] == "office" and row.new_values["tier"] == "dealer"
    assert row.new_values["access_types"] == sorted([world["ta"].code, world["tb"].code])
    assert _copy_audit_rows(db, source.id) == []


def test_n1_failed_target_reason_carries_no_internal_error_text(client, db, world, monkeypatch):
    source, target = world["source"], world["target"]

    def boom(*a, **kw):
        raise RuntimeError("SECRET-SQL-TEXT")

    monkeypatch.setattr(contact_field_reveal_service, "set_granted_keys", boom)
    (row,) = _post(client, source, [target], dry_run=False).json()["results"]
    assert row["status"] == "failed"
    assert "SECRET-SQL-TEXT" not in row["error"]
    assert _copy_audit_rows(db, target.id) == []


# ---------------------------------------------------------------- DO reveals default ON (owner, 4 Oct 2026)


def _hide(db, contact, key):
    db.add(ContactFieldReveal(respond_contact_id=contact.id, field_key=key, granted=False))
    db.flush()


def _false_do_rows(db, contact_id):
    db.expire_all()
    return sorted(
        k
        for (k,) in db.query(ContactFieldReveal.field_key).filter(
            ContactFieldReveal.respond_contact_id == contact_id,
            ContactFieldReveal.granted.is_(False),
            ContactFieldReveal.field_key.like("delivery_orders.%"),
        )
    )


def _facets(row):
    return {c["facet"]: c for c in row["changes"]}


def test_do_default_on_copy_from_a_fresh_source_leaves_every_do_key_on_for_the_target(client, db):
    # The target differs on reveals (it holds one extra key), so the copy really writes them.
    source, target = _contact(db, "freshsrc"), _contact(db, "freshtgt", reveals=[COST])
    assert _post(client, source, [target], dry_run=False).status_code == 200
    assert contact_field_reveal_service.granted_keys(db, target.id) == DO_KEYS
    assert COST not in contact_field_reveal_service.granted_keys(db, target.id)
    assert _false_do_rows(db, target.id) == [], "a fresh source must not hide any DO field on the target"


def test_do_default_on_copy_carries_a_turned_off_do_key_and_keeps_the_rest_on(client, db):
    source, target = _contact(db, "offsrc"), _contact(db, "offtgt")
    _hide(db, source, WAREHOUSE)
    assert _post(client, source, [target], dry_run=False).status_code == 200
    assert contact_field_reveal_service.granted_keys(db, target.id) == [k for k in DO_KEYS if k != WAREHOUSE]
    assert _false_do_rows(db, target.id) == [WAREHOUSE]


def test_do_default_on_dry_run_between_two_fresh_contacts_shows_no_reveal_difference(client, db):
    source, target = _contact(db, "dryfsrc"), _contact(db, "dryftgt")
    (row,) = _post(client, source, [target], dry_run=True).json()["results"]
    assert row["status"] == "unchanged", row
    assert "field_reveals" not in _facets(row), row["changes"]


def test_do_default_on_dry_run_shows_exactly_the_warehouse_difference(client, db):
    fresh, hidden = _contact(db, "dryfresh"), _contact(db, "dryhidden")
    _hide(db, hidden, WAREHOUSE)

    (row,) = _post(client, fresh, [hidden], dry_run=True).json()["results"]
    assert [c["facet"] for c in row["changes"]] == ["field_reveals"], row["changes"]
    change = row["changes"][0]
    assert change["added"] == [LABELS[WAREHOUSE]] and change["removed"] == [], change

    (row,) = _post(client, hidden, [fresh], dry_run=True).json()["results"]
    assert [c["facet"] for c in row["changes"]] == ["field_reveals"], row["changes"]
    change = row["changes"][0]
    assert change["removed"] == [LABELS[WAREHOUSE]] and change["added"] == [], change
