"""PV2 resolver: model keys and phrases -> live records, exactly or not at all.

Contract: documentation/plans/chatbot/CONTRACT-PV2-13oct.md section 4.

Two kinds of input, two rules, no third:

  * INDEX kinds (brand, product_type, attachment_type, spec attribute, enum value): the
    model's `key` is accepted only when the PINNED index printed it AND it is live. A null
    key is never resolved from `phrase`; the phrase is logged as an unknown word and the
    caller clarifies with real candidates for the held scope.
  * NON-INDEX kinds (product, customer, warehouse): `phrase` is matched EXACTLY
    (casefold + whitespace) against code/name columns. No prefix, substring, ILIKE,
    per-word or embedding arm. The one exception is the owner-approved customer-name
    candidate list (13 Oct 23:50): when a customer name is not exact, the customers whose
    name CONTAINS the phrase become options to pick. It only builds options, it never
    resolves on its own.

Numbers are matched against data: a number with no attribute is placed on the held-class
attributes whose REAL stored range holds it (one -> exact + `inferred`, the clarify policy
decides; several -> options; none -> truthful none with the closest real rows).

Labels are display names. Index keys, customer codes and ids stay in `Option.key` /
`payload` / `ResolvedRef.key`, never in a label; product codes appear in labels only
because today's formatters already print them.
"""
from __future__ import annotations

import uuid
from typing import Any, Collection, Iterable, Literal, Mapping, Optional, Protocol, Union

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.services.chatbot.pv2.aliases import norm

# --------------------------------------------------------------------------- #
# Contract types. PV2-CORE declares the parser output in `schema.py`, PV2-CARRY the state
# in `state.py`; until those land on this branch the shapes below (verbatim from the
# contract, sections 1 and 3) stand in, so lanes meet on one declaration at integration.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised once PV2-CORE lands
    from app.services.chatbot.pv2.schema import Comparator, Entity, Op, Spec  # type: ignore
except ImportError:  # pragma: no cover - the stand-in is what runs before integration
    Op = Literal["set", "add", "remove", "clear"]
    Comparator = Literal["eq", "lt", "gt", "between", "about"]

    class Entity(BaseModel):  # type: ignore[no-redef]
        kind: str
        key: Optional[str] = None
        phrase: Optional[str] = None
        op: Op = "set"
        refers_to_held: bool = False

    class Spec(BaseModel):  # type: ignore[no-redef]
        attribute: Optional[str] = None
        value_key: Optional[str] = None
        number: Optional[float] = None
        number_to: Optional[float] = None
        unit: Optional[str] = None
        comparator: Comparator = "eq"
        op: Op = "set"
        phrase: Optional[str] = None

try:  # pragma: no cover - exercised once PV2-CARRY lands
    from app.services.chatbot.pv2.state import Held, Option, ResolvedRef, ResolvedSpec  # type: ignore
except ImportError:  # pragma: no cover
    class Option(BaseModel):  # type: ignore[no-redef]
        key: str
        label: str
        payload: dict = Field(default_factory=dict)

    class ResolvedRef(BaseModel):  # type: ignore[no-redef]
        kind: str
        key: str
        label: str
        phrase: Optional[str] = None

    class ResolvedSpec(BaseModel):  # type: ignore[no-redef]
        attribute: str
        comparator: Comparator = "eq"
        value: Union[str, float, bool]
        value_to: Optional[float] = None
        unit: Optional[str] = None

    class Held(BaseModel):  # type: ignore[no-redef]
        domain: Optional[str] = None
        refs: dict[str, list[ResolvedRef]] = Field(default_factory=dict)
        specs: list[ResolvedSpec] = Field(default_factory=list)
        attachment_type: Optional[str] = None
        period: Optional[Any] = None
        report: Optional[Any] = None
        idea_draft_id: Optional[str] = None


