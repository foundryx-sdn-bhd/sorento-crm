"""Sales asks as a to-do list (lane SALES-ASKS-TODO), service level: AC-ST103 to AC-ST110.

`app.services.stock_ask_service` (todo_for_agent, _apply_update, today_start_utc). Postgres
only (`blank_session`), every test seeds its own agents, customers and asks.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.base import company_scope

from . import _ask_todo_seed as seed
from ._ask_todo_seed import NOW, SORENTO, TODAY_START
from ._pg_fixture import blank_session


@pytest.fixture
def w():
    with blank_session() as db:
        with company_scope(db, frozenset({SORENTO})):
            world = seed.world(db)
            world["db"] = db
            yield world


def _svc():
    from app.services import stock_ask_service

    return stock_ask_service


# ---- AC-ST103 ---------------------------------------------------------------------------


def _user(db, name, contact_id=None):
    from app.models.user import User

    uid = seed.uid()
    row = User(id=uid, email=f"{uid}@zzt.test", name=name, status="ACTIVE", respond_contact_id=contact_id)
    db.add(row)
    db.flush()
    return row


def _stamp(row):
    return (row.done_at, row.done_by_user_id, row.done_by_contact_id)


def test_apply_update_stamps_done_and_clears_on_reopen(w):
    svc, db = _svc(), w["db"]
    row = seed.ask(db, w["x"], w["dealer"], "SRT-103")
    sean = _user(db, "Sean")
    other = _user(db, "Someone Else")

    svc._apply_update(db, row, {"note": "x"}, actor_user_id=sean.id, now=NOW)
    assert _stamp(row) == (None, None, None)

    svc._apply_update(db, row, {"state": "done"}, actor_user_id=sean.id, actor_contact_id=w["ca"], now=NOW)
    assert _stamp(row) == (NOW, sean.id, w["ca"])

    later = NOW + timedelta(hours=2)
    svc._apply_update(db, row, {"state": "done"}, actor_user_id=other.id, actor_contact_id=w["cb"], now=later)
    assert _stamp(row) == (NOW, sean.id, w["ca"])

    svc._apply_update(db, row, {"note": "y"}, actor_user_id=other.id, now=later)
    assert _stamp(row) == (NOW, sean.id, w["ca"])
    assert row.note == "y"

    svc._apply_update(db, row, {"state": "open"}, actor_user_id=sean.id, now=later)
    assert (row.state, *_stamp(row)) == ("open", None, None, None)


def test_apply_update_contact_only_and_user_only_actors(w):
    svc, db = _svc(), w["db"]
    by_contact = seed.ask(db, w["x"], w["dealer"], "SRT-CO")
    by_user = seed.ask(db, w["x"], w["dealer"], "SRT-US")
    sean = _user(db, "Sean")
    svc._apply_update(db, by_contact, {"state": "done"}, actor_contact_id=w["ca"], now=NOW)
    svc._apply_update(db, by_user, {"state": "done"}, actor_user_id=sean.id, now=NOW)
    assert _stamp(by_contact) == (NOW, None, w["ca"])
    assert _stamp(by_user) == (NOW, sean.id, None)


def test_public_update_functions_take_an_actor_and_stamp(w):
    svc, db = _svc(), w["db"]
    olive = _user(db, "Office Olive")
    a1 = seed.ask(db, w["x"], w["dealer"], "SRT-C")
    a2 = seed.ask(db, w["x"], w["dealer"], "SRT-A")
    by_customer = svc.update_for_customer(db, w["x"].id, a1.id, {"state": "done"}, actor_user_id=olive.id)
    by_agent = svc.update_for_agent(db, w["a"].id, a2.id, {"state": "done"}, actor_contact_id=w["ca"])
    assert by_customer.done_by == "Office Olive" and by_customer.done_at is not None
    assert by_agent.done_by == "Agent Alpha" and by_agent.done_at is not None
    db.expire_all()
    assert db.get(seed.StockAsk, a1.id).done_by_user_id == olive.id
    assert db.get(seed.StockAsk, a2.id).done_by_contact_id == w["ca"]


def test_update_for_agent_with_a_linked_user_prefers_the_user_name(w):
    svc, db = _svc(), w["db"]
    sean = _user(db, "Sean Ibrahim", w["ca"])
    row = seed.ask(db, w["x"], w["dealer"], "SRT-BOTH")
    out = svc.update_for_agent(
        db, w["a"].id, row.id, {"state": "done"}, actor_contact_id=w["ca"], actor_user_id=sean.id
    )
    assert out.done_by == "Sean Ibrahim"
    assert (row.done_by_user_id, row.done_by_contact_id) == (sean.id, w["ca"])


# ---- AC-ST104 ---------------------------------------------------------------------------


def test_response_carries_done_fields(w):
    svc, db = _svc(), w["db"]
    sean = _user(db, "Sean")
    by_user = seed.ask(db, w["x"], w["dealer"], "SRT-104U")
    by_contact = seed.ask(db, w["x"], w["dealer"], "SRT-104C")
    open_row = seed.ask(db, w["x"], w["dealer"], "SRT-104O")
    svc._apply_update(db, by_user, {"state": "done"}, actor_user_id=sean.id, actor_contact_id=w["ca"], now=NOW)
    svc._apply_update(db, by_contact, {"state": "done"}, actor_contact_id=w["ca"], now=NOW)
    u, c, o = (d.model_dump() for d in svc.serialize(db, [by_user, by_contact, open_row]))
    for dumped in (u, c, o):
        assert "done_at" in dumped and "done_by" in dumped
        assert not [k for k in dumped if k.endswith("_id") and k != "id"], dumped.keys()
        assert dumped.get("agent_code", "missing") is None
    assert (u["done_by"], u["done_at"]) == ("Sean", NOW)
    assert c["done_by"] == "Agent Alpha"
    assert (o["done_by"], o["done_at"]) == (None, None)


# ---- AC-ST105 / 106 ---------------------------------------------------------------------


def test_todo_scope_is_my_customers_only(w):
    svc, db = _svc(), w["db"]
    ax = seed.ask(db, w["x"], w["dealer"], "SRT-X")
    ay = seed.ask(db, w["y"], w["dealer"], "SRT-Y")
    seed.ask(db, w["z"], w["dealer"], "SRT-Z")
    seed.ask(db, None, w["dealer"], "SRT-NOCUST")
    seed.ask(db, w["x"], w["dealer"], "SRT-DONE", state="done", done_at=NOW)
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert {r.id for r in out["open"]} == {ax.id, ay.id}


def test_todo_includes_every_branch(w):
    svc, db = _svc(), w["db"]
    rows = [
        seed.ask(db, w["x"], w["dealer"], f"SRT-{b}", branch=b)
        for b in ("too_big", "in_stock", "incoming", "no_incoming")
    ]
    console = seed.ask(db, w["x"], w["dealer"], "SRT-CON", source="console")
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert {r.id for r in out["open"]} == {r.id for r in rows} | {console.id}
    assert next(r for r in out["open"] if r.id == console.id).source == "console"
    assert not hasattr(svc, "TODO_BRANCHES")


# ---- AC-ST107 ---------------------------------------------------------------------------


def test_todo_ordering(w):
    svc, db = _svc(), w["db"]
    t = NOW - timedelta(days=1)
    newest = seed.ask(db, w["x"], w["dealer"], "N", created_at=NOW - timedelta(minutes=5))
    tie_b = seed.ask(db, w["x"], w["dealer"], "TB", created_at=t, id="00000000-0000-0000-0000-00000000000b")
    tie_a = seed.ask(db, w["x"], w["dealer"], "TA", created_at=t, id="00000000-0000-0000-0000-00000000000a")
    oldest = seed.ask(db, w["y"], w["dealer"], "O", created_at=NOW - timedelta(days=5))
    d1 = seed.ask(db, w["x"], w["dealer"], "D1", state="done", done_at=NOW - timedelta(hours=2))
    d2 = seed.ask(db, w["x"], w["dealer"], "D2", state="done", done_at=NOW - timedelta(minutes=10))
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert [r.id for r in out["open"]] == [oldest.id, tie_a.id, tie_b.id, newest.id]
    assert [r.id for r in out["done_today"]] == [d2.id, d1.id]


# ---- AC-ST108 ---------------------------------------------------------------------------


def test_today_start_is_malaysia_midnight_utc(w):
    svc, db = _svc(), w["db"]
    assert svc.today_start_utc(datetime(2026, 9, 29, 3, 0)) == datetime(2026, 9, 28, 16, 0)
    assert svc.today_start_utc(datetime(2026, 9, 28, 15, 59)) == datetime(2026, 9, 27, 16, 0)
    assert svc.todo_for_agent(db, w["a"].id, now=NOW)["today_start"] == TODAY_START
    assert svc.todo_for_agent(db, w["a"].id, now=datetime(2026, 9, 28, 15, 59))["today_start"] == datetime(
        2026, 9, 27, 16, 0
    )


# ---- AC-ST109 ---------------------------------------------------------------------------


def test_done_today_window(w):
    svc, db = _svc(), w["db"]
    at_start = seed.ask(db, w["x"], w["dealer"], "AT", state="done", done_at=TODAY_START)
    seed.ask(db, w["x"], w["dealer"], "BEFORE", state="done", done_at=TODAY_START - timedelta(seconds=1))
    reopened = seed.ask(db, w["x"], w["dealer"], "REOPEN", state="open", done_at=None)
    seed.ask(db, w["z"], w["dealer"], "OTHER", state="done", done_at=NOW)
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert [r.id for r in out["done_today"]] == [at_start.id]
    assert reopened.id in {r.id for r in out["open"]}


# ---- AC-ST110 ---------------------------------------------------------------------------


def _bulk(db, w, n):
    base = NOW - timedelta(days=30)
    rows = [
        seed.StockAsk(
            id=seed.uid(),
            company_id=SORENTO,
            customer_id=w["x"].id,
            contact_id=w["dealer"],
            product_code=f"SRT-{i}",
            quantity=1,
            branch="in_stock",
            answer_summary="a",
            created_at=base + timedelta(minutes=i),
        )
        for i in range(n)
    ]
    db.add_all(rows)
    db.flush()
    return rows


def test_todo_cap_500_and_truncated_flag(w):
    svc, db = _svc(), w["db"]
    assert svc.TODO_CAP == 500
    rows = _bulk(db, w, 501)
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert len(out["open"]) == 500
    assert out["truncated"] is True
    assert rows[-1].id not in {r.id for r in out["open"]}  # the newest is the one left out
    assert out["open"][0].id == rows[0].id


def test_todo_at_exactly_500_is_not_truncated(w):
    svc, db = _svc(), w["db"]
    _bulk(db, w, 500)
    out = svc.todo_for_agent(db, w["a"].id, now=NOW)
    assert len(out["open"]) == 500
    assert out["truncated"] is False


# ---- AC-ST105b (#1366) ------------------------------------------------------------------


def _link(db, contact_id, customer):
    from app.models.access import RespondContactCustomer

    db.add(
        RespondContactCustomer(
            id=seed.uid(), contact_id=contact_id, customer_id=customer.id, company_id=SORENTO
        )
    )
    db.flush()


def test_customerless_ask_of_a_contact_linked_to_two_agents_customers_is_in_both_todos(w):
    svc, db = _svc(), w["db"]
    _link(db, w["dealer"], w["x"])  # handled by A
    _link(db, w["dealer"], w["z"])  # handled by B
    loose = seed.ask(db, None, w["dealer"], "SRT-105B")
    stranger = seed.contact(db, "Unlinked Walk-in")
    orphan = seed.ask(db, None, stranger, "SRT-ORPHAN")

    for agent in (w["a"], w["b"]):
        out = svc.todo_for_agent(db, agent.id, now=NOW)
        assert loose.id in {r.id for r in out["open"]}, agent.sales_agent
        assert orphan.id not in {r.id for r in out["open"]}
        row = next(r for r in out["open"] if r.id == loose.id)
        assert row.customer_name is None
    assert orphan.id not in {r.id for r in svc.todo_for_agent(db, None, now=NOW)["open"]}


def test_customerless_ask_is_patchable_by_both_linked_agents_and_by_nobody_else(w):
    svc, db = _svc(), w["db"]
    _link(db, w["dealer"], w["x"])
    _link(db, w["dealer"], w["z"])
    first = seed.ask(db, None, w["dealer"], "SRT-105B-1")
    second = seed.ask(db, None, w["dealer"], "SRT-105B-2")
    stranger = seed.contact(db, "Unlinked Walk-in")
    orphan = seed.ask(db, None, stranger, "SRT-ORPHAN")

    assert svc.update_for_agent(db, w["a"].id, first.id, {"state": "done"}, actor_contact_id=w["ca"]).done_by
    assert svc.update_for_agent(db, w["b"].id, second.id, {"state": "done"}, actor_contact_id=w["cb"]).done_by
    for agent, contact_id in ((w["a"], w["ca"]), (w["b"], w["cb"])):
        with pytest.raises(Exception) as exc:
            svc.update_for_agent(db, agent.id, orphan.id, {"state": "done"}, actor_contact_id=contact_id)
        assert getattr(exc.value, "status_code", None) == 404


# ---- AC-ST105c: the customer-less rule never crosses a company ---------------------------


def test_customerless_ask_in_another_company_is_not_the_agents(w):
    svc, db = _svc(), w["db"]
    cw = seed.cross_company_world(db)
    with company_scope(db, frozenset({SORENTO, cw["mocha"].id})):
        out = svc.todo_for_agent(db, cw["a"].id, now=NOW)
        ids = {r.id for r in out["open"]}
        assert cw["loose"].id in ids  # positive control: same-company customer-less ask
        assert cw["foreign"].id not in ids
        with pytest.raises(Exception) as exc:
            svc.update_for_agent(db, cw["a"].id, cw["foreign"].id, {"state": "done"}, actor_contact_id=cw["ca"])
        assert getattr(exc.value, "status_code", None) == 404
        counts = {c["agent_id"]: c for c in svc.agent_counts(db)}
        assert counts[cw["a"].id]["open"] == 1  # only `loose`
