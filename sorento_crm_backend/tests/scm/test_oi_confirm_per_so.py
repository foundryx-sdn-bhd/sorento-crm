"""AC-CF-1..16 (`PLAN-oi-confirm-per-so.md`,
`oi-confirm-per-so-acceptance-criteria.md`): red tests written BEFORE the coder, against
the contract only - no implementation for this slice exists yet.

S1 of the plan REVERSES G4 (`PLAN-scm-reorder-oi-feedback-1sep.md`): a row raised from
the fulfilment board is born `awaiting` again, not `acknowledged`
(`_handshake_for_raise`, `app/services/project_order_inquiry_service.py`), and
purchasing's own Confirm press is what takes it on - the very thing G4 retired. Most of
the assertions below therefore state the POST-S1 world and fail TODAY for that single
reason: the raise still stamps `ACK_ACKNOWLEDGED`. `tests/test_order_inquiry_handshake.py`
and `tests/test_order_inquiry_handshake_edges.py` pin the PRE-S1 (G4) behaviour this lane
retires and are left untouched here - they are the coder's to update, not the tester's.

Reuses `tests/test_order_inquiry_handshake.py`'s harness wholesale (`world` / `api`
fixtures - one CS user, one purchasing user, one project, one product, one warehouse,
company-scoped and rolled back; `_raise_one_row`, `_as_purchasing`, `_settle`,
`_project_committed`, `_open_po_line`, `_links_of`) rather than rebuilding it: every row
this file touches is seeded fresh, behind that harness's own `zzt-planchg` / `ZZT-`
markers, inside ONE outer transaction rolled back at teardown - nothing here depends on
a row already in the shared local database.

Runs on the REAL database (rolled back), for the same reason the harness does:
`scm.committed_v`, the `ack_state` column and its index all live in the migrated schema,
which a blank scratch schema does not carry.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    ACK_CHANGED,
    ACK_REJECTED,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    IV_ORDER,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryRow,
)
from app.services.project_supply_service import ProjectSupplyService

from ..test_order_inquiry_handshake import (
    ACK_URL,
    LIST,
    WAS,
    _as_purchasing,
    _confirm,
    _core_line,
    _core_so,
    _line_payload,
    _links_of,
    _open_po_line,
    _order_row,
    _project_committed,
    _project_line,
    _project_so,
    _raise_one_row,
    _settle,
    api,
    world,
)
from ..test_order_inquiry_suggested_links import _suggested_of
from ..test_planning_changes import MARKER, _uid

__all__ = ["api", "world"]  # re-exported fixtures; keeps linters from calling them unused


# ---------------------------------------------------------------------------
# seeding helper this file owns: several rows on ONE sales order
# ---------------------------------------------------------------------------


def _raise_n_rows_one_so(api, qtys):
    """N lines of ONE sales order, all confirmed wholly as Buy in a single press - N
    raised inquiry rows sharing one SO number a caller can search by (AC-CF-8b)."""
    client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_lines = [
        _core_line(
            db, core_so, world.product, world.warehouse,
            qty_ordered=qty, required_date=WAS,
        )
        for qty in qtys
    ]
    order = _project_so(
        db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number
    )
    lines = [
        _project_line(db, order, line_no=i + 1, product=world.product, core_line=core_line)
        for i, core_line in enumerate(core_lines)
    ]
    db.commit()

    response = _confirm(
        client, order.id,
        [_line_payload(line.id, buy_qty=qty) for line, qty in zip(lines, qtys)],
    )
    assert response.status_code == 200, response.text
    db.commit()
    return {
        "order": order,
        "so_number": core_so.so_number,
        "lines": lines,
        "rows": [_order_row(world, line) for line in lines],
    }


# ---------------------------------------------------------------------------
# AC-CF-1 / AC-CF-2 / AC-CF-3: the handshake, reversed
# ---------------------------------------------------------------------------


def test_ac_cf_1_row_born_awaiting_on_raise(api):
    """AC-CF-1. A board confirm raises a row `awaiting`, `acknowledged_by`/`_at` null.

    Today `_handshake_for_raise` returns `(ACK_ACKNOWLEDGED, actor_user_id, now, None)`
    for a fresh row (G4) - red on all three assertions until the raise is flipped to
    born-awaiting (S1)."""
    _client, world = api
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]

    assert row.ack_state == ACK_AWAITING
    assert row.acknowledged_by is None
    assert row.acknowledged_at is None


def test_ac_cf_2_carry_keeps_a_confirmed_rows_ack_fields(api):
    """AC-CF-2. The row is forced to `awaiting` first (the S1 precondition the raise
    will create natively) and genuinely taken on via `acknowledge_rows`; CS then
    re-confirms naming a DIFFERENT line of the same order, so the taken-on row is
    CARRIED, not restated. `_handshake_for_raise`'s `carried` branch copies the prior
    row's stamps verbatim and does not read whether the prior state was born-
    acknowledged or genuinely acknowledged - that branch is untouched by S1, so this
    already passes: a regression pin for the half of the handshake this lane leaves
    alone, not a red test."""
    _client, world = api
    fixture = _raise_n_rows_one_so(api, ["4", "6"])
    kept_row, _other_row = fixture["rows"]
    kept_row.ack_state = ACK_AWAITING
    kept_row.acknowledged_by = None
    kept_row.acknowledged_at = None
    world.db.commit()

    with _as_purchasing(world) as buyer:
        assert (
            buyer.post(ACK_URL, json={"row_ids": [str(kept_row.id)]}).status_code == 200
        )
    world.db.commit()
    world.db.refresh(kept_row)
    stamped_by, stamped_at = kept_row.acknowledged_by, kept_row.acknowledged_at
    assert stamped_by and stamped_at

    other_line = fixture["lines"][1]
    response = _confirm(
        _client, fixture["order"].id, [_line_payload(other_line.id, buy_qty="6")]
    )
    assert response.status_code == 200, response.text
    world.db.commit()

    # An unchanged carried row is kept and re-filed under the new revision
    # (ORDERBACK-DELTA, 13 Oct 2026); what this proves is the stamp standing.
    carried = _order_row(world, fixture["lines"][0])
    assert carried.ack_state == ACK_ACKNOWLEDGED
    assert carried.acknowledged_by == stamped_by
    assert carried.acknowledged_at == stamped_at
    assert carried.changed_at is None


def test_ac_cf_3_change_on_an_acknowledged_row_marks_changed_not_reacknowledged(api):
    """AC-CF-3. Forced to `awaiting` first, then genuinely acknowledged by purchasing,
    then CS changes the quantity: `_settle_row_in_place`
    (`project_order_inquiry_service.py`, the `if row.ack_state in (ACK_ACKNOWLEDGED,
    ACK_CHANGED):` block) today re-stamps `ack_state=ACK_ACKNOWLEDGED`,
    `acknowledged_by=<the CS actor>`, `acknowledged_at=<now>` on every change - the
    opposite of AC-CF-3, which wants `changed` and purchasing's PRIOR stamp kept
    untouched. Red on `ack_state`, `acknowledged_by` and `acknowledged_at`."""
    _client, world = api
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    row.ack_state = ACK_AWAITING
    row.acknowledged_by = None
    row.acknowledged_at = None
    world.db.commit()

    with _as_purchasing(world) as buyer:
        assert buyer.post(ACK_URL, json={"row_ids": [str(row.id)]}).status_code == 200
    world.db.commit()
    world.db.refresh(row)
    stamped_by, stamped_at = row.acknowledged_by, row.acknowledged_at
    assert stamped_by and stamped_at

    _settle(world, fixture, qty="25")

    world.db.refresh(row)
    assert row.ack_state == ACK_CHANGED
    assert row.changed_at is not None
    assert row.acknowledged_by == stamped_by
    assert row.acknowledged_at == stamped_at
    assert Decimal(str(row.qty)) == Decimal("25")
    assert Decimal(str(row.previous_qty)) == Decimal("10")


def test_ac_cf_1b_borrow_asker_row_is_born_awaiting(api):
    """AC-CF-1b (review round). `ProjectSupplyService._place_supply_borrows` writes a
    step-3 supply borrow's own asker-side ORDER_BACK row with `supply_decision_id` set
    - board-origin exactly like any other decision-linked row - so S1's rule covers it
    too: born `ACK_AWAITING`, no acknowledge stamps, even though the cascade may have
    already linked it to the donor document (AC-CF-4). Built the same way
    `tests/test_project_supply_borrow_row_ack.py::test_a_supply_borrow_row_is_born_awaiting`
    exercises the method directly, since this file's own fixtures never reach step 3."""
    _client, world = api
    fixture = _raise_one_row(api, qty="10")
    order = fixture["order"]
    line = fixture["line"]
    _po, po_line = _open_po_line(world, qty=50)

    supply = ProjectSupplyService(world.db)
    decision = supply.active_decision(str(order.id))
    assert decision is not None
    inquiry = (
        world.db.query(OrderInquiry)
        .filter(OrderInquiry.id == fixture["row"].order_inquiry_id)
        .one()
    )

    item = SimpleNamespace(
        supply_key=f"po:{po_line.id}",
        qty=Decimal("5"),
        donor_core_line_id=None,
        supply_document=None,
        reason="Step 3 supply borrow",
    )
    entry = SimpleNamespace(borrow=[item])
    checked = [(line, entry, None)]

    supply._place_supply_borrows(
        order, decision, checked, inquiry, actor_user_id=world.buyer
    )
    world.db.commit()

    row = (
        world.db.query(OrderInquiryRow)
        .filter(
            OrderInquiryRow.so_line_id == line.id,
            OrderInquiryRow.verb == IV_ORDER_BACK,
        )
        .order_by(OrderInquiryRow.created_at.desc())
        .first()
    )
    assert row is not None, "the borrow-asker row was not written at all"
    assert row.supply_decision_id is not None, "board-origin, so S1's rule applies"
    assert row.ack_state == ACK_AWAITING
    assert row.acknowledged_by is None
    assert row.acknowledged_at is None


