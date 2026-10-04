# Search-scope disclosure: "Customer: X / Product: Y / Dates: Z" ahead of a
# single-domain ORDER hit (PLAN-chatbot-answer-half-reattach.md slice R5, AC-1694 to
# AC-1696).
#
# A direct port of origin/main's own `tail/compile_state.py::_search_scope_header`
# (deleted on this branch by the S3 turn re-architecture, `129c403c7`): main's whole
# `_AXES` table, the two "always" axes (Customer, Product) plus the four ask-scoped
# ones (Order/Transporter/Container/Warehouse), and the Dates line. The media-confirm
# / escalate-branch / empty-result-set guards main's version carries do not apply here
# - the bridge's own callers already know this is a genuine, single-domain hit before
# they ever reach this module. `tail/` (not `turn/`) because it is free to use `re`
# and to read the resolver's own gate/resolved dicts, the same freedom
# `answer_bridge.py` already has.
#
# Main's best-effort wrapper ("a disclosure bug must never block the answer") lives on
# the one caller, `answer_bridge.apply_scope_block`, which is the whole block main
# wraps: header plus prepend.
from __future__ import annotations

import re
from typing import Any, Mapping

from app.services.chatbot.turn.state import focus_row_label
from app.services.ledger_family import customer_group_of, customer_header_words

# NARROWED (main, captain ruling 2026-08-24, ported verbatim): this header describes
# a DELIVERY ORDER search specifically - it used to gate on "domains the CRM
# date-filters", which let it render "Customer: all customers" on an inbound
# shipment answer, meaningless (a container has no customer).
_DATE_SCOPE_DOMAINS = frozenset({"order"})

# Main's own `_AXES`, verbatim (`origin/main:tail/compile_state.py`), including its
# note that the list is a DELIBERATE DUPLICATE kept in lockstep with
# `not-found-error-message.js`'s own header: a miss opens with the same lines, because
# a search that scanned every customer said so nowhere. `types` are gate
# `entity_type`s; `hints` are the parser's own, which are not always the same word.
_AXES: tuple[dict[str, Any], ...] = (
    {
        "label": "Customer",
        "types": ("customer",),
        "hints": ("customer",),
        "always": True,
        "all_text": "all customers",
    },
    {
        "label": "Product",
        "types": ("product",),
        "hints": ("product",),
        "always": True,
        "all_text": "all products",
    },
    {
        "label": "Order",
        "types": ("customer_order", "order", "order_number"),
        "hints": ("order", "customer_order", "order_number"),
        "always": False,
    },
    {
        "label": "Transporter",
        "types": ("transporter",),
        "hints": ("transporter",),
        "always": False,
    },
    {
        "label": "Container",
        "types": ("inbound_shipment",),
        "hints": ("inbound_shipment", "container"),
        "always": False,
    },
    {"label": "Warehouse", "types": ("warehouse",), "hints": ("warehouse",), "always": False},
)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

#: The SAME guard `turn_runtime._answer_subject` carries, for the same reason: a uuid is
#: an internal identity and never a subject a person reads, and browser pass 2 read one
#: in a header. Defence in depth - nothing writes a uuid into `code`/`title`/`raw` today
#: (`turn/apply.py::_set_kind_field` never does), but this module prints those fields
#: verbatim and the project has already learned this rule once.
_UUID_TEXT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.IGNORECASE
)


def _printable(value: Any) -> str:
    """The word, or "" when it is a raw uuid."""
    text = str(value or "").strip()
    return "" if _UUID_TEXT.match(text) else text


