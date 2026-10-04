"""Shape B: a described set intersected with a domain predicate, inside the CRM.

Membership and the count are SQL over the full company-scoped catalogue; the ranker
only orders the products that already qualify. The count is honest by construction:
it counts distinct variant FAMILIES (what a customer calls "a product"), never rows,
and an unrecognized word is reported as unrecognized rather than silently answering
"none" from the wrong set.

Contract: sorento_crm_n8n/n8n-workflows-init/plans/crm-ask-spec-backward-search.md.
Plan: documentation/plans/PLAN-spec-backward-search.md.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.company import Company
from app.models.certificate import Certificate, CertificateProduct, CertificateRevision
from app.models.inventory import Stock, Warehouse
from app.models.lookup import LookupOption, LookupOptionKeyword, LookupSet
from app.models.marketing import Promotion, PromotionGroup, PromotionProduct
from app.models.procurement import InboundShipment, InboundShipmentLine
from app.models.product import Product, ProductAttachment, ProductCategory, UnitOfMeasure
from app.models.resources import Attachment, AttachmentType
from app.services.company_scope import company_scope
from app.services.error_handler import AppException
from app.services.product_class_signal import backfill_category_signals
from app.services.product_spec_derivation import derive_for_code
from app.services.product_spec_registry import seed_spec_registry
from app.services.product_spec_search import filter_specs, search_specs
from app.services.product_predicate_service import resolve_product_set
from tests._pg_fixture import blank_session

_REFS: dict = {}


def _utc_today() -> date:
    """The service compares validity against `func.current_date()` on a
    session pinned to UTC (`app/database.py` sets `options=-c timezone=utc`).
    Seed fixtures against that same clock, not the local wall clock, or a
    test run between 00:00 and 08:00 Malaysia time seeds a date the service
    still reads as 'today'."""
    return datetime.now(timezone.utc).date()


@pytest.fixture
def db():
    with blank_session() as s:
        ks = ProductCategory(id=str(uuid.uuid4()), category_code="SRT-KS", category_name="SRT-KS")
        wc = ProductCategory(id=str(uuid.uuid4()), category_code="SRT-WC", category_name="SRT-WC")
        uom = UnitOfMeasure(id=str(uuid.uuid4()), uom_code="ZZT-PCS", uom_name="Piece")
        s.add_all([ks, wc, uom])
        s.flush()
        backfill_category_signals(s)
        seed_spec_registry(s)
        _REFS.update({"ks": ks.id, "wc": wc.id, "uom": uom.id})
        yield s


def _product(db, code, description, *, category="ks", variant_of=None):
    row = Product(
        id=str(uuid.uuid4()),
        product_code=code,
        product_name=code,
        description=description,
        category_id=_REFS[category],
        base_uom_id=_REFS["uom"],
        list_price=Decimal("1.00"),
        variant_of_id=variant_of,
    )
    db.add(row)
    db.flush()
    derive_for_code(db, code)
    return row


def _warehouse(db):
    # AC-1346/R22: `is_active` explicit rather than relying on the model's
    # Python-side default - the stock leg now filters on it, so a fixture that
    # left this to chance would silently break the moment that default ever
    # changed.
    wh = Warehouse(
        id=str(uuid.uuid4()), warehouse_code=f"ZZT-{uuid.uuid4().hex[:6]}", warehouse_name="ZZT WH", is_active=True
    )
    db.add(wh)
    db.flush()
    return wh


def _stock(db, product, qty, warehouse=None):
    wh = warehouse or _warehouse(db)
    db.add(
        Stock(
            id=str(uuid.uuid4()),
            product_id=product.id,
            warehouse_id=wh.id,
            quantity_on_hand=qty,
            quantity_reserved=0,
            quantity_damaged=0,
        )
    )
    db.flush()


def _certificate(db, product, *, status="active", scheme="ZZT-SIRIM", valid_until=None):
    cert = Certificate(
        id=str(uuid.uuid4()),
        scheme=scheme,
        certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}",
        status=status,
    )
    db.add(cert)
    db.flush()
    if valid_until is not None:
        rev = CertificateRevision(
            id=str(uuid.uuid4()),
            certificate_id=cert.id,
            revision_no=1,
            valid_until=valid_until,
        )
        db.add(rev)
        db.flush()
        cert.current_revision_id = rev.id
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=product.id))
    db.flush()
    return cert


def _promotion(db, product, *, is_active=True, end_date=None):
    promo = Promotion(id=str(uuid.uuid4()), description=f"ZZT promo {uuid.uuid4().hex[:6]}", is_active=is_active, end_date=end_date)
    db.add(promo)
    db.flush()
    group = PromotionGroup(id=uuid.uuid4(), promotion_id=promo.id, group_name="G")
    db.add(group)
    db.flush()
    db.add(PromotionProduct(id=str(uuid.uuid4()), promotion_id=promo.id, promotion_group_id=group.id, product_id=product.id))
    db.flush()
    return promo


def _attachment_type(db, type_name):
    at = AttachmentType(
        id=str(uuid.uuid4()),
        code=type_name.upper().replace(" ", "_"),
        type_name=type_name,
        allowed_extensions="pdf",
    )
    db.add(at)
    db.flush()
    return at


def _attach(db, product, attachment_type):
    att = Attachment(
        id=str(uuid.uuid4()),
        original_filename="zzt.pdf",
        stored_filename="zzt.pdf",
        file_path="https://cdn/zzt.pdf",
        attachment_type_id=attachment_type.id,
    )
    db.add(att)
    db.flush()
    db.add(ProductAttachment(id=str(uuid.uuid4()), product_id=product.id, attachment_id=att.id))
    db.flush()
    return att


def _totals(db, require, terms=None, **kw):
    return resolve_product_set(db, require=require, free_terms=terms, **kw)


# --------------------------------------------------------------------------- #
# filter_specs: the class-only membership vocabulary                            #
# --------------------------------------------------------------------------- #
def test_filter_specs_resolves_a_class_term(db):
    _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    verdict = filter_specs(db, free_terms=["kitchen sink"])
    assert verdict["class_labels"]
    assert verdict["clause"] is not None
    assert verdict["unrecognized_terms"] == []


def test_filter_specs_drops_known_spec_words_without_flagging_them(db):
    # "wall hung" is a registry mounting value: not a class, but recognized - 
    # it must neither define membership nor be reported as unrecognized.
    verdict = filter_specs(db, free_terms=["wall hung"])
    assert verdict["class_labels"] == []
    assert verdict["unrecognized_terms"] == []


def test_filter_specs_reports_nonsense_as_unrecognized(db):
    verdict = filter_specs(db, free_terms=["flurbish"])
    assert verdict["unrecognized_terms"] == ["flurbish"]
    assert verdict["clause"] is None


# --------------------------------------------------------------------------- #
# Console pass 6 (R23, AC-1347): after R21's fix, the same carry-less "more"    #
# turn reaches `filter_specs` with the reused certificate leg's remainder word  #
# "more" - not a phrase stopword, so it answered "I don't know 'more' as a      #
# product type" instead of the honest unscoped set answer.                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("word", ["more", "next", "lagi", "show more"])
def test_filter_specs_treats_paging_words_as_phrase_stopwords(db, word):
    """AC-1347/R23: measured live - once R21 stops "more" surviving as a bogus
    certificate scheme, the SAME remainder word still reaches `filter_specs` as
    a described-set term, which does not know it either: "which tap has cert"
    -> "which water tap has cert" (clarify) -> "more" answered "I don't know
    'more' as a product type. Try a product type such as ...", never the
    unscoped set answer the reused question demands once its set is gone.

    RED for "more", "next", "lagi": `_PHRASE_STOPWORDS` carries "please" and
    "show" already (R9's own imperative-verb list) but not the paging words
    themselves, so `filter_specs(db, free_terms=["more"])` reports "more"
    unrecognized and its own `clause` is not None. "show more" is a control on
    the ALREADY-covered half ("show") plus the still-missing half ("more") -
    both words must clear before the phrase reports nothing.
    """
    verdict = filter_specs(db, free_terms=[word])
    assert verdict["clause"] is None, verdict
    assert verdict["class_labels"] == [], verdict
    assert verdict["unrecognized_terms"] == [], verdict


def test_filter_specs_still_reports_nonsense_once_paging_words_are_stopwords(db):
    """AC-1347/R23 control: "zzqx" must stay unrecognized regardless of the
    paging-word fix - the stopword list widens by exactly the named words,
    never into a general amnesty for anything unrecognized."""
    verdict = filter_specs(db, free_terms=["zzqx"])
    assert verdict["unrecognized_terms"] == ["zzqx"], verdict
    assert verdict["clause"] is None, verdict


# --------------------------------------------------------------------------- #
# the four legs                                                                 #
# --------------------------------------------------------------------------- #
def test_stock_leg_requires_on_hand_above_zero(db):
    with_stock = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    without = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    _stock(db, with_stock, 3)
    _stock(db, without, 0)
    out = _totals(db, {"stock": True}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]
    assert out["qualifying_total"] == 1


# --------------------------------------------------------------------------- #
# Console pass 6 (R22, AC-1346): the stock leg must mirror the stock list      #
# tool's own visibility - a Stock row in an INACTIVE warehouse is a real       #
# on-hand row the tool itself never renders (inventory_service.py:743 filters  #
# `Stock.warehouse.has(Warehouse.is_active.is_(True))`), so a set-answer       #
# header counting it is a count the customer's own reply can never back up.    #
# --------------------------------------------------------------------------- #


def test_stock_leg_only_counts_stock_in_an_active_warehouse(db):
    """AC-1346/R22: measured live - ACC-SRT9012's only stock row sits in
    warehouse SPARE/P, `is_active = false`; the stock list tool filters it out,
    but `_leg_stock` counted it anyway, so "which bathroom accessory has
    stock" answered "964 ... Showing 4." with five ids sent for four rendered.

    RED: `_leg_stock`'s `exists()` has no `Warehouse` join at all - any Stock
    row with `quantity_on_hand > 0` counts regardless of its warehouse's own
    `is_active` flag, so product A (stock only in the inactive warehouse)
    qualifies with `qualifying_total` 1, not the 0 this AC demands.
    """
    from app.models.inventory import Warehouse

    inactive_wh = Warehouse(
        id=str(uuid.uuid4()), warehouse_code=f"ZZT-{uuid.uuid4().hex[:6]}", warehouse_name="ZZT Inactive WH",
        is_active=False,
    )
    active_wh = Warehouse(
        id=str(uuid.uuid4()), warehouse_code=f"ZZT-{uuid.uuid4().hex[:6]}", warehouse_name="ZZT Active WH",
        is_active=True,
    )
    db.add_all([inactive_wh, active_wh])
    db.flush()

    product_a = _product(db, "ZZT-STK-INACTIVE", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    _stock(db, product_a, 5, warehouse=inactive_wh)
    out_a = _totals(db, {"stock": True}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a
    codes_a = [c["product_code"] for c in out_a["candidates"]]
    assert "ZZT-STK-INACTIVE" not in codes_a, codes_a

    product_b = _product(db, "ZZT-STK-ACTIVE", "SORENTO S/STEEL KITCHEN SINK (900X500X200MM)")
    _stock(db, product_b, 5, warehouse=active_wh)
    out_b = _totals(db, {"stock": True}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 1, out_b
    codes_b = [c["product_code"] for c in out_b["candidates"]]
    assert codes_b == ["ZZT-STK-ACTIVE"], codes_b


def test_certificate_leg_bare_true_means_any_active_register_cert(db):
    certified = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    archived = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    _certificate(db, certified, status="active")
    _certificate(db, archived, status="archived")
    out = _totals(db, {"certificate": True}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]


def test_certificate_object_form_filters_on_validity(db):
    valid = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    expired = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    _certificate(db, valid, valid_until=_utc_today() + timedelta(days=30))
    _certificate(db, expired, valid_until=_utc_today() - timedelta(days=1))
    out = _totals(db, {"certificate": {"validity_state": "valid"}}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]


def test_promotion_leg_requires_an_active_unexpired_promotion(db):
    promoted = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    lapsed = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    switched_off = _product(db, "ZZT-SINK-C", "CABANA CERAMIC KITCHEN SINK (1000X500X140MM)")
    _promotion(db, promoted)
    _promotion(db, lapsed, end_date=_utc_today() - timedelta(days=1))
    _promotion(db, switched_off, is_active=False)
    out = _totals(db, {"promotion": True}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]


def test_attachment_type_leg_resolves_the_customers_label(db):
    drawn = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    bare = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    at = _attachment_type(db, "Technical Drawing")
    _attach(db, drawn, at)
    out = _totals(db, {"attachment_type": "technical drawing"}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]
    assert out["require"]["attachment_type"] == "Technical Drawing"


def test_an_unresolvable_attachment_label_is_unrecognized_not_none(db):
    p = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    _attach(db, p, _attachment_type(db, "Technical Drawing"))
    out = _totals(db, {"attachment_type": "blorp sheet"}, ["kitchen sink"])
    assert out["qualifying_total"] == 0
    assert "blorp sheet" in out["unrecognized_terms"]


def test_multiple_require_keys_are_an_and(db):
    both = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    stock_only = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    wh = _warehouse(db)
    _stock(db, both, 5, wh)
    _stock(db, stock_only, 5, wh)
    _certificate(db, both)
    out = _totals(db, {"stock": True, "certificate": True}, ["kitchen sink"])
    codes = [c["product_code"] for c in out["candidates"]]
    assert codes == ["ZZT-SINK-A"]


def test_an_unknown_require_key_is_a_422(db):
    with pytest.raises(AppException):
        _totals(db, {"blessing": True}, ["kitchen sink"])


# --------------------------------------------------------------------------- #
# the honest count                                                              #
# --------------------------------------------------------------------------- #
def test_qualifying_total_counts_variant_families_not_rows(db):
    parent = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    v1 = _product(db, "ZZT-SINK-A-BK", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM) BLACK", variant_of=parent.id)
    v2 = _product(db, "ZZT-SINK-A-GD", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM) GOLD", variant_of=parent.id)
    wh = _warehouse(db)
    for p in (parent, v1, v2):
        _stock(db, p, 2, wh)
    out = _totals(db, {"stock": True}, ["kitchen sink"])
    assert out["qualifying_total"] == 1
    assert out["truncated"] is False


def test_truncated_when_more_qualify_than_are_shown(db):
    wh = _warehouse(db)
    for i in range(4):
        p = _product(db, f"ZZT-SINK-{i}", f"SORENTO S/STEEL KITCHEN SINK ({900 + i}X500X200MM)")
        _stock(db, p, 1, wh)
    out = _totals(db, {"stock": True}, ["kitchen sink"], limit=2)
    assert out["qualifying_total"] == 4
    assert len(out["candidates"]) == 2
    assert out["truncated"] is True


def test_class_membership_is_a_filter_but_numbers_stay_boosts(db):
    wh = _warehouse(db)
    big = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    small = _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    other_class = _product(db, "ZZT-WC-A", "SORENTO CERAMIC WALL HUNG WATER CLOSET", category="wc")
    for p in (big, small, other_class):
        _stock(db, p, 1, wh)
    out = _totals(
        db,
        {"stock": True},
        ["kitchen sink"],
        specs=[{"key": "length", "value": 1000, "op": "at_least"}],
    )
    codes = [c["product_code"] for c in out["candidates"]]
    # The water closet is filtered out (wrong class); the 800mm sink is NOT
    # filtered by the number - it merely ranks below the 1000mm one.
    assert "ZZT-WC-A" not in codes
    assert set(codes) == {"ZZT-SINK-A", "ZZT-SINK-B"}
    assert codes[0] == "ZZT-SINK-A"
    assert out["qualifying_total"] == 2


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R27, AC-1352): a spec binding with a STRING       #
# value is a membership filter of the described set, alongside class /        #
# product_type / brand - a NUMERIC binding stays boost-only.                   #
# --------------------------------------------------------------------------- #


def _water_closet_with_trap(db, code, *, trap_type, trap_length, qty=5):
    from app.models.product_spec import ProductSpecifications

    product = _product(db, code, f"SORENTO CERAMIC {code} WATER CLOSET", category="wc")
    spec_row = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == product.id).one()
    values = dict(spec_row.values or {})
    values["trap_type"] = {"value": trap_type}
    values["trap_length"] = {"value": trap_length}
    spec_row.values = values
    db.flush()
    _stock(db, product, qty)
    return product


def test_filter_specs_treats_a_string_spec_binding_as_membership(db):
    """AC-1352/R27: `filter_specs(db, specs=[class Water Closet, trap_type
    s_trap])` must keep ONLY the s_trap product - a string-valued spec key
    (trap_type, colour, finish, ...) is a membership filter of the described
    set exactly like class / product_type / brand.

    RED: `_MEMBERSHIP_KEYS` is `("class", "product_type", "brand")` only -
    `trap_type` is silently dropped from `membership`, so the clause is class
    Water Closet alone and BOTH products (s_trap and p_trap) pass it.
    """
    s_trap = _water_closet_with_trap(db, "ZZT-WC-STRAP", trap_type="s_trap", trap_length=250)
    _water_closet_with_trap(db, "ZZT-WC-PTRAP", trap_type="p_trap", trap_length=180)

    verdict = filter_specs(
        db,
        specs=[
            {"key": "class", "value": "Water Closet"},
            {"key": "trap_type", "value": "s_trap"},
        ],
    )
    assert verdict["clause"] is not None, verdict

    from app.models.product_spec import ProductSpecifications

    rows = (
        db.query(Product.product_code)
        .join(ProductSpecifications, ProductSpecifications.product_id == Product.id)
        .filter(verdict["clause"])
        .all()
    )
    codes = {row[0] for row in rows}
    assert codes == {s_trap.product_code}, codes


def test_resolve_product_set_filters_on_the_string_spec_binding(db):
    """AC-1352/R27: the resolver-level contract - `resolve_product_set(require=
    {"stock": True}, specs=[class Water Closet, trap_type s_trap])` must count
    ONLY the s_trap product.

    RED for the SAME reason as the sibling `filter_specs` test: `trap_type`
    never reaches membership, so `qualifying_total` is 2, not 1.
    """
    s_trap = _water_closet_with_trap(db, "ZZT-WC-STRAP2", trap_type="s_trap", trap_length=250)
    _water_closet_with_trap(db, "ZZT-WC-PTRAP2", trap_type="p_trap", trap_length=180)

    out = resolve_product_set(
        db,
        require={"stock": True},
        specs=[
            {"key": "class", "value": "Water Closet"},
            {"key": "trap_type", "value": "s_trap"},
        ],
    )
    assert out["qualifying_total"] == 1, out
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == [s_trap.product_code], codes


def test_resolve_product_set_keeps_a_numeric_spec_binding_as_a_ranking_boost(db):
    """AC-1352/R27: a NUMERIC spec binding (trap_length) must NOT filter
    membership - both water closets qualify regardless of their trap_length -
    but the one matching the stated number ranks first, the same "filter vs
    boost" split `test_class_membership_is_a_filter_but_numbers_stay_boosts`
    already pins for `length`.
    """
    matching = _water_closet_with_trap(db, "ZZT-WC-250", trap_type="s_trap", trap_length=250)
    other = _water_closet_with_trap(db, "ZZT-WC-180", trap_type="p_trap", trap_length=180)

    out = resolve_product_set(
        db,
        require={"stock": True},
        specs=[
            {"key": "class", "value": "Water Closet"},
            {"key": "trap_length", "value": 250},
        ],
        free_terms=["water closet"],
    )
    assert out["qualifying_total"] == 2, out
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert set(codes) == {matching.product_code, other.product_code}, codes
    assert codes[0] == matching.product_code, codes


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R30, AC-1355): the require-only arm (no          #
# `free_terms`, so `search_specs` never runs) must ALSO carry real            #
# `matched_specs` on its candidates, not the hardcoded `[]` it returns today   #
# - the Match line (compile_state.py's `_matched_on_line`) reads this off     #
# every shown row, and a "which tap has cert"-style bare require turn never   #
# supplies free_terms at all.                                                 #
# --------------------------------------------------------------------------- #


def test_resolve_product_set_carries_matched_specs_on_the_require_only_arm(db):
    """AC-1355/R30: `resolve_product_set(require={"stock": True}, specs=[class
    Water Closet, trap_type s_trap, trap_length 250])` with NO `free_terms` -
    the require-only/deterministic-listing arm (`rank_by_words` is False) -
    must still stamp every candidate's `matched_specs` with the keys that
    bound it: "trap_type" and "class" on both qualifying products (both are
    s_trap, both are Water Closet - that IS how they qualified), and
    "trap_length" only on the one whose own value is 250.

    RED: the require-only arm builds each candidate with `"matched_specs":
    []` unconditionally (measured in `product_predicate_service.py`'s own
    listing branch) - `search_specs` (the only code that ever populates this
    key) never runs when `rank_by_words` is False, which is exactly this
    call's shape (no `free_terms`).
    """
    matching = _water_closet_with_trap(db, "ZZT-WC-RO-250", trap_type="s_trap", trap_length=250)
    other = _water_closet_with_trap(db, "ZZT-WC-RO-300", trap_type="s_trap", trap_length=300)

    out = resolve_product_set(
        db,
        require={"stock": True},
        specs=[
            {"key": "class", "value": "Water Closet"},
            {"key": "trap_type", "value": "s_trap"},
            {"key": "trap_length", "value": 250},
        ],
    )
    assert out["qualifying_total"] == 2, out
    by_code = {cand["product_code"]: cand for cand in out["candidates"]}
    assert set(by_code) == {matching.product_code, other.product_code}, by_code

    for code, cand in by_code.items():
        matched = set(cand.get("matched_specs") or [])
        assert "class" in matched, (code, cand)
        assert "trap_type" in matched, (code, cand)

    assert "trap_length" in set(by_code[matching.product_code].get("matched_specs") or []), by_code[
        matching.product_code
    ]
    assert "trap_length" not in set(by_code[other.product_code].get("matched_specs") or []), by_code[
        other.product_code
    ]


# --------------------------------------------------------------------------- #
# Owner regression (PR #833, R32, AC-1357): the require-only arm's listing     #
# order is alphabetical-by-code today - it must instead order by HOW MANY     #
# spec bindings each candidate matched (more first), code only as the         #
# tiebreak among equal counts.                                                #
# --------------------------------------------------------------------------- #


def test_resolve_product_set_orders_the_require_only_arm_by_matched_binding_count(db):
    """AC-1357/R32: three Water Closet products, codes chosen so alphabetical
    order is the WRONG order - "ZZT-WC-A" (s_trap, trap_length=180) matches
    only two of the three stated bindings (class, trap_type), while
    "ZZT-WC-B" and "ZZT-WC-C" (both s_trap, trap_length=250) match all three.
    `resolve_product_set(require={"stock": True}, specs=[class Water Closet,
    trap_type s_trap, trap_length 250])` with NO `free_terms` (the
    require-only arm) must list B and C first (both matched three bindings),
    A last (matched two) - and within the equal-count pair, by code: B before
    C.

    RED today: the require-only arm's listing query is `.order_by(family,
    Product.product_code).distinct(family)` (measured in
    `product_predicate_service.py`) - pure code order, ignorant of
    `matched_specs` - so it returns A, B, C, not B, C, A.
    """
    a = _water_closet_with_trap(db, "ZZT-WC-A", trap_type="s_trap", trap_length=180)
    b = _water_closet_with_trap(db, "ZZT-WC-B", trap_type="s_trap", trap_length=250)
    c = _water_closet_with_trap(db, "ZZT-WC-C", trap_type="s_trap", trap_length=250)

    specs = [
        {"key": "class", "value": "Water Closet"},
        {"key": "trap_type", "value": "s_trap"},
        {"key": "trap_length", "value": 250},
    ]

    out = resolve_product_set(db, require={"stock": True}, specs=specs)
    assert out["qualifying_total"] == 3, out
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == [b.product_code, c.product_code, a.product_code], codes

    # The ranked arm (free_terms present, `rank_by_words` True) is untouched by
    # this fix - it ranks through `search_specs`, not the require-only listing
    # query, and `test_resolve_product_set_keeps_a_numeric_spec_binding_as_a_
    # ranking_boost` already pins that a matching numeric boost ranks first.
    # Here the only pinned claim is the SAME one that test makes for its own
    # pair: the product that also matches the stated trap_length boost (B or
    # C) outranks the one that does not (A) - not a specific order between the
    # two 3-binding matches, which this fix does not touch.
    ranked = resolve_product_set(db, require={"stock": True}, specs=specs, free_terms=["water closet"])
    ranked_codes = [cand["product_code"] for cand in ranked["candidates"]]
    assert ranked_codes[-1] == a.product_code, ranked_codes
    assert set(ranked_codes[:2]) == {b.product_code, c.product_code}, ranked_codes


