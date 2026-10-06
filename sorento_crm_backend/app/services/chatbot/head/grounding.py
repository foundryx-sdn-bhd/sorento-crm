"""Grounding: every descriptor the parser emitted, mapped onto the specification registry.

Fix round 8 on PR #833 (owner retest of round 7, 27 Sep 2026): "is it the parser is not
bounded by what the system has as a spec? like it doesn't know colour is colour one meh,
why it become document type one". The parser had entity kinds for a product, a category,
a brand and a document type, and none for a specification value, so every descriptor it
could not place went into `attachment_type`: "pink colour" came back a photo, "thickness
1.2 mm" a technical drawing, and "gunmetal basin" stayed one category token no set could
be scoped by.

The prompt now teaches a `specification` kind rendered from the registry
(`chatbot_parser_prompt.render_prompt_blocks`), and this module is the deterministic half
that runs after every parse, whatever kind the parser picked:

  * a `category` / `product_type` token keeps only what the thing IS (class and type
    words, "basin"); every other registry word in it becomes a `specification` entity
    ("gunmetal" -> Finish or colour: Gunmetal);
  * an `attachment_type` token is the parser's (ATTACH-GROUNDING-TYPO option (c), owner,
    6 Oct 2026): kept as filed when the parser was confident and its code, whole, is a
    listed document type (`DOCUMENT_TYPE_CODES` or an attachment-type name or code), and
    dropped otherwise. The customer's words are never read for it;
  * a `specification` token the parser emitted is checked against the registry: its key
    must exist and its value must be one of that key's choices (or a number, for a
    numeric key);
  * a word next to a key's own word that is none of its choices ("pink colour", "f trap")
    is an UNKNOWN value of that key, said back with the ones the registry knows.

No descriptor word list lives here (`DOCUMENT_TYPE_CODES` is the parser prompt's own
taxonomy of document codes, not a list of customer words). The registry's keys, labels,
choices, synonyms and units, the class vocabulary and the attachment-type table decide;
staff editing any of them on Product Specifications changes what grounds, with no prompt
or code change.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

#: The entity kind a grounded descriptor carries (parser prompt, `chatbot_entity_kinds`).
SPECIFICATION_HINT = "specification"
#: Older replays and fixtures spell it short.
_SPEC_HINTS = frozenset({SPECIFICATION_HINT, "spec"})
#: The kinds whose raw can hold a descriptor glued to what the thing is.
_CLASS_HINTS = frozenset({"category", "product_type"})
_ATTACHMENT_HINT = "attachment_type"

#: Registry keys that are not descriptors: the class and the type are what the thing IS
#: (they stay in the category token), and the brand is its own entity kind.
_NOT_DESCRIPTOR_KEYS = frozenset({"class", "brand", "product_type"})

#: Words a key's LABEL carries that do not name the key ("Finish or colour", "Number of
#: bowls", "Has a drainer board"). Grammar, not vocabulary.
_LABEL_GRAMMAR = frozenset(
    {"or", "of", "and", "a", "an", "the", "in", "with", "has", "is", "number", "comes", "set", "per", "type"}
)

#: A word with letters then digits stays one token ("srtwc8840", a brand "Sorento3d4d");
#: a number stands apart from its unit ("1.2mm" -> "1.2", "mm").
_TOKEN_RE = re.compile(r"[a-z]+\d[a-z0-9]*|\d+(?:\.\d+)?|[a-z]+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _same(a: str, b: str) -> bool:
    """The same word: equal once case-folded, trimmed and NFKC-normalised, a regular plural
    "s" aside ("basins" is "basin", "colours" is "colour").

    Fix round 10 on PR #833 (owner, 28 Sep 2026: "we don't match 100% isit? i was thinking
    to need exact match though", "for #833 yeah exact only"). This replaced `_near`, which
    read one typo apart as the same word ("thicnkess" as thickness, "kitchne" as kitchen,
    "gunmetl" as gunmetal): a word the catalogue does not hold is now said back as typed,
    never put right."""
    a, b = _fold(a), _fold(b)
    return a == b or _singular(a) == _singular(b)


def _fold(word: str) -> str:
    return unicodedata.normalize("NFKC", str(word or "")).strip().casefold()


@dataclass
class _Key:
    key: str
    label: str
    data_type: str
    unit: str | None
    #: phrase (words joined by one space) -> stored value, for enum, boolean and the
    #: worded numbers of a numeric key ("double bowl" -> 2).
    phrases: dict[str, Any] = field(default_factory=dict)
    #: words that name the key itself ("colour", "finish", "thickness", "thick", "trap")
    names: set[str] = field(default_factory=set)
    #: words that end two or more of its phrases ("trap" in "p trap" / "s trap"): a word
    #: in front of one is in a value position
    heads: set[str] = field(default_factory=set)
    #: the words that stand in front of each head word in a known phrase
    modifiers: dict[str, set[str]] = field(default_factory=dict)
    known: list[str] = field(default_factory=list)


@dataclass
class Vocabulary:
    keys: list[_Key]
    type_phrases: set[str]
    class_phrases: set[str]
    class_words: set[str]
    #: every word any key's choices or names use: never an unknown value of another key
    known_words: set[str]
    attachment_words: set[str]
    brand_words: set[str]


def _words(text: Any) -> list[str]:
    return [w.casefold() for w in _TOKEN_RE.findall(unicodedata.normalize("NFKC", str(text or "")))]


def load_vocabulary(db: "Session") -> Vocabulary:
    """The registry, the class vocabulary and the attachment-type list, read once per
    turn through the readers every other consumer uses (`active_registry`,
    `merged_synonyms`, `merged_allowed_values`, `display_spec_value`)."""
    from app.models.product import ProductCategory
    from app.models.resources import AttachmentType
    from app.services.product_class_signal import CLASS_SYNONYMS, stored_class_labels
    from app.services.product_spec_registry import (
        active_registry,
        display_spec_value,
        merged_allowed_values,
        merged_synonyms,
    )
    from app.services.product_spec_search import SELF_SYNONYM_KEY, brand_names

    keys: list[_Key] = []
    type_phrases: set[str] = set()
    class_words: set[str] = set()
    class_phrases: set[str] = set()
    for label, synonyms in CLASS_SYNONYMS.items():
        class_phrases.update(" ".join(_words(p)) for p in [label, *synonyms])
    class_phrases.update(" ".join(_words(label)) for label in stored_class_labels(db))
    for label, synonyms in db.query(ProductCategory.class_label, ProductCategory.search_synonyms).filter(
        ProductCategory.class_label.isnot(None)
    ):
        class_phrases.update(" ".join(_words(p)) for p in [label, *(synonyms or [])])

    for row in active_registry(db):
        synonyms = merged_synonyms(row)
        if row.spec_key == "class":
            for value, phrases in synonyms.items():
                class_phrases.update(" ".join(_words(p)) for p in [value, *phrases])
            continue
        if row.spec_key == "product_type":
            for value, phrases in synonyms.items():
                for phrase in [str(value).replace("_", " "), *phrases]:
                    if _words(phrase):
                        type_phrases.add(" ".join(_words(phrase)))
            continue
        if row.spec_key in _NOT_DESCRIPTOR_KEYS:
            continue
        data_type = (row.data_type or "").lower()
        entry = _Key(key=row.spec_key, label=row.label or row.spec_key, data_type=data_type, unit=row.unit)
        for value, phrases in synonyms.items():
            if value == SELF_SYNONYM_KEY:
                for phrase in phrases:
                    entry.names.update(_words(phrase))
                continue
            stored: Any = value
            if data_type == "boolean":
                stored = str(value).strip().lower() in {"true", "yes", "1"}
            elif data_type == "numeric":
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                stored = int(number) if number.is_integer() else number
            spoken = [*phrases] if data_type != "enum" else [str(value).replace("_", " "), *phrases]
            for phrase in spoken:
                words = _words(phrase)
                if words:
                    entry.phrases.setdefault(" ".join(words), stored)
        entry.names.update(w for w in _words(entry.label) if w not in _LABEL_GRAMMAR and not w.isdigit())
        entry.names.update(w for w in row.spec_key.split("_") if len(w) >= 4 and w not in _LABEL_GRAMMAR)
        enders: dict[str, set[Any]] = {}
        for phrase, value in entry.phrases.items():
            words = phrase.split()
            if len(words) >= 2:
                enders.setdefault(words[-1], set()).add(str(value))
        entry.heads = {w for w, values in enders.items() if len(values) >= 2}
        for phrase in entry.phrases:
            words = phrase.split()
            if len(words) >= 2 and words[-1] in entry.heads:
                entry.modifiers.setdefault(words[-1], set()).add(words[-2])
        if data_type == "enum":
            labels = dict(getattr(row, "value_labels", None) or {})
            entry.known = sorted({display_spec_value(v, labels) for v in merged_allowed_values(row)})
        keys.append(entry)

    class_phrases.discard("")
    class_words = {w for phrase in class_phrases for w in phrase.split()}
    # A word that names a class or a type is never a key's own name ("basin" in "High
    # basin", "shower" in "Shower union"): it says what the thing is.
    type_words = {w for phrase in type_phrases for w in phrase.split()}
    for entry in keys:
        entry.names -= class_words | type_words

    attachment_words: set[str] = set()
    for (name,) in db.query(AttachmentType.type_name).all():
        attachment_words.update(_singular(w) for w in _words(name))
    from app.services.chatbot.lanes.business.predicate import _BARE_CERT_WORDS

    attachment_words.update(_BARE_CERT_WORDS)
    attachment_words.update(_singular(w) for phrase in PROMPT_DOCUMENT_KINDS for w in _words(phrase))
    brand_words = {w for name in brand_names(db) for w in _words(name)}
    known_words = set(class_words) | type_words
    for entry in keys:
        known_words |= entry.names
        for phrase in entry.phrases:
            known_words.update(phrase.split())
    return Vocabulary(
        keys=keys,
        type_phrases=type_phrases,
        class_phrases=class_phrases,
        class_words=class_words,
        known_words=known_words,
        attachment_words=attachment_words,
        brand_words=brand_words,
    )


#: The document kinds the parser prompt itself tells the model an `attachment_type` may be
#: (its ATTACHMENT TYPE EXTRACTION rows and the kind's canonical codes), on the list even
#: where the attachment-type table is empty: the prompt and this check must agree on what
#: a document word is, or "photo" would be re-filed on a fresh install.
PROMPT_DOCUMENT_KINDS = (
    "photo", "image", "picture", "video", "drawing", "technical drawing", "cad", "blueprint",
    "3d model", "model", "certificate", "gambar", "foto", "imej", "lukisan teknikal",
)


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _display(key: _Key, value: Any) -> str:
    from app.services.product_spec_registry import display_spec_value

    shown = display_spec_value(value)
    return f"{shown} {key.unit}" if key.unit and key.data_type == "numeric" else shown


@dataclass
class Grounded:
    """One descriptor the registry placed (`value` set) or could not (`value` None)."""

    key: str
    label: str
    said: str
    value: Any = None
    unit: str | None = None
    known: list[str] = field(default_factory=list)
    #: the registry's own words for the value ("gunmetal" for a typed "gunmetl")
    words: str | None = None

    def entity(self, template: dict[str, Any]) -> dict[str, Any]:
        return {
            **{k: template.get(k) for k in ("current_message", "confident", "hint_confident", "quantity")},
            "raw": self.said,
            "hint": SPECIFICATION_HINT,
            "canonical_code": None if self.value is None else str(self.value),
            "spec_key": self.key,
            "spec_value": self.value,
            "spec_unit": self.unit,
            "spec_label": self.label,
            "spec_words": self.words,
            "spec_known": list(self.known) if self.value is None else [],
        }


def _covered_by_class(tokens: list[str], vocab: Vocabulary) -> set[int]:
    """Token positions that belong to what the thing IS: every class word, and every type
    phrase ("kitchen tap", "close coupled"), where the whole phrase is said. A word of a
    class phrase on its own ("bowl" of "toilet bowl") is not covered: "double bowl" is a
    bowl count."""
    covered: set[int] = set()
    for phrase in vocab.class_phrases | vocab.type_phrases:
        words = phrase.split()
        for start in range(len(tokens) - len(words) + 1):
            if tokens[start : start + len(words)] == words:
                covered.update(range(start, start + len(words)))
    return covered


def ground_words(text: str, vocab: Vocabulary, *, keep: set[int] | None = None) -> tuple[list[Grounded], set[int]]:
    """Every descriptor `text` names, and the token positions they used.

    Longest registry phrase first, over all keys at once, so "rose gold" is never read as
    "gold" and "under counter" never as "counter". Then numbers with a key's own word
    ("thickness 1.2 mm", "600mm long"), then a word beside a key's own word that is none
    of its choices ("pink colour", "f trap"). Positions in `keep` (the class and type
    words) are never taken."""
    tokens = _words(text)
    keep = set(keep or ())
    used: set[int] = set()
    out: list[Grounded] = []

    def free(i: int) -> bool:
        return i not in used and i not in keep

    # 1. Registry phrases, longest first; a single word also in its plural.
    candidates: list[tuple[int, str, _Key, Any]] = []
    for entry in vocab.keys:
        for phrase, value in entry.phrases.items():
            candidates.append((len(phrase.split()), phrase, entry, value))
    candidates.sort(key=lambda c: -c[0])
    for size, phrase, entry, value in candidates:
        words = phrase.split()
        for start in range(len(tokens) - size + 1):
            span = range(start, start + size)
            if not all(free(i) for i in span):
                continue
            window = tokens[start : start + size]
            exact = window == words
            if not exact and not (size == 1 and _same(window[0], words[0]) and window[0] not in vocab.class_words):
                continue
            taken = set(span)
            # A key word right beside its value is part of what was said ("gunmetal colour").
            for j in (start - 1, start + size):
                if 0 <= j < len(tokens) and free(j) and any(_same(tokens[j], name) for name in entry.names):
                    taken.add(j)
            used.update(taken)
            if not any(g.key == entry.key and g.value == value for g in out):
                said = " ".join(tokens[i] for i in sorted(taken))
                unit = entry.unit if entry.data_type == "numeric" else None
                out.append(Grounded(entry.key, entry.label, said, value, unit, words=phrase))

    # 2. A number beside a numeric key's own word, with its unit when one is said.
    for entry in vocab.keys:
        if entry.data_type != "numeric":
            continue
        for i, token in enumerate(tokens):
            if not free(i) or not any(_same(token, name) for name in entry.names):
                continue
            number_at = next(
                (j for j in (i + 1, i + 2, i - 1, i - 2) if 0 <= j < len(tokens) and free(j) and _NUMBER_RE.match(tokens[j])),
                None,
            )
            if number_at is None:
                continue
            span = {i, number_at}
            unit_at = number_at + 1
            if entry.unit and unit_at < len(tokens) and free(unit_at) and tokens[unit_at] == entry.unit.lower():
                span.add(unit_at)
            number = float(tokens[number_at])
            value = int(number) if number.is_integer() else number
            used.update(span)
            said = " ".join(tokens[min(span) : max(span) + 1])
            out.append(Grounded(entry.key, entry.label, said, value, entry.unit))

    # 3. An unknown value: a word beside a key's own word that is none of its choices.
    #    Beside a word the key's phrases END with ("t trap"), only in a value position
    #    (the shape of the words that stand there, `_in_value_position`); beside the
    #    key's NAME ("pink colour", "colour pink"), whatever the word is.
    from app.services.product_spec_search import _in_value_position

    for entry in vocab.keys:
        if entry.data_type != "enum":
            continue
        for i, token in enumerate(tokens):
            if not free(i):
                continue
            is_head = token in entry.heads
            is_name = any(_same(token, name) for name in entry.names)
            if not (is_head or is_name):
                continue
            sides = (i - 1,) if is_head and not is_name else (i - 1, i + 1)
            for j in sides:
                if not (0 <= j < len(tokens)) or not free(j):
                    continue
                word = tokens[j]
                if _NUMBER_RE.match(word) or word in vocab.brand_words or word in _STOPWORDS or word in vocab.known_words:
                    continue
                if is_head and not is_name and not _in_value_position(word, entry.modifiers.get(token, set())):
                    continue
                said = f"{word} {token}" if is_head and j == i - 1 else word
                used.update({i, j})
                out.append(Grounded(entry.key, entry.label, said, None, None, list(entry.known)))
                break
    return out, used


#: Words that sit around a descriptor without being one ("any", "with", "in").
_STOPWORDS = frozenset(
    {
        "any", "with", "in", "the", "a", "an", "of", "for", "and", "or", "which", "what", "got", "has",
        "have", "is", "are", "ada", "yang", "dalam", "type", "only", "some", "all", "one", "item", "items",
    }
)


def _on_attachment_list(db: "Session", raw: str, vocab: Vocabulary) -> bool:
    """Is `raw` a document type the system has? A type name's words (singular or plural,
    "photo" for Product Photos), a certificate word or scheme, or an alias the owner
    entered on System > Lookup Sets."""
    from app.services.chatbot.lanes.business.predicate import _CERT_RE
    from app.services.product_predicate_service import _lookup_resolve

    words = [_singular(w) for w in _words(raw) if w not in _STOPWORDS]
    if not words:
        return False
    if all(w in vocab.attachment_words for w in words) or _CERT_RE.search(raw):
        return True
    return bool(_lookup_resolve(db, "attachment_type_alias", raw))


#: The document types the parser prompt's taxonomy names (ATTACHMENT TYPE EXTRACTION:
#: "canonical_code is always the taxonomy value"), as the owner listed them on 6 Oct 2026.
DOCUMENT_TYPE_CODES = frozenset(
    {"photo", "video", "technical drawing", "3d model", "certificate", "installation guide"}
)


def _listed_document_type(db: "Session", entity: dict[str, Any]) -> bool:
    """The parser was confident and its code, whole, is a listed document type: a taxonomy
    value or an `attachment_types` name or code. An exact comparison (case aside); no word
    of it and no alias is ever read."""
    from app.models.resources import AttachmentType
    from sqlalchemy import func, or_

    if entity.get("confident") is False:
        return False
    code = _fold(str(entity.get("canonical_code") or entity.get("raw") or ""))
    if not code:
        return False
    if code in DOCUMENT_TYPE_CODES:
        return True
    return (
        db.query(AttachmentType.id)
        .filter(or_(func.lower(AttachmentType.type_name) == code, func.lower(AttachmentType.code) == code))
        .first()
        is not None
    )


def _corrected_class(db: "Session", text: str, vocab: Vocabulary) -> str:
    """A class phrase said in the plural put in the singular against the class vocabulary
    ("kitchen sinks" -> "kitchen sink"), only when the singular phrase names a class and
    the typed one does not. Otherwise the text as typed. Fix round 10 on PR #833: never a
    typo put right ("kitchne sink" stays as typed, and is said back as not found)."""
    from app.services.product_class_signal import resolve_classes_for_term

    if not text or resolve_classes_for_term(db, text):
        return text
    fixed = []
    for word in text.split():
        low = word.lower()
        near = next((w for w in sorted(vocab.class_words) if low not in vocab.class_words and _same(low, w)), None)
        fixed.append(near or word)
    candidate = " ".join(fixed)
    return candidate if candidate != text and resolve_classes_for_term(db, candidate) else text


#: The entity kind of a product the customer named (a code, or a product's own name).
_PRODUCT_HINT = "product"


def _names_a_class_not_a_code(raw: str, vocab: Vocabulary) -> bool:
    """A `product` token that is words only, no code among them, one of which is a class or
    type phrase said word for word ("gunmetal basin", "sorento water closet")."""
    tokens = _words(raw)
    if not tokens or any(any(ch.isdigit() for ch in t) for t in tokens):
        return False
    return bool(_covered_by_class(tokens, vocab))


def _names_two_types(db: "Session", raw: str, vocab: Vocabulary) -> bool:
    """Does `raw` name two different product types word for word, and no type phrase of
    the registry as a whole (`product_spec_search._exact_classes` is None)?"""
    from app.services.product_spec_search import _exact_classes

    tokens = _words(raw)
    for phrase in vocab.type_phrases:
        words = phrase.split()
        if len(words) >= 2 and any(tokens[i : i + len(words)] == words for i in range(len(tokens) - len(words) + 1)):
            return False
    return len(tokens) >= 2 and _exact_classes(db, " ".join(tokens)) is None


def _unheard_words(db: "Session", raw: str, used: set[int], keep: set[int]) -> tuple[list[str], set[int]]:
    """The words of a category token the catalogue does not hold at all (no registry,
    class or brand word, `product_spec_search.unrecognized_words`, and no product's name
    or description word), and `used` with their positions added.

    Fix round 10 on PR #833 ("for #833 yeah exact only"): "pnk water closet" is water
    closets and a word nothing matched, said back in the "Couldn't find" line; the word is
    never searched for as the nearest product. Only beside a class or type phrase the
    token does hold (`keep`): a token that names nothing at all stays whole, the unknown
    product type it is."""
    if not keep:
        return [], used
    from app.services.product_spec_search import _names_a_product, unrecognized_words

    # A word some product carries word for word in its name or description ("deck" in
    # "deck mounted bath mixer", round 5 B1) is the catalogue's own word too.
    alien = {w for w in unrecognized_words(db, raw) if not _names_a_product(db, w)}
    if not alien:
        return [], used
    tokens = _words(raw)
    found: list[str] = []
    positions = set(used)
    for i, token in enumerate(tokens):
        if i in positions or i in keep or token not in alien:
            continue
        positions.add(i)
        if token not in found:
            found.append(token)
    return found, positions


def _remainder(raw: str, used: set[int]) -> str:
    """`raw` without the tokens at `used`, trimmed of stopwords at either end."""
    tokens = _TOKEN_RE.findall(str(raw or ""))
    kept = [t for i, t in enumerate(tokens) if i not in used]
    while kept and kept[0].lower() in _STOPWORDS:
        kept.pop(0)
    while kept and kept[-1].lower() in _STOPWORDS:
        kept.pop()
    return " ".join(kept)


def _validate(entity: dict[str, Any], vocab: Vocabulary) -> list[Grounded]:
    """A `specification` entity the parser emitted, checked against the registry: a key
    it knows and a value among that key's choices, else grounded from its raw words."""
    key_name = str(entity.get("spec_key") or "").strip()
    value = entity.get("spec_value")
    raw = str(entity.get("raw") or "").strip()
    entry = next((k for k in vocab.keys if k.key == key_name), None)
    if entry is not None and value not in (None, ""):
        said = raw or str(value)
        if entry.data_type == "numeric":
            try:
                number = float(str(value).split()[0])
            except (TypeError, ValueError, IndexError):
                number = None
            if number is not None:
                return [Grounded(entry.key, entry.label, said, int(number) if number.is_integer() else number, entry.unit)]
        else:
            spoken = " ".join(_words(value))
            if entry.data_type == "boolean" and isinstance(value, bool):
                return [Grounded(entry.key, entry.label, said, value)]
            if spoken in entry.phrases:
                return [Grounded(entry.key, entry.label, said, entry.phrases[spoken], words=spoken)]
            for phrase, stored in entry.phrases.items():
                if str(stored).lower() == str(value).strip().lower() or str(stored).replace("_", " ").lower() == spoken:
                    return [Grounded(entry.key, entry.label, said, stored, words=phrase)]
    found, _ = ground_words(raw or str(value or ""), vocab)
    if found:
        return found
    if entry is not None and entry.data_type == "enum":
        return [Grounded(entry.key, entry.label, raw or str(value or ""), None, None, list(entry.known))]
    return []


