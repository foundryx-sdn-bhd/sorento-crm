"""Ladder v7.1 - three gaps the fix-pass suite left unwalked (tester pass, S3).

Three cases, three files' worth of behaviour:

- `free_piles_at` (`app/services/scm/supply_assignment.py`) nets a PINNED hold out of the
  pile it offers, even when the hold sits at ANOTHER group's bin - the offer is what is
  left once somebody has already been promised part of it, never the whole pile as it
  stood before anybody was.
- `other_group_left` (`app/services/project_supply_service.py::compose_lines`), the ledger
  R40's offer half needs since the walk itself stopped drawing another group's pile: two
  units of ONE board reaching for the same free pile share it rather than each being
  offered the whole of it.
- `_reissue_without_line` (`app/services/project_supply_service.py`) copies a surviving
  line's hold row for row onto the re-issued revision, and `claim_id` is one of the fields
  copied - a cross-project claim behind a hold does not lapse because a DIFFERENT line of
  the same decision was borrowed away underneath it.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.models.project_so import (
    DECISION_ACTIVE,
    AllocationClaim,
    CLAIM_ACCEPTED,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.supply_assignment import (
    DemandLine,
    Hold,
    SupplyEvent,
    assign,
    free_piles_at,
)

from .._pg_fixture import blank_session
from ..test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _core_line,
    _core_so,
    _product,
    _project_line,
    _project_so,
    _sorento,
    _stock,
    _uid,
    _user,
    _warehouse,
)
from .test_ladder_v7_borrow import LEAD_DAYS, WINDOW_DAY, _components, _decide, _policy
from .test_project_supply_service_ladder import _group_sites, _lead_time, _seed_line, _world

AS_OF = date(2026, 9, 1)
TBA = date(2029, 1, 1)


# --------------------------------------------------------------------------- gap (a)


def test_free_piles_at_nets_out_a_pinned_hold_at_another_groups_bin():
    """`free_piles_at` reads `assignment.lines[].assigned`, and a PINNED item is spent
    before the walk starts (`Assigned.at` is `None` on a pin - it is free at no date at
    all, per the dataclass's own comment). So a hold at IB's bin has to net out of what
    the offer states for IB, whatever date the caller asks about - not only once the walk
    has chronologically reached the holder's own required date.

    Own group BB has 10 on hand (not enough); IB holds 100, of which 40 is already PINNED
    to another line (`ib_holder`, due later than the date this test reads the pile at) -
    a confirmed decision, the only way stock is ever "held by" a line under R40/R21. The
    offer at the BB asker's own date must show IB's REMAINING 60, not its whole 100.
    """
    result = assign(
        "zzt-p",
        as_of=AS_OF,
        tba_from=TBA,
        lead_days=90,
        supply=[
            SupplyEvent(key="on_hand:BRW-BB", kind="on_hand", warehouse="BRW-BB", at=AS_OF, qty=10),
            SupplyEvent(key="on_hand:BRW-IB", kind="on_hand", warehouse="BRW-IB", at=AS_OF, qty=100),
        ],
        demand=[
            DemandLine(
                key="bb_asker", so_number="SObb_asker", line_no=1, warehouse="BRW-BB",
                agent_code="JAY", required_date=date(2026, 9, 20), open_qty=40,
            ),
            DemandLine(
                key="ib_holder", so_number="SOib_holder", line_no=1, warehouse="BRW-IB",
                agent_code="CONTACT O", required_date=date(2026, 9, 25), open_qty=40,
            ),
        ],
        pinned=[Hold(line_key="ib_holder", supply_key="on_hand:BRW-IB", qty=40, warehouse="BRW-IB")],
    )
    # Read WELL BEFORE ib_holder's own required date - the pin nets out immediately, it
    # does not wait for the walk to reach the line it is pinned to.
    piles = free_piles_at(result, at=date(2026, 9, 20), as_of=AS_OF)
    assert [(event.warehouse, qty) for event, qty in piles.get("IB", [])] == [
        ("BRW-IB", 60.0),
    ], "IB's pile must read 100 less the 40 already pinned, not the whole 100"
    assert "BB" not in piles, "BB's own 10 was drawn by the asker itself"


# --------------------------------------------------------------------------- gap (b)


def test_two_units_of_one_board_share_the_same_other_groups_pile_not_duplicate_it():
    """`other_group_left` (`compose_lines`), R40's offer-half ledger.

    The base ASSIGNMENT never draws another group's free pile down (R40: nobody has
    decided anything, so the walk decides nothing either) - which is exactly why two
    units of one board that both reach for it would each be offered the SAME free stock
    without a ledger of the walk's own. One order, two units (different required dates,
    ladder v6), both standing on an own bin that holds nothing; a donor group (IR) holds
    100 free with no demand of its own. The first unit walked takes 60 of it whole
    (Reserve); the second unit sees only 40 left, which is not the whole of its own 60, so
    step 1 gives it nothing (R10/R33) and it buys.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, donor, on_hand=100)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-TWOUNITS{_uid()[:4]}"
        db.flush()
        first_core = _core_line(
            db, core_so, product, own, qty_ordered="60",
            required_date=date.today() + timedelta(days=30),
        )
        second_core = _core_line(
            db, core_so, product, own, qty_ordered="60",
            required_date=date.today() + timedelta(days=31),
        )
        asker = _project_so(db, project)
        _project_line(db, asker, line_no=1, product=product, core_line=first_core)
        _project_line(db, asker, line_no=2, product=product, core_line=second_core)
        db.commit()

        lines = ProjectSupplyService(db).proposal_for(asker)["lines"]
        stated = [
            [(c["kind"], c["qty"], c.get("source_location")) for c in line["components"]]
            for line in lines
        ]
        donor_code = donor.warehouse_code

    assert stated[0] == [("reserve", "60", donor_code)], stated
    assert [(kind, qty) for kind, qty, _loc in stated[1]] == [("buy", "60")], (
        "40 is left of IR's pile once the first unit's 60 is drawn down, which is not "
        f"the whole of the second unit's own 60 (R10/R33): {stated}"
    )


