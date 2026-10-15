"""CHAT-FIT-LINE: every link kind resolves, and a linked answer opens with the fit line.

UAC `documentation/plans/chatbot/chat-fit-line-15oct-acceptance-criteria.md` AC-1..AC-15;
card `CARD-chat-fit-line-15oct.md` (crew answers Q1 a, Q2 a, Q3 a).

Red tests written before the code. Harness reused from `test_seat_as_product.py`: the parse is
mocked, the resolver and the answer path are real. Every parity test runs one turn twice on one
database: the control with the linked codes typed directly, then the ask through the link; the
asked reply must equal `<fit line> + blank line + control reply`. Fake ZZT codes only.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_outstanding_lane import _seed_contact
from tests.chatbot.test_seat_as_product import _codes, _product, _scoped, _turn, _verdict

# --------------------------------------------------------------------------- seed
WC_A, COVER_A = "ZZTWC7101", "ZZTSCBD7101"
WC_B, COVERS_B = "ZZTWC7102", ["ZZTSCBD7102", "ZZTSCBD7102A"]
WC_NONE = "ZZTWC7109"
CAB1, MIRROR1, SHELF1 = "ZZTBF7201", "ZZTMR7201", "ZZTSV7201"
CAB2 = "ZZTBF7202"  # shares SHELF1
CAB3, MIRROR3 = "ZZTBF7203", "ZZTMR7203"  # mirror only
CAB_NONE = "ZZTBF7209"
MIRROR_NONE, SHELF_NONE = "ZZTMR7209", "ZZTSV7209"  # accessories on no link
SC_NONE = "ZZTWC7150-SC"  # an -SC code is a seat cover by the data

LINKS = (
    (COVER_A, WC_A, "seat_cover"),
    (COVERS_B[0], WC_B, "seat_cover"),
    (COVERS_B[1], WC_B, "seat_cover"),
    (MIRROR1, CAB1, "mirror"),
    (SHELF1, CAB1, "shelf"),
    (SHELF1, CAB2, "shelf"),
    (MIRROR3, CAB3, "mirror"),
)
ALL_CODES = (
    WC_A, COVER_A, WC_B, *COVERS_B, WC_NONE, CAB1, MIRROR1, SHELF1, CAB2, CAB3, MIRROR3,
    CAB_NONE, MIRROR_NONE, SHELF_NONE, SC_NONE,
)


def fits(kind: str | None, value: str | None = "x", file_word: str | None = None) -> dict[str, Any]:
    return {"filters": [{"key": "fits", "value": value, "unit": None, "file_word": file_word, "kind": kind}]}


def _seed(session_factory) -> None:
    from app.models.product_compatibility import ProductCompatibility
    from app.models.product_spec import ProductSpecifications
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    rows = {c: seed_product(db, company_id=DEFAULT_COMPANY_ID, code=c) for c in ALL_CODES}
    for accessory, host, kind in LINKS:
        db.add(
            ProductCompatibility(
                company_id=DEFAULT_COMPANY_ID, seat_cover_product_id=rows[accessory].id,
                wc_product_id=rows[host].id, source="manual", kind=kind,
            )
        )
    for code, product_type in ((MIRROR_NONE, "mirror"), (SHELF_NONE, "towel_shelf")):
        db.add(ProductSpecifications(product_id=rows[code].id, values={"product_type": {"value": product_type}}))
    db.commit()


def _set_profile(session_factory, tier: str) -> None:
    import json

    from sqlalchemy import text

    db = session_factory()
    db.execute(text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb)"), {"p": json.dumps({"tier": tier})})
    db.commit()


def _parity(session_factory, monkeypatch, *, typed: list[str], asked: list[str], text_typed: str, text_asked: str,
            find: Any, domain: str | None = "inventory", intent: str | None = "check_stock",
            tier: str | None = None):
    """(control reply, control calls), (asked reply, asked calls) on one database."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    if tier:
        _set_profile(session_factory, tier)
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_fit_parity")
    control = _turn(
        session_factory, monkeypatch, text=text_typed, msg_id="ZZT-fit-control",
        domain_hint=domain, intent_hint=intent, entities=[_product(c) for c in typed],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_fit_parity")
    asked_out = _turn(
        session_factory, monkeypatch, text=text_asked, msg_id="ZZT-fit-asked",
        domain_hint=domain, intent_hint=intent, entities=[_product(c) for c in asked], find=find,
    )
    return control, asked_out


def _assert_line_then_control(a_reply: str, c_reply: str, line: str) -> None:
    assert c_reply.strip(), "the control must answer"
    assert a_reply == f"{line}\n\n{c_reply}", (a_reply, line, c_reply)


# --------------------------------------------------------------------------- AC-1, AC-2 contract


def test_ac1_the_find_filter_schema_requires_kind_enumerated_from_kinds() -> None:
    from app.services.chatbot.head import parser as head_parser
    from app.services.product_compatibility_service import KINDS

    item = head_parser.PARSE_OUTPUT_JSON_SCHEMA["properties"]["find"]["properties"]["filters"]["items"]
    assert "kind" in item["required"], item["required"]
    assert set(item["properties"]["kind"]["type"]) == {"string", "null"}
    assert item["properties"]["kind"]["enum"] == [*KINDS, None]
    assert {"key", "value", "unit", "file_word"} <= set(item["required"])


def test_ac2_the_vocabulary_names_every_kind_pair_and_asks_for_kind() -> None:
    from app.services import product_find
    from app.services.product_compatibility_service import KINDS

    text = product_find.parser_vocabulary().casefold()
    for kind in KINDS.values():
        assert kind.accessory.casefold() in text and kind.host.casefold() in text, kind
    assert "kind" in text


def test_ac2_the_seat_cover_wording_that_ships_today_stays() -> None:
    from app.services import product_find

    text = product_find.parser_vocabulary()
    assert 'The other-side word never IMPLIES an attachment type' in text
    assert "SRTWC8383-RL-MBL technical drawing" in text
    assert "no other side, so it is no find" in text


# --------------------------------------------------------------------------- AC-3, AC-3b, AC-4, AC-7 resolution


def test_ac3_mirror_kind_on_a_cabinet_resolves_its_mirror_only(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits("mirror", "mirror")))
    assert _codes(out) == [MIRROR1] and lines == []


