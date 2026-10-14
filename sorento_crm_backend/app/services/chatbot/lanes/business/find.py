"""A seat cover is resolved like a product (SEAT-AS-PRODUCT, owner rule 13 Oct 2026).

"Resolving seat cover should just be like resolving product. The domain behind should depend
on what's resolved by the parser, as usual. No new lanes in answering."

The parser's `find.filters` (keys from `product_find.FILTERS`) say the message is about the
products standing in a registered relation to a code it names ("seat cover for SRTWC8122",
"WC for SRTSCBD330-UF"). `as_linked_products` is a RESOLUTION step only: each such code is
replaced by its linked codes (`product_compatibility`, #1590; the data decides the direction,
as `product_find._run_fits` reads it), and the turn runs on exactly as if the customer had typed
those codes. The domain the parser resolved (stock, ETA, files, price, nothing asked) answers
through its own tool and formatter; there is no find reply lane.

Card `documentation/plans/chatbot/CARD-seat-as-product-13oct.md` (crew answers 13 Oct):

* Q2 (b): only a code on a link is replaced; any other code the message names answers as typed.
* Q3 (a): a code that is no product's exact code (a family prefix, a typo) goes on as typed,
  to the resolver's own picker or miss.
* Q1 (a): when no code the message names has a link, the reply is one line per code
  (`NO_SEAT_COVER_LINE`), and nothing is fetched. No code named and none carried: the ask
  line. The engine says these lines itself, before any lane (`engine._run_turn`). A code
  beside them that is no product's exact code sends the turn on as typed (Q3 a).

`filter_items` / `filter_keys` stay the readers of the parse's `find` key (the resolve body's
`keep_typed_codes`, the type-word miss filter in `turn_runtime.make_tool_runner`).
"""
from __future__ import annotations

from typing import Any

from app.services import product_find
from app.services.chatbot import jsc

#: Q1 (a), wording approved by the owner (13 Oct): the reply when no named code has a link.
#: One constant, with its ms / zh in `product_find`'s labels.
NO_SEAT_COVER_LINE = product_find.FITS_NONE_LINE
#: The same line for a code the data calls a seat cover.
NO_WC_LINE = product_find.FITS_NO_WC_LINE
#: A fit ask with no code named and none carried (#1601 card rule 6).
ASK_PRODUCT_LINE = product_find.ASK_PRODUCT_LINE


