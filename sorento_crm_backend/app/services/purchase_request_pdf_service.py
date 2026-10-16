"""Render a Purchase Request / Sponsorship Form PDF via WeasyPrint.

Decoupled from the request path: called by the RQ task
``generate_purchase_request_pdf``. Mirrors ``stock_inquiry_pdf_service`` and
``complaint_pdf_service`` - same helpers, same "printed copy agrees with the
screen" rule, same exclusion of internal-only fields (SLA tier/assignee, handling
lock, audit trail) because the printed sheet gets handed to a driver.

Why this exists at all: the only export was Excel, and a long delivery address
stretched one cell to an enormous width, making the printed sheet unusable. A
spreadsheet auto-sizes to its content; a document must not. The items table
pins that with ``table-layout: fixed``; the label/value tables use auto layout
(so a value sits beside its label instead of at a fixed offset) and rely on
``overflow-wrap: anywhere``, which lowers a cell's min-content width so an
unbroken token wraps instead of widening the table.

One table serves both request types (they share ``purchase_requests``), so every
label decision here has to read correctly for a Purchase Request AND a
Sponsorship Form; rows that belong to only one type are emitted only when set.

Two revision modes (round 6, PLAN-portal-submission-revisions 6.3/6.4):

* ``revision_id`` - print ONE version. Same letterhead, fields, items table and
  sign-off, every value read from that version's stored snapshot (header fields
  AND line items).
* ``include_revisions`` - the current form first, then every EARLIER version
  newest first, each on its own page. The newest lineage entry is skipped: it is
  the version the current form shows, so printing it too gave the reader the same
  form twice. Its label, submitter and reason ride on the current form instead.

The request TYPE (purchase request vs sponsorship form) is always read from the
live header, never from a snapshot: a form does not change type, and the type
decides the document's whole shape.
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.procurement import PurchaseRequestHeader
from app.services.document_number import display_document_number
from app.services.pdf_render import (
    PDFRenderingUnavailable,
    embedded_images,
    in_malaysia,
    names_list_html,
    non_image_names,
    photos_section_html,
    render_html,
)
from app.services.pdf_revision_support import (
    appended_revision_entries,
    export_filename,
    filename_with_revision,
    find_revision_entry,
    is_superseded,
    latest_revision_entry,
    revision_attachment_sections,
    revision_document_number,
    revision_entries,
    revision_heading,
    revision_reason,
    snapshot_date,
    snapshot_datetime,
    snapshot_decimal,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PurchaseRequestPDFService",
    "PDFRenderingUnavailable",
    "filename_stem",
]

_SPONSORSHIP = "sponsorship_form"


def filename_stem(request_type: Any) -> str:
    """What the document IS, in front of the number, on every export of it.

    Shared with the route so the pending download row and the finished artifact
    cannot be named differently, and so a folder of downloads sorts into
    purchase requests and sponsorship forms.
    """
    return "sponsorship-form" if str(request_type or "") == _SPONSORSHIP else "purchase-request"

# The printed form is Sorento letterhead. Same three lines the Excel export
# writes (``SORENTO_HEADER`` in purchase-request-excel-export.ts) - the PDF
# replaces that export, so it has to carry the same company block or the printed
# copy stops looking like a company document.
_LETTERHEAD = (
    "SORENTO SDN BHD",
    "No 5, Jalan Astana 2/KU2, Bandar Bukit Raja, 41050 Klang, Selangor, Malaysia.",
    "Tel: +603-3082 9778, Fax: +603-30829278.",
)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

def _blank(value) -> str:
    """Empty renders as empty, not as a dash.

    The Excel export leaves an unfilled cell blank and the printed form is meant
    to be signed off by hand, so a row like ``Approved by:`` has to print as an
    empty space someone can write in - a "-" reads as "not applicable".
    """
    if value is None:
        return ""
    # Timestamps are stored naive UTC; the page must read as the Malaysia wall
    # clock every other surface shows (a date column is left alone).
    value = in_malaysia(value)
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return escape(str(value).strip())


def _date_cell(value) -> str:
    """``6/8/2026`` - every date on a Purchase Request.

    The Excel export wrote the header date as ``6/8/26`` and the rest as
    ``6/8/2026``. Two formats on one page invite the reader to wonder what the
    difference means, and there isn't one, so the document uses a single format
    throughout.

    A snapshot stores its dates as ISO text, so the value is coerced first - a
    revision must print its dates in the same format the live form does - and a
    timestamp is moved to Malaysia time, which is what every other surface shows.
    """
    value = in_malaysia(snapshot_date(value))
    if not isinstance(value, (date, datetime)):
        return _blank(value)
    return f"{value.day}/{value.month}/{value.year}"


def _date_long(value) -> str:
    """``6-Aug-2026`` - the Sponsorship Form date format."""
    value = in_malaysia(snapshot_date(value))
    if not isinstance(value, (date, datetime)):
        return _blank(value)
    return f"{value.day}-{_MONTHS[value.month - 1]}-{value.year}"


def _snapshot_lines(snapshot: dict) -> list:
    """A revision's ``products`` as line-shaped objects.

    The items table reads attributes off a line, so the snapshot dicts are
    presented the same way rather than forking the table renderer. Numerics come
    back as Decimals so a snapshotted quantity or price prints exactly as the
    live row's does. The stored order IS the order (a snapshot has no
    ``sort_order``), and the sort in ``_lines_html`` is stable, so it survives.
    """
    lines = []
    for item in snapshot.get("products") or []:
        item = item or {}
        lines.append(
            SimpleNamespace(
                item_code=item.get("item_code"),
                quantity=snapshot_decimal(item.get("quantity")),
                unit_price=snapshot_decimal(item.get("unit_price")),
                total=snapshot_decimal(item.get("total")),
                remark=item.get("remark"),
                sort_order=None,
            )
        )
    return lines


def _field(label: str, value: str, *, label2: str = "", value2: str = "") -> str:
    """One label/value line, optionally with a second pair on the same line
    (``Sponsorship form number: X    Date: Y``), mirroring the Excel columns."""
    if label2:
        # The right-hand label and its value share ONE cell. As separate columns
        # the label column stretched to its widest member - "Expected date to
        # receive PO:" - so the short "Date:" on another row was left with its
        # value stranded at the far side of that column. In one cell the value
        # always follows its own label, whatever else is on the page.
        return (
            f'<tr><td class="lbl">{escape(label)}</td>'
            f'<td class="val tight">{value}</td>'
            f'<td class="pair" colspan="2">'
            f'<span class="lbl">{escape(label2)}</span>{value2}</td></tr>'
        )
    # No second pair: let the value run to the right edge instead of wrapping
    # inside a quarter-width column. A delivery address is the reason.
    return (
        f'<tr><td class="lbl">{escape(label)}</td>'
        f'<td class="val" colspan="3">{value}</td></tr>'
    )


class PurchaseRequestPDFService:
    def __init__(self, db: Session):
        self.db = db

    def _is_sponsorship(self, req: PurchaseRequestHeader) -> bool:
        return (getattr(req, "request_type", None) or "") == _SPONSORSHIP

    def build_filename(self, req: PurchaseRequestHeader) -> str:
        """Name the file after what the document IS, so a folder of downloads
        sorts into purchase requests and sponsorship forms."""
        # The filename carries the revision too, so two revisions of the same
        # form do not land in a downloads folder as one overwritten file (UAC N5).
        return export_filename(
            self._filename_stem(req), display_document_number(req) or str(req.id)
        )

    def _filename_stem(self, req: PurchaseRequestHeader) -> str:
        return filename_stem(getattr(req, "request_type", None))

    def _load(self, request_id: str) -> PurchaseRequestHeader:
        req = (
            self.db.query(PurchaseRequestHeader)
            .filter(PurchaseRequestHeader.id == str(request_id))
            .first()
        )
        if req is None:
            from app.services.error_handler import handle_not_found

            raise handle_not_found("Purchase Request", request_id)
        return req

    def _lookup_label(self, column: str, value: Optional[str]) -> Optional[str]:
        """Resolve a lookup-bound code to the label the screen shows.

        ``sales_type`` stores ``cash_sales``; the detail page renders "Cash Sales"
        through ``LookupBoundLabel``. Printing the raw code would make the paper
        disagree with the screen. Falls back to the stored value if the binding
        or option is missing - a code on the page beats a blank.
        """
        code = (value or "").strip()
        if not code:
            return None
        try:
            from app.models.lookup import LookupBinding, LookupOption

            binding = (
                self.db.query(LookupBinding)
                .filter(
                    LookupBinding.table_name == "purchase_requests",
                    LookupBinding.column_name == column,
                )
                .first()
            )
            if binding is None:
                return code
            option = (
                self.db.query(LookupOption)
                .filter(
                    LookupOption.set_id == binding.set_id,
                    func.lower(LookupOption.value) == code.lower(),
                )
                .first()
            )
            return (option.label if option else None) or code
        except Exception:  # pragma: no cover - never fail the PDF on a lookup
            logger.warning("purchase request PDF: lookup label failed", exc_info=True)
            return code

    def _attachment_links(self, req: PurchaseRequestHeader) -> list:
        try:
            from app.services.entity_attachment_service import EntityAttachmentService

            return (
                EntityAttachmentService(self.db).list_links("purchase_request", str(req.id))
                or []
            )
        except Exception:  # pragma: no cover - never break the PDF on link lookup
            logger.warning(
                "purchase request PDF: failed to list entity attachment links", exc_info=True
            )
            return []

    def _lines_html(
        self, req: PurchaseRequestHeader, lines: Optional[list] = None
    ) -> tuple[str, str]:
        """Return (table_html, grand_total_text).

        Column sets differ by type, exactly as the Excel export does: a
        Sponsorship Form prices its items (U/P, Total, Grand Total), a Purchase
        Request does not.

        ``lines`` defaults to the live rows; a revision passes its snapshotted
        ones so the printed table is the version's, not today's.
        """
        source = getattr(req, "lines", None) if lines is None else lines
        lines = sorted(
            list(source or []),
            key=lambda ln: (getattr(ln, "sort_order", None) or 0),
        )
        sponsorship = self._is_sponsorship(req)

        if sponsorship:
            cols = '<col style="width:8%"/><col style="width:24%"/><col style="width:9%"/>' \
                   '<col style="width:12%"/><col style="width:13%"/><col style="width:34%"/>'
            head = ("<tr><th>NO.</th><th>Item Code</th><th>Qty</th><th>U/P</th>"
                    "<th>Total</th><th>Remark</th></tr>")
            span_before_total = 3
        else:
            cols = '<col style="width:8%"/><col style="width:30%"/><col style="width:12%"/>' \
                   '<col style="width:50%"/>'
            head = "<tr><th>#</th><th>Item Code</th><th>Qty</th><th>Remark</th></tr>"
            span_before_total = 0

        grand = Decimal("0")
        body: list[str] = []
        for i, ln in enumerate(lines, start=1):
            qty = getattr(ln, "quantity", None)
            cells = [
                f"<td>{i}</td>",
                f'<td>{_blank(getattr(ln, "item_code", None))}</td>',
                f"<td>{_blank(qty)}</td>",
            ]
            if sponsorship:
                unit = getattr(ln, "unit_price", None) or Decimal("0")
                total = getattr(ln, "total", None)
                if total is None:
                    total = (Decimal(str(qty or 0)) * Decimal(str(unit)))
                grand += Decimal(str(total))
                cells.append(f'<td class="num">{Decimal(str(unit)):,.2f}</td>')
                cells.append(f'<td class="num">{Decimal(str(total)):,.2f}</td>')
            cells.append(f'<td>{_blank(getattr(ln, "remark", None))}</td>')
            body.append(f"<tr>{''.join(cells)}</tr>")

        if not body:
            span = 6 if sponsorship else 4
            body.append(
                f'<tr><td colspan="{span}" class="empty">No items on this request.</td></tr>'
            )

        if sponsorship:
            body.append(
                f'<tr class="grand"><td colspan="{span_before_total}"></td>'
                f'<td class="lbl">Grand Total:</td>'
                f'<td class="num">{grand:,.2f}</td><td></td></tr>'
            )

        table = f'<table class="items"><colgroup>{cols}</colgroup>{head}{"".join(body)}</table>'
        return table, f"{grand:,.2f}"

    def _reader(
        self, req: PurchaseRequestHeader, snapshot: Optional[dict] = None
    ) -> Callable[[str], Any]:
        """Field reader: the live row, or a version's stored snapshot.

        One function decides where a value comes from, so the layout below cannot
        accidentally mix a snapshotted field with a live one. A field the
        snapshot never carried (an approval cleared by the revision, a
        ``*_text`` sibling) reads as empty, which is what the version had.
        """
        if snapshot is None:
            return lambda name: getattr(req, name, None)
        return lambda name: snapshot.get(name)

    def _fields_html(
        self,
        req: PurchaseRequestHeader,
        get: Callable[[str], Any],
        *,
        number: Optional[str],
        submitted: Any,
    ) -> str:
        """The label/value block, in the order the Excel document uses."""
        sponsorship = self._is_sponsorship(req)

        rows: list[str] = []
        if sponsorship:
            rows.append(
                _field(
                    "Sponsorship form number:", _blank(number),
                    label2="Date:", value2=_date_long(submitted),
                )
            )
            rows.append('<tr class="spacer"><td colspan="4"></td></tr>')
            subject = get("sponsor_subject_other") or get("sponsor_subject")
            tpv = get("total_project_value_text") or get("total_project_value")
            rows += [
                _field("Customer Name:", _blank(get("customer_name"))),
                # PIC sits directly under Customer Name, on screen and in print, so a
                # reader finds the human where they expect them - and stops anyone
                # appending the contact to the address again.
                _field("PIC:", _blank(get("pic"))),
                _field("Delivery Address:", _blank(get("delivery_address"))),
                _field("Project Title:", _blank(get("project_title"))),
                _field("Total Project Value:", _blank(tpv)),
                _field("Sponsor Subject:", _blank(subject)),
                # SF-SALES-TYPE: the SF carries a sales type too, printed by label as on the PR.
                _field(
                    "Sales Type:",
                    _blank(self._lookup_label("sales_type", get("sales_type"))),
                ),
                _field("Date of Delivery:", _date_long(get("expected_delivery_date"))),
            ]
        else:
            rows.append(
                _field(
                    "Purchase request number:", _blank(number),
                    label2="Date:", value2=_date_cell(submitted),
                )
            )
            rows.append('<tr class="spacer"><td colspan="4"></td></tr>')
            expected_po = get("expected_po_date_text") or _date_cell(get("expected_po_date"))
            rows += [
                _field("Customer Name:", _blank(get("customer_name"))),
                _field("PIC:", _blank(get("pic"))),
                _field("Project Title:", _blank(get("project_title"))),
                _field("Purpose:", _blank(get("purpose"))),
                # Lookup-bound: print the label the screen shows, not the code. The
                # resolver works on a snapshotted code exactly as on a live one.
                _field(
                    "Sales Type:",
                    _blank(self._lookup_label("sales_type", get("sales_type"))),
                ),
                _field(
                    "Expected Date of Delivery:",
                    _date_cell(get("expected_delivery_date")),
                    label2="Expected date to receive PO:",
                    value2=_blank(expected_po),
                ),
            ]
        return f'<table class="fields">{"".join(rows)}</table>'

    def _signoff_html(
        self, req: PurchaseRequestHeader, get: Callable[[str], Any]
    ) -> str:
        """Requested by / Approved by + Date - the sign-off block that closes the
        document. Printed even when unfilled: the blank IS the place to sign."""
        sponsorship = self._is_sponsorship(req)
        fmt = _date_long if sponsorship else _date_cell
        return (
            '<table class="fields signoff">'
            + _field("Requested by:", _blank(get("requested_by")))
            + _field(
                "Approved by:", _blank(get("approved_by")),
                label2="Date:", value2=fmt(get("approved_at")),
            )
            + "</table>"
        )

    def _appendix_html(self, req: PurchaseRequestHeader) -> str:
        """Attachments print only when there are some, so an ordinary form is the
        one-page document the Excel export produced."""
        links = self._attachment_links(req)
        if not links:
            return ""
        images = embedded_images(links, context="purchase request PDF")
        others = non_image_names(links)
        parts = []
        if images:
            parts.append("<h2>Photos</h2>" + photos_section_html(images))
        if others:
            parts.append("<h2>Other Attachments</h2>" + names_list_html(others))
        return "".join(parts)

    def _revision_appendix_html(self, entry: dict) -> str:
        """The same appendix as the live form, for ONE version's files.

        Unlike the live form's, this block always renders: on a historical page
        "Attachments: None." is information (the version carried none), whereas on
        the current form silence keeps an ordinary request to one page.
        """
        images, names = revision_attachment_sections(
            self.db, entry, context="purchase request PDF"
        )
        if not images and not names:
            return "<h2>Attachments</h2>" + names_list_html([])
        parts = []
        if images:
            parts.append("<h2>Photos</h2>" + photos_section_html(images))
        if names:
            parts.append("<h2>Other Attachments</h2>" + names_list_html(names))
        return "".join(parts)

    def _doc_title(self, req: PurchaseRequestHeader) -> str:
        return (
            "Project Sales Sponsorship Form"
            if self._is_sponsorship(req)
            else "Purchase Request"
        )

    def _letterhead_html(self) -> str:
        return "".join(
            f'<div class="{"co" if i == 0 else "co-sub"}">{escape(line)}</div>'
            for i, line in enumerate(_LETTERHEAD)
        )

    def _current_page(
        self, req: PurchaseRequestHeader, latest: Optional[dict] = None
    ) -> str:
        """The form as it stands now - the document this export has always been.

        ``latest`` is the newest lineage entry, passed only by the
        include-revisions export. That entry gets no page of its own (it IS this
        form), so what it uniquely carried - which revision this is, who sent it,
        when, and why - reads here instead, in the same wording and the same two
        lines a revision page uses.
        """
        get = self._reader(req)
        items_html, _ = self._lines_html(req)
        submitted = getattr(req, "submitted_at", None) or getattr(req, "request_date", None)
        version_html = ""
        if latest:
            reason = revision_reason(latest)
            version_html = (
                f'<div class="rev-heading">'
                f"{escape(revision_heading(latest, superseded=False))}</div>"
            ) + (
                f'<div class="rev-reason">Reason: {escape(reason)}</div>' if reason else ""
            )

        return f"""  <div class="letterhead">{self._letterhead_html()}</div>
  <div class="doc-title">{escape(self._doc_title(req))}</div>
  {version_html}

  {self._fields_html(req, get, number=display_document_number(req) or None, submitted=submitted)}

  {items_html}

  {self._signoff_html(req, get)}

  {self._appendix_html(req)}
