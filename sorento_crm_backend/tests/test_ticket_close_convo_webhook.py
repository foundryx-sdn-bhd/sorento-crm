"""The respond-close-convo direct webhook fired when a CRM resolve empties the
contact's open-ticket set.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-M3 (a CRM resolve with no OPEN sibling calls the NEW direct webhook with
            a deterministic payload: tracking id, contact respond_io_id + phone,
            resolved_by as the real CRM identity, resolved_at as aware UTC ISO,
            source "User"; plus the hardening block - idempotency key,
            closedBySource enum, neutral resolver fallback, shared secret)
     AC-M4  (the close message stays gated on "the contact has no open ticket",
            which is exactly the gate this webhook is fired behind)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S4.5)

Asserted at the real seam - the ``n8n_crm_close_convo`` integration_log row the
notifier writes, which carries the exact payload that would be POSTed. Only the
daemon thread / HTTP boundary is stubbed.

Run:
    venv/bin/pytest tests/test_ticket_close_convo_webhook.py -q
"""
from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.models.access import AccessAgent, AgentTeam, RespondContact, Team
from app.models.integration import IntegrationLog
from app.models.sla import SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.schemas.sla import ConversationSLATrackingCreate, ConversationSLATrackingUpdate
from app.services.sla_service import ConversationSLATrackingService
from tests._pg_fixture import blank_session

PHONE = "+60100000079"
RESPOND_IO_ID = "10025599"
RESOLVER_RESPOND_ID = "900002"
CLOSE_WEBHOOK_URL = "https://n8n.test/webhook/respond-close-convo"
WEBHOOK_SECRET = "zzt-shared-secret"
SECRET_HEADER = "X-CRM-Webhook-Secret"
TEAM_NAME = "ZZT Customer Service - Tier 1"


@pytest.fixture
def db(monkeypatch):
    import app.services.queue_service as queue_service

    # The pre-existing best-effort RQ close job stays wired; it must not reach a
    # broker from a test (and it is NOT what this file is about).
    monkeypatch.setattr(queue_service, "enqueue_job", lambda *a, **k: None)

    with blank_session() as session:
        schema = session.get_bind()._execution_options["schema_translate_map"][None]
        session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        yield session


@pytest.fixture(autouse=True)
def _no_webhook_http():
    """The POST runs on a daemon thread - stub the thread so the test asserts the
    payload, not the network."""
    with patch("app.services.crm_close_convo_webhook.threading"):
        yield


@pytest.fixture(autouse=True)
def _webhook_config(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "n8n_crm_webhook_secret", WEBHOOK_SECRET, raising=False)
    monkeypatch.setenv("N8N_CRM_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(
        settings, "n8n_close_convo_webhook_url", CLOSE_WEBHOOK_URL, raising=False
    )
    monkeypatch.setenv("N8N_CLOSE_CONVO_WEBHOOK_URL", CLOSE_WEBHOOK_URL)


def _seed(db, *, resolver_respond_user_id: str | None = RESOLVER_RESPOND_ID):
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code="ZZT-CLOSE", name="ZZT Close"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )
    contact_id = str(uuid.uuid4())
    db.add(
        RespondContact(
            id=contact_id,
            phone_number=PHONE,
            name="Aisyah Rahman",
            respond_io_id=RESPOND_IO_ID,
            session_vars={},
        )
    )
    assignee_id = str(uuid.uuid4())
    db.add(
        User(
            id=assignee_id,
            email="zzt-close-cs1@test.com",
            name="Agent One",
            respond_user_id=resolver_respond_user_id,
        )
    )
    agent_id = str(uuid.uuid4())
    db.add(AccessAgent(id=agent_id, code="ZZT_CLOSE_AGENT", name="ZZT Close Agent"))
    team_id = str(uuid.uuid4())
    db.add(Team(id=team_id, name=TEAM_NAME))
    db.add(
        AgentTeam(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            code="zzt_close_general",
            team_id=team_id,
            tier=1,
            policy_id=policy_id,
        )
    )
    db.commit()
    return {
        "policy_id": policy_id,
        "contact_id": contact_id,
        "assignee_id": assignee_id,
        "agent_code": "ZZT_CLOSE_AGENT",
        "team_set_code": "zzt_close_general",
    }


