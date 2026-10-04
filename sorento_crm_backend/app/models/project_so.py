"""Customer PO to Sales Order (phase 2 of the `projects` module).

Every table here is MODULE side and lives in the `projects` schema (ADR-0011; see the
header of ``app.models.projects`` for the full rules on qualified FKs, pinned audit entity
types and raw SQL). `public.sales_orders` / `public.sales_order_lines` are CORE and SCM
owns them: they learn nothing about projects (finding G5). The link runs the other way,
from `projects.sales_orders.so_id`, and committed demand still reaches the reorder engine
through the core lines exactly as it does today.

**`projects.sales_orders` and `public.sales_orders` are different tables with the same bare
name.** So is the `_lines` pair. `ForeignKey("sales_orders.id")` below is the CORE one,
unqualified on purpose; every FK naming a module table is written `projects.<table>.<col>`.

Three ideas shape the whole file:

* **Documents are versioned, never overwritten** (D14). A revised PO or schedule is a new
  version of the same commitment, and the amendment is the computed DIFFERENCE between two
  versions. That is what purchasing consumes, and it is why `so_amendments.delta_json`
  exists rather than a mutated line.
* **The AI proposes, arithmetic decides** (measured 2026-08-01/02). Extraction lands in
  `extracted_json` with per-line values; nothing is trusted until `qty * unit_price ==
  amount` and the schedule column reconciles to the PO. Findings that fail are rows in
  `so_draft_findings`, and five of them block a publish.
* **The project is the anchor, not the customer** (D18). A pre-order parked under another
  debtor still belongs to this project, so joins go through `project_id`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.models.base import CompanyScopedMixin


def _uuid_str() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- customer PO
#
# The customer PO ROW is phase 1's ``project_purchase_orders`` (see
# ``app.models.projects``). Phase 2 does not fork it: it hangs versioned documents,
# extracted lines and handwriting cards off that same row, so a PO recorded by hand
# and a PO arriving as a scan are one PO with one number, not two.
#
# These constants describe columns phase 2 adds to that table.

PO_STATUS_DRAFT = "draft"
PO_STATUS_APPROVED = "approved"
PO_STATUS_SUPERSEDED = "superseded"

ANNOTATION_PROPOSED = "proposed"
ANNOTATION_ACCEPTED = "accepted"
ANNOTATION_EDITED = "edited"
ANNOTATION_REJECTED = "rejected"


class ProjectPOVersion(Base, CompanyScopedMixin):
    """One uploaded document. Immutable once confirmed: a revision is a NEW version."""

    __tablename__ = "po_versions"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_po_versions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    purchase_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no = Column(Integer, nullable=False)
    attachment_id = Column(
        UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    source_filename = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)

    # Raw model output, kept verbatim so a later prompt change can be re-scored against it.
    # Extraction runs on the worker, so the row carries its own progress. A version
    # stuck on "queued" reads as a hung system, which is why every exit path writes a
    # terminal state and a sentence a person can act on.
    extraction_state = Column(String(16), nullable=False, server_default="queued")
    extraction_error = Column(Text, nullable=True)
    # Which RQ job is reading this, and when it picked the document up.
    #
    # Both exist for one reason: a work-horse that is KILLED does not run the task's own
    # error handling, so nothing inside the dying process can report the death. Something
    # outside has to, and the only honest way to tell a dead read from a slow one is to ask
    # RQ about the job itself. `extraction_started_at` is the fallback for rows that carry
    # no job id (uploaded before this was recorded), and it is what lets the screen say
    # "4 minutes so far" instead of showing an unbounded spinner. See
    # ``app.services.project_extraction_recovery_service``.
    extraction_job_id = Column(String(64), nullable=True)
    extraction_started_at = Column(DateTime(timezone=False), nullable=True)
    extracted_json = Column(JSONB, nullable=True)
    extraction_model = Column(String(80), nullable=True)
    extraction_tokens_in = Column(Integer, nullable=True)
    extraction_tokens_out = Column(Integer, nullable=True)
    # How long the model actually took. Reading a ten page scan is minutes of waiting,
    # and a person told "took 2m 14s" once stops wondering whether it hung.
    extraction_elapsed_ms = Column(Integer, nullable=True)
    # qty * unit_price == amount, per line. Measured, not assumed.
    arithmetic_passed = Column(Integer, nullable=True)
    arithmetic_total = Column(Integer, nullable=True)
    extracted_total = Column(Numeric(15, 2), nullable=True)

    confirmed_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("purchase_order_id", "version_no", name="uq_project_po_versions_no"),
        {"schema": "projects"},
    )


class ProjectPOLine(Base, CompanyScopedMixin):
    """A printed line, as printed.

    ``stock_code_raw`` is deliberately the customer's own truncated column
    (`SRTWC86`, `2155-BLUE`) because that is what the paper says. The real code is
    recovered from the description into ``resolved_product_id`` (AC-M1b, measured).
    """

    __tablename__ = "po_lines"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_po_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    po_version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.po_versions.id", ondelete="CASCADE"), nullable=False
    )
    line_no = Column(Integer, nullable=False)
    stock_code_raw = Column(String(180), nullable=True)
    description_raw = Column(Text, nullable=True)
    qty = Column(Numeric(15, 4), nullable=True)
    uom_raw = Column(String(40), nullable=True)
    unit_price = Column(Numeric(15, 5), nullable=True)
    amount = Column(Numeric(15, 2), nullable=True)
    # Set by a handwritten strike-through, only ever after a human accepts the card (D11).
    is_cancelled = Column(Boolean, nullable=False, server_default="false", default=False)
    resolved_product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    resolution_source = Column(String(32), nullable=True)  # code | description | map | manual
    arithmetic_ok = Column(Boolean, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("po_version_id", "line_no", name="uq_project_po_lines_no"),
        Index("ix_project_po_lines_version", "po_version_id"),
        {"schema": "projects"},
    )


class ProjectPOAnnotation(Base, CompanyScopedMixin):
    """A handwritten note on the scan, as its own reviewable card (D11).

    ``dedup_key`` is what makes re-uploading an annotated scan safe: the same pencil note
    on a later scan is the SAME annotation and must not be proposed twice.
    """

    __tablename__ = "po_annotations"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_po_annotations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    po_version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.po_versions.id", ondelete="CASCADE"), nullable=False
    )
    dedup_key = Column(String(180), nullable=False)
    page_no = Column(Integer, nullable=True)
    crop_attachment_id = Column(
        UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    raw_text = Column(Text, nullable=True)
    written_date = Column(String(40), nullable=True)
    refers_to_lines = Column(JSONB, nullable=True)
    interpretation = Column(String(64), nullable=True)  # cancel_line | amend_code | amend_description | signature | other
    interpretation_json = Column(JSONB, nullable=True)
    state = Column(String(16), nullable=False, server_default=ANNOTATION_PROPOSED)
    actioned_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actioned_at = Column(DateTime(timezone=False), nullable=True)
    action_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("po_version_id", "dedup_key", name="uq_po_annotations_dedup"),
        {"schema": "projects"},
    )


# ----------------------------------------------------------------- delivery schedule


class DeliverySchedule(Base, CompanyScopedMixin):
    """The customer's delivery programme for one PO.

    ``issuer_party_id`` matters: R2 came from SLG Construction while the PO came from
    Buimaco. The issuer is recorded rather than assumed to be the buyer.
    """

    __tablename__ = "delivery_schedules"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer_party_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.parties.id", ondelete="SET NULL"), nullable=True
    )
    label = Column(String(180), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_delivery_schedules_po", "purchase_order_id"),
        {"schema": "projects"},
    )


class DeliveryScheduleVersion(Base, CompanyScopedMixin):
    """One uploaded schedule document.

    ``po_version_id`` is the version the checksum reconciles AGAINST (finding G1): a
    schedule issued before a handwritten cancellation still reconciles to the PO as it
    stood, and rejecting it would reject a document the customer considers correct.
    """

    __tablename__ = "delivery_schedule_versions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    delivery_schedule_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_schedules.id", ondelete="CASCADE"), nullable=False
    )
    version_no = Column(Integer, nullable=False)
    revision_label = Column(String(80), nullable=True)  # "REVISED 1 - 23/7/2026"
    po_version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.po_versions.id", ondelete="SET NULL"), nullable=True
    )
    attachment_id = Column(
        UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    source_filename = Column(Text, nullable=True)
    schedule_date = Column(Date, nullable=True)
    # sha256 of the uploaded bytes, within this schedule: catches the same document
    # uploaded twice before its own extraction differs anything. Nullable - rows from
    # before this column existed carry no hash and are never matched against.
    content_sha256 = Column(String(64), nullable=True)

    extraction_state = Column(String(16), nullable=False, server_default="queued")
    extraction_error = Column(Text, nullable=True)
    # Same pair, same reason, as ProjectPOVersion above: the schedule task has the identical
    # shape and the identical hole, so it gets the identical fix.
    extraction_job_id = Column(String(64), nullable=True)
    extraction_started_at = Column(DateTime(timezone=False), nullable=True)
    extracted_json = Column(JSONB, nullable=True)
    extraction_model = Column(String(80), nullable=True)
    extraction_elapsed_ms = Column(Integer, nullable=True)
    # Per COLUMN, never per document (schedule spike, 2026-08-02): a wholesale reject
    # would reject nearly every real schedule.
    reconciled_columns = Column(Integer, nullable=True)
    total_columns = Column(Integer, nullable=True)
    reconciliation_json = Column(JSONB, nullable=True)

    confirmed_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at = Column(DateTime(timezone=False), nullable=True)
    # Section 9.7(b): a re-date SUGGESTED from a page's coloured cells plus its own
    # margin note, never applied on its own. One entry per product per note:
    # {product_id, item_code, note_text, page_no, state: proposed|accepted|rejected,
    # decided_by, decided_at, cells: [{phase_id, phase_label, qty, old_date, new_date}]}.
    revision_proposals = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("delivery_schedule_id", "version_no", name="uq_schedule_versions_no"),
        {"schema": "projects"},
    )


class ProjectDeliveryPhase(Base, CompanyScopedMixin):
    """A row of the schedule matrix, promoted to a first class phase on the project (D12).

    Identity is ``(area_group, sequence)`` and NOT the label: the COMMON AREA rows carry no
    label at all, which collapsed three phases into one when matching by label was tried
    (finding G6, confirmed by measurement).
    """

    __tablename__ = "delivery_phases"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_delivery_phases"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False
    )
    area_group = Column(String(80), nullable=False)  # TOWER | COMMON AREA | ...
    sequence = Column(Integer, nullable=False)
    label = Column(String(180), nullable=True)
    delivery_date = Column(Date, nullable=True)
    source_version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_schedule_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "area_group", "sequence", name="uq_delivery_phase_identity"),
        Index("ix_delivery_phases_project", "project_id"),
        {"schema": "projects"},
    )


class DeliveryScheduleCell(Base, CompanyScopedMixin):
    """One quantity in the matrix: this phase, this product, this many."""

    __tablename__ = "delivery_schedule_cells"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_schedule_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    phase_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_phases.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    customer_code_raw = Column(String(180), nullable=True)
    qty = Column(Numeric(15, 4), nullable=False)
    # Section 9.7(a): the fill colour behind this cell, `#rrggbb`, when the document
    # tints it -- geometry from the text layer where it parsed, else the vision
    # pass's own `highlighted` flag rendered with a generic tint. Grey/black/white
    # fills (borders, header bands) are never a highlight and are not stored.
    highlight = Column(String(7), nullable=True)
    # Section 9.7(c): written only when a `revision_proposals` entry covering this
    # cell is ACCEPTED. `project_so_delta_service` reads this ahead of the phase's
    # own date for this product, so an amendment proposes ADVANCE/DELAY per line.
    delivery_date_override = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_schedule_cells_version", "version_id"),
        Index("ix_schedule_cells_phase", "phase_id"),
        {"schema": "projects"},
    )


class CustomerItemCodeMap(Base, CompanyScopedMixin):
    """`BUI-HB-SRTWC8613-RL` means `SRTWC8613-RL`, learned once per customer (AC-E4)."""

    __tablename__ = "customer_item_code_map"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    customer_id = Column(
        UUID(as_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    customer_code = Column(String(180), nullable=False)
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    confirmed_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "customer_id", "customer_code", name="uq_customer_code_map"),
        {"schema": "projects"},
    )


# ------------------------------------------------------------------- sales order draft

SO_STATUS_DRAFT = "draft"
SO_STATUS_BLOCKED = "blocked"
SO_STATUS_READY = "ready"
SO_STATUS_PUBLISHED = "published"
SO_STATUS_AMENDED = "amended"
# Sponsorship only (AC-K3, D28). A giveaway is drafted at price zero and Accounts has to
# attend to it before it reaches AutoCount, so this status GATES the publish rather than
# merely labelling the row. A commercial draft never enters it.
SO_STATUS_AWAITING_COSTING = "awaiting_costing"
#: Adopted from the AutoCount sales-order book rather than authored here
#: (`PLAN-fulfilment-planning-from-autocount-so.md` section 4). A status VALUE and not an
#: `origin` column, because fifteen sites already branch on the `(published, amended)` pair
#: and every one of them needs a verdict on adopted either way: a value costs one sweep and
#: no new column. `published_by` / `published_at` stay NULL on such a row, which is the
#: truth - nobody published it.
SO_STATUS_ADOPTED = "adopted"

#: The statuses a fulfilment-planning record can be in: it has left the building, so there
#: is an AutoCount sales order behind it. A draft is reconciled against nothing.
#: Consolidated here rather than restated per call site, because fifteen copies of one
#: tuple is a latent drift bug (plan section 4).
LIVE_SO_STATUSES = (SO_STATUS_PUBLISHED, SO_STATUS_AMENDED, SO_STATUS_ADOPTED)

#: The live statuses of a document THIS SYSTEM AUTHORED. Named rather than spelled out at
#: each site so "why is adopted not here" is answered by the name: an adopted record was
#: never drafted, never costed, never exported to AutoCount and cannot be amended, so every
#: authoring, worksheet, draft-findings and publish path reads this tuple and not
#: `LIVE_SO_STATUSES`.
AUTHORED_LIVE_STATUSES = (SO_STATUS_PUBLISHED, SO_STATUS_AMENDED)

SEVERITY_HARD = "hard"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"


class ProjectSalesOrder(Base, CompanyScopedMixin):
    """A Sales Order this system authored (D1).

    ``so_id`` points at the CORE `sales_orders` row created on publish, which is what SCM
    reads as committed demand. Everything project-shaped lives here so the core table
    stays ignorant of the module (finding G5).
    """

    __tablename__ = "sales_orders"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_sales_orders"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # NULLABLE since migration 383: an ADOPTED order came out of the AutoCount book and has
    # no project registration. Inventing one would put 605 registrations nobody asked for
    # into the pipeline and collide with ADR-0004 registration exclusivity; asking CS to
    # pick one would be a decision per order for a fact the source document does not carry.
    # An AUTHORED order still always has one.
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="RESTRICT"), nullable=True
    )
    purchase_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    schedule_version_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_schedule_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    so_id = Column(
        UUID(as_uuid=False), ForeignKey("sales_orders.id", ondelete="SET NULL"), nullable=True
    )

    area_group = Column(String(80), nullable=True)
    provisional_ref = Column(String(80), nullable=False)
    autocount_doc_no = Column(String(80), nullable=True)
    status = Column(String(24), nullable=False, server_default=SO_STATUS_DRAFT)

    # A parking route: the customer on the document is a convenience, the project is the
    # fact (D18). Excluded from customer analytics and credit.
    is_pre_order = Column(Boolean, nullable=False, server_default="false", default=False)
    is_sponsorship = Column(Boolean, nullable=False, server_default="false", default=False)
    sponsorship_form_id = Column(
        UUID(as_uuid=False), ForeignKey("purchase_requests.id", ondelete="SET NULL"), nullable=True
    )
    # How the lines were grouped, so the next PO from this customer proposes what CS did (G2).
    # It also says which namespace `area_group` holds: an area name for area/learned/manual/
    # subset, an ISO date for delivery_date, `YYYY-MM` for delivery_month.
    # area | learned | manual | subset | delivery_date | delivery_month
    grouping_origin = Column(String(32), nullable=True)

    total_amount = Column(Numeric(15, 2), nullable=True)
    published_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("company_id", "provisional_ref", name="uq_project_so_provisional_ref"),
        Index("ix_project_so_project", "project_id"),
        Index("ix_project_so_status", "status"),
        # One planning record per CORE sales order (AC-FP10). Partial, because NULL is the
        # normal state for every authored order that has not been published yet. This is
        # what makes a doubly-counted confirmed leg in `scm.committed_v` impossible rather
        # than merely unlikely.
        Index(
            "uq_projects_so_core_order",
            "so_id",
            unique=True,
            postgresql_where=text("so_id IS NOT NULL"),
        ),
        {"schema": "projects"},
    )


class ProjectSalesOrderLine(Base, CompanyScopedMixin):
    """One SO line: a COMPONENT, at its own delivery date.

    The PO says `927 SETS`; this is one of the four component lines that come out of it,
    three of them at 0.00, exactly as the quotation and the real SO are written.
    """

    __tablename__ = "sales_order_lines"
    __audit_skip__ = "ingested line table, 679 to 7,502 rows a day (measured 27 Sep 2026, review B3)"
    __audit_parent__ = "project_sales_order_id"  # history rolls up to the header
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_sales_order_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_sales_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_orders.id", ondelete="CASCADE"), nullable=False
    )
    # The reconciled CORE line (PLAN-scm-front-planning.md 6.1). Unqualified on purpose,
    # so this is `public.sales_order_lines`, not the module table of the same bare name.
    #
    # Nullable, because reconciliation is a later step: the AutoCount upload in stage 1B is
    # what fills it, and a line with no link is simply not confirmable yet. `SET NULL`
    # rather than CASCADE because losing the core line loses the LINK, never the project
    # record of what was committed.
    core_sales_order_line_id = Column(
        UUID(as_uuid=False), ForeignKey("sales_order_lines.id", ondelete="SET NULL"), nullable=True
    )
    line_no = Column(Integer, nullable=False)
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=True
    )
    description = Column(Text, nullable=True)
    qty = Column(Numeric(15, 4), nullable=False)
    uom = Column(String(40), nullable=True)
    unit_price = Column(Numeric(15, 5), nullable=False, server_default="0")
    amount = Column(Numeric(15, 2), nullable=False, server_default="0")
    delivery_date = Column(Date, nullable=True)

    phase_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.delivery_phases.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_po_line_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.po_lines.id", ondelete="SET NULL"), nullable=True
    )
    # Which quotation line this consumed balance from, shown on screen always (G3).
    quotation_line_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.quotation_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Where the stock is coming from, once Eling confirms (D17).
    stock_location = Column(String(80), nullable=True)
    explosion_source = Column(String(32), nullable=True)  # package | quotation | direct
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_project_so_lines_order", "project_sales_order_id"),
        # One Project line per core line: the "unique reconciled link" the atomic SO
        # confirmation is written against. Partial, because NULL is the normal state for
        # every unreconciled line and there is no reason to index them.
        Index(
            "uq_projects_so_line_core_line",
            "core_sales_order_line_id",
            unique=True,
            postgresql_where=text("core_sales_order_line_id IS NOT NULL"),
        ),
        {"schema": "projects"},
    )


class SODraftFinding(Base, CompanyScopedMixin):
    """A cross-check result. Five codes are hard stops; the rest are cleared with a reason (D9).

    Almost every finding names a line, or at least a PO line, and belongs to whichever
    order(s) drafted from it. A few do not: a schedule column for a product not on the PO AT
    ALL, a phase from another project, the whole-document total mismatch. Those name no
    order, so `project_sales_order_id` is null and `purchase_order_id` +
    `schedule_version_id` carry it instead -- one row per (PO, schedule version), not one
    per order the build happened to produce, and not deleted when any one of those orders
    is rebuilt or removed.
    """

    __tablename__ = "so_draft_findings"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_sales_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_orders.id", ondelete="CASCADE"), nullable=True
    )
    # Set only when `project_sales_order_id` is null -- see the class docstring.
    purchase_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.purchase_orders.id", ondelete="CASCADE"),
        nullable=True,
    )
    schedule_version_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.delivery_schedule_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    line_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_order_lines.id", ondelete="CASCADE"),
        nullable=True,
    )
    severity = Column(String(8), nullable=False)  # hard | warn | info
    code = Column(String(64), nullable=False)
    detail = Column(Text, nullable=True)
    detail_json = Column(JSONB, nullable=True)
    acknowledged_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=False), nullable=True)
    acknowledged_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_so_findings_order", "project_sales_order_id"),
        Index("ix_so_findings_severity", "severity"),
        Index("ix_so_findings_batch", "purchase_order_id", "schedule_version_id"),
        {"schema": "projects"},
    )


# ----------------------------------------------------------------------- amendments

AMENDMENT_PROPOSED = "proposed"
AMENDMENT_APPROVED = "approved"
AMENDMENT_PUBLISHED = "published"
AMENDMENT_REJECTED = "rejected"

VERB_ADVANCE = "ADVANCE"
VERB_DELAY = "DELAY"
VERB_QTY_CHANGE = "QTY_CHANGE"
VERB_ADD_LINE = "ADD_LINE"
VERB_REMOVE_LINE = "REMOVE_LINE"
VERB_CANCEL_BALANCE = "CANCEL_BALANCE"
VERB_REPOINT_SO = "REPOINT_SO"
VERB_MODEL_CHANGE = "MODEL_CHANGE"


class OrderChangeNotice(Base, CompanyScopedMixin):
    """The client's only trusted hard gate (D15), auto drafted from the computed delta.

    Required for every amendment to a PUBLISHED SO. Editing an unpublished draft is not an
    amendment and raises none (finding G9).
    """

    __tablename__ = "order_change_notices"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    ocn_number = Column(String(64), nullable=False)
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False
    )
    purchase_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_sales_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_orders.id", ondelete="SET NULL"), nullable=True
    )
    reason = Column(Text, nullable=True)
    source_document_kind = Column(String(32), nullable=True)  # revised_po | revised_schedule | none
    source_document_id = Column(UUID(as_uuid=False), nullable=True)
    change_table_json = Column(JSONB, nullable=True)
    approver_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=False), nullable=True)
    created_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "ocn_number", name="uq_ocn_number"),
        {"schema": "projects"},
    )


class SOAmendment(Base, CompanyScopedMixin):
    """The difference between two document versions, in purchasing's own verbs (D14)."""

    __tablename__ = "so_amendments"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_sales_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_orders.id", ondelete="CASCADE"), nullable=False
    )
    ocn_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.order_change_notices.id", ondelete="SET NULL"), nullable=True
    )
    from_version_kind = Column(String(32), nullable=True)  # po | schedule
    from_version_id = Column(UUID(as_uuid=False), nullable=True)
    to_version_id = Column(UUID(as_uuid=False), nullable=True)
    verb_summary = Column(JSONB, nullable=True)  # {"DELAY": 12, "CANCEL_BALANCE": 1}
    delta_json = Column(JSONB, nullable=True)
    # Per-row accept/decline, keyed by the delta row's `row_key` (its index within
    # `delta_json["rows"]`): {"<row_key>": {"decision": "accepted"|"declined", "reason": str|None}}.
    # A row absent from this map is accepted by default, so an amendment nobody has
    # touched still publishes exactly as it did before this column existed.
    row_decisions = Column(JSONB, nullable=True)
    status = Column(String(16), nullable=False, server_default=AMENDMENT_PROPOSED)
    published_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_so_amendments_order", "project_sales_order_id"),
        {"schema": "projects"},
    )