# ---------------------------------------------------------------------------
# AC-CF-4: the cascade never waited for confirm
# ---------------------------------------------------------------------------


def test_ac_cf_4_cascade_still_auto_links_an_awaiting_row_on_raise(api):
    """AC-CF-4. `ProjectSupplyService._draft_links_for_decision` always calls
    `auto_place_for_products(..., include_awaiting=True)` at raise time, so an open PO
    line still reaches a fresh row whatever its ack_state - and under S1 the row IS
    genuinely awaiting the instant it is raised, so this now holds for the real reason
    (the cascade never waits for confirm), not by pre-S1 coincidence."""
    _client, world = api
    po, _line = _open_po_line(world, qty=50)

    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]

    assert row.ack_state == ACK_AWAITING
    # S3 reversal: the cascade walk at raise time suggests here (no book match), it no
    # longer writes a real link - the row's own linkless state stands beside it.
    assert _links_of(world, row) == []
    assert [suggestion.document for suggestion in _suggested_of(world.db, row.id)] == [
        po.po_number
    ]


# ---------------------------------------------------------------------------
# AC-CF-8: row_ids OR filter, never both, never neither
# ---------------------------------------------------------------------------


def test_ac_cf_8a_acknowledge_by_row_ids_still_works(api):
    """AC-CF-8a. `POST /order-inquiries/acknowledge` with `row_ids` exists today and
    must keep working once `filter` lands beside it - the regression pin. Forced to
    `awaiting` first so the press is a genuine transition, not the born-ack no-op."""
    _client, world = api
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]
    row.ack_state = ACK_AWAITING
    row.acknowledged_by = None
    row.acknowledged_at = None
    world.db.commit()

    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"row_ids": [str(row.id)]})
    assert response.status_code == 200, response.text
    assert response.json()["acknowledged"] == 1
    world.db.commit()
    world.db.refresh(row)
    assert row.ack_state == ACK_ACKNOWLEDGED


