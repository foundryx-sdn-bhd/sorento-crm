"""PR / SF printable PDF, and the PIC field.

Two problems this covers, both reported from production.

1. **Printing.** The only export was Excel. A long delivery address stretched one
   cell to an enormous width and the printed sheet was unusable - a data format
   doing a document's job. PR/SF now render a fixed-layout PDF through the same
   mechanism complaints and stock inquiries use, so "tidy" is the acceptance
   criterion, not a nice-to-have: a pathological address must WRAP inside its
   cell and never widen the table.

2. **PIC.** There was nowhere to record the person receiving the delivery, so
   staff typed them into the address:
   ``2, Lebuh Cecil, Ghaut, 10300 George Town, Pulau Pinang Contact: CONTACT Q
   (012-000 0502)``. The address became two facts in one column. PIC is a
   separate, optional, free-text field, printed on the PDF.

PR and SF share one table and one detail component, so every assertion here runs
against BOTH request_type values - a field that works for one and not the other
is the regression this guards.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.procurement import PurchaseRequestHeader
from tests._pg_fixture import blank_session

# The real address from the report, with the contact jammed on the end, plus a
# deliberately unbroken token: `pre-wrap` alone wraps on spaces, so a long
# no-space string is the case that actually escapes a fixed-width cell.
PATHOLOGICAL_ADDRESS = (
    "2, Lebuh Cecil, Ghaut, 10300 George Town, Pulau Pinang, Malaysia, "
    "Level 14 Menara Northam Jalan Sultan Ahmad Shah, Georgetown 10050 "
    "REFERENCE-NO-WITH-NO-SPACES-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)
PIC_VALUE = "CONTACT Q (012-000 0502)"

BOTH_TYPES = ["purchase_request", "sponsorship_form"]


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _make(db, *, request_type: str, **overrides) -> PurchaseRequestHeader:
    fields = dict(
        id=str(uuid.uuid4()),
        request_type=request_type,
        request_number=f"ZZT-{uuid.uuid4().hex[:6]}",
        customer_name="KEE LIN TRADING SDN BHD (PROJECT)",
        project_title="ECO SUMMIT AIRMAS SILK SPRING",
        delivery_address="2, Lebuh Cecil, Ghaut, 10300 George Town",
    )
    fields.update(overrides)  # a caller-supplied value replaces the default
    row = PurchaseRequestHeader(**fields)
    db.add(row)
    db.flush()
    return row


# --- PIC field -------------------------------------------------------------- #

@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_pic_persists_on_both_form_types(db, request_type):
    row = _make(db, request_type=request_type, pic=PIC_VALUE)
    db.commit()
    db.expire_all()

    fetched = db.query(PurchaseRequestHeader).filter_by(id=row.id).one()
    assert fetched.pic == PIC_VALUE


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_pic_is_optional(db, request_type):
    """Explicitly not mandatory - a form with no named contact must still save."""
    row = _make(db, request_type=request_type)
    db.commit()
    db.expire_all()

    assert db.query(PurchaseRequestHeader).filter_by(id=row.id).one().pic is None


# --- PDF -------------------------------------------------------------------- #

def _render(db, row):
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    return PurchaseRequestPDFService(db).render_pdf(str(row.id))


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_renders_a_pdf_for_both_form_types(db, request_type):
    row = _make(db, request_type=request_type, pic=PIC_VALUE)
    db.commit()

    pdf_bytes, filename = _render(db, row)

    assert pdf_bytes[:4] == b"%PDF", "not a PDF"
    assert len(pdf_bytes) > 800, "suspiciously small PDF"
    assert filename.endswith(".pdf")


def test_filename_names_the_document_type(db):
    """A folder of downloads must be sortable by what the document IS."""
    pr = _make(db, request_type="purchase_request", request_number="PR26-0332")
    sf = _make(db, request_type="sponsorship_form", request_number="PSSF26-0354")
    db.commit()

    assert _render(db, pr)[1] == "purchase-request-PR26-0332.pdf"
    assert _render(db, sf)[1] == "sponsorship-form-PSSF26-0354.pdf"


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_pic_is_printed(db, request_type):
    """The whole point of the field is that it reaches the printed page."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type, pic=PIC_VALUE)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "PIC" in html.upper()
    assert "CONTACT Q" in html


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_a_long_address_cannot_widen_the_table(db, request_type):
    """AC-9, the actual bug being fixed.

    The Excel export blew its column out to the width of the address. The PDF
    must not. Two mechanisms, because the two tables have different jobs: the
    items table is `table-layout: fixed`, while the label/value tables use auto
    layout (so a value sits beside its label) and depend on
    `overflow-wrap: anywhere` - which lowers a cell's min-content width, so an
    unbroken token wraps instead of forcing the table wider.
    """
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type, delivery_address=PATHOLOGICAL_ADDRESS)
    db.commit()
    svc = PurchaseRequestPDFService(db)
    html = svc._html(row)

    assert "table.items { width: 100%; table-layout: fixed" in html, (
        "the items table must stay fixed-layout"
    )
    assert "overflow-wrap: anywhere" in html, (
        "auto-layout label/value tables rely on this to keep an unbroken token "
        "from widening the table"
    )
    assert "table.fields td { padding" in html and "overflow-wrap: anywhere" in html
    # And it must actually render at that size.
    pdf_bytes, _ = svc.render_pdf(str(row.id))
    assert pdf_bytes[:4] == b"%PDF"


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_the_printed_form_is_on_sorento_letterhead(db, request_type):
    """The PDF replaces the Excel export, which opened with the company block.
    Without it the printed sheet is an anonymous list of fields, not a Sorento
    document someone can send to a customer."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "SORENTO SDN BHD" in html
    assert "Bandar Bukit Raja" in html
    assert "+603-3082 9778" in html


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_the_form_closes_with_a_signoff_block(db, request_type):
    """Requested by / Approved by / Date close the document in the Excel format
    it replaces. They print even when empty - the blank is where someone signs,
    so a dash would read as "not applicable"."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type)  # no requested_by, no approver
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "Requested by:" in html
    assert "Approved by:" in html
    assert "—" not in html, "an unfilled sign-off must be blank, not an em-dash"


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_one_date_format_per_document(db, request_type):
    """Every date on a page reads the same way.

    The Excel export wrote the header date as ``6/8/26`` and the approval date as
    ``6/8/2026``. Two formats on one sheet make a reader look for a distinction
    that does not exist.
    """
    import re
    from datetime import datetime

    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    stamp = datetime(2026, 8, 6, 10, 30)
    row = _make(db, request_type=request_type, submitted_at=stamp, approved_at=stamp)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    # Two-digit years are the specific inconsistency being ruled out.
    assert not re.search(r"\b\d{1,2}/\d{1,2}/\d{2}\b", html), "a two-digit year slipped in"
    assert html.count("6/8/2026" if request_type == "purchase_request" else "6-Aug-2026") >= 2


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_a_paired_label_keeps_its_own_value(db, request_type):
    """"Date:" and "Expected date to receive PO:" used to share a table column,
    so the column stretched to the longer label and the short one's value ended
    up stranded on the far side of it. Keeping the pair in a single cell is what
    guarantees a value sits beside the label it belongs to."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert '<td class="pair"' in html
    assert '<span class="lbl">Date:</span>' in html, (
        "the right-hand label must live in the same cell as its value"
    )


def test_sales_type_prints_its_label_not_its_code(db):
    """`sales_type` stores `cash_sales`; the screen shows "Cash Sales" through
    LookupBoundLabel. A printed copy showing the code would disagree with the
    screen it was printed from."""
    import uuid as _uuid

    from app.models.lookup import LookupBinding, LookupOption, LookupSet
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    lset = LookupSet(id=str(_uuid.uuid4()), set_key=f"zzt_sales_{_uuid.uuid4().hex[:6]}",
                     name="ZZT Sales Type", is_active=True)
    db.add(lset)
    db.flush()
    db.add(LookupOption(id=str(_uuid.uuid4()), set_id=lset.id,
                        value="cash_sales", label="Cash Sales", is_active=True))
    db.add(LookupBinding(id=str(_uuid.uuid4()), set_id=lset.id,
                         table_name="purchase_requests", column_name="sales_type"))
    row = _make(db, request_type="purchase_request", sales_type="cash_sales")
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "Sales Type:" in html
    assert "Cash Sales" in html
    assert "cash_sales" not in html, "the raw code reached the printed page"


def test_an_unbound_sales_type_still_prints_something(db):
    """No binding, or a value with no matching option: print the stored value.
    A code on the page beats a blank row that looks like nobody filled it in."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type="purchase_request", sales_type="mystery_type")
    db.commit()

    assert "mystery_type" in PurchaseRequestPDFService(db)._html(row)


