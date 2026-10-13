"""DO-PERIOD-SKIP AC-4: Copy access carries the "Skip date-range questions" switch like the other
chatbot switches (the source's value lands on the target, reported as a change)."""
from __future__ import annotations

from tests.test_contact_bulk_copy_access import (  # noqa: F401 - fixtures + helpers
    _by_id,
    _contact,
    _post,
    client,
    db,
    perms,
)

FIELD = "chatbot_skip_period_questions"


def test_ac4_the_source_switch_is_copied_to_the_target(client, db):  # noqa: F811
    source = _contact(db, "dps-source")
    target = _contact(db, "dps-target")
    setattr(source, FIELD, True)
    db.flush()
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    row = _by_id(resp.json())[target.id]
    assert row["status"] == "changed", row
    change = next(c for c in row["changes"] if c["facet"] == FIELD)
    assert (change["before"], change["after"]) == (False, True), change
    db.expire_all()
    assert getattr(db.get(type(target), target.id), FIELD) is True


def test_ac4_a_matching_switch_is_not_a_change(client, db):  # noqa: F811
    source = _contact(db, "dps-s2")
    target = _contact(db, "dps-t2")
    resp = _post(client, source, [target])
    assert resp.status_code == 200, resp.text
    assert all(c["facet"] != FIELD for c in _by_id(resp.json())[target.id]["changes"])
    db.expire_all()
    assert getattr(db.get(type(target), target.id), FIELD) is False