def test_unrecognized_terms_do_not_silently_mean_none(db):
    p = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    _stock(db, p, 1)
    out = _totals(db, {"stock": True}, ["flurbish"])
    assert out["unrecognized_terms"] == ["flurbish"]
    assert out["qualifying_total"] == 0
    assert out["candidates"] == []


# --------------------------------------------------------------------------- #
# company isolation: every leg fail-closed across companies                     #
# --------------------------------------------------------------------------- #
def test_no_leg_bleeds_across_companies(db):
    other = Company(id=str(uuid.uuid4()), code="ZZT-MC", name="ZZT Mocha")
    db.add(other)
    db.flush()

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-SINK-X", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
        _stock(db, theirs, 9)
        _certificate(db, theirs)
        _promotion(db, theirs)
        _attach(db, theirs, _attachment_type(db, "Technical Drawing"))

    # Session scope is Sorento (conftest default): the other company's fully
    # qualifying product must be invisible through EVERY leg.
    for require in (
        {"stock": True},
        {"certificate": True},
        {"promotion": True},
        {"attachment_type": "technical drawing"},
    ):
        out = _totals(db, require, ["kitchen sink"])
        assert out["qualifying_total"] == 0, require
        assert out["candidates"] == [], require


# --------------------------------------------------------------------------- #
# stage-2: the ranker restricted to the qualifying set                          #
# --------------------------------------------------------------------------- #
def test_search_specs_product_ids_whitelist_restricts_candidates(db):
    a = _product(db, "ZZT-SINK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    _product(db, "ZZT-SINK-B", "SORENTO S/STEEL KITCHEN SINK (800X450X200MM)")
    found = search_specs(db, free_terms=["kitchen sink"], product_ids=[a.id])
    codes = [c["product_code"] for c in found["candidates"]]
    assert codes == ["ZZT-SINK-A"]


# --------------------------------------------------------------------------- #
# Attribute-first asks S1 (PLAN-attribute-first-asks.md)                       #
# --------------------------------------------------------------------------- #


def test_resolve_product_set_never_returns_a_silent_zero(db):
    """AC-1302: "water tap" names no class/product_type/brand (AC-1301), and
    `resolve_product_set` must carry that honesty through rather than answering a
    silent, indistinguishable-from-real zero.
    """
    out = _totals(db, {"certificate": True}, ["water tap"])
    assert out["qualifying_total"] == 0
    assert "water tap" in out["unrecognized_terms"]


def test_filter_specs_membership_accepts_product_type_and_brand(db):
    """AC-1307: `filter_specs` membership widens beside `class` to `product_type` and
    `brand` - today only a `class` spec entry defines membership, so a `product_type` /
    `brand` entry is silently dropped and the clause stays `None`.
    """
    from sqlalchemy import func

    from app.models.product_spec import ProductSpecifications

    bidet = _product(db, "ZZT-PT-BIDET", "SORENTO BIDET SPRAY")
    shower = _product(db, "ZZT-PT-SHOWER", "SORENTO SHOWER SET")

    def _set_values(product, **extra):
        row = (
            db.query(ProductSpecifications)
            .filter(ProductSpecifications.product_id == product.id)
            .first()
        )
        values = dict(row.values or {}) if row is not None else {}
        for key, value in extra.items():
            values[key] = {"value": value}
        if row is None:
            row = ProductSpecifications(product_id=product.id, values=values, provenance={})
            db.add(row)
        else:
            row.values = values
        db.flush()

    # The brand is the product's own field since #1286 (D2), never a stored spec value.
    from app.models.product import Brand

    sorento = Brand(id=str(uuid.uuid4()), brand_code="ZZT-PT-SRT", brand_name="SORENTO")
    db.add(sorento)
    db.flush()
    bidet.brand_id = sorento.id
    shower.brand_id = sorento.id
    db.flush()

    _set_values(bidet, product_type="bidet")
    _set_values(shower, product_type="shower_set")

    def _count(clause):
        return (
            db.query(func.count(ProductSpecifications.product_id))
            .filter(clause)
            .scalar()
        )

    by_type = filter_specs(db, specs=[{"key": "product_type", "value": "bidet"}])
    assert by_type["clause"] is not None
    assert _count(by_type["clause"]) == 1

    by_brand = filter_specs(db, specs=[{"key": "brand", "value": "SORENTO"}])
    assert by_brand["clause"] is not None
    assert _count(by_brand["clause"]) == 2


def test_resolve_product_set_intersects_given_product_ids_with_legs(db):
    """AC-1308 (ids only): the LOOKUP-matched ids are the described set, intersected
    with the legs like any other described set."""
    a = _product(db, "ZZT-PID-A", "PRODUCT A")
    b = _product(db, "ZZT-PID-B", "PRODUCT B")
    c = _product(db, "ZZT-PID-C", "PRODUCT C")
    _certificate(db, a)
    out = resolve_product_set(db, require={"certificate": True}, product_ids=[a.id, b.id, c.id])
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-PID-A"]


def test_resolve_product_set_unions_product_ids_with_bindings(db):
    """AC-1308 (both): product A is matched ONLY through the given `product_ids`;
    product B is matched ONLY through the class binding; product C is certified but in
    neither and must stay excluded - the described set is a UNION of the two, then
    intersected with the legs."""
    a = _product(db, "ZZT-UN-A", "SORENTO WIDGET A")
    b = _product(db, "ZZT-UN-B", "SORENTO S/STEEL KITCHEN SINK (900X500X200MM)")
    # AC-1339/R15: a category-filed product IS a member of its class now, so C must
    # sit under a DIFFERENT category (Water Closet, not the default Kitchen Sink) or
    # its own category filing would legitimately join the "kitchen sink" set.
    c = _product(db, "ZZT-UN-C", "SORENTO WIDGET C", category="wc")
    for p in (a, b, c):
        _certificate(db, p)

    out = resolve_product_set(
        db,
        require={"certificate": True},
        product_ids=[a.id],
        specs=[{"key": "class", "value": "kitchen sink"}],
    )
    assert out["qualifying_total"] == 2
    codes = {cand["product_code"] for cand in out["candidates"]}
    assert codes == {"ZZT-UN-A", "ZZT-UN-B"}


def test_resolve_product_set_brand_scopes_the_set(db):
    """AC-1306 / D3: a `brand` filter scopes the whole set, applied on top of the
    described set and the legs - two certified kitchen sinks, one Sorento and one
    Cabana, and only the Sorento one qualifies once `brand="SORENTO"` is asked."""
    from app.models.product import Brand

    sorento = Brand(id=str(uuid.uuid4()), brand_code="ZZT-SRT", brand_name="SORENTO")
    cabana = Brand(id=str(uuid.uuid4()), brand_code="ZZT-CAB", brand_name="CABANA")
    db.add_all([sorento, cabana])
    db.flush()

    a = _product(db, "ZZT-BR-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    b = _product(db, "ZZT-BR-B", "CABANA S/STEEL KITCHEN SINK (1000X500X220MM)")
    a.brand_id = sorento.id
    b.brand_id = cabana.id
    db.flush()
    _certificate(db, a)
    _certificate(db, b)

    out = resolve_product_set(
        db,
        require={"certificate": True},
        specs=[{"key": "class", "value": "kitchen sink"}],
        brand="SORENTO",
    )
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-BR-A"]


def test_product_ids_path_does_not_bleed_across_companies(db):
    """AC-1310: the new `product_ids` leg is fail-closed per company exactly like the
    other four - two companies share a product CODE, both certified, and a
    `product_ids` list naming both uuids must still qualify only the caller's own
    company's row."""
    other = Company(id=str(uuid.uuid4()), code="ZZT-MC2", name="ZZT Mocha 2")
    db.add(other)
    db.flush()

    ours = _product(db, "ZZT-DUP", "SORENTO WIDGET")
    _certificate(db, ours)

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-DUP", "MOCHA WIDGET")
        _certificate(db, theirs)

    # Session scope is Sorento (conftest default): `theirs.id` must not qualify even
    # though it was explicitly named in `product_ids`.
    out = resolve_product_set(db, require={"certificate": True}, product_ids=[ours.id, theirs.id])
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-DUP"]


# --------------------------------------------------------------------------- #
# Attribute-first asks S2 (PLAN-attribute-first-asks.md, D1/D2/D3)             #
# --------------------------------------------------------------------------- #


def _shipment(db, *, arrived: bool):
    row = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=f"ZZT-{uuid.uuid4().hex[:8]}",
        shipment_date=_utc_today(),
        actual_arrival_date=_utc_today() if arrived else None,
    )
    db.add(row)
    db.flush()
    return row


def _shipment_line(db, shipment, product, *, shipped, received):
    row = InboundShipmentLine(
        id=str(uuid.uuid4()),
        shipment_id=shipment.id,
        product_id=product.id,
        quantity_shipped=shipped,
        quantity_received=received,
    )
    db.add(row)
    db.flush()
    return row


def test_incoming_leg_counts_open_lines_on_unarrived_shipments(db):
    """AC-1311: EXISTS inbound_shipment_lines JOIN inbound_shipments (scoped) WHERE
    shipped minus received > 0 AND the shipment has not yet actually arrived. Today
    `REQUIRE_LEGS` has no "incoming" entry at all, so this 422s ("Unknown require
    key(s): incoming") rather than returning a dict - the right red reason for a leg
    that does not exist yet.
    """
    p1 = _product(db, "ZZT-INC-P1", "SORENTO ITEM P1")
    p2 = _product(db, "ZZT-INC-P2", "SORENTO ITEM P2")
    p3 = _product(db, "ZZT-INC-P3", "SORENTO ITEM P3")

    shipment_a = _shipment(db, arrived=False)
    _shipment_line(db, shipment_a, p1, shipped=10, received=0)
    _shipment_line(db, shipment_a, p2, shipped=5, received=5)

    shipment_b = _shipment(db, arrived=True)
    _shipment_line(db, shipment_b, p3, shipped=8, received=0)

    out = resolve_product_set(db, require={"incoming": True})
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-INC-P1"]


def test_incoming_leg_does_not_bleed_across_companies(db):
    """AC-1310: the new `incoming` leg is fail-closed per company exactly like the
    other four (`test_no_leg_bleeds_across_companies`, extended per
    PLAN-attribute-first-asks.md). `InboundShipment`/`InboundShipmentLine` are
    ordinary `CompanyScopedMixin` models (not `__company_shared__`), so the
    before_insert auto-stamp scopes them to `other.id` for free inside the context
    manager."""
    other = Company(id=str(uuid.uuid4()), code="ZZT-MC3", name="ZZT Mocha 3")
    db.add(other)
    db.flush()

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-SINK-INC", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
        shipment = _shipment(db, arrived=False)
        _shipment_line(db, shipment, theirs, shipped=10, received=0)

    out = _totals(db, {"incoming": True}, ["kitchen sink"])
    assert out["qualifying_total"] == 0
    assert out["candidates"] == []


def test_attachment_type_label_resolves_through_the_alias_lookup_set(db):
    """AC-1312: `attachment_type` resolution falls through to the
    `attachment_type_alias` lookup set (keyword "photo" -> option "Product Photos")
    when the raw does not exact-match a code or type_name directly. Today
    `_leg_attachment_type` never reads a lookup set at all, so "photo" is reported
    unrecognized and qualifying_total is 0, not 1 - the wrong VALUE, the right red
    reason."""
    lookup_set = LookupSet(
        id=str(uuid.uuid4()), tenant_id=None, set_key="attachment_type_alias",
        name="Attachment Type Alias", is_active=True,
    )
    db.add(lookup_set)
    db.flush()
    option = LookupOption(
        id=str(uuid.uuid4()), set_id=lookup_set.id, value="Product Photos",
        label="Product Photos", is_active=True,
    )
    db.add(option)
    db.flush()
    db.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword="photo", locale=None))
    db.flush()

    at = _attachment_type(db, "Product Photos")
    with_photo = _product(db, "ZZT-PHOTO-A", "SORENTO ITEM WITH PHOTO")
    _product(db, "ZZT-PHOTO-B", "SORENTO ITEM WITHOUT PHOTO")
    _attach(db, with_photo, at)

    out = resolve_product_set(db, require={"attachment_type": "photo"})
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-PHOTO-A"]
    assert out["require"]["attachment_type"] == "Product Photos"


