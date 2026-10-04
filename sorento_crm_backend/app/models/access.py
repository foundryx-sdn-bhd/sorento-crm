"""Access control models."""
from sqlalchemy import CheckConstraint, Column, String, Boolean, DateTime, ForeignKey, Table, Text, Index, Integer, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base import CompanyScopedMixin
import uuid


class RespondContactCustomer(Base, CompanyScopedMixin):
    """Links a WhatsApp contact to the customer account they belong to.

    Not to be confused with ``customer_contacts``, which is a PERSON under a
    customer (name, job title, typed in by staff). This is the identity link:
    the phone number Respond.io knows, tied to the account that gets quoted and
    invoiced. A contact can exist with no customer (they have never bought
    anything) and a customer can exist with no contact (they were imported).

    **Why a table rather than ``respond_contacts.customer_id``.** ``customers``
    is company-scoped and ``respond_contacts`` is not, so one column cannot hold
    "customer X at Sorento, customer Y at Mocha" - and a company-scoped value
    living on an unscoped table is one join away from leaking across the
    partition. The link row carries its own ``company_id`` and the scope filter
    handles the rest.

    ``is_primary`` exists because a person genuinely can be the contact for two
    accounts; when they are, somebody has to say which one a quote defaults to,
    and the partial unique index below means only one ever can.
    """

    __tablename__ = "respond_contact_customers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=False
    )
    customer_id = Column(
        UUID(as_uuid=False), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    is_primary = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    # How the link was made: 'manual' (a human confirmed it) or the matcher that
    # proposed it. A proposal never writes itself, so nothing here is unreviewed.
    source = Column(String(32), nullable=False, default="manual", server_default="manual")
    linked_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("contact_id", "customer_id", name="uq_respond_contact_customers_pair"),
        Index("ix_respond_contact_customers_contact_id", "contact_id"),
        Index("ix_respond_contact_customers_customer_id", "customer_id"),
        # At most one primary per contact PER COMPANY - the same person may be
        # primary for a Sorento account and a Mocha one.
        Index(
            "uq_respond_contact_customers_one_primary",
            "contact_id",
            "company_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class ContactAccessType(Base):
    """Configurable catalog for contact access types (e.g. end_user, dealer, sorento_dealer). Used for promotion/attachment visibility and contact classification."""
    __tablename__ = "contact_access_types"

    code = Column(String(50), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, nullable=True)
    # Admin-curated synonym list ("customer", "homeowner" -> end_user). Consumed by
    # ContactAccessTypeService.enforce_access_levels_for_contact to resolve free-text
    # AI / user phrasing against the canonical code.
    keywords = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_contact_access_types_is_active", "is_active"),
    )


# Many-to-many: a respond contact can have multiple access types. Replaces the
# legacy single-valued respond_contacts.access_type_code FK so promotion /
# attachment visibility can be evaluated as an overlap between the contact's
# assigned codes and the resource's access_levels JSONB array.
respond_contact_access_types = Table(
    "respond_contact_access_types",
    Base.metadata,
    Column(
        "contact_id",
        Text,
        ForeignKey("respond_contacts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "access_type_code",
        String(50),
        ForeignKey("contact_access_types.code", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=False),
        server_default=func.now(),
        nullable=False,
    ),
    Index("ix_respond_contact_access_types_contact_id", "contact_id"),
    Index("ix_respond_contact_access_types_access_type_code", "access_type_code"),
)


class MarketSegment(Base):
    """Configurable catalog of market segments (retail / project) for CS routing.

    A respond contact carries the segment(s) it belongs to; a team membership
    carries the segment(s) that member serves. team-members / next-assignee
    intersect the two to route a conversation to the right customer-service pool.
    Admin-manageable (add / rename / activate / reorder) via Settings.
    """
    __tablename__ = "market_segments"

    # uuid surrogate PK (design principle: every domain table has a uuid `id`).
    # `code` stays the human-facing business key - unique, and the FK target for
    # customers / respond_contact_market_segments / team_member_market_segments.
    id = Column(UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"))
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, nullable=True)
    # SCM (M2): default demand nature for customers in this segment (continuous | spike).
    demand_nature = Column(String(20), nullable=True)
    # When true, contacts in this segment are offered in the "Requested by" /
    # "Salesperson" requestor picker on PR / SF / stock inquiry (portal + CRM).
    # Fail closed: no flagged segment means the picker offers only the submitter
    # and the currently-saved requestor. Admin-visible in Market Segments.
    is_requestor_selectable = Column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # Portal forms this segment grants BEYOND the base four every contact
    # already sees (PLAN-portal-forms-market-segment D1/D3/D4). Empty = this
    # segment grants nothing extra. Moved here from ContactAccessType -
    # access types no longer carry any portal-form grant.
    portal_form_types = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_market_segments_is_active", "is_active"),
    )


# Many-to-many: a respond contact belongs to zero+ market segments. Empty = the
# contact matches every member (minimum configuration).
respond_contact_market_segments = Table(
    "respond_contact_market_segments",
    Base.metadata,
    Column(
        "contact_id",
        Text,
        ForeignKey("respond_contacts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "segment_code",
        String(50),
        ForeignKey("market_segments.code", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("created_at", DateTime(timezone=False), server_default=func.now(), nullable=False),
    Index("ix_respond_contact_market_segments_contact_id", "contact_id"),
    Index("ix_respond_contact_market_segments_segment_code", "segment_code"),
)


# Many-to-many: a team membership serves zero+ market segments. Empty = the
# member serves every contact (untagged member = serves all).
team_member_market_segments = Table(
    "team_member_market_segments",
    Base.metadata,
    Column(
        "team_member_id",
        UUID(as_uuid=False),
        ForeignKey("team_members.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "segment_code",
        String(50),
        ForeignKey("market_segments.code", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("created_at", DateTime(timezone=False), server_default=func.now(), nullable=False),
    Index("ix_team_member_market_segments_team_member_id", "team_member_id"),
    Index("ix_team_member_market_segments_segment_code", "segment_code"),
)


# Many-to-many: a team membership serves zero+ BRANDS. Empty = the member serves
# every brand (untagged member = serves all), exactly like the segment table above -
# same matching rule, second axis. Brand is orthogonal to company: Cabana and Mocha
# are brands INSIDE the Sorento company, so the company column cannot carry them.
#
# `brand_code` is deliberately NOT an FK to `brands.brand_code`: deleting a brand
# would then silently untag its members, turning specialists into serve-all and
# changing routing without anybody asking for it. Unknown codes are validated at
# save time instead (see `team_member_brand_service`).
team_member_brands = Table(
    "team_member_brands",
    Base.metadata,
    Column(
        "team_member_id",
        UUID(as_uuid=False),
        ForeignKey("team_members.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("brand_code", Text, primary_key=True),
    Column("created_at", DateTime(timezone=False), server_default=func.now(), nullable=False),
    Index("ix_team_member_brands_team_member_id", "team_member_id"),
    Index("ix_team_member_brands_brand_code", "brand_code"),
)


class RespondContact(Base):
    __tablename__ = "respond_contacts"

    id = Column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    phone_number = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=True)
    first_name = Column(Text, nullable=True)
    last_name = Column(Text, nullable=True)
    respond_io_id = Column(Text, nullable=True)  # Respond.io contact id for inbox URL
    # Stable opaque slug for the bookmarkable portal URL /portal/c/{slug}.
    # Identity hint, not a credential - lazily minted on first portal-link use.
    portal_slug = Column(String(16), nullable=True, unique=True, index=True)
    workspace_id = Column(UUID(as_uuid=False), ForeignKey("respond_workspaces.id", ondelete="SET NULL"), nullable=True)
    # Arbitrary per-contact conversation state. Read/overwritten wholesale by
    # GET|PUT /api/v1/external/conversation-variables/{respond_io_id}.
    session_vars = Column(JSONB(astext_type=Text()), nullable=False, server_default=text("'{}'::jsonb"))
    # AC-F4: the sponsorship rollout switch, per CONTACT rather than global. Flagged
    # contacts must pick a registered project; everybody else keeps today's free-text
    # behaviour, which is what lets the requirement be introduced one team at a time
    # instead of breaking the form for everybody on the same day.
    requires_registered_project = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # Per-contact outbound kill switch. False silences every outbound Respond.io
    # send to this contact (text, attachment, template) at the client boundary;
    # reads are untouched. Defaults ON, so a new or migrated row behaves exactly
    # as it did before the switch existed. Flipped in bulk or per contact by
    # scripts/set_contact_outbound.py.
    outbound_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    # CONTACT-BRAND-SCOPE: the brands this contact may see. NULL or empty = every brand.
    # Read only through app.services.contact_brand_scope.contact_brand_scope.
    brand_ids = Column(ARRAY(UUID(as_uuid=False)), nullable=True)
    # Chatbot turn re-architecture (AC-1503): the Profile shelf, one JSONB blob
    # (tier, language, default ledgers - PLAN "Design > State"), written by explicit
    # picks (a WhatsApp pick, or the Contact > Access "Chatbot" card) and read as a
    # `Profile:` hint block on every parse (S3, AC-1548).
    chatbot_profile = Column(JSONB(astext_type=Text()), nullable=False, server_default=text("'{}'::jsonb"))
    # Chatbot memory lane A (contract section 2, round 3 AC-MEM054): the contact's
    # OWN context level, one of "off" | "conversation" | "episodes" | "full". NULL =
    # follow the system default (`system_settings.chatbot_memory.default_level`).
    # DROPS `chatbot_recall_enabled` (the recall re-parse it gated is deleted, round
    # 3): this column, and the CHECK constraint below, are its full replacement.
    chatbot_memory_level = Column(String(16), nullable=True)
    # S6 (owner ruling, 16 Sep 2026): whether this contact may ask for stock is a CRM
    # fact, default ON; the respond.io `is_allowed_stock` custom field is not read.
    chatbot_stock_allowed = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    # Chatbot stock ask v2 S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): per-contact
    # toggles, both default OFF (unlike chatbot_stock_allowed above). notify_salesman -
    # the customer's sales agent gets one WhatsApp line per B1/B2/B4 answered ask.
    # packing_list_allowed - the shipment's packing list is attached on a B3 answer.
    notify_salesman = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    packing_list_allowed = Column(Boolean, nullable=False, server_default=text("false"), default=False)
    # Issue #1328: whether the ETA this contact is told carries the product-or-category
    # `chatbot_eta_offset_days`. One switch for every chat route that prints an ETA (the
    # stock ask and the incoming routes, `app/services/eta_policy.py`). Default ON: the
    # stock ask padded the ETA for everyone before the switch existed.
    chatbot_eta_offset_applied = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    # ESCALATION-CONTROL (owner, 30 Sep 2026): may the chatbot offer this contact a
    # hand-off to a person, or let them force one. The ONE control (access types do not
    # decide it); default ON, every existing contact backfilled ON, unticked on the
    # contact page's Chatbot card to block.
    escalation_allowed = Column(Boolean, nullable=False, server_default=text("true"), default=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by = Column(Text, nullable=True)

    contact_accesses = relationship(
        "ContactAgentAccess",
        back_populates="contact",
        cascade="all, delete-orphan",
    )
    # Many-to-many: contact ↔ access types catalog. Source of truth for which
    # access codes apply to this contact (used by overlap filters on promotion /
    # attachment visibility). Configurable per contact via the FE; no longer
    # synced from Respond.io.
    access_types = relationship(
        "ContactAccessType",
        secondary=respond_contact_access_types,
        order_by="ContactAccessType.sort_order, ContactAccessType.code",
    )
    # Many-to-many: contact ↔ market segments (retail / project). Empty = matches
    # every CS member (minimum config). Configured per contact in the FE.
    market_segments = relationship(
        "MarketSegment",
        secondary=respond_contact_market_segments,
        order_by="MarketSegment.sort_order, MarketSegment.code",
    )
    workspace = relationship("RespondWorkspace", back_populates="respond_contacts")

    __table_args__ = (
        Index("ix_respond_contacts_phone_number", "phone_number"),
        Index("ix_respond_contacts_respond_io_id", "respond_io_id"),
        Index("ix_respond_contacts_workspace_id", "workspace_id"),
        # Round 3 (AC-MEM054): the level lives in the DATABASE, not only in the
        # route's Pydantic Literal - a raw UPDATE bypassing the API can never write
        # a retired or bogus value either.
        CheckConstraint(
            "chatbot_memory_level IS NULL OR chatbot_memory_level IN "
            "('off', 'conversation', 'episodes', 'full')",
            name="ck_respond_contacts_chatbot_memory_level",
        ),
    )


class RespondContactCsRouting(Base):
    """Per-salesman → CS PIC pin (pin-point assignment) overriding round-robin.

    When a procurement form-SLA customer-service stage spawns at approval, the
    assignee resolver (`form_sla_service._start_for_config`) looks up an active pin
    for (respond_contact_id, use_case). A valid pin - where ``cs_pic`` is a member of
    the stage's tier-1 CS team and is active - assigns that user directly; any miss
    (no pin / stale pin / inactive user / non-member) falls back to the existing
    round-robin. ``use_case`` is one of {'purchase_request', 'sponsorship_form'};
    complaint never reads this table, so complaint CS assignment stays round-robin.
    See docs/plans/PLAN-procurement-cs-handoff-and-pinpoint-routing.md.
    """

    __tablename__ = "respond_contact_cs_routing"

    # String PK (migration 231 created this column as VARCHAR). Must NOT be the
    # pg UUID type or SQLAlchemy emits `WHERE id = :id::UUID`, which fails against
    # a varchar column ("operator does not exist: character varying = uuid").
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    respond_contact_id = Column(
        Text,
        ForeignKey("respond_contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    cs_pic_user_id = Column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    use_case = Column(String(50), nullable=False)  # purchase_request | sponsorship_form | complaint | stock_inquiry
    is_active = Column(Boolean, default=True, nullable=False)
    # Generic predicate set: array of {field, operator, value}, AND-combined, evaluated
    # against the form header's own fields. Empty [] = wildcard (always matches). The
    # winning pin for a form = lowest `priority` among matching rows (created_at
    # tiebreak). See app.services.cs_routing_match.
    match_conditions = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    priority = Column(Integer, nullable=False, server_default="0")  # lower wins (pure admin order)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by = Column(Text, nullable=True)

    contact = relationship("RespondContact")
    cs_pic = relationship("User")

    __table_args__ = (
        # Uniqueness is (respond_contact_id, use_case, md5(match_conditions::text))
        # one pin per distinct condition-set per contact+use_case. It is an EXPRESSION
        # unique index (uq_cs_routing_contact_use_case_conditions), created in the
        # migration; SQLAlchemy can't model an md5(jsonb) expression index inline.
        Index("ix_cs_routing_contact", "respond_contact_id"),
        Index("ix_cs_routing_cs_pic", "cs_pic_user_id"),
    )


class AccessAgent(Base):
    __tablename__ = "access_agents"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    code = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    assign_to_new_internal_contacts = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)
    synced_to_excel = Column(Boolean, default=False, nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)

    contact_accesses = relationship("ContactAgentAccess", back_populates="agent")
    agent_teams = relationship("AgentTeam", back_populates="agent", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_access_agents_is_active", "is_active"),
        Index("ix_access_agents_code", "code"),
    )


class ContactAgentAccess(Base):
    __tablename__ = "contact_agent_access"
    
    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    respond_contact_id = Column(Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=True)  # FK to respond_contacts
    respond_contact_phone = Column(Text, nullable=False)  # Keep for backward compatibility
    respond_contact_name = Column(Text, nullable=True)  # Keep for backward compatibility
    agent_id = Column(UUID(as_uuid=False), ForeignKey("access_agents.id", ondelete="CASCADE"), nullable=False)
    is_allowed = Column(Boolean, default=True, nullable=False)
    valid_from = Column(DateTime(timezone=False), nullable=True)
    valid_to = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(Text, nullable=True)
    synced_to_excel = Column(Boolean, default=False, nullable=False)
    last_synced_to_excel = Column(DateTime(timezone=False), nullable=True)
    updated_at = Column(DateTime(timezone=False), nullable=True)
    
    agent = relationship("AccessAgent", back_populates="contact_accesses")
    contact = relationship("RespondContact", back_populates="contact_accesses")
    
    __table_args__ = (
        Index("ix_contact_agent_access_agent_id", "agent_id"),
        Index("ix_contact_agent_access_respond_contact_id", "respond_contact_id"),
        Index("ix_contact_agent_access_respond_contact_phone", "respond_contact_phone"),
        # Unique constraint on respond_contact_id and agent_id to prevent duplicates
        # Note: This will be created via migration as a partial unique index to handle NULL values
    )


class ContactAttachmentType(Base):
    """Document types a contact may retrieve, beyond the global baseline.

    `attachment_types.is_direct_access` is one boolean for everyone: a type is
    dealer-downloadable or nobody can reach it. This adds the missing axis, so the
    Container Status workbook can go to the office without going to dealers.

    Grants only ADD - the baseline is never subtracted from here, so introducing
    this table cannot take a document away from anyone.
    """
    __tablename__ = "contact_attachment_types"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=False
    )
    attachment_type_id = Column(
        UUID(as_uuid=False),
        ForeignKey("attachment_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    created_by = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("contact_id", "attachment_type_id", name="uq_contact_attachment_type"),
        Index("ix_contact_attachment_types_contact", "contact_id"),
    )


class AgentFieldAccess(Base):
    """Which fields an agent may reveal, and to whom.

    `ContactAgentAccess` says WHICH FUNCTIONS a contact may use. This says WHICH
    FIELDS each of those functions may reveal - one level finer, so a sensitive
    column can be added to an existing answer without minting a whole new agent
    and re-granting it to the 53 contacts who already hold the function.

    `contact_id` NULL = the agent-wide default, applying to everyone holding the
    agent. A row WITH a contact overrides that default for that one contact, in
    either direction (`is_allowed` true grants an exception, false revokes one).

    `agent_code` FKs to `access_agents.code` with ON UPDATE CASCADE rather than to
    the id: the code is what the resolver, the seeds and the docs all name, and a
    rename should carry the rows with it.

    Only fields registered in `app.services.field_access.GATED_FIELDS` are
    consulted; a row for anything else is inert. Absence of a row means DENY.
    """
    __tablename__ = "agent_field_access"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_code = Column(
        Text,
        ForeignKey("access_agents.code", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    #: Namespace for the field keys, so `eta_date` on incoming stock and a future
    #: `eta_date` on something else never collide.
    resource = Column(Text, nullable=False)
    field_key = Column(Text, nullable=False)
    #: NULL = applies to every contact holding the agent.
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=True
    )
    is_allowed = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by = Column(Text, nullable=True)
    #: Who last changed the tick. Separate from `created_by` because every row is
    #: pre-seeded denied by the bootstrap, so the insert never has a human behind
    #: it - `created_by` alone would be NULL on every grant anyone ever makes.
    updated_by = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_agent_field_access_lookup", "resource", "agent_code"),
        Index("ix_agent_field_access_contact_id", "contact_id"),
        # Two partial uniques, not one constraint: in Postgres NULLs are distinct,
        # so a plain UNIQUE over the four columns would happily allow a second
        # agent-wide row for the same field.
        Index(
            "uq_agent_field_access_default",
            "agent_code",
            "resource",
            "field_key",
            unique=True,
            postgresql_where=text("contact_id IS NULL"),
        ),
        Index(
            "uq_agent_field_access_override",
            "agent_code",
            "resource",
            "field_key",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
    )


class Team(Base, CompanyScopedMixin):
    """A team belongs to exactly one company (D1).

    Company-scoped, so the Teams admin page and every team picker follow the active
    company switcher. Access agents deliberately are NOT scoped: one agent is a single
    router serving both brands through two ladders (D8).

    Team of users for round-robin assignment.
    """
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text(), nullable=True)
    # Self-FK for the team hierarchy: a member of a parent team can see + act on the
    # work of all descendant teams (any depth). NULL = top-level. SET NULL on delete
    # so removing a parent re-roots its children rather than cascading them away.
    # A parent's members can see and act on every descendant team's work at any
    # depth, so the parent MUST be in the same company - enforced on write in
    # AccessAgentService, and checked by migration 320 before it locks the column in.
    parent_team_id = Column(
        UUID(as_uuid=False),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    agent_teams = relationship("AgentTeam", back_populates="team", cascade="all, delete-orphan")
    parent = relationship("Team", remote_side=[id], foreign_keys=[parent_team_id])

    __table_args__ = (
        Index("ix_teams_name", "name"),
        Index("ix_teams_parent_team_id", "parent_team_id"),
    )


class TeamMember(Base):
    """User membership in a team (ordered for round-robin)."""
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    team_id = Column(UUID(as_uuid=False), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(100), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sort_order = Column(Integer, nullable=True)
    # Per-team round-robin eligibility. Default true = receives auto-assignments.
    # Per-team (NOT per-user): a multi-team member can be RR-eligible in one team and
    # excluded in another. Governs AUTO distribution only - manual takeover/reassign
    # can still target an excluded member, and they still appear in Team Tasks.
    include_in_round_robin = Column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    team = relationship("Team", back_populates="members")
    user = relationship("User", backref="team_memberships")
    # Many-to-many: this membership ↔ market segments it serves. Empty = serves
    # every contact (untagged member = serves all).
    market_segments = relationship(
        "MarketSegment",
        secondary=team_member_market_segments,
        order_by="MarketSegment.sort_order, MarketSegment.code",
    )

    __table_args__ = (
        Index("ix_team_members_team_id", "team_id"),
        Index("ix_team_members_user_id", "user_id"),
        Index("uq_team_members_team_user", "team_id", "user_id", unique=True),
    )


class AgentTeam(Base, CompanyScopedMixin):
    """Link access agent to a team set code and optional tier (1=initial, 2/3=escalation).

    Company-scoped. The explicit ``company_id`` argument on the AccessAgentService
    resolvers is the loud layer - a missed caller is a TypeError. This auto-filter is
    the quiet backstop for the ad-hoc ``AgentTeam`` queries scattered through
    ``sla_service`` that no signature change can reach. Background readers must set an
    explicit scope (``scheduler_session``), or they see nothing.
    """
    __tablename__ = "agent_teams"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(UUID(as_uuid=False), ForeignKey("access_agents.id", ondelete="CASCADE"), nullable=False)
    code = Column(Text, nullable=False)  # Team set code (e.g. marketing_product, retail_director)
    team_id = Column(UUID(as_uuid=False), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    tier = Column(Integer, nullable=True)  # Explicit tier: 1/2/3 within a team set
    # Conversation SLA policy bound to this team set. One policy per (agent, code),
    # cast to every tier row of the set by set_agent_teams. RESTRICT so a policy in
    # use can't be deleted (mirrors the SLA-policy delete guard).
    policy_id = Column(UUID(as_uuid=False), ForeignKey("sla_policies.id", ondelete="RESTRICT"), nullable=True)
    # Whether THIS tier's team is notified when a LOWER-tier SLA deadline is extended.
    # On extend, every higher tier (current+1..3) with this flag set gets a
    # "deadline extended" notice. Default true preserves+extends the old "+1 only"
    # behaviour (now the grandparent is reached too); admins untick to silence a tier.
    notify_on_extension = Column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    agent = relationship("AccessAgent", back_populates="agent_teams")
    team = relationship("Team", back_populates="agent_teams")

    __table_args__ = (
        Index("ix_agent_teams_agent_id", "agent_id"),
        Index("ix_agent_teams_team_id", "team_id"),
        Index("ix_agent_teams_code", "code"),
        Index("ix_agent_teams_tier", "tier"),
        # Company is part of both keys: the same (code, tier) under two companies is
        # the whole point of the feature, and the pre-company keys rejected exactly
        # that. Must stay in step with migration 320 - the scratch-schema test
        # fixtures build their indexes from HERE, not from the migration, so a
        # divergence shows up as a duplicate-key error in tests that pass in prod.
        Index(
            "uq_agent_teams_agent_code_company_tier_null",
            "agent_id",
            "code",
            "company_id",
            unique=True,
            postgresql_where=(tier.is_(None)),
        ),
        Index(
            "uq_agent_teams_agent_code_company_tier",
            "agent_id",
            "code",
            "tier",
            "company_id",
            unique=True,
            postgresql_where=(tier.is_not(None)),
        ),
    )


class AgentTeamRoundRobinCursor(Base):
    """Per (agent, team) cursor for round-robin next assignee."""
    __tablename__ = "agent_team_round_robin_cursors"
    __audit_skip__ = "assignment cursor"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(UUID(as_uuid=False), ForeignKey("access_agents.id", ondelete="CASCADE"), nullable=False)
    team_id = Column(UUID(as_uuid=False), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    # Market-segment discriminator for the rotation. '' = the legacy / no-segment
    # cursor (used when next-assignee gets no contact_id - unchanged behaviour).
    # Non-empty = sorted '|'-joined contact segment codes (e.g. 'project|retail').
    segment_key = Column(String(120), nullable=False, server_default=text("''"))
    last_assigned_user_id = Column(String(100), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_agent_team_rr_cursors_agent_team_segment", "agent_id", "team_id", "segment_key", unique=True),
    )


class McpTool(Base):
    """Persisted catalog row for one MCP tool. Synced from code catalog by
    `app.services.mcp_tool_registry_service.sync_catalog`.

    Pure catalog: the tool→agent ownership model was removed once n8n took over
    agent/team routing. Contact access is enforced per-agent only
    (`contact_agent_access` + `mcp_access_service.evaluate_agent`).
    """

    __tablename__ = "mcp_tools"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool_name = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    module_key = Column(Text, nullable=False, default="", server_default="")
    http_path = Column(Text, nullable=False)
    http_method = Column(Text, nullable=False, default="GET", server_default="GET")
    is_active = Column(Boolean, default=True, nullable=False)
    last_seen_at = Column(DateTime(timezone=False), nullable=False)
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    #: Field-reveal keys this tool's presenter marks `restricted=<key>`, one entry
    #: per gated field: `[{"key": "inventory.sellable", "label": "Sellable stock"}]`.
    #: Written by `sync_catalog` from `ToolSpec.restricted_fields` (a static
    #: declaration, since sync reads the code catalog and never calls the tool).
    #: `[]` for a tool with nothing restricted - the common case. `sync_catalog` only
    #: ever runs where `sorento_crm_mcp` is importable (a local checkout, the seed
    #: script) - the deployed backend image cannot import it (compose builds the
    #: image with `context: ./sorento_crm_backend`, the package is not in
    #: `requirements.txt`), so this column stays at its migration-488 default of
    #: `[]` on every environment that runs from that image. `GET
    #: /system/chatbot/field-reveal-keys` does NOT read this column for that reason;
    #: it serves `contact_field_reveal_service.FIELD_REVEAL_KEYS`, a frozen literal a
    #: CI test pins to the catalogue. This column remains useful wherever the sync
    #: does run and as the historical record of what was declared at each sync.
    restricted_fields = Column(
        JSONB(astext_type=Text()), nullable=False, server_default=text("'[]'::jsonb")
    )
    #: The chatbot domain this tool answers FROM (a `DOMAIN_SPEC` key), stamped by
    #: `sync_catalog` off `app.services.mcp_tool_domains.CHATBOT_TOOL_DOMAINS`.
    #: NULL for a tool listed under no domain.
    #:
    #: **Written, never read (8 Sep 2026.)** It was added days earlier so the chatbot's
    #: tool search could narrow a domain's pool on DATA rather than on the tool name
    #: (owner ruling, "I don't accept the leak" - the PO placed tool's old name
    #: contained "order" and leaked into the `order` pool under a name-LIKE filter).
    #: That search is gone: the business lane reads
    #: `contracts.DOMAIN_SPEC[domain].tools[0]` outright, so no name and no column
    #: decides retrievability any more. The column stays until the next migration that
    #: touches `mcp_tools` carries the drop - production never populated it, so there is
    #: nothing to lose and no reason to churn the table on its own.
    chatbot_domain = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_mcp_tools_module_key", "module_key"),
        Index("ix_mcp_tools_is_active", "is_active"),
        Index("ix_mcp_tools_chatbot_domain", "chatbot_domain"),
    )


class ContactFieldReveal(Base):
    """Per-contact permission to see one RESTRICTED field in a chatbot answer.

    One mechanism for two owner requirements: sellable stock is off by default
    for every contact (D3), and a PO's supplier must never reach a dealer (D4).
    Both are just a field a presenter marked `restricted=<key>` in its
    `field_vocabulary`; this table is the grant.

    Default is HIDDEN: a contact with no row for a key never sees that field,
    so a new restricted field ships invisible everywhere until an admin ticks
    it, the same default-deny `agent_field_access` already established.

    Unlike `agent_field_access` (owned by a FUNCTION, resolved through the
    agents a contact holds), this is granted DIRECTLY on the contact - D4's
    field reveal is not "which agent" but "which contact may see the
    supplier", so there is no owning agent to route the grant through.

    Enforcement lives in the chatbot's `output_structurer` only
    (`ctx.access.attributes`, filled by `head/access.py::check_access`). The
    MCP server itself stays unfiltered - the in-app assistant and n8n
    operators are internal callers, not the customer-facing chatbot.
    """

    __tablename__ = "contact_field_reveals"

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    respond_contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=False
    )
    #: Matches a presenter's `field_vocabulary[key]["restricted"]` value, e.g.
    #: "inventory.sellable", "purchase_orders.supplier".
    field_key = Column(Text, nullable=False)
    #: A row can exist and be revoked (rather than deleted) so a PUT full-list
    #: replace has history to flip back to true instead of losing who granted
    #: it and when. Absence of any row is still the same as `granted=False`.
    granted = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_contact_field_reveals_contact", "respond_contact_id"),
        UniqueConstraint(
            "respond_contact_id", "field_key", name="uq_contact_field_reveals_contact_key"
        ),
    )


class StockVisibilityPolicy(Base):
    """Which locations a chatbot contact may be told about, and in which shape.

    Three tiers in ONE table, distinguished by which key is set:

        contact_id set        -> the per-contact override
        access_type_code set  -> the per-access-type rule (`dealer`, `end_user` ...)
        both NULL             -> the single global default

    A row is exactly one tier, enforced by the CHECK below: a row naming both a
    contact and an access type has no defined precedence, so it must not exist.

    ``warehouse_ids`` is an array rather than an M2M because the list is short,
    read-only at query time, and the admin card replaces it wholesale - there is
    no second writer to race with. NULL = every active warehouse the caller's
    company scope allows; ``[]`` = none at all, which is how a contact is told
    about no stock without inventing a fourth mode.

    The access-type tier is what makes the dealer roll-out scale: one row for
    `dealer` rather than one per dealer contact. When a contact holds several
    types, ``app.services.stock_visibility`` merges them MOST RESTRICTIVE first -
    a contact tagged both `dealer` and `end_user` must not be widened by the
    looser tag.

    Audited (``__audit_track__``) like the other master data an admin edits: one
    row decides what every future answer to a contact contains, so "who changed
    this, and from what" has to be answerable.
    """

    __tablename__ = "stock_visibility_policies"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    #: NULL unless this is the per-contact override tier.
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: NULL unless this is the per-access-type tier. FK to the CODE with
    #: ON UPDATE CASCADE, same reasoning as `agent_field_access.agent_code`: the
    #: code is what the admin screens and the resolver name, and a rename should
    #: carry the rows with it.
    access_type_code = Column(
        String(50),
        ForeignKey("contact_access_types.code", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=True,
    )
    #: detailed | compact | availability. See `stock_visibility.MODES`.
    mode = Column(String(20), nullable=False)
    #: NULL = every active warehouse; [] = none.
    warehouse_ids = Column(ARRAY(UUID(as_uuid=False)), nullable=True)
    #: The include list's sibling, never set alongside it (CHECK below). NULL = no
    #: exclusion; a list = every active warehouse EXCEPT these, including one
    #: created after the row was saved. `[]` is a real, storable value here too -
    #: it means "every active warehouse", the opposite of what `[]` means on
    #: `warehouse_ids`.
    excluded_warehouse_ids = Column(ARRAY(UUID(as_uuid=False)), nullable=True)
    #: Drop the locations holding NONE of the product from the answer. `detailed`
    #: withholds the row, `compact` the location line (the total is unchanged),
    #: and `availability` has no line to withhold so it is unaffected. NEGATIVES
    #: stay visible in both: a count that cannot be true is an anomaly somebody
    #: has to act on, not "none left".
    hide_zero_locations = Column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "mode IN ('detailed', 'compact', 'availability')",
            name="ck_stock_visibility_policies_mode",
        ),
        CheckConstraint(
            "contact_id IS NULL OR access_type_code IS NULL",
            name="ck_stock_visibility_policies_one_tier",
        ),
        # A row picks one location rule, never both: "only these" and "all but
        # these" have no defined precedence between them.
        CheckConstraint(
            "warehouse_ids IS NULL OR excluded_warehouse_ids IS NULL",
            name="ck_stock_visibility_policies_one_location_rule",
        ),
        # Three partial uniques, not one constraint: Postgres NULLs are distinct,
        # so a plain UNIQUE would let a SECOND default row in and the resolution
        # chain would then pick one of them at random.
        Index(
            "uq_stock_visibility_policies_contact",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "uq_stock_visibility_policies_access_type",
            "access_type_code",
            unique=True,
            postgresql_where=text("access_type_code IS NOT NULL"),
        ),
        Index(
            "uq_stock_visibility_policies_default",
            text("(true)"),
            unique=True,
            postgresql_where=text("contact_id IS NULL AND access_type_code IS NULL"),
        ),
    )


class SpecVisibilityPolicy(Base):
    """Which product spec keys (Thickness, Material, ...) the chatbot may reveal to
    a contact. Three tiers in ONE table, distinguished by which key is set:

        contact_id set     -> the per-contact override
        segment_code set   -> the per-market-segment rule (`retail`, `project`, ...)
        both NULL          -> the single global default

    Same doctrine as ``StockVisibilityPolicy`` above, with two differences that
    earned a sibling table rather than reusing that one (PLAN-spec-visibility-
    policy.md "Decisions"): the tier axis is MARKET SEGMENT, not contact access
    type - the owner's own vocabulary for this feature is retail/project - and
    ``app.services.spec_visibility.resolve_policy`` FAILS CLOSED TO THE DEFAULT
    POLICY on an unresolvable contact rather than to no answer at all: a stock
    question with nobody to check against gets zero rows and no block, but a spec
    question always gets an answer, so it has to fall back to something.

    ``spec_keys`` (Show-only) and ``excluded_spec_keys`` (Hide-these) are two
    readings of the SAME list, never both stored on a row (CHECK below): a list
    under ``spec_keys`` is what stays VISIBLE (``null`` = every key, ``[]`` =
    none); a list under ``excluded_spec_keys`` is what is WITHHELD (``[]`` =
    nothing hidden - the opposite of what ``[]`` means on ``spec_keys``).

    Audited (``__audit_track__``) for the same reason as stock visibility: one
    row decides what every future answer to a contact contains.
    """

    __tablename__ = "spec_visibility_policies"
    __audit_track__ = True

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    #: NULL unless this is the per-contact override tier.
    contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: NULL unless this is the per-market-segment tier. FK to the CODE with ON
    #: UPDATE CASCADE, same reasoning as `access_type_code` above: the code is
    #: what the admin screens and the resolver name, and a rename should carry
    #: the rows with it.
    segment_code = Column(
        String(50),
        ForeignKey("market_segments.code", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=True,
    )
    #: The Show-only list. NULL = every registry key visible; [] = none.
    spec_keys = Column(ARRAY(Text), nullable=True)
    #: `spec_keys`'s sibling, the Hide-these list. NULL = not this rule (this row
    #: is Show-only or inert); [] = nothing hidden (the opposite of what [] means
    #: on `spec_keys`); a list = hide exactly these.
    excluded_spec_keys = Column(ARRAY(Text), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "contact_id IS NULL OR segment_code IS NULL",
            name="ck_spec_visibility_policies_one_tier",
        ),
        # A row picks one rule, never both: "show only these" and "hide these"
        # have no defined precedence between them.
        CheckConstraint(
            "spec_keys IS NULL OR excluded_spec_keys IS NULL",
            name="ck_spec_visibility_policies_one_rule",
        ),
        # Three partial uniques, not one constraint: Postgres NULLs are distinct,
        # so a plain UNIQUE would let a SECOND default row in and the resolution
        # chain would then pick one of them at random.
        Index(
            "uq_spec_visibility_policies_contact",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "uq_spec_visibility_policies_segment",
            "segment_code",
            unique=True,
            postgresql_where=text("segment_code IS NOT NULL"),
        ),
        Index(
            "uq_spec_visibility_policies_default",
            text("(true)"),
            unique=True,
            postgresql_where=text("contact_id IS NULL AND segment_code IS NULL"),
        ),
    )
