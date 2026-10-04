"""LOWSTOCK-SEMANTIC: the LIVE parser reads the low stock filters (owner, 4 Oct 2026).

`documentation/plans/chatbot/lowstock-semantic-behaviour-card.md`. The lane no longer reads
a single word of the message, so whether "per vendor", "split by brand", a Malay or Chinese
category, or the owner's own "low stock report for sorento water tap" works is decided HERE,
by the model reading `LOW_STOCK_FILTERS_ADDENDUM`. These tests call the real provider with
the prompt this branch publishes (`SEMANTIC_PARSER_PROMPT` plus the committed policy-blocks
seed, the shape `chatbot_rearch_s4._body` publishes) and grade the `low_stock` key it emits.

Opt-in, never in CI: `LOW_STOCK_LIVE_PARSER=1` (model `LOW_STOCK_LIVE_MODEL`, default
`gpt-5.4-mini`, the production parser model; key `OPENAI_API_KEY`, or a placeholder where an
egress proxy injects it). Each case is one parse, so a paraphrase that fails is named alone.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

LIVE = os.environ.get("LOW_STOCK_LIVE_PARSER") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="live parser run: set LOW_STOCK_LIVE_PARSER=1")

QUESTION = 'Which product category? Reply with a category (e.g. water tap) or "all".'
SEED = Path(__file__).parent / "fixtures" / "prompt_blocks_seed.txt"


def _config():
    from app.services.chatbot.head import parser
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END, SEMANTIC_PARSER_PROMPT

    blocks = SEED.read_text(encoding="utf-8")
    body = f"{SEMANTIC_PARSER_PROMPT.rstrip()}\n\n{BLOCKS_BEGIN}\n{blocks}{BLOCKS_END}\n"
    return parser.ParserConfig(
        system_prompt=body.replace("{{current_date}}", "Sunday, 04 October 2026"),
        prompt_version=0,
        provider="openai",
        model=os.environ.get("LOW_STOCK_LIVE_MODEL", "gpt-5.4-mini"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-proxy-injected",
    )


def _parse(message: str, *, previous: str = "(none)", open_question: dict | None = None) -> dict[str, Any]:
    from app.services.chatbot.head import parser

    block = parser.build_user_block(
        previous_response=previous, latest_user_message=message, pending_kind=None,
        open_question=open_question,
    )
    out = dict(parser.parse(_config(), block))
    print(json.dumps({"message": message, "low_stock": out.get("low_stock"), "intent": out.get("intent_hint"),
                      "domain_in_message": out.get("domain_in_message"),
                      "answer": out.get("open_question_answer"), "entities": out.get("entities")},
                     ensure_ascii=False))
    return out


def _fold(words: Any) -> list[str]:
    return [" ".join(str(w).split()).casefold() for w in (words or [])]


def _has(words: Any, *wanted: str) -> bool:
    got = _fold(words)
    return all(any(w == g or w in g.split() or w in g for g in got) for w in wanted)


def _no_product_entity(out: dict[str, Any], word: str) -> bool:
    """No product entity carries the category word, except one spelled exactly as a word
    the parser also placed in `low_stock` (the lane drops that one: the structured field
    wins, `low_stock_ask.take_entities`)."""
    ls = out.get("low_stock") or {}
    placed = set(_fold([*(ls.get("categories") or []), *(ls.get("brands") or []), *(ls.get("suppliers") or [])]))
    for e in out.get("entities") or []:
        if not isinstance(e, dict) or str(e.get("hint") or "") != "product":
            continue
        raw = " ".join(str(e.get("raw") or "").split()).casefold()
        if word in raw and raw not in placed:
            return False
    return True


FRESH = [
    # (message, categories, brands, suppliers, group_by)
    ("low stock report for sorento water tap", ["tap"], ["sorento"], [], None),
    ("low stock water closet, group them by supplier", ["water closet"], [], [], "supplier"),
    ("low stock for basin, per vendor", ["basin"], [], [], "supplier"),
    ("low stock report water tap, split by brand", ["tap"], [], [], "brand"),
    ("low stock kitchen sink by category and supplier", ["sink"], [], [], "supplier_category"),
    ("low stock report SRT-FT", ["srt-ft"], [], [], None),
    ("low stock 2 in 1 shower set", ["shower"], [], [], None),
    ("low stock report for basin mixer", ["basin mixer"], [], [], None),
    ("stok rendah paip air ikut pembekal", ["tap"], [], [], "supplier"),
    ("低库存 马桶 per vendor", ["water closet"], [], [], "supplier"),
    ("boss, low stock for sorento basin pls, ikut supplier", ["basin"], ["sorento"], [], "supplier"),
    ("sorento 水龙头 低库存报表", ["tap"], ["sorento"], [], None),
    ("low stock report water closet taiyang", ["water closet"], [], ["taiyang"], None),
    ("low stock water tap from jinbaichuan only", ["tap"], [], ["jinbaichuan"], None),
]


@pytest.mark.parametrize("message,categories,brands,suppliers,group_by", FRESH, ids=[c[0] for c in FRESH])
def test_a_fresh_ask(message, categories, brands, suppliers, group_by) -> None:
    out = _parse(message)
    ls = out.get("low_stock") or {}
    assert out.get("intent_hint") == "low_stock_report", out
    assert _has(ls.get("categories"), *categories), ls
    assert _has(ls.get("brands"), *brands), ls
    assert _has(ls.get("suppliers"), *suppliers), ls
    assert ls.get("group_by") in ((group_by,) if group_by else (None, "none")), ls
    for word in categories:
        assert _no_product_entity(out, word), out.get("entities")


def test_all_categories() -> None:
    ls = _parse("low stock report all categories by category").get("low_stock") or {}
    assert ls.get("all_categories") is True and ls.get("group_by") == "category", ls


def test_another_ask_leaves_it_empty() -> None:
    out = _parse("how many CB100 in BRW")
    ls = out.get("low_stock") or {}
    assert out.get("intent_hint") != "low_stock_report"
    assert not ls.get("categories") and not ls.get("suppliers") and ls.get("group_by") is None, ls


OPEN_CATEGORY = {"kind": "free", "about": "low_stock_report", "question": QUESTION, "owed": ["category"]}
OPEN_SUPPLIER = {
    "kind": "pick_one", "about": "low_stock_report", "question": 'Which supplier? Reply with a supplier name or "all".',
    "owed": ["supplier"],
    "options": [{"position": 1, "code": "JINBAICHUAN HARDWARE"}, {"position": 2, "code": "JINBAICHUAN TRADING"}],
}


def test_a_category_answers_the_question() -> None:
    out = _parse("water closet", previous=QUESTION, open_question=OPEN_CATEGORY)
    assert (out.get("open_question_answer") or {}).get("mode") == "fill", out
    assert _has((out.get("low_stock") or {}).get("categories"), "water closet")


def test_a_malay_category_answers_the_question() -> None:
    out = _parse("paip air", previous=QUESTION, open_question=OPEN_CATEGORY)
    assert (out.get("open_question_answer") or {}).get("mode") == "fill", out
    assert _has((out.get("low_stock") or {}).get("categories"), "tap")


def test_an_answer_naming_more_fills_both() -> None:
    out = _parse("water tap by supplier", previous=QUESTION, open_question=OPEN_CATEGORY)
    ls = out.get("low_stock") or {}
    assert _has(ls.get("categories"), "tap") and ls.get("group_by") == "supplier", ls


def test_all_answers_the_question() -> None:
    out = _parse("semua", previous=QUESTION, open_question=OPEN_CATEGORY)
    answer = out.get("open_question_answer") or {}
    ls = out.get("low_stock") or {}
    assert answer.get("mode") == "all" or ls.get("all_categories") is True, out


def test_a_pick_answers_the_supplier_question() -> None:
    out = _parse("the second one", previous="Which supplier do you mean?", open_question=OPEN_SUPPLIER)
    answer = out.get("open_question_answer") or {}
    assert answer.get("mode") == "pick" and answer.get("picked") == [2], answer


def test_cancel_answers_the_question() -> None:
    out = _parse("cancel", previous=QUESTION, open_question=OPEN_CATEGORY)
    assert (out.get("open_question_answer") or {}).get("mode") == "cancel", out


def test_a_new_question_does_not_answer_it() -> None:
    out = _parse("how many CB100 in BRW", previous=QUESTION, open_question=OPEN_CATEGORY)
    assert (out.get("open_question_answer") or {}).get("mode") is None, out
    assert out.get("intent_hint") != "low_stock_report", out


def test_clear_does_not_answer_it() -> None:
    out = _parse("clear", previous=QUESTION, open_question=OPEN_CATEGORY)
    assert (out.get("open_question_answer") or {}).get("mode") in (None, "cancel"), out
    assert out.get("intent_hint") != "low_stock_report" or (out.get("open_question_answer") or {}).get("mode") == "cancel"


REPORT = (
    "Low stock report (sorento water closet, all suppliers, no grouping) - as of 04/10/2026\n"
    "Low: 3 of 10 planned products"
)


@pytest.mark.parametrize("message,key,word", [
    ("taiyang only", "suppliers", "taiyang"),
    ("by supplier", "group_by", "supplier"),
    ("cabana only", "brands", "cabana"),
])
def test_a_refinement_of_the_report_just_shown(message, key, word) -> None:
    out = _parse(message, previous=REPORT)
    ls = out.get("low_stock") or {}
    assert out.get("intent_hint") == "low_stock_report", out
    assert out.get("domain_in_message") is False, out
    if key == "group_by":
        assert ls.get("group_by") == word, ls
    else:
        assert _has(ls.get(key), word), ls


# --------------------------------------------------------------------------- #
# End to end with the live parser: the REAL engine, lane and MCP presenter, the parser
# called for real with this branch's prompt; only the MCP transport is a double
# (`test_low_stock_filter_ask.Console`). This is the console pass the crew asks for.
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_console(session_factory, monkeypatch):
    from app.services.chatbot.head import parser as parser_mod
    from tests.chatbot import test_low_stock_filter_ask as harness

    console = harness._console(session_factory, monkeypatch, grants=[harness.GRANT, harness.SUPPLIER_KEY])
    monkeypatch.setattr(parser_mod, "resolve_config",
                        lambda db, *, current_date, override_version_id=None: _config())
    real_parse = parser_mod.parse

    def say(body: str) -> tuple[str, list[dict[str, Any]]]:
        # `Console.say` installs its double; put the real parser back over it.
        original = monkeypatch.setattr

        def _setattr(target, name, value, *args, **kwargs):
            if target is parser_mod and name == "parse":
                value = real_parse
            return original(target, name, value, *args, **kwargs)

        monkeypatch.setattr = _setattr
        try:
            text, calls = harness.Console.say(console, {}, body)
        finally:
            monkeypatch.setattr = original
        print(json.dumps({"say": body, "reply": text, "calls": calls}, ensure_ascii=False))
        return text, calls

    console.live_say = say
    return console


def _line(text: str) -> str:
    return text.splitlines()[0] if text else ""


class TestLiveConsole:
    def test_the_owner_case(self, live_console) -> None:
        text, calls = live_console.live_say("low stock report for sorento water tap")
        (args,) = calls
        assert args.get("categories") == ["SRT-FT"], args
        assert _line(text).startswith("Low stock report (sorento water tap, all suppliers, no grouping)"), text

    def test_per_vendor_with_a_chinese_category(self, live_console) -> None:
        _text, calls = live_console.live_say("低库存 马桶 per vendor")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"] and args.get("split") == "supplier", args

    def test_the_question_then_a_malay_answer(self, live_console) -> None:
        text, calls = live_console.live_say("low stock report")
        assert calls == [] and text == QUESTION
        _text, calls = live_console.live_say("paip air")
        (args,) = calls
        assert sorted(args.get("categories") or []) == ["CB-FT", "SRT-FT"], args

    def test_a_new_question_wins_over_the_open_question(self, live_console) -> None:
        live_console.live_say("low stock report")
        text, calls = live_console.live_say("how many CB100 in BRW")
        assert calls == [] and "as a category" not in text, text

    def test_split_by_brand_is_asked_and_the_pick_runs(self, live_console) -> None:
        text, calls = live_console.live_say("low stock water tap, split by brand")
        assert calls == [] and text.startswith("I can group the low stock report"), text
        _text, calls = live_console.live_say("2")
        (args,) = calls
        assert args.get("split") == "category", args

    def test_a_refinement_narrows_the_report(self, live_console) -> None:
        live_console.live_say("low stock report sorento water closet")
        text, calls = live_console.live_say("taiyang only")
        (args,) = calls
        assert args.get("categories") == ["SRT-WC"], args
        assert args.get("suppliers") == ["XIAMEN TAIYANG TECHNOLOGY CO.,LTD"], args
