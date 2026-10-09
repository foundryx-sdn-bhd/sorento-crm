"""The per-ask runner (MULTI-ASK-PLAN S4, owner card 9 Oct 2026).

PROD 8 Oct 19:50: "SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么
时候 ，2026 总共卖了多少个，2026年每位业务员共卖了多少个" is six asks. The planner made one fetch
per DOMAIN with one tool each, so four asks were never answered and the rest came back as
dozens of WhatsApp messages of rows. Here the parser's `sub_asks` (one per thing asked, in
order) each run ONE aggregate query, and the reply is one `Label: value` line per ask,
nothing unasked (owner Q1), in the asker's language.

Owner rulings this file carries (card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md):
* Q2: a typed code with variants and no `family` said asks back once (`sub_ask_scope`);
  the reply is an ordinary numbered pick (1 = the exact code, 2 = all of them).
* Q3: supplier = the supplier of the LAST PO line, labelled with that PO.
* Q4: a sold ask with no basis asks once (`sub_ask_basis`) at the end of the answered
  lines; only the sold lines wait on it.
* Q1/Q5: no row cap; the engine packs the text into the fewest WhatsApp messages
  (`engine.pack_parts`).

Runs only for a staff contact and only when the parser named TWO OR MORE asks with at
least one the domain fan-out cannot answer (`RUNNER_MEASURES`); every other turn is
today's path, byte for byte. A code with no exact hit is today's path too (the which-one
picker steers).

Every line reads the raw tool JSON (no `view=render`), so a field gated on a grant is
gated here, from the contact's own grants (`ctx.access.attributes`), the same keys the
renderer's `restricted_fields` reads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn import state as turn_state

logger = logging.getLogger(__name__)

SCOPE_KIND = "sub_ask_scope"
BASIS_KIND = "sub_ask_basis"
KINDS = frozenset({SCOPE_KIND, BASIS_KIND})

#: The measures only this runner answers. A message whose asks are all outside it ("stock
#: and incoming") is the existing domain fan-out's, which already answers it.
RUNNER_MEASURES = frozenset(
    {"received_qty", "last_receipt", "supplier", "last_cost", "sold_total", "sold_by", "other"}
)
SOLD = frozenset({"sold_total", "sold_by"})

GRN_TOOL = "crm_procurement_grn_summary"
COST_TOOL = "crm_procurement_po_last_cost_list"
REPORT_TOOL = "crm_report_ask"
STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_by_product"
#: `crm_report_ask`'s own ceiling (`sales_report_service.TOP_SELLING_N_CEILING`): every row,
#: no cap (owner Q1).
REPORT_TOP_N = 1000

#: The grant each measure is read under (card, vocabulary table).
GRANT = {
    "received_qty": "purchase_orders.placed",
    "last_receipt": "purchase_orders.placed",
    "supplier": "purchase_orders.supplier",
    "last_cost": "purchase_orders.cost",
    "sold_total": "sales_orders.sales_report",
    "sold_by": "sales_orders.sales_report",
}

#: The English label of each measure, a `label_catalog` key; `{window}`, `{basis}` and
#: `{axis}` are filled in.
LABEL = {
    "stock": "Stock",
    "incoming": "Incoming",
    "received_qty": "Received {window}",
    "last_receipt": "Last received",
    "supplier": "Supplier",
    "last_cost": "Last cost",
    "sold_total": "Sold {window} ({basis})",
    "sold_by": "Sold {axis} {window} ({basis})",
}
NO_WINDOW_LABEL = {"received_qty": "Received"}
AXIS = {
    "sales_agent": "per salesman",
    "customer": "per customer",
    "product": "per product",
    "brand": "per brand",
    "category": "per category",
    "location": "per location",
    "channel": "per channel",
    "month": "per month",
}
BASIS_WORD = {"delivered": "delivered", "ordered": "ordered"}

NOT_SHARED = "not shared with you"
NOT_AVAILABLE = "not available yet"
NONE_ON_RECORD = "none on record"
CHECK_FAILED = "couldn't check right now"
WHICH_PERIOD = "which period?"
VARIANT_QUESTION = "{code} has {n} variants. Just {code}, or all {n}?"
JUST_CODE = "Just {code}"
ALL_N = "All {n}"
FAMILY_HEADER = "{code} (all {n} variants)"
BASIS_QUESTION = "Sold as delivered or as ordered?"
AS_DELIVERED = "As delivered"
AS_ORDERED = "As ordered"
SUPPLIER_VALUE = "{supplier} ({po}, {date})"
COST_VALUE = "{currency} {cost} / unit ({po}, {date})"
RECEIPT_VALUE = "{date} ({qty})"
INCOMING_VALUE = "{qty}, ETA {date}"

#: Every catalog key this file fills, so `label_catalog` can keep them out of the
#: running-text match (`DIRECT_ONLY`): they are whole-line templates of this reply only.
CATALOG_KEYS = frozenset(
    {
        *LABEL.values(),
        *NO_WINDOW_LABEL.values(),
        *AXIS.values(),
        *BASIS_WORD.values(),
        NOT_SHARED,
        NOT_AVAILABLE,
        NONE_ON_RECORD,
        CHECK_FAILED,
        WHICH_PERIOD,
        VARIANT_QUESTION,
        JUST_CODE,
        ALL_N,
        FAMILY_HEADER,
        BASIS_QUESTION,
        AS_DELIVERED,
        AS_ORDERED,
        SUPPLIER_VALUE,
        COST_VALUE,
        RECEIPT_VALUE,
        INCOMING_VALUE,
    }
)


@dataclass
class Product:
    """One typed code: the exact product and the family the resolver placed for it."""

    code: str
    exact: dict[str, str]
    family: list[dict[str, str]] = field(default_factory=list)

    def ids(self, family: bool) -> list[str]:
        rows = self.family if family and self.family else [self.exact]
        return [r["uuid"] for r in rows]

    def to_wire(self) -> dict[str, Any]:
        return {"code": self.code, "exact": self.exact, "family": self.family}

    @classmethod
    def from_wire(cls, raw: Any) -> "Product | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("exact"), dict):
            return None
        return cls(code=str(raw.get("code") or ""), exact=raw["exact"], family=list(raw.get("family") or []))


@dataclass
class Run:
    """What one call of the runner needs from the turn."""

    call: Callable[[str, dict[str, Any]], Any]
    localizer: Any
    grants: frozenset[str]
    contact_id: str | None
    space_id: str | None
    stock_allowed: bool = True
    asked_at_turn: int | None = None


@dataclass
class Result:
    text: str
    question: Any = None
    trace: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


def wants(verdict: Any, pending: Any) -> bool:
    """Is this turn the runner's? A fresh multi-ask, or the reply to one of its questions."""
    if _resumed(verdict, pending) is not None:
        return True
    asks = _sub_asks(verdict)
    return len(asks) >= 2 and any(a["measure"] in RUNNER_MEASURES for a in asks)