def test_attachment_type_label_resolves_through_the_type_description(db):
    """ATTACHMENT-MULTI tester re-run (2 Oct 2026, step 5): with no alias row, "photo"
    reached the HAS leg and was answered "I don't know 'photo' as a document type", while
    the entity resolver reads the same word as Product Photos off the type's DESCRIPTION
    (dev's own wording). The leg reads it the same way - but only among the types products
    actually carry, so an internal class that mentions the word never wins."""
    photos = _attachment_type(db, "Product Photos")
    photos.description = "Product Photos, Photo, Image, Pictures by Marketing"
    internal = _attachment_type(db, "Shipment Line")
    internal.description = "Shipment line photo taken at receiving"
    db.flush()
    with_photo = _product(db, "ZZT-DESC-A", "SORENTO ITEM WITH PHOTO")
    _product(db, "ZZT-DESC-B", "SORENTO ITEM WITHOUT PHOTO")
    _attach(db, with_photo, photos)

    out = resolve_product_set(db, require={"attachment_type": "photo"})

    assert out.get("unrecognized_terms") in (None, []), out.get("unrecognized_terms")
    assert out["require"]["attachment_type"] == "Product Photos"
    assert [cand["product_code"] for cand in out["candidates"]] == ["ZZT-DESC-A"]


