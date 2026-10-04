"""Hand pass 12 RED tests, batch 3 - SPEC SEARCH (tester 52).

Owner ruling: "Can you suggest close couple wc available stock in p trap" (turn
7c39e638, `.claude/handpass/hp12-turns-21sep.json`) must answer as a spec search
combined with the stock requirement - the water closets that are close coupled AND
p-trap AND in stock. Source measurement: the captain's own M1 report
(`spec-search-m1-report.md`, replay scripts `replay_m1.py`/`replay_m1_instrumented.py`)
plus this session's OWN direct measurement, run under pytest (so `tests/conftest.py`'s
own company-scope auto-stamp listener is live) against a fresh, four-product catalogue
on the SAME private Postgres every other test in this file uses - never the M1 report's
shared clone DB. Confirmed a smaller instance of the exact same shape the report
measured: `qualifying_total=2` (WC-A AND WC-C both pass, since `specs` only ever
carries `trap_type=p_trap` - WC-C is wall-hung, not close-coupled, and qualifies
anyway) with `unrecognized_terms=["close couple wc"]` - a scaled-down version of the
report's own 97-including-a-wall-hung-row finding, not a different bug.

Two independently measured mechanisms combine to break this turn today:

1. `app/api/v1/system/references.py::resolve_reference_post` (:2604) has two branches -
   `require` (the HAS/predicate branch, `payload.require` truthy) and `spec_fallback`
   (older, runs when `require` is absent or a caller strips it). The live body
   (`resolve_gate.py::resolve_entity_body`, `lanes/business/resolve_gate.py:733-776`)
   sends BOTH `spec_fallback: true` and `require={"stock": true}` + `scope_terms`
   derived from the two `category`-hinted entities ("close couple wc", "p trap"). The
   `require` branch wins (`payload.require and not _code_matched(...)`,
   references.py:2639). Inside it, `derive_search_inputs(..., allow_model=False)`
   (`product_spec_understanding.py`, references.py:2687) is the DETERMINISTIC reader
   that decides `specs` - measured HERE (this session's own instrumented replay,
   scratchpad `probe_own_db.py`) to bind `trap_type=p_trap` off "p trap" but NEVER
   decompose "close couple wc" into `product_type=close_coupled` at all - it is only
   ever tested as an ATOMIC phrase inside `scope_terms` (`resolve_product_set`,
   product_predicate_service.py:576, folds `scope_terms` into `filter_specs`'s
   `free_terms`), which recognises no class/product_type label for the literal string
   "close couple wc" and reports it under `predicate.unrecognized_terms`.
2. Engine side (measured from the trace + code): both entities are parser-hinted
   `category`. `gate.py`'s `NO_TOOL_ID = frozenset({"brand", "category"})` (gate.py:151)
   and `turn/policy_rows.py:401`'s own `kind="category"` policy row mean a category
   entity never carries a resolved PRODUCT id through the ordinary token-resolution
   path - the ONLY route a "close couple wc"/"p trap" ask can ever filter the stock
   tool through is the `predicate`/`spec_search` mechanism above. Per the captain's own
   measurement, `predicate`'s qualifying ids are read nowhere except the miss
   composer's DISPLAY text (`answer.py`'s `by_type`/`found_lines`) - nothing bridges a
   qualifying/spec-search id into the "product" kind's `resolved_candidates` at
   `turn/narrow.py::decide` time, so `inventory`'s `product` policy (`list_all`,
   `policy_rows.py:113`) never sees them and the fetch step is starved of a
   `product_ids` filter regardless of what `predicate` found.

GROUP S-ROUTE measures mechanism 1 directly against the endpoint, real Postgres seeds
(never borrowed). GROUP S-ENGINE/S-GUARD measure mechanism 2 through the full engine
(`engine_mod.run_turn`), same `stub_parser`/`stub_access` + real-resolver harness
`test_rearch_s3_attribute_first.py` already uses (production_services is left at its
default real binding - `spec_fallback`/`understand_phrase` are NOT forced off the way
`test_engine_company_scope.py::_real_resolve_entity` does for other files in this
suite, because this turn's own fix lives inside that exact branch). The MCP boundary
(`MCPRuntimeClient.call_tool`) is the one seam doubled, same convention
`test_rearch_s3_attribute_first.py::_stub_certificate_tools` already uses.

Seeding (measured, this session): `ProductCategory.class_label="Water Closet"` +
`ProductSpecRegistry` (`seed_spec_registry`) + `derive_for_code` off a real
description string is what makes `p-trap`/`close coupled`/`wall hung`/`s-trap` derive
onto `ProductSpecifications.values` exactly as `tests/test_resolve_predicate.py` and
`tests/chatbot/test_rearch_s3_attribute_first.py` already do it - copied, not invented.
Every product code is ZZT-prefixed; every row is fresh per test.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.inventory import Stock, Warehouse
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from tests._pg_fixture import blank_session, unique_code
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, stub_access, stub_parser
from tests.chatbot.test_outstanding_lane import _seed_contact as _seed_business_contact

ENDPOINT = "/api/v1/system/references/resolve"
_USER = {"id": str(uuid.uuid4()), "email": "n8n@example.com"}

STOCK_TOOL = "crm_inventory_stock_balance_list"
PROMOTION_TOOL = "crm_marketing_promotions_list"

TURN_TEXT = "Can you suggest close couple wc available stock in p trap"


def _wc_entities() -> list[dict[str, Any]]:
    """The two `category`-hinted entities the live parser emitted for turn 7c39e638
    (`hp12-turns-21sep.json`, `understood` stage, `entities`), verbatim."""
    return [
        {
            "raw": "close couple wc",
            "hint": "category",
            "confident": True,
            "canonical_code": None,
            "current_message": True,
        },
        {
            "raw": "p trap",
            "hint": "category",
            "confident": True,
            "canonical_code": None,
            "current_message": True,
        },
    ]


def _wc_parser_output(**overrides: Any) -> dict[str, Any]:
    base = dict(
        domain_hint="inventory",
        intent_hint="check_stock",
        user_goal="trying to check available stock for close couple wc in p trap",
        match_mode="and",
        entities=_wc_entities(),
    )
    base.update(overrides)
    return _parser_output(**base)


# --------------------------------------------------------------------------- #
# Shared seeding: four water closets, real spec derivation off real descriptions.
# WC-A: close_coupled + p_trap + stock>0   <- the ONLY one that answers the turn
# WC-B: close_coupled + p_trap + stock=0   <- right specs, wrong stock
# WC-C: wall_hung + p_trap + stock>0       <- right trap, wrong product_type
# WC-D: close_coupled + s_trap + stock>0   <- right product_type, wrong trap
# --------------------------------------------------------------------------- #

_WC_SPECS: dict[str, tuple[str, int]] = {
    "A": ("SORENTO CLOSE COUPLED WATER CLOSET P-TRAP 180MM", 5),
    "B": ("SORENTO CLOSE COUPLED WATER CLOSET P-TRAP 250MM", 0),
    "C": ("SORENTO WALL HUNG WATER CLOSET P-TRAP 180MM", 5),
    "D": ("SORENTO CLOSE COUPLED WATER CLOSET S-TRAP 180MM", 5),
}


def _seed_wc_catalogue(db, *, company_id: str | None) -> dict[str, dict[str, str]]:
    """Seeds WC-A..D directly on `db` (caller owns commit/rollback). Returns
    `{key: {"id": ..., "code": ...}}`. `company_id=None` is the route-level (S-ROUTE)
    shape - `test_resolve_predicate.py`'s own fixture leaves it unset too and relies
    on `apply_company_scope`'s test override (`lambda: None`, all companies visible)
    rather than a real scope; the engine-level groups (S-ENGINE/S-GUARD) pass
    `DEFAULT_COMPANY_ID` explicitly since the real resolver there reads through a
    REAL company-scoped session, seeded via `_seed_business_contact`."""
    cat = ProductCategory(
        id=str(uuid.uuid4()),
        category_code=unique_code("WC")[:50],
        category_name="ZZT Water Closet",
        class_label="Water Closet",
        search_synonyms=["water closet", "wc", "toilet"],
        **({"company_id": company_id} if company_id else {}),
    )
    uom = UnitOfMeasure(
        id=str(uuid.uuid4()),
        uom_code=unique_code("U")[:20],
        uom_name="Each",
        **({"company_id": company_id} if company_id else {}),
    )
    db.add_all([cat, uom])
    db.flush()
    backfill_category_signals(db)
    seed_spec_registry(db)

    wh = Warehouse(
        id=str(uuid.uuid4()),
        warehouse_code=unique_code("WH")[:20],
        warehouse_name="ZZT WH",
        **({"company_id": company_id} if company_id else {}),
    )
    db.add(wh)
    db.flush()

    out: dict[str, dict[str, str]] = {}
    for key, (description, qty) in _WC_SPECS.items():
        code = unique_code(f"WC{key}")
        product = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=code,
            description=description,
            category_id=cat.id,
            base_uom_id=uom.id,
            list_price=Decimal("1.00"),
            **({"company_id": company_id} if company_id else {}),
        )
        db.add(product)
        db.flush()
        derive_for_code(db, code)
        db.add(
            Stock(
                id=str(uuid.uuid4()),
                product_id=product.id,
                warehouse_id=wh.id,
                quantity_on_hand=qty,
                quantity_reserved=0,
                quantity_damaged=0,
                **({"company_id": company_id} if company_id else {}),
            )
        )
        db.flush()
        out[key] = {"id": product.id, "code": code}
    return out


def _live_body(entities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The live-shaped POST body, built the SAME way `resolve_entity_body` builds it
    (`resolve_gate.py:670-780`), reproduced field for field rather than invoked, so
    this group tests the ENDPOINT contract directly with no engine/ctx machinery in
    the way. Verified field-for-field against a real `resolve_entity_body(...)` call
    over the identical parser output (scratchpad `probe_own_db.py`, `BODY:` block)."""
    ents = entities if entities is not None else _wc_entities()
    tokens = [e["raw"] for e in ents]
    return {
        "query": TURN_TEXT,
        "match_mode": "and",
        "tokens": tokens,
        "allowed_entity_types": [e["hint"] for e in ents],
        "access_levels": [],
        "domain": "inventory",
        "fallback_to_all_types": True,
        "limit": 50,
        "spec_fallback": True,
        "understand_phrase": True,
        "hidden_spec_keys": [],
        "dry_run": True,
        "require": {"stock": True},
        "predicate_words": ["stock"],
        "scope_terms": tokens,
    }


