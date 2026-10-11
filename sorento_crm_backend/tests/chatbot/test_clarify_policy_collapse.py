"""CLARIFY-POLICY S5 (AC-24, AC-25): collapse pickers whose options lead to one answer, and
default the outstanding document to Both. UAC: documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

AC-24 is tested on the product picker only (attachments, where the picker already annotates each
option with the types it holds); the stock family pick (`turn/task.py:871-895`) is NOT covered: the
UAC names no annotation for it, so its observable shape is undecided (reported to the captain).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.main import app
from app.models.base import set_company_scope
from app.models.product import Product
from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_dealer_eta_stock_routing import Console, _seed
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture
from tests.chatbot.test_outstanding_lane import (
    PRODUCT_CODE,
    PRODUCT_UUID,
    REPORT_HIT,
    _qf,
    _run_turn,
    _seed_contact,
)
from tests.chatbot.test_product_attachment_picker_stamp import _seed_attachment_type, _seed_file_for
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

PHOTOS = "Product Photos"
FAMILY = "ZZTFAMX"
WITH_FILE = "ZZTFAMX-A"
WITHOUT_FILE = "ZZTFAMX-B"


@pytest.fixture
def family(session_factory, monkeypatch, stub_access):
    _seed(session_factory, dealer=False, salesperson=False, shipments=False)
    console = Console(session_factory, monkeypatch, stub_access)
    db = session_factory()
    set_company_scope(db, frozenset({SORENTO}))
    base = db.query(Product).first()
    ids: dict[str, str] = {}
    for code in (WITH_FILE, WITHOUT_FILE):
        row = Product(
            id=str(uuid.uuid4()), product_code=code, product_name=f"ZZT {code}", category_id=base.category_id,
            base_uom_id=base.base_uom_id, list_price=1, company_id=SORENTO,
        )
        db.add(row)
        ids[code] = row.id
    db.commit()
    type_id = _seed_attachment_type(session_factory, PHOTOS)
    _seed_file_for(
        session_factory, product_id=ids[WITH_FILE], attachment_type_id=type_id, company_id=SORENTO,
        filename="zzt-family-a.jpg",
    )
    console.photo_type = PHOTOS
    try:
        yield console
    finally:
        app.dependency_overrides.clear()


def test_ac24_exactly_one_picker_option_holds_the_asked_data_so_the_bot_answers_for_it(family):
    ask = verdict(
        domain_hint="product_attachment",
        intent_hint="check_product_attachment",
        entities=[entity(FAMILY, hint="product"), entity("photo", hint="attachment_type", canonical_code="photo")],
    )
    reply = family.say(f"photo for {FAMILY}", ask)
    assert "Which product do you mean?" not in reply, reply
    assert "zzt-family-a.jpg" in reply, reply
    assert f"Also matched: {WITHOUT_FILE}." in reply, reply


def test_ac25_outstanding_with_no_document_named_defaults_to_both_with_a_disclose_line(session_factory, monkeypatch):
    _seed_contact(session_factory, variables={})
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=_qf(order_status="outstanding"), text_body="SRTWT7445 outstanding",
        msg_id="ZZT-clp-outstanding-default", attributes=["sales_orders.outstanding"],
        matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        mcp_response=REPORT_HIT,
    )
    reply = (result.reply or {}).get("text") or ""
    assert "Outstanding for which document?" not in reply, reply
    reports = [args for name, args in captured if name == "crm_outstanding_report"]
    assert reports, captured
    assert reports[0].get("scope") in (None, "both"), reports[0]  # Both is the report's own default
    assert "both" in reply.lower(), reply  # the disclose line names the assumption
