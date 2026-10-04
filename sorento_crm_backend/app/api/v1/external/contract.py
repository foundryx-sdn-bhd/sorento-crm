"""Ingest contract version for the ESB (D8, AC-V0-1; v2.1 shape added S4 of
ingest-parity-standardisation; v2.2 adds the SO<->PO linkage fields below,
ingest-contract-2-2-so-links; v2.3 adds `brands` as a first-class EntitySpec,
autocount-brands-ingest; v2.4 adds products code-wins plus the deletions
`codes` field, ingest-products-code-wins; v2.5 adds `stock_balances`, a push
entity that upserts `stock.quantity_on_hand`, ingest-stock-balances-2-5; v2.6 adds
`billing_documents`, AutoCount invoices, cash sales, credit and debit notes into
`finance.billing_documents`, finance S0 #1309).

The ESB gates every new key it sends behind `sorento_contract_version = 2` on
its consumer connection, so it needs one endpoint to ask Sorento what version
and which entities it currently accepts, without inferring either from trial
and error against `/ingest/{entity}`.

Guarded by its own `integration.contract.read` slug rather than a reused
master slug: reading the contract is not the same act as writing any one
entity, and an integration scoped to a single master should not need a grant
on every other master just to check the version.

v2.1 adds four keys describing WHAT changed since v2, so an ESB integrator
reads the diff off this endpoint rather than a changelog: `fields_added`
(new optional fields, per entity - never required, so a v2-shaped payload
still ingests unchanged), `fields_removed` (D15 end state - these now fail
validation, never accepted-and-warned), `status_optional` (which documents
now derive their own status when the field is absent), and `absent_vs_null`
(D14 - true everywhere: an OMITTED field never overwrites a stored value,
only an explicit `null` does). `warnings` is the fixed vocabulary every
verdict's `warnings`/`lines` keys draw from, imported from the modules that
actually raise each one rather than restated here, so this list cannot drift
from what a real response emits.

v2.2 (`CONTRACT_VERSION` bump, `app/api/v1/external/ingest.py`) widens the
AutoCount linkage `purchase_orders`/`shipping_orders` lines carry:
`from_so_line_ref`, `from_so_external`, `from_po_line_ref`, `from_po_number`.
`from_so_numbers` is also added to `fields_added` here even though it
shipped in v2 (V4) - it was never listed on this endpoint, and the ESB now
reads this diff to confirm it, so an omission here would be as wrong as a
field that was never added.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.external.ingest import CONTRACT_VERSION, SUPPORTED_ENTITIES
from app.schemas.canonical_documents import (
    CanonicalBillingDocument,
    CanonicalBillingDocumentLine,
)
from app.services.autocount_doc_ingest_service import CONTRACT_2_7_WARNINGS
from app.services.finance.billing_document_ingest_service import (
    WARN_AGENT_UNRESOLVED,
    WARN_PRODUCT_UNRESOLVED,
    WARN_STALE_IGNORED,
)
from app.services.master_ref_resolver import (
    WARN_AGENT_CREATED,
    WARN_CUSTOMER_CREATED,
    WARN_CUSTOMER_UNRESOLVED,
    WARN_REF_MISMATCH,
    WARN_SUPPLIER_AMBIGUOUS,
    WARN_SUPPLIER_CREATED,
    WARN_UNCLASSIFIED_DEMAND,
    WARN_WAREHOUSE_UNRESOLVED,
)
from app.services.shipping_order_ingest_service import (
    WARN_CONTAINER_UNRESOLVED,
    WARN_RECEIVED_LOCKED,
)
from app.services.stock_balance_ingest_service import WARN_WAREHOUSE_INACTIVE

router = APIRouter()

# New OPTIONAL fields per entity since v2 - additive only (D-final: nothing
# here was ever required, so a v2-shaped payload still ingests unchanged).
# `is_shipping_order` lives ONLY under `purchase_orders` (review S1, 2026-09-06):
# it is parsed off the `purchase_orders` payload to REFUSE a push there and
# redirect it to `shipping_orders` (D6/AC-P3-7) - `CanonicalShippingOrder`
# itself has no such field and `extra="forbid"` would reject it outright, so
# an earlier "documented under shipping_orders too, since that is the family
# it names" note here was wrong and has been removed.
FIELDS_ADDED: dict[str, list[str]] = {
    "products": ["is_discontinued", "remark", "brand_code", "item_type_code"],
    "customers": ["market_segment_code", "region", "sales_agent_code"],
    "sales_orders": ["customer_segment", "customer_region"],
    "purchase_orders": [
        "is_shipping_order",
        "from_so_numbers",
        "from_so_line_ref",
        "from_so_external",
        "from_po_line_ref",
        "from_po_number",
    ],
    "shipping_orders": [
        "container_number",
        "from_so_numbers",
        "from_so_line_ref",
        "from_so_external",
        "from_po_line_ref",
        "from_po_number",
    ],
    # v2.4 (ingest-products-code-wins, SR0): `POST /ingest/products/deletions`
    # accepts an optional `codes` map (source_ref -> code) alongside
    # `source_refs`, read only for this entity - see FIELD_NOTES below.
    "products_deletions": ["codes"],
    # v2.5 (ingest-stock-balances-2-5, Foundryx SR5): a wholly NEW entity
    # rather than new fields on an existing one - listed here anyway (D2)
    # so the ESB's own diff read sees it without special-casing "entities
    # that are new" apart from "fields that are new" on this endpoint.
    "stock_balances": ["item_code", "location_code", "qty", "item_description", "uom_code"],
    # v2.6: every key of the record and of its lines (`lines.<key>`), read off the canonical
    # schema itself so this list cannot drift from what the ingest accepts. `source_doc_no`
    # is the `_Canonical` key every entity carries and is not new.
    "billing_documents": [
        *(key for key in CanonicalBillingDocument.model_fields if key != "source_doc_no"),
        *(f"lines.{key}" for key in CanonicalBillingDocumentLine.model_fields),
    ],
    # v2.7 (#1354 S2): the record is the AutoCount vendor API's own object, so the list is the
    # envelope key plus the AutoCount keys the CRM reads; every other key is kept as sent.
    "delivery_orders": ["book", "DocKey", "DocNo", "DocDate", "LastModified", "Cancelled",
                        "BranchCode", "DebtorCode", "RefDocNo", "Details",
                        "Details.DtlKey", "Details.FromDocType", "Details.FromDocNo",
                        "Details.FromDocDtlKey"],
    "goods_receive_notes": ["book", "DocKey", "DocNo", "DocDate", "LastModified", "Cancelled",
                            "CreditorCode", "Details", "Details.DtlKey", "Details.OurPONo",
                            "Details.FromDocType", "Details.FromDocNo",
                            "Details.FromDocDtlKey"],
    "branches": ["book", "AccNo", "BranchCode", "BranchName"],
    "delivery_orders_deletions": ["book", "doc_date_from", "doc_date_to", "doc_keys"],
    "goods_receive_notes_deletions": ["book", "doc_date_from", "doc_date_to", "doc_keys"],
}

# D24 (captain 2026-09-06): per-entity notes on a field's meaning that
# `fields_added`'s bare name list cannot carry - `name` is transitional on
# `products` specifically, so this stays a one-entry dict rather than a new
# per-field schema nothing else needs yet.
FIELD_NOTES: dict[str, str] = {
    "products": (
        "name is transitional, maps to description when description is absent. "
        "v2.4: a product push resolves by code (case/whitespace-insensitive) when its "
        "own source_ref misses but the row is already linked under this same source "
        "system - the record updates, the STORED reference is kept as-is, and the "
        "verdict carries the ref_mismatch warning."
    ),
    # Both entities get the SAME sentence (D24's shape is one note per
    # entity, not per field) - from_so_external means the identical thing on
    # a purchase-order line and on a shipping-order line.
    "purchase_orders": (
        "from_so_external is the cross-book case of from_so_line_ref: the sales order "
        "lives in another database and is recorded raw, never resolved into a Sorento id"
    ),
    "shipping_orders": (
        "from_so_external is the cross-book case of from_so_line_ref: the sales order "
        "lives in another database and is recorded raw, never resolved into a Sorento id"
    ),
    # v2.4: the deletions-only `codes` fallback above does not extend to
    # reads - `/external/read/products` stays reference-only, so a product
    # linked under a document-minted reference is not found there by an
    # item-code reference.
    "products_deletions": (
        "codes is a source_ref -> code map read only when its own source_ref "
        "misses and the matched product is unlinked or already under this same "
        "source system (same ref_mismatch rule as the products push above). "
        "/external/read/products stays reference-only: a product linked under a "
        "document-minted reference is not found there by an item-code reference."
    ),
    "stock_balances": (
        "upserts stock.quantity_on_hand only, resolved by (item_code, location_code) "
        "rather than source_ref, which is an echo key never stored. "
        "/ingest/stock_balances/deletions zeroes the quantity and keeps the row - it "
        "never removes it - and its body carries pairs (source_ref -> {item_code, "
        "location_code}) instead of codes."
    ),
    "billing_documents": (
        "v2.6. A push names the whole document: an omitted optional field is stored as null "
        "and a line not sent is removed (the one exception to absent_vs_null on this "
        "surface). The idempotency key is (company, document_type, source_ref); source_ref "
        "is {database}:{IV|CS|CN|DN}:{DocKey}. A push identical to what is stored, or older "
        "than it by source_modified_at (warning stale_ignored), answers unchanged and writes "
        "nothing. Customer, agent and product are linked when they resolve inside the anchor "
        "company, never created, never retryable: unresolved lands null with the code kept. "
        "status is posted or cancelled; cancelled is an update. /deletions hard-deletes, or "
        "sets cancelled (deactivated) when a credit or debit note still points at it."
    ),
    "delivery_orders": (
        "v2.7. Each record is the vendor API's DO object with its Details rows, unmapped, and is "
        "stored whole. Envelope book is required. Key (company, book, DocKey); source_ref is "
        "{book}:DO:{DocKey}. A DO whose DocNo matches an upload-created delivery order is "
        "adopted (warning adopted_by_doc_no); the tracking columns stay the upload's. A naive "
        "LastModified is Malaysia time; an older one answers unchanged + stale_ignored. An "
        "unresolved ItemCode or Location is retryable. /deletions cancels the vanished "
        "doc_keys of a DocDate range and never deletes."
    ),
    "goods_receive_notes": (
        "v2.7. As delivery_orders, on the GRN (picking_headers). FromDocDtlKey links the PO or "
        "SPO line; OurPONo alone links the purchase order only. source_ref is "
        "{book}:GRN:{DocKey}."
    ),
    "branches": (
        "v2.7. branchbypage rows as returned. Key (company, book, AccNo, BranchCode). No read "
        "and no deletions door."
    ),
}

# D15 end state (AC-P0-4): these now FAIL validation (extra=forbid), never
# accepted-and-warned - see `deprecated_field` in `WARNINGS` below, which
# stays in the vocabulary as the value a pre-2.1 integration may still be
# checking for, even though nothing emits it any more.
FIELDS_REMOVED: dict[str, list[str]] = {
    "customers": ["credit_limit", "payment_terms_days", "payment_terms_code"],
    "suppliers": ["payment_terms_code"],
}

# Which documents now derive their own `status` when the payload omits it
# (`document_rules.derive_document_status`) rather than requiring it.
STATUS_OPTIONAL: dict[str, bool] = {
    "sales_orders": True,
    "purchase_orders": True,
    "shipping_orders": True,
    # Required: posted or cancelled, AutoCount's Cancelled flag.
    "billing_documents": False,
    # Cancelled is read off the AutoCount record itself.
    "delivery_orders": False,
    "goods_receive_notes": False,
}

# The fixed warning vocabulary, drawn from the constants each producing
# module actually raises, plus the handful of masters-push warnings that are
# still plain literals in `master_ingest_service` (never promoted to their
# own constants - there was exactly one call site for each) and two
# documentation-only entries: `lines.dropped` names the `lines.dropped` COUNT
# key a document verdict carries (D9), not a `warnings` list entry itself;
# `lines.superseded` is the same kind of entry for a shipping order (D27,
# spo-xlsx-supersede) - the count of xlsx-era rows a document's FIRST push
# replaced with the AutoCount line-set, so an ESB reading a verdict can tell a
# supersede apart from a plain create; `deprecated_field` is retired code
# (D15 end state) kept here so a pre-2.1 integration checking for it by name
# still finds it documented.
WARNINGS: list[str] = sorted(
    {
        WARN_CUSTOMER_CREATED,
        WARN_CUSTOMER_UNRESOLVED,
        WARN_SUPPLIER_CREATED,
        WARN_AGENT_CREATED,
        WARN_WAREHOUSE_UNRESOLVED,
        WARN_REF_MISMATCH,
        WARN_UNCLASSIFIED_DEMAND,
        WARN_SUPPLIER_AMBIGUOUS,
        WARN_CONTAINER_UNRESOLVED,
        WARN_RECEIVED_LOCKED,
        WARN_WAREHOUSE_INACTIVE,
        WARN_AGENT_UNRESOLVED,
        WARN_PRODUCT_UNRESOLVED,
        WARN_STALE_IGNORED,
        *CONTRACT_2_7_WARNINGS,
        "category_created",
        "uom_created",
        "brand_created",
        "item_type_created",
        "segment_unknown",
        "lines.dropped",
        "lines.superseded",
        "deprecated_field",
    }
)


@router.get("")
def get_contract():
    """Version, entity list and the v2.1 diff the ESB gates its integration on.

    `SUPPORTED_ENTITIES` directly (review nit) - `shipping_orders` (S3) is
    already a real member of it (folded in by `SHIPPING_ORDER_ENTITIES`), so
    a second, contract-local alias for the same set had nothing left to add.
    """
    return {
        "version": CONTRACT_VERSION,
        "entities": sorted(SUPPORTED_ENTITIES),
        "fields_added": FIELDS_ADDED,
        "field_notes": FIELD_NOTES,
        "fields_removed": FIELDS_REMOVED,
        "status_optional": STATUS_OPTIONAL,
        # D14: absent-vs-null holds across every master, not per-entity - one
        # bool, not a dict of trues.
        "absent_vs_null": True,
        # D14 third state (2026-09-06): a blank string on an optional field is
        # an explicit clear, the same thing a blank xlsx cell means. Required
        # identity fields still refuse it.
        "blank_string_clears": True,
        "warnings": WARNINGS,
    }
