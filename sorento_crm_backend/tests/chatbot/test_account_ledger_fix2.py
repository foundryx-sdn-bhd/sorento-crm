"""Fix round 2 RED tests (ACCOUNT-LEDGER): reviewer B-1, S-1 to S-4, N-1.

* B-1: with NO account on any word, two typed customer words still ask the pick question
  (re-applying the verdict without the customers is only for accounts).
* S-1: staff narrowing never refuses off a TRUNCATED resolver list (a customer match carries
  `display.truncated_more_available`) when no kept row has the level; with the level present
  it still narrows.
* S-3 (owner ruling a): the answer to "Which customer is Account N for?" keeps Account N for
  that ONE answer turn only.
* S-4: staff narrowing keys on the token actually sent to the resolver (canonical_code or raw).
* N-1: the `customer_scope` trace reason for account outcomes is `account_unnamed` and
  `account_level_absent`.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.services.chatbot.lanes.business.services import ResolveGateServices
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot.test_account_ledger_staff import T_1, T_3, T_4, TRADING, _acct, _seed_book, _services
from tests.chatbot.test_customer_scope_lane import (
    REPORT, _ask, _calls, _ent, _link_customers, _scope_events, _turn,
)
from tests.chatbot.test_outstanding_lane import REPORT_HIT, _run_turn, _seed_contact
from tests._mc_lookup_seed import customer as seed_customer
from app.services.company_scope import DEFAULT_COMPANY_ID

H_1 = "HANLIM TRADING SDN BHD [A/C I]"
H_2 = "HANLIM TRADING SDN BHD [A/C II]"
H_BARE = "HANLIM TRADING SDN BHD"
S_NAME = "SOON HENG HARDWARE CO.SDN.BHD."


def _unnamed(account: int) -> dict[str, Any]:
    entity = _ent("x")
    entity["raw"] = None
    entity["account"] = account
    return entity


def _set(session_factory, ids: list[str], levels: list[int | None]) -> None:
    db = session_factory()
    for cid, level in zip(ids, levels):
        if level is not None:
            db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": cid})
    db.commit()


# ------------------------------------------------------------------ B-1


def test_b1_two_typed_words_without_an_account_still_ask_the_pick(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, H_1, S_NAME)
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_ent("Hanlim"), _ent("Soon Heng")]),
        "outstanding for hanlim and soon heng", mcp_response=REPORT_HIT,
    )
    assert "Which one do you mean?" in reply, reply
    assert "Hanlim" in reply and "Soon Heng" in reply, reply
    assert captured == [], captured


# ------------------------------------------------------------------ S-1 truncated list


def _truncating(rows: dict[str, dict[str, Any]], by_token: dict[str, list[str]]) -> ResolveGateServices:
    inner = _services(rows, by_token)

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        payload = inner.resolve_entity(body)
        for resolution in payload["resolutions"]:
            for match in resolution["matches"]:
                match["display"] = {**match["display"], "truncated_more_available": True}
        return payload

    return ResolveGateServices(access_types=inner.access_types, resolve_entity=_resolve_entity, probe=inner.probe)


def _staff_turn(session_factory, monkeypatch, services, entity, body):
    return _turn(
        session_factory, monkeypatch, _ask([entity]), body, resolve_services=services, mcp_response=REPORT_HIT,
    )


def test_s1_truncated_list_without_the_level_never_refuses(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, _captured = _staff_turn(
        session_factory, monkeypatch, _truncating(rows, {"soon heng trading": [T_3, T_4]}),
        _acct("Soon Heng Trading", 2), "soon heng trading account 2",
    )
    assert "has no Account" not in reply, reply
    assert "None of the customers matching" not in reply, reply


def test_s1_truncated_list_with_the_level_still_narrows(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    reply, captured = _staff_turn(
        session_factory, monkeypatch, _truncating(rows, {"soon heng trading": [T_3, T_4, T_1]}),
        _acct("Soon Heng Trading", 1), "soon heng trading account 1",
    )
    (args,) = _calls(captured, REPORT)
    assert args["customer_ids"] == [rows[T_1]["id"]], args


# ------------------------------------------------------------------ S-4 token actually sent


def test_s4_staff_narrowing_keys_on_the_canonical_code_token(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    entity = _acct("soon heng trading", 1)
    entity["canonical_code"] = "300-S254"
    reply, captured = _staff_turn(
        session_factory, monkeypatch, _services(rows, {"300-s254": TRADING}), entity,
        "soon heng trading account 1",
    )
    (args,) = _calls(captured, REPORT)
    assert args["customer_ids"] == [rows[T_1]["id"]], args


# ------------------------------------------------------------------ S-3 the answer keeps the account once


def _hanlim_staff_book(session_factory) -> dict[str, dict[str, Any]]:
    db = session_factory()
    rows: dict[str, dict[str, Any]] = {}
    for name, level in ((H_1, 1), (H_2, 2), (H_BARE, None)):
        row = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        if level is not None:
            db.execute(text("UPDATE customers SET account_level = :n WHERE id = :i"), {"n": level, "i": str(row.id)})
        rows[name] = {"id": str(row.id), "code": row.customer_code, "level": level}
    db.commit()
    return rows


def test_s3_linked_answer_is_narrowed_to_the_asked_account(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    ids = _link_customers(session_factory, H_1, H_2, H_BARE)
    _set(session_factory, ids, [1, 2, None])
    q, c1 = _turn(session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT)
    assert q.strip() == "Which customer is Account 2 for?", q
    assert c1 == []
    _reply, c2 = _turn(session_factory, monkeypatch, _ask([_ent("Hanlim")]), "hanlim", mcp_response=REPORT_HIT)
    (args,) = _calls(c2, REPORT)
    assert args["customer_ids"] == [ids[1]], args


def test_s3_linked_answer_with_its_own_account_uses_that_one(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    ids = _link_customers(session_factory, H_1, H_2, H_BARE)
    _set(session_factory, ids, [1, 2, None])
    _turn(session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT)
    _reply, c2 = _turn(
        session_factory, monkeypatch, _ask([_acct("Hanlim", 1)]), "hanlim account 1", mcp_response=REPORT_HIT,
    )
    (args,) = _calls(c2, REPORT)
    assert args["customer_ids"] == [ids[0]], args


def test_s3_staff_answer_is_narrowed_as_if_the_account_was_typed(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _hanlim_staff_book(session_factory)
    services = _services(rows, {"hanlim": [H_1, H_2, H_BARE]})
    q, _c1 = _turn(
        session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding",
        resolve_services=services, mcp_response=REPORT_HIT,
    )
    assert q.strip() == "Which customer is Account 2 for?", q
    _reply, c2 = _turn(
        session_factory, monkeypatch, _ask([_ent("Hanlim")]), "hanlim", resolve_services=services,
        mcp_response=REPORT_HIT,
    )
    (args,) = _calls(c2, REPORT)
    assert args["customer_ids"] == [rows[H_2]["id"]], args


def test_s3_an_unrelated_turn_drops_the_account_so_the_next_is_not_narrowed(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _hanlim_staff_book(session_factory)
    services = _services(rows, {"hanlim": [H_1, H_2, H_BARE]})
    _turn(
        session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding",
        resolve_services=services, mcp_response=REPORT_HIT,
    )
    # An unrelated ask: no customer word at all.
    _turn(session_factory, monkeypatch, _ask(), "outstanding", resolve_services=services, mcp_response=REPORT_HIT)
    _reply, c3 = _turn(
        session_factory, monkeypatch, _ask([_ent("Hanlim")]), "hanlim", resolve_services=services,
        mcp_response=REPORT_HIT,
    )
    (args,) = _calls(c3, REPORT)
    assert sorted(args["customer_ids"]) == sorted(r["id"] for r in rows.values()), args


# ------------------------------------------------------------------ N-1 trace reasons


def _events(session_factory, monkeypatch, qf, body, **kw):
    result, _captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body, msg_id=f"ZZT-fix2-{uuid.uuid4().hex[:10]}",
        attributes=["sales_orders.outstanding"], mcp_response=REPORT_HIT, **kw,
    )
    return _scope_events(session_factory, result)


def test_n1_unnamed_question_traces_account_unnamed(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    events = _events(session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding")
    assert any(e.get("reason") == "account_unnamed" for e in events), events
    assert not any(e.get("reason") == "typed_customer_word_outside_links" for e in events), events


def test_n1_staff_level_refusal_traces_account_level_absent(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    rows = _seed_book(session_factory)
    events = _events(
        session_factory, monkeypatch, _ask([_acct("Soon Heng Trading", 2)]), "soon heng trading account 2",
        resolve_services=_services(rows, {"soon heng trading": TRADING}),
    )
    assert any(e.get("reason") == "account_level_absent" for e in events), events
    assert not any(e.get("reason") == "typed_customer_word_outside_links" for e in events), events