def test_ac_cf_8b_acknowledge_by_filter_scopes_to_one_so(api):
    """AC-CF-8 / AC-CF-8b. `AcknowledgeRowsRequest` has no `filter` field today
    (`row_ids: List[str] = Field(..., min_length=1)`) - a body naming only `filter` is
    refused for a MISSING `row_ids`, a 422 for the whole unbuilt feature rather than the
    scoping this test is really about (which SO's rows a filter reaches, and that a
    cancelled or rejected row in the same SO is skipped).

    The reject rewrites the WHOLE order's revision (`reject_row` ->
    `_uncover_rejected_line` -> `ProjectSupplyService.uncover_lines`, a plain re-confirm
    naming no lines, so every OTHER covered line of the order is CARRIED): the eligible
    and to-cancel lines' rows this fixture first raised are cancelled and replaced by
    fresh ones the instant that press lands, so the ROW OBJECTS captured before it go
    stale - `eligible_row` in particular would sit at `awaiting` forever, never the row
    the filter-scoped press actually touches. The live row per line is re-read by
    `_order_row` AFTER the reject, and only then is one of them forced cancelled -
    forcing it BEFORE would just be undone by the same carry, since nothing but the row
    itself says the line is cancelled (`_live_handshake` skips it, so the carry raises a
    brand new `awaiting` row for that line exactly as it does for a line nobody touched).
    """
    client, world = api
    here = _raise_n_rows_one_so(api, ["4", "6", "3"])
    eligible_line, to_cancel_line, to_reject_line = here["lines"]
    to_reject_row = here["rows"][2]

    with _as_purchasing(world) as buyer:
        assert (
            buyer.post(
                f"{LIST}/{to_reject_row.id}/reject", json={"reason": "No stock"}
            ).status_code
            == 200
        )
    world.db.commit()

    eligible_row = _order_row(world, eligible_line)
    assert eligible_row.ack_state == ACK_AWAITING, "carried, still to confirm"
    to_cancel_row = _order_row(world, to_cancel_line)
    to_cancel_row.state = INQUIRY_CANCELLED
    world.db.commit()

    elsewhere = _raise_one_row(api, qty="9")

    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"filter": {"query": here["so_number"]}})

    assert response.status_code == 200, response.text
    world.db.refresh(eligible_row)
    assert eligible_row.ack_state == ACK_ACKNOWLEDGED
    world.db.refresh(to_cancel_row)
    assert to_cancel_row.ack_state != ACK_ACKNOWLEDGED, "a cancelled row is skipped"
    world.db.refresh(elsewhere["row"])
    assert elsewhere["row"].ack_state == ACK_AWAITING, "a different SO is untouched"


