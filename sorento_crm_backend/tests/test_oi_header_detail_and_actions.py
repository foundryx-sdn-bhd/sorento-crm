"""S3 - OI header detail, lines filter, related documents, whole-OI confirm/auto-link,
links (`PLAN-oi-header-list-detail.md`, `oi-header-list-detail-acceptance-criteria.md`,
AC-DT-01..03, AC-CF-01..02, AC-AL-01, AC-LK-01).

TEST-FIRST (Phase 2): written against the UAC + the plan's "Contract" section, with NO
implementation to look at. `OrderInquiryHeaderService.get`/`.related_documents`, the
`GET /order-inquiry-headers/{id}` and `.../related-documents` routes, the `inquiry_id`
worklist filter, and the `filter.inquiry_id` branch of acknowledge/auto-place all do not
exist yet - every test below fails on a 404 (route not mounted), a 422 (the filter schema
still `extra="forbid"`s an unknown key), a scoping leak (the filter exists on the wire but
does nothing yet), or an import error, never a fixture bug.

Seeding reuses `tests/test_oi_header_list.py`'s own helpers (`_header`, `_client`, its
`api` fixture here renamed `list_api`, `blank_session`) for the header-list-shaped tests,
and `tests/test_order_inquiry_handshake.py`'s real-database `api`/`world` harness (via
`_raise_one_row`, `_open_po_line`) for AC-AL-01, which needs a REAL confirm-produced,
cascade-linkable row - a hand-built `OrderInquiryRow` with no `so_line_id`/warehouse
context is not something the cascade can act on at all, and a false "nothing moved"
result would be a fixture bug, not a red test. Both `api` fixture names would collide in
one module, so the header-list one is imported under an alias and only the handshake
`api`/`world` pair keeps its bare name (matching how `test_oi_one_header.py` re-exports
fixtures it imports).
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from ._pg_fixture import blank_session
from .test_oi_header_list import (
    BASE,
    HEADERS,
    MARKER,
    VIEW,
    _agent,
    _client,
    _customer,
    _header,
    _restore,
    _sorento,
    _uid,
    _user,
    api as list_api,
)
from .test_order_inquiry_handshake import (
    LIST as HANDSHAKE_LIST,
    VIEW as HANDSHAKE_VIEW,
    _as_purchasing,
    _links_of,
    _open_po_line,
    _raise_one_row,
    api,
    world,
)
from .test_planning_changes import _product as _handshake_product

__all__ = ["list_api", "api", "world"]  # re-exported fixtures

ACKNOWLEDGE = "projects.order_inquiries.acknowledge"


def _ack_client(db, user_id: str):
    return _client(db, user_id, [VIEW, ACKNOWLEDGE])


# =============================================================================
# AC-DT-01 - header detail
# =============================================================================


class TestHeaderDetail:
    def test_detail_full_shape_and_404_boundaries_AC_DT_01(self, list_api):
        """One test, not three: an "unknown id -> 404" or "other company -> 404" check
        on its OWN would pass today by accident (the route does not exist, so EVERY id
        404s) and prove nothing. Ordering the real header's own 200 FIRST means the
        whole test is red for the right reason today, and stays a real assertion on
        company scoping once the route exists - the later 404 checks would then only
        pass if a REAL 404 (unknown / wrong company), not a missing route, produces
        them."""
        from app.models.company import Company

        client, db, company_id = list_api
        customer = _customer(db, company_id, f"{MARKER} Optad")
        agent = _agent(db, f"{MARKER} Sean")
        seeded = _header(
            db, company_id, customer=customer, agent=agent, project_label=f"{MARKER} Proj"
        )

        response = client.get(f"{HEADERS}/{seeded['inquiry'].id}")
        assert response.status_code == 200, response.text
        body = response.json()
        for key in (
            "id",
            "inquiry_no",
            "raised_at",
            "raised_by_name",
            "so_number",
            "so_date",
            "customer_name",
            "project_title",
            "agent_name",
            "lines_total",
            "lines_to_confirm",
            "qty_total",
            "status",
            "order_type",
            "raise_history",
        ):
            assert key in body, f"contract field {key!r} missing from the detail wire"

        unknown = client.get(f"{HEADERS}/{_uid()}")
        assert unknown.status_code == 404, unknown.text

        other = Company(id=_uid(), name=f"{MARKER} Other Co", code=f"ZZT{_uid()[:6]}")
        db.add(other)
        db.flush()
        theirs = _header(db, other.id)
        other_response = client.get(f"{HEADERS}/{theirs['inquiry'].id}")
        assert other_response.status_code == 404, other_response.text


# =============================================================================
# AC-DT-02 - the worklist's own inquiry_id filter
# =============================================================================


def test_worklist_inquiry_id_filter_scopes_to_one_header_and_keeps_its_fields_AC_DT_02(
    list_api,
):
    client, db, company_id = list_api
    mine = _header(
        db,
        company_id,
        rows=[{"item_code": f"{MARKER}-A"}, {"item_code": f"{MARKER}-B"}],
    )
    other = _header(db, company_id, rows=[{"item_code": f"{MARKER}-C"}])

    response = client.get(f"{BASE}/order-inquiries", params={"inquiry_id": mine["inquiry"].id})
    assert response.status_code == 200, response.text
    body = response.json()

    mine_ids = {str(r.id) for r in mine["rows"]}
    other_ids = {str(r.id) for r in other["rows"]}
    returned_ids = {item["id"] for item in body["data"]}

    assert mine_ids <= returned_ids, "every one of this header's own rows must be present"
    assert not (other_ids & returned_ids), (
        "inquiry_id must scope to just this header - today it is not read at all, so "
        "the other header's row leaks into the answer"
    )

    row = next(item for item in body["data"] if item["id"] in mine_ids)
    # `OrderInquiryWorklistRow`'s own field names (`app/schemas/project_order_inquiry.py`):
    # `supplier` / `po_number` / `location` / `verb` (+ `note`) for supplier/PO/location/
    # instruction, `links` for the SPO side (an SPO is a `kind="spo"` entry there, never a
    # top-level field) - `remark` belongs to the per-project `OrderInquiryRowOut`, not this
    # cross-project worklist row.
    for key in ("supplier", "po_number", "location", "verb", "note", "links"):
        assert key in row, f"worklist field {key!r} must survive the inquiry_id filter"


# =============================================================================
# AC-DT-03 - related documents
# =============================================================================


class TestRelatedDocuments:
    def _supplier(self, db, company_id):
        from app.models.procurement import Supplier

        supplier = Supplier(
            id=_uid(), company_id=company_id, supplier_code=f"ZZT-{_uid()[:8]}",
            supplier_name=f"{MARKER} Supplier",
        )
        db.add(supplier)
        db.flush()
        return supplier

    def _po_line(self, db, company_id, supplier):
        from datetime import date

        from app.models.procurement import PurchaseOrder, PurchaseOrderLine

        from .test_oi_header_list import _product

        po = PurchaseOrder(
            id=_uid(), company_id=company_id, po_number=f"ZZT-PO-{_uid()[:8]}",
            supplier_id=supplier.id, issue_date=date(2026, 6, 1), status="active",
        )
        db.add(po)
        db.flush()
        product = _product(db)
        line = PurchaseOrderLine(
            id=_uid(), company_id=company_id, purchase_order_id=po.id, product_id=product.id,
            qty_ordered=Decimal("50"), qty_received=Decimal("0"), line_status="open",
        )
        db.add(line)
        db.flush()
        return po, line

    def _spo(self, db, company_id, supplier):
        from app.models.procurement import SPOAllocation

        from .test_oi_header_list import _product

        product = _product(db)
        allocation = SPOAllocation(
            id=_uid(), company_id=company_id, spo_number=f"SPO-2026/09-{_uid()[:4].upper()}",
            spo_line_number=1, product_id=product.id, supplier_id=supplier.id,
            allocated_quantity=20,
        )
        db.add(allocation)
        db.flush()
        return allocation

    def _link(self, db, row, *, po_line=None, allocation=None, qty, document):
        from app.models.project_so import OrderInquiryLink

        link = OrderInquiryLink(
            id=_uid(), company_id=row.company_id, row_id=row.id,
            po_line_id=po_line.id if po_line else None,
            spo_allocation_id=allocation.id if allocation else None,
            document=document, qty=Decimal(str(qty)),
        )
        db.add(link)
        db.flush()
        return link

    def test_groups_by_po_and_spo_and_ignores_cancelled_rows_AC_DT_03(self):
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            supplier = self._supplier(db, company_id)
            po, po_line = self._po_line(db, company_id, supplier)
            allocation = self._spo(db, company_id, supplier)

            seeded = _header(
                db,
                company_id,
                rows=[
                    {"qty": "4"},  # linked to the PO line
                    {"qty": "6"},  # linked to the SPO allocation
                    {"qty": "999", "state": "cancelled"},  # linked but cancelled - ignored
                ],
            )
            po_row, spo_row, cancelled_row = seeded["rows"]
            self._link(db, po_row, po_line=po_line, qty="4", document=po.po_number)
            self._link(db, spo_row, allocation=allocation, qty="6", document=allocation.spo_number)
            self._link(
                db, cancelled_row, po_line=po_line, qty="999", document=po.po_number
            )
            db.commit()

            result = OrderInquiryHeaderService(db).related_documents(seeded["inquiry"].id)
            assert len(result.purchase_orders) == 1
            po_entry = result.purchase_orders[0]
            assert po_entry.po_id == po.id
            assert po_entry.po_number == po.po_number
            assert po_entry.lines_linked == 1
            assert Decimal(po_entry.qty_linked) == Decimal("4"), (
                "the cancelled row's 999 must not be counted"
            )

            assert len(result.spos) == 1
            spo_entry = result.spos[0]
            assert spo_entry.spo_number == allocation.spo_number
            assert spo_entry.lines_linked == 1
            assert Decimal(spo_entry.qty_linked) == Decimal("6")

    def _spo_naming_a_po(self, db, company_id):
        """A book-fed SPO line (no `po_line_id`) naming its source PO by number, the two
        carrying DIFFERENT suppliers - the owner's 9 Oct screenshot."""
        po_supplier = self._supplier(db, company_id)
        spo_supplier = self._supplier(db, company_id)
        po, _po_line = self._po_line(db, company_id, po_supplier)
        allocation = self._spo(db, company_id, spo_supplier)
        allocation.from_po_number = po.po_number
        db.flush()
        return po_supplier, spo_supplier, allocation

    def test_spo_entry_names_its_source_pos_supplier_OI_SUPPLIER_FROM_PO(self):
        """OI-SUPPLIER-FROM-PO (owner, 9 Oct 2026): the SPO group's supplier is the
        supplier of the PO the SPO line names, never the allocation's own."""
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            po_supplier, _spo_supplier, allocation = self._spo_naming_a_po(db, company_id)
            seeded = _header(db, company_id, rows=[{"qty": "6"}])
            self._link(
                db, seeded["rows"][0], allocation=allocation, qty="6",
                document=allocation.spo_number,
            )
            db.commit()

            result = OrderInquiryHeaderService(db).related_documents(seeded["inquiry"].id)
            [spo_entry] = result.spos
            assert spo_entry.supplier_name == po_supplier.supplier_name

    def test_spo_entry_keeps_its_own_supplier_without_a_known_po_OI_SUPPLIER_FROM_PO(self):
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            spo_supplier = self._supplier(db, company_id)
            allocation = self._spo(db, company_id, spo_supplier)
            allocation.from_po_number = f"ZZT-NOPO-{_uid()[:8]}"
            seeded = _header(db, company_id, rows=[{"qty": "6"}])
            self._link(
                db, seeded["rows"][0], allocation=allocation, qty="6",
                document=allocation.spo_number,
            )
            db.commit()

            result = OrderInquiryHeaderService(db).related_documents(seeded["inquiry"].id)
            [spo_entry] = result.spos
            assert spo_entry.supplier_name == spo_supplier.supplier_name

    def test_spo_popover_names_its_source_pos_supplier_OI_SUPPLIER_FROM_PO(self):
        """The SPO document popover an OI's SPO cell opens (`get_spo_detail`) reads the
        same rule as the Supplier column."""
        from app.services.order_inquiry_worklist_service import (  # noqa: PLC0415
            OrderInquiryWorklistService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            po_supplier, _spo_supplier, allocation = self._spo_naming_a_po(db, company_id)
            db.commit()

            detail = OrderInquiryWorklistService(db).get_spo_detail(allocation.spo_number)
            assert detail["supplier_name"] == po_supplier.supplier_name

    def test_empty_lists_when_nothing_is_linked_AC_DT_03(self):
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )

        with blank_session() as db:
            company_id = _sorento(db)
            seeded = _header(db, company_id)
            db.commit()
            result = OrderInquiryHeaderService(db).related_documents(seeded["inquiry"].id)
            assert result.purchase_orders == []
            assert result.spos == []


