"""CHAT-COMPOSER Sets slice red tests (the set reply): a page is up to 50 products AND at most one
WhatsApp message (about 4,000 characters including the "more" line), the "more" line, the real
"showing X to Y" range, no brand / size count lines, the end of the list.

Stacked on #1744 (SET-STOCK-RENDER), so gate OFF already lists the first 50 products. Goldens are
literals captured from this base (45b104f35 + stacked code 5761ec0b4), gate off.
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business import answer as A
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_composers_stock_xd import _set_gate

MORE_50 = 'Reply "more" to see the next 50.'
FOOT = "_Data last updated: 03/10/2026 18:42:43_"
MAX_CHARS = 4000


def _f(label, value):
    return {"label": label, "value": value}


def _item(i, desc="d"):
    return {
        "title": f"SRTWC{i:03d}",
        "fields": [_f("Product Code", f"SRTWC{i:03d}"), _f("Description", desc), _f("List Price", "MYR 275.00")],
    }


def _env(n, desc="d", start=0):
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": [_item(start + i, desc) for i in range(n)],
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }


def _run(n, total, *, offset=0, desc="d", predicate=None, sem=None):
    pred = {
        "qualifying_total": total,
        "offset": offset,
        "class_labels": ["water closet"],
        "require": {},
        "description": [
            {"key": "trap_type", "label": "Trap", "value": "P trap", "kind": "enum"},
            {"key": "class", "value": "Water Closet", "kind": "enum"},
        ],
        **(predicate or {}),
    }
    ctx = {"tool": "crm_master_products_list", "semantic_input": sem or {}, "predicate": pred}
    return fetch.output_structurer(_env(n, desc), ctx)


def _numbers(text):
    return [int(m) for m in re.findall(r"^(\d+)\. \*Product Code:\*", text, re.MULTILINE)]


# --------------------------------------------------------------------------- #
# Gate off: today's page (the stacked base)
# --------------------------------------------------------------------------- #


def test_gate_off_first_page_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    out = _run(50, 182)
    text = out["response"]
    assert text.startswith("Here's what you want: P trap water closets (182, showing 1 to 50)\n\n1. *Product Code:* SRTWC000\n")
    assert _numbers(text) == list(range(1, 51))
    assert "Reply \"more\"" not in text
    assert text.endswith(f"*List Price:* MYR 275.00\n\n{FOOT}")
    assert "set_window" not in out


def test_gate_off_long_blocks_are_not_cut(monkeypatch):
    """Gate off: 50 long blocks print in full (more than one message), today's bytes."""
    _set_gate(monkeypatch, False)
    text = _run(50, 182, desc="x" * 120)["response"]
    assert _numbers(text) == list(range(1, 51)) and len(text) > MAX_CHARS
    assert "showing 1 to 50)" in text.splitlines()[0]


def test_gate_off_second_page_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    text = _run(32, 182, offset=150)["response"]
    assert text.startswith("Here's what you want: P trap water closets (182, showing 151 to 182)\n\n151. *Product Code:*")
    assert "Reply \"more\"" not in text


# --------------------------------------------------------------------------- #
# Gate on: the "more" line
# --------------------------------------------------------------------------- #


