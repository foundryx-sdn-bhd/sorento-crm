"""Preview an outstanding-orders upload, and apply it once someone confirms.

Two entry points sharing one code path, which is the point: `preview()` and `apply()` build
the SAME diff from the SAME resolution, and apply simply writes what preview showed. A
preview computed differently from the commit is a preview that lies.

Resolution failures are surfaced, never guessed. An unknown item code is reported with its
row number and the line is skipped; it is not invented as a new product, because a typo
would otherwise silently become a SKU that gets planned and purchased.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Iterable, Optional

import sqlalchemy as sa
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, Supplier
from app.models.inventory import Warehouse
from app.models.product import Product
from app.services import customer_identity
from app.services import import_outcome_codes as oc
from app.services.import_outcome import ImportOutcome
from app.services.import_alias_service import AliasResolver
from app.utils.chunking import chunked as _chunked
from app.services.scm.outstanding_diff import (
    ADDED,
    CLOSED,
    DATE_AND_QTY_CHANGED,
    QTY_CHANGED,
    UNCHANGED,
    Diff,
    Line,
    diff_lines,
    states_settled,
)
from app.services.company_scope import get_company_scope, resolve_write_company_id
from app.services.scm import order_link_service
from app.services.scm import plan_exception_service
from app.services.scm import reorder_run_service
from app.services.scm import sales_agent_service
# Imported rather than defined here, and re-exported under the names this module has always
# used. The vocabulary moved to `demand_class.py` when the salesperson master gained a
# `demand_class` column of its own: two modules now WRITE this value and one of them is a
# check constraint, so a second copy of the word list would be a database that accepts what
# the importer rejects.
from app.services.scm.history_sources import HISTORY_SOURCE_SYSTEMS
from app.services.scm.demand_class import class_of as _class_of, classify_document
# The segment DB read this module's `_classify_demand` used to define locally, moved to
# `demand_classifier` (D4) so the AutoCount document ingest classifies off the SAME read
# rather than a second one that could drift. Re-exported under the old name so this
# module's own directly-testing callers (`tests/scm/test_outstanding_import_*`) are
# unaffected.
from app.services.scm.demand_classifier import segment_of as _segment_of
from app.services.scm.customer_label import normalize_debtor_code
from app.services.scm.outstanding_reader import PO, SO, ReadResult, RowProblem, read_workbook
# The supplier back-create rule itself, shared with the purchase-HISTORY channel: the two
# books name the same creditors, so one of them creating and the other leaving them unlinked
# is the same supplier held twice on every screen that reads either.
from app.services.scm.supplier_back_create import (
    CREATED_SUPPLIERS_LISTED,
    back_create_supplier,
    supplier_slug,
)

logger = logging.getLogger(__name__)

# Demand class drives fulfilment priority: `scm.priority_policy.demand_class_weights` is
# keyed on it. A stated split that is not recognisably a project is retail, because that is
# the only other class the seeded weights carry and a new word would score as nothing.
# A split nobody states is NOT retail - see `_class_of`, imported above.

# A quantity difference below this is noise between two exports, not a decision. Same figure
# the diff uses, for the same reason.
_QTY_EPSILON = 0.0005

# S1 (ingest-parity-standardisation): the body MOVED to
# app.services.rules.master_rules.clean_supplier_name (D2), shared with the
# manual supplier create and the ESB masters push - this import keeps the
# name every call site in this module already uses.
from app.services.rules.master_rules import clean_supplier_name as _clean_supplier_name
from app.services.rules import customer_rules


@dataclass(frozen=True)
class _Binding:
    """How one document type maps onto its tables.

    The write path is parameterised by this rather than branching on `doc_type` in a dozen
    places, and `outstanding_diff` stays entirely document-agnostic. Two copy-pasted halves
    is how the two sides drift: the original bug was precisely a write path that ignored
    doc_type and put purchase orders into `sales_orders`.
    """

    header: type            # SalesOrder / PurchaseOrder
    line: type              # SalesOrderLine / PurchaseOrderLine
    number: str             # the header's human document number attribute
    header_fk: str          # the line's FK back to its header
    date: str               # the line's dated axis
    fulfilled: str          # quantity already delivered / received
    party_fk: str           # the header's counterparty FK
    party_code: str         # the file field naming that counterparty
    party: Optional[type]   # the counterparty model, or None when this path does not link one
    party_code_col: str     # that model's code column
    # Two DIFFERENT jobs, deliberately separate fields. `write_status` is what a header this
    # import creates (or lifts) is stamped with; `live_statuses` is the predicate for reading
    # existing ones. They were one field, and the narrower write value then hid every document
    # that had moved on: a purchase order that has taken a goods receipt sits in `received`,
    # so the diff saw a document with no lines and inserted them all again, doubling on-order.
    write_status: str
    live_statuses: tuple[str, ...]
    # Line columns the file RESTATES on every upload, as (line column, extras key). Named
    # for the money it started with, and it carries the non-money columns of the same shape
    # too (the PO currency, the SO per-line UoM): what the pairs share is the rule, not the
    # datatype - a value the file states is written on insert and refreshed on update, and a
    # value it omits leaves the column alone rather than blanking what we already hold.
    money_cols: tuple[tuple[str, str], ...] = ()
    # Header columns fed from the file, per document. Same (column, extras key) shape.
    header_cols: tuple[tuple[str, str], ...] = ()
    # Header columns the file FILLS rather than restates: written only where the header
    # holds nothing. Separate from `header_cols` because the two answer different questions.
    # `header_cols` is for figures the file is the system of record for (the PO date), so a
    # later extract corrects them; these are for a value a person may have set by hand, where
    # a weekly re-upload silently overwriting it is the failure.
    header_fill_cols: tuple[tuple[str, str], ...] = ()
    # The header column the counterparty CODE itself is kept in, when the header has one.
    # Its presence changes what an unresolvable code MEANS: the code is not lost, so the
    # order stays attributable ("Debtor 300-R009") and there is nothing to report. Absent
    # (the PO side), an unresolvable code leaves the document unlinked with no trace, which
    # is a resolution issue the operator has to see.
    party_code_header_col: Optional[str] = None
    # Whether an unresolved counterparty code gets a MINIMAL master row created for it
    # rather than only reported. Purchase-order creditor codes only, originally (AC
    # below): 2,177 of 5,243 PO-book documents in the captain's own file carried a
    # creditor code the supplier master had never seen, so every one of them landed
    # with no supplier at all.
    #
    # AC-P2-1/D8 (S2) turns this on for the sales side too, matching the ESB's own
    # `MasterRefResolver` (which has back-created a customer on a debtor code+name
    # pair since D2) - a debtor code the file ALSO names a customer for is worth
    # creating, and `party_code_header_col` still keeps the raw code on the header
    # even when a name is not sent, so a code-only row is exactly as it was.
    party_back_create: bool = False
    # The counterparty model's own "name" column, read only when `party_back_create` is
    # set - the file's label column (`SUPPLIER` on the PO export) becomes this row's
    # name, so the created supplier is not just a bare code on the expediting list.
    party_name_col: Optional[str] = None
    # The header column fulfilment priority is weighed on, and the header column the
    # project-versus-dealer split is stated in. Both None for purchase orders, which carry
    # no demand at all. `demand_split_col` is the source of truth (plan amendment of
    # 4 Aug 2026); `demand_class_col` is what the policy reads, stamped from it at import.
    demand_class_col: Optional[str] = None
    demand_split_col: Optional[str] = None
    # The header column the salesperson master is linked through, and None for purchase
    # orders, which have no agent at all. Its presence is also what turns the agent half of
    # the classification and the unmapped-agent report on, so the PO path cannot acquire
    # either by accident.
    agent_fk: Optional[str] = None


_BINDINGS: dict[str, _Binding] = {
    SO: _Binding(
        header=SalesOrder, line=SalesOrderLine,
        number="so_number", header_fk="sales_order_id",
        date="required_date", fulfilled="qty_delivered",
        party_fk="customer_id", party_code="debtor_code",
        # The customer IS linked now, exactly as the PO side links its supplier. It used to
        # be deliberately skipped because nothing read `sales_orders.customer_id` from this
        # channel; the plan's demand and trend popovers now name who ordered every line, and
        # 2,546 of the 2,548 orders this feed created had no customer at all - so every one
        # of them read "Unnamed customer" while the debtor code was sitting in the file.
        party=Customer, party_code_col="customer_code",
        # ... and the code itself is kept on the header whether or not it resolves, so an
        # order this feed cannot link is still attributable and still fixable later.
        party_code_header_col="debtor_code",
        # AC-P2-1/D8 (S2): back-created via `customer_rules.back_create_customer`
        # when the file names BOTH a debtor code and a name - `outstanding_reader`
        # already reads the export's `PROJECT/CUSTOMER` column into `Line.label`
        # for every SO row, so no new reading code is needed, only the write.
        party_back_create=True, party_name_col="customer_name",
        # `scm.committed_v` counts exactly one sales-order status, so writing and reading are
        # the same value here.
        write_status="open", live_statuses=("open",),
        # What the customer pays. The extract has stated it since migration 338 seeded the
        # `UNIT PRICE` alias, and nothing on this channel wrote it - so the demand popover,
        # whose whole job is "who ordered it and at what price", quoted a blank on every real
        # row. Same shape and same rule as the PO side's cost: an absent price stays NULL
        # rather than becoming a 0 that reads as goods given away.
        # `discount` and `total_inc` are the rest of the same money line, added the day the
        # detail page started printing them: a unit price beside a quantity is not what the
        # customer was charged. `uom` rides the same mechanism although it is not money at
        # all - the PO side already carries `currency` here for exactly that reason - because
        # what this tuple actually declares is "a line column the file restates and a
        # re-upload must refresh", and the per-line UoM override is one.
        money_cols=(
            ("unit_price", "unit_price"),
            ("discount", "discount"),
            ("line_total", "total_inc"),
            ("uom", "uom"),
        ),
        # OPTIONAL in the file and FILL-only on the header: no export carries an order type
        # today, so this is the column that lets a differently-worded export classify its own
        # documents the day it does, without a release. A value already on the header wins,
        # because that is what a person set and the extract is not the record of it.
        #
        # `order_date` is FILL-only for a different reason: the extract states the SO date
        # and the header had none (the trend reads `order_date` over 24 months, so an order
        # imported without one carries open demand and no history at all - "No order
        # history" beside 51 open units). It fills the gap; an existing date, wherever it
        # came from, stands.
        header_fill_cols=(("order_type", "order_type"), ("order_date", "order_date")),
        demand_class_col="demand_class", demand_split_col="order_type",
        agent_fk="sales_agent_id",
    ),
    PO: _Binding(
        header=PurchaseOrder, line=PurchaseOrderLine,
        number="po_number", header_fk="purchase_order_id",
        date="expected_date", fulfilled="qty_received",
        party_fk="supplier_id", party_code="creditor_code",
        party=Supplier, party_code_col="supplier_code",
        party_back_create=True, party_name_col="supplier_name",
        # NOT "draft". `scm.on_order_v` counts only placed orders, deliberately excluding
        # drafts (M4-D5), and an outstanding-PO extract is a book of orders already placed
        # with suppliers. Writing them as drafts would import the supply and then hide it.
        write_status="active",
        # Every status in which a purchase order is still a live document, matching
        # `scm.on_order_v`, `dashboard_service.PLACED_PO_STATUSES` and
        # `purchase_order_service._ON_ORDER_STATUSES`. A part-received order is the same
        # order, so Monday's extract re-uploaded must diff against it rather than re-order it.
        live_statuses=("active", "received", "partial", "closed"),
        # (line column, extras key). Cost is what the cash co-pilot ranks on, so an absent
        # cost stays absent rather than becoming a zero that reads as free goods.
        # `discount` and `total_inc` are the REST of the same money line, added the day the
        # purchase-order detail page started printing them: a unit cost beside a quantity is
        # not what the supplier charged. `uom` rides the same mechanism although it is not
        # money at all - `currency` already does, for exactly that reason - because what this
        # tuple actually declares is "a line column the file restates and a re-upload must
        # refresh", and the per-line UoM override is one. Same four, in the same order, as
        # the sales book above: the two feeds must not diverge on which of the columns their
        # shared reader carries they choose to keep.
        money_cols=(
            ("unit_cost", "unit_cost"),
            ("discount", "discount"),
            ("line_total", "total_inc"),
            ("uom", "uom"),
            ("currency", "currency"),
        ),
        # `scm.receipt_lead_v` measures lead days from `po.issue_date`, so an order imported
        # without it contributes nothing to the measured supplier lead time.
        header_cols=(("issue_date", "order_date"), ("currency", "currency")),
    ),
}


def _binding(doc_type: str) -> _Binding:
    try:
        return _BINDINGS[doc_type]
    except KeyError:  # pragma: no cover - guarded at the route
        raise ValueError(f"no write binding for document type {doc_type!r}")


@dataclass
class ResolutionIssue:
    row_number: int
    field: str
    value: str
    reason: str


@dataclass
class AgentNotice:
    """An agent code in this file that can classify nothing, and why.

    Its own list rather than a row problem or a resolution issue, because it is neither: the
    file is fine, the code resolved (or was created), and nothing is being skipped. What it
    reports is a gap in MASTER data that only the client can close - which of his 38 codes
    still need a demand class - and that is a different question from "which rows of this file
    could not be read". Kept off the row lists so it cannot bury them.
    """

    code: str
    #: True when this upload is the first thing that has ever named the agent.
    is_new: bool
    reason: str


@dataclass
class PreviewResult:
    doc_type: str
    scope_documents: tuple[str, ...]
    counts: dict
    total_rows: int
    unmapped_headers: list[str]
    missing_columns: list[str]
    row_problems: list[RowProblem] = field(default_factory=list)
    resolution_issues: list[ResolutionIssue] = field(default_factory=list)
    # Documents whose demand class this file cannot decide. Reported, never refused
    # (D23, captain 2026-09-06, AC-P2-8 - reverses QP1/26 Aug 2026): the document still
    # lands with `demand_class` NULL, on both this channel and the ESB, which has never
    # refused on this (`WARN_UNCLASSIFIED_DEMAND`). Named so a re-upload once the customer's
    # market segment or the agent's class is fixed can tell which orders still need it.
    unclassified_documents: list[str] = field(default_factory=list)
    # AC-P2-1 (guide-writer check, 2026-09-06): the codes `apply`'s back-create block
    # WOULD create - reported (never created) on preview, same shape as `apply`'s own
    # `suppliers_created_codes`/`customers_created_codes`. Whichever party this
    # doc_type does not bind stays empty (an SO upload never back-creates a supplier).
    suppliers_created: list[str] = field(default_factory=list)
    customers_created: list[str] = field(default_factory=list)
    samples: dict = field(default_factory=dict)
    # Documents this upload would lift to the live status. Same key as `apply`'s response, so
    # the confirm screen shows the side effect BEFORE it happens and the commit reports the
    # same fact afterwards.
    activated_documents: list[str] = field(default_factory=list)
    # Agent codes in this file that carry no demand class, so the client can see which of his
    # master rows still need one. Same key on both responses, same reason as above.
    unmapped_agents: list[AgentNotice] = field(default_factory=list)
    # Additive safety notices that do not map to a single row or document: "N rows carry a
    # date the reader could not read", and "N rows are shipping orders". The operator reads
    # `row_problems` for WHICH rows; this is the one-line summary that says what the write
    # will do about them, rather than leaving them to infer it from the per-row reasons.
    warnings: list[str] = field(default_factory=list)
    # How many rows this PURCHASE book spent on shipping orders, which this channel does not
    # write. Its own figure rather than one warning string the screen would have to parse:
    # "would import" is a count, and a book that is half SPO would otherwise claim to import
    # twice what it will.
    shipping_order_rows: int = 0
    # What the shipping-order half of the same file would do (R4): the documents and lines
    # it files into `spo_allocations`, the locations no warehouse matches (the line is kept
    # either way), and the open lines it would CLOSE because this book no longer states
    # them. The last one is the number an operator has to see before pressing Confirm
    # upload, not after: a PO-only export settles every shipping order this channel holds.
    spo_documents: int = 0
    spo_lines: int = 0
    spo_unknown_locations: int = 0
    spo_closed: int = 0
    # The same lines split by what they would DO to the table: never held before, held and
    # differing, held and identical. `spo_lines` is their sum. Counted because the screen
    # used to print "721 rows are shipping orders" beside "nothing would change" - both
    # honestly computed, and neither able to contradict the other, because this half stated
    # how many lines it carried and never what they would change.
    spo_new: int = 0
    spo_changed: int = 0
    spo_unchanged: int = 0

    @property
    def ok(self) -> bool:
        # D23 (AC-P2-8): an unclassifiable document no longer refuses the file - it is
        # reported (`unclassified_documents`) and lands with `demand_class` NULL, same
        # end state the ESB has always reached via `WARN_UNCLASSIFIED_DEMAND`.
        return not self.missing_columns

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "ok": self.ok,
            "scope_documents": list(self.scope_documents),
            "counts": self.counts,
            "total_rows": self.total_rows,
            "unmapped_headers": self.unmapped_headers,
            "missing_columns": self.missing_columns,
            "row_problems": [asdict(p) for p in self.row_problems],
            "resolution_issues": [asdict(i) for i in self.resolution_issues],
            # S1 (review re-check, 2026-09-06): count + capped numbers, same
            # shape `apply`'s response uses - a preview that returned the
            # full list while apply returned an int was a silent contract
            # mismatch the FE had to paper over.
            "unclassified_documents": len(self.unclassified_documents),
            "unclassified_documents_numbers": list(self.unclassified_documents)[:CREATED_SUPPLIERS_LISTED],
            # AC-P2-1: same shape as `apply`'s response - count + capped codes.
            "suppliers_created": len(self.suppliers_created),
            "suppliers_created_codes": self.suppliers_created[:CREATED_SUPPLIERS_LISTED],
            "customers_created": len(self.customers_created),
            "customers_created_codes": self.customers_created[:CREATED_SUPPLIERS_LISTED],
            "samples": self.samples,
            "activated_documents": list(self.activated_documents),
            "unmapped_agents": [asdict(a) for a in self.unmapped_agents],
            "warnings": self.warnings,
            "shipping_order_rows": self.shipping_order_rows,
            "spo_documents": self.spo_documents,
            "spo_lines": self.spo_lines,
            "spo_unknown_locations": self.spo_unknown_locations,
            "spo_closed": self.spo_closed,
            "spo_new": self.spo_new,
            "spo_changed": self.spo_changed,
            "spo_unchanged": self.spo_unchanged,
        }


@dataclass
class _Resolved:
    lines: list[Line]
    issues: list[ResolutionIssue]
    product_by_code: dict
    warehouse_by_code: dict
    # Which counterparty each document in the file belongs to. Per DOCUMENT, not per line:
    # the counterparty lives on the header, and a file naming two different creditors on one
    # PO number is a file problem, not a shape this write path should invent support for.
    party_by_doc: dict
    # The counterparty CODE behind each of those, so a report can name it instead of an id.
    party_code_by_doc: dict = field(default_factory=dict)
    # Every counterparty code (normalised) this file names that no master row matched,
    # mapped to the file's own label for it - read only where `_Binding.party_back_create`
    # is set. `apply()` creates one row per key here; `preview()` never does, matching
    # every other creation this module performs. A code the file never labels (the
    # column is missing, or blank on every row) falls back to the code itself.
    party_label_by_code: dict = field(default_factory=dict)
    # The SAME codes, first-seen exactly as the file printed them (case, punctuation) -
    # what the created row's code column is stamped with. Matching stays on the
    # normalised key; display stays on what the operator's own file said.
    party_raw_code_by_code: dict = field(default_factory=dict)
    # PO-book fallback, read only where `_Binding.party_back_create` is set: which
    # document belongs to which CLEANED counterparty name, for a file that names the
    # creditor but carries no code column at all (`party_code` blank on every row). A
    # separate map from the code ones above rather than a shared key space, because a
    # slug is generated, not read off the file, and the two must never collide with a
    # genuine code creating (or matching) the wrong row.
    party_name_key_by_doc: dict = field(default_factory=dict)
    # Normalised name key -> the CLEANED name itself (display form, currency suffix
    # already stripped). `apply()` creates one supplier per key here, exactly as the code
    # map does for `party_raw_code_by_code`.
    party_cleaned_name_by_key: dict = field(default_factory=dict)
    # Header-level values the file states per document (`_Binding.header_cols`), first
    # non-empty wins: the extract repeats them on every row of the document.
    header_by_doc: dict = field(default_factory=dict)
    # Which agent CODE (normalised) sold each document. Per document and first non-empty
    # wins, and a document naming two different agents is REPORTED - the same rule as the
    # counterparty, in both halves, for the same reason: the extract states one value per
    # document on every row of it, so a second value is a file to fix rather than a shape to
    # support. Normalised by `sales_agent_service.normalize_code`, the same authority the
    # master is keyed by, so the class lookup, the row creation and the report all compare
    # one string.
    agent_by_doc: dict = field(default_factory=dict)


def _norm(code: str) -> str:
    return (code or "").strip().upper()


def _in_chunks(query, expr, values) -> list:
    """`query.filter(expr.in_(chunk)).all()` over every chunk of `values`, concatenated.

    Every resolver below used to hand the whole code/name/document set to ONE `IN (...)`
    clause - fine at a few hundred, and exactly the shape that shipped a 13,519-parameter
    statement on the file that dropped the connection (S5 review round 1). `query` is built
    with everything EXCEPT this predicate (its own filters, joins, `order_by`), so the same
    base query is safely reused across chunks - `Query.filter()` returns a new query rather
    than mutating the one it was called on.
    """
    values = list(values)
    if not values:
        return []
    out: list = []
    for chunk in _chunked(values):
        out.extend(query.filter(expr.in_(chunk)).all())
    return out


def _resolve_parties(db: Session, read: ReadResult, bind: _Binding) -> dict[str, str]:
    """Counterparty code -> id, within the active company.

    ORM, not raw SQL, for the same reason as every other lookup here: the isolation filter
    runs on ORM execution only, and reading another company's supplier would attribute this
    order to the wrong one. Who is late is the entire point of an expediting list.
    """
    if bind.party is None:
        return {}
    codes = {_norm(x.get("party_code")) for x in read.extras.values() if x.get("party_code")}
    if not codes:
        return {}
    code_col = getattr(bind.party, bind.party_code_col)
    rows = _in_chunks(db.query(bind.party.id, code_col), func.upper(code_col), codes)
    out = {_norm(code): str(pid) for pid, code in rows}
    if bind.party is Customer:
        # DEBTOR-CODE-UNIQUE: until the merge has run a code can still hold several
        # customers; resolve those to the one the merge keeps instead of whichever row the
        # database returned last.
        counts: dict[str, int] = {}
        for _pid, code in rows:
            counts[_norm(code)] = counts.get(_norm(code), 0) + 1
        for code, n in counts.items():
            if n > 1:
                row = customer_identity.customer_by_code(db, code)
                if row is not None:
                    out[code] = str(row.id)
    return out


def _resolve_parties_by_name(db: Session, read: ReadResult, bind: _Binding) -> dict[str, str]:
    """Cleaned counterparty NAME -> id, within the active company.

    PO-book fallback only (`bind.party_back_create`): read only for rows that state no
    `party_code` at all, since a row that DOES carry a code is resolved by
    `_resolve_parties` and this map is never consulted for it. Matched case-insensitively
    on the cleaned name - the master holds `XIAMEN TAIYANG TECHNOLOGY CO.,LTD`, the file
    says `XIAMEN TAIYANG TECHNOLOGY CO.,LTD (RMB)` - so the raw cell would never match.

    `supplier_name` carries no uniqueness constraint (unlike `supplier_code`), so more
    than one supplier can legitimately share a name - ordered by id so the dict
    comprehension's last-write-wins lands on the same row every time, rather than
    whatever order Postgres happened to return. The order survives chunking: a given NAME
    falls entirely within one chunk (only the CODE/NAME set is split, never the party rows a
    single name matches), so the tie-break is exactly as correct per chunk as it was whole.
    """
    if bind.party is None or not bind.party_back_create or bind.party_name_col is None:
        return {}
    names = {
        _norm(cleaned)
        for x in read.extras.values()
        if not x.get("party_code") and (cleaned := _clean_supplier_name(x.get("party_name")))
    }
    if not names:
        return {}
    name_col = getattr(bind.party, bind.party_name_col)
    rows = _in_chunks(
        db.query(bind.party.id, name_col).order_by(bind.party.id.desc()),
        func.upper(name_col), names,
    )
    return {_norm(name): str(pid) for pid, name in rows}


def _resolve(db: Session, read: ReadResult, bind: _Binding) -> _Resolved:
    """Map codes in the file onto real rows, reporting whatever cannot be resolved.

    Deliberately ORM queries, not raw SQL. `products`, `warehouses` and `sales_orders` are
    all company-scoped, and the isolation filter runs on ORM execution only - a raw
    `SELECT ... FROM products` sees EVERY company. That matters here rather than in theory:
    each company carries its own copy of the catalogue, so 11,390 product codes exist twice
    in this database. A raw lookup keyed by code would resolve to whichever row came back
    last and could attach an imported order line to another company's product.
    """
    issues: list[ResolutionIssue] = []

    item_codes = {_norm(l.item_code) for l in read.lines if l.item_code}
    loc_codes = {_norm(l.location) for l in read.lines if l.location}

    products: dict[str, str] = {
        _norm(code): str(pid)
        for pid, code in _in_chunks(
            db.query(Product.id, Product.product_code), func.upper(Product.product_code),
            item_codes,
        )
    }

    warehouses: dict[str, str] = {
        _norm(code): str(wid)
        for wid, code in _in_chunks(
            db.query(Warehouse.id, Warehouse.warehouse_code),
            func.upper(Warehouse.warehouse_code), loc_codes,
        )
    }

    parties = _resolve_parties(db, read, bind)
    names_by_key = _resolve_parties_by_name(db, read, bind)
    party_by_doc: dict[str, str] = {}
    party_code_by_doc: dict[str, str] = {}
    party_label_by_code: dict[str, str] = {}
    party_raw_code_by_code: dict[str, str] = {}
    party_name_key_by_doc: dict[str, str] = {}
    party_cleaned_name_by_key: dict[str, str] = {}
    header_by_doc: dict[str, dict] = {}
    agent_by_doc: dict[str, str] = {}

    kept: list[Line] = []
    for l in read.lines:
        row = int(l.row_ref) if (l.row_ref or "").isdigit() else 0
        extra = read.extras.get(str(l.row_ref), {})
        pid = products.get(_norm(l.item_code))
        if not pid:
            # Never auto-create: a typo would become a SKU that gets planned and bought.
            issues.append(ResolutionIssue(row, "item_code", l.item_code,
                                          "no product with this code"))
            continue
        if l.location and _norm(l.location) not in warehouses:
            # AC-P2-3/D9 (S2): KEPT, not skipped, matching the ESB's own
            # warehouse-unresolved=NULL+warning rule - an unresolvable
            # LOCATION does not invalidate the demand a line represents the
            # way an unresolvable ITEM does. The write path below already
            # leaves `warehouse_id` NULL for it (`resolved.warehouse_by_code
            # .get(...)` on a location this dict has no entry for).
            issues.append(ResolutionIssue(row, "stock_location", l.location,
                                          "no warehouse with this code"))
        kept.append(l)

        # Who sold it. Read only where the document type HAS an agent, so the purchase book
        # cannot pick one up from a stray column, and normalised through
        # `sales_agent_service.normalize_code` - the ONE authority - so the key this builds is
        # byte-for-byte the key the master is looked up and created under. A second
        # normaliser that merely happened to agree would, the day the two drifted, report
        # every agent as new and create a duplicate master row on every upload.
        if bind.agent_fk:
            agent_code = sales_agent_service.normalize_code(extra.get("agent"))
            if agent_code:
                seen_agent = agent_by_doc.get(l.doc_number)
                if seen_agent is not None and seen_agent != agent_code:
                    # One document is sold by one agent, so a file naming two is a file to
                    # fix. The first row still wins the write, but SAID OUT LOUD - exactly the
                    # counterparty rule below. Silent first-wins would attribute half the
                    # document's demand to a salesperson who never sold it, and where the two
                    # agents carry different demand classes it would also decide the order's
                    # fulfilment priority from whichever row the export happened to list first.
                    issues.append(ResolutionIssue(
                        row, "agent", agent_code,
                        f"{l.doc_number} names two different agent values, "
                        f"{seen_agent} and {agent_code}; the first is being used"))
                agent_by_doc.setdefault(l.doc_number, agent_code)

        # Header-level values the file repeats on every row of the document. First non-empty
        # wins rather than last, so a trailing blank cell cannot erase a stated date.
        if bind.header_cols or bind.header_fill_cols:
            slot = header_by_doc.setdefault(l.doc_number, {})
            for _col, key in bind.header_cols + bind.header_fill_cols:
                if slot.get(key) is None and extra.get(key) is not None:
                    slot[key] = extra.get(key)

        # The counterparty is resolved but NEVER gates the line, and (AC-P2-3/D9, S2)
        # neither does the location any more - only the ITEM does. A quantity with no
        # product cannot be planned, so that row is dropped; a quantity at an unknown
        # location is still real demand/supply, just unlocated, so it is KEPT with
        # `warehouse_id` NULL (see the location check above). An unknown creditor leaves
        # the quantity and the arrival date perfectly true, and dropping the line would
        # make on-order UNDERSTATE supply, which is what causes a second, unnecessary
        # purchase. So it is reported and the header is left unlinked.
        code = _norm(extra.get("party_code"))
        if bind.party is None:
            # Nothing is LINKED on this path, but the code is not discarded either: the
            # demand class falls back to this customer's market segment, and a code kept
            # only per row is a fallback that can never fire. First non-empty wins, the same
            # rule as the header columns, because the extract repeats it on every row.
            if code:
                party_code_by_doc.setdefault(l.doc_number, code)
            continue
        if not code:
            # PO-book fallback: the file names a creditor but states no CODE at all - the
            # shape of the captain's real "PO & SPO outstanding.xlsx" export. Only where
            # `party_back_create` is set, matching the code-side rule that only the purchase
            # book back-creates a counterparty.
            if bind.party_back_create:
                cleaned = _clean_supplier_name(extra.get("party_name"))
                if cleaned:
                    name_key = _norm(cleaned)
                    seen_name_key = party_name_key_by_doc.get(l.doc_number)
                    if seen_name_key is not None and seen_name_key != name_key:
                        # One document has one creditor, exactly the rule the code path
                        # enforces below - the first row still wins, said out loud.
                        issues.append(ResolutionIssue(
                            row, "party_name", cleaned,
                            f"{l.doc_number} names two different creditors, "
                            f"{party_cleaned_name_by_key.get(seen_name_key, seen_name_key)} "
                            f"and {cleaned}; the first is being used"))
                    else:
                        party_name_key_by_doc.setdefault(l.doc_number, name_key)
                        party_cleaned_name_by_key.setdefault(name_key, cleaned)
                        pid_by_name = names_by_key.get(name_key)
                        if pid_by_name is not None:
                            party_by_doc.setdefault(l.doc_number, pid_by_name)
                        # Else: back-created on apply, and reported by the preview
                        # under `suppliers_created_codes` (see the code branch below
                        # for why it is no longer a resolution issue).
            continue
        pid_party = parties.get(code)
        if pid_party is None:
            # An unresolvable code is only a PROBLEM when it would otherwise be lost. The
            # sales book keeps it on the header, so the order stays attributable and there
            # is nothing for the operator to fix in this file - reporting 2,546 of them
            # would bury the rows that really did fail.
            #
            # AC-P2-1/D8 (S2): a Customer additionally needs a NAME to back-create (the
            # unique index is on the pair) - a code-only debtor row falls through to the
            # `party_code_header_col` branch below exactly as it did before this bind
            # turned `party_back_create` on, kept and unreported.
            can_back_create = bind.party_back_create and (
                bind.party is not Customer or l.label
            )
            if can_back_create:
                # The purchase book has nowhere to put an unlinked code, and 2,177 of
                # 5,243 documents in the captain's own file arrived exactly this way -
                # not a typo, a supplier AutoCount already deals with that the master
                # simply hasn't caught up on. `apply()` creates a minimal party row
                # for it and links the document. NOT a resolution issue any more
                # (AC-P2-1, CI catch 2026-09-06): the row LANDS, and a resolution
                # issue is what the FE counts as a skipped row - with D8 back-creating
                # customers on the sales book too, an SO book full of debtors the master
                # has not caught up on read as "N rows skipped" for N rows that import.
                # The preview reports them under `suppliers_created_codes` /
                # `customers_created_codes` instead (`_would_create_parties`).
                party_code_by_doc.setdefault(l.doc_number, code)
                party_raw_code_by_code.setdefault(code, extra.get("party_code") or code)
                if code not in party_label_by_code and l.label:
                    party_label_by_code[code] = l.label
            elif bind.party_code_header_col:
                party_code_by_doc.setdefault(l.doc_number, code)
            else:
                issues.append(ResolutionIssue(
                    row, bind.party_code, code,
                    f"no {bind.party.__name__.lower()} with this code"))
            continue
        seen_code = party_code_by_doc.get(l.doc_number)
        if seen_code is not None and seen_code != code:
            # One document has one counterparty, so a file naming two is a file to fix. The
            # first row still wins the write, but silently: half the document's lines would
            # otherwise be attributed to a company that never sold them, and the supplier
            # scorecard would blame them for a delivery they were never asked to make.
            issues.append(ResolutionIssue(
                row, bind.party_code, code,
                f"{l.doc_number} names two different {bind.party_code} values, "
                f"{seen_code} and {code}; the first is being used"))
            continue
        party_by_doc.setdefault(l.doc_number, pid_party)
        party_code_by_doc.setdefault(l.doc_number, code)

    return _Resolved(lines=kept, issues=issues, product_by_code=products,
                     warehouse_by_code=warehouses, party_by_doc=party_by_doc,
                     party_code_by_doc=party_code_by_doc,
                     party_label_by_code=party_label_by_code,
                     party_raw_code_by_code=party_raw_code_by_code,
                     party_name_key_by_doc=party_name_key_by_doc,
                     party_cleaned_name_by_key=party_cleaned_name_by_key,
                     header_by_doc=header_by_doc, agent_by_doc=agent_by_doc)


def _existing_lines(db: Session, docs: set[str], bind: _Binding, *,
                    fulfilled_into: Optional[dict[str, float]] = None,
                    money_into: Optional[dict[str, dict]] = None) -> list[Line]:
    """Current outstanding lines for the documents named in the file, as diff Lines.

    `row_ref` carries the DB line id so apply() can update in place rather than matching
    twice by content.

    `fulfilled_into`, when given, is filled with each returned line's already
    delivered/received quantity. The diff has no place for it (it is deliberately
    document-agnostic and compares OUTSTANDING figures) but the honesty checks need it, and a
    caller that only wants the diff should not have to unpack a pair to get it.

    `money_into` is the same idea for the money columns, and it is read for every line the
    file restates unchanged - so it comes out of THIS query rather than a lookup per row. On
    the client's 4,349-row book that is one statement instead of four thousand.
    """
    if not docs:
        return []
    # ORM, so the company-isolation filter applies. See `_resolve`: another company's order
    # carrying the same SO number must never be diffed against this upload, let alone closed
    # by it.
    line, header = bind.line, bind.header
    fulfilled = getattr(line, bind.fulfilled)
    money = [getattr(line, col) for col, _key in bind.money_cols]
    # The document-number IN chunked (S5 review round 1): everything else about the query -
    # joins, the other filters - is built ONCE and reused per chunk (`_in_chunks`), only the
    # documents named ever grows with the file.
    base_query = (
        db.query(
            line.id,
            getattr(header, bind.number),
            Product.product_code,
            Warehouse.warehouse_code,
            (line.qty_ordered - fulfilled).label("outstanding"),
            getattr(line, bind.date),
            fulfilled,
            *money,
        )
        .join(header, header.id == getattr(line, bind.header_fk))
        .join(Product, Product.id == line.product_id)
        .outerjoin(Warehouse, Warehouse.id == line.warehouse_id)
        .filter(
            # Every status the readers call live, not only the one this import writes.
            header.status.in_(list(bind.live_statuses)),
            # Open lines ONLY. A closed line that comes back arrives as an ADD, which is where
            # apply() reopens it in place. Returning closed lines here instead would make the
            # pair read as `unchanged` AND break idempotency, because a closed line absent
            # from the next upload would be re-closed on every single re-run.
            line.line_status == "open",
            line.qty_ordered > fulfilled,
        )
    )
    rows = _in_chunks(base_query, getattr(header, bind.number), docs)
    if fulfilled_into is not None:
        fulfilled_into.update({str(r[0]): float(r[6] or 0) for r in rows})
    if money_into is not None:
        money_into.update({
            str(r[0]): {col: r[7 + i] for i, (col, _key) in enumerate(bind.money_cols)}
            for r in rows
        })
    return [
        Line(doc_number=r[1], item_code=r[2], location=r[3] or "",
             qty=float(r[4] or 0), required_date=r[5], row_ref=str(r[0]))
        for r in rows
    ]


def _header_state(db: Session, docs: tuple[str, ...],
                  bind: _Binding) -> dict[str, tuple[str, Optional[str]]]:
    """Each in-scope document's current (status, counterparty id), for the headers that exist.

    ORM for the same isolation reason as every other lookup here.
    """
    if not docs:
        return {}
    number_col = getattr(bind.header, bind.number)
    rows = _in_chunks(
        db.query(number_col, bind.header.status, getattr(bind.header, bind.party_fk)),
        number_col, docs,
    )
    return {r[0]: (r[1], str(r[2]) if r[2] else None) for r in rows}


# `_segment_of` itself now lives in `demand_classifier.py` (D4) and is imported above under
# this name - moved rather than duplicated so the AutoCount document ingest classifies off
# the exact same read. There is deliberately no defaulting wrapper around it (QP1): the
# import refuses a document it cannot classify rather than writing a guess. Since the
# amendment of 4 Aug 2026 the segment was the FALLBACK rather than the source of truth,
# because it was NULL on 3,276 of 3,284 customers. Migration 425 changed that balance - it
# stamped 503 customers retail so the real AutoCount export, which carries no order type
# column, can classify at all - but not the rule: silence here is still silence, and
# treating it as "retail" is exactly the invisible mis-prioritisation the refusal exists to
# prevent.


def _demand_state(db: Session, docs: tuple[str, ...],
                  bind: _Binding) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Each existing in-scope document's (stated split, current classification).

    ORM for the same isolation reason as every other lookup here: another company's order
    carrying this SO number must not decide this one's fulfilment priority.
    """
    if bind.demand_class_col is None or bind.demand_split_col is None or not docs:
        return {}
    number_col = getattr(bind.header, bind.number)
    rows = _in_chunks(
        db.query(number_col, getattr(bind.header, bind.demand_split_col),
                 getattr(bind.header, bind.demand_class_col)),
        number_col, docs,
    )
    return {r[0]: (r[1], r[2]) for r in rows}


