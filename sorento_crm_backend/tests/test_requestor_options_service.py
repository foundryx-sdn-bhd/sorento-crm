"""``requestor_options_service.list_requestor_options`` (PLAN-requested-by-contact-routing.md
groups B/C). Eligibility gate (fail-closed), inactive-segment exclusion, ``q``
filter, ``include_ids`` bypassing both eligibility AND ``q``, and the
names-only response shape (no phone/email/respond_io_id ever leaks).
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import MarketSegment, RespondContact, respond_contact_market_segments
from app.services.requestor_options_service import list_requestor_options
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _segment(db, *, code, is_requestor_selectable=True, is_active=True) -> str:
    seg = MarketSegment(
        code=code, name=code, is_active=is_active, is_requestor_selectable=is_requestor_selectable
    )
    db.add(seg)
    db.commit()
    return seg.code


def _contact(db, *, name, segments=()) -> str:
    c = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6011{uuid.uuid4().hex[:8]}", name=name)
    db.add(c)
    db.flush()
    for code in segments:
        db.execute(
            respond_contact_market_segments.insert().values(contact_id=c.id, segment_code=code)
        )
    db.commit()
    return c.id


def test_zero_flagged_segments_returns_empty_directory(db):
    _segment(db, code="RETAIL", is_requestor_selectable=False)
    contact_y = _contact(db, name="CONTACT Y", segments=["RETAIL"])

    items, has_more = list_requestor_options(db)
    assert items == []
    assert has_more is False


def test_eligible_segment_contact_is_returned(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    contact_y = _contact(db, name="CONTACT Y", segments=["PROJECT"])

    items, has_more = list_requestor_options(db)
    assert [i["id"] for i in items] == [contact_y]
    assert items[0]["name"] == "CONTACT Y"


def test_inactive_segment_excludes_its_contacts(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True, is_active=False)
    _contact(db, name="CONTACT Y", segments=["PROJECT"])

    items, _ = list_requestor_options(db)
    assert items == []


def test_segment_not_flagged_selectable_excludes_its_contacts(db):
    _segment(db, code="RETAIL", is_requestor_selectable=False, is_active=True)
    _contact(db, name="Someone", segments=["RETAIL"])

    items, _ = list_requestor_options(db)
    assert items == []


def test_q_filters_case_insensitive_substring(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    contact_y = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    _contact(db, name="Farah Lim", segments=["PROJECT"])

    items, _ = list_requestor_options(db, q="contact y")
    assert [i["id"] for i in items] == [contact_y]

    items_none, _ = list_requestor_options(db, q="zzz-no-match")
    assert items_none == []


def test_include_ids_bypasses_both_q_and_eligibility(db):
    """D6: the submitting contact + the currently-saved requestor are always
    options, even with no flagged segment and even when `q` would exclude them."""
    contact_x = _contact(db, name="CONTACT X Submitter")  # no segments at all

    items, _ = list_requestor_options(db, q="nonsense-query-that-matches-nobody", include_ids=[contact_x])
    assert [i["id"] for i in items] == [contact_x]


def test_include_ids_union_with_eligible_set_dedupes_by_id(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    contact_y = _contact(db, name="CONTACT Y", segments=["PROJECT"])

    # include_ids repeats the already-eligible id -> no duplicate row.
    items, _ = list_requestor_options(db, include_ids=[contact_y])
    assert len(items) == 1
    assert items[0]["id"] == contact_y


def test_names_only_no_phone_email_respond_io_id_keys_leak(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    _contact(db, name="CONTACT Y", segments=["PROJECT"])

    items, _ = list_requestor_options(db)
    assert len(items) == 1
    assert set(items[0].keys()) == {"id", "name"}


def test_limit_and_has_more(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    for i in range(3):
        _contact(db, name=f"Contact {i}", segments=["PROJECT"])

    items, has_more = list_requestor_options(db, limit=2)
    assert len(items) == 2
    assert has_more is True

    items_all, has_more_all = list_requestor_options(db, limit=10)
    assert len(items_all) == 3
    assert has_more_all is False


def test_duplicate_names_are_distinct_rows_by_id(db):
    _segment(db, code="PROJECT", is_requestor_selectable=True)
    a = _contact(db, name="Cindy", segments=["PROJECT"])
    b = _contact(db, name="Cindy", segments=["PROJECT"])

    items, _ = list_requestor_options(db)
    ids = {i["id"] for i in items}
    assert ids == {a, b}
    assert len(items) == 2


# ---------------------------------------------------------------------------
# filter_form_referenced_ids - `include_ids` must not turn the internal picker
# into an id -> contact-name lookup for arbitrary ids (code-review S7).
# ---------------------------------------------------------------------------


def test_include_ids_filtered_to_ids_referenced_by_a_form_row(db):
    import uuid as _uuid

    from app.models.procurement import PurchaseRequestHeader, StockInquiry
    from app.services.requestor_options_service import filter_form_referenced_ids

    submitter = _contact(db, name="Submitter")
    saved_requestor = _contact(db, name="Saved Requestor")
    stranger = _contact(db, name="Stranger")  # never referenced by any form row

    db.add(
        PurchaseRequestHeader(
            id=str(_uuid.uuid4()),
            request_number="REF-PR-1",
            request_type="purchase_request",
            contact_id=submitter,
        )
    )
    db.add(
        StockInquiry(
            id=str(_uuid.uuid4()),
            inquiry_number="REF-SI-1",
            status="new",
            salesperson_contact_id=saved_requestor,
        )
    )
    db.commit()

    allowed = filter_form_referenced_ids(db, [submitter, saved_requestor, stranger])
    assert set(allowed) == {submitter, saved_requestor}
    # A guessed id resolves to nothing at all.
    assert filter_form_referenced_ids(db, [stranger]) == []
    assert filter_form_referenced_ids(db, []) == []
    assert filter_form_referenced_ids(db, None) == []


def test_eligible_options_are_deduplicated_across_multiple_flagged_segments(db):
    """A contact tagged with two flagged segments joined twice and appeared twice
    in the picker, eating two of the `limit` slots (code-review S4)."""
    _segment(db, code="PROJECT")
    _segment(db, code="SPECIFIER")
    both = _contact(db, name="Double Tagged", segments=["PROJECT", "SPECIFIER"])

    items, _has_more = list_requestor_options(db, q=None, limit=50)
    assert [i["id"] for i in items].count(both) == 1
