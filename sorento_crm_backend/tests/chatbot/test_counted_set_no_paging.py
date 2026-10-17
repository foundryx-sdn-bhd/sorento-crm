"""Counted set answers without paging (owner ruling, 26 Sep 2026 01:55Z, PR #833).

"drop paging. No 'Showing 5', no more / next / lagi carry. A counted set that fits one
WhatsApp message (about 50 rows) is listed in full under its count header; a longer one
states the count and asks how many to show, or offers a narrower filter. Apply to every
leg (certificate, stock, attachment_type, promotion, incoming)."

- up to `answer.SET_LIST_MAX` (50) qualifying: every one listed, header "N taps have X."
- more than that: the count, the question, and no rows. The set's description is kept
  so the answer to the question (the parser's own count key, `top_n`) lists that many.
- a count in the ask itself ("show 20 taps with cert") lists that many.
- "more" / "next" / "lagi" (the verdict's `continuation`) pages nothing.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.chatbot.set_reply import legacy_lines, one_line_header, row_blocks, row_codes  # noqa: F401
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import (
    _cert_capturing_fake_call_tool,
    _s4_codes_in,
    _s4_contact_id,
    _s4_envelope,
    _s4_real_resolve_entity,
    _s4_seed_contact,
    _s4_seed_seven_taps,
    _s4_session_vars,
    _s4_wire_engine,
)


def _tap_cert_verdict(**overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    base = dict(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal="which tap has cert",
        requested_attributes=["cert"],
        entities=[
            {
                "raw": "tap",
                "hint": "product_type",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
    )
    base.update(overrides)
    return _parser_output(**base)


def _bare_verdict(**overrides: Any) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    base = dict(intent_hint=None, domain_hint=None, entities=[], user_goal=None)
    base.update(overrides)
    return _parser_output(**base)


def _turn(engine_mod, session_factory, *, contact_id: str, n: int, text: str) -> str:
    turn = engine_mod.run_turn(
        _s4_envelope(contact_id=contact_id, message_id=f"ZZT-nopage-{contact_id}-{n}", text=text),
        session_factory=session_factory,
    )
    assert turn.status == "done", turn.error
    return (turn.reply or {}).get("text") or ""


def _link_to_default_company(session_factory, contact_id: str) -> None:
    """The recount of a carried set runs on the ENGINE's session, which is scoped to the
    contact's own companies (`engine._contact_company_scope`); the first answer's
    resolver runs on the test's session here. A workspace and a company link make the
    two agree, the way they always do in production (same shape as
    `test_rearch_s3_attribute_first._link_contact_company`)."""
    from sqlalchemy import text as sa_text

    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO, _seed_workspace

    workspace_id = _seed_workspace(session_factory)
    db = session_factory()
    db.execute(
        sa_text("UPDATE respond_contacts SET workspace_id = :w WHERE respond_io_id = :c"),
        {"w": workspace_id, "c": contact_id},
    )
    row_id = db.execute(
        sa_text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": contact_id}
    ).scalar()
    db.execute(
        sa_text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "VALUES (gen_random_uuid(), :rcid, :cid)"
        ),
        {"rcid": row_id, "cid": SORENTO},
    )
    db.commit()


@pytest.fixture()
def seven_taps(session_factory, monkeypatch):
    db = session_factory()
    codes = _s4_seed_seven_taps(db)
    calls: list[dict[str, Any]] = []
    contact_id = _s4_contact_id(f"nopage{uuid.uuid4().hex[:6]}")
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    _link_to_default_company(session_factory, contact_id)
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_cert_capturing_fake_call_tool(db, calls),
    )
    return engine_mod, codes, calls, contact_id


# --------------------------------------------------------------------------- #
# The header, per leg                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "require, base",
    [
        ({"certificate": True}, "Here's what you want: taps with certificates"),
        ({"certificate": {"scheme": "PPS"}}, "Here's what you want: taps with PPS certificates"),
        ({"stock": True}, "Here's what you want: taps with stock"),
        ({"incoming": True}, "Here's what you want: taps with incoming stock"),
        ({"promotion": True}, "Here's what you want: taps with a promotion"),
        ({"attachment_type": "Product Photos"}, "Here's what you want: taps with product photos"),
    ],
)
def test_every_leg_lists_in_full_under_its_count_and_never_says_showing(require, base):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    set answer's header is the product-code answer's own intro naming the described set
    and its count, never "N noun have leg."."""
    from app.services.chatbot.lanes.business.answer import build_set_header

    header = build_set_header(7, 7, "taps", require)
    assert header == f"{base} (7)", header
    assert "Showing" not in header


@pytest.mark.parametrize(
    "require, base",
    [
        ({"certificate": True}, "Here's what you want: taps with certificates"),
        ({"stock": True}, "Here's what you want: taps with stock"),
        ({"incoming": True}, "Here's what you want: taps with incoming stock"),
        ({"promotion": True}, "Here's what you want: taps with a promotion"),
        ({"attachment_type": "Product Photos"}, "Here's what you want: taps with product photos"),
    ],
)
def test_every_leg_past_the_list_limit_states_the_count_and_asks(require, base):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    from app.services.chatbot.lanes.business.answer import SET_LIST_MAX, build_set_header

    assert SET_LIST_MAX == 50
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: "no cap", "always break it
    # down"): the full count and the resolver's breakdown by the next attribute, one line
    # each, never a paging question.
    # SET-STOCK-RENDER (SSR-5/6): no breakdown; the first SET_LIST_MAX products are listed and
    # the header says so, "(1,256, showing 1 to 50)".
    header = build_set_header(1256, SET_LIST_MAX, "taps", require)
    assert header == f"{base} (1,256, showing 1 to 50)", header
    assert "•" not in header, header
    assert "How many" not in header and "too many" not in header, header
    assert "Showing" not in header


