"""Owner hand test on PR #833 (28 Sep 2026 11:27 to 11:32 MYT, console :3083, contact
900000039) and the owner's rulings on the reply alignment page the same day. Fix round 9.

The owner's words (verbatim): "you do like here is what you want, but no incoming, do you
want me to escalate..., I want that structure to stay, i don't want so many route"; "need
to be more line by line, more structured"; "C: no cap, but no explanations and no
repeats"; "always break it down"; "I want us to reuse the functions that we have"; "i
don't want new layer, i want the layer to be existing one". And earlier the same day: "I
don't want too many new kind of answer format coming out just because of this reverse
asking".

So every attribute ask answers through the ONE reply the product-code ask already gives:

  * a hit is the product-code rows under one intro line naming what was asked for;
  * a miss is "Here's what you want:" with what was asked for, what matched line by
    line, what did not match, and one short escalate offer;
  * no explanation of how it searched, no repeated line, no typo mention, no silent brand
    default, and a set too long for one message gives its count and a breakdown by the
    next attribute, never a paging question.

The five turns of the test, in order, each replayed through `engine.run_turn` with the
real resolver, class vocabulary, specification registry and brands table (round 7's
harness); the parser verdict is stubbed as the published parser reads each message, and
the tools in their presenter shapes (round 8's compact stock tool).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pytest

from tests.chatbot.test_attribute_asks_round3 import _ask, _entity
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import _code_ask, _make
from tests.chatbot.test_attribute_asks_round7 import world as r7world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round8 import _Loo, _blocks, _by_code
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _certificate_for, _stock_for
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category, _incoming_for


@pytest.fixture()
def world(r7world):
    """Round 7's world (Sorento, Mocha, Cabana; white P and S trap water closets, basins,
    kitchen sinks with incoming, taps) plus what the owner's five turns name:

      - two GUNMETAL wash basins, one Sorento and one Cabana, each with a certificate and
        NO incoming (the owner's "any gunmetal basin has incoming?" found none);
      - one Sorento CHROME wash basin with incoming (the other finish the old reply
        explained);
      - two kitchen sinks 1.2 mm thick, one Sorento and one Cabana, and one Sorento 1.0 mm
        sink, all with stock.
    """
    db = r7world["db"]
    sorento, cabana = r7world["sorento"], r7world["cabana"]
    uom = r7world["srt_tubs"][0].base_uom_id
    wh = r7world["warehouse"]
    wb, ks = _class_category(db, "WB"), _class_category(db, "KS")

    def make(code, name, description, brand, category):
        return _make(db, code=code, name=name, description=description, brand=brand, category_id=category, uom_id=uom)

    gunmetal = [
        make("CBWB9GM0", "Cabana Gunmetal Basin", "CBWB9GM0 GUNMETAL WASH BASIN", cabana, wb),
        make("SRTWB9GM0", "Sorento Gunmetal Basin", "SRTWB9GM0 GUNMETAL WASH BASIN", sorento, wb),
    ]
    chrome = make("SRTWB9CR0", "Sorento Chrome Basin", "SRTWB9CR0 CHROME WASH BASIN", sorento, wb)
    thick12 = [
        make("CBKS9T12", "Cabana Sink 1.2", "CBKS9T12 KITCHEN SINK (860X500X220X1.2MM)", cabana, ks),
        make("SRTKS9T12", "Sorento Sink 1.2", "SRTKS9T12 KITCHEN SINK (860X500X220X1.2MM)", sorento, ks),
    ]
    thick10 = make("SRTKS9T10", "Sorento Sink 1.0", "SRTKS9T10 KITCHEN SINK (860X500X220X1.0MM)", sorento, ks)
    _incoming_for(db, product_id=chrome.id)
    for p in gunmetal:
        _certificate_for(db, product_id=p.id, valid_until=date.today() + timedelta(days=365))
    for p in [*thick12, thick10]:
        _stock_for(db, product_id=p.id, warehouse_id=wh.id)
    db.commit()
    return {
        **r7world,
        "gunmetal": gunmetal,
        "chrome": chrome,
        "thick12": thick12,
        "thick10": thick10,
        "every": r7world["every"] + gunmetal + [chrome] + thick12 + [thick10],
    }


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Loo(session_factory, monkeypatch, stub_parser, stub_access, world, "compact")


# --------------------------------------------------------------------------- #
# The owner's five messages, as the published parser reads them                 #
# --------------------------------------------------------------------------- #

T1 = "any gunmetal basin has incoming?"
T2 = "cert?"
T3 = "any pnk water closet?"
T4 = "product info pink water closet"
T5 = "kitchen sink 1.2mm thickness"


def _spec(raw: str, key: str, value: Any) -> dict[str, Any]:
    return {**_entity(raw, "specification"), "spec_key": key, "spec_value": value}


def _v1() -> dict[str, Any]:
    """As round 8's trace read it: the whole phrase one category token. Grounding splits
    it (Finish or colour: Gunmetal + basin)."""
    return _ask("incoming", "gunmetal basin", T1)


def _v2() -> dict[str, Any]:
    """"cert?": a domain word alone. The parser keeps nothing of the subject."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal="cert?",
        requested_attributes=["cert"],
        entity_op="reuse",
        entities=[_entity("cert", "attachment_type")],
    )


