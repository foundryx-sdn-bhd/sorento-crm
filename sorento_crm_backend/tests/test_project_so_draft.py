"""P7 sales order drafting: explosion, phase spread, area split, and the two tier gate.

The numbers here are the client's own, measured off the committed golden set in
``sorento_crm_frontend/e2e/fixtures/project-cs/``:

* PO line 1 is `SRTWC8613-RL`, 927 SETS at 392.85, amounting to 364,171.95.
* The handwritten strike-through cancels line 7, `SRTFV1001`, 16 NOS at 295.85, being
  4,733.60.
* The PO's printed total is 1,810,640.62, and 1,810,640.62 - 4,733.60 is exactly the
  quotation's grand total of 1,805,907.02. One identity validates the extraction, the
  cancellation reading and the cross-check together.
* The TOWER schedule places 135 in the first phase and 72 in each of the eleven after it:
  135 + 72 x 11 = 927, which is the column total the customer prints themselves.

Two notes on what these tests exercise.

``item_packages`` (the AutoCount PackageDTL mirror) holds exactly ONE row in this
development database, and its ORM model lives on the unmerged AutoCount branch, so the
service reads it through raw SQL. Every test below therefore exercises the QUOTATION
fallback, which is the path real data takes today. The package path gets its own test that
creates the mirror tables inside the scratch schema, so it is covered rather than assumed.

Cleanup is by rollback (``blank_session``) and every assertion is scoped to rows the test
created: the shared database is a copy of production.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.order import Customer
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.models.project_so import (
    DeliverySchedule,
    DeliveryScheduleCell,
    DeliveryScheduleVersion,
    ProjectDeliveryPhase,
    ProjectPOLine,
    ProjectPOVersion,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
    SODraftFinding,
)
from app.models.projects import (
    ProjectParty,
    ProjectPurchaseOrder,
    ProjectQuotation,
    ProjectQuotationDocument,
    ProjectQuotationLine,
    ProjectQuotationVersion,
)
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException
from app.services.project_so_draft_service import (
    OVERRIDE_PERMISSION,
    ProjectSODraftService,
)

from ._pg_fixture import blank_session

MARKER = "zzt-so"

# The TOWER programme, straight off `delivery-schedule-buimaco-r1.pdf` (R1).
TOWER_R1 = [
    (1, "Level 2 & 7", date(2026, 7, 1), Decimal("135")),
    (2, "Level 8 & 10", date(2026, 8, 3), Decimal("72")),
    (3, "Level 11 to 13", date(2026, 9, 1), Decimal("72")),
    (4, "Level 14 to 16", date(2026, 10, 1), Decimal("72")),
    (5, "Level 17 to 19", date(2026, 11, 2), Decimal("72")),
    (6, "Level 20 to 22", date(2026, 12, 1), Decimal("72")),
    (7, "Level 23 to 25", date(2027, 1, 4), Decimal("72")),
    (8, "Level 26 to 28", date(2027, 2, 1), Decimal("72")),
    (9, "Level 29 to 31", date(2027, 3, 1), Decimal("72")),
    (10, "Level 32 to 34", date(2027, 4, 5), Decimal("72")),
    (11, "Level 35 to 37", date(2027, 5, 3), Decimal("72")),
    (12, "Level 38 to 40", date(2027, 6, 1), Decimal("72")),
]


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _user(db, name: str) -> str:
    user_id = _uid()
    db.add(User(id=user_id, email=f"{user_id}@zzt.test", name=name))
    db.flush()
    return user_id


def _product(db, code: str, list_price: str = "100.00") -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=code,
        product_name=f"{MARKER} {code}",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal(list_price),
    )
    db.add(row)
    db.flush()
    return row


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    return register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tuju Residences {_uid()[:12]}",
    )


def _customer(db, name: str, *, credit_limit: str | None = None) -> Customer:
    row = Customer(
        id=_uid(), customer_code=f"ZZT-{_uid()[:8]}", customer_name=f"{MARKER} {name}"
    )
    db.add(row)
    db.flush()
    if credit_limit is not None:
        # `credit_limit` is a real column that the ORM model does not carry, so the
        # service reads it by name. The scratch schema is built from the models, hence
        # the column has to be added before it can be set.
        db.execute(
            text("ALTER TABLE customers ADD COLUMN IF NOT EXISTS credit_limit numeric(15,2)")
        )
        db.execute(
            text("UPDATE customers SET credit_limit = :v WHERE id = :id"),
            {"v": credit_limit, "id": row.id},
        )
    return row


def _numbering_rule(db, company_id: str) -> None:
    """The provisional-ref counter as production carries it: enabled, and monotonic."""
    from app.models.numbering import DocumentNumberingRule
    from app.services.project_so_draft_service import NUMBERING_DOC_TYPE

    rule = (
        db.query(DocumentNumberingRule)
        .filter(DocumentNumberingRule.doc_type == NUMBERING_DOC_TYPE)
        .first()
    )
    if rule is None:
        rule = DocumentNumberingRule(id=_uid(), doc_type=NUMBERING_DOC_TYPE)
        if hasattr(DocumentNumberingRule, "company_id"):
            rule.company_id = company_id
        db.add(rule)
    rule.enabled = True
    rule.prefix_template = "PSO-"
    rule.number_digits = 6
    rule.next_value = 1
    rule.start_value = 1
    rule.reset_policy = "none"
    rule.last_reset_key = None
    db.flush()


def _party(db, company_id: str, *, customer: Customer | None = None) -> ProjectParty:
    row = ProjectParty(
        id=_uid(),
        company_id=company_id,
        party_type="trading_house",
        name=f"{MARKER} Buimaco {_uid()[:6]}",
        customer_id=customer.id if customer else None,
    )
    db.add(row)
    db.flush()
    return row


def _quotation(db, project, *, lines, total: str | None = None):
    """A quotation version with `lines` as (product, qty, unit_price) in printed order.

    The scope hangs off a DOCUMENT - `project_quotations.document_id` is NOT NULL - so the
    letterhead is seeded here rather than left to a nullable that the database does not have.
    """
    document = ProjectQuotationDocument(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        document_no=f"{MARKER}-Q-{_uid()[:8]}",
    )
    db.add(document)
    db.flush()
    quotation = ProjectQuotation(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        document_id=document.id,
        scope_label=f"{MARKER} Tower and common area",
    )
    db.add(quotation)
    db.flush()
    version = ProjectQuotationVersion(
        id=_uid(),
        company_id=project.company_id,
        quotation_id=quotation.id,
        version_no=1,
        total_amount=Decimal(total) if total else Decimal("0"),
    )
    db.add(version)
    db.flush()
    for index, (product, qty, unit_price) in enumerate(lines):
        db.add(
            ProjectQuotationLine(
                id=_uid(),
                company_id=project.company_id,
                version_id=version.id,
                product_id=product.id,
                product_code_snapshot=product.product_code,
                description_snapshot=product.product_name,
                unit_price=Decimal(unit_price),
                quantity=Decimal(qty),
                uom="UNIT",
                line_total=(Decimal(qty) * Decimal(unit_price)).quantize(Decimal("0.01")),
                sort_order=index,
            )
        )
    db.flush()
    return version


def _po(db, project, *, party, quotation_version=None, po_number=None):
    row = ProjectPurchaseOrder(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        quotation_version_id=quotation_version.id if quotation_version else None,
        po_source="trading_house",
        issuing_party_id=party.id,
        po_number=po_number or f"HQ/26/01/{_uid()[:4]}",
        po_date=date(2026, 1, 19),
        term_days=60,
        status="approved",
    )
    db.add(row)
    db.flush()
    return row


def _po_version(db, po, *, lines, extracted_total=None, version_no=1, confirmed=True):
    """`lines` as (line_no, product|None, code, qty, uom, unit_price, amount, cancelled)."""
    version = ProjectPOVersion(
        id=_uid(),
        company_id=po.company_id,
        purchase_order_id=po.id,
        version_no=version_no,
        extraction_state="done",
        extracted_total=Decimal(extracted_total) if extracted_total else None,
        confirmed_at=date.today() if confirmed else None,
    )
    db.add(version)
    db.flush()
    for line_no, product, code, qty, uom, unit_price, amount, cancelled in lines:
        db.add(
            ProjectPOLine(
                id=_uid(),
                company_id=po.company_id,
                po_version_id=version.id,
                line_no=line_no,
                stock_code_raw=code,
                description_raw=f"{MARKER} {code}",
                qty=Decimal(qty),
                uom_raw=uom,
                unit_price=Decimal(unit_price),
                amount=Decimal(amount),
                is_cancelled=cancelled,
                resolved_product_id=product.id if product else None,
                resolution_source="description" if product else None,
                arithmetic_ok=None,
            )
        )
    db.flush()
    return version


def _schedule(db, project, po, *, po_version=None, revision_label=None, version_no=1):
    schedule = DeliverySchedule(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        purchase_order_id=po.id,
        label=f"{MARKER} programme",
    )
    db.add(schedule)
    db.flush()
    version = DeliveryScheduleVersion(
        id=_uid(),
        company_id=project.company_id,
        delivery_schedule_id=schedule.id,
        version_no=version_no,
        revision_label=revision_label,
        po_version_id=po_version.id if po_version else None,
        extraction_state="done",
        schedule_date=date(2026, 3, 4),
    )
    db.add(version)
    db.flush()
    return version


def _phase(db, project, *, area_group, sequence, label, delivery_date, version=None):
    row = ProjectDeliveryPhase(
        id=_uid(),
        company_id=project.company_id,
        project_id=project.id,
        area_group=area_group,
        sequence=sequence,
        label=label,
        delivery_date=delivery_date,
        source_version_id=version.id if version else None,
    )
    db.add(row)
    db.flush()
    return row


def _cell(db, version, phase, product, qty):
    row = DeliveryScheduleCell(
        id=_uid(),
        company_id=version.company_id,
        version_id=version.id,
        phase_id=phase.id,
        product_id=product.id,
        customer_code_raw=f"BUI-HB-{product.product_code}",
        qty=Decimal(qty),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def seeded():
    with blank_session() as db:
        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        owner = _user(db, f"{MARKER} CONTACT AH")
        yield db, company_id, owner


def _po_line_numbers(db, lines):
    """`ProjectSalesOrderLine` has no `source_po_line_no` column -- only the model in the
    service (its transient `_source_po_line_no` set at build time) does. A test reading the
    row back from a fresh query has to resolve it the same way the service's own
    `_source_line_no` fallback does: through `source_po_line_id`."""
    ids = [line.source_po_line_id for line in lines if line.source_po_line_id]
    by_id = (
        dict(
            db.query(ProjectPOLine.id, ProjectPOLine.line_no)
            .filter(ProjectPOLine.id.in_(ids))
            .all()
        )
        if ids
        else {}
    )
    return {line.id: by_id.get(line.source_po_line_id) for line in lines}


def _findings(db, order_ids, code=None):
    query = db.query(SODraftFinding).filter(
        SODraftFinding.project_sales_order_id.in_(list(order_ids))
    )
    if code:
        query = query.filter(SODraftFinding.code == code)
    return query.all()


def _lines(db, pso_id):
    return (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.project_sales_order_id == pso_id)
        .order_by(ProjectSalesOrderLine.line_no.asc())
        .all()
    )


# --------------------------------------------------------------------- explosion


def test_a_set_explodes_into_a_priced_parent_and_zero_priced_companions(seeded):
    """QT-004188's own shape: item 1 at 392.85, item 2 the companion at the same
    quantity and 0.00. The PO says one SETS line; the sales order says two components."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    companion = _product(db, "SRTWC8613-FC")
    quotation = _quotation(
        db,
        project,
        lines=[(parent, "927", "392.85"), (companion, "927", "0.00")],
        total="364171.95",
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[(1, parent, "SRTWC8613-RL", "927", "SETS", "392.85", "364171.95", False)],
        extracted_total="364171.95",
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, parent, "927")

    result = ProjectSODraftService(db).build(po.id, schedule.id)

    assert len(result["data"]) == 1
    order_id = result["data"][0]["id"]
    lines = _lines(db, order_id)
    assert [line.qty for line in lines] == [Decimal("927.0000"), Decimal("927.0000")]
    priced = [line for line in lines if line.unit_price > 0]
    zero = [line for line in lines if line.unit_price == 0]
    assert len(priced) == 1 and len(zero) == 1
    assert priced[0].product_id == parent.id
    assert priced[0].amount == Decimal("364171.95")
    assert zero[0].product_id == companion.id
    assert zero[0].amount == Decimal("0.00")
    assert {line.explosion_source for line in lines} == {"quotation"}
    # G3: the quotation line the balance came from is on the sales order line, always.
    assert all(line.quotation_line_id for line in lines)


