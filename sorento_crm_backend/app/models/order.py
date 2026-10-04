"""Order management models."""
from sqlalchemy import (
    CheckConstraint,
    Column,
    String,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Text,
    Numeric,
    Index,
    Integer,
    SmallInteger,
    UniqueConstraint,
    event,
    text,
    BigInteger,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base import CompanyScopedMixin
import uuid


def _uuid_str():
    return str(uuid.uuid4())


class OrderStatus(Base):
    __tablename__ = "order_statuses"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    status_code = Column(String(50), unique=True, nullable=False)
    status_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    sequence = Column(Integer, default=0, nullable=False)
    is_final_status = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    
    orders = relationship("Order", back_populates="order_status")
    
    __table_args__ = (
        Index("ix_order_statuses_sequence", "sequence"),
        Index("ix_order_statuses_is_final_status", "is_final_status"),
    )


class CustomerGroup(Base, CompanyScopedMixin):
    """One company's customer, made of several ledgers (`customers` rows).

    The chatbot used to join ledgers by the name rule (`ledger_family_key`), which merges
    different legal entities and cannot join one owner's two names. A group is the office's
    own say-so; a ledger with no group still falls back to the name rule.
    """

    __tablename__ = "customer_groups"
    __audit_track__ = True
    __audit_entity_type__ = "customer_group"
    __audit_columns__ = ["name"]

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_customer_groups_company_name_lower", "company_id", func.lower(name), unique=True),
    )


