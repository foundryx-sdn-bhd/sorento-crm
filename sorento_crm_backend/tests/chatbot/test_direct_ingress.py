"""CHAT-SPEED direct ingress, the job half (UAC AC-6..AC-10).

`app.tasks.chat_turns.run_direct_turn_job` runs a turn the respond.io webhook enqueued and
hands its actions to the n8n sender, which executes them exactly as the spine does after
`/chat/turn`. The n8n copy of the same message, arriving seconds later through the producer
and dispatcher, must come back `duplicate: true` with no second turn (D15), because its
`duplicate` arm sends nothing.

Real, committed Postgres via `SessionLocal`, like `test_d15_duplicate_race.py`: the ordering
test needs two genuinely concurrent connections. Rows are `ZZT-` prefixed and removed by hand.
The sender is faked at `httpx.post`; the outbound guard is graded in its own tests below.
"""
from __future__ import annotations

import json
import threading
import time
import types
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.models.chatbot_turn import ChatbotTurn
from app.models.integration import IntegrationLog
from app.services.chatbot import direct_ingress
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.contracts import Envelope
from app.services.chatbot.head import parser as parser_mod
from app.services.outbound_url_guard import OutboundUrlRejected
from app.tasks import chat_turns
from app.utils.field_encryption import encrypt_secret
from tests.chatbot.test_engine import _parser_output

SENDER_URL = "https://automate-zzt.example.com/webhook/chatbot-sender"
SENDER_KEY = "ZZT-plain-sender-key-0001"


def _message_id(age_seconds: float = 1.0) -> str:
    return str(int((time.time() - age_seconds) * 1_000_000))


def _payload(contact: str, message_id: str, text_value: str = "price for SRTWC8517") -> dict:
    """A respond.io `message.received` body, as the webhook enqueues it."""
    return {
        "event_type": "message.received",
        "contact": {
            "id": contact,
            "firstName": "ZZT",
            "phone": "+60000000901",
            "custom_fields": [{"name": "is_human_intervened", "value": None}],
        },
        "message": {
            "messageId": message_id,
            "contactId": contact,
            "channelId": 12,
            "traffic": "incoming",
            "timestamp": int(message_id) // 1000,
            "message": {"type": "text", "text": text_value},
            "sender": {"source": "contact"},
        },
        "channel": {"id": 12, "source": "whatsapp_cloud"},
    }


def _n8n_copy(payload: dict) -> Envelope:
    """What the n8n producer + dispatcher post to `/chat/turn` for the same message."""
    return Envelope(message=payload, contact=payload["contact"], ingress="webhook")


@pytest.fixture()
def contacts():
    """Seeds committed `respond_contacts` rows; deletes them and their turns afterwards."""
    made: list[str] = []

    def _seed(suffix: str) -> str:
        cid = f"ZZT-direct-{suffix}-{uuid.uuid4().hex[:6]}"
        db = SessionLocal()
        try:
            db.execute(
                text(
                    "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                    "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
                ),
                # A phone of its own: `respond_contacts.phone_number` is UNIQUE, and the
                # suite runs files in parallel against one database.
                {"cid": cid, "phone": f"+600{uuid.uuid4().int % 10**9:09d}", "sv": json.dumps({"variables": {}})},
            )
            db.commit()
        finally:
            db.close()
        made.append(cid)
        return cid

    yield _seed

    db = SessionLocal()
    try:
        for cid in made:
            db.query(ChatbotTurn).filter(ChatbotTurn.contact_respond_id == cid).delete(
                synchronize_session=False
            )
            db.query(IntegrationLog).filter(IntegrationLog.external_reference == cid).delete(
                synchronize_session=False
            )
            db.execute(text("DELETE FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": cid})
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def stub_engine_seams(monkeypatch):
    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    # The AI-allowed gate reads company membership; the seeded contacts belong to nobody.
    monkeypatch.setattr(engine_mod, "_ai_allowed", lambda session_factory, contact: True)


