"""PRODUCT-ALTERNATIVES round B: the Alternative line in every other product domain.

Card Addendum 1, owner 8 Oct 2026 ("okay can"): when an asked product is DISCONTINUED and has
a stored alternative, the reply adds that domain's own answer for the alternative, through
ONE shared resolver in `lanes/business/__init__.py::run_fetch`: the same tool is called a
second time for the alternative (its own logic, permissions and brand guard), and the result
is folded into the reply.

    incoming, dealer   `Alternative: X-NEW: ✅ ETA 20/10/2026` (or `X-NEW: No ETA`)
    incoming, staff    `*Alternative:* X-NEW` under the old rows, then X-NEW's rows
    promotion          after the list: `Alternative: X-NEW: <Promotion> (<start> to <end>)`,
                       or `Alternative: X-NEW: No promotion.`
    attachment         `Alternative: X-NEW: <File Name>`, and X-NEW's file is sent too
    product info       `*Alternative:* X-NEW` under the marker, then X-NEW's block
    delivery           none

Driven through the real `business.run_fetch` with a stubbed MCP seam (`test_do_ask_range.py`'s
harness) over products seeded in a blank Postgres schema.
"""
from __future__ import annotations

import json

import pytest

from app.main import app  # noqa: F401  (first app import)
from app.models.base import set_company_scope
from app.models.product import ProductAlternative
from app.services.chatbot.lanes import business
from app.services.chatbot.lanes.business.services import FetchServices
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests._mc_lookup_seed import product
from tests._pg_fixture import blank_session, unique_code


@pytest.fixture()
def db():
    with blank_session() as session:
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID}))
        yield session


@pytest.fixture()
def pair(db):
    stem = unique_code("ZZALTD")[:24].upper()
    old = product(db, company_id=DEFAULT_COMPANY_ID, code=stem)
    new = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{stem}-NEW")
    old.is_discontinued = True
    db.add(
        ProductAlternative(
            product_id=old.id,
            alternative_product_id=new.id,
            source="auto_new",
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    db.flush()
    return old, new


def _envelope(items, *, result_type, attachments=None, intro="Here are the results."):
    return json.dumps(
        {
            "result_type": result_type,
            "intro": intro if items else "No matching results found.",
            "items": items,
            "attachments": attachments or [],
            "has_result": bool(items),
        }
    )


def _run(db, *, domain, entities, answers, fail_second=False):
    """`answers(product_ids) -> raw envelope`; returns (response text, structured, calls)."""
    calls: list[tuple[str, list]] = []

    def mcp_call(name, args):
        ids = list(args.get("product_ids") or [])
        calls.append((name, ids))
        if fail_second and len(calls) > 1:
            raise RuntimeError("tool down")
        return answers(name, ids)

    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": entities},
        "ctx": {
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "domain_hint": domain,
                    "requested_attributes": [],
                }
            },
            "contact": {},
        },
    }
    fragment = business.run_fetch(
        payload, services=FetchServices(mcp_call=mcp_call), dry_run=False, db=db
    )
    fetch = fragment.get("fetch") or {}
    return str(fetch.get("response") or ""), fetch, calls


def _entity(p):
    return {"uuid": p.id, "entity_type": "product", "code": p.product_code}


def _dealer_item(code, when, *, discontinued=False):
    return {"title": f"{code}: {when}", "fields": [], "flags": {"dealer_view": True, "discontinued": discontinued}}


def _field_item(code, fields, **flags):
    return {
        "title": code,
        "fields": [{"label": label, "value": value} for label, value in fields],
        "flags": flags,
    }


# --------------------------------------------------------------------------- incoming


def test_incoming_dealer_line_names_the_alternatives_eta(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming")
        return _envelope([_dealer_item(new.product_code, "✅ ETA 20/10/2026")], result_type="incoming")

    said, _f, calls = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)

    assert [ids for _n, ids in calls] == [[old.id], [new.id]]
    assert (
        f"{old.product_code}: No ETA\n⚠️  *(PRODUCT DISCONTINUED)*\n"
        f"Alternative: {new.product_code}: ✅ ETA 20/10/2026"
    ) in said, said


def test_incoming_dealer_alternative_with_no_shipment_says_no_eta(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming")
        return _envelope([_dealer_item(new.product_code, "No ETA")], result_type="incoming")

    said, _f, _c = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)
    assert f"Alternative: {new.product_code}: No ETA" in said, said


def test_incoming_staff_names_the_alternative_then_lists_its_rows(db, pair):
    old, new = pair

    def answers(name, ids):
        code = old.product_code if ids == [old.id] else new.product_code
        container = "CONT-OLD" if ids == [old.id] else "CONT-NEW"
        return _envelope(
            [_field_item(code, [("Product Code", code), ("Container", container)])],
            result_type="incoming",
        )

    said, _f, _c = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)

    old_at = said.index("CONT-OLD")
    marker_at = said.index(f"*Alternative:* {new.product_code}")
    new_at = said.index("CONT-NEW")
    assert old_at < marker_at < new_at, said


# --------------------------------------------------------------------------- promotion


def _promo(name, start, end):
    return _field_item(name, [("Promotion", name), ("Start Date", start), ("End Date", end)])


