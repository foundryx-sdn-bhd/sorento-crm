"""CHAT-COMPOSER S6 red tests (product info / spec / price): an empty un-asked field is left out (Q3,
OI-4: a FILLED Dimensions stays), an asked attribute with no value says `No <attr> on record for
<product>.` once per product (R4), and the generic key must not translate a fit none line.

Goldens are literals captured from main code on 17 Oct (S3a landed, gate off).
"""
from __future__ import annotations

import re

import pytest

from app.main import app  # noqa: F401  (first app import resolves the guards cycle)
from app.services.chatbot.label_catalog import Localizer, defaults
from app.services.chatbot.lanes.business import fetch
from tests.chatbot.test_composers_stock_xd import _set_gate

DESC = "SORENTO BIDET C/W TWO WAY TAP WITH HOLDER (G1/2 HOSE BIB TAP) SRT79-SS-BL"
FOOT = "\n\n_Data last updated: 03/10/2026 18:42:43_"


def _f(label, value, key=None):
    d = {"label": label, "value": value}
    if key:
        d["key"] = key
    return d


def _env(items, vocab=None):
    return {
        "result_type": "products",
        "intro": "Here are the matching products.",
        "items": items,
        "spec_vocabulary": vocab or {},
        "has_result": True,
        "last_updated_at": "2026-10-03T18:42:43",
    }


def _item(code, *, price="MYR 275.00", dim="Not defined", desc=None, company=None, specs=()):
    fields = []
    if company:
        fields.append(_f("Company", company, "company_name"))
    fields.append(_f("Product Code", code))
    if desc:
        fields.append(_f("Description", desc))
    fields += [_f("List Price", price), _f("Dimensions", dim)]
    fields += [_f(label, val, f"spec:{k}") for k, label, val in specs]
    return {"title": code, "fields": fields}


def _out(env, attrs=None, hidden=None):
    ctx = {"semantic_input": {"requested_attributes": attrs or []}}
    if hidden:
        ctx["access"] = {"hidden_spec_keys": hidden}
    return fetch.output_structurer(env, ctx)


def _resp(env, attrs=None, hidden=None):
    return _out(env, attrs, hidden)["response"]


# --------------------------------------------------------------------------- #
# TRAIN cp0-245d689a0c: a price ask
# --------------------------------------------------------------------------- #


PRICE_ON = (
    "Here are the matching products.\n\n1. *Product Code:* SRT79-SS-BL\n"
    f"*Description:* {DESC}\n*List Price:* MYR 275.00{FOOT}"
)
PRICE_OFF = (
    "Here are the matching products.\n\n1. *Product Code:* SRT79-SS-BL\n"
    f"*Description:* {DESC}\n*List Price:* MYR 275.00\n*Dimensions:* Not defined{FOOT}"
)


def test_price_answer_cp0_245d689a0c_gate_off(monkeypatch):
    _set_gate(monkeypatch, False)
    assert _resp(_env([_item("SRT79-SS-BL", desc=DESC)]), ["price"]) == PRICE_OFF


def test_price_answer_cp0_245d689a0c_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    assert _resp(_env([_item("SRT79-SS-BL", desc=DESC)]), ["price"]) == PRICE_ON


@pytest.mark.parametrize("gate", [False, True])
def test_price_answer_keeps_filled_dimensions(monkeypatch, gate):
    """OI-4 (a), owner D12: a FILLED Dimensions stays (cp0-7ff78f4770 shape)."""
    _set_gate(monkeypatch, gate)
    text = _resp(_env([_item("SRT79-SS-BL", dim="150.00 x 150.00 x - mm")]), ["price"])
    assert "*Dimensions:* 150.00 x 150.00 x - mm" in text


def test_price_missing_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    text = _resp(_env([_item("X1", price="Not defined")]), ["price"])
    assert text == (
        "Here are the matching products.\n\n1. *Product Code:* X1\n*List Price:* Not defined\n*Dimensions:* Not defined" + FOOT
    )


def test_price_missing_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _resp(_env([_item("X1", price="Not defined")]), ["price"])
    assert "Not defined" not in text
    assert "1. *Product Code:* X1\nNo list price on record for X1." in text


def test_dimensions_missing_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _resp(_env([_item("X1")]), ["size"])
    assert "No dimensions on record for X1." in text
    assert "*Dimensions:*" not in text


def test_no_ask_card_drops_not_defined_gate_on(monkeypatch):
    specs = [("wattage", "Wattage", "60 W"), ("color", "Color", "White")]
    _set_gate(monkeypatch, False)
    off = _resp(_env([_item("X1", specs=specs)], {"wattage": "Wattage", "color": "Color"}))
    assert "*Dimensions:* Not defined" in off and "*Specs:* Wattage: 60 W, Color: White" in off
    _set_gate(monkeypatch, True)
    on = _resp(_env([_item("X1", specs=specs)], {"wattage": "Wattage", "color": "Color"}))
    assert "Not defined" not in on
    assert "*Specs:* Wattage: 60 W, Color: White" in on


# --------------------------------------------------------------------------- #
# R4: an asked spec with no value
# --------------------------------------------------------------------------- #

VOCAB = {"weight": "Weight"}


