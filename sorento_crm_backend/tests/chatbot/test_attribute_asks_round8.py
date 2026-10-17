"""Owner retest of round 7 on PR #833 (27 Sep 2026 12:59 to 13:04 MYT, console :3084 at
b056885e, contact Mr Loo). Fix round 8: F1 grounding, F2 one answer shape per domain.

The owner's words (verbatim): "hmm #833 still cna't hit, I need th answer to be exactly
like when i check stock by product code, for example Stock summary for the requested
products. / 1. Product Code: GB3006C / Total: 9 (O/S: 0) / WH3: 9 (O/S: 0) / (PRODUCT
DISCONTINUED) / Data last updated: 21/09/2026 10:32:45 for detailed mode, we got compact
mode also, my point is the answer should look exactly when i check stock by product code,
this sppliaes to incoming, product attachment etc and every other domain, ... when i ask
"any gunmetal basin has incoming?" it is kinda sus, then i follow up with cert also kinda
sus, then when i ask about pink colour water closet also knida sus, thicnkess also kinda
sus, is it the parser is not boudned by what the system has as a spec? like it doens't
know colour is colour one meh, why it become document type one, ... (I don't want you to
make it smarter for this case only, it should be general)"

Rulings assumed (the owner confirms): a set answer's intro names the described set and
the count ("Here's what you want: Sorento wash basins with stock (276, showing 1 to 10)")
then exactly the product-code rows, detailed or compact by the same mode switch, then the
same footer; the same rule per domain (incoming, attachments, price).

Every turn runs `engine.run_turn` with the real resolver, class vocabulary, specification
registry and brands table (round 7's harness); the parser verdict is stubbed as the
owner's turn traces read it, and the tools in their presenter shapes, with the stock
tool's detailed and compact modes and the "Data last updated" stamp.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

import pytest

from tests.chatbot.test_attribute_asks_round3 import _ask, _display, _entity  # noqa: F401
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import (  # noqa: F401 - fixtures used by name
    _attachments_tool,
    _code_ask,
    _incoming_tool,
    _Leena,
    _make,
    _product_tool,
    world as r7world,
)
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_lane_require import _certificate_for, _stock_for
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category, _incoming_for

STAMP = "2026-09-21T02:32:45Z"


# --------------------------------------------------------------------------- #
# World                                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(r7world):
    """Round 7's world plus the products the owner's retest names:

      - three Sorento GUNMETAL wash basins with incoming, a certificate and stock, one of
        them discontinued; one Sorento CHROME wash basin with incoming (never a gunmetal
        answer);
      - two Sorento UNDERMOUNT wash basins with a certificate;
      - two Sorento kitchen sinks 1.2 mm thick and one 1.0 mm thick, all with stock;
      - two Sorento WHITE water closets with stock (a known colour beside the unknown pink).
    """
    db = r7world["db"]
    sorento = r7world["sorento"]
    uom = r7world["srt_tubs"][0].base_uom_id
    wh = r7world["warehouse"]
    wb, ks, wc = _class_category(db, "WB"), _class_category(db, "KS"), _class_category(db, "WC")

    def make(code, name, description, category):
        return _make(db, code=code, name=name, description=description, brand=sorento, category_id=category, uom_id=uom)

    gunmetal = [make(f"SRTWB8GM{i}", f"Sorento Gunmetal Basin {i}", f"SRTWB8GM{i} GUNMETAL WASH BASIN", wb) for i in range(3)]
    gunmetal[0].is_discontinued = True
    chrome = make("SRTWB8CR0", "Sorento Chrome Basin", "SRTWB8CR0 CHROME WASH BASIN", wb)
    undermount = [make(f"SRTWB8UM{i}", f"Sorento Undermount Basin {i}", f"SRTWB8UM{i} UNDERMOUNT WASH BASIN", wb) for i in range(2)]
    thick12 = [
        make(f"SRTKS8T12{i}", f"Sorento Sink 1.2 {i}", f"SRTKS8T12{i} KITCHEN SINK (860X500X220X1.2MM)", ks) for i in range(2)
    ]
    thick10 = [make("SRTKS8T100", "Sorento Sink 1.0", "SRTKS8T100 KITCHEN SINK (860X500X220X1.0MM)", ks)]
    white = [make(f"SRTWC8WH{i}", f"Sorento White WC {i}", f"SRTWC8WH{i} WHITE WATER CLOSET", wc) for i in range(2)]
    for p in gunmetal + [chrome]:
        _incoming_for(db, product_id=p.id)
    for p in gunmetal + undermount:
        _certificate_for(db, product_id=p.id, valid_until=date.today() + timedelta(days=365))
    for p in gunmetal + thick12 + thick10 + white:
        _stock_for(db, product_id=p.id, warehouse_id=wh.id)
    db.commit()
    return {
        **r7world,
        "gunmetal": gunmetal,
        "chrome": chrome,
        "undermount": undermount,
        "thick12": thick12,
        "thick10": thick10,
        "white": white,
        "every": r7world["every"] + gunmetal + [chrome] + undermount + thick12 + thick10 + white,
    }


# --------------------------------------------------------------------------- #
# Tools: the stock tool's two modes, with the footer stamp                       #
# --------------------------------------------------------------------------- #


def _stock_tool(db, warehouse_code: str, mode: str):
    """`presenters._stock_compact` / the detailed `item()` rows, as the owner reads them:
    compact is Product Code, Total "n (O/S: 0)", one field per location; detailed is one
    field per fact. Discontinued flagged; `last_updated_at` stamped."""
    from app.models.inventory import Stock
    from app.models.product import Product

    def call(args: dict[str, Any]) -> str:
        ids = list(args.get("product_ids") or [])
        rows = db.query(Product).filter(Product.id.in_(ids)).order_by(Product.product_code).all() if ids else []
        items = []
        for p in rows:
            total = sum(int(s.quantity_on_hand or 0) for s in db.query(Stock).filter(Stock.product_id == p.id))
            if mode == "compact":
                fields = [
                    {"key": "product_code", "label": "Product Code", "value": p.product_code},
                    {"key": "total_on_hand", "label": "Total", "value": f"{total} (O/S: 0)"},
                    {"label": warehouse_code, "value": f"{total} (O/S: 0)"},
                ]
            else:
                fields = [
                    {"key": "product_code", "label": "Product Code", "value": p.product_code},
                    {"key": "warehouse", "label": "Warehouse", "value": warehouse_code},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": total},
                    {"key": "quantity_available", "label": "Available", "value": total},
                ]
            items.append({"title": p.product_code, "fields": fields, "flags": {"discontinued": bool(p.is_discontinued)}})
        return json.dumps(
            {
                "result_type": "stock",
                "intro": "Stock summary for the requested products.",
                "items": items,
                "has_result": bool(items),
                "last_updated_at": STAMP,
            }
        )

    return call


def _tools_for(mode: str):
    def _tools(world, calls: list[dict[str, Any]]):
        db = world["db"]
        stock = _stock_tool(db, world["warehouse"].warehouse_code, mode)
        attachments = _attachments_tool(db)
        incoming = _incoming_tool(db)
        products = _product_tool(db)

        def fake_call_tool(name: str, args: dict[str, Any]) -> str:
            calls.append({"name": name, "args": dict(args)})
            if name == "crm_master_product_attachments_list":
                return attachments(args)
            if "incoming" in name or "shipment" in name:
                return incoming(args)
            if "stock" in name or "inventory" in name:
                return stock(args)
            return products(args)

        return fake_call_tool

    return _tools


class _Loo(_Leena):
    def __init__(self, session_factory, monkeypatch, stub_parser, stub_access, world, mode):
        import tests.chatbot.test_attribute_asks_round7 as round7

        monkeypatch.setattr(round7, "_tools", _tools_for(mode))
        super().__init__(session_factory, monkeypatch, stub_parser, stub_access, world)


@pytest.fixture(params=["compact", "detailed"])
def chat(request, session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Loo(session_factory, monkeypatch, stub_parser, stub_access, world, request.param)


@pytest.fixture()
def compact(session_factory, monkeypatch, stub_parser, stub_access, world):
    return _Loo(session_factory, monkeypatch, stub_parser, stub_access, world, "compact")


@pytest.fixture()
def list_max_3(monkeypatch):
    from app.services.chatbot.lanes.business import answer as answer_mod

    monkeypatch.setattr(answer_mod, "SET_LIST_MAX", 3)


# --------------------------------------------------------------------------- #
# The parser's readings, as the owner's traces show them                         #
# --------------------------------------------------------------------------- #

M1A = "any basin has stock"
M1B = "10"
M2 = "any gunmetal basin has incoming?"
M2B = "cert?"
M3 = "any water closet f trap?"
M4 = "any pink colour water closet?"
M5 = "any kitchne sink with thicnkess 1.2 mm"


def _v2() -> dict[str, Any]:
    return _ask("incoming", "gunmetal basin", M2)


def _v2b() -> dict[str, Any]:
    """"cert?" right after: the parser keeps the previous entities and asks for the
    certificate, as the trace shows (it answered the same miss again)."""
    from tests.chatbot.test_engine import _parser_output

    return _parser_output(
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
        user_goal="trying to see the certificates of those basins",
        requested_attributes=["cert"],
        entity_op="reuse",
        entities=[],
    )


def _v3() -> dict[str, Any]:
    return _code_ask(
        M3,
        # ATTACH-GROUNDING-TYPO option (c) (owner, 6 Oct 2026): a parser-filed document type
        # is trusted, so the misfiled "f trap" document of round 8 is now filed where the
        # words belong, the category, and grounds the same way.
        _entity("water closet f trap", "category"),
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
    )


def _v4() -> dict[str, Any]:
    return _code_ask(
        M4,
        _entity("pink colour water closet", "category"),  # option (c), see _v3
        intent_hint="check_product_attachment",
        domain_hint="product_attachment",
        match_mode="or",
    )


def _v5(kind: str = "stock") -> dict[str, Any]:
    return _ask(kind, "kitchne sink thicnkess 1.2 mm", M5)  # option (c), see _v3


# Reply readers ---------------------------------------------------------------- #

_ROW = re.compile(r"^(\d+)\. ")


def _intro(text: str) -> str:
    return text.split("\n\n", 1)[0]


def _blocks(text: str) -> dict[str, str]:
    """{product code: the row block with its number removed} for every numbered row."""
    out: dict[str, str] = {}
    for block in text.split("\n\n"):
        block = block.strip()
        if not _ROW.match(block):
            continue
        body = _ROW.sub("", block, count=1)
        m = re.search(r"\*Product Code:\* ([A-Za-z0-9-]+)", body)
        if m:
            out[m.group(1)] = body
    return out


def _codes(text: str, world) -> set[str]:
    return {
        p.product_code
        for p in world["every"]
        if re.search(rf"(?<![\w-]){re.escape(p.product_code)}(?![\w-])", text)
    }


def _brand(world) -> str:
    return _display(world["sorento"].brand_name)


def _by_code(chat, code: str, kind: str) -> str:
    """The same product asked by its code, in the same domain."""
    intent, domain, attrs = {
        "stock": ("check_stock", "inventory", ("stock",)),
        "incoming": ("check_incoming", "incoming", ("incoming",)),
        "cert": ("check_product_attachment", "product_attachment", ("cert",)),
        "price": ("check_product", "master_products", ("price",)),
    }[kind]
    message = f"{code} {attrs[0]}"
    extra = [{**_entity("cert", "attachment_type"), "canonical_code": "certificate"}] if kind == "cert" else []
    return chat.say(
        message, _code_ask(message, _entity(code, "product"), *extra, attrs=attrs, intent_hint=intent, domain_hint=domain)
    )


# --------------------------------------------------------------------------- #
# F2: one shape per domain                                                       #
# --------------------------------------------------------------------------- #


def test_item1_a_stock_set_reads_exactly_like_the_product_code_answer(chat, world, list_max_3):
    """"any basin has stock" then "10": the rows are the product-code rows, detailed or
    compact by the same mode, with the same flags and the same footer; only the intro
    names the set and the count."""
    first = chat.say(M1A, _ask("stock", "basin", M1A))
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): too many is the count and a breakdown by the next
    # attribute (every brand, no default), never a paging question.
    # SET-STOCK-RENDER (SSR-5): no breakdown lines; the first 3 are listed.
    assert re.match(r"Here's what you want: wash basins with stock \(\d+, showing 1 to 3\)", first), first
    assert not [ln for ln in first.split("\n") if ln.startswith("• ")], first
    assert "too many to list" not in first, first
    text = chat.say(M1B, _ask("stock", "basin", M1B, top_n=2, entities=[]))
    # SET-STOCK-RENDER (SSR-5/6): the ask listed 1 to 3; the typed "10" is read from the text and
    # capped at the list limit, so the next page is 4 to 6.
    total = int(re.search(r"\((\d+), showing 4 to 6\)", text).group(1))
    assert _intro(text) == f"Here's what you want: wash basins with stock ({total}, showing 4 to 6)", text
    assert re.search(r"\n\n_Data last updated: 21/09/2026 \d\d:32:45_$", text.rstrip()), text
    blocks = _blocks(text)
    assert len(blocks) == 3, text
    for code, block in blocks.items():
        single = _blocks(_by_code(chat, code, "stock"))
        assert block == single[code], (block, single[code])
    # No separate set row format and no bold attribute header lines.
    assert "*Brand:*" not in text and "*Product type:*" not in text, text


def test_f2_a_discontinued_row_carries_the_same_flag_as_by_code(compact, world):
    text = compact.say("any gunmetal basin has stock", _ask("stock", "gunmetal basin", "any gunmetal basin has stock"))
    code = world["gunmetal"][0].product_code
    assert "⚠️  *(PRODUCT DISCONTINUED)*" in _blocks(text)[code], text
    assert _blocks(text)[code] == _blocks(_by_code(compact, code, "stock"))[code]
    assert "*(Discontinued)*" not in text, text


@pytest.mark.parametrize("kind", ["incoming", "cert"])
def test_f2_incoming_and_certificate_sets_read_like_the_product_code_answer(compact, world, kind):
    message = f"any gunmetal basin has {kind}"
    text = compact.say(message, _ask(kind, "gunmetal basin", message))
    blocks = _blocks(text)
    assert set(blocks) == {p.product_code for p in world["gunmetal"]}, text
    for code, block in blocks.items():
        assert block == _blocks(_by_code(compact, code, kind))[code], code
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): the intro names its leg once ("no repeats").
    assert f"gunmetal wash basins with {'certificates' if kind == 'cert' else 'incoming stock'} (3)" in _intro(text), text
    assert "*Finish or colour:*" not in text, text


# --------------------------------------------------------------------------- #
# F1: grounding, with the owner's exact messages                                 #
# --------------------------------------------------------------------------- #


def test_item2_gunmetal_basin_incoming_answers_the_gunmetal_basins(compact, world):
    text = compact.say(M2, _v2())
    assert "Could not find" not in text, text
    assert _codes(text, world) == {p.product_code for p in world["gunmetal"]}, text
    assert _intro(text).startswith("Here's what you want: "), text
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no brand named means every brand; the leg is said once.
    assert _intro(text) == "Here's what you want: gunmetal wash basins with incoming stock (3)", text


def test_item2_a_finish_no_basin_has_incoming_says_what_it_looked_for_never_a_category_miss(compact, world):
    """The owner's reply was "Could not find incoming for category gunmetal basin": the
    zero set's recount crashed on a member with no finish (`_near_miss` bound its empty
    list as a JSON string), so the resolver lost the set and the generic miss named the
    parser's kind. A zero set now says what it looked for and what the others hold."""
    message = "any rose gold basin has incoming?"
    text = compact.say(message, _ask("incoming", "rose gold basin", message))
    assert "Could not find" not in text and "category" not in text, text
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no explanation of how it searched: the one reply
    # structure: the finishes the basins with incoming do come in, one line each.
    assert text.startswith("Here's what you want: rose gold wash basins with incoming stock\n• Gunmetal wash basins: 3\n"), text
    assert text.endswith("Couldn't find: rose gold (finish or colour). Would you like me to escalate to purchasing team?"), text