def test_the_quotation_fallback_reproduces_the_quoted_quantities_exactly(seeded):
    """A companion quoted at twice the parent's quantity must come out at twice the
    ordered quantity, to the unit."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    companion = _product(db, "SRTPW0035-CR")
    quotation = _quotation(
        db, project, lines=[(parent, "100", "392.85"), (companion, "200", "0.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db, po, lines=[(1, parent, "SRTWC8613-RL", "50", "SETS", "392.85", "19642.50", False)]
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, parent, "50")

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    lines = _lines(db, result["data"][0]["id"])
    by_product = {line.product_id: line.qty for line in lines}
    assert by_product[parent.id] == Decimal("50.0000")
    assert by_product[companion.id] == Decimal("100.0000")


def test_an_inexact_quotation_ratio_is_reported_rather_than_guessed(seeded):
    """One companion per three parents, with 50 ordered, does not divide.

    The line is drafted whole and `no_package_mapping` says why. Rounding 16.67 into a
    component quantity would commit us to stock nobody ordered.
    """
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    companion = _product(db, "SRTWC8613-FC")
    quotation = _quotation(
        db, project, lines=[(parent, "3", "392.85"), (companion, "1", "0.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db, po, lines=[(1, parent, "SRTWC8613-RL", "50", "SETS", "392.85", "19642.50", False)]
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, parent, "50")

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    order_id = result["data"][0]["id"]
    lines = _lines(db, order_id)
    assert len(lines) == 1
    assert lines[0].explosion_source == "none"
    reported = _findings(db, [order_id], code="no_package_mapping")
    assert len(reported) == 1
    assert companion.product_code in reported[0].detail
    assert reported[0].severity == "warn"


def test_an_item_package_wins_over_the_quotation_grouping(seeded):
    """`item_packages` is authoritative (D10).

    The mirror's ORM model ships on the AutoCount branch, so the tables are created here
    inside the scratch schema. The DDL is rolled back with the rest of the test.
    """
    db, company_id, owner = seeded
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS item_packages ("
            " id uuid PRIMARY KEY, package_code varchar(100) NOT NULL,"
            " description varchar(255))"
        )
    )
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS item_package_lines ("
            " id uuid PRIMARY KEY, item_package_id uuid NOT NULL, product_id uuid NOT NULL,"
            " line_sequence integer NOT NULL, uom varchar(100), qty numeric(15,4),"
            " unit_price numeric(15,2))"
        )
    )
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    packaged = _product(db, "SRTWB243")
    quotation = _quotation(db, project, lines=[(parent, "927", "392.85")])
    package_id = _uid()
    db.execute(
        text("INSERT INTO item_packages (id, package_code) VALUES (:id, :code)"),
        {"id": package_id, "code": "SRTWC8613-RL"},
    )
    for sequence, (product, qty) in enumerate(
        [(parent, "1"), (packaged, "2")], start=1
    ):
        db.execute(
            text(
                "INSERT INTO item_package_lines"
                " (id, item_package_id, product_id, line_sequence, uom, qty, unit_price)"
                " VALUES (:id, :pkg, :product, :seq, 'UNIT', :qty, 0)"
            ),
            {
                "id": _uid(),
                "pkg": package_id,
                "product": product.id,
                "seq": sequence,
                "qty": qty,
            },
        )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db, po, lines=[(1, parent, "SRTWC8613-RL", "10", "SETS", "392.85", "3928.50", False)]
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, parent, "10")

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    lines = _lines(db, result["data"][0]["id"])
    assert {line.explosion_source for line in lines} == {"package"}
    by_product = {line.product_id: (line.qty, line.unit_price) for line in lines}
    assert by_product[parent.id][0] == Decimal("10.0000")
    assert by_product[parent.id][1] == Decimal("392.85000")
    # Two per set, and the companion rides at zero.
    assert by_product[packaged.id][0] == Decimal("20.0000")
    assert by_product[packaged.id][1] == Decimal("0.00000")


# ----------------------------------------------------------------- phase spread


def test_quantities_spread_across_every_phase_and_sum_to_the_po_quantity(seeded):
    """135 + 72 x 11 = 927, which is the column total the customer prints (R1, TOWER).

    Twelve phases and two components make twenty four lines from ONE PO line: the line
    count is driven by product MULTIPLIED BY phase, exactly as SO397450's 99 lines are.
    """
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    companion = _product(db, "SRTWC8613-FC")
    quotation = _quotation(
        db, project, lines=[(parent, "927", "392.85"), (companion, "927", "0.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[(1, parent, "SRTWC8613-RL", "927", "SETS", "392.85", "364171.95", False)],
        extracted_total="364171.95",
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    for sequence, label, delivery_date, qty in TOWER_R1:
        phase = _phase(
            db,
            project,
            area_group="TOWER",
            sequence=sequence,
            label=label,
            delivery_date=delivery_date,
            version=schedule,
        )
        _cell(db, schedule, phase, parent, qty)

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    order_id = result["data"][0]["id"]
    lines = _lines(db, order_id)

    assert len(lines) == 24
    assert sum(line.qty for line in lines if line.product_id == parent.id) == Decimal("927")
    assert sum(line.qty for line in lines if line.product_id == companion.id) == Decimal("927")
    assert len({line.delivery_date for line in lines}) == 12
    # Every line carries its own delivery date, and the first phase is the first line.
    assert lines[0].delivery_date == date(2026, 7, 1)
    assert lines[0].qty == Decimal("135.0000")
    assert result["data"][0]["total_amount"] == Decimal("364171.95")
    assert not _findings(db, [order_id], code="schedule_short")
    assert not _findings(db, [order_id], code="schedule_over")


def _two_po_lines_three_phases(db, company_id, owner):
    """Two PO lines, three phases each, one area -- the fixture both numbering tests share."""
    project = _project(db, company_id, owner)
    product_a = _product(db, "SRTA-100")
    product_b = _product(db, "SRTB-200")
    quotation = _quotation(
        db, project, lines=[(product_a, "300", "10.00"), (product_b, "60", "20.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, product_a, "SRTA-100", "300", "UNIT", "10.00", "3000.00", False),
            (2, product_b, "SRTB-200", "60", "UNIT", "20.00", "1200.00", False),
        ],
        extracted_total="4200.00",
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    dates = [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)]
    for sequence, delivery_date in enumerate(dates, start=1):
        phase = _phase(
            db,
            project,
            area_group="TOWER",
            sequence=sequence,
            label=f"Phase {sequence}",
            delivery_date=delivery_date,
            version=schedule,
        )
        _cell(db, schedule, phase, product_a, "100")
        _cell(db, schedule, phase, product_b, "20")
    return po, schedule, dates


def test_line_numbers_group_by_po_line_then_phase_not_by_delivery_date(seeded):
    """PO line 1's three phases number 1-3 and PO line 2's number 4-6, in delivery-date
    order within each -- not interleaved by date across the two PO lines, which is what a
    bare date sort used to give (the captain's "why does 1 jump to 23, 40, 57 ...")."""
    db, company_id, owner = seeded
    po, schedule, dates = _two_po_lines_three_phases(db, company_id, owner)

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    order_id = result["data"][0]["id"]
    lines = _lines(db, order_id)
    po_line_no = _po_line_numbers(db, lines)

    assert [
        (line.line_no, po_line_no[line.id], line.delivery_date) for line in lines
    ] == [
        (1, 1, dates[0]),
        (2, 1, dates[1]),
        (3, 1, dates[2]),
        (4, 2, dates[0]),
        (5, 2, dates[1]),
        (6, 2, dates[2]),
    ]


def test_renumber_draft_lines_repairs_a_scrambled_draft(seeded):
    """The repair route for a draft numbered before the grouped-order fix (or scrambled
    any other way): re-applies PO line, then phase, then set-component order."""
    db, company_id, owner = seeded
    po, schedule, dates = _two_po_lines_three_phases(db, company_id, owner)
    result = ProjectSODraftService(db).build(po.id, schedule.id)
    order_id = result["data"][0]["id"]

    # Scramble it the way the old date-major sort used to: date first, PO line second.
    lines = _lines(db, order_id)
    po_line_no = _po_line_numbers(db, lines)
    by_key = {(po_line_no[line.id], line.delivery_date): line for line in lines}
    scrambled_order = [
        by_key[(1, dates[0])],
        by_key[(2, dates[0])],
        by_key[(1, dates[1])],
        by_key[(2, dates[1])],
        by_key[(1, dates[2])],
        by_key[(2, dates[2])],
    ]
    for index, line in enumerate(scrambled_order, start=1):
        line.line_no = index
    db.flush()

    changed = ProjectSODraftService(db).renumber_draft_lines(order_id)

    assert changed > 0
    lines = _lines(db, order_id)
    po_line_no = _po_line_numbers(db, lines)
    assert [(line.line_no, po_line_no[line.id]) for line in lines] == [
        (1, 1),
        (2, 1),
        (3, 1),
        (4, 2),
        (5, 2),
        (6, 2),
    ]

    # A no-op re-run touches nothing.
    assert ProjectSODraftService(db).renumber_draft_lines(order_id) == 0


def test_renumber_draft_lines_refuses_once_the_order_is_past_draft(seeded):
    db, company_id, owner = seeded
    po, schedule, _dates = _two_po_lines_three_phases(db, company_id, owner)
    result = ProjectSODraftService(db).build(po.id, schedule.id)
    order = (
        db.query(ProjectSalesOrder)
        .filter(ProjectSalesOrder.id == result["data"][0]["id"])
        .one()
    )
    order.status = "published"
    db.flush()

    with pytest.raises(AppException) as excinfo:
        ProjectSODraftService(db).renumber_draft_lines(order.id)
    assert excinfo.value.status_code == 409


def test_the_area_split_produces_one_sales_order_per_area_and_records_its_origin(seeded):
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    tower_product = _product(db, "SRTWC8613-RL")
    common_product = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db,
        project,
        lines=[(tower_product, "135", "392.85"), (common_product, "16", "295.85")],
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "135", "UNIT", "392.85", "53034.75", False),
            (2, common_product, "SRTUB206-BI", "16", "NOS", "295.85", "4733.60", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    tower = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    common = _phase(
        db,
        project,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 1),
        version=schedule,
    )
    _cell(db, schedule, tower, tower_product, "135")
    _cell(db, schedule, common, common_product, "16")

    result = ProjectSODraftService(db).build(po.id, schedule.id)
    assert len(result["data"]) == 2
    by_area = {row["area_group"]: row for row in result["data"]}
    assert set(by_area) == {"TOWER", "COMMON AREA"}
    assert by_area["TOWER"]["line_count"] == 1
    assert by_area["COMMON AREA"]["line_count"] == 1
    assert {row["grouping_origin"] for row in result["data"]} == {"area"}
    # Distinct provisional refs, and no UUID is the only identifier of either.
    assert len({row["provisional_ref"] for row in result["data"]}) == 2


