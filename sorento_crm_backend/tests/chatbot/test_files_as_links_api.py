"""PHOTO-SEND-CAP round 2, owner Q1 + Q4 (9 Oct 2026): the two switches staff use.

* `respond_contacts.chatbot_files_as_links` (default false = attachment mode): the
  per-contact A/B switch on the contact's Chatbot card. `PUT .../contacts/{id}/chatbot`
  takes it (absent = leave alone) and every contact read carries it -
  `contact_to_response_dict` is built by hand and `response_model` drops undeclared fields.
* `system_settings.chatbot_file_link_expiry_days` (null = never, the owner's choice for now).
Mirrors `test_do_period_skip_api.py`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from tests.chatbot.test_escalation_control_api import (  # noqa: F401 - fixtures + helpers
    BASE,
    _GRANTS,
    _permissions,
    _seed_contact,
    client,
    db,
)

FIELD = "chatbot_files_as_links"
SETTINGS = "/api/v1/user-management/settings/"
SETTINGS_GENERAL = "/api/v1/user-management/settings/general"


def test_the_column_defaults_to_attachment_mode(db):  # noqa: F811
    contact_id = _seed_contact(db)
    stored = db.execute(text(f"SELECT {FIELD} FROM respond_contacts WHERE id = :c"), {"c": contact_id}).scalar()
    assert stored is False


def test_get_carries_the_switch(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    assert client.get(f"{BASE}/{contact_id}").json()[FIELD] is False
    db.execute(text(f"UPDATE respond_contacts SET {FIELD} = true WHERE id = :c"), {"c": contact_id})
    db.commit()
    assert client.get(f"{BASE}/{contact_id}").json()[FIELD] is True


def test_put_sets_and_clears_it_and_absent_leaves_it_alone(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: True})
    assert resp.status_code == 200, resp.text
    assert resp.json()[FIELD] is True
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={"notify_salesman": True})
    assert resp.json()[FIELD] is True
    resp = client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: False})
    assert resp.json()[FIELD] is False


def test_writing_it_needs_the_contact_edit_grant(db, client):  # noqa: F811
    contact_id = _seed_contact(db)
    _GRANTS.discard("user_management.contacts.edit")
    assert client.put(f"{BASE}/{contact_id}/chatbot", json={FIELD: True}).status_code == 403


def test_the_turn_profile_reads_the_switch(db):  # noqa: F811
    from app.services.chatbot import turn_runtime

    contact_id = _seed_contact(db)
    respond_id = db.execute(text("SELECT respond_io_id FROM respond_contacts WHERE id = :c"), {"c": contact_id}).scalar()
    profile, _recall = turn_runtime.load_profile(db, respond_id)
    assert profile.files_as_links is False
    db.execute(text(f"UPDATE respond_contacts SET {FIELD} = true WHERE id = :c"), {"c": contact_id})
    db.commit()
    profile, _recall = turn_runtime.load_profile(db, respond_id)
    assert profile.files_as_links is True


def _settings_row(db, **columns):
    from app.models.user import SystemSetting

    db.add(SystemSetting(id=str(uuid.uuid4()), name="ZZT links co", **columns))
    db.commit()


def test_settings_carry_the_link_expiry_default_never(db, client):  # noqa: F811
    _GRANTS.update({"user_management.settings.view", "user_management.settings.edit"})
    _settings_row(db)
    body = client.get(SETTINGS).json()["settings"]
    assert "chatbot_file_link_expiry_days" in body
    assert body["chatbot_file_link_expiry_days"] is None


def test_settings_save_and_clear_the_link_expiry(db, client):  # noqa: F811
    _GRANTS.update({"user_management.settings.view", "user_management.settings.edit"})
    _settings_row(db)
    saved = client.post(SETTINGS_GENERAL, json={"chatbot_file_link_expiry_days": 30})
    assert saved.status_code == 200, saved.text
    assert client.get(SETTINGS).json()["settings"]["chatbot_file_link_expiry_days"] == 30
    cleared = client.post(SETTINGS_GENERAL, json={"chatbot_file_link_expiry_days": None})
    assert cleared.status_code == 200, cleared.text
    assert client.get(SETTINGS).json()["settings"]["chatbot_file_link_expiry_days"] is None


def test_settings_refuse_a_zero_or_negative_expiry(db, client):  # noqa: F811
    _GRANTS.update({"user_management.settings.edit"})
    _settings_row(db)
    assert client.post(SETTINGS_GENERAL, json={"chatbot_file_link_expiry_days": 0}).status_code == 422