def _agent_state(db: Session, resolved: _Resolved,
                 bind: _Binding) -> tuple[dict[str, Optional[str]], list[AgentNotice]]:
    """Every agent code this file names: the class it carries, and what needs saying about it.

    A read, never a write: `preview` runs this too and must create nothing, so an unknown code
    is REPORTED here and created by `apply` (which is also the only place that should be
    inventing master data).

    Two kinds of notice, told apart because the answers differ. A code nobody holds is new
    master data the client has just acquired without asking for it, and it wants a class. A
    code held with a NULL class is one of the 38 he already has and has not got to yet - which
    is the state every one of them ships in, since the I/III/IV suffix maps to neither company
    nor market segment anywhere in this database and guessing it would silently mis-prioritise
    real orders (UAC AC-3.3). An agent that HAS a class produces no notice at all: a list that
    names all 38 every week is a list nobody reads.
    """
    if not bind.agent_fk:
        return {}, []
    codes = sorted(set(resolved.agent_by_doc.values()))
    if not codes:
        return {}, []
    held = sales_agent_service.resolve_many(db, codes)
    classes: dict[str, Optional[str]] = {}
    notices: list[AgentNotice] = []
    for code in codes:
        agent = held.get(code)
        if agent is None:
            notices.append(AgentNotice(
                code, True,
                "new agent, unclassified: this upload is the first thing to name this agent "
                "code, so the master row is being created with no demand class"))
            continue
        classes[code] = agent.demand_class
        if not agent.demand_class:
            notices.append(AgentNotice(
                code, False,
                "this agent carries no demand class, so it cannot classify an order that "
                "states nothing else"))
    return classes, notices


