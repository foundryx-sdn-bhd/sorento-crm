"""PV2-FIX r8a (r7 live check + the owner's Mr Loo session), items 2-5 and 7-11. Item 1 (the shared v1
composer) is tests/chatbot/test_fix_r8a_attachment_gap.py; item 6 (the Prompts screen label) is
tests/test_pv2_runtime_label.py and the AgentModelCard vitest. Rows: fixtures/livecheck_r7_r8.jsonl
(recorded state-in and parse from chatbot.turns; no model call)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import ClarifyAct, ConversationState, Lookup, ResolvedRef
from app.services.chatbot.turn import pending as v1p
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, mk_state

FIXTURE = Path(__file__).parent / "fixtures" / "livecheck_r7_r8.jsonl"
ROWS = {json.loads(l)["case"]: json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()}
TODAY = date(2026, 10, 14)


def _row(case, **over):
    return dict(ROWS[case], _where=case, **over)


def _run(case, **over):
    (result,) = replay.run_case([_row(case, **over)])
    return result


@pytest.mark.parametrize("case", list(ROWS), ids=list(ROWS))
def test_the_live_turn_replays_to_its_expected_outcome(case):
    assert not _run(case).failures, _run(case).failures


# ------------------------------------------------------------------ 2 the question asked back keeps the original ask


def _deferred(pending):
    d = pending.deferred
    return d if isinstance(d, dict) else d.model_dump()


def test_a_flagged_clarify_keeps_the_original_domain_on_the_pending():
    result = _run("r8-2-eta-mou-question")
    assert isinstance(result.actions[0], ClarifyAct)
    assert _deferred(result.state_out.pending)["domain"] == "incoming"


def test_the_pick_completes_the_deferred_ask():
    first = _row("r8-2-eta-mou-question")
    pick = _row("r8-2-eta-mou-pick-1", turn=2, state_in=None)
    results = replay.run_case([first, pick])
    action = results[1].actions[0]
    assert isinstance(action, Lookup)
    held = results[1].state_out.held
    assert held.domain == "incoming", "1 must answer the ETA that was asked, not re-run stock"
    assert [r.key for r in held.refs["product"]] == ["SRTWC286"]


# ------------------------------------------------------------------ 3 'eta' after a stock answer for a family

MEMBERS = ["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-200", "SRTWC286-SH-NEW", "SRTWC286-SH-NEW-150",
           "SRTWC286-SH-NEW-200", "SRTWC286-SH-NEW-P", "SRTWC286-SH-P", "SRTWC286-SH-PP", "SRTWC286-SH-UF"]


class _FamilyResolver(replay.CannedResolver):
    """The replay resolver plus the family expansion v1's `list_all` narrowing gives (pinned name)."""

    def family_members(self, key):
        return [ResolvedRef(kind="product", key=c, label=c) for c in MEMBERS] if key == "SRTWC286" else []


def _eta(**kw):
    row = ROWS["r8-3-eta-held-family"]
    state = ConversationState.model_validate(row["state_in"])
    parsed = ParserV2Output.model_validate(row["parser_out"])
    state, actions = apply_turn(state, parsed, resolver=_FamilyResolver({}), **kw)
    return state, actions


def test_eta_for_a_held_family_is_sent_to_v1_as_its_members():
    state, actions = _eta()
    assert isinstance(actions[0], Lookup) and state.held.domain == "incoming"
    assert [r.key for r in state.held.refs["product"]] == MEMBERS
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    assert [e["canonical_code"] for e in v["entities"] if e["hint"] == "product"] == MEMBERS


def test_an_availability_dealer_keeps_the_family_and_v1s_which_one():
    state, actions = _eta(stock_availability_only=True)
    assert [r.key for r in state.held.refs["product"]] == ["SRTWC286"]


def test_the_db_resolver_expands_a_family_into_its_members():
    from app.services.chatbot.pv2.resolve import DbResolver

    with S.resolve_session() as db:
        cat = S.category(db)
        for code in MEMBERS[:3]:
            row = S.product(db, code, cat=cat)
            row.family_code = "SRTWC286"
        db.flush()
        members = DbResolver(db, pinned={}).family_members("SRTWC286")
    assert sorted(r.key for r in members) == sorted(MEMBERS[:3])


# ------------------------------------------------------------------ 4 / 8b the mirrored offer keeps v1's own team and this turn's text


