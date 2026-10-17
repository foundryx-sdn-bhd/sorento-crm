"""Pydantic schemas for price tag requests and tag templates."""
from __future__ import annotations

import uuid as _uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)


# ---------------------------------------------------------------------------
# Price tag request line schemas
# ---------------------------------------------------------------------------

#: A hand-typed line price (D2, R6/security review) - matches the column's
#: own `Numeric(12, 2)`. `gt=0`: a manual price is an agreed FIGURE, not a
#: giveaway (0) or a typo with a stray minus sign (-5); an absurd
#: `1E+400`-shaped value is refused by `max_digits` before it ever reaches
#: storage. Every arm that accepts a hand-typed price (create, update, the
#: CRM line PATCH, and revise's own line model below) shares this ONE bound.
ManualSellPrice = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class LinePartIn(BaseModel):
    """One part on the way in (D2, AC-S2-8).

    RESOLVED: `product_id` set. OPEN: `product_id` null and `candidates` holding
    the group's product ids, which is the salesperson saying "any of these, you
    choose". `role` is the choice group's label on both, so a resolved row still
    says which group it answered.
    """

    product_id: Optional[str] = None
    role: Optional[str] = None
    candidates: list[str] = Field(default_factory=list)


class PriceTagRequestLineCreate(BaseModel):
    line_type: str = Field(..., pattern=r"^(product|product_set)$")
    product_id: Optional[str] = None
    product_set_id: Optional[str] = None
    show_promo_price: bool = True
    quantity: int = Field(default=1, ge=1)
    # The catalogue package this line is asked for as, and the parts under it, in
    # display order (D2). `alternatives` is gone - the OR-choices field it carried
    # is what the open part row replaced.
    combo_id: Optional[str] = None
    parts: list[LinePartIn] = Field(default_factory=list)
    included_accessories: Optional[str] = None
    # Free-text note on the line (D6, r7).
    remarks: Optional[str] = None
    # None, not 0: the portal posts the table in order and sends no sort_order,
    # and a default of 0 gave EVERY line the same one, so the relationship's
    # `order_by(sort_order)` returned them in whatever order Postgres liked. The
    # row a refusal names (`line:<index>`) has to be the row the salesperson sees.
    sort_order: Optional[int] = None
    # D1/D2 (S6): the line's OWN price basis - a request-level promotion broke
    # the moment two lines sat on two promotions. Mutually exclusive
    # (AC-S6-4, checked on the whole request below, since that needs the
    # header's `price_mode` too); `promotion_id` must cover this line
    # (AC-S6-5), checked in the service where the line's resolved parts and
    # candidates are known.
    promotion_id: Optional[str] = None
    manual_sell_price: Optional[ManualSellPrice] = None


class PriceTagReviseLineIn(BaseModel):
    """One ``products[]`` entry in a revise payload (D1/D5, R6/security
    review) - the revise composer sends every field
    ``PriceTagRequestLineInput`` (create/update) does, but the raw dict
    never ran through pydantic at all: unlike create/update, where a
    ``Decimal`` field type at least rejects a non-numeric string, a bad
    ``manual_sell_price`` here reached ``_as_decimal`` completely
    unvalidated (``-5``/``0`` converted cleanly, ``"abc"`` raised
    ``decimal.InvalidOperation`` uncaught). Same bound every other arm
    (create, update, the CRM line PATCH) uses.

    ``extra="ignore"``, not ``"forbid"``: the composer's payload carries
    ``combo_id`` / ``parts`` / ``included_accessories`` / ``product_class`` /
    ``line_type`` too - ``_convert_ptag_revise_line`` already reads only the
    fields declared here and drops the rest by design (they are carried over
    from the OLD line instead, Gap D), so this model is not the composer's
    full contract, only the two fields that never had one.
    """

    model_config = ConfigDict(extra="ignore")

    product_id: Optional[str] = None
    product_set_id: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    remarks: Optional[str] = None
    promotion_id: Optional[str] = None
    manual_sell_price: Optional[ManualSellPrice] = None


class PriceTagRequestTagUpdate(BaseModel):
    """PATCH one tag (D3).

    ``choices`` is gone with Split / Pick one (D6, AC-S8-4): every choice
    group is resolved into its own tag at submit now, so there is nothing
    left on a tag for a PATCH to answer - `extra="forbid"` turns a stray
    `choices` key into a 422 naming it, rather than silently doing nothing.
    """

    model_config = ConfigDict(extra="forbid")

    quantity: Optional[int] = Field(default=None, ge=1)
    marketing_price_override: Optional[Decimal] = None
    marketing_override_reason: Optional[str] = None
    #: r10 S6: "Not printed" - skipped by arrange, the print payload and the
    #: design media map. Refused (409) once the request has reached
    #: proof_ready (AC-S6-7).
    print_excluded: Optional[bool] = None


class PriceTagRequestTagResponse(BaseModel):
    """One tag, as every surface reads it.

    `choices_display` is the stored `{role: product_id}` map resolved to codes,
    which is what the rail and the Lines tab show; the raw map is never
    rendered. `list_price` / `sell_price` ride along because price is a TAG fact
    since D4.
    """

    id: str
    line_id: str
    sort_order: int
    #: "1a", "1b" - the line's position plus a letter. Never an id.
    label: str = ""
    quantity: int
    choices: dict[str, str] = {}
    choices_display: list[dict] = []
    open_groups: list[TagOpenGroup] = []
    marketing_price_override: Optional[float] = None
    marketing_override_reason: Optional[str] = None
    list_price: Optional[float] = None
    sell_price: Optional[float] = None
    #: r10 S6, AC-S6-7/S6-9.
    print_excluded: bool = False
    #: r10 S8: set when master data moved under this tag's pin and the
    #: change was applied by itself; cleared by Dismiss.
    data_updated_at: Optional[datetime] = None
    #: r10 S8: what that auto-update changed, old -> new per field.
    data_update_changes: Optional[list["LineDataChange"]] = None
    #: r10 S8: the "Before product update" version number Roll back restores.
    data_update_version: Optional[int] = None


class LinePartCandidateResponse(BaseModel):
    product_id: str
    code: str
    name: str


class PriceTagRequestLinePartResponse(BaseModel):
    """One part under a line, RESOLVED (D2).

    Codes and names, never bare ids: the portal read view and the CRM Lines tab
    both render this, and no id reaches a screen (AC-X-2). Filled by
    `response_with_resolved_lines`, not by `from_attributes` - the model row
    holds product ids and this holds what a person reads.
    """

    id: str
    product_id: Optional[str] = None
    code: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    candidates: list[LinePartCandidateResponse] = []
    sort_order: int


class PriceTagRequestLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    request_id: str
    line_type: str
    product_id: Optional[str] = None
    product_set_id: Optional[str] = None
    show_promo_price: bool
    quantity: int
    combo_id: Optional[str] = None
    # What the package guard found at submit, for marketing to read (D2). NULL =
    # clean; submit is never refused for a package reason.
    package_warning: Optional[str] = None
    included_accessories: Optional[str] = None
    remarks: Optional[str] = None
    sort_order: int
    # What gets printed for this line: one tag by default, N after a split (D3).
    # The marketing override moved onto the TAG in S3 and its columns are dropped
    # by the migration's step 4, so this model no longer carries either.
    tags: list[PriceTagRequestTagResponse] = Field(
        default_factory=list, validation_alias="__resolved_tags__"
    )
    # The package under this line, in display order. Default empty rather than
    # omitted: the portal form and the CRM tab both read the key unconditionally.
    #
    # `validation_alias` is load-bearing, not decoration. This model validates
    # FROM the ORM row, which has its own `parts` relationship holding
    # `PriceTagRequestLinePart` objects whose `candidates` is a list of product
    # id STRINGS - and this field wants resolved objects, so reading the
    # attribute by name raised a validation error and every create 500'd
    # (measured on the lane). Pointing validation at a name the ORM row does not
    # carry leaves the default in place for `_fill_line_parts` to overwrite with
    # the resolved rows. Serialisation is unaffected: the wire key is still
    # `parts`.
    parts: list[PriceTagRequestLinePartResponse] = Field(
        default_factory=list, validation_alias="__resolved_parts__"
    )
    created_at: datetime
    updated_at: datetime

    # Resolved, not stored. A line row holds a product id and nothing a person
    # can read, so the CRM detail page draws these four - and it drew four
    # blanks until they were declared here, because ``response_model`` removes
    # an undeclared field without a word. Filled by the detail route from
    # ``tag_data_service``; the DOCUMENT still stores no figures (ADR 0008).
    code: str = ""
    name: str = ""
    list_price: Optional[float] = None
    sell_price: Optional[float] = None
    # D1/D2 (S6): the line's own promotion / manual price, real columns now.
    # ``promotion_name`` is resolved, not stored, for the same reason
    # ``code``/``name`` are - a bare id is never shown (AC-X-2).
    promotion_id: Optional[str] = None
    promotion_name: Optional[str] = None
    # PRICE-TAG-A3-EXPIRED: the promotion's end date once it has ended (null
    # while live), so the read-only line reads "<name> (expired <date>)".
    promotion_expired_on: Optional[date] = None
    manual_sell_price: Optional[Decimal] = None
    # D3 (S7): why `sell_price` is what it is - `manual` | `promotion` |
    # `list`. Resolved alongside `list_price`/`sell_price`, same reason.
    sell_price_basis: Optional[str] = None


# ---------------------------------------------------------------------------
# Price tag request schemas
# ---------------------------------------------------------------------------


class PriceTagRequestCreate(BaseModel):
    """What the portal posts when it saves a draft (D48a).

    Nothing is required. A draft is a form in progress, and the salesperson types
    it over several sittings; SUBMIT is where completeness is enforced, by
    ``PriceTagRequestService.validate_submittable``, which can name what is
    missing. Both nullable fields match columns that are nullable for the same
    reason.

    ``extra="forbid"`` (D1, S6): the promotion moved to the LINE - a
    request-level ``promotion_id`` is now an unknown field, 422, rather than
    a value this schema quietly accepted and nothing ever read.

    AC-S6-4 (a line's manual price and its promotion are mutually exclusive,
    and manual only applies in Selling mode) is NOT re-validated here - it
    used to be a ``@model_validator`` on this class, but revise's raw-dict
    payload never runs through Pydantic at all, so the rule lived twice with
    two different error shapes. Moved into
    ``PriceTagRequestService._validate_line_price_basis``, the one seam
    create, update, revise (``_add_lines``) and the CRM line PATCH
    (``set_line_price``) all pass through (security review finding).
    """

    model_config = ConfigDict(extra="forbid")

    # FORMS-PROJECT: the explicit project link; null clears it.
    project_id: Optional[str] = None
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    needed_by_date: Optional[date] = None
    notes: Optional[str] = None
    # Header price mode (D5, r7): 'selling' requires a promotion, enforced on
    # SUBMIT by `validate_submittable` (not here - a draft may pick Selling
    # before it has a promotion, same as it may have no debtor yet).
    price_mode: Literal["list", "selling"] = "list"
    lines: list[PriceTagRequestLineCreate] = Field(default_factory=list)

    # Review round 2: the date input clears to "", not omission - Optional[date]
    # rejects that outright with a 422 instead of treating it as "no date".
    @field_validator("needed_by_date", mode="before")
    @classmethod
    def _blank_needed_by_is_none(cls, v):
        return None if v == "" else v

    #: Who prints (r9 D7). A Literal, not a str: the column is String(8), so a
    #: longer value 500s on the flush instead of being refused with a 422.
    print_by: Optional[Literal["office", "self"]] = None


class PriceTagRequestUpdate(BaseModel):
    """A draft edit. ``lines`` omitted leaves the lines alone; ``lines`` given
    replaces them, which is what the form does when it re-saves a draft.

    ``extra="forbid"`` for the same reason as ``PriceTagRequestCreate``.
    AC-S6-4 is not re-validated here either, for the same reason - see that
    class's own docstring.
    """

    model_config = ConfigDict(extra="forbid")

    # FORMS-PROJECT: the explicit project link; null clears it.
    project_id: Optional[str] = None
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    needed_by_date: Optional[date] = None
    notes: Optional[str] = None
    # NOT Optional: the column is NOT NULL, and `price_mode: null` used to
    # reach `setattr(req, "price_mode", None)` in the route (`exclude_unset`
    # only drops an OMITTED field, not one explicitly sent as null) and 500
    # on the flush. Omitted still means "leave it as it is" - `exclude_unset`
    # handles that regardless of what the default is; an explicit null is
    # now a 422, same as any other unknown price_mode value.
    price_mode: Literal["list", "selling"] = "list"
    lines: Optional[list[PriceTagRequestLineCreate]] = None

    # Review round 2: same "" -> None coercion as PriceTagRequestCreate.
    @field_validator("needed_by_date", mode="before")
    @classmethod
    def _blank_needed_by_is_none(cls, v):
        return None if v == "" else v

    #: Who prints (r9 D7). A Literal, not a str: the column is String(8), so a
    #: longer value 500s on the flush instead of being refused with a 422.
    print_by: Optional[Literal["office", "self"]] = None


