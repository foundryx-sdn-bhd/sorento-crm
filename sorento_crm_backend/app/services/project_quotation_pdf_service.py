"""Render ONE quotation issue to the layout of the artifact Sorento actually sends.

Modelled on the real workbook (Sorento to Nadi Cergas, 26/02/2026): sender block, refs on the
right, `To` block, `Attn:`, the subject line, then each scope as a banded table carrying the
sample's column set, a total under the money column per scope, `TOTAL AMOUNT` for the document,
numbered terms and the sign-off.

Two rules run through the whole module, and each is a silent-money bug rather than a visible
failure if it slips:

1. **Everything is read from the ISSUE SNAPSHOT.** The lines come from the ``version_id`` each
   ``project_quotation_issue_scopes`` row recorded, NOT from the scope's current version, and the
   letter, terms and per-scope / grand totals come off the issue rows. A re-download next year has
   to be what was sent, and rendering the live rows would rewrite history invisibly.
2. **A rate-only line prints the words `rate only`** in the money column. Printing RM 0.00 would
   tell the customer it is free; adding it up would overstate the quotation (the sample's five
   alternates come to more than RM 235,000).

Every string that reaches the page goes through ``html.escape``: band labels, descriptions and
scope names are typed off the customer's own bill of quantities, so a stray angle bracket would
otherwise swallow the rest of the row. The rich-text letter and terms are reduced to plain text
blocks for the same reason - the snapshot is customer-visible prose, not a template we control.

WeasyPrint needs native cairo/pango/gdk-pixbuf libs, so the import is guarded exactly as in
``complaint_pdf_service`` and the same ``PDFRenderingUnavailable`` is raised: a host without them
must degrade to a clear message, never take the API down.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape, unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.projects import (
    ProjectQuotation,
    ProjectQuotationDocument,
    ProjectQuotationIssue,
    ProjectQuotationIssueScope,
    ProjectQuotationLine,
    QuotationSignature,
)
from app.models.user import SystemSetting
from app.services import geo_places
from app.services import product_image_service as product_images
from app.services.complaint_pdf_service import PDFRenderingUnavailable
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")

# The sample workbook's column set, in its order. PRODUCT IMAGE is conditional per scope
# (AC-F4): a page of blank cells is worse than no column, and it squeezes the columns that do
# carry information down to an unreadable width.
_IMAGE_COLUMN = "PRODUCT IMAGE"
_COLUMNS: Tuple[str, ...] = (
    "ITEM",
    _IMAGE_COLUMN,
    "TECHNICAL SPEC",
    "DESCRIPTION",
    "BRAND",
    "PRODUCT CODE",
    "QTY",
    "UNIT RATE (RM)",
    "COMPLETE SET",
    "GRAND TOTAL",
)

RATE_ONLY_TEXT = "rate only"

# Rich text arrives from the cover-letter / terms editor. Block ends become line breaks so each
# paragraph or list item can be re-emitted as its own escaped block; nothing is passed through as
# markup.
_BLOCK_BREAK = re.compile(r"(?i)</\s*(p|div|li|h[1-6]|tr|blockquote)\s*>|<\s*br\s*/?\s*>")
_TAG = re.compile(r"<[^>]*>")


# --------------------------------------------------------------------- formatting


def _money(value: Any) -> str:
    """Thousands-separated, two decimals. The sample's money column reads `696,923.00`."""
    try:
        amount = Decimal(value if value is not None else 0)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    return f"{amount.quantize(ZERO):,.2f}"


def item_number(line: Any, position: int) -> str:
    """The ITEM cell: the stored label if a human typed one, otherwise the row's own number.

    The editor no longer asks for a letter (client decision: the number is generated), so almost
    every new line arrives with nothing here and the column would print blank. A stored label still
    WINS, because lines priced before that change carry one and a quotation already in a customer's
    inbox must not renumber itself underneath them.

    ``position`` counts continuously through the scope and does NOT restart at a section heading: a
    section is a heading over one running list, and restarting would give two lines the number 1.
    """
    label = (getattr(line, "item_label", None) or "").strip()
    return label or str(position)