def test_item2_then_cert_switches_the_domain_and_keeps_the_gunmetal_basins(compact, world):
    compact.say(M2, _v2())
    text = compact.say(M2B, _v2b())
    assert "Could not find" not in text, text
    assert _codes(text, world) == {p.product_code for p in world["gunmetal"]}, text
    assert "*Certificate Number:*" in text and "gunmetal wash basins with certificates (3)" in _intro(text), text


@pytest.mark.parametrize("word,kind", [("stock?", "stock"), ("incoming?", "incoming")])
def test_a_one_word_follow_up_switches_the_domain_and_keeps_the_described_set(compact, world, word, kind):
    compact.say(M2, _v2())
    from tests.chatbot.test_engine import _parser_output

    intent, domain = {"stock": ("check_stock", "inventory"), "incoming": ("check_incoming", "incoming")}[kind]
    text = compact.say(
        word,
        _parser_output(intent_hint=intent, domain_hint=domain, match_mode="or", requested_attributes=[kind], entity_op="reuse", entities=[]),
    )
    assert _codes(text, world) == {p.product_code for p in world["gunmetal"]}, text


def test_a_one_word_stock_follow_up_answers_as_the_described_set(compact, world):
    """STOCK-FILL-PREDICATE (live replay, 6 Oct 2026): a carried product beside a stray
    carried word keeps its own line and the word's population is dropped. A carried SET
    is the other case and must not be caught by that rule: its members were placed by the
    words carried beside them, so "stock?" re-describes the set and answers in the set's
    own shape, header and count, never as product lines without one."""
    compact.say(M2, _v2())
    from tests.chatbot.test_engine import _parser_output

    text = compact.say(
        "stock?",
        _parser_output(
            intent_hint="check_stock", domain_hint="inventory", match_mode="or", requested_attributes=["stock"], entity_op="reuse", entities=[]
        ),
    )
    assert "gunmetal wash basins with stock (3)" in _intro(text), text
    assert _codes(text, world) == {p.product_code for p in world["gunmetal"]}, text