def _v3() -> dict[str, Any]:
    """"pnk": a colour word misspelt. The published parser (spk_0002) says the word it
    meant as a finish with no choice: {raw "pink", spec_key finish, spec_value null}."""
    return _code_ask(T3, _entity("water closet", "category"), _spec("pink", "finish", None))


def _v4() -> dict[str, Any]:
    return _code_ask(
        T4, _entity("water closet", "category"), _spec("pink", "finish", None), attrs=("product_info",)
    )


def _v5() -> dict[str, Any]:
    return _code_ask(T5, _entity("kitchen sink", "category"), _spec("1.2mm thickness", "thickness", 1.2))


# Reply readers ---------------------------------------------------------------- #


def _codes(text: str, world) -> set[str]:
    return {
        p.product_code
        for p in world["every"]
        if re.search(rf"(?<![\w-]){re.escape(p.product_code)}(?![\w-])", text)
    }


def _brand_names(world) -> list[str]:
    return [world[k].brand_name for k in ("sorento", "mocha", "cabana")]


_EXPLAINING = ("I looked for", "I don't know", "I know ", "Did you mean", "typo", "too many to list", "How many should I show", "Other brands")


def _plain(text: str, world) -> None:
    """No explanation, no brand the customer did not name, no repeated line."""
    for phrase in _EXPLAINING:
        assert phrase not in text, (phrase, text)
    # A row's own fields ("*List Price:* MYR 100.00") may read alike on two rows; every
    # other line is said once.
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("*")]
    assert len(lines) == len(set(lines)), text


# --------------------------------------------------------------------------- #
# The five turns                                                                 #
# --------------------------------------------------------------------------- #


def test_turn1_gunmetal_basin_incoming_is_here_is_what_you_want_but_no_incoming(chat, world):
    text = chat.say(T1, _v1())
    print(text)
    assert text == (
        "Here's what you want: gunmetal wash basins (2)\n"
        "• CBWB9GM0\n"
        "• SRTWB9GM0\n"
        "\n"
        "But no incoming matched these. Would you like me to escalate to purchasing team?"
    ), text


def test_turn2_cert_keeps_the_gunmetal_basins_of_the_miss(chat, world):
    chat.say(T1, _v1())
    text = chat.say(T2, _v2())
    print(text)
    assert _codes(text, world) == {p.product_code for p in world["gunmetal"]}, text
    assert text.startswith("Here's what you want: gunmetal wash basins with certificates (2)\n\n1. *Product Code:* CBWB9GM0"), text
    for code, block in _blocks(text).items():
        assert block == _blocks(_by_code(chat, code, "cert"))[code], code
    _plain(text, world)


