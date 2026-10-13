"""Table-driven apply_turn tests (CONTRACT-PV2-13oct.md section 3 and 7).

One JSONL object per TURN in `tables/carry_*.jsonl`. `state_in` null = carry the previous
turn's output within the case (a case's first turn: a fresh state). `roll_session` runs before
`apply_turn`, as turn.py does. Expect keys (all optional but `actions`):

  actions       one dict per action: type, plus any of domain, about (str or list of
                acceptable values), not_found, reask, team_key, held_refs (Lookup.held refs)
  held          subset of the held view {domain, refs {kind: [keys]}, attachment_type,
                specs [[attribute, value]], period, report, idea_draft_id}
  pending       null | {kind?, option_keys?, misses?}  ({} = a pending exists)
  topics        domains newest first;  topics_refs  refs-by-kind newest first
  options       labels of state.pending.options
  last_rows_n   len(state.last_rows)
  entity_lookups  exact list of resolver entity lookup keys made this turn
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import ParserV2Output
from app.services.chatbot.pv2.state import (
    ClarifyAct,
    ConversationState,
    Held,
    Lookup,
    OfferHandover,
    Option,
    PendingQuestion,
    new_state,
    roll_session,
)
from tests.chatbot.pv2.carry_fakes import FakeResolver

TABLES = Path(__file__).parent / "tables"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _load() -> "OrderedDict[str, list[dict[str, Any]]]":
    cases: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for path in sorted(TABLES.glob("carry_*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                cases.setdefault(row["case"], []).append(row)
    for case, rows in cases.items():
        assert [r["turn"] for r in rows] == sorted(r["turn"] for r in rows), case
    return cases


CASES = _load()
PARAMS = [
    pytest.param(case, i, id=f"{case}:{row['turn']}")
    for case, rows in CASES.items()
    for i, row in enumerate(rows)
]


def _held_view(h: Held) -> dict[str, Any]:
    return {
        "domain": h.domain,
        "refs": {k: [r.key for r in v] for k, v in h.refs.items() if v},
        "attachment_type": h.attachment_type,
        "specs": [[s.attribute, s.value] for s in h.specs],
        "period": h.period.model_dump(mode="json") if h.period else None,
        "report": h.report.model_dump(mode="json") if h.report else None,
        "idea_draft_id": h.idea_draft_id,
    }


def _refs_view(h: Held) -> dict[str, list[str]]:
    return _held_view(h)["refs"]


def _run_turn(prev: ConversationState | None, row: dict[str, Any]):
    now = datetime.fromisoformat(row["now"])
    if row["state_in"] is not None:
        state = ConversationState.model_validate(row["state_in"])
    else:
        state = prev if prev is not None else new_state(now)
    rolled = roll_session(state, now)
    out = ParserV2Output.model_validate(row["parser_out"])
    resolver = FakeResolver(row["resolver"])
    new, actions = apply_turn(rolled, out, resolver=resolver)
    return rolled, new, actions, resolver, out


def _check_invariants(rolled, new, actions, out):
    assert new.turn_no == rolled.turn_no + 1
    assert [a.ask_index for a in actions] == list(range(len(actions)))
    assert len(actions) == len(out.asks)
    last_pending = None
    for a in actions:
        if isinstance(a, (ClarifyAct, OfferHandover)):
            assert isinstance(a.pending, PendingQuestion)
            for o in a.pending.options:
                assert isinstance(o, Option)
                assert not UUID_RE.search(o.label), o.label
            if not getattr(a, "not_found", False):
                last_pending = a.pending
    if last_pending is not None:
        assert new.pending == last_pending


def _check_action(i: int, exp: dict[str, Any], act: Any):
    assert act.type == exp["type"], f"action {i}: {act!r}"
    if "domain" in exp:
        assert isinstance(act, Lookup) and act.domain == exp["domain"]
    if "held_refs" in exp:
        assert _refs_view(act.held) == exp["held_refs"]
    if "about" in exp:
        want = exp["about"] if isinstance(exp["about"], list) else [exp["about"]]
        assert act.about in want
    for k in ("not_found", "reask"):
        if k in exp:
            assert getattr(act, k) is exp[k]
    if "team_key" in exp:
        assert act.team_key == exp["team_key"]


def _check_expect(exp, rolled, new, actions, resolver):
    assert len(actions) == len(exp["actions"]), actions
    for i, (e, a) in enumerate(zip(exp["actions"], actions)):
        _check_action(i, e, a)
    view = _held_view(new.held)
    for k, v in exp.get("held", {}).items():
        assert view[k] == v, f"held.{k}"
    if "pending" in exp:
        p = exp["pending"]
        if p is None:
            assert new.pending is None
        else:
            assert new.pending is not None
            if "kind" in p:
                assert new.pending.kind == p["kind"]
            if "option_keys" in p:
                assert [o.key for o in new.pending.options] == p["option_keys"]
            if "misses" in p:
                assert new.pending.misses == p["misses"]
    if "topics" in exp:
        assert [t.domain for t in new.topics] == exp["topics"]
    if "topics_refs" in exp:
        assert [_refs_view(t) for t in new.topics] == exp["topics_refs"]
    if "options" in exp:
        assert new.pending is not None
        assert [o.label for o in new.pending.options] == exp["options"]
    if "last_rows_n" in exp:
        assert len(new.last_rows) == exp["last_rows_n"]
    if "entity_lookups" in exp:
        assert resolver.entity_lookups == exp["entity_lookups"]


def test_tables_are_present():
    assert len(CASES) >= 25


@pytest.mark.parametrize("case,idx", PARAMS)
def test_carry_table(case: str, idx: int):
    prev: ConversationState | None = None
    for i, row in enumerate(CASES[case][: idx + 1]):
        rolled, new, actions, resolver, out = _run_turn(prev, row)
        if i == idx:
            _check_invariants(rolled, new, actions, out)
            _check_expect(row["expect"], rolled, new, actions, resolver)
        # turn.py stamps last_turn_at after the turn; apply_turn does not.
        prev = new.model_copy(update={"last_turn_at": datetime.fromisoformat(row["now"])})
