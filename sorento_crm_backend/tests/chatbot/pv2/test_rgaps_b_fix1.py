"""PV2 redesign R-GAPS-b fix round 1 red tests (review findings M1, M2, S1 to S8, SEC1 to SEC3, the refer-line nit).

No model call. DB level (blank Postgres schema, the real `DbResolver` + `apply_turn`) where a resolver fact matters,
fakes otherwise.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.models.base import company_scope, set_company_scope
from app.models.company import Company
from app.models.product import Product
from app.models.product_set import ProductSet, ProductSetMember
from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.schema import Period, Quantity
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, OfferHandover, new_state
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out, spec

PINNED = {"brand": set(), "product_type": {"Water Closet"}, "product_class": {"water_closet"}, "attachment_type": set(),
          "spec_attribute": {"dim_length", "dim_trap"}, "spec_value": set()}
SEVENTH = Period(preset=None, n=None, date_from=date(2026, 10, 7), date_to=date(2026, 10, 7))
OCTOBER = Period(preset=None, n=None, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        S.registry(session, "dim_length", "Length", "numeric", unit="mm")
        S.registry(session, "dim_trap", "Trap outlet length", "numeric", unit="mm")
        yield session


def _ex(kind, key, label=None):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": label or key}]}


def _amb(kind, keys):
    return {"status": "ambiguous", "options": [
        {"key": k, "label": k, "payload": {"kind": kind, "key": k, "label": k}} for k in keys]}


def _pick(key):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _keys(st, kind="product"):
    return sorted(r.key for r in st.held.refs.get(kind, []))


def _qty(st):
    return {r.key: (r.quantity.number if r.quantity else None) for r in st.held.refs.get("product", [])}


def _is_carry(act):
    return isinstance(act, ClarifyAct) and "carry" in act.pending.kind


def _size(n):
    return spec(number=n, unit="mm", phrase=f"{n}mm")


def _turn(db, parsed, state=None, **kw):
    return apply_turn(state or new_state(NOW), parsed, resolver=DbResolver(db, pinned=PINNED), now=NOW, **kw)


def _family(db, family, rows, cat=None):
    cat = cat or S.category(db, class_label="Water Closet")
    for suffix, length, trap in rows:
        p = S.product(db, f"{family}-{suffix}", cat=cat)
        if length is not None:
            S.specs(db, p, klass="Water Closet", dim_length=length, dim_trap=trap)


def _inv(*ents, specs=(), act="new_topic"):
    return out(ask(act=act, domain="inventory", entities=list(ents), specs=list(specs)))


# ------------------------------------------------------------------ M1 / S5 / S6: sizes (RG-2b)

WC = [("A", 250, 180), ("B", 600, 180), ("D", 700, 250)]


@pytest.mark.parametrize("how", ["refine", "new_topic"])
def test_m1_a_follow_up_size_is_checked_against_the_whole_family_not_the_last_sizes_hits(db, how):
    _family(db, "SRTWC286", WC)
    st, _ = _turn(db, _inv(entity("product", phrase="srtwc286"), specs=[_size(250)] if how == "new_topic" else []))
    if how == "refine":
        st, _ = _turn(db, _inv(specs=[_size(250)], act="refines_held"), state=st)
    st, acts = _turn(db, _inv(specs=[_size(600)], act="refines_held"), state=st)
    reply = answer.replay_reply(st, acts)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: {reply}"
    assert "No 600 mm" not in reply
    assert any(k in ("SRTWC286-B", "SRTWC286") for k in _keys(st)), _keys(st)


def test_s5_two_sizes_in_one_ask_narrow_each_other(db):
    _family(db, "SRTWC286", WC)
    st, acts = _turn(db, _inv(entity("product", phrase="srtwc286"), specs=[_size(250), _size(180)]))
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == ["SRTWC286-A"], "A holds 250 and 180; B holds only 180, D only 250"


def test_s6_held_products_with_no_size_rows_get_no_axis_question(db):
    _family(db, "SRTWC999", [("A", None, None), ("B", None, None)])
    _family(db, "SRTWC1", [("A", 250, 100)])
    _family(db, "SRTWC2", [("A", 100, 250)])
    st, acts = _turn(db, _inv(entity("product", phrase="srtwc999"), specs=[_size(250)]))
    assert not (isinstance(acts[0], ClarifyAct) and acts[0].pending.options), "no Length / Trap outlet length question"


# ------------------------------------------------------------------ M2: a repeat is a miss only for the SAME subject

ATT = {"status": "ambiguous", "options": [
    {"key": "drawing", "label": "Drawing", "payload": {"kind": "attachment_type", "key": "drawing"}},
    {"key": "photo", "label": "Photo", "payload": {"kind": "attachment_type", "key": "photo"}}]}
ATTACH = FakeResolver({"entities": {f"product|SRT39{n}": _ex("product", f"SRT39{n}") for n in (1, 2, 3)},
                       "attachment_types": {"manual": ATT}})


def _manual(code):
    return out(ask(act="new_topic", domain="product_attachment", entities=[entity("product", phrase=code)],
                   attachment_type="manual"))


@pytest.mark.parametrize("who", ["staff", "dealer"])
def test_m2_the_same_question_about_three_different_products_is_three_questions_not_a_loop(who):
    can_offer, mode = (False, "none") if who == "staff" else (True, "offer")
    st = new_state(NOW)
    for code in ("SRT391", "SRT392", "SRT393"):
        st, acts = apply_turn(st, _manual(code), resolver=ATTACH, now=NOW, can_offer_handover=can_offer, offer_mode=mode)
        assert isinstance(acts[0], ClarifyAct) and not isinstance(acts[0], OfferHandover), (code, type(acts[0]).__name__)
        assert acts[0].pending.options, f"{code}: its own Drawing / Photo question"
        assert st.pending.misses == 0, f"{code}: a different subject is no miss"


# ------------------------------------------------------------------ carry question: S1 to S4

CARRY = FakeResolver({"entities": {
    "customer|hanlim": _ex("customer", "c-hanlim", "HANLIM"),
    "product|SRT391-GM": _ex("product", "SRT391-GM"),
    "product|SRT392": _ex("product", "SRT392"),
    "product|mou": _amb("product", ["MOU-1", "MOU-2"]),
    "sales_order|SO1": _ex("sales_order", "SO1"),
}})
WHO = pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "dealer"])


def _c(name):
    return entity("customer", phrase=name)


def _p(name):
    return entity("product", phrase=name)


def _t(*ents, act="new_topic", **kw):
    return out(ask(act=act, domain="order", entities=list(ents), **kw))


def _play(*parsed, can_offer=False, state=None):
    st, acts = state or new_state(NOW), None
    for p in parsed:
        st, acts = apply_turn(st, p, resolver=CARRY, now=NOW, can_offer_handover=can_offer)
    return st, acts


@WHO
def test_s1_a_restated_ask_keeps_its_new_details_and_counts_one_miss(can_offer):
    st, acts = _play(_t(_c("hanlim"), period=SEVENTH), _t(_p("SRT391-GM")), can_offer=can_offer)
    assert _is_carry(acts[0])
    st, acts = _play(_t(_p("SRT391-GM"), period=OCTOBER), can_offer=can_offer, state=st)
    assert _is_carry(acts[0]) and st.pending.misses == 1, st.pending.misses
    st, acts = _play(_pick("2"), can_offer=can_offer, state=st)
    assert isinstance(acts[0], Lookup)
    assert st.held.period is not None and st.held.period.date_from == date(2026, 10, 1), "October replaces 7 Oct"


@WHO
def test_s2_a_pick_on_the_carry_question_replays_only_the_carried_item_and_the_period_never_other_held_items(can_offer):
    first = _t(_c("hanlim"), entity("sales_order", phrase="SO1"), period=SEVENTH)
    for pick, customers in (("1", ["c-hanlim"]), ("2", [])):
        st, acts = _play(first, _t(_p("SRT391-GM")), can_offer=can_offer)
        assert _is_carry(acts[0]), type(acts[0]).__name__
        st, acts = _play(_pick(pick), can_offer=can_offer, state=st)
        assert isinstance(acts[0], Lookup)
        assert _keys(st, "product") == ["SRT391-GM"] and _keys(st, "customer") == customers
        assert _keys(st, "sales_order") == [], "a held SO stays out of the replay"


@WHO
def test_s3_the_carry_question_fires_when_the_new_kind_was_held_before_too(can_offer):
    st, acts = _play(_t(_c("hanlim"), _p("SRT391-GM"), period=SEVENTH), _t(_p("SRT392")), can_offer=can_offer)
    assert _is_carry(acts[0]), f"{type(acts[0]).__name__}: HANLIM and the period must not drop silently"


@pytest.mark.parametrize("act", ["new_topic", "refines_held"])
@WHO
def test_s4_a_new_item_that_needs_its_own_question_still_gets_the_carry_question_after_that_pick(can_offer, act):
    st, acts = _play(_t(_c("hanlim"), period=SEVENTH), _t(_p("mou"), act=act), can_offer=can_offer)
    kinds = []
    for _ in range(3):
        if isinstance(acts[0], Lookup):
            break
        kinds.append(acts[0].pending.kind)
        if _is_carry(acts[0]):
            break
        st, acts = _play(_pick("1"), can_offer=can_offer, state=st)
    assert any("carry" in k for k in kinds), f"never asked: {kinds} then {type(acts[0]).__name__}"
    assert not isinstance(acts[0], Lookup)


# ------------------------------------------------------------------ the refer-line nit


def test_the_cap_for_a_barred_contact_puts_the_refer_line_after_a_blank_line():
    abc = FakeResolver({"entities": {"customer|abc": _amb("customer", ["ABC SDN BHD", "ABC TRADING"])}})
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="abc")]))
    st = new_state(NOW)
    for _ in range(3):
        st, acts = apply_turn(st, parsed, resolver=abc, now=NOW, can_offer_handover=False, offer_mode="refer")
    reply = answer.replay_reply(st, acts)
    head, _, rest = reply.partition("I still need")
    assert rest, reply
    sentence, sep, refer = rest.partition("\n\n")
    assert sep and refer.strip(), f"the refer line follows after a blank line: {reply!r}"


# ------------------------------------------------------------------ sets: S7, S8, SEC1 to SEC3


def _set(db, code, members, *, active=True, company=DEFAULT_COMPANY_ID):
    row = ProductSet(id=str(uuid.uuid4()), company_id=company, set_code=code, name=f"Set {code}", is_active=active)
    db.add(row)
    db.flush()
    for i, (product, qty) in enumerate(members):
        db.add(ProductSetMember(id=str(uuid.uuid4()), product_set_id=row.id, product_id=product.id, quantity=qty,
                                sort_order=i))
    db.flush()
    return row


def _prods(db, *codes, cat=None):
    cat = cat or S.category(db, class_label="Water Closet")
    return [S.product(db, c, cat=cat) for c in codes]


def _inv_code(db, phrase, quantity=None, **kw):
    ent = entity("product", phrase=phrase)
    if quantity is not None:
        ent = ent.model_copy(update={"quantity": Quantity(number=quantity, unit=None)})
    return _turn(db, _inv(ent), **kw)


def test_s7_a_base_code_partly_in_sets_offers_the_sets_and_the_variants_in_no_set(db):
    a, b, c = _prods(db, "SRTWC8608-A", "SRTWC8608-B", "SRTWC8608-C")
    _set(db, "SRTWC8608-SET1", [(a, 1), (b, 1)])
    st, acts = _inv_code(db, "SRTWC8608")
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    labels = " | ".join(f"{o.key} {o.label}" for o in acts[0].pending.options)
    assert "SRTWC8608-SET1" in labels and "SRTWC8608-C" in labels, labels


def test_s8_set_members_are_read_company_scoped_by_set_id_and_never_merge_across_companies(db):
    other = Company(id=str(uuid.uuid4()), name="Other Co", code=unique_code("OC")[:50])
    db.add(other)
    db.flush()
    mine = _prods(db, "SRTSETM-1", "SRTSETM-2")
    _set(db, "SRTSETX", [(m, 1) for m in mine])
    with company_scope(db, None):
        cat = S.category(db, class_label="Water Closet")
        theirs = []
        for code in ("SRTSETT-1", "SRTSETT-2"):
            row = Product(id=str(uuid.uuid4()), product_code=code, product_name=code, category_id=cat.id,
                          base_uom_id=S._uom(db), list_price=0, is_active=True, company_id=other.id)
            db.add(row)
            theirs.append(row)
        db.flush()
        _set(db, "SRTSETX", [(t, 1) for t in theirs], company=other.id)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, other.id}))
    st, acts = _inv_code(db, "SRTSETX")
    got = set(_keys(st))
    assert isinstance(acts[0], ClarifyAct) or got in ({"SRTSETM-1", "SRTSETM-2"}, {"SRTSETT-1", "SRTSETT-2"}), (
        f"the two companies' sets merged: {sorted(got)}")


def test_sec1_a_set_is_not_offered_through_a_hidden_or_inactive_family_member(db):
    a, b = _prods(db, "SRTWC8700-A", "SRTWC8700-B")
    b.is_active = False
    b.is_searchable = False
    db.flush()
    _set(db, "SRTWC8700-SET1", [(b, 1)])
    st, acts = _inv_code(db, "SRTWC8700")
    offered = " | ".join(f"{o.key} {o.label}" for o in getattr(getattr(acts[0], "pending", None), "options", []))
    assert "SRTWC8700-SET1" not in offered, f"offered through an inactive member: {offered}"


def test_sec2_set_members_are_chat_visible_and_active_only(db):
    a, b = _prods(db, "SRTSETV-A", "SRTSETV-B")
    b.is_active = False
    b.is_searchable = False
    db.flush()
    _set(db, "SRTSETV", [(a, 1), (b, 1)])
    st, acts = _inv_code(db, "SRTSETV")
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st) == ["SRTSETV-A"], "an inactive, unsearchable member is never held or fetched"


def test_sec2_a_set_with_no_visible_members_is_not_a_set_match(db):
    a, b = _prods(db, "SRTSETN-A", "SRTSETN-B")
    for p in (a, b):
        p.is_active = False
        p.is_searchable = False
    db.flush()
    _set(db, "SRTSETN", [(a, 1), (b, 1)])
    st, acts = _inv_code(db, "SRTSETN")
    assert _keys(st) == [], f"{_keys(st)}: a set with no visible members does not answer"
    assert not isinstance(acts[0], Lookup)


def test_sec3_a_stored_set_quantity_of_zero_stays_zero(db):
    a, b = _prods(db, "SRTSETZ-A", "SRTSETZ-B")
    _set(db, "SRTSETZ", [(a, 0), (b, 1)])
    st, acts = _inv_code(db, "SRTSETZ", quantity=5)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _qty(st) == {"SRTSETZ-A": 0, "SRTSETZ-B": 5}