def test_a_named_count_below_the_total_says_how_many_are_listed():
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    named count says which ones inside the intro's parenthetical count, never "Here are
    the first N."."""
    from app.services.chatbot.lanes.business.answer import build_set_header

    assert build_set_header(60, 20, "taps", {"stock": True}) == (
        "Here's what you want: taps with stock (60, showing 1 to 20)"
    )


def test_the_paging_vocabulary_is_gone():
    from app.services.chatbot.lanes.business import answer

    for name in (
        "is_more_reply",
        "build_set_page_header",
        "build_set_page_exhausted_message",
        "build_set_page_narrow_message",
    ):
        assert not hasattr(answer, name), name


# --------------------------------------------------------------------------- #
# Whole turns                                                                  #
# --------------------------------------------------------------------------- #


def test_a_set_that_fits_is_listed_in_full(session_factory, stub_parser, stub_access, seven_taps):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    assert one_line_header(text).startswith("Here's what you want: taps with certificates (7)"), text
    assert "Showing" not in text, text
    assert _s4_codes_in(text) == set(codes), text
    assert len(calls) == 1 and len(calls[0]["args"]["product_ids"]) == 7, calls


def test_more_after_a_listed_set_pages_nothing(session_factory, stub_parser, stub_access, seven_taps):
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()
    _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    stub_parser(_bare_verdict(message_type="clarification", user_goal="more", continuation=True))
    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="more")

    assert "Showing" not in text, text
    assert "taps have certificates" not in text, text
    assert len(calls) == 1, calls


def test_a_longer_set_states_the_count_asks_and_lists_nothing(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    # header is the product-code answer's own intro naming the described set and its count.
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question): the
    # seven taps are one brand and nothing else splits them, so what fits is listed.
    assert one_line_header(text) == "Here's what you want: taps with certificates (7, showing 1 to 5)", text
    assert "How many" not in text, text
    assert len(_s4_codes_in(text)) == 5, text
    assert "Showing" not in text, text


def test_answering_how_many_lists_that_many_and_then_more_pages_nothing(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()
    _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")
    before = len(calls)

    stub_parser(_bare_verdict(top_n=3, continuation=True, user_goal="show 3"))
    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="3")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    # named count says which ones inside the intro's parenthetical, never "Here are the
    # first N.".
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question): the
    # first answer listed what fits (5 of 7, nothing splits them), so the count typed next
    # continues from there: the last 2, and the count is bare again.
    # SET-STOCK-RENDER (SSR-14): a continuation page says its range, the last page too.
    assert one_line_header(text) == "Here's what you want: taps with certificates (7, showing 6 to 7)", text
    assert len(_s4_codes_in(text)) == 2, text
    assert len(calls) == before + 1 and len(calls[-1]["args"]["product_ids"]) == 2, calls
    # W4 (owner hand test round 2): the set stays carried past what was listed, so the
    # customer's own "another N" continues it; a bare "more" still pages nothing.
    import json

    stored = json.dumps(_s4_session_vars(session_factory, contact_id))
    assert '"shown": 7' in stored, stored

    stub_parser(_bare_verdict(message_type="clarification", user_goal="more", continuation=True))
    text3 = _turn(engine_mod, session_factory, contact_id=contact_id, n=3, text="more")
    assert "Here are" not in text3 and "Showing" not in text3, text3
    assert len(calls) == before + 1, calls


def test_how_many_is_capped_at_the_list_limit(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict(top_n=40, user_goal="show 40 taps with cert"))
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="show 40 taps with cert")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    # named count says which ones inside the intro's parenthetical, never "Here are the
    # first N.".
    assert one_line_header(text).startswith("Here's what you want: taps with certificates (7, showing 1 to 5)"), text
    assert len(_s4_codes_in(text)) == 5, text


def test_a_count_in_the_ask_itself_lists_that_many(session_factory, stub_parser, stub_access, seven_taps):
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict(top_n=2, user_goal="show 2 taps with cert"))
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="show 2 taps with cert")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    # named count says which ones inside the intro's parenthetical, never "Here are the
    # first N.".
    assert one_line_header(text).startswith("Here's what you want: taps with certificates (7, showing 1 to 2)"), text
    assert len(_s4_codes_in(text)) == 2, text
    assert len(calls[0]["args"]["product_ids"]) == 2, calls


def test_a_recount_that_finds_nothing_never_calls_the_tool_unfiltered(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch
):
    """The carried set can be empty by the time the count comes back (the certificates
    were withdrawn in between). The tool must not be called with no product filter."""
    from app.services.chatbot import turn_runtime
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()
    _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")
    before = len(calls)

    real = turn_runtime.page_the_set

    def emptied(db, carry, **kwargs):
        predicate, _ids = real(db, carry, **kwargs)
        return {**predicate, "qualifying_total": 0}, []

    monkeypatch.setattr(turn_runtime, "page_the_set", emptied)
    stub_parser(_bare_verdict(top_n=3, continuation=True, user_goal="show 3"))
    _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="3")

    assert len(calls) == before, calls
