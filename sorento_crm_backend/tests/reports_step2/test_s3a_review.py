"""Phase 2 RED tests - REPORTS-STEP2 S3a review round (resolver half): codes containing "/" keep
resolving (finding 1), and the widened multi-ask kinds never override a product that matches and never
add the customer kind for a dealer (finding 2). Postgres only, fake data.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.models.access import RespondContact, RespondContactCustomer
from app.models.base import company_scope, set_company_scope
from app.models.company import RespondContactCompany
from app.services import entity_resolver as er
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import customer, product
from tests._pg_fixture import blank_session, unique_code
from tests.reports_step2.test_s3a_live import _lane_body

SCOPE = frozenset({DEFAULT_COMPANY_ID})


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, SCOPE)
        yield s


def _codes(items):
    return [m.canonical_code for m in items]


# ------------------------------------------------------------------ finding 1: "/" in stored codes


def test_review1_a_stored_code_with_a_slash_typed_exactly_resolves_exactly(db):
    for code in ("CBCH80020/25", "CBCH80020/26"):
        product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.flush()
    with company_scope(db, SCOPE):
        result = er.resolve_references(db, ["CBCH80020/25"])
    assert _codes(result.resolutions[0].matches) == ["CBCH80020/25"], result.resolutions[0]


def test_review1_a_multi_slash_code_resolves_exactly(db):
    product(db, company_id=DEFAULT_COMPANY_ID, code="CBCH6003/4/5/6")
    db.flush()
    with company_scope(db, SCOPE):
        result = er.resolve_references(db, ["CBCH6003/4/5/6"])
    assert _codes(result.resolutions[0].matches) == ["CBCH6003/4/5/6"], result.resolutions[0]


def test_review1_the_siblings_of_a_slash_code_still_expand_as_a_family(db):
    for code in ("ABC123/25", "ABC123/26"):
        product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.flush()
    with company_scope(db, SCOPE):
        result = er.resolve_references(db, ["ABC123"])
    seen = set(_codes(result.resolutions[0].matches)) | {a.canonical_code for a in result.resolutions[0].alternatives}
    assert {"ABC123/25", "ABC123/26"} <= seen, seen


def test_review1_a_slash_typed_for_a_dash_stored_code_still_finds_it(db):
    product(db, company_id=DEFAULT_COMPANY_ID, code="CB88SS-DIY")
    db.flush()
    with company_scope(db, SCOPE):
        result = er.resolve_references(db, ["CB88SS/DIY"])
    assert _codes(result.resolutions[0].matches) == ["CB88SS-DIY"], result.resolutions[0]


# ------------------------------------------------------------------ finding 2: the widened kinds


def _post(db, body):
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post

    payload = ResolveReferenceRequest.model_validate({k: v for k, v in body.items()
                                                      if k in ResolveReferenceRequest.model_fields})
    with company_scope(db, SCOPE):
        answer = resolve_reference_post(payload, current_user={"id": "u"}, db=db)
    return answer if isinstance(answer, dict) else answer.model_dump()


def _token_matches(answer):
    """Every match the answer carries, by kind (`by_entity_type`) or per token (`resolutions`)."""
    rows = [m for ms in (answer.get("by_entity_type") or {}).values() for m in ms]
    for r in answer.get("resolutions") or []:
        rows += r.get("matches") or []
    return rows


def _entity(raw):
    return {"raw": raw, "hint": "product", "canonical_code": raw, "current_message": True, "confident": True}


def test_review2_an_exact_product_stays_the_product_even_when_a_customer_name_matches(db):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="BASIN-01")
    customer(db, company_id=DEFAULT_COMPANY_ID, name="BASIN TRADING SDN BHD")
    db.flush()
    answer = _post(db, _lane_body([_entity("basin-01")]))
    matches = _token_matches(answer)
    assert {(m["entity_type"], m["uuid"]) for m in matches} == {("product", prod.id)}, matches


def test_review2_a_dealer_multi_ask_never_gets_another_customer_added_as_a_kind(db):
    own = customer(db, company_id=DEFAULT_COMPANY_ID, name="OWN DEALER SDN BHD")
    customer(db, company_id=DEFAULT_COMPANY_ID, name="BASIN TRADING SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="WASHBASIN-1")
    contact = RespondContact(id=str(uuid.uuid4()), respond_io_id=unique_code("DLR"),
                             phone_number=f"+6{unique_code('PH')[:10]}")
    db.add(contact)
    db.flush()
    db.add(RespondContactCompany(id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID))
    db.add(RespondContactCustomer(id=str(uuid.uuid4()), contact_id=contact.id, customer_id=own.id,
                                  company_id=DEFAULT_COMPANY_ID))
    db.flush()
    body = _lane_body([_entity("basin")])
    body["contact_id"] = contact.respond_io_id
    answer = _post(db, body)
    dumped = json.dumps(answer, default=str)
    assert "BASIN TRADING" not in dumped, "another customer's name reached a dealer"
    matches = _token_matches(answer)
    assert prod.id in {m["uuid"] for m in matches}, matches
    assert all(m["entity_type"] == "product" for m in matches), matches


# ------------------------------------------------------------------ finding 1, tightened: the EXACT tier


@pytest.mark.parametrize("code", ["CBCH80020/25", "CBCH6003/4/5/6", "SRTKS2406-O/F", "MWT2800-N/H"])
def test_review1_the_exact_product_probe_finds_a_stored_code_that_contains_a_slash(db, code):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.flush()
    with company_scope(db, SCOPE):
        hits = er._probe_product(db, [code])[code]
    assert [(h.canonical_code, h.uuid, h.match_field) for h in hits] == [(code, row.id, "product_code")], hits


@pytest.mark.parametrize("code", ["CBCH80020/25", "CBCH6003/4/5/6"])
def test_review1_such_a_code_resolves_on_the_exact_tier_not_a_fallback(db, code):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    db.flush()
    with company_scope(db, SCOPE):
        result = er.resolve_references(db, [code], allowed_entity_types=["product"])
    matches = result.resolutions[0].matches
    assert [(m.canonical_code, m.uuid, m.match_tier) for m in matches] == [(code, row.id, "exact")], matches


def test_review1_the_slash_for_dash_typing_still_resolves_on_the_exact_tier(db):
    row = product(db, company_id=DEFAULT_COMPANY_ID, code="CB88SS-DIY")
    db.flush()
    with company_scope(db, SCOPE):
        hits = er._probe_product(db, ["Cb88ss/diy"])["Cb88ss/diy"]
        result = er.resolve_references(db, ["Cb88ss/diy"], allowed_entity_types=["product"])
    assert [h.canonical_code for h in hits] == ["CB88SS-DIY"], hits
    assert [(m.canonical_code, m.match_tier) for m in result.resolutions[0].matches] == [("CB88SS-DIY", "exact")]