"""

    def _revision_page(
        self,
        req: PurchaseRequestHeader,
        entry: dict,
        *,
        superseded: bool,
        page_break: bool = False,
    ) -> str:
        """One superseded version: same letterhead, fields, items and sign-off, every
        value read from that version's snapshot.

        The Date beside the number is the date THIS version was submitted, not the
        record's - on a revision page that is the date the reader means (the live
        ``submitted_at`` is deliberately not re-stamped by a revision).
        Photos are embedded from THAT version's own attachment set, through the
        same helpers the current form's appendix uses, and everything else is
        listed by name.
        """
        snapshot = entry.get("snapshot") or {}
        get = self._reader(req, snapshot)
        items_html, _ = self._lines_html(req, _snapshot_lines(snapshot))
        heading = escape(revision_heading(entry, superseded=superseded))
        reason = revision_reason(entry)
        reason_html = (
            f'<div class="rev-reason">Reason: {escape(reason)}</div>' if reason else ""
        )
        attachments_html = self._revision_appendix_html(entry)
        break_style = ' style="page-break-before: always;"' if page_break else ""

        return f"""  <div class="page"{break_style}>
  <div class="letterhead">{self._letterhead_html()}</div>
  <div class="doc-title">{escape(self._doc_title(req))}</div>
  <div class="rev-heading">{heading}</div>
  {reason_html}

  {self._fields_html(
      req,
      get,
      number=revision_document_number(entry, "request_number"),
      submitted=snapshot_datetime(entry.get("submitted_at")),
  )}

  {items_html}

  {self._signoff_html(req, get)}

  {attachments_html}
  </div>
