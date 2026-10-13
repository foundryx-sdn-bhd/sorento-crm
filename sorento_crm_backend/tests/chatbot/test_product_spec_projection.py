"""A1 - product spec projection in `output_structurer` (AC-901, AC-902).

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`documentation/plans/chatbot/chatbot-growth-r1-acceptance-criteria.md` section A.

No spec TOOL exists in the catalog - this is `crm_master_products_list`'s own
envelope (`result_type: "products"`), gated only on that, never on
`field_vocabulary`/`spec_vocabulary` truthiness (that would break the
zero-spec case: a product with no derived specs must still answer with
today's four fields, not be skipped by the gate).
"""
from __future__ import annotations

from app.services.chatbot.lanes.business import fetch


def _product_envelope(*, specs: list[dict] | None = None) -> dict:
    fields = [
        {"key": "company_name", "label": "Company", "value": "Sorento"},
        {"label": "Product Code", "value": "SRTWC8517"},
        {"label": "Product Name", "value": "Wall Hung Closet"},
        {"label": "List Price", "value": "MYR 199.00"},
        {"label": "Dimensions", "value": "500 x 360 x 350 mm"},
    ]
    for spec in specs or []:
        fields.append(
            {"key": f"spec:{spec['key']}", "label": spec["label"], "value": spec["value"]}
        )
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": [{"title": "SRTWC8517", "fields": fields}],
        "spec_vocabulary": {s["key"]: s["label"] for s in (specs or [])},
        "has_result": True,
    }


_FOUR_SPECS = [
    {"key": "wattage", "label": "Wattage", "value": "60 W"},
    {"key": "thickness", "label": "Thickness", "value": "1.2 mm"},
    {"key": "material", "label": "Material", "value": "Ceramic"},
    {"key": "color", "label": "Color", "value": "White"},
]


def test_no_attributes_asked_keeps_base_fields_plus_one_compact_specs_line():
    envelope = _product_envelope(specs=_FOUR_SPECS)
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    fields = out["answers"][0]["fields"]
    labels = [f["label"] for f in fields]
    # today's four fields, unchanged
    assert labels[:3] == ["Company", "Product Code", "Product Name"]
    # card v4 (AC-19): price and dimensions print only when asked
    assert "List Price" not in labels and "Dimensions" not in labels
    # exactly ONE additional line, not four separate spec fields
    specs_fields = [f for f in fields if f["label"] == "Specs"]
    assert len(specs_fields) == 1
    assert "Wattage: 60 W" in specs_fields[0]["value"]
    assert "Thickness: 1.2 mm" in specs_fields[0]["value"]
    assert "no spec:" not in out["response"]


def test_no_cap_all_ten_keys_render_and_never_say_more():
    """AC-1 (12 Sep 2026, finding 1): the Specs line lists EVERY populated spec
    key - `_SPEC_SUMMARY_CAP` and its "and N more" tail are gone. Flips the old
    cap-at-eight pin (this exact scenario used to assert "and 2 more")."""
    many = [
        {"key": f"k{i}", "label": f"Key{i}", "value": str(i)}
        for i in range(10)
    ]
    envelope = _product_envelope(specs=many)
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    specs_field = next(f for f in out["answers"][0]["fields"] if f["label"] == "Specs")
    assert "more" not in specs_field["value"]
    for i in range(10):
        assert f"Key{i}: {i}" in specs_field["value"]


def test_no_cap_twelve_keys_render_in_registry_order_every_pair_present():
    """AC-1: a 12-key case - registry order preserved, every `Label: value` pair
    present, comma-separated, byte-exact (proves order AND completeness, not
    just membership)."""
    many = [
        {"key": f"k{i}", "label": f"Key{i}", "value": str(i)}
        for i in range(12)
    ]
    envelope = _product_envelope(specs=many)
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    specs_field = next(f for f in out["answers"][0]["fields"] if f["label"] == "Specs")
    expected = ", ".join(f"Key{i}: {i}" for i in range(12))
    assert specs_field["value"] == expected


def test_no_spec_key_at_all_is_the_plain_four_field_answer():
    envelope = _product_envelope(specs=[])
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    fields = out["answers"][0]["fields"]
    assert not any(f["label"] == "Specs" for f in fields)
    assert [f["label"] for f in fields] == ["Company", "Product Code", "Product Name"]


