"""DO-BACKFILL-CRM: a DO backfill batch is quiet in the CRM.

Plan: documentation/plans/autocount/PLAN-do-history-backfill-13oct.md section 0b.3 (Q4 (a)).
UAC:  documentation/plans/autocount/do-history-backfill-13oct-acceptance-criteria.md (AC-DH44..46).

The shared service replays AutoCount DOs from 2023-09-01 with `"backfill": true` on the envelope.
Old DO quantities must not re-decide key-less Cancel SO groups, so a backfill batch runs no
cancel relink and none of its reactions (OI retirement, the handover / purchasing email, the
planning change on the CS board). It also queues no embedding work: about 1.2M events of
OpenAI spend for history nobody searches. A plain push keeps both, as before.
"""
from __future__ import annotations

import copy

from sqlalchemy import func, select

from app.api.v1.external import ingest as ingest_route
from app.models.email_outbox import EmailOutbox
from app.models.embeddings import EmbeddingQueue
from app.services.autocount_cancel_ingest_service import Derived
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    DO_INGEST,
    _records,
    do_records,
    env,
)

SO_NUMBER = "ZZSO-DBQ1"


def _record():
    rec = copy.deepcopy(do_records()[0])
    rec["RefDocNo"] = SO_NUMBER
    for line in rec["Details"]:
        line.update({"FromDocType": "SO", "FromDocNo": SO_NUMBER})
    return rec


def _post(env, records, **extra):
    body = {"companyCode": env.company_code, "book": "db1", "records": records, **extra}
    return env.client.post(DO_INGEST, json=body)


def _spy(monkeypatch):
    """Record every relink and every cancel reaction the route starts. The relink answers as if
    the DO quantities just decided a key-less group: one line fully cancelled, one plan move."""
    calls = {"relink": [], "hooks": []}

    def relink(db, *, company_id, numbers):
        calls["relink"].append(set(numbers))
        return Derived(newly_cancelled=[("00000000-0000-0000-0000-00000000dbq1", "ZZXS-DBQ1")])

    monkeypatch.setattr(ingest_route, "relink_cancels_for_orders", relink)
    monkeypatch.setattr(ingest_route, "_run_cancel_hooks",
                        lambda db, service, *, actor: calls["hooks"].append(service.derived))
    return calls


def _outbox_rows(env) -> int:
    env.db.expire_all()
    return env.db.execute(select(func.count()).select_from(EmailOutbox)).scalar()


def _queue_rows(env, ids) -> int:
    return env.db.execute(
        select(func.count()).select_from(EmbeddingQueue)
        .where(EmbeddingQueue.source_id.in_([str(i) for i in ids]))
    ).scalar()


def _landed_ids(env, doc_key) -> list[str]:
    order = env.order(doc_key)
    return [str(order.id)] + [str(l.id) for l in env.order_lines(order.id)]


def test_backfill_batch_runs_no_cancel_relink(env, monkeypatch):
    """AC-DH44: no relink, so no reaction (OI retire, handover, planning change) can start."""
    calls = _spy(monkeypatch)
    res = _post(env, [_record()], backfill=True)
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    assert calls == {"relink": [], "hooks": []}


def _email_on_retire(monkeypatch):
    """The real cancel hooks run; the OI retirement they start (the #1530 board path, which
    queues the handover and the purchasing email) is doubled by one email_outbox row."""
    from app.services import project_order_inquiry_service as poi

    def relink(db, *, company_id, numbers):
        return Derived(newly_cancelled=[("00000000-0000-0000-0000-00000000dbq1", "ZZXS-DBQ1")])

    def retire(self, cancelled, actor_user_id=None):
        self.db.add(EmailOutbox(event_key="ZZDBQ.handover", recipient_email="zzdbq@test.com",
                                subject="ZZDBQ handover"))
        return len(cancelled)

    monkeypatch.setattr(ingest_route, "relink_cancels_for_orders", relink)
    monkeypatch.setattr(poi.ProjectOrderInquiryService, "retire_rows_for_cancelled_lines", retire)
    monkeypatch.setattr(poi, "flag_rows_for_cancelled_lines", lambda db, ids: None)


def test_backfill_batch_adds_no_email_outbox_row(env, monkeypatch):
    """AC-DH45: nothing reaches purchasing or the handover queue."""
    _email_on_retire(monkeypatch)
    before = _outbox_rows(env)
    _records(_post(env, [_record()], backfill=True))
    assert _outbox_rows(env) == before


def test_plain_batch_still_reaches_the_handover_email(env, monkeypatch):
    """AC-DH45 baseline: the same double writes its row on a plain push."""
    _email_on_retire(monkeypatch)
    before = _outbox_rows(env)
    _records(_post(env, [_record()]))
    assert _outbox_rows(env) == before + 1


def test_backfill_batch_queues_no_embedding_work(env):
    """AC-DH46: no listener event and no bulk insert, for the DO or any of its lines."""
    res = _post(env, [_record()], backfill=True)
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    ids = _landed_ids(env, 900001)
    assert _queue_rows(env, ids) == 0


def test_plain_batch_still_relinks_and_runs_the_cancel_hooks(env, monkeypatch):
    """AC-DH44 baseline: without the flag the relink runs on the numbers the DO names and its
    result reaches the cancel reactions, exactly as before."""
    calls = _spy(monkeypatch)
    res = _post(env, [_record()])
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    assert calls["relink"] == [{SO_NUMBER}]
    assert [d.newly_cancelled for d in calls["hooks"]] == [
        [("00000000-0000-0000-0000-00000000dbq1", "ZZXS-DBQ1")]]


def test_backfill_false_still_relinks(env, monkeypatch):
    calls = _spy(monkeypatch)
    _records(_post(env, [_record()], backfill=False))
    assert calls["relink"] == [{SO_NUMBER}]


def test_plain_batch_still_queues_embeddings(env):
    """AC-DH46 baseline: the listener queues the DO and its lines."""
    _records(_post(env, [_record()]))
    ids = _landed_ids(env, 900001)
    assert _queue_rows(env, ids) >= len(ids)


def test_backfill_dry_run_relinks_nothing(env, monkeypatch):
    calls = _spy(monkeypatch)
    body = {"companyCode": env.company_code, "book": "db1", "records": [_record()],
            "backfill": True}
    _records(env.client.post(f"{DO_INGEST}?dry_run=true", json=body))
    assert calls == {"relink": [], "hooks": []}
