"""Price tag request service: doc-number generation, creation, status transitions.

The status graph:

    new -> designing -> proof_ready -> approved -> ready
                  ^                |
                  |                v
                  +-- changes_requested
    * -> rejected
    * -> void

``void`` and ``rejected`` are reachable from any non-terminal status.
``ready`` is terminal - once exported, no further transitions.
"""
import copy
import logging
import uuid
from typing import Optional
from datetime import date, datetime, timedelta

from sqlalchemy import Integer, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.base import company_scope
from app.models.price_tag import PriceTagRequest, PriceTagRequestLine
from app.services.error_handler import AppException

logger = logging.getLogger(__name__)

# Status constants.
STATUS_NEW = "new"
STATUS_DESIGNING = "designing"
STATUS_PROOF_READY = "proof_ready"
STATUS_CHANGES_REQUESTED = "changes_requested"
STATUS_APPROVED = "approved"
# r9 D8: `ready` is retired. It said a PDF existed and nothing about whether
# anybody had the tags; the office print now records the hand-over instead.
# The constant stays for the migration that maps the old rows over.
STATUS_READY = "ready"
STATUS_READY_FOR_COLLECTION = "ready_for_collection"
STATUS_COLLECTED = "collected"
STATUS_REJECTED = "rejected"
STATUS_VOID = "void"

# Terminal for everyone. `approved` joins them for a SELF print only, which is
# why `is_terminal` takes the request rather than the status (D8).
_TERMINAL = frozenset({STATUS_COLLECTED, STATUS_REJECTED, STATUS_VOID})

PRINT_BY_OFFICE = "office"
PRINT_BY_SELF = "self"
PRINT_BY_CHOICES = frozenset({PRINT_BY_OFFICE, PRINT_BY_SELF})

# Valid transitions: current_status -> set of allowed next statuses.
# ``rejected`` and ``void`` are reachable from any non-terminal status.
VALID_TRANSITIONS: dict[str, set[str]] = {
    STATUS_NEW: {STATUS_DESIGNING, STATUS_REJECTED, STATUS_VOID},
    STATUS_DESIGNING: {
        STATUS_PROOF_READY,
        STATUS_REJECTED,
        STATUS_VOID,
    },
    STATUS_PROOF_READY: {
        STATUS_APPROVED,
        STATUS_CHANGES_REQUESTED,
        STATUS_REJECTED,
        STATUS_VOID,
    },
    STATUS_CHANGES_REQUESTED: {
        STATUS_DESIGNING,
        # Marketing revises and re-sends without a forced detour through
        # `designing` first - the FE's own Mark design ready CTA already
        # shows at designing OR changes_requested (RequestTagDesigner.tsx)
        # and posts `proof_ready` directly; the extra hop served nobody and
        # only produced a 409 the toast then swallowed.
        STATUS_PROOF_READY,
        STATUS_REJECTED,
        STATUS_VOID,
    },
    # The office hand-over (D8). `ready_for_collection` is reachable only when
    # somebody has said the office prints - `transition_status` checks that on
    # top of this table, because the graph alone cannot see `print_by`.
    STATUS_APPROVED: {STATUS_READY_FOR_COLLECTION, STATUS_REJECTED, STATUS_VOID},
    # Its ONLY exit: the tags exist, they are on the counter, and the single
    # remaining question is whether anybody has taken them.
    STATUS_READY_FOR_COLLECTION: {STATUS_COLLECTED},
    # Terminal statuses have no outgoing edges. `ready` is retired (D8) and has
    # no entry at all: the migration maps every row that carried it to
    # `approved`, so nothing can arrive at it again.
    STATUS_COLLECTED: set(),
    STATUS_REJECTED: set(),
    STATUS_VOID: set(),
}

# How many times a create re-derives its number after losing an insert race
# before it gives up. Five is far past what a human-paced portal ever needs; the
# point is that the loop terminates rather than spins.
_DOC_NUMBER_ATTEMPTS = 5

# The classes the package guard warns about when `system_settings` has no row at
# all. Repeats `SystemSetting.price_tag_guarded_classes`' own server default
# (app/models/user.py), which is the source of truth; this copy only covers a
# database with no settings row, which is every fresh install and every test that
# does not seed one.
_DEFAULT_GUARDED_CLASSES = ("Bathroom Furniture", "Kitchen Sink")


def sales_agents_for_contact(db: Session, contact_id: str) -> list:
    """Every active SalesAgent a portal contact is linked to, by code; empty when none.

    PORTAL-MULTI-AGENT (owner, 16 Oct 2026): a contact linked to several agents sees the
    customers of all of them together, so this answers every link instead of the first
    one by code. Delegates to `app.services.sales.portal_agent.agents_for_contact`, so
    there is one copy of the rule, and stays as the module-level seam the debtor lookup,
    Customer asks, Conversations and the form visibility read at call time.
    """
    from app.services.sales.portal_agent import agents_for_contact

    return agents_for_contact(db, contact_id)


