"""Respond-side timestamp resolver.

Covers UAC OBS-S4-19 .. OBS-S4-25.

Resolves `respond_ts` / `delivery_status` for chat rows by calling
`GET /v2/contact/{id}/message/{messageId}`. Chosen over polling Respond's message
list: the lookup is targeted, and a 404 is itself the signal that a message we
believed we sent never existed.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.chat_history import ChatHistory
from app.services import chat_message_resolver as svc
from tests._pg_fixture import blank_session


# No column-type shims needed: on the real schema JSONB/ARRAY are native and the
# BigInteger primary key is backed by a sequence, so inserts fill it themselves.
@pytest.fixture
def db():
    with blank_session() as session:
        yield session


NOW = datetime(2026, 7, 20, 12, 0, 0)


class FakeClient:
    """Stands in for RespondClient. Records calls so we can assert what was asked."""

    class NotFound(Exception):
        pass

    def __init__(self, responses=None, raises=None):
        self.responses = responses or {}
        self.raises = raises or {}
        self.calls = []

    def get_message(self, identifier, message_id):
        self.calls.append((str(identifier), str(message_id)))
        key = str(message_id)
        if key in self.raises:
            raise self.raises[key]
        return self.responses.get(key, {})


def _row(db, *, message_id="m1", respond_ts=None, attempts=0, type="outgoing", contact_id="900000063"):
    row = ChatHistory(
        channel="whatsapp",
        contact_id=contact_id,
        phone_number="+60160000517",
        message="hi",
        sent_at=NOW,
        type=type,
        message_id=message_id,
        respond_ts=respond_ts,
        resolve_attempts=attempts,
    )
    db.add(row)
    db.commit()
    return row


def test_resolves_respond_timestamp_and_status(db):
    row = _row(db, message_id="m1")
    client = FakeClient({"m1": {"timestamp": 1784519974000, "status": "delivered"}})

    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)

    db.refresh(row)
    assert out["resolved"] == 1
    assert row.respond_ts == datetime.utcfromtimestamp(1784519974)
    assert row.delivery_status == "delivered"


def test_passes_contact_identifier_not_just_message_id(db):
    """GET /v2/contact/{identifier}/message/{id} needs both."""
    _row(db, message_id="m1", contact_id="999")
    client = FakeClient({"m1": {"timestamp": 1784519974000}})
    svc.resolve_pending(db, client=client, limit=10, now=NOW)
    assert client.calls == [("999", "m1")]


def test_skips_rows_already_fully_resolved(db):
    """Both halves present - nothing left for Respond to tell us."""
    row = _row(db, message_id="m1", respond_ts=NOW)
    row.delivery_status = "delivered"
    db.commit()
    client = FakeClient({"m1": {"timestamp": 1784519974000}})
    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)
    assert client.calls == []
    assert out["resolved"] == 0


def test_fetches_row_with_respond_ts_but_no_delivery_status(db):
    """`respond_ts` now arrives at ingest, so delivery status is the reason to call.

    Skipping on `respond_ts` alone would leave every ingested row's Delivery column
    permanently blank.
    """
    _row(db, message_id="m1", respond_ts=NOW)
    client = FakeClient({"m1": {"timestamp": 1784519974000, "status": "delivered"}})
    svc.resolve_pending(db, client=client, limit=10, now=NOW)
    assert client.calls == [("900000063", "m1")]


def test_skips_rows_without_message_id(db):
    _row(db, message_id=None)
    client = FakeClient()
    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)
    assert client.calls == []
    assert out["resolved"] == 0


def test_respects_limit(db):
    for i in range(5):
        _row(db, message_id=f"m{i}")
    client = FakeClient({f"m{i}": {"timestamp": 1784519974000} for i in range(5)})
    svc.resolve_pending(db, client=client, limit=2, now=NOW)
    assert len(client.calls) == 2


def test_not_found_increments_attempts(db):
    row = _row(db, message_id="ghost")
    client = FakeClient(raises={"ghost": svc.MessageNotFound("404")})

    svc.resolve_pending(db, client=client, limit=10, now=NOW)

    db.refresh(row)
    assert row.resolve_attempts == 1
    assert row.respond_ts is None
    assert row.delivery_status is None  # not yet concluded


def test_not_found_at_max_attempts_marks_not_sent(db):
    """'If not found, it was not sent' - but only after we've stopped believing a retry."""
    row = _row(db, message_id="ghost", attempts=svc.MAX_RESOLVE_ATTEMPTS - 1)
    client = FakeClient(raises={"ghost": svc.MessageNotFound("404")})

    svc.resolve_pending(db, client=client, limit=10, now=NOW)

    db.refresh(row)
    assert row.resolve_attempts == svc.MAX_RESOLVE_ATTEMPTS
    assert row.delivery_status == "not_sent"