def _all_match_codes(payload: dict[str, Any]) -> set[str]:
    """Every product CODE the response exposes for filtering, across `resolutions`,
    `intersection` and `spec_candidates` - the union the brief asks S1 to assert on,
    since the exact key the engine would read from is itself part of what is broken."""
    codes: set[str] = set()
    for resolution in payload.get("resolutions") or []:
        for m in resolution.get("matches") or []:
            code = m.get("code") or m.get("canonical_code") or m.get("product_code")
            if code:
                codes.add(code)
    for m in payload.get("intersection") or []:
        code = m.get("code") or m.get("canonical_code") or m.get("product_code")
        if code:
            codes.add(code)
    for c in payload.get("spec_candidates") or []:
        code = c.get("code") or c.get("product_code")
        if code:
            codes.add(code)
    return codes


@pytest.fixture()
def client():
    from app.database import get_db
    from app.dependencies import (
        get_current_user,
        get_current_user_or_api_key,
        get_external_api_user,
    )
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope

    with blank_session() as db:
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_external_api_user] = lambda: _USER
        app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
        app.dependency_overrides[get_current_user] = lambda: _USER
        app.dependency_overrides[apply_company_scope] = lambda: None
        try:
            yield TestClient(app), db
        finally:
            app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# GROUP S-ROUTE - the resolver route, real DB seeds, no engine.
