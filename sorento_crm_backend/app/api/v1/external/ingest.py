"""Master data ingest and current-state reads for the ESB (Phase C).

Two directions on one surface:

* **Ingest** (`POST /external/ingest/{entity}`) - the ESB pushes canonical
  masters in. Always `200` with a per-record verdict, even when records fail:
  a batch is not a transaction (AC-AC-15), so a non-2xx would tell the ESB
  nothing about which of 10,000 records landed.

* **Delete** (`POST /external/ingest/{entity}/deletions`) - the ESB says a
  record is gone upstream. A record something still points at is taken out of
  use rather than removed; see ``deletion_service``.

* **Read** (`POST /external/read/{entity}`) - the ESB fetches current values to
  render before/after diffs for human approval (AC-AC-19). POST rather than GET
  because the request carries a batch of source references, and a few hundred
  of them do not belong in a query string.

Permissions reuse the existing per-entity slugs. Writing a warehouse through
the ESB is the same act as writing one through the UI, so it is the same
permission -- an integration that may not edit warehouses must not gain the
ability by coming through a different door.

Both calls are **company-anchored** (group A1): a top-level ``companyCode``, or
the calling integration's binding, names the one company the request writes,
adopts and reads inside. The guard runs after the body has parsed and after the
batch cap, because a caller cannot supply an anchor for a request that never
parsed, and telling it about the anchor before telling it the batch is too big
would cost it a second round trip. See ``company_anchor.py``.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.schemas.ingest_extras import collect_unknown_fields
from app.api.v1.external.company_anchor import resolve_company_anchor
from app.api.v1.external.permissions import require_external_permission_for_path
from app.dependencies import get_external_api_user
from app.database import get_db
from app.models.order import SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation
from app.models.project_so import OrderInquiryRow, ProjectSalesOrderLine
from app.schemas.common import MAX_PAGE_LIMIT
from app.services.error_handler import AppException
from app.services.deletion_service import DeletionService
from app.services.finance.billing_document_ingest_service import (
    BILLING_DOCUMENT_ENTITIES,
    BillingDocumentIngestService,
    BillingDocumentReadService,
)
from app.services.autocount_doc_ingest_service import (
    AUTOCOUNT_BRANCH_ENTITIES,
    AUTOCOUNT_DOC_ENTITIES,
    BOOK_PATTERN,
    GOODS_RECEIVE_NOTES_ENTITY,
    AutocountDocIngestService,
    AutocountDocReadService,
    parse_deletion_body,
    link_waiting_grn_lines,
    run_grn_receipt_hook as _run_grn_receipt_hook,
)
from app.services.document_ingest_service import (
    DOCUMENT_ENTITIES,
    DocumentIngestService,
    DocumentReadService,
)
from app.services.master_ingest_service import (
    ENTITY_SPECS,
    MasterIngestService,
    UnsupportedIngestEntity,
)
from app.services.master_read_service import MasterReadService
from app.services import planning_change_service
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.scm import plan_exception_service, reorder_run_service
from app.services.scm.outstanding_diff import diff_lines
from app.services.scm.outstanding_import_service import supersede_crm_raised_pos
from app.services.user_service import UserPermissionService
from app.services.shipping_order_ingest_service import (
    SHIPPING_ORDER_ENTITIES,
    ShippingOrderIngestService,
    ShippingOrderReadService,
)
from app.services.stock_balance_ingest_service import (
    STOCK_BALANCE_ENTITIES,
    StockBalanceIngestService,
)

ingest_router = APIRouter()
read_router = APIRouter()
logger = logging.getLogger(__name__)

# Per-entity permissions. Ingest writes, so it takes the edit slug; reads take
# view. Deliberately the same slugs the UI uses.
INGEST_PERMISSIONS = {
    "product_categories": "master_data.product_categories.edit",
    "units_of_measure": "master_data.units_of_measure.edit",
    "warehouses": "inventory.warehouses.edit",
    "suppliers": "procurement.suppliers.edit",
    "customers": "order_management.customers.edit",
    "products": "master_data.products.edit",
    "sales_agents": "master_data.sales_agents.edit",
    # autocount-brands-ingest (contract 2.3), D5: ingest takes .edit, not .add,
    # same as every sibling master.
    "brands": "master_data.brands.edit",
    # Documents (group A3). Pushing an order through the ESB is the same act as
    # editing one on the SCM screen, so it is the same slug.
    "sales_orders": "scm.sales_orders.edit",
    "purchase_orders": "scm.purchase_orders.edit",
    # Shipping orders (S3) - no header table, but the same slug shape.
    "shipping_orders": "scm.shipping_orders.edit",
    # stock_balances (contract 2.5, D9): the same slugs the Stock screen's own
    # write path already uses - pushing a balance through the ESB is that
    # same act, so it is that same permission.
    "stock_balances": "inventory.stock.edit",
    # billing_documents (contract 2.6, finance S0, ruling Q9): finance's own slugs. `.edit`
    # is the ingest gate only; no screen creates a billing document.
    "billing_documents": "finance.billing_documents.edit",
    # AutoCount DO / GRN / branches (contract 2.7, #1354 S2): the slugs the DO and GRN screens
    # and the customer master already use. Branches are the debtors' delivery branches.
    "delivery_orders": "order_management.orders.edit",
    "goods_receive_notes": "procurement.grn.edit",
    "branches": "order_management.branches.edit",
}
READ_PERMISSIONS = {
    "product_categories": "master_data.product_categories.view",
    "units_of_measure": "master_data.units_of_measure.view",
    "warehouses": "inventory.warehouses.view",
    "suppliers": "procurement.suppliers.view",
    "customers": "order_management.customers.view",
    "products": "master_data.products.view",
    "sales_agents": "master_data.sales_agents.view",
    "brands": "master_data.brands.view",
    "sales_orders": "scm.sales_orders.view",
    "purchase_orders": "scm.purchase_orders.view",
    "shipping_orders": "scm.shipping_orders.view",
    "stock_balances": "inventory.stock.view",
    "billing_documents": "finance.billing_documents.view",
    "delivery_orders": "order_management.orders.view",
    "goods_receive_notes": "procurement.grn.view",
    # Mapped so every map covers every entity; the route itself answers 404 (no read door).
    "branches": "order_management.branches.view",
}
# Deleting through the ESB is its own act, so it takes its own slug on top of the
# ingest guard the router already carries (group A4 mounts the route). Declared
# here rather than there because this is the one file that says what an entity
# name means on this surface, and three maps that can disagree about the entity
# list is how a hole opens.
DELETE_PERMISSIONS = {
    "product_categories": "master_data.product_categories.delete",
    "units_of_measure": "master_data.units_of_measure.delete",
    "warehouses": "inventory.warehouses.delete",
    "suppliers": "procurement.suppliers.delete",
    "customers": "order_management.customers.delete",
    "products": "master_data.products.delete",
    "sales_agents": "master_data.sales_agents.delete",
    "brands": "master_data.brands.delete",
    "sales_orders": "scm.sales_orders.delete",
    "purchase_orders": "scm.purchase_orders.delete",
    "shipping_orders": "scm.shipping_orders.delete",
    "stock_balances": "inventory.stock.delete",
    "billing_documents": "finance.billing_documents.delete",
    # 2.7: the DO / GRN "deletions" never delete; they cancel what the sweep says vanished.
    "delivery_orders": "order_management.orders.delete",
    "goods_receive_notes": "procurement.grn.delete",
    "branches": "order_management.branches.delete",
}

# A batch cap the ESB can design against. Exceeding it errors rather than
# silently truncating (AC-AC-20): a partial response the caller believes is
# complete is worse than a refusal.
MAX_BATCH = 1000

# Fix round 6 (live need): the batch summary line names how many records
# failed or stayed retryable, but not WHICH ones - so a record retryable
# across several pushes had no server-side trail at all beyond that count.
# Capped so one pathological batch (every record failing the same way)
# cannot turn this into its own flood.
MAX_RECORD_LOG_LINES = 50
# Error VALUES are free text (a resolver's own message, or a raw exception
# string on the sanitized paths) - capped so one long one cannot blow out a
# log line; the KEYS (field names) are never truncated, they are the whole
# point of a machine-readable error.
_ERROR_VALUE_LOG_LIMIT = 200


def _log_record_outcomes(
    entity: str, integration_name, company_id: str, result
) -> None:
    """One INFO line per FAILED/RETRYABLE record, capped at `MAX_RECORD_LOG_LINES`.

    Never logs a payload field beyond `source_ref` and the record's own
    `errors` dict - no line bodies, no customer/agent/product names, nothing
    that was actually sent. Skipped entirely for a dry run: a preview never
    happened as far as the operational log is concerned, the same reason the
    batch summary line's own counts are for the real run only in spirit
    (dry-run callers read the response body, not the server log).
    """
    logged = 0
    suppressed = 0
    for record in result.records:
        if record.outcome.value not in ("failed", "retryable"):
            continue
        if logged >= MAX_RECORD_LOG_LINES:
            suppressed += 1
            continue
        errors = {
            field: (reason[:_ERROR_VALUE_LOG_LIMIT] if isinstance(reason, str) else reason)
            for field, reason in record.errors.items()
        }
        logger.info(
            "ingest.record entity=%s integration=%s company=%s source_ref=%s "
            "outcome=%s errors=%s",
            entity,
            integration_name,
            company_id,
            record.source_ref,
            record.outcome.value,
            json.dumps(errors),
        )
        logged += 1
    if suppressed:
        logger.info(
            "ingest.record_overflow entity=%s suppressed=%d", entity, suppressed
        )


# Masters, documents, shipping orders and stock balances on one surface. The
# set is built from every registry rather than written out, so an entity that
# exists in none of them cannot become reachable here by being spelled
# correctly in this file.
SUPPORTED_ENTITIES = (
    set(ENTITY_SPECS)
    | set(DOCUMENT_ENTITIES)
    | set(SHIPPING_ORDER_ENTITIES)
    | set(STOCK_BALANCE_ENTITIES)
    | set(BILLING_DOCUMENT_ENTITIES)
    | set(AUTOCOUNT_DOC_ENTITIES)
    | set(AUTOCOUNT_BRANCH_ENTITIES)
)

# Bumped whenever the wire shape of an entity changes in a way the ESB must gate
# on (a new required field, a changed enum). Read by `GET /external/contract`
# (D8) so the ESB can check compatibility without trial-and-error against this
# router. A STRING (S4, ingest-parity-standardisation D-final): "2.1" is a
# point release of the same major contract, and a bare int can never express
# that - the ESB's own gate compares it as an opaque value, never arithmetic.
# "2.2" (V5, ingest-contract-2-2-so-links): from_so_line_ref/from_so_external/
# from_po_line_ref/from_po_number added to purchase_orders and
# shipping_orders lines, and from_so_numbers is now listed under
# `fields_added` too (a v2-era field the ESB only now depends on reading
# back). All additive and optional, same as every point release before it.
# "2.3" (autocount-brands-ingest): `brands` joins ENTITY_SPECS as a
# first-class master, with its own INGEST/READ/DELETE permission slugs -
# additive, an ESB on 2.2 simply never sees `brands` in `entities`.
# "2.4" (ingest-products-code-wins, SR0): `/{entity}/deletions` gains an
# optional `codes` map, only read for `products`. Since PRODUCT-REF-COLLISION
# (3 Oct) products are matched by code alone and `codes` is the only way a
# product deletion resolves.
# "2.5" (ingest-stock-balances-2-5, Foundryx SR5): `stock_balances` joins as a
# new push entity - upsert `stock.quantity_on_hand` for a (item_code,
# location_code) pair, and `/{entity}/deletions` zeroes it rather than
# removing the row (D7). `warehouse_inactive` joins `warnings`. Additive: an
# ESB still on 2.4 never sees `stock_balances` in `entities` and keeps
# whatever it did before (Pull stays unchanged, D12).
# "2.6" (finance S0, #1309): `billing_documents` joins as a new push entity - invoices, cash
# sales, credit notes and debit notes into `finance.billing_documents`, whole-document replace,
# a stale guard on `source_modified_at`, and the `unchanged` verdict (with `summary.unchanged`)
# for a push that matches what is stored. `agent_unresolved`, `product_unresolved` and
# `stale_ignored` join `warnings`. Additive: an ESB on 2.5 never sees the entity.
# "2.7" (#1354 S2): `delivery_orders`, `goods_receive_notes` and `branches` join as push
# entities. The record is the AutoCount vendor API's own object (PascalCase, `Details` rows),
# the envelope adds a required `book`, the idempotency key is (company, book, DocKey), a DO / GRN
# adopts an upload-created row by its number, and `/deletions` takes the sweep's vanished
# `doc_keys` for a DocDate range and cancels them, never deletes. Additive.
CONTRACT_VERSION = "2.7"


def _principal_may_delete(db: Session, current_user: dict, entity: str) -> bool:
    """Whether this principal holds `DELETE_PERMISSIONS[entity]` (D30).

    The service must not import FastAPI or resolve a principal of its own, so
    the answer is computed here through the SAME `UserPermissionService` every
    guard on this surface uses - superadmin/admin bypass included - and handed
    over as a bool. No grant, no id, or an entity with no delete slug all
    answer False: the safe half of D30 (rows closed, never removed).
    """
    slug = DELETE_PERMISSIONS.get(entity)
    user_id = current_user.get("id")
    if not slug or not user_id:
        return False
    return UserPermissionService(db).check_user_has_permission(user_id, slug)


def _run_document_hooks(
    db: Session, entity: str, service, *, actor: Optional[str]
) -> tuple[int, int]:
    """D7/S5 (plan section 2.6): post-write reactions, non-dry only.

    Runs AFTER the batch's own `db.commit()` - every hook here reacts to a
    write that has already landed, never to one this call itself decides.
    Each reaction is its OWN try/except -> `logger.warning` and its OWN
    commit: a failed reaction must cost the operator only that reaction (the
    next push produces it again), never the ingest that already succeeded,
    and one reaction's failure must not roll back a sibling reaction that
    already committed.

    `actor` (N1 review fix) is the calling principal's own user id - the
    integration's `act_as_user_id`, resolved once by `get_external_api_user`
    - threaded through to whichever hook records who/what caused the
    reaction, the same attribution an interactive user's action gets.

    `shipping_orders` reacts here too (security review, blocker 2): the
    forward-match sweep used to run INSIDE `ShippingOrderIngestService
    .ingest()` itself, before this route's own `db.commit()` -
    `forward_match_grn_lines_for_spo` commits on success and rolls back on
    failure, so one exception mid-batch discarded every not-yet-committed
    record of the batch while the route still answered 200. Moved here so it
    runs against a batch that has already landed, same as every other hook.

    Returns `(book_repair_moves_dropped, book_follow_rows_dropped)` - how many
    `follow_book_repairing` moves (S4 review fix, 17 Sep) and how many
    `follow_book_for_rows` rows (S2, `PLAN-oi-follow-book-chain.md`, AC-FB-24)
    this push's own hooks (if they ran at all) dropped past their caps - 0 for
    every entity/path that has no such hook.
    """
    book_repair_moves_dropped = 0
    book_follow_rows_dropped = 0
    if entity == "sales_orders":
        _run_plan_exception_hook(db, service, actor=actor)
        _run_planning_change_hook(db, service, actor=actor)
    elif entity == "purchase_orders":
        book_repair_moves_dropped = _run_supersede_and_relink_hooks(db, service, actor=actor)
        book_follow_rows_dropped = _run_follow_book_po_hook(db, service, actor=actor)
    elif entity == "shipping_orders":
        _run_shipping_order_forward_match_hook(db, service, actor=actor)
        _run_shipping_order_shipment_refresh_hook(db, service, actor=actor)
        book_repair_moves_dropped = _run_shipping_order_book_repair_hook(db, service, actor=actor)
        book_follow_rows_dropped = _run_follow_book_spo_hook(db, service, actor=actor)
    return book_repair_moves_dropped, book_follow_rows_dropped


def _run_plan_exception_hook(db: Session, service, *, actor: Optional[str]) -> None:
    """AC-V5-1: a before/after plan-exception batch over this push's products.

    `before` is `service.plan_exception_before` - captured INSIDE the service,
    per product, the first time this batch touched it and before anything was
    written for it (mirrors `outstanding_import_service.apply`'s own
    `before_positions`, taken while the old position is still the one in the
    database). `after` is read here, post-commit, over every product the
    batch actually touched. Called through the MODULE attribute
    (`plan_exception_service.generate_batch`, not a bound import) so a caller
    that monkeypatches the module - the ESB's own smoke tests, this slice's
    own failure-is-logged test - sees the same function this route runs.
    """
    if not service.touched_product_ids or not service.so_numbers:
        return
    try:
        after = plan_exception_service.snapshot(db, list(service.touched_product_ids))
        current = reorder_run_service.today_or_latest_run(db)
        # A run still being built contradicts nothing yet (mirrors the upload's
        # own guard) - only a completed plan can be contradicted.
        if current and current["row"].get("status") != "completed":
            current = None
        plan_exception_service.generate_batch(
            db,
            run_id=str(current["row"]["id"]) if current else None,
            before=service.plan_exception_before,
            after=after,
            # The ingest's own count of changed documents, the same role
            # `diff.counts` plays for the upload (AC-D2b's rule, carried
            # through unchanged rather than recounted from the exceptions).
            delta_count=len(service.written_header_ids),
            source_documents=sorted(service.so_numbers),
            actor=actor,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.plan_exception_hook_failed", exc_info=True)


def _run_planning_change_hook(db: Session, service, *, actor: Optional[str]) -> None:
    """D10 (S2, BL-058): the ESB's own `planning_change_batches` row, built
    from the SAME before/after line picture `outstanding_import_service
    .apply()` diffs after its own upload - `service.so_diff_before`/
    `so_diff_after`, gathered per record in `DocumentIngestService._apply`
    (`Diff` docstring: "the documents named in the extract ARE the scope",
    which is exactly the batch this push named). Called through the MODULE
    attribute (`planning_change_service.build_batch`), same reason as
    `plan_exception_service.generate_batch` above - a caller monkeypatching
    the module sees this call.
    """
    if not service.so_diff_before and not service.so_diff_after:
        return
    try:
        diff = diff_lines(service.so_diff_before, service.so_diff_after)
        applied_line_ids: dict[int, str] = {}
        for change in diff.changes:
            line_id = (change.after.row_ref if change.after else None) or (
                change.before.row_ref if change.before else None
            )
            if line_id:
                applied_line_ids[id(change)] = line_id
        planning_change_service.build_batch(
            db,
            diff,
            applied_line_ids=applied_line_ids,
            order_ids=dict(service.so_header_id_by_number),
            actor=actor,
            import_job_id=None,
            file_name=None,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.planning_change_hook_failed", exc_info=True)


def _run_supersede_and_relink_hooks(db: Session, service, *, actor: Optional[str]) -> int:
    """AC-V5-2: retire CRM-raised POs this push confirms, then relink placements.

    Two hooks, two try/except blocks, two commits - not one wrapping both -
    so a relink failure can never undo a supersession that already landed,
    and vice versa.

    Returns the book-repair hook's own dropped-move count (S4 review fix, 17
    Sep) - 0 when that hook never ran or its own transaction failed, since
    either way nothing past the cap was actually applied.
    """
    book_repair_moves_dropped = 0
    if service.po_supersede_triples:
        try:
            supersede_crm_raised_pos(db, service.po_supersede_triples)
            db.commit()
        except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
            db.rollback()
            logger.warning("ingest.supersede_crm_raised_pos_failed", exc_info=True)

    if service.written_header_ids:
        try:
            with db.begin_nested():
                ProjectOrderInquiryService(db).relink_to_matching_lines(
                    list(service.written_header_ids),
                    actor_user_id=actor,
                    trigger="autocount_ingest",
                )
            db.commit()
        except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
            db.rollback()
            logger.warning("ingest.relink_to_matching_lines_failed", exc_info=True)

    # S5 (`PLAN-oi-replan-received-links.md`): our own link follows `from_so_
    # line_ref` wherever THIS push moved it, after the relink hook above -
    # `service.ref_moves` is `DocumentIngestService`'s own capture, taken
    # inside `_sync_lines` while the old ref was still on the row.
    if getattr(service, "ref_moves", None):
        try:
            with db.begin_nested():
                book_repair_moves_dropped = ProjectOrderInquiryService(db).follow_book_repairing(
                    service.ref_moves, trigger="autocount_ingest",
                    company_id=service.company_id, actor_user_id=actor,
                )
            db.commit()
        except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
            db.rollback()
            logger.warning("ingest.follow_book_repairing_failed", exc_info=True)

    return book_repair_moves_dropped


def _run_shipping_order_forward_match_hook(
    db: Session, service, *, actor: Optional[str]
) -> None:
    """D7 (S3), moved here by the security review (blocker 2): GRN
    forward-match, once per SPO number this batch touched, AFTER the batch's
    own commit rather than inside `ShippingOrderIngestService.ingest()`.

    `forward_match_grn_lines_for_spo_best_effort` is itself already a
    best-effort wrapper (catches, rolls back its OWN transaction, warns), so
    the outer try/except here is defensive symmetry with the SO/PO hooks
    above rather than a defect it is patching over - it should never actually
    fire. Called through the MODULE attribute so a caller monkeypatching
    `grn_spo_matching` sees this call, same reason the SO/PO hooks call
    `plan_exception_service`/`planning_change_service` that way.
    """
    if not service.spo_numbers_touched:
        return
    import app.services.grn_spo_matching as grn_spo_matching

    try:
        for spo_number in sorted(service.spo_numbers_touched):
            grn_spo_matching.forward_match_grn_lines_for_spo_best_effort(
                db, spo_number, company_id=service.company_id
            )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.shipping_order_forward_match_hook_failed", exc_info=True)
    # AutoCount GRN lines are never forward-matched (one row per DtlKey); the ones waiting
    # for these SPOs link through the GRN ingest's own resolver (GRN-PULL-CRM e2e gap 1).
    link_waiting_grn_lines(
        db, company_id=service.company_id, numbers=set(service.spo_numbers_touched)
    )


def _run_shipping_order_book_repair_hook(
    db: Session, service, *, actor: Optional[str]
) -> int:
    """S5 (`PLAN-oi-replan-received-links.md`): the SPO twin of the PO relink hook
    above - `service.ref_moves` is `ShippingOrderIngestService`'s own capture, taken
    inside `_write_row`'s in-place update path and inside `_supersede_xlsx_rows` for
    the xlsx-era allocation the ESB's first ref-bearing push replaces.

    Returns the dropped-move count (S4 review fix, 17 Sep) - 0 when there was
    nothing to repair or the hook's own transaction failed."""
    book_repair_moves_dropped = 0
    if not getattr(service, "ref_moves", None):
        return book_repair_moves_dropped
    try:
        with db.begin_nested():
            book_repair_moves_dropped = ProjectOrderInquiryService(db).follow_book_repairing(
                service.ref_moves, trigger="autocount_ingest",
                company_id=service.company_id, actor_user_id=actor,
            )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.shipping_order_book_repair_hook_failed", exc_info=True)
    return book_repair_moves_dropped


