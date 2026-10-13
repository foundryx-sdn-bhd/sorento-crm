"""PV2-CARRY over the converted v1 corpus (CONTRACT-PV2-13oct.md section 7).

`tables/converted_replay.jsonl` (from `v1_to_v2.py`): every recorded replay turn, its v1
verdict converted to a ParserV2Output and its recorded resolver answers as the fake
resolver, chained per case through `apply_turn`. Where the v1 lane asked a question
(`v1_pending_after`), the next turn starts with that question recorded the way
`turn.after_v1` records it. Asserted: the action shape the conversion pins, every
clarify carries real options with no id in a label, and the PV2-written reply never
leaks a UUID or " · ".

`tables/converted_console.jsonl`: console cases have no parser output until CORE's dev
split records one; their rows are checked for shape here and counted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from itertools import groupby
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import answer, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state, roll_session
from app.services.chatbot.turn.pending import OFFER_KINDS
from tests.chatbot.pv2.carry_fakes import FakeResolver

TABLES = Path(__file__).parent / "tables"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (TABLES / name).read_text().splitlines() if line.strip()]


REPLAY = _rows("converted_replay.jsonl")
CASES = {case: list(turns) for case, turns in groupby(REPLAY, key=lambda r: r["case"])}


def test_converted_tables_cover_the_corpus() -> None:
    assert len(CASES) >= 200
    assert len(REPLAY) >= 870
    console = _rows("converted_console.jsonl")
    assert len(console) >= 300
    for row in console:
        assert row["text"], row["case"]
        assert row["parser_out"] is None and row["source"].startswith("converted:")
        assert " · " in row["expect"]["reply_must_not"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_converted_case(case: str) -> None:
    state = None
    for row in CASES[case]:
        where = f"{case}:{row['turn']}"
        now = datetime.fromisoformat(row["now"])
        out = ParserV2Output.model_validate(row["parser_out"])
        state = roll_session(state or new_state(now), now)
        state_out, actions = apply_turn(state, out, resolver=FakeResolver(row["resolver"]))
        assert len(actions) == len(out.asks) or actions and actions[-1].type == "hand_over", where

        expect = row["expect"]
        if "actions" in expect:
            # A converted "clarify" is any question back: a re-ask that reached the second
            # MISS is the hand-over offer (contract s.3), still a question, never a lookup.
            got = ["clarify" if a.type == "offer_handover" else a.type for a in actions]
            assert got == [e["type"] for e in expect["actions"]], (where, got, expect["actions"])
            for want, act in zip(expect["actions"], actions):
                if "domain" in want and isinstance(act, Lookup):
                    assert act.domain == want["domain"], where

        for act in actions:
            if isinstance(act, ClarifyAct):
                for opt in act.pending.options:
                    assert not UUID.search(opt.label), (where, opt.label)
        reply = answer.replay_reply(state_out, actions)
        assert " · " not in reply and not UUID.search(reply), (where, reply)

        state_out.last_turn_at = now
        pending = row.get("v1_pending_after")
        if pending and not any(isinstance(a, ClarifyAct) for a in actions):
            state_out = turn.after_v1(
                state_out,
                question_kind=pending.get("kind"),
                option_labels=[str(x) for x in pending.get("option_labels") or []],
                offer=pending.get("kind") in OFFER_KINDS,
            )
        state = state_out
