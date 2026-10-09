"""CA-1 `facts.sweep(rebuild=True)`: reload a date range under the current rules, no TRUNCATE.

Tampering a fact row's outcome/domain simulates a row written under an old rule.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from tests.chatbot._chat_facts_helpers import (
    episode_rows,
    fact_rows,
    facts_mod,
    models_mod,
    register_company_scope,
    seed_contact,
    seed_turn,
)

UTC = timezone.utc
SINCE = datetime(2026, 9, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope()


def _cid(tag: str) -> str:
    return f"ZZT-rb-{tag}-{uuid.uuid4().hex[:8]}"


def _day(d: int, h: int = 3) -> datetime:
    return datetime(2026, 9, d, h, 0, tzinfo=UTC)


def _tamper(session_factory, turn_id: str, **cols) -> None:
    fact = models_mod().ChatAskFact
    db = session_factory()
    db.query(fact).filter(fact.turn_id == turn_id).update(cols, synchronize_session=False)
    db.commit()


def _swept(session_factory, cid: str, *turns_at: datetime) -> list[str]:
    seed_contact(session_factory, cid, customer=True)
    ids = [seed_turn(session_factory, cid, created_at=at) for at in turns_at]
    assert facts_mod().sweep(session_factory(), since=SINCE) == len(ids)
    return ids


def test_rebuild_restores_the_rule_outcome_and_domain_without_a_duplicate(session_factory):
    (tid,) = _swept(session_factory, _cid("a"), _day(10))
    good = fact_rows(session_factory, turn_id=tid)[0]
    _tamper(session_factory, tid, outcome="ZZT-old-rule", domain="ZZT-old-domain")

    written = facts_mod().sweep(session_factory(), since=SINCE, until=_day(30), rebuild=True)

    assert written == 1
    rows = fact_rows(session_factory, turn_id=tid)
    assert len(rows) == 1
    assert (rows[0].outcome, rows[0].domain) == (good.outcome, good.domain)


def test_rebuild_leaves_rows_outside_the_range_untouched(session_factory):
    first, second = _swept(session_factory, _cid("b"), _day(10), _day(20))
    _tamper(session_factory, first, outcome="ZZT-old-rule")
    _tamper(session_factory, second, outcome="ZZT-old-rule")

    written = facts_mod().sweep(session_factory(), since=_day(10, 0), until=_day(11, 0), rebuild=True)

    assert written == 1
    assert fact_rows(session_factory, turn_id=first)[0].outcome != "ZZT-old-rule"
    assert fact_rows(session_factory, turn_id=second)[0].outcome == "ZZT-old-rule"


def test_rebuild_keeps_the_episode_id_of_each_fact_row(session_factory):
    (tid,) = _swept(session_factory, _cid("c"), _day(10))
    episode_id = str(uuid.uuid4())
    _tamper(session_factory, tid, episode_id=episode_id, outcome="ZZT-old-rule")

    assert facts_mod().sweep(session_factory(), since=SINCE, until=_day(30), rebuild=True) == 1

    row = fact_rows(session_factory, turn_id=tid)[0]
    assert row.outcome != "ZZT-old-rule"
    assert str(row.episode_id) == episode_id


def test_rebuild_does_not_touch_episode_fact_rows(session_factory):
    cid = _cid("d")
    (tid,) = _swept(session_factory, cid, _day(10))
    models = models_mod()
    frame_id = str(uuid.uuid4())
    db = session_factory()
    db.add(
        models.ChatEpisodeFact(
            frame_id=frame_id, contact_respond_id=cid, opened_at=_day(10), closed_at=_day(10, 4),
            turn_count=1, ask_count=1, failed_turns=0, last_outcome="ZZT-keep", handover=False,
        )
    )
    db.commit()

    facts_mod().sweep(session_factory(), since=SINCE, until=_day(30), rebuild=True)

    eps = episode_rows(session_factory, frame_id=frame_id)
    assert len(eps) == 1 and eps[0].last_outcome == "ZZT-keep"


def test_without_rebuild_a_tampered_row_stays_tampered(session_factory):
    (tid,) = _swept(session_factory, _cid("e"), _day(10))
    _tamper(session_factory, tid, outcome="ZZT-old-rule")

    assert facts_mod().sweep(session_factory(), since=SINCE, until=_day(30)) == 0
    assert fact_rows(session_factory, turn_id=tid)[0].outcome == "ZZT-old-rule"


def test_rebuild_never_writes_test_console_or_open_turns(session_factory):
    cid = _cid("f")
    seed_contact(session_factory, cid)
    seed_turn(session_factory, cid, created_at=_day(10), is_test=True)
    seed_turn(session_factory, cid, created_at=_day(10, 4), ingress="console")
    seed_turn(session_factory, cid, created_at=_day(10, 5), status="processing")

    assert facts_mod().sweep(session_factory(), since=SINCE, until=_day(30), rebuild=True) == 0
    assert fact_rows(session_factory, contact_respond_id=cid) == []


def test_the_task_doorway_passes_rebuild_through(session_factory):
    from app.tasks.chat_turns import sweep_chat_facts

    (tid,) = _swept(session_factory, _cid("g"), _day(10))
    _tamper(session_factory, tid, outcome="ZZT-old-rule")

    assert sweep_chat_facts(session_factory(), since=SINCE, until=_day(30), rebuild=True) == 1
    assert fact_rows(session_factory, turn_id=tid)[0].outcome != "ZZT-old-rule"


def test_an_interrupted_rebuild_keeps_the_old_fact_rows_and_their_episodes(
    session_factory, monkeypatch
):
    first, second = _swept(session_factory, _cid("h"), _day(10), _day(11))
    episodes = {first: str(uuid.uuid4()), second: str(uuid.uuid4())}
    for tid, episode_id in episodes.items():
        _tamper(session_factory, tid, episode_id=episode_id)

    facts = facts_mod()
    real_write = facts._write
    calls = {"n": 0}

    def interrupted(db, row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_write(db, row)

    monkeypatch.setattr(facts, "_write", interrupted)
    db = session_factory()
    with pytest.raises(KeyboardInterrupt):
        facts.sweep(db, since=SINCE, until=_day(30), rebuild=True)
    db.rollback()

    for tid, episode_id in episodes.items():
        rows = fact_rows(session_factory, turn_id=tid)
        assert len(rows) == 1
        assert str(rows[0].episode_id) == episode_id
