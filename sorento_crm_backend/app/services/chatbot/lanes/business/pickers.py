"""Ports of `annotate-incoming-picker.js` (35) and `annotate-customer-picker.js` (122).

Both annotate the picker text the gate ALREADY rendered: same lines, same order, same
numbering, a suffix per numbered line and at most one trailing sentence. Display only -
`compatible_entities`, the roster the next-turn pick resolves against, is untouched by
either, and that is what makes them safe to run after the gate has decided.

`out = gate` in both bodies: the annotator MUTATES the gate item and returns it, so the
`offer` exit carries the gate's own keys plus `escalate_message` / `is_clarification`.
Reproduced, because `not-found-error-message` downstream spreads that item.

The probe seam is a parameter, not a call: `probe` is whatever `sub-get-results` returned
(`{answers|items: [...]}`), or `None` when it did not run. `annotate_customer` tells the
two apart on purpose - a probe that FAILED must render the bare picker, never a confident
"- no DO" on evidence that was never gathered.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.chatbot import jsc

# JS `\d` is ASCII-only; Python's matches every Unicode decimal, so `[0-9]` it is. `$`
# becomes `\Z` because Python's `$` also matches before a trailing newline.
_NUMBERED_LINE = re.compile(r"^\s*[0-9]+\.\s+(.+?)\s*\Z")
_PRODUCT_CODE_LABEL = re.compile(r"product\s*code", re.IGNORECASE)
_CUSTOMER_LABEL = re.compile(r"^\s*customer\s*\Z", re.IGNORECASE)
# The probe is `crm_order_management_orders_list`, and its presenter
# (`sorento_crm_mcp/presenters.py::_orders_list`) emits the DELIVERY ORDER's own date under
# this label on every row. It is what says a DO exists: an order row on its own says only
# that an order exists, which is a different question and the one the picker used to answer.
_ACTUAL_DELIVERY_LABEL = re.compile(r"^\s*actual\s*delivery\s*date\s*\Z", re.IGNORECASE)
_BRACKET_OR_PAREN = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_LEGAL_FORM = re.compile(r"\bSDN\.?\s*BHD\.?\b|\bSDN\b|\bBHD\b")
_NON_ALNUM_UPPER = re.compile(r"[^A-Z0-9]+")

# `dym-annotate`'s `_PAGE_SATURATION`, retuned to the WINDOWED page (2026-08-25). Nothing
# in the envelope reports truncation, so a page that comes back AT the server's hard limit
# is the only available signal that rows were cut, and an attribution built on a truncated
# page is wrong in the one direction that matters. 1000 = the date-scoped cap the orders
# route itself enforces.
PAGE_SATURATION = 1000

# The probe's injected default window, mirrored here so the miss claim stays honest: on a
# defaulted probe the suffix says "no recent delivery", because "recent" bounds the claim
# to the window that was actually measured.
CUSTOMER_PROBE_WINDOW_DAYS = 90


def _norm(value: Any) -> str:
    """`s => String(s ?? '').trim().toLowerCase()`."""
    return jsc.nullish_str(value).strip().lower()


def _probe_rows(probe: Any) -> list[Any] | None:
    """`answers`, else `items`, else None. Neither being an array is the FAILURE signal.

    `sub-get-results` emits one of the two on every successful read, so "neither" can only
    mean the probe did not run or errored out - which `annotate_customer` renders as the
    bare picker rather than as an empty result set.
    """
    if isinstance(jsc.get(probe, "answers"), list):
        return probe["answers"]
    if isinstance(jsc.get(probe, "items"), list):
        return probe["items"]
    return None


def _annotate_lines(source: str, suffix_for) -> str:
    """Map the numbered lines through `suffix_for`; header lines pass through untouched."""
    out_lines = []
    for line in source.split("\n"):
        match = _NUMBERED_LINE.match(line)
        if not match:
            out_lines.append(line)  # header / non-item line
            continue
        out_lines.append(line + suffix_for(match.group(1)))
    return "\n".join(out_lines)


def annotate_incoming(gate: dict[str, Any] | None, *, probe: Any) -> dict[str, Any]:
    """`annotate-incoming-picker` - "- has incoming" / "- no incoming" per numbered line."""
    out = gate if isinstance(gate, dict) else {}
    probe = probe if jsc.truthy(probe) else {}

    # Codes WITH incoming from the probe's answers (`crm_incoming_stock_list` returns a row
    # per product that HAS incoming; title / "Product Code" field = the code). Absent = none.
    answers = _probe_rows(probe)
    if answers is None:
        answers = []
    if any(jsc.truthy(a) and jsc.get(jsc.get(a, "flags"), "dealer_view") is True for a in answers):
        # AVAIL-MODE-REPLIES (cloud live-parser pass at 2ff7f5e9, "eta SRTWC286"): a dealer
        # is told nothing about a variant's incoming before picking it, and the dealer view
        # lists every asked code ("CODE: No ETA" included), so every line read "has
        # incoming". The roster stays as the gate wrote it.
        out["escalate_message"] = jsc.js_string(jsc.get(out, "gate_clarification") or "")
        out["is_clarification"] = False
        out["incoming_by_code"] = {}
        return out
    has_incoming: set[str] = set()
    for a in answers:
        code = jsc.get(a, "title") if jsc.truthy(a) else a
        if jsc.get(jsc.get(a, "flags"), "dealer_view") is True and isinstance(code, str):
            # PR #1329 fix round 2: a dealer's incoming line is "<code>: ETA <dates>" in
            # the title, with no field (the MCP presenter's `_incoming_dealer`;
            # AVAIL-MODE-REPLIES made it one line, the older form was "<code>\nETA: ...").
            # The whole title matched no code, so every roster line read "no incoming".
            code = re.split(r"\n|:\s+(?:\u2705\s*)?ETA\b|:\s+No ETA", code, maxsplit=1)[0]
        if not jsc.truthy(code) and jsc.truthy(a) and isinstance(jsc.get(a, "fields"), list):
            field = jsc.find(
                jsc.get(a, "fields"),
                lambda x: bool(_PRODUCT_CODE_LABEL.search(jsc.js_string(jsc.get(x, "label")))),
            )
            code = jsc.get(field, "value") if jsc.truthy(field) else field
        if jsc.truthy(code):
            has_incoming.add(_norm(code))

    clarification = jsc.get(out, "gate_clarification")
    source = jsc.js_string(clarification) if jsc.truthy(clarification) else ""
    annotated = _annotate_lines(
        source, lambda label: " - has incoming" if _norm(label) in has_incoming else " - no incoming"
    )
    message = annotated
    if len(has_incoming) == 0:
        message += "\n\nNone of these have incoming stock right now."
    out["escalate_message"] = message
    out["is_clarification"] = False  # parity with the not-found require_specific branch
    # The same fact the sentence above states, kept as DATA (turn re-architecture,
    # AC-1526): the new narrower builds its roster from `compatible_entities` and needs
    # the has/no stamp per code, and re-reading it out of the rendered lines would be
    # parsing our own prose. Computed here because this is where the probe answer is.
    out["incoming_by_code"] = {
        code: _norm(code) in has_incoming
        for code in (
            jsc.js_string(jsc.get(e, "code") or jsc.get(e, "canonical_code") or jsc.get(e, "raw"))
            for e in jsc.array(out.get("compatible_entities"))
            if jsc.truthy(e)
        )
        if code
    }
    return out


def _customer_base(value: Any) -> str:
    """`base` - the same base-name rule the picker groups by, so an order row's
    "MASTILE KLANG SDN BHD [A/C I]" matches the "MASTILE KLANG SDN BHD" line it came from.
    """
    s = jsc.nullish_str(value).upper()
    s = _BRACKET_OR_PAREN.sub(" ", s)
    s = _LEGAL_FORM.sub(" ", s)
    s = _NON_ALNUM_UPPER.sub(" ", s)
    return s.strip()


def _row_field(a: Any, label_re: "re.Pattern[str]") -> Any:
    """The `fields[]` entry whose label matches, or None. The envelope's own shape."""
    if not jsc.truthy(a) or not isinstance(jsc.get(a, "fields"), list):
        return None
    field = jsc.find(
        jsc.get(a, "fields"),
        lambda x: bool(label_re.match(jsc.js_string(jsc.get(x, "label") or ""))),
    )
    return jsc.get(field, "value") if jsc.truthy(field) else None


