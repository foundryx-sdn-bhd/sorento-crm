"""The low stock report's filters, settled before any run (LOWSTOCK-FILTER-ASK, LOWSTOCK-SEMANTIC).

`documentation/plans/chatbot/lowstock-semantic-behaviour-card.md`. Owner, 4 Oct 2026:
"remove the rules entirely, this is hard coded". Nothing here reads the message. The
parser's `low_stock` key (`chatbot_parser_prompt.LOW_STOCK_FILTERS_ADDENDUM`) says which
words are the categories, brands and suppliers and how the report is grouped; this module
only RESOLVES those words against the master data and asks when one is missing or does
not resolve:

* product CATEGORY is the one required field: the parser's category words, narrowed by
  its brand words through `product_categories.brand_hint`; a brand alone is every
  category of that brand; `all_categories` is the whole book; nothing -> asked;
* SUPPLIER is optional: the parser's supplier names against the supplier master, a
  numbered pick when one names several, said and asked once when it names none (a second
  miss runs with no supplier filter and says so, crew ruling Q3);
* GROUPING is optional: the parser's `group_by`; a grouping the workbook cannot split by
  (brand, warehouse) is asked as supplier / category / both / none (crew ruling Q2).

What it settles goes to the route as `categories` (codes), `suppliers` (names) and
`split`, applied to the WORKBOOK of the usual whole-book run, and back to the engine as
the FRAME (`route_filters`), persisted on `focus.low_stock` so a refinement the parser
reads as the same ask ("taiyang only", "by supplier": `domain_in_message` false) narrows
the report just shown instead of asking again.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot import required_fields as rf
from app.services.chatbot.lanes.business import services as business_services

ASK_NAME = "low_stock_report"
SUPPLIER_GRANT = "purchase_orders.supplier"

QUESTION = 'Which product category? Reply with a category (e.g. water tap) or "all".'
SUPPLIER_QUESTION = 'Which supplier? Reply with a supplier name or "all".'
GROUPING_QUESTION = "How should the low stock report be grouped?"
GROUPING_PICK = "I can group the low stock report by supplier, by category, or both. Which one? Reply with a number:"
CANCELLED = "Low stock report cancelled."
GIVE_UP = "I still can't place '{word}'. Ask for the low stock report again with a category or \"all\"."

SPLIT_LABELS = {
    "none": "none",
    "supplier": "supplier",
    "category": "category",
    "supplier_category": "supplier x category",
}
#: The parser's `low_stock.group_by` vocabulary (`head/parser.py` schema enum).
GROUP_BY = frozenset({"supplier", "category", "supplier_category", "brand", "warehouse", "none"})
_GROUPING_OPTIONS = (
    ("supplier", "Supplier"),
    ("category", "Category"),
    ("supplier_category", "Supplier x category"),
    ("none", "No grouping"),
)
#: Security S2 (kept from #1445): bound what one parse can make the lane resolve.
MAX_WORDS = 6

#: The engine's own key for the persisted frame (`required_fields.ENGINE_KEYS`).
FRAME_KEY = "low_stock_frame"


# --------------------------------------------------------------------------- #
# Resolving against the master data
# --------------------------------------------------------------------------- #


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
    nothing (reviewer should-fix 1, #1445)."""
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


def _word_run(needle: str, haystack: str) -> bool:
    want, have = needle.split(), haystack.split()
    return bool(want) and any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


def resolve_supplier(db: Any, word: str, _extras: dict[str, Any]) -> rf.Resolved:
    """A supplier name the parser placed, against the active supplier master (crew ruling
    Q5 (a), #1445): an exact name or code wins alone; otherwise every active supplier
    whose name holds the word as a whole-word run, several -> a numbered pick."""
    if db is None:
        return rf.Resolved("unknown")
    want = " ".join(word.split()).casefold()
    if not want:
        return rf.Resolved("unknown")
    rows = _supplier_rows(db)
    exact = [name for low, name, code in rows if want in (low, code)]
    found = list(dict.fromkeys(exact or [name for low, name, _code in rows if _word_run(want, low)]))
    if not found:
        return rf.Resolved("unknown")
    if len(found) == 1:
        return rf.Resolved("ok", value=found, label=found[0])
    return rf.Resolved("ambiguous", options=tuple(([n], n) for n in found))


def _grouping_options(include_supplier: bool) -> list[tuple[str, str]]:
    return [(v, label) for v, label in _GROUPING_OPTIONS if include_supplier or "supplier" not in v]


