"""STOCK-QTY-PARTIAL-FILL problem 2, the search half (UAC AC-15 to AC-17): once the parser
says trap_type + trap_length (+ class + brand), the catalogue narrows to exactly the
products holding every value, "available" keeps `require.stock`, a code beside trap words
narrows to that code's variants carrying them (Q5 (a)), and a spec said after a product
answer picks that product's sibling (Q4 (a)).

Postgres only (`tests/_pg_fixture.blank_session`), a seeded catalogue (CI's database is
empty): Sorento and Mocha water closets at S trap 250 / 300 mm and P trap 180 mm, stock on
some, a colour family (ZZTWC8066-SH-*) whose variants differ by trap length, and a
ZZTWC286-SH / ZZTWC286-SH-P pair. Every code starts with the `ZZT` test prefix.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models.inventory import Stock, Warehouse
from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure
from app.models.product_spec import ProductSpecifications
from app.services.product_class_signal import backfill_category_signals
from app.services.product_code_family import narrow_code_tokens, narrow_code_tokens_by_specs
from app.services.product_predicate_service import resolve_product_set
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from tests._pg_fixture import blank_session

ENDPOINT = "/api/v1/system/references/resolve"
_USER = {"id": str(uuid.uuid4()), "email": "n8n@example.com"}

#: The chatbot's own shape (`resolve_gate.resolve_entity_body`): a spec the parser said and
#: `head/grounding.py` checked against the registry travels `grounded: True`. A grounded
#: NUMBER defines membership like a named choice (`product_spec_search.filter_specs`); an
#: ungrounded one, bound by proximity in the deterministic reader, stays a ranking boost
#: (R27 / AC-1352, PR #833) - the contrast is pinned below.
S300 = [
    {"key": "trap_type", "value": "s_trap", "evidence": "s trap", "grounded": True},
    {"key": "trap_length", "value": 300, "evidence": "300mm", "grounded": True},
]
S250 = [
    {"key": "trap_type", "value": "s_trap", "evidence": "s trap", "grounded": True},
    {"key": "trap_length", "value": 250, "evidence": "250mm", "grounded": True},
]
P_TRAP = [{"key": "trap_type", "value": "p_trap", "evidence": "p trap", "grounded": True}]


class _World:
    def __init__(self, db) -> None:
        self.db = db
        self.wc = ProductCategory(id=str(uuid.uuid4()), category_code="SRT-WC", category_name="SRT-WC")
        self.uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-PCS", uom_name="Piece")
        self.sorento = Brand(id=str(uuid.uuid4()), brand_code="ZZT-SRT", brand_name="SORENTO")
        self.mocha = Brand(id=str(uuid.uuid4()), brand_code="ZZT-MCH", brand_name="MOCHA")
        db.add_all([self.wc, self.uom, self.sorento, self.mocha])
        db.flush()
        backfill_category_signals(db)
        seed_spec_registry(db)
        self.warehouse = Warehouse(
            id=str(uuid.uuid4()), warehouse_code="ZZT-WH", warehouse_name="ZZT WH", is_active=True
        )
        db.add(self.warehouse)
        db.flush()
        self.codes: dict[str, Product] = {}

    def product(self, code: str, description: str, *, brand=None, stock: int | None = None) -> Product:
        row = Product(
            id=str(uuid.uuid4()),
            product_code=code,
            product_name=code,
            description=description,
            category_id=self.wc.id,
            base_uom_id=self.uom.id,
            list_price=Decimal("1.00"),
            brand_id=(brand or self.sorento).id,
        )
        self.db.add(row)
        self.db.flush()
        derive_for_code(self.db, code)
        if stock is not None:
            self.db.add(
                Stock(
                    id=str(uuid.uuid4()),
                    product_id=row.id,
                    warehouse_id=self.warehouse.id,
                    quantity_on_hand=stock,
                    quantity_reserved=0,
                    quantity_damaged=0,
                )
            )
            self.db.flush()
        self.codes[code] = row
        return row

    def values(self, code: str) -> dict:
        row = self.db.query(ProductSpecifications).filter(ProductSpecifications.product_id == self.codes[code].id).one()
        return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (row.values or {}).items()}


@pytest.fixture
def world():
    with blank_session() as s:
        w = _World(s)
        # The described set: the owner's S trap 300mm ask.
        w.product("ZZTWC300S-A", "SORENTO WATER CLOSET S-TRAP 300MM (680X370X760MM)", stock=5)
        w.product("ZZTWC300S-B", "SORENTO WATER CLOSET S-TRAP 300MM (700X380X770MM)", stock=0)
        w.product("ZZTWC250S", "SORENTO WATER CLOSET S-TRAP 250MM (680X370X760MM)", stock=3)
        w.product("ZZTWC180P", "SORENTO WATER CLOSET P-TRAP 180MM (680X370X760MM)", stock=2)
        w.product("ZZTMC300S", "MOCHA WATER CLOSET S-TRAP 300MM (680X370X760MM)", brand=w.mocha, stock=4)
        yield w


@pytest.fixture
def family(world):
    """The 12:45 code family (Q5): colour variants, two trap lengths among them."""
    world.product("ZZTWC8066-SH-MG", "SORENTO WATER CLOSET S-TRAP 250MM MATT GREY", stock=2)
    world.product("ZZTWC8066-SH-MK", "SORENTO WATER CLOSET S-TRAP 250MM MATT BLACK", stock=2)
    world.product("ZZTWC8066-SH-MW", "SORENTO WATER CLOSET S-TRAP 300MM MATT WHITE", stock=2)
    world.product("ZZTWC8066-SH-UF", "SORENTO WATER CLOSET S-TRAP 300MM UF SEAT", stock=2)
    # The 14:50 pair (Q4): a product and its P trap sibling.
    world.product("ZZTWC286-SH", "SORENTO WATER CLOSET S-TRAP 250MM", stock=1)
    world.product("ZZTWC286-SH-P", "SORENTO WATER CLOSET P-TRAP 180MM", stock=1)
    return world


@pytest.fixture()
def client(world):
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key, get_external_api_user
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: world.db
    app.dependency_overrides[get_external_api_user] = lambda: _USER
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[apply_company_scope] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _codes(out: dict) -> list[str]:
    return sorted(c["product_code"] for c in out["candidates"])


# --------------------------------------------------------------------------- #
# The catalogue derives what the parser will ask for                            #
# --------------------------------------------------------------------------- #


def test_the_seed_derives_trap_type_and_trap_length(world):
    """Guard the guard: a description "S-TRAP 300MM" reaches the registry keys the
    parser emits, so the narrowing below is over real derived values."""
    assert world.values("ZZTWC300S-A")["trap_type"] == "s_trap"
    assert float(world.values("ZZTWC300S-A")["trap_length"]) == 300
    assert world.values("ZZTWC180P")["trap_type"] == "p_trap"
    assert float(world.values("ZZTWC250S")["trap_length"]) == 250


# --------------------------------------------------------------------------- #
# AC-15: trap_type + trap_length + class + brand + stock narrow exactly          #
# --------------------------------------------------------------------------- #


def test_s_trap_300_sorento_with_stock_is_exactly_the_in_stock_300(world):
    out = resolve_product_set(
        world.db, require={"stock": True}, specs=S300, scope_terms=["water closet"], brand="SORENTO"
    )
    assert _codes(out) == ["ZZTWC300S-A"], out
    assert out["qualifying_total"] == 1


def test_without_the_brand_the_other_brands_300_joins(world):
    out = resolve_product_set(world.db, require={"stock": True}, specs=S300, scope_terms=["water closet"])
    assert _codes(out) == ["ZZTMC300S", "ZZTWC300S-A"], out


def test_250_returns_the_250_only_never_the_300(world):
    out = resolve_product_set(
        world.db, require={"stock": True}, specs=S250, scope_terms=["water closet"], brand="SORENTO"
    )
    assert _codes(out) == ["ZZTWC250S"], out


def test_without_the_stock_requirement_the_out_of_stock_300_is_counted(world):
    """"available" is what keeps `require.stock`; a plain "which WC is S trap 300mm" lists
    the out-of-stock one too."""
    out = resolve_product_set(
        world.db, require={}, specs=S300, scope_terms=["water closet"], brand="SORENTO"
    )
    assert _codes(out) == ["ZZTWC300S-A", "ZZTWC300S-B"], out


def test_an_ungrounded_trap_length_is_still_only_a_boost(world):
    """R27 / AC-1352 kept: a number the deterministic reader bound by proximity (no
    `grounded`) ranks the 300 first but does not exclude the 250. The parser's grounded
    number is what makes "300mm" a filter."""
    ungrounded = [{k: v for k, v in s.items() if k != "grounded"} for s in S300]
    out = resolve_product_set(
        world.db, require={"stock": True}, specs=ungrounded, scope_terms=["water closet"], brand="SORENTO"
    )
    assert _codes(out) == ["ZZTWC250S", "ZZTWC300S-A"], out
    assert [c["product_code"] for c in out["candidates"]][0] == "ZZTWC300S-A"


def test_p_trap_alone_is_the_p_trap_water_closet(world):
    out = resolve_product_set(world.db, require={"stock": True}, specs=P_TRAP, scope_terms=["water closet"])
    assert _codes(out) == ["ZZTWC180P"], out


def test_the_resolve_route_keeps_the_stock_requirement_through_to_the_answer(client, world):
    """The chatbot's own request shape (`resolve_gate.resolve_entity_body`): the grounded
    specs as `extracted_specs`, the category as `scope_terms`, `require.stock` for
    "available". The predicate block counts the in-stock Sorento 300 alone."""
    body = {
        "query": "Any WC with S trap 300mm available",
        "tokens": ["WC"],
        "allowed_entity_types": ["category"],
        "match_mode": "and",
        "spec_fallback": True,
        "exact_match": True,
        "extracted_specs": S300,
        "scope_terms": ["water closet"],
        "require": {"stock": True},
        "predicate_words": ["available"],
        "set_list_max": 15,
        "dry_run": True,
    }
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 200, response.text
    predicate = response.json()["predicate"]
    assert predicate["require"] == {"stock": True}
    assert predicate["qualifying_total"] == 2, predicate  # Sorento A + Mocha, no brand named
    assert not predicate.get("unrecognized_terms"), predicate


# --------------------------------------------------------------------------- #
# AC-16 (Q5 (a)): a code beside trap words narrows to the variants carrying them  #
# --------------------------------------------------------------------------- #


def test_a_code_with_s_trap_250_keeps_the_250_variants_only(family):
    out = narrow_code_tokens(
        family.db,
        query="ZZTWC8066-SH s trap 250mm 50pc",
        tokens=["ZZTWC8066-SH"],
        allowed_types=["product"],
    )
    assert out == ["ZZTWC8066-SH-MG", "ZZTWC8066-SH-MK"]


def test_a_code_with_a_trap_word_every_variant_carries_is_left_alone(family):
    """Every variant is an S trap: the words pick nothing, the family stands (the colour
    pick is then the right question, as on 6 Oct 12:45)."""
    assert (
        narrow_code_tokens(family.db, query="ZZTWC8066-SH s trap 50pc", tokens=["ZZTWC8066-SH"], allowed_types=["product"])
        is None
    )


def test_the_grounded_values_narrow_the_same_family(family):
    """The same narrowing off the registry VALUES the parser emitted, no word read."""
    out = narrow_code_tokens_by_specs(
        family.db, tokens=["ZZTWC8066-SH"], allowed_types=["product"], specs=S250
    )
    assert out == ["ZZTWC8066-SH-MG", "ZZTWC8066-SH-MK"]


# --------------------------------------------------------------------------- #
# AC-17 (Q4 (a)): a spec after a product answer picks that product's sibling      #
# --------------------------------------------------------------------------- #


def test_the_message_words_cannot_narrow_a_carried_product(family):
    """The measurement the card cites: "How about p trap" carries no code word, so the
    word-based narrowing finds nothing to anchor on."""
    assert (
        narrow_code_tokens(family.db, query="How about p trap", tokens=["ZZTWC286-SH"], allowed_types=["product"])
        is None
    )


def test_the_grounded_p_trap_picks_the_sibling(family):
    out = narrow_code_tokens_by_specs(
        family.db, tokens=["ZZTWC286-SH"], allowed_types=["product"], specs=P_TRAP
    )
    assert out == ["ZZTWC286-SH-P"]


def test_a_spec_the_product_itself_carries_keeps_the_product(family):
    """"how about s trap" over ZZTWC286-SH (an S trap): the product is its own answer."""
    out = narrow_code_tokens_by_specs(
        family.db, tokens=["ZZTWC286-SH"], allowed_types=["product"], specs=[{"key": "trap_type", "value": "s_trap"}]
    )
    assert out == ["ZZTWC286-SH"]


def test_a_spec_no_variant_carries_leaves_the_code_as_it_is(family):
    """No silent widening to the class: the product is answered as asked, exactly as a
    word no variant carries is ignored (`narrow_code_tokens` rule 3)."""
    out = narrow_code_tokens_by_specs(
        family.db,
        tokens=["ZZTWC286-SH"],
        allowed_types=["product"],
        specs=[{"key": "trap_type", "value": "p_trap"}, {"key": "trap_length", "value": 300}],
    )
    assert out is None


def test_a_non_product_token_and_a_brand_spec_are_never_narrowed(family):
    assert narrow_code_tokens_by_specs(family.db, tokens=["HANLIM"], allowed_types=["customer"], specs=P_TRAP) is None
    assert (
        narrow_code_tokens_by_specs(
            family.db, tokens=["ZZTWC286-SH"], allowed_types=["product"], specs=[{"key": "brand", "value": "SORENTO"}]
        )
        is None
    )


def test_the_resolve_route_answers_the_sibling_for_a_carried_product(client, family):
    """The 14:50 turn after Q4 (a): the parser carried SRTWC286-SH (entity_op reuse) and
    the grounded trap_type p_trap; the resolver answers ZZTWC286-SH-P, never the 182
    class-wide P trap water closets and never the S trap product itself."""
    body = {
        "query": "How about p trap",
        "tokens": ["ZZTWC286-SH"],
        "allowed_entity_types": ["product"],
        "match_mode": "and",
        "spec_fallback": True,
        "exact_match": True,
        "extracted_specs": [{"key": "trap_type", "value": "p_trap", "evidence": "p trap", "grounded": True}],
        "dry_run": True,
    }
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    matches = payload.get("intersection") or [m for r in payload.get("resolutions") or [] for m in r.get("matches") or []]
    codes = sorted({m.get("canonical_code") or m.get("code") for m in matches if m.get("entity_type") == "product"})
    assert codes == ["ZZTWC286-SH-P"], payload
    assert "predicate" not in payload, "a code answered the ask; no class-wide set was counted"
