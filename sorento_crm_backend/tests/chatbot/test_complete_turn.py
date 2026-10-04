"""`complete_turn` and `POST /chat/turn/{id}/complete` (AC-201, AC-203, AC-206, D14).

Four properties nothing else can prove:

* the tail WRITES the session, once, through `overwrite_for_contact`, with an
  `integration_log` beside it - D2's "one writer" is the whole point of S2, and after
  this ships n8n's `save-session-vars` is deleted;
* a DRY RUN writes nothing at all outside `chatbot.turns` (D14) and hands the would-be
  patch back instead, so a console or a clone turn is safe by construction;
* the turn CLOSES: status `done`, stage `remembered`, and the head's trace continues into
  `replied` / `remembered` rather than starting a second timeline;
* a second `/complete` for the same turn replays the first answer instead of re-writing
  the session, which is D15's shape one stage further on.

**`SessionLocal` must be patched by any test that drives the endpoint.** The route hands
the engine the module-level `SessionLocal`, the real `DATABASE_URL` engine, and `is_test`
suppresses business writes but not which database the turn row lands in - an earlier
version of the sibling endpoint test left a real half-written row in the shared dev
database. See `test_chat_turn_endpoint.py` for the full finding.

Retired 16 Sep 2026 (AC-1592, coordinator ruling, "complete_turn requires status
delegated; run_turn now completes in-process") - `TestTheTailWritesTheSession`'s 3
tests and `TestDryRunWritesNothing`'s 2 (`test_the_session_is_untouched_and_the_
patch_comes_back_instead`, `test_the_dry_run_patch_equals_what_a_live_run_would_
have_written`; the sibling `test_a_live_turn_does_not_return_the_patch` stays green).

Measured directly (`_head()`'s default `business_query` scenario, this session):
`run_turn` now completes the turn itself - `head.status == "done"`, `head.stage ==
"sent"` - not `"delegated"`. A `complete_turn()` call on that turn therefore hits
its OWN idempotent-replay path (see the still-green `TestGuards.test_completing_
twice_replays_the_first_answer_and_writes_once`), not a first write, so
`_fragments()` (this file's stand-in for n8n's own `sub-output` trigger payload)
is never consulted at all - these 5 tests' entire premise (head delegates with
nothing written; a SEPARATE `/complete` call is the first write) no longer arises
for ANY scenario `_head()` can construct here. The session shape these tests
assert on (`stored["variables"]["domain_hint"]`) is additionally the retired
nested shape (AC-1504/1521) - a real turn's session is five keys flat at the top
level (`focus`, `ideation`, `access_levels`, `open_question`, `contains_flyer`,
measured directly), confirming this is not a narrower fix.

`complete_turn`/`/complete` is NOT retired as a whole - it still answers 410 in S7
mode (`test_s7_dispatch_edges.py::TestCompleteGoneInS7ModeFullApp`) and still
guards/replays correctly (`TestGuards`'s 2 remaining tests, `TestTheEndpoint`'s
whole suite, all still green) - only the "a genuinely still-delegated turn gets a
first write from `/complete`" scenario has no construction left under the current
engine, and finding one (if any branch_kind still delegates today) is engine
investigation, not a mechanical port - flagged, not re-created.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip - registers every model before any query
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
# The endpoint fixtures are REUSED, not rebuilt: `api_key` issues a real integration key
# whose role holds exactly `integration.chat_turn.submit`, and `client` overrides
# `get_db` onto the blank schema. A second copy would be a second thing to keep in step
# with the auth chain.
from tests.chatbot.test_chat_turn_endpoint import api_key, client  # noqa: F401 - fixtures
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output

_COMPLETE_URL = "/api/v1/external/chat/turn/{turn_id}/complete"

PRIOR_SESSION = {"variables": {"domain_hint": "order", "response": "an earlier reply"}}


@pytest.fixture()
def seeded(session_factory):
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps(PRIOR_SESSION)},
    )
    db.commit()
    return db


@pytest.fixture()
def stub_parser(monkeypatch):
    def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
        return parser_mod.ParserConfig(
            system_prompt="stub",
            prompt_version=1,
            provider="openai",
            model="gpt-test",
            api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: _parser_output())
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, **kw: {"allowed": True, "decision": "allow", "agent_name": "General"},
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: None)


def _head(session_factory, **envelope_overrides):
    envelope = _envelope(**envelope_overrides)
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _fragments(**over: Any) -> dict[str, Any]:
    """The `sub-output` trigger contract, minimal: an item plus a lane answer."""
    body = {
        "item": {"branch_kind": "not_supported", "allowed": True},
        "result": None,
        "resolved": None,
        "gate": None,
        "offer_hold": None,
        "suggest_offer": None,
        "not_found": None,
        "incoming_picker": None,
        "access_choice": None,
        "crossdomain_render": None,
        "answer": None,
        "clarify": None,
    }
    body.update(over)
    return body


def _session_of(session_factory, contact_id: str | int = CONTACT_ID) -> dict:
    db = session_factory()
    row = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": str(contact_id)},
    ).first()
    raw = row.session_vars if row is not None else {}
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def _integration_logs(session_factory, business_table: str) -> int:
    db = session_factory()
    return db.execute(
        text("SELECT count(*) FROM integration_log WHERE business_table = :bt"),
        {"bt": business_table},
    ).scalar_one()


class TestDryRunWritesNothing:
    """D14: a test envelope does ZERO writes outside `chatbot.turns`, and the decision is
    made on the ENVELOPE at `/turn`, so a caller cannot turn a console turn into a live
    write by posting `/complete` to a different URL."""

    def test_a_live_turn_does_not_return_the_patch(self, seeded, stub_parser, session_factory):
        head = _head(session_factory, is_test=False)
        done = engine_mod.complete_turn(head.turn_id, _fragments(), session_factory=session_factory)
        assert done.session_patch is None, "the caller reads the session, it is not echoed"


class TestGuards:
    def test_an_unknown_turn_id_is_a_lookup_error_not_a_crash(self, seeded, session_factory):
        with pytest.raises(LookupError):
            engine_mod.complete_turn(
                "11111111-1111-1111-1111-111111111111",
                _fragments(),
                session_factory=session_factory,
            )

    def test_completing_twice_replays_the_first_answer_and_writes_once(
        self, seeded, stub_parser, session_factory
    ):
        head = _head(session_factory, is_test=False)
        first = engine_mod.complete_turn(head.turn_id, _fragments(), session_factory=session_factory)
        second = engine_mod.complete_turn(head.turn_id, _fragments(), session_factory=session_factory)
        assert second.reply == first.reply
        assert _integration_logs(session_factory, "respond_contacts.session_vars") == 1

    # FIXED (coder, 5 Sep): `complete_turn` refuses any status but `delegated` (and the
    # `done` idempotent replay) BEFORE the tail runs. Left unguarded it composed a reply
    # out of whatever `branch_kind` the caller's fragments carried, wrote it to the
    # customer's session, and overwrote `status` / `error` with `done` / null - erasing
    # the R4 / H32 record the trace screen exists to show.
    def test_completing_a_turn_that_never_delegated_is_refused(
        self, seeded, session_factory, monkeypatch
    ):
        """D15's shape one stage earlier: a turn the head never handed to a lane (a
        failed parse) has no tail to run, and `/complete` must say so rather than
        inventing an answer."""
        from app.services.chatbot.head import parser as parser_mod

        def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(
            parser_mod, "parse", lambda config, user_block: (_ for _ in ()).throw(
                parser_mod.ParserError("boom")
            )
        )
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, **kw: {"allowed": True, "decision": "allow", "agent_name": "General"},
        )
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: None)

        head = _head(session_factory, is_test=False)
        assert head.status == "failed", "the head must have actually failed for this to prove anything"

        with pytest.raises(Exception) as raised:
            engine_mod.complete_turn(head.turn_id, _fragments(), session_factory=session_factory)
        assert "409" in str(raised.value) or "not delegated" in str(raised.value).lower()

    # B2 (reviewer finding) / AC-1592: the three tests that used to live here
    # (`test_a_key_outside_the_allowlist_raises_before_the_write`,
    # `test_a_second_probe_key_also_raises_before_the_write`,
    # `test_the_turn_row_is_closed_failed_at_remembered_when_the_allowlist_raises`) are
    # RETIRED, not ported. All three poisoned `tail.compile_state.compile_current_state`'s
    # RETURN VALUE to smuggle an extra key (`dym_probe_entities`, `_dym_probe_input`) into
    # the session write, then asserted AC-203/H15 (`extra="forbid"`) caught it before the
    # write. `tail/compile_state.py` is deleted (AC-1594); `run_tail` (the function
    # `complete_turn` - still live, `engine.py::complete_turn` - itself calls for its tail,
    # confirmed by reading it end to end this session) builds the session write's `payload`
    # from a HARD-CODED five-key dict literal (`focus`, `open_question`, `ideation`,
    # `access_levels`, `contains_flyer`, each read off `state`/`question`/`before` by name)
    # and validates it with `SessionVars(**payload)` - there is no longer any producer-
    # mutable dict for a stray key to leak INTO before that call, so the vulnerability these
    # three tests poisoned for is structurally impossible now, not merely validated-and-
    # rejected. The model-level property (AC-203/H15, "a key outside the five raises") is
    # already re-proven against the CURRENT `SessionVars` shape in `test_rearch_port_tail_
    # units.py::TestSessionVarsIsAWallForTheFiveKeyShape` (same `dym_probe_entities` poison
    # value), a real replacement, not a coverage hole for that half. The other half - that
    # `complete_turn` leaves `respond_contacts`/the prior session untouched and closes the
    # turn `failed` at `remembered` specifically on THIS poison path - has no equivalent
    # injection point left to port to (the general "a tail exception closes the turn failed"
    # property is a different, still-testable claim than this specific one, out of this
    # item's scope; flagged, not silently dropped).


class TestTheEndpoint:
    """The route itself: auth, the module guard and `response_model` survival."""

    def _post(self, client, api_key, turn_id, body):
        return client.post(
            _COMPLETE_URL.format(turn_id=turn_id), json=body, headers={"X-API-Key": api_key}
        )

    def test_without_the_slug_the_route_is_refused(self, client, session_factory):
        resp = client.post(
            _COMPLETE_URL.format(turn_id="11111111-1111-1111-1111-111111111111"),
            json=_fragments(),
        )
        assert resp.status_code in (401, 403), resp.text

    def test_an_unknown_turn_is_a_404_naming_it(self, client, api_key, session_factory, monkeypatch):
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        resp = self._post(client, api_key, "11111111-1111-1111-1111-111111111111", _fragments())
        assert resp.status_code == 404, resp.text
        assert "11111111-1111-1111-1111-111111111111" in resp.json()["detail"]

    def test_the_body_rejects_an_undeclared_field(self, client, api_key, session_factory, monkeypatch):
        """`extra = "forbid"` on the request: a caller sending a field the tail does not
        read is a caller whose expectations have drifted, and a silent drop hides it."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        resp = self._post(
            client, api_key, "11111111-1111-1111-1111-111111111111", {**_fragments(), "nope": {}}
        )
        assert resp.status_code == 422, resp.text

    def test_every_response_field_survives_serialisation(
        self, client, api_key, session_factory, monkeypatch
    ):
        """`response_model` silently DROPS an undeclared field, so each one is asserted."""
        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)
        canned = {
            "turn_id": "22222222-2222-2222-2222-222222222222",
            "reply": {"text": "hi", "quick_replies": "a,b", "result_set": [], "attachments_src": None},
            "actions": [{"kind": "update_contact_fields", "fields": {}, "dry_run": True}],
            "session_patch": {"variables": {"pending": None}},
        }

        class _Fake:
            def as_dict(self):
                return canned

        monkeypatch.setattr("app.api.v1.external.chat.complete_turn", lambda *a, **k: _Fake())
        resp = self._post(client, api_key, canned["turn_id"], _fragments())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("turn_id", "reply", "actions", "session_patch"):
            assert key in body, f"{key!r} missing from the response body: {body}"
        # Every field of `reply`, not just the two the earlier version of this test
        # happened to pick: `text` and `result_set` are exactly as droppable as
        # `attachments_src` if a future edit to `CompleteResponse` forgets one.
        assert body["reply"]["text"] == "hi"
        assert body["reply"]["quick_replies"] == "a,b"
        assert body["reply"]["result_set"] == []
        assert body["reply"]["attachments_src"] is None
        assert body["actions"] == canned["actions"]
        assert body["session_patch"] == canned["session_patch"]

    # FIXED (coder, 5 Sep): the engine raises `AppException(409, ...)`, which IS an
    # `HTTPException`, so the route's existing handler carries the status and the reason
    # through without a new branch.
    def test_complete_on_a_non_delegated_turn_is_a_409(
        self, client, api_key, session_factory, monkeypatch
    ):
        from app.services.chatbot.head import parser as parser_mod

        monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

        def fake_resolve_config(db, *, current_date, override_version_id=None, grants=None):
            return parser_mod.ParserConfig(
                system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
            )

        monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
        monkeypatch.setattr(
            parser_mod, "parse", lambda config, user_block: (_ for _ in ()).throw(
                parser_mod.ParserError("boom")
            )
        )
        monkeypatch.setattr(
            engine_mod,
            "check_access",
            lambda db, **kw: {"allowed": True, "decision": "allow", "agent_name": "General"},
        )
        monkeypatch.setattr(engine_mod, "default_space_id", lambda db: None)

        db = session_factory()
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
            ),
            {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
        )
        db.commit()

        head = engine_mod.run_turn(_envelope(), session_factory=session_factory)
        assert head.status == "failed"

        resp = self._post(client, api_key, head.turn_id, _fragments())
        assert resp.status_code == 409, resp.text
