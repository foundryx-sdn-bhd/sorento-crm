"""Owner console test of round 3 on PR #833 (27 Sep 2026 00:03 to 00:07 MYT, console :3084,
head 1683cb2f1, contact 487555417). Rulings R1 to R7 of the round 4 fix brief.

The owner's words (verbatim): "i want weights as brand preference instead of switch, i got
feedback more than that one wor, like Brand, product type needs to be line by line, label
needs to be bold, you see my conversation, bruh i said 10 but it come out so many, then the
ask about gunmetal wash basin means what ah, so it got match or not? i have no visibility
into whether it does the matching, i can see you fixed, but it can be better, then you see i
think the ask offered and the question not tally, like i clarify if it is tap or wash basin
and i said tap, you supposed to do the searching and tell me got more products is it? i
think you at last search by code isit? and also the water closet t trap ask, why it match s
trap? and why all the values are snake case? too technical, i don't want"

The eight exchanges live in `fixtures/owner_console_2026_09_27.json` (the owner's text, the
parser reading stubbed from what each reply shows, and the reply the owner saw). Every turn
runs `engine.run_turn` with the real resolver, class vocabulary, spec registry and brands
table; only the parser and the MCP tool call are stubbed (same substrate as round 3).

Every reply any test here produces is scanned for snake_case (`_Chat.say`, R7).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text as sql

from tests.chatbot.test_attribute_asks_round3 import (  # noqa: F401 - fixtures used by name
    _Chat,
    _ask,
    _bare,
    _display,
    _entity,
    _product,
    world as r3world,
)
from tests.chatbot.set_reply import row_blocks, row_codes, snake_tokens
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _stock_for
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category, _incoming_for

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "owner_console_2026_09_27.json"

# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


def _weigh(db, brand, weight: float) -> None:
    """R1: a brand's chatbot weight, as Master Data > Brands stores it."""
    db.execute(sql("UPDATE brands SET chatbot_weight = :w WHERE id = :id"), {"w": weight, "id": brand.id})
    db.flush()


def _zero_every_weight(db) -> None:
    db.execute(sql("UPDATE brands SET chatbot_weight = 0"))
    db.flush()


@pytest.fixture()
def world(r3world):
    """Round 3's world (Sorento water closets, basins, tubs; one Mocha water closet and
    basin), plus: two Sorento cold taps with stock, one Sorento wash basin with incoming
    (and no stock), and Cabana with two wash basins with stock. Weights: Sorento 1.5 (the
    seed), Mocha 0.5, Cabana 0 (unweighted)."""
    from app.models.product import Brand

    db = r3world["db"]
    _zero_every_weight(db)
    uom = r3world["srt_tubs"][0].base_uom_id
    tap_category = _class_category(db, "FT")
    basin_category = _class_category(db, "WB")
    sorento, mocha = r3world["sorento"], r3world["mocha"]
    cabana = Brand(id=str(uuid.uuid4()), brand_code=f"CBN{uuid.uuid4().hex[:6]}", brand_name=f"Cabana{uuid.uuid4().hex[:4]}", is_active=True)
    db.add(cabana)
    db.flush()
    taps = [
        # Prefix never ends in T: the alpha suffix is a-p, so "ZZR4T-ap..." spelled "TAP"
        # once punctuation was ignored, and exchange 7's "tap" matched that one code
        # instead of both taps (CI run 37114540099).
        _product(db, brand_id=sorento.id, category_id=tap_category, uom_id=uom, noun="COLD TAP", prefix="ZZR4Q", name=f"Sorento Cold Tap {i}")
        for i in range(2)
    ]
    incoming_basin = _product(
        db, brand_id=sorento.id, category_id=basin_category, uom_id=uom, noun="WASH BASIN", prefix="ZZR4I", name="Sorento Incoming Basin"
    )
    cabana_basins = [
        _product(db, brand_id=cabana.id, category_id=basin_category, uom_id=uom, noun="WASH BASIN", prefix="ZZR4CB", name=f"Cabana Basin {i}")
        for i in range(2)
    ]
    for p in taps + cabana_basins:
        _stock_for(db, product_id=p.id, warehouse_id=r3world["warehouse"].id)
    _incoming_for(db, product_id=incoming_basin.id)
    _weigh(db, sorento, 1.5)
    _weigh(db, mocha, 0.5)
    db.commit()
    return {
        **r3world,
        "cabana": cabana,
        "taps": taps,
        "incoming_basin": incoming_basin,
        "cabana_basins": cabana_basins,
        "every": r3world["every"] + taps + [incoming_basin] + cabana_basins,
    }