# NOTE (merge, Stage 1C + Stage 2): `SOSupplyDecision` used to be declared HERE too.
# Both stages needed `projects.so_supply_decisions` and, with neither merged, each wrote
# its own copy of the class and its own DDL. Stage 2's own docstring named the owner
# ("Stage 1C owns the atomic confirmation that writes it; nothing in SCM writes it"), so
# the Stage 1C declaration further down is the one that survives and Stage 2's duplicate
# is gone. Two mappers on one `__tablename__` is an import-time error, not a style
# problem. Stage 2 still only READS the table, through raw SQL in `scm.committed_v`, and
# that view needs `id` / `project_sales_order_id` / `state`, all of which 1C's table has.


# --------------------------------------------------------------------- order inquiry

INQUIRY_RAISED = "raised"
INQUIRY_ACTIONED = "actioned"
INQUIRY_CANCELLED = "cancelled"
#: A raised ORDER/RESERVE_AND_ORDER row tagged onto a specific outstanding supplier PO
#: line (PLAN-demo-followups-19aug-ladder-v2.md section G, "identify which outstanding
#: PO has quantity to fulfil this order inquiry, tag it"). `committed_v`'s confirmed leg
#: counts `state = 'raised'` only, so placing a row is what removes it from the reorder
#: engine's suggestion - the deliberate, evidence-carrying exception to "a PO is never
#: supply" (`on_order_v` still reads `spo_allocations` only). Untagging returns the row
#: to `raised` and clears `po_ref` / `po_line_id`.
INQUIRY_PLACED = "placed"
#: Some of the row's quantity sits on documents and the rest is still demand
#: (`PLAN-scm-cs-planning-uat.md` section 3.I). The middle the links table made
#: expressible: before it, a cascade that could only cover part of a row SPLIT the row, so
#: nine sales-order lines read as eleven instructions. Now the row keeps its full quantity
#: and `scm.committed_v` nets exactly `qty - sum(links.qty)`. Read as "Partly linked".
INQUIRY_PARTLY_LINKED = "partly_linked"
#: The states a row's LINKS decide, in coverage order. `actioned` and `cancelled` are a
#: person's word about the row and are never overwritten by a link change.
INQUIRY_LINK_STATES = (INQUIRY_RAISED, INQUIRY_PARTLY_LINKED, INQUIRY_PLACED)

