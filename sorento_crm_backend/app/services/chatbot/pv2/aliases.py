"""PV2 aliases: the words staff keep for index keys, and which key wins a shared word.

Contract: documentation/plans/chatbot/CONTRACT-PV2-13oct.md section 4.

Aliases only help the MODEL pick an index key: the index (`pv2/index.py`, PV2-CORE) prints
them as "aka" under their key. Code never matches user text against them. The kinds and keys
are the index's own, so a precedence row means the same thing to the index and to a save:

  * `attachment_type` - key = `attachment_types.code`, words = `search_synonyms`;
  * `product_class`   - key = slug of `product_categories.class_label` (`class_key`), words =
    every active searchable category's `search_synonyms` + `CLASS_SYNONYMS` for that class;
  * `spec_attribute`  - key = `spec_key`, words = the registry's `_self` words (the words that
    name the attribute). Enum value words are not printed in the index, so they are not
    aliases here.

A word under two keys of one kind is a CLASH. Precedence is data, not code: a row in
`chatbot_alias_precedence(kind, alias_norm, winner_key)` settles it, and the index prints the
word only under the winner. A staff save that would create a new clash with no precedence row
is refused, naming the clash (`refuse_conflicts`); `publish_v2` refuses while any is open.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

KINDS: tuple[str, ...] = ("attachment_type", "product_class", "spec_attribute")

# The pseudo-value under which the registry keeps words naming the attribute itself.
_SELF = "_self"
# Registry keys the index does not list as attributes (`pv2/index.py` `_SPEC_SKIP`).
_SPEC_SKIP = frozenset({"class", "brand", "product_type"})
_SLUG_RE = re.compile(r"[^0-9a-z]+")


def class_key(class_label: str | None) -> str:
    """A product class's index key; the same rule as `pv2/index.py` `product_class_key`."""
    return _SLUG_RE.sub("_", str(class_label or "").strip().lower()).strip("_")


def norm(word: object) -> str:
    """Casefold + collapse whitespace. The one normalisation every alias comparison uses."""
    return " ".join(str(word or "").casefold().split())


@dataclass(frozen=True)
class Clash:
    kind: str
    alias: str                       # normalised
    keys: tuple[str, ...]            # sorted, >= 2

    def describe(self) -> str:
        return f'"{self.alias}" is a word for {" and ".join(self.keys)}'


@dataclass
class AliasConflictError(Exception):
    clashes: list[Clash] = field(default_factory=list)

    def __str__(self) -> str:
        return message_for(self.clashes)


def message_for(clashes: list[Clash]) -> str:
    parts = "; ".join(c.describe() for c in clashes)
    return (
        f"This word already belongs to another entry: {parts}. Remove it from one of them, "
        "or ask an administrator to set which entry wins it."
    )


# --------------------------------------------------------------------------- #
# Live alias maps
# --------------------------------------------------------------------------- #
def _add(out: dict[str, list[str]], key: str | None, words: Iterable[object]) -> None:
    if not key:
        return
    bucket = out.setdefault(key, [])
    for word in words or []:
        n = norm(word)
        if n and n not in bucket:
            bucket.append(n)


def _attachment_type_aliases(db: Session) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = db.execute(
        text(
            "SELECT code, type_name, COALESCE(search_synonyms, '[]'::jsonb) AS words "
            "FROM attachment_types WHERE code IS NOT NULL"
        )
    ).all()
    for code, _name, words in rows:
        _add(out, code, words or [])
    return out


def _product_class_aliases(db: Session) -> dict[str, list[str]]:
    from app.services.product_class_signal import CLASS_SYNONYMS

    out: dict[str, list[str]] = {}
    rows = db.execute(
        text(
            "SELECT class_label, COALESCE(search_synonyms, '[]'::jsonb) FROM product_categories "
            "WHERE class_label IS NOT NULL AND is_active AND is_searchable "
            "ORDER BY class_label, category_code"
        )
    ).all()
    for label, words in rows:
        _add(out, class_key(label) or None, words or [])
    for label, words in CLASS_SYNONYMS.items():
        key = class_key(label)
        if key in out:
            _add(out, key, words)
    return out


def _spec_attribute_aliases(db: Session) -> dict[str, list[str]]:
    from app.services.product_spec_registry import active_registry, merged_synonyms

    out: dict[str, list[str]] = {}
    for row in active_registry(db):
        if row.spec_key in _SPEC_SKIP:
            continue
        _add(out, row.spec_key, merged_synonyms(row).get(_SELF) or [])
    return out


_SOURCES = {
    "attachment_type": _attachment_type_aliases,
    "product_class": _product_class_aliases,
    "spec_attribute": _spec_attribute_aliases,
}


def live_aliases(db: Session, kind: str) -> dict[str, list[str]]:
    """{key: [normalised aliases]} as stored now."""
    return _SOURCES[kind](db)


def precedence(db: Session, kind: str) -> dict[str, str]:
    """{alias_norm: winner_key} for one kind."""
    rows = db.execute(
        text("SELECT alias_norm, winner_key FROM chatbot_alias_precedence WHERE kind = :kind"),
        {"kind": kind},
    ).all()
    return {norm(alias): str(winner) for alias, winner in rows}


