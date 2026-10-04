"""The ledger family of a customer row: the TRADING NAME behind it.

A shop is often several `customers` rows, one per ledger, that differ only by a bracketed
marker: `CHIN CHUN HARDWARE SDN BHD - [A/C I]`, `HANLIM TRADING (JB) SDN BHD (SRT)`. Ledgers
of one name are one customer. `ledger_family_key` is the comparison key two such rows share;
`ledger_family_label` is what the family is called.

`group_names` / `family_words` name a list of customer rows by group only (DO-ASK-SIMPLIFY
rule 1, owner rule 2 Oct 2026: no count, no "and N more"), for the chatbot's DO headers and
its customer-scope refusal line alike.

Core, not the chatbot package: the chatbot's narrower (`app/services/chatbot/turn/narrow.py`)
groups a customer roster by it, and the stock-ask record (`app/services/stock_ask_service.py`)
names a customer-less ask by it (ASKS-UX item 4). Core must never import
`app.services.chatbot` (`tests/chatbot/test_import_boundary.py`), so the rule lives here and
both import it. Written with string operations rather than regexes because the chatbot's turn
package, which imports it, may not call `re` (AC-1520), and the rule was born there.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

#: Words that name a company's LEGAL FORM, not the business (`gate._LEGAL_FORM` on main,
#: spelled as words because the turn package may not use regular expressions).
_LEGAL_FORM_WORDS = frozenset({"SDN", "BHD"})


def _without_brackets(text: str) -> str:
    """`text` with every bracketed or parenthesised run removed.

    The ledger marker a customer row carries is always bracketed - `CHIN CHUN HARDWARE
    SDN BHD - [A/C I]`, `HANLIM TRADING (JB) SDN BHD (SRT)` - and it is the only part of
    the name that differs between the ledgers of one trading name.
    """
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch in "[(":
            depth += 1
            continue
        if ch in "])":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def normalise_customer_name(text: str) -> str:
    """The key the group map uses: upper-cased, whitespace collapsed."""
    return " ".join(text.upper().split())


class _TurnGroups:
    """The turn's customer groups (owner ruling 2 Oct 2026: a group is the office's own
    say-so, the name rule only the fallback), indexed once so a roster of hundreds of names
    does no scan per name. Pure strings, no DB, no `re`."""

    def __init__(self, mapping: Mapping[str, str]) -> None:
        #: normalised customer name -> group name
        self.by_name = {normalise_customer_name(k): v for k, v in mapping.items()}
        #: normalised group names, so a typed word equal to a group's name compares as the group
        #: (a KEY only; it never makes a ledger a member: `customer_group_of` is exact-name)
        self.own = {normalise_customer_name(v) for v in mapping.values()}
        #: the comparison keys the groups answer with -> the group's name
        self.keys = {_words(v): v for v in mapping.values()}


_GROUPS: ContextVar[_TurnGroups | None] = ContextVar("ledger_family_groups", default=None)

#: Appended to an UNGROUPED row's key when a group of the turn has the same one, so
#: `JUBIN BMS (NS)` (no group) stays apart from the `JUBIN BMS SDN BHD` group it used to be
#: merged into. No name rule can produce it.
_UNGROUPED = " (NO GROUP)"

#: Owner ruling pending (2 Oct 2026). Does an UNGROUPED ledger whose name-rule key equals a
#: group's key (`JUBIN BMS (NS) SDN BHD` beside the `JUBIN BMS SDN BHD` group) JOIN that group
#: (True) or stay a separate customer (False, today's behaviour)? The ONE switch: the header
#: formatter, `ledger_family_key` and `gate._cust_base` all read it, so flipping it is this line.
UNGROUPED_JOINS_NAME_MATCHED_GROUP = False


def apart_from_group(key: str) -> str:
    """`key` (an UNGROUPED row's name-rule key) as the turn compares it: kept apart from a
    group of the turn with the same key unless `UNGROUPED_JOINS_NAME_MATCHED_GROUP`."""
    groups = _GROUPS.get()
    if not UNGROUPED_JOINS_NAME_MATCHED_GROUP and groups is not None and key in groups.keys:
        return key + _UNGROUPED
    return key


@contextmanager
def customer_groups(mapping: Mapping[str, str]) -> Iterator[None]:
    """Hold `mapping` (customer name -> group name) for the block. Empty = name rule only.

    Set once per turn by the engine and by `stock_ask_service`; outside any block the name
    rule answers alone."""
    token = _GROUPS.set(_TurnGroups(mapping) if mapping else None)
    try:
        yield
    finally:
        _GROUPS.reset(token)


def _label_without_marker(text: str) -> str:
    cleaned = " ".join(_without_brackets(text).split()).strip().strip("-").strip()
    return cleaned or text


def customer_group_of(text: str) -> str | None:
    """The group name the turn holds for this customer name, or None.

    By the row's exact normalised FULL name only (owner ruling (b), 2 Oct 2026): no
    marker-stripped probe, no "is a group's own name" probe. A name the map leaves out
    (rows in and out of a group, or in two) is ungrouped."""
    groups = _GROUPS.get()
    if groups is None:
        return None
    return groups.by_name.get(normalise_customer_name(text))


def _words(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.upper())
    return " ".join(w for w in cleaned.split() if w not in _LEGAL_FORM_WORDS)


def group_key(group_name: str) -> str:
    """The comparison key of a customer GROUP, by its own name."""
    return _words(group_name)


def ledger_family_key(text: str) -> str:
    """The TRADING NAME behind a customer row, as a comparison key.

    Main's `gate._cust_base`, rule for rule: upper-cased, bracketed parts dropped, the
    legal-form words dropped, everything non-alphanumeric collapsed to one space. A name
    the turn's customer groups cover answers with its GROUP's key instead (the group name
    whole, so `JUBIN BMS (1990) SDN BHD` is not `JUBIN BMS`).
    """
    group = customer_group_of(text)
    if group is not None:
        return group_key(group)
    groups = _GROUPS.get()
    if groups is not None and normalise_customer_name(text) in groups.own:
        return group_key(text)
    return apart_from_group(_words(_without_brackets(text.upper())))


def ledger_family_label(text: str) -> str:
    """What the family is CALLED: the group's name when the turn has one for the row,
    else the row's own name without its ledger marker."""
    group = customer_group_of(text)
    if group is not None:
        return group
    groups = _GROUPS.get()
    name_key = _words(_without_brackets(text.upper()))
    if groups is not None and name_key in groups.keys:
        if UNGROUPED_JOINS_NAME_MATCHED_GROUP:
            return groups.keys[name_key]
        # An ungrouped row sharing a group's key is its own customer: its full name.
        return text.strip()
    return _label_without_marker(text)


def customer_header_words(entries: Iterable[tuple[str, str | None]]) -> str:
    """The words a `Customer:` line prints for `entries` = `(customer name, group name or None)`.

    A ledger joins a company line ONLY through its explicit customer group (owner ruling (b),
    2 Oct 2026: no automatic name-rule joining). One word per group (its name), one per
    ungrouped ledger (its own full name); first-seen order, identical words once, no count,
    none dropped. An ungrouped ledger whose name-rule key is a group's key (in these entries)
    prints as that group only under `UNGROUPED_JOINS_NAME_MATCHED_GROUP`."""
    pairs = [(str(n).strip(), g) for n, g in entries if n and str(n).strip()]
    groups = {_words(g): g for _n, g in pairs if g}
    words: list[str] = []
    for name, group in pairs:
        if not group and UNGROUPED_JOINS_NAME_MATCHED_GROUP:
            group = groups.get(_words(_without_brackets(name.upper())))
        word = group or name
        if word not in words:
            words.append(word)
    return ", ".join(words)


def _bracket_runs(text: str) -> list[str]:
    """Every top-level bracketed or parenthesised run in `text`, brackets included."""
    runs: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "[(":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "])" and depth:
            depth -= 1
            if depth == 0:
                runs.append(text[start : i + 1])
    return runs


def _shared_label(names: list[str]) -> str:
    """The family's name: the first row's name without the bracketed parts that tell its
    ledgers apart ("[A/C I]", "[IBORN]"), keeping any every ledger shares, such as the
    branch in "CHENG HUAT HARDWARE (SENTUL) SDN BHD"."""
    first = names[0]
    for run in _bracket_runs(first):
        if not all(run in other for other in names[1:]):
            first = first.replace(run, " ", 1)
    cleaned = " ".join(first.split()).strip().strip("-").strip()
    return cleaned or ledger_family_label(names[0])


def _without_trailing_marker(name: str) -> str:
    """One ledger's group name: its name without the bracketed ledger marker at its END
    ("HANLIM TRADING SDN BHD [A/C I]", "... - [IBORN]", "... (CERAMIC & ELLECI)"); a bracket
    inside the name ("CHENG HUAT HARDWARE (SENTUL) SDN BHD") is part of it and stays."""
    text = " ".join(name.split()).strip().rstrip("-").strip()
    while text.endswith(("]", ")")):
        runs = _bracket_runs(text)
        if not runs or not text.endswith(runs[-1]):
            break
        stripped = text[: -len(runs[-1])].strip().rstrip("-").strip()
        if not stripped:
            break
        text = stripped
    return text


def group_names(names: list[str]) -> list[str]:
    """The group (trading) names behind customer rows, each once, in first-seen order.

    Owner rule (2 Oct 2026): when the chatbot names a customer company it shows the GROUP
    NAME ONLY ("HANLIM TRADING SDN BHD"), never a ledger marker, an account count or "and N
    more". Rows of one family (`ledger_family_key`) are one group; its name keeps the
    brackets every row shares (the branch in "CHENG HUAT HARDWARE (SENTUL) SDN BHD") and
    drops the ones that tell the ledgers apart.
    """
    families: dict[str, list[str]] = {}
    for name in dict.fromkeys(n for n in names if n):
        families.setdefault(ledger_family_key(name) or name, []).append(name)
    return list(
        dict.fromkeys(
            _without_trailing_marker(_shared_label(rows) if len(rows) > 1 else rows[0])
            for rows in families.values()
        )
    )


def family_words(names: list[str]) -> str | None:
    """DO-ASK-SIMPLIFY rule 1: the customer rows in scope, named once per group, comma
    separated ("HANLIM TRADING SDN BHD"; "CHIN CHUN HARDWARE SDN BHD, JIMMY - I"). Each DO
    row still carries its own full ledger name; only the header shortens."""
    groups = group_names(names)
    return ", ".join(groups) if groups else None


_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def account_level_from_name(name: str | None) -> int | None:
    """The `A/C <n>` marker inside a `[..]` or `(..)` of a customer name, as a number.

    Roman I..X or arabic, any case, whitespace tolerant. Read ONCE, by the `acct_ledger_0001`
    seed; the live bot reads the `customers.account_level` setting, never the name.
    """
    if not name:
        return None
    depth = 0
    run: list[str] = []
    for ch in name.upper():
        if ch in "[(":
            if depth == 0:
                run = []
            depth += 1
            continue
        if ch in "])":
            if depth == 1:
                level = _marker_level("".join(run))
                if level is not None:
                    return level
            depth = max(0, depth - 1)
            continue
        if depth >= 1:
            run.append(ch)
    return None


def _marker_level(inside: str) -> int | None:
    """`A/C II` -> 2, from the text inside one bracket pair; None when it is not a marker."""
    text = " ".join(inside.split())
    if not text.startswith("A/C "):
        return None
    token = text[4:].strip()
    if token.isdigit():
        return int(token) or None
    return _ROMAN.get(token)