#: The HANDSHAKE, which is a different question from the supply state beside it
#: (`PLAN-scm-oi-handshake.md` section 2, captain 27 Aug 2026). `state` says where the
#: quantity sits; these say whether purchasing has taken the instruction on.
#:
#: `awaiting` -> `acknowledged` | `rejected`, and an acknowledged row CS then amends
#: becomes `changed` until purchasing acknowledges it again. Nothing links a document to
#: a row before it is acknowledged: links are purchasing's, and a row nobody has read is
#: one CS is still free to change.
ACK_AWAITING = "awaiting"
ACK_ACKNOWLEDGED = "acknowledged"
#: CS amended a row purchasing had ALREADY acknowledged. The links stay, the previous
#: value is carried on the note (part 3's settle-in-place), and purchasing sees the row as
#: a change rather than as something it has already dealt with.
ACK_CHANGED = "changed"
#: Purchasing refused it, with a reason. The row leaves netting and the line goes back to
#: the board undecided, carrying the reason.
ACK_REJECTED = "rejected"
ACK_STATES = (ACK_AWAITING, ACK_ACKNOWLEDGED, ACK_CHANGED, ACK_REJECTED)
#: The two an acknowledgement may be taken on: a row nobody has looked at, and one that
#: has changed since somebody did.
ACK_ACKNOWLEDGEABLE = (ACK_AWAITING, ACK_CHANGED)
#: What a cascade may link. Links are purchasing's word, so an awaiting row gets none
#: (`ProjectOrderInquiryService.auto_place_for_products`).
ACK_LINKABLE = (ACK_ACKNOWLEDGED, ACK_CHANGED)

IV_ORDER = "ORDER"
IV_RESERVE_AND_ORDER = "RESERVE_AND_ORDER"
IV_ADVANCE = "ADVANCE"
IV_DELAY = "DELAY"
IV_CHANGE_SO = "CHANGE_SO"
IV_CANCEL_BALANCE = "CANCEL_BALANCE"
IV_PRE_ORDERED = "PRE_ORDERED_DO_NOT_ORDER"
IV_ALREADY_INBOUND = "ALREADY_INBOUND"
#: A planning-change batch released a line's whole claim: the reserve freed at its own
#: location, and the Buy this line held is no longer bought for this line - it becomes a
#: POOL purchase. An informational change row, exactly like DELAY/ADVANCE, never a fresh
#: purchase of its own (`PLAN-so-book-diff-replanning.md` section 6, captain 19 Aug 2026).
IV_RELEASE = "RELEASE"
#: The ORDER BACK. Two things reach it and they are the same instruction: a borrow left
#: the DONOR location oversold, so the hole it opened is buying work
#: (PLAN-fulfilment-planning-from-autocount-so.md 13.11), and CS writing `ORDER BACK` on
#: the Order Inquiry Form against a quantity owed on a document already on its way
#: (`PLAN-scm-purchasing-uat-journey.md` section 4b, captain 25 Aug 2026).
#:
#: Its own verb rather than `ORDER` for two reasons. The quantity belongs to the donor's
#: location and not to the borrowing line's, so counted as ORDER it would be attributed to
#: the wrong warehouse by `confirmed_unplaced_buy_rows` and cancelled by the Buy-residual
#: rules on re-confirm. And it is the ONE verb whose links may name an `spo_allocations`
#: row as well as a purchase order line: an order back is a shortfall against something
#: already ordered or already shipped, which a fresh ORDER never is.
IV_ORDER_BACK = "ORDER_BACK"
#: The name this verb carried before migration 421 renamed both the constant and the
#: stored value. Bound to the new value rather than deleted, so a reader written against
#: the old spelling keeps meaning the same thing while it is renamed; nothing writes it.
IV_BORROW_SHORTFALL = IV_ORDER_BACK