def test_item3_f_trap_is_an_unknown_trap(compact, world):
    text = compact.say(M3, _v3())
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): the one reply structure, broken down by the key.
    # SET-STOCK-RENDER (SSR-7): the typed header, no breakdown line, no rows, then the miss line.
    assert text.split("\n", 1)[0] == "Here's what you want: f trap water closets", text
    assert not [ln for ln in text.split("\n") if ln.startswith("• ")], text
    assert "*Product Code:*" not in text, text
    assert "Couldn't find: f trap (trap). Would you like me to escalate to" in text, text
    assert "document type" not in text.lower(), text


def test_item4_pink_colour_is_an_unknown_finish_or_colour_never_a_document_type(compact, world):
    text = compact.say(M4, _v4())
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no list of known choices; what the set does come in.
    # SET-STOCK-RENDER (SSR-7): the typed header, no breakdown line, no rows, then the miss line.
    assert text.split("\n", 1)[0] == "Here's what you want: pink water closets", text
    assert not [ln for ln in text.split("\n") if ln.startswith("• ")], text
    assert "*Product Code:*" not in text, text
    assert "Couldn't find: pink (finish or colour)." in text, text
    assert "document type" not in text.lower(), text


M5_SPELT = "any kitchen sink with thickness 1.2 mm"


