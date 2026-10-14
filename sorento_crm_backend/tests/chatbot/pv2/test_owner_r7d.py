"""PV2-FIX r7d: crew's Mr Loo replay (PV2 vs v1 on the owner's scenarios): several attachment kinds, a partial
product matching many, a v1-tagged did-you-mean, and a new console chat starting empty."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, dump_state
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

FIXTURE = Path(__file__).parent / "fixtures" / "livecheck_loo_r7.jsonl"
BY_CASE = {json.loads(l)["case"]: json.loads(l) for l in FIXTURE.read_text(encoding="utf-8").splitlines() if l.strip()}
PROMPT = (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()
TODAY = date(2026, 10, 14)


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _run_row(case):
    (result,) = replay.run_case([dict(BY_CASE[case], _where=case)])
    return result


@pytest.mark.parametrize("case", [c for c in BY_CASE if c != "loo-F2-photo-and-drawing-list"], ids=lambda c: c)
def test_the_loo_turn_replays_to_its_expected_outcome(case):
    result = _run_row(case)
    assert not result.failures, result.failures


# ------------------------------------------------------------------ F several attachment kinds


def test_the_ask_schema_takes_a_list_of_attachment_kinds():
    ask = _ask(attachment_type=["product_photos", "technical_specifications"])
    assert list(ask.attachment_type) == ["product_photos", "technical_specifications"]


def test_both_attachment_kinds_reach_v1_as_entities():
    resolver = FakeResolver({
        "entities": {"product|SRTWC286-SH": {"status": "exact", "refs": [
            {"kind": "product", "key": "SRTWC286-SH", "label": "SRTWC286-SH"}]}},
        "attachment_types": {"product_photos": {"status": "exact", "refs": [
            {"kind": "attachment_type", "key": "product_photos", "label": "Product Photos"}]},
            "technical_specifications": {"status": "exact", "refs": [
                {"kind": "attachment_type", "key": "technical_specifications", "label": "Technical Specifications"}]}},
    })
    parsed = out(_ask(domain="product_attachment", entities=[_e(phrase="SRTWC286-SH")],
                      attachment_type=["product_photos", "technical_specifications"]))
    state, actions = apply_turn(mk_state(), parsed, resolver=resolver)
    assert isinstance(actions[0], Lookup)
    v = bridge.to_v1_verdict(state, actions[0], today=TODAY)
    kinds = sorted(e["canonical_code"] for e in v["entities"] if e["hint"] == "attachment_type")
    assert kinds == ["product_photos", "technical_specifications"], v["entities"]


def test_the_prompt_says_several_attachment_kinds_are_a_list():
    assert "several attachment kinds" in PROMPT


# ------------------------------------------------------------------ G a partial matching many products (v1 settles every match)


def test_a_partial_stock_ask_is_a_lookup_over_every_match_as_v1_answers():
    result = _run_row("loo-B3-WC286_stock")
    assert isinstance(result.actions[0], Lookup), result.actions[0]
    assert len(result.state_out.held.refs["product"]) == 10


def test_a_capped_menu_says_how_many_more():
    from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
    from tests.chatbot.pv2 import _resolve_seed as S

    with S.resolve_session() as db:
        for n in range(1, 11):
            S.product(db, f"SRTWC286-V{n:02d}")
        ent = Entity.model_construct(kind="product", key=None, phrase="WC286-V", op="set", refers_to_held=False,
                                     doc_number=None, quantity=None)
        res = DbResolver(db, pinned={}).resolve_entities([ent], held=Held())[0]
    assert res.status == "ambiguous"
    assert len(res.options) + getattr(res, "more", 0) == 10, (len(res.options), getattr(res, "more", None))


# ------------------------------------------------------------------ H v1's did-you-mean, tagged


V1_DYM = ('Couldn\'t find "SRTWC268-SH" (product). Did you mean:\n'
          "1. SRTWC286-SH - has stock details\n2. SRTWC8268-S - no stock details\n"
          "3. SRTWC8318-SH - no stock details\nReply with a code to continue.")


def test_the_did_you_mean_lists_three_codes_tagged_as_v1_does():
    codes = ["SRTWC286-SH", "SRTWC8268-S", "SRTWC8318-SH", "SRTWC8518-SH", "SRTWC200"]
    row = BY_CASE["loo-B2-SRTWC268-SH_stock"]
    ents = [e for a in row["parser_out"]["asks"] for e in a["entities"]]
    from app.services.chatbot.pv2.schema import ParserV2Output

    parsed = ParserV2Output.model_validate(row["parser_out"])
    table = {f"product|{ents[0]['phrase']}": ("ambiguous", [("product", c) for c in codes], True)}
    ctx = turn.TurnV2Context(
        text=row["text"], now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=_Fake(table), load_exchanges=lambda s: [], offer_mode="none",
        dym_tags=lambda domain, cs: {c: c == "SRTWC286-SH" for c in cs})
    assert turn._run(ctx).direct_reply == V1_DYM


# ------------------------------------------------------------------ I a new console chat starts empty


def _turn_row(db, run_id, text, reply, minutes):
    from app.models.chatbot_turn import ChatbotTurn

    db.add(ChatbotTurn(
        id=str(uuid.uuid4()), contact_respond_id="900000042", ingress="console", is_test=True, status="done",
        envelope={"test_run_id": run_id, "message": {"message": {"message": {"type": "text", "text": text}}}},
        response={"reply": {"text": reply}}, created_at=datetime(2026, 10, 14, 1, minutes, tzinfo=timezone.utc)))
    db.flush()


def test_a_new_console_run_loads_no_exchanges_from_an_earlier_run(session_factory):
    from app.services.chatbot.pv2.history import load_exchanges

    db = session_factory()
    _turn_row(db, "console-run-1", "SRTWC286 stock", "Which one?", 1)
    db.commit()
    common = dict(contact_respond_id="900000042", ingress="console", is_test=True, since=None)
    assert load_exchanges(db, test_run_id="console-run-1", **common) == [("SRTWC286 stock", "Which one?")]
    assert load_exchanges(db, test_run_id="console-run-2", **common) == []


def test_real_contacts_history_is_unchanged_by_the_run_scoping(session_factory):
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot.pv2.history import load_exchanges

    db = session_factory()
    db.add(ChatbotTurn(id=str(uuid.uuid4()), contact_respond_id="900000043", ingress="webhook", is_test=False,
                       status="done", envelope={"message": {"message": {"message": {"type": "text", "text": "hi"}}}},
                       response={"reply": {"text": "hello"}}, created_at=datetime(2026, 10, 14, 1, 1, tzinfo=timezone.utc)))
    db.commit()
    got = load_exchanges(db, contact_respond_id="900000043", ingress="webhook", is_test=False, since=None)
    assert got == [("hi", "hello")]


def test_a_new_console_run_ignores_the_stored_pv2_state_and_earlier_sessions(
    session_factory, seeded, stub_parser, stub_access, fake_turn
):
    from sqlalchemy import text as sql

    from tests.chatbot.pv2.test_core_engine_hook import FakeResult, _set_contact_mode
    from tests.chatbot.test_engine import CONTACT_ID, _envelope
    from app.services.chatbot import engine as engine_mod

    stored = dump_state(mk_state())
    stored["held"] = {"domain": "inventory", "refs": {"product": [
        {"kind": "product", "key": "SRTWC286", "label": "SRTWC286", "phrase": None, "quantity": None}]},
        "specs": [], "period": None, "report": None, "status": None, "portal_form": None,
        "idea_draft_id": None, "attachment_type": None}
    db = session_factory()
    db.execute(sql("update respond_contacts set session_vars = CAST(:sv AS jsonb) where respond_io_id = :c"),
               {"sv": json.dumps({"variables": {}, "pv2": stored}), "c": str(CONTACT_ID)})
    db.commit()
    calls = fake_turn(FakeResult(direct_reply="ok", branch_kind="low_signal"))
    stub_parser()
    stub_access()
    _set_contact_mode(session_factory, "v2")
    envelope = _envelope(is_test=True, test_run_id="console-run-new", ingress="console")
    engine_mod.run_turn(envelope, session_factory=session_factory)
    ctx = calls[0]
    block = ctx.session_block if isinstance(ctx.session_block, dict) else {}
    sv = block.get("session_vars") if isinstance(block.get("session_vars"), dict) else block
    assert not sv.get("pv2"), "a new console run must not see the stored PV2 state"
    assert not (ctx.memory_intake or {}).get("summaries"), "a new console run must not see earlier sessions"


from tests.chatbot.pv2.test_core_engine_hook import fake_turn  # noqa: E402,F401 - fixture
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: E402,F401 - fixtures
