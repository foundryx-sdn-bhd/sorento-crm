"""Render-envelope presenters for the consolidated MCP list tools.

When a tool is called with `view=render`, its (already sanitized) data response is
transformed into ONE uniform envelope so the n8n / messaging consumer needs only a
generic renderer instead of per-tool field-mapping code:

    {
      "result_type": "orders",
      "intro": "Here are the orders I found.",
      "items": [
        {"title": "...", "fields": [{"label": "...", "value": "..."}],
         "flags": {"discontinued": false, "expired": false, "expiring_soon": false,
                   "unallocated": false, "partially_allocated": false}}
      ],
      "attachments": [{"url","filename","mimeType","attachmentType"}],
      "action_links": [{"label","url","type"}],
      "last_updated_at": "...",
      "has_result": true
    }

The envelope is intentionally MARKDOWN-FREE - `fields` carry label/value pairs only.
The consumer owns the skin (bold, numbering, footer). This keeps the envelope
channel-agnostic (WhatsApp, email, web can each render it differently).

`view=render` is OPT-IN: without it, tools return their raw data shape unchanged, so
the AI assistant (which still reads raw) is not affected until it migrates.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Tools that support `view=render`. Used by server._compile_tool to inject the
# `view` param into the generated input schema, and by the dispatcher below.
PRESENTER_TOOLS: frozenset[str] = frozenset(
    {
        "crm_order_management_orders_list",
        "crm_order_management_orders_by_product_list",
        "crm_incoming_stock_list",
        "crm_incoming_stock_by_product",
        "crm_incoming_stock_shipments",
        "crm_marketing_promotions_list",
        "crm_marketing_promotion_products_list",
        "crm_master_products_list",
        "crm_master_product_attachments_list",
        "crm_certificates_list",
        "crm_resource_attachments_list",
        "crm_inventory_stock_balance_list",
        "crm_forms_management_forms_list",
        "crm_portal_link_get",
        "crm_procurement_po_placed_list",
        "crm_procurement_spo_allocations_last_receipt_list",
        "crm_procurement_po_last_cost_list",
        "crm_outstanding_report",
        "crm_low_stock_report",
        "crm_sales_report",
        "crm_top_selling_report",
        "crm_sales_analysis",
    }
)

logger = logging.getLogger(__name__)

_DEFAULT_INTRO = {
    "crm_order_management_orders_list": "Here are the orders I found.",
    "crm_order_management_orders_by_product_list": "Here are the orders I found.",
    "crm_incoming_stock_list": "Here is the incoming stock I found.",
    "crm_incoming_stock_by_product": "Here is the incoming stock I found.",
    "crm_incoming_stock_shipments": "Here are the incoming shipments I found.",
    "crm_marketing_promotions_list": "Here are the matching promotions.",
    "crm_marketing_promotion_products_list": "Here are the matching promotion products.",
    "crm_master_products_list": "Here are the matching products.",
    "crm_master_product_attachments_list": "Here are the product files I found.",
    "crm_certificates_list": "Here are the certificates I found.",
    "crm_resource_attachments_list": "Here are the documents I found.",
    "crm_inventory_stock_balance_list": "Stock details found for the requested products.",
    "crm_forms_management_forms_list": "Here are the forms I found.",
    "crm_portal_link_get": "Here is the link you requested.",
    "crm_procurement_po_placed_list": "Here is the PO placed I found.",
    "crm_procurement_spo_allocations_last_receipt_list": "Here is the last SPO line per product.",
    "crm_procurement_po_last_cost_list": "Here is the last purchase cost per product and location.",
}

_RESULT_TYPE = {
    "crm_order_management_orders_list": "orders",
    "crm_order_management_orders_by_product_list": "orders",
    "crm_incoming_stock_list": "incoming_stock",
    "crm_incoming_stock_by_product": "incoming_stock",
    "crm_incoming_stock_shipments": "incoming_shipments",
    "crm_marketing_promotions_list": "promotions",
    "crm_marketing_promotion_products_list": "promotion_products",
    "crm_master_products_list": "products",
    "crm_master_product_attachments_list": "product_attachments",
    "crm_certificates_list": "certificates",
    "crm_resource_attachments_list": "attachments",
    "crm_inventory_stock_balance_list": "stock",
    "crm_forms_management_forms_list": "forms",
    "crm_portal_link_get": "portal_link",
    "crm_procurement_po_placed_list": "purchase_orders_placed",
    "crm_procurement_spo_allocations_last_receipt_list": "spo_last_receipt",
    "crm_procurement_po_last_cost_list": "po_last_cost",
}

_STOCK_TOOL = "crm_inventory_stock_balance_list"

# The stock tool answers in whichever shape the contact's visibility policy
# allows, so its result_type is decided per RESPONSE, not per tool.
_STOCK_MODE_RESULT_TYPE = {
    "compact": "stock_compact",
    "availability": "stock_availability",
}

_STOCK_COMPACT_INTRO = "Stock summary for the requested products."
# The dealer answer, verbatim. This IS the outbound WhatsApp text (n8n prints the
# intro and nothing else for this mode), so the wording is the contract.
_AVAILABILITY_ASK = "How many units do you need?"

# Passthrough keys preserved from the raw response into the envelope (e.g. the
# escalation hint attached after sanitize). Kept so render mode loses nothing.
_PASSTHROUGH_KEYS = (
    "suggested_escalation",
    "escalate_team",
    "escalated_agent",
    "fallback_used",
    "alternatives",
    "relaxed_axis",
    # Chatbot stock ask v2 S3 (AC-SA314): the per-product `needs_quantity` / `branch`
    # block. The PRESENTER reads it to build the dealer's per-product sentence, and
    # the ENGINE reads it AGAIN, off this same envelope - to open/update/close the
    # stock task (`turn/task.py::tasks_after_reply`) and to decide whether to attach
    # an `incoming` entry's packing list. Dropping it here means the raw fetch never
    # reaches either reader, even though the rendered TEXT still looks right.
    "stock_availability",
    # Every company the backend actually searched, present only when the lookup
    # spanned more than one (see `stamp_lookup_companies` backend-side). A
    # single-company reply never carries it, so it stays byte-identical.
    "lookup_companies",
    # Which fields were withheld from this caller and why. Without it, `render`
    # drops a denied field silently and the agent cannot tell "you may not see
    # this" from "it has not happened yet" - so it guesses, and it guesses the
    # second one out loud.
    "field_access",
    # Filter-wide measures for list answers (backend `stamp_order_summary`,
    # only when the caller asked with include_summary). Not interpreted here:
    # the backend computes, n8n presents. Absent when absent. `pagination` is
    # deliberately NOT passed through - the consumer reads `summary.row_count`
    # and nothing reads the page geometry (reviewer R6: no key without a reader).
    "summary",
    # Which stock-visibility policy answered, so the consumer can branch on the
    # mode (which format to print, whether to ask the quantity question again)
    # instead of inferring it from which block happens to be present.
    "stock_visibility",
)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _filled(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _money(v: Any) -> str | None:
    if not _filled(v):
        return None
    try:
        return f"MYR {float(v):.2f}"
    except (TypeError, ValueError):
        return f"MYR {v}"


def _qty(v: Any) -> Any:
    """Render a quantity compactly - drop a meaningless fractional part.

    ``2.0000`` -> ``"2"``, ``2.5000`` -> ``"2.5"``, ``2.125`` -> ``"2.125"``.
    Non-numeric / None passes through unchanged. Decimal (not float) so we never
    introduce binary artefacts on an already-clean value.
    """
    if v is None:
        return v
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return v
    if d == d.to_integral_value():
        return str(d.to_integral_value())
    s = format(d, "f")  # fixed-point, never exponent notation
    return s.rstrip("0").rstrip(".") if "." in s else s


def _dims(o: dict) -> str | None:
    l, w, h = o.get("dimensions_length"), o.get("dimensions_width"), o.get("dimensions_height")
    if any(_filled(x) for x in (l, w, h)):
        fmt = lambda x: x if _filled(x) else "-"
        return f"{fmt(l)} x {fmt(w)} x {fmt(h)} mm"
    return None


def _distinct_name(code: Any, name: Any) -> str | None:
    """Product name only when it differs from the code.

    Sorento usually sets product_name == product_code, so showing both is
    redundant. Surface the name only when it carries extra info (code != name).
    """
    if not _filled(name):
        return None
    if _filled(code) and str(name).strip() == str(code).strip():
        return None
    return name


def _att_type(a: dict) -> str | None:
    t = a.get("attachment_type")
    if not t:
        return None
    if isinstance(t, str):
        return t
    return t.get("description") or t.get("type_name")


class _Builder:
    """Accumulates items + attachments + action_links while walking the rows."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.attachments: list[dict[str, Any]] = []
        self.action_links: list[dict[str, Any]] = []
        # field key -> permission key (A2/A5/A6, e.g. "inventory.sellable",
        # "purchase_orders.supplier"). The MCP itself stays unfiltered (in-app
        # assistant + n8n operators are internal); `output_structurer` is the
        # only consumer that reads this and drops a field/summary item whose
        # key is here unless the contact's access grants the permission key.
        self.restricted_fields: dict[str, str] = {}
        # spec_key -> registry label (A1). A SEPARATE top-level envelope key from
        # `field_vocabulary` on purpose: that one already drives the incoming
        # clearance/timeline projection in `output_structurer` (gated on its mere
        # truthiness), so folding spec labels into it would misfire that whole
        # machinery on every product spec answer, including a no-attribute ask
        # that must show the compact "Specs:" line rather than have every keyed
        # field stripped down to none.
        self.spec_vocabulary: dict[str, str] = {}
        # A2 (amended 8 Sep 2026): a presenter-computed summary block, rendered after
        # the rows and before any trailing miss note - the SAME shape `present_response`
        # already merges in for a tool whose BACKEND sends a `summary` dict (orders), but
        # populated here for a tool (stock) whose aggregate is computed FROM THE ROWS the
        # presenter already has, with no backend change needed.
        self.summary_items: list[dict[str, Any]] = []

    def restrict(self, key: str, permission: str) -> None:
        self.restricted_fields[key] = permission

    def note_spec(self, key: str, label: str) -> None:
        self.spec_vocabulary[key] = label

    def add_summary_item(self, label: Any, value: Any, *, key: str | None = None) -> None:
        """One `*label:* value` line in the summary block. `key` is what a restricted
        field is dropped by (see `restrict`); omitted, the line is never gated."""
        if not _filled(value):
            return
        field: dict[str, Any] = {"label": label, "value": value}
        if key is not None:
            field["key"] = key
        self.summary_items.append({"title": None, "fields": [field]})

    def item(
        self,
        title: Any,
        pairs: list[tuple[Any, ...]],
        *,
        discontinued=False,
        expired=False,
        expiring_soon=False,
        unallocated=False,
        partially_allocated=False,
        entity_id: Any = None,
    ) -> None:
        # A pair is either (label, value) or (key, label, value). The 3-tuple form
        # carries the CRM field key, which is what a consumer must match on: the
        # label is display text and two vocabularies for the same field already
        # disagree (render says "ETC", field_access.FIELD_LABELS says "ETC
        # (estimated time of container closing)"). The key is also what
        # `field_access.denied[].field` reports, so a consumer can tell "withheld"
        # from "not yet reached" by comparing the same token on both sides.
        # `key` is omitted, never null, when the source key is unknown.
        #
        # `entity_id` (item 1, 17 Sep 2026) rides at the TOP LEVEL of the item, never
        # inside `fields` - a field is rendered verbatim to the customer
        # (`_item_line`/`output_structurer`), and a raw uuid must never be. It is the
        # same identity `narrow.py::_options` carries as `uuid` for a resolver-matched
        # roster; a caller that arms a pick from a flat item list (a forms browse) reads
        # it off here instead.
        fields: list[dict[str, Any]] = []
        for pair in pairs:
            key, lbl, val = pair if len(pair) == 3 else (None, *pair)
            if not _filled(val):
                continue
            fields.append(
                {"label": lbl, "value": val} if key is None else {"key": key, "label": lbl, "value": val}
            )
        if not fields:
            return
        self.items.append(
            {
                "title": title if _filled(title) else None,
                "fields": fields,
                "flags": {
                    "discontinued": bool(discontinued),
                    "expired": bool(expired),
                    # Mutually exclusive with `expired` - a renewal deadline is
                    # not a dead document, and collapsing the two would have the
                    # consumer refuse to serve a certificate that is still valid.
                    "expiring_soon": bool(expiring_soon),
                    "unallocated": bool(unallocated),
                    "partially_allocated": bool(partially_allocated),
                },
                **({"id": entity_id} if _filled(entity_id) else {}),
            }
        )

    def raw_item(self, title: Any, fields: list[dict[str, Any]], flags: dict[str, Any]) -> None:
        """An item whose fields and flags the SOURCE already shaped.

        `item()` maps a raw CRM row: it drops empty values and stamps the five
        standard flags. The stock-visibility blocks arrive pre-shaped by the
        policy - an `availability` answer has no fields AT ALL (that is the
        point of the mode) and carries its own two flags - so mapping them
        through `item()` would delete the answer for being empty.
        """
        self.items.append(
            {
                "title": title if _filled(title) else None,
                "fields": fields,
                "flags": flags,
            }
        )

    def attach(self, a: Any) -> None:
        if not isinstance(a, dict):
            return
        url = a.get("file_path") or a.get("url")
        if not _filled(url):
            return
        entry = {
            "url": url,
            "filename": a.get("stored_filename") or a.get("filename") or a.get("original_filename"),
            "mimeType": a.get("mime_type") or a.get("mimeType"),
            "attachmentType": _att_type(a),
        }
        # When the file was uploaded, per FILE. A document class is re-uploaded
        # under the same name - six revisions of "Container Status 2026.xlsx" are
        # six identical-looking entries - so without this a consumer handing over
        # one of them cannot say how current it is. The envelope-level
        # `last_updated_at` is the newest across the whole answer, which says
        # nothing about the individual file when more than one comes back.
        # Already Malaysia wall-clock: the sanitizer rewrites `uploaded_at`
        # before the presenter sees it.
        uploaded = a.get("uploaded_at")
        if _filled(uploaded):
            entry["uploadedAt"] = uploaded
        self.attachments.append(entry)

    def link(self, label: str, url: str, link_type: str = "portal_link") -> None:
        if _filled(url):
            self.action_links.append({"label": label, "url": url, "type": link_type})


# --------------------------------------------------------------------------
# per-tool builders
# --------------------------------------------------------------------------
def _wh_alloc(allocs: Any) -> str:
    # Issue #1328: a contact denied the quantity gets the allocation with
    # `allocated_quantity` ABSENT (`field_access.STRIP_WITH`) - the warehouse alone,
    # never "BRW (None)".
    return ", ".join(
        f"{a.get('warehouse_code')} ({a['allocated_quantity']})"
        if a.get("allocated_quantity") is not None
        else f"{a.get('warehouse_code')}"
        for a in (allocs or [])
    )


def _alloc_state(row: Any) -> tuple[bool, bool, Any]:
    """Allocation signal for one incoming line: (unallocated, partially_allocated, gap).

    Mutually exclusive booleans. `unallocated` = nothing has been claimed by any
    warehouse yet; `partially_allocated` = some but not all of the shipped quantity is
    claimed, with `gap` carrying the remainder for the consumer's badge text.

    The gap is NOT computed here - the backend derives it against `quantity_shipped`
    (allocations are never decremented on receipt, so `remaining_incoming_quantity` is
    the wrong base) and ships only the gap, so the shipped total never reaches a
    consumer. A backend that predates the field simply omits it: allocations exist, so
    nothing is claimed rather than a partial guessed from the wrong number.
    """
    if not isinstance(row, dict):
        return False, False, None
    if not (row.get("warehouse_allocations") or []):
        # The empty allocation list is itself the signal; no number to show.
        return True, False, None
    gap = row.get("unallocated_quantity")
    try:
        gap = int(gap)
    except (TypeError, ValueError):
        return False, False, None
    return False, gap > 0, (gap if gap > 0 else None)