def _line_money(value: Any) -> str:
    """Money on a LINE, where zero means "no separate charge" and must print blank.

    Real quotations list the components of a complete set on their own rows: a pedestal carries
    the price and its cistern, seat cover and connector follow at nothing, because the money is
    already on the parent. Printed as `0.00` those rows read as free products the customer can
    hold Sorento to. The artifact leaves the cell empty, so this does too.

    Totals keep ``_money``: a scope that genuinely adds up to nothing should say so.
    """
    try:
        amount = Decimal(value if value is not None else 0)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    return "" if amount == ZERO else _money(amount)


def _qty(value: Any) -> str:
    """`1046`, not `1046.00`. The column is stored Numeric(12,2) but quantities are counts, and
    a trailing `.00` on every row is noise a QS has to read past."""
    if value is None:
        return ""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return escape(str(value))
    if amount == amount.to_integral_value():
        return f"{amount.to_integral_value():,}"
    return f"{amount.normalize():,}"


def _cell(value: Any) -> str:
    """Escaped text, or nothing at all.

    A missing piece renders as an empty cell rather than the word `None` or a placeholder dash:
    this is a document a customer reads, and an absent technical spec is not a fact worth stating.
    """
    if value is None:
        return ""
    text = str(value).strip()
    return escape(text) if text else ""


def _multiline(value: Any) -> str:
    """Escaped text with its line breaks kept: a description is typed as several lines (the
    size on its own line, "c/w seat cover" on the next) and has to print that way."""
    return "<br/>".join(_cell(value).splitlines())


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return _cell(value)


def _text_blocks(raw: Optional[str]) -> List[str]:
    """Reduce a rich-text snapshot to escaped plain-text blocks.

    Rendering the stored HTML directly would put unvalidated customer-era markup straight into the
    PDF, and this text is snapshotted from an editor rather than authored here. Splitting on block
    boundaries keeps the paragraph and clause structure, which is all the layout needs.
    """
    if not raw:
        return []
    text = _BLOCK_BREAK.sub("\n", str(raw))
    text = _TAG.sub("", text)
    text = unescape(text)
    return [escape(part.strip()) for part in text.splitlines() if part.strip()]


# --------------------------------------------------------------------- images


def _attachment_data_uri(
    db: Session,
    attachment_id: Optional[str],
    cache: Optional[Dict[str, Optional[str]]] = None,
) -> Optional[str]:
    """The product photograph, inline, downscaled.

    Inlined rather than linked: WeasyPrint would otherwise have to fetch a signed CDN URL at
    render time, and a document that looks different depending on whether the network was up is
    not a record of what was sent.

    Downscaled by ``product_image_service`` before it gets here (S21). The column is 60 CSS px
    wide and the live catalogue's chosen photographs average 1.1 MB, so inlining the originals
    for a 52-line quotation is a document nobody can email.

    Best-effort. Storage being down degrades to a missing picture, never to a quotation that
    cannot be produced - the customer is waiting for a price, not a photograph.

    ``cache`` is per render: one product image commonly repeats down a scope (a WC beside its
    valve and its hose), and without it the same object is downloaded once per line.
    """
    return product_images.data_uri(db, attachment_id, cache)


# --------------------------------------------------------------------- loading


def _load_scopes(db: Session, issue: ProjectQuotationIssue) -> List[Dict[str, Any]]:
    """The issue's scopes in printed order, each with the lines the issue actually recorded.

    ``version_id`` comes off the issue-scope row. Reading ``current_version`` here instead would
    make a re-download show a revision priced months later under the reference the customer holds.
    """
    rows = (
        db.query(ProjectQuotationIssueScope)
        .filter(ProjectQuotationIssueScope.issue_id == issue.id)
        .order_by(
            ProjectQuotationIssueScope.sort_order.asc(),
            ProjectQuotationIssueScope.created_at.asc(),
        )
        .all()
    )

    scopes: List[Dict[str, Any]] = []
    for row in rows:
        quotation = (
            db.query(ProjectQuotation).filter(ProjectQuotation.id == row.quotation_id).first()
        )
        lines = (
            db.query(ProjectQuotationLine)
            .filter(ProjectQuotationLine.version_id == row.version_id)
            .order_by(
                ProjectQuotationLine.sort_order.asc(),
                ProjectQuotationLine.created_at.asc(),
                # See `list_lines`: legacy rows tie on both keys, and this document is
                # the one a customer reads, so its order must not move between renders.
                ProjectQuotationLine.id.asc(),
            )
            .all()
        )
        scopes.append(
            {
                "label": quotation.scope_label if quotation is not None else None,
                "lines": lines,
                # The frozen number, not a re-sum: the PDF must not be able to disagree with the
                # record behind it.
                "total": Decimal(row.scope_total or 0),
            }
        )
    return scopes


