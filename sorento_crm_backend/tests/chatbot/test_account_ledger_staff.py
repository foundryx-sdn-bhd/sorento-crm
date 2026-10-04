"""Phase 2 RED tests - staff: "account N" narrows the which-customer lookup by Account level.

`documentation/plans/chatbot/PLAN-account-ledger-2oct.md` Design 6 and AC-8, AC-9 (owner Q4).
`resolve_gate.run`, right after `services.resolve_entity` and before `run_gate`: for a customer
entity with `account` N and a raw word, the customer matches resolved for that word whose
`customers.account_level` is not N are dropped (read by uuid). No `account` -> unchanged.

Tested through one real `engine.run_turn` (the harness of `test_customer_scope_lane.py`), the
highest seam the existing picker tests use. The asker is an UNLINKED contact (the generic
resolver path, the one staff take); the fake resolver answers the typed word with the real
`customers` rows seeded here, in an order that puts the level one ledger LAST, so a picker that
was not narrowed cannot show a level one name by luck of ordering.

Data (levels are the setting): SOON HENG HARDWARE [A/C II](2), bare(None), [A/C I](1);
SOON HENG TRADING [A/C III](3), [A/C IV](4), [A/C I](1).

Exact texts pinned (card Q4 / UAC AC-9):
  * "SOON HENG TRADING has no Account 2. It has Account 1, Account 3 and Account 4."
    (family label = `ledger_family_label` of the matched rows, levels ascending)
  * 'None of the customers matching "Soon Heng" has Account 7.' when several families match
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests._mc_lookup_seed import customer as seed_customer
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot.test_customer_scope_lane import REPORT, _ask, _calls, _ent, _session_of, _turn
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _seed_contact

H_2 = "SOON HENG HARDWARE [A/C II]"
H_BARE = "SOON HENG HARDWARE"
H_1 = "SOON HENG HARDWARE [A/C I]"
T_3 = "SOON HENG TRADING [A/C III]"
T_4 = "SOON HENG TRADING [A/C IV]"
T_1 = "SOON HENG TRADING [A/C I]"
BOOK = [(H_2, 2), (H_BARE, None), (H_1, 1), (T_3, 3), (T_4, 4), (T_1, 1)]


def _seed_book(session_factory, *, levels: bool = True) -> dict[str, dict[str, Any]]:
    db = session_factory()
    rows: dict[str, dict[str, Any]] = {}
    for name, level in BOOK:
        row = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        if levels and level is not None:
            db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": str(row.id)})
        rows[name] = {"id": str(row.id), "code": row.customer_code, "level": level}
    db.commit()
    return rows


def _services(rows: dict[str, dict[str, Any]], by_token: dict[str, list[str]]) -> ResolveGateServices:
    """A resolver that answers each asked token with the named seeded rows, in the given order."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = [str(t) for t in (body.get("tokens") or [])]
        resolutions = []
        for token in asked:
            names = by_token.get(token.strip().lower(), [])
            resolutions.append(
                {
                    "raw": token,
                    "token": token,
                    "matches": [
                        {
                            "entity_type": "customer",
                            "canonical_code": rows[n]["code"],
                            "uuid": rows[n]["id"],
                            "company_code": "SRT",
                            "display": {"customer_name": n},
                        }
                        for n in names
                    ],
                }
            )
        return {
            "tokens": asked,
            "resolutions": resolutions,
            "unresolved_tokens": [t for t, r in zip(asked, resolutions) if not r["matches"]],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: {"items": [], "has_result": False},
    )


def _acct(raw: str, account: int | None) -> dict[str, Any]:
    entity = _ent(raw)
    entity["account"] = account
    return entity


ALL_NAMES = [n for n, _ in BOOK]
TRADING = [T_3, T_4, T_1]


def _picker_lines(reply: str) -> list[str]:
    return [line for line in reply.split("\n") if re.match(r"^\d+\. ", line)]


def _turn_with(session_factory, monkeypatch, rows, by_token, entity, body):
    return _turn(
        session_factory, monkeypatch, _ask([entity]), body,
        resolve_services=_services(rows, by_token), mcp_response=REPORT_HIT,
    )


def test_account_1_lists_only_the_level_one_ledgers(session_factory, monkeypatch) -> None:
    """AC-8: two families, one line each, each the level one ledger by its exact name."""
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, captured = _turn_with(
        session_factory, monkeypatch, rows, {"soon heng": ALL_NAMES}, _acct("Soon Heng", 1),
        "soon heng account 1 outstanding",
    )
    assert captured == [], captured
    assert "Which customer do you mean?" in reply, reply
    lines = _picker_lines(reply)
    assert len(lines) == 2, lines
    assert any(H_1 in line for line in lines), lines
    assert any(T_1 in line for line in lines), lines
    for other in ("[A/C II]", "[A/C III]", "[A/C IV]"):
        assert other not in reply, (other, reply)
    roster_ids = {
        str(o.get("uuid")) for o in (_session_of(session_factory).get("open_question") or {}).get("options") or []
    }
    assert roster_ids == {rows[H_1]["id"], rows[T_1]["id"]}, roster_ids


def test_a_single_survivor_is_answered_without_a_picker(session_factory, monkeypatch) -> None:
    """"Soon Heng Trading account 1" leaves one ledger: the report runs on it."""
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, captured = _turn_with(
        session_factory, monkeypatch, rows, {"soon heng trading": TRADING}, _acct("Soon Heng Trading", 1),
        "soon heng trading account 1 outstanding",
    )
    assert "Which customer" not in reply, reply
    (args,) = _calls(captured, REPORT)
    assert args["customer_ids"] == [rows[T_1]["id"]], args


def test_a_level_the_name_lacks_is_refused_naming_the_levels_it_has(session_factory, monkeypatch) -> None:
    """AC-9 / Q4."""
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, captured = _turn_with(
        session_factory, monkeypatch, rows, {"soon heng trading": TRADING}, _acct("Soon Heng Trading", 2),
        "soon heng trading account 2",
    )
    assert reply.strip() == "SOON HENG TRADING has no Account 2. It has Account 1, Account 3 and Account 4.", reply
    assert captured == []


def test_a_level_no_family_of_the_word_has_is_refused_with_the_word_quoted(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, captured = _turn_with(
        session_factory, monkeypatch, rows, {"soon heng": ALL_NAMES}, _acct("Soon Heng", 7),
        "soon heng account 7",
    )
    assert reply.strip() == 'None of the customers matching "Soon Heng" has Account 7.', reply
    assert captured == []


def test_no_account_leaves_the_picker_unchanged(session_factory, monkeypatch) -> None:
    """AC-5 regression: the family picker lists the two families, not narrowed to a level."""
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory, levels=False)  # no account asked: levels are irrelevant
    reply, captured = _turn_with(
        session_factory, monkeypatch, rows, {"soon heng": ALL_NAMES}, _acct("Soon Heng", None),
        "soon heng outstanding",
    )
    assert captured == [], captured
    lines = _picker_lines(reply)
    assert len(lines) == 2, lines
    assert any("SOON HENG HARDWARE" in line for line in lines), lines
    assert any("SOON HENG TRADING" in line for line in lines), lines
    assert "has no Account" not in reply