def test_ac_cf_8b2_skipped_counts_every_rejected_row_matched_not_a_hidden_cancelled_one(api):
    """AC-CF-8b (review round), corrected for #992 (one-header-per-SO). `skipped`
    (the contract's own field) was written by the server but asserted by no test: a
    caller had no way to know it actually reports the rejected count a filter matched
    rather than, say, 0 or the whole matched count. Isolated from
    `test_ac_cf_8b_acknowledge_by_filter_scopes_to_one_so` on purpose: that fixture's
    `reject` press cascades a whole-order re-confirm that cancels-and-carries every
    OTHER covered line too (its own docstring explains why), so its own `skipped`
    counts those superseded rows as well as the two this test means to isolate - not
    a clean "rejected + cancelled seeded" arithmetic. Here the rejected and cancelled
    states are forced directly on freshly raised rows, the same way AC-CF-11 forces
    `changed` - no endpoint cascade.

    #992's own `_base` change (S5/AC-OH-50..51, R2) hides a `cancelled` row from
    every filter that does not explicitly ask `state=cancelled` - "not owed, and not
    a row purchasing needs to see". `acknowledge_scope` is built off that SAME
    `_base` (its own docstring: "Select all N matching" must confirm exactly the
    scope the worklist itself is filtered to), so a query-by-SO-number press no
    longer MATCHES the cancelled row at all - it is not merely left alone, it was
    never counted as a candidate in the first place. The honest rule (captain's
    ruling, review round): `skipped` reports what the FILTER matched and the row's
    own state then refused, never a row the filter itself never surfaced to the
    buyer - the dialog's "Skipped N" and the toast must agree with what was on
    screen. So `skipped` here is 1 (the rejected row only), not 2."""
    _client, world = api
    here = _raise_n_rows_one_so(api, ["4", "6", "3"])
    eligible_row, to_reject_row, to_cancel_row = here["rows"]

    to_reject_row.ack_state = ACK_REJECTED
    to_reject_row.rejected_reason = "No stock"
    to_cancel_row.state = INQUIRY_CANCELLED
    world.db.commit()

    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"filter": {"query": here["so_number"]}})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["acknowledged"] == 1
    assert body["skipped"] == 1, body

    world.db.refresh(eligible_row)
    assert eligible_row.ack_state == ACK_ACKNOWLEDGED
    world.db.refresh(to_reject_row)
    assert to_reject_row.ack_state == ACK_REJECTED, "left alone, not acknowledged"
    world.db.refresh(to_cancel_row)
    assert to_cancel_row.ack_state != ACK_ACKNOWLEDGED, "left alone, not acknowledged"


