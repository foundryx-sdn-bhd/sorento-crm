"""RED tests - FORM-ROUTING-CONDITIONS groups RC (routing conditions) and NC-2 (No CS rule CRUD).

* RC-1  routable_fields() offers `product_lines` and `customer_debtor_type` (enum) for PR + SF.
* RC-2  the form values the rules see carry `product_lines` ("has"/"none") and
        `customer_debtor_type` (the linked customer's CURRENT debtor_type, None otherwise).
* RC-3  a `customer_debtor_type equals project_cash` rule wins for a 302- customer;
        `not_equals project_cash` matches a free-text form.
* NC-2  `cs_pic_user_id: null` is a valid rule ("No CS"): no eligibility check, listed back as
        null/null, duplicate-condition rejection still applies.

Seed chain is borrowed from tests/test_cs_routing_targets.py (blank Postgres schema, fake names).
"""
from __future__ import annotations

import uuid

import pytest

from app.main import app  # noqa: E402
from app.models.access import RespondContactCsRouting
from app.models.base import set_company_scope
from app.models.order import Customer
from app.models.procurement import PurchaseRequestHeader, PurchaseRequestLine
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.cs_routing_service import CsRoutingService
from app.services.form_sla_service import FormSLAOrchestrator
from tests._pg_fixture import blank_session
from tests.test_cs_routing_targets import BASE, SORENTO, _client, _pin, _seed, _stored  # noqa: F401


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


def _customer(db, code, name, **kw) -> Customer:
    register_company_scope_listeners()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    row = Customer(customer_code=code, customer_name=name, company_id=DEFAULT_COMPANY_ID, **kw)
    db.add(row)
    db.flush()
    return row


def _form(db, kind="purchase_request", *, contact_id=None, customer_id=None, item_codes=()) -> str:
    pid = str(uuid.uuid4())
    db.add(
        PurchaseRequestHeader(
            id=pid,
            request_type=kind,
            status="approved",
            approval_status="approved",
            source="manual",
            requested_by_contact_id=contact_id,
            customer_id=customer_id,
        )
    )
    db.flush()
    for i, code in enumerate(item_codes):
        db.add(
            PurchaseRequestLine(
                id=str(uuid.uuid4()),
                purchase_request_id=pid,
                item_code=code,
                quantity=1,
                unit_price=1,
                sort_order=i,
            )
        )
    db.flush()
    return pid


# ------------------------------------------------------------------ RC-1


@pytest.mark.parametrize("use_case", ["purchase_request", "sponsorship_form"])
def test_routable_fields_offer_product_lines_and_customer_debtor_type(use_case):
    with blank_session() as db:
        fields = {f["field"]: f for f in CsRoutingService(db).routable_fields(use_case)}

    assert fields["product_lines"] == {
        "field": "product_lines",
        "label": "Product lines",
        "type": "enum",
        "options": [{"value": "has", "label": "Has lines"}, {"value": "none", "label": "No lines"}],
    }
    assert fields["customer_debtor_type"] == {
        "field": "customer_debtor_type",
        "label": "Customer debtor type",
        "type": "enum",
        "options": [
            {"value": "project", "label": "Project"},
            {"value": "project_cash", "label": "Project cash"},
            {"value": "cash_sales", "label": "Cash sales"},
        ],
    }


# ------------------------------------------------------------------ RC-2


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
@pytest.mark.parametrize(
    "item_codes,expected",
    [
        ((), "none"),
        (("ITEM-A",), "has"),
        (("  ", ""), "none"),
        (("", "  ITEM-B  "), "has"),
        ((None,), "none"),
    ],
)
def test_form_values_carry_product_lines(kind, item_codes, expected):
    with blank_session() as db:
        pid = _form(db, kind, item_codes=item_codes)
        values = FormSLAOrchestrator(db)._load_form_field_values(kind, pid)
    assert values["product_lines"] == expected


