"""CHAT-COMPOSER S3a red tests: one miss wording on every producer (M1 to M5), the domain
word in "But no X matched these." (M6), ms and zh through the existing catalogue.

Goldens are literals captured from main code on 17 Oct (S2 landed, gate off).
Everything else a miss carries (offer sentence, "Reply with a code to continue" closer, the
did-you-mean heads, the sibling picker) is owner-ruled and must be byte-identical in both
gate states.
"""
from __future__ import annotations

import re

import pytest

from app.services.chatbot.label_catalog import Localizer, defaults
from tests.chatbot.test_composers_stock_xd import _set_gate
from tests.chatbot.test_multi_code_dym import SUGGEST, _answer, _envelope
from app.services.chatbot.turn.state import Profile

STOCK = "Stock availability:\n\n*Product Code:* SRTWC286\n*Warehouse:* BUKIT RAJA\n*Quantity On Hand:* 12"
ESC = "Would you like me to escalate to warehouse team?"
DYM = (
    '\n\nCouldn\'t find "srt5764" (product). Did you mean:\n1. SRT57-CR\n2. SRT5713\n3. SRT5732'
)
CLOSING = "Reply with a code to continue, or would you like me to escalate to warehouse team?"


def _ms(text):
    return Localizer("ms", defaults("ms")).reply(text)


def _zh(text):
    return Localizer("zh", defaults("zh")).reply(text)


# --------------------------------------------------------------------------- #
# Helpers in composers/__init__.py and composers/misses.py
# --------------------------------------------------------------------------- #


def test_misses_not_found():
    from app.services.chatbot.composers import Reply, misses

    r = misses.not_found(["A"])
    assert isinstance(r, Reply)
    assert r.text == "Couldn't find A."
    assert r.question is None and r.offer is None
    assert misses.not_found(["A", "B"]).text == "Couldn't find A, B."
    assert misses.not_found(["A", "B"], join=lambda xs: " or ".join(xs)).text == "Couldn't find A or B."


def test_stock_miss_line_uses_misses(monkeypatch):
    from app.services.chatbot.composers import misses, stock

    _set_gate(monkeypatch, True)
    assert stock.miss_line(["X"]) == misses.not_found(["X"]).text == "Couldn't find X."
    assert stock.miss_line(["X", "Y"]) == misses.not_found(["X", "Y"]).text
    _set_gate(monkeypatch, False)
    assert stock.miss_line(["X"]) == "Couldn't find: X."


def test_domain_word():
    from app.services.chatbot.composers import misses

    assert misses.domain_word("inventory") == "stock"
    assert misses.domain_word("master_products") == "product"
    assert misses.domain_word("spo_allocation") == "SPO line"
    assert misses.domain_word("incoming") == "incoming"
    assert misses.domain_word("delivery_orders") == "delivery orders"
    assert misses.MISS_WORD == {"inventory": "stock", "master_products": "product", "spo_allocation": "SPO line"}


def test_quantity_question_rides():
    from app.services.chatbot.composers import misses

    assert misses.quantity_question_rides(["inventory"]) is True
    assert misses.quantity_question_rides(["inventory", "promotion"]) is False
    assert misses.quantity_question_rides([]) is False


# --------------------------------------------------------------------------- #
# M1: compose's unplaced-token lines, and Answer.miss_text
# --------------------------------------------------------------------------- #


def test_compose_unplaced_line_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    one = _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})).text
    assert one == f"{STOCK}\nCouldn't find zzq123.\n\n{ESC}"
    two = _answer(_envelope({"SRTWC286": 12}, ["zzq123", "yyq9"], {})).text
    assert two == f"{STOCK}\nCouldn't find zzq123 or yyq9.\n\n{ESC}"


def test_compose_unsuggested_beside_a_did_you_mean_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _answer(_envelope({"SRTWC286": 12}, ["srt5764", "zzq123"], {"srt5764": SUGGEST})).text
    assert text == f"{STOCK}{DYM}\n\nCouldn't find zzq123.\n{CLOSING}"


def test_compose_unplaced_line_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})).text == f"{STOCK}\nI could not find zzq123.\n\n{ESC}"
    assert _answer(_envelope({"SRTWC286": 12}, ["zzq123", "yyq9"], {})).text == (
        f"{STOCK}\nI could not find zzq123 or yyq9.\n\n{ESC}"
    )
    assert _answer(_envelope({"SRTWC286": 12}, ["srt5764", "zzq123"], {"srt5764": SUGGEST})).text == (
        f"{STOCK}{DYM}\n\nI could not find zzq123.\n{CLOSING}"
    )
    assert _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {}), Profile(tier="office")).text == (
        f"{STOCK}\nI could not find zzq123."
    )


def test_answer_miss_text_stamped_gate_on_only(monkeypatch):
    _set_gate(monkeypatch, False)
    assert getattr(_answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})), "miss_text", "") == ""
    _set_gate(monkeypatch, True)
    assert _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})).miss_text == "Couldn't find zzq123."
    # nothing unplaced: nothing stamped
    assert _answer(_envelope({"SRTWC286": 12}, [], {})).miss_text == ""


