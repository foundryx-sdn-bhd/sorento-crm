"""ROGUE-CONVO-REVIEW PR-1: a zero-scope list asks for a filter, and the chatbot's product
list shows only what the chatbot may answer with (class A).

Owner, 6 Oct 2026, one dealer conversation (General Enquiries access), prompt v47. Two
turns fired the same mechanism: the one word "product" (parser `master_products`, zero
entities, `entity_op reuse`, nothing standing) and a code typed while a kind_pick was open
that missed. Both reached `crm_master_products_list` with no ids, and the route's default
page of 50 arbitrary products went out - "FOR ACCOUNT DEPARTMENT ONLY (DISBURSEMENT)", a
masked "****" description and discontinued rows among them.

Rulings (via crew): Q2, a domain-level refuse-empty read off one constant beside the gate's
own matrix (`gate.REFUSES_EMPTY_FETCH`, not a second list in the engine) answered with the
inventory scope sentence worded for products; Q1, the chatbot's list applies `is_searchable` (product and category, the
predicate the resolver already honours), `is_active`, and the contact's brand bound (the
session criterion `set_brand_scope` stamps from `contact_id` + `space_id`, which the fetch
builder already forwards on every tool call - proven here, not built).

Harness: the REAL engine, resolver, gate and narrower over seeded Postgres rows, only the
MCP call doubled (`test_rearch_r13_stock_no_subject.py`'s own convention). Plan:
`documentation/plans/chatbot/PLAN-rogue-convo-review-06oct.md`, UAC alongside. Fake fixture
codes throughout.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from app.models.base import set_company_scope
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._brand_scope_seed import api, branded_product, contact, db, world  # noqa: F401  (fixtures by name)
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_rearch_r5_production_decides import _mcp_double, _seed_contact_and_get
from tests.chatbot.test_rearch_r6_review_round import _run_turn_engine
from tests.chatbot.test_rearch_r12_handpass12 import _said, _unknown_envelope

# The MCP server is a SIBLING package in this monorepo, not a backend dependency - see
# `tests/test_mcp_catalog_ideation.py` for the rationale.
if importlib.util.find_spec("sorento_crm_mcp") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sorento_crm_mcp"))

PRODUCTS_TOOL = "crm_master_products_list"
PRODUCTS_ROUTE = "/api/v1/master-data/products"

#: `answer.not_found_error_message`'s `needs_scope` sentence with `_SCOPE_WORD["master_products"]`.
#: A PREFIX: the filter list after it is built from the gate's allowed kinds.
SCOPE_OPENING = "That would search every product we have"

#: The MCP presenter's unconditional intro for this tool (`presenters.py`), what the dealer read
#: above the 50 rows. Returned ONLY when the tool is actually called.
LISTING_MARKER = "Here are the matching products."


def _catalogue_page(rows: int = 50) -> str:
    return json.dumps(
        {
            "intro": LISTING_MARKER,
            "items": [
                {
                    "flags": {},
                    "title": f"ZZTDUMP{i:04d}",
                    "fields": [
                        {"key": "product_code", "label": "Product Code", "value": f"ZZTDUMP{i:04d}"},
                        {"key": "product_name", "label": "Name", "value": "FOR ACCOUNT DEPARTMENT ONLY"},
                    ],
                }
                for i in range(rows)
            ],
            "has_result": True,
            "attachments": [],
            "result_type": "products",
            "action_links": [],
        }
    )


def _product_ask(**overrides: Any) -> dict[str, Any]:
    """Turn 2's own verdict: `business_query` / `check_product` / `master_products`, NO
    entity of its own (`entity_op: reuse`)."""
    base = dict(
        message_type="business_query",
        intent_hint="check_product",
        domain_hint="master_products",
        domain_in_message=True,
        continuation=False,
        entities=[],
        entity_op="reuse",
        document=[],
        status=None,
        order_status=None,
        routing={"suggested_team": "sales", "suggested_agent": "general_enquiries", "team_source": None},
    )
    base.update(overrides)
    return _parser_output(**base)


def _typed(raw: str) -> dict[str, Any]:
    return {"raw": raw, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}


def _double():
    def _call(name: str, args: dict[str, Any]) -> str:
        if name == PRODUCTS_TOOL:
            return _catalogue_page()
        return _unknown_envelope()

    return _mcp_double(other=_call)


def _products_calls(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [args for name, args in calls if name == PRODUCTS_TOOL]


# =========================================================================== rule A, the ask


class TestABareProductAskNeverListsTheCatalogue:
    """AC-RC-A1 / A2: zero resolved entities, no filter, no date window, no broaden, in a
    domain of `gate.REFUSES_EMPTY_FETCH` - the same question `inventory` already asks."""

    def test_the_products_tool_is_never_called(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        mcp_call, calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_product_ask(), text_body="product",
            msg_id=f"zzt-a1-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        assert _products_calls(calls) == [], _products_calls(calls)

    def test_the_reply_is_the_scope_sentence_worded_for_products(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        mcp_call, _calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch, qf=_product_ask(), text_body="product",
            msg_id=f"zzt-a1r-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
        )
        said = _said(result)
        assert said.startswith(SCOPE_OPENING), said
        assert LISTING_MARKER not in said, said

    def test_a_typed_code_that_missed_never_sweeps_the_catalogue(self, session_factory, monkeypatch) -> None:
        """Turn 7's fetch: a product token the resolver could not place, in this domain, used
        to run the list with no ids under "I could not find srtwc-8318". The miss keeps its
        own wording (the token is named) and the tool is not called - never the scope sentence,
        which is for a message that named nothing."""
        _seed_contact_and_get(session_factory)
        mcp_call, calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch,
            qf=_product_ask(entities=[_typed("srtwc-8318")], entity_op="replace_combine"),
            text_body="srtwc-8318", msg_id=f"zzt-a7-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        assert _products_calls(calls) == [], _products_calls(calls)
        said = _said(result)
        assert LISTING_MARKER not in said, said
        assert not said.startswith(SCOPE_OPENING), said
        assert "srtwc-8318" in said.lower(), said


class TestControlsAroundTheAsk:
    def test_a_scoped_product_ask_still_fetches_with_its_id_and_the_chat_read(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-RC-A3 + the fetch half of A4: one call, the product's id in it, and the
        chatbot's own visibility flag on it."""
        code = unique_code("SRTWC", alpha=True)
        pid = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_contact_and_get(session_factory)
        mcp_call, calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch,
            qf=_product_ask(entities=[_typed(code)], entity_op="replace_combine", requested_attributes=["price"]),
            text_body=f"{code} price", msg_id=f"zzt-a3-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        products = _products_calls(calls)
        assert len(products) == 1, calls
        ids = products[0].get("product_ids")
        ids = json.loads(ids) if isinstance(ids, str) else ids
        assert str(pid) in [str(i) for i in ids], products[0]
        assert products[0].get("chat_visible") is True, products[0]
        assert products[0].get("contact_id") and products[0].get("space_id"), products[0]

    def test_a_multi_domain_ask_with_no_subject_refuses_the_product_leg_only(
        self, session_factory, monkeypatch
    ) -> None:
        """A multi-domain ask, "product and incoming?", with nothing to scope either leg: the
        product leg is refused outright by the runner's probe (never an unfiltered list), the
        incoming leg (`ALLOWS_EMPTY` True, lists the book) still runs. Pins that the probe,
        running per fetch, covers the fan-out shape (`turn/apply._REFUSES_EMPTY_SUBJECT`
        keeps its one inventory row)."""
        _seed_contact_and_get(session_factory)
        mcp_call, calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch,
            qf=_product_ask(
                domain_hint=None, intent_hint=None,
                asks=[{"domain": "master_products"}, {"domain": "incoming"}],
            ),
            text_body="product and incoming?", msg_id=f"zzt-a2multi-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
        )
        assert result.status == "done", result.error
        assert _products_calls(calls) == [], _products_calls(calls)
        assert any(name.startswith("crm_incoming") for name, _args in calls), calls

    def test_a_bare_po_ask_still_lists_the_open_book(self, session_factory, monkeypatch) -> None:
        """`ALLOWS_EMPTY["purchase_order"]` is True on purpose (PLAN-po-spo-warehouse-29sep
        W1) and the domain is not in `REFUSES_EMPTY_FETCH`, so this ask still reaches a PO
        tool."""
        _seed_contact_and_get(session_factory)
        mcp_call, calls = _double()
        result = _run_turn_engine(
            session_factory, monkeypatch,
            qf=_product_ask(domain_hint="purchase_order", intent_hint="check_order"),
            text_body="PO", msg_id=f"zzt-a3po-{uuid.uuid4().hex[:8]}", mcp_call=mcp_call,
            attributes=["purchase_orders.placed"],
        )
        assert result.status == "done", result.error
        assert any(name.startswith("crm_procurement") for name, _args in calls), calls
        assert not _said(result).startswith("That would search every purchase order"), _said(result)


