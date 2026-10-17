"""The per-turn candidate shortlist the parser picks from (CHAT-CLOSED-LISTS S2, design D2).

Code only SHORTLISTS. For the words of the current message it lists what is on file under
them: product codes (the whole code, or a code holding a word that has a digit), product
types and spec values (a registry value, its label or one of its synonyms, as whole words),
and near spellings of either (crew Q1 (a), 17 Oct: similarity only ADDS a line). Every line
says what the candidate is, so the parser chooses the kind as well as the value, or chooses
none and the bot asks. Nothing here decides what a word means, and nothing downstream binds
on a candidate: the parser's pick is validated against the lists, never against this
module's scores.

Bounded: at most `CAP_PER_WORD` candidates per word and `CAP_LINES` lines per message, one
code query per code-like word, and the registry read once per call. Runs in the
session-held phase before the parse; a product read goes through the ORM so the contact's
company and brand scope and `chat_searchable_products` apply as on every other chat read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

HEADER = "Candidates for words of this message (each says what it is; pick one or none):"

CAP_PER_WORD = 5
CAP_LINES = 12
#: The longest run of words read as one phrase ("toilet bowl soft close" is two phrases).
MAX_PHRASE_WORDS = 3
#: Near spellings, as pg_trgm scores them: a spec phrase from 4 characters, a code from 3.
NEAR_PHRASE = 0.55
NEAR_CODE = 0.5
MIN_NEAR_CHARS = 4
MIN_CODE_CHARS = 3

#: Registry keys whose values say what the product IS (the category entity keeps them).
TYPE_KEYS = frozenset({"class", "product_type"})
#: The brand is its own entity kind, with its own `Known brands:` line.
SKIP_KEYS = frozenset({"brand"})

_TOKEN_RE = re.compile(r"[0-9a-z][0-9a-z\-/.]*")
_WORD_RE = re.compile(r"[0-9a-z]+")


@dataclass
class WordCandidates:
    """The candidates listed under one word or phrase of the message, best first."""

    gram: str
    position: int
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Value:
    kind: str  # "product type" | "spec"
    spec_key: str
    value: str
    display: str

    def text(self) -> str:
        if self.kind == "product type":
            return f"product type {self.display} ({self.spec_key} = {self.value})"
        return f"spec {self.spec_key} = {self.value} ({self.display})"


def trigrams(text: str) -> frozenset[str]:
    """pg_trgm's trigrams of `text`: lower case, each alphanumeric run padded with two
    spaces in front and one behind."""
    out: set[str] = set()
    for word in _WORD_RE.findall(text.casefold()):
        padded = f"  {word} "
        out.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return frozenset(out)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """pg_trgm's `similarity`: shared trigrams over all trigrams."""
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _tokens(message: str) -> list[str]:
    return [w for w in (t.rstrip("-/.") for t in _TOKEN_RE.findall((message or "").casefold())) if w]


def _phrase(text: str) -> str:
    return " ".join(_WORD_RE.findall(str(text).casefold().replace("_", " ")))


def _vocabulary(db: "Session") -> tuple[dict[str, list[_Value]], dict[str, list[str]]]:
    """Every enum registry value under each of its phrases (value, label, synonyms), and
    every numeric key under the words that name it ("in 1" for `piece_count`). The class
    key is an open vocabulary: its values are its worded labels plus every class label a
    category carries."""
    from app.models.product import ProductCategory
    from app.services.product_spec_registry import (
        active_registry,
        display_spec_value,
        merged_allowed_values,
        merged_synonyms,
    )

    phrases: dict[str, list[_Value]] = {}
    numeric: dict[str, list[str]] = {}
    for row in active_registry(db):
        data_type = (row.data_type or "").lower()
        if row.spec_key in SKIP_KEYS:
            continue
        synonyms = merged_synonyms(row)
        if data_type == "numeric":
            for said in synonyms.get("_self") or []:
                key = _phrase(said)
                if key and row.spec_key not in numeric.setdefault(key, []):
                    numeric[key].append(row.spec_key)
            continue
        if data_type != "enum":
            continue
        labels = dict(getattr(row, "value_labels", None) or {})
        kind = "product type" if row.spec_key in TYPE_KEYS else "spec"
        values = [*merged_allowed_values(row), *(v for v in synonyms if v != "_self")]
        if row.spec_key == "class":
            values += [label for (label,) in db.query(ProductCategory.class_label).distinct() if label]
        for value in dict.fromkeys(values):
            display = display_spec_value(value, labels)
            entry = _Value(kind, row.spec_key, str(value), display)
            for said in (value, display, *(synonyms.get(value) or [])):
                key = _phrase(said)
                if key and entry not in phrases.setdefault(key, []):
                    phrases[key].append(entry)
    return phrases, numeric


