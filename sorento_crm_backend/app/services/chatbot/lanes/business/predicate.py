"""Shape B's `require` seam: a pure map off what the parser already emits.

Work item B1 (`PLAN-attribute-first-asks.md`). No message-text matching beyond the
existing `_CERT_RE` on an `attachment_type` entity's own `raw` - `derive_require` is
`derive_routing`'s sibling, not a second parser. A turn whose intent carries no leg returns `None` and behaves exactly
as it does today (AC-1322's invariant).

Contract: `documentation/plans/chatbot/attribute-first-asks-acceptance-criteria.md`
AC-1303, AC-1304.
"""
from __future__ import annotations

import re
from typing import Any

# The two cert-word tests, declared HERE since the head module that used to own them is
# retired with the rest of the post-processor. `_CERT_RE` also matches a SCHEME name
# (span, sirim, bomba, ms####, halal, ikram), which is what lets "pps cert" split into a
# bare leg plus a scheme; `_CERTIFICATE_RE` is the plain word on its own.
_CERT_RE = re.compile(r"cert|ikram|span|sirim|bomba|ms\s?[0-9]|halal", re.IGNORECASE)
_CERTIFICATE_RE = re.compile(r"cert|certificate", re.IGNORECASE)

# Intents that need nothing beyond their own name to name a leg - the customer's own
# word for each is also the leg's key, which is what `derive_predicate_words` below
# subtracts from the described set.
_BARE_LEG_BY_INTENT: dict[str, str] = {
    "check_stock": "stock",
    "check_incoming": "incoming",
    "check_promotion": "promotion",
}

# The word ITSELF that names "a certificate", in English and Malay - distinct from
# `_CERT_RE`, which also matches a SCHEME name (span, sirim, bomba, ms9001, halal,
# ikram) so `derive_routing` can route a bare scheme word to the cert team too.
# Stripping this set off the raw is what is left over is a SCHEME word (S2, AC-1303):
# "pps cert" -> scheme "pps", "watermark certificate" -> scheme "watermark", while
# "cert" alone strips to nothing and stays the bare leg.
#
# R21/AC-1345 (console pass 6): every INFLECTION of the word, not only "cert"/
# "certificate" - the head's `entity_op: reuse` hands `derive_require` the
# AttachmentType's own NAME ("Certification"), not the customer's original
# word, so "certs", "certification(s)" and any case must all read as the bare
# leg too, the same as "cert" always has.
_BARE_CERT_WORDS: frozenset[str] = frozenset(
    {"cert", "certs", "certificate", "certificates", "certification", "certifications", "sijil"}
)

# R33/AC-1358 (reviewer round 4, should-fix): word-anchored over the bare
# cert word FAMILY - `cert`/`certs` exactly, `certif`-prefixed for every
# certify/certificate/certification INFLECTION (certified, certifying,
# certificate(s), certification(s), ...), and the Malay `sijil` - never a
# bare substring search. `_CERTIFICATE_RE`/`_CERT_RE` have no word boundary
# at all, so "certainly" and "concert" (both merely CONTAIN "cert") false-
# positived as a certificate question in the no-raw fallback below, and
# `sijil` was never tried there at all (only ever checked against an
# attachment_type RAW, which this fallback by definition has none of).
_BARE_CERT_WORD_RE = re.compile(r"\b(?:certs?|certif\w*|sijil)\b", re.IGNORECASE)


# Words that describe a certificate's own PROPERTY (its validity, expiry, number),
# never a scheme: "valid cert", "cert validity", "certificate expiry", "cert no".
# Reviewer B1 on PR #833: before this they split off as scheme "valid", which no
# register holds, so the answer said "0 products" over an unfiltered list. Validity
# is flagged on every row, never filtered (D-lane), so these read as the bare leg.
_CERT_PROPERTY_WORDS: frozenset[str] = frozenset(
    {
        "valid", "validity", "invalid", "expiry", "expired", "expire", "expires",
        "expiration", "no", "no.", "number", "num", "status", "date", "copy",
        "latest", "current", "active", "still", "sah", "tamat",
    }
)


