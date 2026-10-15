"""PV2 R6 slice A (plan behaviours 1-2): clarify turns are first-class. Red tests, no LLM, no DB.

Every test starts from the shape the LIVE parser emits: act=unclear (or a refine / answer with `clarify.needed`),
answer null, row_pick null, often no open question or an open question that has no options; then the follow-up turn.
Parses are handwritten; fresh paraphrases are in the docstrings ('and what about last month for them?', 'show me the
other ones', 'A/C I only'). The bot's own printed lines are read back (never the user's message).

Tests marked "# guard: passes today" pin behaviour the fix must keep.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import answer, clarify_copy, register
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, option, out, pending

PROFILE = MemoryProfile(audience="staff")
NUMBERED = re.compile(r"^(\d+)\. (.+)$", re.M)


def _cust(key, label):
    return {"status": "exact", "refs": [{"kind": "customer", "key": key, "label": label}]}


def _printed(acts, state):
    """The numbered lines the turn printed, as (number, label)."""
    text = answer.replay_reply(state, acts)
    return [(int(n), label.strip()) for n, label in NUMBERED.findall(text)]


def _stored(st):
    return [(n, o.label) for n, o in enumerate(st.pending.options, start=1)] if st.pending else []


def _unclear(about, phrase=None, **ask_kw):
    return out(ask(act="unclear", **ask_kw), clarify={"needed": True, "about": about, "phrase": phrase})


# ------------------------------------------------------------------ 1 the parse's customer / domain / period are kept


def _context_parse(act):
    """'and last month for lim trading?' with nothing else clear: a customer, a domain and a period, plus a flag."""
    return out(ask(act=act, domain="order", entities=[entity("customer", phrase="lim")], period={"preset": "last_month", "n": None, "date_from": None, "date_to": None}),
               clarify={"needed": True, "about": "domain", "phrase": None})


@pytest.mark.parametrize("act", ["unclear", "refines_held", "new_topic"])
@pytest.mark.parametrize("open_question", [False, True], ids=["no-question", "optionless-question"])
def test_a_clarify_turn_keeps_the_customer_domain_and_period_it_parsed(act, open_question):
    """RED: HEAD returns the flagged/unclear question before any ask is applied, so held keeps none of them."""
    st0 = mk_state(pending_=pending("clarify_domain", [], asked="What would you like me to check?") if open_question else None)
    r = FakeResolver({"entities": {"customer|lim": _cust("CUST-LIM", "LIM TRADING SDN BHD")}})
    st, acts = apply_turn(st0, _context_parse(act), resolver=r, now=NOW)
    assert [x.key for x in st.held.refs.get("customer", [])] == ["CUST-LIM"], st.held
    assert st.held.domain == "order" and st.held.period is not None and st.held.period.preset == "last_month"


def test_a_clarify_turn_about_a_product_keeps_the_brand_and_domain_it_parsed():
    """RED, fresh shape: 'what about the italian ones?' flagged about the entity."""
    parsed = out(ask(act="unclear", domain="inventory", entities=[entity("brand", phrase="itali")]),
                 clarify={"needed": True, "about": "entity", "phrase": None})
    r = FakeResolver({"entities": {"brand|itali": {"status": "exact", "refs": [{"kind": "brand", "key": "ITALI", "label": "ITALI"}]}}})
    st, _ = apply_turn(mk_state(), parsed, resolver=r, now=NOW)
    assert [x.key for x in st.held.refs.get("brand", [])] == ["ITALI"] and st.held.domain == "inventory"


# ------------------------------------------------------------------ 1 every question carries numbered options


def test_an_unclear_turn_naming_an_ambiguous_customer_asks_with_its_candidates_as_options():
    """RED: HEAD never resolves the unclear turn's entity, so the entity question has no options."""
    cfg = {"entities": {"customer|lim": {"status": "ambiguous", "options": [
        {"key": "K1", "label": "LIM TRADING SDN BHD", "payload": {"kind": "customer", "key": "K1", "label": "LIM TRADING SDN BHD"}},
        {"key": "K2", "label": "LIM HENG ENTERPRISE", "payload": {"kind": "customer", "key": "K2", "label": "LIM HENG ENTERPRISE"}}]}}}
    parsed = _unclear("entity", "lim", entities=[entity("customer", phrase="lim")])
    st, acts = apply_turn(mk_state(), parsed, resolver=FakeResolver(cfg), now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    assert [o.label for o in st.pending.options] == ["LIM TRADING SDN BHD", "LIM HENG ENTERPRISE"]
    assert _printed(acts, st) == _stored(st)


def test_which_held_after_rows_were_shown_offers_the_shown_rows():
    """RED: 'show me the other one' flagged which_held with nothing held; the last answer printed two products."""
    rows = [Option(key="1", label="SRTWC286-SH", payload={"kind": "row", "row_kind": "product", "of": 2}),
            Option(key="2", label="SRTWT7203", payload={"kind": "row", "row_kind": "product", "of": 2})]
    st0 = mk_state(last_rows=rows)
    st, acts = apply_turn(st0, _unclear("which_held"), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    assert [o.label for o in st.pending.options] == ["SRTWC286-SH", "SRTWT7203"], st.pending
    assert _printed(acts, st) == _stored(st)


def test_a_domain_question_with_an_entity_in_the_parse_is_not_asked_with_zero_options():
    """RED: about=domain with a product named; HEAD asks 'What would you like me to check?' with no options (I2)."""
    parsed = _unclear("domain", entities=[entity("product", phrase="SRTWC286")])
    r = FakeResolver({"entities": {"product|SRTWC286": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}})
    st, acts = apply_turn(mk_state(), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct)
    assert st.pending.options, "a numbered choice of what to check (or a Lookup), never an empty open question"
    assert _printed(acts, st) == _stored(st)


# ------------------------------------------------------------------ 1 the family confirm's accounts are its options


class _Containing(FakeResolver):
    """The containing-name search for 'A/C I': one family option covering both accounts, flagged as a guess."""

    def resolve_entities(self, ents, *, held):
        res = []
        for e in ents:
            self.calls.append(("entity", f"{e.kind}|{e.phrase}"))
            fam = Option(key="K1", label="ACME TRADING SDN BHD", payload={
                "kind": "customer", "key": "K1", "keys": ["K1", "K2"], "label": "ACME TRADING SDN BHD",
                "labels": ["ACME TRADING SDN BHD [A/C I]", "ACME TRADING SDN BHD [A/C II]"]})
            res.append(SimpleNamespace(status="ambiguous", refs=[], options=[fam], phrase=e.phrase, unrecognised=False,
                                       guess=True, did_you_mean=False, specs=[], pool=None, more=0))
        return res


def test_every_numbered_line_a_family_confirm_prints_is_one_of_its_stored_options_and_memory_numbers_it_alike():
    """RED: HEAD prints the accounts as '1. ..', '2. ..' while the stored options are yes / no (MEMORY: 1=yes 2=no).
    Either the accounts are printed AND stored as the options, or (slice C: the group name is printed alone) no
    numbered line is printed; a printed number that is not an option is the defect (I4)."""
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="A/C I")]))
    st, acts = apply_turn(mk_state(), parsed, resolver=_Containing({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_confirm", type(acts[0]).__name__
    printed = _printed(acts, st)
    assert printed == _stored(st)[: len(printed)], (printed, _stored(st))
    memory = render_memory(st, PROFILE, NOW)
    for n, lab in printed:
        assert f"{n}=" in memory and f"({lab})" in memory.split(f"{n}=")[1].split(" | ")[0], memory


# ------------------------------------------------------------------ 1 no open question: never the "didn't catch" line


@pytest.mark.parametrize("parsed", [
    out(ask(act="answers_bot_question", answer={"option_key": None, "free_value": "that one"})),
    out(ask(act="answers_bot_question", row_pick=3)),
    out(ask(act="unclear"), clarify={"needed": True, "about": "other", "phrase": None}),
], ids=["free-value", "row-pick", "unclear-other"])
def test_with_no_open_question_a_non_answer_is_a_fresh_question_never_the_didnt_catch_line(parsed):
    """RED: HEAD's `_miss(None)` marks the brand-new question as a re-ask and prints 'Sorry, I didn't catch your choice.'"""
    st, acts = apply_turn(mk_state(), parsed, resolver=FakeResolver({}), now=NOW)
    reply = answer.replay_reply(st, acts)
    assert all(not getattr(a, "reask", False) for a in acts), acts
    assert clarify_copy.text("reask", "en") not in reply, reply


# ------------------------------------------------------------------ 2 a pick removes the picked entity whatever its kind tag


def test_a_pick_whose_resolved_kind_differs_from_the_parsed_kind_never_brings_the_question_back():
    """RED: the parse tagged 'SRTX1' a product; the resolver found two DELIVERY ORDERS under it. Picking one must settle
    the entity; HEAD keeps the product entity in the replayed ask (kind mismatch) and asks the same question again."""
    docs = [{"key": f"202610-0{n}", "label": f"202610-0{n}", "payload": {"kind": "delivery_order", "key": f"202610-0{n}", "label": f"202610-0{n}"}}
            for n in (151, 152)]
    r = FakeResolver({"entities": {
        "product|SRTX1": {"status": "ambiguous", "options": docs},
        "delivery_order|202610-0151": {"status": "exact", "refs": [{"kind": "delivery_order", "key": "202610-0151", "label": "202610-0151"}]},
    }})
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("product", phrase="SRTX1")]))
    st1, acts1 = apply_turn(mk_state(), parsed, resolver=r, now=NOW)
    assert isinstance(acts1[0], ClarifyAct) and st1.pending is not None and st1.pending.options, type(acts1[0]).__name__
    first = [(o.key, o.label) for o in st1.pending.options]
    pick = out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))
    st2, acts2 = apply_turn(st1, pick, resolver=r, now=NOW)
    assert isinstance(acts2[0], Lookup), type(acts2[0]).__name__
    assert st2.pending is None or [(o.key, o.label) for o in st2.pending.options] != first
