"""Guard tests for two ACCOUNT-LEDGER rules that had none (review round 3, B-1 to B-3).

`documentation/plans/chatbot/PLAN-account-ledger-2oct.md` Design 6 and 8.

* B-1: an AND-shaped resolver answer whose customer list was cut at the body's `limit`
  (`token_coverage[].coverage[].truncated`) never refuses and never narrows when no row has
  the asked level: the level may sit past the cut.
* B-2 / B-3: `resolve_entity_body` lifts `limit` from 15 to the route's 200 ONLY when a
  customer word typed this message carries an `account`; otherwise it stays at the plain
  lookup limit (15 then, 50 since PICKER-NO-CAP).

Pure unit tests: `narrow_by_account` with a stub level reader, `resolve_entity_body` with a
hand-built ctx. No database.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.chatbot.lanes.business.resolve_gate import narrow_by_account, resolve_entity_body


def _row(uuid: str, name: str) -> dict[str, Any]:
    return {"entity_type": "customer", "uuid": uuid, "canonical_code": uuid, "display": {"customer_name": name}}


def _ask(raw: str | None, account: Any, hint: str = "customer", **over: Any) -> dict[str, Any]:
    entity: dict[str, Any] = {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True}
    if account is not ...:
        entity["account"] = account
    entity.update(over)
    return entity


# ------------------------------------------------------------------ B-1 cut AND list


def test_b1_cut_and_list_without_the_level_neither_refuses_nor_narrows() -> None:
    rows = [_row("u3", "SOON HENG HARDWARE [A/C III]"), _row("u4", "SOON HENG HARDWARE [A/C IV]")]
    resolved: dict[str, Any] = {
        "intersection": list(rows),
        "by_entity_type": {"customer": list(rows)},
        "token_coverage": [
            {"token": "Soon Heng", "coverage": [{"entity_type": "customer", "truncated": True}]}
        ],
    }
    parser = {"entities": [_ask("Soon Heng", 1)]}
    refusal = narrow_by_account(parser, resolved, lambda ids: {"u3": 3, "u4": 4})
    assert refusal is None
    assert resolved["intersection"] == rows
    assert resolved["by_entity_type"]["customer"] == rows


def test_b1_the_same_list_not_cut_refuses() -> None:
    """The control: identical rows, coverage NOT truncated, so the refusal is earned."""
    rows = [_row("u3", "SOON HENG HARDWARE [A/C III]"), _row("u4", "SOON HENG HARDWARE [A/C IV]")]
    resolved: dict[str, Any] = {
        "intersection": list(rows),
        "by_entity_type": {"customer": list(rows)},
        "token_coverage": [
            {"token": "Soon Heng", "coverage": [{"entity_type": "customer", "truncated": False}]}
        ],
    }
    parser = {"entities": [_ask("Soon Heng", 1)]}
    refusal = narrow_by_account(parser, resolved, lambda ids: {"u3": 3, "u4": 4})
    assert refusal == "SOON HENG HARDWARE has no Account 1. It has Account 3 and Account 4.", refusal


# ------------------------------------------------------------------ B-2 / B-3 body limit


def _ctx(entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": {"message": {"message": {"text": "soon heng account 1 outstanding"}}},
        "contact": {"id": "1"},
        "parse": {
            "output": {
                "message_type": "business_query",
                "intent_hint": "check_order",
                "domain_hint": "order",
                "match_mode": "and",
                "access_levels": [],
                "entities": entities,
            }
        },
    }


def test_b2_a_customer_word_with_an_account_lifts_the_limit_to_200() -> None:
    assert resolve_entity_body(_ctx([_ask("Soon Heng", 1)]))["limit"] == 200


@pytest.mark.parametrize(
    "entities",
    [
        pytest.param([_ask("Soon Heng", None)], id="account-null"),
        pytest.param([_ask("Soon Heng", ...)], id="account-absent"),
        pytest.param([_ask("SRTWT7445", 1, hint="product")], id="account-on-a-product"),
        pytest.param([_ask("Soon Heng", 1, current_message=False)], id="carried-word"),
    ],
)
def test_b3_no_account_on_a_customer_word_keeps_the_lookup_limit(entities) -> None:
    # PICKER-NO-CAP (owner, 2 Oct 2026): the plain lookup limit is 50 (was 15), the
    # roster ceiling, so a picker can list every match it is allowed to print.
    assert resolve_entity_body(_ctx(entities))["limit"] == 50
