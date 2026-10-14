"""PV2 redesign R4a (UAC R4a-1..5): the parser sees the printed rows and the held subject.

No LLM, no DB. Parses are handwritten as the new prompt rule would produce them (the person's words are
in each test's docstring or comment, never in the parse). The messages are fresh paraphrases, not battery
wording. State is built by hand or through the real engine hook `_pv2_write_register` with an envelope
carrying `figures` / `printed_rows` / `rows`, the shapes `pv2/register.py` reads.

Pinned here (the tester's choices where the plan is silent):
  * MEMORY's LAST ANSWER ROWS line is `n=<kind> <printed label>` joined by " | ", the latest reply's entries in
    printed order (entries with the register's highest `turn`), a child ends `(in <parent number>)`.
  * `LAST REPLY:` is one line anywhere in MEMORY: `answered <domain>`, `asked <pending kind>`, `not found ...`,
    `none` on a fresh state. A refusal leaves nothing in state (turn.py clears pending/held, only the clock moves),
    so "refused" is NOT reachable from state and is not pinned.
  * A row_pick resolves the picked row's printed label through the resolver as the row's kind.
Tests marked GUARD already pass on HEAD (behaviour that must not regress); the rest are red on HEAD.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, RegisterEntry, new_state
from tests.chatbot.pv2.carry_fakes import (
    NOW,
    FakeResolver,
    ask,
    entity,
    held as mk_held,
    mk_state,
    option,
    out,
    pending,
)

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PROFILE = MemoryProfile(audience="staff")
SO_UUID = "9b1f3c2e-5a47-4d0e-8c11-7f2e6a9d4b30"


def _memory(state) -> str:
    return render_memory(state, PROFILE, NOW)


def _line(memory: str, prefix: str) -> str:
    return next((ln for ln in memory.split("\n") if ln.startswith(prefix)), f"<no {prefix} line>")


def _entry(n, kind, key, label, turn, parent=None):
    return RegisterEntry(id=f"R{n}", kind=kind, key=key, label=label, parent=parent, subject=None, turn=turn)


def _latest_and_previous_register():
    """Turn 4 printed an order with two product lines and a sales order; turn 3 printed one product."""
    return [
        _entry(1, "delivery_order", "DO-KEY-77", "202610-0151", 4),
        _entry(2, "product", "PKEY-0001", "SRTWB1096", 4, parent="R1"),
        _entry(3, "product", "PKEY-0002", "SRTWT7203", 4, parent="R1"),
        _entry(4, "sales_order", SO_UUID, "SO419122", 4),
        _entry(5, "product", "PKEY-0003", "OLDPRODUCT9", 3),
    ]


def _state_with_register(register, **kw):
    st = mk_state(mk_held("order", {"delivery_order": ["202610-0151"]}), turn_no=4, **kw)
    st.register = register
    return st


# ------------------------------------------------------------------ R4a-1 LAST ANSWER ROWS from the register


def test_r4a_1_last_answer_rows_is_the_latest_reply_numbered_with_kind_and_children():
    """RED: HEAD prints only PV2's own last_rows ('none' here)."""
    line = _line(_memory(_state_with_register(_latest_and_previous_register())), "LAST ANSWER ROWS:")
    assert line == (
        "LAST ANSWER ROWS: 1=delivery_order 202610-0151 | 2=product SRTWB1096 (in 1) | "
        "3=product SRTWT7203 (in 1) | 4=sales_order SO419122"
    )


def test_r4a_1_keys_and_uuids_are_never_rendered():
    """RED: the rows must show first (HEAD shows none), and no stored key may leak."""
    memory = _memory(_state_with_register(_latest_and_previous_register()))
    assert "SRTWB1096" in _line(memory, "LAST ANSWER ROWS:")
    for key in ("DO-KEY-77", "PKEY-0001", "PKEY-0002", SO_UUID):
        assert key not in memory, key
    assert not UUID_RE.search(memory)


def test_r4a_1_only_the_latest_reply_is_shown_never_the_previous_one():
    """RED: HEAD shows no register row at all, so the latest block is absent."""
    line = _line(_memory(_state_with_register(_latest_and_previous_register())), "LAST ANSWER ROWS:")
    assert "SRTWT7203" in line and "OLDPRODUCT9" not in line


