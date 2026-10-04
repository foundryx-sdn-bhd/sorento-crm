"""Regression tests for the 24h WhatsApp window detection.

Bug: incoming Respond.io messages carry an empty ``status: []`` array (delivery
status is only tracked for outgoing). The old code read ``status[0].timestamp``
and so dropped every inbound message - the window looked permanently closed even
right after the contact replied. Incoming messages encode their time in
``messageId`` (epoch MICROSECONDS); ``_message_timestamp`` now falls back to it.
"""
from datetime import datetime, timedelta

from app.services import respond_messaging_service as rms
from app.services.respond_messaging_service import _message_timestamp, get_window_state


# 2026-06-09 09:55:16 UTC, as Respond emits it on an incoming message.
INCOMING_MESSAGE_ID = 1780998916000000
INCOMING_DT = datetime(2026, 6, 9, 9, 55, 16)


def test_message_timestamp_from_incoming_message_id():
    item = {
        "messageId": INCOMING_MESSAGE_ID,
        "traffic": "incoming",
        "message": {"text": "any cert for WB241?", "type": "text"},
        "status": [],  # incoming messages have no delivery status
    }
    assert _message_timestamp(item) == INCOMING_DT


def test_message_timestamp_from_outgoing_status():
    # Outgoing keeps using the (earliest) status timestamp = creation time.
    item = {
        "messageId": 1781010492616957,
        "traffic": "outgoing",
        "status": [
            {"value": "pending", "timestamp": 1781010493201},
            {"value": "sent", "timestamp": 1781010505272},
            {"value": "delivered", "timestamp": 1781010506038},
        ],
    }
    assert _message_timestamp(item) == datetime.utcfromtimestamp(1781010493.201)


def _patch_messages(monkeypatch, items):
    class _FakeClient:
        def list_messages(self, identifier, limit=50):
            return {"items": items}

    monkeypatch.setattr(
        "app.services.integration_service.RespondClient", lambda *a, **k: _FakeClient()
    )


def test_window_open_when_recent_inbound_only_has_message_id(monkeypatch):
    # Mimic the real bug case: recent inbound with status=[] + messageId only.
    now = datetime.utcnow()
    recent_id = int((now - timedelta(hours=2)).timestamp()) * 1_000_000
    items = [
        {"traffic": "outgoing", "messageId": 1, "status": [{"timestamp": int(now.timestamp() * 1000)}]},
        {"traffic": "incoming", "messageId": recent_id, "status": []},
    ]
    _patch_messages(monkeypatch, items)
    ws = get_window_state(None, "900000008")
    assert ws["open"] is True
    assert ws["last_incoming_at"] is not None


def test_window_closed_when_inbound_is_old(monkeypatch):
    old_id = int((datetime.utcnow() - timedelta(hours=30)).timestamp()) * 1_000_000
    items = [{"traffic": "incoming", "messageId": old_id, "status": []}]
    _patch_messages(monkeypatch, items)
    ws = get_window_state(None, "900000008")
    assert ws["open"] is False
