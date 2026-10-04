"""CHAT-LANGUAGE slice 4: the answer.py offer suffixes are inline templates; the longest wins."""
from __future__ import annotations

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def test_the_continue_suffix_with_the_offer_is_one_sentence():
    out = _loc("ms").reply(
        "No stock found.\nReply with a number to continue, or would you like me to escalate to warehouse team?"
    )
    assert out.endswith("Balas dengan nombor untuk meneruskan, atau adakah anda mahu saya rujuk kepada pasukan warehouse?")
    assert "escalate" not in out


def test_the_staff_variants_without_the_offer_translate():
    assert _loc("zh").reply("Reply with a number to continue.") == "回复数字继续。"
    assert _loc("ms").reply("Reply a number to pick.") == "Balas nombor untuk memilih."
    assert _loc("zh").reply("Reply 'all dates' to search without the date filter.") == "回复 'all dates' 可不限日期搜索。"


def test_the_yes_suffix_keeps_the_team_verbatim():
    out = _loc("ms").reply("Reply a number to pick, or 'yes' to escalate to customer service.")
    assert out == "Balas nombor untuk memilih, atau 'yes' untuk rujuk kepada customer service."
    assert _loc("ms").reply(out) == out