def _orders_list(rows: list[dict], b: _Builder) -> None:
    for o in rows:
        lines = o.get("lines") or []
        wh = o.get("warehouse")
        if not _filled(wh) and lines:
            w = lines[0].get("warehouse") or {}
            wh = w.get("warehouse_code")
        prods = ", ".join(
            f"{(l.get('product') or {}).get('product_code') or l.get('product_code')} ({_qty(l.get('quantity'))})"
            for l in lines
        )
        b.item(
            o.get("order_number"),
            [
                ("company_name", "Company", o.get("company_name")),
                ("order_number", "Order Number", o.get("order_number")),
                ("debtor_name", "Customer", o.get("debtor_name")),
                ("order_date", "Order Date", o.get("order_date")),
                ("actual_delivery_date", "Actual Delivery Date", o.get("actual_delivery_date")),
                ("order_status", "Status", o.get("order_status")),
                ("pickup_time", "Pickup Time", o.get("pickup_time")),
                ("transporter", "Transporter", o.get("transporter")),
                ("driver_name", "Driver", o.get("driver_name")),
                ("lorry_plate", "Lorry Plate", o.get("lorry_plate")),
                ("warehouse", "Warehouse", wh),
                ("products", "Products", prods),
            ],
        )
    # DO-ASK-SIMPLIFY rule 2 (owner, 2 Oct 2026; every printed field since the 3 Oct hand
    # test): each DO field is a per-contact reveal, hidden from a contact without the grant.
    # The MCP stays unfiltered; the chatbot's `output_structurer` is the gate. The Company
    # line (multi-company installs only) is not a DO field and is never gated.
    b.restrict("order_number", "delivery_orders.order_number")
    b.restrict("debtor_name", "delivery_orders.customer")
    b.restrict("order_date", "delivery_orders.order_date")
    b.restrict("actual_delivery_date", "delivery_orders.delivery_date")
    b.restrict("order_status", "delivery_orders.status")
    b.restrict("pickup_time", "delivery_orders.pickup_time")
    b.restrict("transporter", "delivery_orders.transporter")
    b.restrict("driver_name", "delivery_orders.driver")
    b.restrict("lorry_plate", "delivery_orders.lorry_plate")
    b.restrict("warehouse", "delivery_orders.warehouse")
    b.restrict("products", "delivery_orders.products")


def _orders_so_outstanding(rows: list[dict], b: _Builder) -> None:
    """A3 (AC-905): `order_status=so_outstanding` - open SO lines, a DIFFERENT
    row shape from `_orders_list` (SO number, not order number; no lines[]).

    `outstanding_qty` is RESTRICTED (S2, security review, 13 Sep 2026) - the SAME
    per-contact figure `crm_outstanding_report`'s SO block gates on
    `sales_orders.outstanding` (D13); defence in depth alongside the chatbot lane's
    own redirect (`lanes/business/__init__.py::run_fetch`), which is what stops
    this bucket being CALLED at all without the grant. The MCP itself stays
    unfiltered - the actual gate is `output_structurer`, not here."""
    for r in rows:
        b.item(
            r.get("so_number"),
            [
                ("company_name", "Company", r.get("company_name")),
                ("so_number", "SO Number", r.get("so_number")),
                ("product_code", "Product Code", r.get("product_code")),
                ("outstanding_qty", "Outstanding Qty", _qty(r.get("outstanding_qty"))),
                ("order_date", "Order Date", r.get("order_date")),
                ("customer", "Customer", r.get("customer")),
                (
                    "requested_delivery_date",
                    "Requested Delivery Date",
                    r.get("requested_delivery_date"),
                ),
            ],
        )
    b.restrict("outstanding_qty", "sales_orders.outstanding")


def _purchase_orders_placed(rows: list[dict], b: _Builder) -> None:
    """Owner ruling (11 Sep 2026): Company, PO Number, Product Code, Ordered Qty,
    Outstanding Qty, PO Date, Location, Supplier - Source (`kind`) and Expected Date
    never render, even though the backend still carries both on the raw row for its
    own filtering/sorting. `kind`, when the row has one, is stamped onto the ITEM as a
    top-level key instead (sibling of `title`/`fields`/`flags`) so the chatbot rung can
    still tell a PO row from an SPO row with no rendered Source field. `supplier` is
    RESTRICTED - a dealer never sees it, only a contact holding
    `purchase_orders.supplier` (the actual gate is `output_structurer`, not
    here - the MCP stays unfiltered)."""
    for r in rows:
        n_before = len(b.items)
        b.item(
            r.get("po_number"),
            [
                ("company_name", "Company", r.get("company_name")),
                ("po_number", "PO Number", r.get("po_number")),
                ("product_code", "Product Code", r.get("product_code")),
                ("ordered_qty", "Ordered Qty", _qty(r.get("ordered_qty"))),
                ("outstanding_qty", "Outstanding Qty", _qty(r.get("outstanding_qty"))),
                ("po_date", "PO Date", r.get("po_date")),
                ("location", "Location", r.get("location")),
                ("supplier", "Supplier", r.get("supplier")),
            ],
        )
        # `b.item` returns early (appends nothing) when every pair was empty; only
        # stamp `kind` onto an item that actually exists.
        if len(b.items) > n_before and r.get("kind") is not None:
            b.items[-1]["kind"] = r["kind"]
    b.restrict("supplier", "purchase_orders.supplier")


def _spo_last_receipt(rows: list[dict], b: _Builder) -> None:
    """Owner ruling 8 Sep 2026, against the rendered answer (chatbot-warehouse-entity-and-
    last-in, AC-9 / AC-9b), amended 9 Sep 2026 (`PLAN-chatbot-last-in-container-number.md`,
    owner ruling: "the container number can put below the SPO number in the answer"). A
    row reads, in this order:

        SPO Number, Container Number (if any), Product Code, SPO Quantity,
        GR Quantity (if any), SPO Date, GR Date (if any), Warehouse

    SPO Number stays first because it is the identity line - the same string the item
    title carries. The ORDER is the ruling, so it is asserted as an exact list in
    `tests/test_presenters.py`, not as a membership test.

    `container_number` is None on every line not ingested from a shipping order that
    named its container (14 of 77,260 on the 7 Sep 2026 prod copy) - same "if any"
    treatment as the two GR fields below.

    Both GR fields are "if any", and they are absent independently:

    * `gr_quantity` is None when nothing has been received. The backend already applies
      that rule (`spo_allocations.quantity_received` defaults to 0 and is never null, and
      the zero rows are the OPEN lines this tool exists to surface), so a zero never
      arrives here as a number.
    * `gr_date` is None when no APPROVED GRN header points at the line. 74,300 allocations
      on the 0907 copy carry a received quantity with no GRN row at all (the ESB-stated
      path), so a GR quantity with no GR date is ordinary, not a defect.

    `SPO Date` is labelled `SPO Date (recorded)` when the backend fell back to
    `created_at` (`spo_date_source == "recorded"`, ~3% of lines): a bookkeeping timestamp
    must not be read as a promised delivery date. `b.item` drops any pair whose value is
    None, which is what makes the "if any" fields disappear rather than render empty.
    """
    for r in rows:
        source = str(r.get("spo_date_source") or "").strip().lower()
        spo_date_label = "SPO Date (recorded)" if source == "recorded" else "SPO Date"
        b.item(
            r.get("spo_number"),
            [
                ("spo_number", "SPO Number", r.get("spo_number")),
                ("container_number", "Container Number", r.get("container_number")),
                ("product_code", "Product Code", r.get("product_code")),
                ("spo_quantity", "SPO Quantity", _qty(r.get("spo_quantity"))),
                ("gr_quantity", "GR Quantity", _qty(r.get("gr_quantity"))),
                ("spo_date", spo_date_label, r.get("spo_date")),
                ("gr_date", "GR Date", r.get("gr_date")),
                ("warehouse", "Warehouse", r.get("warehouse")),
            ],
        )


def _po_last_cost(rows: list[dict], b: _Builder) -> None:
    """Owner ruling 12 Sep 2026 (`PLAN-chatbot-last-purchase-cost.md`), against the
    rendered answer, AC-20..AC-24, amended by a THIRD ruling from live verification the
    same day, verbatim: "we should always show discount even though it is null or 0"
    and "we should show supplier also". A row reads, in this order:

        PO Number, Product Code, PO Quantity, PO Date, Cost / unit,
        Discount / unit, Cost after discount / unit, Warehouse (if any),
        Supplier (if any)

    The three money figures are PER UNIT, derived by the backend from the line's own
    `discount` / `line_total` amounts (a LINE figure on `purchase_order_lines`, never a
    unit one). `Discount / unit` is now ALWAYS rendered - the backend's
    `discount_per_unit` is always a number, `0.0` when the line carries none, and `0.0`
    is a FILLED value (`_filled` only excludes None and empty string), so `_line_money`
    below renders `CNY 0.00` rather than dropping it. Money renders
    `f"{currency} {value:.2f}"` using the LINE's own currency, never a hardcoded MYR.
    Only `Warehouse` and `Supplier` stay "if any": a line bought with no warehouse
    stated, or a PO with no supplier, answers with that one field absent.

    RESTRICTED: the three money fields to a contact holding `purchase_orders.cost` (the
    whole-domain gate the chatbot lane applies before this tool is ever called is belt
    and braces alongside this); `Supplier` to a contact holding
    `purchase_orders.supplier` - the SAME key `_purchase_orders_placed` already uses for
    its own `supplier` field, independent of the cost grant.
    """
    for r in rows:
        currency = str(r.get("currency") or "").strip()

        def _line_money(v: Any) -> str | None:
            if not _filled(v):
                return None
            try:
                return f"{currency} {float(v):.2f}".strip()
            except (TypeError, ValueError):
                return None

        b.item(
            r.get("po_number"),
            [
                ("po_number", "PO Number", r.get("po_number")),
                ("product_code", "Product Code", r.get("product_code")),
                ("po_quantity", "PO Quantity", _qty(r.get("po_quantity"))),
                ("po_date", "PO Date", r.get("po_date")),
                ("unit_cost", "Cost / unit", _line_money(r.get("unit_cost"))),
                ("discount_per_unit", "Discount / unit", _line_money(r.get("discount_per_unit"))),
                (
                    "unit_cost_after_discount",
                    "Cost after discount / unit",
                    _line_money(r.get("unit_cost_after_discount")),
                ),
                ("warehouse", "Warehouse", r.get("warehouse")),
                ("supplier", "Supplier", r.get("supplier")),
            ],
        )
    b.restrict("unit_cost", "purchase_orders.cost")
    b.restrict("discount_per_unit", "purchase_orders.cost")
    b.restrict("unit_cost_after_discount", "purchase_orders.cost")
    b.restrict("supplier", "purchase_orders.supplier")


def _orders_by_product(rows: list[dict], b: _Builder) -> None:
    for o in rows:
        matched = o.get("matched_products") or []
        prods = ", ".join(f"{m.get('product_code')} ({_qty(m.get('quantity'))})" for m in matched)
        # DO-ASK-SIMPLIFY (owner hand test 3 Oct 2026): the warehouse is its own switched
        # field, never folded into Products ("X (1) @ BRW"), so the Warehouse reveal hides it.
        whs = ", ".join(
            dict.fromkeys(str(m.get("warehouse_code")) for m in matched if _filled(m.get("warehouse_code")))
        )
        b.item(
            o.get("order_number"),
            [
                ("company_name", "Company", o.get("company_name")),
                ("order_number", "Order Number", o.get("order_number")),
                ("debtor_name", "Customer", o.get("debtor_name")),
                ("order_date", "Order Date", o.get("order_date")),
                ("actual_delivery_date", "Actual Delivery Date", o.get("actual_delivery_date")),
                ("warehouse", "Warehouse", whs),
                ("products", "Products", prods),
            ],
        )
    b.restrict("order_number", "delivery_orders.order_number")
    b.restrict("debtor_name", "delivery_orders.customer")
    b.restrict("order_date", "delivery_orders.order_date")
    b.restrict("actual_delivery_date", "delivery_orders.delivery_date")
    b.restrict("warehouse", "delivery_orders.warehouse")
    b.restrict("products", "delivery_orders.products")


# ---------------------------------------------------------------------------
# order quantity summary -> summary_items in the ITEM shape (QS-8, plan §3d)
# ---------------------------------------------------------------------------
# WHY THIS SHAPE. The render envelope already has one shape the consumer knows
# how to print: items[] {title, fields[{key,label,value}]}. The summary uses the
# same shape and the same renderer - no sentences composed anywhere, no
# vocabulary in n8n, and any consumer can match on `key`, never on a label.
# The numbers come from `summary` (SQL over the WHOLE filter); nothing here
# adds anything up. Names come from what the backend filtered on, never from
# the parser; an unnameable entry is dropped, not coerced.
# NEVER RAISES on its own account - and the render path wraps it anyway.

# D3 (owner console pass, 8 Sep 2026): the owner's order and labels - the SO block, then
# the DO block. The SO figures ride on a row only under `include_pipeline`
# (`stamp_so_outstanding_rows`); absent -> nothing, as every field here. The DO relabels
# (DOs -> DO, Delivered Qty -> Delivered, Pending Qty -> DO Outstanding, Delivered span ->
# Delivery Date) reach every include_summary caller, n8n included - owner ruling 8 Sep.
_SUMMARY_FIELDS = (
    ("customer", "Customer"),
    ("customers", "Customers"),
    ("product_code", "Product Code"),
    ("so_count", "SO"),
    ("so_date", "SO Date"),
    ("so_ordered_qty", "Ordered"),
    ("so_transferred_qty", "Transferred to DO"),
    ("so_outstanding_qty", "SO Outstanding"),
    ("order_count", "DO"),
    ("order_date", "DO Date"),
    ("delivered_quantity", "Delivered"),
    ("delivered_between", "Delivery Date"),
    ("pending_quantity", "DO Outstanding"),
)