def answer(
    run: Run,
    *,
    verdict: dict[str, Any],
    pending: Any,
    candidates: list[dict[str, Any]],
) -> Result | None:
    """The reply, or None when the turn is not the runner's (today's path answers it)."""
    resumed = _resumed(verdict, pending)
    if resumed is not None:
        kind, position, payload = resumed
        products = [p for p in (Product.from_wire(r) for r in payload.get("products") or []) if p]
        asks = [a for a in payload.get("sub_asks") or [] if isinstance(a, dict)]
        if not products or not asks:
            return None
        if kind == SCOPE_KIND:
            return _answer(run, products, asks, family=position == 2, basis=None)
        return _answer(
            run,
            products,
            asks,
            family=bool(payload.get("family")),
            basis="ordered" if position == 2 else "delivered",
            header_only_sold=True,
        )

    asks = _sub_asks(verdict)
    if not (len(asks) >= 2 and any(a["measure"] in RUNNER_MEASURES for a in asks)):
        return None
    products = _products(verdict, candidates)
    if not products:
        return None
    family = verdict.get("family")
    top_basis = verdict.get("basis") if verdict.get("basis") in BASIS_WORD else None
    if family is None:
        ambiguous = next((p for p in products if len(p.family) > 1), None)
        if ambiguous is not None:
            return _ask_scope(run, ambiguous, products, asks)
    return _answer(run, products, asks, family=bool(family), basis=top_basis)


# --------------------------------------------------------------------------- #
# Reading the verdict
# --------------------------------------------------------------------------- #


def _sub_asks(verdict: Any) -> list[dict[str, Any]]:
    raw = verdict.get("sub_asks") if isinstance(verdict, dict) else None
    return [a for a in raw or [] if isinstance(a, dict) and isinstance(a.get("measure"), str)]


def _resumed(verdict: Any, pending: Any) -> tuple[str, int, dict[str, Any]] | None:
    kind = getattr(pending, "kind", None)
    if kind not in KINDS or not isinstance(verdict, dict):
        return None
    reply = verdict.get("open_question_answer")
    if not isinstance(reply, dict) or reply.get("mode") != "pick":
        return None
    picked = [p for p in reply.get("picked") or [] if isinstance(p, int)]
    if len(picked) != 1 or picked[0] not in (1, 2):
        return None
    return kind, picked[0], dict(getattr(pending, "payload", None) or {})


