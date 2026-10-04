"""REFER-SALESMAN (owner ruling 30 Sep 2026, `PLAN-refer-salesman-30sep.md`): every reply
that refers a dealer to their salesman is a Customer asks row.

The stock ask (`stock_availability`) already writes its rows from the fetch's own entries
(`engine._stock_ask_answered_entries`). This module covers the OTHER refer replies - a
dealer's incoming ETA reply, an incoming or stock miss, a code the resolver could not place,
a "no" to a did-you-mean, and (CUSTOMER-ASKS-REFER-ONLY, 1 Oct 2026) every reply a barred
contact (#1406) is referred in - by reading what the turn already knows: the fetch
envelopes, the plan's resolved entities and the question the dealer was answering.

Whether the reply refers at all is NOT read off its text: the caller passes `referred`, the
turn's own mark (`turn/refer.py`, set by the one helper every composer prints the line
through) or a stock ask line the presenter stamped `refers_to_salesman`.
The entries it returns take the same road as the stock ask's
(`stock_ask_service.after_answered_turn`), so there is still ONE writer of `stock_asks`.

Pure: no I/O. Lives outside `turn/` because it reads reply TEXT (the refer sentence a
composer already printed), which the turn package never does - the same reason
`dealer_stock.py` lives here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.chatbot import jsc
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

__all__ = ["REFER_TO_SALESMAN", "BRANCH_INCOMING_ETA", "BRANCH_REFERRED", "referred_entries"]

#: A dealer incoming ask answered with ETAs (one row per product line).
BRANCH_INCOMING_ETA = "incoming_eta"
#: Every other refer reply: a miss, a not-found code, a declined did-you-mean.
BRANCH_REFERRED = "referred"

#: `stock_asks.product_code` is VARCHAR(100).
_CODE_CAP = 100
#: The reply text kept as the answer; the column is TEXT, the cap is a guard.
_ANSWER_CAP = 2000


def referred_entries(
    *,
    referred: bool,
    reply_text: str,
    envelopes: Iterable[dict[str, Any]],
    plan: Any,
    pending_before: Any,
    message_text: str,
    answered: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The `stock_asks` entries a refer reply owes beyond the stock ask's own (`answered`).

    Each entry: `product_code`, `product_id` (when the plan resolved the code, else None
    and the writer resolves it by code), `requested_qty` (only a declined did-you-mean
    carries one), `branch`, `answer_summary` and `refers_to_salesman` (always True: the flag
    `stock_ask_service.refer_entries` keeps rows by). Empty when the turn did not refer
    (`referred` False), or when every product it names already has a stock-branch entry."""
    if not referred:
        return []
    text = (reply_text or "").strip()
    covered = {
        _key(e.get("product_code") or e.get("product_name"))
        for e in answered or []
        if isinstance(e, dict)
    }
    envelopes = [e for e in (envelopes or []) if isinstance(e, dict)]
    uuids = _plan_uuids(plan)
    out: list[dict[str, Any]] = []
    # Whether ANY product was named, covered or not: a reply whose every product already has
    # a stock-branch row owes nothing more, and must not fall through to the typed-text row.
    named = [False]

    def add(code: str, *, branch: str, answer: str, qty: int | None = None) -> None:
        code = _CONTROL.sub(" ", str(code)).strip()[:_CODE_CAP]
        if not code:
            return
        named[0] = True
        if _key(code) in covered:
            return
        covered.add(_key(code))
        out.append(
            {
                "product_code": code,
                "product_id": uuids.get(_key(code)),
                "requested_qty": qty,
                "branch": branch,
                "answer_summary": answer[:_ANSWER_CAP],
                "refers_to_salesman": True,
            }
        )

    # 1. A dealer incoming reply: one line per product, "<code>: ETA <dates>".
    for envelope in envelopes:
        for item in _figures(envelope):
            flags = jsc.get(item, "flags")
            if not (isinstance(flags, dict) and flags.get("dealer_view") is True):
                continue
            code, when = _dealer_line(jsc.get(item, "title"))
            if code:
                add(code, branch=BRANCH_INCOMING_ETA, answer=f"{when}. {REFER_TO_SALESMAN}" if when else REFER_TO_SALESMAN)

    # 2. A miss: the codes the fetch found nothing for, and the tokens it could not place
    #    (read beside step 1 too: "incoming SRT1 XYZ9" answers SRT1's ETA and cannot find
    #    XYZ9, and both are the dealer's asks - review round 1, finding 2). Only a stock or
    #    incoming fetch names PRODUCTS there; another domain's miss is a customer name or an
    #    order number, so a barred contact's miss in it falls through to steps 4 and 5
    #    (CUSTOMER-ASKS-REFER-ONLY review).
    for envelope in envelopes:
        if envelope.get("domain") not in _PRODUCT_DOMAINS:
            continue
        for code in [*_strings(envelope.get("miss")), *_strings(envelope.get("unresolved"))]:
            add(code, branch=BRANCH_REFERRED, answer=text)
    if out:
        return out

    # 3. A "no" to a did-you-mean: the code the dealer typed, and the quantity it carried.
    #    Only on the decline itself (`turn/apply.py` fires `stock_pick_declined`): a "yes"
    #    answers the suggested code through the stock ask, and the typed code is not an
    #    ask of its own (review round 1, finding 1).
    payload = getattr(pending_before, "payload", None) if pending_before is not None else None
    declined = "stock_pick_declined" in (getattr(getattr(plan, "trace", None), "rules_fired", None) or [])
    if declined and isinstance(payload, dict) and payload.get("did_you_mean") is True and payload.get("typed"):
        add(str(payload["typed"]), branch=BRANCH_REFERRED, answer=text, qty=_quantity(payload.get("stock_qty")))
    if out:
        return out

    # 4. Whatever the plan resolved for this turn, except a product the stock ask itself
    #    answered or still owes a quantity (AVAIL-MODE-REPLIES, reviewer B2): it is
    #    recorded by the stock ask, on the turn that answers it, and only there.
    in_stock_block = {
        _key(row.get("product_code"))
        for envelope in envelopes
        for row in (envelope.get("stock_availability") or [])
        if isinstance(row, dict) and row.get("product_code")
    }
    for spec in getattr(plan, "fetch", None) or []:
        for entity in getattr(spec, "entities", None) or []:
            code = _entity_code(entity)
            if code and _key(code) in in_stock_block:
                named[0] = True  # named, and the stock ask's to record
            elif code:
                add(code, branch=BRANCH_REFERRED, answer=text)
    if out:
        return out

    if named[0]:
        return out

    # 5. No product anywhere: the ask as the dealer typed it (crew-ask on PR #1386,
    #    built as recommended so the salesman still sees the ask).
    typed = " ".join((message_text or "").split())
    add(typed or "-", branch=BRANCH_REFERRED, answer=text)
    return out


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
#: The fetch domains whose `miss` / `unresolved` are product codes.
_PRODUCT_DOMAINS = frozenset({"inventory", "incoming"})


