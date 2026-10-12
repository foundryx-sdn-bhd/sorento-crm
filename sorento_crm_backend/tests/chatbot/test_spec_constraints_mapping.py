"""SPEC-PROMO-PARAMS slice 2, C: the business lane maps grounded specs to `spec_constraints`.

Card `documentation/plans/chatbot/CARD-spec-promo-params-12oct.md` section 9, slice 2 and 3.

Contract assumed here (the coordinator's brief, not yet in code):
  * `fetch.spec_constraints_for(parse_output, db) -> list[dict] | None`, pure: the described
    subject (category / product type, resolved through registry data) first, then every
    grounded specification entity with a value, in entity order; None when a typed product
    code is present or nothing maps.
  * `semantic_input["spec_constraints"]` makes `entity_ids_transformer` emit `spec_constraints`
    (a list) and drop `product_ids`, for the three tools below.
  * `fetch.select_tool(domain, spec_constraints=None)` picks `crm_marketing_promotion_products_list`
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


def test_c7_the_subject_is_decided_by_category_data_with_no_code_side_word_rules(vdb):
    """Reviewer round. `_subject_constraint` / `_exact_classes` today read a plural in the singular
    in code (`phrase[-1][:-1]`, `said[:-1]`). Contract: category and registry data decide, code adds
    no word rules. A seeded class label maps; the same word with a trailing "s" that is neither a
    label nor a synonym maps to nothing (the ask stays on the resolver path).

    A made-up class ("Zzt Gizmo") is used rather than "basin", because the built-in class
    vocabulary (`CLASS_SYNONYMS`) already lists "basin" and would answer for a plural too.
    RED: the plural maps to the class, via the code-side "s" strip."""
    import uuid

    from app.models.product import ProductCategory
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    vdb.add(
        ProductCategory(
            id=str(uuid.uuid4()), category_code=f"ZZT{uuid.uuid4().hex[:6].upper()}-GZ",
            category_name="ZZT gizmo category", class_label="Zzt Gizmo", is_searchable=True, search_synonyms=[],
        )
    )
    vdb.commit()

    single = spec_constraints_for(_ask(_category("zzt gizmo")), vdb)
    assert single == [{"key": "class", "op": "eq", "value": "Zzt Gizmo"}], single

    assert spec_constraints_for(_ask(_category("zzt gizmos")), vdb) is None


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
        "crm_marketing_promotion_products_list",
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


def test_c6_empty_constraints_keep_the_promotions_list():
    """RED: unexpected keyword `spec_constraints`. An empty or None list is "no subject"."""
    for none in (None, []):
        picked = _fetch().select_tool("promotion", spec_constraints=none)
        assert [t["name"] for t in picked] == ["crm_marketing_promotions_list"]


def test_c6_promotion_with_a_subject_picks_promotion_products_tool():
    """RED: `select_tool` takes only the domain (unexpected keyword `spec_constraints`)."""
    picked = _fetch().select_tool("promotion", spec_constraints=[{"key": "class", "op": "eq", "value": "Basin"}])
    assert [t["name"] for t in picked] == ["crm_marketing_promotion_products_list"]


def test_c6_promotion_without_a_subject_keeps_the_promotions_list():
    """Guard (passes today): no subject, the domain's first tool."""
    assert [t["name"] for t in _fetch().select_tool("promotion")] == ["crm_marketing_promotions_list"]


def test_c6_both_phrasings_give_the_same_constraints(vdb):
    """RED: `spec_constraints_for` does not exist. 'which basins are on promotion' and
    'basin promotion' differ only in raw text and must map to one identical list."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    a = spec_constraints_for(_ask(_category("basins"), domain="promotion", intent="check_promotion"), vdb)
    b = spec_constraints_for(_ask(_category("basin"), domain="promotion", intent="check_promotion"), vdb)
    assert a and a == b


def test_c6_promotion_ask_with_no_subject_maps_nothing(vdb):
    """RED: same missing function. 'any promo?' has no described subject."""
    from app.services.chatbot.lanes.business.fetch import spec_constraints_for

    assert spec_constraints_for(_ask(domain="promotion", intent="check_promotion"), vdb) is None