def _blocks(text):
    return text.split("\n\n")


def test_spec_miss_gate_off_golden(monkeypatch):
    _set_gate(monkeypatch, False)
    two = _resp(_env([_item("A1"), _item("B2")], VOCAB), ["weight"])
    assert "*Weight:* not recorded for A1, B2" in two
    seven = _resp(_env([_item(f"C{i}") for i in range(7)], VOCAB), ["weight"])
    assert "*Weight:* not recorded for C0, C1, C2, C3, C4 (+2 more)" in seven


def test_spec_miss_one_line_per_product_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    text = _resp(_env([_item("A1"), _item("B2")], VOCAB), ["weight"])
    blocks = _blocks(text)
    assert blocks[1].startswith("1. *Product Code:* A1") and blocks[1].endswith("\nNo weight on record for A1."), blocks
    assert blocks[2].startswith("2. *Product Code:* B2") and blocks[2].endswith("\nNo weight on record for B2."), blocks
    assert "not recorded" not in text


def test_spec_miss_only_the_product_without_a_value_gate_on(monkeypatch):
    _set_gate(monkeypatch, True)
    env = _env([_item("A1", specs=[("weight", "Weight", "5 kg")]), _item("B2")], VOCAB)
    blocks = _blocks(_resp(env, ["weight"]))
    assert "*Weight:* 5 kg" in blocks[1] and "No weight" not in blocks[1]
    assert blocks[2].endswith("\nNo weight on record for B2.")


def test_two_company_rows_each_carry_line(monkeypatch):
    _set_gate(monkeypatch, True)
    env = _env([_item("A1", company="Sorento"), _item("A1", company="Other")], VOCAB)
    blocks = _blocks(_resp(env, ["weight"]))
    assert blocks[1].startswith("1. *Company:* Sorento") and blocks[1].endswith("\nNo weight on record for A1.")
    assert blocks[2].startswith("2. *Company:* Other") and blocks[2].endswith("\nNo weight on record for A1.")


@pytest.mark.parametrize("gate", [False, True])
def test_description_ask_prints_no_line(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    text = _resp(_env([_item("A1")]), ["description"])
    assert "No description on record" not in text


@pytest.mark.parametrize("gate", [False, True])
def test_hidden_spec_line_unchanged(monkeypatch, gate):
    _set_gate(monkeypatch, gate)
    text = _resp(_env([_item("A1")], VOCAB), ["weight"], hidden=["weight"])
    assert "*Weight:* not available" in text and text.count("not available") == 1


def test_answers_state_keeps_identity(monkeypatch):
    _set_gate(monkeypatch, True)
    out = _out(_env([_item("A1", company="Sorento"), _item("A1", company="Other")], VOCAB), ["weight"])
    for ans, company in zip(out["answers"], ("Sorento", "Other")):
        labels = {f["label"]: f["value"] for f in ans["fields"]}
        assert labels["Product Code"] == "A1" and labels["Company"] == company


# --------------------------------------------------------------------------- #
# ms / zh, and the OI-1 style guard on the fit none lines
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line,ms,zh",
    [
        ("No weight on record for X1.", "Tiada weight direkodkan untuk X1.", "没有 X1 的weight记录。"),
        (
            "No list price on record for X1.",
            "Tiada harga senarai direkodkan untuk X1.",
            "没有 X1 的标价记录。",
        ),
        (
            "No dimensions on record for X1.",
            "Tiada dimensi direkodkan untuk X1.",
            "没有 X1 的尺寸记录。",
        ),
    ],
)
def test_missing_lines_translate(monkeypatch, line, ms, zh):
    _set_gate(monkeypatch, True)
    assert Localizer("ms", defaults("ms")).reply(line) == ms
    assert Localizer("zh", defaults("zh")).reply(line) == zh


def test_fit_none_lines_translation_unchanged():
    from app.services import product_find

    labels = product_find._kind_labels()
    assert labels, "the fit catalogue has entries"
    for kind in product_find.KINDS:
        for fn in (product_find.none_line, product_find.no_host_line):
            sentence = fn(kind)
            line = sentence.format(code="SRTWC286-SH")
            for lang in ("ms", "zh"):
                got = Localizer(lang, defaults(lang)).reply(line)
                entry = labels.get(sentence)
                if entry is not None:
                    assert got == entry[lang].format(code="SRTWC286-SH"), (kind, lang, got)
                assert " on record for " not in got, got
    assert Localizer("ms", defaults("ms")).reply("No seat cover is on record for SRTWC286-SH.") == (
        "Tiada penutup tempat duduk direkodkan untuk SRTWC286-SH."
    )


def test_s6_output_clean(monkeypatch):
    _set_gate(monkeypatch, True)
    texts = [
        _resp(_env([_item("SRT79-SS-BL", desc=DESC)]), ["price"]),
        _resp(_env([_item("X1", price="Not defined")]), ["price"]),
        _resp(_env([_item("A1"), _item("B2")], VOCAB), ["weight"]),
        _resp(_env([_item(f"C{i}") for i in range(7)], VOCAB), ["weight"]),
    ]
    for t in texts:
        assert "not recorded for" not in t and "(+" not in t and "Not defined" not in t, t
        assert " · " not in t
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", t)