__all__ = [
    "CUSTOMER_CANDIDATE_CAP", "INDEX_KINDS", "DbResolver", "Entity", "Held", "Option", "Resolution",
    "ResolvedRef", "ResolvedSpec", "Resolver", "Spec", "SpecResolution", "log_unrecognised",
]


class Resolution(BaseModel):
    status: Literal["exact", "ambiguous", "none"]
    refs: list[ResolvedRef] = Field(default_factory=list)
    options: list[Option] = Field(default_factory=list)
    phrase: Optional[str] = None
    unrecognised: bool = False      # true -> chatbot_unrecognised_phrases


class SpecResolution(BaseModel):
    status: Literal["exact", "ambiguous", "none"]
    specs: list[ResolvedSpec] = Field(default_factory=list)
    options: list[Option] = Field(default_factory=list)
    phrase: Optional[str] = None
    unrecognised: bool = False
    # True when code placed a number on the only attribute whose real range holds it: the
    # clarify policy (PV2-CARRY) decides whether that is asked or taken.
    inferred: bool = False
    # Real rows nearest a number no attribute's range holds ("0 -> truthful none + closest").
    closest: list[Option] = Field(default_factory=list)


class Resolver(Protocol):
    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Resolution]: ...
    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[SpecResolution]: ...
    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Resolution: ...
    def resolve_report_spec(self, spec: Any, *, held: Held, profile: Any) -> Any: ...


#: Kinds whose identity is an index key, never a phrase.
INDEX_KINDS: frozenset[str] = frozenset({"brand", "product_type", "attachment_type"})

#: Owner 13 Oct 23:50: at most this many customer-name candidates.
CUSTOMER_CANDIDATE_CAP = 8
#: Real candidates offered when an index key is null.
INDEX_CANDIDATE_CAP = 8
#: Closest real rows offered when no attribute's range holds a number.
CLOSEST_CAP = 5


def _strip_ws(value: str) -> str:
    return "".join(str(value or "").split()).lower()


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ref_option(ref: ResolvedRef) -> Option:
    return Option(key=ref.key, label=ref.label, payload={"kind": ref.kind, "key": ref.key, "label": ref.label})


def _fmt_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