def test_exhausted_rows_are_not_retried(db):
    _row(db, message_id="ghost", attempts=svc.MAX_RESOLVE_ATTEMPTS)
    client = FakeClient()
    svc.resolve_pending(db, client=client, limit=10, now=NOW)
    assert client.calls == []


def test_transient_error_increments_without_concluding(db):
    """A 500 must not be read as 'never sent'."""
    row = _row(db, message_id="m1", attempts=svc.MAX_RESOLVE_ATTEMPTS - 1)
    client = FakeClient(raises={"m1": RuntimeError("respond 503")})

    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)

    db.refresh(row)
    assert out["failed"] == 1
    assert row.delivery_status is None  # crucially NOT not_sent
    assert row.resolve_attempts == svc.MAX_RESOLVE_ATTEMPTS


def test_one_bad_row_does_not_abort_the_batch(db):
    _row(db, message_id="bad")
    good = _row(db, message_id="good")
    client = FakeClient(
        {"good": {"timestamp": 1784519974000}},
        raises={"bad": RuntimeError("boom")},
    )

    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)

    db.refresh(good)
    assert good.respond_ts is not None
    assert out["resolved"] == 1 and out["failed"] == 1


def test_seconds_epoch_is_handled_as_well_as_milliseconds(db):
    """Respond has returned both; treating seconds as ms yields year-58xxx dates."""
    row = _row(db, message_id="m1")
    client = FakeClient({"m1": {"timestamp": 1784519974}})
    svc.resolve_pending(db, client=client, limit=10, now=NOW)
    db.refresh(row)
    assert row.respond_ts.year == 2026


def test_delivered_and_read_timestamps_captured_when_present(db):
    row = _row(db, message_id="m1")
    client = FakeClient({
        "m1": {
            "timestamp": 1784519974000,
            "status": "read",
            "statusTimestamps": {
                "delivered": 1784519975000,
                "read": 1784519980000,
            },
        }
    })
    svc.resolve_pending(db, client=client, limit=10, now=NOW)
    db.refresh(row)
    assert row.delivered_ts == datetime.utcfromtimestamp(1784519975)
    assert row.read_ts == datetime.utcfromtimestamp(1784519980)


def test_missing_timestamp_in_payload_is_not_resolved(db):
    """A response we can't read a clock from must not silently resolve to now()."""
    row = _row(db, message_id="m1")
    client = FakeClient({"m1": {"status": "sent"}})
    out = svc.resolve_pending(db, client=client, limit=10, now=NOW)
    db.refresh(row)
    assert row.respond_ts is None
    assert out["resolved"] == 0


# --- respond_ts derived from the Respond message id -------------------------
#
# Respond's `messageId` IS the message's epoch-microsecond timestamp, so the
# authoritative clock is already in the ingest payload and needs no HTTP call.
# Covers UAC OBS-S4-26 .. OBS-S4-31.

SENT_AT = datetime(2026, 7, 21, 2, 48, 45, 363000)


def test_microsecond_message_id_yields_respond_ts():
    assert svc.respond_ts_from_message_id(
        "1784602125363985", sent_at=SENT_AT
    ) == datetime(2026, 7, 21, 2, 48, 45, 363985)


def test_whole_second_incoming_message_id_yields_respond_ts():
    """Inbound WhatsApp ids land on exact seconds - that granularity is real."""
    assert svc.respond_ts_from_message_id(
        "1784602116000000", sent_at=datetime(2026, 7, 21, 2, 48, 36)
    ) == datetime(2026, 7, 21, 2, 48, 36)


def test_millisecond_epoch_is_rejected_not_read_as_microseconds():
    """1784602125363 as microseconds is 1970 - implausible, so refuse it."""
    assert svc.respond_ts_from_message_id("1784602125363", sent_at=SENT_AT) is None


def test_non_timestamp_id_is_rejected():
    """A short numeric id divided by 1e6 lands in 1970, nowhere near sent_at."""
    assert svc.respond_ts_from_message_id("1234556", sent_at=SENT_AT) is None


def test_non_numeric_and_absent_ids_are_rejected():
    assert svc.respond_ts_from_message_id("abc", sent_at=SENT_AT) is None
    assert svc.respond_ts_from_message_id(None, sent_at=SENT_AT) is None
    assert svc.respond_ts_from_message_id("", sent_at=SENT_AT) is None


def test_id_far_from_sent_at_is_rejected():
    """Guard against ids that parse but describe a different message entirely."""
    assert svc.respond_ts_from_message_id(
        "1784602125363985", sent_at=datetime(2020, 1, 1)
    ) is None
