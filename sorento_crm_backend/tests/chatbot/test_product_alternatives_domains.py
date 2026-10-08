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


full_args: list[dict] = []


def _run(db, *, domain, entities, answers, fail_second=False, requested=None, localizer=None):
    """`answers(product_ids) -> raw envelope`; returns (response text, structured, calls)."""
    calls: list[tuple[str, list]] = []
    full_args.clear()

    def mcp_call(name, args):
        ids = list(args.get("product_ids") or [])
        calls.append((name, ids))
        full_args.append(dict(args))
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
                    "requested_attributes": requested or [],
                }
            },
            "contact": {},
            "localizer": localizer,
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


# --------------------------------------------------------------------------- DISCONTINUED-ASK (#1535)


def _product_envelope(old, new):
    def answers(name, ids):
        code = old.product_code if ids == [old.id] else new.product_code
        return _envelope(
            [_field_item(code, [("Product Code", code), ("List Price", "MYR 1.00")], discontinued=ids == [old.id])],
            result_type="products",
        )

    return answers


def test_is_it_discontinued_names_the_alternative_once_and_no_extra_status_line(db, pair):
    """#1535 answers a status-only ask with one line per product and names the item's
    `alternatives`; the resolver feeds that key, and the alternative's own block (which would
    print "X-NEW: Not discontinued.") is not added to a status-only answer."""
    old, new = pair
    said, _f, calls = _run(
        db,
        domain="master_products",
        entities=[_entity(old)],
        answers=_product_envelope(old, new),
        requested=["discontinued"],
    )
    # Review round B nit 8: the status line names the code alone, so nothing is fetched for it.
    assert len(calls) == 1
    assert said == f"{old.product_code}: \u26a0\ufe0f Discontinued.\nAlternative: {new.product_code}", said


def test_status_beside_another_property_keeps_the_alternatives_block(db, pair):
    old, new = pair
    said, _f, _c = _run(
        db,
        domain="master_products",
        entities=[_entity(old)],
        answers=_product_envelope(old, new),
        requested=["discontinued", "price"],
    )
    assert said.count(f"Alternative: {new.product_code}") + said.count(f"*Alternative:* {new.product_code}") == 1, said
    assert new.product_code in said.split(f"Alternative: {new.product_code}", 1)[-1], said


# --------------------------------------------------------------------------- review round B


def test_staff_incoming_old_code_with_no_rows_still_shows_the_alternatives_rows(db, pair):
    """Reviewer B1: a discontinued code usually has no incoming of its own; staff still get
    the alternative's rows (its container and ETA), not just its name."""
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([], result_type="incoming")
        return _envelope(
            [_field_item(new.product_code, [("Product Code", new.product_code), ("Container", "CONT-NEW")])],
            result_type="incoming",
        )

    said, fetch, _c = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)
    assert said.startswith(
        f"No incoming stock found for {old.product_code}.\n*Alternative:* {new.product_code}\n"
    ), said
    assert "CONT-NEW" in said, said
    assert fetch.get("has_result") is True


def test_a_miss_everywhere_keeps_the_miss_handling(db, pair):
    """Reviewer 4: "No promotion." for the alternative is not a result; the miss stays a
    miss (rerun and escalation still apply)."""
    old, new = pair
    said, fetch, _c = _run(
        db,
        domain="promotion",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope([], result_type="promotions"),
    )
    assert said == f"No promotions found for {old.product_code}.\nAlternative: {new.product_code}: No promotion."
    assert not fetch.get("has_result")


def test_the_second_call_asks_the_same_question_about_the_alternative(db, pair):
    """Security S3: every argument of the second call but the product equals the first's."""
    import uuid as _uuid

    old, new = pair
    type_id = str(_uuid.uuid4())

    def answers(name, ids):
        return _envelope([], result_type="product_attachments")

    _run(
        db,
        domain="product_attachment",
        entities=[_entity(old), {"uuid": type_id, "entity_type": "attachment_type", "code": "Technical Specifications"}],
        answers=answers,
    )
    assert len(full_args) == 2
    first, second = ({k: v for k, v in a.items() if k not in ("product_ids", "_diagnostics")} for a in full_args)
    assert first == second
    assert first.get("attachment_type_ids") == [type_id]
    assert full_args[1]["product_ids"] == [new.id]