def test_ac_cf_8c_row_ids_and_filter_are_mutually_exclusive(api):
    """AC-CF-8c. Both named at once, or neither, is 422. Today `filter` is an
    undeclared field the schema silently ignores (no `model_config`, pydantic v2
    default `extra="ignore"`), so `both` below actually SUCCEEDS on `row_ids` alone
    (200, not 422) - red for the missing mutual-exclusion guard, not a fixture bug.
    `neither` already 422s today, coincidentally, because `row_ids` alone is a required
    field (`min_length=1`); that half is asserted here too so the future error path (a
    bespoke 'name one' validation) is pinned by the same test once it exists."""
    _client, world = api
    fixture = _raise_one_row(api, qty="4")
    row = fixture["row"]

    with _as_purchasing(world) as buyer:
        both = buyer.post(
            ACK_URL,
            json={"row_ids": [str(row.id)], "filter": {"query": "anything"}},
        )
        neither = buyer.post(ACK_URL, json={})

    assert both.status_code == 422, both.text
    assert neither.status_code == 422, neither.text


def test_ac_cf_8d_filter_with_a_bad_uuid_is_422(api):
    """AC-CF-8d (review round). The list/summary/matrix routes all resolve their
    filters through `_worklist_filters`, which runs `validate_uuid_path` on
    `project_id`/`supplier_id`/`agent` before any of it reaches SQL. The acknowledge
    route's `filter` branch called `OrderInquiryWorklistService.acknowledge_scope`
    directly, bypassing that guard, so a malformed id reached Postgres as `invalid
    input syntax for type uuid` - a 500 carrying the statement - instead of a 422
    naming the bad field."""
    _client, world = api
    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"filter": {"project_id": "not-a-uuid"}})

    assert response.status_code == 422, response.text


def test_ac_cf_8e_filter_over_long_query_is_422(api):
    """AC-CF-8e (review round). `AcknowledgeFilter.query` carries no length cap unlike
    the list route's own `Query(..., max_length=_MAX_QUERY_LENGTH)` - a filter could
    send an unbounded string straight into an `ilike` across eleven joined columns."""
    _client, world = api
    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"filter": {"query": "x" * 201}})

    assert response.status_code == 422, response.text