def test_attachment_type_unknown_word_is_unrecognized_with_empty_or_missing_set(db):
    """AC-1312 / AC-1314: an alias word that resolves through NEITHER an empty
    `attachment_type_alias` set NOR a missing one is reported unrecognized, never a
    500, in either state."""
    lookup_set = LookupSet(
        id=str(uuid.uuid4()), tenant_id=None, set_key="attachment_type_alias",
        name="Attachment Type Alias", is_active=True,
    )
    db.add(lookup_set)
    db.flush()

    out_empty_set = resolve_product_set(db, require={"attachment_type": "gambar2"})
    assert out_empty_set["qualifying_total"] == 0
    assert out_empty_set["unrecognized_terms"] == ["gambar2"]

    db.query(LookupSet).filter(LookupSet.set_key == "attachment_type_alias").delete()
    db.flush()

    out_missing_set = resolve_product_set(db, require={"attachment_type": "gambar2"})
    assert out_missing_set["qualifying_total"] == 0
    assert out_missing_set["unrecognized_terms"] == ["gambar2"]


def _scheme_seed(db):
    """Two certified products, schemes PPS and SPAN, plus a `certificate_scheme`
    lookup set carrying only the PPS option/keyword - SPAN stays reachable only by
    its own exact spelling, matching the register (owner enters options by hand)."""
    pps_product = _product(db, "ZZT-SCHEME-PPS", "SORENTO CERTIFIED ITEM PPS")
    span_product = _product(db, "ZZT-SCHEME-SPAN", "SORENTO CERTIFIED ITEM SPAN")
    _certificate(db, pps_product, scheme="PPS")
    _certificate(db, span_product, scheme="SPAN")

    lookup_set = LookupSet(
        id=str(uuid.uuid4()), tenant_id=None, set_key="certificate_scheme",
        name="Certificate Scheme", is_active=True,
    )
    db.add(lookup_set)
    db.flush()
    option = LookupOption(
        id=str(uuid.uuid4()), set_id=lookup_set.id, value="PPS", label="PPS", is_active=True,
    )
    db.add(option)
    db.flush()
    db.add(LookupOptionKeyword(id=str(uuid.uuid4()), option_id=option.id, keyword="pps scheme", locale=None))
    db.flush()
    return pps_product, span_product