class PriceTagRequestService:
    """Stateless helpers for price tag requests."""

    @staticmethod
    def generate_doc_number(db: Session, company_id: str) -> str:
        """``PT-YYYYMM-NNNN``: the month's HIGHEST sequence so far, plus one.

        Two things about ``doc_number`` decide this, and neither is negotiable:
        it is UNIQUE across the whole table, and a draft HARD-deletes (D48b).

        A sequence derived from a COUNT of the surviving rows therefore re-issues
        a number that a deleted draft already spent, and the next create died on
        ``price_tag_requests_doc_number_key`` with a 500 - the salesperson's Save
        Draft simply stopped working after any delete. A COUNT scoped to one
        company had the same fault across companies: the constraint is global, so
        the sequence has to be global too.

        MAX over the four-digit suffix answers both. It is read with the company
        scope OFF for the same reason: the numbering space is the table, not the
        partition, and a scoped read would hand the next company a number that is
        already taken. Nothing but the number is read.

        ``company_id`` stays in the signature because the caller has it and a
        future per-company prefix would need it; it does not narrow the sequence.
        """
        now = datetime.utcnow()
        prefix = f"PT-{now.strftime('%Y%m')}-"
        with company_scope(db, None):
            highest = (
                db.query(
                    func.max(
                        cast(
                            func.substr(PriceTagRequest.doc_number, len(prefix) + 1),
                            Integer,
                        )
                    )
                )
                .filter(PriceTagRequest.doc_number.like(f"{prefix}%"))
                .scalar()
            ) or 0
        return f"{prefix}{int(highest) + 1:04d}"

    @staticmethod
    def create_request(
        db: Session,
        contact_id: str,
        company_id: str,
        data: dict,
        viewer=None,
    ) -> PriceTagRequest:
        """Create a price tag request with its lines.

        ``data`` keys: debtor_code, debtor_name, needed_by_date, notes, lines
        (list of line dicts, each of which may carry its own ``promotion_id``
        / ``manual_sell_price`` - D1, the promotion is a LINE fact since S6,
        and the ONLY place it lives - no request-level default). EVERY one of
        them is optional (D48a): Save Draft validates nothing, so a form with
        one line and no dealer is a request this has to be able to store.
        Completeness is checked on submit by ``validate_submittable``.

        Sets ``portal_draft_at`` on creation (the request starts as a draft).

        ``viewer`` decides which promotions a LINE's own ``promotion_id`` is
        validated and priced against (AC-S6-5, AC-S7-2) - the portal routes
        pass the contact's own audience; every other caller (CRM, and this
        default) gets ``staff_viewer()``, which is not bound by a portal
        contact's audience and matches the same "CRM has no contact to check"
        rule the old header-level audience gate followed.
        """
        request = PriceTagRequestService._insert_with_doc_number(
            db,
            lambda doc_number: PriceTagRequest(
                contact_id=contact_id,
                company_id=company_id,
                debtor_code=data.get("debtor_code"),
                debtor_name=data.get("debtor_name"),
                needed_by_date=data.get("needed_by_date"),
                notes=data.get("notes"),
                price_mode=data.get("price_mode") or "list",
                # Who prints (r9 D7). Null on a draft; `submit` refuses until
                # the salesperson has answered.
                print_by=data.get("print_by"),
                doc_number=doc_number,
                portal_draft_at=datetime.utcnow(),
            ),
            company_id,
        )

        PriceTagRequestService._add_lines(
            db, request, data.get("lines") or [], viewer=viewer
        )
        db.flush()
        return request

    @staticmethod
    def _insert_with_doc_number(db: Session, build, company_id: str) -> PriceTagRequest:
        """Insert a request, taking the next number if another writer took ours.

        The MAX in ``generate_doc_number`` closes the gap a delete leaves, but two
        creates in flight still read the same MAX, and the loser used to get a 500
        instead of the next number. Each attempt runs inside a SAVEPOINT so a
        unique violation costs the attempt and not the whole transaction, which by
        then holds the caller's other work.
        """
        for attempt in range(_DOC_NUMBER_ATTEMPTS):
            request = build(PriceTagRequestService.generate_doc_number(db, company_id))
            db.add(request)
            savepoint = db.begin_nested()
            try:
                db.flush()  # get the id, and find out about a collision here
                savepoint.commit()
                return request
            except IntegrityError:
                savepoint.rollback()
                logger.warning(
                    "Price tag doc number %s was taken while creating; retrying "
                    "(attempt %s of %s).",
                    request.doc_number,
                    attempt + 1,
                    _DOC_NUMBER_ATTEMPTS,
                )
        raise AppException(
            status_code=409,
            message=(
                "Could not allocate a document number for this request. "
                "Please try again."
            ),
            code="DOC_NUMBER_UNAVAILABLE",
        )

    @staticmethod
    def _validate_line_price_basis(
        promotion_id: str | None,
        manual_sell_price,
        price_mode: str,
        *,
        detail: str | None = None,
    ) -> None:
        """AC-S6-4: a manual price and a promotion are mutually exclusive on
        a line, and a manual figure only means anything in Selling mode -
        List mode prints the list price regardless of what was typed, so a
        stray manual figure there is a mistake worth naming rather than a
        value silently thrown away.

        The ONE seam every arm that can set a line's price basis passes
        through - create, update and revise (`_add_lines`), and the CRM line
        PATCH (`set_line_price`) - so none of them can accept something
        another refuses. Used to live twice: once here (raising an
        `AppException`, naming the line by index) and once again as a
        Pydantic ``@model_validator`` on the create/update schemas, which
        revise's raw-dict payload never ran through at all (security review
        finding) - the validator produced a different error SHAPE
        (`RequestValidationError`'s `{"detail": [...]}` list) than this
        one's (`AppException`'s `{"message", "detail", "code"}`), so it was
        deleted rather than kept as an "earlier" duplicate: two shapes for
        the same refusal is worse than one that runs a query later.
        """
        if manual_sell_price is None:
            return
        if promotion_id is not None:
            raise AppException(
                status_code=422,
                message="A manual price and a promotion are mutually exclusive.",
                detail=detail,
                code="INVALID_LINE_PRICE",
            )
        if price_mode != "selling":
            raise AppException(
                status_code=422,
                message="A manual price only applies in Selling mode.",
                detail=detail,
                code="INVALID_LINE_PRICE",
            )

    @staticmethod
    def _add_lines(
        db: Session,
        request: PriceTagRequest,
        lines: list[dict],
        *,
        carry_tags: dict[tuple, list[dict]] | None = None,
        viewer=None,
    ) -> None:
        """Append lines in the order given, which is the order the form shows.

        ``show_promo_price`` is DERIVED per line (D1/D3/AC-S7-5) from the
        request's header ``price_mode`` AND the line's own
        ``sell_price_basis`` (through ``line_pricing`` - S7): a line in
        Selling mode with no covering promotion and no manual figure is
        still LP, same as a line whose promotion offer does not beat list.
        Every line save - create, replace on update - re-derives it from
        scratch, so neither a header flip nor a line's own promotion change
        ever leaves a stale value behind.

        Every line gets its TAGS here too (D3): exactly one, carrying the line's
        quantity and an empty `choices`, unless ``carry_tags`` hands over the set
        a surviving line already had. ``replace_lines`` passes that map, keyed by
        ``(product_id, product_set_id)``, since the form payload has no field for
        any of it - a re-save with just a new remark used to silently wipe a
        marketing-set override, and would now silently un-split the line.

        Raises 422 ``DUPLICATE_LINE`` (round 3, R3-7/AC-R4) naming the code
        the FIRST time the same product or set repeats within ``lines`` -
        before any insert, so the table's own
        ``uq_ptag_line_request_product`` / ``uq_ptag_line_request_set``
        constraints never get the chance to answer with a 500.

        ``viewer`` (AC-S6-5, AC-S7-2) decides which promotions a line's own
        ``promotion_id`` may be validated and priced against - see
        ``create_request``'s own docstring for who passes what.
        """
        from app.services.dealer_kit.pricing import line_pricing
        from app.services.dealer_kit.tag_data_service import staff_viewer

        viewer = viewer or staff_viewer()
        PriceTagRequestService._raise_on_duplicate_line(db, lines)
        selling = request.price_mode == "selling"
        carry_tags = carry_tags or {}
        for idx, line_data in enumerate(lines):
            sort_order = line_data.get("sort_order")
            # Re-review finding: the line's own host id arrives from the
            # portal exactly like a part id does, and was written straight
            # through with no shape check - the very gap that made the D5
            # combo guard (`_add_line_parts`) a cross-company existence
            # oracle in the first place. Routed through the SAME `_part_uuid`
            # the part ids already go through, so a non-UUID string is a 422
            # naming the line here too, not a Postgres `DataError` once it
            # reaches `Product.id.in_(...)` inside the visibility query below.
            product_id = PriceTagRequestService._part_uuid(
                line_data.get("product_id"), idx
            )
            product_set_id = PriceTagRequestService._part_uuid(
                line_data.get("product_set_id"), idx
            )
            key = (product_id, product_set_id)
            # Security review: the line's own host id must be visible to
            # THIS request's company, the same reason `_add_line_parts`
            # scopes a part - written straight through with no company check
            # would answer "does this id exist anywhere" for a product in
            # another company. Scoped lookup, 422 naming the line on a miss.
            if product_id and product_id not in PriceTagRequestService._visible_product_ids(
                db, {product_id}, request.company_id
            ):
                raise AppException(
                    status_code=422,
                    message="This line's product could not be found.",
                    detail=f"line:{idx}",
                    code="INVALID_PART",
                )
            if (
                product_set_id
                and product_set_id
                not in PriceTagRequestService._visible_product_set_ids(
                    db, {product_set_id}, request.company_id
                )
            ):
                raise AppException(
                    status_code=422,
                    message="This line's set could not be found.",
                    detail=f"line:{idx}",
                    code="INVALID_PART",
                )

            promotion_id = line_data.get("promotion_id")
            manual_sell_price = line_data.get("manual_sell_price")
            PriceTagRequestService._validate_line_price_basis(
                promotion_id, manual_sell_price, request.price_mode, detail=f"line:{idx}"
            )
            # D1/D3/AC-S7-5: a line's price basis, resolved through the SAME
            # engine the portal's and the CRM's own line-pricing routes call
            # (S7) - `show_promo_price` can never disagree with what those
            # routes just showed the salesperson.
            basis = "list"
            if product_id:
                parts_data = line_data.get("parts") or []
                # Security review finding (this round): a malformed part or
                # candidate id used to reach `line_pricing`'s
                # `Product.id.in_(...)` BEFORE `_add_line_parts` below ever
                # got a chance to validate it, so a junk candidate 500'd
                # (Postgres `DataError`) instead of the named 422
                # `_add_line_parts` already gives it. Same gate
                # (`_part_uuid`), run here FIRST - every id a pricing lookup
                # is about to use is validated before any query touches it.
                resolved_part_ids = [
                    PriceTagRequestService._part_uuid(p["product_id"], idx)
                    for p in parts_data
                    if p.get("product_id")
                ]
                candidate_ids = [
                    PriceTagRequestService._part_uuid(c, idx)
                    for p in parts_data
                    if not p.get("product_id")
                    for c in (p.get("candidates") or [])
                ]
                pricing_row = line_pricing(
                    db,
                    lines=[
                        {
                            "key": "_p",
                            "product_id": product_id,
                            "part_product_ids": resolved_part_ids,
                            "candidate_product_ids": candidate_ids,
                            "promotion_id": promotion_id,
                            "manual_sell_price": manual_sell_price,
                        }
                    ],
                    viewer=viewer,
                    # A SAVED line's basis is what the salesperson committed
                    # to, never the lookup's auto pick (owner ruling, review
                    # round 2): with no stored promotion the line is `list`,
                    # however many promotions cover its product.
                    auto_pick=False,
                )[0]
                if promotion_id and promotion_id not in {
                    option["id"] for option in pricing_row["promotion_options"]
                }:
                    # AC-S6-5: a line's promotion must be active, visible to
                    # this viewer, and cover at least one product on the line
                    # (the parent, a resolved part, or any candidate) -
                    # answered by whether it shows up in the SAME covering
                    # list `line_pricing` just computed, so validation and
                    # pricing can never disagree about what "covers this
                    # line" means.
                    raise AppException(
                        status_code=422,
                        message="This promotion does not apply to this line.",
                        detail=f"line:{idx}",
                        code="PROMOTION_NOT_AVAILABLE",
                    )
                basis = pricing_row["sell_price_basis"]
            elif product_set_id:
                # Security review finding (H1/S1): this whole block used to
                # be gated on `if product_id:` alone, so a `product_set`
                # line skipped promotion validation AND pricing entirely - a
                # promotion outside the audience was never refused, and a
                # covering one never priced the set as SP. The set's members
                # ARE its resolved products for this purpose (AC-S6-5), the
                # same "parent + fixed parts" reading a product line's own
                # parts get - no single "parent", so they go through as
                # `part_product_ids` with no `product_id` of their own.
                member_ids = PriceTagRequestService._set_member_product_ids(
                    db, product_set_id
                )
                pricing_row = line_pricing(
                    db,
                    lines=[
                        {
                            "key": "_p",
                            "product_id": None,
                            "part_product_ids": member_ids,
                            "candidate_product_ids": [],
                            "promotion_id": promotion_id,
                            "manual_sell_price": manual_sell_price,
                        }
                    ],
                    viewer=viewer,
                    # A SAVED line's basis is what the salesperson committed
                    # to, never the lookup's auto pick (owner ruling, review
                    # round 2): with no stored promotion the line is `list`,
                    # however many promotions cover its product.
                    auto_pick=False,
                )[0]
                if promotion_id and promotion_id not in {
                    option["id"] for option in pricing_row["promotion_options"]
                }:
                    raise AppException(
                        status_code=422,
                        message="This promotion does not apply to this line.",
                        detail=f"line:{idx}",
                        code="PROMOTION_NOT_AVAILABLE",
                    )
                basis = pricing_row["sell_price_basis"]

            line = (
                PriceTagRequestLine(
                    request_id=request.id,
                    line_type=line_data["line_type"],
                    product_id=product_id,
                    product_set_id=product_set_id,
                    show_promo_price=selling and basis != "list",
                    quantity=line_data.get("quantity", 1),
                    combo_id=PriceTagRequestService._resolve_combo_id(
                        db, line_data, request.company_id
                    ),
                    included_accessories=line_data.get("included_accessories"),
                    remarks=line_data.get("remarks"),
                    sort_order=idx if sort_order is None else sort_order,
                    promotion_id=promotion_id,
                    manual_sell_price=manual_sell_price,
                )
            )
            db.add(line)
            db.flush()
            PriceTagRequestService._add_line_parts(
                db,
                line,
                line_data.get("parts") or [],
                index=idx,
                company_id=request.company_id,
            )
            PriceTagRequestService._add_line_tags(db, line, carry_tags.get(key))

    @staticmethod
    def _add_line_tags(db: Session, line, carried: list[dict] | None = None) -> None:
        """The tags that will be printed for this line (D3/D6, AC-S8-1..S8-3).

        One tag per candidate COMBINATION across every open choice group the
        line still has (D6, owner ruling): a line with no open group mints
        exactly one, carrying its quantity and an empty `choices`, same as
        before this slice. A line with one open group of N candidates mints
        N; two open groups of N and M mint N x M, `choices` filled for every
        one of them - straight-line, no Split / Pick one left for the
        designer to do by hand.

        `carried` is a surviving line's existing tag set, handed over by
        `replace_lines`. AC-S8-3: when the line's parts are UNCHANGED (the
        set of `choices` maps this save would mint is exactly what `carried`
        already has), the carried rows are reused as-is - id, sort_order,
        quantity, overrides included - rather than rebuilt, so a remark edit
        does not reshuffle tags the designer has already placed or discard a
        split marketing made by hand before D6 shipped. Anything else
        (different parts, or no carried set at all) mints fresh tags.
        """
        from itertools import product as _cartesian

        from app.models.price_tag import PriceTagRequestTag

        open_groups = [
            (part.role or "", list(part.candidates or []))
            for part in (line.parts or [])
            if not part.product_id and part.candidates
        ]
        if open_groups:
            roles = [role for role, _ in open_groups]
            combos = list(_cartesian(*[candidates for _, candidates in open_groups]))
            new_choice_maps = [dict(zip(roles, combo)) for combo in combos]
        else:
            new_choice_maps = [{}]

        if carried:
            carried_keys = {
                tuple(sorted((row.get("choices") or {}).items())) for row in carried
            }
            new_keys = {tuple(sorted(choices.items())) for choices in new_choice_maps}
            if carried_keys == new_keys:
                rows = carried
                # A line that was never split has exactly one tag, and that
                # tag's quantity is not marketing's - it is the salesperson's
                # number, seeded from the line. Without this, a draft saved
                # at 1, changed to 5 and submitted (or revised to 9) kept a
                # tag at 1 and printed one tile instead of five (review round
                # 2, B1). A SPLIT line keeps its per-tag quantities: once
                # marketing has divided the line up, those numbers are
                # decisions, not a copy of anything.
                if len(rows) == 1:
                    rows = [{**rows[0], "quantity": line.quantity or 1}]
                for index, row in enumerate(rows):
                    db.add(
                        PriceTagRequestTag(
                            id=row.get("id"),
                            line_id=line.id,
                            sort_order=row.get("sort_order", index),
                            quantity=row.get("quantity") or 1,
                            choices=row.get("choices") or {},
                            marketing_price_override=row.get("marketing_price_override"),
                            marketing_override_reason=row.get("marketing_override_reason"),
                            print_excluded=bool(row.get("print_excluded")),
                        )
                    )
                return

        for index, choices in enumerate(new_choice_maps):
            db.add(
                PriceTagRequestTag(
                    line_id=line.id,
                    sort_order=index,
                    quantity=line.quantity or 1,
                    choices=choices,
                )
            )

    @staticmethod
    def _add_line_parts(
        db: Session,
        line,
        parts: list[dict],
        *,
        index: int = 0,
        company_id: str | None = None,
    ) -> None:
        """The package under a line, written in the order the form sent it (AC-S2-8).

        Order is display order on the request, in the tag's parts text (D4) and in
        the designer's rail, so the position is stored rather than left to the
        primary key.

        Two shapes, and the table's own CHECK keeps them apart: a RESOLVED row
        names a product and carries no candidates; an OPEN row names the choice
        group and the candidates it is still choosing between. A row that is
        neither - no product and no candidates - is dropped rather than written,
        because the constraint would refuse it with a 500 the salesperson cannot
        act on and an empty row means nothing anyway.

        EVERY id here comes from the portal, so every id is validated before it
        is stored (security review B1). Two separate failures were reachable by
        any portal contact:

        * a non-UUID candidate is accepted by JSONB and then blows up
          `Product.id.in_(...)` on EVERY later read of that request - a stored
          denial of service on the request, the designer and the PDF alike;
        * an id belonging to another company would be stored and then resolved,
          leaking that product onto this company's tag.

        One scoped query answers both: anything the caller cannot see simply is
        not returned, and a 422 naming the row is what the form can act on.
        """
        from app.models.price_tag import PriceTagRequestLinePart
        from app.models.product import Product
        from app.models.product_combo import ProductCombo

        # D5: a part has nowhere to go without a package. A `product_set`
        # line has no `product_id` and is already skipped by this check; a
        # product line whose product carries zero `ProductCombo` rows must
        # never accept a part, on save exactly like the FE hides "Add part"
        # for it (D4) - the portal is not a trusted client.
        #
        # Security review: `ProductCombo` is not company-scoped on its own -
        # it hangs off its host product, same as `_resolve_combo_id` below -
        # so a bare `ProductCombo` query ignores `company_scope` entirely and
        # this 422 would answer "does this id exist anywhere" for a product
        # in another company. Joined to `Product` so the scope predicate has
        # something to attach to, the same fix `_resolve_combo_id` already
        # has for the combo-id path.
        if parts and line.product_id:
            def _has_combo() -> bool:
                return (
                    db.query(ProductCombo.id)
                    .join(Product, Product.id == ProductCombo.host_product_id)
                    .filter(ProductCombo.host_product_id == line.product_id)
                    .first()
                    is not None
                )

            if company_id:
                with company_scope(db, frozenset({company_id})):
                    has_combo = _has_combo()
            else:
                has_combo = _has_combo()
            if not has_combo:
                raise AppException(
                    status_code=422,
                    message="This product has no package to add a part to.",
                    detail=f"line:{index}",
                    code="PARTS_NEED_COMBO",
                )

        cleaned: list[dict] = []
        wanted: set[str] = set()
        for part in parts:
            product_id = PriceTagRequestService._part_uuid(part.get("product_id"), index)
            candidates = [
                PriceTagRequestService._part_uuid(candidate, index)
                for candidate in (part.get("candidates") or [])
                if candidate
            ]
            if product_id:
                candidates = []
            elif not candidates:
                continue
            cleaned.append(
                {"product_id": product_id, "role": part.get("role"), "candidates": candidates}
            )
            if product_id:
                wanted.add(product_id)
            wanted.update(candidates)

        if wanted:
            # Scoped to the REQUEST's own company, not to whatever scope happens
            # to be ambient: `submit_request` is TOLD which company it is
            # creating for, and a portal contact resolving to another company
            # would otherwise have every one of its own products refused here.
            # Another company's product still reads exactly like one that does
            # not exist, which is the answer that matters.
            found = PriceTagRequestService._visible_product_ids(db, wanted, company_id)
            missing = wanted - found
            if missing:
                raise AppException(
                    status_code=422,
                    message="A product on this line's package could not be found.",
                    detail=f"line:{index}",
                    code="INVALID_PART",
                )

        # Onto the LINE's own collection, not just the session. `db.add` alone
        # writes the row at the next flush and leaves `line.parts` unloaded,
        # and the very next reader is `_add_line_tags`, whose whole job is to
        # find this line's open groups. `SessionLocal` is built
        # `autoflush=False` (`app/database.py:23`), so that lazy load SELECTs
        # BEFORE these rows exist and comes back empty: every request made
        # through the running app minted one tag with empty `choices` while
        # every route test passed, because `blank_session()` autoflushes and
        # therefore wrote the parts out ahead of the read. Appending makes the
        # in-memory line authoritative for every reader in this unit of work,
        # whatever the session's flush policy is; the cascade on `parts` still
        # does the insert.
        for position, part in enumerate(cleaned):
            line.parts.append(
                PriceTagRequestLinePart(
                    line_id=line.id,
                    product_id=part["product_id"],
                    role=part["role"],
                    candidates=part["candidates"],
                    sort_order=position,
                )
            )

    @staticmethod
    def _visible_product_ids(db: Session, wanted: set[str], company_id: str | None) -> set[str]:
        """Which of `wanted` this REQUEST's company can see."""
        from app.models.product import Product

        def _query() -> set[str]:
            return {
                pid
                for (pid,) in db.query(Product.id).filter(Product.id.in_(wanted)).all()
            }

        if not company_id:
            return _query()
        with company_scope(db, frozenset({company_id})):
            return _query()

    @staticmethod
    def _visible_product_set_ids(
        db: Session, wanted: set[str], company_id: str | None
    ) -> set[str]:
        """Which of `wanted` sets this REQUEST's company can see.

        `ProductSet` carries `CompanyScopedMixin` directly (unlike
        `ProductCombo`, which is scoped only through its host product), so a
        bare query already attaches the scope predicate.
        """
        from app.models.product_set import ProductSet

        def _query() -> set[str]:
            return {
                sid
                for (sid,) in db.query(ProductSet.id).filter(ProductSet.id.in_(wanted)).all()
            }

        if not company_id:
            return _query()
        with company_scope(db, frozenset({company_id})):
            return _query()

    @staticmethod
    def _part_uuid(value, index: int) -> str | None:
        """A portal-supplied id, or a 422 naming the row it came from.

        JSONB will store any string at all, and `candidates` is read back into
        `Product.id.in_(...)`, where a non-UUID is a Postgres error rather than
        an empty result - so the check has to happen on the way IN.
        """
        if value in (None, ""):
            return None
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            raise AppException(
                status_code=422,
                message="A product on this line's package is not a valid reference.",
                detail=f"line:{index}",
                code="INVALID_PART",
            ) from None

    @staticmethod
    def _set_member_product_ids(db: Session, product_set_id: str) -> list[str]:
        """A set's resolved products, for promotion validation and pricing
        (security H1/S1) - the same "contributes to price" members
        ``product_set_tag_data`` sums for the set's own list/offer total, so
        a line's promotion basis and the tag's own price can never look at a
        different set of products.
        """
        from app.models.product_set import ProductSetMember

        rows = (
            db.query(ProductSetMember.product_id)
            .filter(
                ProductSetMember.product_set_id == product_set_id,
                ProductSetMember.contributes_to_price.is_(True),
            )
            .all()
        )
        return [row.product_id for row in rows if row.product_id]

    @staticmethod
    def _resolve_combo_id(db: Session, line_data: dict, company_id: str | None) -> str | None:
        """The package this line is asked for as, or None.

        Validated rather than trusted, same reason as the part ids: `combo_id`
        arrives from the portal. It must be a real combo, visible to the caller
        (the query is scoped through the host product), and it must belong to
        THIS line's product - a combo id from another cabinet would price and
        print somebody else's package on this one.

        A combo that fails any of those is stored as None rather than refused:
        the S2 guard then says "No package chosen" on the row, which is the
        warn-and-allow rule the whole slice is built on (AC-S2-5).
        """
        from app.models.product import Product
        from app.models.product_combo import ProductCombo

        raw = line_data.get("combo_id")
        if not raw:
            return None
        try:
            combo_id = str(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            return None
        product_id = line_data.get("product_id")
        if not product_id:
            return None
        # Joined to `Product` so the company predicate has something to attach
        # to - `ProductCombo` is scoped only THROUGH its host - and scoped to the
        # request's own company for the same reason the part ids are.
        def _lookup():
            return (
                db.query(ProductCombo)
                .join(Product, Product.id == ProductCombo.host_product_id)
                .filter(
                    ProductCombo.id == combo_id,
                    ProductCombo.host_product_id == product_id,
                )
                .first()
            )

        if company_id:
            with company_scope(db, frozenset({company_id})):
                combo = _lookup()
        else:
            combo = _lookup()
        return combo.id if combo is not None else None

    @staticmethod
    def _raise_on_duplicate_line(db: Session, lines: list[dict]) -> None:
        """The first repeated product or set in ``lines``, named by code, as a
        422 - before ``_add_lines`` inserts a second row the table's own
        unique constraint would otherwise refuse with an unhandled 500."""
        seen_products: set[str] = set()
        seen_sets: set[str] = set()
        for line_data in lines:
            product_id = line_data.get("product_id")
            if product_id:
                if product_id in seen_products:
                    raise PriceTagRequestService._duplicate_line_refusal(
                        db, product_id=product_id
                    )
                seen_products.add(product_id)
            set_id = line_data.get("product_set_id")
            if set_id:
                if set_id in seen_sets:
                    raise PriceTagRequestService._duplicate_line_refusal(
                        db, product_set_id=set_id
                    )
                seen_sets.add(set_id)

    @staticmethod
    def _duplicate_line_refusal(
        db: Session,
        *,
        product_id: str | None = None,
        product_set_id: str | None = None,
    ) -> AppException:
        from app.models.product import Product
        from app.models.product_set import ProductSet

        if product_id:
            row = db.query(Product).filter(Product.id == product_id).first()
            code = row.product_code if row else product_id
        else:
            row = db.query(ProductSet).filter(ProductSet.id == product_set_id).first()
            code = row.set_code if row else product_set_id
        return AppException(
            status_code=422,
            message=f"{code} appears twice; merge the quantities.",
            detail="lines",
            code="DUPLICATE_LINE",
        )

    @staticmethod
    def replace_lines(
        db: Session, request: PriceTagRequest, lines: list[dict], viewer=None
    ) -> None:
        """Swap a draft's lines for the ones the form just posted.

        A re-save sends the whole table, not a diff: the rows are unsaved form
        state on the client and carry no stable identity there. Deleting through
        the relationship keeps ``delete-orphan`` in charge, so nothing is left
        pointing at the request.

Marketing's own work is not part of the form's payload, so it is captured
        from the OLD rows before they are cleared and carried onto whichever new
        row keeps the same product or set. Since S3 that work lives on the TAGS
        (D3), so what is carried is the tag set itself - its split, its choices,
        its quantities and its overrides - rather than one override per line: a
        salesperson fixing a typo in a remark must not un-split a line marketing
        already turned into four tags.
        """
        carry_tags = {
            (old.product_id, old.product_set_id): [
                {
                    # AC-S8-3: a re-save with unchanged parts keeps the SAME
                    # tag rows, id included - the id is what lets the split
                    # survive the delete-and-reinsert `lines.clear()` below
                    # does, so the saved tag sheet document's own
                    # placements (keyed on it) keep pointing at the right tag.
                    "id": tag.id,
                    "sort_order": tag.sort_order,
                    "quantity": tag.quantity,
                    "choices": dict(tag.choices or {}),
                    "marketing_price_override": tag.marketing_price_override,
                    "marketing_override_reason": tag.marketing_override_reason,
                    # AC-S6-10: "Not printed" survives a revise on a tag whose
                    # choice set is unchanged, same as quantity and overrides.
                    "print_excluded": bool(tag.print_excluded),
                }
                for tag in sorted(
                    old.tags or [], key=lambda t: (t.sort_order or 0, t.id)
                )
            ]
            for old in request.lines
        }
        request.lines.clear()
        db.flush()
        PriceTagRequestService._add_lines(
            db, request, lines, carry_tags=carry_tags, viewer=viewer
        )
        db.flush()
        # `_add_lines` inserts the new rows via `db.add(...)`, not
        # `request.lines.append(...)`, so the in-memory collection is left
        # holding the CLEARED (empty) state even though the DB now has the
        # new rows - a caller reading `request.lines` right after this (e.g.
        # a post-submit PUT's `validate_submittable`, review round 2) saw
        # zero lines regardless of what was just saved. Expiring forces the
        # next access to re-query.
        db.expire(request, ["lines"])

        # A line added to a design already in progress has its tags pinned on
        # save (D16), so they enter the gate the same way the others did.
        if request.status == STATUS_DESIGNING:
            from app.services.dealer_kit import tag_data_service

            tag_data_service.pin_tags(db, request, only_unpinned=True)
            db.flush()

    @staticmethod
    def set_line_price(
        db: Session,
        request: PriceTagRequest,
        line: PriceTagRequestLine,
        data: dict,
        *,
        viewer=None,
    ) -> None:
        """D5/AC-S11-1: the office changing ONE line's own promotion or
        manual price on a request already sent in.

        ``data`` is ``payload.model_dump(exclude_unset=True)`` from
        ``PriceTagRequestLinePricePatch`` - only a key the caller actually
        sent moves the line; an omitted one keeps its current value. D2's
        "picking a promotion clears manual" is symmetric here: whichever
        field THIS patch set to a real value wins and clears the other, so a
        `manual_sell_price` patch clears a standing promotion just as a
        `promotion_id` patch clears a standing manual figure.

        Validates the same AC-S6-4 (mutually exclusive, manual only in
        Selling mode) and AC-S6-5 (promotion must cover the line) rules the
        create/update path runs, through the SAME ``line_pricing`` engine, so
        the CRM route and the portal form can never disagree about what is
        allowed. Clears the line's tags' pins (AC-S11-1) so the existing
        data-change banner carries the change into an already-pinned design
        (r9 D16).
        """
        from app.services.dealer_kit.pricing import line_pricing
        from app.services.dealer_kit.tag_data_service import staff_viewer

        viewer = viewer or staff_viewer()

        promotion_id = (
            data["promotion_id"] if "promotion_id" in data else line.promotion_id
        )
        manual_sell_price = (
            data["manual_sell_price"]
            if "manual_sell_price" in data
            else line.manual_sell_price
        )
        if "promotion_id" in data and data["promotion_id"] is not None:
            manual_sell_price = None
        if "manual_sell_price" in data and data["manual_sell_price"] is not None:
            promotion_id = None

        PriceTagRequestService._validate_line_price_basis(
            promotion_id, manual_sell_price, request.price_mode
        )

        basis = "list"
        if line.product_id:
            resolved_part_ids = [p.product_id for p in line.parts if p.product_id]
            candidate_ids = [
                candidate
                for part in line.parts
                if not part.product_id
                for candidate in (part.candidates or [])
            ]
            pricing_row = line_pricing(
                db,
                lines=[
                    {
                        "key": "_p",
                        "product_id": line.product_id,
                        "part_product_ids": resolved_part_ids,
                        "candidate_product_ids": candidate_ids,
                        "promotion_id": promotion_id,
                        "manual_sell_price": manual_sell_price,
                    }
                ],
                viewer=viewer,
                # A SAVED line's basis is what the salesperson committed
                # to, never the lookup's auto pick (owner ruling, review
                # round 2): with no stored promotion the line is `list`,
                # however many promotions cover its product.
                auto_pick=False,
            )[0]
            if promotion_id and promotion_id not in {
                option["id"] for option in pricing_row["promotion_options"]
            }:
                raise AppException(
                    status_code=422,
                    message="This promotion does not apply to this line.",
                    code="PROMOTION_NOT_AVAILABLE",
                )
            basis = pricing_row["sell_price_basis"]
        elif line.product_set_id:
            # Security review finding (H1/S1), same gap as `_add_lines`: a
            # `product_set` line PATCHed through here skipped promotion
            # validation and pricing entirely.
            member_ids = PriceTagRequestService._set_member_product_ids(
                db, line.product_set_id
            )
            pricing_row = line_pricing(
                db,
                lines=[
                    {
                        "key": "_p",
                        "product_id": None,
                        "part_product_ids": member_ids,
                        "candidate_product_ids": [],
                        "promotion_id": promotion_id,
                        "manual_sell_price": manual_sell_price,
                    }
                ],
                viewer=viewer,
                # A SAVED line's basis is what the salesperson committed
                # to, never the lookup's auto pick (owner ruling, review
                # round 2): with no stored promotion the line is `list`,
                # however many promotions cover its product.
                auto_pick=False,
            )[0]
            if promotion_id and promotion_id not in {
                option["id"] for option in pricing_row["promotion_options"]
            }:
                raise AppException(
                    status_code=422,
                    message="This promotion does not apply to this line.",
                    code="PROMOTION_NOT_AVAILABLE",
                )
            basis = pricing_row["sell_price_basis"]

        line.promotion_id = promotion_id
        line.manual_sell_price = manual_sell_price
        line.show_promo_price = request.price_mode == "selling" and basis != "list"

        for tag in line.tags:
            tag.pinned_tag_data = None
            tag.pinned_at = None
            tag.data_change_ack_hash = None

    @staticmethod
    def submit_request(
        db: Session,
        contact_id: str,
        company_id: str,
        data: dict,
    ) -> PriceTagRequest:
        """Create a price tag request and stamp its package warnings (D2).

        Submit is NEVER refused for a package reason (AC-S2-5, AC-S2-7). The set
        guard that used to 422 an ala-carte Bathroom Furniture line is retired;
        a guarded product that arrives with no package, or with parts taken off,
        carries a `package_warning` marketing reads instead. `DUPLICATE_LINE` and
        the completeness rules are untouched.

        The warnings are stamped AFTER the lines exist, not from the payload:
        the rule reads what was actually stored, so a revision and a submit
        cannot answer differently for the same request.
        """
        request = PriceTagRequestService.create_request(
            db, contact_id, company_id, data
        )
        # Clear the draft timestamp to indicate submission.
        request.portal_draft_at = None
        db.flush()
        PriceTagRequestService.apply_package_warnings(db, request)
        db.flush()
        return request

    @staticmethod
    def auto_assign_from_tracker(db: Session, request: PriceTagRequest) -> str | None:
        """D8: copy the form SLA tracker's resolved assignee onto the request.

        Reads the newest OPEN ``price_tag_request`` tracker for this request -
        the one ``emit_form_event`` just opened, if an active config placed it
        (``_start_for_config`` commits as part of opening it, so this read
        always sees it: no explicit flush needed). No tracker, or a tracker
        with no assignee, leaves the request ``new`` and unclaimed - the Claim
        path is unchanged (AC-S3-2). Returns the assignee id, or ``None``.

        The write (status, assignee, the tag_sheet page) runs inside its own
        SAVEPOINT: ``assigned_to_id`` is set only after ``transition_status``
        has actually succeeded, and a failure anywhere in the block rolls the
        savepoint back rather than leaving a half-write (assigned but still
        ``new``, or vice versa) for the caller's own ``db.commit()`` to
        persist. Called from ``portal_submit_price_tag_request`` in its OWN
        try/except: a failure here must not fail the submit (AC-S3-3).
        """
        from app.models.sla import ConversationSLATracking
        from app.services.sla_scope import open_tracker_scope

        tracker = (
            db.query(ConversationSLATracking)
            .filter(
                ConversationSLATracking.source_entity_type == "price_tag_request",
                ConversationSLATracking.source_entity_id == str(request.id),
                *open_tracker_scope(),
            )
            .order_by(ConversationSLATracking.initiated_at.desc())
            .first()
        )
        if not tracker or not tracker.assigned_to_id:
            return None

        savepoint = db.begin_nested()
        try:
            PriceTagRequestService.transition_status(
                db, str(request.id), STATUS_DESIGNING, user_id=tracker.assigned_to_id,
            )
            request.assigned_to_id = tracker.assigned_to_id
            # B1: a request auto-assigned straight into `designing` needs the
            # same tag_sheet page Claim creates - without this GET
            # .../design 404s NO_PAGE, and auto-assign has already claimed
            # it, so there is no Claim button left to fix it from.
            PriceTagRequestService.ensure_tag_sheet_page(
                db, request, tracker.assigned_to_id,
            )
            db.flush()
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            raise
        return tracker.assigned_to_id

    @staticmethod
    def ensure_tag_sheet_page(
        db: Session, request: PriceTagRequest, user_id: str | None,
    ) -> None:
        """The tag_sheet ``Page`` a request needs before design can happen.

        Idempotent: a no-op once ``request.page_id`` is set. Extracted from
        the CRM claim route (``claim_price_tag_request``) so BOTH claim and
        ``auto_assign_from_tracker`` (D8) create it the same way - a request
        that lands in ``designing`` with no page 404s NO_PAGE on
        ``GET .../design`` (B1).
        """
        if request.page_id:
            return
        from app.models.dealer_kit import Page

        page = Page(
            name=f"Tags - {request.doc_number}",
            slug=f"tag-sheet-{request.doc_number.lower()}",
            kind="tag_sheet",
            request_id=request.id,
            company_id=request.company_id,
            created_by=user_id,
        )
        db.add(page)
        db.flush()
        request.page_id = page.id

    @staticmethod
    def transition_status(
        db: Session,
        request_id: str,
        new_status: str,
        user_id: str | None = None,
        notify_ctx: dict | None = None,
    ) -> PriceTagRequest:
        """Validate and apply a status transition.

        Raises ``AppException`` (409) for invalid transitions.

        ``notify_ctx`` carries whatever the message needs that the row cannot
        say on its own - how many change requests were sent, the rejection
        reason, the round for the assignee's bell.
        """
        request = db.query(PriceTagRequest).filter(
            PriceTagRequest.id == request_id,
        ).first()
        if not request:
            raise AppException(
                status_code=404,
                message=f"Price tag request {request_id} not found.",
                code="NOT_FOUND",
            )

        current = request.status
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise AppException(
                status_code=409,
                message=(
                    f"Cannot transition from '{current}' to '{new_status}'. "
                    f"Allowed: {sorted(allowed) if allowed else 'none (terminal)'}."
                ),
                code="INVALID_TRANSITION",
            )
        if (
            new_status == STATUS_READY_FOR_COLLECTION
            and request.print_by != PRINT_BY_OFFICE
        ):
            # The graph cannot see `print_by`, and this edge exists only for an
            # office print: a salesperson printing their own tags has nothing to
            # collect, and a request nobody has answered the question for has
            # nothing to promise (D7/D8).
            raise AppException(
                status_code=409,
                message=(
                    "Only an office print reaches collection. Set Printing to "
                    "Office prints first."
                ),
                code="INVALID_TRANSITION",
            )

        request.status = new_status
        if new_status == STATUS_DESIGNING:
            # r9 D16: the tags are drawn from what master data said when the
            # design started, so that is the moment it is frozen. Only tags
            # with no pin yet - re-pinning on the way back from
            # `changes_requested` would swallow the very difference the gate
            # exists to show.
            from app.services.dealer_kit import tag_data_service

            tag_data_service.pin_tags(db, request, only_unpinned=True)
        if new_status == STATUS_PROOF_READY:
            # The review round is COUNTED here, not derived from the version
            # history (D4/R1): the "Marked proof ready" snapshot is only
            # written when a draft exists, and the designer's own CTA saves
            # first, so the snapshot was usually skipped and every round came
            # back as 1 - which deduplicated the assignee's bell away from the
            # second round onward.
            request.review_round = (request.review_round or 0) + 1
        # The hand-over's own timestamps (D9). `collected_by_*` is whoever did
        # it: a user here, a contact on the portal's own route, neither when the
        # sweep closes it.
        if new_status == STATUS_READY_FOR_COLLECTION:
            request.ready_for_collection_at = datetime.utcnow()
        elif new_status == STATUS_COLLECTED:
            request.collected_at = datetime.utcnow()
            request.collected_by_user_id = user_id
            request.collected_auto = False
        db.flush()

        # D12: an approve auto-queues one tag-sheet export, so the salesperson
        # never has to ask marketing to run it separately. Function-local
        # module import (not a from-import) - tag_sheet_export_service
        # imports STATUS_APPROVED/STATUS_READY from THIS module at ITS own
        # top level, so a top-level import here would be circular, and tests
        # monkeypatch this module's `request_tag_sheet_export` attribute,
        # which only a call through the module (not a bound name) picks up.
        # Own try/except: a failure here must never turn a successful
        # approve into a failed one (AC-S5-2) - logged, nothing raised.
        if new_status == STATUS_APPROVED:
            try:
                from app.services.dealer_kit import tag_sheet_export_service

                tag_sheet_export_service.request_tag_sheet_export(
                    db, request_id=request_id, user_id=user_id, sheet_ids=None,
                )
            except Exception:
                logger.warning(
                    "Auto-export failed for price_tag_request %s",
                    request_id,
                    exc_info=True,
                )

        # A message is a promise about the database, so the promise is made
        # only once the database has kept it (S6): a notifier that fires before
        # the commit can report a transition a later rollback throws away. The
        # caller's own `db.commit()` afterwards is then a no-op.
        db.commit()

        # D12/D13: EVERY transition reaches the salesperson, including the
        # confirmations of their own actions - a message that says "you
        # approved it" is how somebody knows the button worked. Through the
        # MODULE, so a test can swap the function; wrapped inside the notifier
        # itself, so a messaging outage can never undo the transition.
        from app.services import price_tag_notify

        ctx = dict(notify_ctx or {})
        if new_status == STATUS_DESIGNING and not ctx.get("assignee"):
            # R3: "is being designed by Aisyah", not "by the marketing team".
            # Resolved here rather than at each caller because the two paths
            # set the assignee at different moments - Claim writes it before
            # the transition, the tracker's auto-assign after it - and both
            # hand this the same user id.
            ctx["assignee"] = PriceTagRequestService.user_display_name(
                db, request.assigned_to_id or user_id
            )
        try:
            price_tag_notify.notify_salesperson(db, request, new_status, **ctx)
            price_tag_notify.ring_assignee(
                db,
                request,
                new_status,
                round_no=ctx.get("round") or request.review_round or 1,
            )
        except Exception:
            # The notifier guards itself too; this is the belt for a caller
            # that replaced it. A transition that happened has happened.
            logger.warning(
                "Notification failed for price_tag_request %s",
                request_id,
                exc_info=True,
            )

        return request

    @staticmethod
    def user_display_name(db: Session, user_id: Optional[str]) -> Optional[str]:
        """A staffer as a person reads them: their name, else their email."""
        if not user_id:
            return None
        from app.models.user import User

        user = db.query(User).filter(User.id == user_id).first()
        return (user.name or user.email) if user else None

    @staticmethod
    def collected_by_name(db: Session, request: PriceTagRequest) -> Optional[str]:
        """Who took the tags, as a person reads it (D9/S3).

        The staffer who ticked it off, the salesperson who confirmed on the
        portal, or nobody at all when the sweep closed it - which is a real
        answer, not a missing one, and the card says so in its own words.
        """
        if request.collected_auto:
            return None
        if request.collected_by_user_id:
            return PriceTagRequestService.user_display_name(
                db, request.collected_by_user_id
            )
        if request.collected_by_contact_id:
            from app.models.access import RespondContact

            contact = (
                db.query(RespondContact)
                .filter(RespondContact.id == request.collected_by_contact_id)
                .first()
            )
            return (contact.name or contact.phone_number) if contact else None
        return None

    @staticmethod
    def is_terminal(request: PriceTagRequest) -> bool:
        """Nothing left to do to this request (D8).

        Request-aware, not status-aware: `approved` is the end of the line for
        a salesperson who prints their own tags and the middle of it for an
        office print, so the same status answers differently depending on the
        one column.
        """
        if request.status in _TERMINAL:
            return True
        return (
            request.status == STATUS_APPROVED and request.print_by == PRINT_BY_SELF
        )

    @staticmethod
    def run_auto_collect(db: Session) -> int:
        """Close a hand-over nobody came back for (D11).

        Reads the configured days off the settings row: 0 turns the sweep off
        entirely, which is a legitimate way to run a counter where somebody
        always ticks it by hand. Only `ready_for_collection` rows are in scope -
        an approved request has not been printed, so there is nothing on the
        counter to go stale.

        Returns how many it closed, which is what the scheduler logs.
        """
        from app.models.user import SystemSetting

        settings_row = db.query(SystemSetting).first()
        days = getattr(settings_row, "price_tag_auto_collect_days", 0) or 0
        if days <= 0:
            return 0

        cutoff = datetime.utcnow() - timedelta(days=days)
        stale = (
            db.query(PriceTagRequest)
            .filter(
                PriceTagRequest.status == STATUS_READY_FOR_COLLECTION,
                PriceTagRequest.ready_for_collection_at.isnot(None),
                PriceTagRequest.ready_for_collection_at < cutoff,
            )
            .all()
        )
        for request in stale:
            request.status = STATUS_COLLECTED
            request.collected_at = datetime.utcnow()
            request.collected_auto = True
            # Nobody did this, so nobody is recorded as having done it.
            request.collected_by_user_id = None
            request.collected_by_contact_id = None
        if not stale:
            return 0

        db.flush()
        db.commit()

        # The one transition nobody is present for is the one the salesperson
        # most needs told: their tags were closed overnight (B3). Same copy
        # table, with the context that makes its auto line read correctly.
        from app.services import price_tag_notify

        for request in stale:
            try:
                price_tag_notify.notify_salesperson(
                    db, request, STATUS_COLLECTED, auto=True, days=days
                )
            except Exception:
                logger.warning(
                    "Auto-collect notification failed for %s", request.id, exc_info=True
                )
        return len(stale)

    @staticmethod
    def validate_submittable(
        request: PriceTagRequest, *, require_debtor: bool = True
    ) -> None:
        """What a request needs before it may be submitted (D48a).

        A draft can be sloppy; a submitted request cannot. Every missing field is
        named at once, in ``detail`` as a comma-separated list of keys the form
        knows how to place, so the portal can put each message under the field it
        belongs to instead of showing one sentence in a toast.

        A line with neither a product nor a set cannot exist here: the table's
        ``ck_price_tag_request_lines_one_ref`` refuses it on insert. The form
        catches that one on the client, where the empty row actually is.

        ``require_debtor=False`` (review round 2): a post-submit PUT reuses this
        for its own completeness bar (AC-B10), but that bar is lines-only - the
        debtor was already locked in at the ORIGINAL submit and this PUT does
        not touch it, so re-checking it here would refuse an edit over a field
        the edit never asked about.
        """
        # Who prints is REQUIRED, and refused on its own rather than folded
        # into the list below: it has its own code because the portal form
        # names the gap under the control, and the answer decides whether the
        # request ends at `approved` or waits for a collection (D7).
        if request.print_by not in PRINT_BY_CHOICES:
            raise AppException(
                status_code=422,
                message="Say who prints these tags before submitting.",
                detail="print_by",
                code="PRINT_BY_REQUIRED",
            )

        # D-P2b: need by is optional - dropped from what "complete" requires.
        missing: list[tuple[str, str]] = []
        if require_debtor and not (request.debtor_name or "").strip():
            missing.append(("debtor_name", "a dealer"))
        if not request.lines:
            missing.append(("lines", "at least one line"))
        if missing:
            labels = [label for _, label in missing]
            wanted = (
                labels[0]
                if len(labels) == 1
                else ", ".join(labels[:-1]) + " and " + labels[-1]
            )
            raise AppException(
                status_code=422,
                message=f"This request needs {wanted} before it can be submitted.",
                detail=",".join(key for key, _ in missing),
                code="SUBMIT_INCOMPLETE",
            )

        # D-P2 (owner ruling): Selling with no promotion is a valid end state
        # now - the PRICE_MODE_NEEDS_PROMOTION guard is retired.

    @staticmethod
    def notify_submitted(db: Session, request: PriceTagRequest) -> None:
        """Tell the salesperson their request landed (D12's first line, S9).

        Submit is not a status transition - a submitted request keeps `new`
        until marketing claims it - so it is the one moment the transition
        notifier cannot cover, and the moment somebody most wants to hear that
        the form worked.
        """
        from app.services import price_tag_notify

        try:
            price_tag_notify.notify_salesperson(db, request, "submitted")
        except Exception:
            logger.warning(
                "Submit notification failed for price_tag_request %s",
                request.id,
                exc_info=True,
            )

    @staticmethod
    def validate_claimable(request: PriceTagRequest) -> None:
        """What a request needs before marketing may claim it.

        A draft is refused FIRST and by its own code: it is still the
        salesperson's, and the status it carries (``new``) is the same one a
        submitted request carries, so the status check alone waved it through.
        Claiming one moved it to ``designing``, and the salesperson's later
        Submit reset it to ``new`` - the claim gone, the SLA fired twice, and a
        designer working on something nobody had sent them.
        """
        if request.portal_draft_at is not None:
            raise AppException(
                status_code=409,
                message=(
                    "This request is still a draft on the salesperson's side and "
                    "has not been submitted yet."
                ),
                code="NOT_SUBMITTED",
            )
        if request.status != STATUS_NEW:
            raise AppException(
                status_code=409,
                message="Only requests in 'new' status can be claimed.",
                code="INVALID_STATE",
            )

    @staticmethod
    def validate_designable(request: PriceTagRequest) -> None:
        """Mirrors the FE's ``priceTagActions`` design/"view design" predicate.

        The CRM Lines tab and the request header both compute
        ``actions.some(spec => spec.action === 'design')`` off
        ``priceTagActions(status, assigned_to_id)`` to decide whether the
        designer route is legal right now; this is that same rule, so the
        endpoint that actually writes a design doc cannot be reached from a
        status the UI never offers it from. ``designing`` / ``changes_requested``
        / ``proof_ready`` are legal outright; ``new`` is legal only once
        claimed (``assigned_to_id`` set) - the FE checks ``!assignedToId``
        truthiness only, never "claimed by ME", so this does the same and
        does not additionally require ``assigned_to_id == the caller``.
        """
        legal_claimed_new = request.status == STATUS_NEW and request.assigned_to_id
        legal_status = request.status in (
            STATUS_DESIGNING,
            STATUS_CHANGES_REQUESTED,
            STATUS_PROOF_READY,
        )
        if not (legal_claimed_new or legal_status):
            raise AppException(
                status_code=409,
                message=(
                    "This request's tags cannot be designed in its current "
                    "status."
                ),
                code="INVALID_STATE",
            )

    @staticmethod
    def _fill_line_parts(db: Session, request: PriceTagRequest, response) -> None:
        """Resolve every part row on every line to codes and names (D2).

        One query for every product any part mentions - the resolved rows AND
        the candidates of the open ones - rather than one per row: a request
        with four packaged lines carries twenty part products, and the portal
        read view is not the place to spend twenty round trips.
        """
        from app.models.product import Product
        from app.schemas.price_tag import (
            LinePartCandidateResponse,
            PriceTagRequestLinePartResponse,
        )

        parts_by_line: dict[str, list] = {}
        wanted: set[str] = set()
        for line in request.lines:
            rows = sorted(line.parts or [], key=lambda p: (p.sort_order or 0, p.id))
            parts_by_line[line.id] = rows
            for row in rows:
                if row.product_id:
                    wanted.add(row.product_id)
                for candidate in row.candidates or []:
                    wanted.add(str(candidate))
        if not wanted:
            return

        products = {
            product.id: product
            for product in db.query(Product).filter(Product.id.in_(wanted)).all()
        }

        def _code(product_id):
            product = products.get(product_id)
            return product.product_code if product else None

        def _name(product_id):
            product = products.get(product_id)
            return product.product_name if product else None

        for line in response.lines:
            rows = parts_by_line.get(line.id) or []
            line.parts = [
                PriceTagRequestLinePartResponse(
                    id=row.id,
                    product_id=row.product_id,
                    code=_code(row.product_id),
                    name=_name(row.product_id),
                    role=row.role,
                    candidates=[
                        LinePartCandidateResponse(
                            product_id=str(candidate),
                            code=_code(str(candidate)) or "",
                            name=_name(str(candidate)) or "",
                        )
                        for candidate in (row.candidates or [])
                    ],
                    sort_order=row.sort_order or 0,
                )
                for row in rows
            ]

    # ------------------------------------------------------------------- tags

    @staticmethod
    def tag_body(tag, resolved: dict | None) -> dict:
        """One tag in the shape every surface reads (D3).

        `choices_display` resolves the stored `{role: product_id}` map to codes -
        the raw map is never rendered (AC-X-2) - and the prices come off the ONE
        resolver, so the rail, the Lines tab and the PDF cannot disagree.
        """
        chosen = dict(tag.choices or {})
        by_id = {}
        for group in (resolved or {}).get("open_groups") or []:
            for candidate in group.get("candidates") or []:
                by_id[candidate["product_id"]] = candidate["code"]
        # A resolved choice is no longer an open group, so its code comes off the
        # tag's own resolved parts, matched by id.
        for part in (resolved or {}).get("parts") or []:
            if part.get("product_id"):
                by_id.setdefault(str(part["product_id"]), part.get("code", ""))
        return {
            "id": tag.id,
            "line_id": tag.line_id,
            "sort_order": tag.sort_order or 0,
            "label": (resolved or {}).get("tag_label", ""),
            "quantity": tag.quantity,
            "choices": chosen,
            "choices_display": [
                {"role": role, "code": by_id.get(str(product_id), "")}
                for role, product_id in chosen.items()
            ],
            "open_groups": (resolved or {}).get("open_groups") or [],
            "marketing_price_override": (
                None
                if tag.marketing_price_override is None
                else float(tag.marketing_price_override)
            ),
            "marketing_override_reason": tag.marketing_override_reason,
            "list_price": (resolved or {}).get("list_price"),
            "sell_price": (resolved or {}).get("sell_price"),
            "print_excluded": bool(tag.print_excluded),
            # r10 S8: set when the read seam auto-applied a product-data
            # change; cleared by Dismiss.
            "data_updated_at": tag.data_updated_at,
            "data_update_changes": tag.data_update_changes,
            "data_update_version": tag.data_update_version,
        }

    @staticmethod
    def delete_tag(db: Session, tag) -> None:
        """Remove one tag and its placements. Never the line's last (AC-S3-6)."""
        line = tag.line
        if len(line.tags or []) <= 1:
            raise AppException(
                status_code=422,
                message="A line must keep at least one tag.",
                code="LAST_TAG",
            )
        request_id, tag_id = line.request_id, tag.id
        db.delete(tag)
        db.flush()
        PriceTagRequestService._drop_placements_in_draft(db, request_id, tag_id)
        db.flush()

    @staticmethod
    def _tag_sheet_page(db: Session, request_id: str):
        from app.models.dealer_kit import Page

        return (
            db.query(Page)
            .filter(Page.request_id == request_id, Page.kind == "tag_sheet")
            .first()
        )

    @staticmethod
    def _drop_placements_in_draft(db: Session, request_id: str, tag_id: str) -> None:
        """A deleted tag leaves no placement behind (AC-S3-6)."""
        page = PriceTagRequestService._tag_sheet_page(db, request_id)
        if page is None or not page.draft_doc:
            return
        doc = copy.deepcopy(page.draft_doc)
        changed = False
        for sheet in doc.get("sheets") or []:
            kept = [p for p in (sheet.get("tags") or []) if p.get("request_tag_id") != tag_id]
            if len(kept) != len(sheet.get("tags") or []):
                changed = True
            sheet["tags"] = kept
        if changed:
            page.draft_doc = doc
            flag_modified(page, "draft_doc")

    @staticmethod
    def guarded_classes(db: Session) -> set[str]:
        """The product classes a missing package is worth warning about (D2).

        Read from `system_settings`, never a literal, or the settings control is
        decorative and the tenant that packages shower trays has no way to say
        so. No settings row at all (a fresh install, most tests) falls back to
        the column's own default.
        """
        from app.models.user import SystemSetting

        row = db.query(SystemSetting).first()
        if row is None:
            return set(_DEFAULT_GUARDED_CLASSES)
        configured = getattr(row, "price_tag_guarded_classes", None)
        if configured is None:
            return set(_DEFAULT_GUARDED_CLASSES)
        # An empty list is a legitimate answer - warn about nothing.
        return {str(label) for label in configured}

    @staticmethod
    def apply_package_warnings(db: Session, request: PriceTagRequest) -> None:
        """Stamp `package_warning` on every line of `request` (D2, AC-S2-5).

        Replaces `validate_set_guard`, which raised 422. Runs wherever that one
        ran - `submit_request` and the portal revision path - so a salesperson
        can never submit a bare cabinet and then be blocked from correcting it.

        A product_set line is skipped entirely: parts are a combo fact on a
        PRODUCT line, and nothing about a set is a package question.
        """
        guarded = PriceTagRequestService.guarded_classes(db)
        for line in request.lines:
            line.package_warning = PriceTagRequestService._package_warning_for(
                db, line, guarded
            )

    @staticmethod
    def _package_warning_for(db: Session, line, guarded: set[str]) -> str | None:
        """The D2 rule, in the order it reads on the plan.

        `None` for anything that is not a guarded product line, then the two
        "no package" cases, then whatever the chosen combo asks for that no part
        row answers. A choice group left OPEN is an ANSWER, not an omission: the
        whole point of the open row is that the salesperson may not know which
        basin, and marketing splits it into one tag per candidate later (S3).
        """
        from app.models.product import Product, ProductCategory
        from app.models.product_combo import ProductCombo, ProductComboPart

        if line.line_type != "product" or not line.product_id:
            return None

        product = db.query(Product).filter(Product.id == line.product_id).first()
        if product is None:
            return None
        category = (
            db.query(ProductCategory)
            .filter(ProductCategory.id == product.category_id)
            .first()
        )
        if category is None or category.class_label not in guarded:
            return None

        if not line.combo_id:
            has_combos = (
                db.query(ProductCombo)
                .filter(ProductCombo.host_product_id == line.product_id)
                .first()
                is not None
            )
            return "No package chosen" if has_combos else "No package defined"

        combo_parts = (
            db.query(ProductComboPart)
            .filter(ProductComboPart.combo_id == line.combo_id)
            .order_by(ProductComboPart.sort_order)
            .all()
        )
        if not combo_parts:
            return None

        rows = list(line.parts or [])
        answered_products = {row.product_id for row in rows if row.product_id}
        answered_roles = {row.role for row in rows if row.role}

        missing: list[str] = []
        seen_groups: list[str] = []
        for part in combo_parts:
            if part.choice_group:
                if part.choice_group not in seen_groups:
                    seen_groups.append(part.choice_group)
                continue
            if part.part_product_id not in answered_products:
                # Never the raw id as a fallback: this text is rendered on the
                # portal row and in the CRM Lines tab, and no UUID reaches a
                # screen (AC-X-2). A part whose product will not resolve is
                # named by nothing rather than by its id.
                code = (
                    part.part_product.product_code
                    if part.part_product is not None
                    else ""
                )
                if code:
                    missing.append(code)
        for group in seen_groups:
            # Neither a resolved nor an open row answers this group. A row
            # RESOLVED from the group keeps its `role`, which is why one check
            # covers both shapes.
            if group not in answered_roles:
                missing.append(group)

        return f"Missing: {', '.join(missing)}" if missing else None

    @staticmethod
    def get_request(db: Session, request_id: str) -> PriceTagRequest | None:
        """Fetch a single request by id, eagerly loading lines."""
        return (
            db.query(PriceTagRequest)
            .filter(PriceTagRequest.id == request_id)
            .first()
        )

    @staticmethod
    def resolved_labels(
        db: Session, request_ids: list[str]
    ) -> dict[str, dict]:
        """The names and the line count behind a request's ids, per request id.

        A request row carries a contact id and a user id, and no screen may
        show a UUID. Two set-based queries answer for the whole page: asking
        per row would be four queries per row on a fifty-row listing. D1
        (S6): a promotion is a LINE fact now - there is no single header
        promotion left to name here.
        """
        if not request_ids:
            return {}

        from app.models.access import RespondContact
        from app.models.user import User

        counts = dict(
            db.query(
                PriceTagRequestLine.request_id,
                func.count(PriceTagRequestLine.id),
            )
            .filter(PriceTagRequestLine.request_id.in_(request_ids))
            .group_by(PriceTagRequestLine.request_id)
            .all()
        )

        labels: dict[str, dict] = {}
        rows = (
            db.query(
                PriceTagRequest.id,
                RespondContact.name,
                User.name,
            )
            .select_from(PriceTagRequest)
            .outerjoin(RespondContact, RespondContact.id == PriceTagRequest.contact_id)
            .outerjoin(User, User.id == PriceTagRequest.assigned_to_id)
            .filter(PriceTagRequest.id.in_(request_ids))
            .all()
        )
        for request_id, contact_name, assigned_to_name in rows:
            labels[request_id] = {
                "contact_name": contact_name,
                "assigned_to_name": assigned_to_name,
                "line_count": int(counts.get(request_id, 0)),
            }
        return labels

    @staticmethod
    def touched_request_ids(
        db: Session, requests: list[PriceTagRequest]
    ) -> set[str]:
        """AC-D3: which of ``requests`` a cheap query says were touched since
        their own ``data_checked_at`` (PLAN price-tag-currency-token-extract-
        prompt.md section D).

        ``data_checked_at IS NULL`` (never checked) is touched; a terminal
        request is never touched - nothing on it can be updated, so the
        list route has no reason to pay for its resolve. One grouped SQL over
        every non-terminal candidate, not one query per row: ``max(...)``
        across every table a printed tag's data comes from - the line's own
        product, a part's own product (``diff_pin_against_live`` diffs parts
        too), a set line's members, a set's own name, a product's spec row,
        the line's promotion, and a product attachment - compared against the
        column.

        A deleted image link leaves no timestamp (named gap, plan section D):
        that change surfaces on the next open of the record, not in the list.

        Code review 16 Sep: the spec and promotion arms compare
        ``GREATEST(updated_at, created_at)``, not ``updated_at`` alone -
        ``write_spec_row`` never sets ``product_specifications.updated_at`` on
        insert (the plan named this gap), so a freshly-derived spec row reads
        NULL there and a real change would compare as "never moved".

        Contract: ``ids`` MUST already be company-scoped by the caller (ORM
        rows); this raw SQL adds no company predicate.
        """
        candidates = [
            request
            for request in requests
            if not PriceTagRequestService.is_terminal(request)
        ]
        if not candidates:
            return set()

        from sqlalchemy import bindparam, text

        ids = [str(request.id) for request in candidates]
        checked_at = {str(request.id): request.data_checked_at for request in candidates}

        sql = text(
            """
            WITH line_products AS (
                SELECT l.request_id AS request_id, l.product_id AS product_id,
                       l.promotion_id AS promotion_id
                FROM price_tag_request_lines l
                WHERE l.request_id IN :ids AND l.product_id IS NOT NULL
                UNION ALL
                SELECT l.request_id AS request_id, m.product_id AS product_id,
                       l.promotion_id AS promotion_id
                FROM price_tag_request_lines l
                JOIN product_set_members m ON m.product_set_id = l.product_set_id
                WHERE l.request_id IN :ids AND l.product_set_id IS NOT NULL
            ),
            moved AS (
                SELECT request_id, MAX(moved_at) AS moved_at FROM (
                    SELECT lp.request_id, p.updated_at AS moved_at
                    FROM line_products lp
                    JOIN products p ON p.id = lp.product_id
                    UNION ALL
                    -- Code review 16 Sep: a part's OWN product, resolved or
                    -- picked - `diff_pin_against_live` diffs parts too, and a
                    -- part's product is not always the line's own.
                    SELECT l.request_id, pp.updated_at
                    FROM price_tag_request_lines l
                    JOIN price_tag_request_line_parts part ON part.line_id = l.id
                    JOIN products pp ON pp.id = part.product_id
                    WHERE l.request_id IN :ids AND part.product_id IS NOT NULL
                    UNION ALL
                    SELECT lp.request_id, GREATEST(ps.updated_at, ps.created_at)
                    FROM line_products lp
                    JOIN product_specifications ps ON ps.product_id = lp.product_id
                    UNION ALL
                    SELECT lp.request_id, GREATEST(pr.updated_at, pr.created_at)
                    FROM line_products lp
                    JOIN promotions pr ON pr.id = lp.promotion_id
                    UNION ALL
                    SELECT l.request_id, m.updated_at
                    FROM price_tag_request_lines l
                    JOIN product_set_members m ON m.product_set_id = l.product_set_id
                    WHERE l.request_id IN :ids AND l.product_set_id IS NOT NULL
                    UNION ALL
                    -- Code review 16 Sep: the SET's own name/code (a rename)
                    -- changes what `resolve_request_line_data` diffs for a
                    -- set line, not just its members.
                    SELECT l.request_id, ps2.updated_at
                    FROM price_tag_request_lines l
                    JOIN product_sets ps2 ON ps2.id = l.product_set_id
                    WHERE l.request_id IN :ids AND l.product_set_id IS NOT NULL
                    UNION ALL
                    SELECT lp.request_id, pa.created_at
                    FROM line_products lp
                    JOIN product_attachments pa ON pa.product_id = lp.product_id
                ) x
                GROUP BY request_id
            )
            SELECT request_id, moved_at FROM moved
            """
        ).bindparams(bindparam("ids", expanding=True))

        moved_by_request = {
            str(row.request_id): row.moved_at
            for row in db.execute(sql, {"ids": ids}).all()
        }

        touched: set[str] = set()
        for request_id in ids:
            checked = checked_at.get(request_id)
            if checked is None:
                touched.add(request_id)
                continue
            moved_at = moved_by_request.get(request_id)
            if moved_at is not None and moved_at > checked:
                touched.add(request_id)
        return touched

    @staticmethod
    def list_items(db: Session, requests: list[PriceTagRequest]) -> list:
        """The listing rows the queue draws, names resolved."""
        from app.schemas.price_tag import PriceTagRequestListItem
        from app.services.portal_service import PortalService

        labels = PriceTagRequestService.resolved_labels(
            db, [request.id for request in requests]
        )
        # R3-1/AC-R5: the summary carries whether an unsent revision draft is
        # parked for this row, same shared check the other portal kinds use.
        draft_ids = PortalService(db)._ids_with_revision_draft(  # noqa: SLF001
            "price_tag_request", [str(request.id) for request in requests]
        )
        items = []
        for request in requests:
            item = PriceTagRequestListItem.model_validate(request)
            for key, value in labels.get(request.id, {}).items():
                setattr(item, key, value)
            item.has_revision_draft = str(request.id) in draft_ids
            # Code review 16 Sep (BLOCKER): a terminal request is never
            # "touched" (`touched_request_ids` excludes it on purpose), so
            # nothing ever zeroes a stale stored count from before it closed
            # - the pill would keep reading e.g. "changed - 2" on a request
            # nothing can be updated on. Zeroed here, at read time, rather
            # than written back: a closed request's own column stays
            # whatever it was, which is what the transition itself did not
            # bother to touch.
            if PriceTagRequestService.is_terminal(request):
                item.data_changed_tag_count = 0
            items.append(item)
        return items

    @staticmethod
    def response_with_resolved_lines(db: Session, request: PriceTagRequest):
        """The request, with each line carrying what a person can read off it.

        A line row holds a product id, a quantity and an override; the code, the
        name and both prices live in the product master and the pricing engine.
        Resolved through ``tag_data_service`` - the SAME call the designer and the
        print payload use - so no reader can quote a different price from the tag
        it is about to print.

        A line the resolver skipped (its product has been removed) keeps its blank
        defaults rather than vanishing: a request that silently lists fewer lines
        than were submitted is the worse failure.

        Lives here, not in a route module, because the CRM detail route and the
        portal detail route both answer with it (D49). Same reason
        ``attachments`` is resolved here rather than in either route: one call
        to ``list_attachments_for_entity`` is what keeps the two screens from
        ever disagreeing about what this request's PO files look like
        (PLAN-price-tag-feedback-r2 S1).
        """
        from app.schemas.price_tag import (
            PriceTagRequestAttachment,
            PriceTagRequestResponse,
            PriceTagRequestTagResponse,
        )
        from app.services.dealer_kit import tag_data_service
        from app.services.entity_attachment_service import list_attachments_for_entity

        response = PriceTagRequestResponse.model_validate(request)
        response.collected_by_name = PriceTagRequestService.collected_by_name(
            db, request
        )
        # One resolver row per TAG since S3 (D3). The line's own code, name and
        # prices come off its FIRST tag - every tag on a line prints the same
        # host product, so those three are a line fact even though the rows are
        # per tag.
        # Security review 16 Sep: the horizon is captured BEFORE the resolve,
        # never after - see `store_data_change_count`'s own docstring.
        checked_at = datetime.utcnow()
        rows = tag_data_service.resolve_request_line_data(db, request)
        # AC-D2: the detail route already pays for this exact resolve, so the
        # stored count/timestamp are refreshed here at no extra cost - the
        # list route's own cache stays honest the moment anyone opens the
        # record, not only on its own 30s poll.
        tag_data_service.store_data_change_count(db, request, rows, checked_at)
        db.commit()
        by_tag = {row["tag_id"]: row for row in rows}
        first_by_line: dict[str, dict] = {}
        for row in rows:
            first_by_line.setdefault(row["line_id"], row)
        tags_by_line: dict[str, list] = {}
        for line in request.lines:
            tags_by_line[line.id] = sorted(
                line.tags or [], key=lambda t: (t.sort_order or 0, t.id)
            )

        for line in response.lines:
            line.tags = [
                PriceTagRequestTagResponse(
                    **PriceTagRequestService.tag_body(tag, by_tag.get(tag.id))
                )
                for tag in tags_by_line.get(line.id, [])
            ]
            row = first_by_line.get(line.id)
            if not row:
                continue
            line.code = row["code"]
            line.name = row["name"]
            # float() here rather than trusting the annotation: assigning to a
            # pydantic field does NOT validate, so a Decimal set on a `float` field
            # is serialised as a JSON STRING and the page's `.toFixed(2)` throws.
            line.list_price = None if row["list_price"] is None else float(row["list_price"])
            line.sell_price = None if row["sell_price"] is None else float(row["sell_price"])
            # D1 (S6): `promotion_name` is resolved, not stored (a bare id is
            # never shown, AC-X-2); `sell_price_basis` is the resolver's own
            # answer (D3/AC-S9-3) - both were declared on the schema and
            # silently dropped by `response_model` until copied here too.
            line.sell_price_basis = row.get("sell_price_basis")

        promotion_ids = {
            line.promotion_id for line in request.lines if line.promotion_id
        }
        promotion_names: dict[str, str] = {}
        promotion_ends: dict[str, object] = {}
        if promotion_ids:
            from app.models.marketing import Promotion
            from app.services.dealer_kit.pricing import business_today

            today = business_today()
            for promo in (
                db.query(Promotion.id, Promotion.description, Promotion.end_date)
                .filter(Promotion.id.in_(promotion_ids))
                .all()
            ):
                promotion_names[promo.id] = promo.description or ""
                if promo.end_date is not None and promo.end_date < today:
                    promotion_ends[promo.id] = promo.end_date
        for line in response.lines:
            if line.promotion_id:
                line.promotion_name = promotion_names.get(line.promotion_id)
                line.promotion_expired_on = promotion_ends.get(line.promotion_id)

        PriceTagRequestService._fill_line_parts(db, request, response)

        response.attachments = [
            PriceTagRequestAttachment(**row)
            for row in list_attachments_for_entity(db, "price_tag_request", request.id)
        ]

        # Function-local: breaks a module cycle. tag_sheet_export_service
        # imports STATUS_APPROVED/STATUS_READY from THIS module at its own
        # top level, so a top-level import here would be circular.
        from app.services.dealer_kit.tag_sheet_export_service import (
            latest_completed_export,
            latest_export_status,
        )

        response.has_completed_export = (
            latest_completed_export(db, request.id) is not None
        )
        response.latest_export_status = latest_export_status(db, request.id)

        # R3-1/AC-R1: reverses S8's D-P6 - a submitted request is read-only
        # exactly like a stock inquiry. True for a draft only; a submitted
        # request changes through the revision engine instead.
        response.is_editable = bool(request.portal_draft_at)

        # The header's names, from the same resolver the listing uses so the two
        # screens cannot disagree about who claimed a request.
        for key, value in (
            PriceTagRequestService.resolved_labels(db, [request.id])
            .get(request.id, {})
            .items()
        ):
            setattr(response, key, value)
        return response

    @staticmethod
    def list_requests(
        db: Session,
        *,
        contact_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        include_drafts: bool = True,
    ) -> list[PriceTagRequest]:
        """List requests, optionally filtered by contact_id, status, or search.

        ``include_drafts=False`` is what marketing's queue asks for. A portal
        draft carries status ``new`` exactly like a submitted request, so without
        this the CRM listing showed forms the salesperson was still typing and
        marketing could claim one. The portal's own list leaves it True: a draft
        is the whole point of that screen.
        """
        q = PriceTagRequestService._list_query(
            db,
            contact_id=contact_id,
            status=status,
            search=search,
            include_drafts=include_drafts,
        )
        return q.order_by(PriceTagRequest.created_at.desc()).all()

    @staticmethod
    def _list_query(
        db: Session,
        *,
        contact_id: str | None,
        status: str | None,
        search: str | None,
        include_drafts: bool,
    ):
        """The filtered query both the whole-list and the paged reads run."""
        q = db.query(PriceTagRequest)
        if contact_id:
            q = q.filter(PriceTagRequest.contact_id == contact_id)
        if not include_drafts:
            q = q.filter(PriceTagRequest.portal_draft_at.is_(None))
        if status:
            q = q.filter(PriceTagRequest.status == status)
        if search:
            like = f"%{search}%"
            q = q.filter(
                or_(
                    PriceTagRequest.doc_number.ilike(like),
                    PriceTagRequest.debtor_name.ilike(like),
                )
            )
        return q

    # What the queue may sort by. Only real columns: the salesperson, the
    # assignee, the promotion and the line count are RESOLVED per page rather
    # than stored, so sorting on them would mean sorting something the query
    # cannot see. The listing does not offer those as sortable either.
    SORTABLE_COLUMNS = {
        "doc_number": PriceTagRequest.doc_number,
        "debtor_name": PriceTagRequest.debtor_name,
        "debtor_code": PriceTagRequest.debtor_code,
        "status": PriceTagRequest.status,
        "needed_by_date": PriceTagRequest.needed_by_date,
        "created_at": PriceTagRequest.created_at,
    }

    @staticmethod
    def list_page(
        db: Session,
        *,
        contact_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        include_drafts: bool = True,
        page: int = 1,
        limit: int = 50,
        sort: str | None = None,
        direction: str = "asc",
    ) -> tuple[list[PriceTagRequest], int]:
        """One page of requests, and how many there are in total.

        The listing used to answer the WHOLE table and let the browser cut the
        page out of it: every keystroke shipped every request in the system, and
        the record count under the grid was the length of whatever array had
        arrived rather than what the table holds.
        """
        q = PriceTagRequestService._list_query(
            db,
            contact_id=contact_id,
            status=status,
            search=search,
            include_drafts=include_drafts,
        )
        total = q.order_by(None).count()

        column = PriceTagRequestService.SORTABLE_COLUMNS.get(sort or "")
        if column is None:
            # Newest first is the queue's own order, and it is what an unknown
            # or a resolved-only column falls back to rather than a 400 the
            # reader can do nothing about.
            ordering = [PriceTagRequest.created_at.desc()]
        else:
            ordering = [column.desc() if direction == "desc" else column.asc()]
        # Ends with the id, because created_at ties inside one transaction and a
        # tie makes page 2 repeat a row page 1 already showed.
        ordering.append(PriceTagRequest.id)

        rows = (
            q.order_by(*ordering)
            .offset(max(0, (page - 1) * limit))
            .limit(limit)
            .all()
        )
        return rows, total

    @staticmethod
    def lookup_tag_items(
        db: Session,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Sets and products in ONE list, for the lines table's single Item picker.

        A dealer does not know whether a thing is a set or a product, so the form
        stopped asking (D47) and this is what the one dropdown reads. Each row carries
        the REAL `product_sets.id` / `products.id`, because that is what a line's
        foreign key stores; the portal's generic product lookup answers with a code and
        no id at all, which is why a product line could never be saved.

        Sets come first, then products, each ordered by code and each capped at `limit`
        (so at most `2 * limit` rows reach the picker). Sets lead because they are far
        fewer and are the thing a salesperson is likeliest to miss.

        The catalogue search itself is `tag_data_service`'s, the same one the marketing
        canvas uses, so the portal and the editor cannot disagree about what exists.
        """
        from app.services.dealer_kit import tag_data_service

        items: list[dict] = [
            {
                "kind": "product_set",
                "id": str(row.id),
                "code": row.set_code,
                "name": row.name or "",
            }
            for row in tag_data_service.search_product_sets(db, query, limit=limit)
        ]
        items.extend(
            {
                "kind": "product",
                "id": str(row.id),
                "code": row.product_code,
                "name": row.product_name or "",
            }
            for row in tag_data_service.search_products(db, query, limit=limit)
        )
        return items

    @staticmethod
    def lookup_debtors_for_agent(
        db: Session,
        contact_id: str,
    ) -> list[dict]:
        """Scoped debtor lookup for a portal contact.

        Returns customers assigned to any of the contact's linked active SalesAgents,
        plus distinct debtors from orders for those agents within the last 24 months.
        Returns an empty list if the contact has no linked active agent.
        """
        from app.models.order import Customer, Order

        # Chatbot stock ask v2 S6 (AC-SA601): the agent resolution, shared with the
        # portal's Customer asks page. Module-level lookup so every caller reads the same
        # function at call time.
        agents = sales_agents_for_contact(db, contact_id)
        if not agents:
            return []

        debtors: dict[str, dict] = {}

        # Source 1: customers assigned to these agents (customers.sales_agent_id).
        customers = (
            db.query(Customer)
            .filter(Customer.sales_agent_id.in_([a.id for a in agents]))
            .all()
        )
        for c in customers:
            key = c.customer_code or c.id
            debtors[key] = {
                "customer_id": c.id,
                "customer_code": c.customer_code,
                "customer_name": c.customer_name,
                "debtor_code": c.customer_code,
                "debtor_name": c.customer_name,
                "source": "customer",
            }

        # Source 2: debtors from orders for these agents within last 24 months.
        cutoff = date.today() - timedelta(days=730)  # ~24 months
        orders = (
            db.query(Order.debtor_code, Order.debtor_name)
            .filter(
                Order.agent.in_([a.sales_agent for a in agents]),
                Order.order_date >= cutoff,
                Order.debtor_code.isnot(None),
            )
            .distinct()
            .all()
        )
        for debtor_code, debtor_name in orders:
            if debtor_code and debtor_code not in debtors:
                debtors[debtor_code] = {
                    "customer_id": None,
                    "customer_code": debtor_code,
                    "customer_name": debtor_name,
                    "debtor_code": debtor_code,
                    "debtor_name": debtor_name,
                    "source": "order",
                }

        return list(debtors.values())
