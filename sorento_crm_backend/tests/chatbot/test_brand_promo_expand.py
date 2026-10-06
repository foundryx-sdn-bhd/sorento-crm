"""BRAND-PROMO-EXPAND (owner ruling, 6 Oct 2026: "Sorento is always a brand").

Prod turn 120 (prompt v47) read a photo naming SRTWC8383-RL-GY and SORENTO and left
`focus.extra["promotion"] = [{raw SORENTO, hint promotion, canonical_code SORENTO}]`; the fill
turn "1" then re-sent that row to the resolver (PR #1502 fixes the fill turn's side).

Crew scout (dev DBs, 8,532 turns): the parser never emitted a brand word under `hint:
promotion`. Every stored promotion row on an inventory turn ("sorento", "sorento wc") traces
back to a turn where the parser said `brand` / `category` and the resolver's brand/category
-> promotion fan-out (`entity_resolver._ENTITY_TYPE_EXPANSIONS`) answered the word with
promotion matches; `turn/reconcile.py::apply_reconciliation` then re-typed the hint because
promotion was "the one kind hitting", `apply._set_kind_field` stored the row under
`focus.extra["promotion"]`, and `turn_runtime.with_carried_entities` re-emitted it as a
promotion on every later turn.

Prod trace of the photo turn (crew, 6 Oct 2026): the parser and media_intake emitted
`{raw SORENTO, hint brand}`; the resolver gate (inventory, allowed product/warehouse/
category/brand) answered SORENTO with 20 matches, ALL `promotion` (prefix on the promotion
description), ZERO brand-table matches although the product's own `brand_code` is SORENTO;
`gate_debug.incompatible_only = {SORENTO: [promotion]}`. The shared resolver has no brand
probe at all (`entity_resolver._ENTITY_TYPE_EXPANSIONS`: brand/category fan out to the
promotion probe); the chatbot's own live-brand lookup (`turn_runtime._brand_hinted_entities_
matching_live`, the `brands` table) ran for the order and sales domains only.

Rules pinned here:
1. The parser decides hints. A resolver hit reached only through the brand/category ->
   promotion fan-out confirms the hint, it never re-types it. The carried row keeps `brand` /
   `category`; a brand carried into inventory is a brand (on `focus.brands`), never a
   promotion; the promotion domain's own fetch still lists a brand's promotions off the
   resolver's matches for that turn.
2. (crew A) A brand hint resolves against the `brands` table FIRST, in every domain: a word
   that is a live brand is never sent to the shared resolver outside the promotion domain
   (where the fan-out is what lists that brand's promotions).
3. (crew B) A resolver kind the gate rejected for the domain (`incompatible_only`) is not a
   kind for reconciliation: it never re-types a hint, never arms a kind pick, never reaches
   the focus.

Every uuid here is synthetic; the codes are catalogue product codes (public repo). No real
contact ids.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.chatbot import turn_runtime
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business.gate import ALLOWED
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._avail_mode_console import AvailConsole, Stock, _seed
from tests.chatbot._r9_engine_console import reply
from tests.chatbot._turn_helpers import build_policy, entity, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture
from tests.chatbot.test_rearch_r6_review_round import (
    _grant_access_type,
    _mark_workspace_default,
    _seed_access_type,
)
from tests.chatbot.test_rearch_s3_attribute_first import SORENTO
from tests.chatbot.test_samantha_26sep_s9_brand_resolve import _seed_brand

pytestmark = pytest.mark.usefixtures("stub_access")

CODE = "SRTWC8383-RL-GY"
TICK = "✅"
#: A brand word that is NOT on the live brands table, whose only resolver hits are promotions.
UNLISTED_BRAND = "CABANA"


# --------------------------------------------------------------------------- #
# 1. Reconciliation: a fan-out hit confirms the hint, never re-types it
# --------------------------------------------------------------------------- #


def _apply(v: dict[str, Any], resolved: dict[str, dict[str, int]]):
    from app.services.chatbot.turn.apply import apply

    state = State(focus=Focus(), pending=None, profile=Profile())
    return apply(state, v, build_policy(), resolved=resolved)


def test_a_brand_word_whose_only_hits_are_promotions_stays_a_brand():
    v = verdict(domain_hint="promotion", intent_hint="check_promotion", entities=[entity("sorento", hint="brand")])

    state2, plan = _apply(v, {"sorento": {"promotion": 2}})

    assert plan.trace.reconciled == [], plan.trace.reconciled
    assert state2.focus.brands == ["sorento"], state2.focus.brands
    assert not state2.focus.extra.get("promotion"), state2.focus.extra


def test_a_category_word_whose_only_hits_are_promotions_stays_a_category():
    v = verdict(domain_hint="promotion", intent_hint="check_promotion", entities=[entity("wc", hint="category")])

    state2, plan = _apply(v, {"wc": {"promotion": 3}})

    assert plan.trace.reconciled == [], plan.trace.reconciled
    assert not state2.focus.extra.get("promotion"), state2.focus.extra
    assert [e.get("hint") for e in state2.focus.extra.get("category") or []] == ["category"], state2.focus.extra


def test_a_brand_word_under_inventory_hitting_promotions_stays_a_brand():
    """The prod photo shape at the apply seam: brand beside a product under inventory."""
    v = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[entity(CODE, hint="product"), entity("SORENTO", hint="brand")],
    )

    state2, plan = _apply(v, {CODE: {"product": 1}, "SORENTO": {"promotion": 4}})

    assert plan.trace.reconciled == [], plan.trace.reconciled
    assert state2.focus.brands == ["SORENTO"], state2.focus.brands
    assert not state2.focus.extra.get("promotion"), state2.focus.extra
    assert [p.get("raw") for p in state2.focus.products] == [CODE]


def test_a_genuine_re_type_still_happens():
    """The rule is narrow: a kind the hint does not fan out to still rewrites (AC-1527)."""
    v = verdict(domain_hint=None, entities=[entity("7445", hint="order")])

    state2, plan = _apply(v, {"7445": {"order": 0, "product": 1}})

    assert plan.trace.reconciled == [("7445", "order", "product")]
    assert any(p.get("raw") == "7445" for p in state2.focus.products)


# --------------------------------------------------------------------------- #
# crew B: a kind the gate rejected for the domain is not a kind for reconciliation
# --------------------------------------------------------------------------- #


def _resolve_kinds_with(monkeypatch, *, domain: str, entities: list[dict[str, Any]], resolutions: list[dict[str, Any]], compatible: list[dict[str, Any]]):
    """`resolve_kinds` over a stubbed `resolve_gate.run` whose gate is the production
    shape for `domain`: `compatible_entities` plus the `gate_debug` the real gate writes
    (`allowed_lookup`, and `incompatible_only` per token whose every match is outside it)."""
    allowed = ALLOWED[domain]
    incompatible_only = {
        r["token"]: sorted({m["entity_type"] for m in r["matches"]})
        for r in resolutions
        if r["matches"] and not any(m["entity_type"] in allowed for m in r["matches"])
    }
    gate_debug: dict[str, Any] = {"domain": domain, "allowed_lookup": allowed, "entities_count": len(compatible)}
    if incompatible_only:
        gate_debug["incompatible_only"] = incompatible_only

    def stub(ctx, entry, item, **kwargs):
        return {
            "resolved": {"tokens": [r["token"] for r in resolutions], "resolutions": resolutions, "unresolved_tokens": []},
            "gate": {"compatible_entities": compatible, "gate_debug": gate_debug},
            "aggregate": None,
            "tier_gate": None,
            "_exit_kind": "continue",
        }

    monkeypatch.setattr(resolve_gate, "run", stub)
    ctx = {"parse": {"output": {"domain_hint": domain, "intent_hint": "check_stock", "entities": entities}}}
    return turn_runtime.resolve_kinds(object(), ctx=ctx, branch_kind="check_stock", space_id=None, dry_run=True)


def _match(kind: str, code: str, uuid_: str) -> dict[str, Any]:
    return {"uuid": uuid_, "entity_type": kind, "canonical_code": code, "match_tier": "prefix"}


def test_crew_b_an_incompatible_only_kind_never_re_types_the_hint(monkeypatch):
    """The prod trace at the resolver seam: under inventory the token SORENTO (hint brand)
    matched promotions only, `incompatible_only {SORENTO: [promotion]}`. `resolved_kinds`
    carries no promotion for it, so APPLY keeps the brand and the focus holds no promotion."""
    promo = "22222222-2222-4222-8222-222222222222"
    product_id = "11111111-1111-4111-8111-111111111111"
    outcome = _resolve_kinds_with(
        monkeypatch,
        domain="inventory",
        entities=[entity(CODE, hint="product", canonical_code=None), entity("SORENTO", hint="brand", canonical_code=None)],
        resolutions=[
            {"token": CODE, "matches": [_match("product", CODE, product_id)]},
            {"token": "SORENTO", "matches": [_match("promotion", promo, promo), _match("promotion", promo.replace("2", "3"), promo.replace("2", "3"))]},
        ],
        compatible=[{"uuid": product_id, "entity_type": "product", "code": CODE}],
    )

    assert outcome.resolved_kinds.get(CODE) == {"product": 1}, outcome.resolved_kinds
    assert "promotion" not in (outcome.resolved_kinds.get("SORENTO") or {}), outcome.resolved_kinds

    v = verdict(domain_hint="inventory", intent_hint="check_stock", entities=[entity(CODE, hint="product"), entity("SORENTO", hint="brand")])
    state2, plan = _apply(v, outcome.resolved_kinds)
    assert plan.trace.reconciled == [], plan.trace.reconciled
    assert plan.ask is None or plan.ask.kind != "kind_pick", plan.ask
    assert state2.focus.brands == ["SORENTO"], state2.focus.brands
    assert not state2.focus.extra.get("promotion"), state2.focus.extra


def test_crew_b_an_incompatible_only_kind_never_arms_a_kind_pick_or_a_customer_on_an_inventory_focus(monkeypatch):
    """The generic shape, no fan-out pair involved: a product-hinted code under inventory
    whose only hits are customers. The gate already rejected `customer` for inventory, so
    the hint stays `product` (the token is then unplaced, said back by the miss path) and
    no customer row is written to the focus."""
    customer = "44444444-4444-4444-4444-444444444444"
    outcome = _resolve_kinds_with(
        monkeypatch,
        domain="inventory",
        entities=[entity("7445", hint="product", canonical_code=None)],
        resolutions=[{"token": "7445", "matches": [_match("customer", "7445 TRADING", customer)]}],
        compatible=[],
    )

    assert "customer" not in (outcome.resolved_kinds.get("7445") or {}), outcome.resolved_kinds

    v = verdict(domain_hint="inventory", intent_hint="check_stock", entities=[entity("7445", hint="product")])
    state2, plan = _apply(v, outcome.resolved_kinds)
    assert plan.trace.reconciled == [], plan.trace.reconciled
    assert plan.ask is None or plan.ask.kind != "kind_pick", plan.ask
    assert state2.focus.customers == [], state2.focus.customers
    assert [p.get("hint") for p in state2.focus.products] == ["product"], state2.focus.products


def test_crew_b_a_compatible_kind_still_re_types(monkeypatch):
    """Control: a code hinted `order` under inventory that the resolver places as a product
    (a kind inventory allows) is still rewritten, exactly as AC-1527 says."""
    product_id = "11111111-1111-4111-8111-111111111111"
    outcome = _resolve_kinds_with(
        monkeypatch,
        domain="inventory",
        entities=[entity("7445", hint="order", canonical_code=None)],
        resolutions=[{"token": "7445", "matches": [_match("product", "SRTWT7445", product_id)]}],
        compatible=[{"uuid": product_id, "entity_type": "product", "code": "SRTWT7445"}],
    )

    assert outcome.resolved_kinds.get("7445") == {"product": 1}, outcome.resolved_kinds
    _state2, plan = _apply(verdict(domain_hint="inventory", entities=[entity("7445", hint="order")]), outcome.resolved_kinds)
    assert plan.trace.reconciled == [("7445", "order", "product")]


# --------------------------------------------------------------------------- #
# 2. Engine replays through the dealer console, resolver REAL over seeded rows
# --------------------------------------------------------------------------- #


def _seed_promotion(session_factory, description: str, product_ids: list[str]) -> str:
    """A promotion whose description carries the brand and the class word, so the real
    resolver's promotion probe (`_prefix_probe_promotion`, substring on description)
    answers "sorento" and "wc" with it - the dev shape."""
    from app.models.marketing import Promotion, PromotionGroup, PromotionProduct

    db = session_factory()
    db.info["company_scope"] = frozenset({SORENTO})
    # The entitled tier's `contact_access_types.code`, which is what the column stores
    # (`references._apply_promotion_access_levels_filter` translates the recomposed name
    # "Sorento Dealer" to it); a promotion carrying any other code is invisible to this dealer.
    promo = Promotion(id=str(uuid.uuid4()), description=description, access_levels=["sorento_dealer"])
    db.add(promo)
    db.flush()
    group = PromotionGroup(id=str(uuid.uuid4()), promotion_id=promo.id, group_name="ZZT group")
    db.add(group)
    db.flush()
    for pid in product_ids:
        db.add(PromotionProduct(id=str(uuid.uuid4()), promotion_id=promo.id, promotion_group_id=group.id, product_id=pid))
    db.commit()
    return promo.id


def _brand(raw: str) -> dict[str, Any]:
    return entity(raw, hint="brand", canonical_code=None)


def _category(raw: str) -> dict[str, Any]:
    return entity(raw, hint="category", canonical_code=None)


def _focus_of(console: AvailConsole) -> dict[str, Any]:
    state = console.state if isinstance(console.state, dict) else {}
    focus = state.get("focus")
    return focus if isinstance(focus, dict) else {}


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    """The dealer console with the photo's product seeded, a Sorento WC promotion over it,
    and every resolver body the lane built recorded (`resolver_bodies`).

    The promotion domain enters the resolver at `access_check`, which reads the contact's
    REAL entitlement (`ContactAccessTypeService`) against the default workspace, so the
    workspace is marked default and the contact is granted exactly one tier - a single
    entitled tier never raises the tier ask, and the turn runs the resolver and the fetch."""
    bodies: list[dict[str, Any]] = []
    real_body = resolve_gate.resolve_entity_body

    def recording_body(ctx, **kwargs):
        body = real_body(ctx, **kwargs)
        bodies.append(body)
        return body

    monkeypatch.setattr(resolve_gate, "resolve_entity_body", recording_body)

    payloads: list[dict[str, Any] | None] = []
    real_resolve_kinds = turn_runtime.resolve_kinds

    def recording_resolve_kinds(*args, **kwargs):
        outcome = real_resolve_kinds(*args, **kwargs)
        payloads.append(outcome.payload)
        return outcome

    monkeypatch.setattr(turn_runtime, "resolve_kinds", recording_resolve_kinds)

    def make(**stock_facts: Stock) -> tuple[AvailConsole, str, str]:
        c = AvailConsole(session_factory, monkeypatch, stub_access, phone="+60100000913", **stock_facts)
        _mark_workspace_default(session_factory)
        _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")
        _grant_access_type(session_factory, code="sorento_dealer")
        seeded = _seed(session_factory, [CODE])
        c.codes.update(seeded)
        c.uuid_of.update({code: pid for pid, code in seeded.items()})
        product_id = c.uuid_of[CODE]
        # The live brand, as prod has it (the photo product's own `brand_code` is SORENTO).
        c.brand_id = _seed_brand(session_factory, name="Sorento", code="SORENTO", company_id=SORENTO)  # type: ignore[attr-defined]
        promo_id = _seed_promotion(session_factory, "ZZT Sorento WC promo", [product_id])
        # A second promotion naming a word that is NOT a live brand (crew B's engine shape).
        _seed_promotion(session_factory, f"ZZT {UNLISTED_BRAND.title()} WC promo", [product_id])
        c.resolver_bodies = bodies  # type: ignore[attr-defined]
        c.resolver_payloads = payloads  # type: ignore[attr-defined]
        return c, product_id, promo_id

    return make


def _tokens_sent(bodies: list[dict[str, Any]]) -> list[str]:
    return [str(t) for body in bodies for t in (body.get("tokens") or [])]


def _gate_uuids(payloads: list[dict[str, Any] | None], kind: str) -> set[str]:
    """Every uuid of `kind` the resolver's gate passed as compatible, across the turn."""
    return {
        str(e.get("uuid"))
        for payload in payloads
        if isinstance(payload, dict)
        for e in ((payload.get("gate") or {}).get("compatible_entities") or [])
        if isinstance(e, dict) and e.get("entity_type") == kind
    }


