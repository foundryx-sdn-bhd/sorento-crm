"""GET /api/v1/sla-management/conversation-events/stream - the SSE half of S4.2.

UAC: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
     AC-K1 (an inbound message reaches an open drawer within seconds)
     AC-K2 (nothing open, nothing subscribed - liveness costs nothing when idle)
     AC-K3 (the pending-tasks widget rides the SAME channel, not a second poller)
PLAN: documentation/plans/sla/PLAN-conversation-intervention-tickets.md (S4.2)

Run against a REAL uvicorn on an ephemeral port, not TestClient: both
``starlette.testclient`` and ``httpx.ASGITransport`` buffer the whole response
body before handing it back (verified in their source), which for an endpoint
that never ends is a hang, not a test. A real server also makes the disconnect
assertion honest - the client closes the socket, uvicorn cancels the handler,
and the subscription is released by the generator's own teardown, exactly as in
production.

The fake bus transport (tests/_event_bus_fake.py) is what makes "the
subscription was released" observable without sleeping and hoping.

Run:
    venv/bin/pytest tests/test_conversation_events_stream.py -q
"""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import uvicorn

from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services import conversation_event_bus as bus
from tests._event_bus_fake import FakeEventTransport

PATH = "/api/v1/sla-management/conversation-events/stream"
ME = "8f0f4e0e-0000-4000-8000-00000000ab01"
SOMEBODY_ELSE = "8f0f4e0e-0000-4000-8000-00000000ab02"
MY_OPEN_CONTACT = "10025904"
A_CONTACT_I_DID_NOT_OPEN = "999888777"
# The read timeout also bounds the wait for the response HEADERS, which a loaded CI
# runner (xdist under load) delayed past 3s once (PR #1466, run 37149035956: ReadTimeout
# before the stream opened). No test relies on it to stop reading - `_read_frames` stops
# on its frame count or its own deadline - so slack here only lengthens a failure.
CLIENT_TIMEOUT = httpx.Timeout(10.0, read=15.0)


