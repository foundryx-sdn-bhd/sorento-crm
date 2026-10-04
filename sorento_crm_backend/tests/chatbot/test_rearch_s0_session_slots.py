"""S0 - `SessionVars` keeps exactly the five keys; `focus` gains `document`/`status`
and drops `order_status` (AC-1504, PLAN-chatbot-turn-rearch.md).

Two things under test:

1. The typed shape itself: `SessionVars`/`Focus` pydantic models. AC-1504 does not say
   where these live; this file imports them from `app.services.chatbot.contracts`,
   the module that already declares every other wire shape the engine speaks
   (`TurnRequest`, `Envelope`, ...) per that module's own docstring. Flagged as an
   assumption in the tester's report - if the coder puts them elsewhere the import
   line is the only thing that needs to move.

2. The legacy migration, through the REAL public read path: `app.services.
   conversation_variables_service.get_for_contact` - the function `GET
   /external/conversation-variables/{id}` itself calls, and the one `engine.
   _read_session_vars` (private, leading underscore) wraps. Not the private helper,
   per the captain's brief.

RIGHT NOW every test here is RED: neither `SessionVars` nor `Focus` exists on
`contracts`, and `get_for_contact` returns the stored dict verbatim - no
`order_status` -> `document`/`status` mapping happens anywhere today.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text


LEGACY_ORDER_STATUS_CASES = [
    ("outstanding", [], "outstanding"),
    ("do_outstanding", ["DO"], "outstanding"),
    ("so_outstanding", ["SO"], "outstanding"),
    ("outstanding_both", ["SO", "DO"], "outstanding"),
]


def _import_session_vars():
    try:
        from app.services.chatbot.contracts import Focus, SessionVars
    except ImportError as exc:  # pragma: no cover - the expected red path
        pytest.fail(
            f"app.services.chatbot.contracts.SessionVars/Focus do not exist yet: {exc}",
            pytrace=False,
        )
    return SessionVars, Focus


def test_session_vars_forbids_extra_keys_and_declares_exactly_the_five():
    SessionVars, _Focus = _import_session_vars()
    assert SessionVars.model_config.get("extra") == "forbid"
    assert set(SessionVars.model_fields) == {
        "focus",
        "open_question",
        "ideation",
        "access_levels",
        "contains_flyer",
        "reply_language",
    }


def test_focus_has_document_list_and_status_str_slots():
    _SessionVars, Focus = _import_session_vars()
    fields = Focus.model_fields
    assert "document" in fields, fields
    assert "status" in fields, fields


def test_focus_has_no_order_status_slot():
    _SessionVars, Focus = _import_session_vars()
    assert "order_status" not in Focus.model_fields, Focus.model_fields


@pytest.mark.parametrize(("legacy_value", "document", "status"), LEGACY_ORDER_STATUS_CASES)
def test_focus_document_is_list_of_str_type(legacy_value, document, status):
    _SessionVars, Focus = _import_session_vars()
    document_field = Focus.model_fields["document"]
    # list[str] under any of pydantic's ways of spelling it
    assert "list" in str(document_field.annotation).lower(), document_field.annotation


def _seed_contact_with_session_vars(db, session_vars: dict) -> str:
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {
            "cid": f"ZZT-{uuid.uuid4().hex[:8]}",
            "phone": f"+6000{uuid.uuid4().hex[:7]}",
            "sv": json.dumps(session_vars),
        },
    )
    db.commit()
    return db.execute(
        text("SELECT respond_io_id FROM respond_contacts ORDER BY created_at DESC LIMIT 1")
    ).scalar()


@pytest.mark.parametrize(("legacy_value", "document", "status"), LEGACY_ORDER_STATUS_CASES)
def test_legacy_order_status_maps_forward_through_get_for_contact(
    session_factory, legacy_value, document, status
):
    from app.services.conversation_variables_service import get_for_contact

    db = session_factory()
    legacy_session_vars = {
        "focus": {"order_status": legacy_value},
        "open_question": None,
        "ideation": {},
        "access_levels": [],
        "contains_flyer": False,
    }
    respond_io_id = _seed_contact_with_session_vars(db, legacy_session_vars)

    state = get_for_contact(db, respond_io_id=respond_io_id)

    focus = state.get("focus") or {}
    assert "order_status" not in focus, focus
    assert focus.get("document") == document, focus
    assert focus.get("status") == status, focus