# --------------------------------------------------------------------------- gap (c)


def test_reissue_without_line_carries_a_survivors_claim_id_onto_the_new_revision():
    """`_reissue_without_line` copies a surviving line's hold row for row, and `claim_id`
    is one of the columns copied (`app/services/project_supply_service.py`, the
    `SOLineAllocation(... claim_id=row.claim_id, ...)` inside it).

    A donor order with two lines, both decided; a cross-project `AllocationClaim` already
    stands behind whichever line SURVIVES the borrow (fabricated directly - an S3
    order-borrow decision never writes one of these itself; the point is only that a field
    the code never wrote must not be the field it silently drops). An asker then borrows
    the OTHER line, which supersedes the donor's revision and re-issues it minus that line
    (R25, AC-S3-5's multi-line fix). The survivor's re-issued row must still carry the
    same `claim_id`.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        _stock(db, product, donor_bin, on_hand=20)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        donor_core = _core_so(db, company_id)
        donor_core.so_number = f"ZZTSO-CLAIM{_uid()[:4]}"
        db.flush()
        donor_order = _project_so(db, project)
        donor_lines = []
        donor_core_line_ids = {}
        for line_no in (1, 2):
            core_line = _core_line(
                db, donor_core, product, donor_bin, qty_ordered="10",
                required_date=date.today() + timedelta(days=WINDOW_DAY + 20 + line_no),
            )
            line = _project_line(
                db, donor_order, line_no=line_no, product=product, core_line=core_line,
            )
            donor_lines.append(line)
            donor_core_line_ids[str(line.id)] = str(core_line.id)
        db.commit()
        for line in donor_lines:
            _decide(db, donor_order, line, donor_bin, "10", eling)

        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        component = _components(ProjectSupplyService(db).proposal_for(asker))[0]
        assert component["rung"] == "order_borrow", component
        borrowed_core_line_id = component["donor_core_line_id"]
        survivor_line = next(
            line
            for line in donor_lines
            if donor_core_line_ids[str(line.id)] != borrowed_core_line_id
        )

        active = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == donor_order.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .one()
        )
        survivor_row = (
            db.query(SOLineAllocation)
            .filter(
                SOLineAllocation.decision_id == active.id,
                SOLineAllocation.so_line_id == survivor_line.id,
            )
            .one()
        )
        claim = AllocationClaim(
            id=_uid(),
            company_id=company_id,
            from_project_id=project.id,
            to_project_id=project.id,
            so_line_id=survivor_line.id,
            product_id=product.id,
            warehouse_id=donor_bin.id,
            qty=Decimal("10"),
            state=CLAIM_ACCEPTED,
            reason="zzt fabricated claim behind the survivor's hold",
        )
        db.add(claim)
        db.flush()
        claim_id = claim.id
        survivor_row.claim_id = claim_id
        db.commit()

        ProjectSupplyService(db).confirm(
            asker,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=asker_line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(donor_bin.id),
                                "qty": component["qty"],
                                "reason": "Ladder v7.1 step 2.",
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                                "donor_line_no": component["donor_line_no"],
                                "donor_required_date": component["donor_required_date"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()

        fresh = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == donor_order.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .one()
        )
        reissued_row = (
            db.query(SOLineAllocation)
            .filter(
                SOLineAllocation.decision_id == fresh.id,
                SOLineAllocation.so_line_id == survivor_line.id,
            )
            .one()
        )
        fresh_id, active_id = fresh.id, active.id

    assert fresh_id != active_id, "the donor's revision is re-issued, not reused"
    assert reissued_row.claim_id == claim_id, (
        "the claim behind the survivor's hold must travel with it onto the re-issued "
        "revision, not be dropped because a different line of the same decision moved"
    )
