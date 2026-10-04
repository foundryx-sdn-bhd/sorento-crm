"""Thread scroll-back pagination + in-thread search (UAC AC-L7 / AC-L8).

Two lanes, one contract:

- **Respond.io cursor lane** is primary. Respond is the system of record for a
  WhatsApp conversation and its `cursorId` walk returns the FULL message object
  (attachments, receipts, sender source), so a scrolled-back page looks exactly
  like the live window. `cursorId=<id>` yields older (DESC), `cursorId=-<id>`
  yields newer (ASC) - verified against the live API 2026-08-15.
- **`chat_histories` lane** is the fallback (Respond down / unconfigured) and the
  substrate for search. Keyset on `(sent_at, id)`, never OFFSET.

The invariant both lanes share: **items always come back oldest-to-newest**,
whichever direction was asked for, so the frontend never reverses anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.chat_history import ChatHistory
from app.services import conversation_thread_service as svc
from tests._pg_fixture import blank_session

NOW = datetime(2026, 8, 15, 9, 0, 0)


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


CONTACT = svc.ThreadContact(
    respond_io_id="ZZT900000080",
    phone_number="+60100000001",
    first_name="Zzt",
    last_name="Tester",
)


def _msg(
    db,
    *,
    offset_seconds: int,
    message_id: str | None,
    text: str = "hello",
    type_: str = "incoming",
    sent_at: datetime | None = None,
):
    row = ChatHistory(
        channel="whatsapp",
        contact_id=CONTACT.respond_io_id,
        phone_number=CONTACT.phone_number,
        message=text,
        sent_at=sent_at or (NOW + timedelta(seconds=offset_seconds)),
        type=type_,
        message_id=message_id,
    )
    db.add(row)
    db.flush()
    return row


def _seed_ten(db):
    """m0 (oldest) .. m9 (newest), one second apart."""
    return [_msg(db, offset_seconds=i, message_id=f"100{i}", text=f"body {i}") for i in range(10)]


def _ids(page):
    return [str(i["messageId"]) for i in page["items"]]


# ---------------------------------------------------------------------------
# Local keyset lane
# ---------------------------------------------------------------------------


def test_no_cursor_returns_newest_page_oldest_first(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, limit=4)
    assert _ids(page) == ["1006", "1007", "1008", "1009"]
    assert page["has_more_older"] is True
    assert page["has_more_newer"] is False
    assert page["oldest_message_id"] == "1006"
    assert page["newest_message_id"] == "1009"
    assert page["source"] == "local"


def test_before_page_returns_older_oldest_first(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, before="1006", limit=3)
    # Oldest-to-newest even though the query walked backwards.
    assert _ids(page) == ["1003", "1004", "1005"]
    assert page["has_more_older"] is True
    assert page["has_more_newer"] is True


def test_before_page_short_means_conversation_start(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, before="1002", limit=5)
    assert _ids(page) == ["1000", "1001"]
    # A short page IS the signal that nothing older exists.
    assert page["has_more_older"] is False


def test_has_more_older_true_only_when_page_came_back_full(db):
    _seed_ten(db)
    exact = svc.fetch_thread_page(db, CONTACT, before="1002", limit=2)
    assert _ids(exact) == ["1000", "1001"]
    # A full page claims more even when there is none: the next request settles
    # it with an empty page. Over-claiming costs one request; under-claiming
    # silently truncates the conversation, which is the bug AC-L7 exists to fix.
    assert exact["has_more_older"] is True


def test_after_page_returns_newer_oldest_first(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, after="1006", limit=2)
    assert _ids(page) == ["1007", "1008"]
    assert page["has_more_newer"] is True
    assert page["has_more_older"] is True


def test_around_centres_the_anchor(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, around="1005", limit=5)
    assert _ids(page) == ["1003", "1004", "1005", "1006", "1007"]
    assert page["anchor_message_id"] == "1005"


def test_around_at_the_start_still_includes_the_anchor(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, around="1000", limit=5)
    assert _ids(page)[0] == "1000"
    assert page["has_more_older"] is False


def test_tie_break_on_equal_sent_at_is_stable_and_lossless(db):
    """Four messages share a timestamp: `id` alone decides the walk order."""
    same = NOW + timedelta(seconds=5)
    for i in range(4):
        _msg(db, offset_seconds=0, message_id=f"920{i}", text=f"tie {i}", sent_at=same)
    _msg(db, offset_seconds=-10, message_id="9100", text="older")

    first = svc.fetch_thread_page(db, CONTACT, limit=2)
    assert _ids(first) == ["9202", "9203"]
    second = svc.fetch_thread_page(db, CONTACT, before=first["oldest_message_id"], limit=2)
    # No row repeated, none skipped, across a timestamp collision.
    assert _ids(second) == ["9200", "9201"]
    third = svc.fetch_thread_page(db, CONTACT, before=second["oldest_message_id"], limit=2)
    assert _ids(third) == ["9100"]


def test_unknown_anchor_falls_back_to_the_newest_page(db):
    """An UNKNOWN message id (a purged row) falls back; a cursor that is no message id at all is
    refused (security review, ASKS-UX: it reaches the Respond URL path)."""
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, before="9999", limit=3)
    assert _ids(page) == ["1007", "1008", "1009"]
    with pytest.raises(ValueError):
        svc.fetch_thread_page(db, CONTACT, before="nope", limit=3)


def test_limit_is_clamped(db):
    _seed_ten(db)
    assert len(svc.fetch_thread_page(db, CONTACT, limit=0)["items"]) == 1
    assert svc.fetch_thread_page(db, CONTACT, limit=10_000)["limit"] == svc.MAX_LIMIT


def test_rows_render_as_respond_shaped_items(db):
    _msg(db, offset_seconds=0, message_id="2001", text="> quoted\nreply body", type_="outgoing")
    page = svc.fetch_thread_page(db, CONTACT, limit=5)
    item = page["items"][0]
    assert item["traffic"] == "outgoing"
    assert item["message"]["type"] == "text"
    assert item["message"]["text"] == "> quoted\nreply body"
    # The frontend derives bubble time from status[]/messageId; a local row must
    # carry a usable clock or the date pill vanishes.
    assert item["status"][0]["timestamp"] > 0
    assert item["source"] == "local"


def test_other_contacts_are_never_mixed_in(db):
    _seed_ten(db)
    other = ChatHistory(
        channel="whatsapp",
        contact_id="ZZT999999",
        phone_number="+60100000002",
        message="not mine",
        sent_at=NOW + timedelta(seconds=20),
        type="incoming",
        message_id="9999",
    )
    db.add(other)
    db.flush()
    page = svc.fetch_thread_page(db, CONTACT, limit=50)
    assert "9999" not in _ids(page)


# ---------------------------------------------------------------------------
# Respond.io lane + persistence
# ---------------------------------------------------------------------------


def _respond_item(message_id: int, text: str, traffic: str = "incoming") -> dict:
    return {
        "messageId": message_id,
        "traffic": traffic,
        "message": {"type": "text", "text": text},
        "sender": {"source": "contact" if traffic == "incoming" else "n8n"},
        "status": [],
        "replyTo": None,
    }


class FakeRespondClient:
    """Stands in for RespondClient: newest-first for older walks, ascending for newer."""

    def __init__(self, items: list[dict], *, fail: bool = False):
        # `items` oldest-first; the API returns newest-first.
        self.items = items
        self.fail = fail
        self.calls: list[dict] = []

    def list_messages(self, identifier, limit=50, cursor=None):
        self.calls.append({"identifier": identifier, "limit": limit, "cursor": cursor})
        if self.fail:
            raise RuntimeError("respond is down")
        ordered = list(self.items)
        if cursor and str(cursor).startswith("-"):
            anchor = int(str(cursor)[1:])
            newer = [i for i in ordered if i["messageId"] > anchor]
            return {"items": newer[:limit], "pagination": {}}
        if cursor:
            anchor = int(str(cursor))
            older = [i for i in ordered if i["messageId"] < anchor]
            return {"items": list(reversed(older))[:limit], "pagination": {}}
        return {"items": list(reversed(ordered))[:limit], "pagination": {}}

    def get_message(self, identifier, message_id):
        if self.fail:
            raise RuntimeError("respond is down")
        for i in self.items:
            if str(i["messageId"]) == str(message_id):
                return i
        return {}


def _respond_history(n: int = 6) -> list[dict]:
    base = 1786000000000000
    return [_respond_item(base + i * 1_000_000, f"respond body {i}") for i in range(n)]


def test_respond_lane_is_preferred_and_returned_oldest_first(db):
    client = FakeRespondClient(_respond_history())
    page = svc.fetch_thread_page(db, CONTACT, limit=3, client=client)
    assert page["source"] == "respond"
    assert _ids(page) == [str(1786000000000000 + i * 1_000_000) for i in (3, 4, 5)]
    assert page["has_more_older"] is True


def test_respond_before_page_walks_older(db):
    history = _respond_history()
    client = FakeRespondClient(history)
    anchor = str(history[3]["messageId"])
    page = svc.fetch_thread_page(db, CONTACT, before=anchor, limit=2, client=client)
    assert _ids(page) == [str(history[1]["messageId"]), str(history[2]["messageId"])]
    assert client.calls[-1]["cursor"] == anchor


def test_respond_after_page_walks_newer(db):
    history = _respond_history()
    client = FakeRespondClient(history)
    anchor = str(history[1]["messageId"])
    page = svc.fetch_thread_page(db, CONTACT, after=anchor, limit=2, client=client)
    assert _ids(page) == [str(history[2]["messageId"]), str(history[3]["messageId"])]
    assert client.calls[-1]["cursor"] == f"-{anchor}"


def test_respond_around_unions_both_halves_with_the_anchor(db):
    history = _respond_history(9)
    client = FakeRespondClient(history)
    anchor = str(history[4]["messageId"])
    page = svc.fetch_thread_page(db, CONTACT, around=anchor, limit=5, client=client)
    assert _ids(page) == [str(history[i]["messageId"]) for i in (2, 3, 4, 5, 6)]
    assert page["anchor_message_id"] == anchor


def test_respond_page_is_persisted_into_chat_histories_idempotently(db):
    history = _respond_history(4)
    client = FakeRespondClient(history)
    svc.fetch_thread_page(db, CONTACT, limit=4, client=client)
    svc.fetch_thread_page(db, CONTACT, limit=4, client=client)
    stored = (
        db.query(ChatHistory)
        .filter(ChatHistory.contact_id == CONTACT.respond_io_id)
        .all()
    )
    assert len(stored) == 4
    assert {r.message_id for r in stored} == {str(i["messageId"]) for i in history}
    # Persisted so SEARCH can find pre-ingest history; the read itself changes
    # nothing a user can observe (no mark-read, no window cache).
    assert all(r.type in ("incoming", "outgoing") for r in stored)


def test_persist_does_not_duplicate_a_pre_cutover_row(db):
    """The dedupe index is partial (created_at >= cutover), so an old row is not
    covered by ON CONFLICT - the backfill must check existence explicitly."""
    history = _respond_history(2)
    existing = ChatHistory(
        channel="whatsapp",
        contact_id=CONTACT.respond_io_id,
        phone_number=CONTACT.phone_number,
        message="already here",
        sent_at=NOW,
        type="incoming",
        message_id=str(history[0]["messageId"]),
        created_at=datetime(2026, 1, 1),
    )
    db.add(existing)
    db.flush()
    svc.fetch_thread_page(db, CONTACT, limit=5, client=FakeRespondClient(history))
    rows = (
        db.query(ChatHistory)
        .filter(
            ChatHistory.contact_id == CONTACT.respond_io_id,
            ChatHistory.message_id == str(history[0]["messageId"]),
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].message == "already here"


def test_respond_failure_falls_back_to_the_local_lane(db):
    _seed_ten(db)
    page = svc.fetch_thread_page(db, CONTACT, limit=3, client=FakeRespondClient([], fail=True))
    assert page["source"] == "local"
    assert _ids(page) == ["1007", "1008", "1009"]


def test_persist_failure_never_fails_the_read(db, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("write blew up")

    monkeypatch.setattr(svc, "persist_messages", boom)
    page = svc.fetch_thread_page(db, CONTACT, limit=3, client=FakeRespondClient(_respond_history()))
    assert page["source"] == "respond"
    assert len(page["items"]) == 3


def test_media_message_persists_a_typed_placeholder(db):
    item = {
        "messageId": 1786000009000000,
        "traffic": "incoming",
        "message": {"type": "image", "url": "https://cdn/x.jpg"},
        "sender": {"source": "contact"},
        "status": [],
    }
    svc.fetch_thread_page(db, CONTACT, limit=5, client=FakeRespondClient([item]))
    row = db.query(ChatHistory).filter(ChatHistory.message_id == "1786000009000000").one()
    assert "image" in row.message.lower()


def test_attachment_persists_its_filename_so_search_can_find_it(db):
    item = {
        "messageId": 1786000010000000,
        "traffic": "outgoing",
        "message": {
            "type": "attachment",
            "attachment": {"type": "file", "fileName": "PO%20SPO%202023.xlsx"},
        },
        "sender": {"source": "api"},
        "status": [],
    }
    svc.fetch_thread_page(db, CONTACT, limit=5, client=FakeRespondClient([item]))
    row = db.query(ChatHistory).filter(ChatHistory.message_id == "1786000010000000").one()
    # Percent-decoded: nobody searches for "%20".
    assert row.message == "[file] PO SPO 2023.xlsx"
    assert svc.search_thread(db, CONTACT, q="SPO 2023")["total"] == 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_matches_case_insensitively_newest_first(db):
    _msg(db, offset_seconds=0, message_id="3001", text="Where is my ORDER")
    _msg(db, offset_seconds=1, message_id="3002", text="no match here")
    _msg(db, offset_seconds=2, message_id="3003", text="order confirmed")
    result = svc.search_thread(db, CONTACT, q="order")
    assert [m["message_id"] for m in result["items"]] == ["3003", "3001"]
    assert result["total"] == 2


def test_search_treats_percent_and_underscore_as_literals(db):
    _msg(db, offset_seconds=0, message_id="4001", text="discount is 50% off")
    _msg(db, offset_seconds=1, message_id="4002", text="nothing relevant")
    _msg(db, offset_seconds=2, message_id="4003", text="file_name.pdf attached")
    _msg(db, offset_seconds=3, message_id="4004", text="filexname.pdf attached")

    pct = svc.search_thread(db, CONTACT, q="50% off")
    assert [m["message_id"] for m in pct["items"]] == ["4001"]

    underscore = svc.search_thread(db, CONTACT, q="file_name")
    # `_` is a single-char ILIKE wildcard: unescaped this also matches "filexname".
    assert [m["message_id"] for m in underscore["items"]] == ["4003"]


def test_search_escapes_the_escape_character(db):
    _msg(db, offset_seconds=0, message_id="4101", text=r"path C:\temp ready")
    _msg(db, offset_seconds=1, message_id="4102", text="unrelated")
    result = svc.search_thread(db, CONTACT, q=r"C:\temp")
    assert [m["message_id"] for m in result["items"]] == ["4101"]


def test_search_blank_query_returns_nothing(db):
    _seed_ten(db)
    assert svc.search_thread(db, CONTACT, q="   ")["items"] == []


def test_search_is_capped_and_reports_truncation(db):
    for i in range(8):
        _msg(db, offset_seconds=i, message_id=f"50{i:02d}", text=f"needle {i}")
    result = svc.search_thread(db, CONTACT, q="needle", limit=3)
    assert len(result["items"]) == 3
    assert result["truncated"] is True
    assert result["total"] == 3


def test_search_snippet_is_centred_on_the_match(db):
    long_text = ("filler " * 40) + "NEEDLE" + (" filler" * 40)
    _msg(db, offset_seconds=0, message_id="6001", text=long_text)
    hit = svc.search_thread(db, CONTACT, q="needle")["items"][0]
    assert "NEEDLE" in hit["snippet"]
    assert len(hit["snippet"]) < len(long_text)
    assert hit["snippet"].startswith("…")
    assert hit["direction"] == "incoming"
    assert hit["sent_at"] is not None


def test_search_never_leaks_another_contact(db):
    _msg(db, offset_seconds=0, message_id="7001", text="shared needle")
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id="ZZT888888",
            phone_number="+60100000003",
            message="shared needle",
            sent_at=NOW,
            type="incoming",
            message_id="7002",
        )
    )
    db.flush()
    result = svc.search_thread(db, CONTACT, q="needle")
    assert [m["message_id"] for m in result["items"]] == ["7001"]


def test_search_skips_rows_with_no_message_id(db):
    """A match the thread cannot jump to is not a usable search result."""
    _msg(db, offset_seconds=0, message_id=None, text="anchorless needle")
    _msg(db, offset_seconds=1, message_id="8001", text="jumpable needle")
    result = svc.search_thread(db, CONTACT, q="needle")
    assert [m["message_id"] for m in result["items"]] == ["8001"]


# ---------------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------------

_BASE = "/api/v1/sla-management/conversation-sla-tracking"
_FAKE_ID = "00000000-0000-0000-0000-000000000000"


def test_page_endpoint_requires_a_principal():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        res = client.get(f"{_BASE}/{_FAKE_ID}/conversation/page", params={"limit": 5})
    assert res.status_code in (401, 403), res.text


def test_search_endpoint_requires_a_principal():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        res = client.get(f"{_BASE}/{_FAKE_ID}/conversation/search", params={"q": "hello"})
    assert res.status_code in (401, 403), res.text


def test_page_endpoint_rejects_a_bad_limit():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        res = client.get(f"{_BASE}/{_FAKE_ID}/conversation/page", params={"limit": 5000})
    # Validation or auth, never a 200 and never a 500.
    assert res.status_code in (401, 403, 422), res.text


# ---------------------------------------------------------------------------
# Sender identity (the audit of 2026-08-22)
#
# Respond names the sender of a staff reply by its OWN user id and gives us no
# name with it, so a bubble could only ever be labelled by the transport that
# carried it. `users.respond_user_id` is the bridge back to a CRM user.
# ---------------------------------------------------------------------------


def _staff_user(db, *, respond_user_id: str, name: str):
    from app.models.user import User

    user = User(
        email=f"{respond_user_id}@zzt.invalid",
        name=name,
        respond_user_id=respond_user_id,
    )
    db.add(user)
    db.flush()
    return user


def test_attach_sender_names_names_a_human_send(db):
    _staff_user(db, respond_user_id="ZZT1098828", name="Tay Zhi Yang")
    items = [
        {"sender": {"source": "user", "userId": "ZZT1098828"}},
    ]

    svc._attach_sender_names(db, items)

    assert items[0]["sender"]["name"] == "Tay Zhi Yang"


def test_attach_sender_names_leaves_a_machine_send_unnamed(db):
    # A bot send carries no user id, and is deliberately unlabelled in the UI.
    _staff_user(db, respond_user_id="ZZT1098829", name="Should Not Appear")
    items = [
        {"sender": {"source": "n8n", "userId": None}},
        {"sender": {"source": "api"}},
        {"sender": {"source": "contact"}},
    ]

    svc._attach_sender_names(db, items)

    assert all("name" not in item["sender"] for item in items)


def test_attach_sender_names_skips_a_user_id_we_do_not_hold(db):
    items = [{"sender": {"source": "user", "userId": "ZZT-no-such-user"}}]

    svc._attach_sender_names(db, items)

    assert "name" not in items[0]["sender"]


def test_quick_reply_is_stored_by_its_title_not_as_a_placeholder():
    """A `quick_reply` keeps its prose under `title`, so reading `text` alone
    stored the whole prompt as "[quick_reply]" and put its words beyond the
    reach of in-thread search."""
    item = {
        "message": {
            "type": "quick_reply",
            "title": "No stock for SRTW2600-SS-CR. Reply with a code to continue.",
            "replies": ["SRTW2600", "Yes escalate"],
        }
    }

    assert (
        svc._respond_item_text(item)
        == "No stock for SRTW2600-SS-CR. Reply with a code to continue."
    )


def test_a_typed_placeholder_still_answers_for_a_bodyless_message():
    assert svc._respond_item_text({"message": {"type": "sticker"}}) == "[sticker]"