def _format_date(value: Any) -> str:
    """ISO to DD/MM/YYYY, matching the row fields the CRM already renders."""
    m = _ISO_DATE_RE.match(str(value or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else str(value or "")


def _fold(value: Any) -> str:
    """The same `[-\\s]+`-stripped, lower-cased key `turn.state.fold_token` computes -
    duplicated as a plain string op (not imported) because this module answers to no
    purity guard, but the fold rule itself is one rule, not two."""
    return re.sub(r"[-\s]+", "", str(value or "")).strip().lower()


def _raw_of_token(qf: Mapping[str, Any], token: str) -> str:
    """The word the customer actually typed for a resolver token, off the parser's
    own entities - a resolved code is not always what they wrote."""
    key = _fold(token)
    for ent in (qf.get("entities") or []) if isinstance(qf, Mapping) else []:
        if not isinstance(ent, dict):
            continue
        if _fold(ent.get("raw")) == key or _fold(ent.get("canonical_code")) == key:
            return str(ent.get("raw") or token)
    return str(token)


#: `_axis_words`' answer for an axis that prints no line at all this turn.
_SKIP = object()


def _axis_words(
    gate_json: Mapping[str, Any] | None,
    resolver_json: Mapping[str, Any] | None,
    qf: Mapping[str, Any],
    *,
    axis: Mapping[str, Any],
) -> Any:
    """The words this axis is IN SCOPE by, rendered from the GATE's own
    `compatible_entities` (what was actually put in scope), never from the parser's
    hints alone - a pick off a numbered list scopes the search to ONE row, and the
    header must say so. Main's three fallbacks, in its own order: the customer's own
    typed token, then the parser's hinted raw, then the gate row's own label."""
    types = set(axis["types"])
    hints = set(axis["hints"])
    gate_entities = gate_json.get("compatible_entities") if isinstance(gate_json, Mapping) else None
    rows = [e for e in (gate_entities or []) if isinstance(e, dict) and e.get("entity_type") in types]
    if not rows:
        return None
    words: list[str] = []

    def _add(value: Any) -> None:
        printable = _printable(value)
        if printable and printable not in words:
            words.append(printable)

    if axis["label"] == "Customer":
        # DO-ASK-SIMPLIFY tester pass 1: the customer company's name, not the word typed,
        # whenever the DB has named every row (a code never prints, so a row it has not
        # named falls back to the ladder below). The forced links of a contact asking
        # about an order NUMBER are not a subject of their own: no Customer line.
        if all(row.get("scope") for row in rows) and any(
            isinstance(e, dict) and e.get("entity_type") in ("customer_order", "order", "order_number")
            for e in gate_entities or []
        ):
            return _SKIP
        named = [_printable(row.get("display_name")) for row in rows]
        if all(named):
            return customer_header_words([(n, customer_group_of(n)) for n in named]) or None
    resolutions = resolver_json.get("resolutions") if isinstance(resolver_json, Mapping) else None
    for res in resolutions or []:
        if not isinstance(res, dict):
            continue
        hits = any(
            isinstance(m, dict) and m.get("entity_type") in types for m in (res.get("matches") or [])
        )
        token = str(res.get("token") or "").strip()
        if hits and token:
            _add(_raw_of_token(qf, token))
    if not words:
        for e in (qf.get("entities") or []) if isinstance(qf, Mapping) else []:
            if not isinstance(e, dict):
                continue
            if str(e.get("hint") or "") in hints:
                _add(e.get("raw"))
    if not words and axis["label"] == "Customer":
        # The group's name once per customer company, never ledger by ledger.
        names = [_printable(row.get("display_name") or row.get("title") or row.get("code")) for row in rows]
        return customer_header_words([(n, customer_group_of(n)) for n in names if n]) or None
    if not words:
        for row in rows:
            # Hand pass 12 round 3, Group F (owner ruling): a customer row's own
            # `display_name` - `turn_runtime.py::fill_customer_names`'s DB-resolved
            # per-ledger name, filled onto EVERY fetch's own `compatible_entities`
            # unconditionally, hit or miss - wins over the option's rollup `title`/
            # `code` (`turn_runtime.py::_answer_pending`'s own picked-option code,
            # a ledger FAMILY's shared account code, never a ledger's own name). The
            # MISS-path composer (`lanes/business/answer.py::axis_words`) already
            # made this same preference; this is the HIT-path header's own
            # equivalent, a different render path over the same rows.
            _add(row.get("display_name") or row.get("title") or row.get("code"))
    return ", ".join(words) if words else None


def with_brand_names(
    gate_json: Mapping[str, Any] | None, envelope: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """#1262 fix lane round 3, B1-r2: `gate_json` with the fetch envelope's own
    `brand_names` (the live brands the order fetch was actually filtered by,
    `turn_runtime.order_brand_filter`) as `live_brands`. A copy; unchanged when the
    fetch sent no brand. A follow-up turn that typed nothing has no resolver gate at
    all, so the brand has to come off the fetch, never off the resolver."""
    names = envelope.get("brand_names") if isinstance(envelope, Mapping) else None
    if not names:
        return gate_json
    return {**(gate_json if isinstance(gate_json, Mapping) else {}), "live_brands": list(names)}


def live_brand_words(gate_json: Mapping[str, Any] | None) -> str | None:
    """#1262 fix lane round 2, B1: the live brands an order turn is filtered by
    (`with_brand_names` stamps `live_brands` on the gate), for the "Brand:" line both
    order headers print - this one and the miss composer's in
    `lanes/business/answer.py`."""
    raw = gate_json.get("live_brands") if isinstance(gate_json, Mapping) else None
    words: list[str] = []
    for value in raw if isinstance(raw, list) else []:
        printable = _printable(value)
        if printable and printable not in words:
            words.append(printable)
    return ", ".join(words) if words else None


def _one_typed_word(rows: Any) -> str | None:
    """#1262 fix lane round 7, R5: several carried rows that one typed word resolved to
    ("cheng huat sentul", seven ledgers) are that word, as the turn that typed it
    printed it - never the list of every ledger. Such rows each carry their own
    resolved `name`; a picked ledger family (Group F) carries none and still names
    each ledger."""
    kept = [row for row in rows or [] if isinstance(row, Mapping)]
    if len(kept) < 2 or not all(row.get("name") for row in kept):
        return None
    raws = {_printable(row.get("raw")) for row in kept}
    if len(raws) != 1:
        return None
    return raws.pop() or None


def _focus_words(rows: Any, *, customer: bool = False) -> str | None:
    """The SAME axis, off the FOCUS carry - AC-1695's own case, which main has no
    equivalent for because main's header runs in the tail, where the session's
    carried subject is already on `prev`. A bare positional pick ("1") names no
    entity of its own, so `resolve_kinds` never ran this turn and the gate has
    nothing to read (`ResolveOutcome.payload is None`) - but the ORIGINAL product
    token the pick continues is still on the focus (`turn/apply.py::
    _narrow_and_plan`'s own `_set_kind_field`), and AC-1695 requires the header
    after a pick to name it. Customer and Product only: those are the two axes a
    `Focus` carries."""
    words: list[str] = []
    if customer:
        # DO-ASK-SIMPLIFY tester pass 1: rows that each carry their resolved name print the
        # customer company's name, not the one word typed for them (`_one_typed_word`).
        kept = [row for row in rows or [] if isinstance(row, Mapping)]
        named = [_printable(row.get("display_name") or row.get("name")) for row in kept]
        if kept and all(named):
            return customer_header_words([(n, customer_group_of(n)) for n in named]) or None
    typed = _one_typed_word(rows)
    if typed:
        return typed
    if customer:
        names = [
            _printable(focus_row_label(row)) for row in rows or [] if isinstance(row, Mapping)
        ]
        return customer_header_words([(n, customer_group_of(n)) for n in names if n]) or None
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        # Hand pass 12 round 3, Group F: `display_name` (the caller's own DB-resolved
        # per-ledger name, filled onto a LOCAL copy right before this call, never onto
        # `focus` itself) wins over `name` (only ever stamped for a single-identity
        # pick) and the option's own rollup `raw`/`canonical_code` - the ONE ladder,
        # shared with `turn/compose.py::_subject_line` (hand pass 12 Phase 3 P1).
        value = _printable(focus_row_label(row))
        if value and value not in words:
            words.append(value)
    return ", ".join(words) if words else None


def search_scope_header(
    *,
    domain: str | None,
    qf: Mapping[str, Any] | None,
    gate_json: Mapping[str, Any] | None,
    resolver_json: Mapping[str, Any] | None,
    focus_customers: Any = None,
    focus_products: Any = None,
) -> str | None:
    """"Customer: .../Product: .../Dates: ..." for a single ORDER-domain business
    turn - `None` outside that one domain (`_DATE_SCOPE_DOMAINS`), so the caller's
    own text is untouched for everything else.

    `domain` is the FETCH's own domain (`FetchSpec.domain`), not `qf`'s
    `domain_hint` - main's own port reads `qf.domain_hint or prev.domain_hint`
    because a positional pick names no domain of its own (AC-816 rule 4), and the
    fetch plan has already done that same carry-forward reconciliation by the time
    this runs.
    """
    q = qf if isinstance(qf, Mapping) else {}
    if str(domain or "").lower() not in _DATE_SCOPE_DOMAINS:
        return None
    start = q.get("date_filter_start")
    end = q.get("date_filter_end")
    if not start and not end:
        dates = "all dates"
    elif start and end and start == end:
        dates = _format_date(start) or "all dates"
    else:
        dates = f"{_format_date(start) if start else 'earliest'} to {_format_date(end) if end else 'today'}"
    focus_by_label = {"Customer": focus_customers, "Product": focus_products}
    lines: list[str] = []
    for axis in _AXES:
        words = _axis_words(gate_json, resolver_json, q, axis=axis)
        if words is _SKIP:
            continue
        if words is None and axis["label"] in focus_by_label:
            words = _focus_words(focus_by_label[axis["label"]], customer=axis["label"] == "Customer")
        if axis["always"]:
            lines.append(f"{axis['label']}: {words or axis['all_text']}")
        elif words:
            lines.append(f"{axis['label']}: {words}")
    brand_words = live_brand_words(gate_json)
    if brand_words:
        lines.append(f"Brand: {brand_words}")
    lines.append(f"Dates: {dates}")
    return "\n".join(lines)
