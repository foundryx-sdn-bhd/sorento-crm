"""WA-CONCISE owner hand test round 4 (red-first). Placeholder data only.

F1 compact stock names the company across two companies, F2 no incoming opener, F3 a folded
incoming block keeps its lines and the attached sentence closes the reply, F4 a bare incoming
ask keeps every date, F5 no carry-over preamble and one numbering across sections, F6 the
attachment gap line sits above the `_Updated` footer.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.chatbot._wa_concise_helpers import (
    FOOTER,
    GRANTED,
    INCOMING_TOOL,
    STOCK_TOOL,
    UNGRANTED,
    compact_entry,
    compact_payload,
    envelope_json,
    incoming_payload,
    incoming_shipment,
    render,
)

# =============================== F1 ======================================= #


def _entry(company: str | None, code: str, locs, **kw) -> dict:
    e = compact_entry(code, locs, **kw)
    if company:
        e["company_name"] = company
    return e


def test_f1b_mcp_compact_puts_company_first_when_the_entry_carries_one():
    out = json.loads(
        envelope_json(
            STOCK_TOOL,
            compact_payload([_entry("Mocha", "SRT6542-DIY", [("MOCHA-WH", 1, None)])]),
        )
    )

    labels = [f["label"] for f in out["items"][0]["fields"]]
    assert labels[:2] == ["Company", "Product Code"], labels
    assert out["items"][0]["fields"][0]["value"] == "Mocha"


def test_f1b_guard_mcp_compact_without_company_has_no_company_field():
    out = json.loads(
        envelope_json(STOCK_TOOL, compact_payload([compact_entry("SRT6542-DIY", [("BRW", 1, None)])]))
    )

    assert "Company" not in [f["label"] for f in out["items"][0]["fields"]]


def test_f1c_two_companies_compact_reply_names_each_company():
    text = render(
        STOCK_TOOL,
        compact_payload(
            [
                _entry("Mocha", "SRT6542-DIY", [("MKTG", 1, None), ("MOCHA-WH", 1, None)]),
                _entry("Sorento", "SRT6542-DIY", [("BRW", 0, 233)]),
            ]
        ),
        GRANTED,
    )

    assert text == (
        "1. *Company:* Mocha\n*Product Code:* SRT6542-DIY\n*Total:* 2\n*MKTG:* 1\n*MOCHA-WH:* 1\n\n"
        "2. *Company:* Sorento\n*Product Code:* SRT6542-DIY\n*BRW:* 0 (O/S: 233)\n\n"
        f"{FOOTER}"
    ), text


def test_f1c_company_line_does_not_count_as_a_location():
    text = render(
        STOCK_TOOL,
        compact_payload([_entry("Sorento", "SRT6542-DIY", [("BRW", 5, None)])]),
        UNGRANTED,
    )

    assert text == f"*Company:* Sorento\n*Product Code:* SRT6542-DIY\n*BRW:* 5\n\n{FOOTER}", text


def test_f1c_guard_single_company_compact_is_unchanged():
    text = render(
        STOCK_TOOL,
        compact_payload([_entry(None, "SRT6542-DIY", [("BRW", 5, None), ("MWH", 6, None)])]),
        UNGRANTED,
    )

    assert text == f"*Product Code:* SRT6542-DIY\n*Total:* 11\n*BRW:* 5\n*MWH:* 6\n\n{FOOTER}", text


# F1a: the service. Reuses the blank-schema fixture and seeds of the stock visibility suite.
from tests.test_stock_visibility_policy import (  # noqa: E402,F401
    _contact,
    _policy_row,
    _wh,
    db,
)


def test_f1a_service_compact_entry_carries_company_name_across_two_companies(db):
    from app.models.base import set_company_scope
    from app.models.company import Company
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from app.services.inventory_service import StockService
    from tests._mc_lookup_seed import MOCHA_ID, product, seed_mocha, stock

    seed_mocha(db)
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
    sorento_wh = _wh(db, "ZZTBRW", company_id=DEFAULT_COMPANY_ID)
    mocha_wh = _wh(db, "ZZTMOCHA", company_id=MOCHA_ID)
    a = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SRT6542-S")
    b = product(db, company_id=MOCHA_ID, code="ZZT-SRT6542-M")
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=a.id, warehouse_id=sorento_wh.id, on_hand=3)
    stock(db, company_id=MOCHA_ID, product_id=b.id, warehouse_id=mocha_wh.id, on_hand=4)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[a.id, b.id], contact_id=contact.id)

    names = {e["product_id"]: e.get("company_name") for e in result["stock_summary"]}
    assert names[a.id] == db.get(Company, DEFAULT_COMPANY_ID).name, names
    assert names[b.id] == "Mocha", names


def test_f1a_guard_service_compact_single_company_carries_no_company_name(db):
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from app.services.inventory_service import StockService
    from tests._mc_lookup_seed import product, stock

    wh = _wh(db, "ZZTBRW1", company_id=DEFAULT_COMPANY_ID)
    p = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SINGLE")
    stock(db, company_id=DEFAULT_COMPANY_ID, product_id=p.id, warehouse_id=wh.id, on_hand=3)
    contact = _contact(db)
    _policy_row(db, mode="compact", contact=contact)
    db.flush()

    result = StockService(db).list_stock(product_ids=[p.id], contact_id=contact.id)

    assert result["stock_summary"][0].get("company_name") is None


# =============================== F2 ======================================= #

_INC = incoming_shipment("SRTX", "IAAU1907074", "2026-09-09", 49, [("BRW", 35), ("BRW-BB", 4)])
_INC_LINES = (
    "*Container:* IAAU1907074\n*ETA:* 2026-09-09\n*Incoming Quantity:* 49\n"
    "*Warehouse Allocations:* BRW (35), BRW-BB (4)"
)


def test_f2_incoming_reply_has_no_opener():
    text = render(INCOMING_TOOL, incoming_payload([_INC]), UNGRANTED)

    assert text == f"*Product Code:* SRTX\n{_INC_LINES}", text


def test_f2_guard_incoming_shipments_opener_is_dropped_too():
    text = render(
        "crm_incoming_stock_shipments",
        {"data": [{"shipment_number": "S1", "shipping_container_number": "IAAU1907074"}]},
    )

    assert text.startswith("*Shipment:* S1"), text  # round 8: no row opener


# =============================== F3 ======================================= #


def test_f3_folded_incoming_block_keeps_its_lines_and_footer():
    from app.services.chatbot.answer_bridge import _fold_blocks

    primary = f"*Product Code:* SRTX\n{_INC_LINES}\n\n{FOOTER}"
    xd = "*Product Code:* SRTX\n*Stock:* 0\n*PO:* none"

    kept, rest = _fold_blocks(primary, xd)
    whole = "\n\n".join(p for p in (kept, rest) if p)

    for line in _INC_LINES.split("\n"):
        assert line in whole, (line, whole)
    assert whole.count("*Product Code:* SRTX\n") == 1, whole
    assert whole.rstrip().endswith(FOOTER), whole


from tests.chatbot import test_wa_concise_crossdomain as xd_mod  # noqa: E402
from tests.chatbot import test_wa_concise_review_round2 as r2  # noqa: E402
from tests.chatbot.test_engine import (  # noqa: E402,F401 - fixtures re-exported by name
    seeded,
    stub_access,
    stub_parser,
)

ATTACHED = "I have attached the file(s) below."


def test_f3_attached_sentence_is_the_last_paragraph_and_the_file_is_kept(
    session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
):
    ship = incoming_shipment("SRTX", "IAAU1907074", "2026-09-09", 49, [("BRW", 35), ("BRW-BB", 4)])
    ship["attachment"] = {"file_path": "packing/PL-1.pdf", "filename": "PL-1.pdf", "mime_type": "application/pdf"}
    said, reply = xd_mod._ask_incoming(
        **r2._fx(session_factory, stub_parser, stub_access, monkeypatch),
        codes=["SRTX"],
        tools={INCOMING_TOOL: xd_mod._incoming({"SRTX": ship}), STOCK_TOOL: xd_mod._stock({}), xd_mod.PO_TOOL: xd_mod._po()},
    )

    assert reply["attachments_src"] and reply["attachments_src"][0]["url"] == "packing/PL-1.pdf", reply
    assert said == f"*Product Code:* SRTX\n{_INC_LINES}\n\n{ATTACHED}", said


# =============================== F4 ======================================= #


def test_f4_bare_incoming_ask_keeps_every_date_like_an_eta_ask():
    ship = incoming_shipment("SRTX", "IAAU1907074", "2026-09-09", 49, [("BRW", 35)])
    ship.update(
        {
            "loading_date": "2026-08-01",
            "etd_date": "2026-08-05",
            "etc_date": "2026-08-20",
            "eta_delay_date": "2026-09-12",
        }
    )
    entities = [{"uuid": "u", "entity_type": "product", "code": "SRTX"}]
    bare = render(
        INCOMING_TOOL,
        incoming_payload([ship]),
        {"semantic_input": {"requested_attributes": []}, "entities": entities},
    )
    asked = render(
        INCOMING_TOOL,
        incoming_payload([ship]),
        {"semantic_input": {"requested_attributes": ["estimated_arrival_date"]}, "entities": entities},
    )

    for line in ("*Loading:* 2026-08-01", "*ETD:* 2026-08-05", "*ETC:* 2026-08-20", "*ETA Delay:* 2026-09-12"):
        assert line in bare, (line, bare)
    assert bare == asked, (bare, asked)


# =============================== F5 ======================================= #


@pytest.mark.parametrize(
    "case_name", ["02-follow-up-needs-yesterdays-episode.json", "08-stock-for-the-usual.json"]
)
def test_f5a_no_carry_over_preamble_in_a_remembered_reply(
    case_name, session_factory, stub_access, lane
):
    from tests.chatbot.test_memory_s4_fallback_replay import CASES_DIR, _load_case, _play, sent_text

    case = _load_case(CASES_DIR / case_name)
    turn = case["turn"]
    message = (turn.get("messages") or [turn["message"]])[0]
    result, _prompt, _pk = _play(session_factory, stub_access, lane, case, message, ablate=False)

    sent = sent_text(result)
    assert sent.strip(), "no reply was sent"
    assert "Carrying on from" not in sent and "Your usual:" not in sent, sent


from tests.chatbot.test_memory_s4_fallback_replay import lane  # noqa: E402,F401


def test_f5b_fallback_reply_has_no_last_time_line():
    from app.services.chatbot import copy as copy_mod
    from app.services.chatbot.lanes import fallback
    from tests.chatbot.test_memory_s4_fallback_units import _ctx

    text = fallback.compose(
        "Hi!", _ctx(last_time="Thu 25 Sep, Stock: Asked about stock for X and got an answer"),
        copy_mod.fallback_copy(), "en",
    )

    assert "Last time" not in text, text


def _compose_two_domains(stock_codes: list[str]) -> str:
    from app.services.chatbot.turn.compose import compose
    from app.services.chatbot.turn.policy import Policy
    from app.services.chatbot.turn.state import Focus, Profile, State
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    rows = [
        {**_domain_row("incoming", narrowing={"product": "list_all"}), "label": "incoming"},
        {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"},
    ]
    policy = Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)

    def env(domain, codes, text):
        return {
            "domain": domain, "denied": False, "entities": codes, "product_codes": codes,
            "figures": [{"fields": [{"label": "Product Code", "value": c}]} for c in codes],
            "files": [], "miss": [], "has_result": True, "tool_has_result": True,
            "unresolved": [], "error": None, "lane_text": text,
        }

    inc = env("incoming", ["SRTA"], "*Product Code:* SRTA\n*Container:* IAAU1907074")
    blocks = [f"*Product Code:* {c}\n*BRW:* 5" for c in stock_codes]
    if len(blocks) > 1:
        blocks = [f"{i}. {b}" for i, b in enumerate(blocks, start=1)]
    stk = env("inventory", stock_codes, "\n\n".join(blocks) + f"\n\n{FOOTER}")
    return compose([inc, stk], state, policy, ctx=None).text


def test_f5c_numbering_runs_across_sections():
    text = _compose_two_domains(["SRTB", "SRTC"])

    assert re.findall(r"^(\d+)\. \*Product Code:\* (\w+)", text, re.MULTILINE) == [
        ("1", "SRTA"), ("2", "SRTB"), ("3", "SRTC"),
    ], text


def test_f5c_two_single_block_sections_are_numbered_one_and_two():
    text = _compose_two_domains(["SRTB"])

    assert re.findall(r"^(\d+)\. \*Product Code:\* (\w+)", text, re.MULTILINE) == [
        ("1", "SRTA"), ("2", "SRTB"),
    ], text


def test_f5c_guard_exactly_one_block_overall_stays_unnumbered():
    from tests.chatbot.test_wa_concise_orders_incoming import _orders_text

    assert _orders_text().startswith("*Order Number:*")


# =============================== F6 ======================================= #


def test_f6_attachment_gap_line_sits_above_the_updated_footer():
    from tests.chatbot.test_attachment_multi_type import (
        PHOTOS,
        SH,
        SPECS,
        _attachment_envelope,
        _compose_text,
    )

    env = _attachment_envelope([SH], [(SH, PHOTOS)], [PHOTOS, SPECS])
    env["lane_text"] += "\n\n_Updated 03/10/2026 16:24_"

    text = _compose_text(env)

    assert f"\n\n{SH} has no {SPECS}.\n\n_Updated 03/10/2026 16:24_" in text, repr(text)