def _classify_demand(db: Session, diff: Diff, resolved: _Resolved,
                     state: dict[str, tuple[Optional[str], Optional[str]]],
                     bind: _Binding,
                     agent_classes: dict[str, Optional[str]]) -> tuple[dict[str, str],
                                                                       list[RowProblem]]:
    """What each in-scope document's demand class should be, and what could not be decided.

    Per DOCUMENT, in order: the order type the header already carries, then the one the file
    states (which is also the value that fills an absent header), then the demand class held
    against the AGENT who sold it, and last the customer's market segment via the debtor code
    the file names. When none of the four answers, the document is REPORTED and left exactly
    as it was.

    The agent comes BEFORE the customer (captain, 28 Aug 2026). The sales force is split by
    channel - a project agent sells project work, a trade agent sells trade - so who sold the
    order says what the order is for. The customer does not: one debtor buys through both
    channels (YOTU BUILDER SDN BHD held nine project orders and seven the customer master
    called retail), so its segment is a default about the account, not a statement about
    this order. Read the other way round, a project customer's segment marked retail hid
    SO381895 from the fulfilment board. The two order types still stand ahead of both:
    they are statements about THIS document.

    Defaulting to retail is the failure this column exists to avoid: it under-prioritises a
    project order invisibly, and the wrong answer is stable, so no later upload surfaces it
    either. The symptom is a project that simply stops winning stock.

    Computed here rather than inside `apply` so preview and the commit report the same thing,
    which is the rule the rest of this module already follows.

    Reported as a `RowProblem` rather than a `ResolutionIssue`: the issue list means "a code
    in this file names nothing we hold", and this is the opposite complaint - the file states
    nothing to resolve. Both lists are put in front of the same person on the same screen.

    Since QP1 (captain, 26 Aug 2026) an unclassified document is a VALIDATION ERROR and not
    a notice: the second return value is what refuses the whole upload, document numbers and
    all. "Nothing should be unclassified", and the plan page dropped its Unclassified column
    on the strength of it, so an import that could quietly create another one would put the
    demand somewhere no screen shows it. The refusal is per FILE rather than per document
    because a half-imported order book is a book that disagrees with AutoCount.
    """
    if bind.demand_class_col is None:
        return {}, []

    # The row a document is reported against. The report is per DOCUMENT and both lists are
    # per row, so it is pinned to the first row that named it: a row number the operator can
    # actually find in the file beats a 0 that points at nothing.
    first_row: dict[str, int] = {}
    for l in resolved.lines:
        if l.doc_number not in first_row:
            first_row[l.doc_number] = int(l.row_ref) if (l.row_ref or "").isdigit() else 0

    out: dict[str, str] = {}
    problems: list[RowProblem] = []
    # Resolved once, not per document: `None` on an ambiguous/unset scope leaves
    # `classify_document`'s segment read exactly as ambient-scope-only as `_segment_of`
    # always was, rather than inventing a company this upload never named.
    company_id = resolve_write_company_id(get_company_scope(db), ambiguous=None)
    for number in diff.scope_documents:
        stored_split, current = state.get(number, (None, None))
        stated_split = resolved.header_by_doc.get(number, {}).get("order_type")
        agent_code = resolved.agent_by_doc.get(number, "")
        # ONE ladder (D4): `classify_document` is also what the AutoCount document ingest
        # calls, so the two can never decide the same order two different ways.
        cls = classify_document(
            db,
            stored_order_type=stored_split,
            stated_order_type=stated_split,
            agent_demand_class=agent_classes.get(agent_code),
            debtor_code=resolved.party_code_by_doc.get(number, ""),
            company_id=company_id,
        )
        if cls is not None:
            out[number] = cls
            continue
        if current:
            # Already classified, and this upload knows nothing better. Never downgraded:
            # a project quietly demoted to retail mid-fulfilment shows up only as an order
            # that stopped winning stock, weeks later, with nothing to point at.
            continue
        # Named specifically, because the four sources have four different fixes: an order
        # type on the header, an order type in the export, a demand class on the agent, or
        # a market segment on the customer. "Unclassified" alone tells the operator nothing
        # about which one to go and set. The DEBTOR is named beside the order because it is
        # the fix they will reach for first: give that customer a market segment and the
        # whole file goes in.
        agent_says = (f"agent {agent_code} carries no demand class" if agent_code
                      else "it names no agent")
        debtor = resolved.party_code_by_doc.get(number, "")
        # The buyer's NAME beside the code where the file states one. The code is what the
        # operator searches on and the name is what tells them whether they have the right
        # customer open, and this message is the only place they get either.
        label = resolved.party_label_by_code.get(debtor) if debtor else None
        who = f"debtor {debtor}" + (f" ({label})" if label else "")
        debtor_says = (f"{who} carries no customer market segment" if debtor
                       else "it names no debtor code")
        problems.append(RowProblem(
            first_row.get(number, 0),
            f"{number} states no order type, {debtor_says}, and {agent_says}, so its "
            "demand class cannot be decided; nothing is imported until it can",
            value=number))
    return out, problems


def _honesty_issues(diff: Diff, fulfilled_by_line: dict[str, float],
                    header_state: dict[str, tuple[str, Optional[str]]],
                    resolved: _Resolved, bind: _Binding) -> list[ResolutionIssue]:
    """What the file says that a person has to see before it is believed.

    Computed from the diff alone so preview and apply report the SAME thing: a preview that
    does not mention a side effect the commit performs is a preview that lies.
    """
    out: list[ResolutionIssue] = []

    for c in diff.of_kind(QTY_CHANGED, DATE_AND_QTY_CHANGED):
        if c.before is None or c.after is None:
            continue
        fulfilled = fulfilled_by_line.get(str(c.before.row_ref))
        if fulfilled is None:
            continue
        ordered = c.before.qty + fulfilled
        # The extract states what is OUTSTANDING, so ordered becomes received plus outstanding.
        # Right for a fresh file; a file taken BEFORE the goods receipt inflates the order by
        # exactly what has already arrived. The two are indistinguishable from the file alone,
        # so the write still happens and the question is asked rather than guessed at.
        if fulfilled <= 0 or (fulfilled + c.after.qty) <= ordered + _QTY_EPSILON:
            continue
        row = int(c.after.row_ref) if (c.after.row_ref or "").isdigit() else 0
        out.append(ResolutionIssue(
            row, "qty_ordered", str(c.after.qty),
            f"{c.doc_number} / {c.item_code} already has {fulfilled:g} received, so this "
            f"raises the order from {ordered:g} to {fulfilled + c.after.qty:g}; either the "
            f"order grew or this extract predates the receipt"))

    for number in diff.scope_documents:
        _status, current_party = header_state.get(number, (None, None))
        party_id = resolved.party_by_doc.get(number)
        # A code-less document resolved through the NAME fallback has nothing in
        # `party_code_by_doc` to name here - fall back to the cleaned name the file
        # actually said, so the message still names what changed instead of reading
        # "changed to ".
        code = (resolved.party_code_by_doc.get(number)
                or resolved.party_cleaned_name_by_key.get(
                    resolved.party_name_key_by_doc.get(number, "")))
        if party_id and current_party and current_party != str(party_id):
            # The file is the system of record for who we bought from: chasing the wrong
            # supplier is the failure mode. Overwritten, and said out loud.
            out.append(ResolutionIssue(
                0, bind.party_code, code or "",
                f"the {bind.party_code} on {number} disagrees with the file and is being "
                f"changed to {code}"))

    return out


#: What a document with nothing left owed on it is stamped. The same word on both books -
#: `sales_orders` holds it on 11,006 rows and `purchase_orders` on 8,760 - and both screens
#: word it Completed.
_COMPLETE_STATUS = "closed"


def _complete_documents(diff: Diff) -> set[str]:
    """In-scope documents this upload leaves with no open line at all.

    Read from the DIFF, because that is where the whole answer already is: every open line of
    an in-scope document is in it (`_existing_lines` fetches them all, and a line the file has
    stopped stating closes by absence), so a document whose every change either closes a line
    or adds one that arrives already settled has nothing owed on it.

    Its header follows its lines. Left `open`, a fully delivered order reads as a live
    commitment on every screen and in the status filter, which is exactly the complaint that
    made the two books word `closed` as Completed in the first place.
    """
    with_open_lines = {
        c.doc_number for c in diff.changes
        if c.kind != CLOSED and not (c.after is not None and states_settled(c.after))
    }
    return {number for number in diff.scope_documents if number not in with_open_lines}


def _to_activate(diff: Diff, header_state: dict[str, tuple[str, Optional[str]]],
                 bind: _Binding, complete: set[str]) -> list[str]:
    """In-scope documents whose header is not live and therefore has to be lifted.

    An order-book extract is a book of documents already placed, and AutoCount is the
    system of record for that, so a document it names is by definition no longer a draft.
    Leaving it as one imports the lines and then hides them: `scm.on_order_v` ignores drafts
    (M4-D5), so on-order stays 0 while the response still reports `added: N` - a silent no-op
    reporting success. Reported rather than merely done, because changing a document's status
    crosses a decision boundary a person should see.

    A document the file states COMPLETE is never lifted: it is not a draft waiting to be
    placed, it is an order that finished. Without this exclusion re-uploading a completed
    sales book flips every one of its documents back to `open` on every run, because `closed`
    is not among the sales book's live statuses. The reverse case is the last clause: a
    document that is `closed` and has an open line again really has come back, and on the
    purchase book - where `closed` IS a live status - nothing else would notice.
    """
    return [
        number for number in diff.scope_documents
        if number not in complete
        and (state := header_state.get(number)) is not None
        and (state[0] not in bind.live_statuses or state[0] == _COMPLETE_STATUS)
    ]


def _samples(diff: Diff, limit: int = 8) -> dict:
    """A few real rows per change kind, so the confirm screen shows evidence not just counts."""
    out: dict = {}
    for c in diff.changes:
        if c.kind == "unchanged":
            continue
        bucket = out.setdefault(c.kind, [])
        if len(bucket) >= limit:
            continue
        bucket.append({
            "doc_number": c.doc_number,
            "item_code": c.item_code,
            "location": c.location,
            "qty_before": c.before.qty if c.before else None,
            "qty_after": c.after.qty if c.after else None,
            "date_before": c.before.required_date.isoformat()
                           if c.before and c.before.required_date else None,
            "date_after": c.after.required_date.isoformat()
                          if c.after and c.after.required_date else None,
            "days_moved": c.days_moved,
            "label": (c.after or c.before).label,
        })
    return out


@dataclass
class _Plan:
    """Everything both entry points need, built once so they cannot disagree."""

    read: ReadResult
    resolved: Optional[_Resolved] = None
    diff: Optional[Diff] = None
    issues: list[ResolutionIssue] = field(default_factory=list)
    # Documents that would be (preview) or were (apply) lifted to the live write status.
    activate: list[str] = field(default_factory=list)
    # Documents this upload leaves with nothing owed, whose header is therefore closed.
    complete: set[str] = field(default_factory=set)
    # Document number -> the demand class this upload could decide. A document absent from
    # this map is one nothing could classify, and it is reported rather than defaulted.
    demand: dict[str, str] = field(default_factory=dict)
    # Row-scoped complaints this module raises on top of the reader's own, so both entry
    # points hand the operator ONE list of rows to look at.
    problems: list[RowProblem] = field(default_factory=list)
    # Documents in this file whose demand class could not be decided (QP1). NOT a notice:
    # while this list is non-empty the upload is refused and nothing is written, so preview
    # and commit give the same verdict for the same reason.
    unclassified: list[str] = field(default_factory=list)
    # Agent codes in the file that can classify nothing yet. Master-data gaps, not row
    # failures, so they travel separately.
    agent_notices: list[AgentNotice] = field(default_factory=list)
    # DB line id -> the money columns that line currently holds, read alongside the lines
    # themselves. It is what lets the write path tell a line the file merely restates from
    # one whose PRICE moved, without a lookup per unchanged row.
    money_by_line: dict[str, dict] = field(default_factory=dict)


def _build(db: Session, file_data: bytes, doc_type: str,
           on_total_rows: Optional[Callable[[int], None]] = None) -> _Plan:
    bind = _binding(doc_type)
    resolver = AliasResolver.for_doc_type(db, doc_type)
    read = read_workbook(file_data, doc_type, resolver)
    if on_total_rows is not None:
        # Published the moment the sheet is read, before the diff. Without it `total_rows`
        # first appears when the job completes and the upload drawer shows 0/0 for the whole
        # run, which reads as stuck (the same lesson as the customer importer).
        on_total_rows(read.total_rows)
    if not read.ok:
        return _Plan(read=read)
    resolved = _resolve(db, read, bind)
    fulfilled: dict[str, float] = {}
    money: dict[str, dict] = {}
    existing = _existing_lines(db, {l.doc_number for l in resolved.lines}, bind,
                               fulfilled_into=fulfilled, money_into=money)
    diff = diff_lines(existing, resolved.lines)
    header_state = _header_state(db, diff.scope_documents, bind)
    agent_classes, agent_notices = _agent_state(db, resolved, bind)
    demand, demand_problems = _classify_demand(
        db, diff, resolved, _demand_state(db, diff.scope_documents, bind), bind,
        agent_classes)
    complete = _complete_documents(diff)
    return _Plan(
        read=read,
        resolved=resolved,
        diff=diff,
        issues=resolved.issues + _honesty_issues(diff, fulfilled, header_state,
                                                 resolved, bind),
        activate=_to_activate(diff, header_state, bind, complete),
        complete=complete,
        demand=demand,
        problems=demand_problems,
        unclassified=[str(p.value) for p in demand_problems if p.value],
        agent_notices=agent_notices,
        money_by_line=money,
    )


def _affected_product_ids(plan: _Plan) -> list[str]:
    """Products whose position this restatement could move.

    Only the ones that actually CHANGED: an unchanged line cannot make the plan disagree
    with anything, and snapshotting the whole book on every upload would cost the dated
    engine a run over thousands of products to find the handful that moved.
    """
    if plan.diff is None or plan.resolved is None:
        return []
    changed_codes = {
        c.item_code for c in plan.diff.changes if c.kind != UNCHANGED
    }
    # `product_by_code` is keyed by the NORMALISED code, so normalise on lookup: the diff
    # carries the code as the file spelled it, and a case difference would silently drop the
    # product from the batch rather than fail.
    by_code = plan.resolved.product_by_code
    return sorted(
        {str(by_code[_norm(code)]) for code in changed_codes if by_code.get(_norm(code))}
    )