# =============================================================================
# AC-CF-01 - whole-OI confirm via filter.inquiry_id
# =============================================================================


def test_acknowledge_filter_inquiry_id_scopes_and_skips_rejected_cancelled_AC_CF_01(
    list_api,
):
    """`AcknowledgeFilter` has `extra="forbid"` and no `inquiry_id` field today, so a
    test that stopped at "this 422s" would pass right now for the wrong reason (the
    field is merely unknown, not yet wired) and would have to be INVERTED the moment
    the real feature lands - backwards from a red test. This asserts the actual target
    behaviour (200, scoped confirm, rejected/cancelled skipped, a second header
    untouched), which is red today via that same 422."""
    from app.models.project_so import (  # noqa: PLC0415
        ACK_ACKNOWLEDGED,
        ACK_AWAITING,
        ACK_CHANGED,
        ACK_REJECTED,
        INQUIRY_CANCELLED,
    )

    client, db, company_id = list_api
    ack_client, originals = _ack_client(db, _user(db, f"{MARKER} Buyer"))
    db.commit()
    try:
        header = _header(
            db,
            company_id,
            rows=[
                {"ack_state": ACK_AWAITING},
                {"ack_state": ACK_CHANGED},
                {"ack_state": ACK_REJECTED},
                {"ack_state": ACK_AWAITING, "state": INQUIRY_CANCELLED},
            ],
        )
        other = _header(db, company_id, rows=[{"ack_state": ACK_AWAITING}])

        response = ack_client.post(
            f"{BASE}/order-inquiries/acknowledge",
            json={"filter": {"inquiry_id": header["inquiry"].id}},
        )
        assert response.status_code == 200, response.text

        db.expire_all()
        for row in header["rows"][:2]:
            db.refresh(row)
            assert row.ack_state == ACK_ACKNOWLEDGED, "awaiting/changed rows are confirmed"
        db.refresh(header["rows"][2])
        assert header["rows"][2].ack_state == ACK_REJECTED, "a rejected row is skipped"
        db.refresh(header["rows"][3])
        assert header["rows"][3].ack_state == ACK_AWAITING, "a cancelled row is skipped"
        db.refresh(other["rows"][0])
        assert other["rows"][0].ack_state == ACK_AWAITING, (
            "a second header's rows must be untouched"
        )

        listing = client.get(HEADERS, params={"state": "completed"})
        assert listing.status_code == 200, listing.text
        ids = {item["id"] for item in listing.json()["data"]}
        assert header["inquiry"].id in ids, "a whole-OI confirm lists the header as completed"
    finally:
        _restore(originals)