def _sl_num(v: Any):
    """A renderable count or None. Integral -> int (48.0 -> 48), else float."""
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite() or abs(d) >= Decimal("1e15"):
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def _sl_date(v: Any) -> Optional[str]:
    """dd/mm/yyyy for a date-only string; None on anything unparseable."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            d = datetime.strptime(s, "%Y-%m-%d")
        else:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is not None:
                d = d.astimezone()
    except (ValueError, TypeError, OverflowError):
        return None
    out = f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    if d.hour or d.minute or d.second:
        out += f" {d.hour:02d}:{d.minute:02d}:{d.second:02d}"
    return out


def _sl_between(from_val: Any, to_val: Any) -> Optional[str]:
    a, b = _sl_date(from_val), _sl_date(to_val)
    if a and b:
        return a if a == b else f"{a} – {b}"
    return a or b


def _summary_item(customer: Optional[str], row: dict, *, customers: Optional[int] = None) -> Optional[dict]:
    """One render item from a groups[]/products[] row; None when nothing can be named.
    `customers` is the product TOTAL row's exact customer count (D3: it renders as a
    `Customers` field where a customer row has `Customer`, replacing the old
    "All customers (N)" title)."""
    code = row.get("product_code")
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip()
    values = {
        "customer": customer,
        "customers": customers,
        "product_code": code,
        "so_count": _sl_num(row.get("so_count")),
        "so_date": _sl_between(row.get("so_date_from"), row.get("so_date_to")),
        "so_ordered_qty": _sl_num(row.get("so_ordered_qty")),
        "so_transferred_qty": _sl_num(row.get("so_transferred_qty")),
        "so_outstanding_qty": _sl_num(row.get("so_outstanding_qty")),
        "order_count": _sl_num(row.get("order_count")),
        "order_date": _sl_between(row.get("order_date_from"), row.get("order_date_to")),
        "delivered_quantity": _sl_num(row.get("delivered_quantity")),
        "delivered_between": _sl_between(row.get("delivered_from"), row.get("delivered_to")),
        "pending_quantity": _sl_num(row.get("pending_quantity")),
    }
    fields = [
        {"key": k, "label": lbl, "value": values[k]}
        for k, lbl in _SUMMARY_FIELDS
        if _filled(values[k])
    ]
    if len(fields) <= 1:  # the code alone says nothing
        return None
    title = f"{customer} · {code}" if customer else code
    return {"title": title, "fields": fields}


def summary_items(summary: Any) -> list[dict]:
    """The summary as render items: per product, a leading Total when more than one
    customer took it, then one item per customer x product. [] when nothing renders."""
    if not isinstance(summary, dict):
        return []
    products = summary.get("products") if isinstance(summary.get("products"), list) else []
    groups = summary.get("groups") if isinstance(summary.get("groups"), list) else []
    items: list[dict] = []
    seen_codes: set[str] = set()
    for p in products:
        if not isinstance(p, dict) or not isinstance(p.get("product_code"), str):
            continue
        code = p["product_code"].strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        rows = [
            g for g in groups
            if isinstance(g, dict) and isinstance(g.get("product_code"), str)
            and g["product_code"].strip() == code
            and isinstance(g.get("customer"), str) and g["customer"].strip()
        ]
        # N is the CRM's exact per-product customer count when it has one; the visible
        # groups[] slice can be short of it after the ceiling (cross-model review F).
        n_cust = _sl_num(p.get("customer_count"))
        n_cust = int(n_cust) if (n_cust is not None and n_cust >= 0) else len(rows)
        if n_cust > 1 or (summary.get("groups_truncated") is True and rows):
            total = _summary_item(None, p, customers=max(n_cust, len(rows)))
            if total:
                items.append(total)
        for g in rows:
            it = _summary_item(g["customer"].strip(), g)
            if it:
                items.append(it)
        if not rows:
            # no nameable customer row - the product total still says what was asked
            solo = _summary_item(None, p)
            if solo:
                items.append(solo)
    return items


# D8 (owner console pass, 8 Sep 2026): the three-line "SO Outstanding / DO open / Delivered"
# block that used to be prepended off the top-level `so_outstanding_qty` is gone - the
# per-row SO block (`_SUMMARY_FIELDS`) is the only SO rendering, on both order tools.


def summary_intro(summary: Any, n_items: int) -> Optional[str]:
    """The summary intro (count over the whole filter; truncation notice). `n_items` is
    kept in the signature for callers; the page geometry is no longer stated."""
    if not isinstance(summary, dict):
        return None
    rc = _sl_num(summary.get("row_count"))
    if rc is None:
        return None
    # D3 (owner console pass, 8 Sep 2026): the "Summary over N DOs." sentence is struck -
    # the items say everything; only the truncation notice survives, as its own line.
    # `rc` is still required: a summary with no renderable row_count is not a summary.
    if summary.get("groups_truncated") is True or summary.get("products_truncated") is True:
        return "Not every breakdown is shown — add a customer, a product or a date range."
    return None


#: Clearance fields, in the order a person narrates a container's journey, paired
#: with the label to render. Every one is gated server-side per contact, so the key
#: is simply ABSENT when this caller may not see it - and `b.item` drops empty
#: pairs, so a denied field renders as nothing rather than as a blank row.
#:
#: These sit AFTER the identity pairs (company, product, shipment, container - what
#: the row IS) and BEFORE the quantity pairs. The journey is what a contact asks
#: about, so it leads; quantity and allocation close the row. Nothing is lost when a
#: field is denied: the key is absent and `b.item` skips it, so a contact entitled to
#: none of these reads identity straight into quantity, exactly as before.
#:
#: ETA is the exception that proves the rule - it IS gateable (an admin can revoke
#: it) but ships allowed, so it lives in this list rather than the identity block.
_CLEARANCE_PAIRS = (
    ("estimated_arrival_date", "ETA"),
    ("eta_delay_date", "ETA Delay"),
    ("inspection_date", "CIDB Inspection"),
    ("approval_date", "CIDB Approval"),
    ("gatepass_date", "Gatepass"),
    ("warehouse_arrival_date", "Warehouse Arrival"),
    ("informed_collection_date", "Collection Informed"),
    ("collection_date", "Collection"),
    ("loading_date", "Loading"),
    ("etc_date", "ETC"),
    ("etd_date", "ETD"),
    ("liner_code", "Liner"),
    ("china_forwarder", "China Forwarder"),
    ("malaysia_forwarder", "Malaysia Forwarder"),
    ("consignee", "Consignee"),
    ("delivery_warehouse", "Delivery Warehouse"),
    ("free_days_available", "Free Days Available"),
    ("loc", "Location"),
    ("stacked", "Stacked"),
    ("coa_permit_no", "COA Permit No."),
)


def _incoming_list(rows: list[dict], b: _Builder) -> None:
    start = len(b.items)
    for s in rows:
        for l in s.get("lines") or []:
            unallocated, partial, gap = _alloc_state(l)
            b.item(
                l.get("product_code"),
                [
                    ("company_name", "Company", s.get("company_name")),
                    ("product_code", "Product Code", l.get("product_code")),
                    (
                        "product_name",
                        "Product Name",
                        _distinct_name(l.get("product_code"), l.get("product_name")),
                    ),
                    ("shipping_container_number", "Container", s.get("shipping_container_number")),
                    *((key, label, s.get(key)) for key, label in _CLEARANCE_PAIRS),
                    (
                        "remaining_incoming_quantity",
                        "Incoming Quantity",
                        l.get("remaining_incoming_quantity"),
                    ),
                    (
                        "warehouse_allocations",
                        "Warehouse Allocations",
                        _wh_alloc(l.get("warehouse_allocations")),
                    ),
                    ("unallocated_quantity", "Unallocated Quantity", gap),
                ],
                unallocated=unallocated,
                partially_allocated=partial,
            )
        if s.get("attachment"):
            b.attach(s["attachment"])
    _without_repeats(b, start)


def _incoming_by_product(rows: list[dict], b: _Builder) -> None:
    start = len(b.items)
    for p in rows:
        for s in p.get("shipments") or []:
            unallocated, partial, gap = _alloc_state(s)
            b.item(
                p.get("product_code"),
                [
                    ("company_name", "Company", p.get("company_name")),
                    ("product_code", "Product Code", p.get("product_code")),
                    (
                        "product_name",
                        "Product Name",
                        _distinct_name(p.get("product_code"), p.get("product_name")),
                    ),
                    (
                        "shipping_container_number",
                        "Shipment Container",
                        s.get("shipping_container_number"),
                    ),
                    (
                        "estimated_arrival_date",
                        "Estimated Arrival Date",
                        s.get("estimated_arrival_date"),
                    ),
                    ("batch_number", "Batch", s.get("batch_number")),
                    (
                        "remaining_incoming_quantity",
                        "Incoming Quantity",
                        s.get("remaining_incoming_quantity"),
                    ),
                    (
                        "warehouse_allocations",
                        "Warehouse Allocations",
                        _wh_alloc(s.get("warehouse_allocations")),
                    ),
                    ("unallocated_quantity", "Unallocated Quantity", gap),
                ],
                unallocated=unallocated,
                partially_allocated=partial,
            )
            if s.get("attachment"):
                b.attach(s["attachment"])
    _without_repeats(b, start)


#: The incoming tools a dealer's answer can come back from (PR #1329 fix round).
_INCOMING_TOOLS = frozenset(
    {"crm_incoming_stock_list", "crm_incoming_stock_by_product", "crm_incoming_stock_shipments"}
)


#: The ONE refer sentence a dealer ever reads (owner ruling 30 Sep 2026, REFER-SALESMAN):
#: no "to proceed", no "salesperson", no name. The backend's `turn/task.py::REFER_TO_SALESMAN`
#: is the same words; `tests/chatbot/test_refer_salesman.py` pins the two together.
REFER_TO_SALESMAN = "Please refer to your salesman."


def _incoming_dealer(rows: list[dict], b: _Builder) -> None:
    """The backend's dealer view (`eta_policy.dealer_view`): per product, the code once
    and its distinct ETAs, already sorted and padded. The whole line is the item's TITLE
    and `fields` stays empty, the shape the `availability` stock answer uses, so nothing
    of ours (a container, a quantity, an allocation) has a field to ride in on."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        etas = [e for e in row.get("etas") or [] if _filled(e)]
        # Owner hand test, 3 Oct 2026: an ETA is a tick and its dates, none is "No ETA".
        when = f"{STOCK_YES} ETA {', '.join(etas)}" if etas else "No ETA"
        code = row.get("product_code")
        # AVAIL-MODE-REPLIES rule 3 (owner, 2 Oct 2026): one compact line per product,
        # "<code>: ETA <dates>", never the code with the ETA on a line of its own.
        # `dealer_view` tells the engine's zero-stock ladder this line is a whole answer.
        b.raw_item(f"{code}: {when}" if _filled(code) else when, [], {"dealer_view": True})


def _without_repeats(b: _Builder, start: int) -> None:
    """Drop the items from `start` on that read exactly like an earlier one. Two
    shipment lines on one ETA, told apart only by the container and quantities the
    contact is not shown, are one line to the reader (the owner's product printed twice,
    PR #1329 hand test)."""
    seen: list[dict[str, Any]] = []
    kept = []
    for item in b.items[start:]:
        if item in seen:
            continue
        seen.append(item)
        kept.append(item)
    b.items[start:] = kept


def _incoming_shipments(rows: list[dict], b: _Builder) -> None:
    for s in rows:
        b.item(
            s.get("shipment_number"),
            [
                ("company_name", "Company", s.get("company_name")),
                ("Shipment", s.get("shipment_number")),
                ("Container", s.get("shipping_container_number")),
                ("Estimated Arrival Date", s.get("estimated_arrival_date")),
                ("Total Incoming Quantity", s.get("total_remaining_incoming_quantity")),
                ("Distinct Products", s.get("distinct_products_incoming")),
            ],
        )
        if s.get("attachment"):
            b.attach(s["attachment"])


def _promo_filename(promo: dict) -> Any:
    for a in promo.get("attachments") or []:
        att = a.get("attachment") or a
        if att.get("original_filename") or att.get("stored_filename"):
            return att.get("original_filename") or att.get("stored_filename")
    return promo.get("description")


def _promotions(rows: list[dict], b: _Builder) -> None:
    # Header-only: one item per promotion + its PDF. SKU detail lives in the PDF,
    # so products are intentionally not enumerated.
    for promo in rows:
        name = _promo_filename(promo)
        b.item(
            name,
            [
                ("company_name", "Company", promo.get("company_name")),
                ("Promotion", name),
                ("Start Date", promo.get("start_date")),
                ("End Date", promo.get("end_date")),
            ],
            expired=promo.get("is_expired") is True,
        )
        for a in promo.get("attachments") or []:
            b.attach(a.get("attachment") or a)


def _promotion_products(rows: list[dict], b: _Builder) -> None:
    for pp in rows:
        prod = pp.get("product") or {}
        promo = pp.get("promotion") or {}
        b.item(
            prod.get("product_code"),
            [
                ("company_name", "Company", pp.get("company_name")),
                ("Product Code", prod.get("product_code")),
                ("Product Name", _distinct_name(prod.get("product_code"), prod.get("product_name"))),
                ("Promotion", promo.get("description")),
                ("Selling Price", _money(pp.get("selling_price"))),
                ("List Price", _money(prod.get("list_price"))),
                ("Dimensions", _dims(prod)),
            ],
            discontinued=prod.get("is_discontinued") is True,
            expired=pp.get("is_expired") is True,
        )
        for a in pp.get("promotion_attachments") or []:
            b.attach(a.get("attachment") or a)


def _spec_field_value(spec: dict) -> Any:
    """The VALUE half of "label: value unit" (the field's own label is the
    registry label) - `_qty` compacts a numeric value the same way every other
    quantity field does ("1.2000" -> "1.2")."""
    value = spec.get("value")
    # R7 (PR #833 round 4): an enum value in the CRM's own plain words ("cold_only" ->
    # "Cold only"), off the registry's `value_labels`, when the CRM sent them.
    if isinstance(value, str) and _filled(spec.get("display_value")):
        value = spec["display_value"]
    val_text = _qty(value) if isinstance(value, (int, float)) else value
    unit = spec.get("unit")
    return f"{val_text} {unit}".strip() if _filled(unit) else val_text


def _products(rows: list[dict], b: _Builder) -> None:
    for p in rows:
        desc = p.get("description")
        pairs: list[tuple[Any, ...]] = [
            ("company_name", "Company", p.get("company_name")),
            ("Product Code", p.get("product_code")),
            ("Product Name", _distinct_name(p.get("product_code"), p.get("product_name"))),
            ("Description", desc if _filled(desc) and desc != p.get("product_name") else None),
            # Always surface price + dimensions for the products list; when the
            # row has no value, render "Not defined" instead of dropping the line.
            ("List Price", _money(p.get("list_price")) or "Not defined"),
            ("Dimensions", _dims(p) or "Not defined"),
        ]
        # A1 (AC-901/AC-902): every POPULATED spec key as its own keyed field,
        # already ranked by the backend (rank_weight desc, then label) - this
        # presenter does not re-sort. `output_structurer` is what decides whether
        # to show all of them, one asked key, or the compact "Specs:" line.
        for spec in p.get("specs") or []:
            key = spec.get("key")
            label = spec.get("label")
            if not _filled(key) or not _filled(label):
                continue
            pairs.append((f"spec:{key}", label, _spec_field_value(spec)))
            b.note_spec(str(key), str(label))
        b.item(p.get("product_code"), pairs, discontinued=p.get("is_discontinued") is True)
        for a in p.get("attachments") or []:
            b.attach(a)


FILE_LINK_UNAVAILABLE = "(file link unavailable right now)"


def _product_attachments(rows: list[dict], b: _Builder) -> None:
    for r in rows:
        prod = r.get("product") or {}
        att = r.get("attachment") or {}
        # Cert-bearing rows carry a nested `certificate{}`; every other row has
        # no such key. Absent key = no Valid Until field and `expired` stays
        # false, so the 951 Technical Specifications and all Product Photos rows
        # render exactly as before.
        cert = r.get("certificate") or {}
        if not isinstance(cert, dict):
            cert = {}
        state = cert.get("validity_state")
        b.item(
            prod.get("product_code"),
            [
                ("company_name", "Company", r.get("company_name")),
                ("Product Code", prod.get("product_code")),
                ("Product Name", _distinct_name(prod.get("product_code"), prod.get("product_name"))),
                ("Description", prod.get("description")),
                ("Dimensions", _dims(prod)),
                ("Attachment Type", _att_type(att)),
                ("File Name", att.get("original_filename") or att.get("stored_filename")),
                ("Certificate Number", cert.get("certificate_number")),
                ("Valid Until", cert.get("valid_until")),
                # Says which of the three states this file is in, so the consumer
                # never has to compare Valid Until against today itself.
                ("Validity", _validity_label(state)),
                # D9 (8 Sep 2026): a file whose link could not be signed is still LISTED -
                # the reader learns what exists - and the missing link is said, not hidden.
                # `attach` skips it, so no send action is ever built for it.
                (
                    "file_link",
                    "File Link",
                    FILE_LINK_UNAVAILABLE if not _filled(att.get("file_path") or att.get("url")) else None,
                ),
            ],
            discontinued=prod.get("is_discontinued") is True,
            # Reuses the envelope's EXISTING flags.expired (same mechanism as
            # _promotions), so the n8n renderer needs no update to say "found
            # but expired" for a lapsed certificate.
            expired=cert.get("is_expired") is True,
            expiring_soon=state == "expiring_soon",
        )
        b.attach(att)


def _certificate_file(row: dict) -> dict | None:
    """The CURRENT revision's file for one certificate row, or None.

    Superseded revisions are never returned (MCP-8): only the revision the row
    points at is a candidate. Shapes accepted, in order - a nested
    `current_revision.attachment`, a top-level `attachment`, the `is_current`
    entry of an expanded `revisions[]`, and finally the signed-url pair the
    backend adds for `resolve_signed_urls=true`. Returns None when nothing
    usable is present so the caller emits an EMPTY attachments array rather than
    a null or broken url entry (MCP-11).
    """
    candidates: list[Any] = []
    current = row.get("current_revision")
    if isinstance(current, dict):
        candidates.append(current.get("attachment"))
    candidates.append(row.get("attachment"))
    for revision in row.get("revisions") or []:
        if isinstance(revision, dict) and revision.get("is_current") is True:
            candidates.append(revision.get("attachment"))
    for candidate in candidates:
        if isinstance(candidate, dict) and _filled(
            candidate.get("file_path") or candidate.get("url")
        ):
            return candidate

    url = row.get("download_url") or row.get("preview_url")
    if _filled(url):
        return {
            "url": url,
            "filename": row.get("attachment_filename")
            or row.get("original_filename")
            or row.get("stored_filename"),
            "mime_type": row.get("mime_type"),
            "attachment_type": row.get("attachment_type_name"),
        }
    return None


# The backend's derived validity codes, in the words a person reads. An
# unmapped code falls through de-slugged rather than being dropped - a state the
# envelope cannot name is still better than silence about validity.
_VALIDITY_LABELS = {
    "valid": "Valid",
    "expiring_soon": "Expiring soon",
    "expired": "Expired",
    "not_yet_valid": "Not yet valid",
    "unknown": "Unknown",
}


def _validity_label(state: Any) -> str | None:
    code = str(state or "").strip()
    if not code:
        return None
    known = _VALIDITY_LABELS.get(code)
    if known:
        return known
    words = code.replace("_", " ").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else None