def test_certificate_scheme_normalises_through_the_lookup_set(db):
    """AC-1313: "pps scheme" normalises through the `certificate_scheme` lookup set
    to the register's own spelling "PPS" before equality. Today `_leg_certificate`
    lower-cases and compares the RAW value verbatim against `Certificate.scheme`, so
    "pps scheme" != "pps" and qualifying_total is 0, not 1 - the wrong value."""
    _scheme_seed(db)

    out = resolve_product_set(db, require={"certificate": {"scheme": "pps scheme"}})
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-SCHEME-PPS"]
    assert out["require"]["certificate"]["scheme"] == "PPS"


def test_certificate_unknown_scheme_returns_schemes_on_file(db):
    """AC-1313: a scheme word with no matching option (or keyword) qualifies
    nothing, reports itself unrecognized, and lists the register's distinct
    schemes so the reply can name them. Today `_leg_certificate` neither reports
    "watermark" as unrecognized (it silently filters to zero via a plain SQL
    comparison) nor emits a `schemes_on_file` key at all - both assertions below
    fail on the un-implemented leg."""
    _scheme_seed(db)

    out = resolve_product_set(db, require={"certificate": {"scheme": "watermark"}})
    assert out["qualifying_total"] == 0
    assert "watermark" in out["unrecognized_terms"]
    assert out["schemes_on_file"] == ["PPS", "SPAN"]


def test_schemes_on_file_is_company_scoped(db):
    """AC-1313 / AC-1310: a certificate scheme belonging to a DIFFERENT company must
    never appear in `schemes_on_file`. `Certificate` is `__company_shared__` (a NULL
    `company_id` is a deliberately SHARED row, visible under every scope, per the
    model's own docstring) so the other company's WCM certificate is stamped with an
    EXPLICIT `company_id` - leaving it unset would leak it into Sorento's list
    regardless of `company_scope`."""
    _scheme_seed(db)

    other = Company(id=str(uuid.uuid4()), code="ZZT-SCHCO", name="ZZT Scheme Co")
    db.add(other)
    db.flush()

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-SCHEME-WCM", "MOCHA CERTIFIED ITEM WCM")
        cert = Certificate(
            id=str(uuid.uuid4()),
            scheme="WCM",
            certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}",
            status="active",
            company_id=other.id,
        )
        db.add(cert)
        db.flush()
        db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=theirs.id))
        db.flush()

    out = resolve_product_set(db, require={"certificate": {"scheme": "watermark"}})
    assert out["qualifying_total"] == 0
    assert "WCM" not in out["schemes_on_file"]
    assert out["schemes_on_file"] == ["PPS", "SPAN"]


# --------------------------------------------------------------------------- #
# Security review (11 Sep 2026, PLAN-attribute-first-asks.md SEC-S1/S2,       #
# AC-1334/AC-1335)                                                             #
# --------------------------------------------------------------------------- #