def _v5_spelt(kind: str = "stock") -> dict[str, Any]:
    return _ask(kind, "kitchen sink thickness 1.2 mm", M5_SPELT)  # option (c), see _v3


def test_item5_misspelt_kitchne_and_thicnkess_are_said_back_never_read_as_the_words_they_resemble(compact, world):
    """Fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"): round 8
    read "kitchne" as kitchen and "thicnkess" as thickness, one typo apart. Neither is the
    catalogue's word, so neither binds; both are the "Couldn't find" line. Option (c) (owner,
    6 Oct 2026): the words now reach grounding as the category's, not a misfiled document,
    so the number stays with the sink and the two misspelt words are said back."""
    text = compact.say(M5, _v5("stock"))
    print(text)
    assert "document type" not in text.lower(), text
    assert not _codes(text, world) & {p.product_code for p in world["thick12"] + world["thick10"]}, text
    assert 'Couldn\'t find: "kitchne" and "thicnkess".' in text, text


@pytest.mark.parametrize("kind", ["stock", "cert"])
def test_item5_thickness_1_2_mm_narrows_the_kitchen_sinks(compact, world, kind):
    """Amended to fix round 10 on PR #833: the owner's words spelt as the catalogue spells
    them (the misspelt message is pinned just above)."""
    text = compact.say(M5_SPELT, _v5_spelt(kind))
    assert "document type" not in text.lower(), text
    if kind == "stock":
        assert _codes(text, world) == {p.product_code for p in world["thick12"]}, text
        assert "kitchen sinks with thickness 1.2 mm with stock (2)" in _intro(text), text
    else:
        # No 1.2 mm sink has a certificate: said as a miss about THOSE sinks.
        assert not (_codes(text, world) & {p.product_code for p in world["thick10"]}), text


