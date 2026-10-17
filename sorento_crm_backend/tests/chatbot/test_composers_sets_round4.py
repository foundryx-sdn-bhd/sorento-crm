"""CHAT-COMPOSER Sets round 4 red tests.

U1  a "more" on a zero-qualifying described list pages THE SAME miss list (leg in the header, the
    miss sentence and offer last, the producer's family-code order, nothing repeated or skipped).
S2  a cut summary set page keeps its action links.
S3  a cut summary page slices `answers` by printed row, not by code count.
R2  the other-brands offer keeps the offer and drops the counts (gate on).
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business import answer as A
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_attribute_asks_round2 import _stock_verdict
from tests.chatbot.test_composers_sets_round3 import _codes, _more_verdict, _seed_zero_stock
from tests.chatbot.test_composers_stock_xd import _set_gate

# the engine harness (real resolver, faithful stock tool)
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401 - fixtures used by name
from tests.chatbot.test_set_stock_render import _set_ask, make_chat, world  # noqa: F401

BUDGET = 3900
OFFER = "Would you like me to escalate to warehouse team?"


def _bullets(text):
    return re.findall(r"^• (\S+)$", text, re.MULTILINE)


# --------------------------------------------------------------------------- #
# U1: the second page of a zero-qualifying described list
# --------------------------------------------------------------------------- #


def test_u1_stock_leg_page_two_is_the_same_miss_list(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, True)
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    first = chat.say("any wash basin has stock", _set_ask())
    second = chat.say("more", _more_verdict())
    assert second.startswith("Here's what you want: wash basins (55, showing 51 to 55)"), second[:200]
    one, two = _bullets(first), _bullets(second)
    assert len(one) == 50 and len(two) == 5 and not set(one) & set(two)
    assert (set(one) | set(two)) == world.codes
    assert "But no stock matched these." in second
    assert second.rstrip().endswith(OFFER), second[-160:]
    assert 'Reply "more"' not in second


def test_u1_incoming_leg_page_two_is_the_same_miss_list(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, True)
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    ask = _stock_verdict(
        [{"raw": "wash basin", "hint": "product_type"}],
        "any wash basin has incoming",
        intent_hint="check_incoming",
        domain_hint="incoming",
        requested_attributes=["incoming"],
    )
    first = chat.say("any wash basin has incoming", ask)
    second = chat.say("more", _more_verdict() | {"domain_hint": "incoming", "intent_hint": "check_incoming"})
    assert second.split("\n\n")[0].startswith("Here's what you want: wash basins (55, showing 51 to 55)"), second[:200]
    assert "incoming" not in second.split("\n\n")[0]
    one, two = _bullets(first), _bullets(second)
    assert len(one) == 50 and not set(one) & set(two) and (set(one) | set(two)) == world.codes
    miss = re.search(r"But no [^.]+ matched these\.", first)
    assert miss and "incoming" in miss.group(0), first[-200:]
    assert miss.group(0) in second
    offer = re.search(r"Would you like me to escalate to [^?]+ team\?$", first.rstrip())
    assert offer, first[-160:]
    assert second.rstrip().endswith(offer.group(0)), second[-160:]


def test_u1_variant_families_page_in_the_producers_order(session_factory, monkeypatch, stub_parser, stub_access, world):
    """Several products per family: pages list family codes, in family-code order, no overlap."""
    _set_gate(monkeypatch, True)
    parents = []
    for i in range(52):
        parent = world.basin(world.sorento, f"ZZF{51 - i:02d}", 0)  # created in reverse code order
        variant = world.basin(world.sorento, f"ZZG{i:02d}", 0)
        variant.variant_of_id = parent.id
        parents.append(parent)
    world.commit()
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    first = chat.say("any wash basin has stock", _set_ask())
    second = chat.say("more", _more_verdict())
    one, two = _bullets(first), _bullets(second)
    families = {p.product_code for p in parents}
    assert len(one) == 50 and len(two) == 2, (len(one), len(two))
    assert not set(one) & set(two)
    assert set(one) | set(two) == families
    assert one == sorted(one) and two == sorted(two) and max(one) < min(two)


# --------------------------------------------------------------------------- #
# S2 / S3: a cut summary set page
# --------------------------------------------------------------------------- #


def _f(label, value):
    return {"label": label, "value": value}


def _summary_page(links=True):
    items = []
    for i in range(50):
        for co in ("Sorento", "Mocha"):
            items.append({"title": f"SRTWC{i:03d}", "fields": [_f("Company", co), _f("Product Code", f"SRTWC{i:03d}")]})
    summary = [
        {
            "title": None,
            "fields": [
                _f("Product Code", f"SRTWC{i:03d}"),
                _f("Customer", f"CUSTOMER NUMBER {c} SDN BHD"),
                _f("Ordered Qty", 100 + c),
                _f("SO Outstanding", c),
            ],
        }
        for i in range(50)
        for c in range(4)
    ]
    env = {
        "result_type": "products",
        "intro": "x",
        "items": items,
        "summary_items": summary,
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }
    if links:
        env["action_links"] = [
            {"label": "Download Excel", "url": "https://example.test/a.xlsx"},
            {"label": "Open", "url": "https://example.test/b"},
        ]
    pred = {"qualifying_total": 182, "offset": 0, "class_labels": ["water closet"], "require": {}, "description": []}
    return fetch.output_structurer(env, {"tool": "crm_master_products_list", "semantic_input": {}, "predicate": pred})


def test_s2_gate_off_uncut_page_prints_the_action_links(monkeypatch):
    _set_gate(monkeypatch, False)
    text = _summary_page()["response"]
    assert "1. *Download Excel:* https://example.test/a.xlsx\n2. *Open:* https://example.test/b" in text


def test_s2_a_cut_summary_page_keeps_every_action_link(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _summary_page()
    text = out["response"]
    assert len(text) <= BUDGET, len(text)
    assert out["set_window"]["last"] < 50
    assert "1. *Download Excel:* https://example.test/a.xlsx\n2. *Open:* https://example.test/b" in text, text[-300:]
    assert text.index("Download Excel") > text.index("SO Outstanding")


def test_s3_a_cut_summary_page_slices_answers_by_printed_row(monkeypatch):
    """Two company rows per code: `answers` holds both rows of every printed code, so the last
    printed position resolves to a row, not past the end."""
    _set_gate(monkeypatch, True)
    out = _summary_page(links=False)
    last = out["set_window"]["last"]
    assert 0 < last < 50
    answers = out["answers"]
    assert len(answers) == 2 * last, (len(answers), last)
    assert answers[-1]["title"] == f"SRTWC{last - 1:03d}"
    assert {a["title"] for a in answers} == {f"SRTWC{i:03d}" for i in range(last)}


# --------------------------------------------------------------------------- #
# R2: the other-brands offer without counts
# --------------------------------------------------------------------------- #

BRANDS = [{"brand": "Bravat", "count": 79}, {"brand": "Cabana", "count": 57}]


def test_r2_other_brands_line_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    assert A.other_brands_line(BRANDS, {"stock": True}) == (
        "Other brands with stock: Bravat 79, Cabana 57. Name one to see them."
    )


def test_r2_other_brands_line_gate_on_drops_the_counts(monkeypatch):
    _set_gate(monkeypatch, True)
    line = A.other_brands_line(BRANDS, {"stock": True})
    assert line == "Other brands with stock: Bravat, Cabana. Name one to see them."
    assert not re.search(r"\d", line)


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_r2_other_brands_line_ms_zh_carries_no_counts(monkeypatch, lang):
    _set_gate(monkeypatch, True)
    line = A.other_brands_line(BRANDS, {"stock": True})
    assert not re.search(r"\d", Localizer(lang, defaults(lang)).reply(line))


def _page_with_brands():
    items = [
        {"title": f"SRTWC{i:03d}", "fields": [_f("Product Code", f"SRTWC{i:03d}"), _f("List Price", "MYR 275.00")]}
        for i in range(5)
    ]
    env = {
        "result_type": "products",
        "intro": "x",
        "items": items,
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }
    pred = {
        "qualifying_total": 5,
        "offset": 0,
        "class_labels": ["water closet"],
        "require": {"stock": True},
        "description": [],
        "other_brands": BRANDS,
    }
    return fetch.output_structurer(env, {"tool": "crm_master_products_list", "semantic_input": {}, "predicate": pred})["response"]


def test_r2_set_page_offer_gate_states(monkeypatch):
    _set_gate(monkeypatch, False)
    assert "Other brands with stock: Bravat 79, Cabana 57. Name one to see them." in _page_with_brands()
    _set_gate(monkeypatch, True)
    on = _page_with_brands()
    assert "Other brands with stock: Bravat, Cabana. Name one to see them." in on
    assert "79" not in on and "57" not in on


# --------------------------------------------------------------------------- #
# Round 5: the end of a zero-qualifying list, and the page one header
# --------------------------------------------------------------------------- #


def test_u1_more_after_the_last_miss_page_says_that_is_all(session_factory, monkeypatch, stub_parser, stub_access, world):
    _set_gate(monkeypatch, True)
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    chat.say("any wash basin has stock", _set_ask())
    second = chat.say("more", _more_verdict())
    assert "showing 51 to 55)" in second.split("\n\n")[0]
    for _ in range(2):
        again = chat.say("more", _more_verdict())
        assert "That is all 55." in again, again[:300]
        assert again.startswith("Here's what you want: wash basins (55)"), again[:200]
        assert "with stock" not in again.split("\n\n")[0]
        assert "But no stock matched these." in again
        assert again.index("That is all 55.") < again.index("But no stock matched these.")
        assert not _bullets(again), again
        assert "showing 1 to 50" not in again and 'Reply "more"' not in again


def test_u1_miss_list_headers_never_carry_the_leg(session_factory, monkeypatch, stub_parser, stub_access, world):
    """55 products with NO stock: "with stock (55 ...)" would be a false count (card R4)."""
    _seed_zero_stock(world)
    chat = make_chat(session_factory, monkeypatch, stub_parser, stub_access, world, mode="detailed")
    _set_gate(monkeypatch, False)
    off = chat.say("any wash basin has stock", _set_ask())
    assert off.startswith("Here's what you want: wash basins (55)\n"), off[:120]
    _set_gate(monkeypatch, True)
    one = chat.say("any wash basin has stock", _set_ask())
    two = chat.say("more", _more_verdict())
    end = chat.say("more", _more_verdict())
    heads = [one.split("\n")[0], two.split("\n")[0]]
    assert heads == [
        "Here's what you want: wash basins (55, showing 1 to 50)",
        "Here's what you want: wash basins (55, showing 51 to 55)",
    ], heads
    assert end.startswith("Here's what you want: wash basins (55)"), end[:160]
    for page in (one, two, end):
        assert "with stock" not in page.split("\n\n")[0], page[:160]
        assert "But no stock matched these." in page, page[-200:]
    assert end.index("That is all 55.") < end.index("But no stock matched these.")
