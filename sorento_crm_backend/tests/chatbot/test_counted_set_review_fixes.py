"""Reviewer pass on PR #833 at f2375f402 (26 Sep 2026): B1 to B4 and S2, as whole turns.

Each case runs `engine.run_turn` with the v3 verdict shape, the real resolver and the
real certificate register, with only the MCP tool call stubbed (same substrate as
`test_counted_set_no_paging.py`).

Owner rulings these pin: no silent defaults (clarify instead), a header count never
contradicts the rows under it, the chatbot never pages.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.chatbot.set_reply import legacy_lines, one_line_header, row_blocks, row_codes  # noqa: F401
from tests.chatbot.test_counted_set_no_paging import (  # noqa: F401 - fixture used by name
    _bare_verdict,
    _link_to_default_company,
    _tap_cert_verdict,
    _turn,
    seven_taps,
)
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import (
    _cert_fake_call_tool,
    _s4_codes_in,
    _s4_contact_id,
    _s4_envelope,
    _s4_real_resolve_entity,
    _s4_seed_contact,
    _s4_wire_engine,
    _stock_for,
    _warehouse,
)
from tests.chatbot.test_reverse_asks_owner_phrasings import (
    _availability_fake_call_tool,
    _seed_taps_and_basins,
    _verdict,
)

# --------------------------------------------------------------------------- #
# B1: a cert phrase whose remainder is a certificate PROPERTY, not a scheme,   #
# is the bare certificate leg.                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "phrase",
    ["valid cert", "cert validity", "certificate expiry", "cert no", "expired certificate", "cert number"],
)
def test_a_cert_property_phrase_is_the_bare_certificate_leg(phrase):
    from app.services.chatbot.lanes.business.predicate import derive_require

    assert derive_require({"requested_attributes": [phrase]}) == {"certificate": True}


@pytest.mark.parametrize(
    "phrase, scheme",
    [("PPS cert", "PPS"), ("sirim certificate", "sirim"), ("valid PPS cert", "PPS"), ("zzq cert", "zzq")],
)
def test_a_cert_phrase_naming_a_scheme_still_splits(phrase, scheme):
    from app.services.chatbot.lanes.business.predicate import derive_require

    assert derive_require({"requested_attributes": [phrase]}) == {"certificate": {"scheme": scheme}}


@pytest.mark.parametrize("phrase", ["valid cert", "cert validity", "certificate expiry"])
def test_which_tap_has_valid_cert_answers_the_whole_certified_set(
    session_factory, stub_parser, stub_access, seven_taps, phrase
):
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict(requested_attributes=[phrase], user_goal=f"which tap has {phrase}"))
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text=f"which tap has {phrase}")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    # header is the product-code answer's own intro naming the described set and its count.
    assert one_line_header(text).startswith("Here's what you want: taps with certificates (7)"), text
    assert _s4_codes_in(text) == set(codes), text


# --------------------------------------------------------------------------- #
# B2: an unknown scheme clarifies, naming the schemes on file, and fetches     #
# nothing (AC-1313 / AC-1321 as a whole turn).                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("phrase, word", [("zzq cert", "zzq"), ("PPS cert", "PPS")])
def test_an_unknown_scheme_names_the_schemes_on_file_and_lists_nothing(
    session_factory, stub_parser, stub_access, seven_taps, phrase, word
):
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict(requested_attributes=[phrase], user_goal=f"which tap has {phrase}"))
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text=f"which tap has {phrase}")

    assert f"no {word} certificates" in text, text
    assert "Schemes on file:" in text and "ZZT-CERT" in text, text
    assert "0 products have" not in text and "have certificates." not in text, text
    assert _s4_codes_in(text) == set(), text
    assert calls == [], calls


def test_an_unknown_scheme_as_an_attachment_type_entity_clarifies_too(
    session_factory, stub_parser, stub_access, seven_taps
):
    """The same miss through the attachment_type entity route (the reviewer measured it on
    main too): the header must never say zero over an unfiltered list."""
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(
        _tap_cert_verdict(
            requested_attributes=[],
            entities=[
                {"raw": "tap", "hint": "product_type", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": "zzq cert", "hint": "attachment_type", "canonical_code": "certificate", "current_message": True, "confident": True},
            ],
        )
    )
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has zzq cert")

    assert "Schemes on file:" in text, text
    assert _s4_codes_in(text) == set(), text
    assert calls == [], calls


def test_an_unknown_scheme_on_a_class_word_no_code_carries_still_names_the_schemes(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """"water tap" reaches the Tap class through its synonym and matches no product code,
    so the described set is the ONLY scope the turn has. A zero there is a miss to name,
    never "I need at least one filter"."""
    from tests.chatbot.test_lane_require import _certificate_for

    db = session_factory()
    taps, _basins = _seed_taps_and_basins(db, taps=2, basins=0)
    for tap in taps:
        _certificate_for(db, product_id=tap.id)
    db.commit()
    calls: list[dict[str, Any]] = []
    engine_mod, contact_id = _wired(session_factory, monkeypatch, db, _row_capped(db, calls, default_rows=50))
    stub_parser(
        _verdict(
            domain="product_attachment",
            intent="check_product_attachment",
            attribute="zzq cert",
            class_word="water tap",
            goal="which water tap has zzq cert",
        )
    )
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which water tap has zzq cert")

    assert "no zzq certificates" in text and "Schemes on file:" in text, text
    assert "I need at least one filter" not in text, text
    assert calls == [], calls


def test_an_honest_zero_names_the_set_and_fetches_nothing(session_factory, stub_parser, stub_access, monkeypatch):
    """AC-1319 as a whole turn: taps exist, none holds a certificate. The miss names the
    set and the predicate; it never says "0 taps have certificates." over a fetched list."""
    from tests.chatbot.test_lane_require import _seed_category_and_uom, _seed_registry, _tap_product

    db = session_factory()
    category_id, uom_id = _seed_category_and_uom(db)
    _seed_registry(db)
    for _ in range(3):
        _tap_product(db, category_id=category_id, uom_id=uom_id)
    db.commit()
    calls: list[dict[str, Any]] = []
    engine_mod, contact_id = _wired(session_factory, monkeypatch, db, _row_capped(db, calls, default_rows=50))
    stub_parser(_tap_cert_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    # miss for a described set names it the way its answer would have
    # (`described_set_phrase`), plural "with certificates", never the bare subject's
    # singular "with a certificate".
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): the product-code miss's own
    # structure, the described taps line by line, then "But no certificate matched".
    assert text.startswith("Here's what you want: taps (3)\n• "), text
    assert text.endswith("But no certificate matched these. Would you like me to escalate to marketing product team?"), text
    assert "have certificates." not in text and "has certificates." not in text, text
    assert calls == [], calls


# --------------------------------------------------------------------------- #
# B3: the header counts what the rows show. The tool caps ROWS, so a listed    #
# set asks for enough rows, and when the tool still cuts it short the header   #
# says how many products are really listed.                                    #
# --------------------------------------------------------------------------- #


def _row_capped(db, calls: list[dict[str, Any]], *, default_rows: int, hard_cap: int | None = None):
    """The cert tool with its row cap: `limit` rows (the tool's default when the caller
    sends none), never past `hard_cap`."""
    inner = _cert_fake_call_tool(db)

    def fake_call_tool(name: str, args: dict[str, Any]) -> Any:
        calls.append({"name": name, "args": dict(args)})
        sent = dict(args)
        limit = sent.get("limit") if isinstance(sent.get("limit"), int) else default_rows
        sent["limit"] = min(limit, hard_cap) if hard_cap else limit
        return inner(name, sent)

    return fake_call_tool


def _two_certs_per_tap(session_factory):
    from tests.chatbot.test_lane_require import _certificate_for, _s4_seed_seven_taps

    db = session_factory()
    codes = _s4_seed_seven_taps(db)
    from app.models.product import Product

    for product in db.query(Product).filter(Product.product_code.in_(codes)).all():
        _certificate_for(db, product_id=product.id)
    db.commit()
    return db, codes


def _wired(session_factory, monkeypatch, db, fake):
    contact_id = _s4_contact_id(f"rows{uuid.uuid4().hex[:6]}")
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    _link_to_default_company(session_factory, contact_id)
    engine_mod = _s4_wire_engine(
        session_factory, monkeypatch, resolve_entity=_s4_real_resolve_entity(db), fetch_mcp_call=fake
    )
    return engine_mod, contact_id


def test_a_listed_set_asks_the_tool_for_enough_rows(session_factory, stub_parser, stub_access, monkeypatch):
    """7 taps x 2 files = 14 rows against a tool whose default is 5 rows: the fetch must
    send a row `limit` that fits the listed set, so every tap reaches the reply."""
    db, codes = _two_certs_per_tap(session_factory)
    calls: list[dict[str, Any]] = []
    engine_mod, contact_id = _wired(session_factory, monkeypatch, db, _row_capped(db, calls, default_rows=5))
    stub_parser(_tap_cert_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    # header is the product-code answer's own intro naming the described set and its count.
    assert one_line_header(text) == "Here's what you want: taps with certificates (7)", text
    assert _s4_codes_in(text) == set(codes), text
    assert isinstance(calls[0]["args"].get("limit"), int) and calls[0]["args"]["limit"] >= 14, calls


def test_a_row_cap_that_still_cuts_the_set_says_how_many_are_listed(
    session_factory, stub_parser, stub_access, monkeypatch
):
    """The tool's own hard row cap (4 rows here) cuts 7 taps x 2 files to 2 taps: the
    header must say "showing 1 to 2", never claim the list is complete.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    named count says which ones inside the intro's parenthetical, never "Here are the
    first N.".
    """
    db, codes = _two_certs_per_tap(session_factory)
    calls: list[dict[str, Any]] = []
    engine_mod, contact_id = _wired(
        session_factory, monkeypatch, db, _row_capped(db, calls, default_rows=4, hard_cap=4)
    )
    stub_parser(_tap_cert_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")

    assert one_line_header(text).startswith("Here's what you want: taps with certificates (7, showing 1 to 2)"), text
    assert len(_s4_codes_in(text)) == 2, text


# --------------------------------------------------------------------------- #
# B4: the recount behind "how many should I show?" keeps the dealer's stock    #
# visibility (K4), and an availability row names its product (K5).             #
# --------------------------------------------------------------------------- #


def _dealer_on_availability(session_factory, monkeypatch, *, tag: str):
    from sqlalchemy import text as sa_text

    from app.models.access import StockVisibilityPolicy

    db = session_factory()
    taps, _basins = _seed_taps_and_basins(db, taps=3, basins=0)
    allowed = _warehouse(db)
    hidden = _warehouse(db)
    _stock_for(db, product_id=taps[0].id, warehouse_id=allowed.id, on_hand=37)
    _stock_for(db, product_id=taps[1].id, warehouse_id=hidden.id, on_hand=41)
    _stock_for(db, product_id=taps[2].id, warehouse_id=allowed.id, on_hand=12)
    db.commit()

    contact_id = _s4_contact_id(tag)
    _s4_seed_contact(session_factory, contact_id=contact_id, session_vars={"variables": {}})
    _link_to_default_company(session_factory, contact_id)
    internal_id = db.execute(
        sa_text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": contact_id}
    ).scalar()
    db.add(
        StockVisibilityPolicy(
            id=str(uuid.uuid4()), contact_id=internal_id, mode="availability", warehouse_ids=[allowed.id]
        )
    )
    db.commit()
    calls: list[dict[str, Any]] = []
    engine_mod = _s4_wire_engine(
        session_factory,
        monkeypatch,
        resolve_entity=_s4_real_resolve_entity(db),
        fetch_mcp_call=_availability_fake_call_tool(db, calls),
    )
    return engine_mod, contact_id, taps, calls


def _tap_stock_verdict():
    return _verdict(
        domain="inventory", intent="check_stock", attribute="stock", class_word="tap", goal="which tap got stock"
    )


def test_the_recount_after_how_many_keeps_the_dealers_stock_visibility(
    session_factory, stub_parser, stub_access, monkeypatch
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 1)
    engine_mod, contact_id, taps, calls = _dealer_on_availability(session_factory, monkeypatch, tag="dealerrecount")
    stub_parser(_tap_stock_verdict())
    stub_access()
    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    # header is the product-code answer's own intro naming the described set and its
    # count, never "N noun have leg.".
    first = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap got stock")
    # Amended to fix round 9 on PR #833 (never a paging question): the first answer lists
    # what fits, the count typed next continues with the other one.
    assert one_line_header(first) == "Here's what you want: taps with stock (2, showing 1 to 1)", first
    before = len(calls)

    stub_parser(_bare_verdict(top_n=1, continuation=True, user_goal="show 1"))
    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="1")

    assert one_line_header(text) == "Here's what you want: taps with stock (2)", text
    asked = {pid for c in calls[before:] for pid in (c["args"].get("product_ids") or [])}
    assert taps[1].id not in asked, asked
    assert asked <= {taps[0].id, taps[2].id} and len(asked) == 1, asked


def test_an_availability_row_is_numbered_with_its_product_code(
    session_factory, stub_parser, stub_access, monkeypatch
):
    engine_mod, contact_id, taps, calls = _dealer_on_availability(session_factory, monkeypatch, tag="dealerrow")
    stub_parser(_tap_stock_verdict())
    stub_access()

    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap got stock")

    listed = sorted([taps[0].product_code, taps[2].product_code])
    # Round 4 R3: the row is "N. <name> (<code>)", never a bare "1. ".
    rows = [b[0] for b in row_blocks(text)]
    assert row_codes(text) == listed, text
    for n, row in enumerate(rows, start=1):
        assert row.startswith(f"{n}. ") and row != f"{n}. ", text


# --------------------------------------------------------------------------- #
# S1: the answer to "how many should I show?" does not rest on the parser      #
# filling `top_n` for a bare "10".                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "parser_reads",
    [
        {"top_n": None, "reference_positions": []},
        {"top_n": None, "reference_positions": [3]},
    ],
    ids=["parser_null", "parser_reads_a_position"],
)
def test_a_bare_count_answers_the_question_whatever_the_parser_made_of_it(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch, parser_reads
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict())
    stub_access()
    _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")
    before = len(calls)

    stub_parser(_bare_verdict(message_type="casual", user_goal="3", **parser_reads))
    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="3")

    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a
    # named count says which ones inside the intro's parenthetical, never "Here are the
    # first N.".
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question): the
    # seven taps are one brand and nothing else splits them, so the first answer lists
    # what fits (5 of 7) and the count typed next continues: the last 2, count bare.
    assert one_line_header(text) == "Here's what you want: taps with certificates (7)", text
    assert len(_s4_codes_in(text)) == 2, text
    assert len(calls) == before + 1 and len(calls[-1]["args"]["product_ids"]) == 2, calls


@pytest.mark.parametrize(
    "message, count",
    [("10", 10), ("show 10", 10), ("10 please", 10), ("the first 10", 10), ("top 20", 20), ("Show me 5.", 5)],
)
def test_a_bare_count_message_is_read_as_the_count_while_the_question_is_open(message, count):
    from app.services.chatbot.turn_runtime import with_set_count_from_text

    out = with_set_count_from_text({"top_n": None, "reference_positions": [10]}, message, carried={"set_key": {}})
    assert out["top_n"] == count and out["reference_positions"] == []


@pytest.mark.parametrize(
    "verdict, message, carried",
    [
        ({"top_n": None}, "10", None),
        ({"top_n": None}, "which basin has 10 cert", {"set_key": {}}),
        ({"top_n": None}, "0", {"set_key": {}}),
        ({"top_n": 4}, "10", {"set_key": {}}),
        (
            {"top_n": None, "entities": [{"raw": "SRT10", "current_message": True}]},
            "10",
            {"set_key": {}},
        ),
    ],
    ids=["no_question_open", "not_only_a_count", "zero", "parser_named_one", "names_a_subject"],
)
def test_anything_but_a_bare_count_is_left_to_the_parser(verdict, message, carried):
    from app.services.chatbot.turn_runtime import with_set_count_from_text

    assert with_set_count_from_text(verdict, message, carried=carried) == verdict


# --------------------------------------------------------------------------- #
# N2: one reading of "a count was named" in the engine, apply and fetch.        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_count", [0, True], ids=["zero", "true"])
def test_a_count_that_names_nothing_still_arms_the_question(
    session_factory, stub_parser, stub_access, seven_taps, monkeypatch, bad_count
):
    from app.services.chatbot.lanes.business import answer

    monkeypatch.setattr(answer, "SET_LIST_MAX", 5)
    engine_mod, codes, calls, contact_id = seven_taps
    stub_parser(_tap_cert_verdict(top_n=bad_count))
    stub_access()
    first = _turn(engine_mod, session_factory, contact_id=contact_id, n=1, text="which tap has cert")
    assert one_line_header(first) == "Here's what you want: taps with certificates (7, showing 1 to 5)", first

    stub_parser(_bare_verdict(top_n=3, continuation=True, user_goal="show 3"))
    text = _turn(engine_mod, session_factory, contact_id=contact_id, n=2, text="3")

    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: never a paging question): the
    # seven taps are one brand and nothing else splits them, so the first answer lists
    # what fits (5 of 7) and the count typed next continues: the last 2, count bare.
    assert one_line_header(text) == "Here's what you want: taps with certificates (7)", text