def test_promotion_lists_the_alternatives_promotions_after_the_list(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([_promo("Old Deal", "2026-09-01T00:00:00", "2026-09-30T00:00:00")], result_type="promotions")
        return _envelope([_promo("Raya Promo", "2026-10-01T00:00:00", "2026-10-31T00:00:00")], result_type="promotions")

    said, _f, _c = _run(db, domain="promotion", entities=[_entity(old)], answers=answers)

    assert "Old Deal" in said
    assert said.rstrip().endswith(
        f"Alternative: {new.product_code}: Raya Promo (01/10/2026 to 31/10/2026)"
    ), said


def test_promotion_alternative_with_none_says_no_promotion(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([_promo("Old Deal", "2026-09-01T00:00:00", "2026-09-30T00:00:00")], result_type="promotions")
        return _envelope([], result_type="promotions")

    said, _f, _c = _run(db, domain="promotion", entities=[_entity(old)], answers=answers)
    assert said.rstrip().endswith(f"Alternative: {new.product_code}: No promotion."), said


def test_promotion_miss_on_the_old_code_still_names_the_alternative(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([], result_type="promotions")
        return _envelope([_promo("Raya Promo", "2026-10-01T00:00:00", "2026-10-31T00:00:00")], result_type="promotions")

    said, fetch, _c = _run(db, domain="promotion", entities=[_entity(old)], answers=answers)

    assert said == (
        f"No promotions found for {old.product_code}.\n"
        f"Alternative: {new.product_code}: Raya Promo (01/10/2026 to 31/10/2026)"
    ), said
    assert fetch.get("has_result") is True


# --------------------------------------------------------------------------- attachment


def test_attachment_names_the_alternatives_file_and_sends_it(db, pair):
    old, new = pair

    def answers(name, ids):
        code = old.product_code if ids == [old.id] else new.product_code
        filename = "old-spec.pdf" if ids == [old.id] else "new-spec.pdf"
        return _envelope(
            [
                _field_item(
                    code,
                    [("Product Code", code), ("Attachment Type", "Technical Specifications"), ("File Name", filename)],
                    discontinued=ids == [old.id],
                )
            ],
            result_type="product_attachments",
            attachments=[{"filename": filename, "file_path": f"s3://bucket/{filename}", "mime_type": "application/pdf"}],
            intro="I have attached the file(s) below.",
        )

    said, fetch, _c = _run(db, domain="product_attachment", entities=[_entity(old)], answers=answers)

    assert f"Alternative: {new.product_code}: new-spec.pdf" in said, said
    sent = [a.get("filename") for a in fetch.get("attachments") or []]
    assert sent == ["old-spec.pdf", "new-spec.pdf"], sent


# --------------------------------------------------------------------------- product info


def test_product_info_names_the_alternative_then_its_block(db, pair):
    old, new = pair

    def answers(name, ids):
        code = old.product_code if ids == [old.id] else new.product_code
        price = "MYR 100.00" if ids == [old.id] else "MYR 120.00"
        return _envelope(
            [
                _field_item(
                    code,
                    [("Product Code", code), ("List Price", price)],
                    discontinued=ids == [old.id],
                )
            ],
            result_type="products",
        )

    said, _f, _c = _run(db, domain="master_products", entities=[_entity(old)], answers=answers)

    marker = said.index("*(PRODUCT DISCONTINUED)*")
    alt = said.index(f"*Alternative:* {new.product_code}")
    new_price = said.index("MYR 120.00")
    assert said.index("MYR 100.00") < marker < alt < new_price, said


# --------------------------------------------------------------------------- when it does NOT fire


def test_live_old_code_gets_no_second_call(db, pair):
    old, new = pair
    old.is_discontinued = False
    db.flush()

    def answers(name, ids):
        return _envelope([_dealer_item(old.product_code, "No ETA")], result_type="incoming")

    said, _f, calls = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)
    assert len(calls) == 1
    assert "Alternative" not in said


def test_alternative_asked_in_its_own_right_gets_no_second_call(db, pair):
    old, new = pair

    def answers(name, ids):
        return _envelope(
            [_dealer_item(old.product_code, "No ETA", discontinued=True), _dealer_item(new.product_code, "No ETA")],
            result_type="incoming",
        )

    said, _f, calls = _run(db, domain="incoming", entities=[_entity(old), _entity(new)], answers=answers)
    assert len(calls) == 1
    assert "Alternative" not in said


def test_delivery_never_gets_an_alternative(db, pair):
    old, new = pair
    from app.services.chatbot.lanes.business import alternatives as alt_mod

    assert "crm_order_management_orders_list" not in alt_mod.TOOL_KINDS
    assert "crm_order_management_orders_by_product_list" not in alt_mod.TOOL_KINDS


def test_a_failed_alternative_call_leaves_the_reply_as_it_was(db, pair):
    old, new = pair

    def answers(name, ids):
        return _envelope([_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming")

    said, _f, calls = _run(db, domain="incoming", entities=[_entity(old)], answers=answers, fail_second=True)
    assert len(calls) == 2
    assert "Alternative" not in said
    assert f"{old.product_code}: No ETA" in said