# =============================================================================
# AC-CF-02 - a header returns to Outstanding on a fresh change
# =============================================================================


class TestOutstandingRecomputes:
    def test_returns_to_outstanding_on_a_changed_row_and_on_a_new_awaiting_row_AC_CF_02(
        self, list_api
    ):
        """`OrderInquiryHeaderService.list` re-derives `status` from the LIVE rows on
        every call - never a stored flag - so a header that reads Completed today must
        flip back the moment a row changes or a fresh one is raised. Best-effort call
        shape (`state="all"`, `.items`) - the service does not exist yet, so this fails
        at import regardless."""
        from app.services.order_inquiry_header_service import (  # noqa: PLC0415
            OrderInquiryHeaderService,
        )
        from app.models.project_so import (  # noqa: PLC0415
            ACK_ACKNOWLEDGED,
            ACK_AWAITING,
            ACK_CHANGED,
            INQUIRY_RAISED,
            IV_ORDER,
            OrderInquiryRow,
        )

        _client, db, company_id = list_api
        seeded = _header(db, company_id, rows=[{"ack_state": ACK_ACKNOWLEDGED}])
        service = OrderInquiryHeaderService(db)

        listing = service.list(state="all")
        row = next(h for h in listing.items if h.id == seeded["inquiry"].id)
        assert row.status == "completed"

        seeded["rows"][0].ack_state = ACK_CHANGED
        db.commit()
        listing = service.list(state="all")
        row = next(h for h in listing.items if h.id == seeded["inquiry"].id)
        assert row.status == "outstanding", "a changed row pulls it back"

        seeded["rows"][0].ack_state = ACK_ACKNOWLEDGED
        db.add(
            OrderInquiryRow(
                id=_uid(), company_id=company_id, order_inquiry_id=seeded["inquiry"].id,
                qty=Decimal("1"), verb=IV_ORDER, state=INQUIRY_RAISED, ack_state=ACK_AWAITING,
            )
        )
        db.commit()
        listing = service.list(state="all")
        row = next(h for h in listing.items if h.id == seeded["inquiry"].id)
        assert row.status == "outstanding", "a fresh awaiting row pulls it back too"