def test_single_key_ask_returns_base_fields_plus_that_key():
    """D12 (8 Sep 2026): base fields are on the page - an ask narrows only which SPEC key
    joins them. Card v4 (AC-19): List Price and Dimensions join only when asked."""
    envelope = _product_envelope(specs=_FOUR_SPECS)
    ctx = {"semantic_input": {"requested_attributes": ["wattage"]}}
    out = fetch.output_structurer(envelope, ctx)
    fields = out["answers"][0]["fields"]
    labels = [f["label"] for f in fields]
    assert labels == ["Company", "Product Code", "Product Name", "Wattage"]
    assert fields[-1]["value"] == "60 W"


def test_single_key_ask_matches_by_label_synonym_word():
    """AC-902: "wattage" matches the registry LABEL text, not only the raw key -
    here asking with different casing/spacing still resolves to the same spec."""
    envelope = _product_envelope(specs=_FOUR_SPECS)
    ctx = {"semantic_input": {"requested_attributes": ["Wattage"]}}
    out = fetch.output_structurer(envelope, ctx)
    fields = out["answers"][0]["fields"]
    assert any(f["label"] == "Wattage" for f in fields)


_BASE_LABELS = ["Company", "Product Code", "Product Name"]


def test_asked_key_the_product_lacks_answers_no_label_recorded():
    """Item 8 (8 Sep 2026): the miss is said ONCE, above the items, never per item. D12:
    base fields stay on the page even though the asked key missed."""
    envelope = _product_envelope(specs=_FOUR_SPECS)
    ctx = {"semantic_input": {"requested_attributes": ["voltage"]}}
    out = fetch.output_structurer(envelope, ctx)
    assert [f["label"] for f in out["answers"][0]["fields"]] == _BASE_LABELS
    assert "*voltage:* not recorded for SRTWC8517" in out["response"]
    assert out["response"].count("recorded for") == 1


def test_asked_key_the_product_lacks_uses_registry_label_when_known():
    """The product has none of `_FOUR_SPECS`' keys BUT another product in the same
    result set carries the registry entry, so `spec_vocabulary` knows the label."""
    envelope = _product_envelope(specs=[])
    envelope["spec_vocabulary"] = {"wattage": "Wattage"}
    ctx = {"semantic_input": {"requested_attributes": ["wattage"]}}
    out = fetch.output_structurer(envelope, ctx)
    assert [f["label"] for f in out["answers"][0]["fields"]] == _BASE_LABELS
    assert "*Wattage:* not recorded for SRTWC8517" in out["response"]


# ------------------------------------------------------------------------------------ #
# Item 8 (8 Sep 2026): base fields first, token containment, one miss line.
# ------------------------------------------------------------------------------------ #

def _item(code: str, *, specs: list[dict] | None = None, description: str | None = None) -> dict:
    fields = [
        {"key": "company_name", "label": "Company", "value": "Sorento"},
        {"label": "Product Code", "value": code},
        {"label": "Product Name", "value": f"Closet {code}"},
    ]
    if description:
        fields.append({"label": "Description", "value": description})
    fields += [
        {"label": "List Price", "value": "MYR 1,260.00"},
        {"label": "Dimensions", "value": "500 x 360 x 350 mm"},
    ]
    for spec in specs or []:
        fields.append({"key": f"spec:{spec['key']}", "label": spec["label"], "value": spec["value"]})
    return {"title": code, "fields": fields}


def _family_envelope(items: list[dict], vocab: dict[str, str]) -> dict:
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": items,
        "spec_vocabulary": vocab,
        "has_result": True,
    }


_MATERIAL_VOCAB = {"seat_material": "Seat cover material", "material": "Material"}
_SEAT = {"key": "seat_material", "label": "Seat cover material", "value": "pp"}
_MAT = {"key": "material", "label": "Material", "value": "Ceramic"}


def _labels(out: dict, index: int = 0) -> list[str]:
    return [f["label"] for f in out["answers"][index]["fields"]]


