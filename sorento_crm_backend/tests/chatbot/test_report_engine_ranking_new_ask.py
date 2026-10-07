"""REPORT-ENGINE hand test 8 Oct (AC-HT8-1 to 5): red tests.

G1: a verdict whose own order_status is "sales_ranking" is a NEW_ASK under an open
sales_report_detail offer, whatever domain_in_message says (key-level, never text).
P1-P3: the parser prompt teaches the customer / sales agent axes, the Chinese examples,
and the ranking words in the DOMAIN IN MESSAGE list.
"""
from __future__ import annotations

from tests.chatbot._turn_helpers import entity, verdict


def _offer():
    from app.services.chatbot.turn.pending import ask

    return ask(
        "sales_report_detail",
        [
            {"position": 1, "label": "Sales orders", "payload": {"value": "sales_order"}},
            {"position": 2, "label": "Delivery orders", "payload": {"value": "delivery_order"}},
        ],
        expects="sales_report_detail",
        payload={"filters": {"product_code": "CWC7606-SH"}},
    )


def _decide(v):
    from app.services.chatbot.turn.decide import decide
    from app.services.chatbot.turn.state import Focus

    return decide(v, Focus(products=[entity("CWC7606-SH")]), _offer())


def test_ranking_verdict_under_open_report_offer_is_new_ask():
    from app.services.chatbot.turn.decide import NEW_ASK

    d = _decide(verdict(order_status="sales_ranking", domain_in_message=False,
                        entities=[entity("CWC7606-SH")]))
    assert d.kind == NEW_ASK


def test_customer_ranking_with_this_product_entity_is_new_ask():
    from app.services.chatbot.turn.decide import NEW_ASK

    d = _decide(verdict(order_status="sales_ranking", domain_in_message=False, group_by="customer",
                        entities=[entity("这个产品")]))
    assert d.kind == NEW_ASK


def test_other_status_under_same_offer_still_refines():
    from app.services.chatbot.turn.decide import REFINE

    for status in ("sales_report", None):
        d = _decide(verdict(order_status=status, domain_in_message=False,
                            entities=[entity("CWC7606-SH")]))
        assert d.kind == REFINE, status
        assert d.why == "refines_standing_subject", status


def _addendum() -> str:
    from app.services import chatbot_parser_prompt

    return chatbot_parser_prompt.REPORT_ASK_ADDENDUM


def test_p1_dealer_as_ranked_noun_is_customer_axis():
    a = _addendum()
    for s in (
        "which dealer sold the most",
        "which customer bought the most",
        "哪一位经销商卖得最多",
        "哪个经销商卖得最多",
        "多少个经销商卖这个产品",
        'group_by "customer", sales_channel null',
    ):
        assert s in a, s


def test_p1_salesman_as_ranked_noun_is_sales_agent_axis():
    a = _addendum()
    for s in ("哪一位业务员卖得最多", "which salesman sold the most", 'group_by "sales_agent"'):
        assert s in a, s


def test_p2_sales_ranking_always_domain_in_message_true():
    assert "\nA sales_ranking verdict always sets domain_in_message true." in _addendum()


def test_salesman_example_line_is_complete():
    assert "the code a product entity." in _addendum()


def test_p3_domain_in_message_list_has_ranking_words():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    i = SEMANTIC_PARSER_PROMPT.index("== DOMAIN IN MESSAGE ==")
    block = SEMANTIC_PARSER_PROMPT[i:i + 900]
    for w in ("sold the most", "卖得最多", "排名"):
        assert w in block, w


def test_p3_bare_top_is_not_a_domain_word():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    i = SEMANTIC_PARSER_PROMPT.index("== DOMAIN IN MESSAGE ==")
    block = SEMANTIC_PARSER_PROMPT[i:i + 900]
    items = [w.strip() for w in block.split("\n  - ")[0].replace("\n", " ").replace(" - ", ",").split(",")]
    assert "top" not in items
    assert ", top," not in block


def _anaphora_verdict(status):
    return verdict(order_status=status, domain_in_message=True, entities=[entity("CWC7606-SH")],
                   anaphora={"backward_reference": True})


def test_ranking_with_anaphora_under_open_offer_is_new_ask():
    from app.services.chatbot.turn.decide import NEW_ASK

    assert _decide(_anaphora_verdict("sales_ranking")).kind == NEW_ASK


def test_non_ranking_anaphora_still_refines():
    from app.services.chatbot.turn.decide import REFINE

    d = _decide(_anaphora_verdict("sales_report"))
    assert d.kind == REFINE
    assert d.why == "domain_in_message_anaphora"
