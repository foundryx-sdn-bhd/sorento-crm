"""S1 - monthly OI number, fixed raised date, raise history
(`PLAN-oi-header-list-detail.md`, `oi-header-list-detail-acceptance-criteria.md`).

TEST-FIRST (Phase 2): written against the UAC + the plan's own "Contract"/"Design"
sections, with NO implementation to look at. Every test below must fail today for a real
reason - a missing function/attribute/table, a signature mismatch, or an assertion on
behaviour the plan says does not exist yet - never an import typo or a fixture bug.

Names driven, per the plan and the captain's brief:

* ``app.models.project_so.next_inquiry_no(bind, company_id, ref_date)`` - TODAY takes only
  ``(bind, company_id)`` and mints a 6-digit ``OI-NNNNNN`` with no month in it
  (measured fact, plan "Measured facts"). Calling it with the new 3-arg shape raises
  ``TypeError`` until the signature changes - that IS the red.
* ``INQUIRY_NO_DIGITS`` - today ``6``, the plan wants ``4``.
* ``app.models.project_so.OrderInquiryRaise`` - does not exist yet; imported lazily
  inside a helper so each test that needs it fails on its own ``ImportError`` rather than
  killing collection for the whole file.
* ``OrderInquiry.legacy_inquiry_no`` - not a mapped column yet; reading it raises
  ``AttributeError``.
* ``alembic/versions/523_oi_monthly_no_raises.py`` exposing module-level
  ``backfill_raises(bind)`` / ``renumber_inquiries(bind)`` - the file does not exist yet.

Postgres only, via ``tests/_pg_fixture.py``'s ``blank_session`` - an EMPTY scratch schema
(translated copy of the real DDL), never the shared prod-copy database: several of these
tests seed legacy rows across two months/companies and count every header in view, which
is only safe to do against a schema this test itself created. Every FK is seeded here
(``unique_code``-prefixed via ``_uid``/``MARKER``), never borrowed from an existing row.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.models.project_so import (
    INQUIRY_NO_DIGITS,
    INQUIRY_RAISED,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    next_inquiry_no,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService

from ._pg_fixture import blank_session

MARKER = "zzt-oi-monthno"
MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _oi_prefix_now() -> str:
    """`OI-YYMM-` for today's Malaysia date - the month a header minted now carries."""
    return datetime.now(timezone.utc).astimezone(MY_TZ).strftime("OI-%y%m-")


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "523_oi_monthly_no_raises.py"
)


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    from app.models.user import User

    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _pso(db, company_id: str) -> ProjectSalesOrder:
    """The lightest FK target ``OrderInquiry.project_sales_order_id`` needs - no core
    sales order, no project, both nullable (`app/models/project_so.py:458,470`)."""
    order = ProjectSalesOrder(
        id=_uid(),
        company_id=company_id,
        provisional_ref=f"{MARKER}-PSO-{_uid()[:8]}",
        status="draft",
    )
    db.add(order)
    db.flush()
    return order


def _header(
    db,
    company_id: str,
    *,
    raised_at: datetime | None = None,
    raised_by: str | None = None,
    inquiry_no: str | None = None,
) -> OrderInquiry:
    """One order inquiry header hung off its own fresh `ProjectSalesOrder` (each PSO may
    hold only ONE non-amendment header, `uq_project_order_inquiry_per_sales_order`)."""
    pso = _pso(db, company_id)
    kwargs = dict(
        id=_uid(),
        company_id=company_id,
        project_sales_order_id=pso.id,
        state=INQUIRY_RAISED,
        raised_by=raised_by,
    )
    if raised_at is not None:
        kwargs["raised_at"] = raised_at
    if inquiry_no is not None:
        kwargs["inquiry_no"] = inquiry_no
    header = OrderInquiry(**kwargs)
    db.add(header)
    db.flush()
    return header