_ITEM_BASE_LABELS = ["Company", "Product Code", "Product Name"]
_ITEM_BASE_LABELS_WITH_DESC = ["Company", "Product Code", "Product Name", "Description"]
#: Card v4 (AC-19): the base word asked decides which of the two on-demand lines prints.
_ASKED_LINE = {
    "price": ["List Price"], "list price": ["List Price"], "harga": ["List Price"], "cost": ["List Price"],
    "dimension": ["Dimensions"], "size": ["Dimensions"], "ukuran": ["Dimensions"],
    "saiz": ["Dimensions"],
}


class TestD12BaseFieldsAlwaysRenderOnlySpecsAreOnDemand:
    """D12 (owner ruling, 8 Sep 2026, turn 8f4a8526 "SRTJC802A-1500 product details").
    Turn "list price of X" -> `["price"]` used to drop every OTHER base field (the
    projection kept identity fields + the matched base field + spec hits only) and
    answer with Product Code + Description alone. Base fields are now unconditional."""

    def test_a_base_property_word_asked_produces_no_miss_and_base_fields_are_unaffected(self):
        for word in ("price", "list price", "harga", "cost", "dimension", "size", "ukuran", "saiz"):
            out = fetch.output_structurer(
                _family_envelope([_item("SRTWC286-SH", specs=[_SEAT], description="Wall hung closet")], _MATERIAL_VOCAB),
                {"semantic_input": {"requested_attributes": [word]}},
            )
            assert _labels(out) == _ITEM_BASE_LABELS_WITH_DESC + _ASKED_LINE[word], word
            assert "recorded for" not in out["response"], word

    def test_a_single_token_entry_matches_the_whole_ask_only(self):
        """Review round 2 (S5/S6), still true: "brand name" must not read as the base
        word "name", nor "seat size" as "size" - a token inside a longer ask is not the
        base property. The ask falls through to the spec pass and gets its miss line."""
        for word in ("brand name", "seat size", "selling price"):
            out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH", specs=[_SEAT])], _MATERIAL_VOCAB),
                                          {"semantic_input": {"requested_attributes": [word]}})
            assert _labels(out) == _ITEM_BASE_LABELS, word
            assert f"*{word}:* not recorded for SRTWC286-SH" in out["response"], word
            assert out["response"].count("recorded for") == 1

    def test_a_multi_token_entry_is_still_found_inside_a_longer_ask(self):
        out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH")], {}),
                                      {"semantic_input": {"requested_attributes": ["the list price please"]}})
        assert _labels(out) == _ITEM_BASE_LABELS + ["List Price"]
        assert "recorded for" not in out["response"]

    def test_two_base_property_words_together_produce_no_miss_for_either(self):
        out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH")], {}),
                                      {"semantic_input": {"requested_attributes": ["price", "size"]}})
        assert _labels(out) == _ITEM_BASE_LABELS + ["List Price", "Dimensions"]
        assert "recorded for" not in out["response"]


class TestSpecKeysByTokenContainment:
    """Turn 0682154e "seat cover material of srtwc286" -> `["material"]`: the registry has
    `seat_material` "Seat cover material" AND `material` "Material"; equality matched
    neither and seven items said "no material recorded"."""

    def test_material_reaches_every_key_whose_label_or_key_contains_it_in_registry_order(self):
        out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH", specs=[_SEAT, _MAT])], _MATERIAL_VOCAB),
                                      {"semantic_input": {"requested_attributes": ["material"]}})
        assert _labels(out) == _ITEM_BASE_LABELS + ["Seat cover material", "Material"]
        assert "recorded for" not in out["response"]

    def test_the_whole_phrase_reaches_only_the_key_that_contains_every_token(self):
        out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH", specs=[_SEAT, _MAT])], _MATERIAL_VOCAB),
                                      {"semantic_input": {"requested_attributes": ["seat cover material"]}})
        assert _labels(out) == _ITEM_BASE_LABELS + ["Seat cover material"]

    def test_exact_match_still_wins_and_nothing_else_is_dragged_in(self):
        out = fetch.output_structurer(_family_envelope([_item("SRTWC286-SH", specs=_FOUR_SPECS)], {s["key"]: s["label"] for s in _FOUR_SPECS}),
                                      {"semantic_input": {"requested_attributes": ["wattage"]}})
        assert _labels(out) == _ITEM_BASE_LABELS + ["Wattage"]


