"""EVAL-SETS: chained live turns (`replay.live_rows(..., chain=True)` and `replay.py live --chain`). Synthetic only.

A rehearsal conversation is several live turns of one contact. Each later turn must start from what the earlier
live turn left (its dry-run `session_patch`, read the way the console reads it) and see this run's own exchanges as
its history, not the recorded prod history of the base turn.

Requires from `scripts/chatbot_eval/replay.py`:
  live_rows(..., chain=False): with chain=True, items that share `conv` run as one conversation, in `conv_turn` order
      (groups in order of first appearance; an item without `conv` is its own conversation).
      - the first turn starts from its recorded row (memory and history as of the recorded turn), or, when the item has
        `start: "empty"`, from empty memory and no history;
      - every later turn starts from the memory the previous live turn left (its `session_patch`; none = unchanged),
        sees the previous live reply and this run's earlier exchanges, and counts its turn number on;
      - a turn that does not produce a row with `turn` (stopped, skipped, error) breaks its conversation: the rest of
        that conversation comes back {item_id, run, conv, conv_turn, skipped: "chain_broken"} and never runs;
      - every chained row carries `conv` and `conv_turn`.
  CLI: replay.py live ... --chain
"""
from __future__ import annotations

import copy
import json

import pytest

from app.services.chatbot import console_service
from app.services.chatbot import engine as engine_mod
from scripts.chatbot_eval import replay as replay_mod
from tests.chatbot.conftest import session_factory  # noqa: F401
from tests.chatbot.test_engine import _parser_output, seeded, stub_access  # noqa: F401
from tests.chatbot_eval.test_replay import ChatbotTurn, recorded, _record, _turn_count  # noqa: F401
from tests.chatbot_eval.test_replay_live import (  # noqa: F401
    LAST4, PRIOR_1, PRIOR_2, _walk, live_env, recorded_quoting, recorded_with_history, text_of,
)

ONE, TWO, THREE = "SYNCHAIN-ONE first ask", "SYNCHAIN-TWO short follow-up", "SYNCHAIN-THREE topic switch"


def chat(row, turns, *, conv="c1", start=None, run=1):
    out = []
    for k, msg in enumerate(turns, 1):
        it = {"item_id": f"{conv}-t{k}", "run": run, "message": msg, "recorded_turn_id": row["id"], "conv": conv,
              "conv_turn": k}
        if start:
            it["start"] = start
        out.append(it)
    return out


HELD = {"focus": {"products": [{"canonical_code": "SRTSYN-HELD", "raw": "SRTSYN-HELD"}]}, "open_question": None,
        "ideation": None, "access_levels": None, "contains_flyer": None, "reply_language": "en"}


def holding(row):
    """The recorded row with a non-empty memory before the turn (the `received` record's session_vars), so a test
    can tell the recorded memory from an empty one."""
    row = copy.deepcopy(row)
    rec = next(r for r in row["trace"] if isinstance(r, dict) and r.get("stage") == "received")
    rec.setdefault("raw", {})["session_vars"] = {"session_vars": copy.deepcopy(HELD)}
    assert replay_mod._recorded_memory(row) == HELD
    return row


def run(session_factory, row, items, chain=True):
    return replay_mod.live_rows(items, [row], session_factory=session_factory, max_calls=50, max_usd=10.0,
                                require_key_last4=LAST4, chain=chain)


@pytest.fixture()
def results(monkeypatch, live_env):
    """Every live run_turn result, in call order (wraps the live_env spy)."""
    seen = []
    inner = engine_mod.run_turn

    def keep(envelope, **kw):
        res = inner(envelope, **kw)
        seen.append(res)
        return res

    monkeypatch.setattr(engine_mod, "run_turn", keep)
    return seen


def _state(env):
    return (env.model_extra or {}).get("previous_conversation_state")


def _reply(row):
    return ((row["turn"]["response"] or {}).get("reply") or {}).get("text")


def _turn_no(trace):
    return next(rec["turn_no"] for rec in _walk(trace) if isinstance(rec, dict) and "turn_no" in rec
                and "quoted_turn" in rec)