@pytest.fixture(autouse=True)
def lookup(monkeypatch):
    """The respond.io contact lookup the job makes before the turn (n8n's findcontact).
    `records[contact]` is what respond.io returns; a missing entry is a failed lookup."""
    records: dict[str, Any] = {}
    calls: list[str] = []

    def _fake(db, contact_id):
        calls.append(contact_id)
        return records.get(contact_id, {
            "id": contact_id,
            "firstName": "ZZT",
            "phone": "+60000000901",
            "custom_fields": [{"name": "is_human_intervened", "value": "false"}],
        })

    monkeypatch.setattr(direct_ingress, "_lookup_contact", _fake)
    return types.SimpleNamespace(records=records, calls=calls)


@pytest.fixture()
def sender(monkeypatch):
    """A configured sender on the default workspace, and every POST it receives."""
    workspace = types.SimpleNamespace(
        chatbot_sender_url=SENDER_URL,
        chatbot_sender_key_ciphertext=encrypt_secret(SENDER_KEY),
    )
    monkeypatch.setattr(direct_ingress, "_default_workspace", lambda db: workspace)
    monkeypatch.setattr(direct_ingress, "assert_safe_outbound_url", lambda url, label: None)
    posts: list[dict[str, Any]] = []
    state = {"status": 200, "raise": None}

    def _fake_post(url, **kwargs):
        posts.append({"url": url, **kwargs})
        if state["raise"] is not None:
            raise state["raise"]
        return httpx.Response(state["status"], text="ok", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", _fake_post)
    return types.SimpleNamespace(workspace=workspace, posts=posts, state=state)


def _turns(contact: str) -> list[ChatbotTurn]:
    db = SessionLocal()
    try:
        return (
            db.query(ChatbotTurn)
            .filter(ChatbotTurn.contact_respond_id == contact)
            .order_by(ChatbotTurn.created_at)
            .all()
        )
    finally:
        db.close()


def _outbound_logs(contact: str) -> list[IntegrationLog]:
    db = SessionLocal()
    try:
        return (
            db.query(IntegrationLog)
            .filter(
                IntegrationLog.external_reference == contact,
                IntegrationLog.direction == "outbound",
            )
            .all()
        )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# AC-6: run, send, then the n8n copy is a duplicate
# --------------------------------------------------------------------------- #


def test_job_posts_actions_then_n8n_copy_is_duplicate(contacts, stub_engine_seams, sender):
    contact = contacts("run")
    payload = _payload(contact, _message_id())

    outcome = chat_turns.run_direct_turn_job(payload)

    turns = _turns(contact)
    assert len(turns) == 1
    assert turns[0].ingress == "respond_webhook"
    assert outcome["turn_id"] == str(turns[0].id)
    assert outcome["sent"] is True

    assert len(sender.posts) == 1
    post = sender.posts[0]
    assert post["url"] == SENDER_URL
    assert post["follow_redirects"] is False
    assert post["headers"]["X-Chatbot-Sender-Key"] == SENDER_KEY
    body = post["json"]
    assert body["turn_id"] == str(turns[0].id)
    assert body["contact_id"] == contact
    assert body["actions"] and all("kind" in a for a in body["actions"])
    assert body["actions"] == (turns[0].response or {}).get("actions")

    logs = _outbound_logs(contact)
    assert len(logs) == 1 and logs[0].status == "success"

    # The n8n producer's copy of the SAME message, seconds later.
    replay = engine_mod.run_turn(_n8n_copy(payload), session_factory=SessionLocal)
    assert replay.duplicate is True
    assert len(_turns(contact)) == 1


def test_the_job_envelope_is_live_never_a_test_turn(contacts, stub_engine_seams, sender):
    contact = contacts("live")
    chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    turn = _turns(contact)[0]
    assert turn.is_test is False
    stored = turn.envelope
    assert stored["ingress"] == "respond_webhook"
    assert stored["message"]["message"]["traffic"] == "incoming"


# --------------------------------------------------------------------------- #
# AC-7: nothing to send
# --------------------------------------------------------------------------- #


def test_job_posts_nothing_for_duplicate(contacts, stub_engine_seams, sender):
    """The n8n copy got there first: the job's turn is the duplicate and sends nothing."""
    contact = contacts("dup")
    payload = _payload(contact, _message_id())
    first = engine_mod.run_turn(_n8n_copy(payload), session_factory=SessionLocal)
    assert first.duplicate is False

    outcome = chat_turns.run_direct_turn_job(payload)
    assert outcome["sent"] is False
    assert outcome["duplicate"] is True
    assert sender.posts == []
    assert _outbound_logs(contact) == []


def test_job_posts_nothing_when_the_turn_has_no_actions(contacts, stub_engine_seams, sender, monkeypatch):
    contact = contacts("quiet")
    monkeypatch.setattr(engine_mod, "_ai_allowed", lambda session_factory, contact: False)
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["sent"] is False
    assert sender.posts == []


# --------------------------------------------------------------------------- #
# AC-8 / AC-9: a failed or refused send is visible, never raised
# --------------------------------------------------------------------------- #


def _trace_stages(turn: ChatbotTurn) -> list[dict]:
    return [r for r in (turn.trace or []) if isinstance(r, dict) and r.get("stage") == "sent"]


@pytest.mark.parametrize("failure", ["status", "transport"])
def test_sender_failure_is_recorded(contacts, stub_engine_seams, sender, failure):
    contact = contacts(f"fail-{failure}")
    if failure == "status":
        sender.state["status"] = 502
    else:
        sender.state["raise"] = httpx.ConnectError("unreachable (zzt)")

    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))

    assert outcome["sent"] is False
    logs = _outbound_logs(contact)
    assert len(logs) == 1 and logs[0].status == "failed"
    turn = _turns(contact)[0]
    failed = [r for r in _trace_stages(turn) if r.get("status") == "failed"]
    assert failed, f"no failed `sent` trace record on the turn: {turn.trace}"


