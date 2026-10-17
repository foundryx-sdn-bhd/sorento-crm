"""CHAT-COMPOSER Sets review fix round 1: red tests.

B1  `with_set_more` pages only a bare "more": a real new ask beside `continuation` true is not paged.
B2  an exhausted list answers a bare "more" with "That is all N."; a real follow-up does not.
B3  a cut page ends only where the product CODE changes (two company rows of one code stay together).
S1  one page budget, the repo's 3900, also after ms / zh localization.
S2  grouped set pages are cut too.
S3  a cut page with a skipped duplicate status row keeps `answers` and the range in step with the rows.
N1  a dealer quantity page prints the "more" line before the quantity question.
N2  `page_the_set` with size 0 / None is the stacked base's page (gate-off identity).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_composers_stock_xd import _set_gate

BUDGET = 3900
CARRY = {"set_key": {"shown": 50, "total": 182, "domain": "inventory"}}
EXHAUSTED_CARRY = {"set_key": {"shown": 182, "total": 182, "domain": "inventory"}}


def _verdict(**kw):
    base = {
        "continuation": True,
        "entities": [],
        "requested_attributes": [],
        "top_n": None,
        "reference_positions": [],
        "asks": [],
    }
    return {**base, **kw}


# --------------------------------------------------------------------------- #
# B1: a real new ask beside continuation is not "more"
# --------------------------------------------------------------------------- #

NEW_ASKS = {
    "domain_switch": {"domain_hint": "incoming", "domain_in_message": True},
    "date_window": {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"},
    "request_for_help": {"query_type": "request_for_help"},
    "brand_query": {"query_brands": ["Bravat"]},
    "group_by": {"group_by": "warehouse"},
    "order_status": {"order_status": "outstanding"},
    "broaden_axis": {"broaden_axis": "brand"},
}


@pytest.mark.parametrize("shape", list(NEW_ASKS), ids=list(NEW_ASKS))
def test_b1_a_new_ask_beside_continuation_is_not_more(monkeypatch, shape):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    v = _verdict(**NEW_ASKS[shape])
    assert turn_runtime.with_set_more(v, carried=CARRY) == v


@pytest.mark.parametrize(
    "extra",
    [{}, {"domain_hint": None}, {"domain_hint": "inventory"}, {"domain_hint": "inventory", "domain_in_message": True}],
    ids=["nothing", "domain_none", "same_domain", "same_domain_in_message"],
)
def test_b1_a_bare_more_still_pages(monkeypatch, extra):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    out = turn_runtime.with_set_more(_verdict(**extra), carried=CARRY)
    assert out["set_continue"] is True and out["top_n"] == 50 and out["reference_positions"] == []


# --------------------------------------------------------------------------- #
# B2: the end of the list
# --------------------------------------------------------------------------- #


def test_b2_bare_more_on_an_exhausted_list_is_answered_by_the_end_of_list_reply(monkeypatch):
    """The verdict reaches the existing exhausted route (`set_continue` + `top_n`), which prints
    "That is all N." with no tool call."""
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    out = turn_runtime.with_set_more(_verdict(), carried=EXHAUSTED_CARRY)
    assert out["set_continue"] is True and out["top_n"] == 50


@pytest.mark.parametrize("shape", list(NEW_ASKS), ids=list(NEW_ASKS))
def test_b2_a_real_follow_up_after_a_complete_list_is_not_the_end_of_list(monkeypatch, shape):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    v = _verdict(**NEW_ASKS[shape])
    assert turn_runtime.with_set_more(v, carried=EXHAUSTED_CARRY) == v


def _fake_outcome(monkeypatch, n):
    import app.services.product_predicate_service as pps

    cands = [{"product_id": f"p{i:03d}", "product_code": f"SRTWC{i:03d}"} for i in range(n)]
    monkeypatch.setattr(
        pps,
        "resolve_product_set",
        lambda *a, **k: {"qualifying_total": n, "candidates": cands, "require": {"stock": True}, "truncated": False},
    )
    return cands


def test_b2_page_the_set_on_an_exhausted_carry_says_exhausted(monkeypatch):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    _fake_outcome(monkeypatch, 182)
    carry = {"set_key": {"require": {"stock": True}, "scope_terms": ["p trap"], "access_levels": [], "shown": 182, "total": 182}}
    predicate, ids = turn_runtime.page_the_set(None, carry, size=50)
    assert predicate["exhausted"] is True and ids == []


# --------------------------------------------------------------------------- #
# Page builders
# --------------------------------------------------------------------------- #


def _f(label, value):
    return {"label": label, "value": value}


def _env(items):
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": items,
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }


def _run(env, total, *, sem=None, offset=0):
    pred = {"qualifying_total": total, "offset": offset, "class_labels": ["water closet"], "require": {}, "description": []}
    return fetch.output_structurer(env, {"tool": "crm_master_products_list", "semantic_input": sem or {}, "predicate": pred})


def _twin_items(codes, desc_len):
    out = []
    for i in range(codes):
        for co in ("Sorento", "Mocha"):
            out.append(
                {
                    "title": f"SRTWC{i:03d}",
                    "fields": [
                        _f("Company", co),
                        _f("Product Code", f"SRTWC{i:03d}"),
                        _f("Description", "x" * desc_len),
                        _f("List Price", "MYR 275.00"),
                    ],
                }
            )
    return out


# --------------------------------------------------------------------------- #
# B3: a code's company rows stay together
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("desc_len", list(range(100, 141, 4)))
def test_b3_a_cut_page_never_ends_between_the_rows_of_one_code(monkeypatch, desc_len):
    _set_gate(monkeypatch, True)
    out = _run(_env(_twin_items(40, desc_len)), 90)
    text = out["response"]
    rows = re.findall(r"^\d+\. \*Company:\* (\w+)\n\*Product Code:\* (\w+)", text, re.MULTILINE)
    assert len(text) <= BUDGET, len(text)
    assert len(rows) % 2 == 0, f"page ends inside a code: {len(rows)} rows"
    assert rows[-2][1] == rows[-1][1]
    codes = len({c for _co, c in rows})
    assert out["set_window"]["last"] == codes
    assert text.startswith(f"Here's what you want: water closets (90, showing 1 to {codes})")
    assert out["set_window"]["next"] == min(50, 90 - codes)


def test_b3_page_two_starts_at_the_next_code(monkeypatch):
    """Page 1 listed K codes; page 2 (offset K) numbers from K + 1 rows of the next code."""
    _set_gate(monkeypatch, True)
    first = _run(_env(_twin_items(40, 120)), 90)
    k = first["set_window"]["last"]
    second = _run(_env(_twin_items(40, 120)[2 * k :]), 90, offset=k)
    assert re.search(rf"showing {k + 1} to ", second["response"])
    assert second["set_window"]["first"] == k + 1


# --------------------------------------------------------------------------- #
# S1: one budget
# --------------------------------------------------------------------------- #


def test_s1_one_page_budget_of_3900():
    from app.services.chatbot import engine
    from app.services.chatbot.composers import sets

    mcp_root = str(Path(__file__).resolve().parents[3] / "sorento_crm_mcp")
    if mcp_root not in sys.path:
        sys.path.insert(0, mcp_root)
    from sorento_crm_mcp import presenters

    assert sets.MESSAGE_MAX_CHARS == engine.WHATSAPP_MESSAGE_MAX_CHARS == presenters.WHATSAPP_MESSAGE_MAX_CHARS == BUDGET


@pytest.mark.parametrize("desc_len", [104, 116, 128])
@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_s1_a_cut_page_fits_one_message_after_localization(monkeypatch, lang, desc_len):
    _set_gate(monkeypatch, True)
    text = _run(_env(_twin_items(40, desc_len)), 90)["response"]
    assert len(text) <= BUDGET
    assert len(Localizer(lang, defaults(lang)).reply(text)) <= BUDGET


# --------------------------------------------------------------------------- #
# S2: grouped set pages
# --------------------------------------------------------------------------- #


def test_s2_a_grouped_set_page_is_cut_to_one_message(monkeypatch):
    _set_gate(monkeypatch, True)

    def item(i):
        return {
            "title": f"SRTWC{i:03d}",
            "fields": [_f("Product Code", f"SRTWC{i:03d}"), _f("Description", "x" * 120), _f("List Price", "MYR 275.00")],
        }

    items = [item(i) for i in range(50)]
    env = _env(items)
    env["groups"] = [
        {"label": "Warehouse A", "items": items[:25]},
        {"label": "Warehouse B", "items": items[25:]},
    ]
    out = _run(env, 182)
    text = out["response"]
    printed = len(re.findall(r"^\d+\. \*Product Code:\*", text, re.MULTILINE))
    assert len(text) <= BUDGET, len(text)
    assert 0 < printed < 50
    assert text.startswith(f"Here's what you want: water closets (182, showing 1 to {printed})")
    assert out["set_window"]["last"] == printed
    assert len(out["answers"]) == printed


# --------------------------------------------------------------------------- #
# S3: a skipped duplicate status row
# --------------------------------------------------------------------------- #


def test_s3_cut_page_with_a_skipped_duplicate_status_row_is_in_step(monkeypatch):
    _set_gate(monkeypatch, True)
    items = []
    for i in range(50):
        for co in ("Sorento", "Mocha"):
            items.append(
                {
                    "title": f"SRTWC{i:03d}",
                    "fields": [_f("Company", co), _f("Product Code", f"SRTWC{i:03d}")],
                    "flags": {"discontinued": True},
                    "alternatives": [f"ALT{i:03d}{j:02d}-LONGCODE" for j in range(8)],
                }
            )
    out = _run(_env(items), 150, sem={"requested_attributes": ["discontinued"]})
    text = out["response"]
    printed = re.findall(r"^(SRTWC\d+): ", text, re.MULTILINE)
    assert len(text) <= BUDGET
    assert out["set_window"]["last"] == len(printed)
    assert text.startswith(f"Here's what you want: water closets (150, showing 1 to {len(printed)})")
    answered = {a["title"] for a in out["answers"]}
    assert answered == set(printed)


# --------------------------------------------------------------------------- #
# N1: the question stays last
# --------------------------------------------------------------------------- #


def _dealer_page():
    items = [{"title": f"AP{i:04d}A", "fields": [], "flags": {"needs_quantity": True, "branch": None}} for i in range(50)]
    env = {
        "result_type": "stock_availability",
        "intro": "How many units do you need?",
        "items": items,
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }
    pred = {"qualifying_total": 240, "offset": 0, "class_labels": ["kitchen sink"], "require": {"stock": True}, "description": []}
    return fetch.output_structurer(env, {"tool": "crm_inventory_stock_balance_list", "semantic_input": {}, "predicate": pred})["response"]


def test_n1_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    text = _dealer_page()
    assert text.endswith("50. AP0049A\n\nHow many units do you need?")
    assert 'Reply "more"' not in text


def test_n1_more_line_comes_before_the_quantity_question(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _dealer_page()
    assert text.endswith('50. AP0049A\n\nReply "more" to see the next 50.\n\nHow many units do you need?'), text[-200:]


# --------------------------------------------------------------------------- #
# N2: page_the_set size 0 is the base page
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gate", [False, True])
@pytest.mark.parametrize(
    "size,expect_shown",
    [(None, 50), (0, 50), (10, 10), (50, 50), (80, 50)],
    ids=["none", "zero", "ten", "fifty", "eighty"],
)
def test_n2_page_the_set_sizes_are_the_base_page(monkeypatch, gate, size, expect_shown):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, gate)
    cands = _fake_outcome(monkeypatch, 182)
    carry = {"set_key": {"require": {"stock": True}, "scope_terms": ["p trap"], "access_levels": [], "shown": 0, "total": 182}}
    predicate, ids = turn_runtime.page_the_set(None, carry, size=size)
    assert ids == [c["product_id"] for c in cands[:expect_shown]]
    assert predicate["next_carry"]["set_key"]["shown"] == expect_shown
    assert "offset" not in predicate and "exhausted" not in predicate