def test_form_values_carry_the_customers_current_debtor_type():
    with blank_session() as db:
        cust = _customer(db, f"302-ZZT{_sfx()}", "ZZT CURRENT TYPE SDN BHD")
        pid = _form(db, customer_id=str(cust.id))
        orch = FormSLAOrchestrator(db)
        assert orch._load_form_field_values("purchase_request", pid)["customer_debtor_type"] == "project_cash"

        # CURRENT, not the value at submit time: re-typing the customer moves the form's value.
        cust.debtor_type = "cash_sales"
        db.flush()
        assert orch._load_form_field_values("purchase_request", pid)["customer_debtor_type"] == "cash_sales"


def test_form_values_debtor_type_is_none_without_a_customer_or_with_a_blank_type():
    with blank_session() as db:
        free_text = _form(db)
        walkin = _customer(db, f"301-ZZT{_sfx()}", "CASH (PERSON A)")  # blank type by the DT-2 rule
        linked_blank = _form(db, customer_id=str(walkin.id))
        orch = FormSLAOrchestrator(db)

        assert orch._load_form_field_values("purchase_request", free_text)["customer_debtor_type"] is None
        assert orch._load_form_field_values("purchase_request", linked_blank)["customer_debtor_type"] is None


# ------------------------------------------------------------------ RC-3 (through the real resolver)


def test_debtor_type_rule_routes_a_302_customer_and_not_equals_matches_free_text():
    with blank_session() as db:
        s = _seed(db)
        cust = _customer(db, f"302-ZZT{_sfx()}", "ZZT PROJECT CASH SDN BHD")
        _pin(
            db, s.contact_id, s.outside, priority=0,
            conditions=[{"field": "customer_debtor_type", "operator": "equals", "value": "project_cash"}],
        )
        _pin(
            db, s.contact_id, s.in_team, priority=1,
            conditions=[{"field": "customer_debtor_type", "operator": "not_equals", "value": "project_cash"}],
        )
        linked = _form(db, contact_id=s.contact_id, customer_id=str(cust.id))
        free_text = _form(db, contact_id=s.contact_id)
        orch = FormSLAOrchestrator(db)

        by_link = orch._resolve_pinned_assignee("purchase_request", s.contact_id, SORENTO, linked)
        by_free_text = orch._resolve_pinned_assignee("purchase_request", s.contact_id, SORENTO, free_text)

        assert by_link["id"] == s.outside
        assert by_free_text["id"] == s.in_team


def test_product_lines_rule_routes_by_has_or_none():
    with blank_session() as db:
        s = _seed(db)
        _pin(
            db, s.contact_id, s.outside, priority=0,
            conditions=[{"field": "product_lines", "operator": "equals", "value": "none"}],
        )
        _pin(db, s.contact_id, s.in_team, priority=1, conditions=[])
        no_lines = _form(db, "sponsorship_form", contact_id=s.contact_id)
        with_lines = _form(db, "sponsorship_form", contact_id=s.contact_id, item_codes=("ITEM-A",))
        for pin in db.query(RespondContactCsRouting).filter_by(respond_contact_id=s.contact_id):
            pin.use_case = "sponsorship_form"
        db.flush()
        orch = FormSLAOrchestrator(db)

        assert orch._resolve_pinned_assignee("sponsorship_form", s.contact_id, SORENTO, no_lines)["id"] == s.outside
        assert orch._resolve_pinned_assignee("sponsorship_form", s.contact_id, SORENTO, with_lines)["id"] == s.in_team


# ------------------------------------------------------------------ NC-2