def test_ac_cf_8f_filter_unknown_key_is_422(api):
    """AC-CF-8f (review round). `AcknowledgeFilter` carried no `model_config`, so
    pydantic v2's default `extra="ignore"` silently dropped a misspelled or unknown
    filter key rather than refusing it - a caller who mistyped a key got every row in
    scope confirmed, unfiltered, with no error at all."""
    _client, world = api
    with _as_purchasing(world) as buyer:
        response = buyer.post(
            ACK_URL, json={"filter": {"not_a_real_filter_key": "x"}}
        )

    assert response.status_code == 422, response.text


def test_ac_cf_8g_empty_filter_confirms_only_this_companys_rows(api):
    """AC-CF-8g (ruling, review round: `filter: {}` stays ALLOWED - it is Select all N
    on an unfiltered list, the owner's own explicit press). It must never reach past
    `CompanyScopedMixin`'s scope: a row seeded directly under another company is
    invisible to `_base()` the same way it is to the list route, so an empty filter
    leaves it exactly alone - the `filter` door's own version of
    `test_order_inquiry_handshake_edges.py::test_row_ids_naming_another_companys_row_are_refused_not_skipped`."""
    _client, world = api
    mine = _raise_one_row(api, qty="6")
    row = mine["row"]

    other_company_id = _uid()
    world.db.execute(
        text("INSERT INTO companies (id, name, code) VALUES (:i, :n, :c)"),
        {
            "i": other_company_id,
            "n": f"{MARKER} Other Co",
            "c": f"ZZT{uuid.uuid4().hex[:6]}",
        },
    )
    foreign_row_id = _uid()
    world.db.execute(
        text(
            "INSERT INTO projects.order_inquiry_rows (id, company_id, order_inquiry_id, "
            "so_line_id, qty, verb, ack_state, created_at) VALUES "
            "(:i, :c, :inq, :l, :q, :v, 'awaiting', now())"
        ),
        {
            "i": foreign_row_id,
            "c": other_company_id,
            "inq": str(row.order_inquiry_id),
            "l": str(row.so_line_id),
            "q": Decimal("5"),
            "v": IV_ORDER,
        },
    )
    world.db.commit()

    with _as_purchasing(world) as buyer:
        response = buyer.post(ACK_URL, json={"filter": {}})

    assert response.status_code == 200, response.text
    world.db.refresh(row)
    assert row.ack_state == ACK_ACKNOWLEDGED, "the one row this company owns is confirmed"

    foreign_state = world.db.execute(
        text("SELECT ack_state FROM projects.order_inquiry_rows WHERE id = :i"),
        {"i": foreign_row_id},
    ).scalar()
    assert foreign_state == "awaiting", "a foreign-company row is invisible, never confirmed"


def test_ac_cf_8h_filter_axis_key_must_be_a_uuid(api):
    """AC-CF-8h (re-review). `axis_key` compares against a UUID column on every axis
    the same way `project_id`/`supplier_id`/`agent` do, but was the one id field on
    `AcknowledgeFilter` left without `Field(pattern=UUID_PATTERN)` - a malformed value
    reached the column compare and 500'd instead of 422ing, unlike the list route's own
    `axis_key` query param (`order_inquiries.py:167-172`, `pattern=UUID_PATTERN`)."""
    _client, world = api
    with _as_purchasing(world) as buyer:
        response = buyer.post(
            ACK_URL, json={"filter": {"axis": "product", "axis_key": "oops"}}
        )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# AC-CF-9: CS is still refused
# ---------------------------------------------------------------------------


def test_ac_cf_9_cs_principal_refused_403_on_acknowledge(api):
    """AC-CF-9. `require_permission(ACKNOWLEDGE)` already gates the route - a
    regression pin, not a red test."""
    cs_client, world = api
    fixture = _raise_one_row(api, qty="5")

    response = cs_client.post(ACK_URL, json={"row_ids": [str(fixture["row"].id)]})

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# AC-CF-11: the default view (FE-side, per the plan's own measured facts)
# ---------------------------------------------------------------------------