def _create_ticket(db, seed, *, source_message_id):
    return ConversationSLATrackingService(db).create_tracking(
        ConversationSLATrackingCreate(
            agent_code=seed["agent_code"],
            team_set_code=seed["team_set_code"],
            policy_id=seed["policy_id"],
            assigned_to_id=seed["assignee_id"],
            contact_phone_number=PHONE,
            source_message_id=source_message_id,
            source_message_text="Yes, please connect me to a person.",
        )
    )


def _resolve(db, tracking_id):
    return ConversationSLATrackingService(db).update_tracking(
        str(tracking_id), ConversationSLATrackingUpdate(is_resolved=True)
    )


def _close_logs(db):
    return (
        db.query(IntegrationLog)
        .filter(IntegrationLog.integration_channel == "n8n_crm_close_convo")
        .order_by(IntegrationLog.created_at.asc())
        .all()
    )


def _close_payloads(db):
    return [json.loads(row.request_payload) for row in _close_logs(db)]


class _NoCloseSession:
    def __init__(self, inner):
        self._inner = inner

    def close(self):  # noqa: D401 - deliberate no-op
        return None

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _run_pending_close_sends(db, monkeypatch, *, ok=True):
    """Run the daemon-thread body inline with the HTTP boundary mocked.

    Returns what the webhook service was asked to POST - the only place the
    AC-J6 secret is allowed to exist.
    """
    import app.database as database_module
    from app.services.crm_close_convo_webhook import send_crm_close_convo_webhook_for_log
    from app.services.webhook_service import WebhookService

    calls: list[dict] = []

    def _fake_send(_self, url, payload, headers=None):
        calls.append({"url": url, "payload": payload, "headers": dict(headers or {})})
        if ok:
            return True, 200, {"ok": True}, None, None
        return False, 500, None, "HTTP_ERROR", "n8n exploded"

    monkeypatch.setattr(WebhookService, "send_webhook", _fake_send)
    monkeypatch.setattr(database_module, "SessionLocal", lambda: _NoCloseSession(db))

    for log in _close_logs(db):
        send_crm_close_convo_webhook_for_log(str(log.id))
    return calls


# --------------------------------------------------------------------------- #
# AC-M3 - fires only when the resolve emptied the contact's open-ticket set    #
# --------------------------------------------------------------------------- #