def test_r4a_1_the_shown_rows_are_capped_as_the_register_is():
    """RED: 25 printed rows roll to the register's 20; MEMORY shows 20 and no 21st."""
    from app.services.chatbot.pv2 import register

    fresh = [_entry(n, "product", f"K{n:02d}", f"SRTWC{n:04d}", 4) for n in range(1, 26)]
    st = _state_with_register(register.roll([], fresh))
    assert len(st.register) == 20
    line = _line(_memory(st), "LAST ANSWER ROWS:")
    assert "20=product SRTWC0020" in line
    assert "21=" not in line and "SRTWC0021" not in line


def test_r4a_1_guard_no_register_rows_shows_pv2s_own_last_rows_as_today():
    """GUARD: the fallback line is unchanged."""
    st = mk_state(mk_held("inventory", {"product": ["A1"]}), last_rows=[option("1", "SRTWC6003"), option("2", "SRTWC8066")])
    assert _line(_memory(st), "LAST ANSWER ROWS:") == "LAST ANSWER ROWS: 1=SRTWC6003 | 2=SRTWC8066"
    assert _line(_memory(mk_state()), "LAST ANSWER ROWS:") == "LAST ANSWER ROWS: none"


# ------------------------------------------------------------------ R4a-1b row_pick indexes the shown rows


def _rows_envelope():
    """A reply that printed two product rows whose STORED keys differ from their printed labels."""
    return {
        "domain": "inventory",
        "figures": [],
        "rows": [
            {"kind": "product", "key": "STORED-KEY-A", "label": "SRTWC286-SH"},
            {"kind": "product", "key": "STORED-KEY-B", "label": "SRTWT7203"},
        ],
    }


def _state_after_the_reply():
    """Held inventory; PV2's own stale last_rows (3 rows) from an earlier turn; then the engine hook writes the
    register from the reply (and, per the plan, the shown rows)."""
    st = mk_state(
        mk_held("inventory", {"product": ["A1"]}),
        last_rows=[option("1", "STALE1"), option("2", "STALE2"), option("3", "STALE3")],
    )
    engine_mod._pv2_write_register({"pv2_state": st}, [_rows_envelope()])
    assert [e.label for e in st.register] == ["SRTWC286-SH", "SRTWT7203"]
    return st


def _pick(n):
    return out(ask(act="refines_held", domain="inventory", row_pick=n))