class DbResolver:
    """The live `Resolver`. Pure except for reads; it never writes (see `log_unrecognised`)."""

    def __init__(
        self,
        db: Session,
        *,
        pinned: Mapping[str, Collection[str]],
        customer_scope: Mapping[str, Any] | None = None,
    ) -> None:
        self.db = db
        self.pinned = {kind: {str(k) for k in keys} for kind, keys in (pinned or {}).items()}
        self.customer_scope = customer_scope or {}

    # ------------------------------------------------------------------ entities
    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Resolution]:
        return [self._resolve_entity(e, held) for e in ents]

    def _resolve_entity(self, ent: Entity, held: Held) -> Resolution:
        if ent.op == "clear":
            return Resolution(status="exact", phrase=ent.phrase)
        if ent.refers_to_held and not ent.key and not ent.phrase:
            return self._from_held(ent.kind, held)
        if ent.kind in INDEX_KINDS:
            return self._index_entity(ent.kind, ent.key, ent.phrase, held)
        if ent.kind == "product":
            return self._product(ent.phrase)
        if ent.kind == "customer":
            return self._customer(ent.phrase)
        if ent.kind == "warehouse":
            return self._warehouse(ent.phrase)
        # A kind this resolver does not hold: say so, never guess.
        return Resolution(status="none", phrase=ent.phrase, unrecognised=bool(ent.phrase))

    @staticmethod
    def _from_held(kind: str, held: Held) -> Resolution:
        refs = list((held.refs or {}).get(kind) or [])
        if len(refs) == 1:
            return Resolution(status="exact", refs=refs)
        if refs:
            return Resolution(status="ambiguous", options=[_ref_option(r) for r in refs])
        return Resolution(status="none")

    # ------------------------------------------------------------------ index kinds
    def _index_entity(self, kind: str, key: str | None, phrase: str | None, held: Held) -> Resolution:
        if key is None:
            options = self._index_candidates(kind, held)
            return Resolution(
                status="ambiguous" if options else "none",
                options=options,
                phrase=phrase,
                unrecognised=bool(phrase),
            )
        if key not in self.pinned.get(kind, set()):
            return Resolution(status="none", phrase=phrase or key, unrecognised=True)
        label = self._live_label(kind, key)
        if label is None:
            return Resolution(status="none", phrase=phrase or key, unrecognised=False)
        return Resolution(status="exact", refs=[ResolvedRef(kind=kind, key=key, label=label, phrase=phrase)])

    def _live_label(self, kind: str, key: str) -> str | None:
        return self._live_labels(kind, [key]).get(key)

    def _live_labels(self, kind: str, keys: Iterable[str]) -> dict[str, str]:
        """{key: display label} for the keys of `kind` that exist and are active now."""
        keys = [k for k in keys if k]
        if not keys:
            return {}
        db = self.db
        out: dict[str, str] = {}
        if kind == "brand":
            from app.models.product import Brand

            for code, name in (
                db.query(Brand.brand_code, Brand.brand_name)
                .filter(Brand.brand_code.in_(keys), Brand.is_active.is_(True))
                .all()
            ):
                out.setdefault(code, name or code)
        elif kind == "attachment_type":
            from app.models.resources import AttachmentType

            for code, name in (
                db.query(AttachmentType.code, AttachmentType.type_name).filter(AttachmentType.code.in_(keys)).all()
            ):
                out.setdefault(code, name or code)
        elif kind == "product_type":
            from app.models.base import company_scope
            from app.models.product import ProductCategory
            from app.services.product_spec_registry import (
                display_spec_value, merged_allowed_values,
            )
            from app.models.product_spec import ProductSpecRegistry

            with company_scope(db, None):
                for class_label, code, name in (
                    db.query(ProductCategory.class_label, ProductCategory.category_code, ProductCategory.category_name)
                    .filter(
                        ProductCategory.is_active.is_(True),
                        or_(ProductCategory.class_label.in_(keys), ProductCategory.category_code.in_(keys)),
                    )
                    .all()
                ):
                    if class_label in keys:
                        out.setdefault(class_label, class_label)
                    if code in keys:
                        out.setdefault(code, name or code)
            row = db.query(ProductSpecRegistry).filter(ProductSpecRegistry.spec_key == "product_type").first()
            if row is not None and row.is_active:
                allowed = {str(v) for v in merged_allowed_values(row)}
                for key in keys:
                    if key in allowed:
                        out.setdefault(key, display_spec_value(key, row.value_labels))
        return out

    def _index_candidates(self, kind: str, held: Held) -> list[Option]:
        """Real candidates for a null key, within the held scope where there is one."""
        if kind == "attachment_type":
            carried = self._attachment_types_carried(held)
            if carried is not None:
                return carried
        pinned = sorted(self.pinned.get(kind, set()))
        labels = self._live_labels(kind, pinned)
        options = [
            Option(key=k, label=labels[k], payload={"kind": kind, "key": k, "label": labels[k]})
            for k in pinned
            if k in labels
        ]
        options.sort(key=lambda o: (o.label.casefold(), o.key))
        return options[:INDEX_CANDIDATE_CAP]

    # ------------------------------------------------------------------ attachment type
    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Resolution:
        return self._index_entity("attachment_type", key, None, held)

    def _held_product_ids(self, held: Held) -> list[str] | None:
        refs = list((held.refs or {}).get("product") or [])
        if not refs:
            return None
        from app.models.product import Product

        keys = [_strip_ws(r.key) for r in refs]
        upper = [r.key.upper() for r in refs]
        rows = (
            self.db.query(Product.id)
            .filter(or_(func.lower(func.regexp_replace(Product.product_code, r"\s", "", "g")).in_(keys),
                        Product.family_code.in_(upper)))
            .all()
        )
        return [str(r[0]) for r in rows]

    def _attachment_types_carried(self, held: Held) -> list[Option] | None:
        """The attachment types the held product(s) actually carry; None when no product is held."""
        ids = self._held_product_ids(held)
        if ids is None:
            return None
        if not ids:
            return []
        from app.models.product import ProductAttachment
        from app.models.resources import Attachment, AttachmentType

        rows = (
            self.db.query(AttachmentType.code, AttachmentType.type_name)
            .join(Attachment, Attachment.attachment_type_id == AttachmentType.id)
            .join(ProductAttachment, ProductAttachment.attachment_id == Attachment.id)
            .filter(
                ProductAttachment.product_id.in_(ids),
                Attachment.is_deleted.is_(False),
                AttachmentType.code.isnot(None),
            )
            .distinct()
            .all()
        )
        pinned = self.pinned.get("attachment_type", set())
        options = [
            Option(key=code, label=name or code, payload={"kind": "attachment_type", "key": code, "label": name or code})
            for code, name in rows
            if code in pinned
        ]
        options.sort(key=lambda o: (o.label.casefold(), o.key))
        return options

    # ------------------------------------------------------------------ products
    def _product(self, phrase: str | None) -> Resolution:
        if not phrase or not _strip_ws(phrase):
            return Resolution(status="none", phrase=phrase)
        from app.models.product import Product, chat_searchable_products
        from app.services.entity_resolver import _probe_product, _product_family_order

        hits = _probe_product(self.db, [phrase]).get(phrase) or []
        codes: dict[str, str] = {}
        for hit in hits:
            codes.setdefault(hit.canonical_code, (hit.display or {}).get("product_name") or "")
        if len(codes) == 1:
            code, name = next(iter(codes.items()))
            label = f"{code} {name}".strip()
            return Resolution(status="exact", refs=[ResolvedRef(kind="product", key=code, label=label, phrase=phrase)])
        if len(codes) > 1:  # two products whose codes differ only by case/spacing
            options = [
                Option(key=c, label=f"{c} {n}".strip(), payload={"kind": "product", "key": c, "label": f"{c} {n}".strip()})
                for c, n in sorted(codes.items())
            ]
            return Resolution(status="ambiguous", options=options, phrase=phrase)

        # A model family typed whole ("SRTWC286"): the family code is EXACTLY equal, no prefix.
        family = _strip_ws(phrase).upper()
        variants = sorted(
            {
                code
                for (code,) in self.db.query(Product.product_code)
                .filter(Product.family_code == family, chat_searchable_products())
                .order_by(*_product_family_order())
                .all()
            }
        )
        if variants:
            label = f"{family} ({len(variants)} variant{'s' if len(variants) != 1 else ''})"
            return Resolution(status="exact", refs=[ResolvedRef(kind="product", key=family, label=label, phrase=phrase)])
        return Resolution(status="none", phrase=phrase)

    # ------------------------------------------------------------------ customers
    def _customer_query(self):
        from app.models.order import Customer

        q = self.db.query(Customer.id, Customer.customer_code, Customer.customer_name)
        if self.customer_scope.get("enforced"):
            ids = [str(i) for i in (self.customer_scope.get("ids") or [])]
            q = q.filter(Customer.id.in_(ids))
        return q

    @staticmethod
    def _customer_ref(cid: Any, code: str | None, name: str | None, phrase: str | None) -> ResolvedRef:
        return ResolvedRef(kind="customer", key=str(code or cid), label=str(name or "").strip(), phrase=phrase)

    def _customer(self, phrase: str | None) -> Resolution:
        if not phrase or not norm(phrase):
            return Resolution(status="none", phrase=phrase)
        from app.models.order import Customer

        by_code = (
            self._customer_query()
            .filter(func.lower(func.regexp_replace(Customer.customer_code, r"\s", "", "g")) == _strip_ws(phrase))
            .all()
        )
        exact = by_code or (
            self._customer_query()
            .filter(func.lower(func.regexp_replace(func.trim(Customer.customer_name), r"\s+", " ", "g")) == norm(phrase))
            .order_by(Customer.customer_name, Customer.id)
            .all()
        )
        if len(exact) == 1:
            cid, code, name = exact[0]
            return Resolution(status="exact", refs=[self._customer_ref(cid, code, name, phrase)])
        if exact:
            return Resolution(status="ambiguous", options=self._customer_options(exact, phrase), phrase=phrase)

        # Owner-approved (13 Oct 23:50): the ONE containing-name search. Options only.
        pattern = f"%{_like_escape(norm(phrase))}%"
        candidates = (
            self._customer_query()
            .filter(func.lower(func.regexp_replace(Customer.customer_name, r"\s+", " ", "g")).like(pattern, escape="\\"))
            .order_by(Customer.customer_name, Customer.id)
            .limit(CUSTOMER_CANDIDATE_CAP)
            .all()
        )
        if not candidates:
            return Resolution(status="none", phrase=phrase)
        return Resolution(status="ambiguous", options=self._customer_options(candidates, phrase), phrase=phrase)

    def _customer_options(self, rows: list, phrase: str | None) -> list[Option]:
        out = []
        for cid, code, name in rows:
            ref = self._customer_ref(cid, code, name, phrase)
            out.append(Option(key=ref.key, label=ref.label, payload={"kind": "customer", "key": ref.key, "label": ref.label}))
        return out

    # ------------------------------------------------------------------ warehouses
    def _warehouse(self, phrase: str | None) -> Resolution:
        if not phrase or not norm(phrase):
            return Resolution(status="none", phrase=phrase)
        from app.models.inventory import Warehouse

        rows = (
            self.db.query(Warehouse.warehouse_code, Warehouse.warehouse_name)
            .filter(
                Warehouse.is_active.is_(True),
                or_(
                    func.lower(func.regexp_replace(Warehouse.warehouse_code, r"\s", "", "g")) == _strip_ws(phrase),
                    func.lower(func.regexp_replace(func.trim(Warehouse.warehouse_name), r"\s+", " ", "g")) == norm(phrase),
                ),
            )
            .order_by(Warehouse.warehouse_code)
            .all()
        )
        refs = {code: ResolvedRef(kind="warehouse", key=code, label=name or code, phrase=phrase) for code, name in rows}
        if len(refs) == 1:
            return Resolution(status="exact", refs=list(refs.values()))
        if refs:
            return Resolution(status="ambiguous", options=[_ref_option(r) for r in refs.values()], phrase=phrase)
        return Resolution(status="none", phrase=phrase)

    # ------------------------------------------------------------------ specs
    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[SpecResolution]:
        registry = self._registry()
        classes = self._held_classes(held)
        return [self._resolve_spec(s, registry, classes) for s in specs]

    def _registry(self) -> dict[str, Any]:
        from app.services.product_spec_registry import active_registry

        return {row.spec_key: row for row in active_registry(self.db)}

    def _resolve_spec(self, spec: Spec, registry: dict[str, Any], classes: set[str]) -> SpecResolution:
        if spec.op == "clear":
            return SpecResolution(status="exact", phrase=spec.phrase)
        if spec.attribute is not None:
            if spec.attribute not in self.pinned.get("spec_attribute", set()) or spec.attribute not in registry:
                return SpecResolution(status="none", phrase=spec.phrase or spec.attribute, unrecognised=True)
            return self._spec_on(registry[spec.attribute], spec)
        if spec.number is not None:
            return self._place_number(spec, registry, classes)
        if spec.value_key is not None:
            return self._place_value(spec, registry, classes)
        return SpecResolution(status="none", phrase=spec.phrase, unrecognised=bool(spec.phrase))

    def _pinned_value(self, attribute: str, value: str) -> bool:
        values = self.pinned.get("spec_value")
        return values is None or f"{attribute}.{value}" in values

    def _spec_on(self, row: Any, spec: Spec) -> SpecResolution:
        from app.services.product_spec_registry import display_spec_value, merged_allowed_values

        if row.data_type == "enum":
            allowed = [str(v) for v in merged_allowed_values(row)]
            if spec.value_key is None:
                options = [
                    Option(
                        key=f"{row.spec_key}.{v}",
                        label=display_spec_value(v, row.value_labels),
                        payload={"kind": "spec", "attribute": row.spec_key, "comparator": "eq", "value": v},
                    )
                    for v in allowed
                    if self._pinned_value(row.spec_key, v)
                ]
                return SpecResolution(status="ambiguous" if options else "none", options=options, phrase=spec.phrase)
            if spec.value_key not in allowed or not self._pinned_value(row.spec_key, spec.value_key):
                return SpecResolution(status="none", phrase=spec.phrase or spec.value_key, unrecognised=True)
            return SpecResolution(
                status="exact",
                specs=[ResolvedSpec(attribute=row.spec_key, comparator=spec.comparator, value=spec.value_key)],
                phrase=spec.phrase,
            )
        if row.data_type == "boolean":
            value = str(spec.value_key).lower() != "false" if spec.value_key is not None else True
            return SpecResolution(
                status="exact",
                specs=[ResolvedSpec(attribute=row.spec_key, comparator="eq", value=value)],
                phrase=spec.phrase,
            )
        # numeric
        if spec.number is None:
            return SpecResolution(status="none", phrase=spec.phrase, unrecognised=bool(spec.phrase))
        number = self._to_unit(spec.number, spec.unit, row.unit)
        number_to = self._to_unit(spec.number_to, spec.unit, row.unit) if spec.number_to is not None else None
        if number is None:
            return SpecResolution(status="none", phrase=spec.phrase or spec.unit, unrecognised=True)
        return SpecResolution(
            status="exact",
            specs=[ResolvedSpec(attribute=row.spec_key, comparator=spec.comparator, value=number,
                                value_to=number_to, unit=row.unit)],
            phrase=spec.phrase,
        )

    @staticmethod
    def _to_unit(number: float | None, unit: str | None, target: str | None) -> float | None:
        """`number unit` in the attribute's own unit; None when the units cannot be related."""
        if number is None:
            return None
        if not unit or norm(unit) == norm(target):
            return float(number)
        from app.services.product_spec_search import _UNIT_TO_MM

        u = norm(unit)
        if target == "mm" and u in _UNIT_TO_MM:
            return float(number) * _UNIT_TO_MM[u]
        return None

    # ---- numbers with no attribute: matched against the held class's real data
    def _held_classes(self, held: Held) -> set[str]:
        from app.models.base import company_scope
        from app.models.product import Product, ProductCategory

        classes: set[str] = set()
        types = [r.key for r in (held.refs or {}).get("product_type") or []]
        if types:
            with company_scope(self.db, None):
                for class_label, code in (
                    self.db.query(ProductCategory.class_label, ProductCategory.category_code)
                    .filter(or_(ProductCategory.class_label.in_(types), ProductCategory.category_code.in_(types)))
                    .all()
                ):
                    if class_label:
                        classes.add(class_label)
        ids = self._held_product_ids(held)
        if ids:
            for (class_label,) in (
                self.db.query(ProductCategory.class_label)
                .select_from(Product)
                .join(ProductCategory, ProductCategory.id == Product.category_id)
                .filter(Product.id.in_(ids), ProductCategory.class_label.isnot(None))
                .distinct()
                .all()
            ):
                classes.add(class_label)
        return {c.casefold() for c in classes}

    @staticmethod
    def _applies(row: Any, classes: set[str]) -> bool:
        if not classes:
            return True
        gate = (row.applies_when or {}).get("class")
        if not gate:
            return True
        gate = gate if isinstance(gate, list) else [gate]
        return bool({str(g).casefold() for g in gate} & classes)

    _CLASS_SQL = "lower(COALESCE(ps.values->'class'->>'value', c.class_label))"

    def _range(self, attribute: str, classes: set[str]) -> tuple[float, float] | None:
        sql = (
            "SELECT min((ps.values->:a->>'value')::numeric), max((ps.values->:a->>'value')::numeric) "
            "FROM product_specifications ps JOIN products p ON p.id = ps.product_id "
            "LEFT JOIN product_categories c ON c.id = p.category_id "
            "WHERE jsonb_typeof(ps.values->:a->'value') = 'number' AND p.is_searchable IS NOT FALSE"
        )
        params: dict[str, Any] = {"a": attribute}
        if classes:
            sql += f" AND {self._CLASS_SQL} = ANY(:classes)"
            params["classes"] = sorted(classes)
        lo, hi = self.db.execute(text(sql), params).one()
        if lo is None:
            return None
        return float(lo), float(hi)

    def _closest(self, rows: list[Any], number: float, classes: set[str]) -> list[Option]:
        found: list[tuple[float, Option]] = []
        for row in rows:
            sql = (
                "SELECT p.product_code, (ps.values->:a->>'value')::numeric AS v "
                "FROM product_specifications ps JOIN products p ON p.id = ps.product_id "
                "LEFT JOIN product_categories c ON c.id = p.category_id "
                "WHERE jsonb_typeof(ps.values->:a->'value') = 'number' AND p.is_searchable IS NOT FALSE"
            )
            params: dict[str, Any] = {"a": row.spec_key, "n": number}
            if classes:
                sql += f" AND {self._CLASS_SQL} = ANY(:classes)"
                params["classes"] = sorted(classes)
            sql += " ORDER BY abs((ps.values->:a->>'value')::numeric - :n), p.product_code LIMIT 3"
            for code, value in self.db.execute(text(sql), params).all():
                unit = f" {row.unit}" if row.unit else ""
                label = f"{code} ({row.label} {_fmt_number(float(value))}{unit})"
                found.append(
                    (abs(float(value) - number),
                     Option(key=code, label=label, payload={"kind": "product", "key": code, "label": code}))
                )
        found.sort(key=lambda t: (t[0], t[1].key))
        out: list[Option] = []
        for _, opt in found:
            if opt.key not in {o.key for o in out}:
                out.append(opt)
        return out[:CLOSEST_CAP]

    def _place_number(self, spec: Spec, registry: dict[str, Any], classes: set[str]) -> SpecResolution:
        pinned = self.pinned.get("spec_attribute", set())
        rows = [
            row for key, row in sorted(registry.items())
            if row.data_type == "numeric" and key in pinned and self._applies(row, classes)
        ]
        holding: list[tuple[Any, float, float | None]] = []
        candidates: list[Any] = []
        for row in rows:
            number = self._to_unit(spec.number, spec.unit, row.unit)
            if number is None:
                continue
            candidates.append(row)
            span = self._range(row.spec_key, classes)
            if span is None:
                continue
            number_to = self._to_unit(spec.number_to, spec.unit, row.unit) if spec.number_to is not None else None
            if span[0] <= number <= span[1]:
                holding.append((row, number, number_to))
        if len(holding) == 1:
            row, number, number_to = holding[0]
            return SpecResolution(
                status="exact",
                specs=[ResolvedSpec(attribute=row.spec_key, comparator=spec.comparator, value=number,
                                    value_to=number_to, unit=row.unit)],
                phrase=spec.phrase,
                inferred=True,
            )
        if holding:
            options = [
                Option(
                    key=row.spec_key,
                    label=row.label,
                    payload={"kind": "spec", "attribute": row.spec_key, "comparator": spec.comparator,
                             "value": number, "value_to": number_to, "unit": row.unit},
                )
                for row, number, number_to in holding
            ]
            return SpecResolution(status="ambiguous", options=options, phrase=spec.phrase)
        base = self._to_unit(spec.number, spec.unit, "mm") if spec.unit else float(spec.number)
        closest = self._closest(candidates, base if base is not None else float(spec.number), classes)
        return SpecResolution(status="none", phrase=spec.phrase, closest=closest)

    def _place_value(self, spec: Spec, registry: dict[str, Any], classes: set[str]) -> SpecResolution:
        from app.services.product_spec_registry import display_spec_value, merged_allowed_values

        pinned = self.pinned.get("spec_attribute", set())
        owners = [
            row for key, row in sorted(registry.items())
            if row.data_type == "enum" and key in pinned and self._applies(row, classes)
            and spec.value_key in {str(v) for v in merged_allowed_values(row)}
            and self._pinned_value(key, spec.value_key)
        ]
        if len(owners) == 1:
            row = owners[0]
            return SpecResolution(
                status="exact",
                specs=[ResolvedSpec(attribute=row.spec_key, comparator=spec.comparator, value=spec.value_key)],
                phrase=spec.phrase,
                inferred=True,
            )
        if owners:
            options = [
                Option(
                    key=row.spec_key,
                    label=f"{row.label}: {display_spec_value(spec.value_key, row.value_labels)}",
                    payload={"kind": "spec", "attribute": row.spec_key, "comparator": "eq", "value": spec.value_key},
                )
                for row in owners
            ]
            return SpecResolution(status="ambiguous", options=options, phrase=spec.phrase)
        return SpecResolution(status="none", phrase=spec.phrase or spec.value_key, unrecognised=True)

    # ------------------------------------------------------------------ reports
    def resolve_report_spec(self, spec: Any, *, held: Held, profile: Any) -> Any:
        """PV2-REPORTS owns report resolution (`pv2/reports.py`); this only forwards."""
        from app.services.chatbot.pv2 import reports  # type: ignore[attr-defined]

        return reports.resolve_report_spec(self.db, spec, held=held, profile=profile)


