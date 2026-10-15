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
from app.services.product_compatibility_service import KIND_SEAT_COVER, KINDS

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


#: The entity keys only `as_linked_products` sets (the fit line reads them).
LINK_FIELDS = ("linked_from", "linked_as", "linked_kind")


def without_link_fields(verdict: dict[str, Any]) -> dict[str, Any]:
    """`verdict` with the link fields dropped from every entity: a parser that echoes (or a message
    that crafts) them must never print a fit line, so only the resolution sets them (AC-18)."""
    entities = verdict.get("entities") if isinstance(verdict, dict) else None
    if not any(isinstance(e, dict) and any(k in e for k in LINK_FIELDS) for e in jsc.array(entities)):
        return verdict
    return {
        **verdict,
        "entities": [
            {k: v for k, v in e.items() if k not in LINK_FIELDS} if isinstance(e, dict) else e
            for e in jsc.array(entities)
        ],
    }


def fit_kinds(parse_output: dict[str, Any]) -> list[str | None]:
    """The link kind of every `fits` filter the parse carries, in order, one per kind: a key of
    `KINDS`, or None (the message names no particular kind; an unknown word is none). The parse
    names the kind, never the message's own words (CHAT-FIT-LINE AC-7)."""
    find = parse_output.get("find") if isinstance(parse_output, dict) else None
    out: list[str | None] = []
    for item in jsc.array(find.get("filters") if isinstance(find, dict) else None):
        if not isinstance(item, dict) or item.get("key") != "fits":
            continue
        kind = item.get("kind")
        kind = kind if isinstance(kind, str) and kind in KINDS else None
        if kind not in out:
            out.append(kind)
    return out


def split_relation(relation: str | None) -> tuple[str, str | None]:
    """`focus.relation` as (kind, side). A seat cover relation is the bare side ("covers" /
    "wcs"), as stored before kinds existed; another kind is `"<kind>:<side>"`."""
    if relation and ":" in relation:
        kind, _, side = relation.partition(":")
        return (kind, side) if kind in KINDS else (KIND_SEAT_COVER, side)
    return KIND_SEAT_COVER, relation


def relation_of(kind: str, side: str) -> str:
    return side if kind == KIND_SEAT_COVER else f"{kind}:{side}"


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


#: The most linked codes a model code may answer with (crew audit: one model's 17 WC SKUs on dev
#: fit 2 covers; the limit is on the answer, not on how many SKUs the model has).
MAX_FAMILY = 8


