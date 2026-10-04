"""The low stock report's filters, settled before any run (LOWSTOCK-FILTER-ASK).

`documentation/plans/chatbot/lowstock-filter-ask-behaviour-card.md`, "Ruled behaviour".
The low stock report's config for the shared `required_fields` helper, and the reading of
its own words:

* product CATEGORY is the one required field: a category word ("water tap", "SRT-FT"),
  narrowed by a brand word ("Sorento") through `product_categories.brand_hint`; a brand
  alone is every category of that brand; "all" is the whole book;
* SUPPLIER is optional (never asked): a supplier name in the message, matched against the
  supplier master list, a numbered pick when it names several;
* GROUPING is never asked: "by supplier", "by category", "by supplier and category".

What it settles goes to the route as `categories` (codes), `suppliers` (names) and
`split`, applied to the WORKBOOK of the usual whole-book run (crew ruling Q1 (a)).
"""
from __future__ import annotations

import re
from typing import Any

from app.services.chatbot import required_fields as rf
from app.services.chatbot.lanes.business import services as business_services

ASK_NAME = "low_stock_report"
SUPPLIER_GRANT = "purchase_orders.supplier"

QUESTION = 'Which product category? Reply with a category (e.g. water tap) or "all".'
CANCELLED = "Low stock report cancelled."
GIVE_UP = "I still can't place '{word}'. Ask for the low stock report again with a category or \"all\"."

SPLIT_LABELS = {
    "none": "none",
    "supplier": "supplier",
    "category": "category",
    "supplier_category": "supplier x category",
}

#: Words a low stock ask is made of, never a supplier name.
_ASK_WORDS = frozenset(
    """low stock stok list report reorder reordering laporan rendah needs need what is are
    below level levels for the a an of by group grouped grouping and x please pls show me give
    send all item items product products category categories supplier suppliers in at
    on from with only just""".split()
)


def _category_rows(db: Any, ids: list[str] | None = None) -> list[tuple[str, str, str]]:
    from app.models.product import ProductCategory

    q = db.query(ProductCategory.id, ProductCategory.category_code, ProductCategory.brand_hint)
    if ids is not None:
        q = q.filter(ProductCategory.id.in_(ids))
    return [(str(i), code or "", brand or "") for i, code, brand in q.all()]


def _brand_matches(brands: list[str], hint: str, code: str) -> bool:
    """No brand matches everything; otherwise the category's `brand_hint`, its code prefix
    ("SRT") or that prefix's brand name ("Sorento") equals one of the brand words."""
    from app.services.product_class_signal import BRAND_PREFIXES

    wanted = {b.strip().casefold() for b in brands if b and b.strip()}
    if not wanted:
        return True
    prefix = code.split("-", 1)[0]
    return bool(wanted & {hint.casefold(), prefix.casefold(), BRAND_PREFIXES.get(prefix, "").casefold()})


def _codes(rows: list[tuple[str, str, str]], brands: list[str]) -> list[str]:
    return sorted({code for _i, code, hint in rows if code and _brand_matches(brands, hint, code)})


def _ok(codes: list[str], said: str | None = None) -> rf.Resolved:
    """`said` is how the reply names it back: the user's own words ("Sorento water tap"),
    never the code list (owner, 3 Oct 2026: "Low stock report (water closet, ...)")."""
    return rf.Resolved("ok", value=codes, label=said or ", ".join(codes))


def _said(word: str, brands: list[str]) -> str:
    return " ".join([*brands, " ".join(word.split())]).strip()