def _as_typed(grounded: list[Grounded], entity: dict[str, Any], message: str | None, vocab: Vocabulary, others: list[str]) -> list[Grounded]:
    """`grounded` in the customer's own words, when the parser put a word right.

    Fix round 10 on PR #833 (owner, 28 Sep 2026: "for #833 yeah exact only"). The parser
    is told to spell a colour word right ("any pnk water closet?" -> raw "pink",
    "gunmetl basin" -> raw "gunmetal", value gunmetal). A descriptor none of whose words
    the message holds was put right by the parser, so it binds nothing: it becomes an
    unknown value of its key, said as the customer typed it (the message's words that
    nothing else in the turn and nothing in the catalogue's vocabulary accounts for).
    Left as grounded when the message is not known or holds no such word, and for an
    entity carried from an earlier message."""
    if not message or not grounded or entity.get("current_message") is False:
        return grounded
    said = _words(message)
    if not said:
        return grounded
    out: list[Grounded] = []
    for g in grounded:
        words = _words(g.said)
        if not words or any(any(_same(w, t) for t in said) for w in words):
            out.append(g)
            continue
        accounted = {w for text in others for w in _words(text)}
        typed = [
            t
            for t in said
            if not _NUMBER_RE.match(t)
            and t not in _STOPWORDS
            and t not in vocab.known_words
            and t not in vocab.brand_words
            and t not in vocab.attachment_words
            and t not in accounted
            and not any(_same(t, k) for k in vocab.class_words)
        ]
        typed = [t for i, t in enumerate(typed) if t not in typed[:i] and len(t) >= 3]
        if not typed:
            out.append(g)
            continue
        entry = next((k for k in vocab.keys if k.key == g.key), None)
        known = list(entry.known) if entry is not None else list(g.known)
        out.append(Grounded(g.key, g.label, " ".join(typed), None, None, known))
    return out