# --------------------------------------------------------------------------- #


class TestSRouteRequireBranchMissesCloseCoupled:
    def test_s1_qualifying_set_excludes_wrong_specs_and_must_include_wc_a(self, client) -> None:
        """The union of product codes the response exposes for filtering must contain
        WC-A (close_coupled + p_trap + stock>0, the only correct answer) and must
        NEVER contain WC-B (no stock), WC-C (not close coupled) or WC-D (not p trap).

        MEASURED (this session, actual pytest run against this file's own seeded
        catalogue): today the `require` branch's own `predicate.qualifying_total` is 2
        - WC-A (correct) AND WC-C (wall-hung, WRONG) both pass, because `specs` only
        ever carries `trap_type=p_trap` (the deterministic reader never decomposes
        "close couple wc" into `product_type=close_coupled` at all), so the qualifying
        query has no product_type filter to exclude a wall-hung row. A smaller instance
        of the exact shape the captain's own report measured against its much larger
        clone catalogue (there: 97 qualifying, top row wall-hung; here: 2 qualifying,
        one of them wall-hung) - same root cause, same wrong inclusion, not a different
        bug. RED today: WC-C is wrongly present."""
        tc, db = client
        wc = _seed_wc_catalogue(db, company_id=None)
        db.commit()

        response = tc.post(ENDPOINT, json=_live_body())
        assert response.status_code == 200
        payload = response.json()

        codes = _all_match_codes(payload)
        assert wc["A"]["code"] in codes, (
            f"WC-A (close coupled, p trap, in stock) must be in the qualifying set: {codes!r}, "
            f"predicate={payload.get('predicate')!r}"
        )
        assert wc["B"]["code"] not in codes, f"WC-B has no stock, must never qualify: {codes!r}"
        assert wc["C"]["code"] not in codes, f"WC-C is wall hung, not close coupled: {codes!r}"
        assert wc["D"]["code"] not in codes, f"WC-D is s-trap, not p-trap: {codes!r}"

    def test_s2_close_couple_wc_is_not_reported_unrecognized(self, client) -> None:
        """`predicate.unrecognized_terms` must not name "close couple wc" - the parser
        NAMED a real class (water closet) in a real product_type (close coupled); an
        "unrecognized" stamp on it is what drives the wrong "Couldn't find" copy on the
        live reply (turn 7c39e638's own `replied` stage: `Couldn't find: "close couple
        wc" (category).`). MEASURED RED today (actual pytest run): `predicate` reads
        `unrecognized_terms == ['close couple wc']` for the live-shaped body."""
        tc, db = client
        _seed_wc_catalogue(db, company_id=None)
        db.commit()

        response = tc.post(ENDPOINT, json=_live_body())
        assert response.status_code == 200
        predicate = response.json()["predicate"]
        assert "close couple wc" not in predicate["unrecognized_terms"], predicate