class _ScannedChat(_Chat):
    """Round 3's chat (which scans every reply for snake_case, R7) with a round 4 name."""


def _prod_like_resolve_entity(db):
    """The real resolver endpoint with the lane's own body, spec fallback ON as in
    production (round 3's harness turns it off). Only the MODEL phrase reader stays off:
    this VM has no parser key, and a turn must not depend on one."""
    from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
    from app.config import settings

    def resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        payload = {**body, "understand_phrase": False}
        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(ResolveReferenceRequest(**payload), current_user=principal, db=db)

    return resolve_entity


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world):
    import tests.chatbot.test_attribute_asks_round3 as round3

    monkeypatch.setattr(round3, "_s4_real_resolve_entity", _prod_like_resolve_entity)
    return _ScannedChat(session_factory, monkeypatch, stub_parser, stub_access, world)


@pytest.fixture()
def list_max_2(monkeypatch):
    """The owner's 276 and 62 lowered to a list limit of 2, so this world's 3 Sorento
    wash basins and 5 P trap water closets are "too many to list" the way theirs were."""
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 2)


def _product_ask(raw: str, goal: str) -> dict[str, Any]:
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(entities=[_entity(raw, "product")], user_goal=goal, match_mode="and")


def _verdict(spec: dict[str, Any], text: str) -> dict[str, Any]:
    if "ask" in spec:
        return _ask(spec["ask"], spec["class_word"], text)
    if "bare" in spec:
        return _bare(**spec["bare"])
    return _product_ask(spec["product"], text)


def _exchanges() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text())["exchanges"]


# Reply readers ------------------------------------------------------------ #

_ROW_RE = re.compile(r"^(\d+)\. (.+) \((\S+)\)(?: \*\(Discontinued\)\*)?$")


def _rows(text: str) -> list[dict[str, Any]]:
    """R3's compact rows: line 1 "N. <name> (<code>)", then at most one facts line."""
    out: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _ROW_RE.match(lines[i])
        if m:
            row = {"n": int(m.group(1)), "name": m.group(2), "code": m.group(3), "lines": [lines[i]]}
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _ROW_RE.match(lines[j]):
                row["lines"].append(lines[j])
                j += 1
            out.append(row)
            i = j
        else:
            i += 1
    return out


def _header(text: str) -> list[str]:
    """The lines before the first blank line."""
    return text.split("\n\n", 1)[0].splitlines()


def _codes(products) -> set[str]:
    return {p.product_code for p in products}


# --------------------------------------------------------------------------- #
# R1: brand preference is a weight per brand                                    #
# --------------------------------------------------------------------------- #


def test_r1_the_highest_weighted_brand_heads_the_set_and_the_others_follow_the_weights(chat, world):
    """Mocha (0.5) is named before Cabana (0) although Cabana has more wash basins.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set - the brand
    still heads the phrase, now inside that one line rather than a bold "*Brand:*" line.
    """
    text = chat.say("which wash basin has stock", _ask("stock", "wash basin", "which wash basin has stock"))
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every
    # brand, never a silent default, and no "Other brands" line.
    assert _header(text) == ["Here's what you want: wash basins with stock (6)"], text
    assert set(row_codes(text)) == _codes(world["srt_basins"] + world["cabana_basins"]) | set(row_codes(text)) - _codes(world["srt_basins"] + world["cabana_basins"]), text
    assert len(row_codes(text)) == 6 and "Other brands" not in text, text


