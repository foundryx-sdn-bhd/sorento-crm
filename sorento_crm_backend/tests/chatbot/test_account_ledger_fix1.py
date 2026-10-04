"""Fix round 1 RED tests - security review B1 (ACCOUNT-LEDGER).

An ENFORCED linked contact in a NON-order domain whose customer word passes `match_words`
(a substring of its OWN linked name, at the asked level) but whose resolver answer holds ONLY
other customers must never be told a foreign customer's name. The staff account refusal
(`resolved["account_refusal"]`, built from the resolver's foreign rows) was copied into the
reply for everyone; it may only speak for a contact that is not enforced.

Expected for the enforced contact: the scope refusal naming only its own linked ledgers, or
the normal scoped answer; never a foreign name, never "has no Account". Staff keep the staff
refusal in the same shape (regression).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from tests.chatbot.test_account_ledger_staff import _services
from tests.chatbot.test_customer_scope_lane import _ent, _link_customers, _turn
from tests._mc_lookup_seed import customer as seed_customer
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact

OWN = "ZZT SOON HENG TRADING [A/C II]"
FOREIGN = "ZZT HENG HUP HARDWARE [A/C I]"


def _set_level(session_factory, cid: str, level: int) -> None:
    db = session_factory()
    db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": cid})
    db.commit()


def _foreign_row(session_factory) -> dict[str, dict[str, Any]]:
    db = session_factory()
    row = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name=FOREIGN)
    db.execute(text("UPDATE customers SET account_level = 1 WHERE id = :i"), {"i": str(row.id)})
    db.commit()
    return {FOREIGN: {"id": str(row.id), "code": row.customer_code, "level": 1}}


def _entity() -> dict[str, Any]:
    entity = _ent("heng")
    entity["account"] = 2
    return entity


def _ask_purchase_order(session_factory, monkeypatch, rows):
    return _turn(
        session_factory, monkeypatch,
        _parser_output(
            domain_hint="purchase_order", intent_hint="check_order", order_status=None, entities=[_entity()],
        ),
        "purchase orders for heng account 2", attributes=(),
        resolve_services=_services(rows, {"heng": [FOREIGN]}),
    )


def test_enforced_contact_never_hears_a_foreign_name_in_a_non_order_domain(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    (own_id,) = _link_customers(session_factory, OWN)
    _set_level(session_factory, own_id, 2)
    rows = _foreign_row(session_factory)
    reply, _captured = _ask_purchase_order(session_factory, monkeypatch, rows)
    assert "HENG HUP" not in reply, reply
    assert "has no Account" not in reply, reply
    assert "It has Account" not in reply, reply


def test_staff_still_get_the_staff_refusal_in_the_same_shape(session_factory, monkeypatch) -> None:
    """Regression: an unlinked (non-enforced) contact is told which levels the typed name has."""
    _seed_contact(session_factory, variables={})
    rows = _foreign_row(session_factory)
    reply, captured = _ask_purchase_order(session_factory, monkeypatch, rows)
    assert "has no Account 2" in reply, reply
    assert "HENG HUP HARDWARE" in reply, reply
    assert captured == []