def _run_shipping_order_shipment_refresh_hook(
    db: Session, service, *, actor: Optional[str]
) -> None:
    """D27a: refresh `inbound_shipment_lines.line_status` for every shipment
    whose allocations this batch changed, the way every other writer of
    allocations does.

    Its own hook, post-commit, for a mechanical reason:
    `InboundShipmentService.refresh_shipment_line_statuses` COMMITS, and the
    supersede that touches those shipments runs inside a per-record SAVEPOINT
    - calling it there would commit half a batch and would make a dry run
    write. Own try/except, like every hook here: a failed refresh must cost
    the operator only the refresh.
    """
    shipment_ids = getattr(service, "shipment_ids_touched", None)
    if not shipment_ids:
        return
    from app.services.procurement_service import InboundShipmentService

    try:
        inbound = InboundShipmentService(db)
        for shipment_id in sorted(shipment_ids):
            inbound.refresh_shipment_line_statuses(shipment_id)
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.shipping_order_shipment_refresh_hook_failed", exc_info=True)


def _rows_for_core_line_refs(
    db: Session, refs: set[str], *, company_id: str
) -> list[str]:
    """Order-inquiry row ids whose reconciled core sales-order line carries one
    of these `source_ref`s - what `follow_book_for_rows` (S1) already resolves
    for a row it holds, only starting from the ref instead. Company-scoped by
    hand on BOTH sides (review round item 6): the ESB `X-API-Key` principal's
    ambient session scope is not necessarily narrowed to one company, and
    `follow_book_for_rows` re-scopes internally too, so this is belt only,
    never the sole guard - but the row's OWN `company_id` is checked here too,
    not only the core line's, since a data anomaly could otherwise let a row
    from one company resolve through a core line record another owns.

    Ordered by `(created_at, id)` (review round item 6): `follow_book_for_rows`
    caps AFTER narrowing to these rows, over this exact order, so which row a
    tight cap admits does not depend on Postgres's own scan order either.
    """
    wanted = sorted({str(r) for r in refs if r})
    if not wanted:
        return []
    rows = (
        db.query(OrderInquiryRow.id)
        .join(
            ProjectSalesOrderLine,
            ProjectSalesOrderLine.id == OrderInquiryRow.so_line_id,
        )
        .join(
            SalesOrderLine,
            SalesOrderLine.id == ProjectSalesOrderLine.core_sales_order_line_id,
        )
        .filter(
            SalesOrderLine.source_ref.in_(wanted),
            SalesOrderLine.company_id == company_id,
            OrderInquiryRow.company_id == company_id,
        )
        .order_by(OrderInquiryRow.created_at.asc(), OrderInquiryRow.id.asc())
        .all()
    )
    return [str(row_id) for (row_id,) in rows]