def _linked_codes_holding(db: Any, token: str, kinds: Any = (KIND_SEAT_COVER,)) -> list[Any]:
    """The products on a link of `kinds` whose model segment is, or ends in, `token`; [] when
    none, or when they link to more than `MAX_FAMILY` codes (no one model's answer)."""
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
            ProductCompatibility.kind.in_(kinds)
            & or_(ProductCompatibility.wc_product_id == Product.id, ProductCompatibility.seat_cover_product_id == Product.id),
        )
        .filter(_norm_sql(Product.product_code).like(f"%{escaped}%", escape="\\"))
        .distinct()
        .all()
    )
    # The token names the model: the code's first segment is it, or ends in it ("8152" ->
    # SRTWC8152-BL), never a shorter stem of another model ("SRTWC83" is not SRTWC8383).
    rows = [r for r in rows if normalize_code(r.product_code).split("-")[0].endswith(token)]
    from app.services.product_compatibility_service import accessories_for, hosts_for

    ids = [str(r.id) for r in rows]
    answer = {
        p.product_code
        for kind in kinds
        for found in (accessories_for(db, ids, kind), hosts_for(db, ids, kind))
        for ps in found.values()
        for p in ps
    }
    return [] if len(answer) > MAX_FAMILY else rows


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
    the code (#1601 card rule 6). `carried_relation` (`focus.relation`, "covers" or "wcs", or "<kind>:<side>" for a kind other than seat cover)
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
    from app.services.product_compatibility_service import accessories_for, hosts_for

    # CHAT-FIT-LINE: the kind(s) asked come from the parse. One filter with no kind (the message
    # names no particular kind) is every kind the code holds (AC-3b); a carried ask with no kind of
    # its own keeps the carried one (a relation stored before kinds read as seat cover, AC-6).
    parsed = fit_kinds(verdict)
    carried_kind, carried_side = split_relation(carried_relation)
    if said:
        every = None in parsed or not parsed
        kinds = list(KINDS) if every else [k for k in parsed if k]
    else:
        every = False
        kinds = [k for k in parsed if k] or [carried_kind]
    side_ok = {"covers": said or carried_side != "wcs", "wcs": said or carried_side != "covers"}

    wanted = {normalize_code(entity_code(entities[i])) for i in anchors}
    # The typed code against the catalogue's codes, both normalised (dashes, spaces, case):
    # data against data. Read on the turn's company-scoped session, as every product read is.
    # AC-22: a product the chatbot may not answer with is no anchor either (the rule every resolver
    # tier applies); a hidden code is read as typed, never as a link, a line or a claim.
    from app.services.entity_resolver import _chat_visible_product_ids

    def _visible(found: list[Any]) -> list[Any]:
        ok = _chat_visible_product_ids(db, {str(r.id) for r in found})
        return [r for r in found if str(r.id) in ok]

    rows = _visible(db.query(Product).filter(_norm_sql(Product.product_code).in_(wanted)).all())
    by_code: dict[str, list[Any]] = {}
    for row in rows:
        by_code.setdefault(normalize_code(row.product_code), []).append(row)
    if said:
        # Owner, 13 Oct ("8152 seat cover stock"): a typed code that is no product's is read as
        # the linked SKUs whose code holds it, codes against codes and links only (the
        # containment fallback #1601's find lane used). More than a model's SKUs is no reading.
        for token in wanted - set(by_code):
            family = _visible(_linked_codes_holding(db, token, kinds))
            if family:
                by_code[token] = family
    rows = [r for group in by_code.values() for r in group]
    ids = [str(r.id) for r in rows]
    # kind -> side ("covers": the accessories of a host, "wcs": the hosts of an accessory) -> reads.
    # AC-24: the parsed kind picks the links of a HOST (a forward ask); a code on the ACCESSORY end
    # follows its own links, whichever kind the parse guessed ("which cabinet fits <mirror>").
    reads = {
        kind: {
            "covers": accessories_for(db, ids, kind) if side_ok["covers"] and kind in kinds else {},
            "wcs": hosts_for(db, ids, kind) if side_ok["wcs"] else {},
        }
        for kind in KINDS
    }

    # AC-19: a linked product the chatbot may not answer with is never named (the same rule every
    # resolver tier applies, `chat_searchable_products()` and the contact's brand scope).
    visible = _chat_visible_product_ids(
        db, {str(o.id) for by_side in reads.values() for found in by_side.values() for others in found.values() for o in others}
    )

    replaced: dict[int, list[tuple[str, str, str]]] = {}  # (code, kind, side) per anchor
    unlinked: list[Any] = []  # the products named, on no link
    unknown: list[str] = []  # the codes named that are no product's, as typed
    for i in anchors:
        products = by_code.get(normalize_code(entity_code(entities[i])), [])
        linked: list[tuple[str, str, str]] = []
        hidden = False
        for p in products:
            for kind in KINDS:
                for side, found in reads[kind].items():
                    for other in found.get(str(p.id), []):
                        if str(other.id) not in visible:
                            hidden = True
                        elif other.product_code and all(other.product_code != c for c, _k, _s in linked):
                            linked.append((other.product_code, kind, side))
        if linked:
            # Code order, the same on every database: `accessories_for` orders by the column's
            # collation, and en_US puts SRTSCBD333A-UF before SRTSCBD333-UF where C does not.
            replaced[i] = sorted(linked, key=lambda t: normalize_code(t[0]))
        elif hidden:
            unknown.append(entity_code(entities[i]))  # links that are all hidden: as typed
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
        accessory_of = {kind: product_find._accessories(db, unlinked, kind) for kind in kinds}
        lines: list[str] = []
        for p in unlinked:
            # An accessory of the kind has no host on record, any other product no accessory. With
            # no kind named, one line: the first kind the product is an accessory of, else the
            # first kind (a seat cover, as before kinds existed).
            held = [k for k in kinds if str(p.id) in accessory_of[k]]
            for kind in (held[:1] or kinds[:1]) if every else kinds:
                template = product_find.no_host_line(kind) if str(p.id) in accessory_of[kind] else product_find.none_line(kind)
                line = template.format(code=p.product_code)
                if line not in lines:
                    lines.append(line)
        return verdict, lines
    out: list[Any] = []
    for i, entity in enumerate(entities):
        if i not in replaced:
            out.append(entity)
            continue
        # The customer typed these codes: every other field the entity carries (a quantity,
        # its confidence) rides on each.
        out += [
            {**entity, "raw": code, "canonical_code": code, "current_message": True,
             "linked_from": entity_code(entity), "linked_as": side, "linked_kind": kind}
            for code, kind, side in replaced[i]
        ]
    linked = {**verdict, "entities": out}
    fits_item = items.get("fits") or {}
    relation_word = jsc.js_string(fits_item.get("value") or "").strip().casefold()
    if relation_word:
        # Crew audit: "<WC> seat cover files" came back with the relation's own word ("seat
        # cover", the parser's fits value) also tagged as a file type (Installation Guide). Two of
        # the parser's own fields name the same word; as the relation it names no file type.
        linked["entities"] = [
            e for e in linked["entities"]
            if not (
                isinstance(e, dict)
                and e.get("hint") == "attachment_type"
                and jsc.js_string(e.get("raw") or "").strip().casefold() == relation_word
            )
        ]
    if "file_word" in fits_item and not jsc.js_string(fits_item.get("file_word") or "").strip():
        # The parser says THIS message asks for no file (crew v94 check 2: v94 read "seat cover" as
        # an Installation Guide / "which kind of file?" ask). The relation's word is no file type,
        # so the turn is the bare linked codes: no attachment type of this message, and a file
        # domain is left to the engine's own continuation (null), as a bare code's is.
        linked["entities"] = [
            e for e in linked["entities"]
            if not (isinstance(e, dict) and e.get("hint") == "attachment_type" and e.get("current_message") is not False)
        ]
        if linked.get("domain_hint") == "product_attachment":
            linked["domain_hint"] = None
            linked["intent_hint"] = None
    return linked, []


def fit_lines(entities: list[Any], localizer: Any = None) -> list[str]:
    """CHAT-FIT-LINE: the lines that open a reply reached through a link, one per anchor and kind,
    in the order the answer lists the codes. Read from the resolution (`linked_from` /
    `linked_as` / `linked_kind` on the entities `as_linked_products` made), never from the
    message. Forward (the accessory side listed): `Mirror for <host> is <code>.` or `Mirror for
    <host>: <a>, <b>`; reverse: `<code> fits <host>.` or `<code> fits these cabinets: <a>, <b>`.
    With a `localizer` each line is its catalogued sentence filled directly (the templates are
    `DIRECT_ONLY`: running text is never matched against them)."""

    def say(template: str, **values: str) -> str:
        return localizer.fill(template, **values) if localizer is not None else template.format(**values)

    groups: dict[tuple[str, str, str], list[str]] = {}
    for e in entities:
        if not isinstance(e, dict) or not e.get("linked_from") or e.get("linked_kind") not in KINDS:
            continue
        code = entity_code(e)
        codes = groups.setdefault((str(e["linked_from"]), e["linked_kind"], str(e.get("linked_as"))), [])
        if code and code not in codes:
            codes.append(code)
    lines: list[str] = []
    for (anchor, kind, side), codes in groups.items():
        if not codes:
            continue
        if side == "wcs":
            line = (
                say(product_find.FIT_ONE_REVERSE, code=anchor, host=codes[0])
                if len(codes) == 1
                else say(product_find.fit_many_reverse(kind), code=anchor, codes=", ".join(codes))
            )
        else:
            line = (
                say(product_find.fit_one_forward(kind), code=anchor, linked=codes[0])
                if len(codes) == 1
                else say(product_find.fit_many_forward(kind), code=anchor, codes=", ".join(codes))
            )
        lines.append(line)
    return lines
