"""Regenerates the two `documentation/plans/chatbot/samples/chat-turn-out_of_scope.*.json`
files the executor author renders expressions against.

Not part of the normal suite - it WRITES files outside `tests/`, which nothing else in
this package does. Skipped unless `WRITE_CHATBOT_SAMPLES=1` is set, so a future contract
change (a new action field, a reworded reply) can be re-captured the same way: `cd
sorento_crm_backend && WRITE_CHATBOT_SAMPLES=1 venv/bin/python -m pytest
tests/chatbot/test_generate_out_of_scope_samples.py -q`.

Real bytes, not hand-written: both files are `POST /api/v1/external/chat/turn`'s own
`response_model=TurnResponse` output, through `TestClient` against the blank-schema
`session_factory` (`app.api.v1.external.chat.SessionLocal` patched the same way
`test_chat_turn_endpoint.py`'s `stub_engine_seams` does, or the turn row lands in the real
dev database - see that fixture's docstring for the finding that taught this). The parser
and `check_access` are stubbed the way every chatbot integration test stubs them; the
escalation LANE runs for real (context resolution, team, comment text) with only its own
two I/O seams stubbed (`next_assignee`, `sla_create`) - which is the README's own claim
about how these files were produced, and the reason the assign/comment TEXT below is the
lane's real output rather than a literal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.lanes import escalation_services
from tests.chatbot.test_chat_turn_endpoint import api_key, client  # noqa: F401 - fixtures
from tests.chatbot.test_engine import _envelope

pytestmark = pytest.mark.skipif(
    not os.environ.get("WRITE_CHATBOT_SAMPLES"),
    reason="writes documentation/plans/chatbot/samples/*.json - set WRITE_CHATBOT_SAMPLES=1 to run",
)

_TURN_URL = "/api/v1/external/chat/turn"
_SAMPLES_DIR = (
    Path(__file__).resolve().parents[3] / "documentation" / "plans" / "chatbot" / "samples"
)

_CONTACT_ID = "900000008"
_PHONE = "+60120000527"

# The real dev contact and phone the README names (`contact 900000008 is the dev contact
# and is real`); the parser output is what a live call produces for "I need to speak to a
# human" as `request_for_help` - reproduced literally rather than re-derived so the sample
# reads the same as it always has.
_PARSE_OUTPUT = {
    "message_type": "request_for_help",
    "intent_hint": None,
    "domain_hint": None,
    "scope_intent": None,
    "is_affirmative": None,
    "user_goal": "trying to reach a person",
    "access_levels": [],
    "broaden_axis": None,
    "date_mode": None,
    "date_filter_start": None,
    "date_filter_end": None,
    "match_mode": "and",
    "demand_qty": None,
    "entities": [],
    "entity_op": "replace_combine",
    "scope_exclusive": False,
    "requested_attributes": [],
    "contains_flyer": False,
    "reference_positions": [],
    "reference_target": None,
    "person_mention": None,
    "is_active": None,
    "order_status": None,
    "correction": False,
    "routing": {"suggested_team": "customer_service", "suggested_agent": "general_enquiries", "team_source": None},
    "escalation": {"is_escalation_confirmation": True, "company_pick": None},
}

# UTC instants that read as 2026-09-05 12:00 / 16:00 and 2026-09-06 12:00 in
# Asia/Kuala_Lumpur (+08:00, no DST) - the same clock the sample has always shown.
_INITIATED_AT = "2026-09-05T04:00:00Z"
_DUE_AT = "2026-09-05T08:00:00Z"
_DUE_AT_RESOLUTION = "2026-09-06T04:00:00Z"


@pytest.fixture()
def _stub_seams(monkeypatch, session_factory):
    monkeypatch.setattr("app.api.v1.external.chat.SessionLocal", session_factory)

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: dict(_PARSE_OUTPUT))
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

    # The two I/O seams `escalation.run` reaches on a LIVE (non-dry-run) turn - see the
    # module docstring. Everything else in the lane (context, team, comment text) runs
    # for real against these.
    def fake_next_assignee(body):
        return {
            "assignee_id": "usr-100000001",
            "assignee_respond_user_id": "100000001",
            "is_already_assigned": False,
            "team_set_code": None,
            "brand_code": None,
            "company_id": None,
        }

    def fake_sla_create(body):
        return {
            "id": "sla-sample-1",
            "initiated_at": _INITIATED_AT,
            "due_at": _DUE_AT,
            "due_at_resolution": _DUE_AT_RESOLUTION,
        }

    def fake_build(db):
        return escalation_services.EscalationServices(
            resolve_and_gate=escalation_services._not_live("resolve_and_gate"),
            next_assignee=fake_next_assignee,
            sla_create=fake_sla_create,
            team_members=escalation_services._not_live("team_members"),
        )

    monkeypatch.setattr(escalation_services, "build", fake_build)
    monkeypatch.setattr(
        escalation_services,
        "production_session",
        # The lane now hands its session factory down (H56); this stand-in ignores it
        # and keeps yielding the blank-schema session the rest of the turn writes
        # through, which is the whole point of the fake.
        lambda _factory=None: _FakeSession(session_factory),
    )


class _FakeSession:
    """Stands in for `escalation_services.production_session()` - a context manager
    yielding the SAME blank-schema session the rest of the turn writes through, instead
    of a real `DATABASE_URL` connection."""

    def __init__(self, session_factory):
        self._factory = session_factory
        self._db = None

    def __enter__(self):
        self._db = self._factory()
        return self._db

    def __exit__(self, *exc_info):
        self._db.close()
        return False


def _envelope_for(message_id: str):
    envelope = _envelope(contact={"id": _CONTACT_ID, "firstName": "ZZT", "custom_fields": []})
    envelope.message["contact"]["id"] = _CONTACT_ID
    envelope.message["message"]["messageId"] = message_id
    envelope.message["message"]["contactId"] = _CONTACT_ID
    envelope.message["message"]["message"]["text"] = "I need to speak to a human"
    return envelope


def test_generate_out_of_scope_samples(client, api_key, session_factory, _stub_seams):
    db = session_factory()
    db.execute(
        text("DELETE FROM respond_contacts WHERE respond_io_id = :cid"), {"cid": _CONTACT_ID}
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": _CONTACT_ID, "phone": _PHONE, "sv": json.dumps({"variables": {}})},
    )
    db.commit()

    # The two-condition switch (`delegate_for`'s `enabled_lanes`): without this the code
    # can complete `out_of_scope` but is not TOLD to, and the turn still delegates.
    from app.models.user import SystemSetting

    setting = db.query(SystemSetting).first()
    if setting is None:
        setting = SystemSetting()
        db.add(setting)
    setting.chatbot_completed_lanes = ["out_of_scope"]
    db.commit()

    # Dry run FIRST, against the untouched session: D14 writes nothing, so the live turn
    # right after still sees the same fresh `{"variables": {}}` the dry run did - both
    # samples describe the SAME turn's starting point, not a chain of two.
    dry_envelope = _envelope_for("chat-turn-out_of_scope-dry-run")
    dry_payload = {
        "envelope": {**json.loads(dry_envelope.model_dump_json()), "test_run_id": "ZZT-samples-dry-run"}
    }
    dry_resp = client.post(_TURN_URL, json=dry_payload, headers={"X-API-Key": api_key})
    assert dry_resp.status_code == 200, dry_resp.text
    dry_body = dry_resp.json()
    assert dry_body["branch_kind"] == "out_of_scope"
    assert [a["kind"] for a in dry_body["actions"]] == [
        "send_message",
        "assign_conversation",
        "add_comment",
        "send_message",
    ]

    live_envelope = _envelope_for("chat-turn-out_of_scope-assigned")
    live_payload = {"envelope": json.loads(live_envelope.model_dump_json())}
    live_resp = client.post(_TURN_URL, json=live_payload, headers={"X-API-Key": api_key})
    assert live_resp.status_code == 200, live_resp.text
    live_body = live_resp.json()
    assert live_body["branch_kind"] == "out_of_scope"
    assert [a["kind"] for a in live_body["actions"]] == [
        "send_message",
        "assign_conversation",
        "add_comment",
        "send_message",
    ]

    _SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    (_SAMPLES_DIR / "chat-turn-out_of_scope.assigned.json").write_text(
        json.dumps(live_body, indent=2) + "\n"
    )
    (_SAMPLES_DIR / "chat-turn-out_of_scope.dry-run.json").write_text(
        json.dumps(dry_body, indent=2) + "\n"
    )
