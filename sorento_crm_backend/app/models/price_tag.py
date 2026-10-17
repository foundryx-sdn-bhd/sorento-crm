"""Price tag request models.

A portal contact (Sorento salesperson) submits a request naming products, sets,
pricing mode and a dealer. Marketing designs the tags in the canvas editor and
the salesperson reviews the proof on the portal. See PLAN-price-tag-request.md.

``PriceTagRequest`` is company-scoped (``CompanyScopedMixin``).
``PriceTagRequestLine`` is not scoped itself - it hangs off the request.

``ContactPortalFormOverride`` lives here rather than in ``access.py`` because
it is portal-form infrastructure introduced by this feature; the access module
is already large and the override has no coupling to it beyond the FK.
"""
import uuid

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base import CompanyScopedMixin

DEALER_KIT_SCHEMA = "dealer_kit"


def _uuid_str():
    return str(uuid.uuid4())


class ContactPortalFormOverride(Base):
    """Per-contact toggle for portal form type visibility.

    ``is_enabled`` wins over the base-plus-segment default
    (PLAN-portal-forms-market-segment D1/D3: every contact holds the four
    legacy kinds by default, plus whatever its market segments grant on top).
    A row here saying ``is_enabled=False`` hides the type even if it is a base
    kind or every segment the contact belongs to grants it; ``is_enabled=True``
    shows it even if none of them do.
    """

    __tablename__ = "contact_portal_form_overrides"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    contact_id = Column(
        Text,
        ForeignKey("respond_contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    form_type = Column(String(50), nullable=False)
    is_enabled = Column(Boolean, nullable=False)
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("contact_id", "form_type", name="uq_contact_portal_form_override"),
        Index("ix_contact_portal_form_overrides_contact_id", "contact_id"),
    )


class PriceTagRequest(Base, CompanyScopedMixin):
    """A salesperson's request for printed price tags.

    Lifecycle: new -> designing -> proof_ready -> approved -> ready
    Plus: changes_requested (loops back to designing), rejected, void.
    """

    __tablename__ = "price_tag_requests"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    contact_id = Column(
        Text,
        ForeignKey("respond_contacts.id"),
        nullable=False,
    )
    debtor_code = Column(String(100), nullable=True)
    # FORMS-PROJECT: the explicit project link, picked by a person (no name matching).
    # Nullable; deleting the project unlinks the form, never deletes it.
    project_id = Column(
        UUID(as_uuid=False), ForeignKey("projects.projects.id", ondelete="SET NULL"), nullable=True
    )
    # Nullable since D48a: Save Draft validates nothing, so a half-typed request
    # has to be storable. Completeness is enforced on SUBMIT, in the service, where
    # the refusal can name the field that is missing.
    debtor_name = Column(String(255), nullable=True)
    needed_by_date = Column(Date, nullable=True)  # nullable since D48a, see debtor_name
    notes = Column(Text, nullable=True)
    # Header-level price mode (D5, r7): 'list' | 'selling'. Replaces the old
    # per-line `show_promo_price` switch as the thing the salesperson picks;
    # every line's `show_promo_price` is re-derived from this on save.
    price_mode = Column(String(16), nullable=False, server_default="list")
    status = Column(String(30), nullable=False, server_default="new")
    doc_number = Column(String(30), nullable=False, unique=True)
    page_id = Column(
        UUID(as_uuid=False),
        ForeignKey(f"{DEALER_KIT_SCHEMA}.page.id", ondelete="SET NULL"),
        nullable=True,
    )
    portal_draft_at = Column(DateTime(timezone=False), nullable=True)
    po_extraction_result = Column(JSONB, nullable=True)
    created_by = Column(UUID(as_uuid=False), nullable=True)
    # WHO is designing this. Claiming used to write the user into ``created_by``,
    # which says who made the row and which nothing read back, so the page said
    # "Unclaimed" for the rest of the request's life. String, not UUID, because
    # ``users.id`` is TEXT - which is also why ``created_by`` has no FK and this
    # one does.
    assigned_to_id = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Portal submission revisions (R3-1, migration ptag_0006_revisions), denormalized
    # the same way StockInquiry / PurchaseRequestHeader carry their own pair.
    revision_no = Column(Integer, nullable=False, server_default="0", default=0)
    last_revised_at = Column(DateTime(timezone=False), nullable=True)
    # Who prints (r9 D7): 'office' | 'self'. NULL on every row created before
    # the choice existed, and required at submit from r9 onward - the answer
    # decides whether the request ends at `approved` or waits for a collection.
    print_by = Column(String(8), nullable=True)
    # The office hand-over (r9 D9). `collected_by_user_id` is the staffer who
    # ticked it off, `collected_by_contact_id` the salesperson who confirmed on
    # the portal; `collected_auto` is the sweep, which is neither.
    ready_for_collection_at = Column(DateTime(timezone=False), nullable=True)
    collected_at = Column(DateTime(timezone=False), nullable=True)
    collected_by_user_id = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    collected_by_contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    collected_auto = Column(Boolean, nullable=False, server_default="false")
    # How many proofs this design has been sent for review (r9 D4). COUNTED,
    # not derived: the "Marked proof ready" snapshot it used to be read from is
    # skipped whenever the designer saved first, so every round came back as 1.
    # 0 means it has never been sent - the first proof_ready makes it 1.
    review_round = Column(Integer, nullable=False, server_default="0", default=0)
    # The list's stored product-data-change cache (ptag_0012,
    # PLAN-price-tag-currency-token-extract-prompt.md section D). Written by
    # `tag_data_service.store_data_change_count` wherever the diff already
    # runs; the list route refreshes it only for a row
    # `PriceTagRequestService.touched_request_ids` says was touched since
    # `data_checked_at` - every other row is served from the column with no
    # live resolve at all.
    data_changed_tag_count = Column(Integer, nullable=False, server_default="0")
    data_checked_at = Column(DateTime(timezone=False), nullable=True)

    lines = relationship(
        "PriceTagRequestLine",
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="PriceTagRequestLine.sort_order",
    )

    __table_args__ = (
        Index("ix_price_tag_requests_status", "status"),
        Index("ix_price_tag_requests_contact_id", "contact_id"),
        Index("ix_price_tag_requests_assigned_to_id", "assigned_to_id"),
        Index("ix_price_tag_requests_project_id", "project_id"),
        # company_id index is already created by CompanyScopedMixin (index=True).
    )


class PriceTagRequestLine(Base):
    """One product or product set on a price tag request.

    Not company-scoped: reachable only through its request, which carries the
    partition.
    """

    __tablename__ = "price_tag_request_lines"
    __audit_parent__ = "request_id"  # history rolls up to the header

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    request_id = Column(
        UUID(as_uuid=False),
        ForeignKey("price_tag_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_type = Column(String(20), nullable=False)
    product_id = Column(
        UUID(as_uuid=False),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # No FK to product_sets yet - table not merged from feat/product-sets.
    product_set_id = Column(
        UUID(as_uuid=False),
        ForeignKey("product_sets.id", ondelete="RESTRICT"),
        nullable=True,
    )
    show_promo_price = Column(Boolean, nullable=False, server_default="true")
    quantity = Column(Integer, nullable=False, server_default="1")
    # D1 (ptag_0011): the promotion moved from the header to the LINE - a
    # request-level promotion breaks the moment two lines sit on two
    # promotions. SET NULL, not RESTRICT: deleting a promotion is a marketing
    # change today and must not be blocked by a request from last season.
    promotion_id = Column(
        UUID(as_uuid=False),
        ForeignKey("promotions.id", ondelete="SET NULL"),
        nullable=True,
    )
    # A hand-typed selling price, only meaningful with `promotion_id` NULL
    # (D2) - mutually exclusive, enforced at the schema.
    manual_sell_price = Column(Numeric(12, 2), nullable=True)
    # The catalogue package this line was asked for as (D2). SET NULL, not
    # RESTRICT: deleting a combo is a change to how the product is packaged TODAY
    # and must not be blocked by a request somebody sent last season - the line
    # keeps its own part rows, which are what the salesperson actually asked for.
    combo_id = Column(
        UUID(as_uuid=False),
        ForeignKey("product_combos.id", ondelete="SET NULL"),
        nullable=True,
    )
    # What the package guard found at submit, for marketing to read (D2). NULL =
    # clean. Submit is never refused for a package reason.
    package_warning = Column(Text, nullable=True)
    included_accessories = Column(Text, nullable=True)
    # Free-text note on the line (D6, r7).
    remarks = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    request = relationship("PriceTagRequest", back_populates="lines")

    __table_args__ = (
        CheckConstraint(
            "(line_type = 'product' AND product_id IS NOT NULL AND product_set_id IS NULL) "
            "OR (line_type = 'product_set' AND product_set_id IS NOT NULL AND product_id IS NULL)",
            name="ck_price_tag_request_lines_one_ref",
        ),
        UniqueConstraint("request_id", "product_id", name="uq_ptag_line_request_product"),
        UniqueConstraint("request_id", "product_set_id", name="uq_ptag_line_request_set"),
        Index("ix_price_tag_request_lines_request_id", "request_id"),
        Index("ix_price_tag_request_lines_promotion_id", "promotion_id"),
    )

    parts = relationship(
        "PriceTagRequestLinePart",
        back_populates="line",
        cascade="all, delete-orphan",
        order_by="PriceTagRequestLinePart.sort_order",
    )
    tags = relationship(
        "PriceTagRequestTag",
        back_populates="line",
        cascade="all, delete-orphan",
        order_by="PriceTagRequestTag.sort_order",
    )


class PriceTagRequestLinePart(Base):
    """One part under a request line - what the salesperson asked to come with it (D2).

    Two shapes, and the CHECK is what keeps them apart:

    * RESOLVED - `product_id` set, `candidates` empty. A specific product goes on
      the tag.
    * OPEN - `product_id` NULL, `candidates` holding the group's product ids. The
      salesperson left the choice to marketing, who split it into one tag per
      option (S3) or picked one.

    `role` is the choice group's label on both, so a resolved row still says which
    group it answered. RESTRICT on the product for the same reason a line's own
    product is: a request naming a product is a record, not a soft reference.
    """

    __tablename__ = "price_tag_request_line_parts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("price_tag_request_lines.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id = Column(
        UUID(as_uuid=False),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
    )
    role = Column(String(100), nullable=True)
    candidates = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    line = relationship("PriceTagRequestLine", back_populates="parts")

    __table_args__ = (
        CheckConstraint(
            "(product_id IS NOT NULL AND candidates = '[]'::jsonb) "
            "OR (product_id IS NULL AND jsonb_array_length(candidates) > 0)",
            name="ck_ptag_line_parts_resolved_or_open",
        ),
        Index("ix_price_tag_request_line_parts_line_id", "line_id"),
    )


class PriceTagRequestTag(Base):
    """One printed tag under a request line (D3, S3).

    A LINE is what the salesperson asked for; a TAG is what gets printed. They
    were the same object until S3. A line whose package leaves a choice group
    open is split by marketing into one tag per candidate, each with its own
    quantity, `choices`, geometry (in the tag sheet document, keyed on this id)
    and marketing override.

    `quantity` is seeded from the line's at submit and is marketing's to change
    afterwards - the line keeps the salesperson's own number, so the request
    still shows what was asked for.

    `choices` is `{role: product_id}`: which candidate this tag resolved for
    each choice group. `{}` means nothing has been decided yet, which is what a
    tag looks like the moment it is created.

    The override moved here from the line (migration step 2): two tags split off
    one line print two different basins at two different prices, and a
    line-level figure would put the same hand-set number on both.
    """

    __tablename__ = "price_tag_request_tags"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    line_id = Column(
        UUID(as_uuid=False),
        ForeignKey("price_tag_request_lines.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order = Column(Integer, nullable=False, server_default="0")
    quantity = Column(Integer, nullable=False, server_default="1")
    choices = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    marketing_price_override = Column(Numeric(15, 2), nullable=True)
    marketing_override_reason = Column(Text, nullable=True)
    # The product data gate (r9 D16). `pinned_tag_data` is the `LineTagData`
    # the resolver answered when designing started - what the tag is DRAWN
    # from, so a price edited in master data afterwards cannot rewrite a proof
    # that has already been approved. `data_change_ack_hash` is the live hash
    # somebody looked at and chose to keep, so the same change stops asking.
    # Per TAG, not per line: two tags split off one line resolve to different
    # products, so they are drawn from different data and change apart.
    pinned_tag_data = Column(JSONB, nullable=True)
    pinned_at = Column(DateTime(timezone=False), nullable=True)
    data_change_ack_hash = Column(String(64), nullable=True)
    # r10 S6: marked "Not printed" - skipped by arrange, the print payload and
    # the design media map, never deleted (a toggle, not a decision that
    # throws the tag's own design away).
    print_excluded = Column(Boolean, nullable=False, server_default="false")
    # r10 S8: the auto-apply seam's own record of what it just did, so the
    # rail's "updated" indicator can name the change and Roll back knows which
    # version to restore. Set together, cleared together on Dismiss.
    data_updated_at = Column(DateTime(timezone=False), nullable=True)
    data_update_changes = Column(JSONB, nullable=True)
    data_update_version = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    line = relationship("PriceTagRequestLine", back_populates="tags")

    __table_args__ = (
        Index("ix_price_tag_request_tags_line_id", "line_id"),
    )



class PriceTagReviewComment(Base, CompanyScopedMixin):
    """One pinned change request on a design (r9 D4).

    A salesperson does not describe a change, they point at it: the anchor is
    stored as FRACTIONS of the TAG's own box (0..1), never page millimetres, so
    re-arranging the sheet, paging or zooming cannot move a comment off the
    thing it was pointing at. A general comment carries no anchor at all and
    reads as being about the whole design.

    The anchor is a ``PriceTagRequestTag``, not a line: since the combos slice a
    line may print several tags, and two tags split off one line show two
    different basins. A pin placed on one of them is about THAT tag.

    ``round`` is the number of proofs the design had been sent for when the
    comment was SENT, and it is stored rather than derived: a later proof must
    not renumber a round somebody has already worked off.
    """

    __tablename__ = "price_tag_review_comments"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_uuid_str)
    request_id = Column(
        UUID(as_uuid=False),
        ForeignKey("price_tag_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = a general comment about the whole design, not about one tag.
    tag_id = Column(
        UUID(as_uuid=False),
        ForeignKey("price_tag_request_tags.id", ondelete="CASCADE"),
        nullable=True,
    )
    round = Column(Integer, nullable=False, server_default="1")
    # Fractions of the tag box. w/h are 0 for a point pin, > 0 for a box.
    x = Column(Numeric(6, 4), nullable=True)
    y = Column(Numeric(6, 4), nullable=True)
    w = Column(Numeric(6, 4), nullable=True)
    h = Column(Numeric(6, 4), nullable=True)
    body = Column(Text, nullable=False)
    # Exactly one of the two is set: the salesperson who sent it, or the
    # staffer whose rejection note was persisted as a general comment (D14).
    author_contact_id = Column(
        Text, ForeignKey("respond_contacts.id", ondelete="SET NULL"), nullable=True
    )
    author_user_id = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    resolved_at = Column(DateTime(timezone=False), nullable=True)
    resolved_by_id = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("ix_ptag_review_comments_request_id", "request_id"),
        Index("ix_ptag_review_comments_tag_id", "tag_id"),
    )