def test_replace_all_accepts_a_no_cs_rule_and_lists_it_back_as_null(monkeypatch):
    with blank_session() as db:
        s = _seed(db)
        no_lines = [{"field": "product_lines", "operator": "equals", "value": "none"}]

        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "sponsorship_form", "cs_pic_user_id": None, "match_conditions": no_lines, "priority": 0},
                    {"use_case": "sponsorship_form", "cs_pic_user_id": s.in_team, "match_conditions": [], "priority": 1},
                ]
            },
        )

        assert r.status_code == 200, r.text
        pins = r.json()["pins"]
        assert [(p["cs_pic_user_id"], p["cs_pic_name"], p["match_conditions"]) for p in pins] == [
            (None, None, no_lines),
            (s.in_team, "ZZT Dina Team", []),
        ]
        assert [(uc, uid) for _, uc, uid, _ in _stored(db, s.contact_id)] == [
            ("sponsorship_form", None),
            ("sponsorship_form", s.in_team),
        ]
        # And it reads back through GET.
        got = _client(db, monkeypatch).get(f"{BASE}/{s.contact_id}/cs-routing")
        assert got.status_code == 200, got.text
        assert got.json()["pins"][0]["cs_pic_user_id"] is None
        assert got.json()["pins"][0]["cs_pic_name"] is None


def test_a_wildcard_no_cs_rule_is_allowed(monkeypatch):
    with blank_session() as db:
        s = _seed(db)
        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={"rules": [{"use_case": "purchase_request", "cs_pic_user_id": None, "match_conditions": [], "priority": 0}]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["pins"][0]["cs_pic_user_id"] is None


def test_ineligible_pic_is_still_refused_but_null_is_not(monkeypatch):
    """The eligibility check is skipped only for null, not for a bad id."""
    with blank_session() as db:
        s = _seed(db)
        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "purchase_request", "cs_pic_user_id": None, "match_conditions": [], "priority": 0},
                    {
                        "use_case": "purchase_request",
                        "cs_pic_user_id": s.admin_only,
                        "match_conditions": [{"field": "sales_type", "operator": "equals", "value": "project"}],
                        "priority": 1,
                    },
                ]
            },
        )
        assert r.status_code == 400, r.text
        assert r.json()["message"].startswith("Priority 2: ZZT Aaron AdminOnly cannot be assigned")


def test_duplicate_conditions_are_refused_whatever_the_outcome(monkeypatch):
    with blank_session() as db:
        s = _seed(db)
        cond = [{"field": "product_lines", "operator": "equals", "value": "none"}]
        r = _client(db, monkeypatch).put(
            f"{BASE}/{s.contact_id}/cs-routing",
            json={
                "rules": [
                    {"use_case": "sponsorship_form", "cs_pic_user_id": None, "match_conditions": cond, "priority": 0},
                    {"use_case": "sponsorship_form", "cs_pic_user_id": s.in_team, "match_conditions": cond, "priority": 1},
                ]
            },
        )
        assert r.status_code == 400, r.text
        assert r.json()["message"].startswith(
            "Priority 2 has the same conditions as Priority 1 for Sponsorship Form"
        )
        assert _stored(db, s.contact_id) == []


def test_upsert_accepts_null_as_no_cs_and_still_enforces_the_condition_key():
    with blank_session() as db:
        s = _seed(db)
        cond = [{"field": "product_lines", "operator": "equals", "value": "none"}]
        svc = CsRoutingService(db)

        row = svc.upsert(s.contact_id, "sponsorship_form", None, match_conditions=cond, priority=0)
        assert row.cs_pic_user_id is None

        # Same condition set again = the same row (updated), not a second one.
        again = svc.upsert(s.contact_id, "sponsorship_form", s.in_team, match_conditions=cond, priority=0)
        assert again.id == row.id and again.cs_pic_user_id == s.in_team

        listed = svc.list_for_contact(s.contact_id)
        assert len(listed) == 1


def test_list_for_contact_returns_null_name_for_no_cs():
    with blank_session() as db:
        s = _seed(db)
        _pin(db, s.contact_id, None, use_case="sponsorship_form", conditions=[], priority=0)
        listed = CsRoutingService(db).list_for_contact(s.contact_id)
        assert [(p["cs_pic_user_id"], p["cs_pic_name"]) for p in listed] == [(None, None)]