class OrderInquiry(Base, CompanyScopedMixin):
    """What purchasing is told to do, derived from an SO or its amendment (D16).

    Never a second source of demand: committed quantity stays on `sales_order_lines`.
    """

    __tablename__ = "order_inquiries"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_order_inquiries"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # What a person calls this inquiry: `OI-000001`, the same shape and the same series
    # discipline as `ProjectSalesOrder.provisional_ref`. Nothing in this product may show a
    # UUID, and "the inquiry on SO414033" stops being an answer the moment an amendment
    # raises the second one on that order. Unique per COMPANY, like `provisional_ref` and
    # for the same reason: the read that mints it is company-scoped, so each company has
    # its own series.
    inquiry_no = Column(String(20), nullable=False)
    # The number this header carried before the monthly renumber (`523_oi_monthly_no_
    # raises`, R3) - never re-used going forward, kept only so a number already quoted
    # in an old email still finds its OI (search matches it too). NULL for every header
    # born after that migration.
    legacy_inquiry_no = Column(String(20), nullable=True)
    project_sales_order_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_orders.id", ondelete="CASCADE"), nullable=False
    )
    amendment_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.so_amendments.id", ondelete="SET NULL"), nullable=True
    )
    state = Column(String(16), nullable=False, server_default=INQUIRY_RAISED)
    # The FIRST raise (R5) - written once, at insert, and never re-stamped by a
    # reconfirm since S1 (`523_oi_monthly_no_raises`). Every later raise/reconfirm is
    # its own row on `OrderInquiryRaise` instead; these two columns stay the header's
    # fixed "born on" fact so a number quoted from month N still reads as month N even
    # after a dozen reconfirms.
    raised_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    raised_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "inquiry_no", name="uq_project_order_inquiry_no"),
        Index("ix_project_order_inquiries_order", "project_sales_order_id"),
        # One inquiry per publish, enforced by the database rather than by remembering
        # to check: republishing must not double what purchasing is told to buy.
        # Postgres treats NULLs as distinct, so the sales-order case needs its own
        # predicate rather than a plain unique on the pair.
        Index(
            "uq_project_order_inquiry_per_sales_order",
            "project_sales_order_id",
            unique=True,
            postgresql_where=text("amendment_id IS NULL"),
        ),
        Index(
            "uq_project_order_inquiry_per_amendment",
            "amendment_id",
            unique=True,
            postgresql_where=text("amendment_id IS NOT NULL"),
        ),
        {"schema": "projects"},
    )


#: `OI-2609-0001` (R3, `PLAN-oi-header-list-detail.md`): the MONTH of the header's own
#: first raise, then a four-digit series that starts over every month. Superseded the
#: flat `OI-000001` (six digits, no month) migration `523_oi_monthly_no_raises`
#: renumbers - the old value survives on `legacy_inquiry_no`.
INQUIRY_NO_PREFIX = "OI-"
INQUIRY_NO_DIGITS = 4
#: Every date this file numbers by is Asia/Kuala_Lumpur (R3): a header raised at
#: 2026-09-30 17:30 UTC is already 1 Oct there, and must number under October.
_MY_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def _inquiry_no_prefix(ref_date) -> str:
    """`OI-2609-` for any `date`/`datetime` in September 2026."""
    return f"{INQUIRY_NO_PREFIX}{ref_date:%y%m}-"


def next_inquiry_no(bind, company_id, ref_date) -> str:
    """The next inquiry number for one company, for one MONTH: the highest already
    issued in that month, plus one.

    HIGHEST plus one, never the count: a number that has been issued is in somebody's
    email, so a departed inquiry must not hand it to a different one. Scoped to the
    month's own prefix rather than the whole series, so October opens at `-0001` no
    matter how many September numbers already exist (R3).

    Per COMPANY, matching `uq_project_order_inquiry_no` and `provisional_ref` one table
    over. `bind` is whatever can execute a statement - the flush's own Connection when this
    runs from the stamp below, a Session when a caller asks directly - so the number is
    minted by ONE piece of code however it is reached.
    """
    prefix = _inquiry_no_prefix(ref_date)
    table = OrderInquiry.__table__
    latest = bind.execute(
        select(table.c.inquiry_no)
        .where(table.c.company_id == company_id,
               table.c.inquiry_no.like(f"{prefix}%"))
        .order_by(func.length(table.c.inquiry_no).desc(), table.c.inquiry_no.desc())
        .limit(1)
    ).scalar()
    tail = (latest or "")[len(prefix):]
    highest = int(tail) if tail.isdigit() else 0
    return f"{prefix}{highest + 1:0{INQUIRY_NO_DIGITS}d}"


@event.listens_for(OrderInquiry, "before_insert")
def _stamp_inquiry_no(_mapper, connection, target) -> None:
    """Every inquiry gets its number, whoever created it.

    A listener rather than a line in each writer, for the same reason the company stamp is
    one (`app.models.base`): there are two creation sites today and the invariant is "no
    inquiry exists without a number", which a rule each new writer has to remember is not.

    Minted off the flush's own connection, so it sees the rows this same flush has already
    inserted - two inquiries raised in one confirmation take consecutive numbers - and
    cannot re-enter the flush the way a session query would. A number already set (a
    migration backfill, a test pinning one) is left exactly as it is.

    The MONTH the number opens under is the header's own `raised_at` (R3, converted to
    its Asia/Kuala_Lumpur date) when the caller already set one, else this instant - the
    same "now" the column's own `server_default=func.now()` would otherwise write, so
    the two never disagree about when this header was born.
    """
    if getattr(target, "inquiry_no", None):
        return
    raised_at = getattr(target, "raised_at", None)
    ref_moment = raised_at if raised_at is not None else datetime.utcnow()
    ref_date = ref_moment.replace(tzinfo=timezone.utc).astimezone(_MY_TZ).date()
    target.inquiry_no = next_inquiry_no(connection, target.company_id, ref_date)


#: The two things `OrderInquiryRaise.kind` can be (R5): the header's FIRST raise, and
#: every later reconfirm. Written by `ProjectOrderInquiryService.ensure_inquiry` (create
#: = `raised`, reuse = `reconfirmed`) and by the sheet import's own `_inquiry`, the same
#: shape - never anywhere else, so "one row per confirmation" stays true everywhere a
#: header can be raised or reconfirmed.
OI_RAISE_RAISED = "raised"
OI_RAISE_RECONFIRMED = "reconfirmed"


class OrderInquiryRaise(Base, CompanyScopedMixin):
    """One entry of an order inquiry's raise history (R5, S1
    `PLAN-oi-header-list-detail.md`): who raised or reconfirmed it, and when.

    `OrderInquiry.raised_at`/`raised_by` stay the header's fixed "born on" fact once
    this table exists - the FIRST row here always agrees with them, and every later
    reconfirm adds a row instead of overwriting those two columns, so a number quoted
    in month N still reads as month N no matter how many times CS reconfirms after.

    Its own table rather than `audit_logs` (measured, plan "Design"): audit is off for
    this entity, carries no actor on the batch/import path, and a two-row backfill is
    all migration `523_oi_monthly_no_raises` needs to seed here, while an audit read
    would have nothing to backfill FROM at all.
    """

    __tablename__ = "order_inquiry_raises"
    __audit_skip__ = "append-only raise log, itself a trail"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    order_inquiry_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiries.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind = Column(String(16), nullable=False)
    raised_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    raised_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_order_inquiry_raises_inquiry", "order_inquiry_id"),
        {"schema": "projects"},
    )