def resolve_grouping(_db: Any, word: str, extras: dict[str, Any]) -> rf.Resolved:
    """The parser's `group_by` value (or, answering the grouping pick, an option's own
    label). A split the workbook has is taken; one it has not (brand, warehouse) is the
    supplier / category / both / none pick (crew ruling Q2)."""
    options = _grouping_options(extras.get("include_supplier") is not False)
    want = " ".join((word or "").split()).casefold()
    for value, label in options:
        if want in (value, label.casefold()):
            return rf.Resolved("ok", value=value, label=label)
    if want in GROUP_BY:
        return rf.Resolved("ambiguous", options=tuple((v, label) for v, label in options))
    return rf.Resolved("unknown")


CATEGORY = rf.FieldSpec(name="category", noun="category", question=QUESTION, resolve=resolve_category)
SUPPLIER = rf.FieldSpec(
    name="supplier", noun="supplier", question=SUPPLIER_QUESTION, resolve=resolve_supplier,
    required=False, ask_unknown=True,
)
GROUPING = rf.FieldSpec(
    name="grouping", noun="grouping", question=GROUPING_QUESTION, resolve=resolve_grouping,
    required=False, pick_head=GROUPING_PICK,
)

#: The config (owner ruling Q2, #1445): category required, supplier and grouping taken
#: when the parser gives them. Making the supplier required is `required=True` above.
LOW_STOCK_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(CATEGORY, SUPPLIER, GROUPING),
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
# The parser's reading
# --------------------------------------------------------------------------- #


def reading(verdict: dict[str, Any]) -> dict[str, Any]:
    """The parser's `low_stock` key, normalised: absent (an older prompt version) reads
    as nothing placed. Lists are bounded (`MAX_WORDS`)."""
    raw = verdict.get("low_stock")
    raw = raw if isinstance(raw, dict) else {}

    def words(key: str) -> list[str]:
        items = raw.get(key) if isinstance(raw.get(key), list) else []
        out = [" ".join(w.split()) for w in items if isinstance(w, str) and w.strip()]
        return list(dict.fromkeys(out))[:MAX_WORDS]

    group = raw.get("group_by")
    return {
        "categories": words("categories"),
        "all_categories": raw.get("all_categories") is True,
        "brands": words("brands"),
        "suppliers": words("suppliers"),
        "group_by": group if group in GROUP_BY else None,
    }


def _placed(read: dict[str, Any]) -> set[str]:
    return {w.casefold() for key in ("categories", "brands", "suppliers") for w in read[key]}


def take_entities(verdict: dict[str, Any], frame: dict[str, Any] | None = None) -> dict[str, Any]:
    """Engine seam, a FRESH low stock ask. The entity list keeps only what scopes the RUN:
    a warehouse, and a product the parser did NOT also place in `low_stock` (two readings
    of the same word: the structured field wins). Category, brand and every other entity
    go, because the shared resolver reads them as promotions and ends the turn in a miss
    before the lane (owner hand test, 3 Oct 2026, turn 4a90dd1d). The persisted frame of
    the last settled report rides along for a refinement (`FRAME_KEY`)."""
    if verdict.get("intent_hint") != ASK_NAME:
        return verdict
    out = dict(verdict)
    if isinstance(frame, dict) and frame:
        out[FRAME_KEY] = dict(frame)
    if verdict.get("required_ask"):
        return out
    placed = _placed(reading(verdict))
    kept = []
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict):
            continue
        hint = str(e.get("hint") or "").strip().lower()
        raw = " ".join(str(e.get("raw") or "").split()).casefold()
        if e.get("current_message") is False:
            kept.append(e)
        elif hint == "warehouse" or (hint == "product" and raw not in placed):
            kept.append(e)
    out["entities"] = kept
    return out


# --------------------------------------------------------------------------- #
# The lane's call
# --------------------------------------------------------------------------- #


def _all_of(db: Any, words: list[str], spec: rf.FieldSpec, extras: dict[str, Any]) -> rf.Resolved:
    """Every word resolved, values joined; the first that does not resolve is returned as
    it is (an ambiguous one to pick, an unknown one carrying the word to say back)."""
    values: list[Any] = []
    labels: list[str] = []
    for word in words:
        got = spec.resolve(db, word, extras)
        if got.status == "ambiguous":
            return got
        if got.status != "ok":
            return rf.Resolved("unknown", label=word)
        values += [v for v in got.value if v not in values]
        labels.append(got.label)
    return rf.Resolved("ok", value=values, label=", ".join(labels))


def _group_word(group: str | None, include_supplier: bool) -> str | None:
    """A supplier grouping for a contact without the supplier key is not taken (the
    column is hidden for them, #1445): supplier -> none, supplier x category -> category."""
    if not group or include_supplier:
        return group
    return {"supplier": "none", "supplier_category": "category"}.get(group, group)