def _certificates(rows: list[dict], b: _Builder) -> None:
    for c in rows:
        number = c.get("certificate_number")
        scheme = c.get("scheme")
        title = " ".join(str(x) for x in (scheme, number) if _filled(x)) or number
        state = c.get("validity_state")
        b.item(
            title,
            [
                ("company_name", "Company", c.get("company_name")),
                ("Scheme", scheme),
                ("Certificate Number", number),
                ("Certifying Body", c.get("certifying_body")),
                ("Valid Until", c.get("valid_until")),
                # Humanized: the raw code (`expiring_soon`) leaked into rendered
                # WhatsApp/email output.
                ("Validity", _validity_label(state)),
                ("Covered Products", c.get("covered_product_count")),
            ],
            # Same flags the promotions presenter sets, so an expired certificate
            # is reported as found-but-expired rather than served as live.
            expired=c.get("is_expired") is True,
            expiring_soon=state == "expiring_soon",
        )
        current_file = _certificate_file(c)
        if current_file is not None:
            b.attach(current_file)


#: Every clearance field's CUSTOMER-facing label, keyed the way the envelope keys
#: its fields. Derived from `_CLEARANCE_PAIRS`, so it cannot drift from what a
#: present field renders as.
#:
#: Why it ships at all: an absent field carries no label to borrow, so a consumer
#: saying "not recorded yet" has only the key to name it with. Humanising works by
#: luck - `gatepass_date` -> "Gatepass" is right, `etd_date` -> "Etd" and
#: `eta_delay_date` -> "Eta delay" are not, and no heuristic fixes an acronym
#: without becoming this table. Harvesting the label off a sibling row that DOES
#: carry the field covers the mixed case, but not the case where no row has it.
#:
#: This is the customer register, deliberately: `FIELD_LABELS` backend-side says
#: "ETC (estimated time of container closing)" for an admin choosing grants, this
#: says "ETC" for a dealer reading a chat message.
_CLEARANCE_VOCABULARY = {key: label for key, label in _CLEARANCE_PAIRS}


def _annotate_field_access(envelope: dict[str, Any], tool_name: str) -> None:
    """Give a consumer the labels it needs to name a field it did not receive.

    Both additions are SIBLINGS of `items` and nothing renders them today, so an
    existing consumer is byte-identical. Putting the vocabulary inside
    `items[].fields` instead - by letting empty values through - would push ~20
    blank clearance rows into every incoming envelope, and any renderer that
    renders what it is given would read them out to a customer. That is the dump
    this whole design exists to prevent, arriving through the back door.

    `field_vocabulary` is TOP LEVEL, not inside `field_access`, because the case
    that needs it most has no denial at all: four containers, one carries
    `eta_delay_date` and three do not. Nothing was withheld, so there is no
    `field_access` block to hang it off, and the three rows still have to be
    named out loud.
    """
    fa = envelope.get("field_access")
    if isinstance(fa, dict):
        denied = fa.get("denied")
        if isinstance(denied, list):
            for d in denied:
                # `label` only where we actually know one - never a humanised
                # guess, which is what the consumer can already do for itself.
                if (
                    isinstance(d, dict)
                    and "label" not in d
                    and d.get("field") in _CLEARANCE_VOCABULARY
                ):
                    d["label"] = _CLEARANCE_VOCABULARY[d["field"]]
    if _RESULT_TYPE.get(tool_name) == "incoming_stock":
        envelope["field_vocabulary"] = dict(_CLEARANCE_VOCABULARY)


def _resource_attachments(rows: list[dict], b: _Builder) -> None:
    # No "Type" line - the attachment type (e.g. "Direct Access") is internal
    # plumbing, not meaningful to the end user. Just the file name + the file.
    for att in rows:
        name = att.get("original_filename") or att.get("stored_filename")
        # Upload date, because a document class that is RE-uploaded keeps its name:
        # six revisions of "Container Status 2026.xlsx" render as six identical
        # items, and the agent cannot say which one is current. Rows already
        # arrive newest-first, so the date turns that order into something the
        # agent can state out loud.
        uploaded = att.get("uploaded_at")
        day = str(uploaded)[:10] if _filled(uploaded) else None
        # No File ID. It was added here so a human debugging an answer could find
        # the row again among identically-named files - but `render` is the
        # CUSTOMER-facing view, and it went straight out to dealers on WhatsApp:
        # "File ID: 1e900020-dba5-4e34-ae1c-5e0f90380095" under every document,
        # on every resource-attachment answer, next to the file itself. The uuid
        # is still on the raw (non-render) response and in the CRM UI, which is
        # where someone debugging is looking anyway.
        # Company, because a contact who buys from both Mocha and Sorento gets a
        # current workbook from EACH, and each company names its sheet the same
        # thing. This render deliberately withholds the File ID (above), so the
        # company name is the only handle left to tell the two apart. Absent /
        # shared files (company_id NULL) render no line at all: `b.item` drops any
        # pair whose value is not `_filled`, so nothing empty reaches the reader.
        b.item(
            name,
            [
                ("original_filename", "File Name", name),
                ("company_name", "Company", att.get("company_name")),
                ("uploaded_at", "Uploaded", day),
            ],
        )
        # Strip the type so the delivered attachment carries no "Direct Access" label.
        no_type = {k: v for k, v in att.items() if k != "attachment_type"} if isinstance(att, dict) else att
        b.attach(no_type)


def _stock(rows: list[dict], b: _Builder) -> None:
    def _as_dict(v):
        return v if isinstance(v, dict) else {}

    def _as_str(v):
        return v if isinstance(v, str) and v.strip() else None

    for s in rows:
        # Row shape varies by backend vocab:
        #   • legacy: s["warehouse"] = {warehouse_code, warehouse_name, location}
        #   • Sage:   s["system_location"] = {system_location: <code>, warehouse: <wh name>}
        # Plus flat-key fallbacks. Resolve the location CODE (e.g. "BRW-BB") and the
        # actual WAREHOUSE name (e.g. "BUKIT RAJA") from whichever shape is present.
        prod = _as_dict(s.get("product"))
        wh = _as_dict(s.get("warehouse"))
        sl = _as_dict(s.get("system_location"))
        product_code = prod.get("product_code") or s.get("product_code")
        product_name = prod.get("product_name") or s.get("product_name")
        # System Location = the location code.
        sysloc = (
            sl.get("system_location")
            or wh.get("system_location")
            or wh.get("warehouse_code")
            or _as_str(s.get("system_location"))
            or s.get("warehouse_code")
        )
        # Warehouse = the actual warehouse name.
        wh_name = (
            sl.get("warehouse")
            or sl.get("system_location_description")
            or wh.get("warehouse")
            or wh.get("system_location_description")
            or wh.get("warehouse_name")
            or wh.get("location")
            or _as_str(s.get("warehouse"))
            # Flat-key fallback. Its sibling `system_location` is already handled
            # flat in the sysloc chain above; without this line a flat row renders
            # System Location but leaves Warehouse as "-".
            or _as_str(s.get("system_location_description"))
            or s.get("warehouse_name")
        )
        is_discontinued = (prod.get("is_discontinued") is True) or (s.get("is_discontinued") is True)
        qoh = s.get("quantity_on_hand") if s.get("quantity_on_hand") is not None else s.get("quantity")
        pairs: list[tuple[Any, ...]] = [
            ("company_name", "Company", s.get("company_name")),
            ("product_code", "Product Code", product_code),
            ("product_name", "Product Name", _distinct_name(product_code, product_name)),
            ("warehouse", "Warehouse", wh_name if _filled(wh_name) else "-"),
            ("system_location", "System Location", sysloc if _filled(sysloc) else "-"),
            ("quantity_on_hand", "Quantity On Hand", qoh if qoh is not None else "-"),
        ]
        # D1 (owner console pass, 8 Sep 2026): `*Outstanding:* N` right after the quantity,
        # N = this (product, warehouse) row's own open SO (`open_so_qty`, attached by the
        # backend under `include_sellable` only - absent otherwise, so the row is byte-
        # identical). RESTRICTED behind `inventory.sellable`: the CRM's per-field drop
        # removes it for a contact without the grant. A field, not a summary line - the
        # summary slot is the quantity-summary mode and hides the location rows.
        if s.get("sellable") is not None:
            pairs.append(("open_so_qty", "Outstanding", _stock_int(s.get("open_so_qty"))))
            b.restrict("open_so_qty", "inventory.sellable")
        # Warehouse / System Location always render (even when absent) so every
        # stock row has the same shape - a row with no warehouse joined must not
        # silently drop the fields. "-" placeholder keeps the field present.
        b.item(product_code, pairs, discontinued=is_discontinued)


