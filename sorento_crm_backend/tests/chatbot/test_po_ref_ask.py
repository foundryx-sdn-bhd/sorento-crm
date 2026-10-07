"""SO-PO-REF chatbot (U13-U21): the session's audience decides what "PO" means.

Dealer/customer contacts (every profile that is not `tier == "office"`): the parser's
`po_refs` is answered from `sales_orders.po_ref_key`, scoped to the contact's own customers.
Staff: `po_refs` is ignored and the supplier PO path is untouched.

Harness: `test_so_number_ask.py` (one real `engine.run_turn`, parser/access/resolver/MCP
faked). Every row is seeded here; the CI database is empty. Keys are seeded literally, so a
red here is the lane and the lookup, not the fold helper (`tests/scm/test_po_ref_key.py`).
"""
from __future__ import annotations

import re

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from tests.chatbot.test_customer_scope_lane import _link_customers, _other_customer
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import CONTACT_ID, _run_turn, _seed_contact
from tests.chatbot.test_so_number_ask import DO_DUMP, OUTSTANDING_KEY, _card

BACKEND = Path(__file__).resolve().parents[2]
REF = "4500123"
NOT_FOUND = f"I could not find PO ref {REF}."


def _seed_po_so(
    session_factory,
    so_number: str,
    *,
    customer_id: str | None,
    po_ref: str | None,
    po_ref_key: str | None,
    order_date: str = "2026-09-15",
    debtor_name: str = "ZZT HANLIM TRADING [A/C I]",
) -> None:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import product as seed_product

    db = session_factory()
    so_id = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO sales_orders (id, company_id, so_number, customer_id, debtor_name, order_date, "
            "status, po_ref, po_ref_key, created_at, updated_at) "
            "VALUES (:id, :co, :num, :cust, :dname, :odate, 'open', :po, :key, now(), now())"
        ),
        {
            "id": so_id, "co": DEFAULT_COMPANY_ID, "num": so_number, "cust": customer_id,
            "dname": debtor_name, "odate": order_date, "po": po_ref, "key": po_ref_key,
        },
    )
    prod = seed_product(db, company_id=DEFAULT_COMPANY_ID)
    db.execute(
        text(
            "INSERT INTO sales_order_lines (id, company_id, sales_order_id, product_id, qty_ordered, "
            "qty_delivered, line_status, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :co, :so, :prod, 1, 0, 'open', now(), now())"
        ),
        {"co": DEFAULT_COMPANY_ID, "so": so_id, "prod": str(prod.id)},
    )
    db.commit()


def _po_turn(session_factory, monkeypatch, po_refs, *, body: str = f"status of PO {REF}"):
    qf = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status=None, entities=[], po_refs=po_refs,
    )
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body,
        msg_id=f"ZZT-po-ref-{uuid.uuid4().hex[:10]}", attributes=[OUTSTANDING_KEY],
        matches={}, mcp_response=DO_DUMP,
    )
    return ((result.reply or {}).get("text") or ""), captured


def _scoped(session_factory):
    """A session stamped with the company scope before its first query (the conftest
    after_begin listener has not run for a fresh session, so it would see no rows)."""
    from app.models.base import set_company_scope
    from app.services.company_scope import DEFAULT_COMPANY_ID

    db = session_factory()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    return db


def _make_staff(session_factory) -> None:
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
        {"p": '{"tier": "office"}', "c": str(CONTACT_ID)},
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Parser contract (U18, U21)
# --------------------------------------------------------------------------- #


class TestParserDeclaresPoRefs:
    def test_schema_property_is_a_nullable_string_array(self) -> None:
        from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

        prop = PARSE_OUTPUT_JSON_SCHEMA["properties"].get("po_refs")
        assert prop is not None, "po_refs is not a declared parser output key"
        assert "null" in prop["type"] and "array" in prop["type"], prop
        assert prop["items"]["type"] == "string", prop

    def test_declared_required_and_tolerated_absent(self) -> None:
        from app.services.chatbot.head.parser import (
            DECLARED_KEYS, PARSE_OUTPUT_JSON_SCHEMA, TOLERATED_ABSENT,
        )

        assert "po_refs" in DECLARED_KEYS
        assert "po_refs" in PARSE_OUTPUT_JSON_SCHEMA["required"]
        assert "po_refs" in TOLERATED_ABSENT

    def test_prompt_teaches_it_for_any_po_number(self) -> None:
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert "po_refs" in SEMANTIC_PARSER_PROMPT
        assert "our PO" in SEMANTIC_PARSER_PROMPT