def test_promotion_leg_respects_access_levels(db):
    """AC-1334/SEC-S1: the promotion leg must intersect `Promotion.access_levels`
    with the caller's OWN tier(s), the same name -> code translation
    `references.py`'s `_apply_promotion_access_levels_filter` already uses
    (`ContactAccessType.name` case-insensitive -> `.code`) - a promotion the
    contact's tier cannot see must never count toward `qualifying_total` nor
    name its product.

    World: two class-Tap products, each in its own active promotion - product A's
    promotion open to access code X, product B's restricted to code Y.

    RED: `resolve_product_set` has no `access_levels` parameter at all today, so
    `resolve_product_set(require={"promotion": True}, access_levels=[...])`
    raises `TypeError` before any filtering logic runs - `_leg_promotion` reads
    only `PromotionProduct` / `Promotion.is_active` / the date window, never the
    caller's tier.
    """
    from app.models.access import ContactAccessType

    db.add(ContactAccessType(code="zzt_code_x", name="ZZT Level X"))
    db.add(ContactAccessType(code="zzt_code_y", name="ZZT Level Y"))
    db.flush()

    product_a = _product(db, "ZZT-PROMO-A", "SORENTO CHROME TAP A")
    product_b = _product(db, "ZZT-PROMO-B", "SORENTO CHROME TAP B")

    promo_a = Promotion(
        id=str(uuid.uuid4()), description="ZZT promo A", is_active=True, access_levels=["zzt_code_x"]
    )
    db.add(promo_a)
    db.flush()
    group_a = PromotionGroup(id=uuid.uuid4(), promotion_id=promo_a.id, group_name="G")
    db.add(group_a)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=promo_a.id, promotion_group_id=group_a.id, product_id=product_a.id
        )
    )

    promo_b = Promotion(
        id=str(uuid.uuid4()), description="ZZT promo B", is_active=True, access_levels=["zzt_code_y"]
    )
    db.add(promo_b)
    db.flush()
    group_b = PromotionGroup(id=uuid.uuid4(), promotion_id=promo_b.id, group_name="G")
    db.add(group_b)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=promo_b.id, promotion_group_id=group_b.id, product_id=product_b.id
        )
    )
    db.flush()

    out = resolve_product_set(db, require={"promotion": True}, access_levels=["ZZT Level X"])
    assert out["qualifying_total"] == 1
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-PROMO-A"]

    out_no_tier = resolve_product_set(db, require={"promotion": True}, access_levels=None)
    assert out_no_tier["qualifying_total"] == 2


def test_an_unread_word_offers_no_class_labels_so_none_leak_across_companies(db):
    """AC-1335/SEC-S2: a class label that exists solely in another company must
    never reach a company A reply. Fix round 10 on PR #833 (owner, 28 Sep 2026: "for
    #833 yeah exact only"): `_common_class_labels` and `_nearest_class_labels` are
    retired, so a zero for an unread word offers no class label at all, and this
    pins that the near-spelling of company B's label comes back with none.

    World: company B (Mocha, via `tests/_mc_lookup_seed.seed_mocha`) owns a
    product whose derived spec class is the nonsense label "Zzqsecretclass" -
    unique enough that its presence can only be explained by a cross-company
    leak, never a coincidence.

    RED: `ProductSpecifications` carries no `company_id` at all (not a
    `CompanyScopedMixin` model) and neither helper joins `Product` (the scoped
    side) to filter by it - `_common_class_labels` groups the whole table
    unconditionally, and `_nearest_class_labels`'s own `stored_class_labels` runs
    raw `text()` SQL with no company filter whatsoever - so under company A's
    scope both still see company B's secret class label.
    """
    from tests._mc_lookup_seed import seed_mocha
    from app.models.base import set_company_scope
    from app.models.product_spec import ProductSpecifications
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from app.services.product_predicate_service import resolve_product_set

    mocha = seed_mocha(db)
    with company_scope(db, frozenset({mocha.id})):
        secret_product = _product(db, "ZZT-MCH-SECRET", "MOCHA SECRET ITEM")
        # `_product` already ran `derive_for_code`, which inserted the row (the
        # description names no known class, so it derived nothing) - UPDATE the
        # existing row rather than inserting a second one under the same
        # `product_id` (a real UNIQUE constraint), which is exactly how a
        # deterministic class derivation would have written it in production.
        spec_row = (
            db.query(ProductSpecifications)
            .filter(ProductSpecifications.product_id == secret_product.id)
            .one()
        )
        spec_row.values = {"class": {"value": "Zzqsecretclass"}}
        db.flush()
    db.commit()

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    out = resolve_product_set(db, require={"stock": True}, scope_terms=["zzqsecretclas"])
    assert out["qualifying_total"] == 0, out
    assert "suggestions" not in out and "common_class_labels" not in out, out
    assert "Zzqsecretclass" not in repr(out), out


# --------------------------------------------------------------------------- #
# Correctness review (11 Sep 2026, PLAN-attribute-first-asks.md REV-B1,       #
# AC-1310 rewritten) - `do_orm_execute`'s `with_loader_criteria` scopes ORM    #
# entities, but each leg's `exists().where(...)` references its child table   #
# as a bare correlated subquery, which the listener does not reach. The OLD   #
# cross-company tests seeded BOTH the product and the child under the other  #
# company, so the outer (correctly-scoped) `Product` filter alone produced    #
# the zero - proving nothing about the leg's own subquery. These pairs put    #
# the product and the child in DIFFERENT companies so only the leg's own     #
# scoping (or its absence) can explain the count.                            #
# --------------------------------------------------------------------------- #