def test_the_second_call_goes_through_the_brand_guard(db, pair, monkeypatch):
    """Reviewer 2: the alternative's answer passes the same output guard, with the same scope."""
    from app.services.chatbot.lanes.business import brand_guard

    old, new = pair
    seen: list = []
    real = brand_guard.guard_result

    def spy(tool_name, result, scope, session):
        seen.append(scope)
        return real(tool_name, result, scope, session)

    monkeypatch.setattr(brand_guard, "guard_result", spy)
    _run(
        db,
        domain="incoming",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope(
            [_dealer_item(new.product_code if ids == [new.id] else old.product_code, "No ETA")],
            result_type="incoming",
        ),
    )
    assert len(seen) == 2 and seen[0] == seen[1]


def test_an_alternative_outside_the_contacts_brands_is_never_named(db, pair, monkeypatch):
    """Security S2 / reviewer 3: the brand scope is checked in the plan itself, not only by
    the session's own filter, so an out-of-scope alternative is neither fetched nor named."""
    import uuid as _uuid

    from app.models.product import Brand

    old, new = pair
    brand_a = Brand(id=str(_uuid.uuid4()), brand_code=unique_code("BA")[:40], brand_name="A", company_id=DEFAULT_COMPANY_ID)
    brand_b = Brand(id=str(_uuid.uuid4()), brand_code=unique_code("BB")[:40], brand_name="B", company_id=DEFAULT_COMPANY_ID)
    db.add_all([brand_a, brand_b])
    db.flush()
    old.brand_id = brand_a.id
    new.brand_id = brand_b.id
    db.flush()
    monkeypatch.setattr(business, "_brand_scope_ids", lambda *a, **k: [brand_a.id])

    said, _f, calls = _run(
        db,
        domain="incoming",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope(
            [_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming"
        ),
    )
    assert len(calls) == 1
    assert new.product_code not in said


def test_a_failed_lookup_leaves_the_reply_as_it_was(db, pair, monkeypatch):
    """Security S1: the plan's own lookup failing costs nothing."""
    from app.services import product_alternative_service

    old, new = pair

    def boom(*a, **k):
        raise RuntimeError("lookup down")

    monkeypatch.setattr(product_alternative_service, "alternatives_for", boom)
    said, _f, calls = _run(
        db,
        domain="incoming",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope(
            [_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming"
        ),
    )
    assert len(calls) == 1
    assert f"{old.product_code}: No ETA" in said and "Alternative" not in said


def test_a_counted_set_gets_no_alternatives():
    from app.services.chatbot.lanes.business import alternatives as alt_mod

    assert alt_mod.plan(object(), "crm_master_products_list", [{"entity_type": "product", "uuid": "x"}], predicate={}) == []


def test_two_alternatives_each_get_their_own_promotions(db, pair):
    """Security nit: with one call per alternative, each is credited with its own promotions."""
    old, new = pair
    other = product(db, company_id=DEFAULT_COMPANY_ID, code=f"{old.product_code}-NEW-B")
    db.add(ProductAlternative(product_id=old.id, alternative_product_id=other.id, source="manual", company_id=DEFAULT_COMPANY_ID))
    db.flush()

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([], result_type="promotions")
        if ids == [new.id]:
            return _envelope([_promo("Promo New", "2026-10-01T00:00:00", "2026-10-31T00:00:00")], result_type="promotions")
        return _envelope([_promo("Promo B", "2026-11-01T00:00:00", "2026-11-30T00:00:00")], result_type="promotions")

    said, _f, calls = _run(db, domain="promotion", entities=[_entity(old)], answers=answers)
    assert len(calls) == 3
    assert f"Alternative: {new.product_code}: Promo New (01/10/2026 to 31/10/2026)" in said
    assert f"Alternative: {other.product_code}: Promo B (01/11/2026 to 30/11/2026)" in said
    assert f"Alternative: {new.product_code}: Promo B" not in said


# --------------------------------------------------------------------------- re-review round B


def test_an_alternative_whose_call_returned_nothing_still_shows_the_search(db, pair):
    """Owner, 9 Oct 2026 (reverses re-review 1): "at least we need to show that we have tried
    to search for it". The old code has a row, the alternative's call returns none for its
    code: the reply names the alternative and says it has nothing."""
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope(
                [_field_item(old.product_code, [("Product Code", old.product_code), ("Container", "CONT-OLD")])],
                result_type="incoming",
            )
        return _envelope([], result_type="incoming")

    said, _f, calls = _run(db, domain="incoming", entities=[_entity(old)], answers=answers)
    assert len(calls) == 2
    assert "CONT-OLD" in said
    assert f"Alternative: {new.product_code}: No incoming stock." in said, said


def test_the_miss_sentence_and_tail_read_in_the_contacts_language(db, pair):
    """Reviewer re-review 2: on a localised tool the miss sentence and the Alternative label
    are the contact's language, as compose and `_item_line` would print them."""
    from app.services.chatbot.label_catalog import Localizer, defaults

    old, new = pair
    ms = Localizer("ms", defaults("ms"))

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([], result_type="incoming")
        return _envelope(
            [_field_item(new.product_code, [("Product Code", new.product_code), ("Container", "CONT-NEW")])],
            result_type="incoming",
        )

    said, _f, _c = _run(db, domain="incoming", entities=[_entity(old)], answers=answers, localizer=ms)
    assert said.startswith(ms.sentence(f"No incoming stock found for {old.product_code}.") + "\n"), said
    assert f"*{ms.text('Alternative')}:* {new.product_code}" in said, said
    assert ms.text("Alternative") != "Alternative"


def test_a_fold_that_fails_halfway_leaves_the_reply_as_it_was(db, pair, monkeypatch):
    """Re-review nit 3: the fold works on a copy; a failure half way never leaks into the
    reply the first call made."""
    from app.services.chatbot.lanes.business import alternatives as alt_mod

    old, new = pair

    def half(tool_name, envelope, answers, **kw):
        envelope["items"][0]["flags"]["alternative_lines"] = ["LEAKED"]
        raise RuntimeError("fold broke")

    monkeypatch.setattr(alt_mod, "fold", half)
    said, _f, _c = _run(
        db,
        domain="incoming",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope(
            [_dealer_item(old.product_code if ids == [old.id] else new.product_code, "No ETA", discontinued=ids == [old.id])],
            result_type="incoming",
        ),
    )
    assert "LEAKED" not in said and "Alternative" not in said, said


def test_the_narrowing_filter_rule_holds_for_the_second_call(db, pair, monkeypatch):
    """Re-review nit 3: a tool that needs a narrowing filter never gets an unnarrowed second
    call."""
    from app.services.chatbot.lanes.business import fetch as fetch_mod
    from app.services.chatbot.turn import policy_rows

    old, new = pair
    monkeypatch.setattr(
        policy_rows,
        "ENTITY_FILTER_REQUIRED_TOOLS",
        frozenset(policy_rows.ENTITY_FILTER_REQUIRED_TOOLS) | {"crm_incoming_stock_list"},
    )
    real = fetch_mod.has_narrowing_filter
    monkeypatch.setattr(
        fetch_mod,
        "has_narrowing_filter",
        lambda args, tool_name=None: (args.get("product_ids") or [None])[0] != new.id and real(args, tool_name=tool_name),
    )
    said, _f, calls = _run(
        db,
        domain="incoming",
        entities=[_entity(old)],
        answers=lambda name, ids: _envelope(
            [_dealer_item(old.product_code, "No ETA", discontinued=True)], result_type="incoming"
        ),
    )
    assert len(calls) == 1
    assert "Alternative" not in said


def test_an_expired_promotion_of_the_alternative_says_so(db, pair):
    old, new = pair

    def answers(name, ids):
        if ids == [old.id]:
            return _envelope([], result_type="promotions")
        item = _promo("Old Raya", "2026-04-01T00:00:00", "2026-04-30T00:00:00")
        item["flags"] = {"expired": True}
        return _envelope([item], result_type="promotions")

    said, _f, _c = _run(db, domain="promotion", entities=[_entity(old)], answers=answers)
    assert said.endswith(
        f"Alternative: {new.product_code}: Old Raya (01/04/2026 to 30/04/2026)\n\u26a0\ufe0f  *(EXPIRED)*"
    ), said
