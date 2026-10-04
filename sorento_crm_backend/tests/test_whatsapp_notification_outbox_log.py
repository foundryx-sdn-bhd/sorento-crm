"""Every WhatsApp notification send writes a respond_io outbox log.

Local testing runs with intentionally-wrong Respond creds, so a 401'd send MUST
still produce an integration_log row (not just flip the delivery to failed) - 
same as the complaint / OTP / SLA-escalation paths via `_send_and_log`.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from app.tasks.notification_tasks import _send_whatsapp_for_notification


def _objs():
    db = MagicMock()
    notification = SimpleNamespace(
        id="632da7e3-a60a-4727-8a71-baa77c2970fd",
        data={
            "whatsapp_use_case": "sla_daily_summary",
            "whatsapp_text": "Daily SLA summary: 1 outstanding",
            "whatsapp_context_vars": {"outstanding": "1"},
        },
        event_type="user_sla_daily_summary",
        title="Summary",
        body="body",
        source_entity_type="scheduled_task_user_sla_daily_summary",
        source_entity_id="342b58af-ede7-5f34-8ae7-cefb3c478111",  # composite, NOT a uuid
    )
    user = SimpleNamespace(id="u1")
    delivery = SimpleNamespace(status="pending", error_message=None, sent_at=None)
    contact = SimpleNamespace(id="contact-uuid", respond_io_id="900000104")
    return db, notification, user, delivery, contact


def _patches(contact, send_side_effect=None, send_return=None):
    log_service = MagicMock()
    p_contact = patch(
        "app.services.respond_link_service.resolve_user_respond_contact",
        return_value=contact,
    )
    send = MagicMock(side_effect=send_side_effect, return_value=send_return)
    p_send = patch(
        "app.services.respond_messaging_service.send_text_or_template", send
    )
    p_log = patch(
        "app.services.integration_service.IntegrationLogService",
        return_value=log_service,
    )
    return p_contact, p_send, p_log, log_service


def test_failed_send_writes_failed_outbox_log_with_uuid_business_id():
    db, notification, user, delivery, contact = _objs()
    resp = httpx.Response(401, json={"code": 401, "message": "Token not found"},
                          request=httpx.Request("POST", "https://api.respond.io/x"))
    err = httpx.HTTPStatusError("401", request=resp.request, response=resp)
    # Closed-window send attempts a TEMPLATE; the window-aware send stamps the
    # real attempted payload on the exception (see _attach_send_context).
    err.request_payload = {
        "message": {"type": "whatsapp_template", "use_case": "sla_daily_summary"}
    }
    p_contact, p_send, p_log, log_service = _patches(contact, send_side_effect=err)
    with p_contact, p_send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    assert log_service.create_integration_log.call_count == 1
    call = log_service.create_integration_log.call_args
    log_arg = call.args[0]
    assert log_arg.integration_channel == "respond_io"
    assert log_arg.direction == "outbound"
    assert log_arg.status == "failed"
    assert log_arg.status_code == 401
    assert log_arg.external_reference == "900000104"
    # business_id is the notification UUID, never the composite source_entity_id.
    assert log_arg.business_id == notification.id
    # The logged payload reflects the TEMPLATE that was attempted, not a text default.
    assert call.kwargs["request_payload_dict"]["message"]["type"] == "whatsapp_template"
    assert delivery.status == "failed"


def test_successful_send_writes_success_outbox_log():
    db, notification, user, delivery, contact = _objs()
    p_contact, p_send, p_log, log_service = _patches(
        contact,
        send_return={
            "request_payload": {"message": {"type": "text", "text": "x"}},
            "response": {"messageId": 1},
            "sent_as": "template",
        },
    )
    with p_contact, p_send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    assert log_service.create_integration_log.call_count == 1
    log_arg = log_service.create_integration_log.call_args.args[0]
    assert log_arg.status == "success"
    assert log_arg.external_reference == "900000104"
    assert delivery.status == "sent"


def test_no_contact_marks_failed_without_outbox_log():
    db, notification, user, delivery, _ = _objs()
    p_contact, p_send, p_log, log_service = _patches(None)
    with p_contact, p_send, p_log:
        _send_whatsapp_for_notification(db, notification, user, delivery)

    # No send attempted => no outbox row; delivery still marked failed.
    assert log_service.create_integration_log.call_count == 0
    assert delivery.status == "failed"