def test_sender_url_guarded(contacts, stub_engine_seams, sender, monkeypatch):
    from app.services import outbound_url_guard

    contact = contacts("guard")
    sender.workspace.chatbot_sender_url = "https://127.0.0.1/webhook/zzt"
    monkeypatch.setattr(
        direct_ingress, "assert_safe_outbound_url", outbound_url_guard.assert_safe_outbound_url
    )
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["sent"] is False
    assert sender.posts == []
    assert [log.status for log in _outbound_logs(contact)] == ["failed"]
    with pytest.raises(OutboundUrlRejected):
        outbound_url_guard.assert_safe_outbound_url("https://127.0.0.1/x", label="zzt")


def test_no_sender_configured(contacts, stub_engine_seams, sender):
    contact = contacts("nosender")
    sender.workspace.chatbot_sender_url = None
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["sent"] is False
    assert sender.posts == []
    turn = _turns(contact)[0]
    assert any(r.get("status") == "failed" for r in _trace_stages(turn)), turn.trace


# --------------------------------------------------------------------------- #
# AC-10: ordered per contact even with S7 ordering off
# --------------------------------------------------------------------------- #


def test_direct_turn_is_ordered_with_s7_off(contacts, stub_engine_seams, sender, monkeypatch):
    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: False)
    contact = contacts("order")

    lock = threading.Lock()
    timeline: list[tuple[str, float]] = []

    def slow_parse(config, user_block):
        with lock:
            timeline.append(("start", time.monotonic()))
        time.sleep(0.4)
        with lock:
            timeline.append(("end", time.monotonic()))
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", slow_parse)
    errors: list[BaseException] = []

    def _run(message_id: str) -> None:
        try:
            chat_turns.run_direct_turn_job(_payload(contact, message_id))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first, second = _message_id(2.0), _message_id(1.0)
    t1 = threading.Thread(target=_run, args=(first,))
    t2 = threading.Thread(target=_run, args=(second,))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert errors == []
    starts = [ts for kind, ts in timeline if kind == "start"]
    ends = [ts for kind, ts in timeline if kind == "end"]
    assert len(starts) == 2 and len(ends) == 2, timeline
    assert starts[1] >= ends[0], f"two direct turns of one contact overlapped: {timeline}"


