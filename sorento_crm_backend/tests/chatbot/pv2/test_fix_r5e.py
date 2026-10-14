"""PV2-FIX r5e: a real pick over a v1 product_pick is a pick, never a requantify
(guard `action.picked is None` in apply_turn's requantify rule)."""
from __future__ import annotations

from datetime import date

from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask, Quantity
from app.services.chatbot.pv2.state import Lookup, ResolvedRef

from tests.chatbot.pv2.carry_fakes import ask as _base_ask, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_fix_round2 import _Fake

TODAY = date(2026, 10, 16)


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def test_a_real_pick_with_a_quantity_beside_it_is_a_pick_not_a_requantify():
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    h.refs["product"][0] = ResolvedRef(kind="product", key="SRTWC286", label="SRTWC286",
                                       quantity=Quantity(number=5, unit=None))
    state = mk_state(h, pending_=pending("v1_open_question", [option("1", "A"), option("2", "B")]))
    state.pending.v1_kind = "product_pick"
    qty_only = {"kind": "product", "key": None, "phrase": None, "op": "set", "refers_to_held": False,
                "quantity": {"number": 5, "unit": "unit"}, "doc_number": None}
    state, actions = apply_turn(
        state, out(_ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None},
                        entities=[qty_only])),
        resolver=_Fake({"product|None": ("exact", [("product", "SRTWC286")], False)}))
    action = actions[0]
    assert isinstance(action, Lookup) and action.picked is not None and action.picked.key == "1"
    assert action.requantify is False
    v = bridge.to_v1_verdict(state, action, today=TODAY)
    assert v.get("demand_qty") in (None, 0)
    assert v["open_question_answer"]["mode"] == "pick" and v["open_question_answer"]["picked"] == [1]


# ------------------------------------------------------------------ r5f: did-you-mean reply shape

from tests.chatbot.pv2.test_fix_r5d import _ctx, _single  # noqa: E402


def test_the_did_you_mean_reply_lists_each_code_once_with_no_yes_no_lines():
    from app.services.chatbot.pv2 import turn

    res = turn._run(_ctx(_single(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert res.direct_reply.count("SRTWB999") == 1, res.direct_reply
    for line in res.direct_reply.splitlines():
        low = line.strip().lower()
        assert not (low in ("yes", "no") or low.startswith(("2. yes", "3. yes", "2. no", "3. no"))), line


def test_a_barred_contact_did_you_mean_ends_with_the_refer_line():
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.turn.task import REFER_TO_SALESMAN

    res = turn._run(_ctx(_single(), offer_mode="refer"))
    want = ('Couldn\'t find "SRTXYZ999" (product). Did you mean:\n1. SRTWB999\n'
            f"Reply with a code to continue. {REFER_TO_SALESMAN}")
    assert want in res.direct_reply, res.direct_reply
    assert "escalate" not in res.direct_reply.lower()


def test_typing_the_suggested_code_picks_it():
    from app.services.chatbot.pv2 import turn
    from tests.chatbot.pv2.test_fix_r5d import _ask as _a

    first = turn._run(_ctx(_single(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    pick = out(_a(act="answers_bot_question", answer={"option_key": "SRTWB999", "free_value": None}))
    second = turn._run(_ctx(pick, state=first.state, domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert isinstance(second.actions[0], Lookup)
    assert [r.key for r in second.state.held.refs["product"]] == ["SRTWB999"]
    assert second.state.held.domain == "inventory"