def test_ac3_shelf_kind_on_a_cabinet_resolves_its_shelf_only(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits("shelf", "shelf")))
    assert _codes(out) == [SHELF1] and lines == []


def test_ac3_seat_cover_kind_resolves_seat_covers_and_never_a_cabinets_mirror(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_B, find=fits("seat_cover", "seat cover")))
    assert _codes(out) == COVERS_B
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits("seat_cover", "seat cover")))
    assert _codes(out) == [CAB1], "a cabinet's mirror/shelf link never answers a seat cover ask"
    assert lines == [f"No seat cover is on record for {CAB1}."]


def test_ac3b_no_kind_resolves_every_kind_on_record(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits(None, "goes with")))
    assert _codes(out) == [MIRROR1, SHELF1] and lines == []
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(WC_B, find=fits(None, "goes with")))
    assert _codes(out) == COVERS_B, "a WC holds seat cover links only"


def test_ac4_reverse_an_accessory_with_its_kind_resolves_its_hosts(session_factory) -> None:
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(MIRROR1, find=fits("mirror", "cabinet")))
    assert _codes(out) == [CAB1] and lines == []
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(SHELF1, find=fits("shelf", "cabinet")))
    assert _codes(out) == [CAB1, CAB2]


def test_ac7_the_kind_comes_from_the_parse_never_from_the_value_word(session_factory) -> None:
    """value says "mirror" but the parse's kind says shelf: shelf wins. kind null with a
    "mirror" value resolves every kind on record."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits("shelf", "mirror")))
    assert _codes(out) == [SHELF1]
    out, _lines = find_lane.as_linked_products(_scoped(session_factory), _verdict(CAB1, find=fits(None, "mirror")))
    assert _codes(out) == [MIRROR1, SHELF1]


# --------------------------------------------------------------------------- AC-5 no link, per kind (engine)


@pytest.mark.parametrize(
    "code,kind,value,line",
    [
        (CAB3, "shelf", "shelf", f"No shelf is on record for {CAB3}."),
        (CAB_NONE, "mirror", "mirror", f"No mirror is on record for {CAB_NONE}."),
        (CAB2, "mirror", "mirror", f"No mirror is on record for {CAB2}."),
        (WC_NONE, "seat_cover", "seat cover", f"No seat cover is on record for {WC_NONE}."),
        (MIRROR_NONE, "mirror", "cabinet", f"No cabinet is on record for {MIRROR_NONE}."),
        (SHELF_NONE, "shelf", "cabinet", f"No cabinet is on record for {SHELF_NONE}."),
        (SC_NONE, "seat_cover", "WC", f"No WC is on record for {SC_NONE}."),
    ],
)
def test_ac5_no_link_of_that_kind_is_one_line_and_no_fetch(session_factory, monkeypatch, code, kind, value, line) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"does {code} have any {value}", msg_id="ZZT-fit-none",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(code)], find=fits(kind, value),
    )
    assert calls == [], calls
    assert reply.strip() == line, reply


# --------------------------------------------------------------------------- AC-8, AC-9 forward, one


def test_ac8_ac9_seat_cover_forward_one_stock(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[COVER_A], asked=[WC_A],
        text_typed=f"check stock {COVER_A}", text_asked=f"check stock which seat cover goes on {WC_A}",
        find=fits("seat_cover", "seat cover"),
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Seat cover for {WC_A} is {COVER_A}.")


def test_ac8_ac9_mirror_forward_one_entity_only(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1], asked=[CAB1],
        text_typed=MIRROR1, text_asked=f"which mirror goes on {CAB1}",
        find=fits("mirror", "mirror"), domain=None, intent=None,
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {CAB1} is {MIRROR1}.")


def test_ac8_ac9_shelf_forward_one_stock(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR3], asked=[CAB3],
        text_typed=f"check stock {MIRROR3}", text_asked=f"check stock mirror for {CAB3}",
        find=fits("mirror", "mirror"),
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {CAB3} is {MIRROR3}.")


def test_ac8_a_file_ask_gets_the_line_then_the_photo_reply(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1], asked=[CAB1],
        text_typed=f"{MIRROR1} photo", text_asked=f"{CAB1} mirror photo",
        find=fits("mirror", "mirror", file_word="photo"),
        domain="product_attachment", intent="check_product_attachment",
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {CAB1} is {MIRROR1}.")


@pytest.mark.parametrize("tier", ["office", "dealer"])
def test_ac8_ac15_staff_and_dealer_get_the_same_line_then_their_own_reply(session_factory, monkeypatch, tier) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[COVER_A], asked=[WC_A],
        text_typed=f"check stock {COVER_A}", text_asked=f"check stock which seat cover goes on {WC_A}",
        find=fits("seat_cover", "seat cover"), tier=tier,
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Seat cover for {WC_A} is {COVER_A}.")
    line = a_reply.split("\n\n", 1)[0]
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", a_reply), a_reply
    assert "11" not in line and "stock" not in line.casefold(), line


# --------------------------------------------------------------------------- AC-10, AC-11 several, reverse


def test_ac10_forward_several_is_label_colon_value_in_answer_order(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=COVERS_B, asked=[WC_B],
        text_typed=f"check stock {' '.join(COVERS_B)}", text_asked=f"check stock seat cover on {WC_B}",
        find=fits("seat_cover", "seat cover"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Seat cover for {WC_B}: {COVERS_B[0]}, {COVERS_B[1]}")


def test_ac11_reverse_one_accessory_fits_host(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[CAB3], asked=[MIRROR3],
        text_typed=f"check stock {CAB3}", text_asked=f"check stock which cabinet takes {MIRROR3}",
        find=fits("mirror", "cabinet"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"{MIRROR3} fits {CAB3}.")


def test_ac11_reverse_several_hosts(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[CAB1, CAB2], asked=[SHELF1],
        text_typed=f"check stock {CAB1} {CAB2}", text_asked=f"check stock which cabinet takes {SHELF1}",
        find=fits("shelf", "cabinet"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"{SHELF1} fits these cabinets: {CAB1}, {CAB2}")


def test_ac11_reverse_a_seat_cover_keeps_its_wc_wording(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[WC_A], asked=[COVER_A],
        text_typed=f"check stock {WC_A}", text_asked=f"check stock which WC takes {COVER_A}",
        find=fits("seat_cover", "WC"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"{COVER_A} fits {WC_A}.")


# --------------------------------------------------------------------------- AC-12 one line per anchor / kind


def test_ac12_two_anchors_one_line_each_then_the_answer_for_both(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1, MIRROR3], asked=[CAB1, CAB3],
        text_typed=f"check stock {MIRROR1} {MIRROR3}", text_asked=f"check stock mirror for {CAB1} and {CAB3}",
        find=fits("mirror", "mirror"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply.endswith(f"\n\n{c_reply}"), a_reply
    head = a_reply[: -len(c_reply)]
    first, second = f"Mirror for {CAB1} is {MIRROR1}.", f"Mirror for {CAB3} is {MIRROR3}."
    assert head.startswith(first) and second in head and head.index(first) < head.index(second), head


def test_ac12_two_kinds_of_one_cabinet_one_line_per_kind(session_factory, monkeypatch) -> None:
    both = {"filters": [
        {"key": "fits", "value": "mirror", "unit": None, "file_word": None, "kind": "mirror"},
        {"key": "fits", "value": "shelf", "unit": None, "file_word": None, "kind": "shelf"},
    ]}
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1, SHELF1], asked=[CAB1],
        text_typed=f"check stock {MIRROR1} {SHELF1}", text_asked=f"check stock mirror and shelf for {CAB1}",
        find=both,
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply.endswith(f"\n\n{c_reply}"), a_reply
    head = a_reply[: -len(c_reply)]
    first, second = f"Mirror for {CAB1} is {MIRROR1}.", f"Shelf for {CAB1} is {SHELF1}."
    assert head.startswith(first) and second in head and head.index(first) < head.index(second), head


def test_ac3b_no_kind_answers_every_kind_with_a_line_each(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1, SHELF1], asked=[CAB1],
        text_typed=f"check stock {MIRROR1} {SHELF1}", text_asked=f"check stock what goes with {CAB1}",
        find=fits(None, "goes with"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    assert a_reply.endswith(f"\n\n{c_reply}"), a_reply
    head = a_reply[: -len(c_reply)]
    assert f"Mirror for {CAB1} is {MIRROR1}." in head and f"Shelf for {CAB1} is {SHELF1}." in head, head


# --------------------------------------------------------------------------- AC-6 carry


def test_ac6_a_bare_code_after_a_mirror_answer_carries_the_mirror_kind(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"check stock mirror for {CAB1}", msg_id="ZZT-fit-carry-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(CAB1)], find=fits("mirror", "mirror"),
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_fit_carry")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=MIRROR3, msg_id="ZZT-fit-carry-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(MIRROR3)], domain_in_message=False,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_fit_carry")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=CAB3, msg_id="ZZT-fit-carry-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(CAB3)],
        find=fits("mirror", None), domain_in_message=False,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {CAB3} is {MIRROR3}.")


def test_ac6_a_relation_stored_before_the_lane_reads_as_seat_cover(session_factory) -> None:
    """The stored relation has no kind ("covers"/"wcs"): a bare cabinet after it is no mirror ask."""
    from app.services.chatbot.lanes.business import find as find_lane

    _seed(session_factory)
    verdict = _verdict(WC_A, find=None, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, [COVERS_B[0]], carried_relation="covers")
    assert _codes(out) == [COVER_A] and lines == []
    verdict = _verdict(CAB1, find=None, domain_in_message=False)
    out, lines = find_lane.as_linked_products(_scoped(session_factory), verdict, [COVERS_B[0]], carried_relation="covers")
    assert _codes(out) == [CAB1] and lines == []


# --------------------------------------------------------------------------- AC-13 no line without a link


def test_ac13_typed_codes_carry_no_line(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, _calls = _turn(
        session_factory, monkeypatch, text=f"check stock {MIRROR1}", msg_id="ZZT-fit-typed",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(MIRROR1)],
    )
    assert reply.strip(), reply
    assert not re.match(r"(Mirror|Shelf|Seat cover) for ", reply) and " fits " not in reply.split("\n\n")[0], reply


def test_ac13_a_narrowing_follow_up_with_no_find_carries_no_line(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"check stock seat cover on {WC_B}", msg_id="ZZT-fit-narrow-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(WC_B)], find=fits("seat_cover", "seat cover"),
    )
    reply, _calls = _turn(
        session_factory, monkeypatch, text="just the A one", msg_id="ZZT-fit-narrow-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(COVERS_B[1])],
        find=None, domain_in_message=False, entity_op="replace",
    )
    assert not re.match(r"(Mirror|Shelf|Seat cover) for ", reply) and " fits " not in reply.split("\n\n")[0], reply


def test_ac13_the_no_link_reply_is_the_one_line_with_no_blank_line_after(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"mirror for {CAB_NONE}", msg_id="ZZT-fit-13c",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(CAB_NONE)], find=fits("mirror", "mirror"),
    )
    assert calls == [] and reply == f"No mirror is on record for {CAB_NONE}.", reply


# --------------------------------------------------------------------------- AC-14 ms / zh


def test_ac14_ms_shelf_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[SHELF1], asked=[CAB1],
        text_typed=f"stok {SHELF1} berapa", text_asked=f"rak untuk {CAB1} berapa harga",
        find=fits("shelf", "rak"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Rak untuk {CAB1} ialah {SHELF1}.")


def test_ac14_ms_mirror_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1], asked=[CAB1],
        text_typed=f"stok {MIRROR1} berapa", text_asked=f"cermin untuk {CAB1} ada stok berapa",
        find=fits("mirror", "cermin"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Cermin untuk {CAB1} ialah {MIRROR1}.")


def test_ac14_ms_seat_cover_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[COVER_A], asked=[WC_A],
        text_typed=f"stok {COVER_A} berapa", text_asked=f"penutup tempat duduk untuk {WC_A} berapa",
        find=fits("seat_cover", "penutup tempat duduk"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Penutup tempat duduk untuk {WC_A} ialah {COVER_A}.")


def test_ac14_zh_mirror_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1], asked=[CAB1],
        text_typed=f"{MIRROR1} 有货吗", text_asked=f"{CAB1} 的镜子有货吗",
        find=fits("mirror", "镜子"),
    )
    assert a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"{CAB1} 的镜子是 {MIRROR1}。")


def test_ac14_zh_shelf_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[SHELF1], asked=[CAB1],
        text_typed=f"{SHELF1} 有货吗", text_asked=f"{CAB1} 置物架有货吗",
        find=fits("shelf", "置物架"),
    )
    _assert_line_then_control(a_reply, c_reply, f"{CAB1} 的置物架是 {SHELF1}。")


def test_ac14_zh_seat_cover_forward_one(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[COVER_A], asked=[WC_A],
        text_typed=f"{COVER_A} 多少钱", text_asked=f"{WC_A} 马桶盖多少钱",
        find=fits("seat_cover", "马桶盖"),
    )
    _assert_line_then_control(a_reply, c_reply, f"{WC_A} 的马桶盖是 {COVER_A}。")


def test_ac14_ms_reverse_one(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[CAB3], asked=[MIRROR3],
        text_typed=f"stok {CAB3} berapa", text_asked=f"cermin {MIRROR3} untuk kabinet mana berapa",
        find=fits("mirror", "kabinet"),
    )
    _assert_line_then_control(a_reply, c_reply, f"{MIRROR3} sesuai untuk {CAB3}.")


def test_ac14_zh_reverse_one(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[CAB3], asked=[MIRROR3],
        text_typed=f"{CAB3} 有货吗", text_asked=f"{MIRROR3} 适合哪个浴室柜",
        find=fits("mirror", "浴室柜"),
    )
    _assert_line_then_control(a_reply, c_reply, f"{MIRROR3} 适配 {CAB3}。")


def test_ac14_ms_several_uses_the_catalogued_head(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=COVERS_B, asked=[WC_B],
        text_typed=f"stok {' '.join(COVERS_B)} berapa", text_asked=f"penutup tempat duduk untuk {WC_B} berapa",
        find=fits("seat_cover", "penutup tempat duduk"),
    )
    assert a_reply.endswith(f"\n\n{c_reply}"), a_reply
    line = a_reply[: -len(c_reply)].strip()
    assert line.startswith(f"Penutup tempat duduk untuk {WC_B}:"), line
    assert line.index(COVERS_B[0]) < line.index(COVERS_B[1]), line


def test_ac14_zh_several_uses_the_catalogued_head(session_factory, monkeypatch) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=COVERS_B, asked=[WC_B],
        text_typed=f"{' '.join(COVERS_B)} 有货吗", text_asked=f"{WC_B} 马桶盖有货吗",
        find=fits("seat_cover", "马桶盖"),
    )
    assert a_reply.endswith(f"\n\n{c_reply}"), a_reply
    line = a_reply[: -len(c_reply)].strip()
    assert line.startswith(f"{WC_B} 适配的马桶盖："), line
    assert line.index(COVERS_B[0]) < line.index(COVERS_B[1]), line


# --------------------------------------------------------------------------- AC-15 codes only


@pytest.mark.parametrize("tier", ["office", "dealer"])
def test_ac15_the_line_holds_codes_only_for_staff_and_dealer(session_factory, monkeypatch, tier) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[SHELF1], asked=[CAB1],
        text_typed=f"check stock {SHELF1}", text_asked=f"check stock shelf for {CAB1}",
        find=fits("shelf", "shelf"), tier=tier,
    )
    _assert_line_then_control(a_reply, c_reply, f"Shelf for {CAB1} is {SHELF1}.")
    line = a_reply.split("\n\n", 1)[0]
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", a_reply), a_reply
    assert "11" not in line, line


# --------------------------------------------------------------------------- round 2: AC-17 carried kind


@pytest.mark.parametrize("carried_find", [None, fits(None, None)], ids=["find_null", "kind_null_valueless"])
def test_ac17_a_carried_fit_ask_keeps_its_kind_without_a_kind_in_the_parse(session_factory, monkeypatch, carried_find) -> None:
    """The kind comes from the stored relation: a bare cabinet after a mirror answer is a mirror ask
    even when the parse drops `find` or sends a value-less kind-null `fits`."""
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    _turn(
        session_factory, monkeypatch, text=f"check stock mirror for {CAB1}", msg_id="ZZT-fit-17-1",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(CAB1)], find=fits("mirror", "mirror"),
    )
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_fit_17")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=MIRROR3, msg_id="ZZT-fit-17-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(MIRROR3)], domain_in_message=False,
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_fit_17")
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=CAB3, msg_id="ZZT-fit-17-2",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(CAB3)],
        find=carried_find, domain_in_message=False,
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {CAB3} is {MIRROR3}.")


# --------------------------------------------------------------------------- AC-18 crafted link fields


def test_ac18_link_fields_on_a_parser_entity_never_print_a_fit_line(session_factory, monkeypatch) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_fit_18")
    c_reply, c_calls = _turn(
        session_factory, monkeypatch, text=f"check stock {MIRROR1}", msg_id="ZZT-fit-18-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(MIRROR1)],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_fit_18")
    crafted = {**_product(MIRROR1), "linked_from": CAB1, "linked_as": "covers", "linked_kind": "mirror"}
    a_reply, a_calls = _turn(
        session_factory, monkeypatch, text=f"check stock {MIRROR1}", msg_id="ZZT-fit-18-asked",
        domain_hint="inventory", intent_hint="check_stock", entities=[crafted],
    )
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)
    assert f"Mirror for {CAB1}" not in a_reply, a_reply


# --------------------------------------------------------------------------- AC-19 hidden products


HID_CAB, HID_MIRROR = "ZZTBF7301", "ZZTMR7301"  # the only mirror is hidden by its own flag
MIX_CAB, MIX_VISIBLE, MIX_HIDDEN, MIX_HIDDEN_CAT = "ZZTBF7302", "ZZTMR7302", "ZZTMR7303", "ZZTMR7304"


def _seed_hidden(session_factory) -> None:
    from app.models.product import ProductCategory
    from app.models.product_compatibility import ProductCompatibility
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    rows = {c: seed_product(db, company_id=DEFAULT_COMPANY_ID, code=c)
            for c in (HID_CAB, HID_MIRROR, MIX_CAB, MIX_VISIBLE, MIX_HIDDEN, MIX_HIDDEN_CAT)}
    rows[HID_MIRROR].is_searchable = False
    rows[MIX_HIDDEN].is_searchable = False
    db.flush()
    db.query(ProductCategory).filter(ProductCategory.id == rows[MIX_HIDDEN_CAT].category_id).update(
        {"is_searchable": False}
    )
    for accessory, host in ((HID_MIRROR, HID_CAB), (MIX_VISIBLE, MIX_CAB), (MIX_HIDDEN, MIX_CAB), (MIX_HIDDEN_CAT, MIX_CAB)):
        db.add(ProductCompatibility(
            company_id=DEFAULT_COMPANY_ID, seat_cover_product_id=rows[accessory].id,
            wc_product_id=rows[host].id, source="manual", kind="mirror",
        ))
    db.commit()


def _hidden_parity(session_factory, monkeypatch, *, typed: str, asked: str):
    _seed(session_factory)
    _seed_hidden(session_factory)
    _seed_contact(session_factory, variables={})
    conn = session_factory().get_bind()
    conn.exec_driver_sql("SAVEPOINT zzt_fit_19")
    control = _turn(
        session_factory, monkeypatch, text=f"check stock {typed}", msg_id="ZZT-fit-19-control",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(typed)],
    )
    conn.exec_driver_sql("ROLLBACK TO SAVEPOINT zzt_fit_19")
    asked_out = _turn(
        session_factory, monkeypatch, text=f"check stock mirror for {asked}", msg_id="ZZT-fit-19-asked",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(asked)], find=fits("mirror", "mirror"),
    )
    return control, asked_out


def test_ac19_a_hidden_only_link_is_never_named_and_the_turn_runs_as_typed(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _hidden_parity(session_factory, monkeypatch, typed=HID_CAB, asked=HID_CAB)
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    assert a_reply == c_reply, (a_reply, c_reply)
    assert HID_MIRROR not in a_reply and "on record" not in a_reply and " for " + HID_CAB not in a_reply, a_reply


def test_ac19_with_one_visible_and_two_hidden_accessories_the_line_names_only_the_visible(session_factory, monkeypatch) -> None:
    (c_reply, c_calls), (a_reply, a_calls) = _hidden_parity(session_factory, monkeypatch, typed=MIX_VISIBLE, asked=MIX_CAB)
    assert c_calls and a_calls == c_calls, (a_calls, c_calls)
    _assert_line_then_control(a_reply, c_reply, f"Mirror for {MIX_CAB} is {MIX_VISIBLE}.")
    assert MIX_HIDDEN not in a_reply and MIX_HIDDEN_CAT not in a_reply, a_reply


# --------------------------------------------------------------------------- AC-20 other lines stay as they are

UNRELATED = ["This soft-close seat fits most round WCs.", "Mirror for bathroom: LED"]


@pytest.fixture()
def unrelated_lines(monkeypatch):
    """The usual answer carries two lines shaped like the fit templates (a product note)."""
    from dataclasses import replace

    from app.services.chatbot import engine as engine_mod

    real = engine_mod._run_answer

    def wrapped(**kw):
        answer = kw["answer"]
        if (getattr(answer, "text", "") or "").strip():
            kw["answer"] = replace(answer, text=answer.text + "\n" + "\n".join(UNRELATED))
        return real(**kw)

    monkeypatch.setattr(engine_mod, "_run_answer", wrapped)


@pytest.mark.parametrize(
    "typed_text,asked_text,value,line",
    [
        (f"stok {MIRROR1} berapa", f"cermin untuk {CAB1} ada stok berapa", "cermin", f"Cermin untuk {CAB1} ialah {MIRROR1}."),
        (f"{MIRROR1} 有货吗", f"{CAB1} 的镜子有货吗", "镜子", f"{CAB1} 的镜子是 {MIRROR1}。"),
    ],
    ids=["ms", "zh"],
)
def test_ac20_only_the_fit_line_is_translated(session_factory, monkeypatch, unrelated_lines, typed_text, asked_text, value, line) -> None:
    (c_reply, _c), (a_reply, _a) = _parity(
        session_factory, monkeypatch, typed=[MIRROR1], asked=[CAB1],
        text_typed=typed_text, text_asked=asked_text, find=fits("mirror", value),
    )
    _assert_line_then_control(a_reply, c_reply, line)
    lines = a_reply.splitlines()
    for other in UNRELATED:
        assert other in lines, (other, a_reply)


def test_ac20_a_non_linked_zh_turn_keeps_such_lines_unchanged(session_factory, monkeypatch, unrelated_lines) -> None:
    _seed(session_factory)
    _seed_contact(session_factory, variables={})
    reply, calls = _turn(
        session_factory, monkeypatch, text=f"{MIRROR1} 有货吗", msg_id="ZZT-fit-20-plain",
        domain_hint="inventory", intent_hint="check_stock", entities=[_product(MIRROR1)],
    )
    assert calls, "the turn answers"
    for other in UNRELATED:
        assert other in reply.splitlines(), (other, reply)