def _row_has_do(a: Any) -> bool:
    """Does this order row have a DELIVERY ORDER?

    Owner ruling, console pass 3 (6 Sep 2026). The picker's suffix claims a DO, so an
    order with no `actual_delivery_date` is a customer with NO DO yet - which is exactly
    the answer the person picking needs, and exactly the one the old set (built from every
    row the probe returned) could never give.
    """
    value = _row_field(a, _ACTUAL_DELIVERY_LABEL)
    if value is None:
        value = jsc.get(a, "actual_delivery_date") if jsc.truthy(a) else None
    return jsc.nullish_str(value).strip() != ""


def _customer_of_row(a: Any) -> Any:
    """`custOfRow` - the "Customer" field, else `customer_name`, else `customer`."""
    if not jsc.truthy(a):
        return None
    value = _row_field(a, _CUSTOMER_LABEL)
    if jsc.truthy(value):
        return jsc.js_string(value)
    return jsc.get(a, "customer_name") or jsc.get(a, "customer") or None


def _bases_with_do(rows: list[Any]) -> set[str]:
    """The customer family bases the probe found a real DELIVERY ORDER for.

    ONLY rows that carry a delivery-order date count. `crm_order_management_orders_list`
    returns every matching ORDER, delivered or not, so the old membership test ("this
    customer appears in the probe") stamped "has delivery" on a customer whose orders had
    not shipped.
    """
    with_do: set[str] = set()
    for row in rows:
        if not _row_has_do(row):
            continue
        base = _customer_base(_customer_of_row(row))
        if base:
            with_do.add(base)
    return with_do