def test_undermount_basin_cert_answers_the_undermount_basins(compact, world):
    message = "any undermount basin has cert"
    text = compact.say(message, _ask("cert", "undermount basin", message))
    assert _codes(text, world) == {p.product_code for p in world["undermount"]}, text


# --------------------------------------------------------------------------- #
# Generated from the registry: a colour, a mounting, a trap, a material, a      #
# thickness and a size, each with a product type, each with every domain.       #
# --------------------------------------------------------------------------- #


def _registry_word(db, key: str, value: str) -> str:
    from app.models.product_spec import ProductSpecRegistry
    from app.services.product_spec_registry import merged_synonyms

    row = db.query(ProductSpecRegistry).filter_by(spec_key=key).one()
    return merged_synonyms(row)[value][0]


_DESCRIBED = [
    # (registry key, value or number, product type word, class noun in the intro)
    ("finish", "gunmetal", "basin", "wash basins"),
    ("finish", "white", "water closet", "water closets"),
    ("mounting", "under_counter", "basin", "wash basins"),
    ("trap_type", "p_trap", "water closet", "water closets"),
    ("material", "stainless_steel", "kitchen sink", "kitchen sinks"),
    ("thickness", 1.2, "kitchen sink", "kitchen sinks"),
]


@pytest.mark.parametrize("key,value,type_word,noun", _DESCRIBED, ids=[f"{k}-{v}" for k, v, _, _ in _DESCRIBED])
@pytest.mark.parametrize("kind", ["stock", "incoming", "cert"])
def test_a_registry_descriptor_with_a_product_type_is_grounded_in_every_domain(compact, world, key, value, type_word, noun, kind):
    db = world["db"]
    said = f"thickness {value} mm" if key == "thickness" else _registry_word(db, key, value)
    message = f"any {said} {type_word} has {kind}"
    text = compact.say(message, _ask(kind, f"{said} {type_word}", message))
    assert "document type" not in text.lower() and "Could not find" not in text, text
    # Whatever qualifies, the reply is either a set in the product-code shape or an
    # honest miss that names what was looked for; never a set of the whole class.
    if _blocks(text):
        assert noun in _intro(text), text
        assert "*Product Code:*" in text, text
    else:
        from app.services.product_spec_registry import display_spec_value

        shown = f"{value} mm" if key == "thickness" else display_spec_value(value)
        assert shown.lower() in text.lower(), text