# ------------------------------------------------- the split key is a choice


def _split_fixture(db, company_id, owner):
    """One PO cut three ways: two July phases, one August phase, one undated area.

    The same rows answer all three splits, which is the point: the drafted lines do not
    change, only the key they are cut on.
    """
    project = _project(db, company_id, owner)
    tower_product = _product(db, "SRTWC8613-RL")
    common_product = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db, project, lines=[(tower_product, "30", "392.85"), (common_product, "5", "295.85")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "30", "UNIT", "392.85", "11785.50", False),
            (2, common_product, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    july_1 = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    july_2 = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=2,
        label="Level 8 & 10",
        delivery_date=date(2026, 7, 15),
        version=schedule,
    )
    august = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=3,
        label="Level 11 to 13",
        delivery_date=date(2026, 8, 3),
        version=schedule,
    )
    # An area the customer has not dated yet. It is the only source of an undated line
    # here, so the None group is exercised without inventing a short schedule.
    undated = _phase(
        db,
        project,
        area_group="COMMON AREA",
        sequence=4,
        label=None,
        delivery_date=None,
        version=schedule,
    )
    _cell(db, schedule, july_1, tower_product, "10")
    _cell(db, schedule, july_2, tower_product, "10")
    _cell(db, schedule, august, tower_product, "10")
    _cell(db, schedule, undated, common_product, "5")
    return po, schedule


