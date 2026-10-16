"""AC-12: retrieval returns at most k enabled examples with an embedding, nearest first.

Postgres blank schema (pgvector). Rows are inserted with raw SQL against `chatbot_examples`
so the test does not depend on an ORM class name; the blank schema is built by
`create_all`, so the coder's ORM model for the table must be registered in
`Base.metadata` (imported by `app.models`) or this table is missing here.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from tests._pg_fixture import blank_session

DIM = 1536


def _vec(*head: float) -> str:
    comps = list(head) + [0.0] * (DIM - len(head))
    return "[" + ",".join(str(c) for c in comps) + "]"


def _insert(db, *, area, message, embedding, enabled=True, source="synthetic", expected=None, context=None):
    db.execute(
        text(
            "INSERT INTO chatbot_examples (id, area, message, context, expected_output, embedding, source, enabled) "
            "VALUES (:id, :area, :message, :context, CAST(:eo AS jsonb), CAST(:emb AS vector), :source, :enabled)"
        ),
        {
            "id": str(uuid.uuid4()),
            "area": area,
            "message": message,
            "context": context,
            "eo": json.dumps(expected if expected is not None else {"user_goal": message}),
            "emb": embedding,
            "source": source,
            "enabled": enabled,
        },
    )


@pytest.fixture()
def db():
    with blank_session() as session:
        yield session


def _query() -> list[float]:
    return [1.0, 0.0] + [0.0] * (DIM - 2)


def test_ac12_nearest_first_and_at_most_k(db) -> None:
    from app.services.chatbot.head import prompt_diet

    _insert(db, area="sales", message="far (Test Dealer A)", embedding=_vec(0.0, 1.0))
    _insert(db, area="inventory", message="exact SRT-TEST-1", embedding=_vec(1.0, 0.0), context="ctx one")
    _insert(db, area="order", message="near", embedding=_vec(1.0, 1.0))
    db.flush()

    got = prompt_diet.nearest_examples(db, _query(), k=2)
    assert [e.message for e in got] == ["exact SRT-TEST-1", "near"]
    first = got[0]
    assert (first.area, first.context) == ("inventory", "ctx one")
    assert first.expected_output == {"user_goal": "exact SRT-TEST-1"}


def test_ac12_default_k_is_five(db) -> None:
    from app.services.chatbot.head import prompt_diet

    for i in range(8):
        _insert(db, area="sales", message=f"row {i}", embedding=_vec(1.0, i * 0.1))
    db.flush()
    assert len(prompt_diet.nearest_examples(db, _query())) == 5


def test_ac12_disabled_rows_are_skipped(db) -> None:
    from app.services.chatbot.head import prompt_diet

    _insert(db, area="sales", message="disabled but exact", embedding=_vec(1.0, 0.0), enabled=False)
    _insert(db, area="sales", message="enabled but far", embedding=_vec(0.0, 1.0))
    db.flush()
    got = prompt_diet.nearest_examples(db, _query(), k=5)
    assert [e.message for e in got] == ["enabled but far"]


def test_ac12_rows_without_an_embedding_are_skipped(db) -> None:
    from app.services.chatbot.head import prompt_diet

    _insert(db, area="sales", message="no vector yet", embedding=None)
    _insert(db, area="sales", message="has vector", embedding=_vec(0.0, 1.0))
    db.flush()
    got = prompt_diet.nearest_examples(db, _query(), k=5)
    assert [e.message for e in got] == ["has vector"]


def test_ac12_empty_table_returns_empty_list(db) -> None:
    from app.services.chatbot.head import prompt_diet

    assert prompt_diet.nearest_examples(db, _query(), k=5) == []