def _run_follow_book_po_hook(db: Session, service, *, actor: Optional[str]) -> int:
    """S2 (`PLAN-oi-follow-book-chain.md`, AC-FB-21/25): every SO line ref a
    purchase-order LINE this push wrote (created or updated -
    `service.written_po_line_refs`; `ref_moves` only ever names a ref that
    CHANGED, so a freshly created line, or an existing line naming a
    sales-order line for the FIRST time, is invisible to that capture)
    resolved to the order-inquiry rows of those core lines and handed to
    `ProjectOrderInquiryService.follow_book_for_rows`, so a row that already
    existed picks up the book the moment the purchase side states it, not only
    on the next cascade pass.

    Same shape as the hooks above: its own savepoint, its own commit, best
    effort. Returns the dropped-row count past `FOLLOW_BOOK_FOR_ROWS_MAX_ROWS`
    (AC-FB-24) - 0 when there was nothing to follow or the hook's own
    transaction failed.
    """
    book_follow_rows_dropped = 0
    refs = getattr(service, "written_po_line_refs", None)
    if not refs:
        return book_follow_rows_dropped
    try:
        with db.begin_nested():
            row_ids = _rows_for_core_line_refs(db, refs, company_id=service.company_id)
            if row_ids:
                book_follow_rows_dropped = ProjectOrderInquiryService(db).follow_book_for_rows(
                    row_ids, trigger="autocount_ingest",
                    company_id=service.company_id, actor_user_id=actor,
                    max_rows=ProjectOrderInquiryService.FOLLOW_BOOK_FOR_ROWS_MAX_ROWS,
                )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.follow_book_for_rows_po_hook_failed", exc_info=True)
    return book_follow_rows_dropped