def _cert_scheme_from_raw(raw: str) -> str | None:
    """What is left of `raw` once every bare cert word and every certificate
    PROPERTY word is removed, or None when nothing is - the raw named no scheme."""
    words = [w for w in re.split(r"\s+", raw.strip()) if w]
    remainder = [
        w for w in words if w.lower() not in _BARE_CERT_WORDS and w.lower() not in _CERT_PROPERTY_WORDS
    ]
    return " ".join(remainder).strip() or None


def _attachment_type_code(parser_output: dict[str, Any], raw: str) -> str:
    """The parser code of the `attachment_type` entity that said `raw`, `raw` itself when
    it has none. ATTACH-GROUNDING-TYPO option (c) (owner, 6 Oct 2026): the document leg is
    the parser's type ("technical drawing"), never the customer's spelling ("techincal
    drawings"); grounding has already dropped any code that is not a listed type."""
    for entity in parser_output.get("entities") or []:
        if not isinstance(entity, dict) or str(entity.get("hint") or "").strip().lower() != "attachment_type":
            continue
        if str(entity.get("raw") or "").strip() == raw:
            return str(entity.get("canonical_code") or "").strip() or raw
    return raw


def _listed_require(parser_output: dict[str, Any]) -> dict[str, Any] | None:
    """CHAT-CLOSED-LISTS card R5: the leg the closed-list picks name, from grounding's tag
    alone (`head/grounding._on_closed_lists`), or None when no entity carries one. A scheme
    on file scopes the certificate ask; a certificate kind is the bare ask; a file type is
    its own leg. No word of the raw is read, so a scheme word that is not on file ("span")
    was never an entity and never becomes a filter that matches nothing (cards c04 / c19)."""
    tagged = [
        e
        for e in parser_output.get("entities") or []
        if isinstance(e, dict) and str(e.get("hint") or "").strip().lower() == "attachment_type" and e.get("listed")
    ]
    if not tagged:
        return None
    by_list: dict[str, list[str]] = {}
    for entity in tagged:
        by_list.setdefault(str(entity["listed"]), []).append(str(entity.get("canonical_code") or ""))
    if by_list.get("cert_scheme"):
        return {"certificate": {"scheme": by_list["cert_scheme"][0]}}
    if by_list.get("cert_kind"):
        return {"certificate": True}
    if by_list.get("file_type"):
        return {"attachment_type": by_list["file_type"][0]}
    return None


def _attachment_type_raws(parser_output: dict[str, Any]) -> list[str]:
    """Every `attachment_type` entity's own raw, in order, non-empty, deduped."""
    raws: list[str] = []
    for entity in parser_output.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("hint") or "").strip().lower() != "attachment_type":
            continue
        raw = str(entity.get("raw") or "").strip()
        if raw and raw not in raws:
            raws.append(raw)
    return raws


# The v3 verdict's own carrier for an attribute-first ask (turn re-architecture):
# `requested_attributes` is what the customer asked ABOUT, and for these words the
# attribute IS the leg. Read BEFORE the intent, because v3 states the attribute
# explicitly where the pre-rearch prompt could only imply it through `intent_hint`.
_LEG_BY_ATTRIBUTE_WORD: dict[str, str] = {
    "cert": "certificate",
    "certs": "certificate",
    "certificate": "certificate",
    "certificates": "certificate",
    "certification": "certificate",
    "certifications": "certificate",
    "sijil": "certificate",
    "stock": "stock",
    "stok": "stock",
    "incoming": "incoming",
    "eta": "incoming",
    "promotion": "promotion",
    "promotions": "promotion",
    "promo": "promotion",
    "promosi": "promotion",
    # Fix round 8 on PR #833: a price ask about a described set is a set answer too.
    "price": "price",
    "prices": "price",
    "list price": "price",
    "harga": "price",
}


#: The routing domains whose ask IS a leg (`turn/policy_rows.py`): the domain the parser
#: routed to, and every `switch_words` entry the registry lists for it, name that leg.
#: `master_products` is absent on purpose: its words ("spec", "dimension") ask about the
#: product, never for a figure it has.
_LEG_BY_DOMAIN: dict[str, str] = {"inventory": "stock", "incoming": "incoming", "promotion": "promotion"}

