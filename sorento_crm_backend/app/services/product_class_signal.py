"""Make the class and brand encoded in `category_code` machine-readable.

`product_categories.category_name` is a verbatim copy of `category_code` on all 175
live rows, so the two most valuable ranking signals in the catalog are sitting in a
string nothing reads. The codes decompose as `<BRAND>-<CLASS>`:

    SRT-KS  ->  Sorento  Kitchen Sink
    CB-FT   ->  Cabana   Tap and Fitting
    BRT-WC  ->  Bravat   Water Closet

Class matters more than any other spec key because its coverage is total: every
product has a category, whereas `dimensions_length` is populated on 14.6% of rows and
`item_type` on none. It is therefore the largest single boost in the spec-search
ranker, which is also why an unmapped code must never be guessed at: a wrong class is
the most damaging value the ranker can be handed.

Scope is deliberately the pilot class only (jayson-odoo/sorento-crm#72, the T0 tracer).
Widening is adding entries to `CLASS_SUFFIXES`, not changing this module.

Contract: documentation/plans/products/spec-search-acceptance-criteria.md
AC-T0a-01 .. AC-T0a-05.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.product import ProductCategory

# The `<BRAND>-` half of a category code. Known in full because the brand set is
# small and closed; a code whose prefix is absent here is reported, never guessed.
BRAND_PREFIXES: dict[str, str] = {
    "SRT": "Sorento",
    "CB": "Cabana",
    "M": "Mocha",
    "BRT": "Bravat",
    "IDC": "IDC",
    "IB": "Iborn",
}

# The `-<CLASS>` half. Product counts are live measurements, kept on the line because
# they are the argument for the entry existing.
#
# Absent on purpose, and each for its own reason:
#   CB    (57)  ceramic art basins AND lab sinks in one category - the majority are
#               basins but a lab sink is not one, and a wrong class is the single most
#               damaging value the ranker can be handed.
#   KA    (6)   sink drainers and bottle traps; `is_accessory` already carries them.
#   TINA / ALAN / LJM / NF / M / WT   (<25 each)  one-off Elpine and spare-part buckets.
CLASS_SUFFIXES: dict[str, str] = {
    "FT": "Tap",  # 7,120
    "BA": "Bathroom Accessory",  # 3,570
    "SH": "Shower",  # 2,112
    "WB": "Wash Basin",  # 2,076
    "FC": "Bathroom Furniture",  # 1,973
    "WC": "Water Closet",  # 1,655
    "KS": "Kitchen Sink",  # 1,148
    "JC": "Bathtub and Jacuzzi",  # 646
    "CH": "Cloth Hanger",  # 98
    "UB": "Urinal",  # 58
    "FH": "Flexible Hose",  # 22
    "GNS": "Kitchen Sink",  # 4 - granite sinks are kitchen sinks
}

# Customer language is not catalog language. Matched case-insensitively alongside the
# class label itself. Malay terms included because the corpus is bilingual.
CLASS_SYNONYMS: dict[str, list[str]] = {
    "Kitchen Sink": ["kitchen sink", "sink", "sinki", "dapur"],
    # "water tap" / "water basin": the owner's own words for the two (26 Sep 2026, "which
    # water tap got stock"). Migration 511 appends them to the categories already on file.
    "Tap": ["tap", "faucet", "mixer", "paip", "kepala paip", "water tap"],
    "Wash Basin": ["wash basin", "basin", "washbasin", "sink basin", "besen", "water basin"],
    "Water Closet": ["water closet", "wc", "toilet", "toilet bowl", "closet", "tandas"],
    "Shower": ["shower", "shower head", "rain shower", "hand shower", "pancuran"],
    "Bathroom Furniture": ["cabinet", "mirror", "vanity", "bathroom cabinet", "kabinet"],
    # The two the catalog used to file together. Each answers only to its own words, so
    # "bathtub" stops returning jacuzzis.
    "Bathtub": ["bathtub", "bath tub", "tub"],
    "Jacuzzi": ["jacuzzi", "whirlpool", "spa bath"],
    # Kept: 182 products are classed from their CATEGORY alone, which names both. They
    # genuinely could be either, and answering to both words is the truthful reading.
    "Bathtub and Jacuzzi": ["bathtub", "bath tub", "jacuzzi", "tub", "shower screen"],
    "Bathroom Accessory": [
        "bathroom accessory",
        "towel rail",
        "towel bar",
        "soap holder",
        "paper holder",
        "robe hook",
    ],
    "Cloth Hanger": ["cloth hanger", "clothes hanger", "hanger", "penyidai"],
    "Urinal": ["urinal", "urinary"],
    "Flexible Hose": ["flexible hose", "hose", "shower tube", "hos"],
}

# Categories that carry no class meaning at all. Flagged non-searchable so they cannot
# masquerade as a class once the ranker starts trusting `class_label`.
NON_SEARCHABLE_CODES: frozenset[str] = frozenset({"MISC", "PROJECT", "SRTPART", "VD"})

# Same idea, one level down: a `<BRAND>-<SUFFIX>` whose suffix is a bucket rather than
# a product class. ACC is the catalog's own accessory marker, and the other three hold
# retention sums and rows whose description literally reads "NOT USE THIS CODE".
NON_SEARCHABLE_SUFFIXES: frozenset[str] = frozenset({"ACC", "AT", "GJ", "006"})


def explain_code(category_code: str | None) -> dict[str, str | None]:
    """Why a product is, or is not, eligible for spec derivation.

    Someone troubleshooting an empty result needs to tell four silences apart: the
    class is deliberately out of pilot scope, the category carries no class meaning at
    all, the code does not parse, or everything is fine and the job simply has not run.
    Reporting a bare "no specs" for all four sends them hunting in the ranker for a
    fault that lives in the scope list.
    """
    code = (category_code or "").strip().upper()
    if not code:
        return {"reason": "no_category", "class_label": None, "brand_hint": None, "suffix": None}
    if code in NON_SEARCHABLE_CODES:
        return {
            "reason": "category_non_searchable",
            "class_label": None,
            "brand_hint": None,
            "suffix": None,
        }

    prefix, _, suffix = code.partition("-")
    if not suffix:
        return {"reason": "code_unparsed", "class_label": None, "brand_hint": None, "suffix": None}
    if suffix in NON_SEARCHABLE_SUFFIXES:
        return {
            "reason": "category_non_searchable",
            "class_label": None,
            "brand_hint": None,
            "suffix": suffix,
        }

    brand = BRAND_PREFIXES.get(prefix)
    class_label = CLASS_SUFFIXES.get(suffix)
    if class_label is None:
        return {
            "reason": "class_not_enabled",
            "class_label": None,
            "brand_hint": brand,
            "suffix": suffix,
        }
    return {
        "reason": "eligible",
        "class_label": class_label,
        "brand_hint": brand,
        "suffix": suffix,
    }


def signal_for_code(category_code: str) -> tuple[str | None, str | None] | None:
    """(class_label, brand_hint) for a category code, or None when it is unmapped.

    Returns `(None, None)` for a known-meaningless code so the caller can tell
    "deliberately blank" apart from "we do not recognise this", which is the
    difference between a clean row and one that needs a human.
    """
    code = (category_code or "").strip().upper()
    if not code:
        return None
    if code in NON_SEARCHABLE_CODES:
        return (None, None)

    prefix, _, suffix = code.partition("-")
    if not suffix:
        return None
    if suffix in NON_SEARCHABLE_SUFFIXES:
        return (None, None)

    brand = BRAND_PREFIXES.get(prefix)
    class_label = CLASS_SUFFIXES.get(suffix)
    if class_label is None:
        return None
    return (class_label, brand)


def _desired_row(code: str) -> dict | None:
    """The values this code should hold, or None when the code is unmapped."""
    signal = signal_for_code(code)
    if signal is None:
        return None
    class_label, brand_hint = signal
    return {
        "class_label": class_label,
        "brand_hint": brand_hint,
        "search_synonyms": CLASS_SYNONYMS.get(class_label or "", []),
        "is_searchable": class_label is not None,
    }


def backfill_category_signals(db: Session, *, commit: bool = False) -> dict:
    """Set the class signal on every category whose stored value is wrong.

    Set-where-mismatch, deliberately, NOT update-where-null: this backfill is expected
    to be re-run every time the map grows, and update-where-null cannot repair a row a
    previous run got wrong. Idempotent, so a re-run with nothing to fix writes nothing.

    An unmapped code is left untouched and returned in `unmapped`. Reporting it is the
    point: a category nobody has classified is invisible to spec search, and the only
    way that surfaces is if the job says so.
    """
    updated = 0
    unmapped: list[str] = []

    for row in db.query(ProductCategory).all():
        desired = _desired_row(row.category_code)
        if desired is None:
            unmapped.append(row.category_code)
            continue

        changed = False
        for field, value in desired.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            updated += 1

    db.flush()
    if commit:
        db.commit()

    return {"updated": updated, "unmapped": sorted(unmapped), "unmapped_count": len(unmapped)}


class ClassVocabulary:
    """Every word that names a class, read from the database ONCE, answered from memory.

    THREE SOURCES, because a class can be born in three places and a customer cannot be
    expected to know which:

      1. a category's `class_label` and its `search_synonyms`
      2. a class value products ACTUALLY CARRY, however it got there
      3. words staff bound to a class value in the registry

    (2) is the one that was missing, and it is the one that matters. Class used to come
    only from the category code, so these were all the same list. Class now comes from
    configured derivation rules, so anyone can mint a class the categories have never
    heard of - and "seat cover" resolved to nothing while 172 products sat there
    carrying `Seat Cover`, because the word could only be looked up in the wrong place.
    `Squatting Pan` had been unreachable the same way since the day it was added.

    4. The customer words this module itself ships (`CLASS_SYNONYMS`), for a class the
    catalogue has, consulted only when 1-3 found nothing. A database that converges
    through `create_all` never ran the migration that appended "water basin" to its
    categories, and the owner's "which water basin has stock" answered a clarify there
    (hand test round 2, W6).

    SPEC-CLASS-QUADRATIC (prod, 6 Oct 2026 20:02 MYT): `search_specs(exact=True)` resolves
    EVERY word span of a free term, and the chatbot hands it the whole message, so a
    15-line stock check resolved 3,406 terms at three statements each (10,581 statements,
    18 s inside psycopg2) and the turn overran n8n's budget. The three reads now happen
    once per snapshot; a span lookup is a dict probe. A snapshot is built per call
    (`search_specs`, `_exact_classes`, `resolve_classes_for_term`) and handed down,
    never kept on the process: rows added in the same session are seen by the next one.
    """

    __slots__ = ("_index", "_fallback")

    def __init__(self, db: Session) -> None:
        # So "toilet seat" can be taught to reach Seat Cover in the UI, without inventing
        # a category for it.
        from app.models.product_spec import ProductSpecRegistry
        from app.services.product_spec_registry import merged_synonyms

        index: dict[str, set[str]] = {}
        rows = (
            db.query(ProductCategory)
            .filter(ProductCategory.is_searchable.is_(True))
            .filter(ProductCategory.class_label.isnot(None))
            .all()
        )
        for row in rows:
            label = row.class_label or ""
            index.setdefault(label.lower(), set()).add(label)
            for synonym in row.search_synonyms or []:
                index.setdefault(str(synonym).strip().lower(), set()).add(label)

        for label in stored_class_labels(db):
            index.setdefault(label.lower(), set()).add(label)

        registry_row = db.query(ProductSpecRegistry).filter_by(spec_key="class").first()
        if registry_row is not None:
            for value, words in merged_synonyms(registry_row).items():
                for word in words:
                    index.setdefault(str(word).strip().lower(), set()).add(value)

        fallback: dict[str, set[str]] = {}
        known = {r.class_label for r in rows if r.class_label}
        for label, words in CLASS_SYNONYMS.items():
            if label in known:
                for word in words:
                    fallback.setdefault(str(word).strip().lower(), set()).add(label)

        self._index = index
        self._fallback = fallback

    def resolve(self, term: str) -> list[str]:
        """Class labels a customer's word refers to, matched on label or synonym.

        Case-insensitive and whitespace-tolerant, because this is fed raw customer text.
        Non-searchable categories can never match. Returns a sorted list rather than one
        value: a term may legitimately span classes once the map widens.
        """
        needle = (term or "").strip().lower()
        if not needle:
            return []
        found = self._index.get(needle) or self._fallback.get(needle) or ()
        return sorted(found)


def resolve_classes_for_term(db: Session, term: str) -> list[str]:
    """`ClassVocabulary(db).resolve(term)`: one fresh read of the vocabulary per call.

    The entry point every one-off caller keeps. A caller resolving many terms builds the
    snapshot once and calls `resolve` on it (`product_spec_search.search_specs`).
    """
    return ClassVocabulary(db).resolve(term)


def stored_class_labels(db: Session) -> list[str]:
    """Every class value products actually carry, however it was derived."""
    from sqlalchemy import text as sql_text

    rows = db.execute(
        sql_text(
            "SELECT DISTINCT values->'class'->>'value' AS label "
            "FROM product_specifications WHERE values ? 'class'"
        )
    ).all()
    return sorted({r[0] for r in rows if r[0]})
