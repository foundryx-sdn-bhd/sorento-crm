"""A product code, then words that pick among its variants.

Owner hand test of rounds 4 to 6 on PR #833 (27 Sep 2026, items 2 to 4). A salesperson
types the code she knows and the variant she wants in plain words: "I need srtwc8840 s
trap bowl only price", "Srtwc8840 bowl only price", "Srtwc7604 p trap price". The code
names a family (SRTWC8840-SC, SRTWC8840-LID and the X-prefixed bowls SRTWCX8840-P and
SRTWCX8840-S); the words after it pick the variant. Before this, the words either opened a
category set of unrelated S trap water closets, were ignored, or were folded into the code
("Srtwc7604ptrap") so that nothing matched.

The rule, in order:
  1. A token is a code token when a word of the message is letters then digits and the
     token starts with it (the lane folds "Srtwc7604 p trap" into one token, so the word
     is read back off the message).
  2. The family is every active product whose code, with separators removed, starts with
     that word, or with the same word with an "X" after its letters (SRTWC7604 and
     SRTWCX7604), and does not go on with another digit (8840 is not 88401).
  3. The other words of the message are matched against each variant's code, name,
     description and spec values: first two-word phrases ("p trap", "seat cover"), then
     single words of three letters or more. A word no variant carries ("price", "need",
     "eta") picks nothing and is ignored.
  4. The variants carrying every matched phrase are the answer. When no variant carries
     all of them, the ones carrying the most are ("Did you mean" prefers the variants
     that match what was said). When nothing was matched, or every variant carries it,
     the family is left alone.

The answer replaces the code token with the variants' own codes, which the resolver then
matches exactly, so a code ask never becomes a described set.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

_SPLIT = re.compile(r"[^A-Za-z0-9]+")
_CODE_WORD = re.compile(r"^([A-Z]{2,})(\d[A-Z0-9]*)$")
#: The words the parser types as descriptions of the thing asked about; a code ask drops
#: them from the resolve once they are read as the code's variant words.
_DESCRIPTION_TYPES = frozenset({"category", "product_type", "spec", "product"})
_FAMILY_CAP = 60


def _fold(value: Any) -> str:
    return _SPLIT.sub("", str(value or "")).upper()


def _words(value: Any) -> list[str]:
    return [w for w in _SPLIT.split(str(value or "").upper()) if w]


def _code_word_for(token: str, message_words: list[str]) -> str | None:
    """The message word (letters then digits) the token starts with, longest first."""
    folded = _fold(token)
    best: str | None = None
    for word in message_words:
        if not _CODE_WORD.match(word) or not folded.startswith(word):
            continue
        if best is None or len(word) > len(best):
            best = word
    return best


def _family_rows(db: Session, code_word: str) -> list[tuple[str, dict[str, Any], str]]:
    """`(product_code, derived spec values, searchable text)` per variant of the family
    `code_word` names, one per code, in code order."""
    from app.models.product import Product
    from app.models.product_spec import ProductSpecifications

    letters, rest = _CODE_WORD.match(code_word).groups()  # type: ignore[union-attr]
    pattern = f"^({re.escape(letters)}|{re.escape(letters)}X){re.escape(rest)}([^0-9]|$)"
    folded_code = func.upper(func.regexp_replace(Product.product_code, "[^A-Za-z0-9]", "", "g"))
    rows = (
        db.query(Product, ProductSpecifications.values)
        .outerjoin(ProductSpecifications, ProductSpecifications.product_id == Product.id)
        .filter(Product.is_active.is_(True))
        .filter(folded_code.op("~")(pattern))
        .order_by(Product.product_code)
        .limit(_FAMILY_CAP)
        .all()
    )
    out = []
    seen: set[str] = set()
    for product, values in rows:
        if product.product_code in seen:
            continue
        seen.add(product.product_code)
        values = values if isinstance(values, dict) else {}
        spec_words: list[str] = []
        for entry in values.values():
            value = entry.get("value") if isinstance(entry, dict) else entry
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str):
                    spec_words.append(" ".join(_words(v)))
        text = " ".join(
            [" ".join(_words(product.product_code)), " ".join(_words(product.product_name)), " ".join(_words(product.description)), *spec_words]
        )
        out.append((product.product_code, values, f" {text} "))
    return out


def _family(db: Session, code_word: str) -> list[Any]:
    return [(code, text) for code, _values, text in _family_rows(db, code_word)]


def _holds(values: dict[str, Any], key: str, wanted: Any) -> bool:
    """Does a variant's derived spec row carry `wanted` under `key`? A string value
    compares case-blind; a number as a number; a list value holds it when any member
    does. A variant with no value under the key does not hold it."""
    stored = values.get(key) if isinstance(values, dict) else None
    actual = stored.get("value") if isinstance(stored, dict) else stored
    if actual is None:
        return False
    for candidate in actual if isinstance(actual, list) else [actual]:
        if isinstance(candidate, str) and isinstance(wanted, str):
            if candidate.strip().casefold() == wanted.strip().casefold():
                return True
            continue
        if isinstance(candidate, bool) or isinstance(wanted, bool):
            if candidate == wanted:
                return True
            continue
        try:
            if float(candidate) == float(wanted):
                return True
        except (TypeError, ValueError):
            if candidate == wanted:
                return True
    return False


def narrow_code_tokens_by_specs(
    db: Session,
    *,
    tokens: list[str] | None,
    allowed_types: list[str] | None,
    specs: list[dict[str, Any]] | None,
) -> list[str] | None:
    """`tokens` with each CODE token replaced by the variants of its family that carry
    every grounded spec in `specs`, or None when nothing changes.

    STOCK-QTY-PARTIAL-FILL, owner ruling Q4 (a), 6 Oct 2026 ("How about p trap" after
    "SRTWC286-SH x 10" counted 182 P trap water closets class-wide): a spec said right
    after a product answer is that product's VARIANT. The parser decides it carries the
    product (`entity_op "reuse"` plus the specification entity, the trap-specs addendum);
    this is the resolver half. `narrow_code_tokens` above reads the variant words OFF THE
    MESSAGE and needs the code word in it, which a carried product never is, so the
    grounded spec VALUES (`extracted_specs`, the registry's own keys and choices) pick
    the variant here instead. No word is read.

    The family rule is `narrow_code_tokens`'s own (rule 2). A code whose family has no
    variant carrying the spec is left as it is, exactly as a word no variant carries is
    (rule 3): the product itself is answered, never a silent widening to the class. A
    family every variant of which carries the spec is left alone too (rule 4).
    """
    if not tokens or not specs:
        return None
    wanted = [
        (str(s.get("key")), s.get("value"))
        for s in specs
        if isinstance(s, dict) and s.get("key") and s.get("key") != "brand" and s.get("value") is not None
    ]
    if not wanted:
        return None
    aligned = bool(allowed_types) and len(allowed_types or []) == len(tokens)
    replaced: dict[int, list[str]] = {}
    for i, token in enumerate(tokens):
        kind = str((allowed_types or [])[i] or "").lower() if aligned else ""
        if kind not in ("", "product"):
            continue
        code_word = _fold(token)
        if not _CODE_WORD.match(code_word):
            continue
        family = _family_rows(db, code_word)
        if len(family) < 2:
            continue
        picked = [code for code, values, _text in family if all(_holds(values, key, value) for key, value in wanted)]
        if not picked or len(picked) == len(family):
            continue
        if any(_fold(code) == code_word for code in picked):
            # The product itself carries the spec ("how about s trap" over an S trap):
            # it is its own answer, whatever its siblings carry. Replacing an exact code
            # by itself plus its siblings would turn an answered product into a
            # which-one pick (reviewer, 6 Oct 2026).
            continue
        replaced[i] = picked
    if not replaced:
        return None
    out: list[str] = []
    for i, token in enumerate(tokens):
        for code in replaced.get(i, [token]):
            if code not in out:
                out.append(code)
    return out


def _terms(descriptor: list[str], family: list[tuple[str, str]]) -> list[str]:
    """The descriptor's phrases some variant carries: two-word phrases first, then single
    words of three letters or more."""
    texts = [text for _, text in family]

    def carried(phrase: str) -> bool:
        return any(f" {phrase} " in text for text in texts)

    terms: list[str] = []
    used: set[int] = set()
    for i in range(len(descriptor) - 1):
        if i in used or i + 1 in used:
            continue
        phrase = f"{descriptor[i]} {descriptor[i + 1]}"
        if carried(phrase):
            terms.append(phrase)
            used.update({i, i + 1})
    for i, word in enumerate(descriptor):
        if i in used or len(word) < 3 or word.isdigit():
            continue
        if carried(word) and word not in terms:
            terms.append(word)
    return terms


def pick_variants(db: Session, code_word: str, descriptor: list[str]) -> tuple[list[str], list[str]] | None:
    """`(variant codes, the phrases that picked them)`, or None when the descriptor picks
    nothing (no family, or no word of it is carried by any variant)."""
    family = _family(db, code_word)
    if not family:
        return None
    terms = _terms(descriptor, family)
    if not terms:
        return None
    scored = [(sum(1 for t in terms if f" {t} " in text), code) for code, text in family]
    best = max(score for score, _ in scored)
    picked = [code for score, code in scored if score == best]
    if len(picked) == len(family):
        # Every variant carries what was said ("SRTWC286 water closet"): the words
        # picked nothing, and swapping the code for its whole family (X siblings too)
        # would widen the ask rather than narrow it.
        return None
    return picked, terms


def narrow_code_tokens(
    db: Session, *, query: str, tokens: list[str] | None, allowed_types: list[str] | None
) -> list[str] | None:
    """`tokens` with each code-plus-descriptor token replaced by the variant codes its
    descriptor picks, and the description tokens those words came from dropped. None when
    nothing changes, so the caller's request stays byte-identical."""
    if not tokens:
        return None
    # The code is a whole word of the message ("SRTWC8840-SC" is one code, never
    # SRTWC8840 then "SC"); the descriptor is read word by word around it.
    message = [_fold(w) for w in str(query or "").split() if _fold(w)]
    if not message:
        return None
    replaced: dict[int, list[str]] = {}
    consumed: set[str] = set()
    for i, token in enumerate(tokens):
        code_word = _code_word_for(str(token or ""), message)
        if not code_word:
            continue
        descriptor = [w for raw in str(query or "").split() if _fold(raw) != code_word for w in _words(raw) if not _CODE_WORD.match(w)]
        picked = pick_variants(db, code_word, descriptor)
        if picked is None:
            if _fold(token) != code_word:
                # "Srtwc7604ptrap" whose words picked nothing: the code alone, rather
                # than a folded phrase no product is coded as.
                replaced[i] = [code_word]
            continue
        codes, terms = picked
        replaced[i] = codes
        consumed.update(w for t in terms for w in t.split())
    if not replaced:
        return None
    out: list[str] = []
    for i, token in enumerate(tokens):
        if i in replaced:
            for code in replaced[i]:
                if code not in out:
                    out.append(code)
            continue
        kind = str((allowed_types or [])[i] if allowed_types and i < len(allowed_types) else "").lower()
        words = _words(token)
        if kind in _DESCRIPTION_TYPES and words and set(words) <= consumed:
            # "s trap bowl" as the parser's category beside the code: read as the code's
            # variant words above, so it never opens a category set of its own.
            continue
        out.append(token)
    return out
