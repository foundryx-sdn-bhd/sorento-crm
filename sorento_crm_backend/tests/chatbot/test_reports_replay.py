"""REPORTS-HELP: offline replay of report conversations, graded the way the reports gate grades them.

`PLAN-reports-gate-replay-12oct.md`. The reports-all gate runs the live parser, which is not deterministic
(4 of 20 identical emissions differ run to run), so a red case cannot tell a code bug from a parser wobble.
Here every turn's parser verdict is the RECORDED one, so a red replay is a code bug, every time.

Built on `test_turn_replay.py` (the S6 gate), not beside it: the same four seams are stubbed from the case
file by the same `_install_stubs` (parser verdict, `check_access`, `resolve_entity`, MCP `call_tool`), and
the same `_compare` grades the structure. Three things differ, each because the reports gate does it:

- **State carries like the eval runner carries it.** `scripts/chatbot_eval/runner.py` starts each case with
  `state = {}` and `engine.py:79` sends it as `previous_conversation_state` whenever it is not None (so turn one
  says "remember nothing", `engine._inject_harness_session`), then the previous turn's `session_patch`; the
  contact row is never written. Replay does the same (`_envelope`), so a chain sees the memory the gate saw.
  `test_turn_replay.py` writes the row instead, which is right for its prod chains and wrong here.
- **The case seeds its own catalogue rows** (`seed`, `{table: [row, ...]}`, inserted ON CONFLICT DO NOTHING).
  The report lane resolves brand / sales agent / category words with direct SQL
  (`lanes/business/report_ask.py::_resolve_word` over `services.resolve_brand_token` and friends), not
  through `resolve_entity`, so a recorded brand id is only reachable when its row exists with that id.
- **The last turn is graded by the eval's own grader** (`grade`: `{"expect": <case expect>}`), via
  `scripts.chatbot_eval.grade.grade`, with tool errors read off the turn's stored trace by the eval's own
  `parse_trace`, the previous bot reply of the chain for the stuck check, and the chain's user texts for the
  header-echo rule. A case passes when the label is `correct` or `good_clarify`, as in the gate.

Where cases come from:

- `tests/chatbot/replay_reports/*.json`: hand-built or redacted chains that may live in git.
- `$REPORTS_REPLAY_DIR/**/*.json`: recorded gate chains kept OUT of git, like the eval data dir
  (`CONTRACT-chatbot-eval-runner.md`; refused inside the repo, as `cases.load_cases` refuses it). DEV split
  only: a failure prints the reply text and the file name, which the eval never does for a hidden case. Record
  with `scripts/chatbot_record_turn.py --group reports --out-dir <dir> --contact <case contact>` (read-only).
  That writes one chain per `test_run_id` for the contact, and the eval reuses one run id for a whole run, so
  split a chain by hand when variants were on or several cases share a contact. Then add `seed` (the rows
  behind the ids in the recorded tool args) and `grade` (the case's `expect`) by hand. Unset: only the
  in-repo cases run.

A case may carry `"xfail": "<reason>"` while its fix is open: strict, so the fix turns it into a failure that
says to drop the marker.
"""
from __future__ import annotations

import copy
import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot.test_engine import stub_parser  # noqa: F401 - fixture import
from tests.chatbot.test_turn_replay import (
    _apply_switches,
    _build_envelope,
    _compare,
    _install_stubs,
    _seed_contact,
)

IN_REPO_ROOT = Path(__file__).resolve().parent / "replay_reports"
PASSING_LABELS = ("correct", "good_clarify")


def _roots() -> list[Path]:
    roots = [IN_REPO_ROOT]
    extra = os.environ.get("REPORTS_REPLAY_DIR")
    if extra:
        path = Path(extra).expanduser().resolve()
        assert path.is_dir(), f"REPORTS_REPLAY_DIR={extra!r} is not a directory"
        repo = Path(__file__).resolve().parents[3]
        assert path != repo and repo not in path.parents, f"REPORTS_REPLAY_DIR={extra!r} is inside the repo; gate data stays out of git"
        roots.append(path)
    return roots