def _promotion_verdict(*entities: dict[str, Any]) -> dict[str, Any]:
    """"sorento wc promotion?" as the parser reads it: a brand and a class word under
    promotion, OR-matched (the two words describe one promotion each, not an intersection)."""
    return verdict(
        domain_hint="promotion",
        intent_hint="check_promotion",
        entity_op="replace_combine",
        match_mode="or",
        entities=list(entities),
    )


def _promotion_hinted_tokens(bodies: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for body in bodies:
        for token, hint in zip(body.get("tokens") or [], body.get("allowed_entity_types") or []):
            if str(hint or "").lower() == "promotion":
                out.append(str(token))
    return out


def test_dev_shape_promotion_ask_then_stock_check_carries_no_promotion_row(console):
    """Turn A "sorento wc promotion?" (parser: brand + category under promotion), the real
    resolver answers both words with the seeded promotion. Turn B "check stock" (reuse).
    After A the focus holds the brand as a brand and no promotion row; B sends no
    promotion-hinted token to the resolver and no `promotion_ids` to the stock tool."""
    c, product_id, promo_id = console(**{CODE.replace("-", "_"): Stock(on_hand=100)})

    c.say("sorento wc promotion?", _promotion_verdict(_brand("sorento"), _category("wc")))

    # AC-4 / crew A: under PROMOTION the brand word still reaches the shared resolver (its
    # fan-out is what lists that brand's promotions), and the gate of turn A still hands
    # the fetch that brand's promotion as a compatible entity. (The fetch arm itself is the
    # described-set path on this verdict - "wc with a promotion" - which this blank schema
    # qualifies nothing for; the brief's AC-4 is about the resolver seam, pinned here.)
    assert "sorento" in _tokens_sent(c.resolver_bodies), c.resolver_bodies
    assert promo_id in _gate_uuids(c.resolver_payloads, "promotion"), c.resolver_payloads
    focus = _focus_of(c)
    assert not (focus.get("extra") or {}).get("promotion"), focus.get("extra")
    assert focus.get("brands") == ["sorento"], focus

    mark = len(c.resolver_bodies)
    c.say("check stock", reply(entity_op="reuse"))

    assert _promotion_hinted_tokens(c.resolver_bodies[mark:]) == [], c.resolver_bodies[mark:]
    for args in c.stock_calls:
        assert "promotion_ids" not in args, args
    focus = _focus_of(c)
    assert not (focus.get("extra") or {}).get("promotion"), focus.get("extra")


def test_dev_shape_promotion_ask_then_a_typed_code_stock_ask(console):
    """Turn B names its own code (`replace_combine`): the stock tool is called with that
    product alone, no promotion row rides along, and the focus still holds no promotion."""
    c, product_id, _promo_id = console(**{CODE.replace("-", "_"): Stock(on_hand=100)})

    c.say("sorento wc promotion?", _promotion_verdict(_brand("sorento"), _category("wc")))
    mark = len(c.resolver_bodies)

    c.say(f"stock for {CODE}", reply(entity_op="replace_combine", entities=[entity(CODE, hint="product", canonical_code=None)]))

    assert _promotion_hinted_tokens(c.resolver_bodies[mark:]) == [], c.resolver_bodies[mark:]
    assert c.stock_calls and c.stock_calls[-1]["product_ids"] == [product_id], c.stock_calls
    assert "promotion_ids" not in c.stock_calls[-1], c.stock_calls[-1]
    assert not (_focus_of(c).get("extra") or {}).get("promotion"), _focus_of(c).get("extra")


def _photo_verdict(brand_word: str) -> dict[str, Any]:
    """Turn 120's own verdict: the product and a brand word under inventory."""
    return verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entity_op="replace_combine",
        entities=[entity(CODE, hint="product", canonical_code=None), _brand(brand_word)],
    )


