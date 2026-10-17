"""Audit trail status labels (SR-CORE review): a status-engine move reads by label, an unknown old
status never reads as "no status", and a non-UUID value never reaches the statuses query."""
from __future__ import annotations

from types import SimpleNamespace

from app.api.v1.audit.audit_logs import _derive_description, _status_labels

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


def _row(old, new):
    return SimpleNamespace(
        description=None, old_values=old, new_values=new, entity_type="project_sample_requests",
        action="UPDATE",
    )


def test_known_move_reads_by_label():
    assert _derive_description(_row({"status_id": A}, {"status_id": B}), {A: "Draft", B: "Submitted"}) == (
        "Status: Draft \u2192 Submitted"
    )


def test_first_status_reads_as_none_before():
    assert _derive_description(_row({}, {"status_id": B}), {B: "Submitted"}) == "Status: \u2205 \u2192 Submitted"


def test_unknown_old_status_is_not_shown_as_none():
    text = _derive_description(_row({"status_id": A}, {"status_id": B}), {B: "Submitted"})
    assert text == "Status changed to Submitted"


class _NoQuery:
    def query(self, *a, **k):  # pragma: no cover - reaching it is the failure
        raise AssertionError("a non-UUID status_id must never reach the statuses query")


def test_non_uuid_values_never_reach_the_query():
    rows = [_row({"status_id": "legacy-text"}, {"status_id": "open"})]
    assert _status_labels(_NoQuery(), rows) == {}