#: The intents a leg may be read into from an attribute word or the domain: the product
#: ask, no intent at all, and each leg's own. An order, sales or form ask that happens to
#: request "quantity" is never turned into a stock set.
_LEG_READABLE_INTENTS = frozenset({"", "check_product", *_BARE_LEG_BY_INTENT})


def _leg_of_word(word: str) -> str | None:
    """The leg one word names: `_LEG_BY_ATTRIBUTE_WORD`, else the registry's own switch
    word for a leg's domain ("arriving", "eta", "stok", "promosi")."""
    from app.services.chatbot.turn.policy import default_policy, domain_switch_words

    w = word.strip().lower()
    if not w:
        return None
    if w in _LEG_BY_ATTRIBUTE_WORD:
        return _LEG_BY_ATTRIBUTE_WORD[w]
    return _LEG_BY_DOMAIN.get(domain_switch_words(default_policy()).get(w, ""))


def _leg_in_words(raw: str) -> str | None:
    """The leg a phrase names through any one of its words ("in stock", "have stock",
    "stock level", "incoming stock", "still arriving"). Fix round 12 on PR #833: read by
    the word the registry knows, never by the phrase, so a new wording around a known
    word needs no new entry. "incoming stock" is the incoming leg's own label, so a
    phrase naming both says incoming."""
    legs = [leg for leg in (_leg_of_word(w) for w in re.split(r"[^\w]+", raw) if w) if leg]
    if not legs:
        return None
    return "incoming" if "incoming" in legs else legs[0]


def _require_from_attributes(parser_output: dict[str, Any]) -> dict[str, Any] | None:
    """`{leg: True}` for the first `requested_attributes` entry that names a leg.

    An attribute word the table does not know is NOT guessed into a leg: it stays a
    plain requested attribute and the ordinary answer projects it, which is what a
    spec question ("what is its width") has always done.

    A cert PHRASE ("PPS cert", "sirim certificate") is the same split an
    `attachment_type` raw gets below (S2, AC-1303): the scheme is what is left once the
    bare cert word is removed. Without it the phrase missed the one-word table, fell to
    the bare leg, and "PPS" was then stripped from the remainder as a predicate word, so
    nothing downstream could recover the scheme either.

    Fix round 12 on PR #833 (owner: "why the fix that we applied for incoming cannot work
    for stock? this is too fragile"): any other phrase names a leg through one of its
    words (`_leg_in_words`), on a product ask or the leg's own ask only.
    """
    readable = str(parser_output.get("intent_hint") or "") in _LEG_READABLE_INTENTS
    for raw in parser_output.get("requested_attributes") or []:
        if not isinstance(raw, str):
            continue
        leg = _LEG_BY_ATTRIBUTE_WORD.get(raw.strip().lower())
        if leg:
            return {leg: True}
        words = [w for w in re.split(r"\s+", raw.strip()) if w]
        if len(words) > 1 and _CERT_RE.search(raw):
            scheme = _cert_scheme_from_raw(raw)
            return {"certificate": {"scheme": scheme}} if scheme else {"certificate": True}
        leg = _leg_in_words(raw) if readable else None
        if leg:
            return {leg: True}
    return None


