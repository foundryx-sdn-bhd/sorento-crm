"""The per-contact outbound kill switch: `respond_contacts.outbound_enabled`.

A disabled contact must not receive ANYTHING outbound - text, attachment or
template - no matter which service reached for `RespondClient`. Reads are never
gated: fetching a contact, listing messages or closing a conversation still work
while the switch is off, because the switch is about what WE say, not about what
we can see.

An identifier we cannot resolve to a contact fails OPEN (the send proceeds, with
a warning). Failing closed there would break first-contact sends, where no
`respond_contacts` row exists yet; the switch exists to silence KNOWN contacts.

The Respond HTTP layer is mocked in every test here. Nothing in this file may
reach the real API.

Run: pytest tests/test_respond_outbound_switch.py -v
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


class _NoClose:
    """The test's session, handed to code that owns and closes its own.

    `assert_outbound_enabled` opens a short-lived Session when none is passed
    and closes it in a `finally`. Closing the test's rolled-back session would
    take the rest of the test with it, so proxy everything except `close`.
    """

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):  # noqa: D401 - deliberately inert
        pass


def _seed_contact(db, *, enabled: bool = True, name: str = "ZZT Contact") -> dict:
    cid = str(uuid.uuid4())
    respond_io_id = str(uuid.uuid4().int)[:9]
    phone = f"+6011{str(uuid.uuid4().int)[:8]}"
    db.execute(
        text(
            "INSERT INTO respond_contacts "
            "(id, phone_number, name, respond_io_id, session_vars, outbound_enabled) "
            "VALUES (:i, :p, :n, :r, '{}'::jsonb, :e)"
        ),
        {"i": cid, "p": phone, "n": name, "r": respond_io_id, "e": enabled},
    )
    db.flush()
    return {"id": cid, "respond_io_id": respond_io_id, "phone_number": phone, "name": name}


def _respond_client():
    """A client with credentials supplied, so construction touches no database."""
    from app.services.integration_service import RespondClient

    return RespondClient(api_key="zzt-key", base_url="https://api.test")


# --------------------------------------------------------------------------
# The guard itself
# --------------------------------------------------------------------------


def test_disabled_contact_blocks_the_send(db):
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import assert_outbound_enabled

    contact = _seed_contact(db, enabled=False, name="ZZT Muted")

    with pytest.raises(AppException) as excinfo:
        assert_outbound_enabled(contact["respond_io_id"], db=db)

    assert "ZZT Muted" in str(excinfo.value.detail), "the error must name the contact"


def test_disabled_contact_is_logged_at_error(db, caplog):
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import assert_outbound_enabled

    contact = _seed_contact(db, enabled=False)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(AppException):
            assert_outbound_enabled(contact["respond_io_id"], db=db)

    assert any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text


def test_enabled_contact_passes(db):
    from app.services.respond_outbound_service import assert_outbound_enabled

    contact = _seed_contact(db, enabled=True)
    assert_outbound_enabled(contact["respond_io_id"], db=db)  # must not raise


def test_the_switch_is_matched_by_phone_number_too(db):
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import assert_outbound_enabled

    contact = _seed_contact(db, enabled=False)

    with pytest.raises(AppException):
        assert_outbound_enabled(f"phone:{contact['phone_number']}", db=db)


def test_id_prefixed_identifier_is_stripped_before_lookup(db):
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import assert_outbound_enabled

    contact = _seed_contact(db, enabled=False)

    with pytest.raises(AppException):
        assert_outbound_enabled(f"id:{contact['respond_io_id']}", db=db)


def test_unresolvable_identifier_fails_open_with_a_warning(db, caplog):
    from app.services.respond_outbound_service import assert_outbound_enabled

    with caplog.at_level(logging.WARNING):
        assert_outbound_enabled("id:zzt-nobody-here", db=db)  # must not raise

    assert any(
        r.levelno == logging.WARNING for r in caplog.records
    ), "an unresolved outbound identifier must be warned about, not silent"


def test_a_database_failure_never_silently_allows_the_send(db):
    from app.services import respond_outbound_service as mod

    with patch.object(mod, "contact_for_identifier", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            mod.assert_outbound_enabled("900000008", db=db)


# --------------------------------------------------------------------------
# All three send paths, end to end through RespondClient
# --------------------------------------------------------------------------


def _send_calls(client, identifier):
    """The three outbound paths, as no-arg callables."""
    return {
        "send_message": lambda: client.send_message(identifier, "hello"),
        "send_attachment": lambda: client.send_attachment(
            identifier, "image", "https://cdn.test/a.png"
        ),
        "send_template_message": lambda: client.send_template_message(
            identifier,
            channel_id=1,
            template_name="zzt_template",
            language_code="en",
            body_text="hi",
            parameters=["x"],
        ),
    }


@pytest.mark.parametrize(
    "path", ["send_message", "send_attachment", "send_template_message"]
)
def test_every_send_path_is_blocked_for_a_disabled_contact(db, path):
    from app.services import integration_service as mod
    from app.services.error_handler import AppException

    contact = _seed_contact(db, enabled=False, name="ZZT Muted")
    client = _respond_client()
    http = MagicMock()

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        with pytest.raises(AppException):
            _send_calls(client, contact["respond_io_id"])[path]()

    assert not http.__enter__.called, f"{path} still opened an HTTP client"


@pytest.mark.parametrize(
    "path", ["send_message", "send_attachment", "send_template_message"]
)
def test_every_send_path_proceeds_for_an_enabled_contact(db, path):
    from app.services import integration_service as mod

    contact = _seed_contact(db, enabled=True)
    client = _respond_client()

    response = MagicMock(content=b"{}", status_code=200)
    response.json.return_value = {"messageId": 1}
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        _send_calls(client, contact["respond_io_id"])[path]()

    assert http.__enter__.return_value.post.called, f"{path} never reached Respond"


def test_an_unknown_identifier_still_sends(db):
    """First contact: no `respond_contacts` row yet, so the send must go out."""
    from app.services import integration_service as mod

    client = _respond_client()
    response = MagicMock(content=b"{}", status_code=200)
    response.json.return_value = {"messageId": 1}
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        client.send_message("zzt-unknown-identifier", "hello")

    assert http.__enter__.return_value.post.called


def test_reads_are_never_gated(db):
    """A muted contact can still be fetched - the switch only governs sends."""
    from app.services import integration_service as mod

    contact = _seed_contact(db, enabled=False)
    client = _respond_client()

    response = MagicMock(content=b"{}", status_code=200)
    response.json.return_value = {"id": contact["respond_io_id"]}
    http = MagicMock()
    http.__enter__.return_value.get.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        client.get_contact_by_identifier(contact["respond_io_id"])

    assert http.__enter__.return_value.get.called


def test_the_env_var_allowlist_hack_is_gone():
    """The RESPOND_SEND_ALLOWLIST stopgap is replaced by the column, not kept."""
    from app.services.integration_service import RespondClient

    assert not hasattr(RespondClient, "_assert_send_allowed")


# --------------------------------------------------------------------------
# The chokepoint: exactly ONE method may POST a message to a contact
# --------------------------------------------------------------------------


def _respond_client_methods() -> dict:
    """Every function defined in the body of `class RespondClient`, by name."""
    import ast
    import inspect

    from app.services import integration_service as mod

    tree = ast.parse(inspect.getsource(mod))
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "RespondClient"
    )
    return {
        node.name: node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _string_literals(node) -> list[str]:
    """Every plain and f-string literal fragment inside `node`."""
    import ast

    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def _posts(node) -> bool:
    import ast

    return any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "post"
        for sub in ast.walk(node)
    )


def test_only_the_chokepoint_posts_to_a_message_url():
    """A future send method cannot quietly grow its own unguarded POST.

    Structural, not behavioural: any RespondClient method that both builds a
    `/message` URL and POSTs to it is a contact-message send, and the only one
    allowed to exist is the guarded chokepoint.
    """
    offenders = [
        name
        for name, node in _respond_client_methods().items()
        if name != "_post_contact_message"
        and _posts(node)
        and any("/message" in s for s in _string_literals(node))
    ]

    assert offenders == [], (
        "these RespondClient methods POST to a message URL without going through "
        f"_post_contact_message, so they bypass the outbound switch: {offenders}"
    )


def test_the_chokepoint_is_the_only_caller_of_the_guard():
    """The guard is asserted in one place, so adding a sender cannot forget it."""
    import ast

    callers = [
        name
        for name, node in _respond_client_methods().items()
        if any(
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "assert_outbound_enabled"
            for sub in ast.walk(node)
        )
    ]

    assert callers == ["_post_contact_message"], (
        "assert_outbound_enabled must be called exactly once, inside the "
        f"chokepoint; found it in {callers}"
    )


@pytest.mark.parametrize(
    "path", ["send_message", "send_attachment", "send_template_message"]
)
def test_every_public_sender_delegates_to_the_chokepoint(path):
    client = _respond_client()

    with patch.object(
        type(client), "_post_contact_message", return_value={"messageId": 1}
    ) as chokepoint:
        _send_calls(client, "900000008")[path]()

    assert chokepoint.call_count == 1, f"{path} did not delegate to the chokepoint"
    assert chokepoint.call_args.args[0] == "900000008", (
        f"{path} must hand the identifier to the chokepoint, or the guard "
        "checks the wrong contact"
    )


@pytest.mark.parametrize(
    "path", ["send_message", "send_attachment", "send_template_message"]
)
def test_no_public_sender_reaches_httpx_on_its_own(path):
    """With the chokepoint stubbed out, nothing else may open an HTTP client."""
    from app.services import integration_service as mod

    client = _respond_client()
    http = MagicMock()

    with patch.object(
        type(client), "_post_contact_message", return_value={}
    ), patch.object(mod.httpx, "Client", return_value=http):
        _send_calls(client, "900000008")[path]()

    assert not http.__enter__.called, (
        f"{path} opened its own httpx client instead of delegating the POST"
    )


@pytest.mark.parametrize(
    "path,expected_timeout",
    [("send_message", 15), ("send_attachment", 30), ("send_template_message", 15)],
)
def test_each_sender_keeps_its_own_timeout(path, expected_timeout):
    """Attachments give Respond longer, because Respond fetches the media itself."""
    client = _respond_client()

    with patch.object(
        type(client), "_post_contact_message", return_value={}
    ) as chokepoint:
        _send_calls(client, "900000008")[path]()

    assert chokepoint.call_args.kwargs["timeout"] == expected_timeout


def test_the_chokepoint_posts_to_the_message_endpoint(db):
    from app.services import integration_service as mod

    contact = _seed_contact(db, enabled=True)
    client = _respond_client()

    response = MagicMock(content=b"{}", status_code=200)
    response.json.return_value = {"messageId": 7}
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ) as client_factory:
        result = client._post_contact_message(
            contact["respond_io_id"], {"message": {"type": "text", "text": "hi"}}, timeout=15
        )

    assert result == {"messageId": 7}
    posted_url = http.__enter__.return_value.post.call_args.args[0]
    assert posted_url == f"https://api.test/v2/contact/id:{contact['respond_io_id']}/message"
    assert client_factory.call_args.kwargs["timeout"] == 15


def test_the_chokepoint_attaches_the_response_to_a_failure(db):
    """`send_attachment`'s error context, now shared by every sender."""
    import httpx

    from app.services import integration_service as mod

    contact = _seed_contact(db, enabled=True)
    client = _respond_client()

    response = MagicMock(content=b'{"message":"bad media"}', status_code=400)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400", request=MagicMock(), response=None
    )
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            client.send_attachment(
                contact["respond_io_id"], "image", "https://cdn.test/a.png"
            )

    assert excinfo.value.response is response, "the failing response was not attached"