def test_the_migration_exists_and_chains_onto_a_committed_revision() -> None:
    # The parent is whatever main's head was when the lane last re-parented
    # (scripts/alembic-reparent.sh), so pin only that it names a real revision; the single
    # head itself is the CI alembic-heads gate's job.
    versions = BACKEND / "alembic" / "versions"
    path = versions / "sopr_0001_so_po_ref.py"
    assert path.exists(), "no migration sopr_0001_so_po_ref.py"
    body = path.read_text().replace("'", '"')
    match = re.search(r'^down_revision[^=]*= "([^"]+)"', body, re.MULTILINE)
    assert match, "sopr_0001_so_po_ref has no down_revision"
    parent = match.group(1)
    assert any(
        re.search(rf'^revision[^=]*= ["\']{re.escape(parent)}["\']', p.read_text(), re.MULTILINE)
        for p in versions.glob("*.py")
        if p != path
    ), f"down_revision {parent} is not a committed revision"
    assert "po_ref_key" in body and "ix_sales_orders_po_ref_key" in body


# --------------------------------------------------------------------------- #
# so_status.lookup_po_refs + po_ref_reply_text (U13-U16, U19)
# --------------------------------------------------------------------------- #


class TestLookupPoRefs:
    def _linked(self, session_factory):
        own = _other_customer(session_factory, "ZZT HANLIM TRADING [A/C I]", "ZZT-PO-OWN")
        return own, [(own, "ZZT HANLIM TRADING [A/C I]", "ZZT-PO-OWN")]

    def test_one_so_gives_the_po_line_then_the_card(self, session_factory) -> None:
        from app.services.chatbot import so_status

        own, linked = self._linked(session_factory)
        _seed_po_so(session_factory, "SO430001", customer_id=own, po_ref=REF, po_ref_key=REF)
        result = so_status.lookup_po_refs(_scoped(session_factory), [REF], linked=linked)

        assert len(result.cards) == 1 and result.missing == []
        reply = so_status.po_ref_reply_text(result, REF)
        assert reply == f"PO ref: {REF}\n\n" + _card("SO430001", "Open", "not delivered yet"), reply

    def test_several_sos_newest_order_date_first_with_a_count_line(self, session_factory) -> None:
        from app.services.chatbot import so_status

        own, linked = self._linked(session_factory)
        _seed_po_so(session_factory, "SO430010", customer_id=own, po_ref=REF, po_ref_key=REF, order_date="2026-08-01")
        _seed_po_so(session_factory, "SO430011", customer_id=own, po_ref="PO-4500 123", po_ref_key=REF, order_date="2026-09-20")
        _seed_po_so(session_factory, "SO430012", customer_id=own, po_ref="po 4500123", po_ref_key=REF, order_date="2026-09-01")
        result = so_status.lookup_po_refs(_scoped(session_factory), [REF], linked=linked)

        reply = so_status.po_ref_reply_text(result, REF)
        assert reply == "\n\n".join(
            [
                f"PO ref: {REF}\nSales orders: 3",
                _card("SO430011", "Open", "not delivered yet", date="20 Sep 2026"),
                _card("SO430012", "Open", "not delivered yet", date="1 Sep 2026"),
                _card("SO430010", "Open", "not delivered yet", date="1 Aug 2026"),
            ]
        ), reply

    def test_another_dealers_so_is_a_silent_miss(self, session_factory) -> None:
        from app.services.chatbot import so_status

        _own, linked = self._linked(session_factory)
        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_po_so(session_factory, "SO430020", customer_id=foreign, po_ref=REF, po_ref_key=REF, debtor_name="ZZT FOREIGN SDN BHD")
        result = so_status.lookup_po_refs(_scoped(session_factory), [REF], linked=linked)

        assert result.cards == [] and result.refused is False
        reply = so_status.po_ref_reply_text(result, REF)
        assert reply == NOT_FOUND, reply
        assert "FOREIGN" not in reply

    def test_typed_spelling_is_folded_and_no_partial_match(self, session_factory) -> None:
        from app.services.chatbot import so_status

        own, linked = self._linked(session_factory)
        _seed_po_so(session_factory, "SO430030", customer_id=own, po_ref="4500123", po_ref_key="4500123")
        _seed_po_so(session_factory, "SO430031", customer_id=own, po_ref="PO-4500 123", po_ref_key="4500123")
        _seed_po_so(session_factory, "SO430032", customer_id=own, po_ref="45001234", po_ref_key="45001234")
        hit = so_status.lookup_po_refs(_scoped(session_factory), ["PO 4500-123"], linked=linked)
        near = so_status.lookup_po_refs(_scoped(session_factory), ["4500"], linked=linked)

        assert len(hit.cards) == 2, hit
        assert near.cards == [], near

    def test_no_link_sees_nothing(self, session_factory) -> None:
        from app.services.chatbot import so_status

        own = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_po_so(session_factory, "SO430040", customer_id=own, po_ref=REF, po_ref_key=REF)
        result = so_status.lookup_po_refs(_scoped(session_factory), [REF], linked=[])

        assert result.cards == []
        assert so_status.po_ref_reply_text(result, REF) == NOT_FOUND