def _case_params() -> list:
    params = []
    for root in _roots():
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*.json") if p.is_file()):
            case_id = str(path.relative_to(root)) if root == IN_REPO_ROOT else f"$REPORTS_REPLAY_DIR/{path.relative_to(root)}"
            payload = json.loads(path.read_text())
            marks = []
            if payload.get("xfail"):
                marks.append(pytest.mark.xfail(strict=True, reason=str(payload["xfail"])))
            params.append(pytest.param(path, marks=marks, id=case_id))
    return params


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Same backstop as `test_turn_replay.py`: a parser call that slipped the stub fails, never waits on OpenAI."""
    real_connect = socket.socket.connect

    def _guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("localhost", "127.0.0.1", "::1") and not host.startswith("/"):
            raise AssertionError(f"replay made a network call to {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


# --------------------------------------------------------------------------- #
# Seed, envelope, grade
# --------------------------------------------------------------------------- #


def _seed_rows(session_factory, seed: dict[str, list[dict[str, Any]]] | None) -> None:
    """`{table: [row, ...]}`, each row inserted as given (ids included) unless a row with that key exists."""
    if not seed:
        return
    from sqlalchemy import text

    db = session_factory()
    try:
        for table, rows in seed.items():
            assert all(part.isidentifier() for part in table.split(".")), f"seed table {table!r}"
            for row in rows:
                cols = list(row)
                assert all(c.isidentifier() for c in cols), f"seed columns {cols!r}"
                values = {c: json.dumps(v) if isinstance(v, (dict, list)) else v for c, v in row.items()}
                db.execute(
                    text(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    values,
                )
        db.commit()
    finally:
        db.close()


def _envelope(turn: dict[str, Any], *, message_id: str, state: dict[str, Any] | None):
    """`_build_envelope`, then the eval runner's state carry: `{}` on turn one, then the previous turn's patch."""
    envelope = _build_envelope(turn, message_id=message_id)
    if state is None:
        return envelope
    return envelope.model_copy(update={"previous_conversation_state": copy.deepcopy(state)})


def _user_text(turn: dict[str, Any]) -> str:
    """The respond.io body's text: `message.message.message.text` (`contracts.py` Envelope)."""
    inner = ((turn.get("envelope") or {}).get("message") or {}).get("message") or {}
    return str((inner.get("message") or {}).get("text") or "")


def _trace_of(session_factory, turn_id: Any) -> list | None:
    """Through the ORM: the fixture's `schema_translate_map` sends the row to its scratch schema, which raw SQL
    naming `chatbot.turns` would miss (`tests/_pg_fixture.py`)."""
    if not turn_id:
        return None
    from app.models.chatbot_turn import ChatbotTurn

    db = session_factory()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        trace = row.trace if row is not None else None
    finally:
        db.close()
    return json.loads(trace) if isinstance(trace, str) else trace


def _grade(grade_spec: dict[str, Any], result: Any, trace: list | None, previous_reply: str | None, user_texts: list[str]):
    from scripts.chatbot_eval.cases import parse_case
    from scripts.chatbot_eval.engine import Outcome
    from scripts.chatbot_eval.grade import grade
    from scripts.chatbot_eval.trace import parse_trace

    case = parse_case(
        {"id": "replay", "split": "dev", "message": {"text": user_texts[-1] if user_texts else "-"}, "expect": grade_spec["expect"]}
    )
    _tokens, tool_errors, _notes = parse_trace(trace)
    reply = result.reply or {}
    outcome = Outcome(
        reply_text=str(reply.get("text") or ""),
        branch_kind=result.branch_kind,
        tool_errors=tool_errors,
        raw={"reply": result.reply, "delegate": result.delegate},
    )
    return grade(case.expect, outcome, previous_reply, user_texts)


# --------------------------------------------------------------------------- #
# The test
# --------------------------------------------------------------------------- #


def run_case(payload: dict[str, Any], case_id: str, session_factory, stub_parser, monkeypatch) -> dict[str, Any]:
    """Replays one case; returns `{failures, replies, verdict}` so a caller can assert more than the gate does."""
    from app.services.chatbot import engine as engine_mod

    turns = payload.get("turns") or []
    assert turns, f"{case_id}: no turns recorded"
    contact_id = ((turns[0].get("envelope") or {}).get("contact") or {}).get("id") or 999999999
    _seed_contact(session_factory, contact_id=contact_id)
    _seed_rows(session_factory, payload.get("seed"))

    failures: list[str] = []
    replies: list[str] = []
    user_texts: list[str] = []
    state: dict[str, Any] | None = {}
    result = None
    for step_no, turn in enumerate(turns, start=1):
        _apply_switches(session_factory, turn.get("switches"))
        tool_calls = _install_stubs(monkeypatch, stub_parser, turn=turn)
        envelope = _envelope(turn, message_id=f"ZZT-rpt-replay-{step_no}-{abs(hash(case_id))}", state=state)
        result = engine_mod.run_turn(envelope, session_factory=session_factory)
        _compare(case_id, step_no, turn.get("expected") or {}, result, failures, turn=turn, tool_calls=tool_calls)
        user_texts.append(_user_text(turn))
        replies.append(str((result.reply or {}).get("text") or ""))
        state = result.session_patch if result.session_patch is not None else state

    verdict = None
    if payload.get("grade"):
        previous = replies[-2] if len(replies) > 1 else None
        verdict = _grade(payload["grade"], result, _trace_of(session_factory, result.turn_id), previous, user_texts)
        if verdict.label not in PASSING_LABELS:
            failures.append(f"grade: {verdict.label} {verdict.reasons} on reply {replies[-1]!r}")
    return {"failures": failures, "replies": replies, "verdict": verdict}


