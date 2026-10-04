"""The SLA-assignment WhatsApp template's "Form link" ({{form_url}}) for a
CONVERSATION ticket deep-links to the ticket in the CRM (`/?ticket=<id>`, the
same link the email / in-app notification already carries, UAC AC-G1), not to
the Respond.io inbox. Form-SLA rows keep their in-system record link.

No database: neither branch queries when there is no recipient and no form
entity, which is exactly the shape a conversation ticket has.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.form_sla_service import build_sla_whatsapp_data


@pytest.fixture(autouse=True)
def _urls(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "frontend_base_url", "https://crm.test/", raising=False)
    monkeypatch.setattr(settings, "respond_app_base_url", "https://app.respond.io", raising=False)
    monkeypatch.setattr(settings, "respond_space_id", "364817", raising=False)


def _conversation_ticket():
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        source_entity_type=None,
        source_entity_id=None,
        due_at=None,
        due_at_resolution=None,
        contact=SimpleNamespace(respond_io_id="900000008"),
    )


def test_a_conversation_ticket_links_to_the_crm_ticket_not_the_respond_inbox():
    t = _conversation_ticket()
    data = build_sla_whatsapp_data(None, t, None, "body")
    vars_ = data["whatsapp_context_vars"]
    assert vars_["form_url"] == f"https://crm.test/?ticket={t.id}"
    assert "respond.io" not in vars_["form_url"]
    # portal_url is the same link for this use case, so the template can use either.
    assert vars_["portal_url"] == vars_["form_url"]


def test_without_a_frontend_base_url_the_link_is_still_the_relative_ticket_path(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "frontend_base_url", None, raising=False)
    t = _conversation_ticket()
    data = build_sla_whatsapp_data(None, t, None, "body")
    assert data["whatsapp_context_vars"]["form_url"] == f"/?ticket={t.id}"


def test_a_form_sla_row_keeps_its_record_link():
    t = SimpleNamespace(
        id=str(uuid.uuid4()),
        source_entity_type="complaint",
        source_entity_id="",  # no number lookup needed for this assertion
        due_at=None,
        due_at_resolution=None,
        contact=None,
    )
    data = build_sla_whatsapp_data(None, t, None, "body")
    form_url = data["whatsapp_context_vars"]["form_url"]
    assert form_url.startswith("https://crm.test/")
    assert "?ticket=" not in form_url