def test_stock_leg_respects_company_scope_in_both_directions(db):
    """AC-1310 (rewritten, REV-B1 kill test).

    (a) product in the caller's company, Stock row's OWN `company_id` stamped
        to another company -> qualifying_total 0.
    (b) the reverse: Stock row in the caller's company, product in another
        company -> 0 (the outer Product filter's own job).

    RED (a): `_leg_stock`'s `exists().where(Stock.product_id == Product.id,
    Stock.quantity_on_hand > 0)` carries no `Stock.company_id ==
    Product.company_id` predicate, so a Stock row stamped to ANY company
    counts once its `product_id` matches - `qualifying_total` is 1, not 0.
    """
    other = Company(id=str(uuid.uuid4()), code="ZZT-REVB1-STK", name="ZZT Rev Stock Co")
    db.add(other)
    db.flush()

    ours = _product(db, "ZZT-REV-STK-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    warehouse_a = _warehouse(db)
    db.add(
        Stock(
            id=str(uuid.uuid4()),
            product_id=ours.id,
            warehouse_id=warehouse_a.id,
            quantity_on_hand=5,
            quantity_reserved=0,
            quantity_damaged=0,
            company_id=other.id,
        )
    )
    db.flush()
    out_a = _totals(db, {"stock": True}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-REV-STK-B", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
    _stock(db, theirs, 5)
    out_b = _totals(db, {"stock": True}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 0, out_b


def test_attachment_type_leg_respects_company_scope_in_both_directions(db):
    """AC-1310 (rewritten, REV-B1 kill test): the child that must carry the
    same-company predicate is `ProductAttachment` (the link row) - `Attachment`
    itself is `__company_shared__` (nullable, shared) and is not the isolation
    boundary here.

    (a) product in the caller's company, `ProductAttachment.company_id`
        stamped to another company -> 0.
    (b) the reverse -> 0.

    RED (a): `_leg_attachment_type`'s exists() joins `ProductAttachment` /
    `Attachment` with no `ProductAttachment.company_id == Product.company_id`
    predicate, so the foreign-stamped link still counts.
    """
    other = Company(id=str(uuid.uuid4()), code="ZZT-REVB1-ATT", name="ZZT Rev Attach Co")
    db.add(other)
    db.flush()
    at = _attachment_type(db, "Technical Drawing")

    ours = _product(db, "ZZT-REV-ATT-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    att = Attachment(
        id=str(uuid.uuid4()),
        original_filename="zzt.pdf",
        stored_filename="zzt.pdf",
        file_path="https://cdn/zzt.pdf",
        attachment_type_id=at.id,
    )
    db.add(att)
    db.flush()
    db.add(
        ProductAttachment(
            id=str(uuid.uuid4()), product_id=ours.id, attachment_id=att.id, company_id=other.id
        )
    )
    db.flush()
    out_a = _totals(db, {"attachment_type": "technical drawing"}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-REV-ATT-B", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
    _attach(db, theirs, at)
    out_b = _totals(db, {"attachment_type": "technical drawing"}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 0, out_b


def test_incoming_leg_respects_company_scope_in_both_directions(db):
    """AC-1310 (rewritten, REV-B1 kill test): the child is `InboundShipmentLine`
    via its `InboundShipment` parent.

    (a) product in the caller's company, shipment (+ line) stamped to another
        company -> 0.
    (b) the reverse -> 0.

    RED (a): `_leg_incoming`'s exists() joins `InboundShipmentLine` /
    `InboundShipment` with no same-company predicate against `Product`, so a
    foreign-stamped open shipment line still counts - this is the exact "kill
    test: an unscoped raw-SQL incoming leg stayed green" the reviewer named.
    """
    other = Company(id=str(uuid.uuid4()), code="ZZT-REVB1-INC", name="ZZT Rev Incoming Co")
    db.add(other)
    db.flush()

    ours = _product(db, "ZZT-REV-INC-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    with company_scope(db, frozenset({other.id})):
        shipment_a = _shipment(db, arrived=False)
        _shipment_line(db, shipment_a, ours, shipped=10, received=0)
    out_a = _totals(db, {"incoming": True}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-REV-INC-B", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
    shipment_b = _shipment(db, arrived=False)
    _shipment_line(db, shipment_b, theirs, shipped=10, received=0)
    out_b = _totals(db, {"incoming": True}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 0, out_b


def test_promotion_leg_respects_company_scope_in_both_directions(db):
    """AC-1310 (rewritten, REV-B1 kill test): the child is `PromotionProduct`
    via its `Promotion` parent.

    (a) product in the caller's company, promotion (+ link) stamped to another
        company -> 0.
    (b) the reverse -> 0.

    RED (a): `_leg_promotion`'s exists() joins `PromotionProduct` / `Promotion`
    with no same-company predicate against `Product`, so a foreign-stamped
    active promotion still counts.
    """
    other = Company(id=str(uuid.uuid4()), code="ZZT-REVB1-PROMO", name="ZZT Rev Promo Co")
    db.add(other)
    db.flush()

    ours = _product(db, "ZZT-REV-PROMO-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    with company_scope(db, frozenset({other.id})):
        _promotion(db, ours)
    out_a = _totals(db, {"promotion": True}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-REV-PROMO-B", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
    _promotion(db, theirs)
    out_b = _totals(db, {"promotion": True}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 0, out_b



# --------------------------------------------------------------------------- #
# Fix round 3 (11 Sep 2026, PLAN-attribute-first-asks.md R14, AC-1338(d)) -   #
# unit test for the new `recover_certificate_scheme` helper.                   #
# --------------------------------------------------------------------------- #


def test_recover_certificate_scheme_promotes_bare_true_and_consumes_the_word(db):
    """AC-1338/R14(d): `recover_certificate_scheme(db, require, words)` must
    promote a BARE `{"certificate": True}` require to `{"certificate":
    {"scheme": "PPS"}}` when `words` carries a case-insensitive match against
    the register's own active scheme spelling, returning the word CONSUMED (so
    the caller can drop it from the described-set remainder); a non-bare
    require (already an object) is returned UNCHANGED with nothing consumed.

    RED: `recover_certificate_scheme` does not exist in
    `app.services.product_predicate_service` yet - `ImportError`.
    """
    from app.services.product_predicate_service import recover_certificate_scheme

    certified = _product(db, "ZZT-R14-PPS", "SORENTO ITEM WITH CERT")
    cert = Certificate(
        id=str(uuid.uuid4()), scheme="PPS", certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}", status="active"
    )
    db.add(cert)
    db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert.id, product_id=certified.id))
    db.flush()

    require, consumed = recover_certificate_scheme(db, {"certificate": True}, ["item", "pps"])
    assert require == {"certificate": {"scheme": "PPS"}}, require
    assert consumed == ["pps"], consumed

    require_unchanged, consumed_none = recover_certificate_scheme(
        db, {"certificate": {"scheme": "PPS"}}, ["item", "pps"]
    )
    assert require_unchanged == {"certificate": {"scheme": "PPS"}}, require_unchanged
    assert consumed_none == [], consumed_none

    require_other_key, consumed_other = recover_certificate_scheme(db, {"stock": True}, ["item", "pps"])
    assert require_other_key == {"stock": True}, require_other_key
    assert consumed_other == [], consumed_other


# --------------------------------------------------------------------------- #
# Fix round 3 - R15, AC-1339: `filter_specs` class membership must include a  #
# category-sourced class row, not only a description-derived one.             #
# --------------------------------------------------------------------------- #


def test_filter_specs_class_membership_includes_category_sourced_rows(db):
    """AC-1339/R15: a product filed under a class SOLELY through its category
    (provenance.class.source == "category") is still a member of the described
    set for that class - the company's own filing IS a real claim about what
    the product is, the same standing as a class the description itself named.

    RED: `filter_specs`'s class-membership clause ANDs in
    `provenance.class.source IS DISTINCT FROM 'category'`, which drops the
    category-sourced row - `codes` is `{"ZZT-BA-DER"}`, not both.
    """
    from app.models.product_spec import ProductSpecifications

    category_sourced = _product(db, "ZZT-BA-CAT", "ZZT GENERIC ACCESSORY ITEM")
    derived = _product(db, "ZZT-BA-DER", "ZZT BATHROOM ACCESSORY HOLDER")

    cat_spec = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == category_sourced.id).one()
    cat_spec.values = {"class": {"value": "Bathroom Accessory"}}
    cat_spec.provenance = {"class": {"source": "category"}}
    db.flush()

    der_spec = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == derived.id).one()
    der_spec.values = {"class": {"value": "Bathroom Accessory"}}
    der_spec.provenance = {"class": {"source": "derived"}}
    db.flush()

    verdict = filter_specs(db, specs=[{"key": "class", "value": "Bathroom Accessory"}])
    assert verdict["clause"] is not None, verdict

    rows = (
        db.query(Product.product_code)
        .join(ProductSpecifications, ProductSpecifications.product_id == Product.id)
        .filter(verdict["clause"])
        .all()
    )
    codes = {row[0] for row in rows}
    assert codes == {"ZZT-BA-CAT", "ZZT-BA-DER"}, codes


def test_resolve_product_set_counts_a_category_sourced_class_with_stock(db):
    """AC-1339/R15: `resolve_product_set(require={"stock": True}, scope_terms=
    ["bathroom accessory"])` must count a product whose only class evidence is
    its category filing - the same "which bathroom accessory has stock" turn
    measured live (2,040 products carry the class solely through category
    filing, 999 of them with stock, all reported as qualifying 0 today).

    RED: the class-membership clause's category-provenance exclusion drops the
    row from the described set entirely, so `qualifying_total` is 0, not 1.
    """
    from app.models.product_spec import ProductSpecifications

    product = _product(db, "ZZT-BA-STOCK", "ZZT GENERIC ACCESSORY ITEM")
    spec = db.query(ProductSpecifications).filter(ProductSpecifications.product_id == product.id).one()
    spec.values = {"class": {"value": "Bathroom Accessory"}}
    spec.provenance = {"class": {"source": "category"}}
    db.flush()
    _stock(db, product, 5)

    out = resolve_product_set(db, require={"stock": True}, scope_terms=["bathroom accessory"])
    assert out["qualifying_total"] == 1, out


# --------------------------------------------------------------------------- #
# Fix round 3 - R17/AC-1341 (REV-B1 re-check): the certificate leg is the ONE  #
# leg REV-B1 left without a same-company predicate.                           #
# --------------------------------------------------------------------------- #


def test_certificate_leg_respects_company_scope_in_both_directions(db):
    """AC-1341/R17 (REV-B1 re-check): `_leg_certificate`'s exists() join
    through `CertificateProduct`/`Certificate` carries no same-company
    predicate against `Product` at all. `Certificate` is `__company_shared__`
    (a NULL `company_id` is a deliberately SHARED row), so the fix must keep
    that NULL arm counting while still excluding a certificate stamped to a
    DIFFERENT company.

    (a) product in the caller's company, Certificate stamped to another
        company -> 0.
    (b) the reverse (product in another company, certificate in the caller's
        own scope) -> 0 (the outer Product filter's own job).
    (c) shared arm: a Certificate with company_id NULL linked to the caller's
        own product still counts -> 1.

    RED (a): measured live, `resolve_product_set(require={"certificate":
    True})` under company A counts a certificate stamped to company B and
    linked to A's product - `qualifying_total` is 1, not 0.
    """
    other = Company(id=str(uuid.uuid4()), code="ZZT-REVB1-CERT", name="ZZT Rev Cert Co")
    db.add(other)
    db.flush()

    ours = _product(db, "ZZT-REV-CERT-A", "SORENTO S/STEEL KITCHEN SINK (1000X500X220MM)")
    cert_other = Certificate(
        id=str(uuid.uuid4()), scheme="ZZT-SIRIM", certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}",
        status="active", company_id=other.id,
    )
    db.add(cert_other)
    db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert_other.id, product_id=ours.id))
    db.flush()
    out_a = _totals(db, {"certificate": True}, ["kitchen sink"])
    assert out_a["qualifying_total"] == 0, out_a

    with company_scope(db, frozenset({other.id})):
        theirs = _product(db, "ZZT-REV-CERT-B", "MOCHA S/STEEL KITCHEN SINK (900X500X200MM)")
    cert_ours = Certificate(
        id=str(uuid.uuid4()), scheme="ZZT-SIRIM", certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}", status="active"
    )
    db.add(cert_ours)
    db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert_ours.id, product_id=theirs.id))
    db.flush()
    out_b = _totals(db, {"certificate": True}, ["kitchen sink"])
    assert out_b["qualifying_total"] == 0, out_b

    shared_product = _product(db, "ZZT-REV-CERT-C", "SORENTO S/STEEL KITCHEN SINK (700X400X180MM)")
    cert_shared = Certificate(
        id=str(uuid.uuid4()), scheme="ZZT-SIRIM", certificate_number=f"ZZT-{uuid.uuid4().hex[:8]}",
        status="active", company_id=None,
    )
    db.add(cert_shared)
    db.flush()
    db.add(CertificateProduct(id=str(uuid.uuid4()), certificate_id=cert_shared.id, product_id=shared_product.id))
    db.flush()
    out_c = _totals(db, {"certificate": True}, ["kitchen sink"])
    assert out_c["qualifying_total"] == 1, out_c
    codes_c = [cand["product_code"] for cand in out_c["candidates"]]
    assert codes_c == ["ZZT-REV-CERT-C"], out_c