def filter_items(parse_output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The registered filters the parse carries, by key, in order, first one per key (an
    unknown key is dropped: the registry is the vocabulary, never the model)."""
    find = parse_output.get("find") if isinstance(parse_output, dict) else None
    items: dict[str, dict[str, Any]] = {}
    for item in jsc.array(find.get("filters") if isinstance(find, dict) else None):
        key = item.get("key") if isinstance(item, dict) else None
        if isinstance(key, str) and key in product_find.FILTERS and key not in items:
            items[key] = item
    return items


def filter_keys(parse_output: dict[str, Any]) -> list[str]:
    return list(filter_items(parse_output))


def entity_code(entity: Any) -> str:
    """A product entity's code (canonical, else as typed); "" for anything else."""
    if not isinstance(entity, dict) or jsc.js_string(entity.get("hint") or "") != "product":
        return ""
    return jsc.js_string(entity.get("canonical_code") or entity.get("raw") or "").strip()


def _anchors(entities: list[Any]) -> list[int]:
    """Positions of the product codes the relation is about: this message's own, else the
    carried ones ("which seat cover fits it?")."""
    coded = [i for i, e in enumerate(entities) if entity_code(e)]
    own = [i for i in coded if entities[i].get("current_message") is not False]
    return own or coded


def _bare_continuation(verdict: dict[str, Any], entities: list[Any]) -> bool:
    """The parser's own reading of a continuation: no domain word in THIS message
    (`domain_in_message` false) and nothing named but product codes."""
    own = [e for e in entities if isinstance(e, dict) and e.get("current_message") is not False]
    return verdict.get("domain_in_message") is False and bool(own) and all(entity_code(e) for e in own)


#: The most linked SKUs a partial code may stand for (one model's finishes and variants).
MAX_FAMILY = 8


def _linked_codes_holding(db: Any, token: str) -> list[Any]:
    """The products on a seat-cover link whose normalised code holds `token`; [] when none, or
    when more than `MAX_FAMILY` do (too short a token names no one model)."""
    from sqlalchemy import or_

    from app.models.product import Product
    from app.models.product_compatibility import ProductCompatibility
    from app.services.product_alternative_service import _norm_sql, normalize_code

    if len(token) < 3:
        return []
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = (
        db.query(Product)
        .join(
            ProductCompatibility,
            or_(ProductCompatibility.wc_product_id == Product.id, ProductCompatibility.seat_cover_product_id == Product.id),
        )
        .filter(_norm_sql(Product.product_code).like(f"%{escaped}%", escape="\\"))
        .distinct()
        .all()
    )
    # The token names the model: the code's first segment is it, or ends in it ("8152" ->
    # SRTWC8152-BL), never a shorter stem of another model ("SRTWC83" is not SRTWC8383).
    rows = [r for r in rows if normalize_code(r.product_code).split("-")[0].endswith(token)]
    return [] if len(rows) > MAX_FAMILY else rows


def as_linked_products(
    db: Any,
    verdict: dict[str, Any],
    carried_codes: list[str] | tuple[str, ...] = (),
    *,
    carried_relation: str | None = None,
    message: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """`verdict` with every named code that sits on a `fits` link replaced by its linked codes
    (each marked `linked_from` and `linked_as`, the side listed), and the lines that are the whole reply when nothing was
    replaced (empty otherwise). Nothing else changes: the domain, the intent, every other key
    and every other entity stay the parser's.

    `carried_codes` are the conversation's product codes (`focus.products`): the subject of
    "which seat cover fits it?", whose parse names no code. With neither, the reply asks for
    the code (#1601 card rule 6). `carried_relation` (`focus.relation`, "covers" or "wcs")
    says those codes answered a fit ask, and which side they are (crew v94 check and carry
    ruling, 13 Oct; the #1626 pattern):

    * a bare new code the parser reads as a continuation repeats the fit ask in the same
      direction, even when the parse dropped `find` (a bare cover code after "seat cover for
      <WC>" is that cover, never its WCs);
    * a code of that answer is never re-anchored ("only the 333A one" narrows it);
    * a relation nothing in THIS message names (`value` null) never reaches a message with
      its own domain word, and with no fit answer carried it is no fit ask at all (live: the
      model emits a value-less `fits` on non-fit messages; every real fit ask fills `value`).
    """
    items = filter_items(verdict)
    entities = list(jsc.array(verdict.get("entities")))
    own_codes = [e for e in entities if entity_code(e) and e.get("current_message") is not False]
    echoes = [e for e in entities if entity_code(e) and e.get("current_message") is False]
    # The reply to a question about the fit answer ("photo" after "which kind of file?") names no
    # code; the parser echoes the code the conversation asked about, which may be the earlier
    # fit ask's anchor (crew v94 check 2, row 13). Through the carried relation it is that answer.
    echo_turn = bool(carried_relation and echoes and not own_codes)
    if "fits" in items:
        value = jsc.js_string(items["fits"].get("value") or "").strip()
        # The parser's own two fields contradict a fit ask when the "other side" it names is the
        # file word it names (live: "send me SRTWC8383-RL-MBL drawing" -> value "drawing").
        if value and value.casefold() == jsc.js_string(items["fits"].get("file_word") or "").strip().casefold():
            value = ""
        # The rule has the parser copy the word from THIS message; a copy the message does not
        # hold is none (live: "send me <WC> drawing" -> value "seat cover"), as grounding reads a
        # parser's words against the message (`head/grounding._as_typed`). Unknown message: kept.
        if value and message and value.casefold() not in message.casefold():
            value = ""
        said = bool(value)
    elif carried_relation and (_bare_continuation(verdict, entities) or echo_turn):
        said = False
    else:
        return verdict, []
    from app.services.product_alternative_service import normalize_code

    if not said and not carried_relation:
        # Live, v52 text + this block (13 Oct, 6 runs of the golden set): every real fit ask names
        # the other side in `value`; a `fits` with no value and no fit answer before it is the
        # model filling the schema on a non-fit message ("send me SRTWC8383-RL-MBL drawing", 3 of
        # 3), so it is no fit ask. Reverses reviewer round 4 blocker 1 on that measurement.
        return {**verdict, "find": None}, []
    anchors = _anchors(entities)
    if anchors and carried_relation:
        # A relation rejected here leaves a plain turn: `find` is cleared, so nothing
        # downstream (the resolver's typed-code flag, the type-word miss filter) reads it.
        if not said and not echo_turn and verdict.get("domain_in_message") is True:
            return {**verdict, "find": None}, []
        answered = {normalize_code(c) for c in carried_codes}
        anchors = [i for i in anchors if normalize_code(entity_code(entities[i])) not in answered]
        if not anchors:
            return {**verdict, "find": None}, []
    if not anchors:
        if not carried_codes:
            if verdict.get("domain_in_message") is True:
                # No code and a domain word of its own ("seat cover got promotion?"): no fit
                # question, so the plain turn answers it (live: `fits` 1 of 3 here).
                return {**verdict, "find": None}, []
            return verdict, [ASK_PRODUCT_LINE]
        entities += [
            {"raw": c, "hint": "product", "canonical_code": c, "current_message": False, "confident": True}
            for c in carried_codes
        ]
        verdict = {**verdict, "entities": entities}
        anchors = _anchors(entities)

    from app.models.product import Product
    from app.services.product_alternative_service import _norm_sql
    from app.services.product_compatibility_service import covers_for, wcs_for

    wanted = {normalize_code(entity_code(entities[i])) for i in anchors}
    # The typed code against the catalogue's codes, both normalised (dashes, spaces, case):
    # data against data. Read on the turn's company-scoped session, as every product read is.
    rows = db.query(Product).filter(_norm_sql(Product.product_code).in_(wanted)).all()
    by_code: dict[str, list[Any]] = {}
    for row in rows:
        by_code.setdefault(normalize_code(row.product_code), []).append(row)
    if said:
        # Owner, 13 Oct ("8152 seat cover stock"): a typed code that is no product's is read as
        # the linked SKUs whose code holds it, codes against codes and links only (the
        # containment fallback #1601's find lane used). More than a model's SKUs is no reading.
        for token in wanted - set(by_code):
            family = _linked_codes_holding(db, token)
            if family:
                by_code[token] = family
    rows = [r for group in by_code.values() for r in group]
    ids = [str(r.id) for r in rows]
    covers = covers_for(db, ids) if said or carried_relation != "wcs" else {}
    wcs = wcs_for(db, ids) if said or carried_relation != "covers" else {}

    replaced: dict[int, tuple[str, list[str]]] = {}
    unlinked: list[Any] = []  # the products named, on no link
    unknown: list[str] = []  # the codes named that are no product's, as typed
    for i in anchors:
        products = by_code.get(normalize_code(entity_code(entities[i])), [])
        linked: list[str] = []
        side = ""
        for p in products:
            for listed, others in (("covers", covers.get(str(p.id), [])), ("wcs", wcs.get(str(p.id), []))):
                for other in others:
                    if other.product_code and other.product_code not in linked:
                        linked.append(other.product_code)
                        side = side or listed
        if linked:
            # Code order, the same on every database: `covers_for` orders by the column's
            # collation, and en_US puts SRTSCBD333A-UF before SRTSCBD333-UF where C does not.
            replaced[i] = (side, sorted(linked, key=normalize_code))
        elif products:
            unlinked.append(products[0])
        else:
            unknown.append(entity_code(entities[i]))
    if not replaced:
        if carried_relation and not said:
            # A carried relation that replaced nothing ("only the 333A one") leaves a plain turn.
            return {**verdict, "find": None}, []
        if not said or not unlinked or unknown:
            # A carried, unsaid relation that links nothing in its direction is no fit ask:
            # the code answers as typed (the Q1 line is for a message that asked one).
            # Q3 (a): a named code that is no product's exact code (a family prefix, a typo)
            # is the resolver's to place, picker or miss, so the turn runs on as typed.
            return verdict, []
        seat_covers = product_find._seat_covers(db, unlinked)
        lines = [
            (NO_WC_LINE if str(p.id) in seat_covers else NO_SEAT_COVER_LINE).format(code=p.product_code)
            for p in unlinked
        ]
        return verdict, lines
    out: list[Any] = []
    for i, entity in enumerate(entities):
        if i not in replaced:
            out.append(entity)
            continue
        # The customer typed these codes: every other field the entity carries (a quantity,
        # its confidence) rides on each.
        side, codes = replaced[i]
        out += [
            {**entity, "raw": code, "canonical_code": code, "current_message": True,
             "linked_from": entity_code(entity), "linked_as": side}
            for code in codes
        ]
    linked = {**verdict, "entities": out}
    fits_item = items.get("fits") or {}
    if "file_word" in fits_item and not jsc.js_string(fits_item.get("file_word") or "").strip():
        # The parser says THIS message asks for no file (crew v94 check 2: v94 read "seat cover" as
        # an Installation Guide / "which kind of file?" ask). The relation's word is no file type,
        # so the turn is the bare linked codes: no attachment type of this message, and a file
        # domain is left to the engine's own continuation (null), as a bare code's is.
        linked["entities"] = [
            e for e in out
            if not (isinstance(e, dict) and e.get("hint") == "attachment_type" and e.get("current_message") is not False)
        ]
        if linked.get("domain_hint") == "product_attachment":
            linked["domain_hint"] = None
            linked["intent_hint"] = None
    return linked, []