def test_offer_and_closer_text_unchanged_gate_on(monkeypatch):
    """The text around the miss sentence is the same in both gate states."""
    cases = [
        (_envelope({"SRTWC286": 12}, ["zzq123"], {}), None),
        (_envelope({"SRTWC286": 12}, ["srt5764", "zzq123"], {"srt5764": SUGGEST}), None),
        (_envelope({"SRTWC286": 12}, ["zzq123"], {}), Profile(tier="office")),
    ]
    for env, profile in cases:
        _set_gate(monkeypatch, False)
        off = _answer(env, profile).text
        _set_gate(monkeypatch, True)
        on = _answer(env, profile).text
        assert on == off.replace("I could not find", "Couldn't find")


def test_dealer_eta_miss_reads_miss_text_gate_on(monkeypatch):
    from app.services.chatbot.engine import _dealer_names_the_miss
    from app.services.chatbot.turn import compose as turn_compose

    _set_gate(monkeypatch, True)
    ans = turn_compose.Answer(
        text="SRTW2000: ✅ ETA 19/10/2026\n\nPlease refer to your salesman.\n\nCouldn't find FOO99.",
        miss_text="Couldn't find FOO99.",
    )
    out = _dealer_names_the_miss(ans, [{"unresolved": ["FOO99"]}])
    assert out.text == "SRTW2000: ✅ ETA 19/10/2026\n\nCouldn't find FOO99.\n\nPlease refer to your salesman."
    assert out.text.count("Couldn't find") == 1


def test_dealer_eta_miss_gate_off_golden(monkeypatch):
    from app.services.chatbot.engine import _dealer_names_the_miss
    from app.services.chatbot.turn import compose as turn_compose

    _set_gate(monkeypatch, False)
    ans = turn_compose.Answer(
        text="SRTW2000: ✅ ETA 19/10/2026\n\nPlease refer to your salesman.\n\nI could not find FOO99."
    )
    assert _dealer_names_the_miss(ans, [{"unresolved": ["FOO99"]}]).text == (
        "SRTW2000: ✅ ETA 19/10/2026\n\nCouldn't find: FOO99.\n\nPlease refer to your salesman."
    )


# --------------------------------------------------------------------------- #
# M2 to M5: the answer lane and so_status producers
# --------------------------------------------------------------------------- #


def test_unsuggested_line(monkeypatch):
    from app.services.chatbot.lanes.business.answer import _unsuggested_line

    _set_gate(monkeypatch, False)
    assert _unsuggested_line(["X"]) == "\n\nI could not find X."
    assert _unsuggested_line(["A", "B", "C"]) == "\n\nI could not find A, B or C."
    assert _unsuggested_line([]) == ""
    _set_gate(monkeypatch, True)
    assert _unsuggested_line(["X"]) == "\n\nCouldn't find X."
    assert _unsuggested_line([]) == ""


def test_not_understood_line(monkeypatch):
    from app.services.chatbot.lanes.business.answer import not_understood_line

    _set_gate(monkeypatch, False)
    assert not_understood_line(["pink", "xx"]) == 'Couldn\'t find: "pink" and "xx".'
    assert not_understood_line([]) == ""
    _set_gate(monkeypatch, True)
    assert not_understood_line(["pink", "xx"]) == 'Couldn\'t find "pink" and "xx".'
    assert not_understood_line([]) == ""


def test_what_you_want_missing(monkeypatch):
    from app.services.chatbot.lanes.business.answer import what_you_want_reply

    _set_gate(monkeypatch, False)
    assert what_you_want_reply("taps", [], missing="pink (finish)", team="sales") == (
        "Here's what you want: taps\n\nCouldn't find: pink (finish). Would you like me to escalate to sales team?"
    )
    _set_gate(monkeypatch, True)
    assert what_you_want_reply("taps", [], missing="pink (finish)", team="sales") == (
        "Here's what you want: taps\n\nCouldn't find pink (finish). Would you like me to escalate to sales team?"
    )
    # the other tails and the offer wording are untouched
    assert what_you_want_reply("taps", ["• finish: gunmetal"], leg="incoming", team="sales").endswith(
        "But no incoming matched these. Would you like me to escalate to sales team?"
    )
    assert what_you_want_reply("taps", [], team=None).endswith("But none matched.")


def test_what_you_want_blocked_contact_keeps_the_refer_line(monkeypatch):
    from app.services.chatbot.lanes.business.answer import what_you_want_reply
    from app.services.chatbot.turn.task import REFER_TO_SALESMAN

    _set_gate(monkeypatch, True)
    out = what_you_want_reply("taps", [], missing="pink (finish)", team=REFER_TO_SALESMAN)
    assert out.endswith(f"Couldn't find pink (finish). {REFER_TO_SALESMAN}")


class _Po:
    def __init__(self, missing, cards=()):
        self.groups = {}
        self.cards = list(cards)
        self.missing = missing
        self.refused = False


