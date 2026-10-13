"""MODEL-PRICES: the model price routes, audit rows and the delete pending action (MP-2..MP-8).

Plan: documentation/plans/analytics/model-prices-acceptance-criteria.md. Route level on purpose:
the permission gate and the audit trail are part of what the UAC promises. Runs on a blank
Postgres schema, so `llm_model_prices` starts empty and every assertion is scoped to its rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.services.audit_service import register_audit_listeners
from tests._ai_usage_client import EDIT, VIEW, api_client
from tests._pg_fixture import blank_session, unique_code

register_audit_listeners()

BASE = "/api/v1/system/ai-assistant/model-prices"


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _body(model=None, **over):
    body = {"model": model or f"zzt-{unique_code('m', alpha=True)}", "input_usd_per_1m": 0.75,
            "cached_input_usd_per_1m": 0.075, "output_usd_per_1m": 4.5, "note": "owner, 13 Oct"}
    body.update(over)
    return body


def _create(db, **over):
    with api_client(db, [VIEW, EDIT]) as client:
        res = client.post(BASE, json=_body(**over))
    assert res.status_code == 201, res.text
    return res.json()


def _audits(db, entity_id):
    return db.execute(
        text("SELECT action FROM audit_logs WHERE entity_type = 'llm_model_prices' AND entity_id = :i ORDER BY changed_at"),
        {"i": str(entity_id)},
    ).scalars().all()


def _usage(db, model, n=1, at=None):
    for _ in range(n):
        db.execute(
            text("INSERT INTO ai_assistant_usage_logs (id, feature, provider, model, prompt_tokens, completion_tokens, "
                 "total_tokens, tool_calls_count, response_time_ms, was_answered, created_at) "
                 "VALUES (:id, 'chatbot_parser', 'gemini', :m, 10, 1, 11, 0, 1, true, :at)"),
            {"id": str(uuid.uuid4()), "m": model, "at": at or datetime(2026, 10, 1, 8, 0, 0)},
        )


# ---- MP-3 create ------------------------------------------------------------------------------

def test_mp3_create_returns_201_with_the_row_and_numbers_as_floats(db):
    body = _create(db, model="zzt-create-1")
    assert body["model"] == "zzt-create-1" and body["id"]
    assert body["input_usd_per_1m"] == 0.75 and body["cached_input_usd_per_1m"] == 0.075
    assert body["output_usd_per_1m"] == 4.5 and body["note"] == "owner, 13 Oct"
    assert isinstance(body["input_usd_per_1m"], float) and body["updated_at"]


def test_mp3_create_without_cached_price_or_note(db):
    body = _create(db, cached_input_usd_per_1m=None, note=None)
    assert body["cached_input_usd_per_1m"] is None and body["note"] is None


def test_mp3_duplicate_model_is_409_already_exists(db):
    _create(db, model="zzt-dup")
    with api_client(db, [VIEW, EDIT]) as client:
        res = client.post(BASE, json=_body("zzt-dup"))
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


@pytest.mark.parametrize("over", [
    {"input_usd_per_1m": -0.01},
    {"output_usd_per_1m": -1},
    {"cached_input_usd_per_1m": -0.5},
])
def test_mp3_negative_price_is_422(db, over):
    with api_client(db, [VIEW, EDIT]) as client:
        assert client.post(BASE, json=_body(**over)).status_code == 422


@pytest.mark.parametrize("missing", ["input_usd_per_1m", "output_usd_per_1m", "model"])
def test_mp3_missing_required_field_is_422(db, missing):
    body = _body()
    body.pop(missing)
    with api_client(db, [VIEW, EDIT]) as client:
        assert client.post(BASE, json=body).status_code == 422


# ---- MP-2 list --------------------------------------------------------------------------------

def test_mp2_list_returns_every_row_with_all_fields(db):
    a = _create(db, model="zzt-list-a")
    b = _create(db, model="zzt-list-b", cached_input_usd_per_1m=None)
    with api_client(db, [VIEW]) as client:
        res = client.get(BASE)
    assert res.status_code == 200
    rows = {r["model"]: r for r in res.json()}
    assert set(rows) == {"zzt-list-a", "zzt-list-b"}
    assert set(rows["zzt-list-a"]) >= {"id", "model", "input_usd_per_1m", "cached_input_usd_per_1m",
                                       "output_usd_per_1m", "note", "updated_at"}
    assert rows["zzt-list-a"]["id"] == a["id"] and rows["zzt-list-b"]["cached_input_usd_per_1m"] is None
    assert b["id"] == rows["zzt-list-b"]["id"]


# ---- MP-4 update ------------------------------------------------------------------------------

def test_mp4_update_changes_prices_note_keeps_model_and_moves_updated_at(db):
    row = _create(db, model="zzt-upd")
    db.execute(text("UPDATE llm_model_prices SET updated_at = :t WHERE id = :i"),
               {"t": datetime.utcnow() - timedelta(days=2), "i": row["id"]})
    with api_client(db, [VIEW, EDIT]) as client:
        res = client.put(f"{BASE}/{row['id']}", json={"model": "ignored-rename", "input_usd_per_1m": 1.25,
                                                      "cached_input_usd_per_1m": None,
                                                      "output_usd_per_1m": 6, "note": "raised"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["model"] == "zzt-upd"
    assert body["input_usd_per_1m"] == 1.25 and body["cached_input_usd_per_1m"] is None
    assert body["output_usd_per_1m"] == 6 and body["note"] == "raised"
    assert datetime.fromisoformat(body["updated_at"]) > datetime.utcnow() - timedelta(hours=1)


def test_mp4_update_unknown_id_is_404(db):
    row = _create(db)  # the route exists (a real id updates), so the 404 below is the id, not a missing route
    with api_client(db, [VIEW, EDIT]) as client:
        assert client.put(f"{BASE}/{row['id']}", json={"input_usd_per_1m": 1, "output_usd_per_1m": 2}).status_code == 200
        res = client.put(f"{BASE}/{uuid.uuid4()}", json={"input_usd_per_1m": 1, "output_usd_per_1m": 2})
    assert res.status_code == 404


def test_mp4_update_negative_price_is_422(db):
    row = _create(db)
    with api_client(db, [VIEW, EDIT]) as client:
        res = client.put(f"{BASE}/{row['id']}", json={"input_usd_per_1m": -1, "output_usd_per_1m": 2})
    assert res.status_code == 422


# ---- delete route -----------------------------------------------------------------------------

def test_delete_hard_deletes_204_and_unknown_is_404(db):
    row = _create(db, model="zzt-del")
    with api_client(db, [VIEW, EDIT]) as client:
        assert client.delete(f"{BASE}/{row['id']}").status_code == 204
        assert client.get(BASE).json() == []
        assert client.delete(f"{BASE}/{row['id']}").status_code == 404
    assert db.execute(text("SELECT count(*) FROM llm_model_prices WHERE id = :i"), {"i": row["id"]}).scalar() == 0


# ---- MP-7 gates -------------------------------------------------------------------------------

def test_mp7_view_only_user_reads_but_cannot_write(db):
    row = _create(db, model="zzt-gate")
    with api_client(db, [VIEW]) as client:
        assert client.get(BASE).status_code == 200
        assert client.get(f"{BASE}/unpriced").status_code == 200
        assert client.post(BASE, json=_body()).status_code == 403
        assert client.put(f"{BASE}/{row['id']}", json={"input_usd_per_1m": 1, "output_usd_per_1m": 2}).status_code == 403
        assert client.delete(f"{BASE}/{row['id']}").status_code == 403
    assert db.execute(text("SELECT count(*) FROM llm_model_prices WHERE id = :i"), {"i": row["id"]}).scalar() == 1


def test_mp7_user_without_view_cannot_read(db):
    with api_client(db, []) as client:
        assert client.get(BASE).status_code == 403
        assert client.get(f"{BASE}/unpriced").status_code == 403


# ---- MP-6 unpriced ----------------------------------------------------------------------------

def test_mp6_unpriced_lists_distinct_models_without_a_price_most_calls_first(db):
    _create(db, model="zzt-has-price")
    _usage(db, "zzt-has-price", 2)
    _usage(db, "zzt-gemini-few", 1, at=datetime(2026, 10, 2, 8, 0, 0))
    _usage(db, "zzt-gemini-many", 3, at=datetime(2026, 10, 5, 8, 0, 0))
    _usage(db, None, 2)
    with api_client(db, [VIEW]) as client:
        res = client.get(f"{BASE}/unpriced")
    assert res.status_code == 200
    rows = res.json()
    assert [r["model"] for r in rows] == ["zzt-gemini-many", "zzt-gemini-few"]
    assert [r["calls"] for r in rows] == [3, 1]
    assert rows[0]["last_seen_at"].startswith("2026-10-05")


def test_mp6_a_saved_price_removes_the_model_from_unpriced(db):
    _usage(db, "zzt-gemini-3.5", 2)
    with api_client(db, [VIEW, EDIT]) as client:
        assert [r["model"] for r in client.get(f"{BASE}/unpriced").json()] == ["zzt-gemini-3.5"]
        assert client.post(BASE, json=_body("zzt-gemini-3.5")).status_code == 201
        assert client.get(f"{BASE}/unpriced").json() == []
        assert [r["model"] for r in client.get(BASE).json()] == ["zzt-gemini-3.5"]


# ---- MP-8 audit -------------------------------------------------------------------------------

def test_mp8_create_update_delete_each_write_an_audit_row(db):
    row = _create(db, model="zzt-audit")
    assert _audits(db, row["id"]) == ["INSERT"]
    with api_client(db, [VIEW, EDIT]) as client:
        assert client.put(f"{BASE}/{row['id']}", json={"input_usd_per_1m": 2, "output_usd_per_1m": 3}).status_code == 200
        assert _audits(db, row["id"]) == ["INSERT", "UPDATE"]
        assert client.delete(f"{BASE}/{row['id']}").status_code == 204
    assert _audits(db, row["id"]) == ["INSERT", "UPDATE", "DELETE"]


# ---- MP-5 pending action ----------------------------------------------------------------------

def test_mp5_delete_pending_action_is_registered_with_the_edit_gate():
    import app.services.record_actions  # noqa: F401
    from app.services.form_action_registry import get_action

    action = get_action("llm_model_price.delete")
    assert action is not None
    assert action.entity_types == ("llm_model_price",)
    assert action.permission == "system.ai_assistant_settings.edit"


def test_mp5_delete_pending_action_deletes_the_row(db):
    import app.services.record_actions  # noqa: F401
    from app.services.form_action_registry import get_action

    row = _create(db, model="zzt-pending")
    get_action("llm_model_price.delete").execute(db, {"entity_id": row["id"]})
    db.flush()
    assert db.execute(text("SELECT count(*) FROM llm_model_prices WHERE id = :i"), {"i": row["id"]}).scalar() == 0
