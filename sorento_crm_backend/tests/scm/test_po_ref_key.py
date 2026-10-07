"""SO-PO-REF U7: the one key fold, used at write AND lookup (`app/services/scm/po_ref.py`)."""
from __future__ import annotations

import pytest


def _fold(value):
    from app.services.scm.po_ref import po_ref_key

    return po_ref_key(value)


@pytest.mark.parametrize(
    "typed",
    ["po 4500-123", "PO#4500123", "P.O. No: 4500 123", "4500123", "PO-4500 123", "pono4500123", "  po4500123  "],
)
def test_u7_every_spelling_of_one_po_folds_to_the_bare_number(typed):
    assert _fold(typed) == "4500123"


def test_u7_letters_and_digits_survive_upper_cased():
    assert _fold("SR-2026/10-035") == "SR202610035"


def test_u7_only_one_leading_prefix_is_dropped():
    assert _fold("PO PO 123") == "PO123"


def test_u7_po_alone_is_kept():
    assert _fold("PO") == "PO"
    assert _fold("pono") == "PONO"


@pytest.mark.parametrize("blank", [None, "", "   ", "-- ./"])
def test_u7_blank_gives_none(blank):
    assert _fold(blank) is None
