"""DO-PERIOD-SKIP AC-5 and AC-6: `check_access` carries `skip_period_questions`, and ONE helper
`do_ask.skips_period_questions(ctx)` is the only reader."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.services.chatbot import do_ask
from tests.chatbot.test_memory_profile_facts import _cid, _seed_contact


def _access(session_factory, contact_pk: str) -> dict:
    from app.services.chatbot.head.access import check_access

    return check_access(session_factory(), agent_code="general_enquiries", contact_id=contact_pk, space_id=None)


def test_ac5_a_default_contact_reads_false(session_factory):
    pk = _seed_contact(session_factory, _cid())
    assert _access(session_factory, pk)["skip_period_questions"] is False


def test_ac5_a_switched_on_contact_reads_true(session_factory):
    pk = _seed_contact(session_factory, _cid())
    db = session_factory()
    db.execute(text("UPDATE respond_contacts SET chatbot_skip_period_questions = true WHERE id = :c"), {"c": pk})
    db.commit()
    assert _access(session_factory, pk)["skip_period_questions"] is True


def test_ac5_an_unknown_contact_reads_false(session_factory):
    assert _access(session_factory, str(uuid.uuid4()))["skip_period_questions"] is False


@pytest.mark.parametrize(
    "ctx, expected",
    [
        ({"access": {"skip_period_questions": True}}, True),
        ({"access": {"skip_period_questions": False}}, False),
        ({"access": {}}, False),
        ({}, False),
        ({"access": None}, False),
        ({"access": "yes"}, False),
        ({"access": ["skip_period_questions"]}, False),
    ],
)
def test_ac6_the_helper_reads_the_ctx_key_and_is_false_for_anything_else(ctx, expected):
    assert do_ask.skips_period_questions(ctx) is expected


def test_ac6_no_other_module_reads_the_column_or_the_ctx_key():
    """The only readers: the helper in do_ask and check_access which writes the key."""
    import pathlib

    root = pathlib.Path(do_ask.__file__).resolve().parents[2]  # app/
    allowed = {"do_ask.py", "access.py"}
    offenders = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        if path.name in allowed:
            continue
        if any(m in src for m in ("skip_period_questions\"]", "skip_period_questions')", ".get(\"skip_period_questions\"")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders


# --- round 2 (D1): the engine passes the RESPOND.IO id and the workspace's space_id ---------- #


def _workspace(session_factory) -> tuple[str, str]:
    from app.models.respond_workspace import RespondWorkspace

    db = session_factory()
    ws = RespondWorkspace(space_id=f"ZZT-sp-{uuid.uuid4().hex[:8]}", name="ZZT ws", api_key_ciphertext="x")
    db.add(ws)
    db.commit()
    return ws.id, ws.space_id


def _contact_in(session_factory, workspace_id: str, respond_io_id: str, *, skip: bool) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, workspace_id, phone_number, session_vars, "
            "chatbot_skip_period_questions) VALUES (:id, :rid, :ws, :ph, CAST('{}' AS jsonb), :skip)"
        ),
        {"id": str(uuid.uuid4()), "rid": respond_io_id, "ws": workspace_id,
         "ph": f"+6011{uuid.uuid4().hex[:8]}", "skip": skip},
    )
    db.commit()


def test_d1_the_engines_respond_io_id_and_space_id_read_the_switch(session_factory):
    from app.services.chatbot.head.access import check_access

    ws_id, space_id = _workspace(session_factory)
    rid = f"ZZT-rid-{uuid.uuid4().hex[:8]}"
    _contact_in(session_factory, ws_id, rid, skip=True)
    out = check_access(session_factory(), agent_code="general_enquiries", contact_id=rid, space_id=space_id)
    assert out["skip_period_questions"] is True, out


def test_d1_the_same_respond_io_id_in_another_workspace_does_not_leak_true(session_factory):
    from app.services.chatbot.head.access import check_access

    on_ws, _on_space = _workspace(session_factory)
    off_ws, off_space = _workspace(session_factory)
    rid = f"ZZT-rid-{uuid.uuid4().hex[:8]}"
    _contact_in(session_factory, on_ws, rid, skip=True)
    _contact_in(session_factory, off_ws, rid, skip=False)
    out = check_access(session_factory(), agent_code="general_enquiries", contact_id=rid, space_id=off_space)
    assert out["skip_period_questions"] is False, out
    on_out = check_access(session_factory(), agent_code="general_enquiries", contact_id=rid, space_id=_on_space)
    assert on_out["skip_period_questions"] is True, on_out


# --- round 3 (R2): a contact whose workspace_id is NULL ------------------------------------ #


def test_r2_a_null_workspace_contact_with_the_switch_on_reads_true(session_factory):
    from app.services.chatbot.head.access import check_access

    _ws_id, space_id = _workspace(session_factory)
    rid = f"ZZT-rid-{uuid.uuid4().hex[:8]}"
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, workspace_id, phone_number, session_vars, "
            "chatbot_skip_period_questions) VALUES (:id, :rid, NULL, :ph, CAST('{}' AS jsonb), true)"
        ),
        {"id": str(uuid.uuid4()), "rid": rid, "ph": f"+6011{uuid.uuid4().hex[:8]}"},
    )
    db.commit()
    out = check_access(session_factory(), agent_code="general_enquiries", contact_id=rid, space_id=space_id)
    assert out["skip_period_questions"] is True, out