def test_the_split_key_defaults_to_the_schedule_area(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)

    result = ProjectSODraftService(db).build(po.id, schedule.id)

    by_group = {row["area_group"]: row for row in result["data"]}
    assert set(by_group) == {"TOWER", "COMMON AREA"}
    assert by_group["TOWER"]["line_count"] == 3
    assert {row["grouping_origin"] for row in result["data"]} == {"area"}


def test_splitting_by_delivery_month_gives_one_sales_order_per_month(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)

    result = ProjectSODraftService(db).build(
        po.id, schedule.id, split_by="delivery_month"
    )

    by_group = {row["area_group"]: row for row in result["data"]}
    # Two July phases collapse into one order; the undated line keeps its own.
    assert set(by_group) == {"2026-07", "2026-08", None}
    assert by_group["2026-07"]["line_count"] == 2
    assert by_group["2026-08"]["line_count"] == 1
    assert by_group[None]["line_count"] == 1
    assert {row["grouping_origin"] for row in result["data"]} == {"delivery_month"}
    # Ascending by month, undated last, which is the order the list reads in.
    assert [row["area_group"] for row in result["data"]] == ["2026-07", "2026-08", None]
    assert len({row["provisional_ref"] for row in result["data"]}) == 3


def test_splitting_by_delivery_date_gives_one_sales_order_per_date(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)

    result = ProjectSODraftService(db).build(po.id, schedule.id, split_by="delivery_date")

    assert [row["area_group"] for row in result["data"]] == [
        "2026-07-01",
        "2026-07-15",
        "2026-08-03",
        None,
    ]
    assert {row["line_count"] for row in result["data"]} == {1}
    assert {row["grouping_origin"] for row in result["data"]} == {"delivery_date"}


def test_a_line_with_no_delivery_date_lands_in_its_own_group(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)

    result = ProjectSODraftService(db).build(
        po.id, schedule.id, split_by="delivery_month"
    )

    undated = next(row for row in result["data"] if row["area_group"] is None)
    order = ProjectSODraftService(db).get_order(undated["id"])
    lines = (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.project_sales_order_id == order.id)
        .all()
    )
    assert [line.delivery_date for line in lines] == [None]


def test_rebuilding_under_a_different_split_replaces_the_previous_drafts(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)
    service = ProjectSODraftService(db)

    first = service.build(po.id, schedule.id)
    assert len(first["data"]) == 2

    second = service.build(po.id, schedule.id, split_by="delivery_month")
    assert second["replaced_drafts"] == 2
    assert second["skipped_published"] == 0

    live = (
        db.query(ProjectSalesOrder)
        .filter(ProjectSalesOrder.purchase_order_id == po.id)
        .all()
    )
    # Nothing from the area build survives: the two key spaces are not merged.
    assert sorted(row.area_group or "" for row in live) == ["", "2026-07", "2026-08"]


def test_an_unknown_split_key_is_refused(seeded):
    db, company_id, owner = seeded
    po, schedule = _split_fixture(db, company_id, owner)

    with pytest.raises(AppException) as exc:
        ProjectSODraftService(db).build(po.id, schedule.id, split_by="by_vibes")
    assert exc.value.status_code == 422


# ----------------------------------------------------- the five hard stops


def _minimal(db, company_id, owner, **overrides):
    """One product, one phase, one line: the smallest thing that can carry a finding."""
    project = _project(db, company_id, owner)
    product = _product(db, "SRTWC8613-RL")
    quotation = _quotation(db, project, lines=[(product, "100", "392.85")])
    party = _party(db, company_id, customer=overrides.get("customer"))
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=overrides.get(
            "po_lines",
            [(1, product, "SRTWC8613-RL", "100", "UNIT", "392.85", "39285.00", False)],
        ),
        extracted_total=overrides.get("extracted_total"),
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    scheduled = overrides.get("scheduled", "100")
    if scheduled is not None:
        _cell(db, schedule, phase, product, scheduled)
    return project, product, po, schedule


@pytest.mark.parametrize(
    "code,overrides",
    [
        (
            "line_arithmetic",
            {
                "po_lines": [
                    (1, None, "SRTWC8613-RL", "100", "UNIT", "392.85", "39000.00", False)
                ]
            },
        ),
        ("schedule_short", {"scheduled": "90"}),
        ("schedule_over", {"scheduled": "110"}),
        (
            "unresolved_product",
            {
                "po_lines": [
                    (1, None, "SRTWC8613-RL", "100", "UNIT", "392.85", "39285.00", False)
                ],
                "scheduled": None,
            },
        ),
    ],
)
def test_each_hard_finding_blocks_the_publish(seeded, code, overrides):
    """Five hard stops, all arithmetic. A blocked draft cannot reach AutoCount."""
    db, company_id, owner = seeded
    if code == "line_arithmetic":
        # Keep the product resolved so the ONLY hard finding is the arithmetic one.
        project = _project(db, company_id, owner)
        product = _product(db, "SRTWC8613-RL")
        quotation = _quotation(db, project, lines=[(product, "100", "392.85")])
        party = _party(db, company_id)
        po = _po(db, project, party=party, quotation_version=quotation)
        po_version = _po_version(
            db,
            po,
            lines=[(1, product, "SRTWC8613-RL", "100", "UNIT", "392.85", "39000.00", False)],
        )
        schedule = _schedule(db, project, po, po_version=po_version)
        phase = _phase(
            db,
            project,
            area_group="TOWER",
            sequence=1,
            label="Level 2 & 7",
            delivery_date=date(2026, 7, 1),
            version=schedule,
        )
        _cell(db, schedule, phase, product, "100")
    else:
        _project_row, _product_row, po, schedule = _minimal(
            db, company_id, owner, **overrides
        )

    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order_ids = [row["id"] for row in result["data"]]
    codes = {finding.code for finding in _findings(db, order_ids)}
    assert code in codes, f"expected {code}, got {sorted(codes)}"
    assert all(row["status"] == "blocked" for row in result["data"])

    order = service.get_order(order_ids[0])
    with pytest.raises(AppException) as excinfo:
        service.publish(order, actor_user_id=owner)
    assert excinfo.value.status_code == 409
    assert code in str(excinfo.value.detail)


