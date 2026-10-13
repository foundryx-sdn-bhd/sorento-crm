"""History budget (crew eval 14 Oct: a report follow-up turn sent 24,356 prompt tokens against
a 12k bar; the last 3 bot replies' report tables were the bulk). Every history message is
capped (`history.compact_reply`); what was asked stays in MEMORY (HELD report spec, LAST
ANSWER ROWS), so the model loses nothing it reads.

Budgets are in characters, the unit the cap is written in: no tokenizer runs in tests.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import (
    REPLY_CHAR_CAP,
    USER_CHAR_CAP,
    MemoryProfile,
    compact_exchanges,
    compact_reply,
    history_messages,
    render_memory,
)
from app.services.chatbot.pv2.schema import Period, ReportSpec
from app.services.chatbot.pv2.state import Held, Option
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, mk_state, out

#: The marker line `compact_reply` appends, at most.
MARKER_MAX = 60
#: Three capped exchanges, both sides, with markers.
HISTORY_BUDGET = 3 * (USER_CHAR_CAP + REPLY_CHAR_CAP + 2 * MARKER_MAX)
#: MEMORY with a held report, a pending question and 20 last rows, plus the history.
TURN_BUDGET = HISTORY_BUDGET + 3_000

REPLAY = Path(__file__).resolve().parent.parent / "replay_turns"


def _ranking_table(rows: int, by: str) -> str:
    """A report reply as the v1 formatter prints it: the Read-as header, then one block per
    row (the shape that made the probe's history large)."""
    lines = [f"*Read as:* sales amount, delivered sales, by {by}, last month, top {rows}", ""]
    for n in range(1, rows + 1):
        lines += [
            f"{n}. *{by.title()}:* SAMPLE TRADING SDN BHD NUMBER {n} - [A/C I]",
            f"*Amount:* MYR {n * 1234.5:,.2f}",
            f"*Quantity:* {n * 17}",
            "",
        ]
    return "\n".join(lines)


def _report_history() -> list[tuple[str, str]]:
    return [
        ("top 60 customers by sales last month", _ranking_table(60, "customer")),
        ("by qty", _ranking_table(60, "customer")),
        ("by salesman instead", _ranking_table(60, "sales agent")),
    ]


def _report_state():
    spec = ReportSpec(
        dataset="sales", measures=["qty"], group_by=["sales_agent"], top_n_per_group=None, filters=[],
        period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
        compare_period=None, grain=None, threshold=None, sort=None, top_n=60, basis="delivered",
    )
    rows = [Option(key=str(n), label=f"SAMPLE TRADING SDN BHD NUMBER {n}", payload={}) for n in range(1, 21)]
    return mk_state(Held(domain="sales", report=spec), last_rows=rows)


def test_a_long_reply_keeps_its_opening_lines_and_says_how_many_were_cut():
    table = _ranking_table(60, "customer")
    short = compact_reply(table)
    assert len(short) <= REPLY_CHAR_CAP + MARKER_MAX
    assert short.startswith("*Read as:* sales amount, delivered sales, by customer")
    kept = short.split("\n[... ")[0]
    assert table.startswith(kept)
    dropped = sum(1 for line in table.split("\n")[len(kept.split("\n")):] if line.strip())
    assert short.endswith(f"[... {dropped} more lines not shown]")


def test_a_reply_within_the_cap_is_untouched():
    assert compact_reply("Which figure should I rank by?") == "Which figure should I rank by?"
    assert compact_reply("") == ""


def test_a_single_overlong_line_is_cut_inside_the_line():
    short = compact_reply("x" * 5_000)
    assert short.startswith("x" * REPLY_CHAR_CAP) and len(short) <= REPLY_CHAR_CAP + MARKER_MAX


def test_three_report_turns_render_under_the_history_budget():
    full = history_messages(_report_history())
    capped = history_messages(compact_exchanges(_report_history()))
    full_chars = sum(len(m["content"]) for m in full)
    capped_chars = sum(len(m["content"]) for m in capped)
    assert full_chars > 15_000  # the probe's shape: three 60-row tables
    assert capped_chars <= HISTORY_BUDGET
    assert [m["role"] for m in capped] == ["user", "assistant"] * 3


def test_a_report_follow_up_turn_stays_under_the_turn_budget():
    state = _report_state()
    memory = render_memory(state, MemoryProfile(audience="staff", language="en"), NOW)
    history = history_messages(compact_exchanges(_report_history()))
    total = len(memory) + sum(len(m["content"]) for m in history) + len("<message>the 2nd one</message>")
    assert total <= TURN_BUDGET
    # What the cut replies said is still in MEMORY.
    assert "group_by=sales_agent" in memory and "top_n=60" in memory
    assert "1=SAMPLE TRADING SDN BHD NUMBER 1" in memory


def test_the_largest_recorded_replies_are_capped_too():
    texts = []
    for path in REPLAY.glob("**/*.json"):
        for t in json.loads(path.read_text())["turns"]:
            texts.append((t.get("expected") or {}).get("text") or "")
    biggest = sorted(texts, key=len, reverse=True)[:3]
    assert len(biggest[0]) > 30_000
    capped = compact_exchanges([("q", t) for t in biggest])
    assert all(len(b) <= REPLY_CHAR_CAP + MARKER_MAX for _, b in capped)


def test_run_turn_v2_hands_the_parser_the_capped_history():
    seen = {}

    def parse_parts(history, memory_block, text):
        seen["history"] = history

        class Parsed:
            output = out(ask(act="small_talk"))
            usage: dict = {}
            dropped: list = []

        return Parsed()

    ctx = turn.TurnV2Context(
        text="the 2nd one", now=NOW, raw_state=None, profile=MemoryProfile(), parse=None,
        parse_parts=parse_parts, resolver=FakeResolver(), load_exchanges=lambda since: _report_history(),
    )
    turn.run_turn_v2(ctx)
    assert len(seen["history"]) == 3
    assert all(len(b) <= REPLY_CHAR_CAP + MARKER_MAX for _, b in seen["history"])
    assert sum(len(u) + len(b) for u, b in seen["history"]) <= HISTORY_BUDGET