def _category(db: Any, read: dict[str, Any], brands: list[str], extras: dict[str, Any]) -> Any:
    if read["all_categories"]:
        return rf.ALL
    if read["categories"]:
        return _all_of(db, read["categories"], CATEGORY, extras)
    if brands:
        return brand_categories(db, brands)
    return None


def _from_frame(db: Any, frame: dict[str, Any], read: dict[str, Any], brands: list[str],
                include_supplier: bool) -> dict[str, Any]:
    """A refinement of the report just shown: every field the message did not name is the
    frame's. A brand alone narrows the frame's categories rather than replacing them."""
    given: dict[str, Any] = {}
    category = frame.get("category") if isinstance(frame.get("category"), dict) else None
    if category and not read["all_categories"] and not read["categories"]:
        value, label = category.get("value"), str(category.get("label") or "")
        if brands and isinstance(value, list):
            codes = _codes([r for r in _category_rows(db) if r[1] in value], brands)
            words = str(frame.get("category_words") or "")
            given["category"] = _ok(codes, _said(words, brands) if words else " ".join(brands)) if codes else None
        elif brands and value == rf.ALL:
            given["category"] = brand_categories(db, brands)
        elif not brands:
            given["category"] = rf.ALL if value == rf.ALL else rf.Resolved("ok", value=value, label=label)
    supplier = frame.get("supplier") if isinstance(frame.get("supplier"), dict) else None
    if include_supplier and supplier and not read["suppliers"] and isinstance(supplier.get("value"), list):
        given["supplier"] = rf.Resolved("ok", value=supplier["value"], label=str(supplier.get("label") or ""))
    if not read["group_by"] and frame.get("grouping"):
        given["grouping"] = _group_word(str(frame["grouping"]), include_supplier)
    return {k: v for k, v in given.items() if v is not None}


def _given(db: Any, read: dict[str, Any], brands: list[str], extras: dict[str, Any],
           include_supplier: bool) -> dict[str, Any]:
    given: dict[str, Any] = {}
    category = _category(db, read, brands, extras)
    if category is not None:
        given["category"] = category
    if include_supplier and read["suppliers"]:
        given["supplier"] = _all_of(db, read["suppliers"], SUPPLIER, extras)
    group = _group_word(read["group_by"], include_supplier)
    if group:
        given["grouping"] = group
    return given


def _answer(db: Any, slot: dict[str, Any], parse_output: dict[str, Any], read: dict[str, Any],
            extras: dict[str, Any]) -> tuple[Any, bool]:
    """The reply to the open question: the parser's declared answer first (a pick, "all",
    "cancel", the field's own words), else the bare reply resolved as the answer to the
    question asked (crew ruling Q4: "1", "all", "water closet")."""
    answer = parse_output.get("required_ask_answer")
    answer = answer if isinstance(answer, dict) else {}
    mode = answer.get("mode")
    if mode == "cancel":
        return None, True
    options = list(slot.get("options") or [])
    picked = [p for p in (answer.get("picked") or []) if isinstance(p, int) and not isinstance(p, bool)]
    if mode == "pick" and options and picked and 1 <= picked[0] <= len(options):
        value, label = options[picked[0] - 1]
        return rf.Resolved("ok", value=value, label=label), False
    if mode == "all":
        return rf.ALL, False
    asking = slot.get("asking")
    include_supplier = extras.get("include_supplier") is True
    brands = list(extras.get("brands") or [])
    if asking == "category":
        named = known_brands(db, read["brands"])
        got = _category(db, read, list(dict.fromkeys([*brands, *named])), {**extras, "brands": [*brands, *named]})
        if got is not None:
            return got, False
    elif asking == "supplier" and read["suppliers"]:
        return _all_of(db, read["suppliers"], SUPPLIER, extras), False
    elif asking == "grouping" and read["group_by"]:
        return _group_word(read["group_by"], include_supplier), False
    return str(parse_output.get("required_ask_reply") or ""), False