def test_the_sponsorship_form_has_no_sales_type_row(db):
    """The field is Purchase Request only - an empty row on the SF would imply a
    sales type nobody filled in."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type="sponsorship_form", sales_type="cash_sales")
    db.commit()

    assert "Sales Type:" not in PurchaseRequestPDFService(db)._html(row)


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_labels_are_bold(db, request_type):
    """On a dense form with no column rules, weight is the only thing separating
    a label from the value sitting right beside it."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "td.lbl, table.fields td.pair span.lbl { font-weight: bold; }" in html, (
        "both the left-hand labels and the paired right-hand label must be bold"
    )


def test_only_the_sponsorship_form_is_priced(db):
    """Column sets differ by type, exactly as the two Excel formats do: a
    Sponsorship Form carries U/P, Total and a Grand Total; a Purchase Request
    is quantities only."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    sf = _make(db, request_type="sponsorship_form")
    pr = _make(db, request_type="purchase_request")
    db.commit()
    svc = PurchaseRequestPDFService(db)

    sf_html = svc._html(sf)
    assert "Grand Total:" in sf_html and "U/P" in sf_html

    pr_html = svc._html(pr)
    assert "Grand Total:" not in pr_html
    assert "U/P" not in pr_html


def test_the_sponsorship_form_prints_its_delivery_address(db):
    """The address is where staff were hiding the PIC, so it has to be legible on
    the printed sponsorship form (the Purchase Request format has no address
    row - matching the Excel export it replaces)."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type="sponsorship_form", delivery_address=PATHOLOGICAL_ADDRESS)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row)
    assert "Delivery Address:" in html
    assert "Lebuh Cecil" in html


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_internal_only_fields_are_not_printed(db, request_type):
    """Same rule the complaint / stock-inquiry PDFs follow: a printed copy handed
    to a driver must not carry SLA tiers, assignees or audit trail."""
    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    row = _make(db, request_type=request_type, pic=PIC_VALUE)
    db.commit()

    html = PurchaseRequestPDFService(db)._html(row).lower()
    for leaked in ("sla", "assignee", "escalat", "audit", "handling lock"):
        assert leaked not in html, f"internal field {leaked!r} reached the printed copy"