def _v1_offer(team, kind="team_pick"):
    return v1p.ask(kind, [{"label": "Yes", "position": 1, "entity_type": "team"}], team=team, expects="yes_no",
                   payload={}, asked_at_turn=3)


STALE = ("Stock summary for the requested products.\n\n- *Product Code:* SRTKT39SS\n*Total:* 7\n\n"
         "Would you like me to escalate to purchasing team?")


def test_the_mirrored_offer_names_v1s_own_team_not_a_stale_reply_s():
    state = turn.mirror_v1_pending(mk_state(), _v1_offer("warehouse"), last_reply=STALE)
    assert "purchasing" not in state.pending.asked, state.pending.asked
    assert state.pending.asked == "Would you like me to escalate to warehouse team?"


def test_a_stale_older_reply_never_becomes_the_mirrored_question():
    old = "Last purchase cost for SRTWC286: RM 120.00 per unit.\nCheck anything else?"
    opts = [{"code": f"C{i}", "label": f"SRTWC286-V{i}", "position": i, "entity_type": "product"} for i in range(1, 4)]
    pend = v1p.ask("product_pick", opts, team="purchasing", expects=None, payload={}, asked_at_turn=3)
    state = turn.mirror_v1_pending(mk_state(), pend, last_reply=old)
    assert "purchase cost" not in state.pending.asked, state.pending.asked


def test_a_stale_menu_reply_whose_options_are_not_the_pendings_is_not_mirrored():
    stale = "Which product do you mean? Please choose:\n1. OTHERTHING-A\n2. OTHERTHING-B"
    opts = [{"code": f"C{i}", "label": f"SRTWC286-V{i}", "position": i, "entity_type": "product"} for i in range(1, 4)]
    pend = v1p.ask("product_pick", opts, team="purchasing", expects=None, payload={}, asked_at_turn=3)
    state = turn.mirror_v1_pending(mk_state(), pend, last_reply=stale)
    assert state.pending.asked != "Which product do you mean? Please choose:", state.pending.asked


# ------------------------------------------------------------------ 5 staff SO not-found: the real roster build


def test_the_staff_roster_build_passes_v1s_agent_to_the_roster_read(monkeypatch):
    """Live JD-ST7-8: `fetch_rosters` raised "agent_id or agent_code is required to resolve team_code" and
    `staff_roster_for` swallowed it, so staff got only the not-found line. The real roster read needs
    `routing.suggested_agent`; the stub in the r7b tests never asked for it."""
    import app.services.team_roster_service as trs

    def fake_list_team_roster(db, *, agent_code=None, team_code=None, **kw):
        if not agent_code:
            raise ValueError("agent_id or agent_code is required to resolve team_code (or pass team_id directly).")
        return [{"user_id": "u1", "respond_user_id": "r1", "name": "Sandy Lim", "company_id": None}]

    monkeypatch.setattr(trs, "list_team_roster", fake_list_team_roster)
    build = turn.staff_roster_for(lambda: SimpleNamespace(close=lambda: None), "404285551")
    sentence = 'Couldn\'t find: "SO422057" (sales order). Would you like me to escalate to customer service team?'
    got = build("customer_service", sentence)
    assert got is not None, "the roster build returned None: the staff got no CS offer"
    assert [m["user_id"] for m in got["members"]] == ["u1"]
    assert got["text"].startswith(sentence)


# ------------------------------------------------------------------ 7 language parity


def _loc(lang):
    return label_catalog.Localizer(lang, label_catalog.defaults(lang))


def _reply(case, **ctx_kw):
    row = ROWS[case]
    parsed = ParserV2Output.model_validate(row["parser_out"])
    ctx = turn.TurnV2Context(
        text=row["text"], now=NOW, raw_state=None, profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=replay.CannedResolver(row["resolve"]), load_exchanges=lambda s: [],
        offer_mode="offer", **ctx_kw)
    return turn._run(ctx).direct_reply or ""


def test_the_malay_not_found_offer_uses_v1s_localized_copy():
    loc = _loc("ms")
    want = (loc.fill("Couldn't find: {names}.", names='"SRTWC286-SH" (product)') + " "
            + loc.fill("Would you like me to escalate to {team} team?", team="warehouse"))
    assert want in _reply("r8-7-ms-not-found", domain_teams={"inventory": "warehouse"}), want


