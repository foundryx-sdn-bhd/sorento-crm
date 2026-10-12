"""CHAT-FIND lane contract (L1-L5): `crm_find` through the chatbot's business lane.

Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md sections 2.5 and 4.
Harness: `tests/chatbot/test_low_stock_lane.py` (`run_fetch` with a capturing fake MCP).

* L1 parse `find.filters=[fits]` + a resolved product -> `crm_find(filters=[{key, product_ids}])`
  plus contact params; the reply is the presenter's text verbatim.
* L2 no product entity -> the fixed ask line, no tool call.
* L3 only an unknown filter key -> no `crm_find`.
* L4 allow-list wiring.
* L5 ms / zh localization of the presenter lines, codes untouched.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.chatbot import contracts, label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import FetchServices
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_engine import seeded, stub_access, stub_parser  # noqa: F401
from tests.chatbot.test_outstanding_lane import _capturing_mcp

TOOL = "crm_find"
PRODUCT_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PRODUCT_CODE = "SRTWC8383-RL-MBL"
SPACE = "ZZTFIND-SPACE"
ASK_LINE = "Which product code? For example SRTWC8383-RL-MBL."

PRESENTER_TEXT = (
    f"Seat cover for {PRODUCT_CODE}:\nCode: SRTSCBD823-MBL\nStock: 12"
)
ENVELOPE = json.dumps({"response": PRESENTER_TEXT, "has_result": True, "result_type": "find"})


def _find_parse(*, filters=None, entities=None, **overrides: Any) -> dict[str, Any]:
    if filters is None:
        filters = [{"key": "fits", "value": None}]
    if entities is None:
        entities = [
            {
                "raw": PRODUCT_CODE,
                "hint": "product",
                "canonical_code": PRODUCT_CODE,
                "current_message": True,
                "confident": True,
            }
        ]
    return _parser_output(
        domain_hint="master_products",
        intent_hint="check_product",
        find={"filters": filters},
        entities=entities,
        **overrides,
    )


def _payload(*, parse: dict[str, Any], resolved: bool = True) -> dict[str, Any]:
    compatible = (
        [{"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE, "code": PRODUCT_CODE}]
        if resolved
        else []
    )
    return {
        "gate": {"compatible_entities": compatible},
        "tier_gate": None,
        "ctx": {
            "parse": {"output": parse},
            "contact": {"id": CONTACT_ID},
            "access": {"attributes": []},
        },
    }


def _run(payload, call):
    from app.services.chatbot.lanes.business import run_fetch

    return run_fetch(payload, services=FetchServices(mcp_call=call), space_id=SPACE)


# --------------------------------------------------------------------------- L1


def test_l1_fits_calls_crm_find_with_the_resolved_product(session_factory) -> None:
    call, captured = _capturing_mcp(ENVELOPE)
    result = _run(_payload(parse=_find_parse()), call)

    assert captured, "crm_find was never called"
    name, args = captured[0]
    assert name == TOOL, name
    assert args.get("filters") == [{"key": "fits", "product_ids": [PRODUCT_UUID]}], args
    assert args.get("contact_id") == str(CONTACT_ID), args
    assert args.get("space_id") == SPACE, args
    assert (result or {}).get("response") == PRESENTER_TEXT, result


def test_l1_the_structurer_uses_the_presenter_text_verbatim_and_owns_the_header() -> None:
    out = fetch_mod.output_structurer(
        {"response": PRESENTER_TEXT, "has_result": True, "result_type": "find"}, {"tool": TOOL}
    )
    assert out.get("response") == PRESENTER_TEXT, out
    assert out.get("has_result") is True, out
    assert out.get("own_header") is True, "the generic search-scope header must not print"


# --------------------------------------------------------------------------- L2


def test_l2_no_product_entity_asks_for_the_code_and_calls_nothing(session_factory) -> None:
    call, captured = _capturing_mcp(ENVELOPE)
    result = _run(_payload(parse=_find_parse(entities=[]), resolved=False), call)

    assert captured == [], captured
    assert (result or {}).get("response") == ASK_LINE, result


# --------------------------------------------------------------------------- L3


def test_l3_an_unknown_filter_key_alone_never_reaches_crm_find(session_factory) -> None:
    call, captured = _capturing_mcp(json.dumps({"has_result": False, "items": []}))
    _run(_payload(parse=_find_parse(filters=[{"key": "colour_of", "value": None}])), call)

    assert all(name != TOOL for name, _args in captured), captured


def test_l3_unknown_keys_are_dropped_beside_a_known_one(session_factory) -> None:
    call, captured = _capturing_mcp(ENVELOPE)
    _run(
        _payload(
            parse=_find_parse(
                filters=[{"key": "colour_of", "value": None}, {"key": "fits", "value": None}]
            )
        ),
        call,
    )
    assert captured and captured[0][0] == TOOL, captured
    assert captured[0][1].get("filters") == [{"key": "fits", "product_ids": [PRODUCT_UUID]}]


# --------------------------------------------------------------------------- L4


def test_l4_crm_find_is_wired_into_the_allow_lists() -> None:
    assert TOOL in fetch_mod.CHATBOT_READ_ONLY_TOOLS
    assert contracts.BRAND_SCOPE_TREATMENT[TOOL] == "filtered"
    assert TOOL in contracts.UNDOMAINED_CHATBOT_TOOLS


# --------------------------------------------------------------------------- L5

FIND_LINES = (
    "Code: SRTSCBD823-MBL\n"
    "Status: Discontinued\n"
    "Stock: 12"
)
HEADS = (
    f"Seat cover for {PRODUCT_CODE}:",
    f"{PRODUCT_CODE} fits these WCs:",
    f"No seat cover or WC is on record for {PRODUCT_CODE}.",
)


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_l5_labels_and_values_translate_and_codes_are_untouched(lang) -> None:
    out = _loc(lang).lines(FIND_LINES)
    code_line, status_line, stock_line = out.split("\n")
    assert code_line.endswith("SRTSCBD823-MBL") and code_line != "Code: SRTSCBD823-MBL"
    assert stock_line.endswith("12") and not stock_line.startswith("Stock:")
    assert not status_line.startswith("Status:") or lang == "ms"  # ms keeps the word "Status"
    assert "Discontinued" not in status_line


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_l5_heads_and_the_none_line_translate_with_the_code_kept(lang) -> None:
    loc = _loc(lang)
    for head in HEADS:
        out = loc.lines(head)
        assert out != head, f"{head!r} has no {lang} translation"
        assert PRODUCT_CODE in out


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_l5_the_ask_line_has_a_translation(lang) -> None:
    assert _loc(lang).lines(ASK_LINE) != ASK_LINE


def test_l5_every_registry_label_is_in_the_catalog_with_ms_and_zh() -> None:
    from app.services import product_find

    for row in product_find.FILTERS.values():
        for english in row.labels:
            assert english in label_catalog.LABELS, english
            assert label_catalog.LABELS[english].get("ms")
            assert label_catalog.LABELS[english].get("zh")
    for english in ("Code", "Stock", "Status", "Discontinued", "Seat cover for {code}:",
                    "{code} fits these WCs:", "No seat cover or WC is on record for {code}."):
        entry = label_catalog.LABELS.get(english)
        assert entry and entry.get("ms") and entry.get("zh"), english


def test_l5_the_structurer_localizes_the_reply_with_the_turns_localizer() -> None:
    out = fetch_mod.output_structurer(
        {"response": PRESENTER_TEXT, "has_result": True, "result_type": "find"},
        {"tool": TOOL, "localizer": _loc("ms")},
    )
    assert out["response"] != PRESENTER_TEXT
    assert "SRTSCBD823-MBL" in out["response"] and PRODUCT_CODE in out["response"]
    assert not out["response"].splitlines()[-1].startswith("Stock:")