def _unreadable_date_warning(problems: list[RowProblem]) -> list[str]:
    """AC-safety: a file whose dates the reader could not parse must say so ONCE, up front,
    in plain language - not leave the operator to count "could not read the date" rows in
    the per-row list and infer the consequence themselves. Additive: `row_problems` keeps
    every one of those rows, this just states what happens to them."""
    n = sum(1 for p in problems if "could not read the date" in p.reason)
    if not n:
        return []
    row = "row" if n == 1 else "rows"
    return [
        f"{n} {row} carry a date the reader could not read; those dates will be left as "
        "they are"
    ]


def _shipping_order_warning(read: ReadResult, spo: "_SpoWrite") -> list[str]:
    """The purchase book states two families and they land in two different tables. Said
    once, up front: the operator reads the Test result before pressing Confirm upload, and
    "N of your rows went somewhere else" is a fact they need before it happens, not after.

    It used to say "left out", which was true and is not any more (R4). And it used to stop
    at the row count, which is how the Test result came to print "721 rows are shipping
    orders" directly above "nothing would change": the sentence stated how many rows went
    the other way and never what they would do there, so an operator had no figure to weigh
    against the purchase-order half's own verdict. It now carries the four numbers, and it
    makes no claim about whether anything changes - that is the caller's to say once it has
    read both halves."""
    n = len(read.shipping_order_row_numbers)
    if not n:
        return []
    row = "row" if n == 1 else "rows"
    line = "line" if spo.new == 1 else "lines"
    return [
        f"{n} {row} are shipping orders (SPO): {spo.new} new {line}, "
        f"{spo.changed} changed, {spo.unchanged} unchanged, {spo.closed} would close"
    ]


#: The stamp the OUTSTANDING channel puts on the `spo_allocations` rows it writes. The same
#: value the purchase-order headers carry, so the two halves of one upload are one feed, and
#: the only rows this channel is allowed to close (`po_history_service` writes
#: `scm_spo_history`, the external API writes its own, and neither is ours to settle).
SPO_UPLOAD_SOURCE = "scm_upload"


@dataclass
class _SpoWrite:
    """What the shipping-order half of one upload did, for the summary and the preview."""

    documents: int = 0
    lines: int = 0
    closed: int = 0
    unknown_locations: int = 0
    #: `lines` split by what each one would do to the row it maps to. They sum to `lines`.
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    product_ids: list[str] = field(default_factory=list)


def _spo_plan(db: Session, read: ReadResult) -> tuple[dict, dict, dict]:
    """The masters the shipping-order rows resolve against: products and warehouses by
    code, and the lines grouped per document in file order.

    ORM queries, not raw SQL: `products` and `warehouses` are company-scoped and the
    isolation filter runs on ORM execution only, so a raw lookup would resolve a code
    against every company's rows at once.
    """
    by_document: dict[str, list] = {}
    for line in read.spo_lines:
        by_document.setdefault(line.doc_number, []).append(line)
    if not by_document:
        return {}, {}, {}

    # Chunked through `_in_chunks` (review round 2, S2): a shipping-order book names its
    # own document family, so its product/warehouse code sets grow with the same file size
    # the rest of `apply` already chunks against.
    codes = {_norm(l.item_code) for lines in by_document.values() for l in lines}
    products = {
        _norm(code): str(pid)
        for pid, code in _in_chunks(
            db.query(Product.id, Product.product_code), func.upper(Product.product_code),
            codes,
        )
    }
    locations = {
        _norm(l.location)
        for lines in by_document.values()
        for l in lines
        if l.location
    }
    warehouses: dict[str, str] = {}
    if locations:
        rows = _in_chunks(
            db.query(Warehouse.id, Warehouse.warehouse_code)
            .filter(Warehouse.is_active.is_(True)),
            func.upper(Warehouse.warehouse_code), locations,
        )
        for wid, code in rows:
            # FIRST wins on a case collision, for the reason `po_history_service` states:
            # last would make the answer depend on the order Postgres returned rows in.
            warehouses.setdefault(_norm(code), str(wid))
    return by_document, products, warehouses


@dataclass
class _SpoLinePlan:
    """One shipping-order line of the book, resolved against what the table already holds.

    The IDENTITY question answered once, for the preview and the write together: which row
    (if any) this line IS, and which line number it will carry.
    """

    line: Any
    source_row: Optional[int]
    identity: dict
    product_id: Optional[str]
    code: Optional[str]
    warehouse_id: Optional[str]
    #: The row this line restates. `None` when the book states it for the first time.
    row: Any = None
    #: The number this line will carry. `None` when there is nothing to write - an unknown
    #: item, or a line another feed already owns.
    line_number: Optional[int] = None
    #: A row of the same identity belongs to another feed (the history book, the external
    #: API). Reported in the job detail and left alone.
    owned_elsewhere: bool = False


def _spo_line_plans(db: Session, read: ReadResult) -> tuple[dict, dict]:
    """`{document: [line plans]}` plus the product masters, for both halves of the upload.

    IDENTITY, not file position (review round 28 Aug). The line used to be keyed by where
    it sat in the file, so a re-export that no longer stated a fully received line moved
    every line below it up one - each of those rows keeping its id while gaining a
    different product, which is what anything pointing at the row (an order inquiry link,
    its audit claim, a container tick) was left describing. A line is
    `(shipping order, item, location, which occurrence of the three)` and the number it was
    first given is the number it keeps.

    The occurrence matters: one shipping order routinely states the same item at the same
    place twice, and each of those is a real second container. So the rows of a
    (product, location) are handed out IN ORDER, one per stated line.

    A row another feed owns blocks the line at the same identity rather than being restated
    (`source_system`): a history row reopened here would make a delivery from 2020 read as
    stock on its way in, for ever. New lines are numbered after the highest the document
    already holds, so a fresh line can never land on somebody else's row.
    """
    from app.models.procurement import SPOAllocation

    by_document, products, warehouses = _spo_plan(db, read)
    plans: dict[str, list[_SpoLinePlan]] = {}
    for number in sorted(by_document):
        held = (
            db.query(SPOAllocation)
            .filter(SPOAllocation.spo_number == number)
            .all()
        )
        mine: dict[tuple, list] = {}
        others: dict[tuple, list] = {}
        for row in sorted(held, key=lambda r: (r.spo_line_number or 0, str(r.id))):
            key = (str(row.product_id), _norm(row.location_code or ""))
            bucket = mine if (row.source_system or "") == SPO_UPLOAD_SOURCE else others
            bucket.setdefault(key, []).append(row)
        highest = max([row.spo_line_number or 0 for row in held], default=0)

        document_plans: list[_SpoLinePlan] = []
        for line in by_document[number]:
            product_id = products.get(_norm(line.item_code))
            code = (line.location or "").strip().upper() or None
            plan = _SpoLinePlan(
                line=line,
                source_row=int(line.row_ref) if (line.row_ref or "").isdigit() else None,
                identity=_identity(number, line.item_code, line.location or ""),
                product_id=product_id,
                code=code,
                warehouse_id=warehouses.get(code) if code else None,
            )
            if product_id is not None:
                key = (str(product_id), code or "")
                queue = mine.get(key) or []
                if queue:
                    plan.row = queue.pop(0)
                    plan.line_number = plan.row.spo_line_number
                elif others.get(key):
                    others[key].pop(0)
                    plan.owned_elsewhere = True
                else:
                    highest += 1
                    plan.line_number = highest
            document_plans.append(plan)
        plans[number] = document_plans
    return plans, products


def _spo_stated_keys(plans: dict) -> set[tuple[str, int]]:
    """Which `(document, line number)` the book still states - the closure's own question.

    A line whose item the master does not hold names no row, and a line another feed owns
    is not this channel's to protect: neither can leave a key behind, and both halves of
    the upload read it the same way, which is the whole point (S3). The preview and the
    write used to count `stated` on either side of the product lookup, so a book carrying
    one unknown SKU had the operator confirming one number while a different set of rows
    was settled.
    """
    return {
        (number, plan.line_number)
        for number, document_plans in plans.items()
        for plan in document_plans
        if plan.line_number is not None
    }


def _spo_line_verdict(plan: _SpoLinePlan, read: ReadResult) -> Optional[str]:
    """What one planned line would DO to the table: new, changed, unchanged - or nothing.

    `None` is a line this channel writes no row for at all: an item the master does not
    hold, or a row another feed owns. It is counted in neither `lines` nor any of the three,
    because the file's own row count already says how many rows went this way and the point
    of these figures is what lands.

    "Changed" is decided on the facts the plan and the receipt read off the row - the two
    quantities, where the goods are going, when they are expected, and whether the line is
    open - which is exactly the set the write path restates unconditionally. Cost and
    currency are deliberately not among them: they are written when the file states them,
    they move no plan, and comparing a file's float against the column's decimal would
    report a book as "changed" on every upload.
    """
    if plan.line_number is None:
        return None
    row = plan.row
    if row is None:
        return "new"
    extra = read.extras.get(str(plan.line.row_ref), {})
    ordered, received = _spo_quantities(plan.line, extra)
    status = "open" if ordered - received > 0 else "closed"
    held_warehouse = str(row.warehouse_id) if row.warehouse_id else None
    same = (
        int(row.allocated_quantity or 0) == ordered
        and int(row.quantity_received or 0) == received
        and held_warehouse == plan.warehouse_id
        and (row.location_code or None) == plan.code
        and (row.line_status or "") == status
        # An unreadable date leaves the held one alone, so it cannot make a line changed.
        and (plan.line.required_date is None
             or row.expected_date == plan.line.required_date)
    )
    return "unchanged" if same else "changed"


def _spo_counts(plans: dict, out: _SpoWrite, read: ReadResult) -> None:
    """The figures the summary and the Test result print, counted ONCE for both paths."""
    for _number, document_plans in plans.items():
        if not document_plans:
            continue
        out.documents += 1
        for plan in document_plans:
            if plan.code and plan.warehouse_id is None:
                out.unknown_locations += 1
            verdict = _spo_line_verdict(plan, read)
            if verdict is None:
                continue
            out.lines += 1
            if verdict == "new":
                out.new += 1
            elif verdict == "changed":
                out.changed += 1
            else:
                out.unchanged += 1


def _spo_quantities(line, extra: dict) -> tuple[int, int]:
    """`(ordered, received)` as WHOLE numbers, off the file's own figures.

    `allocated_quantity` and `quantity_received` are INTEGER columns, and the book states
    floats, so `qty + received` handed Postgres 60.400000000000006 to round on its own (S8)
    while the line's status was decided on the unrounded remainder - a row reading 10
    ordered, 10 received and still `open`, which `scm.on_order_v` counts as supply that is
    never coming. The stated order quantity wins where the file has one, exactly as
    `_settled_quantities` reads a settled line; the outstanding quantity plus what has been
    received is the same answer for a file without the column.
    """
    received_raw = float(extra.get("qty_fulfilled") or 0)
    stated = extra.get("qty_ordered")
    ordered_raw = (
        float(stated) if stated is not None else float(line.qty or 0) + received_raw
    )
    return int(round(ordered_raw)), int(round(received_raw))


def _write_spo_lines(db: Session, read: ReadResult, outcome: ImportOutcome) -> _SpoWrite:
    """File the book's shipping-order rows as OPEN `spo_allocations` lines (R4).

    Upserted on the LINE's own identity - `(shipping order, item, location, occurrence)`
    resolved to a stable `spo_line_number` by `_spo_line_plans` - not on where the row sat
    in the file. The number is the same key `po_history_service._write_shipping_order`
    writes on, so the two feeds meet on one row rather than on two that merely look alike.

    OPEN and not received, which is the whole point: `scm.on_order_v` counts a shipping
    order only while it is open with quantity still to come, and until R4 nothing in the
    system could create such a row - the history book writes them closed by construction.

    Only rows THIS channel wrote are ever restated or closed (`source_system`): a history
    row reopened here would make a delivery from 2020 read as stock on its way in, for ever,
    and an external-API row is somebody else's record of the same document.
    """
    from app.models.procurement import SPOAllocation

    out = _SpoWrite()
    plans, _products = _spo_line_plans(db, read)
    _spo_counts(plans, out, read)
    touched: set[str] = set()
    for number in sorted(plans):
        for plan in plans[number]:
            line = plan.line
            if plan.product_id is None:
                outcome.skip(row=plan.source_row, code=oc.PRODUCT_NOT_FOUND,
                             identity=plan.identity, value=line.item_code)
                continue
            if plan.owned_elsewhere:
                outcome.skip(row=plan.source_row, code=oc.DOCUMENT_OWNED_ELSEWHERE,
                             identity=plan.identity, value=number)
                continue
            extra = read.extras.get(str(line.row_ref), {})
            ordered, received = _spo_quantities(line, extra)
            # The STATUS follows the two integers that were actually written, never the
            # file's unrounded remainder: a line stating 10 ordered and 10 received is a
            # line that has landed, whatever 10.4 - 10.0 says.
            outstanding = ordered - received
            row = plan.row
            if row is None:
                row = SPOAllocation(
                    spo_number=number,
                    spo_line_number=plan.line_number,
                    product_id=plan.product_id,
                    warehouse_id=plan.warehouse_id,
                    location_code=plan.code,
                    allocated_quantity=ordered,
                    quantity_received=received,
                    receipt_status="pending" if outstanding > 0 else "fully_received",
                    line_status="open" if outstanding > 0 else "closed",
                    source_system=SPO_UPLOAD_SOURCE,
                    issue_date=extra.get("order_date"),
                    expected_date=line.required_date,
                    unit_cost=extra.get("unit_cost"),
                    currency=extra.get("currency"),
                )
                db.add(row)
                db.flush()
                outcome.success(row=plan.source_row, code=oc.CREATED,
                                identity=plan.identity, value=number,
                                entity_type="spo_allocation", entity_id=row.id)
            else:
                row.product_id = plan.product_id
                row.allocated_quantity = ordered
                row.quantity_received = received
                row.receipt_status = "pending" if outstanding > 0 else "fully_received"
                row.line_status = "open" if outstanding > 0 else "closed"
                row.location_code = plan.code
                row.warehouse_id = plan.warehouse_id
                row.issue_date = extra.get("order_date") or row.issue_date
                # An unreadable date must never blank one the database already holds - the
                # same rule the purchase-order half applies to its own ETA column.
                if line.required_date is not None:
                    row.expected_date = line.required_date
                if extra.get("unit_cost") is not None:
                    row.unit_cost = extra["unit_cost"]
                if extra.get("currency"):
                    row.currency = extra["currency"]
                outcome.updated(row=plan.source_row, identity=plan.identity, value=number,
                                entity_type="spo_allocation", entity_id=row.id)
            touched.add(str(plan.product_id))

    # CLOSED BY ABSENCE, the same rule the purchase-order half applies to its own lines.
    # The outstanding book is the statement of what is still open, and every open row this
    # channel holds was written by an earlier upload of that same book - so a line it has
    # stopped stating has been received. A status change and never a delete: the line was
    # planned against, and erasing it would make last week's plan unexplainable.
    #
    # ONLY when this book carries shipping orders at all. A file with none is not evidence
    # about the shipping-order family - it is a purchase-order-only export, and there are
    # plenty of those - so reading its silence as "every shipping order has landed" would
    # settle the whole open SPO book (715 lines on the dev copy) on an upload that never
    # mentioned one. A book that states SOME shipping orders IS the SPO book, and what it
    # leaves out has been received. The PREVIEW states the count either way, so the number
    # is on screen before Confirm upload rather than discovered after it.
    going = _spo_lines_to_close(db, _spo_stated_keys(plans)) if plans else []
    for row in going:
        row.line_status = "closed"
    out.closed = len(going)
    db.flush()
    out.product_ids = sorted(touched)
    return out


def _spo_lines_to_close(db: Session, stated_keys: set[tuple[str, int]]) -> list:
    """The open shipping-order lines THIS channel holds that a book no longer states.

    One reader for the preview and the write, so the number the operator confirms and the
    rows the commit settles cannot differ.

    D11 (S3): also considers rows `ShippingOrderIngestService` wrote
    (`source_system == "autocount"`) - one shipping-order writer identity
    across the outstanding book and the ESB, so a document the file no
    longer states closes regardless of which channel last touched it.
    `scm_spo_history`/`scm_po_history` (closed history, a different feed
    entirely) are still excluded - neither is this channel's to settle.
    """
    from app.models.procurement import SPOAllocation
    from app.services.document_ingest_service import SOURCE_SYSTEM as ESB_SOURCE_SYSTEM

    return [
        row
        for row in db.query(SPOAllocation).filter(
            SPOAllocation.source_system.in_([SPO_UPLOAD_SOURCE, ESB_SOURCE_SYSTEM]),
            SPOAllocation.line_status == "open",
        )
        if (row.spo_number, row.spo_line_number) not in stated_keys
    ]


def _spo_preview(db: Session, read: ReadResult) -> _SpoWrite:
    """What the shipping-order half WOULD do. Writes nothing.

    The SAME resolution the write runs (`_spo_line_plans`), so the identities, the counts
    and the closure are one answer read twice rather than two implementations that agree
    until a book carries something odd (S3).
    """
    out = _SpoWrite()
    plans, _products = _spo_line_plans(db, read)
    _spo_counts(plans, out, read)
    out.closed = len(_spo_lines_to_close(db, _spo_stated_keys(plans))) if plans else 0
    return out


