"""RED tests for review round 3 (ACCOUNT-LEDGER): two AND customer words, and an enforced
contact's own ledger dropped as an exact-name "sibling".

`documentation/plans/chatbot/PLAN-account-ledger-2oct.md` Design 6 and 8.

* N-a: the AND answer carries no per-token rows, so two customer words that each carry an
  account share ONE stand-in resolution. Narrowing it for the first word, then refusing the
  second off what was left, is wrong ("SOON HENG TRADING has no Account 3. It has Account 1.").
  With more than one word in the AND shape, the rows are narrowed once by the SET of asked
  levels and nothing is refused.
* Security nit: the exact trading name rule ("a word that IS one trading name speaks for that
  name alone") must not drop an ENFORCED contact's own linked ledger. A linked contact whose
  link is "ABC TRADING ENTERPRISE [A/C I]" types "abc trading acc 1" under a non-order domain:
  the scope gate passes (the word is in its link's name), the resolver also answers a foreign
  "ABC TRADING [A/C I]", and the exact rule used to drop the contact's own ledger as a sibling,
  leaving only the foreign row, which the scope screen then refused.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.services.chatbot.lanes.business.resolve_gate import narrow_by_account
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import customer as seed_customer
from tests.chatbot.test_account_ledger_staff import _acct, _services
from tests.chatbot.test_customer_scope_lane import _link_customers, _turn
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact


def _row(uuid: str, name: str) -> dict[str, Any]:
    return {"entity_type": "customer", "uuid": uuid, "canonical_code": uuid, "display": {"customer_name": name}}


# ------------------------------------------------------------------ N-a two AND words


def test_two_and_words_with_accounts_narrow_by_the_set_of_levels_and_never_refuse() -> None:
    rows = [
        _row("t1", "SOON HENG TRADING [A/C I]"),
        _row("t3", "SOON HENG TRADING [A/C III]"),
        _row("h1", "SOON HENG HARDWARE [A/C I]"),
        _row("h2", "SOON HENG HARDWARE [A/C II]"),
        _row("h3", "SOON HENG HARDWARE [A/C III]"),
    ]
    levels = {"t1": 1, "t3": 3, "h1": 1, "h2": 2, "h3": 3}
    resolved: dict[str, Any] = {"intersection": list(rows), "by_entity_type": {"customer": list(rows)}}
    parser = {"entities": [_acct("Soon Heng Trading", 1), _acct("Soon Heng Hardware", 3)]}
    refusal = narrow_by_account(parser, resolved, lambda ids: levels)
    assert refusal is None, refusal
    kept = {m["uuid"] for m in resolved["intersection"]}
    assert kept == {"t1", "t3", "h1", "h3"}, kept
    assert {m["uuid"] for m in resolved["by_entity_type"]["customer"]} == kept


def test_two_and_words_with_no_row_at_any_asked_level_leave_the_rows_alone() -> None:
    """The leave-alone arm: narrowing by {1, 3} would drop EVERY customer row, so nothing is
    dropped and nothing is refused (the multi-word AND path never refuses)."""
    rows = [
        _row("t2", "SOON HENG TRADING [A/C II]"),
        _row("t4", "SOON HENG TRADING [A/C IV]"),
        _row("h2", "SOON HENG HARDWARE [A/C II]"),
    ]
    levels = {"t2": 2, "t4": 4, "h2": 2}
    resolved: dict[str, Any] = {"intersection": list(rows), "by_entity_type": {"customer": list(rows)}}
    parser = {"entities": [_acct("Soon Heng Trading", 1), _acct("Soon Heng Hardware", 3)]}
    refusal = narrow_by_account(parser, resolved, lambda ids: levels)
    assert refusal is None, refusal
    assert resolved["intersection"] == rows
    assert resolved["by_entity_type"] == {"customer": rows}


# ------------------------------------------------------------------ enforced contact, exact name

OWN = "ABC TRADING ENTERPRISE [A/C I]"
FOREIGN = "ABC TRADING [A/C I]"


def test_enforced_contacts_own_ledger_is_never_dropped_as_an_exact_name_sibling(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    (own_id,) = _link_customers(session_factory, OWN)
    db = session_factory()
    foreign = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name=FOREIGN)
    db.execute(
        text("UPDATE customers SET account_level = 1 WHERE id IN (:a, :b)"), {"a": own_id, "b": str(foreign.id)}
    )
    own_code = db.execute(text("SELECT customer_code FROM customers WHERE id = :i"), {"i": own_id}).scalar()
    db.commit()
    rows = {
        OWN: {"id": own_id, "code": own_code, "level": 1},
        FOREIGN: {"id": str(foreign.id), "code": foreign.customer_code, "level": 1},
    }
    reply, _captured = _turn(
        session_factory, monkeypatch,
        _parser_output(
            domain_hint="purchase_order", intent_hint="check_order", order_status=None,
            entities=[_acct("abc trading", 1)],
        ),
        "purchase orders for abc trading acc 1", attributes=(),
        resolve_services=_services(rows, {"abc trading": [FOREIGN, OWN]}),
    )
    assert "under your account" not in reply, reply
    assert FOREIGN not in reply, reply