def settle(db: Any, parse_output: dict[str, Any], *, include_supplier: bool) -> rf.Outcome:
    """Every field settled, or the one line to send instead of running."""
    read = reading(parse_output)
    slot = parse_output.get("required_ask")
    slot = slot if isinstance(slot, dict) and slot.get("ask") == ASK_NAME else None
    if slot is not None:
        # Security S1 (#1445): the permission is the LIVE one, never the slot's copy (a key
        # revoked between the question and the answer stops the supplier words too).
        extras = {**dict(slot.get("extras") or {}), "include_supplier": include_supplier}
        values = {k: v for k, v in (slot.get("values") or {}).items() if include_supplier or k != "supplier"}
        carried = dict(extras.get("carry") or {})
        if not include_supplier:
            carried.pop("suppliers", None)
            carried["group_by"] = _group_word(carried.get("group_by"), False)
        slot = {**slot, "values": values, "extras": extras}
        if not include_supplier and slot.get("asking") == "supplier":
            slot = {**slot, "asking": None}
            return rf.collect(db, LOW_STOCK_ASK, slot=slot, given=_given(
                db, {**reading({}), **carried}, [], extras, include_supplier))
        reply, cancel = _answer(db, slot, parse_output, read, extras)
        # The other fields: what this reply names, else what the first message named.
        merged = {**reading({}), **carried, **{k: v for k, v in read.items() if v}}
        given = _given(db, {**merged, "categories": [], "all_categories": False}, [], extras, include_supplier)
        if slot.get("asking") == "category":
            # How a later refinement names the category back ("Cabana water tap").
            words = read["categories"] or ([reply] if isinstance(reply, str) and reply != rf.ALL else [])
            extras["category_words"] = " ".join(" ".join(words).split())
        return rf.collect(db, LOW_STOCK_ASK, slot=slot, reply=reply if reply is not None else "",
                          given=given, cancel=cancel, extras=extras)

    frame = parse_output.get(FRAME_KEY) if isinstance(parse_output.get(FRAME_KEY), dict) else None
    # A "brand" no category carries is not a brand (owner hand test, 3 Oct 2026, turn
    # 3dec9b68: "taiyang"); it narrows nothing.
    brands = known_brands(db, read["brands"])
    extras = {
        "brands": brands,
        "include_supplier": include_supplier,
        "category_words": " ".join(read["categories"]),
        # What the first message named, carried across the category question.
        "carry": {"suppliers": read["suppliers"], "group_by": read["group_by"]},
    }
    given = _given(db, read, brands, extras, include_supplier)
    if frame and parse_output.get("domain_in_message") is False:
        framed = _from_frame(db, frame, read, brands, include_supplier)
        if "category" in framed:
            # The message named no category of its own: the frame's, narrowed by any brand
            # it named, wins over "every category of that brand".
            given["category"] = framed.pop("category")
        for key, value in framed.items():
            given.setdefault(key, value)
        if "category_words" in frame and not read["categories"]:
            extras["category_words"] = str(frame.get("category_words") or "")
    return rf.collect(db, LOW_STOCK_ASK, given=given, extras=extras)


def route_filters(outcome: rf.Outcome) -> dict[str, Any]:
    """`categories` / `suppliers` / `split` for the route, the line said above the counts,
    and the FRAME the engine persists for a refinement."""
    category = (outcome.values.get("category") or {}).get("value")
    supplier_value = outcome.values.get("supplier") or {}
    supplier = supplier_value.get("value")
    include_supplier = outcome.extras.get("include_supplier") is True
    grouping = (outcome.values.get("grouping") or {}).get("value")
    # The LIVE permission, again: a pick offered while the supplier key was held and
    # answered after it was revoked runs downgraded, as #1445 did, never a refused split.
    grouping = _group_word(grouping if isinstance(grouping, str) else None, include_supplier)
    split = grouping if grouping in SPLIT_LABELS else "none"
    out: dict[str, Any] = {"split": split}
    if isinstance(category, list) and category:
        out["categories"] = category
    if include_supplier and isinstance(supplier, list) and supplier:
        out["suppliers"] = supplier
    category_label = str((outcome.values.get("category") or {}).get("label") or "")
    parts = [category_label if out.get("categories") and category_label else "all categories"]
    if include_supplier:
        if out.get("suppliers"):
            parts.append(f"supplier {', '.join(out['suppliers'])}")
        elif supplier_value.get("missed"):
            # Crew ruling Q3: the second miss runs with no supplier filter AND says so.
            parts.append(f"all suppliers, no supplier '{supplier_value['missed']}' found")
        else:
            parts.append("all suppliers")
    parts.append(f"by {SPLIT_LABELS[split]}" if split != "none" else "no grouping")
    out["header"] = f"Low stock report ({', '.join(parts)})"
    out["frame"] = {
        "category": dict(outcome.values["category"]) if outcome.values.get("category") else None,
        "category_words": str(outcome.extras.get("category_words") or ""),
        "supplier": dict(supplier_value) if out.get("suppliers") else None,
        "grouping": split,
    }
    return out