def _sender(db: Session, document: ProjectQuotationDocument) -> Dict[str, Any]:
    """The letterhead. Name off the company row; address and Tel off the system settings.

    ``companies`` carries only name / code / logo - it has no address or phone column - so the
    sender's postal details come from the singleton ``system_settings`` row, which is where an
    admin already maintains them. Any missing piece renders as nothing rather than as `None`.
    """
    name = None
    if document.company_id:
        company = db.query(Company).filter(Company.id == str(document.company_id)).first()
        name = company.name if company is not None else None

    settings = db.query(SystemSetting).order_by(SystemSetting.id).first()
    if not name and settings is not None:
        name = settings.name
    return {
        "name": name,
        "address_lines": _lines_of(settings.address if settings is not None else None),
        "phone": settings.support_phone if settings is not None else None,
    }


def _lines_of(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in str(raw).splitlines() if part.strip()]


def _document_of(db: Session, issue: ProjectQuotationIssue) -> ProjectQuotationDocument:
    document = (
        db.query(ProjectQuotationDocument)
        .filter(ProjectQuotationDocument.id == issue.document_id)
        .first()
    )
    if document is None:
        raise AppException(
            status_code=404,
            message="Quotation not found.",
            code="quotation_document_not_found",
        )
    return document


# --------------------------------------------------------------------- rendering


def _header_html(
    document: ProjectQuotationDocument,
    issue: ProjectQuotationIssue,
    sender: Dict[str, Any],
) -> str:
    sender_bits: List[str] = []
    if sender["name"]:
        sender_bits.append(f'<div class="sender-name">{_cell(sender["name"])}</div>')
    sender_bits += [f"<div>{_cell(line)}</div>" for line in sender["address_lines"]]
    if sender["phone"]:
        sender_bits.append(f'<div>Tel: {_cell(sender["phone"])}</div>')

    refs = [("Our Ref", _cell(issue.our_ref_text)), ("Your Ref", _cell(document.your_ref))]
    refs.append(("Date", _date(document.doc_date or issue.issued_at)))
    ref_rows = "".join(
        f"<tr><th>{escape(label)}</th><td>{value}</td></tr>" for label, value in refs
    )

    recipient = [f"<div>{_cell(document.recipient_name_snapshot)}</div>"]
    recipient += [
        f"<div>{_cell(line)}</div>" for line in _lines_of(document.recipient_address_snapshot)
    ]
    for phone in _lines_of(document.recipient_phone_snapshot):
        recipient.append(f"<div>{_cell(phone)}</div>")
    attn = (
        f'<div class="attn">Attn: {_cell(document.attn_name)}</div>' if document.attn_name else ""
    )

    subject = (
        f'<div class="subject">{_cell(document.subject_title).upper()}</div>'
        if document.subject_title
        else ""
    )

    return f"""
  <div class="top">
    <div class="sender">{"".join(sender_bits)}</div>
    <table class="refs">{ref_rows}</table>
  </div>
  <div class="to">
    <div class="to-label">To:</div>
    {"".join(recipient)}
    {attn}
  </div>
  {subject}
"""