def _would_create_parties(db: Session, resolved: _Resolved, bind: _Binding) -> tuple[list[str], list[str]]:
    """AC-P2-1 (guide-writer check, 2026-09-06): which supplier/customer codes
    `apply`'s back-create block (below) WOULD create, without creating any of
    them - the same existence checks that block runs (code match, then the
    PO-book cleaned-name fallback), read-only. Returns
    `(supplier_would_create_codes, customer_would_create_codes)`; whichever
    one `bind.party` is not stays empty (an SO upload's party is `Customer`,
    a PO/GRN upload's is `Supplier` - never both in the same call).
    """
    supplier_codes: list[str] = []
    customer_codes: list[str] = []
    if not bind.party_back_create:
        return supplier_codes, customer_codes
    target = customer_codes if bind.party is Customer else supplier_codes
    if resolved.party_raw_code_by_code:
        code_col = getattr(bind.party, bind.party_code_col)
        for code in sorted(resolved.party_raw_code_by_code):
            if bind.party is Customer:
                # Not `.one_or_none()`: a customer code can still hold several rows until
                # the DEBTOR-CODE-UNIQUE merge has run, and that raised MultipleResultsFound.
                existing = customer_identity.customer_by_code(db, code)
            else:
                existing = db.query(bind.party).filter(func.upper(code_col) == code).one_or_none()
            if existing is not None:
                continue
            raw_code = resolved.party_raw_code_by_code.get(code, code)
            if bind.party is Customer and not resolved.party_label_by_code.get(code):
                # D8: a customer needs code AND name - a code with no name is
                # left unresolved, never created, on this path either.
                continue
            target.append(raw_code)
    # PO-book NAME fallback: supplier only (D8 requires code AND name for a
    # customer, so there is no name-alone creation path to preview for one).
    if bind.party is not Customer and resolved.party_cleaned_name_by_key:
        name_col = getattr(bind.party, bind.party_name_col)
        for name_key in sorted(resolved.party_cleaned_name_by_key):
            existing = (
                db.query(bind.party)
                .filter(func.upper(name_col) == name_key)
                .order_by(bind.party.id)
                .first()
            )
            if existing is not None:
                continue
            target.append(resolved.party_cleaned_name_by_key[name_key])
    return supplier_codes, customer_codes


def preview(db: Session, file_data: bytes, doc_type: str = SO) -> PreviewResult:
    """What this upload would change. Writes nothing."""
    plan = _build(db, file_data, doc_type)
    read, diff = plan.read, plan.diff
    if diff is None:
        return PreviewResult(
            doc_type=doc_type, scope_documents=(), counts={}, total_rows=read.total_rows,
            unmapped_headers=read.unmapped_headers, missing_columns=read.missing_columns,
            row_problems=read.problems,
            warnings=_unreadable_date_warning(read.problems),
        )
    row_problems = read.problems + plan.problems
    spo = _spo_preview(db, read) if doc_type == PO else _SpoWrite()
    bind = _binding(doc_type)
    supplier_would_create, customer_would_create = (
        _would_create_parties(db, plan.resolved, bind) if plan.resolved is not None else ([], [])
    )
    return PreviewResult(
        doc_type=doc_type,
        scope_documents=diff.scope_documents,
        counts=diff.counts,
        total_rows=read.total_rows,
        unmapped_headers=read.unmapped_headers,
        missing_columns=read.missing_columns,
        row_problems=row_problems,
        resolution_issues=plan.issues,
        unclassified_documents=plan.unclassified,
        samples=_samples(diff),
        activated_documents=plan.activate,
        unmapped_agents=plan.agent_notices,
        suppliers_created=supplier_would_create,
        customers_created=customer_would_create,
        warnings=_unreadable_date_warning(row_problems)
        + _shipping_order_warning(read, spo),
        shipping_order_rows=len(read.shipping_order_row_numbers),
        spo_documents=spo.documents,
        spo_lines=spo.lines,
        spo_unknown_locations=spo.unknown_locations,
        spo_closed=spo.closed,
        spo_new=spo.new,
        spo_changed=spo.changed,
        spo_unchanged=spo.unchanged,
    )


def _preload_closed_lines(db: Session, bind: _Binding,
                          header_ids: set[str]) -> dict[tuple[str, str], list]:
    """Every closed, revivable line on these documents, grouped by (header, product).

    Loaded ONCE per batch (`apply`'s `_DOCUMENT_BATCH` loop) rather than once per ADDED row.
    The prod trace: the old per-row lookup (`_closed_line`, retired here) excluded every line
    this run had already claimed with a `NOT IN (<ids>)` clause that GREW with every settled
    row - on the completed 2020-Sep 2026 book that list reached 13,519 ids, so each later row
    shipped a statement with that many bound parameters. Rows times settled-ids is quadratic;
    Postgres dropped the connection. Loading the candidates once and excluding in PYTHON
    (`_match_closed_line`'s `exclude_ids`) removes the exclusion from the wire entirely.

    **A HISTORY line is never a candidate.** `po_history_service` writes into these same
    tables, closed and fully received, precisely so `scm.on_order_v` and `scm.po_ordered_v`
    cannot count it. Reviving one sets `line_status='open'` and adds the incoming quantity on
    top of what was already received, which satisfies both views: a 2023 purchase that was
    delivered years ago would read as stock on its way in, permanently, and re-uploading
    would never correct it. The two feeds share the table and are told apart by the stamp
    (`history_sources.HISTORY_SOURCE_SYSTEMS`), filtered out here at the SQL level so a
    history line can never reach the match in `_match_closed_line` at all.

    Ordered by `created_at` per group, exactly as the retired per-row query was, so the
    earliest closed line wins a match the same way it always did.
    """
    if not header_ids:
        return {}
    line = bind.line
    out: dict[tuple[str, str], list] = defaultdict(list)
    for chunk in _chunked(sorted(header_ids)):
        rows = (
            db.query(line)
            .filter(getattr(line, bind.header_fk).in_(chunk),
                    line.line_status == "closed",
                    sa.or_(line.source_system.is_(None),
                           line.source_system.notin_(list(HISTORY_SOURCE_SYSTEMS))))
            # `line.id` breaks a tie on `created_at` deterministically (two lines can share
            # a timestamp when both were written in the same bulk statement) - without it
            # the "earliest wins" rule below depends on whatever order Postgres happens to
            # return same-timestamp rows in.
            .order_by(line.created_at, line.id)
            .all()
        )
        for l in rows:
            out[(str(getattr(l, bind.header_fk)), str(l.product_id))].append(l)
    return out


def _match_closed_line(candidates: dict[tuple[str, str], list], bind: _Binding,
                       header_id: str, product_id: Optional[str],
                       warehouse_id: Optional[str], when: Optional[date],
                       exclude_ids: set[str]):
    """A closed line matching the incoming row exactly, over `_preload_closed_lines`' set.

    Same match the retired `_closed_line` query made - (header, product) exact, warehouse and
    date exact-or-both-null - just against candidates already in memory instead of a query
    per row. An explicit lookup rather than widening `_existing_lines`: the diff must stay
    open-only, or a closed line simply absent from the next upload would be re-closed on
    every re-run and `applied.closed` would never settle at 0.

    `l.line_status == "closed"` is checked on the LIVE object (review round 1b, C5), not only
    at preload time: `candidates` holds the actual ORM instances, so a line an EARLIER match
    in this same batch already reopened (`revived.line_status = "open"`) is excluded here by
    the same attribute that made it a candidate in the first place - self-enforcing, with no
    second bookkeeping set needed for that case. `exclude_ids` is narrower than before for
    exactly that reason: it now only has to carry a line that stays CLOSED after being
    claimed (the settled branch, where `_write_settled` leaves `line_status` alone), because
    the status check cannot see that one for itself. A settled row leaves its line closed, so
    it stays matchable, and two byte-identical rows of a completed book ("totally acceptable
    in 1 SO") would otherwise both land on the first line and the second quantity would
    disappear.
    """
    if product_id is None:
        return None
    for l in candidates.get((str(header_id), str(product_id)), ()):
        if l.line_status != "closed" or str(l.id) in exclude_ids:
            continue
        line_warehouse = str(l.warehouse_id) if l.warehouse_id else None
        want_warehouse = str(warehouse_id) if warehouse_id else None
        if line_warehouse != want_warehouse:
            continue
        if getattr(l, bind.date) != when:
            continue
        return l
    return None


def _preload_lines_by_id(db: Session, bind: _Binding, ids: set[str]) -> dict[str, Any]:
    """Every line these ids name, loaded once instead of once per changed row (S5).

    `diff.changes` carries the DB line id as `row_ref` on its `before` half for CLOSED,
    QTY_CHANGED, DATE_AND_QTY_CHANGED and a repriced UNCHANGED (`_existing_lines` set it that
    way) - the write loop used to re-fetch each one by id, a query per changed row, which is
    exactly the shape the prod trace measured as quadratic once `_closed_line`'s exclusion
    is added on top. Chunked (`app.utils.chunking.chunked`) so a 22,111-row file's id list
    still ships as a handful of statements, none of them over `chunked`'s own 500-per-chunk
    default.

    `ids` is the caller's to narrow (S5 review round 1): the majority of `UNCHANGED` rows
    never touch this dict at all (only a repriced one does), so `apply` only asks for the ids
    it will actually look up rather than every `before.row_ref` in the batch.
    """
    if not ids:
        return {}
    out: dict[str, Any] = {}
    for chunk in _chunked(sorted(str(i) for i in ids)):
        for l in db.query(bind.line).filter(bind.line.id.in_(chunk)).all():
            out[str(l.id)] = l
    return out


#: Both money columns on both books are stored at 2 decimal places, so that is the precision
#: a comparison between the file and the database can honestly be made at. A sheet stating
#: 0.945 comes back out of the column as 0.95, and comparing the raw figures would call that
#: a price change on every single re-upload, for ever.
_MONEY_PLACES = Decimal("0.01")


def _money_2dp(value) -> Optional[Decimal]:
    """`value` as the column would store it, or None when it is not a number at all."""
    if value is None or value == "":
        return None
    try:
        # ROUND_HALF_UP, which is how Postgres rounds into NUMERIC(_, 2). A different
        # rounding here would disagree with the value it just wrote.
        return Decimal(str(value)).quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _money_differs(held: dict, extra: dict, bind: _Binding) -> bool:
    """Whether this extract states money that is not what the line already holds.

    Only what the file STATES counts: an absent value is not a disagreement, for the same
    reason `_refresh_money` never blanks a figure the file omits.
    """
    for col, key in bind.money_cols:
        stated = extra.get(key)
        if stated is None:
            continue
        stored = held.get(col)
        a, b = _money_2dp(stated), _money_2dp(stored)
        if a is not None and b is not None:
            if a != b:
                return True
            continue
        # A currency code, or a price arriving where the column was empty.
        if str(stated).strip() != str(stored if stored is not None else "").strip():
            return True
    return False


#: The currency a PURCHASE document's HEADER is in when the export states none for the
#: document at all (captain, 28 Aug 2026: "our upload should take CNY also for now").
#: This is a HEADER-level fill only - PLAN-po-line-currency-follows-header-22sep.md
#: (owner ruling 22 Sep 2026, "we shouldn't assume CNY") retired the same literal on the
#: LINE path: an unstated line now takes its own header's currency (see
#: `_refresh_money` and the new-line insert below), which is this default only when the
#: header itself had nothing to state either. A FILL, never an overwrite: a stated
#: currency always wins, and a document that already holds one keeps it
#: (`test_a_file_without_a_po_date_column_does_not_blank_the_issue_date`). The sales book
#: is untouched - a customer invoice's currency is not this rule's to guess.
DEFAULT_PO_CURRENCY = "CNY"


def _refresh_money(line, extra: dict, bind: _Binding, *, header_currency: Optional[str]) -> None:
    """Bring the line's money columns up to what this extract says.

    Applied on update, not on insert only: cost is what the cash co-pilot ranks and budgets
    on, so a line frozen at the first extract's price is ranked on last week's money, and a
    line that arrived with the column blank and was priced later would stay blank for ever -
    ranked as if it were free. A value the file does NOT state leaves the column alone rather
    than blanking a cost we already know.

    The one exception is a purchase line's CURRENCY, which is filled with its own
    HEADER's currency (`header_currency`, the `PurchaseOrder` row this line belongs to,
    already header-filled by the caller) when neither the file nor the row states one.
    """
    for col, key in bind.money_cols:
        value = extra.get(key)
        if value is not None:
            setattr(line, col, value)
    if bind.header is PurchaseOrder and not getattr(line, "currency", None):
        line.currency = header_currency


def _settled_quantities(after: Line, extra: dict) -> tuple[float, float]:
    """(ordered, already delivered/received) for a line the file states as SETTLED.

    Taken from the file rather than computed from what we hold, because on a settled line
    the file is the only thing that knows either figure: the row states what was ordered and
    what went out, and the diff's own `qty` is the nothing that is left. A row that somehow
    reaches here without an ordered figure falls back to the outstanding quantity, which is
    the same answer the open path would have written.
    """
    stated_ordered = extra.get("qty_ordered")
    ordered = float(stated_ordered) if stated_ordered is not None else float(after.qty)
    stated_fulfilled = extra.get("qty_fulfilled")
    # No progress column at all: everything ordered has gone, which is what "nothing
    # remaining" means. A 0 here would close a line claiming it delivered none of it.
    fulfilled = (float(stated_fulfilled) if stated_fulfilled is not None
                 else ordered - float(after.qty))
    return ordered, fulfilled


def _settled_qty_differs(line, ordered: float, fulfilled: float, bind: _Binding) -> bool:
    """Whether a closed line already holds the quantities this row states."""
    return (abs(float(line.qty_ordered or 0) - ordered) > _QTY_EPSILON
            or abs(float(getattr(line, bind.fulfilled) or 0) - fulfilled) > _QTY_EPSILON)


def _write_settled(line, after: Line, extra: dict, bind: _Binding,
                   fallback_date: Optional[date], *, header_currency: Optional[str]) -> None:
    """Bring a line the file states as settled up to what the row says, and close it.

    The quantities, the date and the money together: a completed line is a record of what
    happened, and half a record is what makes an imported year unreadable a month later.
    """
    ordered, fulfilled = _settled_quantities(after, extra)
    line.qty_ordered = ordered
    setattr(line, bind.fulfilled, fulfilled)
    setattr(line, bind.date, _write_date(after, fallback_date))
    _refresh_money(line, extra, bind, header_currency=header_currency)
    line.line_status = "closed"


def _write_date(change_after: Optional[Line], fallback: Optional[date]) -> Optional[date]:
    """The date to WRITE for a changed line: the incoming date, unless the reader could not
    read it - in which case the stored date must not be overwritten with `None`.

    `change_after.required_date` is already `None` for both a genuinely blank cell and an
    unreadable one; `date_unreadable` is what tells them apart. `fallback` is whatever the
    line already holds (the diff's own `before.required_date` for an update, or `None` for
    a brand-new line, which has nothing to fall back to).
    """
    if change_after is not None and change_after.date_unreadable:
        return fallback
    return change_after.required_date if change_after is not None else fallback


def _identity(doc_number: str, item_code: str, location: str) -> dict:
    """What names a row in the job detail. No ids - the operator reads document numbers."""
    return {"doc_no": doc_number, "item_code": item_code, "location": location or ""}


def _record_rows_never_written(read: ReadResult, resolved: _Resolved,
                               outcome: ImportOutcome) -> None:
    """Record every source row that did NOT become a line, with the reason it did not.

    Worked out STRUCTURALLY - which row numbers came out of the reader, and which of those
    survived resolution - rather than by reading the wording of a problem. A row can carry a
    problem and still be written (an unreadable date leaves the line dated null; a document
    naming two agents keeps the first), so classifying by message would report rows as
    skipped that are sitting in the database.
    """
    read_rows = {str(l.row_ref) for l in read.lines}
    kept_rows = {str(l.row_ref) for l in resolved.lines}

    for row_number in read.layout_row_numbers:
        outcome.skip(row=row_number, code=oc.NOT_A_LINE)
    for row_number in read.settled_row_numbers:
        outcome.skip(row=row_number, code=oc.NOTHING_OUTSTANDING)
    # The shipping-order rows are NOT recorded here any more (R4): they are written into
    # `spo_allocations` by `_write_spo_lines`, which records an outcome per row itself. A
    # skip recorded here as well would account for the same row twice.

    for problem in read.problems:
        if str(problem.row_number) in read_rows:
            continue  # reported, but the row IS a line - it is counted where it was written
        outcome.skip(
            row=problem.row_number or None,
            code=oc.MISSING_REQUIRED_FIELD if problem.reason.startswith("missing")
            else oc.ROW_ERROR,
            message=problem.reason,
            value=problem.value or None,
        )

    # The only two fields that DROP a row in `_resolve`. Every other issue it raises (an
    # unknown creditor, two agents on one document) is reported while the line is written, so
    # recording those here would count the row twice and claim a skip that never happened.
    codes = {"item_code": oc.PRODUCT_NOT_FOUND, "stock_location": oc.WAREHOUSE_NOT_FOUND}
    for issue in resolved.issues:
        code = codes.get(issue.field)
        if code is None or str(issue.row_number) in kept_rows:
            continue
        outcome.skip(row=issue.row_number or None, code=code, message=issue.reason,
                     value=issue.value or None)


