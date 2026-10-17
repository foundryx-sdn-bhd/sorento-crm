"""The per-turn candidate shortlist the parser picks from (CHAT-CLOSED-LISTS S2, design D2).

Code only SHORTLISTS. For the words of the current message it lists what is on file under
them: product codes (the whole code, or a code holding a word that has a digit), product
types and spec values (a registry value, its label or one of its synonyms, as whole words),
and near spellings of either (crew Q1 (a), 17 Oct: similarity only ADDS a line). Every line
says what the candidate is, so the parser chooses the kind as well as the value, or chooses
none and the bot asks. Nothing here decides what a word means, and nothing downstream binds
on a candidate: the parser's pick is validated against the lists, never against this
module's scores.

Bounded: at most `CAP_PER_WORD` candidates per word and `CAP_LINES` lines per message; one
whole-code query for the message plus one query per word with a digit (at most
`CODE_WORDS_PER_MESSAGE`); the registry read once per call. Runs in the
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
#: Words with a digit read for codes holding them / spelt near them, per message (one
#: query each); every other word costs nothing beyond the one whole-code query.
CODE_WORDS_PER_MESSAGE = 6
#: The longest run of words read as one phrase ("toilet bowl soft close" is two phrases).
MAX_PHRASE_WORDS = 3
#: Near spellings, as pg_trgm scores them: a spec phrase from 4 characters, a code from 3.
NEAR_PHRASE = 0.55
NEAR_CODE = 0.5
#: CHAT-CODE-LOOKUP R1: under the gate a coded word with no exact code on file lists codes
#: from this similarity up (the resolver's floor, `entity_resolver.ENTITY_MISS_SUGGEST_FLOOR`),
#: not holding the word; the parser still chooses.
NEAR_CODE_NO_EXACT = 0.30
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
    #: Candidate line text -> the product code it lists (code lines only).
    codes: dict[str, str] = field(default_factory=dict)


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


def _code_label(code: str, name: str | None, holds: bool, kind: str = "code") -> str:
    from app.services.chatbot_prompt_vars import _one_line

    # Product rows are staff / import text: one line each, so a stored newline can never
    # start a line of its own in the user turn (security review, 17 Oct 2026).
    code, name = _one_line(code), _one_line(name or "")
    label = f"{kind} {code} ({name})" if name else f"{kind} {code}"
    return label if holds else f"near {label}"


def _fold_code(code: str) -> str:
    return re.sub(r"[-\s]+", "", code).casefold()


def _visible_set_clauses(db: "Session") -> list:
    """The filters that leave only the product sets this contact may be told about: active,
    with members, and EVERY member id among the products they may see (chat-searchable, the
    set's own company, brand scope through the ORM listener). Checked positively, so a member
    the scope hides fails the set instead of dropping out of a "hidden" read."""
    from sqlalchemy import not_

    from app.models.product import Product, chat_searchable_products
    from app.models.product_set import ProductSet, ProductSetMember

    members = db.query(ProductSetMember.id).filter(ProductSetMember.product_set_id == ProductSet.id)
    visible = (
        db.query(Product.id)
        .filter(chat_searchable_products(), Product.company_id == ProductSet.company_id)
        .correlate(ProductSet)
    )
    unseen = members.filter(not_(ProductSetMember.product_id.in_(visible.scalar_subquery())))
    return [ProductSet.is_active.is_(True), members.exists(), not_(unseen.exists())]


def visible_set_ids(db: "Session", set_ids: list[str]) -> set[str]:
    """Which of `set_ids` pass `_visible_set_clauses` for the contact the session is scoped to."""
    from app.models.product_set import ProductSet

    if not set_ids:
        return set()
    rows = db.query(ProductSet.id).filter(ProductSet.id.in_(set_ids), *_visible_set_clauses(db)).all()
    return {str(row[0]) for row in rows}


def _set_rows(db: "Session", word: str) -> list[tuple[str, str | None]]:
    """CHAT-CODE-LOOKUP R2: the visible product sets (`_visible_set_clauses`) whose code holds
    `word` or is spelt near it. `NEAR_CODE_NO_EXACT` is the widest floor the caller uses."""
    from sqlalchemy import func, or_

    from app.models.product_set import ProductSet

    norm = func.lower(func.regexp_replace(ProductSet.set_code, r"[-\s]+", "", "g"))
    return [
        (code, name)
        for code, name in db.query(ProductSet.set_code, ProductSet.name)
        .filter(
            or_(norm.contains(word, autoescape=True), func.similarity(norm, word) >= NEAR_CODE_NO_EXACT),
            *_visible_set_clauses(db),
        )
        .order_by(ProductSet.set_code)
        .limit(CAP_PER_WORD * 4)
        .all()
    ]


def _ranked(word: str, rows: list[tuple[str, str | None, str]], floor: float) -> list[tuple[str, str | None, bool, str]]:
    """Product and set rows ranked together (exact, prefix, contains, near by similarity;
    a set before a product on an exact tie), the near ones kept from `floor` up, capped."""
    typed = trigrams(word)
    keyed = []
    for code, name, kind in rows:
        folded = _fold_code(code)
        score = similarity(typed, trigrams(folded))
        rank = 0 if folded == word else 1 if folded.startswith(word) else 2 if word in folded else 3
        if rank == 3 and score < floor:
            continue
        keyed.append(((rank, -score, kind != "set code", code), (code, name, rank < 3, kind)))
    return [row for _key, row in sorted(keyed)][:CAP_PER_WORD]


def _code_candidates(db: "Session", tokens: list[str], *, code_lookup: bool = False) -> list[WordCandidates]:
    """Codes on file under each word: the whole code for any word, in ONE query for the
    whole message; for a word with a digit (the first `CODE_WORDS_PER_MESSAGE` of them) also
    every code holding it and the codes spelt near it, one query each. `code_lookup`
    (CHAT-CODE-LOOKUP): such a word with no exact code on file also lists the codes spelt
    near it from `NEAR_CODE_NO_EXACT` up."""
    from sqlalchemy import case, func, or_

    from app.models.product import Product, chat_searchable_products

    norm = func.lower(func.regexp_replace(Product.product_code, r"[-\s]+", "", "g"))
    words = {position: re.sub(r"[-\s]+", "", token) for position, token in enumerate(tokens)}
    words = {p: w for p, w in words.items() if len(w) >= 2}
    coded = [p for p, w in words.items() if any(ch.isdigit() for ch in w) and len(w) >= MIN_CODE_CHARS]
    coded = coded[:CODE_WORDS_PER_MESSAGE]
    whole_only = sorted({w for p, w in words.items() if p not in coded})
    by_word: dict[str, list[tuple[str, str | None]]] = {}
    if whole_only:
        rows = (
            db.query(norm.label("word"), Product.product_code, Product.product_name)
            .filter(chat_searchable_products(), norm.in_(whole_only))
            .order_by(Product.product_code)
            .limit(CAP_PER_WORD * len(whole_only))
            .all()
        )
        for word, code, name in rows:
            held = by_word.setdefault(word, [])
            if len(held) < CAP_PER_WORD:
                held.append((code, name))
    out: list[WordCandidates] = []
    for position, word in words.items():
        token = tokens[position]
        if position in coded:
            sim = func.similarity(norm, word)
            rows = (
                db.query(Product.product_code, Product.product_name, norm.contains(word, autoescape=True).label("holds"))
                .filter(chat_searchable_products(), or_(norm.contains(word, autoescape=True), sim >= NEAR_CODE))
                .order_by(
                    case((norm == word, 0), (norm.startswith(word, autoescape=True), 1),
                         (norm.contains(word, autoescape=True), 2), else_=3),
                    sim.desc(),
                    Product.product_code,
                )
                .limit(CAP_PER_WORD)
                .all()
            )
            sets = _set_rows(db, word) if code_lookup else []
            set_exact = any(_fold_code(code) == word for code, _name in sets)
            if code_lookup and not set_exact and not any(_fold_code(code) == word for code, _name, _holds in rows):
                held = [(code, name, True) for code, name, holds in rows if holds]
                rows = held + [
                    (code, name, False)
                    for code, name in db.query(Product.product_code, Product.product_name)
                    .filter(chat_searchable_products(), ~norm.contains(word, autoescape=True), norm.op("%")(word), sim >= NEAR_CODE_NO_EXACT)
                    .order_by(sim.desc(), Product.product_code)
                    .limit(CAP_PER_WORD)
                    .all()
                ]
            labelled = [(_code_label(code, name, holds), code) for code, name, holds in rows]
            if sets:
                exact = set_exact or any(_fold_code(code) == word for code, _name, _holds in rows)
                merged = _ranked(
                    word,
                    [(code, name, "code") for code, name, _holds in rows] + [(code, name, "set code") for code, name in sets],
                    NEAR_CODE if exact else NEAR_CODE_NO_EXACT,
                )
                labelled = [(_code_label(code, name, holds, kind), code) for code, name, holds, kind in merged]
        else:
            labelled = [(_code_label(code, name, True), code) for code, name in by_word.get(word, [])]
        if labelled:
            out.append(WordCandidates(
                gram=token, position=position, items=[text for text, _code in labelled], codes=dict(labelled),
            ))
    return out


def shortlist(db: "Session", message: str, *, code_lookup: bool = False) -> list[WordCandidates]:
    """The candidates for every word of `message` that has any, in message order."""
    tokens = _tokens(message)
    if not tokens:
        return []
    merged: dict[str, WordCandidates] = {}
    for entry in _value_candidates(db, tokens) + _code_candidates(
        db, list(dict.fromkeys(tokens)), code_lookup=code_lookup
    ):
        held = merged.setdefault(entry.gram, WordCandidates(gram=entry.gram, position=entry.position))
        held.items.extend(item for item in entry.items if item not in held.items)
        held.codes.update(entry.codes)
    ordered = sorted(merged.values(), key=lambda e: (e.position, -len(e.gram)))
    for entry in ordered:
        exact = [i for i in entry.items if not i.startswith("near ")]
        entry.items = (exact + [i for i in entry.items if i.startswith("near ")])[:CAP_PER_WORD]
    return ordered


def listed_codes(entries: list[WordCandidates]) -> frozenset[str]:
    """The folded codes of the code lines `candidate_lines` renders (the caps applied):
    what grounding checks a product pick against under the gate."""
    from app.services.chatbot.turn.state import token_key

    shown = [entry for entry in entries if entry.items][:CAP_LINES]
    return frozenset(token_key(entry.codes[item]) for entry in shown for item in entry.items if item in entry.codes)


def candidate_lines(entries: list[WordCandidates]) -> list[str]:
    """The block for the parser's user turn: a header and one line per word, capped; empty
    when nothing in the message is on file (the user turn is then unchanged)."""
    lines = [f'- "{entry.gram}": ' + "; ".join(entry.items) for entry in entries if entry.items]
    if not lines:
        return []
    return [HEADER, *lines[:CAP_LINES]]