# =============================================================================
# AC-AL-01 - auto-place filter.inquiry_id
# =============================================================================


def test_auto_place_filter_scopes_to_the_named_header_and_403_without_action_AC_AL_01(api):
    fixture_a = _raise_one_row(api, qty="10")
    _client, world = api
    product_b = _handshake_product(world.db)
    fixture_b = _raise_one_row(api, qty="10", product=product_b)
    _open_po_line(world, qty=50, product=world.product)
    _open_po_line(world, qty=50, product=product_b)

    header_a_id = str(fixture_a["row"].order_inquiry_id)

    with _as_purchasing(world) as buyer:
        response = buyer.post(
            f"{HANDSHAKE_LIST}/auto-place", json={"filter": {"inquiry_id": header_a_id}}
        )
        assert response.status_code == 200, response.text

    world.db.commit()
    world.db.refresh(fixture_b["row"])
    assert not _links_of(world, fixture_b["row"]), (
        "a second header's linkable row must stay unlinked when the filter names "
        "only the first header - today the filter is silently ignored, so both link"
    )

    with _as_purchasing(world, permissions=[HANDSHAKE_VIEW]) as no_action:
        forbidden = no_action.post(
            f"{HANDSHAKE_LIST}/auto-place", json={"filter": {"inquiry_id": header_a_id}}
        )
        assert forbidden.status_code == 403, forbidden.text


# =============================================================================
# AC-LK-01 - the OI link points at the header detail page
# =============================================================================


def test_build_order_inquiry_link_points_at_the_header_detail_page_AC_LK_01():
    from app.services.automation_triggers import build_order_inquiry_link  # noqa: PLC0415

    header_id = str(uuid.uuid4())
    link = build_order_inquiry_link(header_id)
    assert link.endswith(f"/project-sales/order-inquiries/{header_id}"), (
        f"expected a header-detail link, got {link!r} (today this is treated as an SO "
        "number and turned into a worklist search query string)"
    )


def test_so_detail_order_inquiries_entries_carry_id_AC_LK_01(list_api):
    from app.services.scm.sales_order_service import SalesOrderService  # noqa: PLC0415

    _client, db, company_id = list_api
    seeded = _header(db, company_id)
    rows = [{"id": seeded["core"].id}]

    out = SalesOrderService(db).with_order_inquiries(rows)
    entry = out[0]["order_inquiries"][0]
    assert entry["id"] == seeded["inquiry"].id, (
        "with_order_inquiries's own dict carries no 'id' key today"
    )