def _as_int(v: Any) -> int:
    """A quantity as a plain int for ARITHMETIC (product-level summing), 0 for
    anything unreadable - never `None`, so `+=` never raises. Distinct from
    `_stock_int` (display formatting, which passes `None` through unchanged)."""
    if v is None:
        return 0
    try:
        return int(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _stock_int(v: Any) -> Any:
    """A quantity as a plain int. n8n prints `Total: ${value}` straight into the
    message, so a Decimal-shaped string ("500.0000") would be read out as-is."""
    if v is None:
        return v
    try:
        return int(Decimal(str(v)))
    except (InvalidOperation, TypeError, ValueError):
        return v


def _stock_compact(payload: dict, b: _Builder) -> None:
    """`compact`: one item per product, Product Code, Total, then the allowed locations.

    Location order is the backend's (already sorted by code). Re-sorting here
    would let the two answers disagree about the same stock.
    """
    for entry in payload.get("stock_summary") or []:
        if not isinstance(entry, dict):
            continue
        # Product Code leads the fields, keyed like the detailed row's. n8n's
        # output-structurer walks `fields` and does not print `title`, so a
        # code that lives only in the title never reaches the reader and two
        # products' blocks become indistinguishable stacks of numbers.
        code_field = entry.get("product_code")
        fields: list[dict[str, Any]] = []
        if _filled(code_field):
            fields.append({"key": "product_code", "label": "Product Code", "value": code_field})
        total = _stock_int(entry.get("total_on_hand"))
        # D1 (owner console pass, 8 Sep 2026): with `include_sellable` the Total and every
        # warehouse line carry an "(O/S: n)" suffix - the open SO on the warehouses the
        # contact may see on the Total (STOCK-TOTAL-OS-SCOPE: never a hidden warehouse's,
        # never a line with no warehouse), the warehouse's own on its line. The
        # suffix is a `granted_value` on a keyed, RESTRICTED field: the CRM's field drop
        # swaps it in under `inventory.sellable` and strips it otherwise, so the plain
        # number is what an ungranted contact reads and nothing is dropped. Without
        # `sellable` on the entry (no include_sellable) the pair is the old unkeyed one.
        with_os = entry.get("sellable") is not None
        if with_os:
            fields.append({
                "key": "total_on_hand", "label": "Total", "value": total,
                "granted_value": f"{total} (O/S: {_stock_int(entry.get('open_so_qty'))})",
            })
            b.restrict("total_on_hand", "inventory.sellable")
        else:
            fields.append({"label": "Total", "value": total})
        for loc in entry.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            # `warehouse_code` is what the backend declares. `system_location` is
            # the same value under the Sage vocabulary name, read in case a
            # sanitizer relabels the block on the way here.
            code = loc.get("warehouse_code") or loc.get("system_location")
            if not _filled(code):
                continue
            qty = _stock_int(loc.get("quantity_on_hand"))
            if with_os and loc.get("open_so_qty") is not None:
                fields.append({
                    "key": "location_on_hand", "label": str(code), "value": qty,
                    "granted_value": f"{qty} (O/S: {_stock_int(loc.get('open_so_qty'))})",
                })
                b.restrict("location_on_hand", "inventory.sellable")
            else:
                # No `key` on the plain pair: the label IS data (the location the contact
                # is allowed to see), not a CRM field name a consumer could match on.
                fields.append({"label": str(code), "value": qty})
        b.raw_item(entry.get("product_code"), fields, dict(entry.get("flags") or {}))

def _availability_entries(payload: dict) -> list[dict]:
    return [e for e in (payload.get("stock_availability") or []) if isinstance(e, dict)]


def _availability_label(entry: dict) -> Optional[str]:
    """A row with no `product_code` (matched only by name) falls back to
    `product_name`; a row with neither cannot be named to a person at all and is
    skipped by every caller rather than rendered as `None` (no UUIDs in the UI,
    plan point 7: "<P> is product_code, fallback product_name, never the id")."""
    return entry.get("product_code") or entry.get("product_name")


#: R6/R14 (lavish review), AC-SA313: the three branches whose wording never varies.
#: `incoming` is handled separately in `_availability_line` - it is the only branch
#: whose sentence carries a date. REFER-SALESMAN (30 Sep 2026): the verdict is its own
#: sentence and the refer is exactly `REFER_TO_SALESMAN`, on the same line.
#
#: AVAIL-MODE-REPLIES (owner, 2 Oct 2026): got stock / no stock read as a tick and a cross,
#: never as words. `too_big` keeps its words behind a blocked mark (owner v2 note 1): it
#: says nothing about our stock either way, so it is never a tick or a cross.
STOCK_YES = "\u2705"
STOCK_NO = "\u274c"
STOCK_BLOCKED = "\U0001F6AB"
_AVAILABILITY_TAILS = {
    "too_big": f"{STOCK_BLOCKED} the quantity is more than what I can confirm here. {REFER_TO_SALESMAN}",
    "in_stock": f"{STOCK_YES} {REFER_TO_SALESMAN}",
    # REFER-ONLY-FIXES (owner, 3 Oct 2026): `no_incoming` is reached only with no stock
    # (`stock_ask_branch.branch`: some stock is `in_stock`), so the line says both.
    "no_incoming": f"{STOCK_NO} No stock and no incoming. {REFER_TO_SALESMAN}",
}


def _availability_line(entry: dict) -> str:
    """Chatbot stock ask v2 S3, R14/AC-SA313: one line per entry, every line
    starting "<code> x <Q>:" so a multi-product reply reads line by line. The four
    fixed sentences (R6) are the ONLY wording; AC-SA312: no digit of ours besides
    the dealer's own asked quantity and the ETA date (already dd/mm/yyyy on the
    entry, `StockService._apply_stock_visibility`) ever appears."""
    code = _availability_label(entry)
    qty = entry.get("requested_qty")
    return f"{code} x {qty}: {_availability_tail(entry)}"


def _availability_tail(entry: dict) -> str:
    """The sentence after "<code> x <Q>:" for one answered entry."""
    branch = entry.get("branch")
    if branch == "incoming":
        return f"{STOCK_NO} ETA {entry.get('eta')}."
    available = entry.get("available_qty")
    if (
        branch == "in_stock"
        and isinstance(available, int)
        and not isinstance(available, bool)
        and available >= 1
    ):
        # AVAIL-MODE-REPLIES rule 2: some stock, short of the asked quantity, the ask
        # within the category max. The backend sets `available_qty` in that case only
        # (`inventory_service._apply_stock_visibility`), so it is the one figure of ours
        # this mode ever prints.
        return f"{STOCK_YES} {available} available. {REFER_TO_SALESMAN}"
    # Nit, review round 1: an unknown or missing branch is unreachable today
    # (`products.category_id` is NOT NULL, so `inventory_service.py` never
    # leaves `branch` unset) - but if a fallback is kept, `too_big` is the one
    # of the four sentences that claims nothing about our stock either way.
    return _AVAILABILITY_TAILS.get(branch, _AVAILABILITY_TAILS["too_big"])


def _stamp_refers(entries: Any) -> Any:
    """CUSTOMER-ASKS-REFER-ONLY (owner ruling 1 Oct 2026): each answered entry carries
    `refers_to_salesman`, read off the tail this presenter printed for it, so the backend's
    Customer asks writer logs exactly the lines that referred the dealer (B3 `incoming`
    does not). Per entry with a branch, the same rule `_stock_availability` prints the
    lines by; an entry still owing its quantity printed no tail and is not stamped."""
    if not isinstance(entries, list):
        return entries
    rows = [e for e in entries if isinstance(e, dict)]
    if not rows:
        return entries
    # AVAIL-MODE-REPLIES rule 5: an answered line is printed even while another product
    # still owes its quantity, so it is stamped on that turn too.
    return [
        {**e, "refers_to_salesman": _availability_tail(e).endswith(REFER_TO_SALESMAN)}
        if isinstance(e, dict) and e.get("branch")
        else e
        for e in entries
    ]


def _stock_availability(payload: dict, b: _Builder) -> None:
    """`availability`: one R6 sentence per product, and nothing else.

    `fields` stays empty on purpose - this mode exists so a dealer is never told a
    quantity or a location of ours, and an empty field list is the only shape that
    cannot carry one. The item TITLE carries the whole answer (`_availability_line`)
    for every entry with a branch; an entry still missing its quantity keeps the bare
    product code and `_availability_intro` asks (the backend's
    `turn/task.py::StockQtyTask.question()` names the products once a task is open).
    """
    entries = _availability_entries(payload)
    for entry in entries:
        label = _availability_label(entry)
        if not label:
            continue
        # AVAIL-MODE-REPLIES rule 5 (owner, 2 Oct 2026): a product with its answer
        # (a branch) prints that answer even while another product still owes its
        # quantity; one still owed stays its bare code for the question to name.
        title = _availability_line(entry) if entry.get("branch") and not entry.get("needs_quantity") else label
        b.raw_item(
            title,
            [],
            {
                "needs_quantity": bool(entry.get("needs_quantity")),
                "branch": entry.get("branch"),
            },
        )


def _availability_intro(payload: dict) -> str:
    """The whole reply, in one line - or none.

    Any product still missing its quantity makes the turn a question, not an
    answer - so ask, and say nothing about the rest. Once every entry has a
    branch (chatbot stock ask v2 S3 fix round 1, Blocking 1), the per-item
    titles (`_availability_line`) already carry the whole R14 sentence, product
    and quantity included - a shared intro on top of them cannot be true for
    every entry at once: a too_big/no_incoming pairing spoke of "not enough
    stock" even when one of the two entries had plenty, which is false and,
    for `too_big`, a statement about our stock that R6 B1 forbids. So an
    answered reply gets no intro at all; the plan's sample (g) shows the same
    shape, one line per product and nothing before them.
    """
    entries = _availability_entries(payload)
    if any(e.get("needs_quantity") for e in entries):
        return _AVAILABILITY_ASK
    return ""


def _forms(rows: list[dict], b: _Builder) -> None:
    for f in rows:
        # `id` rides at the item's top level (browse rows only - `_FORMS_LIST_KEEP_
        # BROWSE`), never as a rendered field: it is what the engine arms a numbered
        # pick with, not something to say to the customer.
        b.item(f.get("name"), [("Form Name", f.get("name"))], entity_id=f.get("id"))
        # Narrowed form lookups carry the attachment so the form file can be sent.
        if f.get("attachment"):
            b.attach(f["attachment"])


def _portal_link(payload: dict, b: _Builder) -> None:
    url = payload.get("portal_link") or payload.get("url")
    if not _filled(url) and isinstance(payload.get("data"), dict):
        url = payload["data"].get("portal_link") or payload["data"].get("url")
    b.link(payload.get("label") or "Portal Link", url)


def _generic(rows: list[dict], b: _Builder) -> None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        b.item(
            None,
            [(k, v) for k, v in r.items() if v is not None and not isinstance(v, (dict, list))],
        )
        if r.get("attachment"):
            b.attach(r["attachment"])
        for a in r.get("attachments") or []:
            b.attach(a.get("attachment") or a if isinstance(a, dict) else a)


_BUILDERS = {
    "crm_order_management_orders_list": _orders_list,
    "crm_order_management_orders_by_product_list": _orders_by_product,
    "crm_incoming_stock_list": _incoming_list,
    "crm_incoming_stock_by_product": _incoming_by_product,
    "crm_incoming_stock_shipments": _incoming_shipments,
    "crm_marketing_promotions_list": _promotions,
    "crm_marketing_promotion_products_list": _promotion_products,
    "crm_master_products_list": _products,
    "crm_master_product_attachments_list": _product_attachments,
    "crm_certificates_list": _certificates,
    "crm_resource_attachments_list": _resource_attachments,
    "crm_inventory_stock_balance_list": _stock,
    "crm_forms_management_forms_list": _forms,
    "crm_procurement_po_placed_list": _purchase_orders_placed,
    "crm_procurement_spo_allocations_last_receipt_list": _spo_last_receipt,
    "crm_procurement_po_last_cost_list": _po_last_cost,
}


# --------------------------------------------------------------------------
# last_updated_at walk
# --------------------------------------------------------------------------
def _latest_updated(node: Any, depth: int = 0) -> str | None:
    best: str | None = None
    if depth > 6 or not isinstance(node, (dict, list)):
        return best
    if isinstance(node, dict):
        # `uploaded_at` counts: an attachment is never edited in place, so the
        # upload IS its last-updated moment. Without it every document answer
        # reported `last_updated_at: null` and the agent could not say how fresh
        # the file it just handed over actually is.
        for key in ("updated_at", "last_updated_at", "uploaded_at"):
            v = node.get(key)
            if _filled(v) and (best is None or str(v) > best):
                best = str(v)
        children = node.values()
    else:
        children = node
    for child in children:
        sub = _latest_updated(child, depth + 1)
        if sub and (best is None or sub > best):
            best = sub
    return best


def _company_names(lookup_companies: Any) -> str | None:
    """Join the backend's `lookup_companies` names: Mocha or Sorento; A, B or C.

    Order is the backend's (already sorted by name); this only joins.

    Returns None unless EVERY entry has a name. Naming only the companies we
    happen to have a name for would turn "I checked your two companies" into a
    confident, wrong "I checked Sorento" - so an entry with a missing name
    drops the whole clause and the reply falls back to the plain intro.
    """
    if not isinstance(lookup_companies, list) or not lookup_companies:
        return None
    if not all(
        isinstance(c, dict) and _filled(c.get("name")) for c in lookup_companies
    ):
        return None
    names = [str(c.get("name")).strip() for c in lookup_companies]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " or " + names[-1]


def _stock_mode(tool_name: str, data: dict[str, Any]) -> str:
    """Which stock-visibility policy shaped this response.

    Anything other than the two summary modes (including a response with no
    policy block at all, i.e. every caller that is not a contact) is `detailed`,
    which is the envelope this tool has always produced.
    """
    if tool_name != _STOCK_TOOL:
        return "detailed"
    block = data.get("stock_visibility")
    mode = block.get("mode") if isinstance(block, dict) else None
    return mode if mode in _STOCK_MODE_RESULT_TYPE else "detailed"


# --------------------------------------------------------------------------
# dispatcher
# --------------------------------------------------------------------------
def _http_error_envelope(tool_name: str, data: dict) -> dict:
    """The render envelope for a non-2xx route answer (CHATBOT-SELFREF-SCOPE B3): no
    text, `has_result: false`, and the failure spelled out - `error` (one line: method,
    path, status, the route's own code or message), `status_code`, and the body the route
    sent as `detail` - so the chatbot's turn trace carries the reason instead of a
    rendered line that reads as an answer."""
    status = int(data.get("status_code") or 0)
    detail = {
        k: v for k, v in data.items()
        if k not in ("status_code", "error", "http_error", "path", "method", "suggested_escalation")
    }
    return {
        "result_type": tool_name,
        "error": str(data.get("http_error") or data.get("error") or f"{tool_name} returned HTTP {status}"),
        "status_code": status,
        "detail": detail,
        "response": "",
        "answers": [],
        "attachments": [],
        "has_result": False,
    }


def present_response(tool_name: str, raw: str) -> str:
    """Transform a sanitized tool JSON string into the render envelope JSON string.

    Falls back to returning `raw` unchanged on any parse error or unknown shape, so
    enabling `view=render` can never harden into a hard failure for the caller.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if not isinstance(data, dict):
        return raw

    # CHATBOT-SELFREF-SCOPE B3: a non-2xx answer (`http_client.http_error_body` stamps
    # `status_code`) is an ERROR envelope for every presenter tool, never a rendered
    # "could not run" line with `has_result: true`. The chatbot lane reads `error` off
    # the envelope (`lanes/business/__init__.py`, the check before `output_structurer`)
    # and writes the whole envelope to the turn trace, so the status and the route's
    # own `code` / `detail` are what the operator reads.
    if isinstance(data.get("status_code"), int) and data["status_code"] >= 400:
        return json.dumps(_http_error_envelope(tool_name, data))

    # S4 point 5 (AC-1114b): `crm_outstanding_report` never goes through the
    # generic item/field envelope below - the report's shape (two named
    # blocks, each with its own By location / By customer subgroup, D6) has no
    # row list to build items from. A `detail` key on the payload is the SAME
    # "payload-keyed presenter swap" the `so_outstanding` bucket already uses
    # a few lines down (`data.get("order_status") == "so_outstanding"`):
    # present, it swaps in `_outstanding_detail` for that scope; absent, the
    # report itself. Both ride in a MINIMAL envelope (`response` + `has_result`)
    # rather than the generic item/field one: the chatbot lane's own
    # `output_structurer` uses `response` as the reply verbatim
    # (`app/services/chatbot/lanes/business/fetch.py`), and it needs `has_result`
    # because the rendered header is never empty - the text alone cannot tell a
    # total miss (which must escalate, AC-1107) from a hit.
    if tool_name == "crm_outstanding_report":
        return json.dumps(_outstanding_envelope(data))

    # S3 (PLAN-chatbot-sales-report.md): the SAME bypass, for the SAME reason - the
    # delivered report (a Total, period lines, or a ranked drill, then the numbered
    # drill-down options) has no row list the generic envelope could build items from.
    # A body carrying `group_by` renders the drill through the same presenter.
    if tool_name == "crm_sales_report":
        return json.dumps(_sales_report_envelope(data))

    # PLAN-chatbot-top-x-hot-selling-24sep.md S4: the SAME bypass, for the same
    # reason - a ranking plus a header, a count-only how-many question, or one
    # code's detail, none of which the generic item/field envelope could build.
    if tool_name == "crm_top_selling_report":
        return json.dumps(_top_selling_envelope(data))

    # The same bypass, for the same reason: the low stock report's payload is a STATUS
    # (ready / pending / busy) plus an attachment list, not a row collection the generic
    # item/field envelope could build items from. `attachments` rides through untouched -
    # the route decides what a Respond.io attachment entry looks like, and re-shaping it
    # here would be a second copy of that contract.
    if tool_name == "crm_low_stock_report":
        return json.dumps(_low_stock_envelope(data))

    # The same bypass again: the sales analysis answers a table AND a file (or a question,
    # or a refusal), never a row collection (PLAN-retail-sales-reports-26sep R4.2).
    if tool_name == "crm_sales_analysis":
        return json.dumps(_sales_analysis_envelope(data))

    rows = data.get("data")
    if not isinstance(rows, list):
        rows = [] if rows is None else ([rows] if isinstance(rows, dict) else [])

    b = _Builder()
    stock_mode = _stock_mode(tool_name, data)
    # A3 (AC-905): a bucket swap on the SAME tool, not a second tool - the
    # backend echoes `order_status` back on the payload (rows carry no other
    # marker a presenter that never sees the query params could key on).
    so_outstanding = (
        tool_name == "crm_order_management_orders_list"
        and data.get("order_status") == "so_outstanding"
    )
    # The row->item builder for THIS tool/bucket, reused below for `groups[]`
    # (A3, AC-905/AC-906) so a grouped section renders identically to the flat
    # list - one mapping, never a second one that could drift from it.
    row_builder = _orders_so_outstanding if so_outstanding else _BUILDERS.get(tool_name, _generic)
    dealer = tool_name in _INCOMING_TOOLS and data.get("dealer_view") is True
    if tool_name == "crm_portal_link_get":
        _portal_link(data, b)
    elif dealer:
        _incoming_dealer(rows, b)
    elif stock_mode == "compact":
        _stock_compact(data, b)
    elif stock_mode == "availability":
        _stock_availability(data, b)
    elif so_outstanding:
        _orders_so_outstanding(rows, b)
    else:
        row_builder(rows, b)

    # de-dupe attachments by (url, filename). The `url` is the DB `file_path` and is
    # the ONLY resolvable object key - return it verbatim. Do NOT rewrite its last
    # segment from `filename` (the editable `stored_filename`): the stored/display
    # name often differs from the real key (parens/spaces sanitized at upload), so
    # patching it in produced URLs that 404 (e.g. a promotion whose stored_filename
    # kept "(CABANA) … END USER.pdf" while the object key is "CABANA … END USER.pdf").
    seen: set[tuple[Any, Any]] = set()
    attachments: list[dict[str, Any]] = []
    for a in b.attachments:
        sig = (a["url"], a.get("filename"))
        if sig in seen:
            continue
        seen.add(sig)
        attachments.append(a)

    has_result = bool(b.items or attachments or b.action_links)
    if attachments:
        intro = "I have attached the file(s) below."
    elif not has_result:
        # An empty answer over more than one company has to name the companies it
        # searched, or the reader cannot tell "not stocked anywhere" from "I only
        # looked at one of your two companies".
        searched = _company_names(data.get("lookup_companies"))
        intro = (
            f"No matching results found for {searched}."
            if searched
            else "No matching results found."
        )
    elif dealer:
        # The per-product lines and the refer line are the whole reply.
        intro = ""
    elif stock_mode == "compact":
        intro = _STOCK_COMPACT_INTRO
    elif stock_mode == "availability":
        intro = _availability_intro(data)
    elif so_outstanding:
        intro = "Here is the outstanding SO I found."
    else:
        intro = _DEFAULT_INTRO.get(tool_name, "Here are the results I found.")

    envelope: dict[str, Any] = {
        "result_type": (
            "incoming_dealer"
            if dealer
            else "so_outstanding"
            if so_outstanding
            else _STOCK_MODE_RESULT_TYPE.get(stock_mode) or _RESULT_TYPE.get(tool_name, "result")
        ),
        "intro": intro,
        "items": b.items,
        "attachments": attachments,
        "action_links": b.action_links,
        "last_updated_at": _latest_updated(data),
        "has_result": has_result,
    }
    if dealer and has_result:
        # Printed after the product lines (`output_structurer`): a dealer's contact
        # point is their salesman, never a team offer.
        envelope["closing"] = REFER_TO_SALESMAN
    for k in _PASSTHROUGH_KEYS:
        if k in data and _filled(data.get(k)):
            envelope[k] = data[k]
    if stock_mode == "availability" and "stock_availability" in envelope:
        envelope["stock_availability"] = _stamp_refers(envelope["stock_availability"])
    # QS-8: the summary in the ITEM shape, printed by the same renderer as the
    # rows. Only over a real answer (has_result AND rows on the page); absent
    # otherwise, never []. Exception boundary: a hostile leaf inside `summary`
    # must cost the summary, never the envelope the rows already rendered into.
    if has_result and b.items and isinstance(data.get("summary"), dict):
        try:
            _sitems = summary_items(data["summary"])
            _sintro = summary_intro(data["summary"], len(b.items))
        except Exception as _exc:  # pragma: no cover - by contract
            logger.warning("summary_items skipped: %s", _exc)
            _sitems, _sintro = [], None
        if _sitems:
            envelope["summary_items"] = _sitems
            if _sintro:
                envelope["intro"] = _sintro
    # A2 (amended 8 Sep 2026): the presenter-computed block (stock's Open SO / Available
    # lines) - independent of the `data.get("summary")` gate above, because this tool's
    # backend sends no `summary` dict at all; everything it needs is already in `rows`.
    if b.summary_items:
        envelope["summary_items"] = b.summary_items + envelope.get("summary_items", [])
    if b.restricted_fields:
        envelope["restricted_fields"] = dict(b.restricted_fields)
    if b.spec_vocabulary:
        envelope["spec_vocabulary"] = dict(b.spec_vocabulary)
    # Uniform group_by (A3, AC-905/AC-906): the backend already bucketed the
    # rows into `groups: [{key, label, rows}]`; render each bucket's `rows`
    # through the SAME row->item mapping the flat list used, so a grouped
    # section is never a different shape from the ungrouped answer.
    raw_groups = data.get("groups")
    if isinstance(raw_groups, list) and raw_groups:
        rendered_groups: list[dict[str, Any]] = []
        for grp in raw_groups:
            if not isinstance(grp, dict):
                continue
            grp_rows = grp.get("rows") if isinstance(grp.get("rows"), list) else []
            gb = _Builder()
            try:
                row_builder(grp_rows, gb)
            except Exception as _exc:  # pragma: no cover - one bad group must not cost the rest
                logger.warning("group render skipped: %s", _exc)
                continue
            rendered_groups.append(
                {"key": grp.get("key"), "label": grp.get("label"), "items": gb.items}
            )
        if rendered_groups:
            envelope["groups"] = rendered_groups
    _annotate_field_access(envelope, tool_name)
    return json.dumps(envelope)


# --------------------------------------------------------------------------
# outstanding report (SO backlog / DO pending) - PLAN-chatbot-outstanding-report.md
# --------------------------------------------------------------------------
# These two functions render the WHOLE WhatsApp reply directly as a string, not an
# envelope: the report's shape (two named blocks, each with its own By location /
# By customer subgroup, D6) does not fit the generic item/field envelope every
# other tool builds above, and `present_response` never dispatches to them. The
# chatbot business lane (S4) calls `_outstanding_report` / `_outstanding_detail`
# straight, the same way `fetch.py`'s comments already describe this module as the
# row->item mapping other lane code reuses.
#
# `report` is the shape `GET /api/v1/order-management/outstanding-report` returns
# (S2). The header's location line is built from TWO keys on that body, both of
# which the route echoes back from the caller's own query (review round, 13 Sep
# 2026 - the presenter used to read a `location_codes` key that existed only in
# the mock, so a live turn always printed `Location: all`):
#   - `location_token`: the raw location word from the message (`"IB"`), echo-only.
#   - `warehouse_codes`: the exact codes the lane resolved it to, which the route
#     already echoes because they are what it filtered on.
# `so_refused` is the third echo-only key: D13 withheld the SO block, and the one
# sentence that says so prints between the header and the DO block.
# The `do` half is PENDING DOs only (owner ruling, 13 Sep 2026) - `pending_qty`,
# `do_count`, the date range and the two breakdowns, every one of them a pending
# figure; a delivered DO reaches none of them, so no `do_qty` / `delivered_qty`
# exists to print.
# `so` / `do` is `None` when that scope was not asked (D1); present with
# `so_count`/`do_count` == 0 keeps the block's own title but collapses its body to
# one miss line (AC-1107, "the approved shape keeps the block titles") and drops
# that scope from the detail offer, so a miss never advertises a list with
# nothing in it. A partial miss (one scope empty, the other not) prints each
# block independently - the empty one as title + miss line, the other in full.


SO_NOT_ENABLED_MESSAGE = "Sales order figures are not enabled for your account."


def _outstanding_fmt_int(v: Any) -> str:
    """Thousands-separated integer (D8): ``2411`` -> ``"2,411"``. A value that is not
    a clean int passes through as its plain string - a hostile mock must never crash
    the reply."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _outstanding_ddmmyyyy(iso: Any) -> str:
    """``"2026-01-05"`` -> ``"05/01/2026"`` - the ONLY date form this report ever
    prints (D8/AC-1104)."""
    return datetime.strptime(str(iso), "%Y-%m-%d").strftime("%d/%m/%Y")


def _outstanding_date_range(from_iso: Any, to_iso: Any) -> str:
    """No window -> ``"all"``; a single day prints once; otherwise ``"dd/mm/yyyy to
    dd/mm/yyyy"``. Never "oldest" / "newest" / "since" (AC-1104)."""
    if not _filled(from_iso) and not _filled(to_iso):
        return "all"
    if _filled(from_iso) and _filled(to_iso):
        if from_iso == to_iso:
            return _outstanding_ddmmyyyy(from_iso)
        return f"{_outstanding_ddmmyyyy(from_iso)} to {_outstanding_ddmmyyyy(to_iso)}"
    return _outstanding_ddmmyyyy(from_iso or to_iso)


def _outstanding_label(value: Any) -> str:
    """A missing warehouse or a missing CUSTOMER renders as ``Unassigned`` (D8
    transparency) - never hidden, never elided, and never Python's ``None``, which is
    what the owner's smoke test read under *_By customer_* on 13 Sep 2026
    (``None: 178 (O/S: 178)``). One word for both, because to the reader they are the
    same fact: this row belongs to nobody the record names."""
    return str(value) if _filled(value) else "Unassigned"


def _outstanding_location_header(token: Any, codes: Any) -> str:
    """``"IB"`` resolved to two codes -> ``"IB (BRW-IB, MWH-IB)"``; an exact code
    prints alone (the token already equals the one code - brackets would only
    repeat it, AC-1105); no token, or a token that resolved to nothing (AC-1133,
    the lane's job, not this function's), prints ``"all"``."""
    resolved = [c for c in (codes or []) if _filled(c)]
    if not _filled(token) or not resolved:
        return "all"
    if len(resolved) == 1 and str(resolved[0]).casefold() == str(token).casefold():
        return str(resolved[0])
    return f"{token} ({', '.join(str(c) for c in resolved)})"


def _outstanding_group(rows: list, *, name_key: str, total_key: str, outstanding_key: str) -> list[str]:
    """One breakdown group's lines, `name: total (O/S: outstanding)`, in the order given.

    R10: the ROUTE ranks them (outstanding desc, total desc, name asc) and this prints
    them as they arrive - one sort, in the place that knows the numbers, so the reply and
    any later reader agree. R13: the same renderer serves By location, By customer and By
    product, because the owner's ruling is that the three read identically and only the
    subject decides which of them the reader needs.
    """
    return [
        f"{_outstanding_label(row.get(name_key))}: "
        f"{_outstanding_fmt_int(row.get(total_key))} "
        f"(O/S: {_outstanding_fmt_int(row.get(outstanding_key))})"
        for row in rows
    ]


def _outstanding_second_group(report: dict, prefix: str) -> tuple[str, list]:
    """Which group follows By location, and its rows (R13): `*_By customer_*` for a
    product subject, `*_By product_*` for a customer subject, neither when the report was
    asked about both (the route sends only the group the reader needs, and sends the other
    as no key at all)."""
    by_customer = report.get(f"{prefix}_by_customer")
    if by_customer is not None:
        return "customer_name", by_customer
    by_product = report.get(f"{prefix}_by_product")
    if by_product is not None:
        return "product_code", by_product
    return "", []


def _outstanding_so_block(
    so: dict, by_location: list, second_rows: list, second_key: str
) -> str:
    lines = [
        "*Sales order outstanding*",
        # R7 (owner testing round 3, 13 Sep 2026): the COUNT opens both blocks, then the
        # quantities, then the window - one line order for the two, so the reader learns
        # it once.
        f"Sales orders: {_outstanding_fmt_int(so.get('so_count'))}",
        f"Ordered: {_outstanding_fmt_int(so.get('ordered_qty'))}",
        f"Transferred to DO: {_outstanding_fmt_int(so.get('transferred_qty'))}",
        f"Outstanding: {_outstanding_fmt_int(so.get('outstanding_qty'))}",
        f"Order date range: {_outstanding_date_range(so.get('order_date_min'), so.get('order_date_max'))}",
        "*_By location_*",
    ]
    lines.extend(
        _outstanding_group(
            by_location, name_key="code", total_key="ordered_qty", outstanding_key="outstanding_qty"
        )
    )
    if second_key:
        lines.append("*_By customer_*" if second_key == "customer_name" else "*_By product_*")
        lines.extend(
            _outstanding_group(
                second_rows, name_key=second_key, total_key="ordered_qty",
                outstanding_key="outstanding_qty",
            )
        )
    return "\n".join(lines)


def _outstanding_do_block(
    do: dict, by_location: list, second_rows: list, second_key: str
) -> str:
    """R6/R7 (owner testing round 3, 13 Sep 2026): the DO block says OUTSTANDING, never
    "pending", and states the delivered figure beside it - "need to show the delivered
    also, so the by location and by customer needs to be the DO qty (O/S: {pending}) so
    DO qty minus pending should be those quantity delivered".

    Same five-line order as the SO block above (count, quantities, window), and the same
    `name: total (O/S: outstanding)` breakdown shape - over EVERY DO in scope here, so a
    location or customer whose outstanding is 0 still prints `(O/S: 0)` rather than being
    dropped. `Delivery orders` stays the count of DOs still outstanding, which is what the
    detail list below then shows."""
    lines = [
        "*Delivery order outstanding*",
        f"Delivery orders: {_outstanding_fmt_int(do.get('do_count'))}",
        f"DO qty: {_outstanding_fmt_int(do.get('do_qty'))}",
        f"Delivered: {_outstanding_fmt_int(do.get('delivered_qty'))}",
        f"Outstanding: {_outstanding_fmt_int(do.get('pending_qty'))}",
        f"DO date range: {_outstanding_date_range(do.get('do_date_min'), do.get('do_date_max'))}",
        "*_By location_*",
    ]
    lines.extend(
        _outstanding_group(
            by_location, name_key="code", total_key="do_qty", outstanding_key="pending_qty"
        )
    )
    if second_key:
        lines.append("*_By customer_*" if second_key == "customer_name" else "*_By product_*")
        lines.extend(
            _outstanding_group(
                second_rows, name_key=second_key, total_key="do_qty", outstanding_key="pending_qty"
            )
        )
    return "\n".join(lines)


def _outstanding_header_lines(report: dict) -> list[str]:
    """The four lines that say WHAT WAS SEARCHED - the report's own scope, always all
    four, `all` where the filter was not given (R13/R19).

    One writer, three readers: the report, the detail list (owner hand pass 3, row 6 -
    "the detail list starts at 1. DO Number with no Product / Customer / Location / Order
    date header", so the reader of a list could not tell what it was a list OF), and the
    chatbot lane's own copy of the rule for the scope question it asks before either
    exists.

    #1262 slice 9 (F1a): a FIFTH line, `Brand:`, ADDITIVE only - printed when
    `brand_name` is filled, absent otherwise (unlike the four above, it never falls
    back to "all": a report with no brand filter simply never named one, the same
    reason `location_token`/`so_refused` are tacked-on-only elsewhere on this body).
    """
    lines = [
        # R13: `all` when no product was named, the same word the other header lines use
        # for "every one of them" - a customer-subject report is about all their products.
        f"Product: {report.get('product_code') if _filled(report.get('product_code')) else 'all'}",
        f"Customer: {report.get('customer_name') if _filled(report.get('customer_name')) else 'all'}",
        f"Location: {_outstanding_location_header(report.get('location_token'), report.get('warehouse_codes'))}",
        f"Order date: {_outstanding_date_range(report.get('order_date_from'), report.get('order_date_to'))}",
    ]
    if _filled(report.get("brand_name")):
        lines.append(f"Brand: {report['brand_name']}")
    return lines


def _outstanding_report(report: dict) -> str:
    """The SO backlog / DO pending reply (PLAN-chatbot-outstanding-report.md, "The
    reply (contract for Phase 1)"). See the module-level note above for the
    `report` shape."""
    lines = _outstanding_header_lines(report)

    blocks: list[str] = []
    offer: list[str] = []

    so = report.get("so")
    if so is not None:
        if not so.get("so_count"):
            blocks.append("*Sales order outstanding*\nNo open sales order.")
        else:
            so_second_key, so_second_rows = _outstanding_second_group(report, "so")
            blocks.append(
                _outstanding_so_block(
                    so, report.get("so_by_location") or [], so_second_rows or [], so_second_key
                )
            )
            offer.append("Sales order list")

    do = report.get("do")
    if do is not None:
        if not do.get("do_count"):
            blocks.append("*Delivery order outstanding*\nNo outstanding delivery order.")
        else:
            do_second_key, do_second_rows = _outstanding_second_group(report, "do")
            blocks.append(
                _outstanding_do_block(
                    do, report.get("do_by_location") or [], do_second_rows or [], do_second_key
                )
            )
            offer.append("Delivery order list")

    text = "\n".join(lines)
    # D13/AC-1141: the withheld half is named HERE, between the header and the block
    # that did run - never in front of the whole reply, which reads as a refusal of the
    # question itself. The wording is the one literal the chatbot lane also holds
    # (`fetch.SO_NOT_ENABLED_MESSAGE`); the backend image cannot import this package, so
    # the two copies are pinned by a test on each side.
    if report.get("so_refused"):
        text += "\n\n" + SO_NOT_ENABLED_MESSAGE
    if blocks:
        text += "\n\n" + "\n\n".join(blocks)
    if len(offer) == 1:
        # R9 (owner testing round 3, 13 Sep 2026): one option is a sentence, not a list -
        # a numbered list of one asks the reader to choose from a single thing. The
        # position is still 1, so the pick resolves exactly as it does for two.
        text += f"\n\nReply 1 for the {offer[0].lower()}."
    elif offer:
        # R14 (owner ruling, 13 Sep 2026): with both lists on offer, asking for both at
        # once is a third option rather than two round trips.
        options = "\n".join(f"{i + 1}. {label}" for i, label in enumerate([*offer, "Both lists"]))
        text += "\n\n" + "Reply with a number for detail:\n" + options
    return text


# field order fixed by scope (AC-1106); `kind` picks the value formatter below
# (`label` is the Unassigned-when-missing one, shared by Customer and Location).
_OUTSTANDING_SO_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("SO Number", "so_number", "text"),
    ("Customer", "customer_name", "label"),
    # R13: a row names both axes, so a customer-subject list says which product each row
    # is for and a product-subject list still reads the same way.
    ("Product", "product_code", "label"),
    ("Location", "location", "label"),
    ("Ordered", "ordered_qty", "qty"),
    ("Transferred to DO", "transferred_qty", "qty"),
    ("Outstanding", "outstanding_qty", "qty"),
    ("Order Date", "order_date", "date"),
)
#: R3 (owner ruling, 13 Sep 2026): a DETAIL ROW states all three quantities, and
#: `Delivered` prints its 0 rather than being hidden. The BLOCK above the list is
#: pending-only (R1) - these are two different readers' questions: the block answers
#: "how much is still coming", the row answers "what is this DO".
_OUTSTANDING_DO_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("DO Number", "do_number", "text"),
    ("Customer", "customer_name", "label"),
    ("Product", "product_code", "label"),
    ("Location", "location", "label"),
    ("DO Qty", "do_qty", "qty"),
    ("Delivered", "delivered_qty", "qty"),
    # R7: the row says "Outstanding" like the block; the JSON field stays `pending_qty`.
    ("Outstanding", "pending_qty", "qty"),
    ("DO Date", "do_date", "date"),
)