# --------------------------------------------------------------------------- #
# The unknown-words log
# --------------------------------------------------------------------------- #
def log_unrecognised(
    db: Session,
    items: Iterable[tuple[str | None, str]],
    *,
    domain: str | None = None,
    contact_id: str | None = None,
    turn_id: str | None = None,
) -> int:
    """Upsert each (phrase, kind) into `chatbot_unrecognised_phrases`; returns rows touched.

    The phrase is stored normalised, so "Gambar" and " gambar " are one row whose
    `seen_count` climbs. Staff read the table to grow aliases; nothing reads it per turn.
    """
    touched = 0
    seen: set[tuple[str, str]] = set()
    for phrase, kind in items:
        n = norm(phrase)
        if not n or not kind or (n, kind) in seen:
            continue
        seen.add((n, kind))
        db.execute(
            text(
                "INSERT INTO chatbot_unrecognised_phrases "
                "(id, phrase, kind, domain, contact_id, turn_id, seen_count, first_seen_at, last_seen_at) "
                "VALUES (:id, :phrase, :kind, :domain, :contact_id, :turn_id, 1, now(), now()) "
                "ON CONFLICT (phrase, kind) DO UPDATE SET "
                "seen_count = chatbot_unrecognised_phrases.seen_count + 1, "
                "last_seen_at = now(), "
                "domain = COALESCE(EXCLUDED.domain, chatbot_unrecognised_phrases.domain), "
                "contact_id = COALESCE(EXCLUDED.contact_id, chatbot_unrecognised_phrases.contact_id), "
                "turn_id = COALESCE(EXCLUDED.turn_id, chatbot_unrecognised_phrases.turn_id)"
            ),
            {"id": str(uuid.uuid4()), "phrase": n, "kind": kind, "domain": domain,
             "contact_id": contact_id, "turn_id": turn_id},
        )
        touched += 1
    return touched