@pytest.mark.parametrize("case_path", _case_params())
def test_report_replay(case_path: Path, session_factory, stub_parser, monkeypatch) -> None:
    payload = json.loads(case_path.read_text())
    out = run_case(payload, case_path.name, session_factory, stub_parser, monkeypatch)
    assert not out["failures"], f"{case_path.name}:\n  " + "\n  ".join(out["failures"])


def test_the_in_repo_corpus_is_not_empty() -> None:
    assert list(IN_REPO_ROOT.glob("*.json")), f"no report replay cases under {IN_REPO_ROOT}"


# --------------------------------------------------------------------------- #
# What the trace records vs what the MCP seam sends
# --------------------------------------------------------------------------- #


def test_a_recorded_legacy_report_name_answers_the_crm_report_call_it_became(monkeypatch, stub_parser) -> None:
    """The lane traces `crm_sales_report` with its own args (`lanes/business/__init__.py` trace.add "tool"); the
    MCP seam then sends `crm_report` with the registry key added (`services.REPORT_MEASURE_OF`, S5a). Replay
    answers the sent call from the recorded entry and logs it the way the trace recorded it."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    recorded = {"tool": "crm_sales_report", "args": {"product_code": "X1", "date_from": "2026-01-01"}, "envelope": {"lane_text": "ok"}}
    calls = _install_stubs(monkeypatch, stub_parser, turn={"tool_results": [recorded]})
    out = MCPRuntimeClient.call_tool(None, "crm_report", {"product_code": "X1", "date_from": "2026-01-01", "measure": "delivered_sales"})
    assert json.loads(out) == {"lane_text": "ok"}
    assert calls == [{"tool": "crm_sales_report", "args": {"product_code": "X1", "date_from": "2026-01-01"}}], calls


def test_a_domain_only_tool_trace_entry_is_not_a_recorded_call() -> None:
    """`turn/fetch.py::_fetch_one` adds `{"kind": "tool", "domain": ...}` with no name; the recorder reads it as a tool
    event with `tool` None, which no real call can ever match."""

    class _R:
        actions: list = []
        reply: dict = {}
        session_patch = None
        branch_kind = None

    failures: list[str] = []
    turn = {"tool_results": [{"tool": None, "args": None, "envelope": None}]}
    _compare("x", 1, {}, _R(), failures, turn=turn, tool_calls=[])
    assert failures == [], failures


def test_a_truncated_recorded_envelope_fails_as_a_recording_gap_not_an_engine_answer(monkeypatch, stub_parser) -> None:
    """`TurnTrace.add` caps a tool envelope at 8 KB (`trace.cap_envelope`); a capped one is not the answer the
    turn saw, so replaying it would grade the engine on a stub. Replay names the gap instead."""
    from app.services.ai_assistant_service import MCPRuntimeClient

    recorded = {"tool": "crm_report", "args": {}, "envelope": {"truncated": True, "bytes": 9000, "head": "{"}}
    _install_stubs(monkeypatch, stub_parser, turn={"tool_results": [recorded]})
    with pytest.raises(AssertionError, match="truncated"):
        MCPRuntimeClient.call_tool(None, "crm_report", {})


# --------------------------------------------------------------------------- #
# Review round 1 (B1, S1, S2, S4, S6)
# --------------------------------------------------------------------------- #

SAMPLE = IN_REPO_ROOT / "sample-ranking-named-runs-once.json"


def test_b1_the_grader_reads_the_trace_of_the_turn_that_ran(session_factory, stub_parser, monkeypatch) -> None:
    """The turn row lives in the fixture's scratch `chatbot` schema; raw SQL naming `chatbot.turns` misses it."""
    from app.services.chatbot import engine as engine_mod

    seen: list[Any] = []
    real = engine_mod.run_turn

    def _spy(envelope, **kw):
        result = real(envelope, **kw)
        seen.append(result.turn_id)
        return result

    monkeypatch.setattr(engine_mod, "run_turn", _spy)
    run_case(json.loads(SAMPLE.read_text()), "sample", session_factory, stub_parser, monkeypatch)
    trace = _trace_of(session_factory, seen[-1])
    assert trace and any(isinstance(e, dict) and e.get("stage") == "understood" for e in trace), trace