def _products(verdict: dict[str, Any], candidates: list[dict[str, Any]]) -> list[Product]:
    """Each typed product code, with the resolver's exact hit and the family of codes it
    is the prefix of, in typed order. A code with no exact hit stops the runner: the
    which-one picker is today's (card R3)."""

    def fold(value: Any) -> str:
        return turn_state.token_key(str(value or ""))

    rows = [c for c in candidates if isinstance(c, dict) and c.get("uuid")]
    out: list[Product] = []
    seen: set[str] = set()
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict) or e.get("hint") not in (None, "", "product"):
            continue
        typed = str(e.get("canonical_code") or e.get("raw") or "").strip()
        key = fold(typed)
        if not key or key in seen:
            continue
        seen.add(key)
        code_of = lambda c: fold(c.get("canonical_code") or c.get("code"))  # noqa: E731
        exact = [c for c in rows if code_of(c) == key]
        if not exact:
            return []
        family = [c for c in rows if code_of(c).startswith(key)]
        out.append(
            Product(
                code=str(exact[0].get("canonical_code") or exact[0].get("code") or typed),
                exact=_ref(exact[0]),
                family=[_ref(c) for c in family],
            )
        )
    return out


def _ref(c: dict[str, Any]) -> dict[str, str]:
    return {"uuid": str(c["uuid"]), "code": str(c.get("canonical_code") or c.get("code") or "")}


# --------------------------------------------------------------------------- #
# The two questions
# --------------------------------------------------------------------------- #


def _ask_scope(run: Run, product: Product, products: list[Product], asks: list[dict[str, Any]]) -> Result:
    loc = run.localizer
    n = str(len(product.family))
    text = loc.fill(VARIANT_QUESTION, code=product.code, n=n)
    options = [
        {"label": loc.fill(JUST_CODE, code=product.code), "position": 1},
        {"label": loc.fill(ALL_N, n=n), "position": 2},
    ]
    question = turn_pending.ask(
        SCOPE_KIND,
        options,
        asked_at_turn=run.asked_at_turn,
        expects="pick",
        payload={"products": [p.to_wire() for p in products], "sub_asks": asks},
    )
    return Result(text=text, question=question, trace={"asked": SCOPE_KIND, "variants": len(product.family)})


def _basis_question(run: Run, products: list[Product], asks: list[dict[str, Any]], family: bool) -> tuple[str, Any]:
    loc = run.localizer
    options = [
        {"label": loc.fill(AS_DELIVERED), "position": 1},
        {"label": loc.fill(AS_ORDERED), "position": 2},
    ]
    question = turn_pending.ask(
        BASIS_KIND,
        options,
        asked_at_turn=run.asked_at_turn,
        expects="pick",
        payload={"products": [p.to_wire() for p in products], "sub_asks": asks, "family": family},
    )
    return loc.fill(BASIS_QUESTION), question


# --------------------------------------------------------------------------- #
# The answer
# --------------------------------------------------------------------------- #


def _answer(
    run: Run,
    products: list[Product],
    asks: list[dict[str, Any]],
    *,
    family: bool,
    basis: str | None,
    header_only_sold: bool = False,
) -> Result:
    if header_only_sold:
        asks = [a for a in asks if a.get("measure") in SOLD]
    # A sold ask that still has no basis waits for the one question (Q4), unless the
    # contact may not see sales at all (then it is refused on its line, no question).
    sold_waiting = [
        a
        for a in asks
        if a.get("measure") in SOLD
        and not (a.get("basis") in BASIS_WORD or basis)
        and GRANT[a["measure"]] in run.grants
        and _window(a)
    ]
    calls = _Calls(run)
    # The GRN summary's last receipt is all time whatever window it is sent, so a last
    # receipt asked beside a received quantity rides the SAME call (card: one call each).
    calls.grn_window = next((_window(a) for a in asks if a.get("measure") == "received_qty"), None)
    blocks: list[str] = []
    for product in products:
        ids = product.ids(family)
        head = (
            run.localizer.fill(FAMILY_HEADER, code=product.code, n=str(len(ids)))
            if family and len(ids) > 1
            else product.code
        )
        lines = [head]
        for ask in asks:
            if ask in sold_waiting:
                continue
            lines.extend(_lines(run, calls, ask, ids, basis))
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    question = None
    if sold_waiting:
        line, question = _basis_question(run, products, sold_waiting, family)
        text = text + "\n" + line
    return Result(
        text=text,
        question=question,
        trace={"family": family, "basis": basis, "calls": [name for name, _ in calls.made]},
    )


