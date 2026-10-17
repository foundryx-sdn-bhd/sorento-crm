"""CHAT-COMPOSER Sets round 3 red tests.

U1  a described set whose leg qualifies 0 of more than 50 matching products names the first 50 and pages.
U2  `near_miss_reply` prints no count lines (as #1744 did for `unknown_values_reply`).
C1  a set of 50 or fewer whose page is cut by the one-message budget writes the set carry, so "more" pages.
E1  "That is all N." stays as it is today.
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.lanes.business import answer as A
from tests.chatbot.test_composers_stock_xd import _set_gate

# the engine harness (real resolver, faithful stock tool)
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_set_stock_render import _set_ask, make_chat, world  # noqa: F401

MORE = 'Reply "more" to see the next 50.'


# --------------------------------------------------------------------------- #
# U1: described_members_reply
# --------------------------------------------------------------------------- #


def _members_predicate(*, codes=None, breakdown=None, total=182):
    members = {"total": total}
    if codes is not None:
        members["codes"] = codes
    if breakdown is not None:
        members["breakdown"] = breakdown
    return {
        "require": {"stock": True},
        "class_labels": ["water closet"],
        "description": [
            {"key": "trap_type", "label": "Trap", "value": "P trap", "kind": "enum"},
            {"key": "class", "value": "Water Closet", "kind": "enum"},
        ],
        "members": members,
    }


BREAKDOWN = {
    "key": "brand",
    "rows": [{"value": "Bravat", "count": 79}, {"value": "Cabana", "count": 57}, {"value": "Sorento", "count": 46}],
}
CODES = [f"SRTWC{i:03d}" for i in range(50)]


def test_u1_gate_off_golden_keeps_the_count_lines(monkeypatch):
    _set_gate(monkeypatch, False)
    out = A.described_members_reply(_members_predicate(breakdown=BREAKDOWN), "sales")
    assert out == (
        "Here's what you want: P trap water closets (182)\n"
        "• Bravat water closets: 79\n• Cabana water closets: 57\n• Sorento water closets: 46"
        "\n\nBut no stock matched these. Would you like me to escalate to sales team?"
    )


def test_u1_gate_off_up_to_fifty_names_codes_unchanged(monkeypatch):
    _set_gate(monkeypatch, False)
    out = A.described_members_reply(_members_predicate(codes=CODES[:3], total=3), None)
    assert out == (
        "Here's what you want: P trap water closets (3)\n• SRTWC000\n• SRTWC001\n• SRTWC002"
        "\n\nBut no stock matched these."
    )


def test_u1_gate_on_names_the_first_fifty_and_pages(monkeypatch):
    """The producer sends the first 50 member codes beside the total (never a breakdown)."""
    _set_gate(monkeypatch, True)
    out = A.described_members_reply(_members_predicate(codes=CODES), None)
    assert out.startswith("Here's what you want: P trap water closets (182, showing 1 to 50)\n")
    assert [ln for ln in out.splitlines() if ln.startswith("• ")] == [f"• {c}" for c in CODES]
    assert not re.search(r"^• .*: \d+", out, re.MULTILINE), out
    last_code_line = out.index(f"\u2022 {CODES[-1]}")
    assert out[last_code_line:].startswith(f"\u2022 {CODES[-1]}\n\n{MORE}\n\n"), out[last_code_line:]
    assert out.endswith("But no stock matched these."), out[-120:]


def test_u1_gate_on_keeps_the_offer(monkeypatch):
    _set_gate(monkeypatch, True)
    out = A.described_members_reply(_members_predicate(codes=CODES), "sales")
    assert out.endswith("But no stock matched these. Would you like me to escalate to sales team?"), out[-160:]
    assert f"\u2022 {CODES[-1]}\n\n{MORE}\n\n" in out


def test_u1_gate_on_up_to_fifty_is_unchanged(monkeypatch):
    _set_gate(monkeypatch, True)
    assert A.described_members_reply(_members_predicate(codes=CODES[:3], total=3), None) == (
        "Here's what you want: P trap water closets (3)\n• SRTWC000\n• SRTWC001\n• SRTWC002"
        "\n\nBut no stock matched these."
    )


# --------------------------------------------------------------------------- #
# U2: near_miss_reply
# --------------------------------------------------------------------------- #


def _near_predicate():
    return {
        "require": {"stock": True},
        "class_labels": ["bathtub"],
        "description": [{"key": "finish", "label": "Finish", "value": "Gunmetal", "kind": "enum"}],
        "near_miss": {
            "value": "Gunmetal",
            "label": "Finish or colour",
            "class_labels": ["bathtub"],
            "other_values": [{"value": "Chrome", "count": 5}, {"value": "White", "count": 7}],
        },
    }


def test_u2_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    out = A.near_miss_reply(_near_predicate(), "sales")
    assert "• Chrome bathtubs: 5" in out and "• White bathtubs: 7" in out
    assert "Couldn't find: gunmetal (finish or colour)." in out


def test_u2_gate_on_no_count_lines(monkeypatch):
    _set_gate(monkeypatch, True)
    out = A.near_miss_reply(_near_predicate(), "sales")
    assert not re.search(r"^• .*: \d+", out, re.MULTILINE), out
    assert "Chrome" not in out and "White" not in out
    assert "Couldn't find gunmetal (finish or colour)." in out or "Couldn't find: gunmetal (finish or colour)." in out
    assert out.endswith("Would you like me to escalate to sales team?")


# --------------------------------------------------------------------------- #
# C1: a cut page of a set of 50 or fewer is carried
# --------------------------------------------------------------------------- #


def _session_set_page(session_factory, chat):
    from sqlalchemy import text as sa_text

    db = session_factory()
    row = db.execute(
        sa_text("SELECT session_state FROM respond_contacts WHERE respond_io_id = :c"), {"c": chat.contact_id}
    ).scalar()
    return row


def _more_verdict():
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        message_type="business_query",
        intent_hint="check_stock",
        domain_hint="inventory",
        continuation=True,
        entities=[],
        requested_attributes=[],
        user_goal="more",
    )


def _codes(text):
    return list(dict.fromkeys(re.findall(r"\*Product Code:\* (\S+)", text)))


def _seed_basins(world, n):
    for i in range(n):
        world.basin(world.sorento, f"ZZC{i:02d}", 10, 4)
    world.commit()


def test_c1_gate_off_a_set_of_forty_prints_every_row_and_no_more_line(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    _set_gate(monkeypatch, False)
    _seed_basins(world, 40)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    reply = chat.say("any wash basin has stock", _set_ask())
    assert 'Reply "more"' not in reply
    assert len(_codes(reply)) == 40


def test_c1_a_cut_page_of_a_small_set_is_carried_so_more_pages(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    _set_gate(monkeypatch, True)
    _seed_basins(world, 40)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    first = chat.say("any wash basin has stock", _set_ask())
    shown = len(_codes(first))
    assert 0 < shown < 40, "the set of 40 is cut by the one-message budget"
    assert re.search(r'Reply "more" to see the next \d+\.', first), first[-200:]
    second = chat.say("more", _more_verdict())
    assert re.search(rf"showing {shown + 1} to \d+\)", second), second[:200]
    assert _codes(second)[0] not in _codes(first), second[:300]


# --------------------------------------------------------------------------- #
# E1: the end-of-list text is unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gate", [False, True])
def test_e1_that_is_all_stays_as_it_is(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    out = A.build_set_header(182, 0, "P trap water closets", {}, offset=182, exhausted=True)
    assert out == "Here's what you want: P trap water closets (182). That is all 182."


# --------------------------------------------------------------------------- #
# U1 at the engine: a described set whose leg qualifies 0 of more than 50 matches
# --------------------------------------------------------------------------- #


def _seed_zero_stock(world, per_brand=(30, 25)):
    for i in range(per_brand[0]):
        world.basin(world.sorento, f"ZZU{i:02d}", 0)
    for i in range(per_brand[1]):
        world.basin(world.mocha, f"ZZV{i:02d}", 0)
    world.commit()


def test_u1_engine_gate_off_keeps_the_count_lines(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, False)
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    reply = chat.say("any wash basin has stock", _set_ask())
    assert re.search(r"^• .* wash basins: \d+$", reply, re.MULTILINE), reply
    assert "But no stock matched these." in reply
    assert 'Reply "more"' not in reply
    assert not _codes(reply)


def test_u1_engine_gate_on_names_the_first_fifty_and_a_bare_more_pages(
    session_factory, monkeypatch, stub_parser, stub_access, world
):
    _set_gate(monkeypatch, True)
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    first = chat.say("any wash basin has stock", _set_ask())
    assert "(55, showing 1 to 50)" in first.split("\n\n", 1)[0], first[:200]
    assert not re.search(r"^• .* wash basins: \d+$", first, re.MULTILINE), first
    listed = re.findall(r"^• (\S+)$", first, re.MULTILINE)
    assert len(listed) == 50 and set(listed) <= world.codes, first
    assert re.search(r'Reply "more" to see the next 5\.', first)
    assert first.rstrip().endswith("Would you like me to escalate to warehouse team?"), first[-160:]
    assert first.index('Reply "more"') < first.index("But no stock matched these.")
    second = chat.say("more", _more_verdict())
    rest = set(re.findall(r"^• (\S+)$", second, re.MULTILINE)) | set(_codes(second))
    assert rest and rest.isdisjoint(listed) and rest <= world.codes, second[:300]
    assert re.search(r"showing 51 to 55\)", second), second[:200]