def ground(
    db: "Session", verdict: dict[str, Any], *, vocab: Vocabulary | None = None, message: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The verdict with every descriptor grounded, and a note per change for the trace.

    Entities the registry says nothing about are returned untouched, in place: a turn
    with no descriptor is byte-identical to the parse. `message` is the customer's own
    text: a descriptor the parser put right is read as typed (`_as_typed`)."""
    entities = verdict.get("entities")
    if not isinstance(entities, list) or not entities:
        return verdict, []
    touched = [
        e
        for e in entities
        if isinstance(e, dict)
        and str(e.get("hint") or "").strip().lower() in (_CLASS_HINTS | _SPEC_HINTS | {_ATTACHMENT_HINT, _PRODUCT_HINT})
    ]
    if not touched:
        return verdict, []
    vocab = vocab or load_vocabulary(db)
    moved = _category_word_under_documents(db, verdict, vocab)
    if moved:
        # Fix round 9 on PR #833 (owner, 28 Sep 2026): a category word never opens the
        # "Which kind of file do you need?" menu. With no document word anywhere in the
        # ask it is about the products themselves, exactly as a misfiled descriptor is.
        verdict = {**verdict, "domain_hint": "master_products", "intent_hint": "check_product"}
        entities = verdict.get("entities")
    out: list[Any] = []
    dropped = False
    notes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(grounded: list[Grounded], template: dict[str, Any]) -> None:
        for g in grounded:
            mark = (g.key, str(g.value) if g.value is not None else f"?{g.said}")
            if mark in seen:
                continue
            seen.add(mark)
            out.append(g.entity(template))

    for entity in entities:
        if not isinstance(entity, dict):
            out.append(entity)
            continue
        hint = str(entity.get("hint") or "").strip().lower()
        raw = str(entity.get("raw") or "").strip()
        if hint == _PRODUCT_HINT and _names_a_class_not_a_code(raw, vocab):
            # Fix round 10 on PR #833 (owner, 28 Sep 2026: "i tried to search like gunmetal
            # basin, there is no such thing and it gives me flexible trap"): words with no
            # code in them that say what the thing IS are the described set's words, read
            # word for word like a category, never a product search by nearest neighbour.
            entity = {**entity, "hint": "category"}
            hint = "category"
            notes.append({"from": _PRODUCT_HINT, "raw": raw, "to": "category"})
        if hint in _SPEC_HINTS:
            others = [
                str(e.get("raw") or "")
                for e in entities
                if isinstance(e, dict) and e is not entity and str(e.get("hint") or "").strip().lower() not in _SPEC_HINTS
            ]
            grounded = _as_typed(_validate(entity, vocab), entity, message, vocab, others)
            add(grounded, entity)
            notes.append({"from": hint, "raw": raw, "to": [g.__dict__ for g in grounded]})
            continue
        if hint in _CLASS_HINTS and raw and _names_two_types(db, raw, vocab):
            # Fix round 10 on PR #833: "water tap basin" holds two product types word for
            # word ("water tap", a tap, and "basin", a wash basin). No one product is
            # both, and reading it as either or as both would be a guess at what was
            # meant: the phrase is an unknown product type, said back as typed.
            add([Grounded("product_type", "Product type", raw)], entity)
            notes.append({"from": hint, "raw": raw, "to": "unknown product type"})
            continue
        if hint in _CLASS_HINTS and raw:
            tokens = _words(raw)
            keep = _covered_by_class(tokens, vocab)
            grounded, used = ground_words(raw, vocab, keep=keep)
            unheard, used = _unheard_words(db, raw, used, keep)
            grounded = [*grounded, *(Grounded("", "", word) for word in unheard)]
            if not grounded:
                fixed = _corrected_class(db, raw, vocab)
                if fixed != raw:
                    out.append({**entity, "raw": fixed})
                    notes.append({"from": hint, "raw": raw, "to": fixed})
                else:
                    out.append(entity)
                continue
            rest = _corrected_class(db, _remainder(raw, used), vocab)
            if rest:
                out.append({**entity, "raw": rest, "canonical_code": None})
            add(grounded, entity)
            notes.append({"from": hint, "raw": raw, "to": [rest, *[g.__dict__ for g in grounded]]})
            continue
        if hint == _ATTACHMENT_HINT:
            # ATTACH-GROUNDING-TYPO, option (c) (owner, 6 Oct 2026): only the parser
            # understands words. A confident document type whose code is listed stays that
            # type, whatever the customer typed ("techincal drawings", "TD DRAW", "phtos");
            # any other is dropped. The customer's words are never read here.
            if not _listed_document_type(db, entity):
                notes.append({"from": hint, "raw": raw, "to": "dropped: not a listed document type"})
                dropped = True
                continue
        out.append(entity)
    if not moved and dropped and _category_word_under_documents(db, {**verdict, "entities": out}, vocab):
        # Fix round 9 holds after a document type is dropped: with only what the products
        # ARE left, the ask is about the products, never the file-type menu.
        verdict = {**verdict, "domain_hint": "master_products", "intent_hint": "check_product"}
        moved = True
    if moved:
        notes.append({"from": "domain", "raw": "product_attachment", "to": "master_products"})
    if not notes:
        return verdict, []
    grounded_verdict = {**verdict, "entities": out}
    return grounded_verdict, notes


def _category_word_under_documents(db: "Session", verdict: dict[str, Any], vocab: Vocabulary) -> bool:
    """A document-domain ask that names what the products ARE (a category or a
    specification) and no product, no document type and no document word anywhere: the
    requested attributes, the parser's goal and the message itself carry none."""
    if verdict.get("domain_hint") != "product_attachment":
        return False
    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    hints = {str(e.get("hint") or "").strip().lower() for e in entities}
    if not hints & (_CLASS_HINTS | _SPEC_HINTS) or hints - (_CLASS_HINTS | _SPEC_HINTS):
        return False
    from app.services.chatbot.lanes.business.predicate import _BARE_CERT_WORD_RE, _CERT_RE

    said = [str(a) for a in verdict.get("requested_attributes") or [] if isinstance(a, str)]
    said.append(str(verdict.get("user_goal") or ""))
    for text in said:
        if _BARE_CERT_WORD_RE.search(text) or _CERT_RE.search(text):
            return False
        words = [_singular(w) for w in _words(text)]
        if any(w in {_singular(x) for p in PROMPT_DOCUMENT_KINDS for x in _words(p)} for w in words):
            return False
    return not any(isinstance(a, str) and a.strip() and _on_attachment_list(db, a, vocab) for a in verdict.get("requested_attributes") or [])


def specification_entities(entities: Any) -> list[dict[str, Any]]:
    """The grounded `specification` entities of a verdict, in order."""
    return [
        e
        for e in (entities or [])
        if isinstance(e, dict) and str(e.get("hint") or "").strip().lower() in _SPEC_HINTS
    ]


def scope_word(entity: dict[str, Any]) -> str | None:
    """The registry's own words for a grounded enum or boolean value, the way a scope
    term is written ("gunmetal", "under counter", "p trap"). None for a number (it
    travels as a spec, `spec_bound`) and for an unknown value."""
    value = entity.get("spec_value")
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    return str(entity.get("spec_words") or "").strip() or str(value).replace("_", " ")