def derive_require(
    parser_output: dict[str, Any], *, message_text: str | None = None
) -> dict[str, Any] | None:
    """`{leg: value}` for the turn's intent, or `None` when it carries no leg.

    `check_product_attachment` splits on the FIRST `attachment_type` entity's raw:
    a cert-shaped word (`_CERT_RE`, the same regex `derive_routing` already uses for
    the cert-vs-photo team split) maps to the `certificate` leg - bare when the raw
    is only the cert word ("cert", "certificate", "sijil"), else `{"scheme": ...}`
    with the rest of the raw (S2, AC-1303: "pps cert" -> scheme "pps"). The
    function stays pure - it never touches the `certificate_scheme` lookup set,
    that normalisation happens server-side in `_leg_certificate`. Any other label
    passes through verbatim as the `attachment_type` leg, resolved server-side
    (`_leg_attachment_type` in `product_predicate_service.py`) so a new document
    class never needs a parser prompt change.

    R4/AC-1328 (console fix round 2): a SCHEME-ONLY raw ("PPS") carries no
    `_CERT_RE` word of its own - that regex names a cert BODY (cert/ikram/span/
    sirim/bomba/ms####/halal), never a bare register spelling - so this mirrors
    `derive_routing`'s `is_cert`: also certificate when the intent is
    `check_product_attachment` and `_CERTIFICATE_RE` matches `user_goal` (the
    parser's own field, read the same way `derive_routing` does) or, when that is
    absent, `message_text` (the caller's own fallback - `resolve_entity_body`
    passes `_query_text(ctx)`, the same seam every other reader of the raw
    message uses). The raw itself becomes the scheme verbatim in that case - it
    named no cert word to strip.

    R21/AC-1345 (console pass 6): the BARE-WORD check runs FIRST, before the
    `_CERT_RE` test - every word of the raw (whitespace-split, lower-cased)
    being in `_BARE_CERT_WORDS` is the bare `certificate` leg regardless of
    what `_CERT_RE` itself matches. This is what catches "sijil": that word
    names no cert BODY `_CERT_RE` recognises (it is Malay for "certificate",
    not one of the scheme-name synonyms the regex also matches), so without
    this it fell through past the cert branch entirely and reached the
    generic `attachment_type` leg. "PPS certification" still splits: it is
    not ALL bare-cert words, so it falls to the `_CERT_RE` branch below, which
    strips "certification" (now in `_BARE_CERT_WORDS`) and returns scheme
    "PPS".

    R28/AC-1353 (owner console run 10): the head can drop the attachment_type
    entity ENTIRELY - an entity whose raw resolved no `canonical_code`
    ("PPS", measured null) is normalised away before this function ever sees
    it, leaving `entities: []`. With no raw to split on, the cert question
    survives only in `user_goal` / `message_text` - the same fallback R4
    already reads for a scheme-only raw, tried here BEFORE giving up, never
    after: there is no raw left to fall through past.

    R33/AC-1358 (reviewer round 4, should-fix): this no-raw fallback matches
    `_BARE_CERT_WORD_RE`, word-anchored, never `_CERTIFICATE_RE` as a bare
    substring - "certainly, send me the drawing" and "concert hall basin
    photo" both merely CONTAIN "cert" and must not read as a certificate
    question, while "is this certified?" and the Malay "ada sijil untuk
    basin?" genuinely are one.
    """
    from_attributes = _require_from_attributes(parser_output)
    listed = _listed_require(parser_output)
    if from_attributes is not None:
        if from_attributes == {"certificate": True} and (listed is not None or parser_output.get("closed_lists")):
            return listed if listed is not None and "certificate" in listed else from_attributes
        if from_attributes == {"certificate": True}:
            # The attribute names only the bare leg; an attachment_type raw beside it
            # may still carry the scheme ("certificate" + entity "PPS cert").
            for raw in _attachment_type_raws(parser_output):
                if _CERT_RE.search(raw):
                    scheme = _cert_scheme_from_raw(raw)
                    if scheme:
                        return {"certificate": {"scheme": scheme}}
        return from_attributes

    intent = parser_output.get("intent_hint")
    if intent in _BARE_LEG_BY_INTENT:
        return {_BARE_LEG_BY_INTENT[intent]: True}

    # Fix round 12 on PR #833: the domain the parser routed a product ask to is the leg
    # too ("inventory" is stock), whatever the verb around it was.
    domain_leg = _LEG_BY_DOMAIN.get(str(parser_output.get("domain_hint") or ""))
    if domain_leg and str(intent or "") in _LEG_READABLE_INTENTS:
        return {domain_leg: True}

    if intent == "check_product_attachment":
        if listed is not None or parser_output.get("closed_lists"):
            # CHAT-CLOSED-LISTS card R3: no listed pick is asked, never read off the words.
            return listed
        raws = _attachment_type_raws(parser_output)
        if not raws:
            goal_or_message = parser_output.get("user_goal") or message_text or ""
            if _BARE_CERT_WORD_RE.search(str(goal_or_message)):
                return {"certificate": True}
            return None
        raw = raws[0]
        raw_words = [w for w in re.split(r"\s+", raw.strip()) if w]
        if raw_words and all(w.lower() in _BARE_CERT_WORDS for w in raw_words):
            return {"certificate": True}
        if _CERT_RE.search(raw):
            scheme = _cert_scheme_from_raw(raw)
            return {"certificate": {"scheme": scheme}} if scheme else {"certificate": True}
        goal_or_message = parser_output.get("user_goal") or message_text or ""
        if _CERTIFICATE_RE.search(str(goal_or_message)):
            return {"certificate": {"scheme": raw}}
        code = _attachment_type_code(parser_output, raw)
        if code.casefold() == "certificate":
            return {"certificate": True}
        return {"attachment_type": code}

    if intent == "check_product" and _describes_a_set(parser_output):
        # Fix round 9 on PR #833 (owner hand test 28 Sep: "kitchen sink 1.2mm thickness"
        # listed every kitchen sink): a product ask that names a property of the thing
        # is an ask about the described set, counted on the existing no-filter leg
        # (`_leg_price`) so the property narrows it. "described" tells the header this
        # is not a price question.
        return {"price": "described"}

    return None