def test_the_chinese_order_not_found_offer_uses_v1s_localized_copy():
    loc = _loc("zh")
    names = ", ".join(f'"{c}" (product)' for c in ("SRTWC286-SH", "SRTWF12C-GM-NEW", "SRT373-8", "SRTWB999"))
    want = (loc.fill("Couldn't find: {names}.", names=names) + " "
            + loc.fill("Would you like me to escalate to {team} team?", team="customer service"))
    assert want in _reply("r8-7-zh-order-not-found", domain_teams={"order": "customer_service"}), want


# ------------------------------------------------------------------ 8a a product whose name is its code


def test_a_product_named_by_its_own_code_is_labelled_once():
    from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held

    with S.resolve_session() as db:
        row = S.product(db, "SRTWF12C")
        row.product_name = "SRTWF12C"
        db.flush()
        ent = Entity.model_construct(kind="product", key=None, phrase="SRTWF12C", op="set", refers_to_held=False,
                                     doc_number=None, quantity=None)
        res = DbResolver(db, pinned={}).resolve_entities([ent], held=Held())[0]
    assert res.status == "exact"
    assert res.refs[0].label == "SRTWF12C", res.refs[0].label


# ------------------------------------------------------------------ 9 / 10 permission first, 11 a typed code on a switch back


def _turn(case, granted, resolver=None, parsed=None, state=None):
    row = ROWS[case]
    parsed = parsed or ParserV2Output.model_validate(row["parser_out"])
    ctx = turn.TurnV2Context(
        text=row["text"], now=NOW, raw_state=state if state is not None else row["state_in"],
        profile=MemoryProfile(audience="dealer"), parse=lambda m, u: parsed,
        resolver=resolver or replay.CannedResolver(row.get("resolve") or {}), load_exchanges=lambda s: [],
        granted_reveals=frozenset(granted))
    return turn._run(ctx)


@pytest.mark.parametrize("case", ["r8-9-so-without-grant", "r8-9-sales-order-without-grant"])
def test_a_sales_order_ask_without_the_grant_gets_v1s_not_enabled_line_before_any_lookup(case):
    from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

    spy = FakeResolver({})
    res = _turn(case, [], resolver=spy)
    assert res.direct_reply == SO_NOT_ENABLED_MESSAGE, res.direct_reply
    assert spy.calls == [], "the lookup must not run: it would reveal whether the order exists"


def test_with_the_grant_the_sales_order_is_looked_up_as_before():
    from app.services.chatbot.lanes.business.fetch import SO_NOT_ENABLED_MESSAGE

    res = _turn("r8-9-so-without-grant", ["sales_orders.outstanding"])
    assert res.direct_reply != SO_NOT_ENABLED_MESSAGE


def test_a_report_answer_without_the_grant_gets_the_not_enabled_line_before_any_question():
    from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

    res = _turn("r8-10-delivered-without-grant", [])
    assert res.direct_reply == SALES_REPORT_NOT_ENABLED_MESSAGE, res.direct_reply


def test_a_report_answer_with_the_grant_is_unchanged():
    from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

    res = _turn("r8-10-delivered-without-grant", ["sales_orders.sales_report"])
    assert res.direct_reply != SALES_REPORT_NOT_ENABLED_MESSAGE


def test_a_switch_back_that_carries_a_typed_code_uses_it():
    result = _run("r8-11b-switch-back-with-typed-entity")
    assert isinstance(result.actions[0], Lookup), result.actions[0]
    held = result.state_out.held
    assert held.domain == "inventory"
    assert [r.key for r in held.refs["product"]] == ["SRTWC286-SH"]
    assert held.refs["product"][0].quantity.number == 10


# ------------------------------------------------------------------ 11c a bare switch back beside a new ask for the same domain


def test_a_bare_switch_back_beside_a_new_stock_ask_adds_nothing():
    """Probe call 2 on v21: [switch_back T1 (no entity), new_topic inventory SRTWC286-SH x10]. The bare switch back
    resolves the saved inventory topic (no product) and v1 answered 'I need at least one filter'. One stock answer
    for SRTWC286-SH x 10 is the reply."""
    result = _run("r8-11c-bare-switch-back-beside-a-new-ask")
    lookups = [a for a in result.actions if isinstance(a, Lookup)]
    assert len(lookups) == 1, [type(a).__name__ for a in result.actions]
    action = lookups[0]
    assert action.domain == "inventory"
    refs = action.held.refs.get("product", [])
    assert [r.key for r in refs] == ["SRTWC286-SH"], refs
    assert refs[0].quantity.number == 10