class TestSRouteGreenGuards:
    def test_s3i_require_branch_scope_term_it_already_places_is_unchanged(self, client) -> None:
        """GREEN guard, unchanged mechanism: a `require` turn whose scope term the
        branch ALREADY places correctly must keep answering it - pinned against the
        SAME real product/company/spec seeding this file uses (not a copy of
        `test_resolve_predicate.py`'s own kitchen-sink fixture, so the guard is a true
        pin of the shared mechanism under THIS file's own seeded data, not a second
        independent claim). "p trap" alone (no class word) is a scope term the
        deterministic reader already binds today (`derive_search_inputs` ->
        `trap_type=p_trap`, confirmed in `probe_own_db.py`), so a require turn scoped
        by "p trap" ALONE (no "close couple wc") must still recognise it - never listed
        under `unrecognized_terms`."""
        tc, db = client
        _seed_wc_catalogue(db, company_id=None)
        db.commit()

        body = _live_body(entities=[_wc_entities()[1]])  # "p trap" only
        response = tc.post(ENDPOINT, json=body)
        assert response.status_code == 200
        predicate = response.json()["predicate"]
        assert "p trap" not in predicate["unrecognized_terms"], predicate

    def test_s3ii_spec_fallback_branch_without_require_is_unchanged(self, client) -> None:
        """GREEN guard: the SAME body with `require` stripped (spec_fallback branch,
        mechanism 1's OTHER half) is untouched by whatever fixes mechanism 1's
        `require` branch - it must keep returning `spec_candidates`/`resolutions` with
        `match_tier="spec_search"` for this catalogue, exactly as it does today
        (confirmed in `probe_own_db.py`'s own "b-no-require" replay: all four WC codes
        surface as `spec_search` matches today, unchanged by this repair)."""
        tc, db = client
        _seed_wc_catalogue(db, company_id=None)
        db.commit()

        body = _live_body()
        body.pop("require", None)
        body.pop("predicate_words", None)
        body.pop("scope_terms", None)
        response = tc.post(ENDPOINT, json=body)
        assert response.status_code == 200
        payload = response.json()
        assert "predicate" not in payload
        tiers = {
            m.get("match_tier")
            for resolution in payload.get("resolutions") or []
            for m in resolution.get("matches") or []
        }
        assert "spec_search" in tiers, payload


# --------------------------------------------------------------------------- #
# GROUP S-ENGINE - turn 7c39e638 replayed through the real engine, real resolver
# (production_services left at its default binding - spec_fallback/understand_phrase
# NOT forced off), only the MCP boundary doubled.
# --------------------------------------------------------------------------- #