def resolve_category(db: Any, word: str, extras: dict[str, Any]) -> rf.Resolved:
    """A category word -> category CODES, narrowed by the carried brand. An exact code or
    name, else a whole-word run of a name, else the catalogue's class vocabulary ("water
    tap" -> class Tap -> every Tap category). Several codes for one exact word, or several
    classes, are a numbered pick."""
    if db is None:
        return rf.Resolved("unknown")
    brand = list(extras.get("brands") or [])
    said = _said(word, brand)
    matched = business_services.resolve_category_token(db, word)
    if matched:
        codes = _codes(_category_rows(db, [r[0] for r in matched]), brand)
        if not codes:
            return rf.Resolved("unknown")
        if len(codes) > 1:
            return rf.Resolved("ambiguous", options=tuple(([c], c) for c in codes))
        return _ok(codes, said)
    ids, labels = business_services.resolve_category_class(db, word)
    if ids:
        codes = _codes(_category_rows(db, ids), brand)
        return _ok(codes, said) if codes else rf.Resolved("unknown")
    if len(labels) > 1:
        options = []
        for label in labels:
            label_ids, _ = business_services.resolve_category_class(db, label)
            codes = _codes(_category_rows(db, label_ids), brand)
            if codes:
                options.append((codes, label))
        if len(options) == 1:
            return _ok(options[0][0], said)
        if options:
            return rf.Resolved("ambiguous", options=tuple(options))
    return rf.Resolved("unknown")


def known_brands(db: Any, words: list[str]) -> list[str]:
    """The brand words some category carries (`brand_hint` / code prefix); a word naming
    no category's brand ("Moen") is dropped, so it never narrows a later answer to
    nothing (reviewer should-fix 1)."""
    if db is None:
        return []
    rows = _category_rows(db)
    return [w for w in words if w.strip() and _codes(rows, [w])]


def brand_categories(db: Any, brands: list[str]) -> rf.Resolved | None:
    """A brand alone: every category of that brand (or brands). None for no brand."""
    if db is None or not brands:
        return None
    codes = _codes(_category_rows(db), brands)
    return _ok(codes, " ".join(brands)) if codes else None


def _supplier_rows(db: Any) -> list[tuple[str, str, str]]:
    from app.models.procurement import Supplier

    return [
        ((name or "").casefold(), name or "", (code or "").casefold())
        for name, code in db.query(Supplier.supplier_name, Supplier.supplier_code)
        .filter(Supplier.is_active.is_(True))
        .order_by(Supplier.supplier_name)
        .all()
    ]


def _match_supplier(rows: list[tuple[str, str, str]], word: str) -> rf.Resolved:
    want = " ".join(word.split()).casefold()
    if not want:
        return rf.Resolved("unknown")
    exact = [name for low, name, code in rows if want in (low, code)]
    found = list(dict.fromkeys(exact or [name for low, name, _code in rows if _word_run(want, low)]))
    if not found:
        return rf.Resolved("unknown")
    if len(found) == 1:
        return rf.Resolved("ok", value=found, label=found[0])
    return rf.Resolved("ambiguous", options=tuple(([n], n) for n in found))


def resolve_supplier(db: Any, word: str, _extras: dict[str, Any]) -> rf.Resolved:
    """A supplier word against the supplier master list (crew ruling Q5 (a)): an exact
    name or code wins alone; otherwise every active supplier whose name holds the word as
    a whole-word run, several -> a numbered pick."""
    if db is None:
        return rf.Resolved("unknown")
    return _match_supplier(_supplier_rows(db), word)


def _word_run(needle: str, haystack: str) -> bool:
    want, have = needle.split(), haystack.split()
    return bool(want) and any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


CATEGORY = rf.FieldSpec(name="category", noun="category", question=QUESTION, resolve=resolve_category)
SUPPLIER = rf.FieldSpec(
    name="supplier", noun="supplier", question="Which supplier?", resolve=resolve_supplier, required=False,
)

#: The config (owner ruling Q2): category required, supplier taken when given. Making the
#: supplier required is `required=True` on `SUPPLIER` above, nothing else.
LOW_STOCK_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(CATEGORY, SUPPLIER),
    reroute={
        "message_type": "business_query",
        "intent_hint": "low_stock_report",
        "domain_hint": "inventory",
        "order_status": None,
        "document": None,
        "status": None,
    },
    cancelled=CANCELLED,
    give_up=GIVE_UP,
))


# --------------------------------------------------------------------------- #
# Reading the first message
# --------------------------------------------------------------------------- #