def _fill_verdict() -> dict[str, Any]:
    """Turn 121: "1" answering the quantity question (`entity_op reuse`, no entities)."""
    return reply(
        entity_op="reuse",
        open_question_answer={
            "mode": "fill",
            "picked": [1],
            "items": [{"position": 1, "code": CODE, "qty": 1}],
            "qty_for_all": None,
        },
    )


@pytest.mark.parametrize(
    "brand_word",
    [
        pytest.param("SORENTO", id="live-brand"),
        pytest.param(UNLISTED_BRAND, id="unlisted-brand-word"),
    ],
)
def test_prod_photo_shape_brand_beside_product_under_inventory_then_fill_turn(console, brand_word):
    """Turn 120's own verdict: the product and a brand word under inventory, a promotion
    whose description carries that word seeded so the real resolver's fan-out answers it
    with promotions only (the prod trace: `incompatible_only {SORENTO: [promotion]}`).
    After the turn the focus holds the brand as a brand, no promotion row. The fill turn
    "1" (entity_op reuse) answers the availability line with the stock tool called for the
    product alone and nothing promotion-hinted sent to the resolver.

    Two words: the live brand (prod's own SORENTO, crew A: resolved against the brands
    table first, so the shared resolver is never asked about it) and a word that is on no
    brands table (crew B alone keeps it off the focus as a promotion)."""
    c, product_id, _promo_id = console(**{CODE.replace("-", "_"): Stock(on_hand=100)})

    first = c.say(f"how many units of {CODE} {brand_word}", _photo_verdict(brand_word))

    assert f"How many units of {CODE}?" in first.split("\n"), first
    focus = _focus_of(c)
    assert not (focus.get("extra") or {}).get("promotion"), focus.get("extra")
    assert focus.get("brands") == [brand_word], focus
    assert [p.get("uuid") for p in focus.get("products") or []] == [product_id], focus.get("products")
    assert all(t.kind != "kind_pick" for t in [c.turn_traces[-1]] if t is not None and hasattr(t, "kind")), c.turn_traces[-1]

    mark = len(c.resolver_bodies)
    second = c.say("1", _fill_verdict())

    assert second.split("\n")[0] == f"{CODE} x 1: {TICK} Got stock.", second
    assert _promotion_hinted_tokens(c.resolver_bodies[mark:]) == [], c.resolver_bodies[mark:]
    assert c.stock_calls[-1]["product_ids"] == [product_id], c.stock_calls[-1]
    assert "promotion_ids" not in c.stock_calls[-1], c.stock_calls[-1]