def _row(db, header: OrderInquiry, *, created_at: datetime | None = None) -> OrderInquiryRow:
    from app.models.project_so import IV_ORDER

    kwargs = dict(
        id=_uid(),
        company_id=header.company_id,
        order_inquiry_id=header.id,
        qty=Decimal("10"),
        verb=IV_ORDER,
        state=INQUIRY_RAISED,
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    row = OrderInquiryRow(**kwargs)
    db.add(row)
    db.flush()
    return row


def _raise_rows(db, order_inquiry_id: str) -> list:
    """The raise-history table this slice adds - not on the model yet, so any test
    reaching this raises ``ImportError`` (S1 is not implemented)."""
    from app.models.project_so import OrderInquiryRaise  # noqa: PLC0415

    return (
        db.query(OrderInquiryRaise)
        .filter(OrderInquiryRaise.order_inquiry_id == order_inquiry_id)
        .order_by(OrderInquiryRaise.raised_at.asc())
        .all()
    )


def _migration_module():
    assert _MIGRATION_PATH.exists(), f"migration not found at {_MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location(
        "zzt_523_oi_monthly_no_raises", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# AC-NO-01/02/03/04 - the monthly number
# =============================================================================


class TestMonthlyNumber:
    def test_september_header_gets_oi_2609_four_digits_first_of_month_AC_NO_01(self):
        """`OI-2609-0001`, four digits, the first of the month - `next_inquiry_no`'s new
        3-arg shape. Today's function takes only `(bind, company_id)`, so this raises
        `TypeError` before the assertions below ever run."""
        with blank_session() as db:
            company_id = _sorento(db)
            no = next_inquiry_no(db, company_id, date(2026, 9, 5))
            assert no == "OI-2609-0001"
            assert INQUIRY_NO_DIGITS == 4

    def test_a_second_september_header_takes_the_next_free_number_AC_NO_01(self):
        with blank_session() as db:
            company_id = _sorento(db)
            first = next_inquiry_no(db, company_id, date(2026, 9, 5))
            # Simulate the first number having actually been issued.
            _header(db, company_id, inquiry_no=first, raised_at=datetime(2026, 9, 5))
            db.flush()
            second = next_inquiry_no(db, company_id, date(2026, 9, 20))
            assert second == "OI-2609-0002"

    def test_october_header_starts_its_own_series_while_september_numbers_exist_AC_NO_01(
        self,
    ):
        with blank_session() as db:
            company_id = _sorento(db)
            for _ in range(3):
                no = next_inquiry_no(db, company_id, date(2026, 9, 10))
                _header(db, company_id, inquiry_no=no, raised_at=datetime(2026, 9, 10))
                db.flush()
            october_no = next_inquiry_no(db, company_id, date(2026, 10, 1))
            assert october_no == "OI-2610-0001", (
                "October starts its own -0001 even though September already holds 3"
            )

    def test_myt_edge_a_utc_instant_after_midnight_myt_numbers_under_the_next_month_AC_NO_01(
        self,
    ):
        """A UTC instant of 2026-09-30 17:30 is 2026-10-01 01:30 in Asia/Kuala_Lumpur -
        the header must number under OI-2610-, not OI-2609-. Exercises the real insert
        path (the `before_insert` listener), not `next_inquiry_no` directly, because the
        MYT conversion is the listener's own job before it calls the minting function."""
        with blank_session() as db:
            company_id = _sorento(db)
            # A September header already exists, so a listener that forgot to convert
            # to MYT and numbered off UTC (still 30 Sep at 17:30) would collide with
            # this slot, not merely disagree on the prefix.
            _header(db, company_id, raised_at=datetime(2026, 9, 30, 10, 0))
            db.flush()

            utc_instant = datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc)
            myt_date = utc_instant.astimezone(MY_TZ).date()
            assert myt_date == date(2026, 10, 1), "sanity check on the fixture's own premise"

            # Every datetime on this wire is naive UTC (module convention elsewhere in
            # this codebase, e.g. OrderInquiryDocumentAllocation.linked_at) - so the
            # column is stamped with the naive UTC wall-clock value.
            header = _header(db, company_id, raised_at=utc_instant.replace(tzinfo=None))
            db.commit()
            db.refresh(header)
            assert header.inquiry_no.startswith("OI-2610-"), header.inquiry_no

    def test_two_headers_in_one_flush_take_consecutive_numbers_AC_NO_02(self):
        """Two headers raised in the SAME transaction, before either is committed - each
        its own `db.add` + `db.flush()`, which is how `ensure_inquiry` itself writes
        (`self.db.add(inquiry); self.db.flush()`) and therefore how two confirmations
        inside one request would actually reach this listener. A single literal
        `db.flush()` batching both INSERTs together is a different (and NOT how
        `ensure_inquiry` writes) SQLAlchemy scenario: the ORM finalises every pending
        object's `before_insert` before issuing either row's INSERT, so neither listener
        can see the other's number yet and it is not what this AC is pinning."""
        with blank_session() as db:
            company_id = _sorento(db)
            header_1 = _header(db, company_id, raised_at=datetime(2026, 9, 12))
            header_2 = _header(db, company_id, raised_at=datetime(2026, 9, 12))

            assert header_1.inquiry_no.startswith("OI-2609-"), header_1.inquiry_no
            assert header_2.inquiry_no.startswith("OI-2609-"), header_2.inquiry_no
            tail_1 = int(header_1.inquiry_no.rsplit("-", 1)[-1])
            tail_2 = int(header_2.inquiry_no.rsplit("-", 1)[-1])
            assert abs(tail_2 - tail_1) == 1, "consecutive numbers off the same transaction"

    def test_a_preset_number_is_never_reminted_and_a_later_reconfirm_keeps_it_AC_NO_03(
        self,
    ):
        """A header whose number is already set is left alone by the insert listener
        (true today); the interesting half is that a reconfirm in a LATER month must
        still not touch it. The dated mint (AC-NO-01) is this AC's own precondition -
        this fails there first until that lands, which is the correct order of failure:
        AC-NO-03 cannot mean anything before AC-NO-01 is real."""
        with blank_session() as db:
            company_id = _sorento(db)
            pso = _pso(db, company_id)
            actor_1 = _user(db, f"{MARKER} Eling")
            actor_2 = _user(db, f"{MARKER} Joey")
            db.commit()

            service = ProjectOrderInquiryService(db)
            # `ensure_inquiry` mints off the clock (Malaysia day), so the expected month is
            # read from the clock too - either side of the call, should it straddle midnight.
            # Hard-coding "OI-2609-" went red on 1 Oct 2026 (TEST-DATEBOMB-1001).
            before = _oi_prefix_now()
            header = service.ensure_inquiry(pso, actor_user_id=actor_1)
            db.commit()
            minted = header.inquiry_no
            assert minted.startswith(
                (before, _oi_prefix_now())
            ), f"expected the dated format, got {minted!r}"

            # A reconfirm the following month: the header now reads as raised last month.
            header.raised_at = header.raised_at.replace(day=1) - timedelta(days=5)
            db.commit()
            reused = service.ensure_inquiry(pso, actor_user_id=actor_2)
            db.commit()
            assert reused.id == header.id
            assert reused.inquiry_no == minted, "never re-minted on reconfirm"

    def test_a_preset_number_survives_a_reconfirm_without_burning_an_october_slot_AC_NO_03(
        self,
    ):
        """Makes the reconfirm guard bite on its own, independent of AC-NO-01: a header
        already carrying a dated September number, reconfirmed later (in October), must
        neither change that number NOR silently reserve an October slot for the
        non-event - the next REAL new October header must still open at -0001, not
        -0002."""
        with blank_session() as db:
            company_id = _sorento(db)
            preset_header = _header(
                db,
                company_id,
                inquiry_no="OI-2609-0007",
                raised_at=datetime(2026, 9, 5),
            )
            actor = _user(db, f"{MARKER} Joey")
            db.commit()

            pso = (
                db.query(ProjectSalesOrder)
                .filter(ProjectSalesOrder.id == preset_header.project_sales_order_id)
                .one()
            )
            reused = ProjectOrderInquiryService(db).ensure_inquiry(pso, actor_user_id=actor)
            db.commit()
            assert reused.id == preset_header.id
            assert reused.inquiry_no == "OI-2609-0007", (
                "a reconfirm, even narratively in October, must not touch the number"
            )

            # The reconfirm runs on the clock, so a burned slot would be in THIS month: the
            # fresh header is raised now too, or the guard only bit while today was October.
            prefix = _oi_prefix_now()
            fresh_header = _header(
                db, company_id, raised_at=datetime.now(timezone.utc).replace(tzinfo=None)
            )
            db.commit()
            assert fresh_header.inquiry_no in (
                prefix + "0001", _oi_prefix_now() + "0001"
            ), (
                "the reconfirm above must not have consumed this month's slot - a burned "
                "slot would leave this at -0002"
            )

    def test_renumber_migration_gives_gapless_per_company_per_month_numbers_AC_NO_04(
        self,
    ):
        with blank_session() as db:
            company_id = _sorento(db)
            # Legacy numbers, two months, one company (the fixture's own scratch schema
            # holds no other headers to collide with) - assertions scope to these ids only.
            september_ids = []
            for i, legacy in enumerate(["OI-000010", "OI-000011", "OI-000012"]):
                header = _header(
                    db,
                    company_id,
                    inquiry_no=legacy,
                    raised_at=datetime(2026, 9, 1) + timedelta(days=i),
                )
                september_ids.append(header.id)
            october_ids = []
            for i, legacy in enumerate(["OI-000020", "OI-000021"]):
                header = _header(
                    db,
                    company_id,
                    inquiry_no=legacy,
                    raised_at=datetime(2026, 10, 1) + timedelta(days=i),
                )
                october_ids.append(header.id)
            db.commit()

            module = _migration_module()
            module.renumber_inquiries(db.connection())
            db.commit()
            db.expire_all()

            september_headers = (
                db.query(OrderInquiry)
                .filter(OrderInquiry.id.in_(september_ids))
                .order_by(OrderInquiry.raised_at.asc())
                .all()
            )
            october_headers = (
                db.query(OrderInquiry)
                .filter(OrderInquiry.id.in_(october_ids))
                .order_by(OrderInquiry.raised_at.asc())
                .all()
            )

            assert [h.inquiry_no for h in september_headers] == [
                "OI-2609-0001",
                "OI-2609-0002",
                "OI-2609-0003",
            ]
            assert [h.inquiry_no for h in october_headers] == [
                "OI-2610-0001",
                "OI-2610-0002",
            ]
            assert [h.legacy_inquiry_no for h in september_headers] == [
                "OI-000010",
                "OI-000011",
                "OI-000012",
            ]
            every_new_no = [h.inquiry_no for h in september_headers + october_headers]
            assert len(every_new_no) == len(set(every_new_no)), "no duplicates"