def test_a_form_with_no_line_items_still_renders(db):
    """An empty section gets an explicit empty state, never a crash or a blank."""
    row = _make(db, request_type="purchase_request")
    db.commit()

    pdf_bytes, _ = _render(db, row)
    assert pdf_bytes[:4] == b"%PDF"


def test_a_missing_record_is_a_404_not_a_500(db):
    from fastapi import HTTPException

    from app.services.purchase_request_pdf_service import PurchaseRequestPDFService

    with pytest.raises((HTTPException, Exception)) as exc:
        PurchaseRequestPDFService(db).render_pdf(str(uuid.uuid4()))
    assert "404" in str(exc.value) or "not found" in str(exc.value).lower()


# --- AI extraction ---------------------------------------------------------- #

@pytest.mark.parametrize(
    "form_key", ["portal.purchase_request", "portal.sponsorship_form"]
)
def test_pic_is_an_extractable_field_on_both_portal_forms(form_key):
    """The portal pre-fills from an uploaded document; PIC has to be in that set
    or the field the user was told about silently never populates."""
    from app.services.ai_extract.form_schema_registry import get_form_schema

    names = {f.name for f in get_form_schema(form_key)}
    assert "pic" in names, f"{form_key} cannot extract PIC"


@pytest.mark.parametrize(
    "form_key", ["portal.purchase_request", "portal.sponsorship_form"]
)
def test_the_pic_prompt_disambiguates_from_the_salesperson(form_key):
    """The model's failure mode here is grabbing whichever human name it sees
    first. customer_name needed the same steer and its note is long for exactly
    that reason."""
    from app.services.ai_extract.form_schema_registry import get_form_schema

    spec = next(f for f in get_form_schema(form_key) if f.name == "pic")
    note = (spec.note or "").lower()
    assert note, "PIC has no note, so nothing stops the model taking the salesperson"
    assert "salesperson" in note or "sales person" in note


