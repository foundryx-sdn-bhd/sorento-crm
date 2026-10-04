"""Canonical shapes for the DOCUMENTS the ESB pushes: sales and purchase orders.

The masters' rules all hold here (`canonical_masters`): canonical rather than
AutoCount, unknown keys dropped and logged, related records addressed by reference rather than
by a Sorento id. Three things are different, and each is a property of a
document rather than of a master:

**A document owns its lines.** They arrive nested, not as a second entity, so
one push is one atomic statement about the whole order. Splitting them would let
a header land without its lines - an order for nothing, which the netting reads
as fully covered demand.

**Its line key is the line's own.** `source_ref` on a line is AutoCount's
`DtlKey`, not a position: a line deleted upstream must not silently renumber the
ones after it onto each other's allocations. Two lines carrying the same key in
one record is therefore a validation failure rather than a last-one-wins, since
picking one would drop a quantity somebody ordered.

**Its status is a vocabulary.** Five canonical words - `open`, `partial`,
`fulfilled`, `closed`, `cancelled` - map onto two different Sorento vocabularies
(sales orders and purchase orders do not share theirs). The mapping lives in
`document_ingest_service`; what is fixed here is that `status` is required. A
document with no state is not a document anyone can plan against.

Money and quantity are `Decimal`, never float: these figures are summed into
what a customer is charged.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.canonical_masters import _Canonical
from app.schemas.ingest_extras import INGEST_MODEL_CONFIG, note_unknown_fields
from app.utils.rtf import strip_rtf

#: Each entry of `from_so_numbers` (V4) - length-capped like every other
#: document number on this surface, never a full row on its own.
_SoNumber = Annotated[str, Field(max_length=100)]


class _SalesOrderExternalRef(BaseModel):
    """The cross-book case of `from_so_line_ref` (V5): the sales order lives
    in ANOTHER AutoCount database, so its key does not resolve here. Recorded
    raw and NEVER resolved into a Sorento id - the key belongs to a book this
    system does not hold, and there is nothing local to point it at.

    `db` is required WHEN THIS OBJECT IS PRESENT: a cross-book ref naming no
    book cannot be told apart from any other, so the object as a whole stays
    optional at the line's own field rather than this one being optional
    inside it. `doc_key`/`doc_no`/`dtl_key` are whatever the ESB knows about
    the OTHER book's document - none of them a join key here.
    """

    model_config = INGEST_MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _note_unknown(cls, data):
        note_unknown_fields(cls, data)
        return data

    db: str = Field(..., min_length=1, max_length=100)
    doc_key: Optional[int] = None
    doc_no: Optional[str] = Field(None, max_length=100)
    dtl_key: Optional[int] = None


class _CanonicalLine(BaseModel):
    """Shared line rules. Not `_Canonical`: a line has no `source_doc_no`."""

    model_config = INGEST_MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _note_unknown(cls, data):
        note_unknown_fields(cls, data)
        return data

    # AutoCount's DtlKey. The upsert key within the document, which is what lets
    # a line keep its Sorento id - and therefore its allocations - across syncs.
    source_ref: str = Field(..., min_length=1, max_length=255)
    # Integration reference of the product, never a code: a code is unique per
    # company only, while the ref already names one row. Optional ONLY in the
    # sense that `product_code` may stand in for it (v2, D1) - the validator
    # below still requires one of the two, because `product_id` is NOT NULL on
    # both line tables and a line without either is not a line that can exist.
    product_ref: Optional[str] = Field(None, max_length=255)
    # v2 code/name fallback (D1). Products are never back-created - a code the
    # catalogue does not hold stays retryable, the same as an unresolved ref -
    # so `product_name` is accepted and never used: a typo must never become a
    # SKU. `warehouse_code` follows the same ref-then-code ladder as the ref,
    # but an unresolved SENT code lands NULL with a warning rather than
    # retryable (D10) - a warehouse is optional, a product is not.
    product_code: Optional[str] = Field(None, max_length=100)
    product_name: Optional[str] = Field(None, max_length=255)
    warehouse_ref: Optional[str] = Field(None, max_length=255)
    warehouse_code: Optional[str] = Field(None, max_length=100)
    qty_ordered: Decimal = Field(..., ge=0)
    discount: Optional[Decimal] = None
    line_total: Optional[Decimal] = None
    uom: Optional[str] = Field(None, max_length=100)
    # AutoCount's Seq (D11). Position only, for telling apart ref-less rows
    # that share the same (product, warehouse, outstanding) key at cutover -
    # and, since PLAN-so-lines-autocount-order.md, ALSO persisted verbatim as
    # `sales_order_lines.line_no` (the Lines tab's own ordering address). A
    # purchase-order line still has no column for it - popped there as before.
    line_number: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def _product_ref_or_code(self):
        if not self.product_ref and not self.product_code:
            raise ValueError("product_ref or product_code is required")
        return self


class CanonicalSalesOrderLine(_CanonicalLine):
    qty_delivered: Optional[Decimal] = Field(Decimal("0"), ge=0)
    unit_price: Optional[Decimal] = None
    # PER LINE, not per header: one order routinely carries several dates and the
    # coverage timeline has no time axis without them (ADR-0011).
    required_date: Optional[date] = None


class CanonicalPurchaseOrderLine(_CanonicalLine):
    qty_received: Optional[Decimal] = Field(Decimal("0"), ge=0)
    unit_cost: Optional[Decimal] = None
    currency: Optional[str] = Field(None, max_length=3)
    expected_date: Optional[date] = None
    # V4 (plan section 2.5): the sales orders the ESB already knows this
    # purchase line is FOR. `str_strip_whitespace` (the model config above)
    # already strips each entry; blank ones are dropped here rather than
    # rejected - an ESB that sends `["SO-A", ""]` names one real number, not
    # a bad one. Absent or `[]` both mean "nothing to claim" (schema pin,
    # AC-V4 tests) - never a trigger of its own.
    from_so_numbers: Optional[list[_SoNumber]] = Field(None, max_length=50)
    # V5 (AutoCount linkage widen): the EXACT sales-order line this purchase
    # line was raised for, when it lives in the SAME database - joinable to
    # `sales_order_lines.source_ref`, same convention as this line's own
    # `source_ref` (`"{database}:{DocKey}:{DtlKey}"`). A resolvable ref lets
    # the claim resolver skip the number+item-code match `from_so_numbers`
    # relies on, which cannot tell apart two lines of the same item on one
    # sales order. Absent when it is simply not yet known - NOT mutually
    # exclusive with `from_so_external` below (retracted guarantee, see its
    # own comment): the two come from different AutoCount columns and can
    # both be sent on one line.
    from_so_line_ref: Optional[str] = Field(None, max_length=255)
    # V5: the cross-book case - see `_SalesOrderExternalRef`. MAY be present
    # ALONGSIDE `from_so_line_ref` on the SAME line - AutoCount's
    # `FromSODtlKey` (same-book, gives `from_so_line_ref`) and the ICB
    # plugin's UDFs (gives this field) are different columns describing
    # different books, and a line raised from a same-book sales order AND
    # tagged by the ICB plugin legitimately carries both (the ESB withdrew
    # an earlier guarantee that this never happens - no validator was ever
    # written to enforce it, so there is nothing to relax here beyond this
    # comment). The two never describe the SAME book; this one is recorded
    # and never resolved into a Sorento id, because its key belongs to a
    # database Sorento does not hold.
    from_so_external: Optional[_SalesOrderExternalRef] = None
    # V5: the SOURCE purchase-order line this one was raised from (an
    # inter-company book transfer chain), same `source_ref` format.
    from_po_line_ref: Optional[str] = Field(None, max_length=255)
    # V5: the source purchase order's own document number.
    from_po_number: Optional[str] = Field(None, max_length=100)

    @field_validator("from_so_numbers")
    @classmethod
    def _drop_blank_so_numbers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return [v for v in value if v] if value is not None else None


class _CanonicalDocument(_Canonical):
    # Canonical, mapped per document type in `document_ingest_service`. Required:
    # a document whose state nobody stated cannot be told from an open one, and
    # that difference is the whole of demand planning.
    status: str = Field(..., min_length=1, max_length=50)
    # Declared here so the validator below can read it, and re-declared with the
    # concrete line type by each subclass - which is what actually parses. `Any`
    # rather than `list[_CanonicalLine]` only because a mutable field's type is
    # invariant, so a narrowing subclass would be an override error for a
    # relationship that is correct.
    # `max_length` (SEC5): a cap on the CARDINALITY of the list, not on any one
    # line's own size - an unbounded array is the DOS surface a length limit
    # elsewhere on the line does not close.
    lines: list[Any] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def _line_refs_are_unique(self):
        """One DtlKey, one line.

        Two lines sharing a key cannot both be upserted onto it. Last-one-wins
        would drop a quantity the customer ordered, and it would do so silently
        on every sync - so the record is refused and quarantined instead.
        """
        seen: set[str] = set()
        duplicates: set[str] = set()
        for line in self.lines:
            if line.source_ref in seen:
                duplicates.add(line.source_ref)
            seen.add(line.source_ref)
        if duplicates:
            raise ValueError(f"duplicate line source_ref: {', '.join(sorted(duplicates))}")
        return self


class CanonicalSalesOrder(_CanonicalDocument):
    """`source_ref` is AutoCount's DocKey; `so_number` its DocNo.

    Both are needed and they are not interchangeable: the DocKey is stable and
    is what makes a re-push idempotent, while the DocNo is what the extract
    importer already wrote into `so_number` and is therefore the only thing a
    FIRST sync can adopt an existing row by.
    """

    # D20 (S2): optional here - absent, `document_ingest_service` derives it
    # via `document_rules.derive_document_status`. `CanonicalShippingOrder`
    # gets the same override for the same reason (D20 for SPO, S3) - see its
    # own comment below.
    status: Optional[str] = Field(None, min_length=1, max_length=50)
    so_number: str = Field(..., min_length=1, max_length=100)
    customer_ref: Optional[str] = Field(None, max_length=255)
    # v2 code/name fallback (D1/D2). `customer_code` is written to
    # `sales_orders.debtor_code` whenever sent, ref or no ref (D9); a customer
    # is back-created only when BOTH are present (D2) - the unique index is on
    # the pair, and a code-only row would collide with a later named one.
    # `max_length=50` (SEC4): matches `customers.customer_code`'s own
    # `String(50)` column - a longer value can only ever fail at INSERT.
    customer_code: Optional[str] = Field(None, max_length=50)
    customer_name: Optional[str] = Field(None, max_length=255)
    # D16/D8 (S2): used ONLY when back-creating a customer off (customer_code,
    # customer_name) - never applied to an existing row (that is what the
    # customers master push's own market_segment_code/region are for).
    customer_segment: Optional[str] = Field(None, max_length=50)
    customer_region: Optional[str] = Field(None, max_length=80)
    sales_agent_ref: Optional[str] = Field(None, max_length=255)
    agent_code: Optional[str] = Field(None, max_length=100)
    # v2 demand classification (D4). FILL-only: written to `sales_orders.order_type`
    # only when the header holds none yet, the same rule `customer_code` follows for
    # `debtor_code`. `demand_class` itself stays an unaccepted key (AC-V2-7) - it is
    # DERIVED from this ladder, never stated directly, so a payload naming it outright
    # would let a document claim a priority nothing here actually earned.
    order_type: Optional[str] = Field(None, max_length=50)
    doc_date: Optional[date] = None
    requested_delivery_date: Optional[date] = None
    # Capped so an unbounded note cannot reach `label_from_note` on every push (security
    # review, SPL-B1) - AutoCount's own note control has never printed anything close to
    # 8000 characters. The cap is enforced AFTER the validator below strips the RTF
    # wrapper, not before it: a legitimately short note under RTF markup can easily run
    # past 8000 raw characters and must not be rejected for it.
    internal_note: Optional[str] = Field(None, max_length=8000)
    # AutoCount `SO.Ref` - PLAN-so-project-label.md. Free text, and read for a project
    # name (rule 3, `app.services.project_label_rules.label_from_ref`) only when the note
    # names none. Same precedent as `CanonicalShippingOrder.container_number` below,
    # which carries `PO.Ref` for the same reason: a field AutoCount already prints that
    # this contract had never accepted before.
    ref: Optional[str] = Field(None, max_length=255)
    # AutoCount `SO.Transferable` (PLAN-so-transferable.md). AutoCount prints `T`/`F`, which
    # Pydantic's lax bool already reads, case-insensitive, alongside its other fixed boolean
    # words (`true`/`false`, `yes`/`no`, `1`/`0`, ...); anything outside that set fails the
    # record rather than guessing whether an order is demand. Absent or null leaves the
    # stored value alone, so an ESB build that does not send it blanks nothing.
    transferable: Optional[bool] = None
    lines: list[CanonicalSalesOrderLine] = Field(default_factory=list, max_length=2000)

    @field_validator("internal_note", mode="before")
    @classmethod
    def _plain_text_note(cls, value: Any) -> Any:
        """AutoCount's note control pushes raw RTF - cleaned here, once, at the edge.

        Every reader (`SalesOrderDetail`, `project_fulfilment_board_service`, SCM, MCP)
        reads the stored column, so a validator here is what keeps them all plain rather
        than each one re-deriving it from `{\\rtf1...}`.

        `mode="before"` - runs BEFORE the field's own `max_length=8000`, and truncates to
        it here rather than letting that check reject the push: RTF markup routinely runs
        several times longer than the plain text it wraps, so a legitimately short note is
        rejected on its raw byte count if the cap is checked before stripping. Truncating
        (not rejecting) the rare oversized PLAIN note is the same choice `label_from_ref`
        and every other free-text field on this contract makes - cap the input, do not
        fail the document over a note nobody reads past the first paragraph anyway.

        A non-string value (an int, a list) is passed through unchanged so Pydantic's own
        type check still raises its usual 422 on it - `mode="before"` sees the raw JSON
        value, not the `Optional[str]` this field declares.
        """
        if not isinstance(value, str):
            return value
        plain = strip_rtf(value)
        if plain and len(plain) > 8000:
            plain = plain[:8000]
        return plain


class CanonicalPurchaseOrder(_CanonicalDocument):
    # D20 (S2): same override as CanonicalSalesOrder.status - see its comment.
    status: Optional[str] = Field(None, min_length=1, max_length=50)
    po_number: str = Field(..., min_length=1, max_length=100)
    supplier_ref: Optional[str] = Field(None, max_length=255)
    # v2 code/name fallback (D1). `agent_code` is accepted for symmetry with
    # the sales-order shape but IGNORED here: a purchase order has no agent FK,
    # and there is nowhere on `purchase_orders` for it to land.
    # `max_length=50` (SEC4): matches `suppliers.supplier_code`'s own
    # `String(50)` column.
    supplier_code: Optional[str] = Field(None, max_length=50)
    supplier_name: Optional[str] = Field(None, max_length=255)
    agent_code: Optional[str] = Field(None, max_length=100)
    issue_date: Optional[date] = None
    expected_date: Optional[date] = None
    currency: Optional[str] = Field(None, max_length=3)
    # D6 (S3, AC-P3-7): AutoCount's `UDF_ShipOrder`. A purchase order the
    # payload flags this way is refused the same as an `SPO-` numbered one
    # (`document_ingest_service`'s D5 refusal) - a document number alone
    # misses a differently-numbered shipping order AutoCount still marks as
    # one.
    is_shipping_order: Optional[bool] = None
    lines: list[CanonicalPurchaseOrderLine] = Field(default_factory=list, max_length=2000)


class CanonicalShippingOrderLine(_CanonicalLine):
    """A shipping-order line - one `spo_allocations` row (D3, S3)."""

    qty_received: Optional[Decimal] = Field(Decimal("0"), ge=0)
    unit_cost: Optional[Decimal] = None
    expected_date: Optional[date] = None
    # V4, same rule as `CanonicalPurchaseOrderLine.from_so_numbers` - a
    # shipping-order line dedicates against a sales order exactly as a
    # purchase-order line does (`resolve()` decides which purchase table by
    # `spo_number`'s own family, not by which entity pushed the claim).
    from_so_numbers: Optional[list[_SoNumber]] = Field(None, max_length=50)
    # V5, same fields and same rules as `CanonicalPurchaseOrderLine`'s own -
    # a shipping-order line dedicates against a sales order, and is raised
    # from a purchase order, exactly the way a purchase-order line is.
    from_so_line_ref: Optional[str] = Field(None, max_length=255)
    from_so_external: Optional[_SalesOrderExternalRef] = None
    from_po_line_ref: Optional[str] = Field(None, max_length=255)
    from_po_number: Optional[str] = Field(None, max_length=100)

    @field_validator("from_so_numbers")
    @classmethod
    def _drop_blank_so_numbers(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return [v for v in value if v] if value is not None else None


class CanonicalShippingOrder(_CanonicalDocument):
    """`source_ref` is AutoCount's DocKey; `spo_number` its DocNo.

    Unlike a sales or purchase order, this shape addresses no header table -
    `spo_number` and `spo_line_number` together ARE the identity of the rows
    it writes (D3). `from_so_numbers` (V4) lives on the LINE, the same as a
    purchase-order line's - see `CanonicalShippingOrderLine`.
    """

    # D20 (S3): optional, same override and same reason as
    # `CanonicalSalesOrder.status` - absent, `ShippingOrderIngestService`
    # derives it via `document_rules.derive_document_status` over the
    # pushed lines' own ordered/received quantities (all received = closed,
    # else open) - there is no header row to read an EXISTING status off,
    # unlike the SO/PO case, so derivation never sees anything but `None`.
    status: Optional[str] = Field(None, min_length=1, max_length=50)
    # `max_length=50` (review nit): matches `spo_allocations.spo_number`'s own
    # `String(50)` column - `sales_orders.so_number`/`purchase_orders.po_number`
    # are `String(100)`, which is why only THIS number is narrower.
    spo_number: str = Field(..., min_length=1, max_length=50)
    supplier_ref: Optional[str] = Field(None, max_length=255)
    # `max_length=50` (SEC4): matches `suppliers.supplier_code`'s own
    # `String(50)` column.
    supplier_code: Optional[str] = Field(None, max_length=50)
    supplier_name: Optional[str] = Field(None, max_length=255)
    # Accepted for symmetry with the other two documents but IGNORED here -
    # same reason as `CanonicalPurchaseOrder.agent_code`.
    agent_code: Optional[str] = Field(None, max_length=100)
    issue_date: Optional[date] = None
    expected_date: Optional[date] = None
    # D6 (S3): AutoCount's `PO.Ref` - cleaned through the shared
    # `shipping_order_rules.extract_container_number` before it lands on
    # every allocation of this document.
    container_number: Optional[str] = Field(None, max_length=100)
    currency: Optional[str] = Field(None, max_length=3)
    lines: list[CanonicalShippingOrderLine] = Field(default_factory=list, max_length=2000)


# ------------------------------------------------------------------ billing documents
#: Finance S0 (#1309, contract 2.6). Closed vocabularies, mirrored by the CHECKs on
#: `finance.billing_documents` (`app/models/finance.py`).
BILLING_DOCUMENT_TYPES = ("invoice", "cash_sale", "credit_note", "debit_note")
BILLING_DOCUMENT_STATUSES = ("posted", "cancelled")
#: Types whose line quantities must not be negative (a return is a credit note, never a
#: negative invoice line).
_NON_NEGATIVE_QTY_TYPES = frozenset({"invoice", "cash_sale"})
#: `total` must equal `net_total + tax_total` within one sen.
_TOTAL_TOLERANCE = Decimal("0.01")


#: Bounds matching the columns (`numeric(15,2)`, `numeric(15,4)`, `numeric(7,4)`), so a value
#: the table cannot hold, or an exponent that would overflow the arithmetic, fails its own
#: record by field instead of reaching the database. Non-finite values are refused too.
_Money = Annotated[
    Decimal,
    Field(ge=Decimal("-9999999999999.99"), le=Decimal("9999999999999.99"), allow_inf_nan=False),
]
_Qty4 = Annotated[
    Decimal,
    Field(ge=Decimal("-99999999999.9999"), le=Decimal("99999999999.9999"), allow_inf_nan=False),
]
_Rate4 = Annotated[Decimal, Field(ge=0, le=Decimal("999.9999"), allow_inf_nan=False)]


class CanonicalBillingDocumentLine(BaseModel):
    """One item line of a billing document (plan 3.3.2).

    Unlike an order line, `product_ref`/`product_code` are both optional: a debit note's
    charge line often names no item, and an unresolved product lands NULL with the code kept
    rather than holding the document back (a billing document is money).
    """

    model_config = INGEST_MODEL_CONFIG

    @model_validator(mode="before")
    @classmethod
    def _note_unknown(cls, data):
        note_unknown_fields(cls, data)
        return data

    # AutoCount DtlKey: the line's identity within its document across re-pushes.
    source_ref: str = Field(..., min_length=1, max_length=255)
    line_number: Optional[int] = Field(None, ge=0)
    product_ref: Optional[str] = Field(None, max_length=255)
    product_code: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=8000)
    uom: Optional[str] = Field(None, max_length=20)
    # Sign checked per document type on the header (a credit note may carry a negative).
    quantity: _Qty4
    unit_price: Optional[_Qty4] = None
    discount_amount: Optional[_Money] = None
    net_amount: Optional[_Money] = None
    tax_code: Optional[str] = Field(None, max_length=20)
    tax_rate: Optional[_Rate4] = None
    tax_amount: Optional[_Money] = None
    line_total: Optional[_Money] = None
    # As sent: the SO or DO line this one was transferred from (A6). `from_line_ref` is an
    # SO line's `source_ref` when the shared service can name one.
    from_doc_type: Optional[str] = Field(None, max_length=10)
    from_doc_no: Optional[str] = Field(None, max_length=50)
    from_line_ref: Optional[str] = Field(None, max_length=255)

    @model_validator(mode="before")
    @classmethod
    def _blank_optional_string_is_null(cls, data):
        # Same third state as `_Canonical`: a blank optional string is an explicit null.
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            value = data.get(name)
            if isinstance(value, str) and not value.strip() and not field.is_required():
                data[name] = None
        return data


class CanonicalBillingDocument(_Canonical):
    """An AutoCount billing document header with its lines (plan 3.3.2).

    A push names the WHOLE document: every optional field omitted is stored as null and every
    line not sent is removed, so the stored row is always exactly the last push. `source_ref`
    is `{database}:{IV|CS|CN|DN}:{DocKey}` (A2); `doc_date` has no floor (ruling Q2).
    """

    document_type: str = Field(..., min_length=1, max_length=20)
    doc_no: str = Field(..., min_length=1, max_length=50)
    doc_date: date
    status: str = Field(..., min_length=1, max_length=20)
    # AutoCount LastModified (A3). An aware value is converted to naive UTC.
    source_modified_at: Optional[datetime] = None
    customer_ref: Optional[str] = Field(None, max_length=255)
    # Stored as `debtor_code` (the `sales_orders` rule), and the fallback when the ref misses.
    customer_code: Optional[str] = Field(None, max_length=64)
    customer_name: Optional[str] = Field(None, max_length=255)
    agent_code: Optional[str] = Field(None, max_length=100)
    currency_code: Optional[str] = Field(None, min_length=3, max_length=3)
    currency_rate: Optional[Annotated[Decimal, Field(gt=0, lt=Decimal("1e10"))]] = None
    net_total: _Money
    tax_total: _Money
    total: _Money
    # Net in MYR as AutoCount converted it (A5); what the invoiced basis sums (ruling Q12).
    local_net_total: _Money
    against_doc_no: Optional[str] = Field(None, max_length=50)
    against_source_ref: Optional[str] = Field(None, max_length=255)
    ref: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=8000)
    # SEC5: a cap on the cardinality, the same as every other document.
    lines: list[CanonicalBillingDocumentLine] = Field(default_factory=list, max_length=2000)

    @field_validator("document_type", "status")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("document_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in BILLING_DOCUMENT_TYPES:
            raise ValueError(
                f"unknown document_type {value!r}; expected one of: "
                f"{', '.join(BILLING_DOCUMENT_TYPES)}"
            )
        return value

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in BILLING_DOCUMENT_STATUSES:
            raise ValueError(
                f"unknown status {value!r}; expected one of: "
                f"{', '.join(BILLING_DOCUMENT_STATUSES)}"
            )
        return value

    @field_validator("currency_code")
    @classmethod
    def _upper_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value

    @field_validator("source_modified_at")
    @classmethod
    def _naive_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value


def billing_document_errors(payload: CanonicalBillingDocument) -> dict[str, str]:
    """The cross-field rules (UAC S0-19), keyed by the field they concern.

    Not a `model_validator`: an error raised there carries no location, so the verdict would
    say `_` where the shared service needs to be told which field to fix.
    """
    errors: dict[str, str] = {}
    seen: set[str] = set()
    duplicates: set[str] = set()
    for line in payload.lines:
        if line.source_ref in seen:
            duplicates.add(line.source_ref)
        seen.add(line.source_ref)
    if duplicates:
        errors["lines"] = f"duplicate line source_ref: {', '.join(sorted(duplicates))}"
    if abs(payload.total - (payload.net_total + payload.tax_total)) > _TOTAL_TOLERANCE:
        errors["total"] = (
            f"total {payload.total} is not net_total + tax_total "
            f"({payload.net_total} + {payload.tax_total}) within {_TOTAL_TOLERANCE}"
        )
    if payload.document_type in _NON_NEGATIVE_QTY_TYPES:
        for index, line in enumerate(payload.lines):
            if line.quantity < 0:
                errors[f"lines.{index}.quantity"] = (
                    f"negative quantity on a {payload.document_type} line"
                )
    return errors
