"""Shape B: a described product set intersected with a domain predicate.

"What faucets have certs" cannot be answered by resolving a top-K list and joining
ids across the wire: whenever the qualifying products fall outside K, the answer is
a false "none". So the intersection happens HERE, over the full company-scoped
catalogue - membership and the count are SQL, and the ranker only orders the
products that already qualify (`search_specs(product_ids=...)`).

Extending to a new domain is one function and one `REQUIRE_LEGS` entry - the legs
are a code-side registry on purpose. A predicate leg is behavior (validity logic,
ledger semantics, a join graph), not data, so registering SQL fragments as rows
would be a security surface wearing a config table's clothes.

Two standing rules every leg MUST follow:

- **ORM only, never raw ``text()`` SQL.** Company isolation is injected by the
  ``do_orm_execute`` listener (`app/services/company_scope.py`) into every ORM
  SELECT touching a `CompanyScopedMixin` model; raw SQL bypasses it entirely.
- **Reach unscoped link tables through a scoped side.** `certificate_products`
  carries no `company_id`; the certificate leg joins through `Certificate`
  (scoped), never the link table alone.

Contract: sorento_crm_n8n/n8n-workflows-init/plans/crm-ask-spec-backward-search.md.
Plan: documentation/plans/PLAN-spec-backward-search.md.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy import String as _String
from sqlalchemy import cast as _cast
from sqlalchemy import case, exists, func, literal_column, or_, true
from sqlalchemy.dialects.postgresql import ARRAY as _ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import ColumnElement

from app.models.access import ContactAccessType
from app.models.certificate import Certificate, CertificateProduct, CertificateRevision
from app.models.inventory import Stock, Warehouse
from app.models.marketing import Promotion, PromotionProduct
from app.models.procurement import InboundShipment, InboundShipmentLine
from app.models.product import Brand, Product, ProductAttachment
from app.models.product_spec import ProductSpecifications
from app.models.resources import Attachment, AttachmentType
from app.services.error_handler import AppException
from app.services.lookup_resolver import LookupResolverService
from app.services.product_spec_search import (
    _states,
    filter_specs,
    is_generic_free_term,
    search_specs,
    values_only,
)


class _UnrecognizedLabel(Exception):
    """A require value that resolved to nothing - reported, never swallowed.

    Same honesty class as an unrecognized free term: "your word mapped to no
    document type" must reach the customer as a clarify, not as a silent "none".

    ``extra`` merges straight into `resolve_product_set`'s own return dict on
    the miss (D2, AC-1313) - the certificate leg's only use today is
    ``schemes_on_file``, so a scheme miss can still name what IS on file.
    """

    def __init__(self, label: str, *, extra: dict[str, Any] | None = None):
        super().__init__(label)
        self.label = label
        self.extra = extra or {}


def _bound_spec_matches(values: dict | None, specs: list[dict] | None) -> set[str]:
    """R30/AC-1355: which of `specs`' own keys (bindings - class included) the
    product's OWN spec row satisfies - the require-only arm's answer to what
    `search_specs` computes for the ranked arm, since that function never
    runs without `free_terms`.

    String values compare through `_states` (case-insensitive, list-aware -
    the same equality the ranker itself uses). A numeric value compares by
    exact equality - the ranker's own tolerance window is a per-key registry
    lookup this arm has no reason to repeat; a require-only turn asks whether
    the described set has stock/certs/etc, not to rank by closeness, so exact
    is the honest answer here (the customer's binding either matches the
    stored value or it does not).
    """
    matched: set[str] = set()
    for entry in specs or []:
        key, target = entry.get("key"), entry.get("value")
        if key is None or target is None:
            continue
        stored = (values or {}).get(key)
        if not isinstance(stored, dict):
            continue
        actual = stored.get("value")
        if actual is None:
            continue
        if (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and isinstance(target, (int, float))
            and not isinstance(target, bool)
        ):
            if float(actual) == float(target):
                matched.add(key)
        elif _states(actual, target):
            matched.add(key)
    return matched


def _lookup_resolve(db: Session, set_key: str, raw: str) -> str | None:
    """The lookup set's own resolved VALUE, or None on any miss.

    Covers both 404 shapes `LookupResolverService` raises: the set itself is
    missing (an install nobody has run the owner's data entry on yet), and the
    set exists but no option/keyword matches `raw`. Either way a leg reads this
    as "unrecognized", never a 500 (D3, AC-1314).
    """
    try:
        return LookupResolverService(db).resolve(set_key, raw).value
    except AppException:
        return None


def _attachment_type_row(db: Session, label: str) -> AttachmentType | None:
    return (
        db.query(AttachmentType)
        .filter(
            or_(
                func.lower(AttachmentType.code) == label.lower(),
                func.lower(AttachmentType.type_name) == label.lower(),
            )
        )
        .first()
    )


def _product_type_by_description(db: Session, label: str) -> AttachmentType | None:
    """The ONE product-facing attachment type whose name, code or description carries the
    customer's word (ATTACHMENT-MULTI tester re-run, 2 Oct 2026).

    The entity resolver already reads "photo" as Product Photos off the description
    ("Product Photos, Photo, Image, Pictures by Marketing" on dev); this leg did not, so
    the HAS path answered "I don't know 'photo' as a document type". Only types products
    actually carry are candidates (the same set `_attachment_type_names_on_file` names), so
    an internal class whose description mentions the word never wins, and two candidates
    are no answer at all.
    """
    word = label.strip()
    if len(word) < 3:
        return None
    pattern = f"%{word}%"
    rows = (
        db.query(AttachmentType)
        .join(Attachment, Attachment.attachment_type_id == AttachmentType.id)
        .join(ProductAttachment, ProductAttachment.attachment_id == Attachment.id)
        .filter(
            or_(
                AttachmentType.type_name.ilike(pattern),
                AttachmentType.code.ilike(pattern),
                AttachmentType.description.ilike(pattern),
            )
        )
        .distinct()
        .limit(2)
        .all()
    )
    return rows[0] if len(rows) == 1 else None


def _attachment_type_names_on_file(db: Session) -> list[str]:
    """Every PRODUCT-FACING `AttachmentType.type_name` on file, sorted (second
    console pass, AC-1329): only types that actually appear in
    `product_attachments` - never the whole `AttachmentType` table, which also
    carries internal document classes (Container Status, Complaint Document, a
    company's GRN/portal-submission attachments, ...) a customer never asks
    about. `AttachmentType` itself carries no company scope, but the join
    through `ProductAttachment`/`Attachment` (both `CompanyScopedMixin`) is
    itself company-scoped by the existing `do_orm_execute` listener, so this
    plain join is correct with no manual filter."""
    rows = (
        db.query(AttachmentType.type_name)
        .join(Attachment, Attachment.attachment_type_id == AttachmentType.id)
        .join(ProductAttachment, ProductAttachment.attachment_id == Attachment.id)
        .distinct()
        .all()
    )
    return sorted({str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()})


def _access_level_codes(db: Session, access_levels: list[str] | None) -> set[str] | None:
    """Caller-supplied access-level values translated to canonical
    `contact_access_types.code` values - the same case-insensitive name -> code
    translation `references._apply_promotion_access_levels_filter` already
    uses, PLUS two more (R18/AC-1342, SEC-S1 re-check): a value equal to a
    `code` itself, and a bare TIER TOKEN (the parsed head's own recomposed
    tier vocabulary - "dealer", "office", "end_user" - never a brand-qualified
    code on its own) matched against every code that IS the token or ENDS
    WITH `_<token>` ("dealer" -> dealer, cabana_dealer, mocha_dealer; "office"
    -> sorento_office, cabana_office). Read off the table, no tier list in
    code. `None` when `access_levels` itself is empty/falsy (no restriction,
    every tier counts); an EMPTY set when nothing translated to a known code
    (a filter that matches nothing, never a silent no-op that lets every
    promotion back in)."""
    names_lower = {str(n).strip().lower() for n in (access_levels or []) if n and str(n).strip()}
    if not names_lower:
        return None
    # R24/AC-1348 (round 3 re-check, both reviewers): the token is interpolated
    # straight into a SQL LIKE pattern below - `\`, `%` and `_` are all LIKE
    # metacharacters, so a caller-supplied value containing one must be
    # escaped to its LITERAL spelling first, or it silently widens the match
    # (measured live: `["%"]` returned every code carrying an underscore
    # instead of the empty set this AC demands). Backslash is escaped FIRST so
    # the escaping added for `%`/`_` is not itself re-escaped.
    def _escape_like(token: str) -> str:
        return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    token_suffix_matches = [
        func.lower(ContactAccessType.code).like(f"%\\_{_escape_like(token)}", escape="\\")
        for token in names_lower
    ]
    rows = (
        db.query(ContactAccessType.code)
        .filter(
            or_(
                func.lower(ContactAccessType.name).in_(names_lower),
                func.lower(ContactAccessType.code).in_(names_lower),
                *token_suffix_matches,
            )
        )
        .all()
    )
    return {r[0] for r in rows}


def _leg_attachment_type(db: Session, value: Any, access_levels: list[str] | None = None) -> ColumnElement:
    """Has an attachment of the type the CUSTOMER NAMED - label in, not code.

    Resolution tries, in order: exact code / type_name (today, mirrors
    `_probe_attachment_type` so "technical drawing" works wherever it already
    works), then the `attachment_type_alias` lookup set (D2, AC-1312) - "photo",
    "picture", "gambar" reach whatever type the owner has aliased them to.
    Resolving server-side is what keeps new document classes out of the parser
    prompt: the parser ships four key names, the labels live in this table.

    R6/AC-1329 (console fix round 2): a miss here carries
    `attachment_types_on_file` - the product-facing AttachmentType names - so
    the reply can clarify the label as a DOCUMENT type ("I don't know 'photo'
    as a document type. Types I know: ...") rather than the product-type
    sentence, which answers the wrong question for a file label.
    """
    label = str(value or "").strip()
    row = _attachment_type_row(db, label)
    if row is None:
        aliased_label = _lookup_resolve(db, "attachment_type_alias", label)
        if aliased_label:
            row = _attachment_type_row(db, aliased_label)
    if row is None:
        row = _product_type_by_description(db, label)
    if row is None:
        raise _UnrecognizedLabel(
            label, extra={"attachment_types_on_file": _attachment_type_names_on_file(db)}
        )
    clause = exists().where(
        ProductAttachment.product_id == Product.id,
        ProductAttachment.company_id == Product.company_id,
        Attachment.id == ProductAttachment.attachment_id,
        Attachment.attachment_type_id == row.id,
    )
    # The echo shows what the label resolved TO, so n8n can render the CRM's
    # reading of the word back to the customer.
    clause._resolved_display = row.type_name or row.code  # type: ignore[attr-defined]
    return clause


def _schemes_on_file(db: Session) -> list[str]:
    """Every distinct scheme the register carries an ACTIVE certificate under,
    sorted, company-scoped (`Certificate` is `__company_shared__` - the
    `do_orm_execute` listener already ORs in the NULL-company shared rows, so a
    plain scoped query is correct here without any manual company_id filter)."""
    rows = db.query(Certificate.scheme).filter(Certificate.status == "active").distinct().all()
    return sorted({str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()})


def recover_certificate_scheme(
    db: Session, require: dict[str, Any], words: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """R14/AC-1338 (third console pass): a BARE `{"certificate": True}` require
    whose described-set remainder still holds the scheme word never split off
    the attachment_type raw ("which item has PPS cert" - "PPS" was never
    separated from "cert" upstream). Tried only when `require["certificate"]`
    is exactly `True` (an already-scoped `{"scheme": ...}` object is left
    untouched) - each word is tried, in order, against the register's own
    active scheme spellings (`_schemes_on_file`, case-insensitive) then the
    `certificate_scheme` lookup set's keywords, and the FIRST hit promotes
    `require` to `{"certificate": {"scheme": <register spelling>}}` and
    reports that ONE word CONSUMED, so the caller can drop it from the
    described-set remainder before it reaches `filter_specs` as an
    unrecognized set word. No hit at all returns `require`/`words` unchanged -
    a remainder that names no real scheme stays exactly as honest as it is
    today (AC-1320's own regression guard).
    """
    if require.get("certificate") is not True:
        return require, []
    schemes_on_file = _schemes_on_file(db)
    by_lower = {scheme.lower(): scheme for scheme in schemes_on_file}
    for word in words:
        cleaned = str(word or "").strip()
        if not cleaned:
            continue
        resolved = by_lower.get(cleaned.lower()) or _lookup_resolve(db, "certificate_scheme", cleaned)
        if resolved:
            return {**require, "certificate": {"scheme": resolved}}, [word]
    return require, []


def _leg_certificate(db: Session, value: Any, access_levels: list[str] | None = None) -> ColumnElement:
    """In the certificate register. Bare ``true`` = any active-register cert
    (decided); the object form narrows: ``scheme`` through the
    `certificate_scheme` lookup set (D2, AC-1313) then equality on the resolved
    value, ``validity_state: "valid"`` through the current revision's window -
    validity is DERIVED from revision dates, never stored (see the certificate
    model). A scheme with no matching option/keyword raises with
    `schemes_on_file` attached, so the reply can name what IS on file rather
    than answer a silent zero.

    Joins through ``Certificate`` because ``certificate_products`` has no
    company_id.

    R17/AC-1341 (third console pass, REV-B1 re-check): an explicit same-company
    predicate against `Certificate` - `with_loader_criteria` (the
    `do_orm_execute` listener's own scoping mechanism) does not reach a bare
    `exists().where(...)` subquery, so without this a certificate stamped to a
    DIFFERENT company still counted once its product_id matched. The `is_(None)`
    arm keeps `Certificate`'s own deliberately SHARED rows (`__company_shared__`,
    a NULL `company_id`) counting - strict equality alone would undercount them.
    """
    conditions = [
        CertificateProduct.product_id == Product.id,
        Certificate.id == CertificateProduct.certificate_id,
        or_(Certificate.company_id.is_(None), Certificate.company_id == Product.company_id),
        Certificate.status == "active",
    ]
    resolved_value = value
    if isinstance(value, dict):
        scheme = str(value.get("scheme") or "").strip()
        if scheme:
            # R5/AC-1328 (console fix round 2): the register's OWN active
            # spellings are tried FIRST, by case-insensitive equality - a
            # scheme word that IS a real spelling ("pps" against a register
            # carrying "PPS") must resolve even on a fresh install where the
            # owner has not yet entered it into the `certificate_scheme`
            # lookup set (D3's own empty-set default). The lookup set is the
            # ALIAS mechanism for a word that is NOT itself a register
            # spelling, not the only path to one that already is.
            schemes_on_file = _schemes_on_file(db)
            by_lower = {s.lower(): s for s in schemes_on_file}
            resolved_scheme = by_lower.get(scheme.lower())
            if resolved_scheme is None:
                resolved_scheme = _lookup_resolve(db, "certificate_scheme", scheme)
            if resolved_scheme is None:
                raise _UnrecognizedLabel(scheme, extra={"schemes_on_file": schemes_on_file})
            conditions.append(func.lower(Certificate.scheme) == resolved_scheme.lower())
            resolved_value = {**value, "scheme": resolved_scheme}
        if str(value.get("validity_state") or "").strip().lower() == "valid":
            conditions.extend(
                [
                    CertificateRevision.id == Certificate.current_revision_id,
                    or_(
                        CertificateRevision.valid_until.is_(None),
                        CertificateRevision.valid_until >= func.current_date(),
                    ),
                ]
            )
    clause = exists().where(*conditions)
    if resolved_value is not value:
        clause._resolved_display = resolved_value  # type: ignore[attr-defined]
    return clause


def _leg_promotion(db: Session, value: Any, access_levels: list[str] | None = None) -> ColumnElement:
    """Member of an active promotion: switched on AND inside its date window.

    REV-B1/AC-1310 (third console pass): `with_loader_criteria` (the
    `do_orm_execute` listener's own scoping mechanism) filters ORM entities,
    not a bare `exists().where(...)` subquery, so this EXISTS needs its own
    explicit same-company predicate against `Promotion` - the parent side
    `PromotionProduct` joins through - or a foreign-company promotion linked
    to one of the caller's products still counts.

    SEC-S1/AC-1334: also intersects `Promotion.access_levels` with the
    caller's OWN tier(s) - unfiltered, `qualifying_total` and the named
    products could disclose a tier-restricted promotion the contact cannot
    actually see. `access_levels=None` (or empty) is no restriction; a name
    that fails to translate to any known code is a filter that matches
    nothing, never a silent no-op.
    """
    conditions = [
        PromotionProduct.product_id == Product.id,
        Promotion.id == PromotionProduct.promotion_id,
        Promotion.company_id == Product.company_id,
        Promotion.is_active.is_(True),
        or_(Promotion.start_date.is_(None), Promotion.start_date <= func.current_date()),
        or_(Promotion.end_date.is_(None), Promotion.end_date >= func.current_date()),
    ]
    allowed_codes = _access_level_codes(db, access_levels)
    if allowed_codes is not None:
        conditions.append(
            Promotion.access_levels.op("?|")(_cast(list(allowed_codes), _ARRAY(_String)))
        )
    return exists().where(*conditions)


def _leg_stock(
    db: Session, value: Any, access_levels: list[str] | None = None, stock_policy: Any = None
) -> ColumnElement:
    """Plain on-hand > 0. Deliberately NOT the MCP's
    ``exclude_zero_system_adjustment`` semantics - that filter answers a
    different question ("hide rows an adjustment zeroed"), this one answers
    "is there any stock at all".

    REV-B1/AC-1310: an explicit `Stock.company_id == Product.company_id`
    predicate - `with_loader_criteria` does not reach a bare `exists()`
    subquery, so without this a `Stock` row stamped to another company still
    counts once its `product_id` matches.

    R22/AC-1346 (console pass 6): also `Warehouse.company_id == Product.
    company_id` and `Warehouse.is_active.is_(True)` - mirrors the stock list
    tool's own visibility filter (`inventory_service.py`'s
    ``Stock.warehouse.has(Warehouse.is_active.is_(True))``), so the header
    never counts a product whose only on-hand row sits in an inactive
    warehouse the answer itself would never show.

    `stock_policy` is the ASKING contact's stock visibility policy
    (`stock_visibility.resolve_policy`), when there is one: only the locations
    it allows count, through the same `warehouse_criterion` the stock tool
    answers from (stock ask v2 R3). A dealer on "Availability only" limited to
    one site pool is never told a product "has stock" off a location the
    policy hides. `None` (staff, API key, no contact) keeps every active
    warehouse, which is the tool's own answer for them too.
    """
    from app.services.stock_visibility import warehouse_criterion

    return exists().where(
        Stock.product_id == Product.id,
        Stock.company_id == Product.company_id,
        Warehouse.id == Stock.warehouse_id,
        Warehouse.company_id == Product.company_id,
        Warehouse.is_active.is_(True),
        warehouse_criterion(stock_policy, Stock.warehouse_id),
        Stock.quantity_on_hand > 0,
    )


def _leg_incoming(db: Session, value: Any, access_levels: list[str] | None = None) -> ColumnElement:
    """An open shipment line: shipped minus received is still positive AND the
    shipment has not actually arrived yet (D1, AC-1311). Joins through
    `InboundShipment` for the arrival check AND for company scope (REV-B1/
    AC-1310) - `with_loader_criteria` does not reach a bare `exists()`
    subquery, so an explicit `InboundShipment.company_id == Product.company_id`
    predicate is required; without it a foreign-stamped open shipment line
    still counts (the reviewer's own kill test)."""
    return exists().where(
        InboundShipmentLine.product_id == Product.id,
        InboundShipment.id == InboundShipmentLine.shipment_id,
        InboundShipment.company_id == Product.company_id,
        (
            func.coalesce(InboundShipmentLine.quantity_shipped, 0)
            - func.coalesce(InboundShipmentLine.quantity_received, 0)
        )
        > 0,
        InboundShipment.actual_arrival_date.is_(None),
    )


# R29/AC-1354: the `certificate_ids` cap - the same shape as `references.
# _SET_ID_CAP` / `answer.SET_ID_CAP` (200), reused here rather than
# imported to keep this module's only import of `references.py` at zero (the
# dependency runs the other way).
_CERTIFICATE_ID_CAP = 200

# One entry per domain. A new domain lands as one function + one line here + one
# noun in the n8n parser - never as another inline block in references.py.
def _leg_price(db: Session, value: Any, access_levels: list[str] | None = None) -> ColumnElement:
    """Every product of the described set: a price ask ("any gunmetal basin price") is
    answered for the set itself, each row the product-code price answer. Fix round 8 on
    PR #833 (owner retest of round 7: the set answer is the product-code answer "for
    incoming, product attachment etc and every other domain"). No filter: a product with
    no list price is still one the customer asked about, and its row says so."""
    return true()


REQUIRE_LEGS: dict[str, Callable[..., ColumnElement]] = {
    "attachment_type": _leg_attachment_type,
    "certificate": _leg_certificate,
    "promotion": _leg_promotion,
    "stock": _leg_stock,
    "incoming": _leg_incoming,
    "price": _leg_price,
}


def _brand_rows(db: Session) -> list[Brand]:
    """Every active brand, longest name first so "no logo" beats "no"."""
    rows = db.query(Brand).filter(Brand.is_active.is_(True)).all()
    rows = [r for r in rows if (r.brand_name or "").strip() and r.brand_name.strip().lower() != "others"]
    return sorted(rows, key=lambda r: -len(r.brand_name.strip()))


def _brand_word_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(name.strip())}(?![\w-])", re.IGNORECASE)


def _bind_brand_words(
    db: Session, brand: str | None, scope_terms: list[str] | None
) -> tuple[str | None, list[str] | None]:
    """`(brand, scope_terms)` with every brand name in a scope term taken out of it.

    The brands table is the source (owner brief W1): the word leaves the term, and the
    first brand named becomes the set's brand when the caller named none. A term that
    was only the brand is dropped."""
    if not scope_terms:
        return brand, scope_terms
    rows = _brand_rows(db)
    if not rows:
        return brand, scope_terms
    out: list[str] = []
    for term in scope_terms:
        text = str(term or "")
        for row in rows:
            pattern = _brand_word_re(row.brand_name)
            if pattern.search(text):
                if not brand:
                    brand = row.brand_name.strip()
                text = pattern.sub(" ", text)
        text = " ".join(text.split())
        if text:
            out.append(text)
    return brand, out


def _split_class_tail(db: Session, scope_terms: list[str] | None) -> list[str] | None:
    """A scope term that names no class as a whole, split at the longest tail that does:
    "wall hung basin" -> ["wall hung", "basin"] (or, round 4 R2, at the longest head that
    does: "water closet p trap" -> ["water closet", "p trap"]), so the class ("basin" -> Wash Basin) and
    the rest ("wall hung" -> mounting) both define the set. Owner turn 7 read "wall hung
    basin" as mounting alone and answered every wall hung product.

    Only when the leading words bind a spec of their own: "water tap" with no "water tap"
    synonym on file stays whole, so it is still reported as a phrase nobody knows and
    clarified with the nearest class (AC-1301/AC-1320), never answered as every tap."""
    from app.services.product_class_signal import resolve_classes_for_term
    from app.services.product_spec_search import resolve_terms_to_specs

    if not scope_terms:
        return scope_terms
    out: list[str] = []
    for term in scope_terms:
        words = str(term or "").split()
        if len(words) < 2 or resolve_classes_for_term(db, term):
            out.append(term)
            continue
        for i in range(1, len(words)):
            tail = " ".join(words[i:])
            head = " ".join(words[:i])
            if resolve_classes_for_term(db, tail) and resolve_terms_to_specs(db, [head]):
                out.extend([head, tail])
                break
        else:
            # Round 4 R2 (owner exchange 3): the class first and the spec after it,
            # "water closet p trap" -> ["water closet", "p trap"]. Kept whole, the class
            # never bound and the header lost its Product type line.
            for i in range(len(words) - 1, 0, -1):
                head = " ".join(words[:i])
                tail = " ".join(words[i:])
                if resolve_classes_for_term(db, head) and resolve_terms_to_specs(db, [tail]):
                    out.extend([head, tail])
                    break
            else:
                out.append(term)
    return out


def _display_name(name: str) -> str:
    """"SORENTO" -> "Sorento"; a name staff typed in mixed case stays as typed."""
    name = (name or "").strip()
    return name.title() if name.isupper() else name


def _sentence_case(text: str) -> str:
    text = " ".join(str(text or "").replace("_", " ").split())
    return text[:1].upper() + text[1:].lower() if text else text


def describe_set(
    db: Session, *, brand: str | None, membership: dict[str, list[str]]
) -> list[dict[str, str]]:
    """The described set in plain words, one `{key, label, value}` per binding, in the
    order the header says them: Brand, Product type, then every other spec key.

    Owner brief W2 on PR #833: labels and values come from the spec registry's own
    display fields (`label`, `value_labels`), so "mounting: wall_hung" reads
    "Mounting: Wall hung" and a staff edit to either label shows up here."""
    from app.models.product_spec import ProductSpecRegistry
    from app.services.product_spec_registry import display_spec_value

    out: list[dict[str, str]] = []
    if brand:
        # Round 3 W4: no "(default)" - the reply's last line names the other brands.
        out.append({"key": "brand", "label": "Brand", "value": _display_name(brand)})
    classes = [c for c in membership.get("class") or [] if c]
    others = [k for k in membership if k not in ("class", "brand")]
    rows = (
        {
            row.spec_key: row
            for row in db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key.in_(others)).all()
        }
        if others
        else {}
    )
    # Owner hand test of rounds 4 to 6 on PR #833, item 5 ("close couple wc ... p trap" got
    # both "Product type: Water closet" and "Type: Close coupled"): what the thing IS is one
    # line, "Product type: Close coupled water closet", never two. The registry's own
    # `product_type` key is the narrower noun, so it leads, and the class noun follows
    # when the value does not already say it ("Kitchen tap" stays "Kitchen tap").
    kinds = [
        display_spec_value(v, dict(getattr(rows.get("product_type"), "value_labels", None) or {}))
        for v in membership.get("product_type") or []
    ]
    if kinds:
        named = []
        for kind in kinds:
            noun = _sentence_case(classes[0]).lower() if len(classes) == 1 else ""
            named.append(kind if not noun or noun in kind.lower() else f"{kind} {noun}")
        out.append({"key": "class", "label": "Product type", "value": " or ".join(named)})
        others = [k for k in others if k != "product_type"]
    elif classes:
        out.append({"key": "class", "label": "Product type", "value": " or ".join(_sentence_case(c) for c in classes)})
    for key in others:
        row = rows.get(key)
        labels = dict(getattr(row, "value_labels", None) or {})
        values = [display_spec_value(v, labels) for v in membership[key]]
        # Fix round 8 on PR #833: a grounded number reads with its unit ("1.2 mm").
        unit = getattr(row, "unit", None) if row is not None and (row.data_type or "") == "numeric" else None
        if unit:
            values = [f"{v} {unit}" for v in values]
        out.append(
            {
                "key": key,
                "label": (row.label if row is not None and row.label else _sentence_case(key)),
                "value": " or ".join(values),
                # Fix round 8: how the set's intro says it ("with thickness 1.2 mm",
                # "gunmetal", "rimless").
                "kind": (row.data_type or "enum").lower() if row is not None else "enum",
            }
        )
    return out


def row_labels(db: Session, candidates: list[dict]) -> dict[str, dict[str, Any]]:
    """`{product_code: {"name": "Sorento Wall Basin"}}` for the candidates: the name a
    set row leads with.

    Owner brief W3 on PR #833: "codes alone are useless to the user". Round 3 W1: a
    product whose name is only its code is named by its description, then by its code.
    Round 4 R3: the row is "name (code)" and the facts the ask was about, so no spec
    values ride along any more."""
    ids = [c.get("product_id") for c in candidates if c.get("product_id")]
    if not ids:
        return {}
    names = {
        str(pid): ((name or "").strip(), (description or "").strip().lstrip("*").strip())
        for pid, name, description in db.query(Product.id, Product.product_name, Product.description)
        .filter(Product.id.in_(ids))
        .all()
    }
    out: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        code = cand.get("product_code")
        if not code:
            continue
        name, description = names.get(str(cand.get("product_id"))) or ("", "")
        out[code] = {"name": next((n for n in (name, description) if n and n != code), code)}
    return out


#: Keys that name WHAT the set is rather than a property of it; a near miss recounts
#: the set without a property, never without its class.
_SET_NOUN_KEYS = frozenset({"class", "brand", "product_type"})


def _near_miss(db: Session, *, membership: dict[str, list[str]], legs: list, brand: str | None) -> dict | None:
    """The zero set recounted without its one spec value, by that key's other values.

    Round 4 R4 (owner console test on PR #833: "the ask about gunmetal wash basin means
    what ah, so it got match or not? i have no visibility into whether it does the
    matching"): "No gunmetal wash basins with incoming stock (I looked for Finish or
    colour: Gunmetal among wash basins). 12 wash basins have incoming stock in another
    finish or colour: Chrome 5, ...". Only for a set with a class to count among and
    exactly one property key holding one value, which is the case the owner hit; a set
    of two property keys says the plain miss (trigger to widen: an owner turn that
    names two). The same legs, brand scope and membership clause as the count itself,
    one grouped query."""
    from app.models.product_spec import ProductSpecRegistry
    from app.services.product_spec_registry import display_spec_value
    from app.services.product_spec_search import membership_clause

    classes = list(membership.get("class") or [])
    props = [k for k in membership if k not in _SET_NOUN_KEYS]
    if not classes or len(props) != 1 or len(membership[props[0]]) != 1:
        return None
    key = props[0]
    value = membership[key][0]
    if not isinstance(value, str):
        # A grounded number (fix round 8) has no other choices to recount by.
        return None
    rest = {k: v for k, v in membership.items() if k != key}
    parent = aliased(Product)
    family = func.coalesce(parent.product_code, Product.product_code)
    stored = ProductSpecifications.values[key]["value"]
    # Round 5 S1 (reviewer pass at d6fa2b31): a LIST value ("Rose Gold + Matt Black" on
    # one product) is a member of each of its values, exactly as `membership_clause`
    # matches it by containment, so it is grouped under each. A scalar string is a list
    # of one; any other JSON type holds no value to name.
    as_list = case(
        (func.jsonb_typeof(stored) == "array", stored),
        (func.jsonb_typeof(stored) == "string", func.jsonb_build_array(stored)),
        # A literal empty ARRAY. `_cast("[]", JSONB)` bound the Python string "[]" as the
        # JSON string "\"[]\"", a scalar, so any member with no value for the key made
        # the whole recount fail ("cannot extract elements from a scalar") and the turn
        # lost its predicate (fix round 8 on PR #833: the owner's "Could not find
        # incoming for category gunmetal basin").
        else_=literal_column("'[]'::jsonb", JSONB),
    )
    element = func.jsonb_array_elements_text(as_list)
    query = (
        db.query(family.label("family"), element.label("value"))
        .select_from(Product)
        .outerjoin(parent, parent.id == Product.variant_of_id)
        .join(ProductSpecifications, ProductSpecifications.product_id == Product.id)
        .filter(Product.is_active.is_(True), *legs)
    )
    clause = membership_clause(rest)
    if clause is not None:
        query = query.filter(clause)
    if brand:
        query = query.join(Brand, Brand.id == Product.brand_id).filter(
            func.lower(Brand.brand_name) == brand.strip().lower()
        )
    members = query.subquery()
    per_value = db.query(members.c.value, func.count(func.distinct(members.c.family))).group_by(members.c.value).all()
    # A product with two other values is ONE product in the total, however many values
    # it is listed under.
    others_only = func.lower(members.c.value) != str(value).lower()
    other_total = int(
        db.query(func.count(func.distinct(members.c.family))).filter(others_only).scalar() or 0
    )
    registry = db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == key).first()
    labels = dict(getattr(registry, "value_labels", None) or {})
    others = sorted(
        (
            {"value": display_spec_value(v, labels), "count": int(n)}
            for v, n in per_value
            if n and str(v).lower() != str(value).lower()
        ),
        key=lambda o: (-o["count"], o["value"]),
    )
    return {
        "key": key,
        "label": registry.label if registry is not None and registry.label else _sentence_case(key),
        "value": display_spec_value(value, labels),
        "class_labels": sorted(classes),
        "other_values": others,
        "other_total": other_total,
    }


def _set_breakdown(
    db: Session, members_query, *, membership: dict[str, list[str]], brand: str | None, key: str | None = None
) -> dict | None:
    """A set counted by the next attribute that splits it: `{key, label, rows: [{value,
    count}]}`, largest first, each count in distinct variant families as the set's own
    count is.

    Fix round 9 on PR #833 (owner, 28 Sep 2026: "no cap", "always break it down"): a set
    too long for one reply gives its full count and this breakdown, never a paging
    question. The next attribute is the brand when none was named (no brand named means
    every brand), then the class, then each registry key in the registry's own order that
    the ask did not already bind; the first that splits the set in two or more is used.
    `key` forces one (the key of a value the registry does not know: "pink water
    closets" is said beside the finishes water closets come in). One query over the
    members' brand and specification row; `_near_miss` counts the same way."""
    from app.services.product_spec_registry import active_registry, display_spec_value

    members = members_query.subquery()
    rows = (
        db.query(members.c.family, Brand.brand_name, ProductSpecifications.values)
        .select_from(members)
        .join(Product, Product.id == members.c.pid)
        .outerjoin(Brand, Brand.id == Product.brand_id)
        .outerjoin(ProductSpecifications, ProductSpecifications.product_id == members.c.pid)
        .all()
    )
    if not rows:
        return None
    registry = [row for row in active_registry(db) if (row.data_type or "enum").lower() in ("enum", "numeric")]
    by_key = {row.spec_key: row for row in registry}
    order = [key] if key else [
        *(["brand"] if not brand else []),
        *(["class"] if len(membership.get("class") or []) != 1 else []),
        *[row.spec_key for row in registry if row.spec_key not in membership and row.spec_key not in ("brand", "class")],
    ]
    for name in order:
        counts: dict[str, set[str]] = {}
        row = by_key.get(name)
        labels = dict(getattr(row, "value_labels", None) or {})
        for family_code, brand_name, values in rows:
            if name == "brand":
                found = [_display_name(brand_name)] if brand_name else []
            else:
                stored = ((values or {}).get(name) or {}).get("value") if isinstance(values, dict) else None
                found = stored if isinstance(stored, list) else ([stored] if stored not in (None, "") else [])
                shown = []
                for v in found:
                    text = display_spec_value(v, labels) if name != "class" else _sentence_case(str(v))
                    if row is not None and row.unit and (row.data_type or "") == "numeric":
                        text = f"{text} {row.unit}"
                    shown.append(text)
                found = shown
            for value in found:
                counts.setdefault(str(value), set()).add(str(family_code))
        if len(counts) >= (1 if key else 2):
            label = "Brand" if name == "brand" else "Product type" if name == "class" else (
                row.label if row is not None and row.label else _sentence_case(name)
            )
            return {
                "key": name,
                "label": label,
                "rows": sorted(
                    ({"value": value, "count": len(families)} for value, families in counts.items()),
                    key=lambda r: (-r["count"], r["value"]),
                ),
            }
    return None


def resolve_product_set(
    db: Session,
    *,
    require: dict,
    specs: list[dict] | None = None,
    free_terms: list[str] | None = None,
    scope_terms: list[str] | None = None,
    limit: int | None = None,
    product_ids: list[str] | None = None,
    brand: str | None = None,
    access_levels: list[str] | None = None,
    stock_policy: Any = None,
    prefer_weighted_brand: bool = False,
    brand_is_default: bool = False,
    breakdown_over: int | None = None,
    breakdown_key: str | None = None,
) -> dict:
    """(described set) ∩ (require legs), with an honest count.

    Fix round 9 on PR #833 (owner, 28 Sep 2026: "always break it down", "no cap"):
    ``breakdown_over`` is the longest set one reply lists; a set longer than it carries
    ``breakdown``, its members counted by the next attribute that splits them
    (`_set_breakdown`), and a set that qualifies nothing carries ``members``, the
    described products without the require legs, so the miss can say what matched.
    ``breakdown_key`` names the key to break the set down by (a value the registry does
    not know is said back beside the values the set does hold).

    The described set is the UNION of ``product_ids`` (ids LOOKUP already
    matched by name or code prefix) and the ``class`` / ``product_type`` /
    ``brand`` membership `filter_specs` derives from ``specs`` / ``free_terms``
    / ``scope_terms`` - attribute-first asks, work item C3, AC-1306/1308. Either
    alone is enough to describe the set; when both are given a product only
    needs ONE of them. ``brand`` scopes the WHOLE set on top of that union
    (D3): it reads `Product.brand_id` live, not the spec-derived echo, so it is
    correct even when a product's brand changed since its spec row was last
    derived.

    ``scope_terms`` (C2 repair) is membership-ONLY - a caller-derived class word
    ("tap" out of "which tap has cert") that must scope the set but must NEVER
    also drive `search_specs` ranking: a product that qualifies purely through
    `product_ids` and carries no `ProductSpecifications` row at all (routine for
    a LOOKUP-only match) would otherwise be silently evicted by the ranker's own
    evidence floor the moment ANY free term made `rank_by_words` true, even
    though the term named nothing about that specific product's attributes.
    ``free_terms`` keeps its old, dual role (membership AND ranking) for a
    caller that supplies real descriptive text of its own.

    Returns ``{candidates, qualifying_total, truncated, unrecognized_terms,
    require}`` - candidates are ranker-shaped (stage 2 runs `search_specs` over
    the qualifying ids only), ``require`` is the echo with ``attachment_type``
    as-resolved.

    ``qualifying_total`` counts DISTINCT VARIANT FAMILIES, not rows: five
    finishes of one sink are one product to the person asking, and that is also
    exactly what the ranker's display collapse shows. A row count would say "80
    faucets" where 40 models exist.

    ``access_levels`` (SEC-S1/AC-1334): the caller's own tier NAMES, threaded
    into every leg (only `_leg_promotion` reads them today) so a tier the
    contact cannot see never counts toward `qualifying_total` nor names its
    product. `None` is no restriction - the caller is the resolve endpoint
    itself, which always passes its own `payload.access_levels`.
    """
    unknown = sorted(set(require) - set(REQUIRE_LEGS))
    if unknown:
        raise AppException(
            status_code=422,
            message=f"Unknown require key(s): {', '.join(unknown)}",
            code="UNKNOWN_REQUIRE_KEY",
        )

    # W1 (owner hand test round 2, turns 5/7/9): a brand word inside the parser's class
    # word ("sorento wash basin") is a BRAND, never a spec value that defines membership
    # on its own; and a class noun at the tail of a longer word ("wall hung basin") is
    # still the class. Both read here, so the recount of a carried set gets them too.
    brand, scope_terms = _bind_brand_words(db, brand, scope_terms)
    scope_terms = _split_class_tail(db, scope_terms)

    scoping_terms = [*(free_terms or []), *(scope_terms or [])]
    # The two kinds stay APART here (hand pass 12 R8): a scope term's own registry
    # bindings define membership, a free term's value-key binding stays boost-only.
    # Folding them into one list is what left "close couple wc" scoping nothing but
    # the class, so a set answer spanned wall-hung and s-trap water closets.
    verdict = filter_specs(
        db,
        specs=specs,
        free_terms=list(free_terms or []),
        scope_terms=list(scope_terms or []),
    )
    unrecognized = list(verdict["unrecognized_terms"])

    require_echo: dict[str, Any] = {}
    legs: list[ColumnElement] = []
    for key, value in require.items():
        if value in (None, False):
            continue
        try:
            # `stock_policy` is the stock leg's alone: the asking contact's own
            # locations (see `_leg_stock`).
            extra = {"stock_policy": stock_policy} if key == "stock" else {}
            clause = REQUIRE_LEGS[key](db, value, access_levels=access_levels, **extra)
        except _UnrecognizedLabel as miss:
            unrecognized.append(miss.label)
            require_echo[key] = miss.label
            # A predicate the CRM could not read qualifies NOTHING - an honest
            # zero plus the unrecognized label is a clarify on the n8n side; a
            # leg silently skipped would be a wrong count presented as truth.
            return {
                "candidates": [],
                "qualifying_total": 0,
                "truncated": False,
                "unrecognized_terms": unrecognized,
                "require": require_echo | {k: v for k, v in require.items() if k != key},
                **miss.extra,
            }
        require_echo[key] = getattr(clause, "_resolved_display", value)
        legs.append(clause)

    # A free term whose every content word is generic ("item", "product",
    # "anything") named NOTHING about what the customer means - "no description
    # given", not "described something unreadable" (AC-1302's honest zero stays
    # for a REAL word like "water tap" that still binds nothing).
    described_given = (
        any(t and t.strip() and not is_generic_free_term(t) for t in scoping_terms)
        or bool(specs)
    )
    # What the caller has to RANK against, not merely what defines membership.
    # `specs`-only calls (structural bindings, no customer words) fall to the
    # deterministic listing below - `search_specs` drops any candidate with no
    # positive evidence, which would silently evict a product that only
    # qualified through `product_ids`, the OTHER half of the union.
    rank_by_words = bool([t for t in (free_terms or []) if t and t.strip()])
    if verdict["clause"] is None and described_given and not product_ids:
        # Words were given and none named a class/product_type/brand, and there
        # is no id set either: the described set is undefined. Answering the
        # predicate over the WHOLE catalogue would be an answer to a question
        # nobody asked.
        # Fix round 10 on PR #833 (owner, 28 Sep 2026: "we don't match 100% isit? i was
        # thinking to need exact match though", "for #833 yeah exact only"): a word
        # that names no product type is said back as it was typed ("Couldn't find:
        # water tap basin (product type)"), never matched to the nearest class label
        # (the retired `_nearest_class_labels`, a close-spelling guess) nor answered with the
        # catalogue's most common ones (`_common_class_labels`, retired with it).
        return {
            "candidates": [],
            "qualifying_total": 0,
            "truncated": False,
            "unrecognized_terms": unrecognized,
            "require": require_echo,
            "class_labels": verdict["class_labels"],
        }

    parent = aliased(Product)
    family = func.coalesce(parent.product_code, Product.product_code)

    def _base(query, *, with_legs: bool = True):
        query = (
            query.select_from(Product)
            .outerjoin(parent, parent.id == Product.variant_of_id)
            .filter(Product.is_active.is_(True), *(legs if with_legs else []))
        )
        described: list[ColumnElement] = []
        if product_ids:
            described.append(Product.id.in_(list(product_ids)))
        if verdict["clause"] is not None:
            described.append(
                exists().where(
                    ProductSpecifications.product_id == Product.id, verdict["clause"]
                )
            )
        if described:
            query = query.filter(described[0] if len(described) == 1 else or_(*described))
        if brand:
            query = query.join(Brand, Brand.id == Product.brand_id).filter(
                func.lower(Brand.brand_name) == brand.strip().lower()
            )
        return query

    # R1 (owner ruling on PR #833, 27 Sep 2026: "weights as brand preference instead of
    # switch"): a customer who names no brand gets the highest weighted brand the set
    # reaches (`Brand.chatbot_weight`, Master Data > Brands), and the other brands' counts
    # in weight order, then by count. No weighted brand in the set leaves the set whole; a
    # brand the customer named always wins.
    other_brands: list[dict[str, Any]] = []
    set_brands: list[dict[str, Any]] = []
    if not brand:
        per_brand = (
            _base(db.query(Brand.brand_name, Brand.chatbot_weight, func.count(func.distinct(family))))
            .join(Brand, Brand.id == Product.brand_id)
            .group_by(Brand.brand_name, Brand.chatbot_weight)
            .all()
        )
        # One entry per spelled name: two rows of one brand name (two companies) count
        # together and rank by the higher weight.
        counts: dict[str, int] = {}
        weights: dict[str, float] = {}
        for name, weight, n in per_brand:
            if not n:
                continue
            counts[name] = counts.get(name, 0) + int(n)
            weights[name] = max(weights.get(name, 0.0), float(weight or 0))
        ranked = sorted(counts, key=lambda name: (-weights[name], -counts[name], name))
        # Fix round 9 on PR #833: no brand named means every brand. The set's brands are
        # still returned (never said in the reply), so a brand named next narrows this set
        # (`turn_runtime.with_brand_from_offer`).
        if len(ranked) > 1:
            set_brands = [{"brand": _display_name(name), "count": counts[name]} for name in ranked]
        if prefer_weighted_brand and ranked and weights[ranked[0]] > 0:
            brand = ranked[0]
            brand_is_default = True
            other_brands = [{"brand": _display_name(name), "count": counts[name]} for name in ranked[1:]]

    qualifying_total = _base(db.query(func.count(func.distinct(family)))).scalar() or 0

    candidates: list[dict] = []
    if qualifying_total:
        qualifying_ids = [row[0] for row in _base(db.query(Product.id)).all()]
        if rank_by_words:
            # Stage 2: rank INSIDE the qualifying set. Floor 0 on purpose - the
            # floor exists to stop confident nonsense, and membership has
            # already established these products answer the question. Dropped
            # numeric entries re-enter here as boosts, so "1000mm" still orders
            # the shortlist even though it never defined it.
            found = search_specs(
                db,
                specs=specs,
                free_terms=free_terms,
                product_ids=qualifying_ids,
                limit=limit,
                floor=0.0,
            )
            candidates = found["candidates"]
        else:
            # require-only ("what products have certs"): nothing to rank BY,
            # so the shortlist orders by HOW MANY stated bindings a candidate
            # matched (R32/AC-1357) - `order_by`/`distinct(family)` here is
            # ONLY what postgres's `DISTINCT ON` needs to pick one row per
            # family, not the final display order, which is computed below
            # once `matched_specs` is known. No SQL `.limit()`: the cap is
            # applied AFTER sorting by match count, or a product that matched
            # every binding could be cut before it was ever compared.
            rows = (
                _base(
                    db.query(
                        Product.id,
                        Product.product_code,
                        Product.product_name,
                        family.label("family"),
                    )
                )
                .order_by(family, Product.product_code)
                .distinct(family)
                .all()
            )
            # The spec block for the rows actually shown, in ONE query. A row that
            # reached the customer through a predicate rather than a description
            # is still a product they will ask questions about, and answering
            # "here are five with certs" with no values to tell them apart is the
            # same dead end `display.specifications` exists to close.
            spec_rows = (
                db.query(ProductSpecifications)
                .filter(ProductSpecifications.product_id.in_([str(r[0]) for r in rows]))
                .all()
                if rows
                else []
            )
            spec_by_product = {str(row.product_id): row for row in spec_rows}
            candidates = []
            for product_id, code, name, _family in rows:
                spec_row = spec_by_product.get(str(product_id))
                values = spec_row.values if spec_row is not None else None
                candidates.append(
                    {
                        "product_id": str(product_id),
                        "product_code": code,
                        "summary": name,
                        "class": ((values or {}).get("class") or {}).get("value"),
                        # None, never {}: nothing was recorded for this product, and
                        # an empty block would read as "recorded, and empty".
                        "specifications": values_only(values) if values is not None else None,
                        # R30/AC-1355: this arm never runs `search_specs` (nothing
                        # to rank BY), so it is the only source of `matched_specs`
                        # for its own rows - a bare `[]` here is what silenced the
                        # Match line ("_Matched on: ...") for every require-only
                        # answer. Every BINDING whose value the product's own spec
                        # row satisfies - `specs` itself PLUS `verdict["class_labels"]`
                        # (a scope-term-derived class, "water closet", never
                        # reaches `specs` at all - `filter_specs` binds it straight
                        # into `membership["class"]` off `scope_terms`/`free_terms`,
                        # the same class-implication `search_specs` computes for its
                        # OWN `implied_classes`, mirrored here for the arm that never
                        # calls it).
                        "matched_specs": sorted(
                            _bound_spec_matches(
                                values,
                                list(specs or [])
                                + [{"key": "class", "value": label} for label in verdict["class_labels"]],
                            )
                        ),
                        # No customer words were scored here, so nothing was
                        # preferred either. Present so the shape matches a ranked row.
                        "preferred_specs": [],
                        "score": 0.0,
                        "is_discontinued": False,
                    }
                )
            # R32/AC-1357: more-matched first, product_code the tiebreak among
            # equal counts - `matched_specs` was only just computed above, so
            # this is the earliest point the true display order is knowable.
            candidates.sort(key=lambda c: (-len(c["matched_specs"]), c["product_code"]))
            candidates = candidates[: (limit or 15)]

    # E2/AC-1316: the described set's own class label(s), for the header's noun
    # (`answer.set_noun_for`). Unioned with what the qualifying candidates
    # THEMSELVES carry, not `verdict["class_labels"]` alone - a set scoped
    # purely through `product_ids` (LOOKUP matched "tap" against a product
    # CODE, never through `filter_specs`' own membership binding) still has a
    # real class the header should say, and the candidates already carry it.
    class_labels = set(verdict["class_labels"])
    for row in candidates:
        value = row.get("class")
        if value:
            class_labels.add(str(value))
    if not class_labels:
        # R3 (console fix round 2): a described set whose class derivation
        # reads nothing (a bidet's own class comes from the description's
        # TRAILING noun - "SPRAY", not "BIDET" - so it stays unmapped) still
        # has a real noun to say: the candidates' own `product_type`
        # ("bidet"), never the generic "products" this would otherwise fall
        # back to. Only tried when class named NOTHING at all, so a real
        # class always wins over this fallback.
        for row in candidates:
            # REV-S5: a local named `specs` here shadowed the function's own
            # `specs: list[dict] | None` parameter for the rest of the loop body.
            row_specs = row.get("specifications") or {}
            value = row_specs.get("product_type") if isinstance(row_specs, dict) else None
            if value:
                class_labels.add(str(value))

    # R29/AC-1354: a SCHEME-narrowed certificate leg surfaces only THAT
    # scheme's own certificate ids - present only on the scheme form, the
    # same convention `schemes_on_file` follows, since a bare leg has no
    # scheme to narrow by (a product certified under both PPS and WCM must
    # never have its WCM file rendered for a PPS question). Scoped to the
    # CANDIDATES actually shown, never the whole qualifying family - a certificate the caller
    # never sees a product for is not one it needs to fetch either. Same
    # explicit same-company predicate as `_leg_certificate` itself (the
    # `do_orm_execute` listener's `with_loader_criteria` does not reliably
    # reach every join shape here either), NULL-shared arm included.
    #
    # Owner hand test of rounds 4 to 6 on PR #833, item 7 ("any kitchen tap has cert" came
    # back as Product Photos rows): the BARE leg surfaces its certificate ids too, so the
    # attachments call narrows to the certificate files and never lists a product's photos
    # under a certificate question.
    certificate_ids: list[str] | None = None
    cert_require = require_echo.get("certificate")
    if cert_require and candidates:
        candidate_ids = [row["product_id"] for row in candidates]
        cert_filters = [
            CertificateProduct.product_id.in_(candidate_ids),
            or_(Certificate.company_id.is_(None), Certificate.company_id == Product.company_id),
            Certificate.status == "active",
        ]
        if isinstance(cert_require, dict) and cert_require.get("scheme"):
            cert_filters.append(func.lower(Certificate.scheme) == str(cert_require["scheme"]).lower())
        cert_rows = (
            db.query(Certificate.id)
            .join(CertificateProduct, CertificateProduct.certificate_id == Certificate.id)
            .join(Product, Product.id == CertificateProduct.product_id)
            .filter(*cert_filters)
            .distinct()
            .all()
        )
        certificate_ids = sorted({str(row[0]) for row in cert_rows})[:_CERTIFICATE_ID_CAP]

    outcome: dict[str, Any] = {
        "candidates": candidates,
        "qualifying_total": int(qualifying_total),
        "truncated": int(qualifying_total) > len(candidates),
        "unrecognized_terms": unrecognized,
        "require": require_echo,
        "class_labels": sorted(class_labels),
    }
    # W1: the brand the set is scoped to, as the brands table spells it.
    if brand:
        outcome["brand"] = brand
    if brand and brand_is_default:
        outcome["brand_default"] = True
    if other_brands:
        outcome["other_brands"] = other_brands
    elif set_brands:
        outcome["set_brands"] = set_brands
    # R4 (round 4 on PR #833): a set that qualifies nothing says what it looked for and
    # how many qualify in the key's other values.
    if not qualifying_total and not product_ids:
        near = _near_miss(db, membership=verdict.get("membership") or {}, legs=legs, brand=brand)
        if near:
            outcome["near_miss"] = near
    membership = verdict.get("membership") or {}
    if breakdown_key or (breakdown_over is not None and int(qualifying_total) > breakdown_over):
        broken = _set_breakdown(
            db, _base(db.query(Product.id.label("pid"), family.label("family"))), membership=membership, brand=brand, key=breakdown_key
        )
        if broken:
            outcome["breakdown"] = broken
    if breakdown_over is not None and not qualifying_total and legs and not breakdown_key:
        # Fix round 9: the described products without the legs - "Here's what you want"
        # names them, then "But no incoming matched these", as the product-code miss does.
        members_total = int(_base(db.query(func.count(func.distinct(family))), with_legs=False).scalar() or 0)
        if members_total:
            members: dict[str, Any] = {"total": members_total}
            if members_total > breakdown_over:
                broken = _set_breakdown(
                    db, _base(db.query(Product.id.label("pid"), family.label("family")), with_legs=False), membership=membership, brand=brand
                )
                if broken:
                    members["breakdown"] = broken
            else:
                codes = (
                    _base(db.query(family.label("family"), Product.product_code), with_legs=False)
                    .order_by(family, Product.product_code)
                    .distinct(family)
                    .all()
                )
                members["codes"] = sorted({str(f) for f, _code in codes})
            outcome["members"] = members
    # W2: what was identified, for the header. Present only when something was.
    description = describe_set(
        db, brand=brand, membership=verdict.get("membership") or {}
    )
    if description:
        outcome["description"] = description
    # W4: the set's own description, exactly as counted, so a page of it replays the
    # SAME set (`turn_runtime.page_the_set`) instead of re-reading the parser's words.
    outcome["set_specs"] = [
        {"key": key, "value": value, **({"grounded": True} if isinstance(value, (int, float)) else {})}
        for key, values in (verdict.get("membership") or {}).items()
        for value in values
    ]
    # W3: a readable lead for every row the fetch will render.
    labels = row_labels(db, candidates)
    if labels:
        outcome["row_labels"] = labels
    if certificate_ids is not None:
        outcome["certificate_ids"] = certificate_ids
    return outcome