# --------------------------------------------------------------------------- #
# Clash detection (pure)
# --------------------------------------------------------------------------- #
def _owners(alias_map: Mapping[str, Iterable[str]]) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = {}
    for key, words in alias_map.items():
        for word in words:
            n = norm(word)
            if n:
                owners.setdefault(n, set()).add(key)
    return owners


def _settled(alias: str, keys: set[str], prec: Mapping[str, str]) -> bool:
    return prec.get(alias) in keys


def find_clashes(kind: str, alias_map: Mapping[str, Iterable[str]], prec: Mapping[str, str]) -> list[Clash]:
    """Every word under >= 2 keys that no precedence row settles. Sorted by alias."""
    return [
        Clash(kind, alias, tuple(sorted(keys)))
        for alias, keys in sorted(_owners(alias_map).items())
        if len(keys) > 1 and not _settled(alias, keys, prec)
    ]


def winners(alias_map: Mapping[str, Iterable[str]], prec: Mapping[str, str]) -> dict[str, list[str]]:
    """{key: aliases it prints}: a shared word only under its winner, an unsettled one nowhere."""
    owners = _owners(alias_map)
    out: dict[str, list[str]] = {key: [] for key in alias_map}
    for key, words in alias_map.items():
        for word in words:
            n = norm(word)
            if not n or n in out[key]:
                continue
            keys = owners[n]
            if len(keys) == 1 or prec.get(n) == key:
                out[key].append(n)
    return out


# --------------------------------------------------------------------------- #
# DB-facing API
# --------------------------------------------------------------------------- #
def check_conflicts(
    db: Session,
    kind: str,
    key: str | None,
    aliases: Iterable[object],
    *,
    previous: Iterable[object] = (),
) -> list[Clash]:
    """The clashes a save of `aliases` under `key` would CREATE.

    Only words new to this key are checked (`previous` = what the key held before the
    save), so an untouched word that already clashed never blocks an unrelated edit;
    `open_clashes` reports those. A clash is settled when precedence names a winner among
    the keys involved.
    """
    if not key:
        return []
    before = {norm(w) for w in previous}
    new = []
    for word in aliases or []:
        n = norm(word)
        if n and n not in before and n not in new:
            new.append(n)
    if not new:
        return []
    owners = _owners({k: v for k, v in live_aliases(db, kind).items() if k != key})
    prec = precedence(db, kind)
    out: list[Clash] = []
    for alias in new:
        others = owners.get(alias)
        if not others:
            continue
        keys = set(others) | {key}
        if _settled(alias, keys, prec):
            continue
        out.append(Clash(kind, alias, tuple(sorted(keys))))
    return out


def refuse_conflicts(
    db: Session,
    kind: str,
    key: str | None,
    aliases: Iterable[object],
    *,
    previous: Iterable[object] = (),
) -> None:
    """Raise a 409 `AppException` naming every clash the save would create."""
    clashes = check_conflicts(db, kind, key, aliases, previous=previous)
    if clashes:
        from app.services.error_handler import AppException

        raise AppException(status_code=409, message=message_for(clashes), code="ALIAS_CLASH")


def open_clashes(db: Session) -> list[Clash]:
    """Every unsettled clash across all kinds. `publish_v2` refuses while this is non-empty."""
    out: list[Clash] = []
    for kind in KINDS:
        out.extend(find_clashes(kind, live_aliases(db, kind), precedence(db, kind)))
    return out


def index_aliases(db: Session, kind: str) -> dict[str, list[str]]:
    """{key: aliases} for the index: each shared word printed only under its winning key."""
    return winners(live_aliases(db, kind), precedence(db, kind))


# --------------------------------------------------------------------------- #
# Save-path helpers (category, attachment type, spec registry)
# --------------------------------------------------------------------------- #
def clean_words(words: Iterable[object] | None) -> list[str]:
    """Stripped, non-empty, one per normalised spelling; the first spelling kept."""
    out: list[str] = []
    seen: set[str] = set()
    for word in words or []:
        text_ = " ".join(str(word or "").split())
        n = norm(text_)
        if n and n not in seen:
            seen.add(n)
            out.append(text_)
    return out


def spec_row_aliases(row) -> list:
    """The words a spec registry row prints as aliases now: its `_self` words, none while
    it is inactive or not an index attribute."""
    from app.services.product_spec_registry import merged_synonyms

    if not row.is_active or row.spec_key in _SPEC_SKIP:
        return []
    return list(merged_synonyms(row).get(_SELF) or [])


def refuse_spec_row_conflicts(db: Session, row, before: Iterable[object]) -> None:
    """A spec registry save: refuse the attribute words it adds that another attribute holds.

    `before` is `spec_row_aliases(row)` as it was before the save ([] for a new row), so
    re-activating a row checks every word it brings back."""
    refuse_conflicts(db, "spec_attribute", row.spec_key, spec_row_aliases(row), previous=before)
