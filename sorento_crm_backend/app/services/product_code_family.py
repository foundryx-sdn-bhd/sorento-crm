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
  3. The other words of the code's OWN LINE of the message are matched against each
     variant's code, name, description and spec values: first two-word phrases ("p trap",
     "seat cover"), then single words of three letters or more. A word no variant carries
     ("price", "need", "eta") picks nothing and is ignored. Another line is another ask
     (NARROW-CODE-OWN-LINE, the owner's 15-line stock check of 6 Oct 2026: the lines
     "SRTWT5608-FRG x 5" and "SRTWT5608-GM x 10" must not narrow the line "SRTWT5608 x 5").
  4. The variants carrying every matched phrase are the answer. When no variant carries
     all of them, the ones carrying the most are ("Did you mean" prefers the variants
     that match what was said). When nothing was matched, or every variant carries it,
     the family is left alone.
  5. A bare base code whose variants the message also types as their own tokens is never
     narrowed: each of those is an ask of its own, and the bare code is one too. A code
     with its own words beside it ("Srtwc8840 seat cover") is still picked by them.

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


def _family(db: Session, code_word: str) -> list[Any]:
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
        spec_words: list[str] = []
        for entry in (values or {}).values() if isinstance(values, dict) else []:
            value = entry.get("value") if isinstance(entry, dict) else entry
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str):
                    spec_words.append(" ".join(_words(v)))
        text = " ".join(
            [" ".join(_words(product.product_code)), " ".join(_words(product.product_name)), " ".join(_words(product.description)), *spec_words]
        )
        out.append((product.product_code, f" {text} "))
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
    return _pick(_family(db, code_word), descriptor)


def _pick(family: list[tuple[str, str]], descriptor: list[str]) -> tuple[list[str], list[str]] | None:
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
    # SRTWC8840 then "SC"); the descriptor is read word by word around it, on the
    # code's own line only: a multi-line message is one ask per line.
    lines = [line.split() for line in str(query or "").splitlines()]
    message = [_fold(w) for words in lines for w in words if _fold(w)]
    if not message:
        return None
    folded_tokens = [_fold(t) for t in tokens]
    replaced: dict[int, list[str]] = {}
    consumed: set[str] = set()
    for i, token in enumerate(tokens):
        folded = folded_tokens[i]
        code_word = _code_word_for(str(token or ""), message)
        if not code_word:
            continue
        # The code's own line(s): the ones holding the code word and, when the token folds
        # more than the code (the words typed beside it), the token's words too, so a code
        # typed on two lines with two descriptors reads each line for its own token.
        code_lines = [words for words in lines if any(_fold(w) == code_word for w in words)]
        own_lines = [words for words in code_lines if folded in "".join(_fold(w) for w in words)] or code_lines
        descriptor = [
            w for words in own_lines for raw in words if _fold(raw) != code_word for w in _words(raw) if not _CODE_WORD.match(w)
        ]
        family = _family(db, code_word)
        variants = {_fold(code) for code, _ in family} - {code_word}
        if folded == code_word and any(f in variants for f in folded_tokens):
            # "SRTWT5608 x 5" beside "SRTWT5608-FRG x 5" and "SRTWT5608-GM x 10": the
            # variants are asks of their own, so the bare code is one too, never a
            # family to pick from. A code with its own words beside it is not bare.
            picked = None
        else:
            picked = _pick(family, descriptor)
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