def test_crew_a_a_live_brand_under_inventory_resolves_against_the_brands_table_first(console):
    """crew A: the photo turn's SORENTO is a live brand (`brands.brand_code`). It is matched
    there, so the shared resolver (which has no brand probe and would fan the word out to
    promotions) is never asked about it: no resolver body of the turn carries the token.
    The product's own lookup is unaffected."""
    c, product_id, _promo_id = console(**{CODE.replace("-", "_"): Stock(on_hand=100)})

    first = c.say(f"how many units of {CODE} SORENTO", _photo_verdict("SORENTO"))

    assert f"How many units of {CODE}?" in first.split("\n"), first
    sent = _tokens_sent(c.resolver_bodies)
    assert "SORENTO" not in {t.upper() for t in sent}, c.resolver_bodies
    assert CODE in sent, c.resolver_bodies
    assert _focus_of(c).get("brands") == ["SORENTO"], _focus_of(c)


def test_crew_a_an_unlisted_brand_word_under_inventory_still_reaches_the_resolver(console):
    """The guard of crew A's rule (AC-S9-3): a brand word on no brands table is not this
    contact's brand to answer for, so it still goes to the resolver as today."""
    c, _product_id, _promo_id = console(**{CODE.replace("-", "_"): Stock(on_hand=100)})

    c.say(f"how many units of {CODE} {UNLISTED_BRAND}", _photo_verdict(UNLISTED_BRAND))

    assert UNLISTED_BRAND in _tokens_sent(c.resolver_bodies), c.resolver_bodies
