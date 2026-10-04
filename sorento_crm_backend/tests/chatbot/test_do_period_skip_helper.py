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
