"""Phase 2 RED test - REPORTS-STEP2 S3a live diagnosis, rpt-015: the REAL resolve path.

The lane builds the resolver body with `resolve_gate.resolve_entity_body`, which sends
`allowed_entity_types` = the parser's hints (["product", "product"] for the v54 emission, where "scr" is
hinted as a product). The earlier tests called `resolve_references` with no type filter, so they never
saw that "scr" is then never probed as a customer. This one posts the lane's own body through the real
`resolve_reference_post` route function, on seeded data.
"""
from __future__ import annotations

import pytest

from app.models.base import company_scope, set_company_scope
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import customer, product
from tests._pg_fixture import blank_session

SCR_CUSTOMER = "SCR MARKETING (M) SDN BHD"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _lane_body(entities):
    from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

    ctx = {
        "text": {"message": {"message": {"text": "Cb88ss/diy got stock? got outstanding for scr"}}},
        "contact": {"id": "1"},
        "parse": {"output": {
            "message_type": "business_query", "intent_hint": "check_stock", "domain_hint": "inventory",
            "match_mode": "and", "access_levels": [], "entity_op": "replace_combine", "entities": entities,
            "asks": [{"domain": "inventory", "intent": "check_stock"}, {"domain": "order", "intent": "check_order"}],
        }},
    }
    return resolve_entity_body(ctx)


def test_rpt015_the_lanes_own_resolve_body_resolves_scr_to_the_customer_and_the_code_to_one_product(db):
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post

    for code in ("CB88SS-DIY", "CB1536SS-CR", "ACC-SCREW", "BT015-SS-CR"):
        product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    wanted = customer(db, company_id=DEFAULT_COMPANY_ID, name=SCR_CUSTOMER)
    db.flush()
    body = _lane_body([
        {"raw": "Cb88ss/diy", "hint": "product", "canonical_code": "Cb88ss/diy", "current_message": True,
         "confident": True},
        {"raw": "scr", "hint": "product", "canonical_code": "scr", "current_message": True, "confident": True},
    ])
    payload = ResolveReferenceRequest.model_validate({k: v for k, v in body.items()
                                                      if k in ResolveReferenceRequest.model_fields})
    with company_scope(db, frozenset({DEFAULT_COMPANY_ID})):
        answer = resolve_reference_post(payload, current_user={"id": "u"}, db=db)
    answer = answer if isinstance(answer, dict) else answer.model_dump() if hasattr(answer, "model_dump") else answer
    resolutions = {r["token"]: r for r in answer["resolutions"]}
    assert [(m["entity_type"], m["canonical_code"]) for m in resolutions["Cb88ss/diy"]["matches"]] == [
        ("product", "CB88SS-DIY")], resolutions["Cb88ss/diy"]
    scr = [(m["entity_type"], m.get("uuid")) for m in resolutions["scr"]["matches"]]
    assert scr == [("customer", wanted.id)], resolutions["scr"]