def _describes_a_set(parser_output: dict[str, Any]) -> bool:
    """A specification entity this turn (`head/grounding.py`), known value or not, or a
    class or product type word (chat fix PR-A fix round 1 F3, W12 "Do you have any
    bathtub?": the reply states how many there are, not a page of looked-up codes), and no
    product named: the ask is about products described by what they are and have."""
    entities = [e for e in parser_output.get("entities") or [] if isinstance(e, dict)]
    if any(str(e.get("hint") or "").strip().lower() == "product" for e in entities):
        return False
    return any(
        str(e.get("hint") or "").strip().lower() in ("specification", "spec", "category", "product_type")
        and e.get("current_message") is not False
        for e in entities
    )


def derive_predicate_words(
    parser_output: dict[str, Any], require: dict[str, Any] | None, *, message_text: str | None = None
) -> list[str]:
    """Every word the described set must strip from the customer's own query.

    Every `attachment_type` entity's raw, plus the intent's own word for a bare leg
    (`stock`, `incoming`, `promotion` - the leg key IS the word, since nothing else
    named it). `check_product_attachment` needs no extra word: the attachment_type
    raw already covers it.

    R28/AC-1353: when `require` is the BARE `certificate` leg (`{"certificate":
    True}`) and no attachment_type raw contributed a word (there was none to
    read - `derive_require` recovered it off `message_text` instead), the word
    that actually earned the leg never left the remainder on its own. Pull the
    cert word(s) `message_text` itself carries (matching `_BARE_CERT_WORD_RE`,
    the same word-anchored pattern `derive_require` used - R33/AC-1358) so the
    described-set reader strips the bare word ITSELF, with no trailing
    punctuation glued on ("certified", never "certified?"), rather than
    reading "item pps cert" as an unrecognized phrase.
    """
    if not require:
        return []
    words = _attachment_type_raws(parser_output)
    for raw in parser_output.get("requested_attributes") or []:
        if isinstance(raw, str) and raw.strip() and raw not in words:
            words.append(raw)
    intent = parser_output.get("intent_hint")
    if intent in _BARE_LEG_BY_INTENT:
        leg = _BARE_LEG_BY_INTENT[intent]
        if leg not in words:
            words.append(leg)
    if not words and require.get("certificate") is True and message_text:
        for match in _BARE_CERT_WORD_RE.finditer(message_text):
            word = match.group()
            if word and word not in words:
                words.append(word)
    # Owner hand test of rounds 4 to 6 on PR #833, item 1 ("65502 eta" answered "I did not
    # understand "eta""): every word of the message that names the leg, or that selected
    # the leg's domain in the first place, is the predicate's word too - never a word the
    # described set has to bind.
    # A message that is nothing BUT the ask's own words ("promo", "stock") names no set,
    # and is left exactly as before.
    leg_words = _leg_words(require)
    message_words = _MESSAGE_WORD_RE.findall(message_text or "")
    if any(w.lower() not in leg_words for w in message_words):
        for word in message_words:
            if word.lower() in leg_words and word not in words:
                words.append(word)
    return words


_MESSAGE_WORD_RE = re.compile(r"[A-Za-z0-9]+")