def test_price_of_a_described_set_reads_like_the_product_code_price(compact, world):
    message = "any gunmetal basin price"
    verdict = _code_ask(message, _entity("gunmetal basin", "category"), attrs=("price",))
    text = compact.say(message, verdict)
    blocks = _blocks(text)
    assert set(blocks) == {p.product_code for p in world["gunmetal"]}, text
    for code, block in blocks.items():
        assert block == _blocks(_by_code(compact, code, "price"))[code], code
    # Amended to fix round 9 on PR #833 (owner, 28 Sep 2026): no silent brand default.
    assert _intro(text) == "Here's what you want: gunmetal wash basins with prices (3)", text


# --------------------------------------------------------------------------- #
# Regression: the owner's messages of this retest, in order                     #
# --------------------------------------------------------------------------- #


def test_the_retest_in_order(compact, world, list_max_3):
    replies = [
        compact.say(M1A, _ask("stock", "basin", M1A)),
        compact.say(M1B, _ask("stock", "basin", M1B, top_n=3, entities=[])),
        compact.say(M2, _v2()),
        compact.say(M2B, _v2b()),
        compact.say(M3, _v3()),
        compact.say(M4, _v4()),
        compact.say(M5, _v5()),
    ]
    assert "with stock (" in replies[1].split("\n")[0], replies[1]
    assert _codes(replies[2], world) == {p.product_code for p in world["gunmetal"]}, replies[2]
    assert _codes(replies[3], world) == {p.product_code for p in world["gunmetal"]}, replies[3]
    assert "Couldn't find: f trap (trap)." in replies[4] and "Couldn't find: pink (finish or colour)." in replies[5], replies[4:6]
    # Amended to fix round 10 on PR #833 ("for #833 yeah exact only"): the misspelt
    # "kitchne" and "thicnkess" bind nothing and are said back.
    assert not _codes(replies[6], world), replies[6]
    assert 'Couldn\'t find: "kitchne" and "thicnkess".' in replies[6], replies[6]
    for text in replies:
        assert "document type" not in text.lower(), text
