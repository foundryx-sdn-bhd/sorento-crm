"""The Consignment Requisition print (SR-CORE, AC-SR-60): an A4 page in the paper form's
layout (mock v2 frame 5) - header fields, the internal box, the board row first, the Brand /
Model / Qty lines, and the footer ticks in the paper's own words. Same bordered-table style as `stock_inquiry_pdf_service`.

`build_html` takes the detail dict from `sample_request_service.serialize_detail`, so the
printed copy can only show what the detail response shows.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from typing import Any, Dict, Optional

from app.services.pdf_render import today_in_malaysia

# The paper form's own wording (mock v2 frame 5), so Inventory and logistics read it as today.
_MODES = (
    ("driver", "Deliver-Van"),
    ("self_deliver", "Self-Deliver"),
    ("self_collect", "Self-Collection"),
)
# "Either" is the paper's blank: neither box is ticked.
_GOODS = (("demo", "Demo Sets"), ("new", "New Goods"))


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    return escape(str(value))


def _date(value: Any) -> str:
    """A date field: the detail dict carries ISO strings, the paper writes dd/mm/yyyy. Only
    the date fields come through here, so free text that looks like a date prints as typed."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return _fmt(value)


def _row(label: str, value: Any) -> str:
    return f"<tr><th>{escape(label)}</th><td>{_fmt(value)}</td></tr>"


def _ticks(options, chosen: Optional[str]) -> str:
    return "&nbsp;&nbsp;".join(
        f"{'[x]' if key == chosen else '[ ]'} {escape(label)}" for key, label in options
    )


def build_html(d: Dict[str, Any]) -> str:
    customer = d.get("customer_name") or "-"
    customer = (
        f"{escape(str(d['customer_code']))} - {escape(str(customer))}"
        if d.get("customer_code")
        else f"{escape(str(customer))} (No debtor code)"
    )
    delivery = _date(d.get("delivery_date"))
    ship_to = " ".join(x for x in (d.get("ship_to_name"), d.get("ship_to_address")) if x) or None
    site = " ".join(
        x for x in (d.get("site_contact_name"), d.get("site_contact_phone"), d.get("site_contact_note")) if x
    ) or None

    header = "".join(
        [
            _row("Sales Person", d.get("salesperson_name")),
            f"<tr><th>Date</th><td>{_date(d.get('request_date'))}</td></tr>",
            f"<tr><th>Customer Name</th><td>{customer}</td></tr>",
            _row("Project", d.get("project_name")),
            _row("For", d.get("scope_note")),
            _row("Request Delivery Date", delivery),
            _row("Shipping Address", ship_to),
            _row("Contact Person", site),
        ]
    )

    item_rows = []
    number = 1
    if d.get("board_mounting"):
        label = d.get("board_label") or ""
        item_rows.append(
            f"<tr><td>{number}</td><td></td>"
            f"<td><b>MOUNT &amp; LABEL SAMPLE BOARD - {escape(label)}</b></td><td></td><td></td></tr>"
        )
        number += 1
    for line in d.get("lines") or []:
        model = escape(line["product_code"])
        item_rows.append(
            f"<tr><td>{number}</td><td>{_fmt(line.get('brand_name'))}</td><td>{model}</td>"
            f"<td>{line['qty']}</td><td>{_fmt(line.get('remark'))}</td></tr>"
        )
        number += 1
    items = "".join(item_rows) or '<tr><td colspan="5" class="empty">No items</td></tr>'

    loan = f"{d.get('loan_value')} {escape(str(d.get('loan_unit') or ''))}"
    return f"""<!doctype html><html><head><meta charset="utf-8"/>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #1a1a1a; font-size: 11px; }}
  .company {{ font-weight: bold; font-size: 14px; }}
  .doc-title {{ text-align: center; font-size: 15px; font-weight: bold;
                letter-spacing: .06em; text-decoration: underline; padding: 8px 0 10px; }}
  .meta {{ color: #666; font-size: 9px; text-align: center; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
  table.form {{ border: 2px solid #1a1a1a; }}
  th, td {{ border: 1px solid #b8b8b8; padding: 6px 8px; vertical-align: top; text-align: left; }}
  table.form th {{ width: 24%; background: #f2f2f2; text-transform: uppercase; font-size: 10px; }}
  table.items th {{ background: #f2f2f2; font-size: 10px; text-transform: uppercase; }}
  table.internal {{ width: 60%; }}
  .empty {{ color: #999; font-style: italic; }}
  .ticks p {{ margin: 4px 0; }}
</style></head><body>
  <div class="company">SORENTO SDN BHD</div>
  <div class="doc-title">CONSIGNMENT REQUISITION FORM &nbsp; {escape(str(d.get('request_no') or ''))}</div>
  <div class="meta">{escape(str(d.get('status_label') or ''))} &middot; printed {today_in_malaysia().strftime('%d/%m/%Y')}</div>
  <table class="form">{header}</table>
  <table class="form internal">
    <tr><th colspan="4">For Internal Purpose</th></tr>
    <tr><th>Consignment No</th><td>{_fmt(d.get('consignment_no'))}</td><th>PO No</th><td>{_fmt(d.get('po_no'))}</td></tr>
  </table>
  <table class="items">
    <tr><th style="width:6%">No</th><th style="width:16%">Brand</th><th>Model</th><th style="width:8%">Qty</th><th style="width:26%">Remark</th></tr>
    {items}
  </table>
  <div class="ticks">
    <p><b>Delivery:</b> {_ticks(_MODES, d.get('delivery_mode'))}</p>
    <p><b>Loan Period:</b> {loan}</p>
    <p><b>Goods Taken From:</b> {_ticks(_GOODS, d.get('goods_from'))}</p>
    <p><b>Purpose:</b> {_fmt(d.get('purpose'))}</p>
  </div>
</body></html>"""