def _key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _strings(values: Any) -> list[str]:
    return [str(v).strip() for v in (values or []) if isinstance(v, str) and v.strip()] if isinstance(values, list) else []


def _figures(envelope: dict[str, Any]) -> list[Any]:
    for key in ("figures", "answers", "items"):
        rows = envelope.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


#: AVAIL-MODE-REPLIES: the dealer's ETA line is "<code>: ✅ ETA <dates>" or "<code>: No ETA"
#: (owner hand test, 3 Oct 2026); "<code>: ETA <dates>" is the form before it.
_ONE_LINE_ETA = re.compile(r"^(?P<code>.+?):\s+(?P<when>(?:\u2705\s*)?ETA\b.*|No ETA)$")


def _dealer_line(title: Any) -> tuple[str, str]:
    """`"<code>: ETA <dates>"` -> (code, "ETA <dates>"); a title with no code (the
    `/shipments` route) -> ("", the line). The two-line `"<code>\\nETA: <dates>"` an older
    presenter printed still reads the same way."""
    lines = [ln.strip() for ln in str(title or "").splitlines() if ln.strip()]
    if not lines:
        return "", ""
    if len(lines) == 1:
        one = _ONE_LINE_ETA.match(lines[0])
        if one:
            return one.group("code").strip(), one.group("when").strip()
        return ("", lines[0]) if lines[0].upper().startswith("ETA") else (lines[0], "")
    return lines[0], " ".join(lines[1:])


def _entity_code(entity: Any) -> str:
    if not isinstance(entity, dict):
        return ""
    if entity.get("hint") not in (None, "product"):
        return ""
    for key in ("canonical_code", "code", "raw"):
        value = entity.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _plan_uuids(plan: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in getattr(plan, "fetch", None) or []:
        for entity in getattr(spec, "entities", None) or []:
            code = _entity_code(entity)
            uuid = entity.get("uuid") if isinstance(entity, dict) else None
            if code and isinstance(uuid, str) and uuid:
                out.setdefault(_key(code), uuid)
    return out


def _quantity(value: Any) -> int | None:
    """Mirrors `turn/apply.py::_stated_quantity`, without importing the turn package."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 1 else None
    if isinstance(value, str) and value.strip().lstrip("+").isdigit():
        return int(value.strip()) or None
    return None
