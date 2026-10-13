"""SPEC-PROMO-PARAMS slice 2, C: the business lane maps grounded specs to `spec_constraints`.

Card `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 9, slice 2 and 3.

Contract assumed here (the coordinator's brief, not yet in code):
  * `fetch.spec_constraints_for(parse_output, db) -> list[dict] | None`, pure: the described
    subject (category / product type, resolved through registry data) first, then every
    grounded specification entity with a value, in entity order; None when a typed product
    code is present or nothing maps.
  * `semantic_input["spec_constraints"]` makes `entity_ids_transformer` emit `spec_constraints`
    (a list) and drop `product_ids`, for the three tools below.
  * `fetch.select_tool(domain)` keeps `crm_marketing_promotions_list` (owner hand test 13 Oct)
    for the promotion domain when spec_constraints is non-empty, else the domain's first tool.

`spec_constraints_for` does not exist yet, so it is imported inside each test body: collection
works and each test fails with ImportError on that name (C1 to C3, C6a) or on a plain
assertion about the transformer output (C4, C5) or an unexpected `spec_constraints` keyword (C6).
"""
from __future__ import annotations

import pytest

from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_lane_require import _seed_registry

TRIGGER_UUID = "6136ea6b-1699-46ec-8e8e-f60c8bb64310"
CONSTRAINTS = [
    {"key": "product_type", "op": "eq", "value": "floor_grating"},
    {"key": "size", "op": "eq", "value": 250},
]


@pytest.fixture()
def vdb(session_factory):
    db = session_factory()
    _seed_registry(db)
    db.commit()
    yield db
    db.close()


def _fetch():
    from app.services.chatbot.lanes.business import fetch

    return fetch


def _category(raw):
    return {
        "raw": raw, "hint": "category", "canonical_code": None,
        "current_message": True, "confident": True, "hint_confident": True,
    }


def _spec(key, value, raw):
    return {
        "raw": raw, "hint": "specification", "canonical_code": None if value is None else str(value),
        "current_message": True, "confident": True, "hint_confident": True,
        "spec_key": key, "spec_value": value, "spec_unit": "mm" if key == "size" else None,
        "spec_label": key, "spec_words": None, "spec_known": [],
    }


def _ask(*entities, domain="inventory", intent="check_stock"):
    return verdict(entities=list(entities), domain_hint=domain, intent_hint=intent)


# ------------------------------------------------------------------ spec_constraints_for


def test_c1_described_ask_maps_subject_then_specs(vdb):
    """RED: `spec_constraints_for` does not exist (ImportError)."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    out = spec_constraints_for(_ask(_category("floor grating"), _spec("size", 250, "250mm")), vdb)
    assert out == CONSTRAINTS


def test_c1b_subject_comes_first_whatever_the_entity_order(vdb):
    """RED: same missing function. The spec entity is listed before the category."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    out = spec_constraints_for(_ask(_spec("size", 250, "250mm"), _category("floor grating")), vdb)
    assert out == CONSTRAINTS


def test_c2_typed_product_code_maps_nothing(vdb):
    """RED: same missing function."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    code = {
        "raw": "SRT62-GM", "hint": "product", "canonical_code": "SRT62-GM",
        "current_message": True, "confident": True, "hint_confident": True,
    }
    assert spec_constraints_for(_ask(code, _spec("size", 250, "250mm")), vdb) is None


def test_c3_a_specification_without_a_value_is_not_mapped(vdb):
    """RED: same missing function. The unknown-value clarify keeps that entity."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    out = spec_constraints_for(
        _ask(_category("floor grating"), _spec("finish", None, "blak"), _spec("size", 250, "250mm")), vdb
    )
    assert out is not None
    assert [c["key"] for c in out] == ["product_type", "size"]
    assert not [c for c in out if c["key"] == "finish"]


def _shared_reader_constraint(db, raw):
    """What the SHARED readers say about a category raw, with none of the lane's own rules:
    the grounding vocabulary's `product_type` phrases (`ground_words`), else the class
    vocabulary's `_exact_classes` (which reads a plural in the singular itself)."""
    from app.services.chatbot.head.grounding import ground_words, load_vocabulary
    from app.services.product_spec_search import _exact_classes

    found, _used = ground_words(raw, load_vocabulary(db))
    types = [g for g in found if g.key == "product_type" and g.value is not None]
    if len(types) == 1:
        return {"key": "product_type", "op": "eq", "value": types[0].value}
    classes = _exact_classes(db, raw) or []
    if classes:
        return {"key": "class", "op": "eq", "value": classes[0] if len(classes) == 1 else list(classes)}
    return None


