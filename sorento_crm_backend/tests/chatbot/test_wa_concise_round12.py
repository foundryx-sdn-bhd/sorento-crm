"""WA-CONCISE round 12 (red-first): defects from the live golden set. Placeholder data only.

G3 no generic opener above rows, G8 the attached line once and last (en and zh), G11 the
footer closes a two-section body, G5 the stock + incoming fold of a translated reply is one
block per code and numbered (ms and zh)."""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services.chatbot import label_catalog
from app.services.chatbot.label_catalog import Localizer
from app.services.chatbot.lanes.business import fetch
from app.services.chatbot.turn.compose import compose
from app.services.chatbot.turn.policy import Policy
from app.services.chatbot.turn.state import Focus, Profile, State
from tests.chatbot._turn_helpers import TIER_ORDER_FIXTURE, _domain_row

FOOTER = "_Updated 11/09/2026 17:26_"
ATTACHED = "I have attached the file(s) below."


def _loc(lang: str) -> Localizer:
    return Localizer(lang, label_catalog.defaults(lang))


# ---- G3 ------------------------------------------------------------------- #


def test_g3_no_generic_opener_above_rows():
    env = {"items": [{"title": "R1", "fields": [{"label": "Reference", "value": "REF-1"}]}], "has_result": True}

    out = fetch.output_structurer(env, {"semantic_input": {}})

    assert "Here are the results." not in out["response"], out["response"]
    assert out["response"].startswith("*Reference:* REF-1"), out["response"]


def test_g3_guard_zero_rows_keep_their_intro():
    env = {"intro": "No matching results found.", "items": [], "has_result": False}

    out = fetch.output_structurer(env, {"semantic_input": {}})

    assert out["response"] == "No matching results found."


# ---- compose helpers ------------------------------------------------------ #


def _compose(sections, lang: str = "en") -> str:
    """sections: (domain, lane_text, files)."""
    rows = [{**_domain_row(d, narrowing={"product": "list_all"}), "label": d} for d, _t, _f in sections]
    policy = Policy.from_rows(domains=rows, kinds=[], tier_order=TIER_ORDER_FIXTURE)
    state = State(focus=Focus(), pending=None, profile=Profile(), turn_no=2)
    loc = _loc(lang)
    ctx = SimpleNamespace(localizer=loc) if lang != "en" else None
    envs = []
    for domain, text, files in sections:
        code = re.search(r"Product Code:\*|产品代码:\*|Kod Produk:\*", text)
        envs.append(
            {
                "domain": domain, "denied": False, "entities": ["SRTX"], "product_codes": ["SRTX"],
                "figures": [{"fields": [{"label": "Product Code", "value": "SRTX"}]}] if code else [],
                "files": files, "miss": [], "has_result": True, "tool_has_result": True,
                "unresolved": [], "error": None, "lane_text": loc.reply(text) if lang != "en" else text,
            }
        )
    return compose(envs, state, policy, ctx=ctx).text


# ---- G8 ------------------------------------------------------------------- #

_PL = [{"url": "packing/PL-1.pdf", "filename": "PL-1.pdf"}]


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
def test_g8_attached_line_once_and_last(lang):
    lane = f"{ATTACHED}\n\n*Product Code:* SRTX\n*Container:* IAAU1907074"
    sentence = _loc(lang).reply(ATTACHED) if lang != "en" else ATTACHED

    text = _compose([("incoming", lane, _PL)], lang)

    assert text.count(sentence) == 1, text
    assert text.rstrip().split("\n\n")[-1] == sentence, text


# ---- G11 ------------------------------------------------------------------ #


def test_g11_footer_closes_the_body_of_a_stock_plus_incoming_reply():
    stock = f"*Product Code:* SRTA\n*BRW:* 5\n\n{FOOTER}"
    incoming = "*Product Code:* SRTX\n*Container:* IAAU1907074\n*ETA:* 2026-09-09"

    text = _compose([("inventory", stock, []), ("incoming", incoming, [])])

    assert text.count(FOOTER) == 1, text
    assert text.rstrip().endswith(FOOTER), text


# ---- G5 ------------------------------------------------------------------- #

_INC = "*Container:* IAAU1907074\n*ETA:* 2026-09-09\n*Incoming Quantity:* 49"
_XD = f"*Product Code:* SRTX\n*Stock:* 0\n{_INC}"
_CASES = {
    "one code": ("*Product Code:* SRTX\n*BRW:* 0 (O/S: 56)", _XD),
    "two codes": (
        "1. *Product Code:* SRTA\n*BRW:* 5\n\n2. *Product Code:* SRTX\n*BRW:* 0 (O/S: 56)",
        _XD,
    ),
}


@pytest.mark.parametrize("lang", ["en", "ms", "zh"])
@pytest.mark.parametrize("case", list(_CASES))
def test_g5_fold_of_a_translated_reply_matches_the_english_fold(case, lang):
    from app.services.chatbot.answer_bridge import _fold_blocks

    primary, xd = _CASES[case]
    en_kept, en_rest = _fold_blocks(primary, xd)
    english = "\n\n".join(p for p in (en_kept, en_rest) if p)
    loc = _loc(lang)
    expected = loc.reply(english) if lang != "en" else english

    kept, rest = _fold_blocks(
        loc.reply(primary) if lang != "en" else primary, loc.reply(xd) if lang != "en" else xd
    )

    assert "\n\n".join(p for p in (kept, rest) if p) == expected, (kept, rest)