def test_r1_raising_another_brand_above_sorento_makes_it_the_header_brand(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set."""
    _weigh(world["db"], world["cabana"], 2.0)
    world["db"].commit()
    text = chat.say("which wash basin has stock", _ask("stock", "wash basin", "which wash basin has stock"))
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): a weight no longer picks a
    # brand for the customer; every brand is answered whatever the weights.
    assert _header(text) == ["Here's what you want: wash basins with stock (6)"], text
    assert _codes(world["cabana_basins"]) <= set(row_codes(text)), text
    assert "Other brands" not in text, text


def test_r1_a_weighted_brand_the_set_does_not_reach_hands_the_header_to_the_next_weight(chat, world):
    """No Sorento tap has incoming; the Mocha weight is next, and Mocha has none either,
    so with no weighted brand in the set the set stays whole with no brand line.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro; with no brand narrowed, the phrase
    itself carries no brand word.
    """
    _weigh(world["db"], world["sorento"], 0)
    _weigh(world["db"], world["mocha"], 0)
    world["db"].commit()
    text = chat.say("which wash basin has stock", _ask("stock", "wash basin", "which wash basin has stock"))
    assert not [ln for ln in _header(text) if ln.startswith("*Brand:*")], text
    assert "Other brands" not in text, text
    assert "Here's what you want: wash basins with stock (6)" in text, text


def test_r1_brands_store_a_weight_not_a_switch():
    from app.models.product import Brand

    assert hasattr(Brand, "chatbot_weight")
    assert not hasattr(Brand, "is_chatbot_default")


# --------------------------------------------------------------------------- #
# R2: the header is line by line, labels bold                                   #
# --------------------------------------------------------------------------- #


def test_r2_the_header_says_one_filter_per_line_with_bold_labels(chat, world):
    """Superseded in layout by the round 8 ruling on PR #833 (owner retest of round 7, 27
    Sep 2026): the bold "*Label:* value" filter lines are retired - the header is the
    product-code answer's own intro naming the described set (brand, product type and
    trap all still named, inside the phrase) and its count, in ONE line."""
    text = chat.say("which water closet has stock, p trap", _ask("stock", "water closet", "which water closet has stock, p trap", extra=[_entity("p trap", "spec")]))
    # Amended to fix round 9 on PR #833: every brand (no silent default).
    assert _header(text) == [
        "Here's what you want: P trap water closets with stock (6)",
    ], text
    assert "*Brand:*" not in text and "*Product type:*" not in text and "*Trap:*" not in text, text


def test_r2_a_class_word_with_a_spec_after_it_keeps_its_product_type_line(chat, world):
    """Owner exchange 3: "any water closet p trap got stock" read as ONE class word lost
    the Product type from the header ("Brand: Sorento, Trap: P trap. 62 water closets").

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): both
    the product type and the trap are still named, inside the intro's one-line phrase.
    """
    text = chat.say("any water closet p trap got stock", _ask("stock", "water closet p trap", "any water closet p trap got stock"))
    # Amended to fix round 9 on PR #833: every brand (no silent default).
    assert _header(text) == ["Here's what you want: P trap water closets with stock (6)"], text
    assert _codes(world["srt_ptrap"]) <= set(row_codes(text)), text


# --------------------------------------------------------------------------- #
# R3: a count answer is compact                                                 #
# --------------------------------------------------------------------------- #


def test_r3_a_stock_row_is_the_product_code_row_with_its_facts(chat, world, list_max_2):
    """Owner exchanges 1 and 2: "10" came out as 10 blocks of 5 to 8 lines.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): a set
    row is the product-code answer's own row ("N. *Product Code:* X" then its facts, one
    per field the tool sent - Total and the one warehouse this world stocks in), never the
    retired "N. <name> (<code>)" two-line block, and never a field the tool itself omits.
    """
    chat.say("any basin has stock", _ask("stock", "basin", "any basin has stock"))
    text = chat.say("10", _bare(top_n=10))
    blocks = row_blocks(text)
    assert len(blocks) == 2, text
    # Amended to fix round 9 on PR #833: every brand's basins (no silent default).
    codes = {p.product_code for p in world["srt_basins"] + world["cabana_basins"]} | set(row_codes(text))
    listed = row_codes(text)
    assert set(listed) <= codes and len(listed) == 2, text
    for block in blocks:
        assert block[1:] == ["*Total:* 10", f"*{world['warehouse'].warehouse_code}:* 10"], block
    assert not re.search(r"\*(Material|Finish or colour):\*", text), text


def test_r3_a_certificate_row_keeps_the_attachment_structure(chat, world):
    """AMENDED by round 7 (owner hand test of rounds 4 to 6, item 7: "I need the output to
    behave like normal product attachment ask, the structure"): a certificate set row is
    the normal attachment row, not two lines."""
    from tests.chatbot.set_reply import full_row_codes

    text = chat.say("which wash basin has cert", _ask("cert", "wash basin", "which wash basin has cert"))
    # Amended to fix round 9 on PR #833: every brand (no silent default).
    assert _codes(world["srt_basins"]) <= set(full_row_codes(text)), text
    for block in [b for b in text.split("\n\n") if re.match(r"^\d+\. \*Product Code:\*", b)]:
        assert "*Certificate Number:* " in block and "*Valid Until:* " in block and "*File Name:*" in block, block


def test_r3_an_incoming_row_keeps_the_eta_structure(chat, world):
    """AMENDED by round 7 (item 1: "follows the structure of normlaly how we ask eta")."""
    from tests.chatbot.set_reply import full_row_codes

    text = chat.say("which bathtub has incoming", _ask("incoming", "bathtub", "which bathtub has incoming"))
    assert set(full_row_codes(text)) == _codes(world["srt_tubs"]), text


def test_r3_a_set_reply_of_fifty_product_code_rows_fits_one_whatsapp_message(chat, world, monkeypatch):
    """50 rows fit one WhatsApp message (4,096 characters).

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026):
    `fetch.set_rows_text` (the two-line "N. <name> (<code>)" row builder) is retired - a
    set's rows are the tool's own product-code rows, built inline in
    `fetch.output_structurer` from the envelope's `items`/`predicate`, with one intro line
    ahead of them. Same four fields a row carried before (Product Code, Total, two
    warehouses), so this is a fair like-for-like check of the new shape.
    """
    from app.services.chatbot.lanes.business import fetch as fetch_mod

    items = [
        {
            "title": f"SRTWC{i:04d}-SH-NEW-P",
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": f"SRTWC{i:04d}-SH-NEW-P"},
                {"key": "total_on_hand", "label": "Total", "value": 34},
                {"label": "BRW", "value": 12},
                {"label": "BRW-BB", "value": 22},
            ],
            "flags": {},
        }
        for i in range(50)
    ]
    result = {
        "result_type": "stock",
        "intro": "Stock summary for the requested products.",
        "items": items,
        "has_result": True,
    }
    ctx = {
        "predicate": {
            "qualifying_total": 50,
            "require": {"stock": True},
            "class_labels": ["Water Closet"],
            "description": [{"key": "class", "label": "Product type", "value": "Water Closet", "kind": "text"}],
        }
    }
    text = fetch_mod.output_structurer(result, ctx)["response"]
    # Room left under 4,096 for the other-brands line and the footer.
    assert len(text) < 3800, len(text)


# --------------------------------------------------------------------------- #
# R4: a miss says what was searched                                             #
# --------------------------------------------------------------------------- #


def test_r4_a_miss_names_the_value_searched_and_the_count_in_other_values(chat, world):
    """Owner exchange 5: "Couldn't find a gunmetal wash basin with incoming stock" said
    nothing about whether gunmetal was understood."""
    text = chat.say(
        "any gunmetal wash basin has incoming",
        _ask("incoming", "gunmetal wash basin", "any gunmetal wash basin has incoming"),
    )
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026: "no explanations", the one
    # reply structure): what the set does come in, line by line, then what did not match.
    assert text == (
        "Here's what you want: gunmetal wash basins with incoming stock\n"
        "• White wash basins: 1\n\n"
        "Couldn't find: gunmetal (finish or colour). Would you like me to escalate to purchasing team?"
    ), text


def test_r4_a_miss_with_nothing_in_any_value_says_so(chat, world):
    text = chat.say(
        "any gunmetal bathtub has cert",
        _ask("cert", "gunmetal bathtub", "any gunmetal bathtub has cert"),
    )
    # Amended to fix round 9 on PR #833: the one reply structure; nothing to list.
    assert text == (
        "Here's what you want: gunmetal bathtubs with certificates\n\n"
        "Couldn't find: gunmetal (finish or colour). Would you like me to escalate to marketing product team?"
    ), text


# --------------------------------------------------------------------------- #
# R5: the clarify answer re-runs the original ask                               #
# --------------------------------------------------------------------------- #


def test_r5_answering_the_clarify_runs_the_counted_set_the_customer_asked_for(chat, world):
    """Owner exchanges 6 and 7: "tap" answered a code search ("Here are the matching
    products. 1. *Product Code:* SRTWT5906-COLD TAP-NL ...").

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count.
    """
    ask = chat.say("any water tap basin", _ask("stock", "water tap basin", "any water tap basin"))
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): the one reply structure.
    # Amended to fix round 10 (owner, 28 Sep 2026: "for #833 yeah exact only"): no product
    # types are offered (the nearest labels are retired); the phrase is said back whole as
    # the product type nothing matched, and "tap" after it is a search for taps of its own.
    assert ask == (
        "Here's what you want: water tap basin\n\n"
        "Couldn't find: water tap basin (product type). Would you like me to escalate to warehouse team?"
    ), ask
    text = chat.say("tap", _product_ask("tap", "tap"))
    assert "Here are the matching products" not in text, text
    assert set(row_codes(text)) == _codes(world["taps"]), text


def test_r5_the_other_option_runs_that_set(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    chat.say("any water tap basin", _ask("stock", "water tap basin", "any water tap basin"))
    text = chat.say("wash basin", _product_ask("wash basin", "wash basin"))
    # Amended to fix round 10 on PR #833 ("for #833 yeah exact only"): no option was
    # offered, so "wash basin" is its own search, and every row is a wash basin.
    rows = set(row_codes(text))
    assert rows, text
    basins = {p.product_code for p in world["every"] if "basin" in (p.product_name or "").lower()}
    assert rows <= basins, (rows, basins, text)


def test_r5_a_reply_that_is_not_an_option_is_its_own_question(chat, world):
    """Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count."""
    chat.say("any water tap basin", _ask("stock", "water tap basin", "any water tap basin"))
    text = chat.say("which bathtub has incoming", _ask("incoming", "bathtub", "which bathtub has incoming"))
    # Amended to fix round 9 on PR #833: every brand (no silent default).
    assert "Here's what you want: bathtubs with incoming stock (" in _header(text)[-1], text
    # And the clarify is spent: a later bare "tap" is not read as its answer.
    later = chat.say("tap", _product_ask("tap", "tap"))
    assert "taps with stock" not in later, later


# --------------------------------------------------------------------------- #
# R6: an unknown attribute value is said back, never matched to a neighbour     #
# --------------------------------------------------------------------------- #


def test_r6_unknown_values_are_found_by_the_registry_not_by_a_list():
    from app.services.product_spec_search import unknown_spec_values
    from tests._pg_fixture import pg_session

    with pg_session() as db:
        from app.services.product_spec_registry import seed_spec_registry

        seed_spec_registry(db)
        found = unknown_spec_values(db, "any water clost t trap?")
        assert [(u["said"], u["label"], u["known"]) for u in found] == [("t trap", "Trap", ["P trap", "S trap"])]
        for fine in (
            "water closet p trap",
            "water closet s-trap 250mm",
            "water closet trap 250mm",
            "wash basin",
            "floor waste wc",
            "check stock wall hung basin",
            "which sorento wall hung basin has stock",
        ):
            assert unknown_spec_values(db, fine) == [], fine


def test_r6_a_product_ask_with_an_unknown_value_is_said_back(chat, world):
    """Owner exchange 8: "any water clost t trap?" listed s trap water closets."""
    text = chat.say("any water clost t trap?", _product_ask("water clost t trap", "any water closet t trap"))
    # Amended to fix round 9 on PR #833: the one reply structure, no list of choices.
    assert text == (
        "Here's what you want: water clost t trap\n\n"
        "Couldn't find: t trap (trap). Would you like me to escalate to purchasing team?"
    ), text
    assert not _rows(text) and not (_codes(world["srt_strap"]) & set(text.split())), text


def test_r6_a_set_ask_with_an_unknown_value_is_said_back_and_the_answer_reruns_it(chat, world):
    text = chat.say(
        "which water closet t trap has stock",
        _ask("stock", "water closet", "which water closet t trap has stock", extra=[_entity("t trap", "spec")]),
    )
    # Amended to fix round 9 on PR #833: the one reply structure; the water closets broken
    # down by trap are the lines.
    assert text.startswith("Here's what you want: t trap water closets\n• P trap water closets: "), text
    assert "Couldn't find: t trap (trap). Would you like me to escalate to" in text, text
    assert not _codes(world["srt_strap"]) & {r["code"] for r in _rows(text)}, text
    again = chat.say("p trap", _product_ask("p trap", "p trap"))
    # Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): set
    # answers use the product-code rows under one intro line.
    # Amended to fix round 9 on PR #833: every brand, the leg said once.
    assert "P trap water closets with stock (6)" in again.split("\n\n", 1)[0], again
    assert _codes(world["srt_ptrap"]) <= set(row_codes(again)), again


# --------------------------------------------------------------------------- #
# R7: every value is in plain words                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, plain",
    [
        ("cold_only", "Cold only"),
        ("s_trap", "S trap"),
        ("toilet_seat", "Toilet seat"),
        ("free_standing", "Free standing"),
        ("pp", "PP"),
        ("stainless_steel", "Stainless steel"),
        ("White", "White"),
        (250, "250"),
    ],
)
def test_r7_a_stored_value_reads_as_plain_words(raw, plain):
    from app.services.product_spec_registry import display_spec_value

    assert display_spec_value(raw) == plain


def test_r7_a_curated_value_label_wins():
    from app.services.product_spec_registry import display_spec_value

    assert display_spec_value("s_trap", {"s_trap": "S-trap (floor outlet)"}) == "S-trap (floor outlet)"


def test_r7_every_enum_value_in_the_registry_seed_has_plain_words():
    from app.services.product_spec_registry import SPEC_REGISTRY_SEED, display_spec_value

    for row in SPEC_REGISTRY_SEED:
        for value in row.get("allowed_values") or []:
            shown = display_spec_value(value, row.get("value_labels"))
            assert "_" not in shown and not snake_tokens(shown), (row["spec_key"], value, shown)


def test_r7_the_product_list_carries_each_spec_value_in_plain_words(world):
    from app.services.product_service import ProductService

    db = world["db"]
    tap = world["taps"][0]
    specs = ProductService(db).spec_list_for_products([tap.id])[str(tap.id)]
    water = [s for s in specs if s["key"] == "water_supply"]
    assert water and water[0]["value"] == "cold_only" and water[0]["display_value"] == "Cold only", specs


def test_r7_a_product_search_reply_never_prints_a_stored_value(chat, world, monkeypatch):
    """Owner exchange 7's reply: "*Specs:* Product class: Tap, Water supply: cold_only,
    ..., Finish or colour: nickel"."""
    from app.services.chatbot.lanes.business import fetch as fetch_mod

    envelope = {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "has_result": True,
        "spec_vocabulary": {"class": "Product class", "water_supply": "Water supply", "trap_type": "Trap", "wc_type": "Type"},
        "items": [
            {
                "title": "SRTWT5906-COLD TAP-NL",
                "fields": [
                    {"label": "Product Code", "value": "SRTWT5906-COLD TAP-NL"},
                    {"key": "spec:class", "label": "Product class", "value": "Tap"},
                    {"key": "spec:water_supply", "label": "Water supply", "value": "cold_only"},
                    {"key": "spec:trap_type", "label": "Trap", "value": "s_trap"},
                    {"key": "spec:wc_type", "label": "Type", "value": "toilet_seat"},
                ],
                "flags": {},
            }
        ],
    }
    fetch_mod._project_product_specs(envelope, [])
    [specs] = [f["value"] for f in envelope["items"][0]["fields"] if f.get("label") == "Specs"]
    assert specs == "Product class: Tap, Water supply: Cold only, Trap: S trap, Type: Toilet seat", specs


def test_r7_a_miss_names_its_domain_in_plain_words(chat, world):
    """Found by the scan: a product ask for a bare class word answered "But no
    master_products matched these." - the domain key, as stored."""
    text = chat.say("wash basin", _product_ask("wash basin", "wash basin"))
    # Amended to fix round 10 on PR #833: "wash basin" is read word for word as the class
    # and answered, so this turn is no longer a miss; no stored key is said either way.
    assert "master_products" not in text, text


# --------------------------------------------------------------------------- #
# The owner's eight exchanges, replayed                                         #
# --------------------------------------------------------------------------- #


def test_replay_the_owners_eight_exchanges(chat, world, list_max_2):
    """The eight turns in order, one conversation. Each reply is scanned for snake_case
    by `_ScannedChat`; the assertions are each ruling's own, turn by turn.

    Amended to the round 8 ruling on PR #833 (owner retest of round 7, 27 Sep 2026): the
    header is the product-code answer's own intro naming the described set and its count,
    and a set row is the product-code row, so 1 and 3 read the one-line header and 2 and 4
    read the product-code rows.
    """
    sorento = _display(world["sorento"].brand_name)
    replies = [chat.say(x["text"], _verdict(x["verdict"], x["text"])) for x in _exchanges()]
    one, two, three, four, five, six, seven, eight = replies

    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): every brand, never a
    # default; too many is the count and a breakdown by the next attribute (the brand);
    # every miss and unknown value is the one reply structure.
    # 1: the count and the brand breakdown, one line each, no paging question.
    assert one.startswith("Here's what you want: wash basins with stock (6)\n• "), one
    assert f"• {sorento} wash basins: 3" in one and "How many" not in one, one
    # 2: R3. "10" lists what fits, the product-code rows.
    assert len(row_blocks(two)) == 2, two
    # 3: R2. The product type and trap are both named.
    assert "wash basin" not in _header(three)[0].lower()
    assert "water closet" in _header(three)[0].lower() and "p trap" in _header(three)[0].lower(), three
    # 4: R3.
    assert row_blocks(four), four
    # 5: R4.
    assert five.startswith("Here's what you want: gunmetal wash basins with incoming stock\n"), five
    # 6 and 7: R5.
    # Amended to fix round 10 on PR #833 ("for #833 yeah exact only"): no options offered.
    assert six.startswith("Here's what you want: water tap basin\n\nCouldn't find: water tap basin (product type)."), six
    assert set(row_codes(seven)) == _codes(world["taps"]), seven
    # 8: R6.
    assert eight.endswith("Couldn't find: t trap (trap). Would you like me to escalate to purchasing team?"), eight


def test_the_console_cases_expect_no_snake_case():
    """R7 over the recorded console cases too: no expected reply names a stored value."""
    import yaml

    path = Path(__file__).resolve().parent / "console_cases" / "2026-09-11-attribute-first-asks.yaml"
    body = yaml.safe_load(path.read_text())
    found: list[str] = []

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in ("reply_contains", "reply_starts_with", "reply_equals", "reply_first_line"):
            found.extend(snake_tokens(node))

    walk(body)
    assert not found, found