def test_so_status_miss(monkeypatch):
    from app.services.chatbot import so_status

    _set_gate(monkeypatch, False)
    assert so_status.po_ref_reply_text(_Po(["X"]), "X") == "I could not find PO ref X."
    assert so_status.reply_text(_Po(["A", "B"], ["C1"]), refusal="") == "C1\n\nI could not find A or B."
    assert so_status.reply_text(_Po(["A", "B", "C"]), refusal="") == "I could not find A, B or C."
    _set_gate(monkeypatch, True)
    assert so_status.po_ref_reply_text(_Po(["X"]), "X") == "Couldn't find PO ref X."
    assert so_status.reply_text(_Po(["A", "B"], ["C1"]), refusal="") == "C1\n\nCouldn't find A or B."
    assert so_status.reply_text(_Po(["A", "B", "C"]), refusal="") == "Couldn't find A, B or C."


def test_miss_sentences_translate_ms_zh(monkeypatch):
    from app.services.chatbot import so_status
    from app.services.chatbot.lanes.business.answer import (
        _unsuggested_line,
        not_understood_line,
        what_you_want_reply,
    )

    _set_gate(monkeypatch, True)
    sentences = [
        _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})).miss_text,
        _unsuggested_line(["X"]).strip(),
        not_understood_line(["pink", "xx"]),
        what_you_want_reply("taps", [], missing="pink (finish)", team=None).split("\n\n")[-1],
        so_status.po_ref_reply_text(_Po(["X"]), "X"),
        so_status.reply_text(_Po(["A", "B"]), refusal=""),
    ]
    for s in sentences:
        assert s.startswith("Couldn't find"), s
        assert _ms(s).startswith("Tidak dapat menemui"), (s, _ms(s))
        assert _zh(s).startswith("找不到"), (s, _zh(s))
        assert "Couldn't find" not in _ms(s) and "Couldn't find" not in _zh(s)


# --------------------------------------------------------------------------- #
# M6: the domain word (cp0-75e516bd40)
# --------------------------------------------------------------------------- #


def _breakdown(domain):
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    def rows(n):
        return [
            {
                "entity_type": "product",
                "uuid": f"u{i}",
                "canonical_code": f"SRTWC18{i}-BL",
                "code": f"SRTWC18{i}-BL",
                "display": {"description": "d"},
            }
            for i in range(n)
        ]

    parser = {
        "domain_hint": domain,
        "entities": [{"raw": "intelligent toilet", "hint": "product", "confident": False, "current_message": True}],
        "routing": {"suggested_team": "warehouse"},
        "access_levels": [],
    }
    resolved = {
        "by_entity_type": {"product": ["SRTWC180-BL"]},
        "resolutions": [{"token": "intelligent toilet", "matches": rows(10)}],
        "unresolved_tokens": [],
        "tokens": [],
    }
    gate = {"gate_passed": True, "compatible_entities": rows(10)}
    return not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)["escalate_message"]


HEAD = "Here's what you want:\n• product: SRTWC180-BL (+9 more)\n\n"


@pytest.mark.parametrize(
    "domain,off_word,on_word",
    [("master_products", "master products", "product"), ("inventory", "inventory", "stock"), ("incoming", "incoming", "incoming")],
)
def test_breakdown_miss_word(monkeypatch, domain, off_word, on_word):
    _set_gate(monkeypatch, False)
    assert _breakdown(domain) == f"{HEAD}But no {off_word} matched these. {ESC}"
    _set_gate(monkeypatch, True)
    assert _breakdown(domain) == f"{HEAD}But no {on_word} matched these. {ESC}"


@pytest.mark.parametrize("domain", ["master_products", "inventory"])
def test_breakdown_miss_word_translates(monkeypatch, domain):
    _set_gate(monkeypatch, True)
    text = _breakdown(domain)
    for out in (_ms(text), _zh(text)):
        assert "matched these" not in out, out
        assert "SRTWC180-BL" in out


# --------------------------------------------------------------------------- #
# Property: no S3a gate-on output carries an old miss wording or an internal name
# --------------------------------------------------------------------------- #


def test_s3_output_clean(monkeypatch):
    from app.services.chatbot import so_status
    from app.services.chatbot.lanes.business.answer import (
        _unsuggested_line,
        not_understood_line,
        what_you_want_reply,
    )

    _set_gate(monkeypatch, True)
    texts = [
        _answer(_envelope({"SRTWC286": 12}, ["zzq123"], {})).text,
        _answer(_envelope({"SRTWC286": 12}, ["srt5764", "zzq123"], {"srt5764": SUGGEST})).text,
        _unsuggested_line(["X"]),
        not_understood_line(["pink", "xx"]),
        what_you_want_reply("taps", [], missing="pink (finish)", team="sales"),
        so_status.po_ref_reply_text(_Po(["X"]), "X"),
        so_status.reply_text(_Po(["A", "B"]), refusal=""),
        _breakdown("master_products"),
        _breakdown("inventory"),
    ]
    for t in texts:
        assert "I could not find" not in t and "Couldn't find:" not in t, t
        assert not re.search(r"master[_ ]products|\binventory\b", t), t
        assert " · " not in t
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)
