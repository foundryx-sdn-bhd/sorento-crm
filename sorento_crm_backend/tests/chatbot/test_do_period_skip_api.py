"""DO-PERIOD-SKIP AC-1 to AC-4: the contact's "Skip date-range questions" switch over the API.

`respond_contacts.chatbot_skip_period_questions` (default false). `PUT .../contacts/{id}/chatbot`
takes it (absent = leave alone), every contact read carries it (`contact_to_response_dict` is built
by hand and `response_model` drops undeclared fields, so it is asserted), and Copy access copies it.
Mirrors `test_escalation_control_api.py`.
"""
from __future__ import annotations

from sqlalchemy import text

from tests.chatbot.test_escalation_control_api import (  # noqa: F401 - fixtures + helpers
    BASE,
    _GRANTS,
    _permissions,
    _seed_contact,
    client,
    db,
)

FIELD = "chatbot_skip_period_questions"


def test_ac1_the_model_maps_the_column_default_false(db):  # noqa: F811
    from app.models.access import RespondContact

    assert hasattr(RespondContact, FIELD)
    contact_id = _seed_contact(db)
    stored = db.execute(text(f"SELECT {FIELD} FROM respond_contacts WHERE id = :c"), {"c": contact_id}).scalar()
    assert stored is False


def test_ac2_a_new_contact_reads_false_on_get(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    assert client.get(f"{BASE}/{contact_id}").json()[FIELD] is False


def test_ac2_a_switched_on_contact_reads_true_on_get_and_in_the_list(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    db.execute(text(f"UPDATE respond_contacts SET {FIELD} = true WHERE id = :c"), {"c": contact_id})
    db.commit()
    assert client.get(f"{BASE}/{contact_id}").json()[FIELD] is True
    listed = client.get(BASE, params={"limit": 100})
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    rows = payload.get("items") or payload.get("data") or payload
    mine = [r for r in rows if r.get("id") == contact_id]
    assert mine and mine[0][FIELD] is True


def test_ac3_put_sets_and_clears_it_and_absent_leaves_it_alone(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: True})
    assert resp.status_code == 200, resp.text
    assert resp.json()[FIELD] is True

    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"notify_salesman": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()[FIELD] is True

    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: False})
    assert resp.json()[FIELD] is False
    stored = db.execute(text(f"SELECT {FIELD} FROM respond_contacts WHERE id = :c"), {"c": contact_id}).scalar()
    assert stored is False


def test_ac3_writing_it_needs_the_contact_edit_grant(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    _GRANTS.discard("user_management.contacts.edit")
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: True})
    assert resp.status_code == 403, resp.text


def test_ac3_a_non_boolean_is_a_422(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: "maybe"})
    assert resp.status_code == 422, resp.text


def test_ac4_copy_access_lists_the_switch():
    from app.services.contact_access_copy_service import SWITCHES

    assert FIELD in [column for column, _label in SWITCHES]