def test_n8n_copy_with_s7_off_still_takes_no_ticket(contacts, stub_engine_seams, monkeypatch):
    """With direct ingress off for the contact, an n8n turn is unordered exactly as today."""
    from app.services.chatbot import dispatch

    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dispatch, "contact_ticket", lambda *a, **k: pytest.fail("ticket taken with S7 off")
    )
    contact = contacts("n8n-off")
    result = engine_mod.run_turn(
        _n8n_copy(_payload(contact, _message_id())), session_factory=SessionLocal
    )
    assert result.duplicate is False


def test_n8n_copy_of_a_covered_contact_waits_for_the_direct_turn(
    contacts, stub_engine_seams, sender, monkeypatch
):
    """Message A runs through the direct path; message B's n8n copy arrives while A is still
    running (B's own direct job is stuck behind A on the single fast worker). With S7 off the
    n8n copy used to take no ticket, so B could be answered beside A, out of order. While
    direct ingress covers the contact, every live turn of it takes the ticket."""
    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: False)
    contact = contacts("mixed")
    monkeypatch.setattr(
        engine_mod,
        "_read_switches",
        _switches_with(engine_mod._read_switches, mode="contacts", contacts=contact),
    )

    lock = threading.Lock()
    timeline: list[tuple[str, float]] = []

    def slow_parse(config, user_block):
        with lock:
            timeline.append(("start", time.monotonic()))
        time.sleep(0.4)
        with lock:
            timeline.append(("end", time.monotonic()))
        return _parser_output()

    monkeypatch.setattr(parser_mod, "parse", slow_parse)
    errors: list[BaseException] = []

    def _direct() -> None:
        try:
            chat_turns.run_direct_turn_job(_payload(contact, _message_id(2.0)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _n8n() -> None:
        try:
            engine_mod.run_turn(
                _n8n_copy(_payload(contact, _message_id(1.0))), session_factory=SessionLocal
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=_direct)
    t2 = threading.Thread(target=_n8n)
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert errors == []
    starts = [ts for kind, ts in timeline if kind == "start"]
    ends = [ts for kind, ts in timeline if kind == "end"]
    assert len(starts) == 2 and len(ends) == 2, timeline
    assert starts[1] >= ends[0], f"the n8n copy ran beside the direct turn: {timeline}"


def _switches_with(real, *, mode: str, contacts: str):
    """`_read_switches` with the direct-ingress fields overridden: the committed test DB's
    settings row is shared, so the switch is set on the snapshot, never on the row."""
    import dataclasses

    def _read(db):
        return dataclasses.replace(
            real(db), chatbot_direct_ingress_mode=mode, chatbot_direct_ingress_contacts=contacts
        )

    return _read


# --------------------------------------------------------------------------- #
# The producer's takeover gate, read through the contact lookup
# --------------------------------------------------------------------------- #


def test_the_looked_up_contact_is_the_envelope_contact(contacts, stub_engine_seams, sender, lookup):
    contact = contacts("lookup")
    lookup.records[contact] = {
        "id": contact,
        "firstName": "ZZT",
        "custom_fields": [
            {"name": "is_human_intervened", "value": None},
            {"name": "lifecycle", "value": "dealer"},
        ],
    }
    chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert lookup.calls == [contact]
    stored = _turns(contact)[0].envelope["contact"]
    assert stored["custom_fields"] == lookup.records[contact]["custom_fields"]


@pytest.mark.parametrize("value", [True, "true"])
def test_human_takeover_runs_no_turn(contacts, stub_engine_seams, sender, lookup, value):
    """n8n's If1 drops a message whose contact is human-intervened; so does this path, and
    the n8n copy is dropped by the producer too, so the bot stays silent as today."""
    contact = contacts("takeover")
    lookup.records[contact] = {
        "id": contact,
        "custom_fields": [{"name": "is_human_intervened", "value": value}],
    }
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["skipped"] == "human_intervened"
    assert outcome["sent"] is False
    assert _turns(contact) == []
    assert sender.posts == []


def test_failed_lookup_runs_no_turn(contacts, stub_engine_seams, sender, lookup, monkeypatch):
    """A lookup that fails leaves the message to the n8n copy rather than guessing."""
    contact = contacts("lookupfail")
    monkeypatch.setattr(direct_ingress, "_lookup_contact", lambda db, contact_id: None)
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["skipped"] == "lookup_failed"
    assert _turns(contact) == []
    assert sender.posts == []


# --------------------------------------------------------------------------- #
# Security review L3: the sender speaks for the DEFAULT workspace only
# --------------------------------------------------------------------------- #


def test_a_contact_of_another_workspace_runs_no_turn(contacts, stub_engine_seams, sender, monkeypatch):
    contact = contacts("otherws")
    monkeypatch.setattr(direct_ingress, "_on_default_workspace", lambda db, contact_id: False)
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    assert outcome["skipped"] == "non_default_workspace"
    assert _turns(contact) == []
    assert sender.posts == []


def test_on_default_workspace_reads_the_contacts_own_workspace():
    from app.models.access import RespondContact
    from app.schemas.respond_workspace import RespondWorkspaceCreate
    from app.services.respond_workspace_service import RespondWorkspaceService
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        svc = RespondWorkspaceService(db)
        default = svc.create(RespondWorkspaceCreate(space_id="ZZT-ws-default", api_key="k", is_default=True))
        other = svc.create(RespondWorkspaceCreate(space_id="ZZT-ws-other", api_key="k"))
        for cid, ws in (("ZZT-c-default", default.id), ("ZZT-c-other", other.id), ("ZZT-c-none", None)):
            db.add(RespondContact(
                respond_io_id=cid, phone_number=f"+600{uuid.uuid4().int % 10**9:09d}", workspace_id=ws
            ))
        db.commit()

        assert direct_ingress._on_default_workspace(db, "ZZT-c-default") is True
        assert direct_ingress._on_default_workspace(db, "ZZT-c-none") is True
        assert direct_ingress._on_default_workspace(db, "ZZT-c-unknown") is True
        assert direct_ingress._on_default_workspace(db, "ZZT-c-other") is False


# --------------------------------------------------------------------------- #
# Review B1 / S3 / S5
# --------------------------------------------------------------------------- #


def test_read_switches_carries_the_direct_ingress_fields():
    """B1: the engine's per-turn snapshot is a manual builder; the ordering rule reads it."""
    from app.models.user import SystemSetting
    from tests._pg_fixture import blank_session

    with blank_session() as db:
        db.add(SystemSetting(
            id=str(uuid.uuid4()), name="ZZT Co",
            chatbot_direct_ingress_mode="contacts", chatbot_direct_ingress_contacts="ZZT-a\nZZT-b",
        ))
        db.commit()
        switches = engine_mod._read_switches(db)
        assert switches.chatbot_direct_ingress_mode == "contacts"
        assert switches.chatbot_direct_ingress_contacts == "ZZT-a\nZZT-b"
        assert direct_ingress.covers(switches, "ZZT-b") is True
        assert direct_ingress.covers(switches, "ZZT-c") is False


def test_a_delegating_direct_turn_is_closed_not_left_delegated(
    contacts, stub_engine_seams, sender, monkeypatch
):
    """S3: no n8n spine waits on a direct turn, so a lane that would delegate fails it with
    an error that names direct ingress (not S7, which is off here)."""
    monkeypatch.setattr(engine_mod, "_s7_mode", lambda *args, **kwargs: False)
    monkeypatch.setattr(engine_mod, "CRM_COMPLETED_BRANCH_KINDS", frozenset())
    contact = contacts("delegate")
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id()))
    turn = _turns(contact)[0]
    assert turn.status == "failed", (turn.status, turn.error)
    assert "direct ingress" in (turn.error or "").lower()
    assert "turn s7 mode off" not in (turn.error or "").lower()
    assert outcome["duplicate"] is False


def test_a_job_that_runs_late_is_dropped(contacts, stub_engine_seams, sender, lookup):
    """S5: the 120 s window is re-checked when the job starts, so a turn that waited behind a
    backlog never answers a message the n8n copy has long since handled."""
    contact = contacts("late")
    outcome = chat_turns.run_direct_turn_job(_payload(contact, _message_id(age_seconds=121)))
    assert outcome["skipped"] == "stale"
    assert lookup.calls == []
    assert _turns(contact) == []
