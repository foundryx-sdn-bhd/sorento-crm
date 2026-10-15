"""FIND rule text follows crew ruling R9 (16 Oct) and the owner's 15 Oct fit-line ruling.

A bare fit ask ("seat cover for X") is product information, not a bare code. Plan: PLAN-chat-fix-find-rule-16oct.md. Text only.
"""
from __future__ import annotations

import re


def _text() -> str:
    from app.services import product_find

    return product_find.parser_vocabulary()


def test_a_bare_fit_ask_is_product_information() -> None:
    text = _text()
    m = re.search(r"With nothing else asked.{0,400}", text, re.S)
    assert m
    clause = m.group(0)
    assert "master_products" in clause and "check_product" in clause
    assert "never sets master_products" not in text
    assert "a file domain needs a file word in THIS message" in text
    assert "BARE ENTITY CONTINUATION of a file answer" in text
    assert "a bare code right after a fit ask is the same fit ask for the new code" in text