def _scope_html(
    db: Session, scope: Dict[str, Any], image_cache: Dict[str, Optional[str]]
) -> str:
    lines: Sequence[ProjectQuotationLine] = scope["lines"]

    # The column set depends on whether ANY line has an image, so the whole scope is resolved
    # before a single row is written.
    show_image = any(line.image_attachment_id for line in lines)
    images = (
        {
            line.id: _attachment_data_uri(db, line.image_attachment_id, image_cache)
            for line in lines
        }
        if show_image
        else {}
    )
    columns = [c for c in _COLUMNS if c != _IMAGE_COLUMN or show_image]
    # TECHNICAL SPEC prints the line's drawing (QUOTATION-UX, AC-QLG11), stamped at issue like the
    # photo. The column stays whether or not any line has one: it is in the sample's column set.
    drawings = {
        line.id: _attachment_data_uri(db, line.drawing_attachment_id, image_cache)
        for line in lines
        if line.drawing_attachment_id
    }

    body: List[str] = []
    current_band: Optional[str] = None
    for position, line in enumerate(lines, start=1):
        band = (line.band_label or "").strip()
        # Printed once, above the line that opens the band. Repeating the same label on the next
        # line is a data quirk rather than a new section, and printing it twice would leave a QS
        # unable to tell where the section starts.
        if band and band != current_band:
            current_band = band
            body.append(
                f'<tr class="band"><td colspan="{len(columns)}">{_cell(band)}</td></tr>'
            )

        image_cell = ""
        if show_image:
            uri = images.get(line.id)
            picture = f'<img src="{escape(uri)}"/>' if uri else ""
            image_cell = f'<td class="img">{picture}</td>'

        qty_text = _qty(line.quantity)
        if line.uom:
            qty_text = f"{qty_text} {_cell(line.uom)}"

        money = (
            f'<span class="rate-only">{RATE_ONLY_TEXT}</span>'
            if line.is_rate_only
            else _line_money(line.line_total)
        )
        drawing = drawings.get(line.id)
        spec_cell = f'<img src="{escape(drawing)}"/>' if drawing else ""
        # Typed tech-spec text from before drawings printed: kept, under the description.
        legacy = (
            f'<div class="legacy-spec">{_multiline(line.technical_spec)}</div>'
            if _cell(line.technical_spec)
            else ""
        )
        body.append(
            "<tr>"
            f'<td class="item">{_cell(item_number(line, position))}</td>'
            f"{image_cell}"
            f'<td class="spec">{spec_cell}</td>'
            f'<td class="desc">{_multiline(line.description_snapshot)}{legacy}</td>'
            f"<td>{_cell(line.brand_snapshot)}</td>"
            f"<td>{_cell(line.product_code_snapshot)}</td>"
            f'<td class="num">{qty_text}</td>'
            f'<td class="num">{_line_money(line.unit_price)}</td>'
            f"<td>{_cell(line.complete_set)}</td>"
            f'<td class="num">{money}</td>'
            "</tr>"
        )

    if not body:
        body.append(
            f'<tr><td class="empty" colspan="{len(columns)}">No items priced in this '
            "scope.</td></tr>"
        )

    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    return f"""
  <div class="scope">
    <div class="scope-label">{_cell(scope["label"])}</div>
    <table class="grid">
      <thead><tr>{head}</tr></thead>
      <tbody>{"".join(body)}</tbody>
      <tfoot><tr>
        <td class="total-label" colspan="{len(columns) - 1}">TOTAL</td>
        <td class="num total">{_money(scope["total"])}</td>
      </tr></tfoot>
    </table>
  </div>
"""


def _terms_html(issue: ProjectQuotationIssue) -> str:
    clauses = _text_blocks(issue.terms_rendered)
    if not clauses:
        return ""
    items = "".join(f"<li>{clause}</li>" for clause in clauses)
    return (
        '<div class="terms"><div class="terms-title">TERMS &amp; CONDITIONS</div>'
        f"<ol>{items}</ol></div>"
    )


def _signature(db: Session, signature_id: Optional[str]) -> Optional[QuotationSignature]:
    if not signature_id:
        return None
    return (
        db.query(QuotationSignature)
        .filter(QuotationSignature.id == str(signature_id))
        .first()
    )


def _signature_img(signature: Optional[QuotationSignature]) -> str:
    """The stroke image, straight off the row.

    ``image_data_uri`` is stored inline precisely so rendering a signed document does not depend on
    object storage being reachable, so it is used as-is rather than resolved through an attachment.
    """
    if signature is None or not signature.image_data_uri:
        return ""
    return f'<img class="sig" src="{escape(str(signature.image_data_uri))}"/>'


