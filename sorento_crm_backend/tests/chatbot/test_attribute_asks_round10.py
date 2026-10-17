"""Owner hand test of round 9 on PR #833 (28 Sep 2026 15:0x MYT, console :3084) and the
owner's ruling. Fix round 10.

The owner's words (verbatim): "i tried to search like gunmetal basin, there is no such
thing and it gives me flexible trap, we don't match 100% isit? i was thinking to need
exact match though", and the ruling "for #833 yeah exact only".

So an attribute word, a colour or finish word and a product-type word match the
catalogue's own vocabulary exactly (case-folded, trimmed, NFKC, a plural "s" aside) or
not at all. No nearest class label, no one-typo reading, no relevance guess and no
embedding or trigram neighbour of a word: a product that holds none of the asked values
exactly is never a result. A word that matched nothing is said in the round 9 "Couldn't
find" line, never offered back as a suggestion list.

The world is round 7's (Sorento, Mocha, Cabana; white water closets, basins, kitchen
sinks, taps) with NO gunmetal wash basin, plus the product the owner was shown: a
gunmetal flexible trap for a wash basin, filed under bathroom accessories, and a chrome
one beside it. On the hand-test database the resolver's embedding tier answers the word
"basin" with its nearest product; `embedding_says_trap` plays that neighbour here.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from tests.chatbot.test_attribute_asks_round3 import _ask, _entity
from tests.chatbot.test_attribute_asks_round3 import world as r3world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round4 import world as r4world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round7 import _code_ask, _make
from tests.chatbot.test_attribute_asks_round7 import world as r7world  # noqa: F401 - fixture used by name
from tests.chatbot.test_attribute_asks_round8 import _Loo
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_reverse_asks_owner_phrasings import _class_category


@pytest.fixture()
def world(r7world):
    db = r7world["db"]
    sorento = r7world["sorento"]
    uom = r7world["srt_tubs"][0].base_uom_id
    ba = _class_category(db, "BA")
    # The real BA categories carry the class (dev: class_label "Bathroom Accessory", searchable);
    # `_class_category` only codes the category, so give this one what backfill gives the real rows.
    from app.models.product import ProductCategory

    db.query(ProductCategory).filter(ProductCategory.id == ba).update(
        {"class_label": "Bathroom Accessory", "is_searchable": True}
    )

    def trap(code, name, description):
        return _make(db, code=code, name=name, description=description, brand=sorento, category_id=ba, uom_id=uom)

    traps = [
        trap("SRTFT10GM", "Sorento Basin Flexible Trap Gunmetal", "SRTFT10GM GUNMETAL FLEXIBLE TRAP FOR WASH BASIN"),
        trap("SRTFT10CR", "Sorento Basin Flexible Trap Chrome", "SRTFT10CR CHROME FLEXIBLE TRAP FOR BASIN"),
    ]
    db.commit()
    return {**r7world, "traps": traps, "every": r7world["every"] + traps}


@pytest.fixture()
def embedding_says_trap(monkeypatch, world):
    """The resolver's embedding tier, as the hand-test database answers a word with no
    code match: its nearest product, the gunmetal flexible trap."""
    from app.services import entity_resolver

    trap = world["traps"][0]

    def nearest(db, token, allowed_entity_types=None):
        if allowed_entity_types is not None and "product" not in allowed_entity_types:
            return []
        return [
            entity_resolver.ResolvedEntity(
                entity_type="product",
                canonical_code=trap.product_code,
                uuid=str(trap.id),
                match_field="embedding:product",
                match_tier="embedding",
                similarity=0.91,
                display={"semantic_match": True},
            )
        ]

    monkeypatch.setattr(entity_resolver, "_tier3_embedding_lookup", nearest)


@pytest.fixture()
def chat(session_factory, monkeypatch, stub_parser, stub_access, world, embedding_says_trap):
    return _Loo(session_factory, monkeypatch, stub_parser, stub_access, world, "compact")


def _spec(raw: str, key: str, value: Any) -> dict[str, Any]:
    return {**_entity(raw, "specification"), "spec_key": key, "spec_value": value}


def _codes(text: str, world) -> set[str]:
    return {
        p.product_code
        for p in world["every"]
        if re.search(rf"(?<![\w-]){re.escape(p.product_code)}(?![\w-])", text)
    }


#: Every product type of the world other than a wash basin, as a reply would name it.
_OTHER_TYPES = ("trap", "tap", "water closet", "kitchen sink", "bathtub", "accessor", "towel", "seat cover", "shower")

_ESCALATE = re.compile(r" Would you like me to escalate to \w+ team\?$")


# --------------------------------------------------------------------------- #
# 1. "gunmetal basin": no gunmetal basins exist                                  #
# --------------------------------------------------------------------------- #

G1 = "gunmetal basin"


@pytest.mark.parametrize(
    "verdict,leg",
    [
        # As the published parser (spk_0002) reads it: the class word and the finish.
        pytest.param(lambda: _code_ask(G1, _entity("basin", "category"), _spec("gunmetal", "finish", "Gunmetal")), "", id="product-ask"),
        # As round 8's trace read it: one category token, split by grounding.
        pytest.param(lambda: _code_ask(G1, _entity("gunmetal basin", "category")), "", id="one-category-token"),
        # As a product name: words, no code. Before round 10 this reached the ranker.
        pytest.param(lambda: _code_ask(G1, _entity("gunmetal basin", "product")), "", id="one-product-token"),
        pytest.param(lambda: _ask("stock", "gunmetal basin", G1), " with stock", id="stock"),
        pytest.param(lambda: _ask("incoming", "gunmetal basin", G1), " with incoming stock", id="incoming"),
    ],
)
def test_gunmetal_basin_is_wash_basins_only_never_the_flexible_trap(chat, world, verdict, leg):
    text = chat.say(G1, verdict())
    print(text)
    lines = text.split("\n")
    assert lines[0] == f"Here's what you want: gunmetal wash basins{leg}", text
    assert all(re.fullmatch(r"• \w+ wash basins: \d+", line) for line in lines[1:-2]), text
    assert lines[-2] == "", text
    assert lines[-1].startswith("Couldn't find: gunmetal (finish or colour)."), text
    assert _ESCALATE.search(lines[-1]), text
    assert not _codes(text, world) & {p.product_code for p in world["traps"]}, text
    for other in _OTHER_TYPES:
        assert other not in text.lower().replace("escalate", ""), (other, text)


@pytest.mark.parametrize(
    "message,verdict,leg",
    [
        # Fix round 12 on PR #833: the owner's phrase on :3084 and the script's, as v46 reads
        # them (the leg's ask, category + finish). The gunmetal trap HAS stock here, so a
        # stock word that let a word neighbour in would list it.
        pytest.param(
            "any gunmetal basin have stock",
            lambda: _code_ask("any gunmetal basin have stock", _entity("basin", "category"), _spec("gunmetal", "finish", "Gunmetal"), attrs=("stock",), intent_hint="check_stock", domain_hint="inventory"),
            " with stock",
            id="owner-have-stock",
        ),
        pytest.param(
            "any gunmetal basin has incoming?",
            lambda: _code_ask("any gunmetal basin has incoming?", _entity("basin", "category"), _spec("gunmetal", "finish", "Gunmetal"), attrs=("incoming",), intent_hint="check_incoming", domain_hint="incoming"),
            " with incoming stock",
            id="script-has-incoming",
        ),
    ],
)
def test_round12_console_owner_and_script_phrases_stay_attribute_first(chat, world, message, verdict, leg):
    from tests.chatbot.test_lane_require import _stock_for

    db = world["db"]
    for trap in world["traps"]:
        _stock_for(db, product_id=trap.id, warehouse_id=world["warehouse"].id, on_hand=937)
    db.commit()
    text = chat.say(message, verdict())
    print(text)
    lines = text.split("\n")
    assert lines[0] == f"Here's what you want: gunmetal wash basins{leg}", text
    assert lines[-1].startswith("Couldn't find: gunmetal (finish or colour)."), text
    assert "Stock summary" not in text, text
    assert not _codes(text, world) & {p.product_code for p in world["traps"]}, text


def test_a_gunmetal_flexible_trap_is_found_when_asked_for_by_its_own_type(chat, world):
    """Exact is not blind: the trap is a bathroom accessory with a gunmetal finish, and
    asking for exactly that finds it."""
    message = "gunmetal bathroom accessory"
    text = chat.say(
        message, _code_ask(message, _entity("bathroom accessory", "category"), _spec("gunmetal", "finish", "Gunmetal"))
    )
    print(text)
    assert _codes(text, world) == {"SRTFT10GM"}, text


# --------------------------------------------------------------------------- #
# 2. "any pnk water closet?": a word nothing holds                                #
# --------------------------------------------------------------------------- #

P1 = "any pnk water closet?"


@pytest.mark.parametrize(
    "verdict",
    [
        # spk_0002's reading: the parser puts the colour word right ("pink"). Grounding
        # reads the customer's own word instead (`grounding._as_typed`).
        pytest.param(lambda: _code_ask(P1, _entity("water closet", "category"), _spec("pink", "finish", None)), id="parser-put-it-right"),
        pytest.param(lambda: _code_ask(P1, _entity("water closet", "category"), _spec("pnk", "finish", None)), id="parser-as-typed"),
    ],
)
def test_pnk_water_closet_says_pnk_as_typed(chat, world, verdict):
    text = chat.say(P1, verdict())
    print(text)
    lines = text.split("\n")
    assert lines[0] == "Here's what you want: pnk water closets", text
    # SET-STOCK-RENDER (SSR-7): no breakdown lines.
    assert not [ln for ln in lines if re.fullmatch(r"• .+: [\d,]+", ln)], text
    assert "Couldn't find: pnk (finish or colour). Would you like me to escalate to" in text, text
    assert "pink" not in text.lower(), text


def test_pnk_inside_the_category_token_is_said_back_never_searched(chat, world):
    text = chat.say(P1, _code_ask(P1, _entity("pnk water closet", "category")))
    print(text)
    lines = text.split("\n")
    # SET-STOCK-RENDER (SSR-7): no breakdown lines; the typed header and the miss line stay.
    assert lines[0] == "Here's what you want: pnk water closets", text
    assert not [ln for ln in lines if re.fullmatch(r"• .+: [\d,]+", ln)], text
    assert 'Couldn\'t find: "pnk".' in text, text
    assert "*Product Code:*" not in text and "product:" not in text, text


# --------------------------------------------------------------------------- #
# 3. "water tap basin": two product types in one phrase                          #
# --------------------------------------------------------------------------- #

W1 = "water tap basin"


@pytest.mark.parametrize(
    "verdict,team",
    [
        pytest.param(lambda: _code_ask(W1, _entity(W1, "category")), "purchasing", id="product-ask"),
        pytest.param(lambda: _ask("stock", W1, W1), "warehouse", id="stock"),
    ],
)
def test_water_tap_basin_is_one_unknown_product_type(chat, world, verdict, team):
    """It holds two exact phrases, "water tap" (a tap) and "basin" (a wash basin). No one
    product is both, and answering taps, basins or both would be guessing what was
    meant, so the phrase is said back whole as a product type nothing matched. Before
    round 10 the stock ask offered "• Taps / • Wash basins" (the nearest labels) and the
    product ask listed products that merely had the words in their sentence."""
    text = chat.say(W1, verdict())
    print(text)
    assert text == (
        "Here's what you want: water tap basin\n"
        "\n"
        f"Couldn't find: water tap basin (product type). Would you like me to escalate to {team} team?"
    ), text


# --------------------------------------------------------------------------- #
# The mechanisms, pinned on their own                                            #
# --------------------------------------------------------------------------- #


def test_no_difflib_nearest_class_label_remains():
    from app.services import product_predicate_service

    assert not hasattr(product_predicate_service, "_nearest_class_labels")
    assert not hasattr(product_predicate_service, "_common_class_labels")
    assert "difflib" not in inspect.getsource(product_predicate_service)


def test_an_unread_word_zero_carries_no_suggestions(world):
    from app.services.product_predicate_service import resolve_product_set

    out = resolve_product_set(world["db"], require={"stock": True}, scope_terms=["watr tap"])
    assert out["qualifying_total"] == 0
    assert out["unrecognized_terms"] == ["watr tap"]
    assert "suggestions" not in out and "common_class_labels" not in out


def test_exact_ranking_never_admits_a_product_on_a_word_in_its_sentence(world):
    from app.services.product_spec_search import search_specs

    db = world["db"]
    found = search_specs(db, specs=[{"key": "finish", "value": "Gunmetal"}], free_terms=["gunmetal basin"], exact=True)
    assert found["candidates"] == [], found["candidates"]
    loose = search_specs(db, specs=[{"key": "finish", "value": "Gunmetal"}], free_terms=["gunmetal basin"])
    assert "SRTFT10GM" in {c["product_code"] for c in loose["candidates"]}, "the non-chatbot ranker is unchanged"
    two = search_specs(db, free_terms=["water tap basin"], exact=True)
    assert two["candidates"] == []
    basins = search_specs(db, free_terms=["basins"], exact=True)
    assert basins["candidates"] and all(c["class"] == "Wash Basin" for c in basins["candidates"])


def test_lookup_ids_are_code_matches_only():
    from app.api.v1.system.references import _collect_lookup_product_ids

    result = {
        "resolutions": [
            {"token": "basin", "matches": [{"entity_type": "product", "uuid": "a", "match_tier": "embedding"}]},
            {"token": "gunmetal basin", "matches": [{"entity_type": "product", "uuid": "b", "match_tier": "trgm"}]},
            {"token": "srtwc8840", "matches": [
                {"entity_type": "product", "uuid": "c", "match_tier": "prefix"},
                {"entity_type": "product", "uuid": "d", "match_tier": "embedding"},
            ]},
        ],
        "intersection": [{"entity_type": "product", "uuid": "e", "match_tier": "and"}],
    }
    assert _collect_lookup_product_ids(result) == ["c", "e"]


@pytest.mark.parametrize(
    "raw,bound",
    [
        ("thicnkess 1.2 mm", False),
        ("gunmetl", False),
        ("gunmetal", True),
        ("GUNMETAL", True),
        ("ｇｕｎｍｅｔａｌ", True),  # NFKC: full-width letters are the same word
        ("thickness 1.2 mm", True),
    ],
)
def test_grounding_binds_a_word_only_as_the_catalogue_spells_it(world, raw, bound):
    from app.services.chatbot.head.grounding import ground_words, load_vocabulary

    grounded, _ = ground_words(raw, load_vocabulary(world["db"]))
    assert any(g.value is not None for g in grounded) is bound, [g.__dict__ for g in grounded]


def test_exact_ranking_reads_a_number_as_the_number_stored(world):
    """1.2 mm is 1.2 mm: a 1.25 mm sink is inside the registry's tolerance band and the
    ordinary ranker would count it, the chatbot's exact ranking does not."""
    from app.services.product_spec_search import search_specs

    db = world["db"]
    ks = _class_category(db, "KS")
    uom = world["srt_tubs"][0].base_uom_id
    for code, mm in (("SRTKS10T120", "1.2"), ("SRTKS10T125", "1.25")):
        _make(db, code=code, name=f"Sink {mm}", description=f"{code} KITCHEN SINK (860X500X220X{mm}MM)", brand=world["sorento"], category_id=ks, uom_id=uom)
    db.commit()
    found = search_specs(db, specs=[{"key": "thickness", "value": 1.2}], free_terms=["kitchen sink"], exact=True)
    assert {c["product_code"] for c in found["candidates"]} == {"SRTKS10T120"}, found["candidates"]