# ------------------------------------------------------- batch-level findings


def test_a_schedule_column_for_a_product_not_on_the_po_is_batch_level_only(seeded):
    """A finding that names no PO line belongs to no order: one row, keyed by the (PO,
    schedule version) pair, and zero rows on any of the drafted orders."""
    db, company_id, owner = seeded
    built = _two_area_build(db, company_id, owner)
    po, schedule = built["po"], built["schedule"]
    stray = _product(db, "SRT-NOT-ON-PO")
    _phase_stray = _phase(
        db,
        built["project"],
        area_group="TOWER",
        sequence=2,
        label="Level 9",
        delivery_date=date(2026, 8, 1),
        version=schedule,
    )
    _cell(db, schedule, _phase_stray, stray, "890")

    result = built["service"].build(po.id, schedule.id)
    order_ids = [row["id"] for row in result["data"]]

    assert not _findings(db, order_ids, code="schedule_over")
    batch = built["service"].list_schedule_findings(po.id, schedule.id)
    over = [row for row in batch if row.code == "schedule_over"]
    assert len(over) == 1
    assert over[0].project_sales_order_id is None
    # No trailing `.0000` in the sentence a person reads.
    assert "890 of" in over[0].detail
    assert "890.0000" not in over[0].detail


def test_a_po_lines_finding_attaches_only_to_the_order_that_drafted_it(seeded):
    """The TOWER order carries the short-scheduled product; the COMMON AREA order does
    not carry it at all, so its own findings must stay empty and its publish unblocked by
    a sibling's problem."""
    db, company_id, owner = seeded
    built = _two_area_build(db, company_id, owner)
    po, schedule = built["po"], built["schedule"]
    # Short-schedule the TOWER product: 6 of the 10 the PO ordered.
    built["tower_cell"].qty = Decimal("6")
    db.flush()

    result = built["service"].build(po.id, schedule.id)
    by_area = {row["area_group"]: row["id"] for row in result["data"]}
    tower_order = built["service"].get_order(by_area["TOWER"])
    common_order = built["service"].get_order(by_area["COMMON AREA"])

    assert any(f.code == "schedule_short" for f in built["service"].blocking_findings(tower_order))
    assert not built["service"].blocking_findings(common_order)

    # COMMON AREA publishes untouched by TOWER's problem.
    built["service"].publish(common_order, actor_user_id=owner)
    assert common_order.status == "published"
    with pytest.raises(AppException) as excinfo:
        built["service"].publish(tower_order, actor_user_id=owner)
    assert excinfo.value.status_code == 409


def test_a_batch_level_finding_blocks_no_order_and_can_be_acknowledged_on_its_own(seeded):
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(
        db, company_id, owner, extracted_total="40000.00"
    )
    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order = service.get_order(result["data"][0]["id"])

    assert not any(f.code == "total_mismatch" for f in service.blocking_findings(order))
    # It still publishes cleanly - nothing about this order is wrong.
    service.publish(order, actor_user_id=owner)
    assert order.status == "published"

    batch = service.list_schedule_findings(po.id, schedule.id)
    mismatch = next(row for row in batch if row.code == "total_mismatch")
    service.acknowledge_finding(
        mismatch.id,
        reason="Confirmed against the printed total, page 2.",
        actor_user_id=owner,
        permissions={OVERRIDE_PERMISSION},
    )
    db.refresh(mismatch)
    assert mismatch.acknowledged_by == owner


def test_a_batch_level_finding_is_replaced_not_duplicated_on_rebuild(seeded):
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(
        db, company_id, owner, extracted_total="40000.00"
    )
    service = ProjectSODraftService(db)
    service.build(po.id, schedule.id)
    service.build(po.id, schedule.id)

    batch = service.list_schedule_findings(po.id, schedule.id)
    assert len([row for row in batch if row.code == "total_mismatch"]) == 1


def test_a_hard_finding_needs_the_override_permission_to_acknowledge(seeded):
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(
        db, company_id, owner, scheduled="90"
    )
    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order = service.get_order(result["data"][0]["id"])
    hard = service.blocking_findings(order)[0]

    with pytest.raises(AppException) as excinfo:
        service.acknowledge_finding(
            hard.id,
            reason="Customer confirmed the balance follows in a second schedule.",
            actor_user_id=owner,
            permissions={"projects.projects.edit"},
        )
    assert excinfo.value.status_code == 403

    service.acknowledge_finding(
        hard.id,
        reason="Customer confirmed the balance follows in a second schedule.",
        actor_user_id=owner,
        permissions={"projects.projects.edit", OVERRIDE_PERMISSION},
    )
    db.refresh(hard)
    assert hard.acknowledged_by == owner
    assert hard.acknowledged_reason.startswith("Customer confirmed")
    assert not service.blocking_findings(order)
    body = service.publish(order, actor_user_id=owner)
    assert body["status"] == "published"