def _outstanding_envelope(report: dict) -> dict:
    """What `present_response` returns for `crm_outstanding_report`: the rendered text
    plus the one fact the text cannot carry.

    `has_result` is "a block that was asked for came back with rows" - `so_count` or
    `do_count` for the report, the row list for a detail render. It is what the chatbot
    lane reads to send a TOTAL miss down the existing escalate path (AC-1107); reading
    the text instead can only ever say "yes", because the header renders either way.
    """
    detail = report.get("detail")
    if detail in ("so", "do", "both"):
        rows_present = (
            bool(report.get("so_rows")) or bool(report.get("do_rows"))
            if detail == "both"
            else bool(report.get(f"{detail}_rows"))
        )
        # Row 6 (owner hand pass 3): the list carries the SAME scope header the summary
        # prints. The list itself is unchanged - `_outstanding_detail` renders the rows
        # and nothing else, which is what its own goldens pin - and the header is added
        # here, where the summary's is, so the two cannot drift.
        return {
            "result_type": "outstanding_detail",
            "response": "\n".join(_outstanding_header_lines(report))
            + "\n\n"
            + _outstanding_detail(report, detail),
            "has_result": rows_present,
        }
    so = report.get("so")
    do = report.get("do")
    return {
        "result_type": "outstanding_report",
        "response": _outstanding_report(report),
        "has_result": bool(
            (isinstance(so, dict) and so.get("so_count"))
            or (isinstance(do, dict) and do.get("do_count"))
        ),
    }


#: The three lines the low stock report can answer with (AC-61). Written here, once, and
#: handed to the lane verbatim: one writer, one wording.
_LOW_STOCK_PENDING = "Preparing the low stock report - it will be sent here when ready."
#: A test turn's pending (the console, `dry_run`): the route never hands it to the worker,
#: so nothing is pushed; the file still lands in My Downloads.
_LOW_STOCK_PENDING_DRY_RUN = (
    "Preparing the low stock report - test turn: it will be in My Downloads, "
    "nothing is sent to WhatsApp."
)
#: Both busy lines NAME the report (console round 3, defect C): "a plan is already running"
#: alone left the reader - and the console assertion - guessing which plan, and the two
#: busies have different fixes (wait a minute vs wait ten).
_LOW_STOCK_BUSY_IN_FLIGHT = (
    "A low stock report plan is already running - try again in a minute."
)
_LOW_STOCK_BUSY_RATE_LIMITED = (
    "Too many low stock reports in the last 10 minutes - try again shortly."
)
_LOW_STOCK_ERROR = "Could not run the low stock report right now."
#: defect D: a scope the plan admitted nothing for. Saying "Low: 0 of 0 planned products"
#: beside an empty workbook reads as a broken report; this says what actually happened.
_LOW_STOCK_EMPTY = "Nothing was planned for that scope - no low stock report to send."


def _low_stock_as_of(iso: Any) -> str:
    """dd/mm/yyyy, the only date form this product writes for a reader. An unparseable or
    absent stamp prints as it arrived rather than as today's date: a run that froze no rows
    has no as-of, and inventing one would date a book that was never built."""
    try:
        return _outstanding_ddmmyyyy(iso)
    except (TypeError, ValueError):
        return str(iso) if iso else "-"