class OrderInquiryRow(Base, CompanyScopedMixin):
    """One instruction.

    ``covered_by`` is why an ORDER row was NOT emitted (AC-I3a, FIFO): the pre-order or
    the inbound SPO that already covers this quantity.

    ``note`` is the other half of an amendment instruction. A verb on its own is not
    actionable: DELAY without the date it moved from, or CHANGE SO without the sales
    order it moves to, sends purchasing back to a person to ask. Coverage and
    explanation are separate fields because they answer separate questions.
    """

    __tablename__ = "order_inquiry_rows"
    __audit_skip__ = "line table (service writes log_audit explicitly), 451 to 4,601 rows a day (measured 27 Sep 2026, review B3)"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_order_inquiry_rows"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    order_inquiry_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.order_inquiries.id", ondelete="CASCADE"), nullable=False
    )
    so_line_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.sales_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_code = Column(String(120), nullable=True)
    qty = Column(Numeric(15, 4), nullable=False)
    delivery_date = Column(Date, nullable=True)
    stock_location = Column(String(80), nullable=True)
    verb = Column(String(32), nullable=False)
    spo_ref = Column(String(80), nullable=True)
    #: The document CS NAMED for an order back - "202604-S0083", "SPO-2026/08-0061" - as
    #: they spelled it (`PLAN-scm-purchasing-uat-journey.md` section 4b). Not a link: a
    #: citation is what the auto-link walk tries FIRST, before any location tier or date,
    #: and a document this system does not hold is recorded rather than refused (AC-J2).
    #: Its own column rather than `spo_ref` or `po_ref`, because those two are now the
    #: DERIVED display of the first link and a citation is a different fact from a
    #: placement - overloading either would make "where is this row linked" unanswerable.
    cited_document = Column(String(80), nullable=True)
    # The confirmed supply decision this Buy residual came from (front planning 6.3).
    # Nullable, because amendment exception rows and every row raised before Stage 1C
    # belong to no decision. SET NULL rather than CASCADE: a decision that is deleted
    # must not take purchasing's ledger with it.
    #
    # Stage 2 reads it: SCM counts Project demand as the qty on rows pointing at an ACTIVE
    # decision, which is what stops a superseded revision's Buy being bought again.
    supply_decision_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.so_supply_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    covered_by = Column(Text, nullable=True)
    # The FIRST link's document and line, kept as a one-word display for the readers that
    # print a single PO number (`PLAN-scm-cs-planning-uat.md` section 3.I: "`po_ref` /
    # `po_line_id` / `spo_ref` on the row become derived display (first link) and stop
    # being written"). The TRUTH is `projects.order_inquiry_links`, one row per placement:
    # a row may sit on two lines of one purchase order and on an SPO allocation at the
    # same time, and a single column cannot say that. Nothing writes these two by hand any
    # more - `refresh_link_state` derives them from the links whenever the links change.
    # SET NULL, not CASCADE: a purchase order line getting deleted must not take the
    # instruction's history with it.
    po_ref = Column(String(80), nullable=True)
    po_line_id = Column(
        UUID(as_uuid=False), ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    note = Column(Text, nullable=True)
    # A planning-change advance/delay whose fresh proposal pool covers the line keeps the
    # pool take (the composition still holds Reserve) rather than relabelling it onto Buy,
    # so a PLACED row this line already holds is REDIRECTED instead of cancelled: it stays
    # tagged to its real PO, its `qty` is untouched, but it now replenishes the pool that
    # was just drawn down for this line rather than serving this line's own need (the
    # captain, 21 Aug 2026 - "it takes the outstanding PO to BRW"). `True` is what tells
    # `refresh_for_decision`'s placed-quantity netting, and the worklist's "Taken from PO"
    # aggregate, to stop counting it for THIS line - it is real placed quantity, just not
    # this line's anymore.
    redirected_to_pool = Column(Boolean, nullable=False, server_default="false", default=False)
    state = Column(String(16), nullable=False, server_default=INQUIRY_RAISED)
    # "Did purchasing act on this" (AC-I7) is only half answered by a state. A state
    # with nobody's name and no time on it cannot say when, or who to ask.
    actioned_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actioned_at = Column(DateTime(timezone=False), nullable=True)
    #: The handshake (`PLAN-scm-oi-handshake.md`), beside the supply state above and never
    #: merged with it: `state` says where the quantity sits, this says whether purchasing
    #: has taken the instruction on. Defaulted rather than nullable - a NULL would be a
    #: second spelling of `awaiting` that every reader would have to know about.
    ack_state = Column(String(16), nullable=False, server_default=ACK_AWAITING, default=ACK_AWAITING)
    acknowledged_by = Column(
        String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at = Column(DateTime(timezone=False), nullable=True)
    rejected_by = Column(
        String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at = Column(DateTime(timezone=False), nullable=True)
    #: Required when rejecting. A refusal with no reason sends CS back to a person to ask,
    #: which is the thing the board cell's "Rejected by X: Y" exists to stop.
    rejected_reason = Column(Text, nullable=True)
    #: When CS last amended a row purchasing had already acknowledged. Its own column, not
    #: a reading of some general updated-at: every other write to the row would move that,
    #: and "what has changed since I looked" is the only question this answers.
    changed_at = Column(DateTime(timezone=False), nullable=True)
    #: What the row said BEFORE the last settle-in-place restated it: the two facts the
    #: Was / Now table prints. Columns rather than a reading of the note beside them - the
    #: note is prose for a person ("Was 10, no previous delivery date"), and a screen that
    #: had to parse it back into a quantity read the sentence's own comma as part of the
    #: number. Written by `_settle_row_in_place`, the one writer that knows what the row
    #: was, and overwritten by each further settle: the question is "what changed since I
    #: looked", not "every value this row ever held".
    previous_qty = Column(Numeric(15, 4), nullable=True)
    previous_delivery_date = Column(Date, nullable=True)
    #: OI-PRODUCT-FOLLOW (`PLAN-oi-product-follow-2oct.md` R1): the item code the row
    #: carried before the last settle-in-place moved it onto the SO line's new product,
    #: the "was X" beside the new code. Same writer and same overwrite rule as the two
    #: columns above.
    previous_item_code = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    #: PLAN-scm-supplied-with-companions.md section 3.2. DERIVED, never typed - the one
    #: writer is `ProjectOrderInquiryService.derive_bundles`. How much of THIS row's
    #: quantity rides inside another item's own line rather than needing its own PO line.
    #: Never exceeds `qty - linked` (a link a person already made stays a link).
    bundled_qty = Column(
        Numeric(15, 4), nullable=False, default=0, server_default=text("0")
    )
    #: The ANCHOR row - the first host row the rule matched, display-only ("Included
    #: with CKS1050"). SET NULL: the anchor row being cancelled or superseded must not
    #: take this row down with it, only un-bundle it on the next `derive_bundles` pass.
    bundled_with_row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_rows.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_project_order_inquiry_rows_inquiry", "order_inquiry_id"),
        Index("ix_project_order_inquiry_rows_ack_state", "ack_state"),
        Index("ix_project_order_inquiry_rows_state", "state"),
        Index("ix_project_order_inquiry_rows_po_line", "po_line_id"),
        # Stage 1C's name, because 1C's `374_so_supply_decisions` is the migration that
        # creates this index; Stage 2 proposed `..._supply_decision` for the same column
        # and its duplicate DDL is dropped with the duplicate model above.
        Index("ix_project_order_inquiry_rows_decision", "supply_decision_id"),
        Index("ix_project_order_inquiry_rows_bundled_with", "bundled_with_row_id"),
        {"schema": "projects"},
    )


class OrderInquiryLink(Base, CompanyScopedMixin):
    """One placement: part of an order inquiry row's quantity, on ONE document line.

    The captain, 25 August 2026, walking SO414285: "1 line here should correspond to 1
    line in sales order, so 1 line can be placed by multiple PO and SPO". Before this
    table `order_inquiry_rows` held exactly one `po_line_id`, so a cascade that needed two
    purchase-order lines SPLIT the row - M310-CR-PJ's 8 became 5 + 3, MSK11C's 67 became
    10 + 57, and nine sales-order lines read as eleven instructions on a screen whose whole
    job is to say what to buy. The row now keeps its full quantity and carries links.

    **Exactly one of the two targets is set**, held by a CHECK constraint rather than by
    remembering: `po_line_id` for a purchase-order line, `spo_allocation_id` for a shipping
    order (since migration 420 that is where an SPO lives). Which one is legal depends on
    the ROW's verb, not on this table: only an `ORDER_BACK` row may name an allocation
    (`PLAN-scm-purchasing-uat-journey.md` section 4b), because an order back is a shortfall
    against something already ordered while a fresh ORDER is a new purchase. That rule
    lives in the service, where the verb is readable.

    **`qty` is positive and the sum across a row never exceeds the row's own quantity.**
    The service holds the second half; the first is a CHECK, because a zero or negative
    link is not a smaller placement, it is a row that should not exist.

    **CASCADE on the row, SET NULL on both targets.** Deleting the instruction takes its
    placements with it - they mean nothing without it. Deleting a purchase order line does
    NOT: the link keeps its quantity and its `document` display, so "this was on
    202607-S0105" survives the line being re-imported under a different id, exactly as
    `order_inquiry_rows.po_line_id` already did.

    `claim_id` points at the `scm.order_link_claim` this placement wrote, so an unlink can
    take down the evidence it put up and only that. Nullable: a claim is best-effort audit
    (the identity it keys on may already belong to another feed), and a link with no claim
    is still a link.
    """

    __tablename__ = "order_inquiry_links"
    __audit_skip__ = "link table, 230 to 2,351 rows a day (measured 27 Sep 2026, review B3)"
    #: Born after migration 354, so no pre-move name constrains it; it takes the `project_`
    #: prefix its siblings carry (`tests/test_projects_audit_entity_types.py`), because
    #: `audit_log.entity_type` is one flat namespace with no schema in it and a bare
    #: `order_inquiry_links` is a name another module could later claim.
    __audit_entity_type__ = "project_order_inquiry_links"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_rows.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("purchase_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    spo_allocation_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "spo_allocations.id",
            ondelete="SET NULL",
            name="fk_order_inquiry_links_spo_allocation",
        ),
        nullable=True,
    )
    #: The document number as it was when the link was made - `202607-S0105`,
    #: `SPO-2026/08-0061`. Denormalised on purpose: `ON DELETE SET NULL` on both targets
    #: means the id can go, and a link that can no longer say WHICH document it was on is
    #: not evidence of anything.
    document = Column(String(80), nullable=True)
    qty = Column(Numeric(15, 4), nullable=False)
    linked_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    linked_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    #: Written by the cascade rather than by a person clicking. The trigger that made it is
    #: on the ROW's note, which is where "why is this linked" was already answered.
    auto = Column(Boolean, nullable=False, server_default="false", default=False)
    claim_id = Column(
        UUID(as_uuid=False),
        ForeignKey("scm.order_link_claim.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    #: PLAN-oi-request-cs-reserve.md 3.1: the THIRD target a link may name - a row of an
    #: `OrderInquiryReserveRequest` Eling confirmed.
    #:
    #: `CASCADE`, NOT `SET NULL` (B2, security review round 2 - the original `SET NULL`
    #: was wrong, not merely unsafe-by-omission: `__table_args__` below requires EXACTLY
    #: ONE of the three targets set at all times, so setting only THIS one null while a
    #: reserve link's other two are already null leaves the row satisfying none of them,
    #: which the CHECK rejects outright). Deleting the request row this link was made
    #: against removes the placement WITH it - there is no "keep the link, forget which
    #: request confirmed it" reading the CHECK would even allow, unlike a PO/SPO link,
    #: whose OTHER two targets stay null while its own document is merely re-imported.
    reserve_request_row_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "projects.order_inquiry_reserve_request_rows.id",
            ondelete="CASCADE",
            name="fk_order_inquiry_links_reserve_request_row",
        ),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(po_line_id IS NOT NULL)::int + (spo_allocation_id IS NOT NULL)::int"
            " + (reserve_request_row_id IS NOT NULL)::int = 1",
            name="ck_order_inquiry_links_one_target",
        ),
        CheckConstraint("qty > 0", name="ck_order_inquiry_links_qty_positive"),
        Index("ix_order_inquiry_links_row", "row_id"),
        Index("ix_order_inquiry_links_po_line", "po_line_id"),
        Index("ix_order_inquiry_links_spo_allocation", "spo_allocation_id"),
        Index("ix_order_inquiry_links_reserve_request_row", "reserve_request_row_id"),
        # SF-9 (security review, `oirs_0002_reserve_round2` amended): the lost-update
        # backstop for `commit_request` - at most one link may ever name a given reserve
        # request row, so a caller that reaches the flush on a stale read (the
        # `.with_for_update()` lock in the service is the primary defence) hits this
        # constraint and 409s instead of writing a second link. Partial: the CHECK
        # above already requires exactly one target per link, so every non-reserve
        # link leaves this column NULL and must stay out of the unique set.
        Index(
            "uq_order_inquiry_links_reserve_request_row",
            "reserve_request_row_id",
            unique=True,
            postgresql_where=text("reserve_request_row_id IS NOT NULL"),
        ),
        {"schema": "projects"},
    )


