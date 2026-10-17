"""CHAT-COMPOSER S1 change (a): one dealer miss wording, plus AC-1.8 (full answered
block in a rebuilt stock mix). AC-1.3, AC-1.8, AC-1.7.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.turn import compose as turn_compose
from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import verdict

TITLE = "CB890SS-BL x 10: ❌ No stock and no incoming. Please refer to your salesman."
MARK = "⚠️  *(PRODUCT DISCONTINUED)*"
ALT = "Alternative: CB890SS-BL-DIY x 10: ❌ No stock, ETA 21/10/2026."
BLOCK = f"{TITLE}\n{MARK}\n{ALT}"


class _Spec:
    domain = "inventory"
    entities = [{"uuid": "x"}]
    filters: dict = {}


class _Plan:
    fetch = [_Spec()]


def _set_gate(monkeypatch, on):
    """Gate on needs the new Settings field (red until S0). Gate off is the default, so a
    Settings without the field is already off and the call is a no-op."""
    if on:
        monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)
    else:
        if hasattr(settings, "chat_composer_enabled"):
            monkeypatch.setattr(settings, "chat_composer_enabled", False, raising=False)


def _on(monkeypatch):
    _set_gate(monkeypatch, True)


def _off(monkeypatch):
    _set_gate(monkeypatch, False)


def _item(*, discontinued=False, alternative=False, title=TITLE):
    flags = {"branch": "no_stock"}
    if discontinued:
        flags["discontinued"] = True
    if alternative:
        flags["alternative_lines"] = ["CB890SS-BL-DIY x 10: ❌ No stock, ETA 21/10/2026."]
    return {"title": title, "flags": flags}


def _envelopes(item, *, unresolved=None, extra_rows=()):
    env = {
        "stock_availability": [
            ht.row("CB890SS-BL", needs_quantity=False, requested_qty=10, branch="no_stock"),
            *extra_rows,
        ],
        "items": [item],
    }
    if unresolved:
        env["unresolved"] = unresolved
    return [env]


def _run(envelopes, asked):
    from app.services.chatbot.engine import _stock_ask_reply

    return _stock_ask_reply(
        turn_compose.Answer(text="How many units do you need?"),
        ht.state(availability_only=True),
        envelopes,
        _Plan(),
        verdict(domain_hint="inventory", entities=asked),
        turn_no=1,
    )


MIX_ASKED = [ht.asked("CB890SS-BL", 10)]
MISSES = ["cb890-bl", "cb890-GM- diy"]


def test_stock_mix_miss_gate_on(monkeypatch):
    _on(monkeypatch)
    out = _run(_envelopes(_item(), unresolved=MISSES), MIX_ASKED)
    assert out.text.endswith("Couldn't find cb890-bl, cb890-GM- diy."), out.text
    assert "Couldn't find:" not in out.text


def test_stock_mix_miss_gate_off_unchanged(monkeypatch):
    _off(monkeypatch)
    out = _run(_envelopes(_item(), unresolved=MISSES), MIX_ASKED)
    assert out.text == f"{TITLE}\n\nCouldn't find: cb890-bl, cb890-GM- diy."


def test_eta_miss_gate_on_refer_stays_last(monkeypatch):
    from app.services.chatbot.engine import _dealer_names_the_miss

    _on(monkeypatch)
    ans = turn_compose.Answer(
        text="SRTW2000: ✅ ETA 19/10/2026\n\nPlease refer to your salesman.\n\nI could not find FOO99."
    )
    out = _dealer_names_the_miss(ans, [{"unresolved": ["FOO99"]}])
    assert out.text == "SRTW2000: ✅ ETA 19/10/2026\n\nCouldn't find FOO99.\n\nPlease refer to your salesman."


def test_eta_miss_gate_off_unchanged(monkeypatch):
    from app.services.chatbot.engine import _dealer_names_the_miss

    _off(monkeypatch)
    ans = turn_compose.Answer(
        text="SRTW2000: ✅ ETA 19/10/2026\n\nPlease refer to your salesman.\n\nI could not find FOO99."
    )
    out = _dealer_names_the_miss(ans, [{"unresolved": ["FOO99"]}])
    assert out.text == "SRTW2000: ✅ ETA 19/10/2026\n\nCouldn't find: FOO99.\n\nPlease refer to your salesman."


def test_miss_line_gate_states(monkeypatch):
    from app.services.chatbot.composers import stock

    _off(monkeypatch)
    assert stock.miss_line(["X", "Y"]) == "Couldn't find: X, Y."
    _on(monkeypatch)
    assert stock.miss_line(["X", "Y"]) == "Couldn't find X, Y."
    assert stock.miss_line(["X"]) == "Couldn't find X."


def test_none_for():
    from app.services.chatbot.composers import stock

    assert stock.none_for("stock", ["X", "Y"]) == "No stock for X, Y."
    assert stock.none_for("incoming", ["X"]) == "No incoming for X."


def test_miss_line_translates_ms_zh(monkeypatch):
    from app.services.chatbot.composers import stock

    _on(monkeypatch)
    line = stock.miss_line(["X"])
    assert Localizer("ms", defaults("ms")).reply(line) == "Tidak dapat menemui X."
    assert Localizer("zh", defaults("zh")).reply(line) == "找不到 X。"


@pytest.mark.parametrize("gate", [False, True])
def test_dealer_did_you_mean_unchanged_gate_on(monkeypatch, gate):
    from app.services.chatbot import dealer_stock

    _set_gate(monkeypatch, gate)
    cands = [
        {"product": "ELP3754", "uuid": ht.uuid_of("ELP3754")},
        {"product": "ELP3755", "uuid": ht.uuid_of("ELP3755")},
    ]
    text, _pick = dealer_stock.did_you_mean("elp3753", cands)
    assert text == "Couldn't find ELP3753. Did you mean:\n1. ELP3754\n2. ELP3755"
    single, _ = dealer_stock.did_you_mean("elp3753", cands[:1])
    assert single == "Couldn't find ELP3753. Did you mean ELP3754?"


# --------------------------------------------------------------------------- #
# AC-1.8 (crew OI-1 (a)): the rebuilt mix keeps each answered product's full block
# --------------------------------------------------------------------------- #


def test_dealer_block_is_title_marker_alternative():
    from app.services.chatbot.composers import stock

    assert stock.dealer_block(_item(discontinued=True, alternative=True)) == BLOCK
    assert stock.dealer_block(_item()) == TITLE
    assert stock.dealer_block(_item(discontinued=True)) == f"{TITLE}\n{MARK}"
    two = _item(alternative=True)
    two["flags"]["alternative_lines"].append("CB890SS-BL-UF x 10: ✅ Got stock.")
    assert stock.dealer_block(two).endswith(f"\n{ALT}\nAlternative: CB890SS-BL-UF x 10: ✅ Got stock.")


def test_dealer_block_equals_item_line_of_the_main_path():
    from app.services.chatbot.composers import stock
    from app.services.chatbot.lanes.business.fetch import output_structurer

    item = _item(discontinued=True, alternative=True)
    env = {
        "items": [item],
        "answers": [{"text": "x"}],
        "has_result": True,
        "stock_availability": [{"product_code": "CB890SS-BL"}],
    }
    text = output_structurer(env, {"tool": "crm_inventory_stock_balance_list", "semantic_input": {}})["response"]
    assert text == f"Here are the results.\n\n1. {BLOCK}"
    assert stock.dealer_block(item) == text.split("\n\n1. ", 1)[1]


def test_rebuilt_mix_with_miss_keeps_marker_and_alternative_gate_on(monkeypatch):
    _on(monkeypatch)
    out = _run(_envelopes(_item(discontinued=True, alternative=True), unresolved=["X1"]), MIX_ASKED)
    assert out.text == f"{BLOCK}\n\nCouldn't find X1."


def test_rebuilt_mix_with_miss_drops_them_gate_off(monkeypatch):
    _off(monkeypatch)
    out = _run(_envelopes(_item(discontinued=True, alternative=True), unresolved=["X1"]), MIX_ASKED)
    assert out.text == f"{TITLE}\n\nCouldn't find: X1."


QUESTION_ASKED = [ht.asked("CB890SS-BL", 10), ht.asked("ELP3754")]


def test_rebuilt_mix_with_question_keeps_marker_and_alternative_gate_on(monkeypatch):
    _on(monkeypatch)
    env = _envelopes(_item(discontinued=True, alternative=True), extra_rows=[ht.row("ELP3754")])
    out = _run(env, QUESTION_ASKED)
    assert out.text == f"{BLOCK}\n\nHow many units of ELP3754?"


def test_rebuilt_mix_with_question_drops_them_gate_off(monkeypatch):
    _off(monkeypatch)
    env = _envelopes(_item(discontinued=True, alternative=True), extra_rows=[ht.row("ELP3754")])
    out = _run(env, QUESTION_ASKED)
    assert out.text == f"{TITLE}\n\nHow many units of ELP3754?"