"""

    def _document(self, body: str) -> str:
        return f"""<!doctype html><html><head><meta charset="utf-8"/>
<style>
  @page {{ size: A4; margin: 14mm 14mm; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #000; font-size: 11px; }}
  .co {{ font-size: 15px; font-weight: bold; letter-spacing: .02em; }}
  .co-sub {{ font-size: 10.5px; }}
  .letterhead {{ padding-bottom: 10px; }}
  .doc-title {{ font-size: 13px; font-weight: bold; margin: 14px 0 12px; }}
  /* table-layout:fixed + the break rules are what stop a long delivery address
     widening a column and wrecking the print - the exact failure the Excel
     export had. Without `fixed` the cell grows to its content. */
  /* `auto` layout, not `fixed`: a label column sized to its widest label puts
     every value right next to its label instead of at a fixed 22% - the
     sign-off block used to derive different widths because its first row spans
     columns, leaving "Tester" stranded far from "Requested by:".
     `overflow-wrap: anywhere` shrinks a cell's min-content width, so an
     unbroken delivery address still wraps under auto layout rather than
     widening the table - the guarantee is kept, by a different mechanism. */
  table.fields {{ width: 100%; table-layout: auto; border-collapse: collapse; }}
  table.fields td {{ padding: 2.5px 14px 2.5px 0; vertical-align: top;
                     white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }}
  /* Labels bold: on a dense form the eye needs the label to separate from the
     value it sits beside, and there is no column rule doing that job. */
  table.fields td.lbl, table.fields td.pair span.lbl {{ font-weight: bold; }}
  table.fields td.lbl {{ width: 1%; white-space: nowrap; }}
  /* label and value in one cell: the label keeps its own gap, the value
     follows immediately, and nothing on another row can push them apart. */
  table.fields td.pair span.lbl {{ padding-right: 14px; }}
  /* A paired row shrinks both its own cells: the left value to its content
     and the pair to one unbroken line. Otherwise the left column soaks up
     the row width and the right-hand date is squeezed against the margin,
     wrapping mid-value ("6-" / "Aug-2026"). */
  table.fields td.tight {{ width: 1%; white-space: nowrap; }}
  table.fields td.pair {{ white-space: nowrap; }}
  table.fields tr.spacer td {{ padding: 0; height: 8px; }}
  .signoff {{ margin-top: 22px; }}
  table.items {{ width: 100%; table-layout: fixed; border-collapse: collapse; margin-top: 6px; }}
  table.items th, table.items td {{ border: 1px solid #999; padding: 4px 6px; text-align: left;
                                    vertical-align: top; font-size: 10px;
                                    overflow-wrap: anywhere; word-break: break-word; }}
  table.items th {{ background: #f2f2f2; font-weight: bold; }}
  table.items td.num {{ text-align: right; }}
  table.items tr.grand td {{ border: none; font-weight: bold; padding-top: 6px; }}
  table.items tr.grand td.lbl {{ text-align: right; }}
  .empty {{ color: #777; font-style: italic; }}
  h2 {{ font-size: 11px; font-weight: bold; margin: 18px 0 6px; }}
  .photos {{ display: flex; flex-wrap: wrap; gap: 10px; }}
  figure {{ margin: 0; width: 30%; }}
  figure img {{ width: 100%; border: 1px solid #ddd; }}
  figcaption {{ font-size: 9px; color: #777; margin-top: 2px; word-break: break-all; }}
  ul {{ margin: 4px 0 0 16px; padding: 0; }}
  /* A revision page names itself: which version, why it changed, who sent it. */
  .rev-heading {{ font-size: 11px; font-weight: bold; margin: 0 0 4px; }}
  .rev-reason {{ font-size: 10px; color: #444; margin: 0 0 10px;
                 white-space: pre-wrap; overflow-wrap: anywhere; }}
</style></head><body>
{body}
</body></html>"""

    def _html(self, req: PurchaseRequestHeader) -> str:
        """The current form on its own - the export's default document."""
        return self._document(self._current_page(req))

    def build_html(
        self,
        request_id: str,
        *,
        revision_id: Optional[str] = None,
        include_revisions: bool = False,
    ) -> str:
        """The document HTML, without WeasyPrint - what the tests assert on."""
        return self._build(
            request_id, revision_id=revision_id, include_revisions=include_revisions
        )[0]

    def _entity_type(self, req: PurchaseRequestHeader) -> str:
        """Revision rows are keyed on the header's own ``request_type``: a
        sponsorship form and a purchase request share this table and this
        service, and their lineages must never be read under one type."""
        return str(getattr(req, "request_type", None) or "purchase_request")

    def _build(
        self,
        request_id: str,
        *,
        revision_id: Optional[str] = None,
        include_revisions: bool = False,
    ) -> tuple[str, str]:
        """(html, filename). One place decides which pages a request produces."""
        req = self._load(request_id)
        filename = self.build_filename(req)
        label = "sponsorship form" if self._is_sponsorship(req) else "purchase request"

        # A single revision wins over the lineage: the two are mutually exclusive
        # and the route rejects the combination, so this is the second line of
        # defence rather than the contract.
        if revision_id:
            entries = revision_entries(self.db, self._entity_type(req), str(req.id))
            entry = find_revision_entry(entries, revision_id, label=label)
            return (
                self._document(
                    self._revision_page(
                        req, entry, superseded=is_superseded(entries, entry)
                    )
                ),
                filename_with_revision(
                    self._filename_stem(req),
                    entry,
                    "request_number",
                    fallback=display_document_number(req) or str(req.id),
                ),
            )

        if not include_revisions:
            return self._document(self._current_page(req)), filename

        # The EARLIER versions print newest first, behind the current form. The
        # newest entry is the version this form already shows, so it gets no page
        # of its own - its label, submitter and reason go onto the current page.
        entries = revision_entries(self.db, self._entity_type(req), str(req.id))
        pages = [self._current_page(req, latest_revision_entry(entries))]
        pages += [
            self._revision_page(
                req,
                entry,
                superseded=is_superseded(entries, entry),
                page_break=True,
            )
            for entry in appended_revision_entries(entries)
        ]
        return self._document("".join(pages)), filename

    def render_pdf(
        self,
        request_id: str,
        revision_id: Optional[str] = None,
        include_revisions: bool = False,
    ) -> tuple[bytes, str]:
        """Return (pdf_bytes, filename). Raises PDFRenderingUnavailable if
        WeasyPrint cannot render (native libs missing)."""
        html, filename = self._build(
            request_id, revision_id=revision_id, include_revisions=include_revisions
        )
        return render_html(html), filename