class OrderInquirySuggestedLink(Base, CompanyScopedMixin):
    """A guess the cascade walk made, never a placement (`PLAN-oi-links-autocount-truth-
    24sep.md` section 3.3, the owner's ruling on issue #1215: "the suggested link
    shouldn't be counted as real link").

    Same one-target CHECK shape as `OrderInquiryLink` above, minus the reserve target (a
    CS reserve is always a person's act, never a guess) - and minus `claim_id` /
    `linked_by`: a suggestion holds no `scm.order_link_claim` and nobody's name is on it.
    CASCADE on BOTH the row and the target, unlike a real link's `SET NULL` on the
    target: a real link keeps its display once its document is gone because it is
    evidence of something that happened, while a suggestion of a line that no longer
    exists means nothing and should simply vanish.

    Written and replaced only by `ProjectOrderInquiryService._write_suggested_links`,
    one pass at a time, and trimmed by `_write_link` (since `e5e3dd70`; `place_on_po_
    allocations` calls it, but it is not the trim's own choke point) the moment a real
    link lands on the same target (AC-LT-15). `qty` follows the row's own priority through
    the walk, so `suggested_at` and the row's `delivery_date` (read via the row, not
    duplicated here) are what a later trim reads to decide which suggestion goes first.
    """

    __tablename__ = "order_inquiry_suggested_links"
    __audit_entity_type__ = "project_order_inquiry_suggested_links"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_rows.id", ondelete="CASCADE"),
        nullable=False,
    )
    po_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("purchase_order_lines.id", ondelete="CASCADE"),
        nullable=True,
    )
    spo_allocation_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "spo_allocations.id",
            ondelete="CASCADE",
            name="fk_order_inquiry_suggested_links_spo_allocation",
        ),
        nullable=True,
    )
    #: Denormalised the same way `OrderInquiryLink.document` is - see that column's own
    #: comment. Here it is display only: nothing reads it to decide coverage.
    document = Column(String(80), nullable=True)
    qty = Column(Numeric(15, 4), nullable=False)
    #: Why the walk offered this - `raise`, `worklist`, `link_now`, `acknowledge`,
    #: `po_confirm`, `decision_confirm` - the same trigger vocabulary a real link's
    #: `auto` note already carries, so "why is this suggested" is answerable the same way.
    trigger = Column(String(32), nullable=True)
    suggested_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(po_line_id IS NOT NULL)::int + (spo_allocation_id IS NOT NULL)::int = 1",
            name="ck_order_inquiry_suggested_links_one_target",
        ),
        CheckConstraint("qty > 0", name="ck_order_inquiry_suggested_links_qty_positive"),
        Index("ix_order_inquiry_suggested_links_row", "row_id"),
        Index("ix_order_inquiry_suggested_links_po_line", "po_line_id"),
        Index("ix_order_inquiry_suggested_links_spo_allocation", "spo_allocation_id"),
        {"schema": "projects"},
    )


#: PLAN-oi-request-cs-reserve.md (R1-R11): purchasing asks CS to cover part of a row from
#: own or pool stock before buying the balance. Two tables: the REQUEST (one per ask,
#: `OI-2609-0678 request #2`, addressed by ordinal within the inquiry rather than its own
#: number - R5 repeats the cycle on the remaining qty rather than amending) and its ROWS
#: (one per order-inquiry row asked, `OrderInquiryReserveRequestRow` below).
RESERVE_REQUESTED = "requested"
RESERVE_RESERVED = "reserved"
RESERVE_CANCELLED = "cancelled"


class OrderInquiryReserveRequest(Base, CompanyScopedMixin):
    """One "request CS to reserve" ask, covering one or more order inquiry rows.

    `ordinal` addresses it within the inquiry (`OI-2609-0678 request #2`) - a reserve
    request earns no number of its own (section 2, "Reserve requests get NO number").
    `state` walks `requested` -> `reserved` (Eling's Confirm, 3.3) or `requested` ->
    `cancelled` (the requester's own Cancel, or anyone holding the reserve permission,
    3.2); once `reserved` it stays history even if every link it wrote is later unlinked
    (R5's "no amend after confirm; repeat instead", AC-RS-14).
    """

    __tablename__ = "order_inquiry_reserve_requests"
    __audit_entity_type__ = "project_order_inquiry_reserve_requests"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    order_inquiry_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiries.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal = Column(Integer, nullable=False)
    state = Column(String(16), nullable=False, server_default=RESERVE_REQUESTED)
    requested_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    note = Column(Text, nullable=True)
    reserved_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reserved_at = Column(DateTime(timezone=False), nullable=True)
    cancelled_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"state IN ('{RESERVE_REQUESTED}', '{RESERVE_RESERVED}', '{RESERVE_CANCELLED}')",
            name="ck_order_inquiry_reserve_requests_state",
        ),
        UniqueConstraint(
            "order_inquiry_id", "ordinal", name="uq_order_inquiry_reserve_requests_ordinal"
        ),
        Index("ix_order_inquiry_reserve_requests_inquiry", "order_inquiry_id"),
        {"schema": "projects"},
    )


class OrderInquiryReserveRequestRow(Base, CompanyScopedMixin):
    """One order inquiry row named on a reserve request.

    `warehouse_id` defaults to the row's own pool (R3) at request time and is Eling's own
    to change at reserve time (3.3) - stored here, never re-derived, so the reserved mail
    and the link's own `document` always print what she actually chose. `qty_reserved` is
    null while the request is open; the moment Eling confirms it holds the answer for
    every row of the request in one call (3.3's all-or-nothing), including a genuine `0`
    with its required `reason`.
    """

    __tablename__ = "order_inquiry_reserve_request_rows"
    __audit_entity_type__ = "project_order_inquiry_reserve_request_rows"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    request_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_reserve_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_rows.id", ondelete="CASCADE"),
        nullable=False,
    )
    qty_requested = Column(Numeric(15, 4), nullable=False)
    warehouse_id = Column(
        UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    qty_reserved = Column(Numeric(15, 4), nullable=True)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("qty_requested > 0", name="ck_order_inquiry_reserve_rows_qty_positive"),
        UniqueConstraint(
            "request_id", "row_id", name="uq_order_inquiry_reserve_request_rows_row"
        ),
        Index("ix_order_inquiry_reserve_request_rows_request", "request_id"),
        Index("ix_order_inquiry_reserve_request_rows_row", "row_id"),
        {"schema": "projects"},
    )


#: PLAN-oi-request-cs-reserve.md section 6c, F3: one history row per reserve/unreserve
#: on a request row. `requested` / `cancelled` are never rows here - they derive straight
#: off `OrderInquiryReserveRequest` (`requested_at`/`requested_by`, `cancelled_at`/
#: `cancelled_by`), which is already the one copy of that fact.
RESERVE_EVENT_RESERVED = "reserved"
RESERVE_EVENT_UNRESERVED = "unreserved"