# --------------------------------------------------------------------------- #
# Turn level (U13-U15, U17, U18, U19, U20, U21)
# --------------------------------------------------------------------------- #


class TestDealerTurn:
    def test_u13_one_so(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430101", customer_id=own, po_ref=f"PO-{REF}", po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, captured = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip() == f"PO ref: {REF}\n\n" + _card("SO430101", "Open", "not delivered yet", name="ZZT OWN A"), reply

    def test_u14_several_newest_first(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        for number, day in (("SO430111", "2026-08-10"), ("SO430112", "2026-09-25"), ("SO430113", "2026-09-02")):
            _seed_po_so(session_factory, number, customer_id=own, po_ref=REF, po_ref_key=REF, order_date=day, debtor_name="ZZT OWN A")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip().startswith(f"PO ref: {REF}\nSales orders: 3\n\n"), reply
        assert reply.index("SO430112") < reply.index("SO430113") < reply.index("SO430111"), reply

    def test_u15_only_another_dealers_so_carries_it(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A")
        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_po_so(session_factory, "SO430121", customer_id=foreign, po_ref=REF, po_ref_key=REF, debtor_name="ZZT FOREIGN SDN BHD")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip() == NOT_FOUND, reply

    def test_u16_typed_spelling_finds_both_stored_spellings(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430131", customer_id=own, po_ref="4500123", po_ref_key=REF, debtor_name="ZZT OWN A")
        _seed_po_so(session_factory, "SO430132", customer_id=own, po_ref="PO-4500 123", po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, _ = _po_turn(session_factory, monkeypatch, ["PO 4500-123"])

        assert "SO430131" in reply and "SO430132" in reply, reply
        assert "Sales orders: 2" in reply, reply

    def test_u19_no_dot_separators(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430141", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")
        _seed_po_so(session_factory, "SO430142", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        header = reply.strip().split("\n\n")[0]
        assert header == f"PO ref: {REF}\nSales orders: 2", header
        assert "·" not in header and "•" not in header

    def test_u20_unlinked_dealer_sees_no_so(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        someone = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_po_so(session_factory, "SO430151", customer_id=someone, po_ref=REF, po_ref_key=REF)

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip() == NOT_FOUND, reply

    def test_u18_without_a_parser_po_refs_the_words_alone_do_nothing(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430161", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, _ = _po_turn(session_factory, monkeypatch, None)

        assert "PO ref:" not in reply and "SO430161" not in reply, reply


class TestStaffTurn:
    def test_u17_u21_staff_ignore_po_refs(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        _make_staff(session_factory)
        own = _other_customer(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430201", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert "PO ref:" not in reply, reply
        assert "SO430201" not in reply, reply
        assert "I could not find PO ref" not in reply, reply


# --------------------------------------------------------------------------- #
# Round 2: the parser also emits the PO number as an entity (live console, head 55a643751)
# --------------------------------------------------------------------------- #


def _po_turn_with_entity(session_factory, monkeypatch, hint: str, *, raw: str = REF):
    qf = _parser_output(
        domain_hint="order", intent_hint="check_order", order_status=None, po_refs=[REF],
        entities=[{"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}],
    )
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=f"status of PO {REF}",
        msg_id=f"ZZT-po-ref-ent-{uuid.uuid4().hex[:10]}", attributes=[OUTSTANDING_KEY],
        matches={}, mcp_response=DO_DUMP,
    )
    return ((result.reply or {}).get("text") or ""), captured


def _carried_raws(session_factory) -> list[str]:
    """Every `raw` the session's focus carries to the next turn, wherever it sits."""
    from tests.chatbot.test_outstanding_lane import _session_of

    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("raw"), str):
                found.append(node["raw"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_session_of(session_factory).get("focus") or {})
    return found


@pytest.mark.parametrize("hint", ["product", "order"])
class TestPoNumberAlsoEmittedAsAnEntity:
    def test_a_one_match_is_exactly_the_po_answer(self, session_factory, monkeypatch, hint) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430301", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")

        reply, _ = _po_turn_with_entity(session_factory, monkeypatch, hint)

        assert reply.strip() == f"PO ref: {REF}\n\n" + _card("SO430301", "Open", "not delivered yet", name="ZZT OWN A"), reply
        assert "could not find" not in reply and "Did you mean" not in reply, reply

    def test_b_no_match_is_exactly_the_one_miss_line(self, session_factory, monkeypatch, hint) -> None:
        _seed_contact(session_factory, variables={})
        _link_customers(session_factory, "ZZT OWN A")

        reply, _ = _po_turn_with_entity(session_factory, monkeypatch, hint)

        assert reply.strip() == NOT_FOUND, reply

    def test_c_the_word_is_not_carried_to_the_next_turn(self, session_factory, monkeypatch, hint) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        _seed_po_so(session_factory, "SO430311", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")

        _po_turn_with_entity(session_factory, monkeypatch, hint)

        assert REF not in _carried_raws(session_factory), _carried_raws(session_factory)


# --------------------------------------------------------------------------- #
# Round 2: scope pins (security review gaps)
# --------------------------------------------------------------------------- #


class TestScopePins:
    def test_d_out_of_scope_so_is_neither_counted_nor_shown(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_po_so(session_factory, "SO430321", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")
        _seed_po_so(session_factory, "SO430322", customer_id=foreign, po_ref=REF, po_ref_key=REF, debtor_name="ZZT FOREIGN SDN BHD")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert "Sales orders:" not in reply, reply
        assert "SO430321" in reply and "SO430322" not in reply and "FOREIGN" not in reply, reply

    def test_d_two_in_scope_one_out_of_scope_counts_two(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        for number in ("SO430331", "SO430332"):
            _seed_po_so(session_factory, number, customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")
        _seed_po_so(session_factory, "SO430333", customer_id=foreign, po_ref=REF, po_ref_key=REF, debtor_name="ZZT FOREIGN SDN BHD")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip().startswith(f"PO ref: {REF}\nSales orders: 2\n\n"), reply
        assert "SO430333" not in reply and "FOREIGN" not in reply, reply

    def test_e_linked_contact_with_scope_not_enforced_still_sees_only_links(self, session_factory, monkeypatch) -> None:
        """`enforced` is false for a linked contact holding an office access type; the PO
        answer reads the links, never the enforced flag."""
        import dataclasses

        from app.services import contact_customer_scope as scope_mod

        real = scope_mod.contact_customer_scope
        monkeypatch.setattr(
            scope_mod, "contact_customer_scope",
            lambda db, contact_id: dataclasses.replace(real(db, contact_id), staff=True),
        )
        _seed_contact(session_factory, variables={})
        own, = _link_customers(session_factory, "ZZT OWN A")
        foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
        _seed_po_so(session_factory, "SO430341", customer_id=own, po_ref=REF, po_ref_key=REF, debtor_name="ZZT OWN A")
        _seed_po_so(session_factory, "SO430342", customer_id=foreign, po_ref=REF, po_ref_key=REF, debtor_name="ZZT FOREIGN SDN BHD")

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert "SO430341" in reply and "SO430342" not in reply and "FOREIGN" not in reply, reply
        assert "Sales orders:" not in reply, reply

    def test_f_no_customer_scope_at_all_finds_nothing(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        anyone = _other_customer(session_factory, "ZZT ANY SDN BHD")
        _seed_po_so(session_factory, "SO430351", customer_id=anyone, po_ref=REF, po_ref_key=REF)
        _seed_po_so(session_factory, "SO430352", customer_id=None, po_ref=REF, po_ref_key=REF)

        reply, _ = _po_turn(session_factory, monkeypatch, [REF])

        assert reply.strip() == NOT_FOUND, reply