def test_the_chokepoint_requires_an_api_key(db):
    from app.services.integration_service import RespondClient

    client = RespondClient(api_key="", base_url="https://api.test")
    client.api_key = None

    with pytest.raises(ValueError):
        client.send_message("900000008", "hello")


def test_non_message_endpoints_stay_ungated(db):
    """Conversation status/assignee are not messages to the contact.

    Deliberate boundary: a muted contact's conversation can still be closed and
    reassigned, because those say nothing to the customer.
    """
    from app.services import integration_service as mod

    contact = _seed_contact(db, enabled=False)
    client = _respond_client()

    response = MagicMock(content=b"{}", status_code=200)
    response.json.return_value = {}
    http = MagicMock()
    http.__enter__.return_value.post.return_value = response

    with patch("app.database.SessionLocal", return_value=_NoClose(db)), patch.object(
        mod.httpx, "Client", return_value=http
    ):
        client.close_conversation(contact["respond_io_id"], category="resolved")
        client.set_conversation_assignee(contact["respond_io_id"], "user-1")

    assert http.__enter__.return_value.post.call_count == 2


# --------------------------------------------------------------------------
# Bulk control (what the CLI drives)
# --------------------------------------------------------------------------


def test_bulk_off_then_on_round_trips(db):
    from app.services.respond_outbound_service import set_all_outbound, status_counts

    for _ in range(3):
        _seed_contact(db, enabled=True)

    assert set_all_outbound(db, enabled=False) == 3
    counts = status_counts(db)
    assert counts == {"enabled": 0, "disabled": 3, "total": 3}

    assert set_all_outbound(db, enabled=True) == 3
    assert status_counts(db) == {"enabled": 3, "disabled": 0, "total": 3}


