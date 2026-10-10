"""REF-PROBES S7: the parser is taught the five reference-kind hints, the prompt is published
by an alembic migration, and the entity gate lets the new kinds through for an order ask.

Expected migration file: alembic/versions/*parser_ref_kinds*.py (e.g. rkind_0001_parser_ref_kinds.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
HINTS = ("delivery_order", "sales_order", "customer_po_ref", "purchase_order", "purchase_request")
GATE_KINDS = ("sales_order", "customer_po_ref", "purchase_order", "purchase_request")


def test_ref_kinds_addendum_names_every_hint_and_is_in_the_prompt():
    from app.services import chatbot_parser_prompt as prompt

    addendum = getattr(prompt, "REF_KINDS_ADDENDUM", None)
    assert addendum, "REF_KINDS_ADDENDUM is not defined in chatbot_parser_prompt"
    for hint in HINTS:
        assert hint in addendum, hint
        assert hint in prompt.SEMANTIC_PARSER_PROMPT, hint
    assert addendum.strip() in prompt.SEMANTIC_PARSER_PROMPT


def test_the_publish_migration_exists_and_chains_onto_a_committed_revision():
    versions = BACKEND / "alembic" / "versions"
    found = sorted(versions.glob("*parser_ref_kinds*.py"))
    assert found, "no migration matching alembic/versions/*parser_ref_kinds*.py"
    path = found[0]
    body = path.read_text().replace("'", '"')
    match = re.search(r'^down_revision[^=]*= "([^"]+)"', body, re.MULTILINE)
    assert match, f"{path.name} has no down_revision"
    parent = match.group(1)
    assert any(
        re.search(rf'^revision[^=]*= ["\']{re.escape(parent)}["\']', p.read_text(), re.MULTILINE)
        for p in versions.glob("*.py")
        if p != path
    ), f"down_revision {parent} is not a committed revision"
    rev = re.search(r'^revision[^=]*= "([^"]+)"', body, re.MULTILINE)
    assert rev and len(rev.group(1)) <= 32, "revision id must be 32 chars or fewer"
    assert "purchase_request" in body or "REF_KINDS_ADDENDUM" in body


@pytest.mark.parametrize("kind", GATE_KINDS)
def test_gate_lets_the_new_kinds_through_for_an_order_ask(kind):
    from app.services.chatbot.lanes.business.gate import ALLOWED

    assert kind in ALLOWED["order"], (kind, ALLOWED["order"])
    assert kind in ALLOWED["sales"], (kind, ALLOWED["sales"])