def test_r4a_1b_row_pick_takes_the_shown_row_and_resolves_its_label_through_the_resolver():
    """RED: HEAD indexes the stale last_rows, so row 2 is STALE2 and the resolver is never asked."""
    r = FakeResolver({"entities": {
        "product|SRTWT7203": {"status": "exact", "refs": [{"kind": "product", "key": "RESOLVED-7203", "label": "SRTWT7203"}]},
        "product|STORED-KEY-B": {"status": "exact", "refs": [{"kind": "product", "key": "WRONG-FROM-STORED-KEY", "label": "x"}]},
    }})
    st, acts = apply_turn(_state_after_the_reply(), _pick(2), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert [x.key for x in st.held.refs["product"]] == ["RESOLVED-7203"]
    assert "product|SRTWT7203" in r.entity_lookups
    assert "product|STORED-KEY-B" not in r.entity_lookups


def test_r4a_1b_row_pick_one_takes_the_first_shown_row():
    """RED: same reason, row 1."""
    r = FakeResolver({"entities": {
        "product|SRTWC286-SH": {"status": "exact", "refs": [{"kind": "product", "key": "RESOLVED-286SH", "label": "SRTWC286-SH"}]},
    }})
    st, acts = apply_turn(_state_after_the_reply(), _pick(1), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert [x.key for x in st.held.refs["product"]] == ["RESOLVED-286SH"]


def test_r4a_1b_row_pick_beyond_the_shown_rows_is_a_miss():
    """RED: HEAD still has 3 stale rows, so row 3 is not a miss there; with 2 shown rows it is."""
    st, acts = apply_turn(_state_after_the_reply(), _pick(3), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert [x.key for x in st.held.refs["product"]] == ["A1"]


def test_r4a_1b_guard_row_pick_beyond_pv2s_own_rows_is_a_miss():
    """GUARD: with no register rows the old behaviour holds."""
    st = mk_state(mk_held("inventory", {"product": ["A1"]}), last_rows=[option("1", "X1"), option("2", "X2")])
    _, acts = apply_turn(st, _pick(5), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct)


# ------------------------------------------------------------------ R4a-2 HELD codes and LAST REPLY


def test_r4a_2_held_shows_the_printed_product_code_beside_its_kind():
    """RED: a held product whose label is a description prints the description at HEAD, not the code."""
    from app.services.chatbot.pv2.state import Held, ResolvedRef

    # R4a fix 1 B1: the resolver marks a code key; HELD prints only marked keys
    h = Held(domain="inventory", refs={"product": [ResolvedRef(kind="product", key="SRTWC286-SH", label="Black seat cover short", code_key=True)]})
    held_block = _memory(mk_state(h)).split("PENDING QUESTION")[0]
    assert re.search(r"product\W{0,3}SRTWC286-SH", held_block), held_block


def test_r4a_2_guard_held_never_prints_a_customer_key_or_uuid():
    """GUARD: only codes go in, never an internal key."""
    from app.services.chatbot.pv2.state import Held, ResolvedRef

    uid = "123e4567-e89b-12d3-a456-426614174000"
    h = Held(domain="order", refs={"customer": [ResolvedRef(kind="customer", key=uid, label="Ahmad Trading")]})
    memory = _memory(mk_state(h))
    assert "Ahmad Trading" in memory and uid not in memory


def _exact(kind, key):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": key}]}


def test_r4a_2_last_reply_says_answered_with_the_domain_after_a_lookup():
    """RED: HEAD has no LAST REPLY line."""
    r = FakeResolver({"entities": {"product|SRTWC8517": _exact("product", "SRTWC8517")}})
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWC8517")]))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    memory = _memory(st)
    assert [ln for ln in memory.split("\n") if ln.startswith("LAST REPLY:")] == ["LAST REPLY: answered inventory"]


def test_r4a_2_last_reply_says_asked_with_the_question_kind_when_a_question_is_pending():
    """RED: HEAD has no LAST REPLY line. The question's own text is not echoed in it."""
    opts = [{"key": c, "label": c, "payload": {"kind": "customer", "key": c, "label": c}} for c in ("ABC SDN", "ABC TRD")]
    r = FakeResolver({"entities": {"customer|abc": {"status": "ambiguous", "options": opts}}})
    parsed = out(ask(act="new_topic", domain="order", entities=[entity("customer", phrase="abc")]))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and st.pending.options
    line = _line(_memory(st), "LAST REPLY:")
    assert line == "LAST REPLY: asked clarify_entity"


def test_r4a_2_last_reply_says_not_found_with_the_missed_phrase_after_a_miss():
    """RED: HEAD has no LAST REPLY line. A staff contact (no hand-over offer) gets a bare not-found question; the
    phrase is the parsed entity's, not the message."""
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="zzq7781")]))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=FakeResolver({}), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct) and acts[0].not_found and not st.pending.options
    assert _line(_memory(st), "LAST REPLY:") == "LAST REPLY: not found zzq7781"


def test_r4a_2_last_reply_is_none_on_a_fresh_state_and_is_one_line_inside_memory():
    """RED: every MEMORY key prints, `none` when empty."""
    memory = _memory(mk_state())
    lines = memory.split("\n")
    assert [ln for ln in lines if ln.startswith("LAST REPLY:")] == ["LAST REPLY: none"]
    assert lines[0] == "<memory>" and lines[-1] == "</memory>"


# ------------------------------------------------------------------ R4a-3 reference parses reach the Lookup


def _products(*codes):
    return {f"product|{c}": _exact("product", c) for c in codes}


def _shown_state(*codes, domain="inventory"):
    """Held domain with the shown products; the register's latest reply printed the same codes."""
    st = mk_state(mk_held(domain, {"product": list(codes)}), turn_no=4)
    st.register = [_entry(n, "product", c, c, 4) for n, c in enumerate(codes, start=1)]
    return st