def _low_stock_envelope(payload: dict) -> dict:
    """What `present_response` returns for `crm_low_stock_report` (AC-61).

    Four shapes:

    * `ready`   - two lines, the as-of date and how many of the planned products are low.
                  The "of <m>" half is what stops "Low: 12" reading as the whole
                  catalogue. The workbook itself rides in `attachments`, which is what
                  makes the engine emit a `send_attachments` action. `has_result` is False
                  when a count is missing (a `ready` payload that lost the count-write
                  race), so the count line is never rendered as "Low: None of None".
    * `pending` - the plan outran the turn; the worker pushes the file when it is ready
                  (AC-44), so the bot says so and stops.
    * `busy`    - one line, chosen by `reason`: a plan already running for the company
                  (AC-49) or this contact over the rate limit. Both NAME the report.
    * `ready` with `all_count == 0` (or no `as_of`) - the plan admitted nothing for that
      scope: one line, no attachment, rather than "Low: 0 of 0" beside an empty workbook.
    * anything else (an `error` status, an error body carrying `message`/`code`, or a
      non-dict) - the report could not be produced (no company, an excluded product, a
      broker down, a run the worker marked failed). The bot says so and STOPS: the fixed
      "could not run" line, verbatim, `has_result` True.

    `has_result` is True on every branch - each IS a terminal answer the bot gives. It is
    NOT the miss path (reviewer S1 / AC-44a / N6 wanted "never pending"; the console then
    found that a `has_result` False here routes into the inventory domain's GENERIC miss,
    "Could not find inventory - escalate to warehouse team?", which is the wrong wording
    for a run that failed). Rendering the error line verbatim, like busy / pending, is what
    keeps the bot on THIS tool's own words. No UUID reaches the text - the payload carries
    `run_id` and `download_id`, and neither is a thing to say to a customer.
    """
    status = payload.get("status") if isinstance(payload, dict) else None
    if status == "ready":
        low = payload.get("low_count")
        total = payload.get("all_count")
        # defect D: the plan admitted NOTHING for this scope. The workbook is empty and
        # `as_of` is null, so there is no report to send - say that in one line and send no
        # attachment, rather than "Low: 0 of 0 planned products" beside an empty file.
        if total == 0 or payload.get("as_of") in (None, ""):
            return {
                "result_type": "low_stock_report",
                "response": _LOW_STOCK_EMPTY,
                "attachments": [],
                "has_result": True,
            }
        # A ready row that somehow reached here without its counts (reviewer S3 guards this
        # server-side, but the presenter must not print "Low: None of None"): send the file,
        # drop the count line, and let the lane treat the shorter reply as a real answer.
        if low is None or total is None:
            return {
                "result_type": "low_stock_report",
                "response": f"Low stock report - as of {_low_stock_as_of(payload.get('as_of'))}",
                "attachments": payload.get("attachments") or [],
                "has_result": True,
            }
        response = "\n".join((
            f"Low stock report - as of {_low_stock_as_of(payload.get('as_of'))}",
            f"Low: {low} of {total} planned products",
        ))
        return {
            "result_type": "low_stock_report",
            "response": response,
            "attachments": payload.get("attachments") or [],
            "has_result": True,
        }
    if status == "busy":
        # defect C: WHICH busy. `in_flight` clears in about a minute; `rate_limited` needs
        # the window to roll. An unknown/absent reason keeps the in-flight wording, which
        # is the one a caller hits without doing anything wrong.
        reason = payload.get("reason") if isinstance(payload, dict) else None
        busy_line = (
            _LOW_STOCK_BUSY_RATE_LIMITED if reason == "rate_limited"
            else _LOW_STOCK_BUSY_IN_FLIGHT
        )
        return {"result_type": "low_stock_report", "response": busy_line,
                "attachments": [], "has_result": True}
    if status == "pending":
        line = _LOW_STOCK_PENDING_DRY_RUN if payload.get("dry_run") is True else _LOW_STOCK_PENDING
        return {"result_type": "low_stock_report", "response": line,
                "attachments": [], "has_result": True}
    # Unknown / error / a raw error body: the error line verbatim, never pending and never
    # the generic inventory miss.
    return {"result_type": "low_stock_report", "response": _LOW_STOCK_ERROR,
            "attachments": [], "has_result": True}


_OUTSTANDING_BOTH_HEADINGS = {"so": "*Sales order list*", "do": "*Delivery order list*"}


def _outstanding_detail(report: dict, scope: str) -> str:
    """Numbered SO (``scope="so"``) or DO (``scope="do"``) list (AC-1106), served
    from the SAME report the presenter already rendered - no second fetch, no
    re-parse (D10). Every row renders, in whatever order `so_rows` / `do_rows`
    already carry (the route sorts them, per the backend contract); "no `+N
    more`" - D8 - falls out of this loop never truncating."""
    if scope == "both":
        # R14: one reply, both lists, each under a heading so the reader can tell which
        # one they are in. SO first, the same order the offer lists them.
        parts = [
            f"{_OUTSTANDING_BOTH_HEADINGS[one]}\n{_outstanding_detail(report, one)}"
            for one in ("so", "do")
        ]
        return "\n\n".join(parts)

    if scope == "so":
        rows = report.get("so_rows") or []
        field_defs = _OUTSTANDING_SO_FIELDS
    else:
        rows = report.get("do_rows") or []
        field_defs = _OUTSTANDING_DO_FIELDS

    items: list[str] = []
    for i, row in enumerate(rows, start=1):
        field_lines = []
        for label, key, kind in field_defs:
            value = row.get(key)
            if kind == "qty":
                value = _outstanding_fmt_int(value)
            elif kind == "label":
                value = _outstanding_label(value)
            elif kind == "date" and _filled(value):
                value = _outstanding_ddmmyyyy(value)
            field_lines.append(f"*{label}:* {value}")
        items.append(f"{i}. " + "\n".join(field_lines))
    return "\n\n".join(items)


# --------------------------------------------------------------------------
# sales report (delivered basis, v4) - PLAN-chatbot-selfref-scope-30sep.md
# --------------------------------------------------------------------------
# The reply to `GET /api/v1/order-management/sales-report` (mock v4,
# `documentation/mockups/sales-report/index.html`, owner-approved 2 Oct 2026). The
# body is the route's delivered report over `delivery_order_lines`: header echoes
# (`customer_name`, `product_code` / `product_codes`, `channel` + `channel_shown`,
# `location_token` + `warehouse_codes`, `date_from` / `date_to`), `total {qty,
# amount}`, `periods[]` (latest first, the route's grain), and for a drill
# `group_by` + ranked `rows[]` + `more`, then `options[]` (the drill-downs offered).
#
# The numbering of the options is written HERE and only here: the options continue
# after the rows actually printed, and `_sales_report_envelope` hands the same
# numbers to the lane (`options[{idx, label, value, aliases}]`), which arms the
# `sales_report_detail` open question from them.
#
# Reused from the outstanding section above: `_outstanding_fmt_int`,
# `_outstanding_ddmmyyyy` / `_outstanding_date_range`, `_outstanding_label`,
# `_outstanding_location_header`.


def _rm_money(v: Any) -> str:
    """``RM 1,234.50`` (AC-1609); zero prints ``RM 0.00``. Thousands-separated,
    unlike ``_money()`` above (which prints ``MYR value`` with no separator for a
    different tool)."""
    try:
        return f"RM {float(v):,.2f}"
    except (TypeError, ValueError):
        return f"RM {v}"


_SALES_MONTH_ABBR = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _sales_month_label(month_key: Any) -> str:
    """``"2026-09"`` (or ``"2026-09-15"``) -> ``"Sep 2026"``. Hand-rolled rather than
    ``strftime("%b")`` so the abbreviation never depends on the process locale. An
    unparseable key passes through as its plain string - a hostile mock must never
    crash the reply."""
    s = str(month_key or "").strip()
    try:
        year, month = s.split("-")[:2]
        return f"{_SALES_MONTH_ABBR[int(month)]} {year}"
    except (ValueError, IndexError, TypeError):
        return s


def _sales_channel_header(channel: Any) -> str:
    """``"dealer"`` -> ``"Dealer"``, ``"project"`` -> ``"Project"``, anything else
    (absent, null-class) -> ``"all"`` (S8). The top selling header's word."""
    s = str(channel).strip().lower() if _filled(channel) else ""
    if s == "dealer":
        return "Dealer"
    if s == "project":
        return "Project"
    return "all"


def _sales_report_channel(channel: Any) -> str:
    """The sales report's Channel line (mock v4): the dealer channel is Retail."""
    s = str(channel).strip().lower() if _filled(channel) else ""
    if s in ("dealer", "retail"):
        return "Retail"
    if s == "project":
        return "Project"
    return "all"


def _sales_product_header(typed_code: Any, codes: Any) -> str:
    """S19 second fix round (owner ruling, 19 Sep 2026): the Product line is the
    COMMA-SEPARATED list of every code the typed stem COVERS. One covered code prints
    bare; more than 10 collapses to a count; no product at all prints ``"all"``. An
    empty list with a typed code falls back to the bare typed code."""
    if not _filled(typed_code):
        return "all"
    resolved = [c for c in (codes or []) if _filled(c)]
    if len(resolved) > 10:
        return f"{typed_code} ({len(resolved)} products)"
    if len(resolved) >= 1:
        return ", ".join(str(c) for c in resolved)
    return str(typed_code)


SALES_REPORT_MISS_MESSAGE = "No sales found."

# The drill title per `group_by`; the DO list's title carries no "delivery date" words
# (mock v4, captain ruling 6).
_SALES_DRILL_TITLES = {
    "customer": "*By customer*, delivery date",
    "product": "*By product*, delivery date",
    "sales_agent": "*By sales agent*, delivery date",
    "delivery_order": "*Delivery orders*,",
}

# The typed words a pick may use besides its number, per option value. The lane's label
# match reads these (`turn/decide.py::_positions_by_label`).
_SALES_OPTION_ALIASES = {
    "customer": ["by customer"],
    "product": ["by product"],
    "sales_agent": ["by sales agent"],
    "delivery_order": ["DO", "delivery order", "delivery orders"],
}


def _sales_qty_amount(entry: Any) -> str:
    entry = entry if isinstance(entry, dict) else {}
    return f"Qty {_outstanding_fmt_int(entry.get('qty'))}, {_rm_money(entry.get('amount'))}"


def _sales_period_label(period: dict, grain: Any) -> str:
    start, end = period.get("from"), period.get("to")
    if grain == "month":
        return _sales_month_label(start)
    if grain == "week":
        try:
            first = datetime.strptime(str(start), "%Y-%m-%d").strftime("%d/%m")
            last = datetime.strptime(str(end), "%Y-%m-%d").strftime("%d/%m")
        except (TypeError, ValueError):
            return f"{start} to {end}"
        return first if first == last else f"{first} to {last}"
    try:
        return _outstanding_ddmmyyyy(start)
    except (TypeError, ValueError):
        return str(start)


def _sales_report_header(report: dict) -> list[str]:
    customer = report.get("customer_name")
    lines = [
        f"*Customer:* {customer if _filled(customer) else 'all'}",
        f"*Product:* {_sales_product_header(report.get('product_code'), report.get('product_codes'))}",
    ]
    if report.get("channel_shown"):
        lines.append(f"*Channel:* {_sales_report_channel(report.get('channel'))}")
    if _filled(report.get("location_token")):
        lines.append(
            f"*Location:* {_outstanding_location_header(report.get('location_token'), report.get('warehouse_codes'))}"
        )
    lines.append(f"*Delivery date:* {_outstanding_date_range(report.get('date_from'), report.get('date_to'))}")
    return lines


def _sales_report_rows(report: dict) -> list[dict]:
    rows = report.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _sales_report_periods(report: dict) -> list[dict]:
    periods = report.get("periods")
    return [p for p in periods if isinstance(p, dict)] if isinstance(periods, list) else []


def _sales_report_options(report: dict) -> list[dict]:
    """The numbered drill-down roster: ONE writer of the numbering. Options continue
    after the rows the reply prints (rows 1 to N, options N+1...)."""
    raw = report.get("options")
    if not isinstance(raw, list):
        return []
    start = len(_sales_report_rows(report)) if _filled(report.get("group_by")) else 0
    options: list[dict] = []
    for opt in raw:
        if not isinstance(opt, dict) or not _filled(opt.get("key")):
            continue
        value = str(opt["key"])
        options.append(
            {
                "idx": start + len(options) + 1,
                "label": str(opt.get("label") or value),
                "value": value,
                "aliases": list(_SALES_OPTION_ALIASES.get(value, [])),
            }
        )
    return options


def _sales_report_has_result(report: dict) -> bool:
    """A period or a drill row came back (a drill body may carry either or both)."""
    return bool(_sales_report_periods(report)) or bool(_sales_report_rows(report))


def _sales_report_drill_lines(report: dict) -> list[str]:
    group_by = str(report.get("group_by"))
    window = _outstanding_date_range(report.get("date_from"), report.get("date_to"))
    title = _SALES_DRILL_TITLES.get(group_by, f"*By {group_by.replace('_', ' ')}*, delivery date")
    lines = [f"{title} {window}"]
    for i, row in enumerate(_sales_report_rows(report), start=1):
        name = _outstanding_label(row.get("name"))
        if group_by == "delivery_order":
            parts = [name]
            if _filled(row.get("date")):
                try:
                    parts.append(_outstanding_ddmmyyyy(row.get("date")))
                except (TypeError, ValueError):
                    parts.append(str(row.get("date")))
            if _filled(row.get("customer_name")):
                parts.append(str(row.get("customer_name")))
            name = ", ".join(parts)
        lines.append(f"{i}. {name}: {_sales_qty_amount(row)}")
    try:
        more = int(report.get("more") or 0)
    except (TypeError, ValueError):
        more = 0
    if more > 0:
        lines.append(f"and {_outstanding_fmt_int(more)} more")
    lines.append(f"*Total:* {_sales_qty_amount(report.get('total'))}")
    return lines


def _sales_report(report: dict) -> str:
    """The full WhatsApp reply (mock v4, AC-SR-27). A refusal prints its message alone;
    a default view prints the header, the Total and one line per period (in the order
    given, the ROUTE sorts); a drill prints its title, the rows, `and N more` and the
    Total; both end with the numbered drill-down options. No periods (or no rows) prints
    the header and "No sales found." with no options."""
    if report.get("status") == "refused":
        return str(report.get("message") or "")
    if not _sales_report_has_result(report):
        return "\n".join(_sales_report_header(report)) + "\n\n" + SALES_REPORT_MISS_MESSAGE
    if _filled(report.get("group_by")):
        text = "\n".join(_sales_report_drill_lines(report))
    else:
        grain = report.get("grain")
        body = [f"*Total:* {_sales_qty_amount(report.get('total'))}"]
        body.extend(
            f"{_sales_period_label(p, grain)}: {_sales_qty_amount(p)}" for p in _sales_report_periods(report)
        )
        text = "\n".join(_sales_report_header(report)) + "\n\n" + "\n".join(body)
    options = _sales_report_options(report)
    if options:
        text += "\n\n*Drill down:*\n" + "\n".join(f"{o['idx']}. {o['label']}" for o in options)
    return text


def _sales_report_envelope(report: dict) -> dict:
    """What `present_response` returns for `crm_sales_report`: the rendered text plus
    the facts the text cannot carry.

    `has_result` is "a period (or a drill row) came back" - the header renders on a
    total miss too, so reading the TEXT would call a miss an answer and the lane's
    escalate offer (AC-1658) would never fire. A refusal is a final answer
    (`has_result` true, no options). `options` is the numbered drill-down roster the
    lane arms its `sales_report_detail` open question from (AC-SR-28)."""
    if report.get("status") == "refused":
        return {
            "result_type": "sales_report",
            "response": _sales_report(report),
            "has_result": True,
            "options": [],
        }
    has_result = _sales_report_has_result(report)
    return {
        "result_type": "sales_report",
        "response": _sales_report(report),
        "has_result": has_result,
        "options": _sales_report_options(report) if has_result else [],
    }


# --------------------------------------------------------------------------
# top X hot selling (PLAN-chatbot-top-x-hot-selling-24sep.md; S1 presenter, S4
# wired to the route's own body as built on PR #1263). The same minimal envelope
# as the sales report for the same reason: the header renders on a miss too, so
# only `has_result` can tell the lane a miss (escalate) from a hit. Reuses the
# sales report's and the outstanding report's formatters directly (money,
# quantity, dates, channel, months) so the three replies cannot drift apart.
#
# Body (`TopSellingResponse`): `rank_by` quantity|amount, `basis`, `group`, `n`,
# `date_from`/`date_to` (the resolved window), `filters {customer_name,
# category_name, sales_agent, channel, dealer_scoped}`, `total_count`, `rows
# [{rank, code, name, quantity, amount}]`, `totals`, `sales_agent_fill_rate`
# (0 to 1, only under an agent filter) and `detail` (the detail offer's answer).
# --------------------------------------------------------------------------

