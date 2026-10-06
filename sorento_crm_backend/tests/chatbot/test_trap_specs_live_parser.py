"""STOCK-QTY-PARTIAL-FILL: the LIVE parser reads trap specs the same way every time
(owner, 6 Oct 2026, prompt v47: "Any WC with S trap 300mm available" came back
`trap_length 300` at 10:07 and `dim_width 300` at 14:19).

The rule is the prompt addendum `alembic/data/chatbot_semantic_parser.trap-specs.addendum.txt`
(plus the list-fragment one), and whether the model honours it is decided HERE, against the
real provider:

  * the production text of 1 Oct 2026 (`chatbot_semantic_parser.prod-20261001.txt`),
    expected RED (xfail, not strict: a model that happens to get it right is not a failure);
  * the same text with the addenda applied (`_trap_specs_prompt.patched_text`), expected
    GREEN.

Opt-in, never in CI: `CHATBOT_LIVE_LLM=1` (the suite's `live_external` marker; model
`TRAP_SPECS_LIVE_MODEL`, default `gpt-5.4-mini`, the production parser model; key
`OPENAI_API_KEY`, or a placeholder where an egress proxy injects it). Same pattern as
`test_order_broaden_live_parser.py`. The cloud sandbox has no parser key: crew runs this on
the Mac and pastes the printed verdicts on the PR. The policy blocks (the Specification
lines the parser is bounded by) are rendered from the registry seed, so the model sees the
same `Specification trap_type` / `trap_length` / `dim_width` lines production renders.
UAC AC-13.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from tests.chatbot import _trap_specs_prompt as tp

pytestmark = pytest.mark.live_external

TODAY = "Tuesday, 06 October 2026"
KNOWN_BRANDS = "Sorento (SRT), Mocha (MCH), Cabana (CBN)"

#: After "SRTWC286-SH x 10" was answered (contact 550337680, 14:50 MYT).
ANSWERED_FOCUS = SimpleNamespace(
    domains=["inventory"],
    customers=[],
    products=[{"raw": "srtwc286-sh", "name": "SRTWC286-SH", "canonical_code": "SRTWC286-SH"}],
    document=[],
    status=None,
    date_window=None,
    tasks=[],
)
ANSWERED_REPLY = "SRTWC286-SH x 10: 🚫 the quantity is more than what I can confirm here. Please refer to your salesman."


def _blocks(db) -> str:
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, render_prompt_blocks

    return f"{BLOCKS_BEGIN}\n{render_prompt_blocks(db)}{BLOCKS_END}\n"


def _system(label: str, db) -> str:
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    text = tp.prod_text() if label == "prod" else tp.patched_text()
    # The production file carries the blocks baked at publish time; swap them for the
    # registry's own render so the Specification lines are the seed's.
    if BLOCKS_BEGIN in text and BLOCKS_END in text:
        head, rest = text.split(BLOCKS_BEGIN, 1)
        _old, tail = rest.split(BLOCKS_END, 1)
        text = head + _blocks(db).rstrip("\n") + tail
    return text.replace("{{current_date}}", TODAY)


def _config(system_text: str):
    from app.services.chatbot.head import parser

    return parser.ParserConfig(
        system_prompt=system_text,
        prompt_version=0,
        provider="openai",
        model=os.environ.get("TRAP_SPECS_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(system_text: str, message: str, *, label: str, focus=None, previous="(none)", recent=None) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    block = parser.build_user_block(
        previous_response=previous,
        latest_user_message=message,
        pending_kind=None,
        focus=focus,
        recent_exchanges=recent,
        known_brands=KNOWN_BRANDS,
    )
    out = dict(parser.parse(_config(system_text), block))
    print(
        json.dumps(
            {
                "prompt": label,
                "message": message,
                "domain_hint": out.get("domain_hint"),
                "intent_hint": out.get("intent_hint"),
                "entity_op": out.get("entity_op"),
                "entities": [
                    {k: e.get(k) for k in ("raw", "hint", "canonical_code", "quantity", "spec_key", "spec_value")}
                    for e in (out.get("entities") or [])
                    if isinstance(e, dict)
                ],
            },
            ensure_ascii=False,
        )
    )
    return out


def _specs(out: dict[str, Any]) -> dict[str, Any]:
    return {
        e.get("spec_key"): e.get("spec_value")
        for e in (out.get("entities") or [])
        if isinstance(e, dict) and e.get("hint") == "specification"
    }


def _hints(out: dict[str, Any]) -> set[str]:
    return {str(e.get("hint")) for e in (out.get("entities") or []) if isinstance(e, dict)}


PROD = pytest.param("prod", marks=pytest.mark.xfail(reason="the 1 Oct text reads the trap length as a width at times", strict=False))
PATCHED = pytest.param("patched")


@pytest.fixture()
def db(session_factory):
    from tests.chatbot.test_lane_require import _seed_registry

    s = session_factory()
    _seed_registry(s)
    s.commit()
    yield s
    s.close()


@pytest.mark.parametrize("label", [PROD, PATCHED])
@pytest.mark.parametrize(
    "message",
    ["Any WC with s trap 300mm available?", "Any WC with S trap 300mm available", "tandas s trap 300mm ada stok?"],
)
def test_s_trap_300mm_is_trap_type_and_trap_length(db, label: str, message: str) -> None:
    out = _parse(_system(label, db), message, label=label)
    specs = _specs(out)
    assert specs.get("trap_type") == "s_trap", out
    assert specs.get("trap_length") in (300, 300.0), out
    assert "dim_width" not in specs and "dim_length" not in specs and "dim_height" not in specs, out
    assert "category" in _hints(out), out
    assert out.get("intent_hint") == "check_stock", out


@pytest.mark.parametrize("label", [PROD, PATCHED])
def test_a_code_with_trap_words_keeps_the_product_and_both_specs(db, label: str) -> None:
    out = _parse(_system(label, db), "Srtwc8066-sh s trap 250mm 50pc", label=label)
    products = [e for e in out.get("entities") or [] if e.get("hint") == "product"]
    assert len(products) == 1 and products[0].get("quantity") == 50, out
    assert products[0].get("canonical_code", "").upper().replace(" ", "") == "SRTWC8066-SH", out
    specs = _specs(out)
    assert specs.get("trap_type") == "s_trap" and specs.get("trap_length") in (250, 250.0), out


def test_the_brand_sentence_keeps_the_brand_and_the_two_specs(db) -> None:
    """#1509's control: once the brand is kept, the spec search gets brand + class +
    trap_type + trap_length 200."""
    out = _parse(_system("patched", db), "FIND S TRAP 200MM SORENTO WC", label="patched")
    specs = _specs(out)
    assert specs.get("trap_type") == "s_trap" and specs.get("trap_length") in (200, 200.0), out
    assert {"brand", "category"} <= _hints(out), out


def test_a_size_word_still_names_its_own_size_key(db) -> None:
    """The control the rule must not break: a width WORD is a width."""
    out = _parse(_system("patched", db), "any water closet 600mm wide", label="patched")
    specs = _specs(out)
    assert specs.get("dim_width") in (600, 600.0), out
    assert "trap_length" not in specs, out


@pytest.mark.parametrize("label", [PROD, PATCHED])
def test_how_about_p_trap_after_a_product_answer_keeps_the_product(db, label: str) -> None:
    """Q4 (a), owner 6 Oct 2026: parser-decided through the existing fields."""
    out = _parse(
        _system(label, db),
        "How about p trap",
        label=label,
        focus=ANSWERED_FOCUS,
        previous=ANSWERED_REPLY,
        recent=[("SRTWC286-SH x 10", ANSWERED_REPLY)],
    )
    assert _specs(out).get("trap_type") == "p_trap", out
    assert out.get("entity_op") == "reuse", out
    assert out.get("domain_hint") in (None, "inventory"), out
    assert out.get("domain_hint") != "master_products", out


def test_a_class_word_after_a_product_answer_is_a_new_class_wide_ask(db) -> None:
    out = _parse(
        _system("patched", db),
        "any p trap water closet?",
        label="patched",
        focus=ANSWERED_FOCUS,
        previous=ANSWERED_REPLY,
        recent=[("SRTWC286-SH x 10", ANSWERED_REPLY)],
    )
    assert "category" in _hints(out), out
    assert _specs(out).get("trap_type") == "p_trap", out
    assert out.get("entity_op") != "reuse", out