_SPLIT_PATTERNS = (
    (re.compile(r"\b(per|each)\s+(supplier|suppliers)\b|\bsupplier[\s-]?wise\b"), "supplier"),
    (re.compile(r"\b(per|each)\s+(category|categories)\b|\bcategory[\s-]?wise\b"), "category"),
    (re.compile(r"\bby\s+(supplier|suppliers)\s*(and|x|&|/|,)\s*(category|categories)\b"), "supplier_category"),
    (re.compile(r"\bby\s+(category|categories)\s*(and|x|&|/|,)\s*(supplier|suppliers)\b"), "supplier_category"),
    (re.compile(r"\bby\s+(supplier|suppliers)\b"), "supplier"),
    (re.compile(r"\bby\s+(category|categories)\b"), "category"),
)
_ALL_CATEGORIES = re.compile(r"\ball\s+(product\s+)?(category|categories)\b")


def split_from(text: str) -> str:
    low = (text or "").casefold()
    for pattern, split in _SPLIT_PATTERNS:
        if pattern.search(low):
            return split
    return "none"


def says_all_categories(text: str) -> bool:
    return bool(_ALL_CATEGORIES.search((text or "").casefold()))


def leftover_words(text: str, used: list[str]) -> list[str]:
    """What is left of the message once the ask's own words, the grouping and every word
    the parser already placed (category, brand, location, product) are taken out: where
    a supplier name can be."""
    low = " " + (text or "").casefold() + " "
    for pattern, _split in _SPLIT_PATTERNS:
        low = pattern.sub(" ", low)
    for word in sorted((u for u in used if u), key=len, reverse=True):
        low = re.sub(r"(?<!\w)" + re.escape(word.casefold()) + r"(?!\w)", " ", low)
    return [w for w in re.findall(r"[\w&.'-]+", low) if w not in _ASK_WORDS]


#: Security S2: bound the leftover-word search (one supplier read per turn, then at most
#: MAX_LEFTOVER_WORDS x MAX_SUPPLIER_WORDS in-memory matches).
MAX_LEFTOVER_WORDS = 12
MAX_SUPPLIER_WORDS = 6


def supplier_word(db: Any, words: list[str]) -> str | None:
    """The LONGEST run of consecutive leftover words that names a supplier (reviewer
    should-fix 5: "hi can you jinbaichuan trading this month" is JINBAICHUAN TRADING, not
    one phrase that matches nothing)."""
    words = words[:MAX_LEFTOVER_WORDS]
    if db is None or not words:
        return None
    rows = _supplier_rows(db)
    for size in range(min(len(words), MAX_SUPPLIER_WORDS), 0, -1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start : start + size])
            if _match_supplier(rows, phrase).status != "unknown":
                return phrase
    return None