# --- the portal allowlist (this one actually shipped broken) ---------------- #

@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_pic_survives_a_portal_submission(kind):
    """PIC was on the portal form, the user filled it, and it silently vanished.

    ``PortalService`` applies an explicit per-kind allowlist of editable fields;
    anything absent is dropped without an error, so the field rendered, accepted
    input, and saved NULL. Adding a column and a form widget is not enough - the
    allowlist is a third place that has to know about it.
    """
    from app.services.portal_service import PortalService

    # instance method, but the allowlist depends only on `kind`
    fields = PortalService._editable_fields(None, kind)  # type: ignore[arg-type]
    assert "pic" in fields, (
        f"pic missing from the {kind} portal allowlist - the field renders, accepts "
        "input, and is then silently dropped on submit"
    )


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_pic_is_echoed_back_when_a_draft_is_reopened(db, request_type):
    """The write allowlist and the read serializer are two SEPARATE lists.

    With `pic` in the allowlist only, a portal draft saved the value to the
    column and then reopened with the box blank - the value was in the database
    the whole time, so it read as "save did nothing". `_serialize_request_detail`
    is a manual dict builder, so a new column reaches the portal only when it is
    added there by hand.
    """
    from app.services.portal_service import PortalService

    row = _make(db, request_type=request_type, pic=PIC_VALUE)
    db.commit()

    detail = PortalService(db)._serialize_request_detail(row)
    assert "pic" in detail, "the portal never sends pic back, so a saved draft reopens blank"
    assert detail["pic"] == PIC_VALUE


@pytest.mark.parametrize("request_type", BOTH_TYPES)
def test_every_editable_field_is_sent_back_to_the_portal(db, request_type):
    """The general form of the PIC bug, which has now bitten twice (`pic`, then
    `sales_type`).

    A portal field lives in two hand-maintained lists: ``_editable_fields``
    decides what a submit is allowed to write, ``_serialize_request_detail``
    decides what a reopened draft is told. A field in the first but not the
    second saves correctly and then renders blank - which reads as "the save did
    nothing" even though the column holds the value. Nothing errors, so only an
    assertion over the whole list catches it.
    """
    from app.services.portal_service import PortalService

    row = _make(db, request_type=request_type)
    db.commit()
    svc = PortalService(db)

    returned = set(svc._serialize_request_detail(row))
    writable = set(svc._editable_fields(request_type))
    missing = sorted(writable - returned)
    assert not missing, (
        f"{request_type}: {missing} can be written from the portal but are never "
        "returned, so a saved draft reopens with those boxes blank"
    )