# =========================================================================== rule A, visibility


def _seed_visibility_world(db) -> tuple[str, dict[str, str]]:
    """Four products under one unique prefix: one the chatbot may answer with, one flagged
    `is_searchable=False`, one in a category flagged so, one inactive."""
    prefix = unique_code("ZSV", alpha=True)
    searchable = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT visible",
        company_id=DEFAULT_COMPANY_ID,
    )
    hidden_category = ProductCategory(
        id=str(uuid.uuid4()), category_code=unique_code("CAT")[:50], category_name="ZZT MISC",
        company_id=DEFAULT_COMPANY_ID, is_searchable=False,
    )
    uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code=unique_code("UOM")[:20], uom_name="Each", company_id=DEFAULT_COMPANY_ID)
    db.add_all([searchable, hidden_category, uom])
    db.flush()
    codes = {
        "ok": f"{prefix}-OK",
        "flagged": f"{prefix}-FLAG",
        "hidden_category": f"{prefix}-MISC",
        "inactive": f"{prefix}-OLD",
    }
    rows = {
        "ok": dict(category_id=searchable.id),
        "flagged": dict(category_id=searchable.id, is_searchable=False),
        "hidden_category": dict(category_id=hidden_category.id),
        "inactive": dict(category_id=searchable.id, is_active=False),
    }
    for key, code in codes.items():
        db.add(
            Product(
                id=str(uuid.uuid4()), product_code=code, product_name=f"ZZT {code}",
                base_uom_id=uom.id, list_price=10, company_id=DEFAULT_COMPANY_ID, **rows[key],
            )
        )
    db.flush()
    return prefix, codes