def test_ac_cf_11_no_ack_param_lists_everything_to_confirm_narrows(api):
    """AC-CF-11. The backend contract is UNCHANGED by this lane:
    `order_inquiry_worklist_service.py`'s `if ack:` gate means an absent `ack` param has
    always meant no filter, and S3 makes the FE send `ack=to_confirm` when the URL is
    silent, per the plan's own measured facts ("FE: absent `?ack` = no filter"). Pinned
    here as a regression guard on the mechanism S3 depends on, not a red test: `awaiting`
    and `changed` are forced directly since a fresh raise cannot naturally produce
    either pre-S1."""
    client, world = api
    awaiting = _raise_one_row(api, qty="1")
    changed = _raise_one_row(api, qty="1")
    acknowledged = _raise_one_row(api, qty="1")

    # `awaiting` needs no forcing under S1 - a fresh raise is born awaiting already.
    # `changed` still has to be forced directly: nothing short of a real settle-in-place
    # produces `changed`, and that is a different seam from this test's subject.
    # `acknowledged` has to be taken on for REAL, through the route - under S1 a raise
    # alone leaves it `awaiting` exactly like the other two, so without this press it is
    # indistinguishable from `awaiting` and the whole point of the third fixture is lost.
    changed["row"].ack_state = ACK_CHANGED
    changed["row"].changed_at = datetime.utcnow()
    world.db.commit()
    with _as_purchasing(world) as buyer:
        assert (
            buyer.post(
                ACK_URL, json={"row_ids": [str(acknowledged["row"].id)]}
            ).status_code
            == 200
        )
    world.db.commit()

    every = client.get(LIST, params={"limit": 200}).json()
    every_ids = {row["id"] for row in every["data"]}
    for fixture in (awaiting, changed, acknowledged):
        assert str(fixture["row"].id) in every_ids, "no ?ack param filters nothing"

    to_confirm = client.get(LIST, params={"ack": "to_confirm", "limit": 200}).json()
    to_confirm_ids = {row["id"] for row in to_confirm["data"]}
    assert str(awaiting["row"].id) in to_confirm_ids
    assert str(changed["row"].id) in to_confirm_ids
    assert str(acknowledged["row"].id) not in to_confirm_ids


# ---------------------------------------------------------------------------
# AC-CF-12: the tile's own count
# ---------------------------------------------------------------------------


def test_ac_cf_12_summary_to_confirm_counts_a_freshly_raised_row(api):
    """AC-CF-12. Once S1 ships, a fresh raise is born `awaiting`, so it counts in
    `summary.ack.to_confirm` (`_acks`, `order_inquiry_worklist_service.py`, which
    already sums `awaiting + changed` correctly) the instant CS confirms. Today the
    raise is still born `acknowledged` (G4), so the row is ABSENT from `to_confirm` -
    red for that reason, not a summary-arithmetic bug."""
    client, world = api
    before = client.get(
        f"{LIST}/summary", params={"project_id": str(world.project.id)}
    ).json()["ack"]["to_confirm"]

    _raise_one_row(api, qty="7")

    after = client.get(
        f"{LIST}/summary", params={"project_id": str(world.project.id)}
    ).json()["ack"]["to_confirm"]

    assert after == before + 1


# ---------------------------------------------------------------------------
# AC-CF-14: planning reads confirmed demand only
# ---------------------------------------------------------------------------


def test_ac_cf_14_demand_excludes_awaiting_includes_confirmed_and_changed(api):
    """AC-CF-14. `demand.horizon_committed_select_sql()`'s `PLANNED_ACK_STATES =
    ("acknowledged", "changed")` already excludes anything else - the SQL predicate is
    right today. What is wrong is the BORN state: a fresh raise is `acknowledged`
    already (G4), so it is already inside the plan's demand the instant CS confirms,
    the opposite of what this test pins. Red on the first assertion."""
    _client, world = api
    fixture = _raise_one_row(api, qty="10")
    row = fixture["row"]

    assert _project_committed(world, planned=True) == Decimal("0"), (
        "a freshly raised row must not be planning demand until purchasing confirms it"
    )

    with _as_purchasing(world) as buyer:
        assert buyer.post(ACK_URL, json={"row_ids": [str(row.id)]}).status_code == 200
    world.db.commit()
    assert _project_committed(world, planned=True) == Decimal("10")

    _settle(world, fixture, qty="15")
    assert _project_committed(world, planned=True) == Decimal("15"), (
        "a changed row stays counted"
    )