@pytest.mark.parametrize(
    "raw",
    [
        "Zzt Gizmo",
        "zzt gizmos",
        "ZZT GIZMO",
        "basins",
        "no such thing",
        "jacuzzi",
        "jacuzzis",
    ],
)
def test_c7_the_lane_subject_equals_what_the_shared_class_readers_say(vdb, raw):
    """Captain ruling (plural reading stays in the shared `_exact_classes`; the headline ask is
    "which basins are on promotion"): the LANE adds no word rules of its own. Whatever the shared
    readers produce for a raw is exactly the subject constraint, no more and no less.

    Seeded: a made-up class "Zzt Gizmo" and a Wash Basin class with synonym "basin" (the plural
    "basins" is read by the shared reader, so it must keep mapping). RED for "jacuzzi" and
    "jacuzzis": no category carries that class, the shared readers map nothing, but the lane's own
    `CLASS_SYNONYMS` fallback (and its own `said[:-1]` strip) turns them into class "Jacuzzi"."""
    import uuid

    from app.models.product import ProductCategory
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    for label, synonyms in (("Zzt Gizmo", []), ("Wash Basin", ["basin"])):
        vdb.add(
            ProductCategory(
                id=str(uuid.uuid4()), category_code=f"ZZT{uuid.uuid4().hex[:6].upper()}-X",
                category_name=f"ZZT {label} category", class_label=label, is_searchable=True,
                search_synonyms=list(synonyms),
            )
        )
    vdb.commit()

    expected = _shared_reader_constraint(vdb, raw)
    got = spec_constraints_for(_ask(_category(raw)), vdb)
    assert got == ([expected] if expected else None), (raw, expected, got)


def test_c7b_the_headline_plural_still_maps_through_the_shared_reader(vdb):
    """Guard: "basins" with a seeded Wash Basin class (synonym "basin") keeps mapping."""
    import uuid

    from app.models.product import ProductCategory
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    vdb.add(
        ProductCategory(
            id=str(uuid.uuid4()), category_code=f"ZZT{uuid.uuid4().hex[:6].upper()}-WB",
            category_name="ZZT basin category", class_label="Wash Basin", is_searchable=True,
            search_synonyms=["basin"],
        )
    )
    vdb.commit()
    assert spec_constraints_for(_ask(_category("basins")), vdb) == [
        {"key": "class", "op": "eq", "value": "Wash Basin"}
    ]


# ------------------------------------------------------------------ entity_ids_transformer


def _trigger(tool):
    return {
        "entities": [{"uuid": TRIGGER_UUID, "entity_type": "product", "code": "A"}],
        "tool": tool,
        "semantic_input": {"contact_id": "1", "space_id": "s", "spec_constraints": CONSTRAINTS},
    }


@pytest.mark.parametrize(
    "tool",
    [
        "crm_inventory_stock_balance_list",
        "crm_master_products_list",
        "crm_marketing_promotions_list",
    ],
)
def test_c4_c5_transformer_carries_spec_constraints_and_drops_product_ids(tool):
    """RED: `semantic_input["spec_constraints"]` is never read, so the args have no
    `spec_constraints` and still carry the resolver's `product_ids`."""
    out = _fetch().entity_ids_transformer(_trigger(tool))
    assert out.get("spec_constraints") == CONSTRAINTS, out
    assert isinstance(out["spec_constraints"], list)
    assert "product_ids" not in out, out


def test_c4b_without_spec_constraints_product_ids_are_untouched():
    """Guard (passes today): the typed-code path keeps its product_ids."""
    trig = _trigger("crm_inventory_stock_balance_list")
    trig["semantic_input"].pop("spec_constraints")
    out = _fetch().entity_ids_transformer(trig)
    assert out["product_ids"] == [TRIGGER_UUID]
    assert "spec_constraints" not in out


# ------------------------------------------------------------------ promotion tool choice


def test_c6_a_promotion_ask_keeps_the_promotions_list():
    """Owner hand test 13 Oct: a size in a promotion ask only resolves the products; the answer
    is the existing promotions list, so the tool choice never switches."""
    assert [t["name"] for t in _fetch().select_tool("promotion")] == ["crm_marketing_promotions_list"]


def test_c6_promotion_without_a_subject_keeps_the_promotions_list():
    """Guard (passes today): no subject, the domain's first tool."""
    assert [t["name"] for t in _fetch().select_tool("promotion")] == ["crm_marketing_promotions_list"]


def test_c6_both_phrasings_give_the_same_constraints(vdb):
    """RED: `spec_constraints_for` does not exist. 'which basins are on promotion' and
    'basin promotion' differ only in raw text and must map to one identical list."""
    import uuid

    from app.models.product import ProductCategory
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    # Wash Basin class with synonym "basin", as C7b seeds it: both phrasings map through the
    # shared class reader (the lane's built-in CLASS_SYNONYMS fallback is gone).
    vdb.add(
        ProductCategory(
            id=str(uuid.uuid4()), category_code=f"ZZT{uuid.uuid4().hex[:6].upper()}-WB",
            category_name="ZZT basin category", class_label="Wash Basin", is_searchable=True,
            search_synonyms=["basin"],
        )
    )
    vdb.commit()
    a = spec_constraints_for(_ask(_category("basins"), domain="promotion", intent="check_promotion"), vdb)
    b = spec_constraints_for(_ask(_category("basin"), domain="promotion", intent="check_promotion"), vdb)
    assert a and a == b


def test_c6_promotion_ask_with_no_subject_maps_nothing(vdb):
    """RED: same missing function. 'any promo?' has no described subject."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    assert spec_constraints_for(_ask(domain="promotion", intent="check_promotion"), vdb) is None