def _fold(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _in_text(raw: str, text: str) -> bool:
    """The word, whole, in the message: case and whitespace folded."""
    return bool(raw) and re.search(rf"(?<!\w){re.escape(_fold(raw))}(?!\w)", _fold(text)) is not None


def take_words(verdict: dict[str, Any], text: str) -> dict[str, Any]:
    """Engine seam, a FRESH low stock ask: the category and brand words come off the
    entity list (the shared resolver would read them as promotions and end the turn in a
    miss before the lane, the owner's own report) onto `low_stock_words`, with the message
    text the lane reads the supplier and grouping words from."""
    if verdict.get("intent_hint") != ASK_NAME or verdict.get("required_ask"):
        return verdict
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
    kept, words = [], {"category": [], "brand": []}
    for e in entities:
        hint = str(e.get("hint") or "").strip().lower()
        raw = " ".join(str(e.get("raw") or "").split())
        if e.get("current_message") is False:
            kept.append(e)
            continue
        if hint == "product" and raw and not any(ch.isdigit() for ch in raw):
            # LOW_STOCK_ADDENDUM tells the parser a bare token here "usually is" a
            # product, so "water tap" can arrive hinted product. A product code always
            # carries digits; a word without any is a product TYPE.
            hint = "category"
        if hint in ("category", "brand") and raw:
            # Live parser finding (3 Oct 2026): it carries an earlier turn's word as
            # current_message; only what THIS message names is taken, else dropped.
            if _in_text(raw, text):
                words[hint].append(raw)
        elif hint in ("warehouse", "product"):
            # The only entities the run is scoped by. Every other word (a supplier name,
            # "supplier" itself) stays in the message text, where `settle` reads it; as an
            # entity it reached the resolver, missed, and the miss handler answered over
            # the report (owner hand test, 3 Oct 2026, turn 4a90dd1d).
            kept.append(e)
    return {**verdict, "entities": kept, "low_stock_words": words, "low_stock_text": text}


# --------------------------------------------------------------------------- #
# The lane's call
# --------------------------------------------------------------------------- #


def settle(db: Any, parse_output: dict[str, Any], *, include_supplier: bool) -> rf.Outcome:
    """Every field settled, or the one line to send instead of running."""
    slot = parse_output.get("required_ask")
    slot = slot if isinstance(slot, dict) and slot.get("ask") == ASK_NAME else None
    if slot is not None:
        # Security S1: the permission is the LIVE one, never the slot's copy (a key
        # revoked between the question and the answer stops the supplier words too).
        extras = dict(slot.get("extras") or {})
        given = dict(extras.get("given") or {})
        split = str(extras.get("split") or "none")
        if not include_supplier:
            given.pop("supplier", None)
            split = {"supplier": "none", "supplier_category": "category"}.get(split, split)
        values = {k: v for k, v in (slot.get("values") or {}).items() if include_supplier or k != "supplier"}
        slot = {**slot, "values": values,
                "extras": {**extras, "given": given, "split": split, "include_supplier": include_supplier}}
        if not include_supplier and slot.get("asking") == "supplier":
            slot = {**slot, "asking": None}
            return rf.collect(db, LOW_STOCK_ASK, slot=slot, given=given)
        return rf.collect(
            db, LOW_STOCK_ASK, slot=slot, reply=str(parse_output.get("required_ask_reply") or ""),
            given=given,
        )

    words = parse_output.get("low_stock_words") if isinstance(parse_output.get("low_stock_words"), dict) else {}
    text = str(parse_output.get("low_stock_text") or "")
    # A "brand" no category carries stays a plain word, free to be the supplier (owner
    # hand test, 3 Oct 2026, turn 3dec9b68: "taiyang" came hinted brand).
    brands = known_brands(db, list(words.get("brand") or []))
    category_word = " ".join(words.get("category") or [])
    split = split_from(text)
    given: dict[str, Any] = {}
    if says_all_categories(text):
        given["category"] = rf.ALL
    elif category_word:
        given["category"] = category_word
    elif brands:
        given["category"] = brand_categories(db, brands)
    if include_supplier:
        word = supplier_word(db, leftover_words(text, [*(words.get("category") or []), *brands]))
        if word:
            given["supplier"] = word
    else:
        split = {"supplier": "none", "supplier_category": "category"}.get(split, split)
    extras = {
        "brands": brands,
        "split": split,
        "given": {k: v for k, v in given.items() if isinstance(v, str)},
        "include_supplier": include_supplier,
    }
    return rf.collect(db, LOW_STOCK_ASK, given=given, extras=extras)


def route_filters(outcome: rf.Outcome) -> dict[str, Any]:
    """`categories` / `suppliers` / `split` for the route, and the line said above the
    counts."""
    category = (outcome.values.get("category") or {}).get("value")
    supplier = (outcome.values.get("supplier") or {}).get("value")
    include_supplier = outcome.extras.get("include_supplier") is True
    split = str(outcome.extras.get("split") or "none")
    out: dict[str, Any] = {"split": split}
    if isinstance(category, list) and category:
        out["categories"] = category
    if include_supplier and isinstance(supplier, list) and supplier:
        out["suppliers"] = supplier
    category_label = str((outcome.values.get("category") or {}).get("label") or "")
    parts = [category_label if out.get("categories") and category_label else "all categories"]
    if include_supplier:
        parts.append(f"supplier {', '.join(out['suppliers'])}" if out.get("suppliers") else "all suppliers")
    parts.append(f"by {SPLIT_LABELS[split]}" if split in SPLIT_LABELS and split != "none" else "no grouping")
    out["header"] = f"Low stock report ({', '.join(parts)})"
    return out
