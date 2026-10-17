"""CHAT-COMPOSER Lists slice red tests, L1: the code-lookup list (a multi-match answered as cards).

Spec: `documentation/plans/chatbot/chat-composer-seam-design-lists.md` section 1 (L1), LD1, LD7 (a code list needing more than one message never states a total),
section 7. Gate on: the cards are cut on a card boundary to one message, the intro gains the range,
the page ends with the "more" line, a carry of the matched product ids lets "more" print the rest.
Gate off: today's bytes. No implementation exists when these are written.
"""
from __future__ import annotations

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import LANE_LABELS, Localizer, defaults
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_composers_stock_xd import _set_gate

BUDGET = 3900
INTRO = "Here are the matching products."
FOOT = "_Data last updated: 03/10/2026 18:42:43_"
NARROW = "Those are the first 20 matches. Send more of the code to narrow it."
TOOL = "crm_master_products_list"


def _f(label, value):
    return {"label": label, "value": value}


def _uuid(code, k=0):
    return f"00000000-0000-4000-8000-{abs(hash(code)) % 10**8:08d}{k:04d}"


def _code(i):
    return f"SRTWC{i:03d}"


def _card(code, desc, company=None):
    fields = [_f("Product Code", code), _f("Description", desc), _f("List Price", "MYR 275.00")]
    if company:
        fields.insert(0, _f("Company", company))
    return {"title": code, "fields": fields}


def _env(items):
    return {
        "result_type": "products",
        "intro": INTRO,
        "items": items,
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }


def _entities(codes, twins=False):
    out = []
    for c in codes:
        for k in range(2 if twins else 1):
            out.append({"entity_type": "product", "uuid": _uuid(c, k), "code": c, "canonical_code": c})
    return out


def _run(n, *, desc="x" * 330, tool=TOOL, entities="auto", items=None, localizer=None, predicate=None, sem=None):
    rows = items if items is not None else [_card(_code(i), desc) for i in range(n)]
    codes = list(dict.fromkeys(_code_of(r) for r in rows))
    ctx = {
        "tool": tool,
        "semantic_input": sem or {},
        "entities": _entities(codes) if entities == "auto" else entities,
    }
    if localizer is not None:
        ctx["localizer"] = localizer
    if predicate is not None:
        ctx["predicate"] = predicate
    return fetch.output_structurer(_env(rows), ctx)


def _code_of(row):
    return next(f["value"] for f in row["fields"] if f["label"] == "Product Code")


def _blocks(text):
    return [b for b in text.split("\n\n") if "*Product Code:*" in b]


def _numbers(text):
    import re

    return [int(m) for m in re.findall(r"^(\d+)\. \*Product Code:\*", text, re.MULTILINE)]


# --------------------------------------------------------------------------- #
# Gate off: today's bytes
# --------------------------------------------------------------------------- #


def test_gate_off_code_list_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    out = _run(13)
    blocks = [
        f"{i + 1}. *Product Code:* {_code(i)}\n*Description:* {'x' * 330}\n*List Price:* MYR 275.00" for i in range(13)
    ]
    assert out["response"] == "\n\n".join([INTRO, *blocks, FOOT])
    assert "set_window" not in out
    assert len(out["answers"]) == 13


# --------------------------------------------------------------------------- #
# Gate on: cut to one message
# --------------------------------------------------------------------------- #