def type_values(db: "Session") -> dict[str, str]:
    """The product types a candidate line can name (`product type T (K = V)`), as
    `{casefolded V: T}`: what grounding checks a class pick against under the gate."""
    phrases, _numeric = _vocabulary(db)
    return {
        value.value.casefold(): value.display
        for values in phrases.values()
        for value in values
        if value.kind == "product type"
    }


def _is_number(word: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?", word))


def _numeric_candidates(numeric: dict[str, list[str]], grams: list[tuple[int, str]], words: list[str]) -> list[WordCandidates]:
    """A numeric key's own words in the message, with the number beside them when there is
    one ("4 in 1" -> piece_count 4); the reading of the number is still the parser's."""
    out: list[WordCandidates] = []
    for position, gram in grams:
        keys = numeric.get(gram)
        if not keys:
            continue
        end = position + len(gram.split())
        if position > 0 and _is_number(words[position - 1]):
            number, shown, at = words[position - 1], f"{words[position - 1]} {gram}", position - 1
        elif end < len(words) and _is_number(words[end]) and not _is_number(gram.split()[-1]):
            number, shown, at = words[end], f"{gram} {words[end]}", position
        else:
            number, shown, at = None, gram, position
        items = [f"spec {key} = {number}" if number else f"spec {key} (a number)" for key in keys]
        out.append(WordCandidates(gram=shown, position=at, items=items))
    return out


def _value_candidates(db: "Session", tokens: list[str]) -> list[WordCandidates]:
    vocab, numeric = _vocabulary(db)
    if not vocab and not numeric:
        return []
    words = [_phrase(t) for t in tokens]
    grams: list[tuple[int, str]] = []
    for size in range(MAX_PHRASE_WORDS, 0, -1):
        for start in range(len(words) - size + 1):
            gram = " ".join(w for w in words[start : start + size] if w)
            if gram:
                grams.append((start, gram))
    numbered = _numeric_candidates(numeric, grams, words)

    best: dict[_Value, tuple[int, float, int, str]] = {}  # value -> (exact, score, position, gram)
    for position, gram in grams:
        for value in vocab.get(gram, []):
            if best.get(value, (0,))[0] == 0 or len(gram) > len(best[value][3]):
                best[value] = (1, 1.0, position, gram)
    # A word inside a longer phrase that is on file exactly is that phrase's word ("flexible"
    # in "double flexible", "basin" in "basin mixer"): the longer phrase is what was said, so
    # neither the shorter exact reading nor a near spelling of it is listed.
    exact_spans = [(p, p + len(g.split())) for _, (e, _s, p, g) in best.items() if e]

    def inside_longer(position: int, gram: str) -> bool:
        end = position + len(gram.split())
        return any(lo <= position and end <= hi and hi - lo > end - position for lo, hi in exact_spans)

    best = {v: held for v, held in best.items() if not inside_longer(held[2], held[3])}
    phrase_trigrams = {phrase: trigrams(phrase) for phrase in vocab}
    for position, gram in grams:
        if len(gram.replace(" ", "")) < MIN_NEAR_CHARS or gram in vocab:
            continue
        end = position + len(gram.split())
        if any(lo <= position and end <= hi for lo, hi in exact_spans):
            continue
        gram_trigrams = trigrams(gram)
        size = len(gram.split())
        for phrase, values in vocab.items():
            # A near spelling is the same number of words spelt differently; one word is
            # never "near" a longer phrase that merely contains it ("seat", "toilet seat").
            if len(phrase.split()) != size:
                continue
            score = similarity(gram_trigrams, phrase_trigrams[phrase])
            if score < NEAR_PHRASE:
                continue
            for value in values:
                held = best.get(value)
                if held is None or (held[0] == 0 and score > held[1]):
                    best[value] = (0, score, position, gram)

    by_gram: dict[str, WordCandidates] = {}
    for value, (exact, score, position, gram) in sorted(
        best.items(), key=lambda item: (-item[1][0], -item[1][1], item[0].kind != "product type")
    ):
        entry = by_gram.setdefault(gram, WordCandidates(gram=gram, position=position))
        entry.items.append(value.text() if exact else f"near {value.text()}")
    return list(by_gram.values()) + numbered


def _code_candidates(db: "Session", tokens: list[str]) -> list[WordCandidates]:
    """Codes on file under each word: the whole code for any word; for a word with a digit
    also every code holding it and the codes spelt near it."""
    from sqlalchemy import case, func, or_

    from app.models.product import Product, chat_searchable_products

    norm = func.lower(func.regexp_replace(Product.product_code, r"[-\s]+", "", "g"))
    out: list[WordCandidates] = []
    for position, token in enumerate(tokens):
        word = re.sub(r"[-\s]+", "", token)
        if len(word) < 2:
            continue
        coded = any(ch.isdigit() for ch in word) and len(word) >= MIN_CODE_CHARS
        if coded:
            sim = func.similarity(norm, word)
            match = or_(norm.contains(word, autoescape=True), sim >= NEAR_CODE)
            order = (
                case((norm == word, 0), (norm.startswith(word, autoescape=True), 1),
                     (norm.contains(word, autoescape=True), 2), else_=3),
                sim.desc(),
                Product.product_code,
            )
        else:
            sim = None
            match = norm == word
            order = (Product.product_code,)
        rows = (
            db.query(
                Product.product_code,
                Product.product_name,
                norm.contains(word, autoescape=True).label("holds"),
            )
            .filter(chat_searchable_products(), match)
            .order_by(*order)
            .limit(CAP_PER_WORD)
            .all()
        )
        if not rows:
            continue
        entry = WordCandidates(gram=token, position=position)
        from app.services.chatbot_prompt_vars import _one_line

        for code, name, holds in rows:
            # Product rows are staff / import text: one line each, so a stored newline can
            # never start a line of its own in the user turn (security review, 17 Oct 2026).
            code, name = _one_line(code), _one_line(name or "")
            label = f"code {code} ({name})" if name else f"code {code}"
            entry.items.append(label if holds else f"near {label}")
        out.append(entry)
    return out


def shortlist(db: "Session", message: str) -> list[WordCandidates]:
    """The candidates for every word of `message` that has any, in message order."""
    tokens = _tokens(message)
    if not tokens:
        return []
    merged: dict[str, WordCandidates] = {}
    for entry in _value_candidates(db, tokens) + _code_candidates(db, list(dict.fromkeys(tokens))):
        held = merged.setdefault(entry.gram, WordCandidates(gram=entry.gram, position=entry.position))
        held.items.extend(item for item in entry.items if item not in held.items)
    ordered = sorted(merged.values(), key=lambda e: (e.position, -len(e.gram)))
    for entry in ordered:
        exact = [i for i in entry.items if not i.startswith("near ")]
        entry.items = (exact + [i for i in entry.items if i.startswith("near ")])[:CAP_PER_WORD]
    return ordered


def candidate_lines(entries: list[WordCandidates]) -> list[str]:
    """The block for the parser's user turn: a header and one line per word, capped; empty
    when nothing in the message is on file (the user turn is then unchanged)."""
    lines = [f'- "{entry.gram}": ' + "; ".join(entry.items) for entry in entries if entry.items]
    if not lines:
        return []
    return [HEADER, *lines[:CAP_LINES]]
