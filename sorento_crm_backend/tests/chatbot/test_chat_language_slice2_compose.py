"""CHAT-LANGUAGE slice 2: compose still recognises the localized bare "no results" fallback."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.turn.compose import compose

from tests.chatbot.test_chat_language_render import _env, _policy, _state


def _text(lane_text: str, lang: str | None, *, domain: str = "inventory", label: str = "stock") -> str:
    from app.services.chatbot.turn.policy import Policy
    from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

    ctx = SimpleNamespace(localizer=Localizer(lang, label_catalog.defaults(lang))) if lang else None
    env = _env(lane_text)
    env["figures"] = []
    env["domain"] = domain
    row = {**_domain_row(domain, narrowing={"product": "list_all"}), "label": label}
    policy = Policy.from_rows(domains=[row], kinds=[], tier_order=TIER_ORDER_FIXTURE)
    return compose([env], _state(), policy, ctx=ctx).text


def test_the_english_bare_fallback_is_replaced_by_the_subject_line():
    text = _text("No matching results found.", None)
    assert "No matching results found." not in text
    assert "No stock found for" in text


# The policy rows' own labels (`turn/policy_rows.py`): stock :134, orders :155, last in :269,
# outstanding purchase orders :293, last purchase cost :307.
ROWS = [
    ("inventory", "stock", "Tiada stok ditemui untuk", "未找到 "),
    ("order", "orders", "Tiada pesanan ditemui untuk", "的订单。"),
    ("last_in", "last in", "Tiada SPO ditemui untuk", "的 SPO。"),
    ("po_outstanding", "outstanding purchase orders", "Tiada PO ditemui untuk", "的 PO。"),
    ("last_cost", "last purchase cost", "Tiada kos belian ditemui untuk", "的采购成本。"),
]


def test_an_ms_and_a_zh_bare_fallback_name_the_subject_in_the_replys_language():
    for domain, label, ms_line, zh_part in ROWS:
        for lang in ("ms", "zh"):
            localized = label_catalog.LABELS["No matching results found."][lang]
            text = _text(localized, lang, domain=domain, label=label)
            assert localized not in text, (label, lang)
            assert "found for" not in text, (label, lang)
            if lang == "ms":
                assert ms_line in text, (label, text)
            else:
                assert zh_part in text and "未找到" in text, (label, text)