class OrderInquiryReserveEvent(Base, CompanyScopedMixin):
    """One reserve or unreserve on a request row - the dialog's own History tab (F3).

    `reserve_request_row_id` CASCADEs off its own parent row, so the history a request
    row's `reserve/unreserve` calls wrote goes with it rather than orphaning; a reserve
    LINK is a separate, live fact (`OrderInquiryLink.reserve_request_row_id`,
    `SET NULL`) - this table is the append-only audit trail beside it, never the source
    of the link's own quantity.
    """

    __tablename__ = "order_inquiry_reserve_events"
    __audit_skip__ = "reserve event log, itself a trail"
    __audit_entity_type__ = "project_order_inquiry_reserve_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    reserve_request_row_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.order_inquiry_reserve_request_rows.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind = Column(String(16), nullable=False)
    qty = Column(Numeric(15, 4), nullable=False)
    warehouse_id = Column(
        UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    note = Column(Text, nullable=True)
    actor_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"kind IN ('{RESERVE_EVENT_RESERVED}', '{RESERVE_EVENT_UNRESERVED}')",
            name="ck_order_inquiry_reserve_events_kind",
        ),
        # 6e.4 (security N3): `>= 0` - "Reserve 0" writes a `reserved` event of 0 so
        # History shows the decision (oirs_0004_reserve_event_zero).
        CheckConstraint("qty >= 0", name="ck_order_inquiry_reserve_events_qty_positive"),
        Index("ix_order_inquiry_reserve_events_row", "reserve_request_row_id"),
        {"schema": "projects"},
    )


# ------------------------------------------------------------------------ allocation

ALLOC_SOURCE_BRW = "brw"
ALLOC_SOURCE_OWN = "own"
ALLOC_SOURCE_OTHER_PROJECT = "other_project"
ALLOC_SOURCE_ORDER = "order"
#: Free stock at a location outside the Reserve pool (front planning 6.1). Borrow like
#: `other_project`, but there is no donor project to ask, so it carries no claim row.
#: A model constant, not a database enum: `source_type` is a plain String(16).
ALLOC_SOURCE_OTHER_LOCATION = "other_location"
#: Ladder v2 (`PLAN-demo-followups-19aug-ladder-v2.md` section E rung 3): positive,
#: uncommitted stock at a SIBLING location of this line's ownership group, at another
#: site. Kind `reserve` like `own`/`brw` - it is free stock, nobody's to ask - but never
#: this line's OWN location (rule 7: the own-location Reserve rung is gone) and never the
#: shared site pool (that is `brw`), so it carries its own source so a report can tell
#: "reserved at home" from "taken from a sibling location" apart.
ALLOC_SOURCE_GROUP_TAKE = "group_take"

#: UI mapping, stated once: `own`/`brw`/`group_take` are Reserve, `other_project`/
#: `other_location` are Borrow, `order` is Buy.
ALLOC_RESERVE_SOURCES = (ALLOC_SOURCE_OWN, ALLOC_SOURCE_BRW, ALLOC_SOURCE_GROUP_TAKE)
ALLOC_BORROW_SOURCES = (ALLOC_SOURCE_OTHER_PROJECT, ALLOC_SOURCE_OTHER_LOCATION)

CLAIM_REQUESTED = "requested"
CLAIM_ACCEPTED = "accepted"
CLAIM_REFUSED = "refused"

# ------------------------------------------------------- the SO supply decision (1C)

DECISION_ACTIVE = "active"
DECISION_SUPERSEDED = "superseded"
DECISION_CHALLENGED = "challenged"


class SOSupplyDecision(Base, CompanyScopedMixin):
    """One atomic promise about a Project SO (PLAN-scm-front-planning.md 3.1, 6.2).

    Confirmation is at SO level and never per line: there is one active revision per order
    and no line carries a workflow state of its own. What a revision COVERS is the set of
    lines the planner confirmed, which since
    ``PLAN-fulfilment-planning-from-autocount-so.md`` 13.4 may be a SUBSET of the order's
    lines - the planner decides the lines they are sure about and deliberately leaves the
    rest undecided, so that demand keeps flowing to reorder planning. A stale line among
    the chosen ones still rolls back the lot, and deciding more lines later is a new
    revision covering the union, which is what revisions already did.

    A line is covered iff ``line_snapshots`` holds an object for it. That JSONB is
    therefore load-bearing rather than display evidence: ``scm.committed_v`` reads
    ``core_line_id`` out of it to decide which sales-order lines have left the sheet leg.

    ``line_snapshots`` freezes what was decided, in the words it was decided in: line
    number, the Project and core line ids, product, location, required date, open quantity,
    each component's quantity AND the deterministic reason string that produced it
    (section 3.2), the suggestion basis, the lifecycle warning, and the reasons CS typed.
    The normalized `so_line_allocations` rows remain the components; this is the header
    that groups them and the evidence that survives a later fact change.

    Two uniqueness rules, both in the database rather than in a remembered check: a partial
    unique index makes ONE active revision per Project SO (so the second racer's insert
    fails and it loses cleanly instead of double-claiming stock), and `(order, revision_no)`
    makes the revision chain a chain.
    """

    __tablename__ = "so_supply_decisions"
    __audit_entity_type__ = "project_so_supply_decisions"
    __audit_track__ = True
    # `undo_journal` deliberately excluded (review round, board-undo-last-confirm): it
    # is a replay script, not a fact CS or purchasing reads, and it can be large - the
    # audit trail names WHAT happened, never carries the raw journal that reverses it.
    __audit_columns__ = [
        "project_sales_order_id",
        "revision_no",
        "state",
        "source_revision",
        "line_snapshots",
        "confirmed_by",
        "confirmed_at",
        "suspected_system_issue",
        "supersedes_id",
        "superseded_at",
        "superseded_reason",
    ]

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_sales_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sales_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_no = Column(Integer, nullable=False)
    state = Column(String(16), nullable=False, server_default=DECISION_ACTIVE)
    #: The order's status and `updated_at` at confirmation, as text. Display evidence
    #: only: what the revision was decided against, in a form a person can read.
    source_revision = Column(String(120), nullable=True)
    line_snapshots = Column(JSONB, nullable=False)

    confirmed_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at = Column(DateTime(timezone=False), nullable=True)
    #: Any line of this revision was flagged "this might be a system problem" (R10,
    #: migration 439). The flag itself is per LINE, in `line_snapshots` beside its
    #: `amend_reason`, which is where every other per-line word of a decision is frozen;
    #: this is the revision's own answer to "was anything on it flagged", so a doubt can be
    #: found in SQL without walking a JSONB array.
    suspected_system_issue = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    supersedes_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.so_supply_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_at = Column(DateTime(timezone=False), nullable=True)
    superseded_reason = Column(Text, nullable=True)

    #: What THIS revision's own Confirm wrote, for `undo_last_confirm` to replay
    #: backwards (`PLAN-board-undo-last-confirm.md`, migration `undo_0001`). NULL means
    #: unjournalled - minted outside the two board confirm routes (`uncover_lines`, a
    #: pre-lane revision) - and therefore not undoable. `app.services.
    #: project_supply_undo_service.UndoJournal` writes it; nothing else does.
    #:
    #: `none_as_null=True` (hotfix, undo_0003): without it, SQLAlchemy's JSON type
    #: serialises a Python `None` VALUE as the JSON literal `null`, not a SQL NULL -
    #: the column itself is never NULL, it holds a JSON scalar - and
    #: `_journalled_decision_clause`'s `isnot(None)` guard does not see that, so
    #: Postgres's `jsonb_array_length` throws on it. With `none_as_null=True`, writing
    #: Python `None` through this type (ORM or Core `.values(undo_journal=None)`)
    #: always produces a real SQL NULL.
    undo_journal = Column(JSONB(none_as_null=True), nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_so_supply_decisions_order", "project_sales_order_id"),
        Index(
            "uq_so_supply_decisions_active",
            "project_sales_order_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        UniqueConstraint(
            "project_sales_order_id", "revision_no", name="uq_so_supply_decisions_revision"
        ),
        {"schema": "projects"},
    )