# ---------------------------------------------------------------------------
# AC-CF-16: the deploy migration (R1)
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "oicf_0001_board_rows_to_confirm.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("zzt_oicf_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(db):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _migration_module()
    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        module.upgrade()
    return module


def _run_downgrade(db, module) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(connection=db.connection())
    with Operations.context(context):
        module.downgrade()


def test_ac_cf_16_migration_flips_open_board_rows_only(api):
    """AC-CF-16 (R1). `alembic/versions/oicf_0001_board_rows_to_confirm.py` does not
    exist yet, so `_migration_module()` fails inside `exec_module` (the file cannot be
    opened) - the right red for a slice not yet built. Once it exists: a board row
    (`supply_decision_id` set, `acknowledged`, still open) flips to `awaiting` with its
    ack stamps cleared; a sheet row (`supply_decision_id IS NULL`), a cancelled board
    row and an actioned board row are all left exactly as they are; `downgrade()`
    restores the flipped row (system-attributed, the same convention
    `454_order_inquiry_born_ack.py` uses for its own backfill)."""
    _client, world = api

    # Every fixture below is stamped ACKNOWLEDGED directly, simulating deploy day: under
    # G4 (this migration's whole reason to exist) EVERY pre-existing row already reads
    # `acknowledged`, whatever raised it. A fresh raise under S1 is born `awaiting`
    # instead, so without this stamp none of these four would meet the migration's own
    # `WHERE ack_state = 'acknowledged'` and the test would pass without the UPDATE ever
    # touching a row - proving nothing about the migration at all.
    def _stamp_acknowledged(row):
        row.ack_state = ACK_ACKNOWLEDGED
        row.acknowledged_by = world.buyer
        row.acknowledged_at = datetime.utcnow()

    board = _raise_one_row(api, qty="4")["row"]
    assert board.supply_decision_id is not None, "raised from the board, so it has one"
    _stamp_acknowledged(board)

    sheet = _raise_one_row(api, qty="6")["row"]
    sheet.supply_decision_id = None  # what the Excel importer's own row looks like
    _stamp_acknowledged(sheet)

    cancelled = _raise_one_row(api, qty="3")["row"]
    cancelled.state = INQUIRY_CANCELLED
    _stamp_acknowledged(cancelled)

    actioned = _raise_one_row(api, qty="5")["row"]
    actioned.state = INQUIRY_ACTIONED
    _stamp_acknowledged(actioned)

    world.db.commit()
    stamped_sheet_by = sheet.acknowledged_by
    stamped_sheet_at = sheet.acknowledged_at

    module = _run_upgrade(world.db)

    world.db.expire_all()
    world.db.refresh(board)
    world.db.refresh(sheet)
    world.db.refresh(cancelled)
    world.db.refresh(actioned)

    assert board.ack_state == ACK_AWAITING
    assert board.acknowledged_by is None
    assert board.acknowledged_at is None

    assert sheet.ack_state == ACK_ACKNOWLEDGED
    assert sheet.acknowledged_by == stamped_sheet_by
    assert sheet.acknowledged_at == stamped_sheet_at

    assert cancelled.ack_state == ACK_ACKNOWLEDGED
    assert actioned.ack_state == ACK_ACKNOWLEDGED

    _run_downgrade(world.db, module)

    world.db.expire_all()
    world.db.refresh(board)
    assert board.ack_state == ACK_ACKNOWLEDGED
    assert board.acknowledged_at is not None