def test_turn_two_starts_from_the_memory_turn_one_left(session_factory, recorded, live_env, results):
    run(session_factory, recorded, chat(recorded, [ONE, TWO]))
    first = results[0]
    left = console_service._next_state({"reply": first.reply, "session_patch": first.session_patch})
    assert left is not None and left != replay_mod._recorded_memory(recorded), \
        "turn one left no new memory, so this test could not tell a carried state from the recorded one"
    assert _state(live_env["envelopes"][1]) == left


def test_turn_one_starts_from_the_recorded_memory(session_factory, recorded, live_env):
    row = holding(recorded)
    run(session_factory, row, chat(row, [ONE, TWO]))
    assert _state(live_env["envelopes"][0]) == HELD


def test_a_fresh_start_runs_turn_one_on_empty_memory(session_factory, recorded, live_env):
    row = holding(recorded)
    run(session_factory, row, chat(row, [ONE, TWO], start="empty"))
    assert _state(live_env["envelopes"][0]) == {}


def test_turn_two_sees_turn_one_and_not_the_recorded_prod_history(session_factory, recorded_with_history, live_env):
    rows = run(session_factory, recorded_with_history, chat(recorded_with_history, [ONE, TWO], start="empty"))
    first_reply = _reply(rows[0])
    assert first_reply, "turn one produced no reply text"
    block_one, block_two = live_env["parse_calls"][0], live_env["parse_calls"][1]
    assert PRIOR_1 not in block_one and PRIOR_2 not in block_one, "a fresh start saw the recorded prod history"
    assert ONE in block_two, "turn two's history lacks turn one's message"
    assert f"User: {ONE}" in block_two, "turn two's recent exchanges lack turn one"
    assert first_reply.splitlines()[0] in block_two, "turn two's history lacks turn one's reply"
    prev = next((line for line in block_two.splitlines() if line.startswith("Previous response:")), "")
    assert first_reply.splitlines()[0] in prev, "turn two's Previous response is not turn one's reply"
    assert PRIOR_1 not in block_two and PRIOR_2 not in block_two, "turn two saw the recorded prod history"


def test_a_chain_from_the_recorded_state_keeps_the_recorded_history_and_adds_this_run(
        session_factory, recorded_with_history, live_env):
    run(session_factory, recorded_with_history, chat(recorded_with_history, [ONE, TWO]))
    block_one, block_two = live_env["parse_calls"][0], live_env["parse_calls"][1]
    assert PRIOR_2 in block_one, "turn one lost the recorded prod history"
    assert PRIOR_2 in block_two, "turn two lost the recorded prod history it chained from"
    assert ONE in block_two, "turn two's history lacks turn one's message"


def test_the_turn_number_counts_on_within_a_conversation(session_factory, recorded_with_history, live_env):
    rows = run(session_factory, recorded_with_history, chat(recorded_with_history, [ONE, TWO, THREE]))
    nos = [_turn_no(r["turn"]["trace"]) for r in rows]
    assert nos[1] == nos[0] + 1 and nos[2] == nos[0] + 2, nos


def test_the_turn_number_counts_on_when_the_recorded_row_has_no_anchor(session_factory, recorded, live_env):
    row = copy.deepcopy(recorded)   # the fixture's turn is gone from the DB; without a prompt time there is no anchor
    for rec in _walk(row["trace"]):
        if rec.get("kind") == "prompt_text":
            rec.pop("at", None)
    rows = run(session_factory, row, chat(row, [ONE, TWO, THREE]))
    nos = [_turn_no(r["turn"]["trace"]) for r in rows]
    assert nos[1] == nos[0] + 1 and nos[2] == nos[0] + 2, nos


def _reply_to(env):
    node = env.model_dump(mode="json", by_alias=True) if hasattr(env, "model_dump") else env
    return next((rec.get("replyTo") or rec.get("reply_to") for rec in _walk(node)
                 if isinstance(rec, dict) and (rec.get("replyTo") or rec.get("reply_to"))), None)


def test_a_chained_turn_carries_no_recorded_quoted_message(session_factory, recorded_quoting, live_env):
    assert _reply_to(recorded_quoting["envelope"]), "the fixture's recorded envelope quotes nothing"
    run(session_factory, recorded_quoting, chat(recorded_quoting, [ONE, TWO], start="empty", conv="fresh"))
    run(session_factory, recorded_quoting, chat(recorded_quoting, [ONE, TWO], conv="kept"))
    fresh_one, fresh_two, kept_one, kept_two = live_env["envelopes"]
    assert _reply_to(fresh_one) is None and _reply_to(fresh_two) is None, "a fresh conversation quoted a prod message"
    assert _reply_to(kept_one), "turn one on its recorded row lost the recorded quote"
    assert _reply_to(kept_two) is None, "a later turn re-quoted the recorded message"


