"""REPORT-ENGINE owner retest 8 Oct (AC-HT8-6 to 10): red tests.

Prompt tests are static. Code tests read parser keys only (scripted verdicts, no LLM):
C1 the lane's refine applies a channel / brand word, C2 a fresh ranking takes no channel
from the focus.
"""
from __future__ import annotations

import pytest

from tests.chatbot._turn_helpers import verdict

HELD = {
    "group_by": "customer",
    "basis": "delivered",
    "measure": "qty",
    "sort": "desc",
    "top_n": 3,
    "date_from": "2026-01-01",
    "date_to": "2026-12-31",
    "category_ids": ["cat-wc"],
    "brand_ids": ["brand-sorento"],
}


def _refine_out(**kw):
    out = {"order_status": "sales_ranking", "ranking_refine": True, "sales_ranking_frame": dict(HELD),
           "entities": [], "report_ask_words": {}}
    out.update(kw)
    return out


def _args(out):
    from app.services.chatbot.lanes.business import report_ask as ra

    outcome, line = ra.settle(None, out, [])
    assert line is None, line
    assert outcome is not None
    return ra.route_args(outcome)


def test_c1_refine_with_dealer_channel_adds_channel_and_keeps_held_args():
    a = _args(_refine_out(sales_channel="dealer"))
    assert a.get("channel") == "dealer"
    assert a["brand_ids"] == ["brand-sorento"] and a["category_ids"] == ["cat-wc"]
    assert a["group_by"] == "customer" and a["top_n"] == 3


def test_c1_refine_with_brand_word_adds_resolved_brand_ids(monkeypatch):
    from app.services.chatbot.lanes.business import report_ask as ra

    monkeypatch.setattr(ra, "_resolve_word", lambda db, hint, word: (["brand-cabana"], ["Cabana"]))
    held = {k: v for k, v in HELD.items() if k != "brand_ids"}
    a = _args(_refine_out(sales_ranking_frame=held, report_ask_words={"brand": ["cabana"]}))
    assert a.get("brand_ids") == ["brand-cabana"]
    assert a["category_ids"] == ["cat-wc"] and a["group_by"] == "customer"


@pytest.mark.parametrize("held_channel", ["project", None])
def test_c1_refine_without_a_channel_keeps_the_held_channel(held_channel):
    frame = dict(HELD)
    if held_channel:
        frame["channel"] = held_channel
    a = _args(_refine_out(sales_ranking_frame=frame, sales_channel=None, top_n=5))
    assert a.get("channel") == held_channel


def _lane_out(v, focus):
    from app.services.chatbot.turn_runtime import lane_parse_output

    return lane_parse_output(v, focus=focus)


def test_c2_fresh_ranking_takes_no_channel_from_the_focus():
    from app.services.chatbot.turn.state import Focus

    v = verdict(order_status="sales_ranking", ranking_refine=False, sales_channel=None, group_by="customer",
                domain_hint="order")
    out = _lane_out(v, Focus(sales_channel="dealer"))
    assert not out.get("sales_channel")


def test_c2_refine_on_a_held_dealer_ranking_still_runs_with_dealer():
    from app.services.chatbot.turn.state import Focus, report_from_frame

    frame = dict(HELD, channel="dealer")
    v = verdict(order_status="sales_ranking", ranking_refine=True, sales_channel=None, top_n=5, domain_hint="order")
    out = _lane_out(v, Focus(sales_channel="dealer", report=report_from_frame(frame)))
    assert _args(out).get("channel") == "dealer"


def _addendum() -> str:
    from app.services import chatbot_parser_prompt

    return chatbot_parser_prompt.REPORT_ASK_ADDENDUM


def test_p4_business_model_in_addendum():
    a = _addendum()
    for s in ("Sorento -> salesman (业务员", "A dealer IS our customer", "代理商", "客户"):
        assert s in a, s


def test_p5_ranked_noun_in_any_language():
    a = _addendum()
    for s in ("In any language or mix, the ranked noun", "哪一位customer sell最多"):
        assert s in a, s


def test_p6_dealer_only_is_a_channel_refine():
    a = _addendum()
    assert '"dealer only" -> ranking_refine true, sales_channel "dealer"' in a


@pytest.mark.parametrize(
    "zh,en",
    [("马桶", "water closet"), ("面盆", "wash basin"), ("水龙头", "tap"),
     ("浴室柜", "bathroom cabinet"), ("厨房水槽", "kitchen sink"), ("淋浴", "shower")],
)
def test_p7_chinese_category_words_translate_to_english_raw(zh, en):
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT as P

    assert "category entity's canonical_code is the English class word" in P
    assert f"{zh} -> canonical_code \"{en}\"" in P