def supersede_crm_raised_pos(
    db: Session,
    triples: Iterable[tuple[str, str, str]],
    *,
    outcome: Optional[ImportOutcome] = None,
) -> tuple[int, list[dict]]:
    """Close the CRM's own recommendation once AutoCount confirms the same order.

    The shared body behind BOTH callers that state a confirmed physical purchase:
    the outstanding-PO upload (`_supersede_crm_raised_pos` below, a thin adapter that
    builds `triples` from its own resolved `Line`s) and the AutoCount ingest route's
    PO hook (D7, S5), which has no `_Resolved`/`Line` of its own - only the
    `(product_id, supplier_id, po_number)` a just-written line already carries.

    The captain's ruling of 21 Aug: the CRM can raise its own purchase order from a
    reorder-plan confirm (`source_system == "scm_recommendation"`, active after
    `bulk_confirm`). When the SAME physical order is later keyed into AutoCount -
    whichever channel states it - the two must not both count as on-order for the
    same (product, supplier); AutoCount is the book of record (the same doctrine the
    `adopted` handover above and `_preload_closed_lines`' history guard already follow), so
    its statement is what retires the CRM's own draft-turned-order, never the reverse.

    Matched on (product, supplier), never on document number: the CRM's own number
    and AutoCount's number for the same physical order are never the same string, so
    a document-number match would never fire. Only an ACTIVE, OPEN CRM line
    qualifies - a `draft_recommendation` PO is not on-order at all yet (M4-D5) and has
    nothing to supersede, and an already-closed line is left alone, which is what
    makes re-running the same upload (or re-pushing the same document) a no-op rather
    than a second attempt at something already done.

    Any order-inquiry row PLACED on a line this closes is untagged FIRST, through
    `ProjectOrderInquiryService.unplace_rows` rather than reimplemented here, so it
    reads `raised` again before this SAME import's own auto-place pass to pick them
    up again.

    `outcome` is optional (`None` for the ingest route, which keeps no `ImportOutcome`
    of its own): when given, each close is recorded through the SAME `LINE_CLOSED`
    code the diff's own closes use, with the `message` naming which AutoCount
    document is the reason, so the job detail is where an operator finds out a
    CRM-raised PO disappeared either way. The item code for that identity is read
    off the matched line's own product, not passed in - a triple names the product
    by id, and every caller already knows it means the same product it resolved.

    Never called from `preview()`: like every other write in this module, a
    supersession only happens on `apply` (or, for the ingest route, a non-dry run).
    """
    pairs: dict[tuple[str, str], str] = {}
    for product_id, supplier_id, po_number in triples:
        if product_id and supplier_id:
            pairs.setdefault((str(product_id), str(supplier_id)), po_number)
    if not pairs:
        return 0, []

    product_ids = {product_id for product_id, _supplier_id in pairs}
    # Chunked (review round 2, N1): a batch's own supersession candidates are bounded by
    # its documents, but the product set they name is not guaranteed small.
    candidates = _in_chunks(
        db.query(PurchaseOrderLine, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .filter(
            PurchaseOrder.source_system == "scm_recommendation",
            PurchaseOrder.status == "active",
            PurchaseOrderLine.line_status == "open",
        ),
        PurchaseOrderLine.product_id, product_ids,
    )
    to_close: list[tuple] = []
    for line, header in candidates:
        supplier_id = str(header.supplier_id) if header.supplier_id else None
        if supplier_id is None:
            continue
        ac_number = pairs.get((str(line.product_id), supplier_id))
        if ac_number:
            to_close.append((line, header, ac_number))
    if not to_close:
        return 0, []

    # Untagged BEFORE the close, so the rows this frees are `raised` in time for
    # THIS SAME import's own auto-place pass to pick them up again.
    line_ids = [str(line.id) for line, _h, _n in to_close]
    from app.models.project_so import INQUIRY_PLACED, OrderInquiryRow

    placed_row_ids = [
        row_id for (row_id,) in
        _in_chunks(
            db.query(OrderInquiryRow.id).filter(OrderInquiryRow.state == INQUIRY_PLACED),
            OrderInquiryRow.po_line_id, line_ids,
        )
    ]
    if placed_row_ids:
        from app.services.project_order_inquiry_service import (
            ProjectOrderInquiryService,
        )

        ProjectOrderInquiryService(db).unplace_rows(placed_row_ids)

    # For the outcome identity only (below) - one query for the whole batch of
    # matched products, rather than one per closed line.
    item_codes: dict[str, str] = {}
    if outcome is not None:
        item_codes = dict(
            db.query(Product.id, Product.product_code)
            .filter(Product.id.in_({str(line.product_id) for line, _h, _n in to_close}))
            .all()
        )

    touched_headers: dict[str, PurchaseOrder] = {}
    superseded_documents: list[dict] = []
    for line, header, ac_number in to_close:
        line.line_status = "closed"
        touched_headers[str(header.id)] = header
        if outcome is not None:
            outcome.updated(
                code=oc.LINE_CLOSED,
                identity=_identity(
                    header.po_number, item_codes.get(str(line.product_id), ""), ""
                ),
                value=header.po_number,
                message=f"{header.po_number} superseded by AutoCount PO {ac_number}",
                entity_type="order_line",
                entity_id=line.id,
            )
        superseded_documents.append({
            "po_number": header.po_number,
            "superseded_by": ac_number,
        })
        logger.info(
            "outstanding import: CRM-raised PO %s line %s (product %s) superseded "
            "by AutoCount PO %s", header.po_number, line.id, line.product_id, ac_number,
        )
    db.flush()

    # A PO whose every line just closed is itself closed - the header's status is a
    # summary of its lines, not a separate fact somebody has to notice went stale.
    for header in touched_headers.values():
        still_open = (
            db.query(PurchaseOrderLine.id)
            .filter(PurchaseOrderLine.purchase_order_id == header.id,
                    PurchaseOrderLine.line_status == "open")
            .first()
        )
        if still_open is None:
            header.status = "closed"
    db.flush()

    return len(to_close), superseded_documents


def _supersede_crm_raised_pos(db: Session, resolved: _Resolved,
                              outcome: ImportOutcome,
                              lines: Optional[list[Line]] = None) -> tuple[int, list[dict]]:
    """Thin adapter over `supersede_crm_raised_pos` (extracted, S5): builds the
    `(product_id, supplier_id, po_number)` triples the shared function wants from
    this upload's own `_Resolved` state.

    `lines`, default `resolved.lines`: `apply` (S5 review round 1, B2/B3) calls this once
    PER DOCUMENT BATCH now, passing only the lines that batch's documents named, so the
    supersession runs inside that batch's own transaction, before its commit, rather than
    once for the whole file after every batch has already committed - a defect here fails
    only the batch it belongs to, and the caller's counts/documents accumulate across calls.
    """
    triples = []
    for l in (lines if lines is not None else resolved.lines):
        supplier_id = resolved.party_by_doc.get(l.doc_number)
        product_id = resolved.product_by_code.get(_norm(l.item_code))
        if supplier_id and product_id:
            triples.append((product_id, supplier_id, l.doc_number))
    return supersede_crm_raised_pos(db, triples, outcome=outcome)


#: Documents `apply` commits together (S5). A worker killed mid-run keeps every batch
#: already committed rather than losing the whole file to one uncommitted transaction, and
#: each batch's own preloads (`_preload_closed_lines`, `_preload_lines_by_id`) stay well
#: under the 1,000 bound-parameter statement that dropped the connection in production even
#: on a document that carries far more than one line.
_DOCUMENT_BATCH = 500


def _write_header(db: Session, bind: _Binding, doc_type: str, plan: _Plan, lift: set,
                  resolved: _Resolved, agent_ids: dict[str, str],
                  existing_headers: dict, order_ids: dict[str, str],
                  activated: list[str], adopted: list[str], number: str) -> None:
    """Create or update ONE document's header, and record its id in `order_ids`.

    Extracted out of `apply`'s batch loop (review round 1b, C7) so the per-document body is
    flat again - behaviour-preserving, the exact logic the inline loop body ran. `plan`,
    `existing_headers`, `order_ids`, `activated`, `adopted` are all mutated in place; nothing
    is returned.
    """
    header = existing_headers.get(number)
    if header is None:
        header = bind.header(**{
            bind.number: number,
            # A document the file states COMPLETE is born closed. Created live and
            # closed a moment later it would be the same row, but a whole-year book
            # would first announce thousands of live orders to every listener on the
            # status column.
            "status": (_COMPLETE_STATUS if number in plan.complete
                       else bind.write_status),
            "source_system": "scm_upload",
            "source_ref": doc_type,
        })
        db.add(header)
        db.flush()
        # So a batch that CREATES a document can still look its own header's
        # currency back up later in the SAME batch (`_write_change`'s
        # `header_currency`, PLAN-po-line-currency-follows-header-22sep.md) - this
        # dict otherwise only ever held PRE-EXISTING headers.
        existing_headers[number] = header
    else:
        if number in plan.complete:
            # Nothing is owed on it any more. The header follows its lines: left
            # open, a fully delivered order reads as a live commitment on every
            # screen.
            header.status = _COMPLETE_STATUS
        if number in lift:
            # The extract is evidence the document has been placed, and AutoCount is
            # the system of record for that. Left as a draft the lines land and the
            # supply is invisible, while the response still reports them as added.
            header.status = bind.write_status
            activated.append(number)
        # ADOPTION. A document another feed created - the Order Inquiry sheet is the
        # one that does this - is taken over here, because this extract is a
        # statement of the WHOLE open book and that sheet is one person's working
        # record. Without the handover the sheet keeps ownership, and its next
        # upload reverts a quantity CS just corrected. Only a foreign source is
        # claimed: re-uploading this extract must not churn the rows it already owns.
        if (header.source_system or "") not in ("", "scm_upload"):
            header.source_system = "scm_upload"
            header.source_ref = doc_type
            adopted.append(number)
    # Header-level values the file states (PO DATE, currency). Written whenever the
    # file supplies one: `scm.receipt_lead_v` measures lead days from `issue_date`,
    # so discarding it costs the module every lead-time observation it could have had.
    for col, key in bind.header_cols:
        value = resolved.header_by_doc.get(number, {}).get(key)
        if value is not None:
            setattr(header, col, value)
    # A purchase header with no currency, from the file or from before, is CNY
    # (`DEFAULT_PO_CURRENCY`); a stated one has just been written above and stands.
    if doc_type == PO and not getattr(header, "currency", None):
        header.currency = DEFAULT_PO_CURRENCY
    # Columns the file FILLS rather than restates. The sales book's order type is
    # the case: for a document this upload creates the file is the only evidence
    # there is, while a value already on the header was set by a person and a weekly
    # re-upload silently overwriting it is the failure.
    for col, key in bind.header_fill_cols:
        value = resolved.header_by_doc.get(number, {}).get(key)
        if value is not None and not getattr(header, col, None):
            setattr(header, col, value)
    # Who sold it. Written whenever the file names an agent we could resolve or
    # create, because the extract is the record of that and a re-upload restating
    # the same code is a no-op. An absent code never clears an existing link: a file
    # that simply left the column blank is not evidence that the order changed hands.
    if bind.agent_fk:
        agent_id = agent_ids.get(resolved.agent_by_doc.get(number, ""))
        if agent_id:
            setattr(header, bind.agent_fk, agent_id)
    # What the fulfilment policy actually weighs, stamped from the split above.
    # Written ONLY when this upload could decide it: a document nothing classified
    # keeps whatever it already had and is reported by name instead
    # (`_classify_demand`).
    cls = plan.demand.get(number)
    if cls and bind.demand_class_col:
        setattr(header, bind.demand_class_col, cls)
    # Attach the counterparty when the file named one we could resolve. A missing
    # code never overwrites a present link; a code that CONTRADICTS it does, because
    # the file is the system of record for who we bought from and chasing the wrong
    # supplier is the failure mode. Both halves are reported by `_honesty_issues`.
    party_id = resolved.party_by_doc.get(number)
    if party_id and str(getattr(header, bind.party_fk, None) or "") != str(party_id):
        setattr(header, bind.party_fk, party_id)
    # The code as printed, kept whether or not it linked. The file is the record of
    # what the document says, so a restated code overwrites; a file that simply
    # omits it never blanks one we already hold.
    #
    # `party_code_by_doc` holds it already `_norm`ed (trimmed, upper), which is what
    # `customer_label.normalize_debtor_code` writes from the history feed. The two
    # feeds write the SAME column, so a difference in spelling shows up as two
    # Who-bought-it rows for one debtor - passed through that helper here so there
    # is one authority for the value rather than two that merely agree today.
    if bind.party_code_header_col:
        code = normalize_debtor_code(resolved.party_code_by_doc.get(number))
        if code:
            setattr(header, bind.party_code_header_col, code)
    order_ids[number] = header.id


def _write_change(db: Session, bind: _Binding, order_ids: dict[str, str],
                  resolved: _Resolved, read: ReadResult, outcome: ImportOutcome,
                  applied: dict, applied_line_ids: dict[int, str],
                  settled_line_ids: set[str], closed_candidates: dict,
                  lines_by_id: dict, money_differs_by_c: dict[int, bool], c,
                  *, header_currency: Optional[str]) -> None:
    """Write ONE diff change - CLOSED, ADDED, unchanged, or a real qty/date change.

    `header_currency` (PLAN-po-line-currency-follows-header-22sep.md): this change's
    OWN document's header currency, already resolved by `_write_header` before any
    change is written - the fallback a purchase line with no stated currency takes,
    on every path below that used to reach for `DEFAULT_PO_CURRENCY` directly.

    Extracted out of `apply`'s batch loop (review round 1b, C7) so the per-document body is
    flat again - behaviour-preserving, the exact logic the inline loop body ran (every
    `continue` there is a `return` here, since this now runs once per change rather than as
    one iteration of a bigger loop). `applied`, `applied_line_ids`, `settled_line_ids` are
    mutated in place; nothing is returned.
    """
    source_row = (int(c.after.row_ref) if c.after is not None
                  and (c.after.row_ref or "").isdigit() else None)
    identity = _identity(c.doc_number, c.item_code, c.location)
    if c.kind == CLOSED:
        line = lines_by_id.get(str(c.before.row_ref))
        if line is not None:
            if c.after is None:
                # Closed by ABSENCE: the file has stopped stating this line. A
                # status change, never a delete, and never a fabricated receipt
                # - the line was planned against, and inventing a receipt no GRN
                # supports would corrupt lead-time measurement and the picking
                # reconciliation.
                line.line_status = "closed"
            else:
                # Closed by STATEMENT: the row is in the file and says the line
                # is finished. Here the delivered figure is not invented, it is
                # quoted - so it is written, along with the date and the money
                # the same row carries. Anything less leaves a completed line
                # claiming it shipped nothing, on the only record of that order
                # this system will ever have.
                _write_settled(line, c.after,
                               read.extras.get(str(c.after.row_ref), {}), bind,
                               c.before.required_date, header_currency=header_currency)
            applied["closed"] += 1
            # `source_row` is None for the absence half - there is no row in the
            # upload to point at - and the row number itself for a stated
            # settlement. Recorded either way: it is the destructive half, and
            # the job detail is where somebody goes to find out what an upload
            # took away.
            outcome.updated(row=source_row, code=oc.LINE_CLOSED, identity=identity,
                            value=c.doc_number, entity_type="order_line",
                            entity_id=c.before.row_ref)
            applied_line_ids[id(c)] = str(c.before.row_ref)
        elif source_row is not None:
            # The line has gone since the diff was read, and this half came from
            # a real source row - so it needs its own outcome or the job finishes
            # short of its own total. Same answer the update branch gives.
            outcome.skip(row=source_row, code=oc.ORDER_NOT_FOUND, identity=identity,
                         value=c.doc_number,
                         message="the line this row settles no longer exists")
        return

    if c.kind == ADDED:
        extra = read.extras.get(str(c.after.row_ref), {})
        product_id = resolved.product_by_code.get(_norm(c.item_code))
        warehouse_id = (resolved.warehouse_by_code.get(_norm(c.location))
                        if c.location else None)
        settled = states_settled(c.after)
        # A line that comes back is the SAME line, and the receipt already
        # booked against it belongs to it. Inserting a second row would leave
        # that receipt stranded on the old one while the new row starts at zero
        # received, so the two together claim the full ordered quantity is still
        # at sea - overstated for good, and stable, so re-uploading never
        # corrects it.
        revived = _match_closed_line(
            closed_candidates, bind, order_ids[c.doc_number], product_id,
            warehouse_id, c.after.required_date, settled_line_ids)
        if revived is not None and settled:
            # NOT a revival: the file states this line finished, and the line is
            # already closed. This is what a re-upload of a completed book runs
            # into on every row, so it has to leave the database exactly as it
            # found it - reopening here would make a delivered order read as
            # stock on its way in, and adding the quantity on top would do it
            # twice.
            held = {col: getattr(revived, col) for col, _key in bind.money_cols}
            ordered, fulfilled = _settled_quantities(c.after, extra)
            moved = (_settled_qty_differs(revived, ordered, fulfilled, bind)
                     or _money_differs(held, extra, bind))
            _write_settled(revived, c.after, extra, bind, getattr(revived, bind.date),
                           header_currency=header_currency)
            settled_line_ids.add(str(revived.id))
            applied_line_ids[id(c)] = str(revived.id)
            if moved:
                applied["updated"] += 1
                outcome.updated(row=source_row, identity=identity, value=c.doc_number,
                                entity_type="order_line", entity_id=revived.id)
            else:
                applied["unchanged"] += 1
                outcome.unchanged(row=source_row, identity=identity, value=c.doc_number)
            return
        if revived is not None:
            already = float(getattr(revived, bind.fulfilled) or 0)
            # Setting `line_status = "open"` here is ALSO what keeps a second row
            # wanting the identical match from reviving this same line twice
            # (review round 1b, C5): `_match_closed_line` checks `line_status ==
            # "closed"` on the live object, `revived` IS that object, and
            # `candidates` was never re-queried mid-batch - so the very mutation
            # this line needed anyway is what excludes it, with no separate
            # `settled_line_ids` entry required for this branch.
            revived.line_status = "open"
            revived.qty_ordered = already + c.after.qty
            setattr(revived, bind.date,
                    _write_date(c.after, getattr(revived, bind.date)))
            _refresh_money(revived, extra, bind, header_currency=header_currency)
            applied["added"] += 1
            applied_line_ids[id(c)] = str(revived.id)
            outcome.success(row=source_row, code=oc.CREATED, identity=identity,
                            value=c.doc_number, entity_type="order_line",
                            entity_id=revived.id)
            return
        # An explicit id, not the column's own default: `planning_change_service`
        # (called after this function flushes) needs the CORE line id an ADDED
        # change wrote, and that default is a client-side callable SQLAlchemy
        # would not resolve onto this instance until the flush below.
        new_line_id = str(uuid.uuid4())
        # A completed document this database has never seen: the whole line is
        # written from the file, closed, with the order and the quantity that
        # went out both intact. Ordered and fulfilled come from the row rather
        # than from `c.after.qty`, which on a settled line is the nothing that
        # is left.
        ordered, fulfilled = (_settled_quantities(c.after, extra) if settled
                              else (c.after.qty, 0))
        fields = {
            "id": new_line_id,
            bind.header_fk: order_ids[c.doc_number],
            "product_id": product_id,
            "warehouse_id": warehouse_id,
            "qty_ordered": ordered,
            bind.fulfilled: fulfilled,
            bind.date: c.after.required_date,
            "line_status": _COMPLETE_STATUS if settled else "open",
        }
        for col, key in bind.money_cols:
            fields[col] = extra.get(key)
        # A new purchase line with no stated currency takes its header's currency, the
        # same fallback `_refresh_money` applies to a line that already exists.
        if bind.header is PurchaseOrder and not fields.get("currency"):
            fields["currency"] = header_currency
        db.add(bind.line(**fields))
        if settled:
            # Claimed for this run, so a second identical row of the same
            # completed book inserts its own line instead of landing on this one
            # (it is not in `closed_candidates` either, for the same reason).
            settled_line_ids.add(new_line_id)
        applied["added"] += 1
        applied_line_ids[id(c)] = new_line_id
        outcome.success(row=source_row, code=oc.CREATED, identity=identity,
                        value=c.doc_number)
        return

    if c.kind == "unchanged":
        # Unchanged is about the QUANTITY and the DATE, which is what the diff
        # compares. The same line can still be repriced, and nothing else on
        # either channel ever revisits that column, so a line left alone here
        # quotes last week's money for ever. Compared at the 2 decimals the
        # column stores, so a sheet that states a third one cannot report an
        # update on every re-upload. `money_differs_by_c` was computed once,
        # above, when `lines_by_id` was built - read back rather than asked
        # twice, so the two can never quietly disagree.
        extra = read.extras.get(str(c.after.row_ref), {})
        if money_differs_by_c.get(id(c), False):
            line = lines_by_id.get(str(c.before.row_ref))
            if line is not None:
                _refresh_money(line, extra, bind, header_currency=header_currency)
                applied["updated"] += 1
                outcome.updated(row=source_row, identity=identity, value=c.doc_number,
                                entity_type="order_line", entity_id=line.id)
                return
        applied["unchanged"] += 1
        outcome.unchanged(row=source_row, identity=identity, value=c.doc_number)
        return

    # qty and/or date changed: update the row the diff paired, in place.
    line = lines_by_id.get(str(c.before.row_ref))
    if line is None:
        # The line the diff paired against has gone since it was read. Nothing
        # is written, so the row is a skip rather than a silent nothing.
        outcome.skip(row=source_row, code=oc.ORDER_NOT_FOUND, identity=identity,
                     value=c.doc_number,
                     message="the line this row updates no longer exists")
        return
    # The extract states what is OUTSTANDING, so ordered is outstanding plus
    # whatever has already been delivered or received. Writing the figure
    # straight in would erase a booked receipt and leave the goods counted as
    # still at sea.
    already = float(getattr(line, bind.fulfilled) or 0)
    line.qty_ordered = already + c.after.qty
    # `c.kind` reaches here for QTY_CHANGED too, not only a real date move: an
    # unreadable incoming date leaves `_classify` reading the date as unchanged,
    # but the row still lands in this branch on the quantity alone, and a
    # straight `c.after.required_date` would still write the `None` `_classify`
    # was built to avoid. `_write_date` keeps the line's own stored date in that
    # case.
    setattr(line, bind.date, _write_date(c.after, c.before.required_date))
    _refresh_money(line, read.extras.get(str(c.after.row_ref), {}), bind,
                   header_currency=header_currency)
    applied["updated"] += 1
    applied_line_ids[id(c)] = str(line.id)
    outcome.updated(row=source_row, identity=identity, value=c.doc_number,
                    entity_type="order_line", entity_id=line.id)


def _claim_stated_so_links(db: Session, read: ReadResult) -> int:
    """One `po_upload` claim per line whose `FromSODocList` names a sales order (G12/D2).

    The history channel has done this since migration 358 and writes `po_history`; this is
    the same fact off the same column on the OUTSTANDING book, and the two sources are kept
    apart so an upload result can say which book stated a pairing. Both write through
    `order_link_service.claim_book_pairing`, so there is one get-or-create rather than two
    that merely agree.

    Both families in one loop: `read.lines` is the purchase-order half, `read.spo_lines` the
    shipping-order half, and a claim carries the DOCUMENT NUMBER rather than a row id, so
    which table it lands in is `resolve()`'s decision and is made from the number's own
    prefix. Resolution is asked for right here, narrowed to the sales orders this book
    named: without it the claim's `so_line_id` stays NULL, and dedication reads only
    RESOLVED claims - a claim nothing resolved would sit in the table saying nothing.

    `seen` is this call's own memory, keyed as the unique index is. One book states the same
    (document, item) pairing on more than one row routinely (two containers of one line),
    and nothing is flushed until the end, so a claim added moments ago is invisible to the
    SELECT inside the helper.
    """
    company_id = resolve_write_company_id(get_company_scope(db), ambiguous=None)
    seen: set[tuple] = set()
    written = 0
    so_numbers: set[str] = set()
    for line in list(read.lines) + list(read.spo_lines):
        stated = (read.extras.get(str(line.row_ref), {}) or {}).get("so_number")
        so_number = _clean_so(stated)
        if not so_number or not line.doc_number or not line.item_code:
            continue
        key = (company_id, so_number, line.doc_number, line.item_code)
        if key in seen:
            continue
        seen.add(key)
        order_link_service.claim_book_pairing(
            db,
            company_id=company_id,
            so_number=so_number,
            po_number=line.doc_number,
            item_code=line.item_code,
            source=order_link_service.SOURCE_PO_UPLOAD,
        )
        so_numbers.add(so_number)
        written += 1
    if not written:
        return 0
    db.flush()
    # Both sides, in the machinery that already knows how to find them: the sales line by
    # (SO number, item code), the purchase side in `purchase_order_lines` or
    # `spo_allocations` decided by the document number's prefix.
    order_link_service.resolve(db, so_numbers=so_numbers)
    return written


def _clean_so(value) -> Optional[str]:
    """The sales-order number as the file spells it, trimmed.

    A cell naming SEVERAL orders (`SO123, SO456`) is deliberately NOT split: which line the
    second one describes is exactly the guess G12 exists to stop, and a claim naming the
    wrong customer's order is worse than no claim at all - the line simply stays
    unattributed and is counted for Joey to fix at the source.
    """
    text = str(value or "").strip()
    if not text or any(sep in text for sep in (",", ";", "/", "|")):
        return None
    return text


def apply(db: Session, file_data: bytes, doc_type: str = SO,
          actor: Optional[str] = None, outcome: Optional[ImportOutcome] = None,
          on_total_rows: Optional[Callable[[int], None]] = None,
          file_name: Optional[str] = None) -> dict:
    """Write the upload. Returns the same counts the preview showed.

    Closing is `line_status = 'closed'`, not a delete. The line existed and was planned
    against; erasing it would make last week's plan unexplainable, and `scm.committed_v`
    already excludes non-open lines (migration 311).

    `outcome` records what happened to each SOURCE ROW for the job detail. Optional so a
    direct caller (and every existing test) keeps the old signature; when absent a throwaway
    non-persisting recorder stands in, so there is exactly one code path either way.
    """
    outcome = outcome or ImportOutcome(None, persist=False)
    plan = _build(db, file_data, doc_type, on_total_rows=on_total_rows)
    read, resolved, diff = plan.read, plan.resolved, plan.diff
    if diff is None or resolved is None:
        return {"ok": False, "missing_columns": read.missing_columns, "counts": {}}

    # D23 (captain 2026-09-06, AC-P2-8, reverses QP1): an order nothing can classify no
    # longer refuses the file - it lands with `demand_class` NULL (`_write_headers`'s own
    # `cls = plan.demand.get(number)` already leaves the column untouched when nothing
    # classified it) and is reported below, `unclassified_documents`/
    # `unclassified_documents_numbers`, the same shape `suppliers_created`/
    # `suppliers_created_codes` already use. Fixing the customer's market segment (or the
    # order type, or the agent's class) and re-uploading is still the whole remedy - it is
    # simply no longer the ONLY way the file is accepted at all.

    _record_rows_never_written(read, resolved, outcome)

    # What this JOB accounts for, which is more than the file states. A line closed by its
    # ABSENCE from the upload carries an outcome and no source row: with the file's own row
    # count as the total, a five-row file that closes one line finishes "6 / 5" with a
    # progress bar past 100%. Published again here - the diff is known and nothing has been
    # written yet - so the denominator is right before the first write. A line the file
    # STATES settled is not counted here: it has a source row of its own, and counting it
    # would push the total past the number of outcomes there are to record.
    closed_rows = sum(1 for c in diff.changes if c.kind == CLOSED and c.after is None)
    total_rows = read.total_rows + closed_rows
    if on_total_rows is not None:
        on_total_rows(total_rows)

    # The BEFORE half of every Plan Exception (AC-D4), taken while the old position is still
    # the one in the database. Reconstructing it afterwards by inverting the deltas would be
    # a second implementation of netting whose only job is to agree with the first, and the
    # day it stopped agreeing the before-column would be a number nobody could reproduce.
    #
    # Best-effort like the generation below, and for the same reason: the upload is the
    # operation the user asked for. A defect in the exception path must not send them back to
    # re-upload a book that would otherwise have gone in - it must cost them the batch, which
    # the next upload produces again.
    # The products this upload moved. Read once: the SO channel's plan-exception snapshot
    # below needs them, and BOTH channels state them on the result so the Order Inquiries
    # page can scope its "Link now" to the book that just arrived (AC-H13).
    touched_pids = _affected_product_ids(plan)
    exception_pids = list(touched_pids) if doc_type == SO else []
    before_positions: dict = {}
    if exception_pids:
        try:
            before_positions = plan_exception_service.snapshot(db, exception_pids)
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception("plan exception BEFORE snapshot failed; skipping the batch")
            exception_pids = []

    bind = _binding(doc_type)
    lift = set(plan.activate)

    # The salesperson master, created where this file names a code nobody holds (AC-6.4).
    # Resolved once per CODE rather than per document: one agent sells many orders, and
    # creating inside the header loop would issue the same lookup for each of them.
    # Deliberately before the loop and never inside `preview`, which writes nothing.
    agent_ids: dict[str, str] = {}
    if bind.agent_fk:
        for code in sorted(set(resolved.agent_by_doc.values())):
            agent = sales_agent_service.resolve_or_create(db, code)
            if agent is not None:
                agent_ids[code] = agent.id

    # The supplier master, back-created where a PO-book creditor code names nobody it
    # holds - the mirror of the agent creation just above, and the same pattern
    # `order_service._upsert_customer_from_debtor` uses for a sales debtor: find by code
    # first, create a minimal row, flush so the id is usable before the header links it,
    # commit stays with this transaction. One creation per distinct code (the dict key),
    # never inside `preview`.
    #
    # NOT a backfill: only documents THIS upload names get linked. A NULL `supplier_id`
    # left by an earlier upload of the same document stays NULL until that document is
    # re-uploaded - the next upload of the PO book links it then, same as any other
    # figure this importer restates.
    #
    # `supplier_code` is unique per COMPANY (migration 305), not globally, so a code
    # another company already holds is created here too - as its own row, under this
    # company, exactly as if nobody anywhere held it (`test_outstanding_import_company_
    # isolation.py`). The insert still runs inside a savepoint: a concurrent upload
    # creating the same code for this SAME company, or a schema still on the model's
    # bare column-level unique (a `create_all` scratch schema, unlike this suite's
    # migrated one), must not poison the whole transaction - the code is left exactly as
    # unresolved as it always was rather than crashing the upload.
    created_supplier_codes: list[str] = []
    # AC-P2-1/D8 (S2): the customer mirror of `created_supplier_codes` - reported
    # under its own `customers_created`/`customers_created_codes` keys, the same
    # shape the supplier side already reports under.
    created_customer_codes: list[str] = []
    if bind.party_back_create and resolved.party_raw_code_by_code:
        party_ids_by_code: dict[str, str] = {}
        code_col = getattr(bind.party, bind.party_code_col)
        # Driven by the RAW-code map, not the label map: the file's label column is
        # optional (`_MINIMAL` PO uploads carry no SUPPLIER name at all), and a code with
        # no label is still a code to create - it just falls back to naming itself.
        for code in sorted(resolved.party_raw_code_by_code):
            if bind.party is Customer:
                # Not `.one_or_none()`: a customer code can still hold several rows until
                # the DEBTOR-CODE-UNIQUE merge has run, and that raised MultipleResultsFound.
                existing = customer_identity.customer_by_code(db, code)
            else:
                existing = db.query(bind.party).filter(func.upper(code_col) == code).one_or_none()
            if existing is not None:
                party_ids_by_code[code] = str(existing.id)
                continue
            raw_code = resolved.party_raw_code_by_code.get(code, code)
            name = resolved.party_label_by_code.get(code)
            if bind.party is Customer:
                # AC-P2-1/D8: a customer needs BOTH code and name (the unique
                # index is on the pair) - unlike the supplier side, a code
                # with no name is left exactly as unresolved as it always
                # was, still attributable via `party_code_header_col`.
                if not name:
                    continue
                created = customer_rules.back_create_customer(db, code=raw_code, name=name)
                if created is None:
                    continue
                party_ids_by_code[code] = str(created.id)
                created_customer_codes.append(raw_code)
                continue
            created = back_create_supplier(
                db, code=raw_code, name=name or raw_code, party=bind.party,
                code_col=bind.party_code_col, name_col=bind.party_name_col)
            if created is None:
                continue
            party_ids_by_code[code] = str(created.id)
            created_supplier_codes.append(raw_code)
        for number, code in resolved.party_code_by_doc.items():
            pid = party_ids_by_code.get(code)
            if pid and number not in resolved.party_by_doc:
                resolved.party_by_doc[number] = pid

    # The PO-book NAME fallback: a document whose only creditor evidence is a NAME
    # (`party_name_key_by_doc`, built when the file states no code at all - the captain's
    # real "PO & SPO outstanding.xlsx" export). `_resolve` already linked whatever an
    # existing supplier matched by name; what is left here is one back-creation per
    # distinct cleaned name, same savepoint guard and same reporting as the code path
    # above. Re-queried by name rather than trusting `_resolve`'s snapshot, for the same
    # reason the code loop re-queries by code: a name this run's own code-based creation
    # (or an earlier name in THIS loop) just created must be found, not re-created.
    # Supplier only: no S2 scenario needs a customer back-created off a NAME
    # alone (D8 requires code AND name), and `_clean_supplier_name`'s cleaning
    # rule (trailing currency notes, etc.) is a supplier-specific concern.
    if bind.party_back_create and bind.party is not Customer and resolved.party_cleaned_name_by_key:
        party_ids_by_name_key: dict[str, str] = {}
        name_col = getattr(bind.party, bind.party_name_col)
        for name_key in sorted(resolved.party_cleaned_name_by_key):
            cleaned = resolved.party_cleaned_name_by_key[name_key]
            # `.first()`, not `.one_or_none()`: unlike `supplier_code`, `supplier_name`
            # carries no uniqueness constraint - the real master already holds more than
            # one supplier under the identical name in places - so more than one match
            # is a fact of the data, not a defect this import should crash on. Ordered
            # so the choice is at least DETERMINISTIC across a re-upload rather than
            # whatever order Postgres happened to return rows in.
            existing = (
                db.query(bind.party)
                .filter(func.upper(name_col) == name_key)
                .order_by(bind.party.id)
                .first()
            )
            if existing is not None:
                party_ids_by_name_key[name_key] = str(existing.id)
                continue
            slug = supplier_slug(db, cleaned, party=bind.party,
                                 code_col=bind.party_code_col)
            created = back_create_supplier(
                db, code=slug, name=cleaned, party=bind.party,
                code_col=bind.party_code_col, name_col=bind.party_name_col)
            if created is None:
                continue
            party_ids_by_name_key[name_key] = str(created.id)
            # The cleaned NAME, not the generated slug: it is what the operator's file
            # actually said and is what they will recognise, where the slug is an
            # internal code they never typed.
            created_supplier_codes.append(cleaned)
        for number, name_key in resolved.party_name_key_by_doc.items():
            pid = party_ids_by_name_key.get(name_key)
            if pid and number not in resolved.party_by_doc:
                resolved.party_by_doc[number] = pid

    # Header per document in scope, created if absent. `write_status` differs per type: an
    # outstanding-PO extract is a book of orders already PLACED, and `scm.on_order_v`
    # deliberately ignores drafts, so writing them as drafts would import supply and hide it.
    order_ids: dict[str, str] = {}
    activated: list[str] = []
    #: Documents another feed created that this extract has taken ownership of. Reported so
    #: the handover is visible rather than a silent change of who may edit what.
    adopted: list[str] = []

    applied = {"added": 0, "updated": 0, "closed": 0, "unchanged": 0}
    # The CORE line id each change actually wrote, keyed by the change object's identity
    # (stable for the life of this call: `diff.changes` is never rebuilt below). Best-effort
    # readers downstream - `planning_change_service.build_batch` - need this because an
    # ADDED change's own `row_ref` is the source ROW NUMBER, not a line id: there is no id
    # to have until the insert below runs.
    applied_line_ids: dict[int, str] = {}
    #: Closed lines this run has claimed while LEAVING them closed - the settled branch only
    #: (review round 1b, C5): a reopened line no longer needs an entry here, because
    #: `_match_closed_line` checks `line_status == "closed"` on the live object and the
    #: reopen itself (`revived.line_status = "open"`) is what excludes it from a second
    #: match. A settled row leaves its line closed, so THAT case stays matchable in
    #: `_preload_closed_lines`' candidates even after being claimed, and two identical rows
    #: of a completed book would otherwise both land on the first line and the second
    #: quantity would simply vanish.
    settled_line_ids: set[str] = set()
    # Accumulated across every batch's own PER-BATCH reaction call (S5 review round 1,
    # B2/B3): `_supersede_crm_raised_pos` and the relink pass now run once per document
    # batch rather than once for the whole file, so their counts/documents build up here
    # exactly the way `applied` above does for the line writes.
    superseded_lines = 0
    superseded_documents: list[dict] = []
    relinked = 0

    # Grouped by document, not walked flat: `diff.changes` is already sorted (doc, item,
    # location) (`outstanding_diff.diff_lines`), so this is a partition, not a re-order - and
    # it is what lets the batch loop below write one document's header AND lines together.
    changes_by_doc: dict[str, list] = defaultdict(list)
    for c in diff.changes:
        changes_by_doc[c.doc_number].append(c)

    # S5: committed in batches of `_DOCUMENT_BATCH` documents rather than once for the whole
    # file. A worker killed mid-run used to lose the entire upload to one uncommitted
    # transaction (the completed 2020-Sep 2026 book, 82,257 rows, died after an hour with
    # nothing kept); now it leaves every batch that finished behind, and the outcome rows
    # (their own session, flushed alongside) say exactly where it stopped.
    # `outcome.flush(publish=True)` after each commit is also what keeps the activity card
    # counting up WHILE the job runs, rather than jumping from 0 to the final total only on
    # completion.
    for doc_batch in _chunked(diff.scope_documents, _DOCUMENT_BATCH):
        doc_batch_set = set(doc_batch)
        # One bulk lookup per batch instead of one SELECT per document (review round 1b, C1):
        # `doc_batch` is at most `_DOCUMENT_BATCH` documents, so a single `IN (...)` here
        # never approaches the bound-parameter ceiling the other preloads chunk against. The
        # dict comprehension's "last wins" on a repeated key is never actually ambiguous:
        # `uq_sales_orders_company_so_number` / `uq_purchase_orders_company_po_number` make
        # `(company_id, number)` unique, and this query already runs inside one company's
        # scope, so at most one row can ever come back per document number.
        number_col = getattr(bind.header, bind.number)
        existing_headers = {
            getattr(h, bind.number): h
            for h in db.query(bind.header).filter(number_col.in_(list(doc_batch))).all()
        }
        for number in doc_batch:
            _write_header(db, bind, doc_type, plan, lift, resolved, agent_ids,
                         existing_headers, order_ids, activated, adopted, number)

        # Closed-line candidates and existing-line lookups for THIS BATCH's documents only
        # (S5): one preload each instead of one query per ADDED / CLOSED / changed row. The
        # write below matches at most one header at a time, so preloading beyond this batch
        # would only hold memory a later batch's commit does not need yet.
        batch_changes = [c for number in doc_batch for c in changes_by_doc.get(number, ())]
        # Skipped entirely when nothing in this batch is ADDED (review round 1b, cap
        # finding): `closed_candidates` is read only by the ADDED branch below, and a batch
        # that is all CLOSED / repriced / qty-changed rows - a re-upload against a book
        # already on file - never looks at it.
        closed_candidates = (
            _preload_closed_lines(db, bind, {order_ids[number] for number in doc_batch})
            if any(c.kind == ADDED for c in batch_changes) else {}
        )
        # Narrowed to the rows that will actually be looked up (S5 review round 1, S6): a
        # CLOSED or a real qty/date change always needs its line, but the common
        # `unchanged` case never touches `lines_by_id` unless the file also repriced it -
        # fetching the line object for every merely-unchanged row was wasted work at file
        # scale. `_money_differs` is computed here once and cached by change identity so the
        # `unchanged` branch below reads the same answer rather than asking twice.
        money_differs_by_c: dict[int, bool] = {}
        line_ids: set[str] = set()
        for c in batch_changes:
            if c.before is None:
                continue
            if c.kind == UNCHANGED:
                extra = read.extras.get(str(c.after.row_ref), {})
                differs = _money_differs(
                    plan.money_by_line.get(str(c.before.row_ref), {}), extra, bind)
                money_differs_by_c[id(c)] = differs
                if not differs:
                    continue
            line_ids.add(str(c.before.row_ref))
        lines_by_id = _preload_lines_by_id(db, bind, line_ids)

        for number in doc_batch:
            # This document's own header currency, already resolved by `_write_header`
            # above (`existing_headers` now holds every header the batch touched,
            # created or pre-existing alike) - the fallback `_write_change` gives an
            # unstated purchase line, in place of the old `DEFAULT_PO_CURRENCY` literal.
            header_currency = getattr(existing_headers.get(number), "currency", None)
            for c in changes_by_doc.get(number, ()):
                _write_change(db, bind, order_ids, resolved, read, outcome, applied,
                             applied_line_ids, settled_line_ids, closed_candidates,
                             lines_by_id, money_differs_by_c, c,
                             header_currency=header_currency)
            # Self-heal (issue #969): the book upload writes its ADDED lines straight in
            # `_write_change` (`db.add(bind.line(**fields))`), bypassing `_upsert_lines`'s
            # own self-heal, which only sees the manual FE edit. Per order rather than per
            # row - the tightest spot after this order's own line-create pass, before its
            # batch commits. Same gate as the ESB heal: an adopted, unauthored mirror only.
            # Also gated on this document having ADDED a line at all (review round 2, S1),
            # the same way `closed_candidates` above is: without it every document in the
            # batch paid for the `ProjectSalesOrder` lookup even when nothing was added -
            # measured about 3,800 extra statements on a full book upload.
            if doc_type == SO and any(
                c.kind == ADDED for c in changes_by_doc.get(number, ())
            ):
                from app.models.project_so import SO_STATUS_ADOPTED, ProjectSalesOrder
                from app.services.project_so_adoption_service import (
                    ProjectSOAdoptionService,
                )

                project_so = (
                    db.query(ProjectSalesOrder)
                    .filter(
                        ProjectSalesOrder.so_id == order_ids[number],
                        ProjectSalesOrder.status == SO_STATUS_ADOPTED,
                        ProjectSalesOrder.project_id.is_(None),
                    )
                    .first()
                )
                if project_so is not None:
                    ProjectSOAdoptionService(db).mirror_missing_lines(project_so)

        # CRM<->AutoCount supersession (captain, 21 Aug) and the placement relink, PER
        # BATCH and inside that batch's own transaction, before its commit (S5 review round
        # 1, B2/B3): both read only THIS batch's own documents/lines, so both can be scoped
        # and both land or roll back together with the lines they follow - never a batch
        # whose lines committed while its reaction silently did not. Supersession keeps its
        # existing rule of never being wrapped in a best-effort guard: a failure here must
        # fail the whole BATCH (not the whole upload any more - the earlier batches already
        # committed) rather than let AutoCount lines land while the CRM's own duplicate
        # stays open.
        if doc_type == PO:
            batch_lines = [l for l in resolved.lines if l.doc_number in doc_batch_set]
            batch_superseded, batch_superseded_docs = _supersede_crm_raised_pos(
                db, resolved, outcome, lines=batch_lines)
            superseded_lines += batch_superseded
            superseded_documents.extend(batch_superseded_docs)

            # Section 3.G, AC-G3, scoped to this batch's own headers. Best-effort, same
            # reason the single-transaction reactions below are: the write has already
            # succeeded, and a defect in the reaction must cost the operator a relocation
            # the next upload makes again, never the upload itself.
            try:
                from app.services.project_order_inquiry_service import (
                    ProjectOrderInquiryService,
                )

                # On its OWN savepoint (review round 2), same as `_write_spo_lines` below:
                # a DB error here must not poison this batch's own commit - the lines this
                # batch already wrote are good regardless of whether the relink is.
                with db.begin_nested():
                    relinked += ProjectOrderInquiryService(db).relink_to_matching_lines(
                        [order_ids[number] for number in doc_batch],
                        actor_user_id=actor, trigger="po_book_upload",
                    )
            except Exception:  # pragma: no cover - defensive, see above
                logger.exception("re-linking placements failed for a confirmed PO upload")

        # One commit per batch (S5), not one for the whole file: a worker killed after this
        # point keeps every batch up to here, its lines AND its reactions above together.
        # No `db.flush()` first - `Session.commit()` flushes on its own before it emits
        # COMMIT, so a separate call here was one more round trip for nothing.
        # `outcome.flush(publish=True)` runs AFTER the commit above, never during it (review
        # round 1b, C2: the buffer-full auto-flush inside `_record` never publishes) - so
        # the card can only ever LAG the committed state by up to one batch, never lead it:
        # a crash right after this line still leaves `processed_rows` at the previous
        # batch's true, already-durable count.
        db.commit()
        outcome.flush(publish=True)

    # This job's own count, exactly like `closed_rows` above: a line closed by supersession
    # carries an outcome and no source row either, so the total has to grow with it or
    # `processed` runs past `total_rows` on the progress bar. Published again here (review
    # round 2, S3): every batch's own supersession outcomes were already counted into
    # `outcome`'s running total DURING the loop (each batch's `outcome.flush(publish=True)`
    # published `processed_rows` reflecting them), but the JOB's own `total_rows` field was
    # still the pre-supersession figure until now - `processed_rows` could momentarily read
    # ahead of the total the card shows beside it. `_run_scm_upload_job`'s `on_total_rows`
    # wrapper (C3) only touches `total_rows` on a call after the first, never resetting
    # `processed_rows` back to 0, so this is safe to call again here.
    total_rows += superseded_lines
    if on_total_rows is not None:
        on_total_rows(total_rows)

    # The three reactions below run ONCE for the whole file, after every batch has already
    # committed - NOT per batch, unlike supersession and the relink pass above (S5 review
    # round 1, B2/B3). Each one genuinely cannot be scoped to a subset of this upload's
    # changes without changing what it MEANS:
    #
    #   * the plan-exception batch is ONE before/after comparison against the reorder plan
    #     for the WHOLE upload - `before_positions` was captured once, before any batch
    #     wrote anything, and splitting it into one exception batch per document chunk would
    #     turn a single coherent "did this upload contradict the plan" answer into
    #     `_DOCUMENT_BATCH`-many fragments the exception screen (keyed on ONE
    #     `exception_batch_id`) has no contract for reading;
    #   * the shipping-order write reads `read.spo_lines` - a document family the diff never
    #     batches at all, since SPO documents are filtered out of `diff.scope_documents` at
    #     the reader (R4) - so there is no "this batch's SPO lines" to scope it to;
    #   * the planning-change batch is ONE reviewable unit for the SO book's re-planning
    #     screen (`order_count` / `line_count` describe the WHOLE upload), and fragmenting
    #     it the same way would break that screen's contract too.
    #
    # A defect in any of the three costs the operator that one reaction for the whole
    # upload, never the write itself, which has already committed, batch by batch, above.

    # AC-D2a: exceptions are a BATCH produced on confirmation, not ad-hoc signals arriving
    # one at a time. Best-effort, because the upload itself has already succeeded: a failure
    # to diff the plan must not fail the write and send the operator back to re-upload a book
    # that is already in.
    exception_batch_id = None
    if exception_pids:
        try:
            after_positions = plan_exception_service.snapshot(db, exception_pids)
            # The batch belongs to the PLAN it contradicts. Without this the exception
            # screen - which is a view OF a run - filters by a run id no batch carries and
            # shows nothing for ever, while the rows sit in the table.
            current = reorder_run_service.today_or_latest_run(db)
            # A run still being built contradicts nothing yet, so the batch would hang off a
            # plan that has no rows to disagree with. Only a completed plan can be contradicted.
            if current and current["row"].get("status") != "completed":
                current = None
            batch = plan_exception_service.generate_batch(
                db,
                run_id=str(current["row"]["id"]) if current else None,
                before=before_positions,
                after=after_positions,
                # The upload's OWN count of changed lines, carried through unchanged so the
                # screen can reconcile deltas against exceptions (AC-D2b).
                delta_count=sum(
                    n for kind, n in diff.counts.items() if kind != "unchanged"
                ),
                source_documents=list(diff.scope_documents),
                actor=actor,
            )
            exception_batch_id = str(batch.id)
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception("plan exception batch failed for a confirmed SO upload")

    # The shipping-order half of the same book (R4). Written after the purchase-order half
    # and before the reactions below, so "Link now" is offered the products BOTH halves
    # touched: a row whose only cover is a shipping order is exactly the row this feature
    # exists for, and a product list that named only the purchase-order side would leave it
    # unlinked.
    #
    # On its OWN savepoint (review round 28 Aug): the purchase-order half is already
    # written and committed-in-spirit by here, and a defect on this side must cost the
    # operator the shipping orders of one upload - reported on the job, and re-filed by the
    # next book - never the whole import they would then have to run again.
    spo = _SpoWrite()
    if doc_type == PO:
        try:
            with db.begin_nested():
                spo = _write_spo_lines(db, read, outcome)
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception("the shipping-order half of a confirmed PO upload failed")
            spo = _SpoWrite()
            outcome.fail(
                code=oc.DB_ERROR,
                message="The shipping orders in this book could not be filed.",
            )
    if spo.product_ids:
        touched_pids = sorted(set(touched_pids) | set(spo.product_ids))

    # G12/D2 (captain, 2 Sep 2026): the book's own `FromSODocList` column becomes a CLAIM.
    # Written after BOTH halves of the purchase book are on disk, so the resolver below can
    # find whichever family each pairing names - `purchase_order_lines` for a `######-S####`
    # number, `spo_allocations` for an `SPO-` one.
    #
    # This is the feed most attribution arrives on. G12's lock refuses an unattributed
    # project-bin line to the automatic pass, and until this ran the outstanding channel
    # read the column, recognised it, and threw it away - so a re-upload could never seed a
    # single dedication and the unclaimed count below could never fall. It runs on every
    # upload, not only the first: a claim is get-or-create on `uq_scm_order_link_claim_
    # identity`, so re-uploading the same book leaves exactly the claims it left last time.
    #
    # Best-effort, like the reactions below it and for the same reason: the book is already
    # written, and a defect here must cost the operator the dedication of ONE upload - which
    # the next upload of the same book states again - rather than the whole import.
    claims_written = 0
    if doc_type == PO:
        try:
            claims_written = _claim_stated_so_links(db, read)
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception("claiming the SO links a PO/SPO book states failed")

    # PLAN-so-book-diff-replanning.md section 2: the SO book's own reaction. Best-effort
    # for the same reason the exception batch above is - the upload has already succeeded,
    # and a defect in the reaction must cost the operator a batch the next upload produces
    # again, never the upload itself. Only the SO channel plans against a project; a PO
    # book has nothing to react to here.
    planning_change_batch = None
    if doc_type == SO and diff.has_material_change:
        try:
            from app.services import planning_change_service

            batch = planning_change_service.build_batch(
                db,
                diff,
                applied_line_ids=applied_line_ids,
                order_ids=order_ids,
                actor=actor,
                import_job_id=getattr(outcome, "import_job_id", None),
                file_name=file_name,
            )
            if batch is not None:
                planning_change_batch = {
                    "id": str(batch.id),
                    "order_count": batch.order_count,
                    "line_count": batch.line_count,
                }
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception("planning change batch failed for a confirmed SO upload")

    # G12 (`PLAN-scm-reorder-oi-feedback-1sep.md` S6, AC-6.11): only the PO channel
    # writes OPEN lines this rule counts. Company-wide and always current rather than
    # scoped to this upload's own rows - see `project_bin_lock`'s own docstring.
    unclaimed_project_bin_lines = None
    if doc_type == PO:
        try:
            from app.services.scm.project_bin_lock import (
                count_unclaimed_project_bin_lines,
            )

            unclaimed_project_bin_lines = count_unclaimed_project_bin_lines(db)
        except Exception:  # pragma: no cover - defensive, see above
            logger.exception(
                "counting unclaimed project-bin lines failed for a PO/SPO upload"
            )

    return {
        "ok": True,
        "exception_batch_id": exception_batch_id,
        "planning_change_batch": planning_change_batch,
        # Everything this upload accounted for: every non-blank row of the file, plus the
        # lines it closed by absence. One outcome per unit, so processed reaches total exactly.
        "total_rows": total_rows,
        # The file's own row count, kept beside it: it is the number the operator sees at the
        # top of their spreadsheet, and the two differing is the closures.
        "file_rows": read.total_rows,
        "counts": diff.counts,
        "applied": applied,
        # The products whose position this upload could have moved, and the documents it
        # names. What "Link now" is narrowed by (AC-H13); `scope_documents` is also what
        # the purchase-order list is filtered to when the buyer goes to look at them.
        "product_ids": touched_pids,
        "scope_documents": list(diff.scope_documents),
        "activated_documents": activated,
        "adopted_documents": adopted,
        # CRM-raised purchase orders this same upload retired because AutoCount now
        # states the identical (product, supplier) - see `_supersede_crm_raised_pos`.
        "superseded_documents": superseded_documents,
        # How many order-inquiry placements this upload moved onto the line whose warehouse
        # matches the demand (AC-G3). 0 on the SO channel and on a PO book that changed no
        # location - which is most of them.
        "relinked_placements": relinked,
        # The shipping-order half of this book (R4): documents and lines filed into
        # `spo_allocations`, lines closed because the book no longer states them, and
        # locations no warehouse matched (the line is kept and the raw code with it).
        "spo_documents": spo.documents,
        "spo_lines": spo.lines,
        "spo_closed": spo.closed,
        "spo_unknown_locations": spo.unknown_locations,
        # The same lines split by what they did: filed for the first time, restated with a
        # different figure, or already matching what we held. The confirm screen reads the
        # identical three off the preview, so what the operator agreed to and what the job
        # reports are one answer.
        "spo_new": spo.new,
        "spo_changed": spo.changed,
        "spo_unchanged": spo.unchanged,
        # G12 (S6, AC-6.11): open project-bin PO/SPO lines no claim names, company-wide,
        # as of THIS upload's own claim-resolution pass. `None` on an SO-channel upload,
        # which writes no supply line this rule covers.
        "unclaimed_project_bin_lines": unclaimed_project_bin_lines,
        # G12/D2: the SO<->PO pairings this book STATED (`FromSODocList`), whether they were
        # new or already held. What Joey's backfill in AutoCount is measured by from the
        # other side, beside the unclaimed count above.
        "so_links_claimed": claims_written,
        "resolution_issues": [asdict(i) for i in plan.issues],
        "row_problems": [asdict(p) for p in read.problems + plan.problems],
        # Reported by the commit as well as by the preview, and stated as it was BEFORE the
        # creation above: "new agent, unclassified" is exactly the fact the operator needs
        # after the fact, and re-reading the master here would report every one of them as
        # merely unclassified, losing which ones this upload invented.
        "unmapped_agents": [asdict(a) for a in plan.agent_notices],
        # AC: an operator must never discover an invented supplier by surprise. The count
        # is the whole truth; the list is capped the same way `_samples` caps diff
        # evidence, because a report naming every one of a few thousand codes is not one
        # a person can read either.
        "suppliers_created": len(created_supplier_codes),
        "suppliers_created_codes": created_supplier_codes[:CREATED_SUPPLIERS_LISTED],
        # AC-P2-1/D8 (S2): same shape, the customer side of the same fact.
        "customers_created": len(created_customer_codes),
        "customers_created_codes": created_customer_codes[:CREATED_SUPPLIERS_LISTED],
        # D23/AC-P2-8: same shape again - the count is the whole truth, the list is
        # capped so a book with hundreds of unclassifiable orders does not print all of
        # them; `plan.unclassified` is document NUMBERS, not codes, hence `_numbers`
        # rather than `_codes` to name what the values actually are.
        "unclassified_documents": len(plan.unclassified),
        "unclassified_documents_numbers": list(plan.unclassified)[:CREATED_SUPPLIERS_LISTED],
    }