def customer_bases_with_do(probe: Any) -> set[str] | None:
    """The same answer `annotate_customer` stamps its lines from, for a caller that
    builds its OWN roster (the re-architected narrower, owner hand pass 2 item 2).

    `None` means NOT MEASURED - the probe failed, or its page saturated - which is the
    annotator's own unprobed arm and must read as "no stamp", never as "no DO". One
    rule, two callers, so the line a customer reads and the roster the session stores
    cannot disagree about which families have a delivery order.
    """
    rows = _probe_rows(probe)
    if rows is None or len(rows) >= PAGE_SATURATION:
        return None
    return _bases_with_do(rows)


def product_codes_in(probe: Any) -> set[str] | None:
    """The product codes a probe's rows are ABOUT, or None for NOT MEASURED.

    The general shape of `annotate_incoming`'s own `has_incoming` set, for the two
    rosters that have no annotator of their own: the promotion products read and the PO
    placed read (owner hand pass 3, rows 1 and 7 - "product roster without stamps"). The
    CODE is read off the "Product Code" field first and the row title second, because a
    PO row is titled by its PO number while a promotion-product row is titled by the
    code; reading the title first stamped a roster with PO numbers nothing matched.

    An EXPIRED row does not count. A promotion that ended is not a promotion the customer
    can be offered, and stamping "has promo" from one would promise a price that is gone.

    `None` means the probe did not run or its page saturated, which every caller reads as
    "no stamp", never as "no promo".
    """
    rows = _probe_rows(probe)
    if rows is None or len(rows) >= PAGE_SATURATION:
        return None
    codes: set[str] = set()
    for row in rows:
        flags = jsc.get(row, "flags") if jsc.truthy(row) else None
        if jsc.truthy(flags) and jsc.get(flags, "expired") is True:
            continue
        value = _row_field(row, _PRODUCT_CODE_LABEL)
        if not jsc.truthy(value):
            value = jsc.get(row, "title") if jsc.truthy(row) else None
        if jsc.truthy(value):
            codes.add(_norm(value))
    return codes


