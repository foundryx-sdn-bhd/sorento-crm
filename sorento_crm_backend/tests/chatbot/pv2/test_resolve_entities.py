"""PV2-RESOLVE entity + attachment-type resolution (CONTRACT-PV2-13oct section 4).

Written from the contract, before resolve.py exists. No LLM, no network.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held, ResolvedRef

from tests.chatbot.pv2 import _resolve_seed as S


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def ent(kind, key=None, phrase=None, op="set", held=False) -> Entity:
    return Entity(kind=kind, key=key, phrase=phrase, op=op, refers_to_held=held)


def mk_held(**refs) -> Held:
    return Held(
        domain=None,
        refs={kind: list(rows) for kind, rows in refs.items()},
        specs=[],
        attachment_type=None,
        period=None,
        report=None,
        idea_draft_id=None,
    )


def ref(kind, key, label=None) -> ResolvedRef:
    return ResolvedRef(kind=kind, key=key, label=label or key, phrase=None)


def resolver(db, *, customer_scope=None, **pinned) -> DbResolver:
    base = {"brand": set(), "product_type": set(), "attachment_type": set(), "spec_attribute": set(), "spec_value": set()}
    base.update({k: set(v) for k, v in pinned.items()})
    return DbResolver(db, pinned=base, customer_scope=customer_scope)


def one(db, entity, *, held=None, customer_scope=None, **pinned):
    out = resolver(db, customer_scope=customer_scope, **pinned).resolve_entities([entity], held=held or mk_held())
    assert len(out) == 1
    return out[0]


def clean(res):
    for r in res.refs:
        S.assert_clean_label(r.label)
    for o in res.options:
        S.assert_clean_label(o.label)


# --- 1/2: index kinds -------------------------------------------------------
def test_brand_pinned_and_live_is_exact_with_display_name(db):
    S.brand(db, "Sorento Brand", code="SORENTO")
    res = one(db, ent("brand", key="SORENTO"), brand={"SORENTO"})
    assert res.status == "exact"
    assert [(r.kind, r.key, r.label) for r in res.refs] == [("brand", "SORENTO", "Sorento Brand")]
    assert res.unrecognised is False


def test_brand_label_is_brand_name_not_code(db):
    row = S.brand(db, "Mocha Home")
    res = one(db, ent("brand", key=row.brand_code), brand={row.brand_code})
    assert res.status == "exact"
    assert res.refs[0].key == row.brand_code
    assert res.refs[0].label == "Mocha Home"


@pytest.mark.parametrize(
    "pinned,live,why",
    [
        (set(), True, "live but not in the pinned index"),
        ({"GHOST"}, False, "pinned but no such brand"),
        ({"SORENTO"}, "inactive", "pinned but the brand is inactive"),
    ],
)
def test_brand_not_pinned_or_not_live_is_none_and_unrecognised(db, pinned, live, why):
    if live is True:
        S.brand(db, "Sorento Brand", code="SORENTO")
        key = "SORENTO"
    elif live == "inactive":
        S.brand(db, "Sorento Brand", active=False, code="SORENTO")
        key = "SORENTO"
    else:
        key = "GHOST"
    res = one(db, ent("brand", key=key, phrase=key), brand=pinned)
    assert res.status == "none", why
    assert res.refs == []
    if live is True:  # not in the pinned index: the phrase goes to the unknown-words log
        assert res.unrecognised is True


def test_product_class_pinned_live_and_not_pinned(db):
    # Key = the index's PRODUCT CLASSES key (slug of the class label); label = the class label.
    S.category(db, class_label="Water Closet")
    ok = one(db, ent("product_class", key="water_closet"), product_class={"water_closet"})
    assert ok.status == "exact" and ok.refs[0].key == "water_closet" and ok.refs[0].label == "Water Closet"
    bad = one(db, ent("product_class", key="water_closet", phrase="wc"), product_class=set())
    assert bad.status == "none" and bad.unrecognised is True


def test_product_type_key_is_a_product_type_value_products_carry(db):
    p = S.product(db, "WC-ONE", cat=S.category(db, class_label="Water Closet"))
    S.specs(db, p, klass="Water Closet", product_type="one_piece")
    ok = one(db, ent("product_type", key="one_piece"), product_type={"one_piece"})
    assert ok.status == "exact" and ok.refs[0].label == "One piece"
    gone = one(db, ent("product_type", key="two_piece"), product_type={"two_piece"})
    assert gone.status == "none" and gone.refs == []


def test_attachment_type_pinned_and_live_is_exact_with_type_name(db):
    S.attachment_type(db, "technical_specifications", "Technical Specifications")
    res = resolver(db, attachment_type={"technical_specifications"}).resolve_attachment_type(
        "technical_specifications", held=mk_held()
    )
    assert res.status == "exact"
    assert res.refs[0].key == "technical_specifications"
    assert res.refs[0].label == "Technical Specifications"


@pytest.mark.parametrize("pinned", [set(), {"other"}])
def test_attachment_type_not_pinned_is_none_even_if_live(db, pinned):
    S.attachment_type(db, "technical_specifications", "Technical Specifications")
    res = resolver(db, attachment_type=pinned).resolve_attachment_type("technical_specifications", held=mk_held())
    assert res.status == "none" and res.unrecognised is True and res.refs == []


def test_attachment_type_pinned_but_not_live_is_none(db):
    res = resolver(db, attachment_type={"ghost_type"}).resolve_attachment_type("ghost_type", held=mk_held())
    assert res.status == "none" and res.refs == []


# --- 3: null key + phrase never resolves ------------------------------------
def test_brand_null_key_with_exact_name_phrase_is_not_resolved(db):
    S.brand(db, "Sorento Brand", code="SORENTO")
    res = one(db, ent("brand", key=None, phrase="SORENTO"), brand={"SORENTO"})
    assert res.status != "exact"
    assert res.refs == []
    assert res.unrecognised is True
    assert res.phrase == "SORENTO"


def test_attachment_type_null_key_offers_only_types_the_held_product_carries(db):
    cat = S.category(db, class_label="Water Closet")
    prod = S.product(db, "SRTWC286-A", cat=cat)
    carried1 = S.attachment_type(db, "technical_specifications", "Technical Specifications")
    carried2 = S.attachment_type(db, "product_photos", "Product Photos")
    S.attachment_type(db, "certification", "Certification")  # exists, product does not carry it
    S.attach(db, prod, carried1)
    S.attach(db, prod, carried2)
    held = mk_held(product=[ref("product", "SRTWC286-A")])
    pin = {"technical_specifications", "product_photos", "certification"}
    res = resolver(db, attachment_type=pin).resolve_attachment_type(None, held=held)
    assert res.status == "ambiguous"
    assert {o.label for o in res.options} == {"Technical Specifications", "Product Photos"}
    assert not {o.label for o in res.options} & pin  # labels are names, never codes
    assert res.refs == []
    clean(res)


# --- 4: products -------------------------------------------------------------
def test_product_code_exact_case_and_whitespace_insensitive(db):
    S.product(db, "SRTWC286-X")
    for phrase in ("SRTWC286-X", "srtwc286-x", "  SRTWC286-X  ", "srtwc286-x\n"):
        res = one(db, ent("product", phrase=phrase))
        assert res.status == "exact", phrase
        assert [(r.kind, r.key) for r in res.refs] == [("product", "SRTWC286-X")]
        clean(res)


def test_product_code_prefix_and_substring_do_not_match(db):
    # PV2-FIX 14 Oct: products use v1's search (crew brief). A prefix or substring phrase
    # gives candidates, never an exact auto-pick, unless the hit is unique.
    S.product(db, "SRTWC286-X")
    S.product(db, "SRTWC286-Y")
    for phrase in ("SRTWC28", "SRTWC", "WC286"):
        res = one(db, ent("product", phrase=phrase))
        assert res.status == "ambiguous", phrase
        assert {o.key for o in res.options} == {"SRTWC286-X", "SRTWC286-Y"}, phrase
        assert res.refs == []


def test_family_code_exact_resolves_to_one_family_ref_with_variant_count(db):
    cat = S.category(db, class_label="Water Closet")
    S.product(db, "SRTWC286-A", cat=cat)
    S.product(db, "SRTWC286-B", cat=cat)
    res = one(db, ent("product", phrase="srtwc286"))
    assert res.status == "exact"
    assert len(res.refs) == 1
    assert res.refs[0].key == "SRTWC286"
    assert "2" in res.refs[0].label
    clean(res)


def test_non_searchable_product_is_not_resolved(db):
    hidden = S.category(db, class_label="Misc", searchable=False)
    S.product(db, "MISC-001", cat=hidden)
    assert one(db, ent("product", phrase="MISC-001")).status == "none"


# --- 5: customers ------------------------------------------------------------
def test_customer_exact_code_and_exact_name(db):
    row = S.customer(db, "300-D093", "Deluxe Home Center")
    by_code = one(db, ent("customer", phrase="300-d093"))
    assert by_code.status == "exact" and len(by_code.refs) == 1
    assert by_code.refs[0].key in (row.customer_code, row.id)
    assert "Deluxe Home Center" in by_code.refs[0].label
    by_name = one(db, ent("customer", phrase="  deluxe   HOME center "))
    assert by_name.status == "exact" and len(by_name.refs) == 1
    assert by_name.refs[0].key in (row.customer_code, row.id)
    clean(by_code)
    clean(by_name)


def test_customer_partial_name_is_ambiguous_with_names_never_codes_or_ids(db):
    a = S.customer(db, "CODE-AAA-1", "ACME TRADING")
    b = S.customer(db, "CODE-BBB-2", "Acme Hardware")
    S.customer(db, "CODE-CCC-3", "Other Co")
    res = one(db, ent("customer", phrase="acme"))
    assert res.status == "ambiguous"
    assert res.refs == []
    assert {o.label for o in res.options} == {"ACME TRADING", "Acme Hardware"}
    for o in res.options:
        for secret in (a.customer_code, b.customer_code, a.id, b.id):
            assert secret not in o.label
    clean(res)


def test_customer_single_partial_candidate_is_still_never_auto_resolved(db):
    S.customer(db, "CODE-AAA-1", "ACME TRADING")
    res = one(db, ent("customer", phrase="acme"))
    assert res.status == "ambiguous"
    assert res.refs == []
    assert [o.label for o in res.options] == ["ACME TRADING"]


def test_customer_candidates_capped_at_eight(db):
    for i in range(12):
        S.customer(db, f"CAP-{i:02d}", f"Acme Shop {i:02d}")
    res = one(db, ent("customer", phrase="acme"))
    assert res.status == "ambiguous"
    assert len(res.options) == 8


def test_customer_zero_candidates_is_none(db):
    S.customer(db, "CODE-1", "Something Else")
    res = one(db, ent("customer", phrase="acme"))
    assert res.status == "none"
    assert res.refs == [] and res.options == []


def test_customer_scope_enforced_excludes_customers_outside_ids(db):
    inside = S.customer(db, "IN-1", "Acme Inside")
    outside = S.customer(db, "OUT-1", "Acme Outside")
    scope = {"enforced": True, "ids": [inside.id]}
    partial = one(db, ent("customer", phrase="acme"), customer_scope=scope)
    assert [o.label for o in partial.options] == ["Acme Inside"]
    assert one(db, ent("customer", phrase="OUT-1"), customer_scope=scope).status == "none"
    assert one(db, ent("customer", phrase="Acme Outside"), customer_scope=scope).status == "none"
    unrestricted = one(db, ent("customer", phrase="OUT-1"), customer_scope={"enforced": False, "ids": []})
    assert unrestricted.status == "exact"
    assert outside.id not in [r.key for r in partial.refs]


# --- 6: labels ---------------------------------------------------------------
def test_labels_never_carry_uuid_or_middle_dot(db):
    S.customer(db, "C-1", "Acme · Trading")  # a name with the separator must not leak into a label format
    S.customer(db, "C-2", "Acme Hardware")
    cat = S.category(db, class_label="Water Closet")
    S.product(db, "SRTWC286-A", cat=cat)
    S.product(db, "SRTWC286-B", cat=cat)
    S.brand(db, "Sorento Brand", code="SORENTO")
    results = [
        one(db, ent("customer", phrase="acme")),
        one(db, ent("product", phrase="SRTWC286")),
        one(db, ent("product", phrase="SRTWC286-A")),
        one(db, ent("brand", key="SORENTO"), brand={"SORENTO"}),
    ]
    for res in results:
        for r in res.refs:
            assert " · " not in r.label.replace("Acme · Trading", "")
            assert not S.UUID_RE.search(r.label)
        for o in res.options:
            assert not S.UUID_RE.search(o.label)
    # the formatter's own separator must not appear for the clean names
    hardware = [o for o in results[0].options if o.label == "Acme Hardware"]
    assert hardware


# --- 7: refers_to_held -------------------------------------------------------
def test_refers_to_held_one_two_none(db):
    one_held = mk_held(brand=[ref("brand", "SORENTO")])
    r1 = one(db, ent("brand", held=True), held=one_held)
    assert r1.status == "exact" and [r.key for r in r1.refs] == ["SORENTO"]

    two_held = mk_held(brand=[ref("brand", "SORENTO", "Sorento"), ref("brand", "MOCHA", "Mocha")])
    r2 = one(db, ent("brand", held=True), held=two_held)
    assert r2.status == "ambiguous"
    assert {o.label for o in r2.options} == {"Sorento", "Mocha"}
    assert r2.refs == []

    r3 = one(db, ent("brand", held=True), held=mk_held())
    assert r3.status == "none" and r3.refs == []


def test_results_keep_entity_order_one_per_entity(db):
    S.brand(db, "Sorento Brand", code="SORENTO")
    S.product(db, "SRTWC286-X")
    out = resolver(db, brand={"SORENTO"}).resolve_entities(
        [ent("product", phrase="SRTWC286-X"), ent("brand", key="SORENTO"), ent("product", phrase="NOPE")],
        held=mk_held(),
    )
    assert [r.status for r in out] == ["exact", "exact", "none"]
    assert out[0].refs[0].kind == "product" and out[1].refs[0].kind == "brand"