def test_cut_to_one_message(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _run(13)
    text = out["response"]
    assert len(text) <= BUDGET, len(text)
    assert text.splitlines()[0] == "Here are the matching products (showing 1 to 9)."
    assert _numbers(text) == list(range(1, 10))
    assert text.endswith('Reply "more" to see the next 4.\n\n' + FOOT), text[-160:]
    assert len(out["answers"]) == 9


def test_fits_is_unchanged(monkeypatch):
    items_short = [_card(_code(i), "d") for i in range(5)]
    _set_gate(monkeypatch, False)
    off = _run(5, items=items_short)
    _set_gate(monkeypatch, True)
    on = _run(5, items=items_short)
    assert on["response"] == off["response"]
    assert "set_window" not in on
    assert on["answers"] == off["answers"]


def test_window_carry_shape(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _run(13)
    win = out["set_window"]
    assert (win["first"], win["last"], win["total"], win["next"]) == (1, 9, 13, 4)
    assert win["carry"] == {
        "code_list": True,
        "set_noun": "matching products",
        "code_ids": [{"code": _code(i), "ids": [_uuid(_code(i))]} for i in range(13)],
        "total": 13,
        "shown": 9,
    }
    assert "capped" not in win["carry"]


def test_twins_ride_together(monkeypatch):
    """Two companies' rows of one code are never split by the cut, and share one code_ids entry."""
    _set_gate(monkeypatch, True)
    items = []
    for i in range(13):
        for co in ("Sorento", "Mocha"):
            items.append(_card(_code(i), "x" * 150, company=co))
    out = _run(0, items=items, entities=_entities([_code(i) for i in range(13)], twins=True))
    win = out["set_window"]
    last = win["last"]
    assert 0 < last < 13
    answers = out["answers"]
    assert len(answers) == 2 * last
    assert [_code_of(a) for a in answers].count(_code(last - 1)) == 2
    carry = win["carry"]
    assert len(carry["code_ids"]) == 13
    assert carry["code_ids"][0] == {"code": _code(0), "ids": [_uuid(_code(0), 0), _uuid(_code(0), 1)]}
    assert carry["shown"] == last


def test_no_page_without_ids_or_contiguity(monkeypatch):
    """A printed code with no product entity, or codes A, B, A: the page is not offered."""
    _set_gate(monkeypatch, False)
    base_missing = _run(13, entities=_entities([_code(i) for i in range(12)]))["response"]
    items_aba = [_card(_code(i % 2 if i < 3 else i), "x" * 330) for i in range(13)]
    base_aba = _run(0, items=items_aba)["response"]
    _set_gate(monkeypatch, True)
    missing = _run(13, entities=_entities([_code(i) for i in range(12)]))
    assert missing["response"] == base_missing and "set_window" not in missing
    aba = _run(0, items=items_aba)
    assert aba["response"] == base_aba and "set_window" not in aba


def test_order_list_is_not_a_code_list(monkeypatch):
    _set_gate(monkeypatch, False)
    off = _run(13, tool="crm_order_management_orders_list")
    _set_gate(monkeypatch, True)
    on = _run(13, tool="crm_order_management_orders_list")
    assert on["response"] == off["response"]
    assert "set_window" not in on


# --------------------------------------------------------------------------- #
# Range helper and localization
# --------------------------------------------------------------------------- #


def test_ranged_helper():
    from app.services.chatbot.composers import sets

    assert sets.RANGE_CAPPED == "(showing {first} to {last})"
    assert sets.ranged(INTRO, "(showing 1 to 9)") == "Here are the matching products (showing 1 to 9)."
    assert sets.ranged("Stock details found for the requested products.", "(showing 1 to 9)") == (
        "Stock details found for the requested products (showing 1 to 9)."
    )
    assert sets.ranged("以下是匹配的产品。", "（显示第 1 至 9 个）") == "以下是匹配的产品（显示第 1 至 9 个）。"
    assert sets.ranged("no full stop", "(1)") == "no full stop (1)"


@pytest.mark.parametrize(
    "lang,first_line",
    [
        ("zh", "以下是匹配的产品（显示第 1 至 9 个）。"),
        ("ms", "Berikut ialah produk yang sepadan (menunjukkan 1 hingga 9)."),
    ],
)
def test_header_ms_zh(monkeypatch, lang, first_line):
    _set_gate(monkeypatch, True)
    out = _run(13, localizer=Localizer(lang, defaults(lang)))
    assert out["response"].splitlines()[0] == first_line


def test_range_key_gate_off_absent_from_defaults(monkeypatch):
    _set_gate(monkeypatch, False)
    for lang in ("ms", "zh"):
        assert "(showing {first} to {last})" not in defaults(lang)
    _set_gate(monkeypatch, True)
    assert "(showing {first} to {last})" in LANE_LABELS
    for lang in ("ms", "zh"):
        assert "(showing {first} to {last})" in defaults(lang)


# --------------------------------------------------------------------------- #
# page_the_set: the code_list branch reads no database
# --------------------------------------------------------------------------- #


class _NoDb:
    def __getattr__(self, name):  # any use of the session fails the test
        raise AssertionError(f"page_the_set touched the database: {name}")


def _carry(shown, n=13, **extra):
    return {
        "set_key": {
            "code_list": True,
            "set_noun": "matching products",
            "code_ids": [{"code": _code(i), "ids": [_uuid(_code(i))]} for i in range(n)],
            "total": n,
            "shown": shown,
            **extra,
        }
    }


def test_page_the_set_code_list(monkeypatch):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    predicate, ids = turn_runtime.page_the_set(_NoDb(), _carry(9))
    assert ids == [_uuid(_code(i)) for i in range(9, 13)]
    assert predicate["qualifying_total"] == 13
    assert predicate["offset"] == 9
    assert predicate["set_noun"] == "matching products"
    assert predicate["next_carry"]["set_key"]["shown"] == 13
    assert not predicate.get("exhausted")

    predicate, ids = turn_runtime.page_the_set(_NoDb(), _carry(13))
    assert ids == []
    assert predicate["exhausted"] is True
    assert predicate["next_carry"]["set_key"]["shown"] == 13


# --------------------------------------------------------------------------- #
# LD7: a code list that needs more than one message never states a total
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n", [13, 19, 20, 25])
def test_a_cut_list_never_states_a_total(monkeypatch, n):
    _set_gate(monkeypatch, True)
    out = _run(n)
    text = out["response"]
    assert text.splitlines()[0] == "Here are the matching products (showing 1 to 9)."
    assert f"{n}," not in text.split("\n\n")[0]
    assert f'Reply "more" to see the next {n - 9}.' in text
    carry = out["set_window"]["carry"]
    assert "capped" not in carry
    assert carry["shown"] == 9 and len(carry["code_ids"]) == n


def test_a_code_list_carry_survives_page_the_set_total_less(monkeypatch):
    from app.services.chatbot import turn_runtime

    _set_gate(monkeypatch, True)
    predicate, ids = turn_runtime.page_the_set(_NoDb(), _carry(9, n=20))
    assert ids == [_uuid(_code(i)) for i in range(9, 20)]
    assert predicate["offset"] == 9
    assert predicate["next_carry"]["set_key"]["shown"] == 20
    assert "capped" not in predicate["next_carry"]["set_key"]
    predicate, ids = turn_runtime.page_the_set(_NoDb(), _carry(20, n=20))
    assert ids == [] and predicate["exhausted"] is True


def _list_predicate(total, offset, **extra):
    return {
        "require": {},
        "qualifying_total": total,
        "truncated": False,
        "unrecognized_terms": [],
        "class_labels": [],
        "set_noun": "matching products",
        "code_list": True,
        "offset": offset,
        **extra,
    }


def _narrow(n):
    return f"Those are the first {n} matches. Send more of the code to narrow it."


@pytest.mark.parametrize("total", [13, 20])
def test_page_two_header_and_end(monkeypatch, total):
    """Pages 2+ keep the Sets header with a total-less range; the last page ends with the narrow
    sentence stating the codes listed, never "That is all N."."""
    _set_gate(monkeypatch, True)
    items = [_card(_code(i), "d") for i in range(9, total)]
    out = _run(0, items=items, predicate=_list_predicate(total, 9))
    text = out["response"]
    assert text.splitlines()[0] == f"Here's what you want: matching products (showing 10 to {total})"
    assert _numbers(text) == list(range(10, total + 1))
    lines = [ln for ln in text.strip().split("\n") if ln]
    assert lines[-1] == FOOT and lines[-2] == _narrow(total), lines[-3:]
    assert "That is all" not in text
    assert 'Reply "more"' not in text


@pytest.mark.parametrize("total", [13, 20])
def test_past_the_end_is_the_narrow_sentence_alone(monkeypatch, total):
    _set_gate(monkeypatch, True)
    out = _run(0, items=[], predicate=_list_predicate(total, total, exhausted=True))
    assert out["response"].strip() == _narrow(total)


def test_capped_sentence_catalogued_gate_on_only(monkeypatch):
    key = "Those are the first {n} matches. Send more of the code to narrow it."
    assert key in LANE_LABELS
    assert "(showing {first} to {last})" in LANE_LABELS
    for k in (key, "(showing {first} to {last})"):
        for lang in ("ms", "zh"):
            assert LANE_LABELS[k][lang] and LANE_LABELS[k][lang] != k
    _set_gate(monkeypatch, True)
    for lang in ("ms", "zh"):
        assert Localizer(lang, defaults(lang)).reply(NARROW) != NARROW
    _set_gate(monkeypatch, False)
    for lang in ("ms", "zh"):
        assert Localizer(lang, defaults(lang)).reply(NARROW) == NARROW
        assert "(showing {first} to {last})" not in defaults(lang)
