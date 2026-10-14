"""What a product set costs.

The price is DERIVED, never stored. Sorento's catalogue parks the whole set's
list price on one member - `SRTWCX8608-RL` the pedestal reads 1180.00 while
`SRTWCY8608` the cistern reads 0.00 - so the set price is a sum over whichever
members are ticked, not a sum over all of them and not a number typed twice.

Deriving it at read time is the point: a member's price changes and the set
follows with no backfill and no second source of truth to drift.

An absent price is not zero. A set with nothing ticked, or with no members yet,
returns `None` and a reason. The dealer-kit pricing work already paid for this
lesson - a list price of zero is missing data, not a free product - and a set
mid-authoring that claims "RM 0.00" is worse than one that says it has no basis.

UAC group D. Plan: `documentation/plans/master-data/PLAN-product-sets.md`.

Not to be confused with `product_predicate_service.py`, which filters products by
spec predicate for the chatbot's backward search. That module used to hold this
name.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

# Reasons a set has no computable price. Returned rather than raised: "this set
# has no price basis yet" is an ordinary state during authoring, not an error.
NO_MEMBERS = "no_members"
NO_MEMBER_CONTRIBUTES = "no_member_contributes"


@dataclass(frozen=True)
class SetPrice:
    """Both figures, always.

    A dataclass rather than a dict so a field cannot be quietly lost on the way
    out - FastAPI's ``response_model`` drops anything it was not told about, and
    an override that vanishes between the service and the wire looks exactly
    like an override nobody set.
    """

    computed: Optional[Decimal]
    override: Optional[Decimal]
    reason: Optional[str] = None

    @property
    def is_overridden(self) -> bool:
        return self.override is not None

    @property
    def resolved(self) -> Optional[Decimal]:
        """What to show. The override when set, otherwise the computed figure."""
        return self.override if self.override is not None else self.computed

    def as_dict(self) -> dict[str, Any]:
        return {
            "computed": self.computed,
            "override": self.override,
            "resolved": self.resolved,
            "is_overridden": self.is_overridden,
            "reason": self.reason,
        }


def resolve_set_price(product_set) -> SetPrice:
    """``SUM(member.list_price * quantity)`` over the ticked members.

    Pure over an already-loaded set: no session, no queries, so the rule is
    table-testable and cannot accidentally depend on request scope.

    The override is reported even when the computed figure is absent. A person
    who typed a price for a set with no basis should see their own number, not
    have it swallowed because the derivation had nothing to work with.
    """
    override = _as_decimal(getattr(product_set, "list_price_override", None))
    members = list(getattr(product_set, "members", None) or [])

    if not members:
        return SetPrice(computed=None, override=override, reason=NO_MEMBERS)

    contributing = [m for m in members if getattr(m, "contributes_to_price", False)]
    if not contributing:
        return SetPrice(computed=None, override=override, reason=NO_MEMBER_CONTRIBUTES)

    computed = Decimal("0")
    for member in contributing:
        price = _as_decimal(getattr(getattr(member, "product", None), "list_price", None))
        quantity = _as_decimal(getattr(member, "quantity", None))
        # A missing member price contributes nothing rather than blowing up the
        # whole set: one unpriced part should not make the other two unanswerable.
        computed += (price or Decimal("0")) * (quantity if quantity is not None else Decimal("1"))

    return SetPrice(computed=computed.quantize(Decimal("0.01")), override=override)


def _as_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


# --------------------------------------------------------------- the CRUD service
#
# Authoring lives here rather than in the router so the price rule above and the
# writes that depend on it stay in one file. A set is NOT orderable, so there is
# deliberately no stock write, no costing and no order path - only the master a
# person authors and the read the detail page renders.

from typing import Any, Optional  # noqa: E402

from sqlalchemy import func, or_  # noqa: E402
from sqlalchemy.orm import Session, joinedload  # noqa: E402

from app.models.base import set_company_scope  # noqa: E402
from app.models.inventory import Stock  # noqa: E402
from app.models.marketing import PromotionProduct  # noqa: E402
from app.models.product import Product, ProductAttachment  # noqa: E402
from app.models.product_set import ProductSet, ProductSetMember  # noqa: E402
from app.services.company_scope import get_company_scope, resolve_write_company_id  # noqa: E402
from app.services.error_handler import AppException  # noqa: E402


class _ListPage:
    def __init__(self, data, total, page, limit):
        self.data, self.total, self.page, self.limit = data, total, page, limit


class ProductSetService:
    """Create, read, edit and hard-delete product sets.

    Company scope is NOT an argument. Every query is ORM, so `do_orm_execute`
    applies the caller's scope: another company's set is invisible, which is why
    reading one is a 404 rather than a 403 - a scoped reader must not learn that
    it exists.
    """

    def __init__(self, db: Session):
        self.db = db

    # ----------------------------------------------------------------- reading

    def _require(self, product_set_id: str) -> ProductSet:
        row = (
            self.db.query(ProductSet)
            .options(joinedload(ProductSet.members).joinedload(ProductSetMember.product))
            .filter(ProductSet.id == product_set_id)
            .first()
        )
        if row is None:
            raise AppException(status_code=404, message="Product set not found")
        return row

    def get(self, product_set_id: str) -> ProductSet:
        product_set = self._require(product_set_id)
        self._decorate(
            product_set,
            available=self._available_for([m.product_id for m in product_set.members]),
        )
        return product_set

    def list(self, *, page: int = 1, limit: int = 50, query: Optional[str] = None) -> _ListPage:
        q = self.db.query(ProductSet).options(
            joinedload(ProductSet.members).joinedload(ProductSetMember.product)
        )
        if query and query.strip():
            needle = f"%{query.strip().lower()}%"
            q = q.filter(
                or_(
                    func.lower(ProductSet.set_code).like(needle),
                    func.lower(ProductSet.name).like(needle),
                )
            )
        total = q.count()
        rows = q.order_by(ProductSet.set_code.asc()).offset((page - 1) * limit).limit(limit).all()
        # ONE stock query for every member of every row on the page, rather than
        # one per row. The grid shows a Complete sets column, and a column that
        # reads "-" on every row is worse than no column at all.
        available = self._available_for(
            [m.product_id for row in rows for m in row.members]
        )
        for row in rows:
            self._decorate(row, available=available)
        return _ListPage(rows, total, page, limit)

    def select(
        self, *, query: Optional[str] = None, ids: Optional[list[str]] = None, limit: int = 20
    ) -> list[ProductSet]:
        """Sets for a line picker, decorated, in ONE stock query for the batch.

        `ids` returns exactly those sets (inactive included, so a saved line can still name its
        set); otherwise `query` matches code or name over ACTIVE sets, capped at `limit`. Both
        read through the scoped session, so another company's set is simply absent.
        """
        q = self.db.query(ProductSet).options(
            joinedload(ProductSet.members).joinedload(ProductSetMember.product)
        )
        if ids:
            q = q.filter(ProductSet.id.in_(ids))
        else:
            q = q.filter(ProductSet.is_active.is_(True))
            if query and query.strip():
                needle = f"%{query.strip().lower()}%"
                q = q.filter(
                    or_(
                        func.lower(ProductSet.set_code).like(needle),
                        func.lower(ProductSet.name).like(needle),
                    )
                )
        q = q.order_by(ProductSet.set_code.asc())
        if not ids:
            q = q.limit(limit)
        rows = q.all()
        available = self._available_for([m.product_id for row in rows for m in row.members])
        for row in rows:
            self._decorate(row, available=available)
        return rows

    # ----------------------------------------------------------------- writing

    def create(self, payload: dict[str, Any], *, created_by: Optional[str]) -> ProductSet:
        set_code = (payload.get("set_code") or "").strip()
        if not set_code:
            raise AppException(status_code=422, message="A set code is required")
        self._pin_scope_for_new_set()
        self._reject_duplicate_code(set_code, exclude_id=None)

        product_set = ProductSet(
            set_code=set_code,
            name=(payload.get("name") or "").strip(),
            is_active=payload.get("is_active", True),
            created_by=created_by,
        )
        self.db.add(product_set)
        self.db.flush()
        self._replace_members(product_set, payload.get("members") or [])
        self.db.commit()
        return self.get(product_set.id)

    def update(
        self, product_set_id: str, payload: dict[str, Any], *, updated_by: Optional[str]
    ) -> ProductSet:
        product_set = self._require(product_set_id)
        # Pin to the set's OWN company before any code-based lookup below. An
        # X-API-Key caller with no contact/space params loaded this row under the
        # `None` (all-companies) scope - `_require` matches by id, not by code,
        # so that lookup is unambiguous - but `_reject_duplicate_code` and
        # `_replace_members` both match by CODE, which is unique per company
        # only, so without this pin they would search across both companies
        # instead of the one this set actually belongs to.
        #
        # Deferred import: `app.api.v1.external.utils` sits behind
        # `app.api.v1.__init__` -> `master_data` -> this module at package-load
        # time, so importing it at module level here would be a circular import.
        from app.api.v1.external.utils import pin_scope_to_companies

        pin_scope_to_companies(self.db, [product_set.company_id], anchor="This product set")

        if "set_code" in payload:
            set_code = (payload["set_code"] or "").strip()
            if not set_code:
                raise AppException(status_code=422, message="A set code is required")
            self._reject_duplicate_code(set_code, exclude_id=product_set.id)
            product_set.set_code = set_code
        if "name" in payload:
            product_set.name = (payload["name"] or "").strip()
        if "is_active" in payload:
            product_set.is_active = bool(payload["is_active"])

        if "list_price_override" in payload:
            override = payload["list_price_override"]
            product_set.list_price_override = override
            # The badge names who set it, so clearing must clear the stamp too -
            # an override attributed to someone who has since removed it is worse
            # than no attribution at all.
            product_set.override_set_by = updated_by if override is not None else None
            # A real datetime, not `func.now()`: the audit listener snapshots this row
            # to JSON at flush, and a SQL expression is not a value until the
            # database has evaluated it.
            product_set.override_set_at = datetime.utcnow() if override is not None else None

        # Omitted leaves membership alone; `[]` empties it. The FE relies on the
        # difference: renaming a set must not silently drop its members.
        if "members" in payload:
            self._replace_members(product_set, payload["members"] or [])

        self.db.commit()
        return self.get(product_set.id)

    def delete(self, product_set_id: str) -> None:
        """Hard delete, per the CRUD standard. Members cascade; products do not."""
        product_set = self._require(product_set_id)
        self.db.delete(product_set)
        self.db.commit()

    # ---------------------------------------------------------------- internals

    def _pin_scope_for_new_set(self) -> None:
        """Pin the session to ONE company before any code-based lookup runs.

        `set_code` and `product_code` are unique PER COMPANY only
        (`uq_product_sets_company_code`, `uq_products_company_product_code`), so
        an X-API-Key caller with no contact/space params - the `None`
        (all-companies) scope migration 414 grants
        `master_data.product_sets.add`/`.edit` to - would otherwise let
        `_reject_duplicate_code` and `_replace_members` search across both
        companies and pick a row by physical order (11k+ codes exist in both).
        `resolve_write_company_id` is the SAME decision `before_insert` makes
        when it stamps the new set's own `company_id`, so pinning to it here
        keeps the duplicate check and the member lookup consistent with the
        company the set is about to be created in: a real single-company scope
        passes through unchanged, `None` pins to the incumbent company (the
        same fallback the stamp would use anyway), and an unset/ambiguous scope
        still raises exactly as `before_insert` would.
        """
        company_id = resolve_write_company_id(get_company_scope(self.db))
        set_company_scope(self.db, frozenset({company_id}))

    def _reject_duplicate_code(self, set_code: str, *, exclude_id: Optional[str]) -> None:
        q = self.db.query(ProductSet).filter(
            func.lower(ProductSet.set_code) == set_code.lower()
        )
        if exclude_id:
            q = q.filter(ProductSet.id != exclude_id)
        if q.first() is not None:
            raise AppException(
                status_code=409,
                message=f"A set with the code {set_code} already exists for this company",
            )

    def _replace_members(self, product_set: ProductSet, members: list[dict[str, Any]]) -> None:
        # Read before the delete, so a product dropped by this replace can be
        # told apart from one that stays (its row is deleted and re-created
        # below either way, since membership is replaced wholesale - but the
        # PRODUCT it names is what the fan-out cleanup below cares about).
        previous_product_ids = {str(m.product_id) for m in product_set.members}

        for existing in list(product_set.members):
            self.db.delete(existing)
        self.db.flush()

        kept_product_ids: set[str] = set()
        for index, raw in enumerate(members):
            code = (raw.get("product_code") or "").strip()
            if not code:
                continue
            product = (
                self.db.query(Product)
                .filter(func.lower(Product.product_code) == code.lower())
                .first()
            )
            # Named, not counted. "1 product could not be found" sends someone
            # hunting; the code sends them straight to it. The company scope
            # filter is what makes another company's product invisible here, so
            # this is also the message for a cross-company member.
            if product is None:
                raise AppException(
                    status_code=422,
                    message=f"No product in this company carries the code {code}",
                )
            kept_product_ids.add(str(product.id))
            self.db.add(
                ProductSetMember(
                    product_set_id=product_set.id,
                    product_id=product.id,
                    quantity=raw.get("quantity") or 1,
                    contributes_to_price=bool(raw.get("contributes_to_price")),
                    sort_order=raw.get("sort_order", index),
                )
            )
        self.db.flush()

        removed_product_ids = previous_product_ids - kept_product_ids
        if removed_product_ids:
            self._detach_set_fanout_links(product_set.id, removed_product_ids)

    def _detach_set_fanout_links(self, product_set_id: str, product_ids: set[str]) -> None:
        """Remove the attachment/promotion links THIS set's own fan-out created
        for a product that just left it (D10).

        `linked_via_set_id` is stamped ONLY when `_resolve_codes` expanded a set
        code into its members (`app/api/v1/external/product_attachments.py`,
        `app/api/v1/external/promotions.py`) - a NULL value means a person or an
        exact product code made the link, and that is never touched here. A
        cert-bearing attachment's `product_attachments` row is written solely by
        `CertificateService` (COV-1), which never sets `linked_via_set_id`, so
        those rows are naturally excluded by the same filter, no carve-out
        needed. Matched on the pair (this set, this product) so a link fanned
        out for a DIFFERENT member of the same set is left alone.

        Deleting the WHOLE set is a different question, answered by the
        `ON DELETE SET NULL` foreign key (migration 412): the documents a
        deleted set once linked must survive it, only their provenance goes.
        This method only runs for a member that leaves a set that still exists.
        """
        self.db.query(ProductAttachment).filter(
            ProductAttachment.linked_via_set_id == product_set_id,
            ProductAttachment.product_id.in_(list(product_ids)),
        ).delete(synchronize_session=False)
        self.db.query(PromotionProduct).filter(
            PromotionProduct.linked_via_set_id == product_set_id,
            PromotionProduct.product_id.in_(list(product_ids)),
        ).delete(synchronize_session=False)

    def _available_for(self, product_ids: list[str]) -> dict[str, int]:
        """`{product_id: units across every warehouse}` in one query."""
        if not product_ids:
            return {}
        rows = (
            self.db.query(Stock.product_id, func.sum(Stock.quantity_available))
            .filter(Stock.product_id.in_(list(set(product_ids))))
            .group_by(Stock.product_id)
            .all()
        )
        return {str(pid): int(total or 0) for pid, total in rows}

    def _decorate(self, product_set: ProductSet, *, available: dict[str, int]) -> None:
        """Attach the derived figures the serializer reads.

        Throwaway instance attrs, the same shape `ProductService` uses for the
        variant graph, so the schema never reaches into the relationships itself.
        """
        product_set.price = resolve_set_price(product_set)
        product_set.member_count = len(product_set.members)
        product_set.complete_sets = None
        product_set.limiting_member_code = None

        complete = None
        limiting = None
        for member in product_set.members:
            have = available.get(str(member.product_id), 0)
            member.available = have
            quantity = _as_decimal(member.quantity) or Decimal("1")
            if quantity <= 0:
                continue
            # A discontinued member contributes nothing: the set survives it (the
            # flyer code is still asked about) but it cannot be completed, and the
            # reason is the member's own name.
            sets_from_member = (
                0
                if getattr(member.product, "is_discontinued", False)
                else int(Decimal(have) // quantity)
            )
            if complete is None or sets_from_member < complete:
                complete, limiting = sets_from_member, member.product.product_code

        product_set.complete_sets = complete
        product_set.limiting_member_code = limiting


# ----------------------------------------------------------- the DRIVER member (R19)
#
# A set is never stocked, never ordered and never costed - its members are - so every
# figure a set row shows on the loading plan has to be read off ONE of them.


def driver_members(db: Session, set_ids: list[str]) -> dict[str, ProductSetMember]:
    """`{set_id: the member whose figures the set reads}` (R19), in ONE pass.

    The driver is the member belonging to the FEWEST sets, ties broken by `sort_order` and
    then by product code. On Sorento's catalogue that is always the pedestal, without
    anybody having to tag one.

    Both alternatives were rejected for the same reason, and the reason is `CWCY605`: that
    cistern sits in six different sets. A minimum across members would understate every one
    of them (each set would read the cistern's own thin demand), and a sum would count the
    cistern six times. The pedestal belongs to one set, so its numbers describe that set and
    nothing else.

    Every tie is broken on a stated value, ending on the product code, so two runs of the
    same plan cannot disagree about whose numbers a row is showing.

    Batched because `container_request_service._set_rows` asks for every set on a supplier's
    statement at once, and a query per set on a page of forty is how a screen gets slow
    without anyone noticing.
    """
    wanted = [str(i) for i in dict.fromkeys(set_ids) if i]
    if not wanted:
        return {}

    members = (
        db.query(ProductSetMember)
        .options(joinedload(ProductSetMember.product))
        .filter(ProductSetMember.product_set_id.in_(wanted))
        .all()
    )
    if not members:
        return {}

    # How many sets each of these products belongs to. Joined through `product_sets` so the
    # caller's company scope applies: another company's set must not sway which member of
    # ours drives a row.
    counts = dict(
        db.query(ProductSetMember.product_id, func.count(ProductSetMember.id))
        .join(ProductSet, ProductSet.id == ProductSetMember.product_set_id)
        .filter(
            ProductSetMember.product_id.in_(
                list({str(m.product_id) for m in members})
            )
        )
        .group_by(ProductSetMember.product_id)
        .all()
    )

    best: dict[str, ProductSetMember] = {}
    for member in sorted(
        members,
        key=lambda m: (
            counts.get(str(m.product_id), 1),
            m.sort_order or 0,
            (getattr(m.product, "product_code", None) or ""),
            str(m.id),
        ),
    ):
        best.setdefault(str(member.product_set_id), member)
    return best


def driver_member(db: Session, set_id: str) -> Optional[ProductSetMember]:
    """The one set's driver, or None when the set has no members yet."""
    return driver_members(db, [str(set_id)]).get(str(set_id))