@pytest.fixture(scope="module")
def base_url():
    """A real ASGI server for this module. Lifespan off: the stream needs no
    scheduler, no listener registration and no database."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server never came up"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture
def events():
    fake = FakeEventTransport()
    bus.set_transport(fake)
    try:
        yield fake
    finally:
        bus.set_transport(None)


@pytest.fixture
def signed_in(monkeypatch):
    """Authenticated as ME, with no database behind it: once running, the stream
    reads none.

    Both principals are overridden because the whole sla-management router is
    mounted behind the module guard, which resolves its own
    ``get_current_user_or_api_key``.

    ``entitled_contacts`` is the one thing on this path that DOES need a
    session (it drops ``?contacts=`` ids the caller has no ticket standing for).
    Its own suite is tests/test_conversation_events_contact_scope.py; here it is
    stubbed to a pass-through so these tests stay about the transport.
    """
    from app.api.v1.sla import conversation_events

    monkeypatch.setattr(
        conversation_events, "entitled_contacts", lambda _db, _uid, requested: requested
    )
    me = {"id": ME, "email": "me@test.com"}
    app.dependency_overrides[get_current_user] = lambda: me
    app.dependency_overrides[get_current_user_or_api_key] = lambda: me
    app.dependency_overrides[get_db] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def _read_frames(response, *, expected: int, timeout: float = 5.0) -> list[str]:
    """Collect SSE frames until ``expected`` data frames arrive or time is up."""
    frames: list[str] = []
    buffer = ""
    deadline = time.time() + timeout

    def _data_count() -> int:
        return len([f for f in frames if "data:" in f])

    try:
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                frames.append(frame)
            if _data_count() >= expected or time.time() >= deadline:
                break
    except httpx.ReadTimeout:
        pass
    return frames


def _data_frames(frames: list[str]) -> list[dict]:
    out = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[len("data:"):].strip()))
    return out


def _event_names(frames: list[str]) -> list[str]:
    return [
        line[len("event:"):].strip()
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("event:")
    ]


def _wait_for_subscription(events: FakeEventTransport, count: int = 1) -> None:
    """Wait until the endpoint has subscribed, so a publish cannot race ahead of
    it: pub/sub has no backlog, by design (a reconnect refetches, AC-K4)."""
    deadline = time.time() + 10
    while time.time() < deadline:
        if events.active_subscriptions >= count:
            return
        time.sleep(0.02)
    raise AssertionError("the stream never subscribed")


def _wait_for_release(events: FakeEventTransport, timeout: float = 10.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if events.active_subscriptions == 0:
            return 0
        time.sleep(0.05)
    return events.active_subscriptions


def test_the_stream_refuses_a_caller_with_no_session(base_url, events):
    """Auth denial: the stream names who is listening, so it is never anonymous."""
    response = httpx.get(f"{base_url}{PATH}", timeout=10.0)

    assert response.status_code == 401
    assert events.active_subscriptions == 0, "a refused caller must not subscribe"


def test_an_event_for_me_arrives_as_a_frame(base_url, signed_in, events):
    with httpx.Client(timeout=CLIENT_TIMEOUT) as client:
        with client.stream("GET", f"{base_url}{PATH}") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            _wait_for_subscription(events)

            bus.publish(bus.EVENT_TICKET_CREATED, user_id=ME, entity_id="t-1")
            frames = _read_frames(response, expected=2)

    payloads = _data_frames(frames)
    assert any(
        p.get("type") == bus.EVENT_TICKET_CREATED and p.get("user_id") == ME
        for p in payloads
    ), frames
    assert "ready" in _event_names(frames), (
        "the first frame names the stream as live so the FE can refetch on connect"
    )


def test_an_event_for_a_contact_i_have_open_arrives(base_url, signed_in, events):
    with httpx.Client(timeout=CLIENT_TIMEOUT) as client:
        with client.stream(
            "GET", f"{base_url}{PATH}", params={"contacts": MY_OPEN_CONTACT}
        ) as response:
            _wait_for_subscription(events)

            bus.publish(bus.EVENT_MESSAGE, contact_id=MY_OPEN_CONTACT)
            frames = _read_frames(response, expected=2)

    payloads = [p for p in _data_frames(frames) if p.get("type") != "ready"]
    assert [p["type"] for p in payloads] == [bus.EVENT_MESSAGE]
    assert payloads[0]["contact_id"] == MY_OPEN_CONTACT
    assert payloads[0]["user_id"] is None


def test_somebody_elses_work_never_reaches_me(base_url, signed_in, events):
    """Filtered server-side: a stream that fanned everything to everybody would
    leak who is talking to whom and would scale with total traffic."""
    with httpx.Client(timeout=CLIENT_TIMEOUT) as client:
        with client.stream(
            "GET", f"{base_url}{PATH}", params={"contacts": MY_OPEN_CONTACT}
        ) as response:
            _wait_for_subscription(events)

            bus.publish(bus.EVENT_TICKET_UPDATED, user_id=SOMEBODY_ELSE, entity_id="t-2")
            bus.publish(bus.EVENT_MESSAGE, contact_id=A_CONTACT_I_DID_NOT_OPEN)
            # Then one that IS mine, so the read has a definite end: anything
            # that arrived before it would have to be a leak.
            bus.publish(bus.EVENT_MESSAGE, contact_id=MY_OPEN_CONTACT)
            frames = _read_frames(response, expected=2)

    payloads = [p for p in _data_frames(frames) if p.get("type") != "ready"]
    assert [p["contact_id"] for p in payloads] == [MY_OPEN_CONTACT]


def test_a_disconnect_releases_the_subscription(base_url, signed_in, events):
    """AC-K2: when nothing is open, nothing is held - no subscription left
    behind by a closed drawer or a navigated-away tab."""
    with httpx.Client(timeout=CLIENT_TIMEOUT) as client:
        with client.stream("GET", f"{base_url}{PATH}") as response:
            _wait_for_subscription(events)
            assert events.active_subscriptions == 1
            _read_frames(response, expected=1)

    assert _wait_for_release(events) == 0


def test_a_quiet_stream_sends_a_heartbeat(base_url, signed_in, events, monkeypatch):
    """Proxies reap an idle connection; the comment frame keeps it alive and is
    invisible to EventSource consumers."""
    from app.api.v1.sla import conversation_events

    monkeypatch.setattr(conversation_events, "HEARTBEAT_SECONDS", 0.1)
    monkeypatch.setattr(conversation_events, "POLL_SECONDS", 0.02)

    with httpx.Client(timeout=CLIENT_TIMEOUT) as client:
        with client.stream("GET", f"{base_url}{PATH}") as response:
            frames = _read_frames(response, expected=99, timeout=1.5)

    assert any(frame.startswith(":") for frame in frames), frames