def _signature_place_html(signature: Optional[QuotationSignature]) -> str:
    """Where the signer stood, as a place a reader recognises.

    `3.03927, 101.80660` on its own means nothing to whoever opens this document, so it prints as
    `near Kajang, Selangor`. The exact figures are not on this line (client decision, 2026-08-05:
    they read as noise here) - they stay on the stored signature row for whoever needs them.

    Resolved through ``geo_places``, the same offline table the CRM screen's answer comes from, so
    the document and the screen cannot disagree about where somebody signed. Offline because this
    render must not depend on an outside service being reachable, must not leak the customer's
    position to one, and must say the same thing when this issue is re-downloaded years later.

    Omitted entirely when the browser gave no fix. A `GPS: -` row on a customer-facing quotation is
    noise about a normal state, not a fact worth printing.
    """
    if signature is None:
        return ""
    described = geo_places.describe_coordinates(signature.gps_lat, signature.gps_lng)
    if not described:
        return ""
    return f'<div class="sig-gps">GPS location: {escape(described)}</div>'


def _sign_off_html(
    db: Session, document: ProjectQuotationDocument, issue: ProjectQuotationIssue
) -> str:
    """The closing, Sorento's signature, and the counter-signature once it exists.

    Sorento's signature is always present: an unsigned document cannot be issued (AC-H1), and it is
    COPIED onto the issue, so re-signing the draft later cannot rewrite what this revision carried.
    The customer block is omitted rather than shown empty - an unsigned issue is a legitimate
    resting state (AC-H8), and a blank "Accepted by" box on a printed page reads as a failure.
    """
    bits = [
        '<div class="closing">We trust the above prices are to your satisfaction.</div>',
        '<div class="closing">Thank You.</div>',
    ]
    sorento = _signature(db, issue.sorento_signature_id)
    picture = _signature_img(sorento)
    if picture:
        bits.append(f'<div class="sig-box">{picture}</div>')
    if document.signatory_name:
        bits.append(f'<div class="signatory">{_cell(document.signatory_name)}</div>')
    if document.signatory_phone:
        bits.append(f'<div class="signatory-phone">{_cell(document.signatory_phone)}</div>')
    bits.append(_signature_place_html(sorento))
    signoff = f'<div class="signoff">{"".join(bits)}</div>'

    customer = _signature(db, issue.customer_signature_id)
    if customer is None:
        return signoff

    accepted = [
        '<div class="accepted-title">Accepted by</div>',
        f'<div class="sig-box">{_signature_img(customer)}</div>',
        f'<div class="signatory">{_cell(customer.signer_name)}</div>',
    ]
    if issue.accepted_at:
        accepted.append(f'<div>Date: {_date(issue.accepted_at)}</div>')
    accepted.append(_signature_place_html(customer))
    return f'{signoff}<div class="accepted">{"".join(accepted)}</div>'


_STYLE = """
  @page { size: A4 landscape; margin: 12mm 10mm; }
  body { font-family: 'Helvetica Neue', Arial, sans-serif; color: #111; font-size: 9px; }
  .top { display: flex; justify-content: space-between; gap: 20px; }
  .sender { line-height: 1.4; }
  .sender-name { font-weight: 700; font-size: 12px; }
  table.refs { border-collapse: collapse; }
  table.refs th { text-align: left; padding: 1px 10px 1px 0; font-weight: 600; }
  table.refs td { padding: 1px 0; }
  .to { margin-top: 12px; line-height: 1.4; }
  .to-label { font-weight: 600; }
  .attn { margin-top: 4px; font-weight: 600; }
  .subject { margin: 12px 0 10px; font-weight: 700; text-transform: uppercase; }
  .letter p { margin: 0 0 6px; }
  .scope { margin-top: 12px; }
  /* A scope heading stranded at the foot of a page, or a row split across two, makes the
     document unreadable against the customer's own bill of quantities. */
  .scope-label { font-weight: 700; text-transform: uppercase; margin-bottom: 3px;
                 break-after: avoid; }
  table.grid tr { break-inside: avoid; }
  table.grid { width: 100%; border-collapse: collapse; table-layout: fixed; }
  table.grid th, table.grid td { border: 1px solid #999; padding: 3px 4px; vertical-align: top;
                                 word-wrap: break-word; }
  table.grid th { background: #eee; font-size: 8px; text-align: left; }
  table.grid td.num { text-align: right; }
  table.grid td.item { text-align: center; width: 24px; }
  table.grid td.img { width: 60px; }
  table.grid td.img img { width: 100%; }
  table.grid td.spec img { width: 100%; }
  .legacy-spec { margin-top: 3px; }
  tr.band td { background: #f4f4f4; font-weight: 700; }
  .rate-only { font-style: italic; }
  td.total-label { text-align: right; font-weight: 700; }
  td.total { font-weight: 700; }
  td.empty { color: #777; font-style: italic; }
  .grand { margin-top: 8px; text-align: right; font-weight: 700; font-size: 11px; }
  .terms { margin-top: 14px; }
  .terms-title { font-weight: 700; margin-bottom: 4px; }
  .terms ol { margin: 0; padding-left: 16px; }
  .terms li { margin-bottom: 2px; }
  /* The closing sentence, the signature and the name are one statement: split across a page
     break they read as an unsigned document with a stray signature on the next sheet. */
  .signoff { margin-top: 14px; line-height: 1.5; break-inside: avoid; }
  .signatory { font-weight: 700; }
  .sig-box { margin-top: 10px; }
  /* Provenance, not the sign-off itself: quieter than the name it sits under, and never big
     enough to compete with the money above it. */
  .sig-gps { margin-top: 2px; font-size: 8px; color: #444; }
  img.sig { height: 44px; max-width: 180px; }
  .accepted { margin-top: 16px; border-top: 1px solid #ccc; padding-top: 6px;
              break-inside: avoid; }
  .accepted-title { font-weight: 700; }
"""