def test_two_runs_of_one_conversation_are_two_chains(session_factory, recorded, live_env):
    row = holding(recorded)
    items = chat(row, [ONE, TWO], start="empty", run=1) + chat(row, [THREE, TWO], start="empty", run=2)
    for it in items:
        it["item_id"] += f"-r{it['run']}"
    run(session_factory, row, [items[0], items[2], items[1], items[3]])
    by_text = {}
    for env in live_env["envelopes"]:
        by_text.setdefault(text_of(env), []).append(_state(env))
    assert by_text[ONE] == [{}] and by_text[THREE] == [{}], "a run's turn one started from the other run's memory"


def test_turns_run_in_conv_turn_order_whatever_the_file_order(session_factory, recorded, live_env):
    items = chat(recorded, [ONE, TWO, THREE])
    rows = run(session_factory, recorded, [items[2], items[0], items[1]])
    assert [text_of(e) for e in live_env["envelopes"]] == [ONE, TWO, THREE]
    assert [r["conv_turn"] for r in rows] == [1, 2, 3]


def test_chained_rows_carry_conv_and_conv_turn(session_factory, recorded, live_env):
    rows = run(session_factory, recorded, chat(recorded, [ONE, TWO], conv="rh-007"))
    assert [(r["conv"], r["conv_turn"]) for r in rows] == [("rh-007", 1), ("rh-007", 2)]
    assert all("turn" in r for r in rows)


def test_a_broken_turn_skips_the_rest_of_its_conversation_only(session_factory, recorded, live_env, monkeypatch):
    inner = engine_mod.run_turn

    def boom(envelope, **kw):
        if text_of(envelope) == ONE:
            raise RuntimeError("synthetic failure")
        return inner(envelope, **kw)

    monkeypatch.setattr(engine_mod, "run_turn", boom)
    items = chat(recorded, [ONE, TWO], conv="a") + chat(recorded, [THREE, TWO], conv="b")
    rows = run(session_factory, recorded, items)
    by = {r["item_id"]: r for r in rows}
    assert by["a-t1"].get("error") == "RuntimeError"
    assert by["a-t2"].get("skipped") == "chain_broken" and "turn" not in by["a-t2"]
    assert "turn" in by["b-t1"] and "turn" in by["b-t2"]
    assert [text_of(e) for e in live_env["envelopes"]].count(TWO) == 1, "the broken conversation's turn two ran"


def test_without_chain_every_item_starts_from_the_recorded_memory(session_factory, recorded, live_env):
    rows = run(session_factory, recorded, chat(recorded, [ONE, TWO]), chain=False)
    assert all(_state(e) == replay_mod._recorded_memory(recorded) for e in live_env["envelopes"])
    assert all("conv_turn" not in r for r in rows)


def test_a_chain_writes_nothing(session_factory, recorded, live_env):
    before = _turn_count(session_factory)
    run(session_factory, recorded, chat(recorded, [ONE, TWO, THREE]))
    assert _turn_count(session_factory) == before


def test_cli_chain_flag_runs_the_items_as_conversations(tmp_path, session_factory, recorded, live_env, monkeypatch):
    import app.database

    monkeypatch.setattr(app.database, "SessionLocal", session_factory)
    row = holding(recorded)
    items, rec, out = tmp_path / "items.jsonl", tmp_path / "rec.jsonl", tmp_path / "out.jsonl"
    items.write_text("".join(json.dumps(i) + "\n" for i in chat(row, [ONE, TWO], start="empty")))
    rec.write_text(json.dumps(row) + "\n")
    code = replay_mod.main(["live", "--items", str(items), "--recorded", str(rec), "--out", str(out), "--max-calls", "50",
                            "--max-usd", "10", "--require-key-last4", LAST4, "--chain"])
    assert code == 0
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    assert [(r["conv"], r["conv_turn"]) for r in rows] == [("c1", 1), ("c1", 2)]
    assert _state(live_env["envelopes"][0]) == {}
