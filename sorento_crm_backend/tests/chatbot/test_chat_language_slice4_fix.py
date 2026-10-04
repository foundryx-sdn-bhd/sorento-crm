"""CHAT-LANGUAGE slice 4 fix round: composer sentences with the domain label and the joiners,
rendered from the REAL compose output, then the final reply pass."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State

from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


def _policy() -> Policy:
    rows = [
        {**_domain_row("inventory", narrowing={"product": "list_all"}), "label": "stock"},
        {**_domain_row("order", narrowing={"product": "list_all"}), "label": "orders"},
    ]
    return Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)


def _state() -> State:
    return State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)


def _env(**over) -> dict:
    env = {
        "domain": "inventory",
        "denied": False,
        "entities": ["ABC123"],
        "product_codes": ["ABC123"],
        "figures": [],
        "files": [],
        "miss": [],
        "has_result": False,
        "tool_has_result": False,
        "unresolved": [],
        "error": None,
        "lane_text": None,
    }
    env.update(over)
    return env


def _final(env: dict, lang: str) -> list[str]:
    loc = _loc(lang)
    text = compose([env], _state(), _policy(), ctx=SimpleNamespace(localizer=loc)).text
    return loc.reply(text).split("\n")


@pytest.mark.parametrize(
    "lang, header, fetch",
    [
        ("ms", "*stok* untuk ABC123:", "Saya tidak dapat mendapatkan stok sekarang, sila cuba lagi."),
        ("zh", "ABC123 的*库存*：", "暂时无法获取库存，请稍后再试。"),
    ],
)
def test_the_domain_label_reads_in_the_reply_language_in_header_and_fetch_error(lang, header, fetch):
    lines = _final(_env(error="timeout"), lang)
    assert header in lines
    assert fetch in lines


def test_the_not_enabled_line_uses_the_translated_label():
    assert "*stok*: ini tidak diaktifkan untuk akaun anda." in _final(_env(denied=True), "ms")
    assert "*库存*：您的账户未开通此功能。" in _final(_env(denied=True), "zh")


def test_the_rung_line_translates_the_labels_and_the_joiner():
    env = _env(rungs_tried=["inventory", "order"])
    assert "Tiada juga untuk stok atau pesanan." in _final(env, "ms")
    assert "库存或订单 也没有。" in _final(env, "zh")


def test_the_unplaced_line_translates_the_joiner_only_in_the_composer_list():
    env = _env(unresolved=["X", "Y"], error="timeout")
    assert "Saya tidak dapat menemui X atau Y." in _final(env, "ms")
    assert "找不到 X或Y。" in _final(env, "zh")


def test_an_or_inside_a_presenter_value_or_a_names_token_is_never_split():
    loc = _loc("ms")
    # Not a compose-joined list: the same words arrive in a value and stay as they are.
    assert loc.reply("I could not find ZZT tray or stock.") == "Saya tidak dapat menemui ZZT tray or stock."
    assert loc.reply("Nothing on stock or orders either.") == "Tiada juga untuk stock or orders."
    assert loc.reply("*stock* for A:") == "*stock* untuk A:"


def test_item4_mid_line_the_company_offer_beats_the_plain_team_template():
    out = _loc("ms").reply("Got it. Would you like me to escalate to *ACME* warehouse team?")
    assert out == "Got it. Adakah anda mahu saya rujuk kepada pasukan warehouse *ACME*?"


def test_item5_a_token_never_spans_a_newline():
    text = "I could not find A\nB."
    assert _loc("ms").reply(text) == text
    assert _loc("ms").reply("I could not find A.\nB.") == "Saya tidak dapat menemui A.\nB."


def test_item8_zh_drops_the_space_after_a_full_width_stop_between_two_sentences():
    out = _loc("zh").reply("Done. Okay, noted. Which one?")
    assert out == "Done. 好的，已记录。哪一个？"
    assert _loc("zh").reply("Which one? Which one?") == "哪一个？哪一个？"
    # ms keeps its ASCII space.
    assert _loc("ms").reply("Okay, noted. Which one?") == "Baik, dicatat. Yang mana satu?"


def test_item7_the_new_rows_translate_ms_and_zh():
    ms, zh = _loc("ms"), _loc("zh")
    assert ms.reply("What would you like me to know?") == "Apa yang anda mahu saya tahu?"
    # Too generic for running text: only the composer that owns it fills it (`fill`).
    assert zh.reply("I have A and B.") == "I have A and B."
    assert zh.fill("I have {names}.", names="A and B") == "我已记下 A and B。"
    assert ms.reply("Do you mean customer ACME or sales agent TAN? Reply 1 for the customer, 2 for the sales agent.") == (
        "Adakah anda maksudkan pelanggan ACME atau ejen jualan TAN? Balas 1 untuk pelanggan, 2 untuk ejen jualan."
    )
    assert ms.reply(
        "Do you mean a customer named 'tan' or sales agent TAN? Reply 1 for the customer, 2 for the sales agent."
    ).startswith("Adakah anda maksudkan pelanggan bernama 'tan' atau ejen jualan TAN?")
    assert zh.reply("and 3 others, reply with the full code.") == "还有 3 个，请回复完整代码。"
    assert ms.reply(
        "This inquiry has been routed to the respective person-in-charge (PIC) from warehouse team. "
        "We will get back to you soon. Thanks for your patience."
    ).startswith("Pertanyaan ini telah diserahkan kepada pegawai bertanggungjawab (PIC) daripada pasukan warehouse.")


def test_item7_the_short_question_header_reads_in_the_reply_language():
    from app.services.chatbot.turn import compose as compose_mod
    from app.services.chatbot.turn.pending import ask

    options = [{"position": i, "label": f"C{i}", "code": f"C{i}"} for i in range(1, 8)]
    pending = ask("product_pick", options, asked_at_turn=1, payload={})
    state = SimpleNamespace(focus=SimpleNamespace(status="top_selling", top_selling={"x": 1}), turn_no=2)
    loc = _loc("ms")
    out = compose_mod.compose_question(pending, state, loc)
    final = loc.reply(out.text)
    assert "Saya menemui 7, sila taip lebih sedikit daripada nama itu." in final
    assert "Which" not in final


def test_final_entities_only_joiner_and_lead_read_in_the_reply_language():
    from app.services.chatbot.turn.compose import entities_only_reply

    for lang, expected in (
        ("ms", "Tidak dapat menemui A dan B. Sila tanya semula dengan kod yang betul."),
        ("zh", "找不到 A和B。请用正确的代码再问一次。"),
    ):
        loc = _loc(lang)
        text = entities_only_reply([], ["A", "B"], from_photo=False, localizer=loc)
        assert loc.reply(text) == expected, lang
    ms = _loc("ms")
    placed = entities_only_reply(["A", "B"], [], from_photo=False, localizer=ms)
    assert ms.reply(placed) == "Saya ada A dan B. Apa yang anda mahu saya tahu?"


def test_final_a_generic_i_have_sentence_inside_a_reply_is_left_alone():
    ms = _loc("ms")
    assert ms.reply("I have checked with the warehouse.") == "I have checked with the warehouse."
    assert "I have {names}." not in label_catalog.INLINE


def test_final_a_very_long_line_is_not_scanned_and_stays_fast():
    import time

    ms = _loc("ms")
    line = "I could not find " + "x" * 5000
    start = time.perf_counter()
    assert ms.reply(line) == line
    assert ms.reply("y" * 5000 + " Would you like me to escalate?") == "y" * 5000 + " Would you like me to escalate?"
    assert time.perf_counter() - start < 1.0
    # A short line next to it still translates.
    assert ms.reply("y" * 5000 + "\nWould you like me to escalate?").endswith("\nAdakah anda mahu saya rujuk kepada pasukan kami?")


def test_final_a_token_is_bounded_to_200_characters():
    ms = _loc("ms")
    ok = "I could not find " + "x" * 200 + "."
    too_long = "I could not find " + "x" * 201 + "."
    assert ms.reply(ok).startswith("Saya tidak dapat menemui")
    assert ms.reply(too_long) == too_long