def customer_base(value: Any) -> str:
    """The family base of one customer name - `_customer_base` for callers outside."""
    return _customer_base(value)


def annotate_customer(
    gate: dict[str, Any] | None, *, probe: Any, parser: dict[str, Any] | None
) -> dict[str, Any]:
    """`annotate-customer-picker` - which candidates have a delivery ORDER."""
    out = gate if isinstance(gate, dict) else {}
    probe = probe if jsc.truthy(probe) else {}
    parser = parser if isinstance(parser, dict) else {}

    # WHICH WINDOW WAS PROBED: the probe always sends a delivery-date window - the parser's
    # own bounds when the customer named one, else the injected 90-day default. This mirror
    # of "defaulted iff the parser supplied NEITHER bound" is what keeps the suffix wording
    # honest; change the probe's default without revisiting this and the claim loses its bound.
    probe_windowed = (
        jsc.get(parser, "date_filter_start") is None and jsc.get(parser, "date_filter_end") is None
    )

    out["is_clarification"] = False  # parity with the not-found require_specific branch
    out["customer_probe_window_days"] = CUSTOMER_PROBE_WINDOW_DAYS if probe_windowed else None

    # THE BARE PICKER: `not-found-error-message` renders a require_specific turn as
    # `escalate_message = gate.gate_clarification` verbatim, so this string IS today's live
    # output for this turn. Every arm that cannot honestly annotate returns exactly it.
    clarification = jsc.get(out, "gate_clarification")
    bare = jsc.js_string(clarification) if jsc.truthy(clarification) else ""

    # D2 - PROBE FAILED vs PROBE FOUND NOTHING. The probe carries
    # `onError: continueRegularOutput`, so a transient failure arrives as an ordinary item.
    # Without this arm the failure is indistinguishable from an empty answer set and every
    # line renders a confident "- no delivery" on evidence that was never gathered.
    rows = _probe_rows(probe)
    if rows is None:
        out["escalate_message"] = bare
        out["customer_probe_hits"] = None  # null = not measured
        out["customer_probe_skip_reason"] = "probe_unavailable"
        return out

    # F - PAGE SATURATION. A saturated page withholds the annotation and renders the bare
    # picker; it can NEVER invent one.
    if len(rows) >= PAGE_SATURATION:
        out["escalate_message"] = bare
        out["customer_probe_hits"] = None
        out["customer_probe_skip_reason"] = "page_saturated"
        return out

    # ONLY rows that carry a delivery-order date count. `crm_order_management_orders_list`
    # returns every matching ORDER, delivered or not, so the old membership test ("this
    # customer appears in the probe") stamped "has delivery" on a customer whose orders
    # had not shipped.
    with_do = _bases_with_do(rows)

    # NO reordering, no renumbering - the numbers are the pick affordance. Suffixes only,
    # and a plain hyphen, never an em-dash. The wording is the owner's: "has DO" / "no DO",
    # one stamp whether or not the probe was windowed (the window is a fact about the
    # MEASUREMENT and is still reported on `customer_probe_window_days`; hedging it into
    # the line is what made the old "no recent delivery" unreadable at WhatsApp width).
    suffix_hit = " - has DO"
    suffix_miss = " - no DO"
    annotated = _annotate_lines(
        bare,
        lambda label: suffix_hit if _customer_base(label) in with_do else suffix_miss,
    )
    message = annotated
    if len(with_do) == 0:
        message += (
            "\n\nNone of these have a recent DO."
            if probe_windowed
            else "\n\nNone of these have a matching DO."
        )
    out["escalate_message"] = message
    out["customer_probe_hits"] = len(with_do)
    out["customer_probe_skip_reason"] = None
    return out
