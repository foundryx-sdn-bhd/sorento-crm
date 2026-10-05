"""Which fields an agent may be told (slice 4, redesigned).

The risk this gate exists for: `/incoming-stock/list` and `crm_incoming_stock_list`
already serve dealer- and salesperson-facing agents. Adding ETA delay, CIDB
inspection/approval and gatepass to that response means that on the day it ships,
every one of those agents can answer questions it was never meant to answer.

Four properties carry the whole thing:

1. **An unentitled caller's DATA is byte-identical to today's.** Not "nulled" - the
   keys are ABSENT. Absent means "you may not see this"; null means "not reached
   yet". An LLM reading the response narrates a null as the latter, which is a lie
   with the confidence of a fact. (The response gains one additive sibling key,
   `field_access`, carrying the reason; the rows themselves are untouched.)
2. **A denial says WHICH thing is wrong** - the contact did not resolve at all,
   or the agent was never assigned, or it was assigned and this field is not
   ticked. Each needs a different fix, so a bare `false` sends someone to the
   wrong screen - and naming the wrong one is worse, because that screen then
   looks perfectly correct.
3. **A contact's question is answered with the CONTACT's grants**, never the API
   key's. n8n calls with a privileged act-as user; if that decided it, every
   contact would be entitled the moment n8n asked on their behalf.
4. **Default deny.** A gated field with no row is invisible. Adding a sensitive
   column must not expose it to the 53 contacts already holding the agent.

Everything fails closed: unresolvable contact, inactive agent, expired grant, a
lookup that raises.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.models.access import (
    AccessAgent,
    AgentFieldAccess,
    ContactAgentAccess,
    RespondContact,
)
from app.services.field_access import (
    ALLOWED,
    CONTACT_NOT_FOUND,
    DEFAULT_ALLOWED,
    FIELD_NOT_ALLOWED,
    GATED_FIELDS,
    NOT_GATED,
    apply_field_access,
    check_attributes,
    contact_agent_codes,
    decide,
    resolve_contact_id,
)
from tests._pg_fixture import blank_session, unique_code

OWNER = "incoming_stock_enquiries"
RESOURCE = "incoming_stock"


@pytest.fixture
def db():
    with blank_session() as s:
        yield s


def _agent(db, *, code=OWNER, active=True) -> AccessAgent:
    agent = AccessAgent(
        id=str(uuid.uuid4()), code=code, name="Incoming stock enquiries", is_active=active
    )
    db.add(agent)
    db.flush()
    return agent


def _contact(db, *, respond_io_id=None) -> RespondContact:
    contact = RespondContact(
        id=unique_code("CONTACT"),
        phone_number=f"+60{uuid.uuid4().int % 10**9:09d}",
        name="ZZT Contact",
        respond_io_id=respond_io_id,
    )
    db.add(contact)
    db.flush()
    return contact


def _grant(db, contact, agent, *, allowed=True, valid_from=None, valid_to=None):
    db.add(
        ContactAgentAccess(
            id=str(uuid.uuid4()),
            respond_contact_id=contact.id,
            respond_contact_phone=contact.phone_number,
            agent_id=agent.id,
            is_allowed=allowed,
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )
    db.flush()


def _field(db, field_key, *, agent_code=OWNER, contact=None, allowed=True):
    db.add(
        AgentFieldAccess(
            id=str(uuid.uuid4()),
            agent_code=agent_code,
            resource=RESOURCE,
            field_key=field_key,
            contact_id=contact.id if contact else None,
            is_allowed=allowed,
        )
    )
    db.flush()


def _tree_fields(*fields):
    return [f"incoming_stock.{f}" for f in fields]


def _seed_registry(db):
    """The incoming domain and its 23 field rows, as the migration leaves them."""
    from tests.chatbot._access_seed import make_domain, make_field

    make_domain(db, "incoming")
    for f in GATED_FIELDS[RESOURCE]:
        make_field(db, "incoming", f"incoming_stock.{f}")


def _give_tree(db, contact, *fields, domain=True):
    """S5a: the gate reads the access tree. Give `contact` a role ticking `fields`
    (and the incoming domain unless `domain` is False)."""
    from tests.chatbot._access_seed import give_role, make_role, rid

    _seed_registry(db)
    role = make_role(
        db, rid("r"), domains=["incoming"] if domain else [], fields=_tree_fields(*fields)
    )
    give_role(db, contact.id, role)
    return role


def _entitled_contact(db, *fields):
    """A contact whose role ticks `fields` (and the incoming domain). It also holds the
    legacy agent, which must make no difference."""
    contact = _contact(db)
    agent = _agent(db)
    _grant(db, contact, agent)
    _give_tree(db, contact, *fields)
    return contact, agent


def _role_of(db, contact):
    from app.models.chatbot_access import ContactChatbotRole

    return db.query(ContactChatbotRole.role_id).filter(ContactChatbotRole.contact_id == contact.id).scalar()


def _payload() -> dict:
    """The response shape as it exists TODAY, plus the new gated keys."""
    return {
        "data": [
            {
                "shipment_number": "SHP-1",
                "shipping_container_number": "GXYU5106903",
                "estimated_arrival_date": "2026-07-18",
                "eta_delay_date": "2026-07-22",
                "gatepass_date": None,
                "liner_code": "CMA",
                "attachment": {"id": "att-1", "original_filename": "pl.pdf"},
                "lines": [{"product_code": "P-1", "remaining_incoming_quantity": 5}],
            }
        ],
        "empty": False,
        "pagination": {"total": 1, "page": 1, "limit": 10},
    }


#: The rows as the endpoint returned them before this slice.
TODAYS_ROWS = [
    {
        "shipment_number": "SHP-1",
        "shipping_container_number": "GXYU5106903",
        "estimated_arrival_date": "2026-07-18",
        "attachment": {"id": "att-1", "original_filename": "pl.pdf"},
        "lines": [{"product_code": "P-1", "remaining_incoming_quantity": 5}],
    }
]


# ------------------------------------------------------------------- stripping


def test_an_unentitled_callers_rows_are_byte_identical_to_todays(db):
    """The assertion that says "shipping this breaks nothing"."""
    gated = apply_field_access(db, _payload(), resource=RESOURCE, current_user={"id": None})

    assert gated["data"] == TODAYS_ROWS
    assert gated["pagination"] == {"total": 1, "page": 1, "limit": 10}
    assert gated["empty"] is False


def test_gated_keys_are_absent_not_null(db):
    """Absent means "you may not see this"; null means "not reached yet". An LLM
    reads a null as the second and says so out loud."""
    gated = apply_field_access(db, _payload(), resource=RESOURCE, current_user={"id": None})
    row = gated["data"][0]

    for key in GATED_FIELDS[RESOURCE]:
        if key in DEFAULT_ALLOWED:
            continue  # ships allowed; covered by its own test below
        assert key not in row, f"{key} must be absent, not present-and-null"
    # ...and specifically the one that was already null before stripping.
    assert "gatepass_date" not in row


def test_the_public_eta_still_flows(db):
    """ETA is gateable but ships ALLOWED, so the deploy is inert.

    `estimated_arrival_date` is what the 53 contacts holding
    `incoming_stock_enquiries` ask about every day. Adding it to the registry must
    let an admin REVOKE it, never silently withhold it - a gate that removes an
    answer people already have is a regression wearing a security badge.
    """
    gated = apply_field_access(db, _payload(), resource=RESOURCE, current_user={"id": None})

    assert gated["data"][0]["estimated_arrival_date"] == "2026-07-18"
    assert "estimated_arrival_date" in GATED_FIELDS[RESOURCE], "revocable"
    assert "estimated_arrival_date" in DEFAULT_ALLOWED, "but allowed by default"


def test_a_contact_with_no_role_gets_no_eta_not_even_the_default(db):
    """S5a: for a chat contact the tree decides. ETA is ticked on the presets, so it
    reaches whoever holds a preset; a contact with no role reaches nothing."""
    contact = _contact(db)
    (decision,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id=contact.id
    )
    assert decision.outcome == FIELD_NOT_ALLOWED


def test_a_role_tick_reveals_eta_and_a_remove_override_takes_it_back(db):
    from tests.chatbot._access_seed import override

    contact, _ = _entitled_contact(db, "estimated_arrival_date")
    (shown,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id=contact.id
    )
    assert shown.outcome == ALLOWED
    override(db, contact.id, "incoming", field_key="incoming_stock.estimated_arrival_date", granted=False)
    (hidden,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id=contact.id
    )
    assert hidden.outcome == FIELD_NOT_ALLOWED


def test_eta_can_be_revoked_for_one_contact_only(db):
    from tests.chatbot._access_seed import give_role, override

    contact, _ = _entitled_contact(db, "estimated_arrival_date")
    other = _contact(db)
    give_role(db, other.id, _role_of(db, contact))
    override(db, contact.id, "incoming", field_key="incoming_stock.estimated_arrival_date", granted=False)

    (denied,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id=contact.id
    )
    (kept,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id=other.id
    )
    assert denied.outcome == FIELD_NOT_ALLOWED
    assert kept.outcome == ALLOWED


def test_stripping_does_not_mutate_the_callers_payload(db):
    """The same row objects can be shared with another response or a cache."""
    payload = _payload()
    apply_field_access(db, payload, resource=RESOURCE, current_user={"id": None})

    assert payload["data"][0]["eta_delay_date"] == "2026-07-22", "original untouched"


def test_an_unknown_resource_is_passed_straight_through(db):
    payload = _payload()
    assert apply_field_access(db, payload, resource="nothing_registered") == payload


# ------------------------------------------------- the denial says WHICH problem


def test_a_contact_with_no_role_is_denied_with_a_reason(db):
    contact = _contact(db)
    _agent(db)

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)

    assert decision.outcome == FIELD_NOT_ALLOWED
    assert decision.as_dict()["reason"]


def test_a_role_without_the_field_reports_field_not_allowed(db):
    """Holding the incoming domain is not enough: the field has to be ticked."""
    contact, _ = _entitled_contact(db)  # incoming domain granted, no field ticked

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)

    assert decision.outcome == FIELD_NOT_ALLOWED


def test_a_field_tick_counts_only_when_the_incoming_domain_is_granted(db):
    contact = _contact(db)
    _give_tree(db, contact, "eta_delay_date", domain=False)

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)

    assert decision.outcome == FIELD_NOT_ALLOWED


def test_each_field_is_decided_on_its_own_in_one_call(db):
    contact, _ = _entitled_contact(db, "eta_delay_date")

    outcomes = {
        d.field: d.outcome
        for d in decide(
            db, resource=RESOURCE, fields=["eta_delay_date", "gatepass_date"], contact_id=contact.id
        )
    }

    assert outcomes == {"eta_delay_date": ALLOWED, "gatepass_date": FIELD_NOT_ALLOWED}


def test_an_ungated_field_is_reported_as_not_gated(db):
    contact, _ = _entitled_contact(db)

    # `shipment_number` is the answer itself and never gated. (The container number
    # was the example here until #1328 made it deniable for dealers.)
    (decision,) = decide(
        db, resource=RESOURCE, fields=["shipment_number"], contact_id=contact.id
    )

    assert decision.outcome == NOT_GATED
    assert decision.allowed is True


def test_the_denial_reason_rides_along_with_the_data(db):
    """So the answer can say "I can't share that" rather than inventing a status."""
    contact, _ = _entitled_contact(db, "eta_delay_date")

    gated = apply_field_access(db, _payload(), resource=RESOURCE, contact_id=contact.id)

    denied = {d["field"]: d["outcome"] for d in gated["field_access"]["denied"]}
    assert "eta_delay_date" not in denied, "the ticked field is not in the denied list"
    assert denied["gatepass_date"] == FIELD_NOT_ALLOWED
    assert "not yet reached" in gated["field_access"]["note"]


def test_a_fully_entitled_caller_gets_no_field_access_block(db):
    """Nothing denied means nothing to explain - the response stays exactly the
    shape it has today."""
    contact, _ = _entitled_contact(db, *GATED_FIELDS[RESOURCE])
    payload = _payload()

    gated = apply_field_access(db, payload, resource=RESOURCE, contact_id=contact.id)

    assert gated == payload
    assert "field_access" not in gated


# ------------------------------------------------------------ agent resolution


def test_a_revoked_grant_holds_nothing(db):
    contact = _contact(db)
    _grant(db, contact, _agent(db), allowed=False)

    assert contact_agent_codes(db, contact.id) == set()


def test_an_expired_grant_holds_nothing(db):
    contact = _contact(db)
    _grant(
        db,
        contact,
        _agent(db),
        valid_from=datetime.utcnow() - timedelta(days=30),
        valid_to=datetime.utcnow() - timedelta(days=1),
    )

    assert contact_agent_codes(db, contact.id) == set()


def test_a_grant_that_has_not_started_holds_nothing(db):
    contact = _contact(db)
    _grant(db, contact, _agent(db), valid_from=datetime.utcnow() + timedelta(days=1))

    assert contact_agent_codes(db, contact.id) == set()


def test_a_legacy_agent_grant_and_field_row_reveal_nothing_without_the_tree(db):
    contact = _contact(db)
    agent = _agent(db)
    _grant(db, contact, agent)
    _field(db, "eta_delay_date")

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)
    assert decision.outcome == FIELD_NOT_ALLOWED


def test_an_unknown_contact_says_so_instead_of_blaming_the_grant(db):
    """Reporting AGENT_NOT_ASSIGNED here sends an admin to check grants that were
    never consulted - and when the contact DOES hold the agent, that screen looks
    perfectly correct, so the real cause stays hidden. Cost a real debugging
    session: a space_id from another workspace read as "assign the agent"."""
    (decision,) = decide(
        db, resource=RESOURCE, fields=["eta_delay_date"], contact_id="no-such-contact"
    )
    assert decision.outcome == CONTACT_NOT_FOUND
    assert "space_id" in decision.as_dict()["reason"], "name the usual cause"


def test_no_contact_supplied_is_also_not_a_grant_problem(db):
    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=None)
    assert decision.outcome == CONTACT_NOT_FOUND


def test_a_respond_io_id_resolves_to_the_internal_contact(db):
    """n8n thinks in Respond.io ids; grants key on the internal one. Guessing wrong
    denies a contact who IS entitled, which reads as a broken feature."""
    contact = _contact(db, respond_io_id="respondio-12345")
    _give_tree(db, contact, "eta_delay_date")

    assert resolve_contact_id(db, "respondio-12345") == contact.id

    (decision,) = decide(
        db, resource=RESOURCE, fields=["eta_delay_date"], contact_id="respondio-12345"
    )
    assert decision.outcome == ALLOWED


# ------------------------------------------------------- default and override


def test_a_gated_field_with_no_tick_is_denied(db):
    """Default deny. A newly registered field reaches nobody until a role ticks it."""
    contact, _ = _entitled_contact(db, "eta_delay_date")

    outcomes = {
        d.field: d.outcome
        for d in decide(
            db, resource=RESOURCE, fields=["eta_delay_date", "gatepass_date"], contact_id=contact.id
        )
    }
    assert outcomes == {"eta_delay_date": ALLOWED, "gatepass_date": FIELD_NOT_ALLOWED}


def test_a_per_contact_add_override_reveals_what_the_role_does_not(db):
    from tests.chatbot._access_seed import override

    contact, _ = _entitled_contact(db)
    override(db, contact.id, "incoming", field_key="incoming_stock.gatepass_date", granted=True)

    (decision,) = decide(db, resource=RESOURCE, fields=["gatepass_date"], contact_id=contact.id)
    assert decision.outcome == ALLOWED


def test_a_per_contact_remove_override_hides_what_the_role_ticks(db):
    from tests.chatbot._access_seed import override

    contact, _ = _entitled_contact(db, "eta_delay_date")
    override(db, contact.id, "incoming", field_key="incoming_stock.eta_delay_date", granted=False)

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)
    assert decision.outcome == FIELD_NOT_ALLOWED


def test_one_contacts_override_does_not_touch_another(db):
    from tests.chatbot._access_seed import give_role, override

    contact, _ = _entitled_contact(db, "eta_delay_date")
    other = _contact(db)
    give_role(db, other.id, _role_of(db, contact))
    override(db, other.id, "incoming", field_key="incoming_stock.eta_delay_date", granted=False)

    (kept,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)
    (hidden,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=other.id)
    assert kept.outcome == ALLOWED
    assert hidden.outcome == FIELD_NOT_ALLOWED


def test_an_agent_field_row_is_inert_for_the_gate(db):
    """The legacy `agent_field_access` rows no longer decide anything."""
    contact = _contact(db)
    _grant(db, contact, _agent(db))
    _field(db, "eta_delay_date")
    _field(db, "eta_delay_date", contact=contact, allowed=True)

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)
    assert decision.outcome == FIELD_NOT_ALLOWED


def test_an_unticked_field_stays_hidden_whatever_other_fields_are_ticked(db):
    contact, _ = _entitled_contact(db, "gatepass_date")

    (decision,) = decide(db, resource=RESOURCE, fields=["eta_delay_date"], contact_id=contact.id)
    assert decision.outcome == FIELD_NOT_ALLOWED


# ------------------------------------------------------------ caller precedence


def test_a_contacts_question_uses_the_contacts_grants_not_the_api_keys(db, monkeypatch):
    """n8n calls with a privileged act-as user. If that decided it, every contact
    would be entitled the moment n8n asked on their behalf."""
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, u, s: True
    )
    contact = _contact(db)  # holds nothing

    gated = apply_field_access(
        db,
        _payload(),
        resource=RESOURCE,
        current_user={"id": "privileged"},
        contact_id=contact.id,
        staff_permission="procurement.packing_lists.view_clearance",
    )
    assert "eta_delay_date" not in gated["data"][0]

    # ...while the same privileged user asking for THEMSELVES still sees it.
    seen = apply_field_access(
        db,
        _payload(),
        resource=RESOURCE,
        current_user={"id": "privileged"},
        staff_permission="procurement.packing_lists.view_clearance",
    )
    assert seen["data"][0]["eta_delay_date"] == "2026-07-22"


def test_a_staff_user_without_the_permission_is_denied(db, monkeypatch):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "check_user_has_permission", lambda self, u, s: False
    )
    gated = apply_field_access(
        db,
        _payload(),
        resource=RESOURCE,
        current_user={"id": "someone"},
        staff_permission="procurement.packing_lists.view_clearance",
    )
    assert gated["data"] == TODAYS_ROWS


def test_a_permission_lookup_that_raises_fails_closed(db, monkeypatch):
    """A broken permission service must not become an open door."""
    from app.services.user_service import UserPermissionService

    def _boom(self, user_id, slug):
        raise RuntimeError("permission service is down")

    monkeypatch.setattr(UserPermissionService, "check_user_has_permission", _boom)

    gated = apply_field_access(
        db,
        _payload(),
        resource=RESOURCE,
        current_user={"id": "someone"},
        staff_permission="procurement.packing_lists.view_clearance",
    )
    assert gated["data"] == TODAYS_ROWS


def test_a_broken_grant_lookup_holds_nothing_rather_than_everything(db):
    """A database hiccup must not become an open door."""

    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("connection reset")

        def execute(self, *a, **k):
            raise RuntimeError("connection reset")

    assert contact_agent_codes(_Broken(), "anyone") == set()
    assert resolve_contact_id(_Broken(), "anyone") is None
    (decision,) = decide(_Broken(), resource=RESOURCE, fields=["eta_delay_date"], contact_id="anyone")
    assert decision.allowed is False


# ------------------------------------------------------------- the preflight


def test_check_attributes_reports_each_field_separately(db):
    contact, _ = _entitled_contact(db, "eta_delay_date")

    result = check_attributes(
        db,
        resource=RESOURCE,
        attributes=["eta_delay_date", "gatepass_date", "estimated_arrival_date"],
        contact_id=contact.id,
    )

    assert result["all_allowed"] is False
    by_field = {a["field"]: a["outcome"] for a in result["attributes"]}
    assert by_field == {
        "eta_delay_date": ALLOWED,
        "gatepass_date": FIELD_NOT_ALLOWED,
        # No longer default-allowed for a contact: the tree has to tick it.
        "estimated_arrival_date": FIELD_NOT_ALLOWED,
    }


def test_check_attributes_is_all_allowed_when_everything_passes(db):
    contact, _ = _entitled_contact(db, "eta_delay_date")

    result = check_attributes(
        db, resource=RESOURCE, attributes=["eta_delay_date"], contact_id=contact.id
    )
    assert result["all_allowed"] is True


# ---------------------------------------------------- the query must select them


def test_the_service_selects_every_gated_column():
    """`incoming_list` returns column tuples, not ORM instances.

    A gated column missing from the SELECT reads as None on the row, and the gate
    would pass that straight through to an ENTITLED caller as "not reached yet" - a
    silent wrong answer rather than a crash. This is the bug that shipped for one
    build.
    """
    import inspect

    from app.services import incoming_stock_service

    source = inspect.getsource(incoming_stock_service.IncomingStockService.incoming_list)
    assert "CLEARANCE_KEYS" in source, "the query must select the gated columns"
    # Both halves: selected in the query AND spread into the payload.
    assert source.count("CLEARANCE_KEYS") >= 2


def test_the_gate_runs_on_the_route_not_only_in_the_service():
    """The service returns everything by design, so the route is the single place
    access is applied. If that call is ever dropped, the fields leak."""
    import inspect

    from app.api.v1 import incoming_stock

    # #1328: every incoming route answering a contact goes through ONE gate,
    # `_for_contact`, which applies the field reveals for the resolved contact.
    # `tests/test_incoming_contact_rules.py` pins the behaviour through the routes.
    gate = inspect.getsource(incoming_stock._for_contact)
    assert "apply_field_access" in gate
    assert "contact_id=contact.resolved or _UNRESOLVED_CONTACT" in gate
    for route in (
        incoming_stock.get_incoming_list,
        incoming_stock.get_incoming_for_product,
        incoming_stock.get_incoming_shipments,
        incoming_stock.get_incoming_shipment_products,
        incoming_stock.get_incoming_shipment_attachment,
    ):
        assert "_for_contact(" in inspect.getsource(route), route.__name__
    # The staff (no contact) path on /list still runs the RBAC gate.
    assert "apply_field_access" in inspect.getsource(incoming_stock.get_incoming_list)


def test_every_gated_field_is_a_real_column():
    """A typo in the registry gates nothing and silently leaks the real column."""
    from app.models.procurement import InboundShipment, InboundShipmentLine
    from app.services.field_access import NON_CLEARANCE_KEYS

    for field in GATED_FIELDS[RESOURCE]:
        if field in NON_CLEARANCE_KEYS:
            continue
        assert hasattr(InboundShipment, field), f"{field} is not a column"
    # #1328: the two non-clearance keys are real too - the container number is a
    # shipment column, and the quantity is the line's remaining incoming quantity
    # (computed off `quantity_shipped`; `tests/test_incoming_contact_rules.py` pins
    # that the payload carries it and that a denial removes it).
    assert NON_CLEARANCE_KEYS == {"shipping_container_number", "remaining_incoming_quantity"}
    assert hasattr(InboundShipment, "shipping_container_number")
    assert hasattr(InboundShipmentLine, "quantity_shipped")


# ------------------------------------------------------ workspace disambiguation


def _workspace(db, space_id: str):
    from app.models.respond_workspace import RespondWorkspace

    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=space_id,
        name=f"ZZT {space_id}",
        api_key_ciphertext="zzt-not-a-real-key",
    )
    db.add(ws)
    db.flush()
    return ws


def test_the_same_respond_io_id_in_two_workspaces_needs_the_space_id(db):
    """n8n sends contact_id + space_id. Resolving the Respond.io id without the
    workspace could land on a stranger in another workspace and answer with THEIR
    grants."""
    ws_a, ws_b = _workspace(db, "space-A"), _workspace(db, "space-B")

    a = _contact(db, respond_io_id="shared-io-id")
    a.workspace_id = ws_a.id
    b = _contact(db, respond_io_id="shared-io-id")
    b.workspace_id = ws_b.id
    db.flush()

    assert resolve_contact_id(db, "shared-io-id", "space-A") == a.id
    assert resolve_contact_id(db, "shared-io-id", "space-B") == b.id


def test_an_ambiguous_respond_io_id_resolves_to_nothing_rather_than_a_coin_flip(db):
    ws_a, ws_b = _workspace(db, "space-A"), _workspace(db, "space-B")
    a = _contact(db, respond_io_id="shared-io-id")
    a.workspace_id = ws_a.id
    b = _contact(db, respond_io_id="shared-io-id")
    b.workspace_id = ws_b.id
    db.flush()

    assert resolve_contact_id(db, "shared-io-id") is None


def test_an_internal_id_wins_before_any_workspace_lookup(db):
    """A caller who already resolved the contact must not be second-guessed by a
    space_id that happens to be wrong or absent."""
    contact = _contact(db)
    assert resolve_contact_id(db, contact.id, "space-that-does-not-exist") == contact.id


def test_the_wrong_space_id_denies_rather_than_falling_back(db):
    """Falling back to "any workspace" would make space_id decorative, and the
    ambiguous case would silently start picking one."""
    ws = _workspace(db, "space-A")
    contact = _contact(db, respond_io_id="only-in-a")
    contact.workspace_id = ws.id
    db.flush()

    assert resolve_contact_id(db, "only-in-a", "space-B") is None


def test_space_id_reaches_the_decision(db):
    """The route threads it through decide() - without that the disambiguation
    exists but nothing uses it."""
    ws = _workspace(db, "space-A")
    contact = _contact(db, respond_io_id="io-1")
    contact.workspace_id = ws.id
    db.flush()
    _give_tree(db, contact, "estimated_arrival_date")

    (ok,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id="io-1", space_id="space-A"
    )
    assert ok.outcome == ALLOWED

    # The wrong workspace resolves to NOBODY - a lookup failure, not a revoked tick.
    (denied,) = decide(
        db, resource=RESOURCE, fields=["estimated_arrival_date"], contact_id="io-1", space_id="space-B"
    )
    assert denied.outcome == CONTACT_NOT_FOUND


def test_the_route_passes_space_id_to_the_gate():
    """A param the route accepts but drops is worse than not having it: n8n would
    believe it was disambiguating."""
    import inspect

    from app.api.v1 import incoming_stock

    source = inspect.getsource(incoming_stock.get_incoming_list)
    assert "space_id" in source
    assert "space_id=space_id" in source
