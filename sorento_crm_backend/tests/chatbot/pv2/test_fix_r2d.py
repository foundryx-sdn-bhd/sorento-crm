"""PV2-FIX r2 batch D (dev run r2-2): no null user_goal, unique customer-family labels."""
from __future__ import annotations

import uuid

import pytest

from app.models.order import Customer, CustomerGroup
from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.resolve import DbResolver, Entity, Held
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import dump_state
from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, option, out, pending

SRT = {"entities": {"product|SRTWC286": {"status": "exact", "refs": [
    {"kind": "product", "key": "SRTWC286", "label": "SRTWC286"}]}}}
TEXT = "any stock of srtwc286 please"


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        yield session


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _run(parsed, state):
    ctx = turn.TurnV2Context(
        text=TEXT, now=NOW, raw_state=dump_state(state), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=FakeResolver(SRT), load_exchanges=lambda s: [],
    )
    return turn._run(ctx)


# ------------------------------------------------------------------ 1 user_goal


def test_a_lookup_verdict_carries_the_customers_own_message_as_user_goal():
    ent = {"kind": "product", "key": None, "phrase": "SRTWC286", "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": None}
    r = _run(out(_ask(domain="inventory", entities=[ent])), mk_state())
    assert r.verdict is not None
    assert r.verdict["user_goal"] == TEXT


def test_a_v1_open_question_pick_verdict_carries_user_goal_too():
    state = mk_state(mk_held("inventory", {"product": ["SRTWC286"]}),
                     pending_=pending("v1_open_question", [option("1", "Option A"), option("2", "Option B")]))
    r = _run(out(_ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None})), state)
    assert r.verdict is not None
    assert r.verdict["open_question_answer"]["mode"] == "pick"
    assert r.verdict["user_goal"] == TEXT


# ------------------------------------------------------------------ 2 unique family labels


def _customer(db, code, name, group_id=None):
    row = Customer(id=str(uuid.uuid4()), customer_code=code, customer_name=name, is_active=True,
                   company_id=DEFAULT_COMPANY_ID, customer_group_id=group_id)
    db.add(row)
    db.flush()
    return row


def test_a_grouped_and_an_ungrouped_ledger_never_show_two_options_with_one_label(db):
    group = CustomerGroup(id=str(uuid.uuid4()), name="FULLSHUN SANITARYWARE SDN BHD", company_id=DEFAULT_COMPANY_ID)
    db.add(group)
    db.flush()
    _customer(db, "FSS1", "FULLSHUN SANITARYWARE SDN BHD [A/C I]", group.id)
    _customer(db, "FSS2", "FULLSHUN SANITARYWARE SDN BHD - [CERAMIC]", group.id)
    _customer(db, "FSS3", "FULLSHUN SANITARYWARE SDN BHD - [IBORN]")
    _customer(db, "FSB1", "FULLSHUN BATH CONCEPT SDN BHD (PROJECT)")
    ent = Entity.model_construct(kind="customer", key=None, phrase="Fullshun", op="set", refers_to_held=False,
                                 doc_number=None, quantity=None)
    # The engine holds the office's groups for the whole turn (`ledger_family.customer_groups`).
    from app.services.customer_group_service import family_overrides
    from app.services.ledger_family import customer_groups

    with customer_groups(family_overrides(db)):
        res = DbResolver(db, pinned={}).resolve_entities([ent], held=Held())[0]
    assert res.status == "ambiguous"
    labels = [o.label for o in res.options]
    assert len(labels) == len(set(labels)), labels
    assert len(res.options) == 2, labels
    san = next(o for o in res.options if "SANITARYWARE" in o.label)
    assert set(san.payload.get("keys") or []) == {"FSS1", "FSS2", "FSS3"}