def _result(text: str) -> Any:
    class _R:
        reply = {"text": text}
        delegate = None
        branch_kind = "business_query"

    return _R()


def test_b1_a_tool_error_in_the_trace_grades_error_like_the_gate() -> None:
    trace = [{"stage": "looked_up", "raw": {"envelopes": [{"tool": "crm_report", "error": "crm_report returned HTTP 422"}]}}]
    verdict = _grade({"expect": {"outcome": "answer", "must": [{"text": "AGENT A"}]}}, _result("AGENT A"), trace, None, ["x"])
    assert verdict.label == "error", verdict


def test_s2_a_reply_repeating_the_previous_one_grades_stuck() -> None:
    verdict = _grade({"expect": {"outcome": "answer", "must": [{"text": "AGENT Z"}]}}, _result("Which period?"), [], "Which period?", ["a", "b"])
    assert verdict.label == "stuck", verdict


def test_s2_an_unmet_expectation_is_a_grade_failure(session_factory, stub_parser, monkeypatch) -> None:
    payload = json.loads(SAMPLE.read_text())
    payload["grade"]["expect"]["must"] = [{"text": "AGENT Z"}]
    out = run_case(payload, "sample-unmet", session_factory, stub_parser, monkeypatch)
    assert any(f.startswith("grade: wrong") for f in out["failures"]), out


def test_s1_turn_one_carries_an_empty_state_like_the_gate_runner() -> None:
    """`scripts/chatbot_eval/runner.py` starts each case with `state = {}` and `engine.py` sends it whenever it
    is not None, so the gate's first turn says "remember nothing" (`engine._inject_harness_session`)."""
    turn = json.loads(SAMPLE.read_text())["turns"][0]
    assert _envelope(turn, message_id="m", state={}).previous_conversation_state == {}


def test_s2_the_two_turn_chain_needs_turn_one_state(session_factory, stub_parser, monkeypatch) -> None:
    """Kill check for the carry: the chain case's turn 2 names no brand, so dropping the state loses the held ask."""
    payload = json.loads((IN_REPO_ROOT / "sample-ranking-period-answered-next-turn.json").read_text())
    import tests.chatbot.test_reports_replay as me

    real = me._envelope
    monkeypatch.setattr(me, "_envelope", lambda turn, *, message_id, state: real(turn, message_id=message_id, state={}))
    out = run_case(payload, "chain-no-state", session_factory, stub_parser, monkeypatch)
    assert out["failures"], out


def test_s4_a_replay_dir_inside_the_repo_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("REPORTS_REPLAY_DIR", str(IN_REPO_ROOT.parent))
    with pytest.raises(AssertionError, match="inside the repo"):
        _roots()


def test_s6_a_legacy_call_maps_back_even_when_the_turn_also_recorded_crm_report(monkeypatch, stub_parser) -> None:
    from app.services.ai_assistant_service import MCPRuntimeClient

    turn = {
        "tool_results": [
            {"tool": "crm_report", "args": {"measure": "delivered_amount"}, "envelope": {"lane_text": "ranking"}},
            {"tool": "crm_sales_report", "args": {"product_code": "X1"}, "envelope": {"lane_text": "sales"}},
        ]
    }
    calls = _install_stubs(monkeypatch, stub_parser, turn=turn)
    out = MCPRuntimeClient.call_tool(None, "crm_report", {"product_code": "X1", "measure": "delivered_sales"})
    assert json.loads(out) == {"lane_text": "sales"}
    assert calls[-1]["tool"] == "crm_sales_report", calls


def test_s1_run_case_sends_the_empty_state_on_turn_one(session_factory, stub_parser, monkeypatch) -> None:
    import tests.chatbot.test_reports_replay as me

    states: list[Any] = []
    real = me._envelope

    def _spy(turn, *, message_id, state):
        states.append(copy.deepcopy(state))
        return real(turn, message_id=message_id, state=state)

    monkeypatch.setattr(me, "_envelope", _spy)
    run_case(json.loads(SAMPLE.read_text()), "sample", session_factory, stub_parser, monkeypatch)
    assert states == [{}], states