def test_more_line_first_page(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _run(50, 182)
    text = out["response"]
    assert text.endswith(f"*List Price:* MYR 275.00\n\n{MORE_50}\n\n{FOOT}"), text[-200:]
    assert out["set_window"] == {"first": 1, "last": 50, "total": 182, "next": 50}


def test_more_line_counts_remaining(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _run(50, 182, offset=100)["response"]
    assert text.startswith("Here's what you want: P trap water closets (182, showing 101 to 150)")
    assert 'Reply "more" to see the next 32.' in text
    assert _numbers(text)[0] == 101


def test_second_page_numbering_and_header(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _run(50, 182, offset=50)
    text = out["response"]
    assert text.startswith("Here's what you want: P trap water closets (182, showing 51 to 100)\n\n51. *Product Code:*")
    assert _numbers(text) == list(range(51, 101))
    assert 'Reply "more" to see the next 50.' in text
    assert out["set_window"]["first"] == 51 and out["set_window"]["last"] == 100


def test_no_more_line_on_the_last_page(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _run(32, 182, offset=150)["response"]
    assert text.startswith("Here's what you want: P trap water closets (182, showing 151 to 182)")
    assert 'Reply "more"' not in text
    assert text.endswith(f"*List Price:* MYR 275.00\n\n{FOOT}")


def test_more_line_named_count(monkeypatch):
    """A named 10 of 240 shows 10; the line offers the next 50 (a "more" page is the full page)."""
    _set_gate(monkeypatch, True)
    out = _run(10, 240, sem={"top_n": 10})
    assert 'Reply "more" to see the next 50.' in out["response"]
    assert out["set_window"]["last"] == 10


def test_more_line_sits_above_other_brands_and_the_footer(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _run(50, 182, predicate={"other_brands": [{"brand": "Bravat", "count": 79}], "require": {"stock": True}})["response"]
    i_rows, i_more, i_brands, i_foot = (
        text.index("50. *Product Code:*"),
        text.index(MORE_50),
        text.index("Other brands"),
        text.index(FOOT),
    )
    assert i_rows < i_more < i_brands < i_foot


# --------------------------------------------------------------------------- #
# One page is at most one message
# --------------------------------------------------------------------------- #


def test_page_ends_at_one_message_when_blocks_are_long(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _run(50, 182, desc="x" * 120)
    text = out["response"]
    shown = _numbers(text)
    k = len(shown)
    assert 0 < k < 50, k
    assert shown == list(range(1, k + 1))
    assert len(text) <= MAX_CHARS, len(text)
    assert text.startswith(f"Here's what you want: P trap water closets (182, showing 1 to {k})")
    assert f'Reply "more" to see the next {min(50, 182 - k)}.' in text
    assert out["set_window"] == {"first": 1, "last": k, "total": 182, "next": min(50, 182 - k)}
    assert len(out["answers"]) == k


def test_page_cut_still_ends_with_the_footer_and_never_splits_a_block(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _run(50, 182, desc="x" * 120)["response"]
    assert text.endswith(FOOT)
    blocks = [b for b in text.split("\n\n") if re.match(r"^\d+\. \*Product Code:\*", b)]
    assert all(b.count("*List Price:*") == 1 for b in blocks)


def test_short_blocks_still_page_at_fifty(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _run(50, 182)["response"]
    assert len(_numbers(text)) == 50 and len(text) <= MAX_CHARS


# --------------------------------------------------------------------------- #
# R2 / R9: a set reply shows products, never brand or size count lines
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("gate", [False, True])
def test_set_reply_lists_products_and_no_count_lines(monkeypatch, gate):
    """TRAIN cp0-665ba3e897: 182 products, a brand breakdown known, no count named."""
    _set_gate(monkeypatch, gate)
    text = _run(
        50,
        182,
        predicate={"breakdown": {"key": "brand", "rows": [{"value": "Bravat", "count": 79}, {"value": "Cabana", "count": 57}]}},
    )["response"]
    assert _numbers(text), "a set reply never shows zero products"
    assert not re.search(r"^• .*: \d+", text, re.MULTILINE), text


@pytest.mark.parametrize("gate", [False, True])
def test_spec_set_rows_are_the_single_code_block(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    out = _run(3, 3)
    rows = out["response"].split("\n\n")[1:-1]
    assert rows[0] == "1. *Product Code:* SRTWC000\n*Description:* d\n*List Price:* MYR 275.00"
    single = fetch.output_structurer(_env(1), {"tool": "crm_master_products_list", "semantic_input": {}})["response"]
    assert single.split("\n\n")[1] == rows[0]


# --------------------------------------------------------------------------- #
# The end of the list
# --------------------------------------------------------------------------- #

EXHAUSTED = "Here's what you want: P trap water closets (182). That is all 182."


def test_end_of_list_text_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    header = A.build_set_header(182, 0, "P trap water closets", {}, offset=182, exhausted=True)
    assert header == EXHAUSTED
    for lang in ("ms", "zh"):
        assert Localizer(lang, defaults(lang)).reply(header) == EXHAUSTED


def test_end_of_list_text_is_same_in_english_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert A.build_set_header(182, 0, "P trap water closets", {}, offset=182, exhausted=True) == EXHAUSTED


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_that_is_all_translates_gate_on(monkeypatch, lang):
    _set_gate(monkeypatch, True)
    out = Localizer(lang, defaults(lang)).reply(EXHAUSTED)
    assert "That is all" not in out, out
    assert "182" in out and out != EXHAUSTED


@pytest.mark.parametrize(
    "lang,expected",
    [("ms", 'Balas "more" untuk lihat 50 seterusnya.'), ("zh", "回复 \"more\" 查看接下来的 50 个。")],
)
def test_more_line_ms_zh(monkeypatch, lang, expected):
    _set_gate(monkeypatch, True)
    assert Localizer(lang, defaults(lang)).reply(MORE_50) == expected


@pytest.mark.parametrize("lang", ["ms", "zh"])
def test_more_line_key_absent_gate_off(monkeypatch, lang):
    _set_gate(monkeypatch, False)
    assert Localizer(lang, defaults(lang)).reply(MORE_50) == MORE_50