def build_issue_html(db: Session, issue: ProjectQuotationIssue) -> str:
    """The whole document as HTML, straight off the issue snapshot.

    Exposed separately from ``render_issue_pdf`` so the layout can be asserted as a readable
    string, and so a host without WeasyPrint's native libraries can still be tested.
    """
    document = _document_of(db, issue)
    sender = _sender(db, document)
    scopes = _load_scopes(db, issue)

    letter = "".join(f"<p>{block}</p>" for block in _text_blocks(issue.cover_letter_rendered))
    letter_html = f'<div class="letter">{letter}</div>' if letter else ""
    # One cache for the whole document: the same product often appears in more than one scope.
    image_cache: Dict[str, Optional[str]] = {}
    scopes_html = "".join(_scope_html(db, scope, image_cache) for scope in scopes)

    return f"""<!doctype html><html><head><meta charset="utf-8"/>
<style>{_STYLE}</style></head><body>
{_header_html(document, issue, sender)}
  {letter_html}
  {scopes_html}
  <div class="grand">TOTAL AMOUNT&nbsp;&nbsp;RM {_money(issue.grand_total)}</div>
  {_terms_html(issue)}
  {_sign_off_html(db, document, issue)}
</body></html>"""


def build_filename(issue: ProjectQuotationIssue) -> str:
    """Named after the reference the customer quotes back, so a saved file is identifiable."""
    ref = (issue.our_ref_text or "").strip()
    if not ref:
        ref = f"R{issue.issue_no}"
    safe = re.sub(r"[^A-Za-z0-9]+", "-", ref).strip("-") or "quotation"
    return f"quotation-{safe}.pdf"


def render_issue_pdf(db: Session, issue: ProjectQuotationIssue) -> Tuple[bytes, str]:
    """Return ``(pdf_bytes, filename)`` for one issue.

    Raises ``PDFRenderingUnavailable`` when WeasyPrint cannot render, so a host without
    cairo/pango returns a clear message instead of a 500 nobody can diagnose.
    """
    html = build_issue_html(db, issue)
    try:
        from weasyprint import HTML  # noqa: WPS433 (guarded optional dep)
    except Exception as e:  # ImportError or native-lib load error
        raise PDFRenderingUnavailable(
            f"PDF rendering is unavailable on this host (WeasyPrint import failed: {e})."
        ) from e
    try:
        pdf_bytes = HTML(string=html).write_pdf()
    except Exception as e:
        raise PDFRenderingUnavailable(
            f"PDF rendering failed (WeasyPrint native libraries missing or broken: {e})."
        ) from e
    # write_pdf() is typed as optional (it returns None when given a target to write into). An
    # empty result would otherwise be served to the customer as a zero-byte quotation.
    if not pdf_bytes:
        raise PDFRenderingUnavailable("PDF rendering produced no output.")
    return pdf_bytes, build_filename(issue)