# --------------------------------------------------------------------------- #
# Fix round 3 - R18/AC-1342 (SEC-S1 re-check): `_access_level_codes` must also #
# accept a bare TIER TOKEN (dealer/office/end_user), not only a               #
# `contact_access_types.name`.                                                #
# --------------------------------------------------------------------------- #


def test_access_level_codes_expands_a_bare_tier_token(db):
    """AC-1342/R18: `_access_level_codes` must accept a `contact_access_types.
    code` value directly and a BARE TIER TOKEN that is not itself any single
    code ("dealer") - the parsed head's own recomposed tier vocabulary - by
    matching every code that IS the token or ENDS WITH `_<token>`, read off
    the table with no list in code. A name still translates as before; an
    unknown value still yields the empty set; an empty list is no restriction
    (`None`).

    RED: `_access_level_codes` only equality-matches `access_levels` against
    `ContactAccessType.name` - "dealer" (a CODE-shaped tier token, not a name)
    matches no row's `name` at all, so `{"dealer"} -> set()`, not the two codes
    this AC demands.
    """
    from app.models.access import ContactAccessType
    from app.services.product_predicate_service import _access_level_codes

    db.add_all(
        [
            ContactAccessType(code="dealer", name="Sorento Dealer"),
            ContactAccessType(code="cabana_dealer", name="Cabana Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()

    assert _access_level_codes(db, ["dealer"]) == {"dealer", "cabana_dealer"}
    assert _access_level_codes(db, ["office"]) == {"sorento_office"}
    assert _access_level_codes(db, ["Sorento Dealer"]) == {"dealer"}
    assert _access_level_codes(db, ["zzq"]) == set()
    assert _access_level_codes(db, []) is None


def test_access_level_codes_treats_wildcard_characters_literally(db):
    """AC-1348/R24 (both reviewers, should-fix): the bare-tier-token suffix
    match interpolates the caller's own value straight into a SQL LIKE pattern
    with no escaping - a value that itself contains `%`, `_` or `\\` must still
    be read as its LITERAL spelling, never as a wildcard that widens the
    match.

    RED: measured live, `_access_level_codes(db, ["%"])` returns SEVEN of the
    eight seeded codes (every one carrying an underscore, since the
    interpolated "%" makes the pattern `%\\_%`, "any code with an underscore in
    it") instead of the empty set this AC demands. `["%dealer"]` and
    `["d%r"]` must also select nothing - neither is any seeded code's literal
    spelling.
    """
    from app.models.access import ContactAccessType
    from app.services.product_predicate_service import _access_level_codes

    db.add_all(
        [
            ContactAccessType(code="dealer", name="Sorento Dealer"),
            ContactAccessType(code="cabana_dealer", name="Cabana Dealer"),
            ContactAccessType(code="mocha_dealer", name="Mocha Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
            ContactAccessType(code="cabana_office", name="Cabana Office"),
            ContactAccessType(code="end_user", name="End User"),
            ContactAccessType(code="sorento_end_user", name="Sorento End User"),
        ]
    )
    db.flush()

    assert _access_level_codes(db, ["%"]) == set(), _access_level_codes(db, ["%"])
    assert _access_level_codes(db, ["%dealer"]) == set(), _access_level_codes(db, ["%dealer"])
    assert _access_level_codes(db, ["d%r"]) == set(), _access_level_codes(db, ["d%r"])
    # The ordinary bare-token contract is unaffected by the escaping fix.
    assert _access_level_codes(db, ["dealer"]) == {"dealer", "cabana_dealer", "mocha_dealer"}


def test_promotion_leg_respects_a_bare_tier_token(db):
    """AC-1342/R18: `resolve_product_set`'s promotion leg must resolve a bare
    TIER TOKEN the same way `_access_level_codes` does - "dealer" must count a
    promotion restricted to "cabana_dealer" (a brand-qualified dealer code, no
    name equal to "dealer" itself), and "office" must count NONE of it.

    RED: `_access_level_codes` only equality-matches `access_levels` against
    `ContactAccessType.name`; "dealer" translates to an EMPTY set and the
    promotion counts ZERO, not the 1 this AC demands.
    """
    from app.models.access import ContactAccessType

    db.add_all(
        [
            ContactAccessType(code="cabana_dealer", name="Cabana Dealer"),
            ContactAccessType(code="sorento_office", name="Sorento Office"),
        ]
    )
    db.flush()

    product = _product(db, "ZZT-TIER-A", "SORENTO CHROME TAP A")
    promo = Promotion(
        id=str(uuid.uuid4()), description="ZZT promo tier", is_active=True, access_levels=["cabana_dealer"]
    )
    db.add(promo)
    db.flush()
    group = PromotionGroup(id=uuid.uuid4(), promotion_id=promo.id, group_name="G")
    db.add(group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=promo.id, promotion_group_id=group.id, product_id=product.id
        )
    )
    db.flush()

    out_dealer = resolve_product_set(db, require={"promotion": True}, access_levels=["dealer"])
    assert out_dealer["qualifying_total"] == 1, out_dealer

    out_office = resolve_product_set(db, require={"promotion": True}, access_levels=["office"])
    assert out_office["qualifying_total"] == 0, out_office


# --------------------------------------------------------------------------- #
# Round 3 re-check (R25, AC-1349): the resolver-level half of the contract -   #
# a stated tier NAME must count only the promotion restricted to its OWN      #
# code, never an unrelated tier's.                                            #
# --------------------------------------------------------------------------- #


def test_resolve_product_set_counts_only_the_stated_tiers_promotion(db):
    """AC-1349/R25: `resolve_product_set(require={"promotion": True},
    access_levels=["Sorento Dealer"])` must count ONLY the promotion
    restricted to the "dealer" code - a promotion restricted to "end_user"
    must never count toward a dealer's own `qualifying_total`.

    This is the resolver-level half of the contract `resolve_entity_body`
    must now feed with the tier gate's own recomposed names rather than the
    parser's bare tokens; the leg itself (`_access_level_codes` / `_leg_
    promotion`) already translates a NAME correctly, so this may already be
    green - kept as the pinned contract regardless, per the captain's
    instruction.
    """
    from app.models.access import ContactAccessType

    db.add_all(
        [
            ContactAccessType(code="dealer", name="Sorento Dealer"),
            ContactAccessType(code="end_user", name="End User"),
        ]
    )
    db.flush()

    dealer_product = _product(db, "ZZT-TIER-DEALER", "SORENTO CHROME TAP DEALER")
    dealer_promo = Promotion(
        id=str(uuid.uuid4()), description="ZZT promo dealer", is_active=True, access_levels=["dealer"]
    )
    db.add(dealer_promo)
    db.flush()
    dealer_group = PromotionGroup(id=uuid.uuid4(), promotion_id=dealer_promo.id, group_name="G")
    db.add(dealer_group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=dealer_promo.id, promotion_group_id=dealer_group.id,
            product_id=dealer_product.id,
        )
    )

    end_user_product = _product(db, "ZZT-TIER-ENDUSER", "SORENTO CHROME TAP END USER")
    end_user_promo = Promotion(
        id=str(uuid.uuid4()), description="ZZT promo end user", is_active=True, access_levels=["end_user"]
    )
    db.add(end_user_promo)
    db.flush()
    end_user_group = PromotionGroup(id=uuid.uuid4(), promotion_id=end_user_promo.id, group_name="G")
    db.add(end_user_group)
    db.flush()
    db.add(
        PromotionProduct(
            id=str(uuid.uuid4()), promotion_id=end_user_promo.id, promotion_group_id=end_user_group.id,
            product_id=end_user_product.id,
        )
    )
    db.flush()

    out = resolve_product_set(db, require={"promotion": True}, access_levels=["Sorento Dealer"])
    assert out["qualifying_total"] == 1, out
    codes = [cand["product_code"] for cand in out["candidates"]]
    assert codes == ["ZZT-TIER-DEALER"], codes