def test_an_acknowledged_reason_stays_on_the_sales_order_after_publish(seeded):
    """A warning is cleared with a reason, and the reason is still readable afterwards."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    product = _product(db, "SRTWC8613-RL")
    quotation = _quotation(db, project, lines=[(product, "100", "392.85")])
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    # The PO prices it at 400.00 where the quotation says 392.85: a warning, not a stop.
    po_version = _po_version(
        db, po, lines=[(1, product, "SRTWC8613-RL", "100", "UNIT", "400.00", "40000.00", False)]
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, product, "100")

    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order = service.get_order(result["data"][0]["id"])
    warn = _findings(db, [order.id], code="price_vs_quotation")
    assert len(warn) == 1
    assert order.status == "draft"
    # Priced from the quotation, with the difference put in front of a person.
    assert _lines(db, order.id)[0].unit_price == Decimal("392.85000")

    service.acknowledge_finding(
        warn[0].id,
        reason="Contractor agreed to the quoted price on the phone, 2 August.",
        actor_user_id=owner,
        permissions={"projects.projects.edit"},
    )
    db.refresh(order)
    assert order.status == "ready"
    body = service.publish(order, actor_user_id=owner)
    assert body["status"] == "published"
    still_there = _findings(db, [order.id], code="price_vs_quotation")[0]
    assert still_there.acknowledged_reason.startswith("Contractor agreed")


def test_the_credit_warning_reads_outstanding_plus_this_order_against_the_limit(seeded):
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco", credit_limit="50000.00")
    customer.ar_outstanding = Decimal("30000.00")
    customer.ar_as_of = date(2026, 8, 1)
    db.flush()
    _project_row, _product_row, po, schedule = _minimal(
        db, company_id, owner, customer=customer
    )

    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order_id = result["data"][0]["id"]
    exposure = _findings(db, [order_id], code="credit_exposure")
    assert len(exposure) == 1
    # 30,000 owed plus 39,285 ordered against a 50,000 limit.
    assert exposure[0].detail_json["exposure"] == "69285.00"
    assert "01 Aug 2026" in exposure[0].detail
    # A warning, so it does not stop the publish (contract section 5).
    service.publish(service.get_order(order_id), actor_user_id=owner)


# ---------------------------------------------------------------- the golden set


def test_the_drafted_totals_reproduce_the_real_sales_orders_to_the_cent(seeded):
    """The client's three sales orders add up to their quotation, exactly.

    Read off the committed documents:

        SO397450 TOWER          1,611,107.81
        SO397460 COMMON AREA       74,677.32
        SO376200 early subset     120,121.89
        ------------------------------------
        QT-004188 grand total   1,805,907.02

    and separately, on the PO itself: 1,810,640.62 printed, minus the pencilled
    cancellation of 4,733.60, is that same 1,805,907.02.

    SO376200 predates this PO, so this build does not re-draft it -- which is why the PO's
    printed total is larger than what the engine drafts here. Its 120,121.89 is carried in
    the assertion as the constant it is on the paper.
    """
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    wc = _product(db, "SRTWC8613-RL")
    connector = _product(db, "SRTWC8613-FC")
    tower_rest = _product(db, "SRTWB7055")
    common = _product(db, "SRTUB206-BI")
    cancelled = _product(db, "SRTFV1001")
    tower_remainder = Decimal("1611107.81") - Decimal("364171.95")  # 1,246,935.86
    quotation = _quotation(
        db,
        project,
        lines=[
            (wc, "927", "392.85"),
            (connector, "927", "0.00"),
            (tower_rest, "1", str(tower_remainder)),
            (common, "1", "74677.32"),
        ],
        total="1805907.02",
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation, po_number="HQ/26/01/121")
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, wc, "SRTWC8613-RL", "927", "SETS", "392.85", "364171.95", False),
            # The struck-through line, cancelled by hand months later (D11).
            (7, cancelled, "SRTFV1001", "16", "NOS", "295.85", "4733.60", True),
            (51, tower_rest, "SRTWB7055", "1", "UNIT", str(tower_remainder), str(tower_remainder), False),
            (52, common, "SRTUB206-BI", "1", "UNIT", "74677.32", "74677.32", False),
        ],
        extracted_total=str(
            Decimal("364171.95")
            + Decimal("4733.60")
            + tower_remainder
            + Decimal("74677.32")
        ),
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    tower_phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    common_phase = _phase(
        db,
        project,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 24),
        version=schedule,
    )
    _cell(db, schedule, tower_phase, wc, "927")
    _cell(db, schedule, tower_phase, tower_rest, "1")
    _cell(db, schedule, common_phase, common, "1")

    service = ProjectSODraftService(db)
    result = service.build(po.id, schedule.id)
    order_ids = [row["id"] for row in result["data"]]
    by_area = {row["area_group"]: row for row in result["data"]}

    # The printed total reconciles against our own sum of the lines, cancellation and all.
    assert not _findings(db, order_ids, code="total_mismatch")
    # The cancelled quantity never becomes a sales order line (AC-E3a).
    drafted_products = {
        line.product_id for order_id in order_ids for line in _lines(db, order_id)
    }
    assert cancelled.id not in drafted_products

    assert by_area["TOWER"]["total_amount"] == Decimal("1611107.81")
    assert by_area["COMMON AREA"]["total_amount"] == Decimal("74677.32")
    early_subset = Decimal("120121.89")
    assert (
        by_area["TOWER"]["total_amount"]
        + by_area["COMMON AREA"]["total_amount"]
        + early_subset
        == Decimal("1805907.02")
    )
    assert Decimal("1810640.62") - Decimal("4733.60") == Decimal("1805907.02")


def test_a_companion_line_names_the_parent_it_belongs_to(seeded):
    """Stated, not inferred: a companion detached from its set is unfulfillable."""
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    parent = _product(db, "SRTWC8613-RL")
    companion = _product(db, "SRTWC8613-FC")
    quotation = _quotation(
        db, project, lines=[(parent, "10", "392.85"), (companion, "10", "0.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db, po, lines=[(1, parent, "SRTWC8613-RL", "10", "SETS", "392.85", "3928.50", False)]
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, parent, "10")

    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    detail = service.serialize_detail(service.get_order(built["data"][0]["id"]))
    by_product = {row["product_id"]: row for row in detail["lines"]}
    parent_row = by_product[parent.id]
    companion_row = by_product[companion.id]

    assert parent_row["parent_line_id"] == parent_row["id"]
    assert parent_row["is_companion"] is False
    assert companion_row["parent_line_id"] == parent_row["id"]
    assert companion_row["is_companion"] is True


def test_the_schedule_version_picker_lists_every_version_on_the_po(seeded):
    """Per PURCHASE ORDER, not per schedule row: R2 arrived as its own schedule."""
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(db, company_id, owner)
    service = ProjectSODraftService(db)

    schedule_versions = service.list_schedule_versions(po.id)
    assert len(schedule_versions) == 1
    assert schedule_versions[0]["id"] == schedule.id
    assert schedule_versions[0]["po_version_no"] == 1


def test_the_list_row_carries_the_po_key_and_the_import_file_only_once_published(seeded):
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(db, company_id, owner)
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    row = built["data"][0]
    assert row["purchase_order_id"] == po.id
    assert row["import_file_url"] is None

    order = service.get_order(row["id"])
    service.publish(order, actor_user_id=owner)
    published_row = service.serialize_row(order)
    assert published_row["import_file_url"].endswith(f"/sales-orders/{order.id}/import-file")


# ------------------------------------------------------------------- regrouping


def test_a_manual_regroup_is_remembered_for_that_customer(seeded):
    """AC-F4b. The shape CS published IS the preference, read back off the orders."""
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco")
    party = _party(db, company_id, customer=customer)

    first = _project(db, company_id, owner)
    tower_product = _product(db, "SRTWC8613-RL")
    moved_product = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db, first, lines=[(tower_product, "10", "392.85"), (moved_product, "5", "295.85")]
    )
    po = _po(db, first, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "10", "UNIT", "392.85", "3928.50", False),
            (2, moved_product, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule = _schedule(db, first, po, po_version=po_version)
    tower = _phase(
        db,
        first,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    common = _phase(
        db,
        first,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 1),
        version=schedule,
    )
    _cell(db, schedule, tower, tower_product, "10")
    _cell(db, schedule, common, moved_product, "5")

    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    common_order = next(
        service.get_order(row["id"])
        for row in built["data"]
        if row["area_group"] == "COMMON AREA"
    )
    common_line = _lines(db, common_order.id)[0]

    # CS moves the common-area line into the tower order: two proposals become one.
    surviving = service.regroup(
        common_order, [{"area_group": "TOWER", "line_ids": [common_line.id]}]
    )
    assert len(surviving) == 1
    assert surviving[0].area_group == "TOWER"
    assert surviving[0].grouping_origin == "manual"
    assert len(_lines(db, surviving[0].id)) == 2

    # A second PO from the same customer, with the same area split proposed by the
    # schedule, must now propose what CS actually did.
    second = _project(db, company_id, owner)
    quotation2 = _quotation(
        db, second, lines=[(tower_product, "10", "392.85"), (moved_product, "5", "295.85")]
    )
    po2 = _po(db, second, party=party, quotation_version=quotation2)
    po_version2 = _po_version(
        db,
        po2,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "10", "UNIT", "392.85", "3928.50", False),
            (2, moved_product, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule2 = _schedule(db, second, po2, po_version=po_version2)
    tower2 = _phase(
        db,
        second,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule2,
    )
    common2 = _phase(
        db,
        second,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 1),
        version=schedule2,
    )
    _cell(db, schedule2, tower2, tower_product, "10")
    _cell(db, schedule2, common2, moved_product, "5")

    proposed = service.build(po2.id, schedule2.id)
    assert len(proposed["data"]) == 1
    assert proposed["data"][0]["area_group"] == "TOWER"
    assert proposed["data"][0]["grouping_origin"] == "learned"
    assert proposed["data"][0]["line_count"] == 2


def test_regrouping_refuses_to_leave_a_line_behind(seeded):
    db, company_id, owner = seeded
    _project_row, product, po, schedule = _minimal(db, company_id, owner)
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    order = service.get_order(built["data"][0]["id"])

    with pytest.raises(AppException) as excinfo:
        service.regroup(order, [{"area_group": "TOWER", "line_ids": []}])
    # An empty group is refused by the schema in the route; the service refuses the
    # partition that would orphan a line.
    assert excinfo.value.status_code == 422


# -------------------------------------------------------------- idempotent build


def test_rebuilding_replaces_its_own_drafts_and_leaves_published_orders_alone(seeded):
    db, company_id, owner = seeded
    project = _project(db, company_id, owner)
    tower_product = _product(db, "SRTWC8613-RL")
    common_product = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db, project, lines=[(tower_product, "10", "392.85"), (common_product, "5", "295.85")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "10", "UNIT", "392.85", "3928.50", False),
            (2, common_product, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    tower = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    common = _phase(
        db,
        project,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 1),
        version=schedule,
    )
    _cell(db, schedule, tower, tower_product, "10")
    _cell(db, schedule, common, common_product, "5")

    service = ProjectSODraftService(db)
    first = service.build(po.id, schedule.id)
    assert first["replaced_drafts"] == 0
    tower_order = next(
        service.get_order(row["id"])
        for row in first["data"]
        if row["area_group"] == "TOWER"
    )
    published_ref = tower_order.provisional_ref
    service.publish(tower_order, actor_user_id=owner)

    second = service.build(po.id, schedule.id)
    assert second["skipped_published"] == 1
    assert second["replaced_drafts"] == 1

    live = (
        db.query(ProjectSalesOrder)
        .filter(ProjectSalesOrder.purchase_order_id == po.id)
        .all()
    )
    refs = {row.provisional_ref: (row.status, row.area_group) for row in live}
    assert refs[published_ref] == ("published", "TOWER")
    # The published TOWER order is untouched AND not re-drafted: a second TOWER draft
    # beside a published one would be a duplicate commitment.
    assert sorted(status for status, _area in refs.values()) == ["published", "ready"]
    assert [area for status, area in refs.values() if status != "published"] == [
        "COMMON AREA"
    ]


def _two_area_build(db, company_id, owner, *, numbering_rule: bool = False):
    """One PO, one schedule, two area groups: the smallest thing a rebuild can replace."""
    if numbering_rule:
        _numbering_rule(db, company_id)
    project = _project(db, company_id, owner)
    tower_product = _product(db, "SRTWC8613-RL")
    common_product = _product(db, "SRTUB206-BI")
    quotation = _quotation(
        db, project, lines=[(tower_product, "10", "392.85"), (common_product, "5", "295.85")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, tower_product, "SRTWC8613-RL", "10", "UNIT", "392.85", "3928.50", False),
            (2, common_product, "SRTUB206-BI", "5", "NOS", "295.85", "1479.25", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    tower = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    common = _phase(
        db,
        project,
        area_group="COMMON AREA",
        sequence=13,
        label=None,
        delivery_date=date(2027, 6, 1),
        version=schedule,
    )
    tower_cell = _cell(db, schedule, tower, tower_product, "10")
    _cell(db, schedule, common, common_product, "5")

    return {
        "project": project,
        "po": po,
        "schedule": schedule,
        "tower_product": tower_product,
        "tower_cell": tower_cell,
        "service": ProjectSODraftService(db),
    }


def _rebuild_shape(db, po):
    """Every order this PO carries, as (ref, area group, lines) in reference order."""
    orders = (
        db.query(ProjectSalesOrder)
        .filter(ProjectSalesOrder.purchase_order_id == po.id)
        .order_by(ProjectSalesOrder.provisional_ref.asc())
        .all()
    )
    return [
        (
            order.provisional_ref,
            order.area_group,
            [
                (line.line_no, line.product_id, line.qty, line.delivery_date)
                for line in _lines(db, order.id)
            ],
        )
        for order in orders
    ]


def test_rebuilding_from_the_same_inputs_is_a_no_op(seeded):
    """Re-uploading the same PO and schedule must produce the SAME orders, not new ones.

    "Repeat upload is a no-op" is only true if the reference survives it: a rebuild that
    replaces PSO-000123 with PSO-000125 leaves CS looking at a number nobody wrote down,
    and every reference to the old one (an email, a printed worksheet) now points at a row
    that no longer exists. The rows are genuinely deleted and re-inserted -- that is what
    makes the rebuild correct when the schedule DID change -- so the provisional ref is
    carried across per area group instead.

    NO numbering rule is seeded, because production has none for this doc type: nothing
    seeds `project_sales_order`, so the real install takes ``_next_ref``'s fallback branch
    ("the highest existing PSO- reference plus one"). That is the path this test has to
    exercise. The fallback's own hazard -- the deleted drafts are gone from the count by
    the time a NEW group asks for a number -- is pinned separately below.
    """
    db, company_id, owner = seeded
    built = _two_area_build(db, company_id, owner)
    service, po, schedule = built["service"], built["po"], built["schedule"]

    service.build(po.id, schedule.id)
    before = _rebuild_shape(db, po)
    assert len(before) == 2

    second = service.build(po.id, schedule.id)
    assert second["replaced_drafts"] == 2
    assert second["skipped_published"] == 0

    assert _rebuild_shape(db, po) == before


def test_rebuilding_is_still_a_no_op_when_a_numbering_rule_is_configured(seeded):
    """The same guarantee on the other branch of ``_next_ref``.

    An install that DOES seed a `project_sales_order` numbering rule takes the counter
    instead of the fallback, and that counter only ever goes up: without the carry across
    the rebuild the second build would hand back PSO-000003 and PSO-000004.
    """
    db, company_id, owner = seeded
    built = _two_area_build(db, company_id, owner, numbering_rule=True)
    service, po, schedule = built["service"], built["po"], built["schedule"]

    service.build(po.id, schedule.id)
    before = _rebuild_shape(db, po)
    assert len(before) == 2

    service.build(po.id, schedule.id)

    assert _rebuild_shape(db, po) == before


def test_an_area_group_that_appears_in_a_rebuild_gets_its_own_reference(seeded):
    """A group added by a revised schedule must never be handed a carried reference.

    On the fallback path (which is production's, no numbering rule is seeded anywhere)
    ``_next_ref`` counts the highest PSO- reference in the table. The rebuild deletes the
    drafts it is replacing BEFORE minting, so those numbers are no longer in the table
    while it is still owing them back to their area groups -- and the groups are minted in
    alphabetical order, so a new group sorting FIRST is offered exactly the number the
    group after it is about to take back. Both rows then claim one reference, which the
    unique index on (company_id, provisional_ref) refuses in production.
    """
    db, company_id, owner = seeded
    built = _two_area_build(db, company_id, owner)
    service, po, schedule = built["service"], built["po"], built["schedule"]
    project, tower_product = built["project"], built["tower_product"]

    first = service.build(po.id, schedule.id)
    carried = {row["area_group"]: row["provisional_ref"] for row in first["data"]}
    assert set(carried) == {"TOWER", "COMMON AREA"}

    # The revised schedule moves 4 of the tower's 10 units into a new area group whose
    # name sorts before every group already drafted.
    built["tower_cell"].qty = Decimal("6")
    alpha = _phase(
        db,
        project,
        area_group="ALPHA BLOCK",
        sequence=2,
        label="Block A",
        delivery_date=date(2026, 8, 3),
        version=schedule,
    )
    _cell(db, schedule, alpha, tower_product, "4")
    db.flush()

    second = service.build(po.id, schedule.id)
    refs = {row["area_group"]: row["provisional_ref"] for row in second["data"]}

    assert set(refs) == {"ALPHA BLOCK", "TOWER", "COMMON AREA"}
    # The groups that were already drafted keep the number CS has written down.
    assert refs["TOWER"] == carried["TOWER"]
    assert refs["COMMON AREA"] == carried["COMMON AREA"]
    # And the new one is genuinely new, rather than a second claim on a carried number.
    assert len(set(refs.values())) == 3
    stored = {
        row.provisional_ref
        for row in db.query(ProjectSalesOrder)
        .filter(ProjectSalesOrder.purchase_order_id == po.id)
        .all()
    }
    assert len(stored) == 3


def test_a_published_order_cannot_be_edited_in_place(seeded):
    db, company_id, owner = seeded
    _project_row, _product_row, po, schedule = _minimal(db, company_id, owner)
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    order = service.get_order(built["data"][0]["id"])
    line = _lines(db, order.id)[0]
    service.publish(order, actor_user_id=owner)

    with pytest.raises(AppException) as excinfo:
        service.update_line(order, line.id, {"qty": Decimal("5")})
    assert excinfo.value.status_code == 409
    assert "amendment" in str(excinfo.value.detail).lower()


def test_the_import_file_carries_the_real_document_header_and_columns(seeded):
    """Stage 1 (D3). Header refs and columns are SO397450's, `Reserve Qty` included."""
    db, company_id, owner = seeded
    customer = _customer(db, "Buimaco")
    _project_row, _product_row, po, schedule = _minimal(
        db, company_id, owner, customer=customer
    )
    service = ProjectSODraftService(db)
    built = service.build(po.id, schedule.id)
    order = service.get_order(built["data"][0]["id"])
    published = service.publish(order, actor_user_id=owner)
    assert published["import_file_url"].endswith(f"/sales-orders/{order.id}/import-file")

    filename, body = service.import_file(order)
    assert filename == f"{order.provisional_ref}.csv"
    assert f"Your Ref No.,{po.po_number}" in body
    assert "Terms,*Net 60 days" in body
    assert "***TOWER***" in body
    assert "Item,Description,Reserve Qty,Qty,Delivery Date,UOM,U/Price,Disc.,Total" in body
    assert "SRTWC8613-RL" in body


# ------------------------------------------------------------ publish anyway
#
# Clearing 30 findings one at a time to publish an order the manager has already decided to
# publish is the constraint, not the check itself. The override is the SAME override
# `acknowledge_finding` carries -- the reason is recorded on every finding it waves through --
# only asked once instead of once per finding.


def _blocked_pair(db, company_id, owner):
    """A draft carrying TWO hard findings, so the bulk part of the override is real.

    Two products on one PO line each, one short-scheduled and one over-scheduled: both
    findings name a PO line, so both are the order's OWN (unlike `total_mismatch`, which
    moved to the batch level and no longer blocks any specific order - see
    `test_a_batch_level_finding_blocks_no_order`).
    """
    project = _project(db, company_id, owner)
    short_product = _product(db, "SRTWC8613-RL")
    over_product = _product(db, "SRTWC8613-FC")
    quotation = _quotation(
        db, project, lines=[(short_product, "100", "392.85"), (over_product, "50", "10.00")]
    )
    party = _party(db, company_id)
    po = _po(db, project, party=party, quotation_version=quotation)
    po_version = _po_version(
        db,
        po,
        lines=[
            (1, short_product, "SRTWC8613-RL", "100", "UNIT", "392.85", "39285.00", False),
            (2, over_product, "SRTWC8613-FC", "50", "UNIT", "10.00", "500.00", False),
        ],
    )
    schedule = _schedule(db, project, po, po_version=po_version)
    phase = _phase(
        db,
        project,
        area_group="TOWER",
        sequence=1,
        label="Level 2 & 7",
        delivery_date=date(2026, 7, 1),
        version=schedule,
    )
    _cell(db, schedule, phase, short_product, "90")  # schedule_short: 90 of 100
    _cell(db, schedule, phase, over_product, "60")  # schedule_over: 60 scheduled, PO orders 50

    service = ProjectSODraftService(db)
    order = service.get_order(service.build(po.id, schedule.id)["data"][0]["id"])
    assert len(service.blocking_findings(order)) >= 2
    return service, order


def test_publish_anyway_clears_every_hard_finding_and_publishes(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)
    expected = len(service.blocking_findings(order))

    body = service.publish(
        order,
        actor_user_id=owner,
        acknowledge_blocking=True,
        reason="Manager signed it off in the room, 18 August.",
        permissions={"projects.projects.edit", OVERRIDE_PERMISSION},
    )
    assert body["status"] == "published"
    assert body["acknowledged_findings"] == expected
    assert not service.blocking_findings(order)


def test_publish_anyway_records_the_reason_and_the_actor_on_each_finding(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)
    ids = [finding.id for finding in service.blocking_findings(order)]

    service.publish(
        order,
        actor_user_id=owner,
        acknowledge_blocking=True,
        reason="Manager signed it off in the room, 18 August.",
        permissions={OVERRIDE_PERMISSION},
    )
    for finding in _findings(db, [order.id]):
        if finding.id not in ids:
            continue
        assert finding.acknowledged_by == owner
        assert finding.acknowledged_at is not None
        # The record says it was waved through on the way out, not cleared one by one.
        assert "Published anyway" in finding.acknowledged_reason
        assert "Manager signed it off in the room" in finding.acknowledged_reason


def test_publish_without_the_flag_still_refuses(seeded):
    """The default path is byte-identical to what it was: a hard stop is a stop."""
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)

    with pytest.raises(AppException) as excinfo:
        service.publish(order, actor_user_id=owner)
    assert excinfo.value.status_code == 409
    assert len(service.blocking_findings(order)) >= 2