class PriceTagRequestOfficeUpdate(BaseModel):
    """What the office may change on a SUBMITTED request (r9 D7).

    One field, deliberately: everything else belongs to the salesperson and
    goes through the revision engine. A separate model from
    ``PriceTagRequestUpdate`` so a draft edit and an office fix cannot drift
    into each other's fields. ``extra="forbid"`` (D1, S6): a header
    ``promotion_id`` is no longer a thing this route - or any route - accepts.
    """

    model_config = ConfigDict(extra="forbid")

    print_by: Optional[Literal["office", "self"]] = None
    # FORMS-PROJECT: the office links (or clears) the request's project.
    project_id: Optional[str] = None


class PriceTagRequestLinePricePatch(BaseModel):
    """D5/S11: the office changing ONE line's own price basis.

    Both fields optional and independently settable - a promotion pick and a
    manual figure are mutually exclusive (AC-S6-4), checked the same way the
    create/update schemas check it, once ``price_mode`` (a REQUEST fact) is
    known - the route reads it off the request row, not this body.
    ``extra="forbid"`` for the same reason every other price tag schema
    forbids it: an unknown key here would silently do nothing.
    """

    model_config = ConfigDict(extra="forbid")

    promotion_id: Optional[str] = None
    manual_sell_price: Optional[ManualSellPrice] = None


class PriceTagRequestAttachment(BaseModel):
    """One row of ``entity_attachment_service.list_attachments_for_entity``'s
    output, typed rather than left as a bare ``dict`` - an untyped field is
    exactly how the CRM FE's own copy of this shape (`priceTagRequestService
    .ts`) drifted from the real one (id/created_at that were never sent) with
    nothing catching it. Both the CRM and the portal detail routes answer with
    this shape (D49); the portal side additionally needs uploader attribution
    to gate its own unlink control, which is why every field the service emits
    is declared here too rather than trimmed to what the CRM screen shows.
    """

    model_config = ConfigDict(from_attributes=True)

    link_id: str
    attachment_id: str
    filename: Optional[str] = None
    size: Optional[int] = None
    url: Optional[str] = None
    content_type: Optional[str] = None
    uploaded_at: Optional[str] = None
    uploader_kind: Optional[str] = None
    uploaded_by_name: str = "Unknown"
    uploaded_by_role: str = "unknown"
    can_unlink: bool = True


class PriceTagRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contact_id: str
    company_id: Optional[str] = None
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    project_label: Optional[str] = None  # `CODE - Title`
    debtor_code: Optional[str] = None
    # Optional since D48a: a draft may carry neither, and a non-optional field
    # refuses to serialise a None even though the schema declared it.
    debtor_name: Optional[str] = None
    needed_by_date: Optional[date] = None
    notes: Optional[str] = None
    price_mode: str = "list"
    status: str
    doc_number: str
    page_id: Optional[str] = None
    portal_draft_at: Optional[datetime] = None
    created_by: Optional[str] = None
    # WHO is designing it. ``created_by`` is the creator and stays that; this is
    # what the header's "Assigned to" reads, and it read "Unclaimed" forever
    # because neither the column nor the name existed on the wire.
    assigned_to_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # R3-1: the revise composer's stale-write guard sends this straight back
    # as `expected_revision_no`, and the header line reads it too.
    revision_no: int = 0
    last_revised_at: Optional[datetime] = None
    # Who prints, and the hand-over that follows for an office print (r9 D7/D9).
    # Declared here or `response_model` drops them without a word (LESSONS).
    print_by: Optional[str] = None
    ready_for_collection_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    collected_auto: bool = False
    #: WHO collected them: the staffer, the salesperson, or nobody when the
    #: sweep closed it. A name, never an id.
    collected_by_name: Optional[str] = None
    #: Which review round the design is on (D4). The pin overlay greys the
    #: earlier rounds against it, so it has to travel even when no comment from
    #: the current round exists yet.
    review_round: int = 0
    lines: list[PriceTagRequestLineResponse] = []

    # Resolved, not stored. Filled by
    # ``PriceTagRequestService.response_with_resolved_lines``; a request holds a
    # contact id and a user id, and both are UUIDs the screen may not show.
    # Declared here because ``response_model`` removes what it does not know
    # about without a word. D1 (S6): the promotion moved to the LINE - there is
    # no single request-level promotion to name here any more.
    assigned_to_name: Optional[str] = None
    contact_name: Optional[str] = None
    line_count: int = 0

    # The PO files the salesperson attached, in
    # ``entity_attachment_service.list_attachments_for_entity``'s shape
    # (link_id, attachment_id, filename, size, url, content_type, ...). Filled
    # by ``response_with_resolved_lines`` for both the CRM and the portal
    # detail routes (D49) - declared here for the same reason as the four
    # fields above it: an undeclared field is dropped by ``response_model``
    # without a word (AC-S1-5).
    attachments: list[PriceTagRequestAttachment] = Field(default_factory=list)

    # Whether a completed (READY) tag sheet PDF export exists for this request.
    # Filled by ``response_with_resolved_lines`` from ``user_downloads``, same as
    # the four fields above - the portal read-only view's gear needs to know
    # whether to enable Download PDF without a second round trip, and an
    # undeclared field is dropped by ``response_model`` without a word
    # (PLAN-price-tag-feedback-r2 S2).
    has_completed_export: bool = False

    #: r10 S9: `ready | pending | failed | null`, off the request's most
    #: recent tag sheet PDF export regardless of its outcome - tells the
    #: portal "never asked" from "in progress" from "failed" so Download PDF
    #: can read the right label instead of a dead button (AC-S9-1). Filled
    #: the same way as `has_completed_export` above.
    latest_export_status: Optional[str] = None

    # D-P6/AC-B6: whether a post-submit edit is currently allowed - True for
    # a draft, or a submitted request at New / Changes requested; False at
    # every other status. Filled by ``response_with_resolved_lines`` for the
    # same reason as the fields above it. The FE Edit button reads this,
    # never the status list.
    is_editable: bool = False


class PriceTagRequestListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    contact_id: str
    project_id: Optional[str] = None
    project_code: Optional[str] = None
    project_label: Optional[str] = None  # `CODE - Title`
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    status: str
    doc_number: str
    needed_by_date: Optional[date] = None
    notes: Optional[str] = None
    created_at: datetime
    # The four things the queue actually draws in its columns, and the four it
    # drew blank: the request row holds ids, and a listing may not show a UUID.
    # Resolved for the whole page in two set-based queries, never per row.
    # D1 (S6): a promotion is a LINE fact now, so there is no header
    # `promotion_id`/`promotion_name` to carry here any more.
    assigned_to_id: Optional[str] = None
    assigned_to_name: Optional[str] = None
    contact_name: Optional[str] = None
    line_count: int = 0
    # A request that was saved and never submitted still carries status "new",
    # so this is the only thing that tells a draft from a submitted request. The
    # portal landing's Draft filter reads it (D45); without it every draft would
    # list as New, and a schema drops what it does not declare just as silently
    # as a response_model does.
    portal_draft_at: Optional[datetime] = None
    # R3-1/AC-R5: the same revision fields the legacy kinds' own summaries
    # carry - the portal card badge and the settings-driven Revisions tab
    # both read these instead of a second round trip.
    revision_no: int = 0
    last_revised_at: Optional[datetime] = None
    has_revision_draft: bool = False
    # AC-D5: the stored count from the product-data-change cache (PLAN
    # price-tag-currency-token-extract-prompt.md section D). Served from the
    # column for an untouched row; the list route refreshes it first for a
    # row a cheap query says was touched since its last check.
    data_changed_tag_count: int = 0


# ---------------------------------------------------------------------------
# Status transition
# ---------------------------------------------------------------------------


class TransitionPayload(BaseModel):
    status: str
    #: A rejection reason, kept as a general review comment (r9 D14) - which is
    #: why it is bounded the same way a pin's body is.
    note: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Tag template document (the layer model, in Python)
#
# The document itself is JSONB and the API takes it as a plain ``dict``, on
# purpose: the editor owns the shape and a template saved by an older build has
# to keep opening. What needs a schema is the SEED - eight documents nobody
# typed into the editor, written by hand from a PDF, whose only reader is a
# renderer that draws nothing at all for a layer kind it does not recognise. A
# mistyped ``price_badge`` would ship as a tag with no price on it and look like
# a pricing bug.
#
# Mirrors `lib/dealer-kit/tag-template-types.ts`. ``extra='forbid'`` throughout,
# because the error worth catching is a key spelled ``asset_id`` where the
# renderer reads ``assetId`` - which a permissive model accepts in silence.
# ---------------------------------------------------------------------------


SLOT_BINDINGS = (
    "product_image",
    "code",
    "name",
    "dimensions",
    "spec_lines",
    "included_accessories",
    "list_price",
    "sell_price",
    "badges",
    "alternatives",
    "accessories",
    "set_members",
)


