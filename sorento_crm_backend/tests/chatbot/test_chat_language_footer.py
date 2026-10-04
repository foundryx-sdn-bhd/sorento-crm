"""CHAT-LANGUAGE fix round 1, items 4 to 6: text matchers, the refer line, the stock-only scope."""
from __future__ import annotations

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import fetch

TS = "2026-10-02T09:15:30"


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


# --------------------------------------------------------------------------- #
# Item 4 (S1): the footer matchers recognise the localized footer
# --------------------------------------------------------------------------- #


def test_item4_a_counted_set_with_other_brands_keeps_the_ms_footer_last():
    envelope = {
        "result_type": "stock",
        "intro": "Stock summary for the requested products.",
        "items": [
            {
                "title": "SRTTP1",
                "fields": [{"key": "product_code", "label": "Product Code", "value": "SRTTP1"}],
                "flags": {},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }
    ctx = {
        "semantic_input": {},
        "tool": "crm_inventory_stock_balance_list",
        "localizer": _loc("ms"),
        "predicate": {
            "qualifying_total": 1,
            "require": {"stock": True},
            "class_labels": ["tap"],
            "other_brands": [{"brand": "Bravat", "count": 79}],
        },
    }
    text = fetch.output_structurer(envelope, ctx)["response"]
    footer = "_Data dikemas kini: 02/10/2026 09:15:30_"
    assert text.endswith(footer), text
    assert text.index("Other brands") < text.index(footer), text


def test_item4_the_promo_footer_regex_matches_every_language():
    for lang in ("ms", "zh"):
        footer = _loc(lang).text("Data last updated: 02/10/2026 09:15:30")
        match = answer_mod._DATA_LAST_UPDATED_RE.search(f"intro\n\n_{footer}_")
        assert match and match.group(0) == f"_{footer}_", lang
    english = answer_mod._DATA_LAST_UPDATED_RE.search("x\n_Data last updated: 02/10/2026 09:15:30_")
    assert english is not None


# --------------------------------------------------------------------------- #
# Item 5 (S4): the refer line is recognised in every language
# --------------------------------------------------------------------------- #


def test_item5_refer_sentences_cover_the_three_languages():
    sentences = label_catalog.refer_sentences()
    assert "Sila rujuk jurujual anda." in sentences
    assert "请联系您的销售员。" in sentences
    assert len(sentences) == 3


def test_item5_an_ms_verdict_plus_an_escalation_offer_does_not_double_the_refer_line():
    from app.services.chatbot import dealer_stock

    ms = "A1 x 1: ya, stok ada. Sila rujuk jurujual anda."
    text, question = dealer_stock.without_escalation(
        f"{ms}\n\nWould you like me to escalate to the warehouse team?", None
    )
    assert text == ms
    assert question is None
    zh = "A1 x 1: 有库存。请联系您的销售员。"
    text, _ = dealer_stock.without_escalation(
        f"{zh}\n\nWould you like me to escalate to the warehouse team?", None
    )
    assert text == zh


# --------------------------------------------------------------------------- #
# Item 6 (S5): only the catalogued tools are localized
# --------------------------------------------------------------------------- #


def test_item6_a_tool_outside_the_localized_set_with_an_ms_localizer_stays_english():
    """Slice 1 pinned a PO envelope here; slice 2 localizes it, so a still-English tool stands in."""
    envelope = {
        "result_type": "products",
        "intro": "Stock summary for the requested products.",
        "items": [
            {
                "title": "PO-1",
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTSWT3001"},
                    {"label": "Total", "value": 51},
                ],
                "flags": {"discontinued": True},
            }
        ],
        "has_result": True,
        "last_updated_at": TS,
    }
    ctx = {"semantic_input": {}, "tool": "crm_master_products_list", "localizer": _loc("ms")}
    text = fetch.output_structurer(envelope, ctx)["response"]
    assert "*Product Code:* SRTSWT3001" in text
    assert "*Total:* 51" in text
    assert "PRODUCT DISCONTINUED" in text
    assert "_Data last updated: 02/10/2026 09:15:30_" in text
    assert "Jumlah" not in text and "Ringkasan" not in text and "dikemas" not in text


# --------------------------------------------------------------------------- #
# Fix round 2, items 3 and 4: any language, and a staff-edited wording, is still recognised
# --------------------------------------------------------------------------- #


def _edited_ms() -> Localizer:
    table = {
        **label_catalog.defaults("ms"),
        "Data last updated: {ts}": "Dikemas kini: {ts}",
        f"yes, we have stock. {label_catalog.REFER_TO_SALESMAN}": "ya, ada. Sila hubungi jurujual anda.",
    }
    return Localizer("ms", table)


def test_item4_an_edited_footer_is_still_recognised():
    loc = _edited_ms()
    assert "Dikemas kini:" in label_catalog.footer_leads(loc)
    assert "Dikemas kini:" not in label_catalog.footer_leads()
    match = answer_mod._data_last_updated_re(loc).search("x\n\n_Dikemas kini: 02/10/2026 09:15_")
    assert match and match.group(0) == "_Dikemas kini: 02/10/2026 09:15_"


def test_item4_an_edited_refer_sentence_is_still_recognised():
    from app.services.chatbot import dealer_stock

    loc = _edited_ms()
    assert "Sila hubungi jurujual anda." in label_catalog.refer_sentences(loc)
    body = "A1 x 1: ya, ada. Sila hubungi jurujual anda."
    text, _ = dealer_stock.without_escalation(
        f"{body}\n\nWould you like me to escalate to the warehouse team?", None, localizer=loc
    )
    assert text == body


def test_item3_compose_splits_on_any_languages_footer():
    from types import SimpleNamespace

    from app.services.chatbot.turn.compose import compose
    from tests.chatbot.test_chat_language_render import _env, _policy, _state

    # An English footer under an ms turn (a reply the lane did not localize).
    lane = "*Product Code:* SRTSWT3001-GM\n\n_Data last updated: 02/10/2026 09:15:30_"
    text = compose([_env(lane)], _state(), _policy(), ctx=SimpleNamespace(localizer=_loc("ms"))).text
    assert text.index("Tiada stok ditemui") < text.index("_Data last updated")