def _lines(run: Run, calls: "_Calls", ask: dict[str, Any], ids: list[str], basis: str | None) -> list[str]:
    loc = run.localizer
    measure = ask.get("measure")
    if measure not in LABEL:
        return [f"{_own_label(ask)}: {loc.fill(NOT_AVAILABLE)}"]
    window = _window(ask)
    ask_basis = ask.get("basis") if ask.get("basis") in BASIS_WORD else basis
    label = _label(loc, ask, window, ask_basis)
    if measure in SOLD and ask.get("group_by") and ask.get("group_by") not in AXIS:
        return [f"{_own_label(ask)}: {loc.fill(NOT_AVAILABLE)}"]
    need = GRANT.get(measure)
    if (need and need not in run.grants) or (measure == "stock" and not run.stock_allowed):
        return [f"{label}: {loc.fill(NOT_SHARED)}"]
    if measure in SOLD and window is None:
        return [f"{_own_label(ask)}: {loc.fill(WHICH_PERIOD)}"]
    try:
        if measure in ("received_qty", "last_receipt"):
            rows = calls.rows(GRN_TOOL, {"product_ids": ids, **_dates(window if measure == "received_qty" else calls.grn_window)})
            if measure == "received_qty":
                return [f"{label}: {_qty(sum(_num(r.get('received_qty')) for r in rows))}"]
            dated = [r for r in rows if r.get("last_receipt_date")]
            if not dated:
                return [f"{label}: {loc.fill(NONE_ON_RECORD)}"]
            newest = max(r["last_receipt_date"] for r in dated)
            qty = sum(_num(r.get("last_receipt_qty")) for r in dated if r["last_receipt_date"] == newest)
            return [f"{label}: {loc.fill(RECEIPT_VALUE, date=_date(newest), qty=_qty(qty))}"]
        if measure in ("supplier", "last_cost"):
            rows = [r for r in calls.rows(COST_TOOL, {"product_ids": ids, "top_n": 1}) if r.get("po_date")]
            if not rows:
                return [f"{label}: {loc.fill(NONE_ON_RECORD)}"]
            last = max(rows, key=lambda r: (str(r.get("po_date")), str(r.get("po_number") or "")))
            po, when = str(last.get("po_number") or ""), _date(last.get("po_date"))
            if measure == "supplier":
                if not last.get("supplier"):
                    return [f"{label}: {loc.fill(NONE_ON_RECORD)}"]
                return [f"{label}: {loc.fill(SUPPLIER_VALUE, supplier=str(last['supplier']), po=po, date=when)}"]
            cost = last.get("unit_cost_after_discount")
            if cost is None:
                cost = last.get("unit_cost")
            if cost is None:
                return [f"{label}: {loc.fill(NONE_ON_RECORD)}"]
            return [
                f"{label}: "
                + loc.fill(COST_VALUE, currency=_currency(last.get("currency")), cost=f"{float(cost):,.2f}", po=po, date=when)
            ]
        if measure == "stock":
            payload = calls.payload(STOCK_TOOL, {"product_ids": ids})
            summary = payload.get("stock_summary") if isinstance(payload, dict) else None
            total = sum(_num(r.get("total_on_hand")) for r in summary or [] if isinstance(r, dict))
            return [f"{label}: {_qty(total)}"]
        if measure == "incoming":
            rows = calls.rows(INCOMING_TOOL, {"product_ids": ids, **_dates(window, "eta_from", "eta_to")})
            qty = sum(
                _num(s.get("remaining_incoming_quantity"))
                for r in rows
                for s in r.get("shipments") or []
                if isinstance(s, dict)
            )
            etas = [str(r["nearest_estimated_arrival_date"]) for r in rows if r.get("nearest_estimated_arrival_date")]
            if not qty:
                return [f"{label}: 0"]
            if not etas:
                return [f"{label}: {_qty(qty)}"]
            return [f"{label}: {loc.fill(INCOMING_VALUE, qty=_qty(qty), date=_date(min(etas)))}"]
        # sold_total / sold_by
        args = {
            "product_ids": ids,
            **_dates(window),
            "basis": ask_basis,
            "measure": "qty",
        }
        if measure == "sold_by":
            args.update(group_by=ask["group_by"], top_n=REPORT_TOP_N)
        payload = calls.payload(REPORT_TOOL, args)
        if not isinstance(payload, dict) or payload.get("status") not in (None, "ok"):
            return [f"{label}: {loc.fill(CHECK_FAILED)}"]
        if measure == "sold_total":
            return [f"{label}: {_qty(_num((payload.get('total') or {}).get('qty')))}"]
        rows = [r for r in payload.get("rows") or [] if isinstance(r, dict)]
        if not rows:
            return [f"{label}: 0"]
        return [f"{label}:"] + [
            f"{r.get('rank') or i}. {r.get('name') or '-'}: {_qty(_num(r.get('qty')))}" for i, r in enumerate(rows, 1)
        ]
    except _CallFailed:
        return [f"{label}: {loc.fill(CHECK_FAILED)}"]