# =============================================================================
# AC-RD-01/02/03 - fixed raised date + raise history
# =============================================================================


class TestRaiseHistory:
    def test_ensure_inquiry_writes_raised_then_reconfirmed_rows_AC_RD_01(self):
        with blank_session() as db:
            company_id = _sorento(db)
            pso = _pso(db, company_id)
            actor_1 = _user(db, f"{MARKER} Eling")
            actor_2 = _user(db, f"{MARKER} Joey")
            db.commit()

            service = ProjectOrderInquiryService(db)
            header = service.ensure_inquiry(pso, actor_user_id=actor_1)
            db.commit()
            first_raised_at = header.raised_at

            rows = _raise_rows(db, header.id)
            assert len(rows) == 1
            assert rows[0].kind == "raised"
            assert rows[0].raised_by == actor_1

            reused = service.ensure_inquiry(pso, actor_user_id=actor_2)
            db.commit()
            assert reused.id == header.id
            assert reused.raised_at == first_raised_at, "fixed for life (R5)"
            assert reused.raised_by == actor_1, "never re-attributed to the reconfirmer"

            rows = _raise_rows(db, header.id)
            assert len(rows) == 2, "one row added by the reconfirm, not zero"
            assert rows[1].kind == "reconfirmed"
            assert rows[1].raised_by == actor_2

    def test_two_ensure_inquiry_calls_inside_one_uncommitted_pass_add_one_row_AC_RD_01(
        self,
    ):
        """Best-effort reading of "two writes inside one confirmation add one row, not
        two": two callers reach `ensure_inquiry` for the SAME order without a commit
        boundary between them (`refresh_for_decision`'s own branch and
        `project_supply_service`'s step-3 borrow fallback can both run inside one HTTP
        confirm). Whatever the real guard turns out to be, it must not double-count
        inside one uncommitted unit of work - report to the captain if the real seam is a
        different boundary than "no intervening commit"."""
        with blank_session() as db:
            company_id = _sorento(db)
            pso = _pso(db, company_id)
            actor = _user(db, f"{MARKER} Aina")
            db.commit()

            service = ProjectOrderInquiryService(db)
            service.ensure_inquiry(pso, actor_user_id=actor)
            service.ensure_inquiry(pso, actor_user_id=actor)  # same pass, no commit between
            db.commit()

            header = db.query(OrderInquiry).filter(
                OrderInquiry.project_sales_order_id == pso.id
            ).one()
            rows = _raise_rows(db, header.id)
            assert len(rows) == 1, "one write inside one confirmation, not two"

    def test_backfill_writes_raised_at_the_earlier_of_header_and_first_row_AC_RD_02(self):
        with blank_session() as db:
            company_id = _sorento(db)
            header_raised_at = datetime(2026, 9, 10, 9, 0)
            header = _header(db, company_id, raised_at=header_raised_at)
            earliest_row_created_at = header_raised_at - timedelta(days=2)
            _row(db, header, created_at=earliest_row_created_at)
            db.commit()

            module = _migration_module()
            module.backfill_raises(db.connection())
            db.commit()
            db.expire_all()

            rows = _raise_rows(db, header.id)
            assert len(rows) == 2
            assert rows[0].kind == "raised"
            assert rows[0].raised_at == earliest_row_created_at
            assert rows[1].kind == "reconfirmed"
            assert rows[1].raised_at == header_raised_at

            refreshed = db.query(OrderInquiry).filter(OrderInquiry.id == header.id).one()
            assert refreshed.raised_at == earliest_row_created_at, (
                "the header's own raised_at moves to the first of the two"
            )

    def test_backfill_writes_one_raised_row_when_the_two_times_are_equal_AC_RD_02(self):
        with blank_session() as db:
            company_id = _sorento(db)
            same_time = datetime(2026, 9, 10, 9, 0)
            header = _header(db, company_id, raised_at=same_time)
            _row(db, header, created_at=same_time)
            db.commit()

            module = _migration_module()
            module.backfill_raises(db.connection())
            db.commit()
            db.expire_all()

            rows = _raise_rows(db, header.id)
            assert len(rows) == 1
            assert rows[0].kind == "raised"

    def test_backfill_keeps_raised_by_when_the_header_was_never_re_stamped_AC_RD_02(self):
        """S4 (reviewer, fix round 22 Sep 2026): a header with no `reconfirmed` row (the
        two times land within the same minute, matching the equal-times test above) still
        names the person who raised it - the backfill must not blank a `raised_by` it
        actually knows just because it always used to write NULL there."""
        with blank_session() as db:
            company_id = _sorento(db)
            raiser = _user(db, f"{MARKER} Raiser")
            same_time = datetime(2026, 9, 10, 9, 0)
            header = _header(db, company_id, raised_by=raiser, raised_at=same_time)
            _row(db, header, created_at=same_time)
            db.commit()

            module = _migration_module()
            module.backfill_raises(db.connection())
            db.commit()
            db.expire_all()

            rows = _raise_rows(db, header.id)
            assert len(rows) == 1
            assert rows[0].kind == "raised"
            assert rows[0].raised_by == raiser

    def test_backfill_blanks_raised_by_when_a_reconfirm_is_also_written_AC_RD_02(self):
        """The mirror case: once the two times differ by more than a minute, the
        header's own `raised_by` names the LAST reconfirmer (the `reconfirmed` row this
        same backfill writes), not the original raiser - so the `raised` row's own
        `raised_by` stays NULL rather than being attributed to the wrong person."""
        with blank_session() as db:
            company_id = _sorento(db)
            reconfirmer = _user(db, f"{MARKER} Reconfirmer")
            header_raised_at = datetime(2026, 9, 10, 9, 0)
            header = _header(db, company_id, raised_by=reconfirmer, raised_at=header_raised_at)
            earliest_row_created_at = header_raised_at - timedelta(days=2)
            _row(db, header, created_at=earliest_row_created_at)
            db.commit()

            module = _migration_module()
            module.backfill_raises(db.connection())
            db.commit()
            db.expire_all()

            rows = _raise_rows(db, header.id)
            assert len(rows) == 2
            raised_row = next(r for r in rows if r.kind == "raised")
            reconfirmed_row = next(r for r in rows if r.kind == "reconfirmed")
            assert raised_row.raised_by is None, (
                "the true first raiser is unknown - the header only remembers the last "
                "reconfirmer"
            )
            assert reconfirmed_row.raised_by == reconfirmer

    def test_header_detail_raise_history_is_newest_first_with_names_AC_RD_03(self):
        """`OrderInquiryHeaderService.get(id).raise_history` after one raise + two
        reconfirms - three entries, newest first, `by_name` resolved. The service does
        not exist yet (S2/S3), so this fails on import before the assertions run."""
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            pso = _pso(db, company_id)
            eling = _user(db, f"{MARKER} Eling")
            joey = _user(db, f"{MARKER} Joey")
            aina = _user(db, f"{MARKER} Aina")
            db.commit()

            service = ProjectOrderInquiryService(db)
            header = service.ensure_inquiry(pso, actor_user_id=eling)
            db.commit()
            service.ensure_inquiry(pso, actor_user_id=joey)
            db.commit()
            service.ensure_inquiry(pso, actor_user_id=aina)
            db.commit()

            detail = OrderInquiryHeaderService(db).get(header.id)
            history = detail.raise_history
            assert len(history) == 3
            kinds = [entry.kind for entry in history]
            assert kinds == ["reconfirmed", "reconfirmed", "raised"], "newest first"
            assert history[0].by_name is not None
            assert history[0].at is not None

    def test_a_header_predating_the_migration_keeps_its_first_raise_and_last_reconfirm_AC_RD_03(
        self,
    ):
        """A header the backfill touched (no prior `order_inquiry_raises` rows) still
        answers `raise_history` with at least its first raise and, when different, its
        last reconfirm - the migration's own backfill is what makes that true."""
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            header_raised_at = datetime(2026, 9, 10, 9, 0)
            header = _header(db, company_id, raised_at=header_raised_at)
            earliest_row_created_at = header_raised_at - timedelta(days=2)
            _row(db, header, created_at=earliest_row_created_at)
            db.commit()

            module = _migration_module()
            module.backfill_raises(db.connection())
            db.commit()
            db.expire_all()

            detail = OrderInquiryHeaderService(db).get(header.id)
            kinds = [entry.kind for entry in detail.raise_history]
            assert kinds == ["reconfirmed", "raised"]
