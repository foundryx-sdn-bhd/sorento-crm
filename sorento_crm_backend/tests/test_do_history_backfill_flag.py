"""DO-HISTORY C3: a quiet backfill push (envelope `"backfill": true` on delivery_orders).

Plan: documentation/plans/autocount/PLAN-do-history-backfill-13oct.md section 5.
UAC:  documentation/plans/autocount/do-history-backfill-13oct-acceptance-criteria.md (AC-DH40..42).

The shared service replays about 270,000 DOs from 2023-09-01. Through the push route each would
write an audit row for its header and an embedding event for itself and every line. With the
flag the route suppresses both for that request and writes one summary IMPORT audit row per
batch instead. Without the flag nothing changes.
"""
from __future__ import annotations

import copy

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.embeddings import EmbeddingQueue
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    DO_INGEST,
    GRN_INGEST,
    _records,
    do_records,
    env,
    grn_records,
)

def _post(env, url, records, **extra):
    body = {"companyCode": env.company_code, "book": "db1", "records": records, **extra}
    return env.client.post(url, json=body)


def _queue_rows(env, ids) -> int:
    return len(env.db.execute(
        select(EmbeddingQueue.id).where(EmbeddingQueue.source_id.in_([str(i) for i in ids]))
    ).all())


def _audit_rows(env, order_id) -> int:
    return len(env.db.execute(
        select(AuditLog.id).where(AuditLog.entity_type == "orders",
                                  AuditLog.entity_id == str(order_id))
    ).all())


def _summary_rows(env):
    return [
        r for r in env.db.execute(
            select(AuditLog).where(AuditLog.entity_type == "orders", AuditLog.action == "IMPORT")
        ).scalars().all()
        if (r.new_values or {}).get("backfill") is True
    ]


def _landed_ids(env, doc_key) -> list[str]:
    order = env.order(doc_key)
    return [str(order.id)] + [str(l.id) for l in env.order_lines(order.id)]


def test_plain_push_still_audits_and_enqueues_embeddings(env):
    """AC-DH41: the baseline the flag is measured against."""
    res = env.push_do([copy.deepcopy(do_records()[0])])
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    ids = _landed_ids(env, 900001)
    assert _queue_rows(env, ids) >= len(ids)
    assert _audit_rows(env, ids[0]) >= 1
    assert _summary_rows(env) == []


def test_backfill_push_writes_no_row_audit_and_no_embedding_event(env):
    """AC-DH40."""
    res = _post(env, DO_INGEST, [copy.deepcopy(r) for r in do_records()], backfill=True)
    recs = _records(res)
    assert recs["db1:DO:900001"]["outcome"] == "created"
    for doc_key in (900001, 900002):
        ids = _landed_ids(env, doc_key)
        assert _queue_rows(env, ids) == 0
        assert _audit_rows(env, ids[0]) == 0
    (summary,) = _summary_rows(env)
    assert summary.new_values["book"] == "db1"
    assert summary.new_values["created"] == 2
    assert summary.new_values["records"] == 2


def test_suppression_ends_with_the_backfill_request(env):
    """AC-DH40: a later plain push on the same session audits and enqueues again."""
    _post(env, DO_INGEST, [copy.deepcopy(do_records()[0])], backfill=True)
    env.push_do([copy.deepcopy(do_records()[1])])
    ids = _landed_ids(env, 900002)
    assert _queue_rows(env, ids) >= len(ids)
    assert _audit_rows(env, ids[0]) >= 1


def test_backfill_dry_run_writes_nothing(env):
    """AC-DH40 + dry run: no DO, no summary row."""
    body = {"companyCode": env.company_code, "book": "db1",
            "records": [copy.deepcopy(do_records()[0])], "backfill": True}
    res = env.client.post(f"{DO_INGEST}?dry_run=true", json=body)
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    assert _summary_rows(env) == []


def test_backfill_flag_must_be_a_boolean(env):
    res = _post(env, DO_INGEST, [copy.deepcopy(do_records()[0])], backfill="yes")
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "INVALID_BODY"


def test_backfill_flag_is_refused_on_other_entities(env):
    """Only delivery_orders knows the flag; elsewhere it is an error, never silently ignored."""
    res = _post(env, GRN_INGEST, [copy.deepcopy(grn_records()[0])], backfill=True)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "INVALID_BODY"


def test_backfill_false_is_a_plain_push(env):
    res = _post(env, DO_INGEST, [copy.deepcopy(do_records()[0])], backfill=False)
    assert _records(res)["db1:DO:900001"]["outcome"] == "created"
    ids = _landed_ids(env, 900001)
    assert _queue_rows(env, ids) >= len(ids)
    assert _summary_rows(env) == []