def test_publish_anyway_needs_a_reason(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)

    with pytest.raises(AppException) as excinfo:
        service.publish(
            order,
            actor_user_id=owner,
            acknowledge_blocking=True,
            reason="  ",
            permissions={OVERRIDE_PERMISSION},
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "finding_reason_required"
    # Nothing was acknowledged on the way to the refusal.
    assert all(f.acknowledged_at is None for f in service.blocking_findings(order))
    assert order.status != "published"


def test_publish_anyway_needs_the_override_permission(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)

    with pytest.raises(AppException) as excinfo:
        service.publish(
            order,
            actor_user_id=owner,
            acknowledge_blocking=True,
            reason="Manager signed it off in the room, 18 August.",
            permissions={"projects.projects.edit"},
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["code"] == "finding_override_denied"
    assert all(f.acknowledged_at is None for f in service.blocking_findings(order))
    assert order.status != "published"


# The three refusals the override does NOT touch (D28, AC-K3): they are states, not
# findings, and there is nothing to acknowledge. One test each because the seed helpers
# create the product by code, so two builds in one transaction collide.
_OVERRIDE = {
    "acknowledge_blocking": True,
    "reason": "Manager signed it off in the room, 18 August.",
    "permissions": {OVERRIDE_PERMISSION},
}


def test_publish_anyway_still_refuses_an_already_published_order(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)
    service.publish(order, actor_user_id=owner, **_OVERRIDE)

    with pytest.raises(AppException) as excinfo:
        service.publish(order, actor_user_id=owner, **_OVERRIDE)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "so_already_published"


def test_publish_anyway_still_refuses_an_order_awaiting_costing(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)
    order.status = "awaiting_costing"
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.publish(order, actor_user_id=owner, **_OVERRIDE)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "so_awaiting_costing"


def test_publish_anyway_still_refuses_an_order_with_no_lines(seeded):
    db, company_id, owner = seeded
    service, order = _blocked_pair(db, company_id, owner)
    for line in _lines(db, order.id):
        db.delete(line)
    db.flush()

    with pytest.raises(AppException) as excinfo:
        service.publish(order, actor_user_id=owner, **_OVERRIDE)
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "so_has_no_lines"
