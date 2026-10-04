"""Procurement models."""
from sqlalchemy import BigInteger, CheckConstraint, Column, String, Boolean, DateTime, ForeignKey, Text, Integer, Numeric, Index, Date, Computed, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base import CompanyScopedMixin
import uuid

# Forward references for relationships
from typing import TYPE_CHECKING


def _contact_display_name(contact) -> "str | None":
    """Freeform `name`, else first + last. Same precedence as
    requestor_options_service.contact_display_name (one definition of the
    requestor's human name across picker, write path and response)."""
    if contact is None:
        return None
    name = (
        (getattr(contact, "name", None) or "").strip()
        or " ".join(
            p
            for p in [
                (getattr(contact, "first_name", None) or "").strip(),
                (getattr(contact, "last_name", None) or "").strip(),
            ]
            if p
        ).strip()
    )
    return name or None

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.inventory import Warehouse, StorageZone
    from app.models.resources import Attachment


class Supplier(Base, CompanyScopedMixin):
    __tablename__ = "suppliers"
    __audit_track__ = True  # who changed what (Sub-plan D Tier-2)

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True here would make create_all build
    # the old global suppliers_supplier_code_key and reject the same code in a
    # second company (fix round 4).
    supplier_code = Column(String(50), nullable=False)
    supplier_name = Column(String(255), nullable=False)
    contact_name = Column(String(150), nullable=True)
    email = Column(String(150), nullable=True)
    phone_number = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    # S2 (`PLAN-local-supplier-oi-routing.md`): replaces the free-text `country` column,
    # NULL on every row it ever held. RESTRICT: a country still named by a supplier is not
    # a country `CountryService.delete_country` may remove out from under it.
    country_id = Column(
        UUID(as_uuid=False), ForeignKey("countries.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    payment_terms_days = Column(Integer, default=30, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # SCM (M0): denormalized latest composite supplier score (written by M2 job).
    current_performance_score = Column(Numeric, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    product_suppliers = relationship("ProductSupplier", back_populates="supplier")
    inbound_shipments = relationship("InboundShipment", back_populates="supplier")
    country = relationship("Country")

    @property
    def country_code(self) -> "str | None":
        return self.country.code if self.country else None

    @property
    def country_name(self) -> "str | None":
        return self.country.name if self.country else None

    __table_args__ = (
        Index(
            "uq_suppliers_company_supplier_code",
            "company_id", "supplier_code",
            unique=True,
        ),
        Index("ix_suppliers_is_active", "is_active"),
        Index("ix_suppliers_city", "city"),
    )


class ProductSupplier(Base, CompanyScopedMixin):
    __tablename__ = "product_suppliers"
    # Owner ruling on #1305 (28 Sep 2026, "ok keep skip now"): the skip wins over AC-AU-01's
    # tracking. A cost change is still traced through product_supplier_costs.
    __audit_skip__ = "product to supplier link from the master sync, 264 to 4,865 rows a day (measured 27 Sep 2026, review B3)"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    supplier_id = Column(UUID(as_uuid=False), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    # Nullable since `cpc3_lead_time_nullable` (#1288 round 6, R3): a link the cost upload
    # creates takes the supplier's most common lead time, else stays empty. A hand-created
    # link still has to give one (`ProductSupplierCreate` requires it).
    standard_lead_time_days = Column(Integer, nullable=True)
    # SCM (M0): sourcing parameters used by the reorder engine.
    moq = Column(Integer, nullable=True)
    order_multiple = Column(Integer, nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), nullable=True)
    is_primary_supplier = Column(Boolean, default=False, nullable=False)
    lead_time_variability_days = Column(Numeric, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    product = relationship("Product", back_populates="product_suppliers")
    supplier = relationship("Supplier", back_populates="product_suppliers")
    
    __table_args__ = (
        Index("ix_product_suppliers_product_id", "product_id"),
        Index("ix_product_suppliers_supplier_id", "supplier_id"),
        Index("uq_product_suppliers_product_id_supplier_id", "product_id", "supplier_id", unique=True),
    )


class InboundShipment(Base, CompanyScopedMixin):
    __tablename__ = "inbound_shipments"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite partial unique
    # index in __table_args__ below (WHERE shipment_number IS NOT NULL, same
    # multiple-NULL semantics 305 preserved). A bare unique=True here would make
    # create_all build the old global inbound_shipments_shipment_number_key and
    # reject the same number in a second company (fix round 4).
    shipment_number = Column(String(50), nullable=True)
    supplier_id = Column(UUID(as_uuid=False), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    shipment_date = Column(Date, nullable=False)
    estimated_arrival_date = Column(Date, nullable=True)
    actual_arrival_date = Column(Date, nullable=True)
    bill_of_lading_number = Column(String(100), nullable=True)
    shipping_container_number = Column(String(100), nullable=True)
    invoice_number = Column(String(100), nullable=True)
    shipment_status = Column(String(50), default="in_transit", nullable=False)
    total_items_shipped = Column(Integer, nullable=True)
    total_cartons = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    attachment_id = Column(UUID(as_uuid=False), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True)
    access_levels = Column(JSONB, nullable=False, server_default='["dealer","end_user"]')
    # Which regions this packing list serves: 'west' / 'east' (REGION-PACKING-LIST).
    regions = Column(ARRAY(Text), nullable=False, server_default=text("'{west}'"))
    synced_to_excel = Column(Boolean, default=False, server_default="false", nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)

    # ---- Container status (clearance and delivery) --------------------------
    # One `Container Status 2026.xlsx` row IS one packing list, so these live on
    # this table rather than in a milestone child table (decisions D1, D3). Flat
    # columns; revision history comes from `__audit_track__` below, not from a
    # separate revisions table (D5).
    #
    # Names match the Phase 1 frontend contract exactly - see `ClearanceFields`
    # in packing-lists/types/packingList.types.ts. Anything renamed here must be
    # renamed there in the same change.
    loc = Column(String(50), nullable=True)
    liner_code = Column(String(50), nullable=True)
    china_forwarder = Column(String(100), nullable=True)
    malaysia_forwarder = Column(String(100), nullable=True)
    consignee = Column(String(150), nullable=True)
    free_days_available = Column(Integer, nullable=True)
    stacked = Column(String(50), nullable=True)

    loading_date = Column(Date, nullable=True)
    etc_date = Column(Date, nullable=True)
    etd_date = Column(Date, nullable=True)
    # First-published ETA. `eta_delay_date` is the revised one and the accurate
    # one; auto-transitions key off it, never off ATA (B6, D34).
    eta_delay_date = Column(Date, nullable=True)
    inspection_date = Column(Date, nullable=True)
    approval_date = Column(Date, nullable=True)
    gatepass_date = Column(Date, nullable=True)
    delivery_warehouse = Column(String(150), nullable=True)
    warehouse_arrival_date = Column(Date, nullable=True)
    informed_collection_date = Column(Date, nullable=True)
    collection_date = Column(Date, nullable=True)

    # ATA / ORI DOC RECEIVED / K1 SUBMISSION / YARD ARRIVALS are deliberately NOT
    # columns. Filled 6 / 4 / 4 / 4 across 407 containers, read by nothing, and a
    # column nobody maintains is worse than no column - the retained source file
    # keeps the history (D34).

    coa_permit_no = Column(String(100), nullable=True)
    # Which workbook tab the row came from. Traceability only - it must never
    # derive status (A2).
    source_sheet = Column(String(100), nullable=True)

    # ---- The container workbook's own header and costs ---------------------
    # `FSCU8103365.xlsx` prints a twelve-line header block above the lines, and three of
    # its lines had nowhere to live: SEAL NO, SHIPPER, and the forwarder's `SO :` booking
    # reference (`forwarder_order_ref` - "SO" in this codebase is a sales order).
    seal_number = Column(String(50), nullable=True)
    shipper = Column(String(255), nullable=True)
    forwarder_order_ref = Column(String(100), nullable=True)

    # Typed per container, never per line (the captain's ruling, 27 Aug): the sheet's
    # footer apportions clearance and China freight by each company's share of the VOLUME
    # and insurance by its share of the AMOUNT, so one figure covers the whole container.
    # `insurance_rate` is a rate the share is multiplied by, not a cost.
    clearance_cost = Column(Numeric(15, 2), nullable=True)
    china_freight_cost = Column(Numeric(15, 2), nullable=True)
    insurance_rate = Column(Numeric(15, 4), nullable=True)

    #: Which box this container is being loaded into. NULL means the tenant's default size,
    #: resolved on read rather than copied in (S5, migration 465). Capacity is a property of
    #: the CONTAINER, not any one proforma invoice that fed it - this column used to live on
    #: `scm.proforma_invoice`, one level too low: a packing list routinely consolidates
    #: several PIs, and the box only has one size however many of them are inside it.
    container_size_id = Column(
        UUID(as_uuid=False), ForeignKey("scm.container_size.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Every ETA revision writes an `audit_logs` row with old_values/new_values,
    # which is what replaces a revisions table (D5).
    __audit_track__ = True

    supplier = relationship("Supplier", back_populates="inbound_shipments")
    attachment = relationship("Attachment")
    container_size = relationship("ContainerSize")
    # No delete cascade, on purpose (SPO-CASCADE, owner ruling 28 Sep 2026: "we shouldn't
    # delete on cascade"). A shipping order is a document in its own right (see
    # `SPOAllocation`); this shipment is only the container it was booked on. Deleting the
    # shipment UNLINKS its lines (`InboundShipmentService._unlink_allocations` nulls
    # `inbound_shipment_id` on each one as an audited update; `passive_deletes` leaves any
    # straggler to the FK's own SET NULL), and a later shipment naming the same container
    # picks them up again (`InboundShipmentService._relink_allocations_for_shipment`).
    # `cascade="all, delete-orphan"` here is what hard-deleted the 18 AutoCount-synced lines
    # of SPO-2026/09-0104 when a user deleted PL-2609-059, and the sync never re-pushes.
    spo_allocations = relationship(
        "SPOAllocation",
        back_populates="inbound_shipment",
        cascade="save-update, merge",
        passive_deletes=True,
    )
    shipment_lines = relationship(
        "InboundShipmentLine",
        back_populates="shipment",
        cascade="all, delete-orphan",
    )

    @property
    def display_total_items(self):
        """Total items for display: sum of quantity_shipped from lines when present and > 0, else total_items_shipped."""
        if self.shipment_lines and len(self.shipment_lines) > 0:
            total = sum((line.quantity_shipped or 0) for line in self.shipment_lines)
            if total > 0:
                return total
        return self.total_items_shipped

    @property
    def display_total_cartons(self):
        """Total cartons for display: sum of cartons_count from lines when present and > 0, else total_cartons."""
        if self.shipment_lines and len(self.shipment_lines) > 0:
            total = sum((line.cartons_count or 0) for line in self.shipment_lines)
            if total > 0:
                return total
        return self.total_cartons

    __table_args__ = (
        Index(
            "uq_inbound_shipments_company_shipment_number",
            "company_id", "shipment_number",
            unique=True,
            postgresql_where=text("shipment_number IS NOT NULL"),
        ),
        Index("ix_inbound_shipments_supplier_id", "supplier_id"),
        Index("ix_inbound_shipments_shipment_number", "shipment_number"),
        Index("ix_inbound_shipments_shipment_status", "shipment_status"),
        Index("ix_inbound_shipments_access_levels", "access_levels", postgresql_using="gin"),
        # The importer matches on the container number across EVERY status, so the
        # existing status-scoped indexes do not help it.
        Index("ix_inbound_shipments_container_number", "shipping_container_number"),
        # "Which containers are still open" drives the tracking poll (~77/day).
        Index("ix_inbound_shipments_eta_delay_date", "eta_delay_date"),
        CheckConstraint(
            "cardinality(regions) >= 1 AND regions <@ ARRAY['west','east']::text[]",
            name="ck_inbound_shipments_regions",
        ),
        # No other `CheckConstraint` here on purpose, even though one exists on the shared
        # local/prod database (`inbound_shipments_shipment_status_check`, added outside any
        # migration in this repo - see `coverage_service.py`'s note). Several existing
        # `blank_session()` tests drive this column through values that constraint would
        # reject (`completed`, `partial_received` - dead aliases per that same note); adding
        # it here would make those pre-existing tests fail against a `create_all` schema for
        # a reason unrelated to what they are testing. Migration 405 still extends the LIVE
        # constraint with `draft`; this column's vocabulary just is not additionally
        # enforced on a scratch schema.
    )


class ShipmentTrackingObservation(Base, CompanyScopedMixin):
    """What an integration SAW, never what the record says.

    Append-only. Only liner and CIDB adapters write here; there is deliberately
    no human write path and no update path (E1). Nothing in this table may mutate
    a column on `inbound_shipments` - a test runs a full ingest and asserts the
    shipment row comes out byte-identical (E2).

    Paired `*_observed` columns on the shipment were rejected: they overwrite on
    every poll, which destroys the timing evidence, and the timing evidence is
    the whole reason the validation period exists (D7). `observed_at` is when the
    SOURCE says it happened; `fetched_at` is when we asked. The gap between
    `fetched_at` and the human's later edit is the "we knew N days sooner"
    number the validation report reads.
    """

    __tablename__ = "shipment_tracking_observations"
    __audit_skip__ = "tracking observation log, itself a trail"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(
        UUID(as_uuid=False),
        ForeignKey("inbound_shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Which shipment column this observation is ABOUT (eta_delay_date,
    # inspection_date, ...). A plain string, not an FK: the vocabulary is the
    # column set above, and an enum would need a migration per new field.
    field_key = Column(String(60), nullable=False)
    # Kept as text. A carrier that returns something unparseable is still
    # evidence, and coercing it here would either lose it or invent a date.
    observed_value = Column(Text, nullable=True)
    # `liner_cma`, `liner_whl`, `cidb_epermit`, ... or `unsupported` for a
    # carrier with no adapter, so the coverage gap is visible rather than silent.
    source = Column(String(60), nullable=False)
    # The source's own identifier for what it showed us (booking ref, permit no,
    # page URL) so an observation can be traced back by hand.
    source_ref = Column(String(255), nullable=True)
    observed_at = Column(DateTime(timezone=False), nullable=True)
    fetched_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    shipment = relationship("InboundShipment")

    __table_args__ = (
        # The validation report reads "every observation for this shipment and
        # field, newest first".
        Index(
            "ix_shipment_tracking_obs_shipment_field",
            "shipment_id",
            "field_key",
            "fetched_at",
        ),
        Index("ix_shipment_tracking_obs_source", "source"),
    )


class InboundShipmentLine(Base, CompanyScopedMixin):
    __tablename__ = "inbound_shipment_lines"
    __audit_parent__ = "shipment_id"  # history rolls up to the header
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    shipment_id = Column(UUID(as_uuid=False), ForeignKey("inbound_shipments.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    # Whose line this is. A container carries several factories' goods and each sends its
    # own packing list, so the supplier belongs on the LINE; the header supplier is derived
    # from these and goes NULL when they disagree.
    # NULLABLE: the n8n PDF path names no supplier, and a line whose factory we were never
    # told is an honest NULL rather than an invented owner.
    supplier_id = Column(UUID(as_uuid=False), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    quantity_shipped = Column(Integer, nullable=False)
    uom_id = Column(UUID(as_uuid=False), ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True)
    batch_number = Column(String(100), nullable=True)
    serial_number_range_from = Column(String(100), nullable=True)
    serial_number_range_to = Column(String(100), nullable=True)
    carton_number = Column(String(50), nullable=True)
    cartons_count = Column(Integer, default=1, nullable=False)
    weight_per_carton = Column(Numeric(10, 2), nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    # The unit `unit_cost` is stated in (AC-C3.2). Mirrors purchase_order_lines.currency
    # as String(3) so the incoming and the ordered figure are comparable at all; a cost
    # with no currency is a number with no meaning.
    # NULLABLE, and never defaulted: where no currency is knowable (the packing list does
    # not state one and the linked PO line has none either) it stays NULL. A house default
    # here would silently assert that ordered and incoming are in the same unit, which is
    # the whole content of the variance.
    currency = Column(String(3), nullable=True)
    # Volume for this line, as the packing list stated it (`总体积(cbm)`, or per-unit times
    # the quantity). The reader has always parsed it and thrown it away; the consolidated
    # packing list needs it for the per-factory and per-company subtotals.
    cbm = Column(Numeric(12, 4), nullable=True)
    # The supplier's own note on the line (`备注`). Kept separate from the discrepancies the
    # system derives, which are computed at read time and never stored.
    remarks = Column(Text, nullable=True)
    # The supplier's own wording for the item (migration 466, S9), copied off
    # `proforma_invoice_line.description` at convert. NULL on a line drafted before this
    # column existed or uploaded from a packing list with no description column of its own -
    # both the export and the grid read the product's name instead when this is unset.
    description = Column(Text, nullable=True)

    # ---- What the container workbook measures the line by -------------------
    # Read off the supplier's proforma / packing list, editable on the packing list
    # afterwards. The sheet derives CTN QTY from `quantity_shipped / pcs_per_carton`,
    # CBM / CTN from the carton's L x W x H, and TOTAL NW / TOTAL GW from the per-carton
    # weights times the carton count - so none of those five is stored, only its inputs.
    #
    # Centimetres, as the sheet's `SIZE (CM)` block states them: converting on the way in
    # would make the stored number disagree with the paper the supplier sent.
    material = Column(String(255), nullable=True)
    pcs_per_carton = Column(Numeric(15, 4), nullable=True)
    carton_length_cm = Column(Numeric(10, 2), nullable=True)
    carton_width_cm = Column(Numeric(10, 2), nullable=True)
    carton_height_cm = Column(Numeric(10, 2), nullable=True)
    # `weight_per_carton` above stays and is READ AS GROSS when this pair is null: it is
    # the only weight the line has ever had, and rewriting the rows that carry it would
    # lose the one figure some containers hold.
    net_weight_per_carton = Column(Numeric(10, 3), nullable=True)
    gross_weight_per_carton = Column(Numeric(10, 3), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    synced_to_excel = Column(Boolean, default=False, server_default="false", nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    spo_allocated_quantity = Column(Integer, default=0, nullable=False)
    quantity_received = Column(Integer, default=0, nullable=False)
    line_status = Column(String(50), default="in_transit", nullable=False)
    
    shipment = relationship("InboundShipment", back_populates="shipment_lines")
    product = relationship("Product", back_populates="inbound_shipment_lines")
    uom = relationship("UnitOfMeasure", foreign_keys=[uom_id])
    supplier = relationship("Supplier", foreign_keys=[supplier_id])
    __table_args__ = (
        Index("ix_inbound_shipment_lines_shipment_id", "shipment_id"),
        Index("ix_inbound_shipment_lines_product_id", "product_id"),
        Index("ix_inbound_shipment_lines_supplier_id", "supplier_id"),
        # One row per product PER SUPPLIER, replacing the (shipment, product) unique
        # constraint that made a second factory's packing list overwrite the first's.
        # `NULLS NOT DISTINCT` (PG 15+) keeps a supplier-less line unique on the product
        # alone, so the n8n PDF path behaves exactly as it did.
        #
        # NON-unique since S4 (AC-D1, `scm-supplier-documents-pi-first`): a supplier's own
        # carton split (Kailu's SRTSC14-GM shipping 50@50/ctn and 35@35/ctn) is two lines of
        # the same (shipment, product, supplier), not one - convert writes one shipment
        # line per matched packing row now. Still indexed, for the lookups that filter on
        # the same three columns; renamed off the `uk_` prefix so its own name does not
        # keep claiming a uniqueness the table no longer has.
        Index(
            "ix_inbound_shipment_lines_ship_prod_sup",
            "shipment_id",
            "product_id",
            "supplier_id",
        ),
    )


class SPOAllocation(Base, CompanyScopedMixin):
    """A shipping-order LINE: what one SPO promises, of one product, to one location.

    Since migration 420 (`PLAN-scm-cs-planning-uat.md` section K, captain's Q6 ruling of
    25 Aug 2026) this table holds the SPO DOCUMENT itself, not only the allocation of an
    inbound shipment. Both writers meet on one shape: the purchase-history import files
    every `SPO-` document here, and `spo_conversion_service` files the SPO it raises from a
    draft shipment here. `scm.on_order_v` reads this table and nothing else, so an SPO is
    incoming supply for the first time.

    Two columns are nullable because of that, and each says something:

      * `inbound_shipment_id` - a shipping order exists before anybody books a container
        for it. NULL means "promised, not yet on a named shipment".
      * `warehouse_id` - the book names a stock location we do not hold on 6,520 of the
        captain's own SPO lines. The raw code is kept in `location_code` so nothing is lost,
        and a row with no warehouse counts as incoming supply NOWHERE (`on_order_v` requires
        a warehouse); a location we cannot place cannot cover a line standing at one.
    """
    __tablename__ = "spo_allocations"
    # Stays opted out of the default-on hooks: the audit standard's measured 10x ceiling
    # (`tests/test_audit_standard_s0_round3.py::projected_rows_per_day`) cannot credit the
    # sync-writer exclusion, and this table's volume alone breaches it on paper. What the
    # SPO-CASCADE incident needed (18 synced lines gone with no row saying who or when) is
    # written explicitly instead: `procurement_service._audit_spo_line` records every
    # staff-driven delete of a line (single, bulk, whole document) and the unlink when a
    # packing list is deleted, attributed to the stamped actor.
    __audit_skip__ = "shipping order allocation lines from the sync, 2,602 to 29,343 rows a day (measured 27 Sep 2026, review B3)"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    spo_number = Column(String(50), nullable=True)
    spo_line_number = Column(Integer, nullable=True)
    #: SET NULL, never CASCADE (SPO-CASCADE, migration `spo_cascade_0001_set_null`): the
    #: shipment is where the document was booked, not what owns it. Its deletion returns
    #: the line to "promised, not yet on a named shipment"; `container_number` below is
    #: what a later shipment relinks on.
    inbound_shipment_id = Column(UUID(as_uuid=False), ForeignKey("inbound_shipments.id", ondelete="SET NULL"), nullable=True)
    warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    #: The stock location exactly as the book spelled it, whether or not we hold it. The
    #: only record of where an unheld-location line was meant to go.
    location_code = Column(String(50), nullable=True)
    storage_zone_id = Column(UUID(as_uuid=False), ForeignKey("storage_zones.id", ondelete="SET NULL"), nullable=True)
    allocated_quantity = Column(Integer, nullable=False)
    uom_id = Column(UUID(as_uuid=False), ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True)
    receipt_status = Column(String(50), default="pending", nullable=False)
    #: `server_default` as well as `default`, because the live table carries `DEFAULT 0` on
    #: both and a create_all database did not. Every writer that reaches this table with raw
    #: SQL - the purchase-history import, the SPO conversion, the tests that seed a document
    #: line - names the columns it cares about and leaves the rest to the default, so the
    #: two schemas have to agree about what the default is or the same INSERT works on the
    #: dev copy and raises NotNullViolation in CI.
    quantity_received = Column(Integer, default=0, server_default="0", nullable=False)
    quantity_rejected = Column(Integer, default=0, server_default="0", nullable=False)
    allocation_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    # Which Supply PO line this allocation draws down. The chain is PO -> SPO -> GRN, and
    # this table carried no PO reference at all; only picking_lines.po_line_id existed,
    # which is one step too late (goods-received, after the allocation was decided).
    # NULLABLE: 860 pre-existing rows have no PO, and stock can arrive against no PO.
    # The constraint is named explicitly so a create_all schema and a migrated schema agree.
    # Left implicit, Postgres names it `spo_allocations_po_line_id_fkey` under create_all
    # while migration 311 creates `fk_spo_allocations_po_line_id`, and anything that drops
    # the constraint by name then works on one path and fails on the other.
    po_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "purchase_order_lines.id",
            ondelete="SET NULL",
            name="fk_spo_allocations_po_line_id",
        ),
        nullable=True,
    )
    synced_to_excel = Column(Boolean, default=False, server_default="false", nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    # --- the document the line belongs to (migration 420) ----------------------------
    #: Which feed wrote it: `scm_spo_history` / `scm_po_history` / `scm_upload` for an
    #: imported document, NULL for a row this system raised itself. The migration's
    #: downgrade moves exactly the stamped rows back into `purchase_orders`.
    source_system = Column(String(50), nullable=True)
    #: The SPO's own document date, and the line's promised arrival. `expected_date` is what
    #: rung 1 of the fulfilment ladder compares against a sales line's required date, so a
    #: shipping order with no shipment booked can still be read as timely incoming supply.
    issue_date = Column(Date, nullable=True)
    expected_date = Column(Date, nullable=True)
    supplier_id = Column(
        UUID(as_uuid=False),
        ForeignKey("suppliers.id", ondelete="SET NULL", name="fk_spo_allocations_supplier_id"),
        nullable=True,
    )
    unit_cost = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), nullable=True)
    #: `open` / `closed`, the same word the purchase-order line carries. History lands
    #: closed, so it can never read as supply however its receipt status is later edited.
    line_status = Column(String(50), nullable=False, server_default="open", default="open")
    # --- AutoCount ingest v2 (S3, D3) -------------------------------------------------
    #: AutoCount's DtlKey - the LINE's own identity across re-pushes. NULL on every
    #: pre-existing (xlsx-era) row; D11 adoption is what claims those without ever
    #: writing a ref onto them retroactively.
    source_ref = Column(String(255), nullable=True)
    #: AutoCount's DocKey - names the DOCUMENT this line belongs to (the group of rows
    #: sharing one `spo_number`), since a shipping order has no header table of its own
    #: (D3). A push finds its rows by this column, falling back to `spo_number` alone
    #: for a document's first sync.
    source_doc_ref = Column(String(255), nullable=True)
    #: AutoCount's `PO.Ref` / the SPO xlsx Loading Date cell, cleaned through
    #: `shipping_order_rules.extract_container_number` (D6, migration 477).
    #: Stored on EVERY allocation of a document, whether or not it resolves
    #: to a held `InboundShipment` - `inbound_shipment_id` is the resolved
    #: link, this is the raw fact so a later shipment can still be relinked.
    container_number = Column(String(100), nullable=True)
    #: The receipt a DECLARER stated for this line, as opposed to the one a GRN
    #: proves (spo-xlsx-supersede D28c, migration 488). Written only by AutoCount's
    #: own `qty_received` (TransferedQty) on a push and by the supersede / dedupe
    #: carry; never by the GRN recompute, which is exactly why it exists:
    #: `quantity_received` alone cannot say whether a figure was stated or derived,
    #: so deleting a GRN either erased AutoCount's own statement or stranded a
    #: redistributed share on a sibling with nothing behind it. The group recompute
    #: writes `max(stated_received, its share of the approved picking total)`.
    #: NULL reads as 0: every row written before this column existed stated nothing.
    stated_received = Column(Integer, nullable=True)
    #: When the ESB stopped naming this line (spo-xlsx-supersede D28d, migration
    #: 488). Set by the leftover sweep (a re-push of the same DocKey no longer
    #: states it) and by the DocKey-change path (the document was re-created, so
    #: the old DocKey's rows are history); cleared the moment a push names the
    #: row again. Without it a retired line is indistinguishable from a live
    #: fully received one - it rejoined its `(spo_number, product, location)`
    #: group, took a share of a sibling's GRN, and could be REOPENED when a GRN
    #: was deleted, showing 58 open units on a 29-unit order. The group
    #: recompute skips a retired row entirely.
    retired_at = Column(DateTime(timezone=True), nullable=True)
    # --- AutoCount linkage widen (V5, ingest-contract-2-2-so-links) ------------------
    #: The SOURCE purchase-order line this shipping-order line was raised from,
    #: same `"{database}:{DocKey}:{DtlKey}"` format as `source_ref` above. Raw
    #: pass-through, never resolved into an id: `po_line_id` above already means
    #: something narrower (a Sorento-raised SPO's own supply chain), and this is
    #: what lets an order-inquiry row that reserved against this SPO print the
    #: purchase order the buyer actually reads. NULL when the ESB has not stated
    #: one; an omitted field on a re-push never clears a value already stored
    #: (absent_vs_null, `shipping_order_ingest_service._line_values`).
    from_po_line_ref = Column(String(255), nullable=True)
    #: The source purchase order's own document number, alongside the ref above.
    from_po_number = Column(String(100), nullable=True)
    #: The exact sales-order line this shipping-order line was raised for,
    #: same format and same B2 uniform-persistence rule as
    #: `PurchaseOrderLine.from_so_line_ref` below - see its comment.
    from_so_line_ref = Column(String(255), nullable=True)
    #: The cross-book case of a sales-order line reference - see the
    #: identical comment on `PurchaseOrderLine.from_so_external` below.
    from_so_external = Column(JSONB, nullable=True)

    inbound_shipment = relationship("InboundShipment", back_populates="spo_allocations")
    supplier = relationship("Supplier", foreign_keys=[supplier_id])
    po_line = relationship("PurchaseOrderLine", foreign_keys=[po_line_id])
    warehouse = relationship("Warehouse", back_populates="spo_allocations")
    storage_zone = relationship("StorageZone", back_populates="spo_allocations")
    product = relationship("Product", back_populates="spo_allocations")
    uom = relationship("UnitOfMeasure", foreign_keys=[uom_id])
    #: `passive_deletes=True` (spo-xlsx-supersede, reviewer cleanup): the FK is
    #: `ON DELETE SET NULL`, and without this SQLAlchemy loads this collection
    #: on `session.delete(allocation)` and NULLs each child's
    #: `spo_allocation_id` ITSELF - which would undo a repoint the first-push
    #: supersede has just made (D27) whenever the collection was already in the
    #: identity map. Leaving it to the database makes the
    #: repoint-then-delete ordering structural rather than a matter of which
    #: rows happened to be loaded.
    picking_lines = relationship(
        "PickingLine", back_populates="spo_allocation", passive_deletes=True
    )
    
    __table_args__ = (
        Index("ix_spo_allocations_inbound_shipment_id", "inbound_shipment_id"),
        Index("ix_spo_allocations_warehouse_id", "warehouse_id"),
        Index("ix_spo_allocations_product_id", "product_id"),
        # The LINE is the identity, not the (document, product, location) triple: the
        # captain's book states the same product on one SPO 13,305 times over - two
        # containers of one item - and the old triple key forbade every one of them.
        UniqueConstraint(
            "company_id", "spo_number", "spo_line_number",
            name="uk_spo_allocations_company_spo_line",
        ),
        # What the triple key was also doing, kept as a plain lookup: every reader that
        # asks "what is coming for this product, here" hits these three columns.
        Index("ix_spo_allocations_spo_product_warehouse", "spo_number", "product_id", "warehouse_id"),
        # AutoCount ingest v2 (S3): a push finds its document's rows by DocKey.
        Index("ix_spo_allocations_company_source_doc_ref", "company_id", "source_doc_ref"),
        # D6 (migration 477): the relink sweep's own lookup, and a container is
        # unique per company only (the same code legitimately arrives twice).
        Index("ix_spo_allocations_company_container", "company_id", "container_number"),
        # One DtlKey can adopt at most one line, company-wide - partial so the
        # countless ref-less xlsx-era rows never collide with each other or with it.
        Index(
            "uq_spo_allocations_company_source_ref",
            "company_id", "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
    )


class PickingHeader(Base, CompanyScopedMixin):
    __tablename__ = "picking_headers"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True here would make create_all build
    # the old global picking_headers_picking_number_key and reject the same
    # number in a second company (fix round 4).
    picking_number = Column(String(50), nullable=False)
    # DISPLAY-width, not match-width: a multi-SPO GRN stores every SPO it covers
    # ("SPO-A, SPO-B") so the list says so instead of showing a dash. Allocation
    # matching stays scalar (`_spo_match_key` / `_normalize_spo_number` compare ONE
    # normalized SPO), so a joined value equals no single SPO and never
    # false-links. Widened by migration 317; see also `_single_spo_or_none`.
    spo_number = Column(String(255), nullable=True)
    picking_type = Column(String(50), nullable=False)
    source_entity_type = Column(String(50), nullable=True)
    source_entity_id = Column(UUID(as_uuid=False), nullable=True)
    picking_date = Column(Date, server_default=func.current_date(), nullable=False)
    picked_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    inspection_status = Column(String(50), default="pending", nullable=False)
    quality_remarks = Column(Text, nullable=True)
    inspected_by_user_id = Column(UUID(as_uuid=False), nullable=True)
    inspection_date = Column(DateTime(timezone=False), nullable=True)
    picking_status = Column(String(50), default="draft", nullable=False)
    total_items_picked = Column(Integer, nullable=True)
    total_items_discrepancy = Column(Integer, nullable=True)
    total_cost = Column(Numeric(15, 2), nullable=True)
    notes = Column(Text, nullable=True)
    # Provenance. A GRN can arrive three ways - a staff create, an Excel import, or
    # the external (n8n / AutoCount) API - and the row itself recorded none of
    # them, so "who created this GRN, and into which company" could only be
    # guessed by bracketing `created_at` against import_jobs, which fails outright
    # for the external path (no job, no user). Written ONCE on insert and never
    # touched by a re-import, so the answer survives an overwrite.
    #   created_by      staff user id; NULL for external-API writes
    #   source_system   'ui' | 'import' | 'external_api'
    #   import_job_id   the job, when an import wrote it -> file + uploader + the
    #                   company snapshot that was active
    created_by = Column(UUID(as_uuid=False), nullable=True)
    source_system = Column(String(30), nullable=True)
    import_job_id = Column(
        UUID(as_uuid=False),
        ForeignKey("import_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # AutoCount GRN ingest (#1354 S2, plan 2.4). A GRN is AutoCount-OWNED when `doc_key` is
    # set; the Excel GRN import then leaves its header and lines alone. `source_system`
    # above keeps its written-once meaning ('autocount' only on a GRN the ingest created).
    source_book = Column(String(20), nullable=True)
    doc_key = Column(BigInteger, nullable=True)
    source_modified_at = Column(DateTime(timezone=True), nullable=True)
    source_vanished_at = Column(DateTime(timezone=True), nullable=True)
    last_synced_at = Column(DateTime(timezone=False), nullable=True)
    source_record = Column(JSONB, nullable=True)
    creditor_code = Column(String(100), nullable=True)
    creditor_name = Column(String(255), nullable=True)
    supplier_do_no = Column(String(100), nullable=True)
    purchase_agent = Column(String(100), nullable=True)
    ship_via = Column(String(100), nullable=True)
    ship_info = Column(String(255), nullable=True)
    ref = Column(String(255), nullable=True)
    ref_doc_no = Column(String(100), nullable=True)
    remarks = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    doc_status = Column(String(20), nullable=True)
    is_cancelled = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    currency_code = Column(String(10), nullable=True)
    currency_rate = Column(Numeric(18, 8), nullable=True)
    subtotal_amount = Column(Numeric(15, 2), nullable=True)
    tax_amount = Column(Numeric(15, 2), nullable=True)
    total_amount = Column(Numeric(15, 2), nullable=True)
    local_net_total = Column(Numeric(15, 2), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    picking_lines = relationship("PickingLine", back_populates="picking_header")
    
    __table_args__ = (
        Index(
            "uq_picking_headers_company_picking_number",
            "company_id", "picking_number",
            unique=True,
        ),
        Index("ix_picking_headers_picking_type", "picking_type"),
        Index("ix_picking_headers_picking_number", "picking_number"),
        Index("ix_picking_headers_picking_status", "picking_status"),
        Index("ix_picking_headers_spo_number", "spo_number"),
        # "what did this job create?" is the question that started this, so it is
        # indexed; created_by is for filtering a listing by uploader.
        Index("ix_picking_headers_import_job_id", "import_job_id"),
        Index("ix_picking_headers_created_by", "created_by"),
        Index(
            "uq_picking_headers_company_book_doc_key",
            "company_id", "source_book", "doc_key",
            unique=True,
            postgresql_where=text("doc_key IS NOT NULL"),
        ),
    )


class PickingLine(Base, CompanyScopedMixin):
    __tablename__ = "picking_lines"
    __audit_skip__ = "line table, 268 to 4,047 rows a day (measured 27 Sep 2026, review B3)"
    __audit_parent__ = "picking_header_id"  # history rolls up to the header
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    picking_header_id = Column(UUID(as_uuid=False), ForeignKey("picking_headers.id", ondelete="CASCADE"), nullable=False)
    spo_allocation_id = Column(UUID(as_uuid=False), ForeignKey("spo_allocations.id", ondelete="SET NULL"), nullable=True)
    # What the SHEET said this line was received against - the line's own "Our PO
    # No." when populated, else the GRN header's single-SPO fallback. Stored
    # whether or not it matched an allocation, so a line the matcher could not
    # place still reads as stated instead of as a dash, and so the forward matcher
    # can revisit it the day its allocation arrives.
    #
    # Faithful to the sheet: no case folding, no separator rewriting. The
    # tolerating is `_spo_match_key`'s job at match time; this column is evidence,
    # and evidence is not normalised. NULL for a multi-SPO cell (it names no single
    # allocation, so a stored value would display a claim the scalar matcher can
    # never honour). Same width as `picking_headers.spo_number`.
    spo_number_raw = Column(String(255), nullable=True)
    # SCM (M0): soft link to the originating PO line when this pick is a goods-received
    # against a purchase order (drives supplier lead-time / quality snapshots).
    po_line_id = Column(UUID(as_uuid=False), ForeignKey("purchase_order_lines.id", ondelete="SET NULL"), nullable=True)
    qty_accepted = Column(Integer, nullable=True)
    qty_rejected = Column(Integer, nullable=True)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity_expected = Column(Integer, nullable=False)
    quantity_picked = Column(Integer, nullable=False)
    quantity_discrepancy = Column(Integer, Computed("(quantity_expected - quantity_picked)"), nullable=False)
    uom_id = Column(UUID(as_uuid=False), ForeignKey("units_of_measure.id", ondelete="SET NULL"), nullable=True)
    picked_condition = Column(String(50), default="good", nullable=False, server_default="good")
    condition_remarks = Column(Text, nullable=True)
    batch_number_picked = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    line_total = Column(Numeric(15, 2), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    source_warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    destination_warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    synced_to_excel = Column(Boolean, default=False, server_default="false", nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    # AutoCount GRN ingest (#1354 S2, plan 2.5). `qty` is AutoCount's exact quantity; the
    # integer quantity columns above carry it rounded. `purchase_order_id` is the
    # document-number link from `OurPONo`; the line links are `po_line_id` /
    # `spo_allocation_id`.
    dtl_key = Column(BigInteger, nullable=True)
    seq = Column(Integer, nullable=True)
    item_code = Column(String(100), nullable=True)
    location_code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    # `uom_code`, not `uom`: `uom` is this model's UnitOfMeasure relationship.
    uom_code = Column(String(30), nullable=True)
    qty = Column(Numeric(15, 4), nullable=True)
    foc_qty = Column(Numeric(15, 4), nullable=True)
    discount_text = Column(String(50), nullable=True)
    discount_amount = Column(Numeric(15, 2), nullable=True)
    tax_amount = Column(Numeric(15, 2), nullable=True)
    delivery_date = Column(Date, nullable=True)
    proj_no = Column(String(50), nullable=True)
    our_po_no = Column(String(100), nullable=True)
    our_po_date = Column(Date, nullable=True)
    from_doc_type = Column(String(10), nullable=True)
    from_doc_no = Column(String(100), nullable=True)
    from_dtl_key = Column(BigInteger, nullable=True)
    purchase_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("purchase_orders.id", ondelete="SET NULL", name="fk_picking_lines_purchase_order_id"),
        nullable=True,
    )

    picking_header = relationship("PickingHeader", back_populates="picking_lines")
    spo_allocation = relationship("SPOAllocation", back_populates="picking_lines")
    product = relationship("Product", back_populates="picking_lines")
    uom = relationship("UnitOfMeasure", foreign_keys=[uom_id])
    source_warehouse = relationship("Warehouse", foreign_keys=[source_warehouse_id], back_populates="source_picking_lines")
    destination_warehouse = relationship("Warehouse", foreign_keys=[destination_warehouse_id], back_populates="destination_picking_lines")
    
    __table_args__ = (
        Index("ix_picking_lines_picking_header_id", "picking_header_id"),
        Index("ix_picking_lines_product_id", "product_id"),
        Index("ix_picking_lines_spo_allocation_id", "spo_allocation_id"),
        Index("ix_picking_lines_po_line_id", "po_line_id"),
        Index("ix_picking_lines_purchase_order_id", "purchase_order_id"),
        Index(
            "uq_picking_lines_header_dtl_key",
            "picking_header_id", "dtl_key",
            unique=True,
            postgresql_where=text("dtl_key IS NOT NULL"),
        ),
        # The forward-matching query, exactly: "unlinked lines whose stated SPO
        # reduces to this match key". Partial, because a linked line is never a
        # candidate. `upper` and `regexp_replace` are both IMMUTABLE, so the
        # expression is indexable. It MUST strip the same set as
        # `procurement_service._spo_match_key` (`[^A-Za-z0-9]`, then uppercase) or
        # SQL would offer a candidate the Python comparison then rejects - the same
        # twin obligation the container-number normalizers carry. Migration
        # 324_grn_line_spo_number_raw emits the identical statement.
        Index(
            "ix_picking_lines_spo_number_raw_key",
            func.upper(func.regexp_replace(spo_number_raw, "[^A-Za-z0-9]", "", "g")),
            postgresql_where=spo_allocation_id.is_(None),
        ),
    )


class PurchaseOrder(Base, CompanyScopedMixin):
    """SCM purchase order (supply / on-order source). Public core record - survives
    module uninstall. Sits with suppliers / PR in the procurement domain."""
    __tablename__ = "purchase_orders"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique per COMPANY, not globally - see `SalesOrder.so_number`. Migration 305
    # dropped `purchase_orders_po_number_key` for
    # `uq_purchase_orders_company_po_number`.
    po_number = Column(String(100), nullable=False)
    supplier_id = Column(UUID(as_uuid=False), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    issue_date = Column(Date, nullable=True)
    expected_date = Column(Date, nullable=True)
    status = Column(String(50), default="draft", nullable=False)  # draft | draft_recommendation | active
    currency = Column(String(3), nullable=True)
    source_system = Column(String, nullable=True)
    source_ref = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    supplier = relationship("Supplier")
    lines = relationship(
        "PurchaseOrderLine",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        # Insertion order, with the id as the tie breaker: lines written in one
        # transaction share created_at (one now() per transaction), and without
        # the tie breaker Postgres hands them back in whichever order it likes.
        # The detail response and its consumers (the CI test that takes
        # lines[0] as "the open line") need one stable order.
        order_by="[PurchaseOrderLine.created_at, PurchaseOrderLine.id]",
    )

    __table_args__ = (
        Index(
            "uq_purchase_orders_company_po_number",
            "company_id",
            "po_number",
            unique=True,
        ),
        Index("ix_purchase_orders_supplier_id", "supplier_id"),
        Index("ix_purchase_orders_po_number", "po_number"),
        Index("ix_purchase_orders_status", "status"),
    )


class PurchaseOrderLine(Base, CompanyScopedMixin):
    """Open PO line - feeds on-order / net-position views by product×warehouse."""
    __tablename__ = "purchase_order_lines"
    __audit_skip__ = "sync line table, 5,224 to 51,107 rows a day (measured 27 Sep 2026, review B3)"
    __audit_parent__ = "purchase_order_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_order_id = Column(UUID(as_uuid=False), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    qty_ordered = Column(Numeric(15, 4), default=0, nullable=False)
    qty_received = Column(Numeric(15, 4), default=0, nullable=False)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    # The rest of the money line the AutoCount PO detail listing states, and the unit the
    # quantity was ordered IN. All three nullable, and an absent figure stays absent: a 0
    # discount claims a discount of nothing was given and a 0 total claims free goods.
    # `uom` is the per-line OVERRIDE - the serializer falls back to the product's base unit -
    # because a purchase order written in cartons must not read in pieces.
    discount = Column(Numeric(15, 2), nullable=True)
    line_total = Column(Numeric(15, 2), nullable=True)
    uom = Column(String(100), nullable=True)
    currency = Column(String(3), nullable=True)
    expected_date = Column(Date, nullable=True)
    moq_snapshot = Column(Integer, nullable=True)
    order_multiple_snapshot = Column(Integer, nullable=True)
    line_status = Column(String(50), default="open", nullable=False)
    source_system = Column(String, nullable=True)
    source_ref = Column(String, nullable=True)
    # --- AutoCount linkage widen (V5, ingest-contract-2-2-so-links) ------------------
    # B2 (review ruling): every field the wire sends is persisted uniformly on
    # BOTH `purchase_order_lines` and `spo_allocations`, not on `spo_allocations`
    # alone - `from_so_line_ref` in particular is the whole point of this
    # slice, and a purchase order pushed before its sales order must not lose
    # the exact ref forever. Same format as `source_ref` above
    # (`"{database}:{DocKey}:{DtlKey}"`), joinable to `sales_order_lines
    # .source_ref`. `order_link_service.write_line_ref_claims` resolves it
    # at write time when possible; `_exact_so_line_for` re-reads it straight
    # off this column on a later `resolve()` sweep when it was not - which is
    # the reason it is stored here at all, not merely consumed and discarded.
    from_so_line_ref = Column(String(255), nullable=True)
    #: The SOURCE purchase-order line this one was raised from (an
    #: inter-company book transfer chain), same format as the ref above -
    #: raw pass-through, never resolved into an id.
    from_po_line_ref = Column(String(255), nullable=True)
    #: The source purchase order's own document number, alongside the ref.
    from_po_number = Column(String(100), nullable=True)
    # The cross-book case of a sales-order line reference - the sales order
    # lives in ANOTHER AutoCount database, so its key cannot resolve here.
    # Recorded raw, verbatim from the payload's `from_so_external` object -
    # the smallest honest place for a fact that never becomes a Sorento id
    # (see `order_link_service.write_line_ref_claims`'s docstring for why
    # this is not a `scm.order_link_claim` row: that table's identity
    # requires a real `so_number`, which a cross-book key does not carry).
    from_so_external = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    purchase_order = relationship("PurchaseOrder", back_populates="lines")
    product = relationship("Product")
    warehouse = relationship("Warehouse")

    __table_args__ = (
        Index("ix_purchase_order_lines_purchase_order_id", "purchase_order_id"),
        Index("ix_purchase_order_lines_product_id", "product_id"),
        Index("ix_purchase_order_lines_warehouse_id", "warehouse_id"),
        Index("ix_purchase_order_lines_product_warehouse_status", "product_id", "warehouse_id", "line_status"),
        # S3 perf quick wins (AC-3.1, migration 456_reorder_perf_quickwins) - every
        # decision-list / plans-list read resolves a recommendation's draft/active PO
        # line by this pair (`decision_service._po_for_rec` / `_pos_for_recs` /
        # `_refresh_run_counts`). Declared on the model, not just created by the
        # migration's raw DDL: CI's `bootstrap_env` builds its schema from
        # `Base.metadata.create_all` and stamps alembic to head WITHOUT running any
        # migration body (`.github/workflows/deploy.yml` - "alembic upgrade head
        # cannot build this schema from zero"), so an index that exists only as a
        # migration's `op.execute("CREATE INDEX ...")` is invisible there - found by
        # `tests/scm/test_s3_reorder_perf_quickwins.py::TestIndexUsage::
        # test_the_index_exists` failing in CI while passing against the hand-migrated
        # shared dev DB.
        Index("ix_purchase_order_lines_source_ref_system", "source_ref", "source_system"),
    )


class StockInquiry(Base):
    """Stock inquiry model. Set __audit_track__ = True for automatic audit logging of changes."""
    __tablename__ = "stock_inquiries"
    # NOTE: `salesperson_contact_id` (the routing key, below) is the requestor
    # the salesman the inquiry is FOR. `contact_id` stays the SUBMITTER, who owns
    # the conversation and receives every update.
    __audit_track__ = True
    __audit_entity_type__ = "stock_inquiry"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    inquiry_number = Column(String(50), nullable=True)
    # Display label (what was submitted); kept for PDFs, list columns and search.
    salesperson = Column(Text, nullable=True)
    # Requestor FK - the CS pin lookup key. TEXT to match respond_contacts.id.
    salesperson_contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    # lazy="joined": the display name is read on every list row, so resolve it in
    # the same SELECT instead of one extra query per row.
    salesperson_contact = relationship(
        "RespondContact", foreign_keys=[salesperson_contact_id], lazy="joined"
    )

    @property
    def salesperson_contact_name(self) -> "str | None":
        """Requestor display name resolved LIVE from the FK, so a contact rename
        fixes every screen with no backfill. Read-only; the `salesperson` text
        column remains the point-in-time record and the fallback once the FK is
        cleared."""
        return _contact_display_name(self.salesperson_contact)
    product_code = Column(Text, nullable=True)
    item_description = Column(Text, nullable=True)
    project_customer = Column(Text, nullable=True)
    project_name = Column(Text, nullable=True)
    quantity = Column(Text, nullable=True)
    delivery_date = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    additional_remark = Column(Text, nullable=True)
    purchasing_response = Column(Text, nullable=True)
    contact_id = Column(Text, nullable=True)
    space_id = Column(Text, nullable=True)
    respond_inbox_url = Column(Text, nullable=True)
    status = Column(String(50), default="new", nullable=False)
    portal_draft_at = Column(DateTime(timezone=False), nullable=True)  # set while user is editing in submission portal; cleared on Submit
    last_responded_by = Column(Text, nullable=True)
    last_responded_at = Column(DateTime(timezone=False), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=False), nullable=True)
    rejected_by = Column(Text, nullable=True)
    rejected_from = Column(String(50), nullable=True)  # pending_project_sales | pending_purchasing; used on reopen
    reopen_reason = Column(Text, nullable=True)
    reopened_at = Column(DateTime(timezone=False), nullable=True)
    reopened_by = Column(Text, nullable=True)
    # Void (terminal, irreversible). DB FK voided_by -> users.id added in migration
    # 287_form_void (kept off the model, mirroring rejected_by/reopened_by).
    void_reason = Column(Text, nullable=True)
    voided_by = Column(Text, nullable=True)  # users.id of the actor who voided
    voided_at = Column(DateTime(timezone=False), nullable=True)
    # Portal submission revisions (PLAN-portal-submission-revisions), denormalized so the
    # list badge and the revision fence cost no per-row query. Contact-initiated
    # revisions only - the full lineage (including resubmissions) lives in
    # portal_form_revisions. The reason is read from the latest revision row, never
    # copied here, so there is nothing to drift.
    revision_no = Column(Integer, nullable=False, server_default="0", default=0)
    last_revised_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=True)

    @property
    def response_write_allowed(self) -> bool:
        """Whether ``purchasing_response`` may be written at this status (UAC O1).

        Read straight off ``response_gate`` - the same module the write path raises
        from - so the flag the UI gates on and the rule the server enforces cannot
        disagree. Derived live; never a column, nothing to backfill.
        """
        from app.services.response_gate import STOCK_INQUIRY, is_response_status_allowed

        return is_response_status_allowed(STOCK_INQUIRY, self.status)

    __table_args__ = (
        Index("ix_stock_inquiries_product_code", "product_code"),
        Index("ix_stock_inquiries_delivery_date", "delivery_date"),
        Index("ix_stock_inquiries_created_at", "created_at"),
        Index("ix_stock_inquiries_inquiry_number", "inquiry_number"),
    )


class PurchaseRequestHeader(Base):
    """Purchase request / sponsorship form header. Set __audit_track__ = True for automatic audit logging."""
    __tablename__ = "purchase_requests"
    __audit_track__ = True
    __audit_entity_type__ = "purchase_request"  # API uses this entity_type for both PR and sponsorship

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_type = Column(String(50), nullable=False)  # purchase_request | sponsorship_form
    request_number = Column(String(50), nullable=True)
    request_date = Column(Date, nullable=True)
    customer_name = Column(Text, nullable=True)
    # Person in charge at the delivery site: free text, "name and contact number".
    # Optional. Exists so the receiving contact stops being appended to
    # delivery_address (see migration 313).
    pic = Column(Text, nullable=True)
    project_title = Column(Text, nullable=True)
    # AC-F3: ONE form, not two. The link is nullable because every existing sponsorship
    # has none, and because an unflagged contact is deliberately still allowed to submit
    # without one. project_title stays as the display fallback (AC-F6) -- the ~28 real
    # rows are linked by hand, and no fuzzy backfill writes a link nobody checked.
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True
    )
    purpose = Column(Text, nullable=True)
    delivery_address = Column(Text, nullable=True)  # sponsorship form
    total_project_value = Column(Numeric(15, 2), nullable=True)  # sponsorship form (numeric)
    total_project_value_text = Column(Text, nullable=True)  # descriptive e.g. "BULK ORDER EST RM1.6MIL"
    sponsor_subject = Column(Text, nullable=True)  # sponsorship: showroom/mockup/others (lookup-bound)
    sponsor_subject_other = Column(Text, nullable=True)  # sponsorship: free-text detail when sponsor_subject='others'
    sales_type = Column(String(50), nullable=True)  # PR: project/cash_sales (lookup-bound); routes CS assignment. SF leaves NULL.
    expected_delivery_date = Column(Date, nullable=True)
    expected_po_date = Column(Date, nullable=True)
    expected_po_date_text = Column(Text, nullable=True)
    # Display label (what was submitted); kept for PDFs, list columns and search.
    requested_by = Column(Text, nullable=True)
    # Requestor FK - the CS pin lookup key, so a form submitted ON BEHALF OF
    # someone routes to THEIR pinned CS. `contact_id` stays the submitter, who
    # keeps receiving every update. TEXT to match respond_contacts.id.
    requested_by_contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_contact = relationship(
        "RespondContact", foreign_keys=[requested_by_contact_id], lazy="joined"
    )

    @property
    def requested_by_contact_name(self) -> "str | None":
        """Requestor display name resolved LIVE from the FK (see
        StockInquiry.salesperson_contact_name)."""
        return _contact_display_name(self.requested_by_contact)
    requested_at = Column(Date, nullable=True)  # DEPRECATED - superseded by submitted_at (top date) + request_date (footer date)
    submitted_at = Column(DateTime(timezone=False), nullable=True)  # auto-stamped on every submit (incl. resubmit); top "Date" on the document
    status = Column(String(50), default="draft", nullable=False)
    portal_draft_at = Column(DateTime(timezone=False), nullable=True)  # set while user is editing in submission portal; cleared on Submit
    source = Column(String(50), default="external", nullable=False)
    external_reference = Column(Text, nullable=True)
    contact_id = Column(Text, nullable=True)
    space_id = Column(Text, nullable=True)
    respond_inbox_url = Column(Text, nullable=True)
    approver_user_id = Column(String(100), nullable=True)
    approver_email = Column(String(255), nullable=True)
    requested_approval_by_user_id = Column(String(100), nullable=True)  # user who set to pending / sent approval link
    approval_status = Column(String(50), nullable=True)  # pending | approved | rejected
    approved_at = Column(DateTime(timezone=False), nullable=True)
    approved_by = Column(Text, nullable=True)
    # Dedicated rejecter user id (users.id). Populated on both reject paths so the
    # rejection banner can resolve a name + wa.me phone. NULL for external-email
    # approvers and legacy rejections (approved_by then holds only a name string).
    rejected_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approval_signature_ref = Column(Text, nullable=True)
    approval_comments = Column(Text, nullable=True)
    # Void (terminal, irreversible). DB FK voided_by -> users.id added in migration
    # 287_form_void (kept off the model, mirroring approver_user_id String(100)).
    void_reason = Column(Text, nullable=True)
    voided_by = Column(String(100), nullable=True)  # users.id of the actor who voided
    voided_at = Column(DateTime(timezone=False), nullable=True)
    # Portal submission revisions, denormalized (see StockInquiry.revision_no). One pair
    # of columns serves BOTH request types - purchase_request and sponsorship_form share
    # this table.
    revision_no = Column(Integer, nullable=False, server_default="0", default=0)
    last_revised_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    lines = relationship(
        "PurchaseRequestLine",
        back_populates="purchase_request",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_purchase_requests_request_type", "request_type"),
        Index("ix_purchase_requests_request_date", "request_date"),
        Index("ix_purchase_requests_customer_name", "customer_name"),
    )


class PurchaseRequestLine(Base):
    __tablename__ = "purchase_request_lines"
    __audit_parent__ = "purchase_request_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_request_id = Column(
        UUID(as_uuid=False),
        ForeignKey("purchase_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_code = Column(Text, nullable=True)
    quantity = Column(Numeric(15, 2), nullable=True)
    remark = Column(Text, nullable=True)
    unit_price = Column(Numeric(15, 2), nullable=True)  # sponsorship form line
    total = Column(Numeric(15, 2), nullable=True)  # sponsorship form line (qty * unit_price)
    sort_order = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    purchase_request = relationship("PurchaseRequestHeader", back_populates="lines")

    __table_args__ = (
        Index("ix_purchase_request_lines_purchase_request_id", "purchase_request_id"),
    )


class ApprovalToken(Base):
    """One-time token for public approval/sign links (no login)."""
    __tablename__ = "approval_tokens"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=False)
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires = Column(DateTime(timezone=False), nullable=False)
    used_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_approval_tokens_entity_type_entity_id", "entity_type", "entity_id"),
    )


class ViewToken(Base):
    """Reusable token for public view links (no login, read-only). One per purchase request."""
    __tablename__ = "view_tokens"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=False)
    token = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_view_tokens_entity_type_entity_id", "entity_type", "entity_id", unique=True),
    )