def test_bulk_counts_only_the_rows_it_changes(db):
    from app.services.respond_outbound_service import set_all_outbound

    _seed_contact(db, enabled=True)
    _seed_contact(db, enabled=False)

    assert set_all_outbound(db, enabled=False) == 1, "an already-disabled row is not a change"
    assert set_all_outbound(db, enabled=False) == 0, "a second run changes nothing"


def test_bulk_dry_run_writes_nothing(db):
    from app.services.respond_outbound_service import set_all_outbound, status_counts

    _seed_contact(db, enabled=True)
    _seed_contact(db, enabled=True)

    assert set_all_outbound(db, enabled=False, dry_run=True) == 2
    assert status_counts(db)["enabled"] == 2, "dry run wrote to the database"


def test_single_contact_toggle_by_each_reference_form(db):
    from app.services.respond_outbound_service import (
        find_contact,
        set_contact_outbound,
        status_counts,
    )

    contact = _seed_contact(db, enabled=True)

    for ref in (contact["id"], contact["respond_io_id"], contact["phone_number"]):
        assert find_contact(db, ref) is not None, f"{ref!r} did not resolve"

    row, changed = set_contact_outbound(db, contact["respond_io_id"], enabled=False)
    assert changed is True
    assert row.outbound_enabled is False
    assert status_counts(db) == {"enabled": 0, "disabled": 1, "total": 1}

    _, changed_again = set_contact_outbound(db, contact["phone_number"], enabled=False)
    assert changed_again is False, "an already-disabled contact is not a change"

    row, changed = set_contact_outbound(db, contact["id"], enabled=True)
    assert changed is True and row.outbound_enabled is True


def test_single_contact_dry_run_writes_nothing(db):
    from app.services.respond_outbound_service import set_contact_outbound, status_counts

    contact = _seed_contact(db, enabled=True)

    _, changed = set_contact_outbound(db, contact["id"], enabled=False, dry_run=True)
    assert changed is True, "dry run still reports what WOULD change"
    assert status_counts(db)["enabled"] == 1, "dry run wrote to the database"


def test_unknown_contact_reference_raises(db):
    from app.services.error_handler import AppException
    from app.services.respond_outbound_service import set_contact_outbound

    with pytest.raises(AppException):
        set_contact_outbound(db, "zzt-no-such-contact", enabled=False)