# Fixed lines the lane sends BEFORE any fetch (owner: no default metric, clarify
# when unsure whether a category is a filter or the ranking grain, or which basis
# an ambiguous word means). Declared here so the goldens and the lane read one
# literal each.
TOP_SELLING_ASK_METRIC = "By quantity or by amount?"
TOP_SELLING_ASK_GROUP = (
    "Do you want the top items inside one category, or the categories ranked against each other?"
)
TOP_SELLING_ASK_BASIS = "Delivered (transferred to DO) or ordered?"
TOP_SELLING_REFUSED_OTHER_CUSTOMER = "Sorry, I can only share sales figures for your own account."

# The shared safety ceiling on a named N (owner, 30 Sep 2026: no 100 cap, "100, 200").
# A COPY of the backend's `sales_report_service.TOP_SELLING_N_CEILING` (this package cannot
# import the backend); `tests/test_presenters_top_selling.py` pins the two equal.
TOP_SELLING_N_CEILING = 1000
# One WhatsApp message's budget: the platform limit is 4096 characters, and the headroom
# is for the line the lane may put above the first part and the "(k/m)" marker.
WHATSAPP_MESSAGE_MAX_CHARS = 3900
# The agent fill-rate note prints below this share (plan "The reply": the route
# sends the rate whenever an agent filter is used, the presenter decides).
_TOP_SELLING_AGENT_NOTE_BELOW = 0.95


def _top_selling_is_category(report: dict) -> bool:
    return report.get("group") == "category"


def _top_selling_filters(report: dict) -> dict:
    filters = report.get("filters")
    return filters if isinstance(filters, dict) else {}


def _top_selling_count(report: dict) -> int:
    try:
        return int(report.get("total_count") or 0)
    except (TypeError, ValueError):
        return 0


def _top_selling_agent_note(report: dict) -> str | None:
    rate = report.get("sales_agent_fill_rate")
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return None
    if rate >= _TOP_SELLING_AGENT_NOTE_BELOW:
        return None
    return f"Note: only {int(round(rate * 100))}% of sales orders in this period carry a sales agent."


def _top_selling_axes(report: dict) -> list[str]:
    """Every filter axis, ``all`` where none was named, the agent note under its
    line, then the window. Shared by the ranking and the detail reply."""
    filters = _top_selling_filters(report)

    def _or_all(key: str) -> str:
        value = filters.get(key)
        return str(value) if _filled(value) else "all"

    lines = [
        f"Customer: {_or_all('customer_name')}",
        f"Category: {_or_all('category_name')}",
        # Fix lane round 4 (owner retest, 27 Sep 2026): the brand filter.
        f"Brand: {_or_all('brand_name')}",
        f"Sales agent: {_or_all('sales_agent')}",
    ]
    note = _top_selling_agent_note(report)
    if note:
        lines.append(note)
    lines.append(f"Channel: {_sales_channel_header(filters.get('channel'))}")
    lines.append(f"Delivery date: {_outstanding_date_range(report.get('date_from'), report.get('date_to'))}")
    return lines


def _top_selling_metric_lines(report: dict) -> list[str]:
    return [
        f"Ranked by: {'Amount' if report.get('rank_by') == 'amount' else 'Quantity'}",
        f"Basis: {'Ordered' if report.get('basis') == 'ordered' else 'Delivered (transferred to DO)'}",
    ]


def _top_selling_is_bottom(report: dict) -> bool:
    return report.get("direction") == "bottom"


#: Fix lane round 4 (owner retest, 27 Sep 2026): how a least sold ranking treats an
#: item that sold nothing. The plan never ranks one (the route ranks sales), so the
#: reply says so instead of letting "least sold" read as "sold least of everything".
TOP_SELLING_NO_SALE_LINE = "Items with no sale in this period are not ranked."


def _top_selling_title(report: dict) -> str:
    """``*Top N selling items*`` / ``*Top selling items*``; a bottom ranking reads
    ``*Bottom N selling items*`` / ``*Least sold items*`` (owner retest, 27 Sep 2026:
    "cold selling" and "least sold" rank ascending)."""
    noun = "categories" if _top_selling_is_category(report) else "items"
    n = report.get("n")
    if _filled(n):
        one = str(n) == "1"
        word = ("category" if noun == "categories" else "item") if one else noun
        return f"*{'Bottom' if _top_selling_is_bottom(report) else 'Top'} {n} selling {word}*"
    return f"*Least sold {noun}*" if _top_selling_is_bottom(report) else f"*Top selling {noun}*"


def _top_selling_header(report: dict) -> str:
    """Title, metric, basis, the FULL count (owner: the header states it), then
    every filter axis."""
    noun = "categories" if _top_selling_is_category(report) else "items"
    lines = [
        _top_selling_title(report),
        *_top_selling_metric_lines(report),
        f"{noun.capitalize()} with sales: {_outstanding_fmt_int(_top_selling_count(report))}",
    ]
    if _top_selling_is_bottom(report):
        lines.append(TOP_SELLING_NO_SALE_LINE.replace("Items", noun.capitalize()))
    return "\n".join([*lines, *_top_selling_axes(report)])


def _top_selling_code(row: dict) -> str:
    """The row's CODE only, never its name (owner, 26 Sep 2026: "don't need to show
    name, just show code will do"). A product with no category ranks under a null
    code and prints ``Unassigned``."""
    code = row.get("code")
    return str(code) if _filled(code) else "Unassigned"


def _top_selling_row(row: dict) -> str:
    """``n. CODE: Qty q, RM v``; ``n`` is the body's own rank: the route sorts,
    never this."""
    return (
        f"{row.get('rank')}. {_top_selling_code(row)}: Qty {_outstanding_fmt_int(row.get('quantity'))}, "
        f"{_rm_money(row.get('amount') or 0)}"
    )


def _top_selling_rows(report: dict) -> list[dict]:
    rows = report.get("rows") if isinstance(report.get("rows"), list) else []
    return [r for r in rows if isinstance(r, dict)][:TOP_SELLING_N_CEILING]


def whatsapp_parts(text: str, limit: int = WHATSAPP_MESSAGE_MAX_CHARS) -> list[str]:
    """`text` as WhatsApp-sized messages, in order. One part when it fits (unmarked,
    so a short reply reads exactly as before); otherwise lines are packed greedily,
    never splitting a line (one row is one line), and every part opens with its own
    ``(k/m)`` line. The caller joins the parts with a blank line; the backend engine
    (`engine.split_marked_message`) sends each marked part as its own message.

    The marker's width is reserved in every part's budget, so a part plus its marker
    stays within `limit`. A single line longer than the budget goes out on its own."""
    if len(text) <= limit:
        return [text]
    budget = limit - len("(9999/9999)\n")
    parts: list[list[str]] = [[]]
    size = 0
    for line in text.split("\n"):
        if not parts[-1] and not line.strip():
            continue  # a part never opens on a blank line
        grown = size + len(line) + (1 if parts[-1] else 0)
        if parts[-1] and grown > budget:
            while parts[-1] and not parts[-1][-1].strip():
                parts[-1].pop()  # nor closes on one
            parts.append([])
            size = 0
            if not line.strip():
                continue
            grown = len(line)
        parts[-1].append(line)
        size = grown
    chunks = ["\n".join(p) for p in parts if p]
    total = len(chunks)
    return [f"({i}/{total})\n{chunk}" for i, chunk in enumerate(chunks, start=1)]


def _top_selling_detail(report: dict) -> str:
    """The detail offer's answer: one code's customers and months under the SAME
    filters and basis the ranking ran with, each list in the route's own order
    (the ranking metric desc). A code with no sales has no `detail` and takes the
    miss line instead (`_top_selling`)."""
    detail = report.get("detail") if isinstance(report.get("detail"), dict) else {}
    code = detail.get("code") if _filled(detail.get("code")) else "Unassigned"
    lines = [f"*{code}: customers and months*", *_top_selling_metric_lines(report), *_top_selling_axes(report)]
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    lines.append(
        f"Total: Qty {_outstanding_fmt_int(totals.get('quantity'))}, {_rm_money(totals.get('amount') or 0)}"
    )
    customers = [r for r in (detail.get("by_customer") or []) if isinstance(r, dict)]
    months = [r for r in (detail.get("by_month") or []) if isinstance(r, dict)]
    blocks = ["\n".join(lines)]
    if customers:
        blocks.append(
            "*_By customer_*\n"
            + "\n".join(
                f"{i}. {_outstanding_label(r.get('customer_name'))}: Qty "
                f"{_outstanding_fmt_int(r.get('quantity'))}, {_rm_money(r.get('amount') or 0)}"
                for i, r in enumerate(customers, start=1)
            )
        )
    if months:
        blocks.append(
            "*_By month_*\n"
            + "\n".join(
                f"{_sales_month_label(r.get('month'))}: Qty "
                f"{_outstanding_fmt_int(r.get('quantity'))}, {_rm_money(r.get('amount') or 0)}"
                for r in months
            )
        )
    # Grill Q6 (owner, 30 Sep 2026): a code bought by hundreds of customers is split too.
    return "\n\n".join(whatsapp_parts("\n\n".join(blocks)))


def _top_selling(report: dict) -> str:
    """The whole WhatsApp reply for one route body. Four shapes:

    * `detail` present: the detail offer's answer (customers and months);
    * rows present: the ranking plus the detail offer (owner: required);
    * no rows but a count: the message named no N (the lane asked the route for the
      count only), so state the count (the header) and ask how many (owner rulings
      26 Sep: no default N, no partial list, no "more");
    * no rows and no count: the miss line.

    Length never cuts the list (owner, 30 Sep 2026: "100, 200"): a ranking longer
    than one WhatsApp message is split here, between rows (`whatsapp_parts`), so the
    delivery does not lean on n8n chunking it."""
    if isinstance(report.get("detail"), dict):
        return _top_selling_detail(report)
    header = _top_selling_header(report)
    category = _top_selling_is_category(report)
    rows = _top_selling_rows(report)
    if not rows:
        total = _top_selling_count(report)
        if total > 0:
            noun = "categories" if category else "items"
            return (
                header + f"\n\nHow many {noun} do you want to see? "
                f"Reply with a number from 1 to {min(total, TOP_SELLING_N_CEILING)}."
            )
        return header + "\n\n" + SALES_REPORT_MISS_MESSAGE
    offer = (
        "Reply with a rank number to see that category's top items."
        if category
        else "Reply with a rank number to see that item's customers and months."
    )
    body = "\n".join(_top_selling_row(r) for r in rows)
    return "\n\n".join(whatsapp_parts(header + "\n\n" + body + "\n\n" + offer))


def _top_selling_pick_row(row: dict, *, category: bool) -> dict:
    """One printed line as a pick row (owner, PR #1258 05:32Z: the list behaves like
    the customer and product pickers). The `{idx, label, code, entity_type}` roster
    shape with no `name` (owner ruling 26 Sep ~07:40Z: code only, and the route sends
    none); `idx` is the printed rank so a later "2" means line 2, and the label is the
    code the line printed, which is what a typed answer matches exactly
    (`turn/decide._positions_by_label`)."""
    return {
        "idx": row.get("rank"),
        "label": _top_selling_code(row),
        "code": row.get("code"),
        "entity_type": "category" if category else "product",
    }


def _top_selling_envelope(report: dict) -> dict:
    """What `present_response` returns for `crm_top_selling_report`. The how-many
    reply is not a miss (nothing to escalate), so it carries `has_result: true`
    under its own `result_type`, which the lane reads to arm nothing. `result_set`
    is the pick list the lane arms as a sticky `top_selling_pick` roster (backend
    `turn/pending.top_selling_pick`); empty whenever no ranked list was printed,
    the detail reply included (the list it answers stays open on its own).

    A route refusal arrives here as the AppException body (`{message, detail,
    code}`, no `rank_by`): the two the customer is meant to read become their fixed
    lines, and anything else is an `error` envelope the lane treats as a failure."""
    if "rank_by" not in report and _filled(report.get("code")):
        code = str(report.get("code"))
        refusal = {
            "customer_not_permitted": TOP_SELLING_REFUSED_OTHER_CUSTOMER,
            "sales_report_not_enabled": "Sales report is not enabled for your account.",
        }.get(code)
        if refusal is None:
            return {"error": f"{code}: {report.get('message') or ''}".strip()}
        return {
            "result_type": "top_selling_refused",
            "response": refusal,
            "has_result": True,
            "result_set": [],
        }
    if isinstance(report.get("detail"), dict):
        return {
            "result_type": "top_selling_detail",
            "response": _top_selling(report),
            "has_result": True,
            "result_set": [],
        }
    rows = _top_selling_rows(report)
    how_many = not rows and _top_selling_count(report) > 0
    category = _top_selling_is_category(report)
    return {
        "result_type": "top_selling_how_many" if how_many else "top_selling",
        "response": _top_selling(report),
        "has_result": bool(rows) or how_many,
        "result_set": [_top_selling_pick_row(r, category=category) for r in rows],
    }


# --------------------------------------------------------------------------------------
# crm_sales_analysis (PLAN-retail-sales-reports-26sep S1; Owner ruling 26 Sep 07:16 Q2,
# "always text + file, no cutoff").
#
# The text IS the answer: a header (report and company, channel, basis, period, the row
# count), one line per row, then the totals line - every row, whatever the count (n8n
# chunks a long message). One value column reads `label: RM a`; two or more read
# `label: a | b | c` under a line naming the columns (Q9 (a): every column on every line).
# A negative prints in brackets, an empty cell "-". The Excel of the same query rides in
# `attachments`; when it is still being built the text ends "The Excel follows here." and
# the worker pushes it. A question or a refusal is one line and no file (AC-R4-3).
# --------------------------------------------------------------------------------------

_SALES_ANALYSIS_PENDING = "The Excel follows here."
_SALES_ANALYSIS_FILE_FAILED = "Could not build the sales report Excel right now."
_SALES_ANALYSIS_ERROR = "Could not run the sales report right now."


def _sales_figure(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001 - a value the route never sends; print it as is
        return str(value)
    text = f"{abs(amount):,.2f}"
    return f"({text})" if amount < 0 else text


def _sales_analysis_text(payload: dict) -> str:
    lines = [
        f"*{payload.get('report') or 'Sales'}, {payload.get('company') or ''}*".replace(", *", "*"),
        f"Channel: {payload.get('channel') or 'All channels'}",
        f"Basis: {payload.get('basis') or ''}",
        f"Period: {payload.get('period') or ''}",
        f"{payload.get('count_label') or 'Rows'}: {payload.get('total_count', 0)}",
        "",
    ]
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    totals = (payload.get("totals") or {}).get("values") or []
    if len(columns) == 1:
        def _rm(value: Any) -> str:
            # A month with no sales is "-", never "RM -".
            text = _sales_figure(value)
            return text if text == "-" else f"RM {text}"

        for row in rows:
            values = row.get("values") or [None]
            lines.append(f"{row.get('label')}: {_rm(values[0])}")
        lines.append(f"Total: {_rm(totals[0] if totals else None)}")
    else:
        lines.append(f"{payload.get('rows_label') or 'Row'}: " + " | ".join(str(c) for c in columns))
        for row in rows:
            lines.append(
                f"{row.get('label')}: " + " | ".join(_sales_figure(v) for v in row.get("values") or [])
            )
        lines.append("Total: " + " | ".join(_sales_figure(v) for v in totals))
    return "\n".join(lines)


def _sales_analysis_envelope(payload: dict) -> dict:
    status = payload.get("status")
    envelope = {"result_type": "sales_analysis", "attachments": [], "has_result": True}
    if status in ("clarify", "refused", "busy"):
        return {**envelope, "response": str(payload.get("message") or _SALES_ANALYSIS_ERROR)}
    if status not in ("ready", "pending", "error") or not isinstance(payload.get("rows"), list):
        return {**envelope, "response": _SALES_ANALYSIS_ERROR}
    text = _sales_analysis_text(payload)
    if status == "pending":
        return {**envelope, "response": f"{text}\n\n{_SALES_ANALYSIS_PENDING}"}
    if status == "error":
        return {**envelope, "response": f"{text}\n\n{_SALES_ANALYSIS_FILE_FAILED}"}
    attachments = payload.get("attachments")
    return {
        **envelope,
        "response": text,
        "attachments": attachments if isinstance(attachments, list) else [],
    }