def _refs_from_written_spo_allocations(
    db: Session, allocation_ids: set[str], *, company_id: str
) -> set[str]:
    """The SO line refs the book states for these allocations - each one's own
    `from_so_line_ref`, or, when it names only a purchase-order line, that
    line's own `from_so_line_ref` (matched on `from_po_number` AND the line's
    own `source_ref` - `_chain_allocations`'s exact-key reasoning:
    `purchase_order_lines.source_ref` is not unique, the August extract wrote
    bare ordinals onto hundreds of lines each).

    `from_po_line_ref` is stripped before it becomes part of the key (review
    round item 6), the same way `_chain_allocations` itself strips it: a
    trailing/leading space on either side of the match would otherwise miss
    silently rather than resolve."""
    wanted = sorted({str(i) for i in allocation_ids if i})
    if not wanted:
        return set()
    rows = (
        db.query(
            SPOAllocation.from_so_line_ref,
            SPOAllocation.from_po_line_ref,
            SPOAllocation.from_po_number,
        )
        .filter(SPOAllocation.id.in_(wanted), SPOAllocation.company_id == company_id)
        .all()
    )
    refs = {r[0] for r in rows if r[0]}
    chain_keys = {
        (r[2], (r[1] or "").strip()) for r in rows if not r[0] and (r[1] or "").strip() and r[2]
    }
    if not chain_keys:
        return refs
    po_numbers = sorted({key[0] for key in chain_keys})
    line_refs = sorted({key[1] for key in chain_keys})
    po_lines = (
        db.query(
            PurchaseOrderLine.source_ref,
            PurchaseOrderLine.from_so_line_ref,
            PurchaseOrder.po_number,
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .filter(
            PurchaseOrder.po_number.in_(po_numbers),
            PurchaseOrderLine.source_ref.in_(line_refs),
            PurchaseOrderLine.company_id == company_id,
        )
        .all()
    )
    for source_ref, from_so_ref, po_number in po_lines:
        if from_so_ref and (po_number, source_ref) in chain_keys:
            refs.add(from_so_ref)
    return refs


def _run_follow_book_spo_hook(db: Session, service, *, actor: Optional[str]) -> int:
    """S2 (`PLAN-oi-follow-book-chain.md`, AC-FB-22): the SPO twin of the PO
    hook above - `service.written_spo_allocation_ids` is `ShippingOrderIngest
    Service`'s own capture of every allocation this push wrote (created,
    updated or adopted).

    Same shape: its own savepoint, its own commit, best effort. Returns the
    dropped-row count past `FOLLOW_BOOK_FOR_ROWS_MAX_ROWS` - 0 when there was
    nothing to follow or the hook's own transaction failed.
    """
    book_follow_rows_dropped = 0
    allocation_ids = getattr(service, "written_spo_allocation_ids", None)
    if not allocation_ids:
        return book_follow_rows_dropped
    try:
        with db.begin_nested():
            refs = _refs_from_written_spo_allocations(
                db, allocation_ids, company_id=service.company_id
            )
            row_ids = (
                _rows_for_core_line_refs(db, refs, company_id=service.company_id)
                if refs
                else []
            )
            if row_ids:
                book_follow_rows_dropped = ProjectOrderInquiryService(db).follow_book_for_rows(
                    row_ids, trigger="autocount_ingest",
                    company_id=service.company_id, actor_user_id=actor,
                    max_rows=ProjectOrderInquiryService.FOLLOW_BOOK_FOR_ROWS_MAX_ROWS,
                )
        db.commit()
    except Exception:  # noqa: BLE001 - best-effort, the ingest already succeeded
        db.rollback()
        logger.warning("ingest.follow_book_for_rows_spo_hook_failed", exc_info=True)
    return book_follow_rows_dropped


def _book(payload: dict) -> str:
    """Contract 2.7: the AutoCount book the records came from (`db1` = Sorento)."""
    book = payload.get("book")
    if not isinstance(book, str) or not BOOK_PATTERN.fullmatch(book):
        raise AppException(
            status_code=422,
            message="Body must contain 'book': 1 to 20 characters of letters, digits, _ or -",
            code="INVALID_BODY",
        )
    return book


def _cancel_vanished(entity: str, payload: dict, dry_run: bool, db: Session, current_user: dict):
    """Contract 2.7 deletions: the sweep's vanished DocKeys for a DocDate range, cancelled in
    place and never deleted (plan 1.10)."""
    book = _book(payload)
    try:
        doc_keys, date_from, date_to = parse_deletion_body(payload)
    except ValueError as exc:
        raise AppException(status_code=422, message=str(exc), code="INVALID_BODY")
    if len(doc_keys) > MAX_BATCH:
        raise AppException(
            status_code=413,
            message=(
                f"Batch of {len(doc_keys)} exceeds the maximum of {MAX_BATCH}. "
                "Split it; the response is never silently truncated."
            ),
            code="BATCH_TOO_LARGE",
        )
    company_id = resolve_company_anchor(db, payload, current_user)
    service = AutocountDocIngestService(
        db, current_user.get("integration_id"), company_id=company_id, book=book
    )
    result = service.delete(entity, doc_keys, date_from, date_to, dry_run=dry_run)
    if dry_run:
        db.rollback()
    else:
        db.commit()
        if entity == GOODS_RECEIVE_NOTES_ENTITY:
            _run_grn_receipt_hook(db, service)
    summary = result.as_dict()["summary"]
    logger.info(
        "deletion.batch entity=%s integration=%s company=%s dry_run=%s "
        "deactivated=%d not_found=%d failed=%d",
        entity,
        current_user.get("integration_name"),
        company_id,
        dry_run,
        summary["deactivated"],
        summary["not_found"],
        summary["failed"],
    )
    return result.as_dict()


def _no_branch_door(entity: str) -> None:
    """Contract 2.7: `branches` is ingest only; its read and deletions doors do not exist."""
    if entity in AUTOCOUNT_BRANCH_ENTITIES:
        raise AppException(
            status_code=404,
            message="'branches' has no read or deletions door",
            code="UNKNOWN_ENTITY",
        )


def _entity(entity: str) -> str:
    if entity not in SUPPORTED_ENTITIES:
        raise AppException(
            status_code=404,
            message=(
                f"Unsupported entity '{entity}'. "
                f"Expected one of: {', '.join(sorted(SUPPORTED_ENTITIES))}"
            ),
            code="UNKNOWN_ENTITY",
        )
    return entity


# The three routes below are deliberately plain `def`, never `async def`
# (production incident 2026-09-07). The service call is synchronous SQLAlchemy
# work taking 14-31s per 200-record batch, and an `async def` route ran it ON
# the gunicorn worker's event loop: every other request on that worker stalled
# for the whole batch, and when one batch ran past 120s (a lock wait) the loop
# missed gunicorn's heartbeat, the arbiter logged WORKER TIMEOUT and killed the
# worker, nginx answered 502 "upstream prematurely closed" and the ESB
# re-offered its whole 5,000-row run. A `def` route runs in Starlette's
# threadpool: the loop keeps serving and heartbeating, the batch takes as long
# as it takes. Pinned by tests/test_ingest_routes_are_sync.py.
@ingest_router.post("/{entity}")
def ingest_masters(
    entity: str = Path(...),
    payload: dict = Body(...),
    dry_run: bool = Query(
        False,
        description=(
            "Preview only. Resolves every record exactly as a real ingest would, "
            "adoption matching included, reports the outcome each record would "
            "receive plus a field-level diff for records that would overwrite an "
            "existing row, then writes nothing."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_external_api_user),
):
    """Accept a batch of canonical master records.

    Returns `200` with a per-record verdict -- created, updated, failed or
    retryable -- so the ESB can quarantine per record and re-drain the
    retryables without a human deciding what a 4xx meant.

    With `?dry_run=true` the same work happens and is then rolled back, so an
    operator can see what a sync would overwrite before it does. Defaults to
    false: an existing caller that does not know about the parameter must keep
    getting a real ingest.
    """
    entity = _entity(entity)

    records = payload.get("records")
    if not isinstance(records, list):
        raise AppException(
            status_code=422,
            message="Body must contain a 'records' array",
            code="INVALID_BODY",
        )
    if len(records) > MAX_BATCH:
        raise AppException(
            status_code=413,
            message=(
                f"Batch of {len(records)} exceeds the maximum of {MAX_BATCH}. "
                "Split it; the response is never silently truncated."
            ),
            code="BATCH_TOO_LARGE",
        )

    book = _book(payload) if entity in AUTOCOUNT_DOC_ENTITIES | AUTOCOUNT_BRANCH_ENTITIES else None

    company_id = resolve_company_anchor(db, payload, current_user)

    # One endpoint, three services. A document owns its lines and points at
    # five masters, and a shipping order owns lines with no header at all -
    # both different write shapes from a master row - but the envelope, the
    # batch cap, the verdicts and the dry-run rollback are the caller's
    # contract and must not fork, so the branch is here and nowhere else.
    extra: dict = {}
    if entity in SHIPPING_ORDER_ENTITIES:
        ingester = ShippingOrderIngestService
        # D30 (spo-xlsx-supersede): a first-push supersede REMOVES the xlsx-era
        # rows it replaces, which is a deletion act - so it is gated on the
        # same slug the deletions endpoint demands, resolved here (the one
        # place that already knows what an entity name means on this surface)
        # and passed to the service as a plain bool. Without the grant the
        # service closes and annotates those rows instead.
        extra["may_delete"] = _principal_may_delete(db, current_user, entity)
    elif entity in DOCUMENT_ENTITIES:
        ingester = DocumentIngestService
    elif entity in BILLING_DOCUMENT_ENTITIES:
        # Finance S0: a sibling of the shipping-order service, no post-write hooks.
        ingester = BillingDocumentIngestService
    elif entity in STOCK_BALANCE_ENTITIES:
        ingester = StockBalanceIngestService
        # Fix round 2 (#1257): its Stock Ledger rows name the integration's
        # act-as user as `created_by`.
        extra["actor_user_id"] = current_user.get("id")
    elif entity in AUTOCOUNT_DOC_ENTITIES | AUTOCOUNT_BRANCH_ENTITIES:
        # Contract 2.7: the AutoCount record as the vendor API returned it, keyed per book.
        ingester = AutocountDocIngestService
        extra["book"] = book
    else:
        ingester = MasterIngestService
    service = ingester(
        db,
        integration_id=current_user.get("integration_id"),
        company_id=company_id,
        **extra,
    )
    # Unknown payload keys are dropped by every canonical schema (owner decision, 1 Oct
    # 2026); their NAMES, never their values, are logged once for the whole request so a
    # field the ESB believes it sends is not lost silently.
    with collect_unknown_fields() as unknown_fields:
        try:
            result = service.ingest(entity, records, dry_run=dry_run)
        except UnsupportedIngestEntity as exc:
            raise AppException(status_code=404, message=str(exc), code="UNKNOWN_ENTITY")
    if unknown_fields:
        logger.info(
            "ingest.unknown_fields entity=%s integration=%s fields=%s",
            entity,
            current_user.get("integration_id"),
            ",".join(sorted(unknown_fields)),
        )

    if dry_run:
        # The service has already rolled back; this is the second of two locks
        # on the same door. A preview that writes is the one outcome this
        # endpoint must never produce, so neither layer relies on the other.
        db.rollback()
    else:
        # Committed once for the batch. Each record already succeeded or rolled
        # back inside its own savepoint, so this persists exactly the good ones.
        db.commit()
        # D7/S5: post-write hooks, non-dry only (AC-V5-4). `MasterIngestService`
        # carries none of the attributes these read, so only a
        # `DocumentIngestService` or `ShippingOrderIngestService` batch
        # reaches this.
        if isinstance(service, (DocumentIngestService, ShippingOrderIngestService)):
            # S4 review fix (17 Sep): the hook return is the only place this batch's
            # own `follow_book_repairing` cap-overflow count reaches the caller - it
            # runs after `result` was already built, so it is folded in here rather
            # than lost to a log line only the operator never sees. S2 (AC-FB-24)
            # widened the tuple to carry `follow_book_for_rows`' own dropped count
            # the same way.
            (
                result.book_repair_moves_dropped,
                result.book_follow_rows_dropped,
            ) = _run_document_hooks(db, entity, service, actor=current_user.get("id"))
        elif entity == GOODS_RECEIVE_NOTES_ENTITY:
            _run_grn_receipt_hook(db, service)

    logger.info(
        "ingest.batch entity=%s integration=%s company=%s dry_run=%s "
        "created=%d updated=%d failed=%d retryable=%d unchanged=%d",
        entity,
        current_user.get("integration_name"),
        company_id,
        dry_run,
        result.created,
        result.updated,
        result.failed,
        result.retryable,
        result.unchanged,
    )
    if not dry_run:
        _log_record_outcomes(
            entity, current_user.get("integration_name"), company_id, result
        )
    return result.as_dict()


@ingest_router.post(
    "/{entity}/deletions",
    # TWO guards, both entity-resolved. The router already carries the ingest
    # guard (`.edit`), and this adds `.delete` on top: removing a record through
    # the ESB is a different act from syncing one, and an integration trusted to
    # keep masters up to date must not gain the ability to empty them.
    dependencies=[Depends(require_external_permission_for_path(DELETE_PERMISSIONS))],
)
def delete_records(
    entity: str = Path(...),
    payload: dict = Body(...),
    dry_run: bool = Query(
        False,
        description=(
            "Preview only. Resolves every reference, probes its dependents and "
            "reports the verdict each one would receive - deleted, deactivated, "
            "not found or failed - then writes nothing."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_external_api_user),
):
    """Delete a batch of records the source system says are gone (group A4).

    Always `200` with a per-reference verdict, for the reason the ingest is: a
    batch is not a transaction, and a non-2xx would tell the caller nothing about
    which of its references landed.

    A record something still points at is NOT removed - it is taken out of use
    (`deactivated`) and keeps its reference. See ``deletion_service`` for why
    that is not left to the database to decide.
    """
    entity = _entity(entity)
    _no_branch_door(entity)

    if entity in AUTOCOUNT_DOC_ENTITIES:
        return _cancel_vanished(entity, payload, dry_run, db, current_user)

    source_refs = payload.get("source_refs")
    if not isinstance(source_refs, list):
        raise AppException(
            status_code=422,
            message="Body must contain a 'source_refs' array",
            code="INVALID_BODY",
        )
    if len(source_refs) > MAX_BATCH:
        raise AppException(
            status_code=413,
            message=(
                f"Batch of {len(source_refs)} exceeds the maximum of {MAX_BATCH}. "
                "Split it; the response is never silently truncated."
            ),
            code="BATCH_TOO_LARGE",
        )

    # "2.4" (ingest-products-code-wins, SR0): optional `codes`, a
    # source_ref -> code map so a product deletion can still find its row
    # when the reference alone misses. Absent is fine (every pre-2.4 caller);
    # present has to be an object of strings, or nothing is deleted - the
    # SAME shape/code as the `source_refs` check above, so the ESB does not
    # learn two different 422 conventions on one endpoint.
    codes = payload.get("codes")
    if codes is not None:
        if not isinstance(codes, dict) or any(
            not isinstance(value, str) for value in codes.values()
        ):
            raise AppException(
                status_code=422,
                message="Body 'codes', when present, must be an object of source_ref -> code strings",
                code="INVALID_BODY",
            )
        # Fix round 1: the SAME cap `source_refs` already carries, refused
        # with the same status/code/shape - `codes` is a second per-batch
        # array on this body and must not be a way around the limit above.
        if len(codes) > MAX_BATCH:
            raise AppException(
                status_code=413,
                message=(
                    f"Batch of {len(codes)} 'codes' entries exceeds the maximum of {MAX_BATCH}. "
                    "Split it; the response is never silently truncated."
                ),
                code="BATCH_TOO_LARGE",
            )

    company_id = resolve_company_anchor(db, payload, current_user)

    # stock_balances (D7): a `pairs` map (`source_ref -> {item_code,
    # location_code}`) instead of a reference this entity never stores - the
    # SAME body-level shape/status the `codes` map above uses. Absent is
    # treated as `{}` (every ref then reports `not_found`), never a 422 -
    # unlike `codes`, `pairs` is this entity's only way to name what it means
    # to delete, so there is nothing else the caller could have sent instead.
    if entity in STOCK_BALANCE_ENTITIES:
        pairs = payload.get("pairs")
        if pairs is None:
            pairs = {}
        elif not isinstance(pairs, dict):
            raise AppException(
                status_code=422,
                message="Body 'pairs' must be an object of source_ref -> {item_code, location_code}",
                code="INVALID_BODY",
            )
        if len(pairs) > MAX_BATCH:
            raise AppException(
                status_code=413,
                message=(
                    f"Batch of {len(pairs)} 'pairs' entries exceeds the maximum of {MAX_BATCH}. "
                    "Split it; the response is never silently truncated."
                ),
                code="BATCH_TOO_LARGE",
            )
        service = StockBalanceIngestService(
            db,
            integration_id=current_user.get("integration_id"),
            company_id=company_id,
            actor_user_id=current_user.get("id"),
        )
        result = service.delete(source_refs, pairs, dry_run=dry_run)
    else:
        service = DeletionService(
            db,
            integration_id=current_user.get("integration_id"),
            company_id=company_id,
        )
        try:
            result = service.delete(entity, source_refs, codes=codes, dry_run=dry_run)
        except UnsupportedIngestEntity as exc:
            raise AppException(status_code=404, message=str(exc), code="UNKNOWN_ENTITY")

    if dry_run:
        # The service has already rolled back; this is the second of two locks on
        # the same door, exactly as on the ingest.
        db.rollback()
    else:
        db.commit()

    summary = result.as_dict()["summary"]
    logger.info(
        "deletion.batch entity=%s integration=%s company=%s dry_run=%s "
        "deleted=%d deactivated=%d not_found=%d failed=%d",
        entity,
        current_user.get("integration_name"),
        company_id,
        dry_run,
        summary["deleted"],
        summary["deactivated"],
        summary["not_found"],
        summary["failed"],
    )
    return result.as_dict()


@read_router.post("/{entity}")
def read_current_state(
    entity: str = Path(...),
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_external_api_user),
):
    """Current canonical values for a batch of source references (AC-AC-19).

    Batched by design: rendering a diff over a few hundred records must not
    become a few hundred round trips.
    """
    entity = _entity(entity)
    _no_branch_door(entity)

    source_refs = payload.get("source_refs")
    if not isinstance(source_refs, list):
        raise AppException(
            status_code=422,
            message="Body must contain a 'source_refs' array",
            code="INVALID_BODY",
        )
    if len(source_refs) > MAX_BATCH:
        raise AppException(
            status_code=413,
            message=(
                f"Requested {len(source_refs)} references, above the maximum of {MAX_BATCH}. "
                "Page the request; the response is never silently truncated."
            ),
            code="BATCH_TOO_LARGE",
        )

    company_id = resolve_company_anchor(db, payload, current_user)

    if entity in STOCK_BALANCE_ENTITIES:
        # D8: pairs, not refs alone - `source_ref` is never stored for this
        # entity, so the caller has to name the (item_code, location_code)
        # pair it wants read back for each ref. Absent is `{}`, every ref
        # then reports `not_found` - the same "nothing to look up with"
        # answer a genuinely unresolvable pair gives.
        pairs = payload.get("pairs")
        if not isinstance(pairs, dict):
            pairs = {}
        return StockBalanceIngestService(db, company_id=company_id).current_state(
            source_refs, pairs
        )

    if entity in AUTOCOUNT_DOC_ENTITIES:
        return AutocountDocReadService(db, company_id=company_id).current_state(
            entity, source_refs
        )

    if entity in SHIPPING_ORDER_ENTITIES:
        reader = ShippingOrderReadService
    elif entity in BILLING_DOCUMENT_ENTITIES:
        reader = BillingDocumentReadService
    elif entity in DOCUMENT_ENTITIES:
        reader = DocumentReadService
    else:
        reader = MasterReadService
    return reader(db, company_id=company_id).current_state(entity, source_refs)