def _run(state, parsed, codes):
    r = FakeResolver({"entities": _products(*codes)})
    st, acts = apply_turn(state, parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    return st, acts[0]


def _keys(lookup):
    return sorted(x.key for x in lookup.held.refs.get("product", []))


def test_r4a_3_guard_one_row_that_2nd_item():
    """GUARD ("that 2nd item" after three rows): the parse copies row 2's code."""
    st = _shown_state("SRTWC8517", "SRTWC8518", "SRTWC8519")
    parsed = out(ask(act="refines_held", domain="inventory", entities=[entity("product", phrase="SRTWC8518")]))
    new, lookup = _run(st, parsed, ["SRTWC8518"])
    assert _keys(lookup) == ["SRTWC8518"] and [x.key for x in new.held.refs["product"]] == ["SRTWC8518"]


def test_r4a_3_guard_first_and_last_of_three_rows():
    """GUARD ("first and last please" / "yang pertama dan terakhir"): one entity per named row, middle dropped."""
    st = _shown_state("SRTWC8517", "SRTWC8518", "SRTWC8519")
    parsed = out(ask(act="refines_held", domain="inventory",
                     entities=[entity("product", phrase="SRTWC8517"), entity("product", phrase="SRTWC8519")]),
                 language="ms")
    _, lookup = _run(st, parsed, ["SRTWC8517", "SRTWC8519"])
    assert _keys(lookup) == ["SRTWC8517", "SRTWC8519"]


def test_r4a_3_guard_dua_dua_both_rows():
    """GUARD (Manglish "dua-dua lah"): both rows of two."""
    st = _shown_state("SRTWT7203", "SRTWB1096")
    parsed = out(ask(act="refines_held", domain="inventory",
                     entities=[entity("product", phrase="SRTWT7203"), entity("product", phrase="SRTWB1096")]),
                 language="mixed")
    _, lookup = _run(st, parsed, ["SRTWT7203", "SRTWB1096"])
    assert _keys(lookup) == ["SRTWB1096", "SRTWT7203"]


def test_r4a_3_guard_zh_both_rows():
    """GUARD (zh "两个都要看"): both rows of two, language zh."""
    st = _shown_state("SRTWT7203", "SRTWB1096")
    parsed = out(ask(act="refines_held", domain="incoming",
                     entities=[entity("product", phrase="SRTWT7203"), entity("product", phrase="SRTWB1096")]),
                 language="zh")
    _, lookup = _run(st, parsed, ["SRTWT7203", "SRTWB1096"])
    assert _keys(lookup) == ["SRTWB1096", "SRTWT7203"] and lookup.domain == "incoming"


def test_r4a_3_guard_all_of_them():
    """GUARD ("the whole lot, thanks"): one entity per shown row."""
    codes = ["SRTWC8517", "SRTWC8518", "SRTWC8519", "SRTWC8520"]
    st = _shown_state(*codes)
    parsed = out(ask(act="refines_held", domain="inventory", entities=[entity("product", phrase=c) for c in codes]))
    _, lookup = _run(st, parsed, codes)
    assert _keys(lookup) == codes


def test_r4a_3_an_order_products_child_reaches_the_lookup_alone():
    """("is this product still in stock?" after an order list): the parse copies the child's code with the
    inventory domain; the Lookup holds exactly that product, not the order."""
    st = _state_with_register(_latest_and_previous_register())
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWB1096")]))
    new, lookup = _run(st, parsed, ["SRTWB1096"])
    assert lookup.domain == "inventory" and _keys(lookup) == ["SRTWB1096"]
    assert not lookup.held.refs.get("delivery_order")


def test_r4a_3_a_second_order_row_picked_by_number_reaches_the_lookup():
    """("no 2 only" after an order list): the parse copies the printed document number."""
    st = _state_with_register([_entry(1, "delivery_order", "k1", "202610-0151", 4), _entry(2, "delivery_order", "k2", "202610-0152", 4)])
    r = FakeResolver({"entities": {"delivery_order|202610-0152": _exact("delivery_order", "202610-0152")}})
    parsed = out(ask(act="refines_held", domain="order", entities=[entity("delivery_order", phrase="202610-0152")]))
    new, acts = apply_turn(st, parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup)
    assert [x.key for x in acts[0].held.refs["delivery_order"]] == ["202610-0152"]


# ------------------------------------------------------------------ R4a-4 the prompt gains no word list


def test_r4a_4_the_prompt_gains_no_word_list_and_keeps_its_row_reading():
    """GUARD (source check): no line of particle / paraphrase lists was added (43 lines hold 4+ quoted examples at
    HEAD) and the published prompt keeps reading LAST ANSWER ROWS."""
    text = (Path(__file__).resolve().parents[3] / "app/services/chatbot/pv2/prompt_v2.txt").read_text(encoding="utf-8")
    wordy = [ln for ln in text.split("\n") if len(re.findall(r'["“]([^"”]{1,30})["”]', ln)) >= 4]
    assert len(wordy) <= 43, len(wordy)
    for word in ("dua-dua", "两个都要", "有冇", "ada tak"):
        assert word not in text, word
    assert "LAST ANSWER ROWS" in text