class SOSupplyDecisionDraft(Base, CompanyScopedMixin):
    """A decision SAVED on the planning board but not yet confirmed (S4, R-F).

    `PLAN-scm-fulfilment-feedback-2sep.md` S4: Save decision used to write React state and
    nothing else, so leaving the page lost every line the planner had settled. This is the
    row that survives it - shared, not per user, because there is one planning team and a
    second planner opening the same board must see the same saved lines.

    ITS OWN TABLE, not a `draft` state on `so_supply_decisions` (the PLAN's "Deviation
    (3 Sep)"): that table is one row per ORDER REVISION, with `revision_no` and
    `line_snapshots` NOT NULL and a partial unique index making one active revision per
    order. A per-line draft on it would either loosen those NOT NULLs or fabricate a
    revision number for a decision nobody has confirmed, and the confirmed audit trail
    depends on both.

    ADDRESSED BY THE BOARD'S OWN CONTRIBUTION KEY, which is
    `${sales_order_id}|${line_no}|${item_code}|${bucket_key}` (`_Row.key` in
    `project_fulfilment_board_service.py`, rebuilt on the frontend by `standingsFor`). So
    `sales_order_id` here is the CORE sales order (`sales_orders.id`), never the planning
    mirror: the board is built from the core book, and a line whose order nobody has
    adopted yet still has a key and can still be saved.

    IDENTITY IS THE CORE SALES ORDER LINE (C2, code review round 4), and every other part
    of that key is stored display rather than identity. NONE of the four parts is durable:
    `line_no` is POSITIONAL whenever the order's lines are not all mirrored
    (`FulfilmentBoardService._line_numbers`), so a re-upload that moves an earlier line's
    required date renumbers every line after it and the draft stopped attaching; and on
    such an order the mirror row numbers the same physical line differently again, so the
    confirmation - which deletes by the MIRROR's `line_no` - deleted nothing and the draft
    survived its own promotion. `bucket_key` moves with the board's GRANULARITY
    (`bucket_key_for`), so the same line is `2026-09-07` at week and `2026-09-09` at day.
    The core line id moves with none of that: it is the row the board, the mirror and the
    confirmation all agree names this line.

    `company_id` (from `CompanyScopedMixin`) is nullable ONLY as the mixin's own backfill-
    window convention (N5, fix round 5) - `uq_so_supply_decision_drafts_line` is
    `(company_id, core_line_id)`, and Postgres does not treat two NULLs as equal, so that
    constraint only holds one draft per line while every write actually carries a company
    id; it assumes company-scoped writes, the same as `do_orm_execute`'s own filter does.
    """

    __tablename__ = "so_supply_decision_drafts"
    __audit_entity_type__ = "project_so_supply_decision_drafts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    sales_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sales_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: WHAT THIS DRAFT IS. Resolved from the contribution key at save time
    #: (`project_line_draft_service._resolve_core_line`) and matched on everywhere after:
    #: the board stamps a draft onto a row by it, and the confirmation deletes by it.
    #: CASCADE, because a draft on a line that no longer exists has nothing to be about.
    core_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sales_order_lines.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: What the line was CALLED on the board when it was saved. Recorded and re-stamped on
    #: every save, never matched on: see the class docstring.
    line_no = Column(Integer, nullable=False)
    item_code = Column(String(100), nullable=False)
    #: Which column of the board the save was made in. Recorded, never matched on: see the
    #: class docstring.
    bucket_key = Column(String(32), nullable=False)
    #: The composition the planner saved, in the frontend's own `BoardDecision` words. The
    #: server stores and returns it and reads nothing out of it: the confirmation is posted
    #: from the board's own body, so a shape that grew a field here would still travel.
    decision = Column(JSONB, nullable=False)
    #: The LINE's own facts at save time - `{"open_qty": ..., "required_date": ...}` - not
    #: the engine's proposal (S1, code review round 3, captain ruling: staleness is judged
    #: on the line's own facts, never on the proposal). A proposal depends on which orders
    #: share the board, its granularity and its window (`_allocate` draws the shared piles
    #: in board order), so comparing PROPOSED snapshots flipped stale falsely across views
    #: and silently dropped a saved line from Confirm the moment a planner opened a
    #: different filter or granularity. `_attach_drafts` compares this against the row's
    #: CURRENT `qty` / `required_date` on every board read (AC-4.4). NULL means the save
    #: recorded no facts, which reads as "not stale": claiming a line changed when nothing
    #: was written down would put a warning on the row with no evidence behind it.
    #:
    #: Renamed from `proposed_snapshot` (hand-altered on the dev DB with `ALTER TABLE
    #: projects.so_supply_decision_drafts RENAME COLUMN proposed_snapshot TO
    #: line_snapshot;` - the table was hand-created there too, see migration
    #: `461_so_supply_decision_drafts`).
    line_snapshot = Column(JSONB, nullable=True)
    #: WHAT THE ENGINE SUGGESTED at save time (D12, #573, captain ruling): the board
    #: contribution's own `sources` the moment this draft was written, in the frontend's
    #: `BoardSource[]` words - opaque here, the same as `decision` above. NOT the same
    #: fact `line_snapshot` above carries and NEVER read for staleness (that stays the
    #: line's own facts, S1): this exists only so `SalesOrderService.serialize`'s
    #: `supply_proposed` can read a saved-but-unconfirmed line's suggested composition,
    #: the way an active revision's frozen `proposed_components` already does. NULL on a
    #: draft saved before this column shipped, or on a save nothing was offered for - see
    #: migration `463_draft_proposed`.
    proposed = Column(JSONB, nullable=True)
    saved_by = Column(
        String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    saved_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "core_line_id",
            name="uq_so_supply_decision_drafts_line",
        ),
        Index("ix_so_supply_decision_drafts_order", "sales_order_id"),
        {"schema": "projects"},
    )


class SOLineAllocation(Base, CompanyScopedMixin):
    """Where one sales-order line's stock is coming from, once a person has said so (D17).

    Ranked candidates are computed live from `stock`, never stored: a snapshot of somebody
    else's on-hand goes stale the moment they ship, and acting on a stale figure is the
    failure this slice exists to stop. Only the DECISION is persisted here.

    `source_type = "order"` is the honest fourth option: no existing stock covers the line
    and it has to be bought. It carries no warehouse, which is what makes it different from
    a location that happens to hold zero.
    """

    __tablename__ = "so_line_allocations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    so_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sales_order_lines.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type = Column(String(16), nullable=False)
    warehouse_id = Column(
        UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    # The project the stock is being taken FROM, when it is held for someone else.
    source_project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True
    )
    qty = Column(Numeric(15, 4), nullable=False)
    claim_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.allocation_claims.id", ondelete="SET NULL"), nullable=True
    )
    # Which atomic SO decision wrote this component (front planning 6.1/6.2). NULL on
    # every row written before Stage 1C, which is why the free-stock reader treats a NULL
    # decision as a live hold: a legacy confirmed allocation still holds its stock.
    decision_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.so_supply_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: What CS typed. Required on Borrow, which is the one component nobody may write in
    #: silence (AC-B09), and carried here beside the quantity it justifies.
    reason = Column(Text, nullable=True)
    #: The donor's position at the moment the Borrow was confirmed: free before, free
    #: after the whole borrow, and what was already committed there. Frozen because the
    #: live figure a month later cannot say what the decision was taken against.
    donor_impact_snapshot = Column(JSONB, nullable=True)
    confirmed_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_so_line_allocations_line", "so_line_id"),
        Index("ix_so_line_allocations_decision", "decision_id"),
        {"schema": "projects"},
    )


class AllocationClaim(Base, CompanyScopedMixin):
    """One project asking another for stock it is holding (AC-H4).

    Nothing moves on silence. A claim sits in `requested` until the other project's CS
    accepts or refuses it, and a refusal must carry a reason: "no" without one sends the
    asker back to a person rather than to another source.
    """

    __tablename__ = "allocation_claims"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    from_project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False
    )
    to_project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="CASCADE"), nullable=False
    )
    so_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sales_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_id = Column(
        UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id = Column(
        UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    qty = Column(Numeric(15, 4), nullable=False)
    state = Column(String(16), nullable=False, server_default=CLAIM_REQUESTED)
    reason = Column(Text, nullable=True)
    requested_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_allocation_claims_to_project", "to_project_id", "state"),
        Index("ix_allocation_claims_line", "so_line_id"),
        {"schema": "projects"},
    )


# --------------------------------------------------------------------------- #
# P8a: divergence between our record and AutoCount's copy of it (D25, Group N) #
# --------------------------------------------------------------------------- #

DIVERGENCE_OPEN = "open"
DIVERGENCE_RESOLVED = "resolved"

DIVERGENCE_SOURCE_UPLOAD = "upload"
DIVERGENCE_SOURCE_ESB = "esb"

RESOLUTION_ACCEPT_THEIRS = "accept_theirs"
RESOLUTION_KEEP_OURS = "keep_ours"


class ProjectSODivergence(Base, CompanyScopedMixin):
    """One inbound comparison that found something (AC-N2).

    Our values are NOT overwritten. Theirs are held beside them until a person answers
    line by line, and while this row is `open` the sales order cannot be amended (AC-N5):
    amending a record we already know is wrong is how two systems drift apart for good.

    At most one open row per sales order, enforced by a partial unique index. A CS
    uploading the same export twice gets one reconciliation, recomputed, not a stack.
    """

    __tablename__ = "so_divergences"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_so_divergences"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    project_sales_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sales_orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    autocount_doc_no = Column(String(80), nullable=True)
    ingest_source = Column(String(16), nullable=False, server_default=DIVERGENCE_SOURCE_UPLOAD)
    status = Column(String(16), nullable=False, server_default=DIVERGENCE_OPEN)

    compared_count = Column(Integer, nullable=False, server_default="0")
    agreeing_count = Column(Integer, nullable=False, server_default="0")
    differing_count = Column(Integer, nullable=False, server_default="0")

    # Set the moment any row is answered KEEP OURS: AutoCount is holding a value we have
    # decided is wrong, so a corrective document has to go back. Generated on request,
    # never stored, exactly as the original import file is.
    corrective_publish_required = Column(Boolean, nullable=False, server_default="false")
    corrective_publish_taken_at = Column(DateTime(timezone=False), nullable=True)

    detected_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=False), nullable=True)
    resolved_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_project_so_divergences_order", "project_sales_order_id"),
        Index("ix_project_so_divergences_status", "status", "detected_at"),
        {"schema": "projects"},
    )


class ProjectSODivergenceLine(Base, CompanyScopedMixin):
    """One compared row: ours, theirs, and which fields disagree (AC-N3).

    Rows that AGREE are stored too. The screen collapses them behind a count, and "47
    lines agree" is only worth reading if the 47 were written down.

    `ours_json` / `theirs_json` hold the compared values as strings at the scale the
    columns store, so the reconciliation screen renders exactly what was compared rather
    than re-deriving it from rows that may since have moved.
    """

    __tablename__ = "so_divergence_lines"
    # Audit entity type pinned to the pre-move table name (ADR-0011).
    __audit_entity_type__ = "project_so_divergence_lines"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    divergence_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.so_divergences.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope = Column(String(8), nullable=False)  # header | line
    presence = Column(String(16), nullable=False)  # both | ours_only | theirs_only
    so_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("projects.sales_order_lines.id", ondelete="SET NULL"),
        nullable=True,
    )
    line_no = Column(Integer, nullable=True)
    product_code = Column(String(80), nullable=True)
    ours_json = Column(JSONB, nullable=True)
    theirs_json = Column(JSONB, nullable=True)
    differing_fields = Column(JSONB, nullable=True)

    resolution = Column(String(16), nullable=True)  # accept_theirs | keep_ours
    reason = Column(Text, nullable=True)
    resolved_by = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_project_so_divergence_lines_divergence", "divergence_id"),
        Index("ix_project_so_divergence_lines_so_line", "so_line_id"),
        {"schema": "projects"},
    )