def _listed(payload: dict[str, Any]) -> set[str]:
    """Codes in a list payload: the service hands ORM rows, the route hands dicts."""
    return {
        row["product_code"] if isinstance(row, dict) else row.product_code
        for row in payload.get("data") or []
    }


class TestTheChatbotListShowsOnlyWhatItMayAnswerWith:
    """AC-RC-A4 (owner ruling Q1): `chat_visible` applies `chat_searchable_products()` AND
    `is_active`; the admin list (no flag) is unchanged."""

    def test_service_chat_visible_drops_flagged_hidden_category_and_inactive_rows(self) -> None:
        from app.services.product_service import ProductService

        with blank_session() as db:
            set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
            prefix, codes = _seed_visibility_world(db)
            chat = ProductService(db).list_products(query=prefix, chat_visible=True, limit=50)
            assert _listed(chat) == {codes["ok"]}, _listed(chat)

    def test_service_a_named_inactive_code_is_still_answered(self) -> None:
        """A code the customer named resolves whether or not the product is active, so the
        chat read keeps an inactive row the call asks for by id; only the unscoped list is
        bounded to active rows. A flagged (`is_searchable=False`) row is out even by id: the
        resolver never places it, and the list agrees."""
        from app.models.product import Product
        from app.services.product_service import ProductService

        with blank_session() as db:
            set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
            prefix, codes = _seed_visibility_world(db)
            ids = {
                row.product_code: str(row.id)
                for row in db.query(Product).filter(Product.product_code.in_(list(codes.values()))).all()
            }
            by_id = ProductService(db).list_products(
                product_ids=[ids[codes["inactive"]], ids[codes["flagged"]]], chat_visible=True, limit=50
            )
            assert _listed(by_id) == {codes["inactive"]}, _listed(by_id)

    def test_service_admin_list_still_returns_every_row(self) -> None:
        from app.services.product_service import ProductService

        with blank_session() as db:
            set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
            prefix, codes = _seed_visibility_world(db)
            admin = ProductService(db).list_products(query=prefix, limit=50)
            assert _listed(admin) == set(codes.values()), _listed(admin)

    def test_route_chat_visible_for_a_brand_scoped_contact(self, api, db, world) -> None:
        """The MCP shape end to end: X-API-Key + `contact_id` + `space_id` + `chat_visible`.
        The brand bound (the router-level session criterion, #1512) and the chat read stack:
        a SORENTO-scoped contact sees the searchable SORENTO product alone - not the MOCHA or
        NULL-brand rows (brand), not the SORENTO row flagged `is_searchable=False` (chat)."""
        hidden = branded_product(db, unique_code("SRH", alpha=True), world.sorento)
        hidden.is_searchable = False
        scoped = contact(db, brand_ids=[world.sorento.id])
        db.commit()

        chat = api.get(PRODUCTS_ROUTE, scoped, query="ZZT", chat_visible="true", limit=200)
        assert chat.status_code == 200, chat.text
        listed = _listed(chat.json())
        assert world.p_sorento.product_code in listed, listed
        assert hidden.product_code not in listed, listed
        assert world.p_mocha.product_code not in listed and world.p_null.product_code not in listed, listed

        # Control: the same contact without the flag still sees the flagged SORENTO row
        # (brand bound alone), and a caller with no contact identity and no flag sees all.
        plain = api.get(PRODUCTS_ROUTE, scoped, query="ZZT", limit=200)
        assert hidden.product_code in _listed(plain.json()), _listed(plain.json())
        everyone = api.get(PRODUCTS_ROUTE, None, query="ZZT", limit=200)
        assert {world.p_sorento.product_code, world.p_mocha.product_code, world.p_null.product_code, hidden.product_code} <= _listed(everyone.json())


def test_the_mcp_catalog_declares_chat_visible_on_the_products_tool() -> None:
    """The compiled tool builds its signature from the ToolSpec's `query_params`: a param
    missing there never reaches the backend however well the fetch builder sets it."""
    from sorento_crm_mcp.catalog import CATALOG

    spec = next(s for s in CATALOG if s.name == PRODUCTS_TOOL)
    assert "chat_visible" in spec.query_params, spec.query_params