class Customer(Base, CompanyScopedMixin):
    __tablename__ = "customers"
    # Who changed this customer, and from what. Every write path is ORM
    # (CustomerService for the edit form, the order import's debtor upsert, the
    # customer import), so one flush listener covers all of them - but only in a
    # process that HAS that listener. app/main.py registers it at startup, so the
    # API covers the edit form; worker.py registers the company-scope listeners
    # only, so nothing an RQ job writes is audited per row today. That is why the
    # customer import writes its own coarse job-level row.
    __audit_track__ = True
    # Singular, matching the coarse row the customer import already writes.
    # `_CUSTOMER_ENTITY_TYPE` in app/tasks/import_tasks.py suppresses the per-row
    # audit by this exact string, so the two are pinned together by a test - a
    # mismatch would silently restore 4,000 rows per import.
    __audit_entity_type__ = "customer"
    # Identity, contact details and commercial classification: the fields a person
    # sets and later has to account for. Left out on purpose: `id` is the audit
    # row's own entity_id, `company_id` its own column, and created/updated
    # bookkeeping is what `changed_at` + `user_id` already say - repeating them
    # inside every diff is noise, not history.
    __audit_columns__ = [
        "customer_code",
        "customer_name",
        "email",
        "phone_number",
        "mobile_number",
        "is_active",
        "registered_name",
        "trading_name",
        "registration_number",
        "industry",
        "website",
        "billing_address",
        "country",
        "tax_id",
        "notes",
        "account_owner_user_id",
        "customer_type",
        "salutation",
        "first_name",
        "last_name",
        "market_segment_code",
        "region",
        # Who handles the customer: assigned from the customer form, the contact card's
        # agent column and the sales agent's Customers tab, so a move has to be traceable.
        "sales_agent_id",
        "account_level",
        "customer_group_id",
    ]

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Not column-unique. Real customers can share a single code across
    # multiple debtor names (e.g. "300-D093" maps to both "Deluxe Home Center
    # (KTN)" and "Deluxe Home Center AC (I)"). Uniqueness enforced via the
    # composite functional index in __table_args__ below: lower(customer_code)
    # + lower(customer_name) must be distinct.
    customer_code = Column(String(50), nullable=False)
    customer_name = Column(String(255), nullable=False)
    email = Column(String(150), nullable=True)
    phone_number = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    # Extended profile (added by commercial_core to support lead/customer flows)
    registered_name = Column(String(255), nullable=True)
    trading_name = Column(String(255), nullable=True)
    registration_number = Column(String(100), nullable=True)
    industry = Column(String(120), nullable=True)
    website = Column(String(500), nullable=True)
    billing_address = Column(JSONB, nullable=True)
    country = Column(String(100), nullable=True)
    tax_id = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    account_owner_user_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    customer_type = Column(String(20), nullable=False, server_default="company")
    salutation = Column(String(32), nullable=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    mobile_number = Column(String(50), nullable=True)
    # SCM (M2): demand-nature channel routing via the customer's market segment.
    market_segment_code = Column(String(50), ForeignKey("market_segments.code", ondelete="SET NULL"), nullable=True)
    # AutoCount `Debtor.AreaCode` (migration 476, D16). Free text, not an FK -
    # AutoCount states no reference table for it.
    region = Column(String(80), nullable=True)
    # How this row got here. Set to 'project_lead' by the lead wizard, which can
    # create a customer for an organisation that has never bought anything: order and
    # invoice pickers can filter prospects out if the noise becomes real. Null on the
    # 2,391 pre-existing rows, which is the honest answer for them.
    source = Column(String(32), nullable=True)
    # Accounts receivable, ingested from AutoCount (D23). Here rather than computed
    # because AutoCount is the ledger: without it a credit warning can only compare
    # one order's value against a limit, which ignores everything already owed.
    ar_outstanding = Column(Numeric(15, 2), nullable=True)
    ar_ageing_json = Column(JSONB, nullable=True)  # {"current":..,"30":..,"60":..,"90+":..}
    ar_as_of = Column(DateTime(timezone=False), nullable=True)
    # Links customer/dealer to their assigned salesperson. Source for the portal
    # debtor dropdown (customers assigned to the contact's linked agent).
    sales_agent_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sales_agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Which numbered account this ledger is ("account 1" in a chatbot message selects the
    # ledger at level 1). NULL = not a numbered account. A setting the office edits; the
    # name marker is read once, by migration acct_ledger_0001's seed, never again.
    account_level = Column(SmallInteger, nullable=True)
    # The group this ledger belongs to (see `CustomerGroup`). NULL = ungrouped.
    customer_group_id = Column(
        UUID(as_uuid=False),
        ForeignKey("customer_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    orders = relationship("Order", back_populates="customer")
    customer_contacts = relationship(
        "CustomerContact",
        back_populates="customer",
        cascade="all, delete-orphan",
    )
    # The salesperson master row `sales_agent_id` points at. Eager (the customers list
    # draws every row, and a per-row lazy load would be one query per customer for a
    # single code + name), but `selectin` rather than `joined`: `joined` chains through
    # `SalesAgent.contact` (also `lazy="joined"`, `app/models/sales_agent.py`), so it added
    # TWO LEFT OUTER JOINs to every `Customer` load, including ones that never read an
    # agent field at all - `/customers/select` (~6,397 rows, unpaged by default) and the
    # importer's bulk `query(Customer)` (PR #1177 review, nit 5). `selectin` is still one
    # extra query for the whole result set (never per-row), just not joined onto rows that
    # do not ask for it.
    sales_agent = relationship("SalesAgent", lazy="selectin")
    # `selectin` for the same reason as `sales_agent`: the list draws the group name on
    # every row, and this is one extra query for the page, never one per row.
    customer_group = relationship("CustomerGroup", lazy="selectin")

    @property
    def customer_group_name(self):
        """The group's name, for `CustomerResponse` (no UUID is ever shown)."""
        group = self.customer_group
        return group.name if group else None

    @property
    def sales_agent_code(self):
        """The agent code (`sales_agents.sales_agent`), for `CustomerResponse`.

        A UUID is never shown in the UI (Cursor rule), so the response carries this
        alongside the id.
        """
        agent = self.sales_agent
        return agent.sales_agent if agent else None

    @property
    def sales_agent_name(self):
        """The person behind the code (`sales_agents.person_label`), same reason as
        `sales_agent_code` - null until the captain annotates the agent."""
        agent = self.sales_agent
        return agent.person_label if agent else None

    __table_args__ = (
        Index("ix_customers_is_active", "is_active"),
        Index("ix_customers_customer_code", "customer_code"),
        Index("ix_customers_account_owner_user_id", "account_owner_user_id"),
        Index("ix_customers_customer_group_id", "customer_group_id"),
        CheckConstraint("account_level >= 1", name="ck_customers_account_level"),
        # Composite uniqueness - see column docstring. Created as a functional
        # UNIQUE INDEX by migration 220 so case + whitespace differences don't
        # produce silent duplicates, then re-created WITH company_id by migration
        # 305: the same code+name legally exists once per company (884 pairs are
        # held by both Sorento and Mocha today), so the pre-305 global shape would
        # reject a real row. `products` was updated to its composite at the time
        # and this one was not, which mattered because a test building its schema
        # from `Base.metadata.create_all` got the GLOBAL index and failed on data
        # production accepts. Keep this in step with the live index.
        Index(
            "uq_customers_company_code_name_lower",
            "company_id",
            func.lower(func.btrim(customer_code)),
            func.lower(func.btrim(customer_name)),
            unique=True,
        ),
    )


@event.listens_for(Customer, "init")
def _assign_customer_id(target, args, kwargs):  # noqa: ANN001
    """Give a new customer its id at construction, not at flush.

    The audit listener runs in ``before_flush``, where a primary key still waiting on
    its column default reads as ``None``; the row is then skipped as "no PK yet" and
    the create goes unrecorded. The importer already assigned the id by hand for its
    own reasons - doing it here instead means every construction site is audited,
    including ones written later, rather than whichever ones remembered.

    A caller that supplies its own id keeps it.
    """
    kwargs.setdefault("id", str(uuid.uuid4()))


class CustomerContact(Base, CompanyScopedMixin):
    """Main or stakeholder person linked to a business customer profile."""

    __tablename__ = "customer_contacts"
    __audit_parent__ = "customer_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String(255), nullable=False)
    job_title = Column(String(120), nullable=True)
    email = Column(String(150), nullable=True)
    phone = Column(String(50), nullable=True)
    mobile = Column(String(50), nullable=True)
    sync_source = Column(String(32), nullable=False, default="manual")
    contact_role = Column(String(20), nullable=False, default="stakeholder")
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    customer = relationship("Customer", back_populates="customer_contacts")

    __table_args__ = (
        CheckConstraint(
            "contact_role IN ('main', 'stakeholder')",
            name="ck_customer_contacts_contact_role",
        ),
        Index("ix_customer_contacts_customer_id", "customer_id"),
        Index(
            "uq_customer_contacts_one_main_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("contact_role = 'main'"),
        ),
    )


class Transporter(Base, CompanyScopedMixin):
    """Lookup table for distinct transporter names referenced by orders.transporter.

    Seeded by migration 200_transporters_table from existing orders. `orders.transporter`
    (free-text) is retained for legacy compat; `orders.transporter_id` is the canonical FK.
    """
    __tablename__ = "transporters"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique indexes
    # in __table_args__ below. A bare unique=True here would make create_all
    # build the old global uq_transporters_code/uq_transporters_normalized_name
    # and reject the same code/name in a second company (fix round 4).
    code = Column(String(100), nullable=False)
    name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index(
            "uq_transporters_company_code", "company_id", "code", unique=True
        ),
        Index(
            "uq_transporters_company_normalized_name",
            "company_id", "normalized_name",
            unique=True,
        ),
    )


class Order(Base, CompanyScopedMixin):
    __tablename__ = "orders"
    __audit_track__ = True  # who changed what (Sub-plan D Tier-2)

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    # Unique PER COMPANY, not globally (migration 305): composite unique index in
    # __table_args__ below. A bare unique=True would rebuild the old global
    # orders_order_number_key under create_all and reject the same number in a
    # second company.
    order_number = Column(String(100), nullable=False)
    order_date = Column(Date, nullable=True)
    estimated_delivery_date = Column(Date, nullable=True)
    actual_delivery_date = Column(Date, nullable=True)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    order_status_id = Column(UUID(as_uuid=False), ForeignKey("order_statuses.id", ondelete="SET NULL"), nullable=True)
    transporter_id = Column(UUID(as_uuid=False), ForeignKey("transporters.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    updated_by = Column(UUID(as_uuid=False), nullable=True)
    billing_address_id = Column(UUID(as_uuid=False), nullable=True)
    shipping_address_id = Column(UUID(as_uuid=False), nullable=True)
    created_time = Column(DateTime(timezone=False), nullable=True)
    debtor_code = Column(String(100), nullable=True)
    debtor_name = Column(String(255), nullable=True)
    agent = Column(String(100), nullable=True)
    is_cancelled = Column(Boolean, default=False, nullable=False)
    remarks_cs = Column(Text, nullable=True)
    order_type = Column(String(50), nullable=True)
    pickup_time = Column(String(20), nullable=True)
    checker = Column(String(100), nullable=True)
    transporter = Column(String(100), nullable=True)
    driver_name = Column(String(100), nullable=True)
    lorry_plate = Column(String(50), nullable=True)
    customer_ref = Column(String(255), nullable=True)
    delivery_remarks_cs = Column(Text, nullable=True)
    delivery_remarks = Column(Text, nullable=True)
    salesman = Column(String(100), nullable=True)
    trips = Column(Integer, nullable=True)
    warehouse = Column(String(50), nullable=True)
    delivery_days = Column(Integer, nullable=True)
    kpi_warning = Column(Boolean, default=False, nullable=False)
    subtotal_amount = Column(Numeric(15, 2), default=0, nullable=False)
    discount_amount = Column(Numeric(15, 2), default=0, nullable=False)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), default=0, nullable=False)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(DateTime(timezone=False), nullable=True)
    synced_to_excel = Column(Boolean, default=False, nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    # AutoCount DO ingest (#1354 S2, plan 2.2). A row is AutoCount-OWNED when `doc_key` is
    # set: the ingest then owns every column below plus order_number / order_date /
    # created_time / customer_id / debtor_* / agent / remarks / is_cancelled / the money
    # columns and all lines, and the tracking upload keeps its own (plan section 3).
    source_book = Column(String(20), nullable=True)
    doc_key = Column(BigInteger, nullable=True)
    source_modified_at = Column(DateTime(timezone=True), nullable=True)
    source_vanished_at = Column(DateTime(timezone=True), nullable=True)
    last_synced_at = Column(DateTime(timezone=False), nullable=True)
    source_record = Column(JSONB, nullable=True)
    branch_code = Column(String(100), nullable=True)
    branch_name = Column(String(255), nullable=True)
    deliver_address = Column(Text, nullable=True)
    deliver_contact = Column(String(255), nullable=True)
    deliver_phone = Column(String(100), nullable=True)
    ship_via = Column(String(100), nullable=True)
    ship_info = Column(String(255), nullable=True)
    ref = Column(String(255), nullable=True)
    ref_doc_no = Column(String(100), nullable=True)
    sales_order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("sales_orders.id", ondelete="SET NULL", name="fk_orders_sales_order_id"),
        nullable=True,
    )
    description = Column(Text, nullable=True)
    doc_status = Column(String(20), nullable=True)
    currency_code = Column(String(10), nullable=True)
    currency_rate = Column(Numeric(18, 8), nullable=True)
    local_net_total = Column(Numeric(15, 2), nullable=True)

    customer = relationship("Customer", back_populates="orders")
    order_status = relationship("OrderStatus", back_populates="orders")
    lines = relationship(
        "OrderLine",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLine.line_sequence",
    )

    __table_args__ = (
        Index("uq_orders_company_order_number", "company_id", "order_number", unique=True),
        Index("ix_orders_customer_id", "customer_id"),
        Index("ix_orders_order_status_id", "order_status_id"),
        Index("ix_orders_order_date", "order_date"),
        Index("ix_orders_order_number", "order_number"),
        Index("ix_orders_created_by", "created_by"),
        Index("ix_orders_deleted_at", "deleted_at"),
        Index("ix_orders_debtor_code", "debtor_code"),
        Index("ix_orders_kpi_warning", "kpi_warning"),
        Index("ix_orders_is_cancelled", "is_cancelled"),
        Index(
            "uq_orders_company_book_doc_key",
            "company_id", "source_book", "doc_key",
            unique=True,
            postgresql_where=text("doc_key IS NOT NULL"),
        ),
        Index("ix_orders_sales_order_id", "sales_order_id"),
    )


class OrderLine(Base, CompanyScopedMixin):
    """Delivery order detail line: product + warehouse + qty + pricing."""
    __tablename__ = "order_lines"
    __audit_skip__ = "line table, 699 to 32,458 rows a day (measured 27 Sep 2026, review B3)"
    __audit_parent__ = "order_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    line_sequence = Column(Integer, nullable=False, default=1)
    order_id = Column(
        UUID(as_uuid=False),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id = Column(
        UUID(as_uuid=False),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    warehouse_id = Column(
        UUID(as_uuid=False),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity = Column(Numeric(15, 4), default=0, nullable=False)
    unit_price = Column(Numeric(15, 4), nullable=True)
    discount = Column(Numeric(15, 4), default=0, nullable=True)
    total = Column(Numeric(15, 4), nullable=True)
    tax = Column(Numeric(15, 4), nullable=True)
    total_excluding_tax = Column(Numeric(15, 4), nullable=True)
    total_including_tax = Column(Numeric(15, 4), nullable=True)
    # The sales order line this DO line delivers (AutoCount's "transferred from"), the seam
    # sales targets count delivered quantities by DO date through (sales plan 3.2, 16.1).
    # Filled by the DO integration; NULL on every line uploaded before it. SET NULL, so
    # deleting a sales order line never deletes a delivery.
    sales_order_line_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "sales_order_lines.id",
            ondelete="SET NULL",
            name="fk_order_lines_sales_order_line_id",
        ),
        nullable=True,
    )
    # AutoCount DO ingest (#1354 S2, plan 2.3): identity, typed columns and what the line
    # says it was transferred from (kept as sent; `sales_order_line_id` is the resolved link).
    dtl_key = Column(BigInteger, nullable=True)
    item_code = Column(String(100), nullable=True)
    location_code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    uom = Column(String(30), nullable=True)
    foc_qty = Column(Numeric(15, 4), nullable=True)
    discount_text = Column(String(50), nullable=True)
    batch_no = Column(String(100), nullable=True)
    delivery_date = Column(Date, nullable=True)
    proj_no = Column(String(50), nullable=True)
    your_po_no = Column(String(100), nullable=True)
    your_po_date = Column(Date, nullable=True)
    from_doc_type = Column(String(10), nullable=True)
    from_doc_no = Column(String(100), nullable=True)
    from_dtl_key = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    order = relationship("Order", back_populates="lines")
    product = relationship("Product", backref="order_lines")
    warehouse = relationship("Warehouse", backref="order_lines")

    __table_args__ = (
        Index("ix_order_lines_order_id", "order_id"),
        Index("ix_order_lines_product_id", "product_id"),
        Index("ix_order_lines_warehouse_id", "warehouse_id"),
        Index(
            "ix_order_lines_order_product_warehouse",
            "order_id",
            "product_id",
            "warehouse_id",
            unique=False,
        ),
        UniqueConstraint("order_id", "line_sequence", name="uq_order_lines_order_id_line_sequence"),
        Index("ix_order_lines_sales_order_line_id", "sales_order_line_id"),
        Index(
            "uq_order_lines_order_dtl_key",
            "order_id", "dtl_key",
            unique=True,
            postgresql_where=text("dtl_key IS NOT NULL"),
        ),
    )


class SalesOrder(Base, CompanyScopedMixin):
    """SCM sales order (demand / committed source). Public core record - survives
    module uninstall. Sits with Order/DO in the order domain."""
    __tablename__ = "sales_orders"
    __audit_skip__ = "AutoCount sales order mirror written by the ESB sync, 10,829 to 68,710 rows a day (measured 27 Sep 2026, review B3); S1 records the ingest verdict as an event"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    # Unique per COMPANY, not globally: migration 305 dropped
    # `sales_orders_so_number_key` and created `uq_sales_orders_company_so_number`
    # over (company_id, so_number). The model kept the old global flag, so a
    # schema built from the models (CI, the scratch fixture) could not hold one
    # number in two companies at all - which is what production holds.
    so_number = Column(String(100), nullable=False)
    customer_id = Column(UUID(as_uuid=False), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    # The debtor code the source document printed, kept whether or not it resolves to a
    # customer. Both SO importers write it: an order whose code names nobody we hold is
    # still attributable ("Debtor 300-R009"), and the day that customer is created the code
    # is what makes the link fixable. Never a substitute for the FK - `customer_id` stays
    # the link, this is the evidence behind it.
    debtor_code = Column(String(64), nullable=True)
    # The customer name the source document was issued under (CUSTOMER-CODE-IDENTITY,
    # owner decision 30 Sep 2026): AutoCount lets a user edit the debtor name on the SO
    # itself, so it is per document, and it is what every SO-facing screen shows, with
    # the master name only as the fallback for an order that carries none. The master
    # `customers.customer_name` is never written from a document. Migration sdn_0001.
    debtor_name = Column(String(255), nullable=True)
    # Who sold it, as the salesperson master (`sales_agents`), resolved from the agent code
    # the extract states. Nullable because most orders predate the column and no export is
    # required to carry an agent; `SET NULL` because deleting a salesperson must not delete
    # their orders. It is also the fourth and last source the demand class is read from.
    sales_agent_id = Column(UUID(as_uuid=False), ForeignKey("sales_agents.id", ondelete="SET NULL"), nullable=True)
    order_date = Column(Date, nullable=True)
    # Customer-requested delivery / ship-by date (M1-D14). Distinct from order_date
    # (when the SO was raised); drives the FE "requested_delivery_date" column.
    requested_delivery_date = Column(Date, nullable=True)
    order_type = Column(String(50), nullable=True)  # lookup code (continuous / spike vocab)
    # project | retail, resolved from the customer's market segment at import. Belongs to
    # the DEMAND, not the buyer: the same customer can place both kinds, and fulfilment
    # priority is decided by this. Nullable because a row whose class cannot be resolved is
    # rejected at import rather than silently defaulted to retail.
    demand_class = Column(String(32), nullable=True)
    # Which feed ORIGINATED this order - 'scm_order_inquiry' when the Order Inquiry sheet
    # created it. Its own column, never source_system: adoption by the outstanding extract
    # overwrites source_system to take ownership, and project demand is keyed on origin
    # (S13b), so reading origin off source_system would delete demand as a side effect of
    # the handover. Stamped at creation, never rewritten.
    demand_origin = Column(String(32), nullable=True)
    priority = Column(String(20), nullable=True)
    status = Column(String(50), default="open", nullable=False)
    source_system = Column(String, nullable=True)
    source_ref = Column(String, nullable=True)
    # The document number in the system this order came from, and free text carried with it.
    # Both columns already existed on the table and were simply not mapped, so every read
    # through the ORM silently dropped them - the Order Inquiry feed needs `internal_note` to
    # keep a project name it cannot resolve to a customer.
    source_doc_no = Column(String, nullable=True)
    internal_note = Column(Text, nullable=True)
    # The project this order belongs to, in the reader's own words - never a UUID. Resolved
    # by `app.services.project_label_rules` from whichever of four sources ranks highest
    # (see `project_label_source` below); a manual correction (rank 5) is a later slice.
    # Nothing ever clears it once written.
    project_label = Column(Text, nullable=True)
    # Which rule wrote `project_label`: `inquiry` (the Order Inquiry sheet) > `note` (the
    # AutoCount note's PROJECT line) > `ref` (AutoCount `SO.Ref`) > `delivery` (a delivery
    # address in the note). Decides whether a later, lower-ranked write is allowed to
    # overwrite it (`apply_project_label`).
    project_label_source = Column(String(16), nullable=True)
    # AutoCount's SO header `Transferable` (T/F), owned by AutoCount and written only by the
    # ESB ingest (`document_ingest_service`). F means "not confirmed yet for the queue"
    # (owner, 1 Oct 2026), so the Stock Debt view does not count the order. NULL means the
    # source never said - every order that predates the field or came from Excel - and
    # counts like T. Migration sotr_0001.
    is_transferable = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    customer = relationship("Customer")
    lines = relationship(
        "SalesOrderLine",
        back_populates="sales_order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # A unique INDEX rather than a UniqueConstraint, which is the object
        # migration 305 actually creates, and the form `Product` already carries.
        Index(
            "uq_sales_orders_company_so_number", "company_id", "so_number", unique=True
        ),
        Index("ix_sales_orders_customer_id", "customer_id"),
        Index("ix_sales_orders_debtor_code", "debtor_code"),
        Index("ix_sales_orders_so_number", "so_number"),
        Index("ix_sales_orders_status", "status"),
        Index("ix_sales_orders_sales_agent_id", "sales_agent_id"),
        # Sales targets bucket achievement by order date (sales plan 3.1, 16.1).
        Index("ix_sales_orders_order_date", "order_date"),
        # `order_link_service.book_so_numbers_by_ref` filters `source_ref IN (...)` to
        # resolve the book's own SO linkage on a purchase-order line (review of PR #764,
        # F3) - unindexed, this was a sequential scan over the whole table on every PO
        # detail read. Partial: `source_ref` is null on every sales order that did not
        # arrive through the book, which is most rows on an older order book.
        Index(
            "ix_sales_orders_source_ref", "source_ref",
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
    )


class SalesOrderLine(Base, CompanyScopedMixin):
    """Open SO line - feeds committed / net-position views by product×warehouse."""
    __tablename__ = "sales_order_lines"
    __audit_skip__ = "AutoCount sync line table, 47,507 to 286,917 rows a day (measured 27 Sep 2026, review B3)"
    __audit_parent__ = "sales_order_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    sales_order_id = Column(UUID(as_uuid=False), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=True)
    qty_ordered = Column(Numeric(15, 4), default=0, nullable=False)
    qty_delivered = Column(Numeric(15, 4), default=0, nullable=False)
    # What the customer pays for this line. The column has always been on the table and
    # was simply never mapped (same as `source_doc_no` / `internal_note` on the header
    # above), so every read through the ORM dropped it and a schema built from the models
    # did not carry it at all. Mapped now because the demand drill quotes it: "who ordered
    # it and at what price" is the question the buyer opens that popover with.
    unit_price = Column(Numeric(15, 2), nullable=True)
    # The rest of the money the sales book states per line, and the reason the detail page
    # could only ever print a unit price beside a quantity. Both NULL when the file said
    # nothing: a discount of 0 claims a discount of nothing was given, and a line total of
    # 0 claims the line was free - neither is a fact any export handed us.
    # `line_total` is the file's `Total (Inc)` - what the customer was actually charged
    # for the line, after the discount - and it is what the header's `total_amount` sums.
    discount = Column(Numeric(15, 2), nullable=True)
    line_total = Column(Numeric(15, 2), nullable=True)
    priority = Column(String(20), nullable=True)  # inherit from header / override
    # When this line's quantity must be on hand. PER LINE, not per header: the order
    # inquiry the business works from states a delivery date per line and one SO routinely
    # carries several, so the header's requested_delivery_date cannot drive netting.
    # Without this the Coverage Timeline has no time axis at all (ADR-0011).
    required_date = Column(Date, nullable=True)
    # A per-line override of the unit of measure, same family as `unit_price` above: the
    # column has always been on the table and was never mapped, so a stated UoM (e.g. a
    # line sold in "BOX" against a product whose base UOM is "PCS") silently fell back to
    # `_uom_for(product)` on every read. NULL means "nobody overrode it" - the serializer
    # still falls back to the product's base UOM in that case, not blank.
    uom = Column(String(100), nullable=True)
    # What purchasing should plan for, when that is not simply what is still owed to the
    # customer. NULL means nobody has said otherwise, so the netting falls back to
    # `qty_ordered - qty_delivered`. Set by the Order Inquiry feed, which is CS stating the
    # quantity they want covered; kept apart from `qty_ordered` so a sales-order amendment
    # and a purchasing decision cannot overwrite one another.
    qty_required = Column(Numeric(15, 4), nullable=True)
    # Where this line stands in purchasing. `not_reviewed` (nobody has looked at it yet, and
    # it still counts as demand), `needs_purchase` (CS says buy it, nothing placed),
    # `ordered` (a purchase order covers it), `covered` (CS ruled it out).
    purchasing_status = Column(
        String(24), default="not_reviewed", server_default="not_reviewed", nullable=False
    )
    line_status = Column(String(50), default="open", nullable=False)
    source_system = Column(String, nullable=True)
    source_ref = Column(String, nullable=True)
    # AutoCount's own `Seq` (PLAN-so-lines-autocount-order.md), kept as it arrives. NULL
    # means this line has never been numbered by AutoCount (order inquiry, upload,
    # absorbed history, manual, or an AutoCount line pushed before this column existed).
    line_no = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    sales_order = relationship("SalesOrder", back_populates="lines")
    product = relationship("Product")
    warehouse = relationship("Warehouse")

    __table_args__ = (
        Index("ix_sales_order_lines_sales_order_id", "sales_order_id"),
        Index("ix_sales_order_lines_product_id", "product_id"),
        Index("ix_sales_order_lines_warehouse_id", "warehouse_id"),
        Index("ix_sales_order_lines_product_warehouse_status", "product_id", "warehouse_id", "line_status"),
    )


class OrderInquiryConflict(Base, CompanyScopedMixin):
    """One disagreement between the Order Inquiry sheet's warehouse and the
    ESB's (migration 476, D22).

    The Order Inquiry import only ever writes `warehouse_id` where it is
    still NULL (`project_order_inquiry_import_service`); the ESB SO line
    writer overwrites a non-NULL warehouse when AutoCount states a different
    location, and records the disagreement here so the worklist can render
    it rather than the change vanishing silently.
    """
    __tablename__ = "order_inquiry_conflicts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    sales_order_id = Column(UUID(as_uuid=False), ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False)
    sales_order_line_id = Column(UUID(as_uuid=False), ForeignKey("sales_order_lines.id", ondelete="CASCADE"), nullable=False)
    previous_warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True)
    new_warehouse_id = Column(UUID(as_uuid=False), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True)
    # What overwrote the inquiry's warehouse - "autocount_esb" today, room for
    # a second source later without a schema change.
    source = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # `company_id`'s own index comes from `CompanyScopedMixin`'s `Column(index=True)`
    # (auto-named `ix_order_inquiry_conflicts_company_id`, same as the migration's
    # explicit one) - declaring it again here would collide.
    __table_args__ = (
        Index("ix_order_inquiry_conflicts_sales_order_id", "sales_order_id"),
    )