def _label(loc: Any, ask: dict[str, Any], window: tuple[str, str] | None, basis: str | None) -> str:
    measure = ask["measure"]
    template = LABEL[measure] if window or measure not in NO_WINDOW_LABEL else NO_WINDOW_LABEL[measure]
    values = {
        "window": _window_text(loc, window) if window else "",
        "basis": loc.fill(BASIS_WORD[basis]) if basis in BASIS_WORD else "",
        "axis": loc.fill(AXIS.get(ask.get("group_by") or "", "")) if ask.get("group_by") else "",
    }
    text = loc.fill(template, **values)
    # A sold label with no window yet (the period is asked) or no basis: no empty brackets.
    return " ".join(text.replace("()", "").replace("（）", "").split())


def _own_label(ask: dict[str, Any]) -> str:
    return str(ask.get("label") or ask.get("measure") or "").strip()


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #


def _window(ask: dict[str, Any]) -> tuple[str, str] | None:
    start, end = ask.get("date_from"), ask.get("date_to")
    if not (isinstance(start, str) and isinstance(end, str) and start and end):
        return None
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    if a > b:
        return None
    return a.isoformat(), b.isoformat()


def _window_text(loc: Any, window: tuple[str, str]) -> str:
    a, b = date.fromisoformat(window[0]), date.fromisoformat(window[1])
    if a.year == b.year and (a.month, a.day) == (1, 1) and (b.month, b.day) == (12, 31):
        return str(a.year)
    from app.services.chatbot.label_catalog import RANGE

    return loc.fill(RANGE, **{"from": _date(window[0]), "to": _date(window[1])})


def _dates(window: tuple[str, str] | None, start: str = "date_from", end: str = "date_to") -> dict[str, str]:
    return {start: window[0], end: window[1]} if window else {}


def _date(value: Any) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _qty(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _currency(value: Any) -> str:
    code = str(value or "").strip().upper()
    return "RM" if code in ("", "MYR", "RM") else code


# --------------------------------------------------------------------------- #
# Tool calls, one per distinct (tool, args)
# --------------------------------------------------------------------------- #


class _CallFailed(Exception):
    pass


class _Calls:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.made: list[tuple[str, dict[str, Any]]] = []
        self.grn_window: tuple[str, str] | None = None
        self._cache: dict[str, Any] = {}

    def payload(self, name: str, args: dict[str, Any]) -> Any:
        # Security review B2: EVERY call names the contact, as the fetch path's
        # `entity_ids_transformer` does. Without the pair the route's
        # `_resolve_api_key_scope` reads ALL companies and the stock route skips the
        # contact's stock visibility. The space falls back the same way (review S1).
        from app.services.chatbot.lanes.business.fetch import space_id_or_default

        args = {**args, "contact_id": self.run.contact_id, "space_id": space_id_or_default(self.run.space_id)}
        clean = {k: v for k, v in args.items() if v is not None and v != []}
        key = name + repr(sorted((k, repr(v)) for k, v in clean.items()))
        if key not in self._cache:
            self.made.append((name, clean))
            try:
                result = self.run.call(name, clean)
            except Exception:  # noqa: BLE001 - one failed ask never sinks the others (R6)
                logger.warning("sub_asks: %s failed", name, exc_info=True)
                self._cache[key] = _CallFailed()
            else:
                self._cache[key] = result
        value = self._cache[key]
        if isinstance(value, _CallFailed):
            raise _CallFailed()
        if isinstance(value, dict) and (value.get("error") or (value.get("status_code") or 0) >= 400):
            raise _CallFailed()
        return value

    def rows(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self.payload(name, args)
        data = payload.get("data") if isinstance(payload, dict) else payload
        return [r for r in data or [] if isinstance(r, dict)] if isinstance(data, list) else []