class TestOneMissLinePerAskedWord:
    def test_seven_items_without_the_key_produce_one_line_capped_at_five_codes(self):
        items = [_item(f"SRTWC286-SH-{i}") for i in range(7)]
        out = fetch.output_structurer(_family_envelope(items, _MATERIAL_VOCAB),
                                      {"semantic_input": {"requested_attributes": ["material"]}})
        for i in range(7):
            assert _labels(out, i) == _ITEM_BASE_LABELS
        response = out["response"]
        assert response.count("recorded for") == 1
        # the registry's EXACT match names the line ("material" -> "Material")
        assert "*Material:* not recorded for SRTWC286-SH-0, SRTWC286-SH-1, SRTWC286-SH-2, SRTWC286-SH-3, SRTWC286-SH-4 (+2 more)" in response
        assert "no material recorded" not in response

    def test_the_line_names_only_the_items_without_a_hit(self):
        items = [_item("SRTWC286-SH", specs=[_SEAT]), _item("SRTWC286-SH-NEW-P")]
        out = fetch.output_structurer(_family_envelope(items, _MATERIAL_VOCAB),
                                      {"semantic_input": {"requested_attributes": ["material"]}})
        assert _labels(out, 0) == _ITEM_BASE_LABELS + ["Seat cover material"]
        assert _labels(out, 1) == _ITEM_BASE_LABELS
        assert out["response"].count("recorded for") == 1
        assert "not recorded for SRTWC286-SH-NEW-P" in out["response"]
        assert "not recorded for SRTWC286-SH," not in out["response"]

    def test_an_unknown_word_is_named_as_asked_and_a_known_one_by_its_registry_label(self):
        items = [_item("A1"), _item("A2")]
        out = fetch.output_structurer(_family_envelope(items, {"wattage": "Wattage"}),
                                      {"semantic_input": {"requested_attributes": ["voltage", "wattage"]}})
        assert "*voltage:* not recorded for A1, A2" in out["response"]
        assert "*Wattage:* not recorded for A1, A2" in out["response"]
        assert out["response"].count("recorded for") == 2  # one per asked word, never per item

    def test_a_base_hit_is_never_a_miss(self):
        items = [_item("A1"), _item("A2")]
        out = fetch.output_structurer(_family_envelope(items, {}),
                                      {"semantic_input": {"requested_attributes": ["price"]}})
        assert "recorded for" not in out["response"]


def test_no_attribute_asked_is_byte_identical_to_the_compact_specs_path():
    envelope = _family_envelope([_item("SRTWC286-SH", specs=[_SEAT, _MAT]), _item("SRTWC286-SH-NEW-P")], _MATERIAL_VOCAB)
    import copy
    before = copy.deepcopy(envelope)
    out = fetch.output_structurer(envelope, {"semantic_input": {"requested_attributes": []}})
    assert "recorded for" not in out["response"]
    assert "spec_misses" not in envelope
    assert _labels(out, 0) == ["Company", "Product Code", "Product Name", "Specs"]
    assert _labels(out, 1) == ["Company", "Product Code", "Product Name"]
    # an item with no spec keeps its fields, less the two on-demand lines (card v4, AC-19)
    assert envelope["items"][1]["fields"] == [
        f for f in before["items"][1]["fields"] if f["label"] not in ("List Price", "Dimensions")
    ]


def test_product_details_names_no_property_and_shows_neither_price_nor_dimensions():
    """D12 (owner ruling, 8 Sep 2026, turn 8f4a8526 "SRTJC802A-1500 product details"):
    "product details" names no property (prompt fix, both bodies) - `requested_attributes`
    reaches this function empty, which is the no-attribute path and always carried the
    base fields. Pinned here as a named regression, not only via the generic no-attribute
    test above."""
    envelope = _family_envelope([_item("SRTJC802A-1500")], {})
    out = fetch.output_structurer(envelope, {"semantic_input": {"requested_attributes": []}})
    # Card v4 (AC-19, supersedes D12's always-on price and dimensions).
    assert "*List Price:*" not in out["response"]
    assert "*Dimensions:*" not in out["response"]