def _capturing_call_tool(monkeypatch, *, stock_rows_by_ids: dict[str, list[dict[str, Any]]] | None = None):
    """Patches `MCPRuntimeClient.call_tool` directly (the seam
    `test_rearch_s3_attribute_first.py::_stub_certificate_tools` already uses for this
    same real-resolver harness) - `engine_mod.business_services.fetch_services`/
    `production_services` are left untouched, so the ONLY double in this group is the
    network boundary. Records every `(name, arguments)` call."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, dict(arguments)))
        if name == STOCK_TOOL and stock_rows_by_ids:
            wanted = arguments.get("product_ids") or []
            rows = []
            for pid in wanted:
                rows.extend(stock_rows_by_ids.get(pid, []))
            return json.dumps(
                {
                    "items": rows,
                    "has_result": bool(rows),
                    "attachments": [],
                    "action_links": [],
                    "result_type": "stock_compact",
                    "intro": "Stock summary for the requested products." if rows else "No matching results found.",
                }
            )
        return json.dumps({"items": [], "has_result": False})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", fake_call_tool)
    return calls


def _stock_row(code: str, total: int) -> dict[str, Any]:
    return {
        "flags": {},
        "title": code,
        "fields": [
            {"key": "product_code", "label": "Product Code", "value": code},
            {"key": "total_on_hand", "label": "Total", "value": total},
        ],
    }


class TestSEngineStockToolFiltersToTheQualifyingProduct:
    def test_stock_tool_is_called_with_the_qualifying_product_and_the_reply_answers_it(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Replays turn 7c39e638 through the real engine (real resolver, real narrower)
        over this file's own seeded WC-A..D. RED today (mechanism 2, module
        docstring): the stock tool call today carries no `product_ids` at all for
        these two `category`-hinted entities, whatever the resolver found."""
        _seed_business_contact(session_factory, variables={})
        db = session_factory()
        wc = _seed_wc_catalogue(db, company_id=DEFAULT_COMPANY_ID)
        db.commit()

        calls = _capturing_call_tool(
            monkeypatch,
            stock_rows_by_ids={wc["A"]["id"]: [_stock_row(wc["A"]["code"], 5)]},
        )

        stub_parser(_wc_parser_output())
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert stock_calls, f"the stock tool must be called at all: {calls!r}"
        product_ids_sent = set(stock_calls[0].get("product_ids") or [])
        assert product_ids_sent == {wc["A"]["id"]}, (
            f"expected only WC-A's id, got {product_ids_sent!r} (all calls: {calls!r})"
        )
        diagnostics = stock_calls[0].get("_diagnostics") or {}
        assert diagnostics.get("entities_in") != 0, diagnostics

        text = (result.reply or {}).get("text", "")
        assert wc["A"]["code"] in text, text
        assert "Couldn't find" not in text, text
        assert "escalate" not in text.lower(), text


# --------------------------------------------------------------------------- #
# GROUP S-GUARD - a turn whose named entities resolve to NO placeable filter must
# never call a list tool with zero filters (`miss_suggest.py`'s own documented danger:
# "crm_inventory_stock_balance_list spans every product when called with none").
# --------------------------------------------------------------------------- #


class TestSGuardNeverCallsStockToolUnfiltered:
    def test_unplaceable_entities_never_call_stock_tool_with_zero_filters(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """Replays 7c39e638 with NOTHING in the catalogue that could ever answer it (no
        WC seed at all - the real resolver genuinely finds nothing placeable for
        either "close couple wc" or "p trap", the same "nothing placeable" shape the
        brief names). The stock tool must not be called at all; the reply must be a
        miss naming what could not be found, never a bare unfiltered listing."""
        _seed_business_contact(session_factory, variables={})

        calls = _capturing_call_tool(monkeypatch)

        stub_parser(_wc_parser_output())
        stub_access()

        from app.services.chatbot import engine as engine_mod

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        stock_calls = [args for name, args in calls if name == STOCK_TOOL]
        assert not stock_calls, f"the stock tool must never be called with zero filters: {stock_calls!r}"

        text = (result.reply or {}).get("text", "")
        assert "close couple wc" in text.lower() or "p trap" in text.lower(), text

    def test_green_guard_a_genuine_list_all_promotion_ask_still_calls_its_tool(
        self, session_factory, stub_parser, stub_access, monkeypatch
    ) -> None:
        """GREEN guard: a genuine "what's on promotion" ask names NO entity at all (the
        SAME `entities=[]` shape `test_rearch_s3_attribute_first.py::
        TestTierVisiblePromotionsOnly` already pins as a real, working, entity-less
        `list_all`-shaped flow) and must keep calling its own tool today, unlike the
        S-GUARD case above where entities WERE named but none placed."""
        _seed_business_contact(session_factory, variables={})

        calls = _capturing_call_tool(monkeypatch)

        stub_parser(
            _parser_output(
                domain_hint="promotion",
                intent_hint=None,
                entities=[],
                requested_attributes=["promotion"],
                user_goal="what's on promotion",
            )
        )
        stub_access(attributes=["promotion.view"])

        from app.services.chatbot import engine as engine_mod

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        promo_calls = [args for name, args in calls if name == PROMOTION_TOOL]
        assert promo_calls, f"a genuine list-all promotion ask must still call its tool: {calls!r}"