class _StrictProps(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageSourceAsset(_StrictProps):
    type: Literal["asset"]
    assetId: str


class ImageSourceAttachment(_StrictProps):
    type: Literal["product_attachment"]
    attachmentId: str


class CropRect(_StrictProps):
    x: float
    y: float
    width: float
    height: float


class ImageLayerPropsDoc(_StrictProps):
    kind: Literal["image"]
    source: Optional[Union[ImageSourceAsset, ImageSourceAttachment]] = None
    fit: Literal["cover", "contain", "stretch"] = "contain"
    cropRect: Optional[CropRect] = None
    maskShape: Optional[Literal["none", "circle"]] = "none"


class LayerPaddingDoc(_StrictProps):
    """A layer's own internal margin, in millimetres (S3). Absent means zero
    on every side, so a document saved before S3 validates unchanged."""

    top: float
    right: float
    bottom: float
    left: float


class TextLayerPropsDoc(_StrictProps):
    kind: Literal["text"]
    text: str
    fontFamily: str
    fontSize: float
    fontWeight: int
    color: str
    align: Literal["left", "center", "right"]
    lineHeight: float
    letterSpacing: float
    padding: Optional[LayerPaddingDoc] = None


class PolygonPointDoc(_StrictProps):
    """One polygon corner, normalized to [0, 1] against the layer's own box (S4)."""

    x: float
    y: float


class ShapeLayerPropsDoc(_StrictProps):
    kind: Literal["shape"]
    shape: Literal["rect", "rounded_rect", "ellipse", "line", "polygon"]
    fill: str
    stroke: str
    strokeWidth: float
    cornerRadius: float
    # Only a polygon carries corners, and only once one has been moved: absent
    # means the box's own four (S4, AC-S4-8), which is why no old document
    # needs migrating.
    points: Optional[list[PolygonPointDoc]] = None


class ProductSlotLayerPropsDoc(_StrictProps):
    kind: Literal["product_slot"]
    fieldKey: str


class PriceBadgeLayerPropsDoc(_StrictProps):
    kind: Literal["price_badge"]
    variant: Literal["list_only", "promo"]
    fill: str
    textColor: str
    cornerRadius: float
    showNett: bool
    # Prints `RM` before the figure (S3c, AC-13/14/15). Absent = true, so a
    # badge saved before this flag existed still reads `RM 760`.
    showCurrency: Optional[bool] = None
    # The list-only callout (r4b, AC-S6-1/2): absent means no box, so every
    # badge in the eight seeded layouts prints exactly as it did, and the
    # corners are the same normalized shape a polygon carries.
    showBox: Optional[bool] = None
    points: Optional[list[PolygonPointDoc]] = None
    # The figure's typography (r4b, AC-S6-4/5). Every field optional, absent
    # meaning "the size and face this badge already drew at" - the canvas
    # derives one from the box, the print page uses a fixed point size.
    fontFamily: Optional[str] = None
    fontSize: Optional[float] = None
    fontWeight: Optional[int] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    align: Optional[Literal["left", "center", "right"]] = None
    lineHeight: Optional[float] = None
    letterSpacing: Optional[float] = None
    # The figure's own inset from the callout's edge (S3b). Absent = 0 on
    # every side.
    padding: Optional[LayerPaddingDoc] = None
    # The callout's own inset from the layer box (S3b). Absent means this
    # badge was saved before `margin` existed, in which case `padding` above
    # used to do both jobs at once - `priceBadgeInsets` in the frontend's
    # `price-badge.ts` is the one place that resolves the legacy rule.
    margin: Optional[LayerPaddingDoc] = None


class BadgeLayerPropsDoc(_StrictProps):
    kind: Literal["badge"]
    assetId: str


class GroupBindingDoc(_StrictProps):
    product_id: Optional[str] = None
    product_set_id: Optional[str] = None


class GroupLayerPropsDoc(_StrictProps):
    kind: Literal["group"]
    children: list[str]
    binding: Optional[GroupBindingDoc] = None


TagLayerPropsDoc = Annotated[
    Union[
        ImageLayerPropsDoc,
        TextLayerPropsDoc,
        ShapeLayerPropsDoc,
        ProductSlotLayerPropsDoc,
        PriceBadgeLayerPropsDoc,
        BadgeLayerPropsDoc,
        GroupLayerPropsDoc,
    ],
    Field(discriminator="kind"),
]


class TagLayerDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal[
        "image", "text", "shape", "product_slot", "price_badge",
        "badge", "group",
    ]
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0
    z_index: int
    locked: bool = False
    visible: bool = True
    slot_binding: Optional[Literal[SLOT_BINDINGS]] = None  # type: ignore[valid-type]
    text_override: Optional[str] = None
    props: TagLayerPropsDoc

    @model_validator(mode="after")
    def _type_agrees_with_props(self) -> "TagLayerDoc":
        """``type`` and ``props.kind`` are the same fact written twice.

        The renderers switch on ``props.kind`` and the inspector switches on
        ``type``; a layer where the two disagree draws as one thing and edits as
        another, which is not a state any code downstream checks for.
        """
        if self.type != self.props.kind:
            raise ValueError(
                f"layer type '{self.type}' does not match props kind '{self.props.kind}'"
            )
        return self


class TagTemplateDocModel(BaseModel):
    """The whole document, with every internal reference checked."""

    model_config = ConfigDict(extra="forbid")

    layers: list[TagLayerDoc]
    width_mm: float
    height_mm: float

    @model_validator(mode="after")
    def _references_resolve(self) -> "TagTemplateDocModel":
        ids = [layer.id for layer in self.layers]
        duplicates = {value for value in ids if ids.count(value) > 1}
        if duplicates:
            raise ValueError(f"duplicate layer ids: {sorted(duplicates)}")

        known = set(ids)
        for layer in self.layers:
            if isinstance(layer.props, GroupLayerPropsDoc):
                missing = [child for child in layer.props.children if child not in known]
                if missing:
                    raise ValueError(
                        f"group '{layer.id}' names layers that are not in the document: {missing}"
                    )
        return self


# ---------------------------------------------------------------------------
# Tag template schemas
# ---------------------------------------------------------------------------


class TagTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    family: str = Field(..., min_length=1, max_length=50)
    doc: dict = Field(default_factory=dict)
    print_size: dict = Field(default_factory=dict)


class TagTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    family: Optional[str] = Field(default=None, max_length=50)
    doc: Optional[dict] = None
    print_size: Optional[dict] = None


class TagTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    family: str
    doc: dict
    print_size: dict
    company_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # The live pointer (PLAN D7). Absent = never published, so the request
    # designer's list never sees this row. Filled by the route, not by
    # ``from_attributes`` alone, because the published branch of the list
    # route substitutes a VERSION's doc/print_size for the draft above.
    published_version_id: Optional[str] = None
    published_version_no: Optional[int] = None


# ---------------------------------------------------------------------------
# Tag template versions (S5)
# ---------------------------------------------------------------------------


class TagTemplatePublishIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class TagTemplateVersionResponse(BaseModel):
    """One row of the Versions sheet. No ``doc`` - the list is deliberately
    light; a version's document is fetched only when View is clicked."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    template_id: str
    version_no: int
    note: Optional[str] = None
    created_by: Optional[str] = None
    # Resolved, not stored - a version row holds a user id and nothing a
    # person can read (no UUIDs in the UI). Filled by the route.
    created_by_name: Optional[str] = None
    created_at: datetime


class TagTemplateVersionDetailResponse(TagTemplateVersionResponse):
    """A past version's full document, for View (D16) - read-only on the
    canvas, never mutated."""

    doc: dict
    print_size: dict


# ---------------------------------------------------------------------------
# Save as template (S4, PLAN D1, AC-S4-7)
# ---------------------------------------------------------------------------


class TagTemplateFromTagCreate(BaseModel):
    """What the designer's "Save as template" posts. Creates the template AND
    publishes it as v1 in one transaction - see the route docstring."""

    name: str = Field(..., min_length=1, max_length=255)
    family: str = Field(..., min_length=1, max_length=50)
    doc: dict = Field(default_factory=dict)
    print_size: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tag size presets (S4, PLAN D2)
# ---------------------------------------------------------------------------


class TagSizePresetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    width_mm: float = Field(..., ge=10)
    height_mm: float = Field(..., ge=10)
    #: r10 S7: the configured "per A4" grid - absent means arrange derives
    #: the best fit itself.
    sheet_cols: Optional[int] = Field(default=None, ge=1)
    sheet_rows: Optional[int] = Field(default=None, ge=1)
    sheet_turn: bool = False


class TagSizePresetUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    width_mm: Optional[float] = Field(default=None, ge=10)
    height_mm: Optional[float] = Field(default=None, ge=10)
    #: r10 S7. `None` (sent explicitly) clears the grid back to `Auto`.
    sheet_cols: Optional[int] = Field(default=None, ge=1)
    sheet_rows: Optional[int] = Field(default=None, ge=1)
    sheet_turn: Optional[bool] = None


class TagSizePresetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    width_mm: float
    height_mm: float
    created_by: Optional[str] = None
    # Resolved, not stored - a preset row holds a user id and nothing a
    # person can read (no UUIDs in the UI). Filled by the route.
    created_by_name: Optional[str] = None
    #: r10 S7: the configured "per A4" grid; null means derive.
    sheet_cols: Optional[int] = None
    sheet_rows: Optional[int] = None
    sheet_turn: bool = False
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Tag sheet design doc
# ---------------------------------------------------------------------------


class TagSheetDocPayload(BaseModel):
    """Payload for saving a tag sheet design version."""
    doc: dict
    commit_message: Optional[str] = None


class TagFont(BaseModel):
    """A brand font the editor and the print page load through ``@font-face``."""

    name: str
    family: str
    url: str


class DesignMediaMixin(BaseModel):
    """The three maps a tag sheet needs to DRAW itself (r9 S1/D1).

    Declared once and inherited by both design responses, because
    ``response_model`` drops an undeclared field without a word (LESSONS) and
    the whole point of r9 S1 is that the two previews and the PDF read the same
    three values.
    """

    #: assetId -> signed URL, for every library asset the document names.
    assets: dict[str, str] = {}
    #: attachmentId -> signed URL, for the bound products' own photos.
    images: dict[str, str] = {}
    #: Brand fonts, as same-origin paths (a signed CDN URL has no CORS header).
    fonts: list[TagFont] = []


class TagSheetDocResponse(BaseModel):
    """Response for getting/saving a tag sheet design."""
    page_id: str
    version: int
    doc: Optional[dict] = None
    # WHICH of the two documents this answer came from (B1): ``draft`` is the
    # autosaved work in progress, ``version`` the last deliberate save. The
    # caller has to be able to tell them apart - reopening the designer on a
    # draft and reopening it on the last saved version look identical
    # otherwise, and only one of them is what the user was last looking at.
    # ``version`` above stays the number of the latest immutable version either
    # way, so a draft still reports the version it is sitting on top of.
    source: Literal["draft", "version"] = "version"


class TagSheetDesignResponse(TagSheetDocResponse, DesignMediaMixin):
    """What the CRM Design section reads (r9 S1/D1): the document, its resolved
    lines and the three media maps, in ONE call.

    Separate from ``TagSheetDocResponse`` because the two SAVE routes answer
    that one and have no business resolving artwork; the read route answers
    this. Before r9 the CRM section fetched the doc here and the artwork from
    the asset library route, which is gated on ``dealer_kit.library.manage`` -
    a permission marketing does not hold, so the section drew grey boxes for
    the one role that needs it most.
    """

    lines: list[ResolvedLineData] = []


# ---------------------------------------------------------------------------
# Portal form visibility
# ---------------------------------------------------------------------------


class PortalFormVisibilityResponse(BaseModel):
    visible_types: list[str]


# ---------------------------------------------------------------------------
# Debtor lookup
# ---------------------------------------------------------------------------


class DebtorForAgentItem(BaseModel):
    customer_id: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    debtor_code: Optional[str] = None
    debtor_name: Optional[str] = None
    source: str


# ---------------------------------------------------------------------------
# Tag sheet export
# ---------------------------------------------------------------------------


class TagSheetExportIn(BaseModel):
    """Body for POST /price-tag-requests/{id}/export."""
    sheet_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional filter: only render these sheet ids. None = all sheets.",
    )


class TagSheetExportOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    download_id: str = Field(serialization_alias="downloadId")
    status: str
    filename: Optional[str] = None


# ---------------------------------------------------------------------------
# Tag data for the canvas editor (S3b)
#
# Money crosses this boundary as `float`, not `Decimal`. The arithmetic is done
# in Decimal inside the pricing engine and never here; what a browser needs is a
# number it can format, and Pydantic serialises a Decimal as a JSON string,
# which the canvas would then have to parse back. Formatting happens at the
# edge, once - see the note at the top of `services/dealer_kit/pricing.py`.
# ---------------------------------------------------------------------------


class ProductSearchItem(BaseModel):
    """One row of the editor's product picker. Deliberately three fields."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    product_code: str
    product_name: str


class ProductSetSearchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    set_code: str
    name: str


class TagItemLookupItem(BaseModel):
    """One row of the portal lines table's single Item picker (D47).

    `kind` is what turns into the line's `line_type`, and `id` into whichever of
    `product_id` / `product_set_id` matches it. Both are the real row id: the
    column is a foreign key, and a code there is refused by Postgres.
    """

    kind: Literal["product", "product_set"]
    id: str
    code: str
    name: str


def _uuid_or_none(value: Optional[str]) -> Optional[str]:
    """A caller-supplied id, or a pydantic `ValueError` (422) naming the
    field (R8/R8b, security review): every id on this lookup line reaches a
    UUID column somewhere downstream (`Product.id.in_(...)`,
    `PromotionProduct.promotion_id == ...`), where a non-UUID string is a
    Postgres `DataError` (500), not an empty result.
    """
    if value is None:
        return None
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("must be a valid UUID") from None


class LinePricingLineIn(BaseModel):
    """One line to price (D4, S7). ``manual_sell_price`` is deliberately NOT
    here - a line has no manual figure until it is SAVED, so neither lookup
    route's caller ever has one to send; ``line_pricing`` the service
    function still accepts it as a plain dict key, for ``resolve_tags_live``
    (S9) to reuse the one engine with a persisted line's own figure.

    R7/R8 (security review): each id list is capped at 50, and every id -
    ``product_id``, each ``part_product_ids`` / ``candidate_product_ids``
    entry, ``promotion_id`` - is UUID-shape checked here rather than
    reaching a Postgres `DataError` further down.
    """

    key: str
    product_id: Optional[str] = None
    part_product_ids: list[str] = Field(default_factory=list, max_length=50)
    candidate_product_ids: list[str] = Field(default_factory=list, max_length=50)
    promotion_id: Optional[str] = None

    @field_validator("product_id", "promotion_id", mode="after")
    @classmethod
    def _validate_id(cls, value):
        return _uuid_or_none(value)

    @field_validator("part_product_ids", "candidate_product_ids", mode="after")
    @classmethod
    def _validate_id_list(cls, values):
        return [_uuid_or_none(v) for v in values]


class LinePricingRequest(BaseModel):
    """R7 (security review): `lines` is capped at 200 - the lookup route had
    no body ceiling at all, unlike every other list-shaped payload in this
    module.

    `price_mode` is gone (it rode along from an earlier draft of this
    contract): neither the portal nor the CRM route ever reads
    `payload.price_mode`, `line_pricing` itself takes no such parameter, and
    `sell_price` is a real number in List mode too - AC-S7-1 never gave it a
    job, so dropping it removes dead API surface rather than wiring a
    behaviour nothing asks for.
    """

    lines: list[LinePricingLineIn] = Field(default_factory=list, max_length=200)


class LinePricingPromotionOption(BaseModel):
    id: str
    description: str
    sell_price: float
    # PRICE-TAG-A3-EXPIRED: the end date of an ended A3 flyer still pricing
    # tags inside its type's bound; null for a live promotion. The dropdown
    # labels the option "(expired <date>)" from it.
    expired_on: Optional[date] = None


class LinePricingCandidateOut(BaseModel):
    product_id: str
    list_price: Optional[float] = None
    sell_price: Optional[float] = None


class LinePricingRow(BaseModel):
    """One line's answer (D4's API contract shape).

    R10 (security review): every money field is a plain ``float``, the same
    as ``ResolvedLineData`` - unlike ``manual_sell_price`` on the request/
    line schemas (a documented exception that stays a ``Decimal``, so it
    keeps serialising as a JSON string, per ``test_patch_line_manual_price``),
    this is a LOOKUP row the plan's own contract (AC-S7-1) gives no such
    carve-out, so a caller must be able to do arithmetic on it without a
    ``parseFloat``.
    """

    key: str
    list_price: float
    promotion_options: list[LinePricingPromotionOption] = []
    auto_promotion_id: Optional[str] = None
    sell_price: Optional[float] = None
    sell_price_basis: str
    parts_at_list: list[str] = []
    candidates: list[LinePricingCandidateOut] = []


class TagImage(BaseModel):
    """One photo of a bound product, signed for the viewer that asked."""

    attachment_id: str
    url: str
    is_primary: bool


class SpecValue(BaseModel):
    """One reviewed spec of a product, as `{{spec.<key>}}` draws it (D58)."""

    key: str
    label: str
    value: str
    unit: Optional[str] = None


class SpecKeyItem(BaseModel):
    """One key the merge-field catalogue offers under Specs."""

    key: str
    label: str
    unit: Optional[str] = None


class ProductTagData(BaseModel):
    """Everything a product block draws. Resolved per request, never stored."""

    id: str
    code: str
    name: str
    dimensions: str
    spec_lines: list[str] = []
    specs: list[SpecValue] = []
    images: list[TagImage] = []
    list_price: Optional[float] = None
    offer_price: Optional[float] = None
    promotion_id: Optional[str] = None
    # The product's own `products.barcode` (PLAN D14, price-tag-feedback-r2),
    # for the tag editor's barcode layer (S7). Null when the product carries
    # none, which the layer renders as an editor placeholder / nothing on
    # print.
    barcode: Optional[str] = None
    # S4: staff-authored tag copy, a template rendered against this SAME
    # product's own data (S11). Absent renders nothing (Q5) - no fallback to
    # spec_lines/description. Declared here or `response_model` drops it
    # without a word - see `ResolvedLineData.data_changes`'s own note.
    price_tag_description: Optional[str] = None
    # AC-A10: `products.currency`, defaulted the same way `resolve_prices`
    # already defaults it - the text-slot price prints a bare figure now
    # (Slice A), so the currency travels as its own field.
    currency: str = "MYR"


class ProductSetMemberTagData(BaseModel):
    product_id: str
    code: str
    name: str
    dimensions: str
    quantity: float


class ProductSetTagData(BaseModel):
    id: str
    set_code: str
    name: str
    members: list[ProductSetMemberTagData] = []
    list_price: Optional[float] = None
    offer_price: Optional[float] = None
    promotion_id: Optional[str] = None
    # AC-A11: the first member's currency.
    currency: str = "MYR"


class ResolvePreviewIn(BaseModel):
    """What the template editor wants priced.

    One of ``product_id`` / ``product_set_id`` is required; the route answers
    422 when neither is named, because a preview of nothing is a mistake rather
    than an empty result.
    """

    product_id: Optional[str] = None
    product_set_id: Optional[str] = None
    promotion_id: Optional[str] = None


class ResolvePreviewOut(BaseModel):
    product: Optional[ProductTagData] = None
    product_set: Optional[ProductSetTagData] = None


class LineDataChange(BaseModel):
    """One field master data has moved under a pinned tag (r9 D17)."""

    field: str
    label: str
    old: Optional[str] = None
    new: Optional[str] = None
    old_image_url: Optional[str] = None
    new_image_url: Optional[str] = None
    #: Why the value moved, when that is not obvious ("Promotion ended").
    note: Optional[str] = None


class TagDataChangeSet(BaseModel):
    """One TAG's worth of pending decision, named by its code not its id.

    Per tag rather than per line since the combos slice: two tags split off one
    line resolve different products, so they are drawn from different data and
    a Keep on one must not silence the other. `line_id` and `tag_label` are
    what the reader is shown ("1a" under the line's code), never the ids.
    """

    tag_id: str
    tag_label: str = ""
    line_id: str
    code: str
    name: str
    changes: list[LineDataChange] = []


class TagOpenGroupCandidate(BaseModel):
    product_id: str
    code: str


class TagOpenGroup(BaseModel):
    """A choice group this tag has not resolved (D3).

    The candidate carries its id beside its code because "Pick one" has to name
    it back to `PATCH .../tags/{tag_id}`, whose `choices` is `{role: product_id}`.
    Only the code is ever rendered (AC-X-2).
    """

    role: str
    candidates: list[TagOpenGroupCandidate] = []


class TagPartData(BaseModel):
    """One resolved part on a tag, D7's "full product data" (AC-S9-1) - a
    layer may pick ANY part as its subject, so a part needs everything the
    host already carries.
    """

    #: Carried so a caller can match a part back to the choice that produced it.
    #: Never rendered - the code is what a reader sees (AC-X-2).
    product_id: Optional[str] = None
    code: str
    name: str
    dimensions: str = ""
    spec_lines: list[str] = []
    specs: list[SpecValue] = []
    images: list[TagImage] = []
    barcode: Optional[str] = None
    list_price: Optional[float] = None
    #: Offer under the LINE's promotion, or None - never a fall back to list
    #: (AC-S9-1): a part printing at list beside its own code is not "on
    #: sale", the tag's box total is what decides that.
    sell_price: Optional[float] = None
    # AC-A11: this part's OWN product's currency, not the host's.
    currency: str = "MYR"
    #: AC-S4-4: this part's OWN product's tag copy, not the host's - a
    #: subjectPart layer reads THIS, never the host's.
    price_tag_description: Optional[str] = None
    #: r10 S6: the choice group this row belongs to ("Kitchen Tap"), so the
    #: subject picker groups candidates under it. Absent on a fixed part.
    role: Optional[str] = None
    #: r10 S6: true on the candidate THIS tag's own choices name.
    chosen: Optional[bool] = None


class ResolvedLineData(BaseModel):
    """Display data for one TAG, for the designer and the print page (D3).

    One row per tag since S3, not per line: `line_id` says which line asked for
    it and `tag_id` is what the document, the rail and the resolved-data map key
    on.
    """

    tag_id: str
    tag_label: str = ""
    open_groups: list[TagOpenGroup] = []
    #: r10 S6: every product this tag's combo could show - a superset of
    #: `own_parts`, which is what this tag itself prints and prices.
    parts: list[TagPartData] = []
    #: r10 S6: the parts this tag itself prints - fixed parts plus its own
    #: chosen candidate. Absent on a row pinned before r10, where `parts`
    #: was already this list (AC-S6-11) - the FE falls back to `parts`.
    own_parts: Optional[list[TagPartData]] = None
    line_id: str
    code: str
    name: str
    dimensions: str
    spec_lines: str
    specs: list[SpecValue] = []
    set_members: str = ""
    images: list[TagImage] = []
    list_price: Optional[float] = None
    sell_price: Optional[float] = None
    # D7 (S9): the HOST alone, never the roll-up above - a price badge's
    # `subjectPart: -1` reads THIS.
    parent_list_price: Optional[float] = None
    parent_sell_price: Optional[float] = None
    # D3/AC-S9-3: why `sell_price` is what it is - `manual` | `promotion` |
    # `list`.
    sell_price_basis: Optional[str] = None
    show_promo_price: bool
    included_accessories: str = ""
    quantity: int
    # Empty for a set line: a set has no barcode of its own (S7).
    barcode: Optional[str] = None
    # AC-A11/A12: the tag's own currency (response_model gate).
    currency: str = "MYR"
    # S4: staff-authored tag copy, a template rendered against this SAME
    # line's own data (S11) - the line host's own value, distinct from each
    # part's own `price_tag_description` above. Declared here or
    # `response_model` drops it without a word, exactly like `data_changes`
    # below - measured, `test_dealer_kit_tag_data_routes.py::
    # test_resolve_prices_carries_price_tag_description_on_the_line_and_its_parts`.
    price_tag_description: Optional[str] = None
    # What master data has moved under this line since it was pinned (r9 D17).
    # Declared here or `response_model` drops it without a word, which is how
    # the CRM designer's own red dot went missing while the detail page's did
    # not: they read the same resolver through two different response models.
    #
    # Optional, and absent rather than empty when the resolver did not compute
    # it: `[]` means "nothing has moved", and a terminal request runs no live
    # resolve at all, so answering `[]` there would be a claim nobody checked.
    data_changes: Optional[list[LineDataChange]] = None

    @model_serializer(mode="wrap")
    def _drop_absent_data_changes(self, handler):
        """No key at all when there is no diff to report (S10)."""
        data = handler(self)
        if data.get("data_changes") is None:
            data.pop("data_changes", None)
        return data


class PortalTagSheetDesignResponse(DesignMediaMixin):
    """The portal's design preview (D11): the same doc `TagSheetDocResponse`
    carries, PLUS the resolved line data the CRM designer reads through a
    SEPARATE `/resolve-prices` call - the portal has no such second call, so
    this route answers both in one response, media included (r9 S1/D1)."""

    page_id: str
    version: int
    doc: Optional[dict] = None
    source: Literal["draft", "version"] = "version"
    lines: list[ResolvedLineData] = []


class AssetResponse(BaseModel):
    """One row of the Dealer Kit artwork library.

    ``url`` is null when the file cannot be signed - absent rather than broken,
    the same rule the catalogue uses for a background it cannot serve.
    """

    id: str
    name: str
    kind: str
    tags: list[str] = []
    url: Optional[str] = None
    mime_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Pinned change requests (r9 S2/D4-D6)
# ---------------------------------------------------------------------------

#: Long enough for anything a person types about one tag, short enough that a
#: single request cannot post a novel into the column.
REVIEW_COMMENT_MAX_LENGTH = 2000
#: Pins in one round. A round is placed by hand, one click at a time.
REVIEW_COMMENTS_MAX_PER_ROUND = 50


class ReviewCommentPin(BaseModel):
    """One pin as the portal sends it: an anchor on a TAG, and what to change.

    ``allow_inf_nan=False`` is not a nicety: ``float("nan")`` passes every
    comparison (``0 <= nan <= 1`` is False, but ``nan < 0`` is False too), so a
    hand-rolled range check lets it through, ``numeric(6,4)`` stores it, and
    every later read of that request 500s on serialisation - permanently.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    #: The tag the pin was placed on. Absent = a general comment about the
    #: whole design. A line prints one tag per option since the combos slice,
    #: and each copy of a tag on the sheet is the SAME tag, so the anchor needs
    #: no placed-copy id: the pin draws on every copy of its own tag.
    tag_id: Optional[str] = None
    #: Fractions of the tag box, 0..1. Absent on a general comment.
    x: Optional[float] = Field(None, ge=0, le=1)
    y: Optional[float] = Field(None, ge=0, le=1)
    #: 0 for a point pin, > 0 for a box.
    w: Optional[float] = Field(None, ge=0, le=1)
    h: Optional[float] = Field(None, ge=0, le=1)
    body: str = Field(..., max_length=REVIEW_COMMENT_MAX_LENGTH)


class RequestChangesPayload(BaseModel):
    """A whole round in one call (D5).

    ``note`` alone is the legacy body, accepted for one release and stored as a
    general comment, so a portal that has not reloaded still works.

    Bounded on both axes: a round is a handful of pins somebody placed by hand,
    so fifty is far past any real one and small enough that no single call can
    write an unbounded number of rows.
    """

    comments: list[ReviewCommentPin] = Field(
        default_factory=list, max_length=REVIEW_COMMENTS_MAX_PER_ROUND
    )
    note: Optional[str] = Field(None, max_length=REVIEW_COMMENT_MAX_LENGTH)


class ReviewCommentResponse(BaseModel):
    """One `price_tag_review_comments` row, as both surfaces read it."""

    id: str
    request_id: str
    tag_id: Optional[str] = None
    round: int
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    body: str
    author_name: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RequestChangesResponse(BaseModel):
    """What Send answers: where the request went, and what it wrote."""

    status: str
    round: int
    comments: list[ReviewCommentResponse] = []


class ReviewCommentResolvePayload(BaseModel):
    resolved: bool


# ---------------------------------------------------------------------------
# The product data gate and the request's history (r9 S5/D18-D19)
# ---------------------------------------------------------------------------


class TagPinPayload(BaseModel):
    """What to do about the change master data has made under a tag."""

    action: Literal["update", "keep"]


class TagPinResponse(BaseModel):
    tag_id: str
    pinned_at: Optional[datetime] = None


class TagDismissResponse(BaseModel):
    """r10 S8: the answer to `POST tags/{id}/dismiss` - nothing on the tag
    moved (the new data is already pinned), so there is nothing to echo
    beyond which tag it was."""

    tag_id: str


class RequestVersionSummary(BaseModel):
    """One row of the design's history. Names, never ids."""

    version: int
    commit_message: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: datetime
