"""REPORT-ENGINE round 4 (AC-HT8-11 to 15): red tests. Parser keys only, no LLM."""
from __future__ import annotations

import re

import pytest

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot.test_report_engine_owner_retest import HELD, _args

ZH = re.compile(r"[一-鿿]")


def _lane_out(v, focus):
    from app.services.chatbot.turn_runtime import lane_parse_output

    return lane_parse_output(v, focus=focus)


# AC-HT8-11
def test_refine_never_takes_the_focus_channel():
    from app.services.chatbot.turn.state import Focus, report_from_frame

    v = verdict(order_status="sales_ranking", ranking_refine=True, sales_channel=None, top_n=10, domain_hint="order")
    out = _lane_out(v, Focus(sales_channel="dealer", report=report_from_frame(dict(HELD))))
    assert not out.get("sales_channel")
    assert _args(out).get("channel") is None


def test_refine_keeps_the_held_frame_channel():
    from app.services.chatbot.turn.state import Focus, report_from_frame

    frame = dict(HELD, channel="dealer")
    v = verdict(order_status="sales_ranking", ranking_refine=True, sales_channel=None, top_n=10, domain_hint="order")
    out = _lane_out(v, Focus(sales_channel="dealer", report=report_from_frame(frame)))
    assert _args(out).get("channel") == "dealer"


def test_sales_report_still_gets_the_focus_channel():
    from app.services.chatbot.turn.state import Focus, report_from_frame

    v = verdict(order_status="sales_report", sales_channel=None, domain_hint="order")
    assert _lane_out(v, Focus(sales_channel="dealer")).get("sales_channel") == "dealer"


# AC-HT8-12
def _top_selling_words(canonical):
    from app.services.chatbot.turn import apply as ap
    from app.services.chatbot.turn.decide import CARRY, Decision
    from app.services.chatbot.turn.plan import Trace
    from app.services.chatbot.turn.state import Focus, report_from_frame

    focus = Focus(status="top_selling", top_selling={"rank_by": "quantity"})
    e = entity("马桶", "category", canonical_code=canonical)
    ap._top_selling_rules(focus, verdict(order_status="top_selling", entities=[e]), Decision(CARRY, "x"), Trace())
    return focus.top_selling["category_words"]


def test_top_selling_reads_the_category_canonical_code_before_raw():
    assert _top_selling_words("water closet") == ["water closet"]


def test_top_selling_category_without_canonical_code_keeps_raw():
    assert _top_selling_words(None) == ["马桶"]


def _ranking_words(canonical):
    from app.services.chatbot.lanes.business import report_ask as ra

    v = verdict(order_status="sales_ranking", entities=[entity("马桶", "category", canonical_code=canonical)])
    return ra.take_words(v, "")["report_ask_words"]["category"]


def test_ranking_take_words_reads_the_category_canonical_code():
    assert _ranking_words("water closet") == ["water closet"]


def test_ranking_take_words_category_without_canonical_code_keeps_raw():
    assert _ranking_words(None) == ["马桶"]


def test_prompt_carries_the_category_canonical_code_rule_outside_top_selling():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as P, TOP_SELLING_ADDENDUM as T

    rule = "category entity's canonical_code is the English class word"
    assert rule in P
    for pair in ('马桶 -> canonical_code "water closet"', '面盆 -> canonical_code "wash basin"',
                 '水龙头 -> canonical_code "tap"'):
        assert pair in P, pair
    assert rule not in T


# AC-HT8-13
def test_prompt_channel_section_and_this_product_rules():
    from app.services.chatbot_parser_prompt import SALES_REPORT_ADDENDUM as S, SEMANTIC_PARSER_PROMPT as P

    assert S.index("== SALES_CHANNEL") < S.index('"which dealer sold the most X" -> sales_channel null')
    assert 'a bare answer ("qty", "数量", "delivered", "已送货") never sets sales_channel' in S
    assert '"这个产品" / "this product" is never an entity' in P


# AC-HT8-14
def test_ranking_clarify_lines_are_catalogued_in_chinese():
    from app.services.chatbot import label_catalog as lc
    from app.services.chatbot.lanes.business import report_ask as ra

    for english in (ra.PERIOD_QUESTION, ra.TOP_N_QUESTION, "I don't know '{word}' as a brand.",
                    "I don't know '{word}' as a sales agent.", "I don't know '{word}' as a location."):
        assert ZH.search(lc.LABELS.get(english, {}).get("zh", "")), english


# AC-HT8-15
def test_refine_list_closing_line_allows_the_brand_or_category_entity():
    from app.services.chatbot_parser_prompt import REPORT_ASK_ADDENDUM as A

    assert "entities [] (except the brand or category refine)" in A


# Round 5
def test_category_canonical_code_rule_is_scoped_to_ranking_asks():
    from app.services.chatbot_parser_prompt import CANONICAL_CODE_ADDENDUM as C

    scope = "on a sales_ranking or top_selling ask, a category entity's canonical_code is the English class word"
    assert scope in C
    assert 'about hint "product" only, except:' in C
    assert C.index('about hint "product" only, except:') < C.index(scope)


@pytest.mark.parametrize("zh,en", [("坐厕", "water closet"), ("洗手盆", "wash basin"),
                                   ("水槽", "kitchen sink"), ("花洒", "shower")])
def test_category_synonyms_are_in_the_prompt(zh, en):
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as P

    assert f'{zh} -> canonical_code "{en}"' in P.replace("厨房水槽", "")


@pytest.mark.parametrize("noun", ["period", "basis"])
def test_unknown_period_and_basis_lines_are_catalogued_in_chinese(noun):
    from app.services.chatbot import label_catalog as lc

    english = f"I don't know '{{word}}' as a {noun}."
    assert ZH.search(lc.LABELS.get(english, {}).get("zh", "")), english