#: The routing domain each leg answers in (`turn.policy_rows`), whose `switch_words` are
#: the customer's own words for it.
_DOMAIN_BY_LEG: dict[str, str] = {
    "incoming": "incoming",
    "stock": "inventory",
    "promotion": "promotion",
    "certificate": "product_attachment",
    "price": "master_products",
}


def _leg_words(require: dict[str, Any]) -> frozenset[str]:
    """Every single word that names one of `require`'s legs: `_LEG_BY_ATTRIBUTE_WORD`'s
    words for it, the bare cert words for a certificate leg, and the leg's domain's own
    `switch_words` (deferred import: the policy table lives with the turn engine)."""
    from app.services.chatbot.turn.policy import default_policy, domain_switch_words

    legs = set(require or {})
    words = {w for w, leg in _LEG_BY_ATTRIBUTE_WORD.items() if leg in legs}
    if "certificate" in legs:
        words |= _BARE_CERT_WORDS
    domains = {_DOMAIN_BY_LEG[leg] for leg in legs if leg in _DOMAIN_BY_LEG}
    words |= {w for w, domain in domain_switch_words(default_policy()).items() if domain in domains and " " not in w}
    return frozenset(w.lower() for w in words)


def names_a_leg(word: str) -> bool:
    """Is `word` one of the customer's own words for a leg or its domain ("eta",
    "incoming", "stock", "cert")?"""
    return str(word or "").strip().lower() in _leg_words({leg: True for leg in _DOMAIN_BY_LEG})


#: The intent each leg's own ask carries (`_BARE_LEG_BY_INTENT` read backwards, plus the
#: two document legs).
_INTENT_BY_LEG: dict[str, str] = {
    **{leg: intent for intent, leg in _BARE_LEG_BY_INTENT.items()},
    "certificate": "check_product_attachment",
    "attachment_type": "check_product_attachment",
}
_DOMAIN_OF_DOCUMENT_LEGS: dict[str, str] = {"certificate": "product_attachment", "attachment_type": "product_attachment"}


def describes_a_product_set(verdict: dict[str, Any]) -> bool:
    """The verdict describes products by what they are or have: a product type, category
    or specification entity of this message, and no product code typed. The set's own
    test, the one `resolve_gate.resolve_entity_body` sends a `require` for."""
    from app.services.chatbot.lanes.business.resolve_gate import _names_a_typed_code

    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    described = any(
        str(e.get("hint") or "").strip().lower() in ("product_type", "category", "specification", "spec")
        and e.get("current_message") is not False
        for e in entities
    )
    return described and not _names_a_typed_code(verdict)


def with_set_leg(verdict: dict[str, Any], *, message_text: str | None = None) -> dict[str, Any]:
    """Fix round 12 on PR #833 (owner, 28 Sep 2026: "why the fix that we applied for
    incoming cannot work for stock? this is too fragile, that means our solution is not
    right"). An ask that describes a product set is the attribute-first lane's, and the
    stock, incoming, promotion or document word in it chooses only which figures the rows
    show. So the leg (`derive_require`, read off the intent, the domain and every
    requested attribute's words through the registry) sets the verdict's domain and
    intent before APPLY plans the turn: the plan, the gate, the tool, the rows and the
    escalation team all follow the leg, whichever verb carried it. A product ask that
    requested "stock" listed product rows under a stock header before this.

    Unchanged: a verdict that describes no set, a leg-less set (a product ask about it,
    `{"price": "described"}`), and a verdict already on the leg's domain."""
    intent_now = str(verdict.get("intent_hint") or "")
    if intent_now not in _LEG_READABLE_INTENTS and intent_now != "check_product_attachment":
        # A low stock report, an order or a sales ask is its own answer, never a set.
        return verdict
    if not describes_a_product_set(verdict):
        return verdict
    require = derive_require(verdict, message_text=message_text) or {}
    leg = next(iter(require), None)
    if leg is None or leg == "price":
        return verdict
    domain = _DOMAIN_OF_DOCUMENT_LEGS.get(leg) or _DOMAIN_BY_LEG.get(leg)
    intent = _INTENT_BY_LEG.get(leg)
    if not domain or not intent:
        return verdict
    if verdict.get("domain_hint") == domain and verdict.get("intent_hint") == intent:
        return verdict
    return {**verdict, "domain_hint": domain, "intent_hint": intent}