def test_resolving_the_last_open_ticket_fires_the_close_webhook_once(db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    _resolve(db, t1.id)

    payloads = _close_payloads(db)
    assert len(payloads) == 1, "one close per emptied conversation"
    body = payloads[0]
    assert body["tracking_id"] == str(t1.id)
    assert body["source"] == "User"
    assert body["closedBySource"] == "crm"
    assert body["contact"]["respond_io_id"] == RESPOND_IO_ID
    assert body["contact"]["phone"] == PHONE
    assert body["resolved_by"]["respond_user_id"] == RESOLVER_RESPOND_ID
    assert body["resolved_by"]["crm_user_id"] == seed["assignee_id"]
    assert body["resolved_by"]["name"] == "Agent One"
    assert body["resolved_at"].endswith("Z"), "aware UTC ISO, never a naive string"
    assert body["open_ticket_count"] == 0
    assert body["crm"]["business_id"] == str(t1.id)


def test_a_sibling_still_open_means_no_close_webhook(db):
    """A second enquiry from the same contact is still live: the conversation is
    not finished, so nothing may tell n8n to close it."""
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-a")
    _create_ticket(db, seed, source_message_id="wamid.close-b")

    _resolve(db, t1.id)

    assert _close_payloads(db) == []


def test_resolving_the_sibling_afterwards_then_fires_it(db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-a")
    t2 = _create_ticket(db, seed, source_message_id="wamid.close-b")

    _resolve(db, t1.id)
    assert _close_payloads(db) == []

    _resolve(db, t2.id)
    payloads = _close_payloads(db)
    assert len(payloads) == 1
    assert payloads[0]["tracking_id"] == str(t2.id)


def test_re_resolving_an_already_resolved_ticket_does_not_fire_it_again(db):
    """`update_tracking` short-circuits an already-resolved row; the webhook must
    ride that short-circuit, not re-announce a close on every retry."""
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    _resolve(db, t1.id)
    _resolve(db, t1.id)

    assert len(_close_payloads(db)) == 1


# --------------------------------------------------------------------------- #
# AC-M3 hardening - idempotency key, resolver fallback, closed enum            #
# --------------------------------------------------------------------------- #


def test_the_event_id_is_derived_from_tracking_id_and_resolved_at(db):
    """Retries WILL happen: the same resolve must always carry the same key."""
    from app.services.crm_close_convo_webhook import build_close_convo_payload

    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")
    _resolve(db, t1.id)
    db.refresh(t1)

    body = _close_payloads(db)[0]
    again = build_close_convo_payload(
        tracking_id=str(t1.id),
        contact=db.query(RespondContact).filter(RespondContact.id == seed["contact_id"]).first(),
        resolver=None,
        resolver_respond_user_id=None,
        resolved_at=t1.resolved_at,
        team_name=None,
    )
    assert body["event_id"] == again["event_id"]
    assert body["event_id"] != build_close_convo_payload(
        tracking_id=str(uuid.uuid4()),
        contact=None,
        resolver=None,
        resolver_respond_user_id=None,
        resolved_at=t1.resolved_at,
        team_name=None,
    )["event_id"]


def test_an_unmapped_resolver_falls_back_to_the_team_name_never_undefined(db):
    """AC-M3 hardening 3: the contact-facing close message needs a readable
    name even when the resolver has no Respond mapping."""
    seed = _seed(db, resolver_respond_user_id=None)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    _resolve(db, t1.id)

    body = _close_payloads(db)[0]
    assert body["resolved_by"]["respond_user_id"] is None
    assert body["team_name"] == TEAM_NAME, (
        "the team is snapshotted BEFORE the resolve blanks team_set_code"
    )
    assert body["resolved_by"]["display_name"] == "Agent One"


def test_the_display_name_never_leaks_a_crm_uuid_as_a_respond_user_id(db):
    seed = _seed(db, resolver_respond_user_id=str(uuid.uuid4()))
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    _resolve(db, t1.id)

    body = _close_payloads(db)[0]
    assert body["resolved_by"]["respond_user_id"] is None
    assert body["resolved_by"]["display_name"]


def test_the_neutral_fallback_is_used_when_there_is_neither_name_nor_team(db):
    from app.services.crm_close_convo_webhook import (
        NEUTRAL_RESOLVER_NAME,
        build_close_convo_payload,
    )

    body = build_close_convo_payload(
        tracking_id=str(uuid.uuid4()),
        contact=None,
        resolver=None,
        resolver_respond_user_id=None,
        resolved_at=None,
        team_name=None,
    )
    assert body["resolved_by"]["display_name"] == NEUTRAL_RESOLVER_NAME
    assert body["closedBySource"] == "crm"


# --------------------------------------------------------------------------- #
# AC-J6 secret - on the request, never in the stored row                        #
# --------------------------------------------------------------------------- #


def test_the_secret_is_on_the_request_and_absent_from_the_stored_log(db, monkeypatch):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")
    _resolve(db, t1.id)

    row = _close_logs(db)[0]
    assert WEBHOOK_SECRET not in (row.request_headers or "")

    calls = _run_pending_close_sends(db, monkeypatch)
    assert len(calls) == 1
    assert calls[0]["url"] == CLOSE_WEBHOOK_URL
    assert calls[0]["headers"][SECRET_HEADER] == WEBHOOK_SECRET

    row = _close_logs(db)[0]
    assert WEBHOOK_SECRET not in (row.request_headers or ""), (
        "sending must not write the secret back either"
    )


# --------------------------------------------------------------------------- #
# Outbox on success AND failure; skip-when-unset; never raises                  #
# --------------------------------------------------------------------------- #


def test_the_outbox_row_records_success(db, monkeypatch):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")
    _resolve(db, t1.id)

    _run_pending_close_sends(db, monkeypatch, ok=True)
    row = _close_logs(db)[0]
    # "sent" is the shared IntegrationLogService vocabulary for a delivered
    # webhook awaiting an n8n callback - not a close-specific status.
    assert row.status == "sent"
    assert row.status_code == 200
    assert row.processed_at is not None


def test_the_outbox_row_records_failure(db, monkeypatch):
    """A 500 from n8n must leave a readable outbox row, not vanish."""
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")
    _resolve(db, t1.id)

    _run_pending_close_sends(db, monkeypatch, ok=False)
    row = _close_logs(db)[0]
    assert row.error_message == "n8n exploded"
    assert row.status_code == 500
    # The shared service parks a failed send back on "pending" with a retry time.
    assert row.status == "pending"
    assert row.next_retry_at is not None


def test_an_unconfigured_webhook_url_skips_silently_and_warns(db, monkeypatch, caplog):
    from app.config import settings

    monkeypatch.setattr(settings, "n8n_close_convo_webhook_url", None, raising=False)
    monkeypatch.delenv("N8N_CLOSE_CONVO_WEBHOOK_URL", raising=False)

    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    with caplog.at_level(logging.WARNING):
        updated = _resolve(db, t1.id)

    assert updated.is_resolved is True, "the resolve is unaffected"
    assert _close_payloads(db) == []
    assert any(
        "N8N_CLOSE_CONVO_WEBHOOK_URL" in record.getMessage() for record in caplog.records
    ), "an unwired webhook must be visible in the log, not silent"


def test_the_settings_page_url_wins_over_the_env_fallback(db, monkeypatch):
    """Launch wiring: the operator sets the URL on Settings > Integrations. The
    system_settings column is read first; the env var stays only as a fallback."""
    from app.config import settings
    from app.models.user import SystemSetting

    settings_url = "https://n8n.test/webhook/from-settings-page"
    row = db.query(SystemSetting).first() or SystemSetting()
    row.n8n_close_convo_webhook_url = settings_url
    db.add(row)
    db.commit()

    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-settings-1")
    _resolve(db, t1.id)
    assert [row.endpoint for row in _close_logs(db)] == [settings_url]

    # And with the env unset entirely, the settings column alone is enough.
    monkeypatch.setattr(settings, "n8n_close_convo_webhook_url", None, raising=False)
    monkeypatch.delenv("N8N_CLOSE_CONVO_WEBHOOK_URL", raising=False)
    t2 = _create_ticket(db, seed, source_message_id="wamid.close-settings-2")
    _resolve(db, t2.id)
    assert [row.endpoint for row in _close_logs(db)] == [settings_url, settings_url]


def test_a_notifier_explosion_never_fails_the_resolve(db, caplog):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    def _boom(*_a, **_k):
        raise RuntimeError("n8n unreachable")

    with caplog.at_level(logging.WARNING), patch(
        "app.services.crm_close_convo_webhook.notify_ticket_resolved_close", _boom
    ):
        updated = _resolve(db, t1.id)

    assert updated.is_resolved is True
    db.refresh(t1)
    assert t1.is_resolved is True


def test_a_contact_with_no_respond_id_skips_the_webhook(db, caplog):
    seed = _seed(db)
    db.query(RespondContact).filter(RespondContact.id == seed["contact_id"]).update(
        {"respond_io_id": None}
    )
    db.commit()
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")

    with caplog.at_level(logging.WARNING):
        _resolve(db, t1.id)

    assert _close_payloads(db) == []


# --------------------------------------------------------------------------- #
# AC-F3 - form SLA rows are a different family and never close a conversation   #
# --------------------------------------------------------------------------- #


def test_a_form_sla_resolve_never_fires_the_close_webhook(db):
    seed = _seed(db)
    t1 = _create_ticket(db, seed, source_message_id="wamid.close-1")
    db.query(type(t1)).filter(type(t1).id == t1.id).update(
        {"source_entity_type": "complaint", "source_entity_id": str(uuid.uuid4())}
    )
    db.commit()

    _resolve(db, t1.id)

    assert _close_payloads(db) == []