@pytest.mark.parametrize("turn", ["pnk", "product info"])
def test_turns3_and_4_pink_water_closet_breaks_the_water_closets_down_by_finish(chat, world, turn):
    message, verdict = (T3, _v3()) if turn == "pnk" else (T4, _v4())
    text = chat.say(message, verdict)
    print(text)
    lines = text.split("\n")
    # Amended to fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"):
    # the word is said as the customer typed it. The parser still puts "pnk" right to
    # "pink" (`_v3`, spk_0002); grounding reads the customer's own word back
    # (`grounding._as_typed`). Turn 4 typed "pink" and is unchanged.
    said, other = ("pnk", "pink") if turn == "pnk" else ("pink", "pnk")
    assert lines[0] == f"Here's what you want: {said} water closets", text
    assert any(re.fullmatch(r"• White water closets: \d+", line) for line in lines), text
    assert f"Couldn't find: {said} (finish or colour). Would you like me to escalate to" in text, text
    assert other not in text, text
    _plain(text, world)


def test_turn5_kitchen_sink_1_2mm_thickness_lists_every_brand(chat, world):
    text = chat.say(T5, _v5())
    print(text)
    assert _codes(text, world) == {p.product_code for p in world["thick12"]}, text
    _plain(text, world)
    for name in _brand_names(world):
        assert name not in text.split("\n")[0], text


# --------------------------------------------------------------------------- #
# The rulings the five turns stand for, pinned on their own                       #
# --------------------------------------------------------------------------- #


def test_too_many_is_the_full_count_and_a_breakdown_by_brand_never_a_paging_question(chat, world, monkeypatch):
    """No brand named means every brand; a set longer than one reply gives its count and
    the next attribute's breakdown (the brand, since none was named), one line each."""
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 2)
    message = "any water closet has stock"
    text = chat.say(message, _ask("stock", "water closet", message))
    print(text)
    lines = text.split("\n")
    total = int(re.match(r"Here's what you want: water closets with stock \((\d+)\)$", lines[0]).group(1))
    rows = [re.fullmatch(r"• (\S+) water closets: (\d+)", line) for line in lines[1:]]
    assert rows and all(rows), text
    assert sum(int(m.group(2)) for m in rows) == total, text
    assert {m.group(1) for m in rows} >= {world["sorento"].brand_name, world["cabana"].brand_name}, text
    assert "*Product Code:*" not in text, text
    _plain(text, world)


def test_a_category_word_never_opens_the_which_kind_of_file_menu(chat, world):
    text = chat.say(
        "basin",
        _code_ask("basin", _entity("basin", "category"), intent_hint="check_product_attachment", domain_hint="product_attachment"),
    )
    print(text)
    assert "Which kind of file" not in text, text
    assert "*Product Code:*" in text, text


def test_a_document_word_still_asks_about_documents(chat, world):
    """The guard is the category word ALONE: "any basin has cert" stays a certificate ask."""
    message = "any gunmetal basin has cert"
    text = chat.say(message, _ask("cert", "gunmetal basin", message))
    assert text.startswith("Here's what you want: gunmetal wash basins with certificates (2)"), text


def test_the_five_turns_in_order(chat, world):
    """The owner's test as typed, one conversation: each reply is the one structure."""
    replies = [chat.say(T1, _v1()), chat.say(T2, _v2()), chat.say(T3, _v3()), chat.say(T4, _v4()), chat.say(T5, _v5())]
    for text in replies:
        print(text, end="\n\n=====\n\n")
        _plain(text, world)
    assert replies[0].startswith("Here's what you want: gunmetal wash basins (2)\n"), replies[0]
    assert replies[0].endswith("But no incoming matched these. Would you like me to escalate to purchasing team?"), replies[0]
    assert replies[1].startswith("Here's what you want: gunmetal wash basins with certificates (2)"), replies[1]
    # Amended to fix round 10 on PR #833: turn 3 says "pnk" as typed.
    assert replies[2].startswith("Here's what you want: pnk water closets\n• White water closets: "), replies[2]
    assert replies[3].startswith("Here's what you want: pink water closets\n• White water closets: "), replies[3]
    assert replies[4].startswith("Here's what you want: kitchen sinks with thickness 1.2 mm (2)"), replies[4]
    assert _codes(replies[4], world) == {p.product_code for p in world["thick12"]}, replies[4]
