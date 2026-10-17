"""The per-ask runner (MULTI-ASK-PLAN S4, owner card 9 Oct 2026).

PROD 8 Oct 19:50: "SRTWT7445,2026 年 入库了多少数量 , 供应商是谁 ， 采购价是多少钱。 最后一次进库是什么
时候 ，2026 总共卖了多少个，2026年每位业务员共卖了多少个" is six asks. The planner made one fetch
per DOMAIN with one tool each, so four asks were never answered and the rest came back as
dozens of WhatsApp messages of rows. Here the parser's `sub_asks` (one per thing asked, in
order) each run ONE aggregate query, and the reply is one `Label: value` line per ask,
nothing unasked (owner Q1), in the asker's language.

Owner rulings this file carries (card: documentation/plans/chatbot/CARD-multi-ask-plan-09oct.md):
* Q2: a typed code with variants and no `family` said asks back once (`sub_ask_scope`),
  before the runner answers (round 6, G1; multi-ask turns only, owner ruling Q1 (b): a
  one-ask turn keeps today's path): `scope_question` here, asked by the engine. An exact code with variants is
  "just X, or all N?"; a code that is only a prefix lists its codes after "all N". The
  reply re-runs the original verdict with the scope settled (`scope_reply`).
* Q3: supplier = the supplier of the LAST PO line, labelled with that PO.
* Q4: a sold ask with no basis asks once (`sub_ask_basis`) at the end of the answered
  lines; only the sold lines wait on it. A sold ask with no period asks that first
  (`sub_ask_period`, round 6, G3), the same way.
* Q1/Q5: no row cap; the engine packs the text into the fewest WhatsApp messages
  (`engine.pack_parts`).

Runs only for a staff contact and only when the parser named TWO OR MORE asks (round 6,
G2: every such message, stock and incoming included); every other turn is today's path.
A customer, brand or sales agent the resolver placed narrows the sold lines (their ids
ride `crm_report_ask`); one it could not place is today's path, never an answer over
every customer.

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
OPTION_JUST = "just this code"
OPTION_ALL = "all variants"
BASIS_KIND = "sub_ask_basis"
PERIOD_KIND = "sub_ask_period"
KINDS = frozenset({SCOPE_KIND, BASIS_KIND, PERIOD_KIND})
SOLD = frozenset({"sold_total", "sold_by"})
#: The non-product entity kinds a sold line can be narrowed by, and the `crm_report_ask`
#: param each one's resolved ids ride (`catalog.py`: ids, never names).
FILTER_PARAMS = {"customer": "customer_ids", "brand": "brand_ids", "sales_agent": "sales_agent_ids"}

GRN_TOOL = "crm_procurement_grn_summary"
#: "Last in" is the last SPO line's delivery date everywhere (owner ruling R-LASTIN, 9 Oct 2026).
SPO_TOOL = "crm_procurement_spo_allocations_last_receipt_list"
COST_TOOL = "crm_procurement_po_last_cost_list"
REPORT_TOOL = "crm_report_ask"
STOCK_TOOL = "crm_inventory_stock_balance_list"
INCOMING_TOOL = "crm_incoming_stock_by_product"
#: `/inventory/stock/balance`'s own `limit` ceiling (`api/v1/inventory/stock.py`).
STOCK_LIMIT = 5000
#: A family larger than this is today's path (review R3): its ids would ride every GET and
#: the stored question. Far above any real family (the owner's code has 8).
FAMILY_MAX = 200
#: A prefix-only code lists its codes after "all N" up to this many; past it the question
#: offers "all N" and asks for the full code.
PICK_MAX = 20
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
    "incoming": "Incoming {window}",
    "received_qty": "Received {window}",
    "last_receipt": "Last in",
    "supplier": "Supplier",
    "last_cost": "Last cost",
    "sold_total": "Sold {window} ({basis})",
    "sold_by": "Sold {axis} {window} ({basis})",
}
NO_WINDOW_LABEL = {"received_qty": "Received", "incoming": "Incoming"}
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
TOO_MANY_REPORTS = "too many sales reports just now, try again in a few minutes"
#: The replayed verdict's key naming the code(s) a scope reply's answer is about.
FAMILY_FOR = "family_for"
VARIANT_QUESTION = "{code} has {n} variants. Just {code}, or all {n}?"
JUST_CODE = "Just {code}"
ALL_N = "All {n}"
FAMILY_HEADER = "{code} (all {n} variants)"
PREFIX_QUESTION = "{code} has {n} variants. Which one?"
TYPE_FULL_CODE = "Or type the full code."
BASIS_QUESTION = "Sold as delivered or as ordered?"
PERIOD_QUESTION = "Sold over which period?"
WHAT_ABOUT = "What would you like to know about {name}? Stock, incoming, cost or sales?"
AS_DELIVERED = "As delivered"
AS_ORDERED = "As ordered"
SUPPLIER_VALUE = "{supplier} ({po}, {date})"
COST_VALUE = "{currency} {cost} / unit ({po}, {date})"
RECEIPT_VALUE = "{date} ({qty}, {spo})"
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
        TOO_MANY_REPORTS,
        VARIANT_QUESTION,
        JUST_CODE,
        ALL_N,
        FAMILY_HEADER,
        PREFIX_QUESTION,
        TYPE_FULL_CODE,
        BASIS_QUESTION,
        PERIOD_QUESTION,
        WHAT_ABOUT,
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
    """One typed code: the exact product (None when the code is only a prefix, round 6
    G1) and the family the resolver placed for it."""

    code: str
    exact: dict[str, str] | None
    family: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_variant(self) -> bool:
        """An exact product whose code is a variant of its model family ("SRTWT7445-LV"),
        not the family head ("SRTWT7445"), by the CODE-VARIANTS rule."""
        from app.services.product_family import family_code_for

        head = family_code_for(self.code)
        return self.exact is not None and head is not None and head != "".join(self.code.split()).upper()

    def ids(self, family: bool) -> list[str]:
        rows = self.family if (family or self.exact is None) and self.family else [self.exact]
        return [r["uuid"] for r in rows if r]

    def to_wire(self) -> dict[str, Any]:
        return {"code": self.code, "exact": self.exact, "family": self.family}

    @classmethod
    def from_wire(cls, raw: Any) -> "Product | None":
        if not isinstance(raw, dict):
            return None
        exact = raw.get("exact") if isinstance(raw.get("exact"), dict) else None
        family = [r for r in raw.get("family") or [] if isinstance(r, dict)]
        if exact is None and not family:
            return None
        return cls(code=str(raw.get("code") or ""), exact=exact, family=family)


@dataclass
class Run:
    """What one call of the runner needs from the turn."""

    call: Callable[[str, dict[str, Any]], Any]
    localizer: Any
    grants: frozenset[str]
    contact_id: str | None
    space_id: str | None
    #: Every catalogue product whose code starts with a code, `[{uuid, code}]`: the WHOLE
    #: family, since the resolver's prefix probe stops at `PREFIX_LIMIT` (review S4).
    family_of: Callable[[str], list[dict[str, str]]] | None = None
    stock_allowed: bool = True
    asked_at_turn: int | None = None


@dataclass
class Result:
    text: str
    question: Any = None
    trace: dict[str, Any] = field(default_factory=dict)
    #: The products the answer was ABOUT (`[{uuid, code}]`), the next turn's focus: the
    #: exact code after "just this one", never the whole family the resolver placed
    #: (crew live check 6). None when the turn only asked.
    focus_products: list[dict[str, str]] | None = None


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #


def resumes(verdict: Any, pending: Any) -> bool:
    """Is this turn the reply to one of the runner's own questions?"""
    return _resumed(verdict, pending) is not None


def wants(verdict: Any, pending: Any) -> bool:
    """Is this turn the runner's? A fresh multi-ask, or the reply to one of its questions."""
    return _resumed(verdict, pending) is not None or len(_sub_asks(verdict)) >= 2


def answer(
    run: Run,
    *,
    verdict: dict[str, Any],
    pending: Any,
    candidates: list[dict[str, Any]],
    compatible: list[dict[str, Any]] | None = None,
    settled: bool = True,
) -> Result | None:
    """The reply, or None when the turn is not the runner's (today's path answers it).

    `compatible` is the resolver's placed entities, the source of the customer, brand
    and sales agent ids a sold line is narrowed by; `settled` is False when the resolver
    is still asking which one (its picker answers, never a sum over every match)."""
    resumed = _resumed(verdict, pending)
    if resumed is not None:
        kind, position, payload = resumed
        products = [p for p in (Product.from_wire(r) for r in payload.get("products") or []) if p]
        asks = [a for a in payload.get("sub_asks") or [] if isinstance(a, dict)]
        if not products or not asks:
            return None
        filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
        family = bool(payload.get("family"))
        if kind == PERIOD_KIND:
            start, end = payload["window"]
            asks = [a if _window(a) else {**a, "date_from": start, "date_to": end} for a in asks]
            basis = payload.get("basis") if payload.get("basis") in BASIS_WORD else None
            return _answer(run, products, asks, family=family, basis=basis, filters=filters, header_only_sold=True)
        return _answer(
            run,
            products,
            asks,
            family=family,
            basis="ordered" if position == 2 else "delivered",
            filters=filters,
            header_only_sold=True,
        )

    asks = _sub_asks(verdict)
    if len(asks) < 2:
        return None
    filters = _filters(verdict, compatible or [], settled=settled)
    if filters is None:
        return None
    products = _scoped_products(run, verdict, candidates)
    if not products:
        return None
    top_basis = verdict.get("basis") if verdict.get("basis") in BASIS_WORD else None
    # A null `family` is the exact code: a family head with variants was asked before this
    # ran (`scope_question`), and an exact variant is answered as typed (owner, 12 Oct).
    family = bool(verdict.get("family"))
    scoped = verdict.get(FAMILY_FOR)
    if family and isinstance(scoped, list):
        # The scope reply's "all" widens only the code it was asked about.
        keys = {turn_state.token_key(str(code)) for code in scoped}
        for p in products:
            if p.exact is not None and turn_state.token_key(p.code) not in keys:
                p.family = [p.exact]
    return _answer(run, products, asks, family=family, basis=top_basis, filters=filters)


def scope_question(
    run: Run,
    *,
    verdict: dict[str, Any],
    candidates: list[dict[str, Any]],
    replay: dict[str, Any],
) -> Result | None:
    """Round 6 G1: the ONE "which of them?" question, asked before either path answers,
    when the parser left `family` null and a code typed this message maps to two or more
    products. `replay` is the verdict the reply re-runs with the scope settled
    (`scope_reply`). Multi-ask turns only. None when nothing needs asking."""
    # Owner ruling Q1 (b), 9 Oct: a one-ask turn keeps today's path, no scope question.
    if verdict.get("family") is not None or len(_sub_asks(verdict)) < 2:
        return None
    products = _scoped_products(run, verdict, candidates, typed_only=True)
    # Owner ruling 12 Oct 2026: an exact variant code ("SRTWT7445-LV") is answered as typed;
    # only a family head or a bare prefix asks "only this one or all N?".
    ambiguous = next((p for p in products or [] if len(p.family) > 1 and not p.is_variant), None)
    if ambiguous is None:
        return None
    loc = run.localizer
    n = str(len(ambiguous.family))
    all_n = loc.fill(ALL_N, n=n)
    if ambiguous.exact is not None:
        text = loc.fill(VARIANT_QUESTION, code=ambiguous.code, n=n)
        # The option CODE is what the parser matches a reply against (`turn/question._options`
        # shows it, with the printed label beside it): a meaning, never the label's wording,
        # so "只要这个" or "all of them" read as a pick (crew live check 3).
        options = [
            {"code": OPTION_JUST, "label": loc.fill(JUST_CODE, code=ambiguous.code), "position": 1},
            {"code": OPTION_ALL, "label": all_n, "position": 2},
        ]
    else:
        # A code that is only a prefix has no "just this one": "all N" first, then every
        # code by number, so a reply naming one is a pick of that code.
        options = [{"code": OPTION_ALL, "label": all_n, "position": 1}]
        lines = [loc.fill(PREFIX_QUESTION, code=ambiguous.code, n=n), f"1. {all_n}"]
        if len(ambiguous.family) <= PICK_MAX:
            for i, row in enumerate(ambiguous.family, 2):
                options.append({"code": row["code"], "label": row["code"], "position": i})
                lines.append(f"{i}. {row['code']}")
        else:
            lines.append(loc.fill(TYPE_FULL_CODE))
        text = "\n".join(lines)
    question = turn_pending.ask(
        SCOPE_KIND,
        options,
        asked_at_turn=run.asked_at_turn,
        expects="pick",
        # `domain` (review S7): the domain the reply goes back to, so a bare "just this
        # one" is read under the ask's domain like any other pick (`turn/apply`).
        payload={
            "asked": ambiguous.to_wire(),
            "verdict": replay,
            "domain": verdict.get("domain_hint") or "purchase_cost",
        },
    )
    return Result(text=text, question=question, trace={"asked": SCOPE_KIND, "variants": len(ambiguous.family)})


def scope_reply(verdict: Any, pending: Any) -> dict[str, Any] | None:
    """The verdict a reply to the scope question re-runs: the asked turn's own, with
    `family` settled and, for a picked code, that code typed in place of the prefix. The
    reply is read only as the parser read it (`_answered_position`). None when the turn
    is not that reply."""
    if getattr(pending, "kind", None) != SCOPE_KIND or not isinstance(verdict, dict):
        return None
    payload = dict(getattr(pending, "payload", None) or {})
    replay, asked = payload.get("verdict"), Product.from_wire(payload.get("asked"))
    reply = verdict.get("open_question_answer")
    if not isinstance(replay, dict) or asked is None:
        return None
    options = getattr(pending, "options", None) or []
    position = _answered_position(reply, SCOPE_KIND, options) if isinstance(reply, dict) else None
    picked: str | None = None
    if position is not None:
        # "All" answers the question about the ASKED code only (review B1, a4a02904): any
        # other exact code typed in the message stays as typed (`answer`, FAMILY_FOR).
        if asked.exact is not None:
            return {**replay, "family": position == 2, FAMILY_FOR: [asked.code], "open_question_answer": None}
        if position == 1:
            return {**replay, "family": True, FAMILY_FOR: [asked.code], "open_question_answer": None}
        picked = next((o.get("code") for o in options if isinstance(o, dict) and o.get("position") == position), None)
    else:
        # A full code typed to the over-20 question (no numbered line offered): the parser's
        # item code or a product entity of this message, when it is a member of the asked
        # family. Parser fields only.
        members = {str(r.get("code")).casefold(): str(r["code"]) for r in asked.family if r.get("code")}
        typed = [str(i.get("code") or "") for i in (reply or {}).get("items") or [] if isinstance(i, dict)] if isinstance(reply, dict) else []
        typed += [
            str(e.get("canonical_code") or e.get("raw") or "")
            for e in verdict.get("entities") or []
            if isinstance(e, dict) and e.get("hint") in (None, "", "product") and e.get("current_message") is not False
        ]
        hits = {members[t.strip().casefold()] for t in typed if t.strip().casefold() in members}
        if len(hits) == 1 and asked.exact is None:
            picked = hits.pop()
    if not picked:
        return None
    key = turn_state.token_key(asked.code)
    entities = [
        {**e, "raw": picked, "canonical_code": picked}
        if isinstance(e, dict)
        and e.get("hint") in (None, "", "product")
        and turn_state.token_key(str(e.get("canonical_code") or e.get("raw") or "")) == key
        else e
        for e in replay.get("entities") or []
    ]
    return {**replay, "entities": entities, "family": False, "open_question_answer": None}


def vague_token(verdict: Any, focus_products: list[Any]) -> str | None:
    """Round 6 G4: the product word a message names with nothing asked about it, or None.

    The parser named no domain, no ask and no sub-ask, did not read the message as a
    follow-up (`continuation` and `anaphora.backward_reference` both false: round 6b, the
    live case-B parse was `confident: true`), and every entity is a product typed this
    message that the conversation is not already about. Such a message does not inherit
    the last turn's domain; the reply asks what is wanted. A real follow-up ("CB6633"
    after a stock ask) keeps today's carry. Reads the parser's fields only."""
    if not isinstance(verdict, dict):
        return None
    if verdict.get("domain_hint") or verdict.get("asks") or _sub_asks(verdict):
        return None
    anaphora = verdict.get("anaphora") if isinstance(verdict.get("anaphora"), dict) else {}
    if verdict.get("continuation") is not False or anaphora.get("backward_reference") is not False:
        return None
    entities = [e for e in verdict.get("entities") or [] if isinstance(e, dict)]
    if not entities:
        return None
    known = {
        turn_state.token_key(str(p.get(k) or ""))
        for p in focus_products or []
        if isinstance(p, dict)
        for k in ("canonical_code", "raw")
        if p.get(k)
    }
    for e in entities:
        if e.get("hint") not in (None, "", "product") or e.get("current_message") is False:
            return None
        if turn_state.token_key(str(e.get("canonical_code") or e.get("raw") or "")) in known:
            return None
    return ", ".join(str(e.get("raw") or e.get("canonical_code") or "").strip() for e in entities)


# --------------------------------------------------------------------------- #
# Reading the verdict
# --------------------------------------------------------------------------- #


def _sub_asks(verdict: Any) -> list[dict[str, Any]]:
    raw = verdict.get("sub_asks") if isinstance(verdict, dict) else None
    return [a for a in raw or [] if isinstance(a, dict) and isinstance(a.get("measure"), str)]


def _resumed(verdict: Any, pending: Any) -> tuple[str, int, dict[str, Any]] | None:
    kind = getattr(pending, "kind", None)
    if kind not in (BASIS_KIND, PERIOD_KIND) or not isinstance(verdict, dict):
        return None
    payload = dict(getattr(pending, "payload", None) or {})
    if kind == PERIOD_KIND:
        # The answer to "which period?" is the window the parser read off the reply; a
        # reply naming its own subject or asks is a new message (G4b), not the answer.
        window = _window({"date_from": verdict.get("date_filter_start"), "date_to": verdict.get("date_filter_end")})
        if window is None or verdict.get("entities") or _sub_asks(verdict):
            return None
        return kind, 0, {**payload, "window": window}
    reply = verdict.get("open_question_answer")
    if not isinstance(reply, dict):
        return None
    position = _answered_position(reply, kind, getattr(pending, "options", None) or [])
    if position is None:
        return None
    return kind, position, payload


def _answered_position(reply: dict[str, Any], kind: str, options: list[Any]) -> int | None:
    """Which option the PARSER read the reply as (crew live check 3: "已出货" to the basis
    question came back as an item naming the option, not a `pick`). Every reading is the
    parser's own: a pick of one position, one item naming a position or an option's code,
    or "all" to the variant question. This reads no word of the message."""
    offered = {
        o["position"]: o for o in options if isinstance(o, dict) and isinstance(o.get("position"), int)
    }
    picked = [p for p in reply.get("picked") or [] if isinstance(p, int) and not isinstance(p, bool)]
    if reply.get("mode") == "pick" and len(picked) == 1 and picked[0] in offered:
        return picked[0]
    if reply.get("mode") == "all" and kind == SCOPE_KIND:
        return next((p for p, o in offered.items() if o.get("code") == OPTION_ALL), None)
    items = [i for i in reply.get("items") or [] if isinstance(i, dict)]
    # A reply the parser read as no answer, a cancel or a no never resumes (review N2).
    if len(items) == 1 and reply.get("mode") not in (None, "cancel", "no"):
        item = items[0]
        if item.get("position") in offered:
            return int(item["position"])
        code = str(item.get("code") or "").strip().casefold()
        for position, option in offered.items():
            if code and str(option.get("code") or "").casefold() == code:
                return position
    return None


def _filters(verdict: dict[str, Any], compatible: list[dict[str, Any]], *, settled: bool) -> dict[str, list[str]] | None:
    """The customer, brand and sales agent ids the resolver placed for this message's
    non-product entities, by the `crm_report_ask` param each rides (G2: "sold to ABC
    Trading" is ABC's sales). None, today's path, when any such entity is of another
    kind, was not placed, or is still being asked about: the runner never answers over
    every customer (review S1)."""
    out: dict[str, list[str]] = {}
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict) or e.get("hint") in (None, "", "product"):
            continue
        param = FILTER_PARAMS.get(str(e.get("hint")))
        if param is None or not settled:
            return None
        ids = sorted(
            {str(c["uuid"]) for c in compatible if isinstance(c, dict) and c.get("entity_type") == e["hint"] and c.get("uuid")}
        )
        if not ids:
            return None
        out[param] = ids
    return out


def _scoped_products(
    run: Run, verdict: dict[str, Any], candidates: list[dict[str, Any]], *, typed_only: bool = False
) -> list[Product] | None:
    """`_products`, each family widened to the whole catalogue family (`run.family_of`).
    None when a family is past `FAMILY_MAX` (review R3: today's path)."""
    products = _products(verdict, candidates, run.family_of, typed_only=typed_only)
    if run.family_of is not None:
        for p in products:
            whole = run.family_of(p.code)
            if len(whole) > FAMILY_MAX:
                return None
            if len(whole) > len(p.family):
                p.family = whole
    return products


def _products(
    verdict: dict[str, Any],
    candidates: list[dict[str, Any]],
    family_of: Callable[[str], list[dict[str, str]]] | None = None,
    *,
    typed_only: bool = False,
) -> list[Product]:
    """Each typed product code, with the resolver's exact hit and the family of codes it
    is the prefix of, in typed order. A code with no exact hit is its prefix family
    (round 6 G1: the scope question lists it); one that is neither stops the runner."""

    def fold(value: Any) -> str:
        return turn_state.token_key(str(value or ""))

    rows = [c for c in candidates if isinstance(c, dict) and c.get("uuid")]
    out: list[Product] = []
    seen: set[str] = set()
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict) or e.get("hint") not in (None, "", "product"):
            continue
        if typed_only and e.get("current_message") is False:
            continue
        typed = str(e.get("canonical_code") or e.get("raw") or "").strip()
        key = fold(typed)
        if not key or key in seen:
            continue
        seen.add(key)
        code_of = lambda c: fold(c.get("canonical_code") or c.get("code"))  # noqa: E731
        exact = [c for c in rows if code_of(c) == key]
        family = [_ref(c) for c in rows if code_of(c).startswith(key)]
        if not exact and not family and family_of is not None:
            family = family_of(typed)
        if not exact and not family:
            return []
        if not exact and len(family) == 1:
            out.append(Product(code=family[0]["code"], exact=family[0], family=family))
            continue
        out.append(
            Product(
                code=str(exact[0].get("canonical_code") or exact[0].get("code") or typed) if exact else typed,
                exact=_ref(exact[0]) if exact else None,
                family=family,
            )
        )
    return out


def _ref(c: dict[str, Any]) -> dict[str, str]:
    return {"uuid": str(c["uuid"]), "code": str(c.get("canonical_code") or c.get("code") or "")}


# --------------------------------------------------------------------------- #
# The sold questions
# --------------------------------------------------------------------------- #


def _sold_question(
    run: Run,
    kind: str,
    products: list[Product],
    asks: list[dict[str, Any]],
    *,
    family: bool,
    basis: str | None,
    filters: dict[str, list[str]],
) -> tuple[str, Any]:
    """The period question (G3) or the basis one (Q4), the last line of the reply."""
    loc = run.localizer
    payload = {
        "products": [p.to_wire() for p in products],
        "sub_asks": asks,
        "family": family,
        "basis": basis,
        "filters": filters,
        "domain": "sales",
    }
    if kind == PERIOD_KIND:
        return loc.fill(PERIOD_QUESTION), turn_pending.ask(PERIOD_KIND, [], asked_at_turn=run.asked_at_turn, payload=payload)
    options = [
        {"code": "delivered", "label": loc.fill(AS_DELIVERED), "position": 1},
        {"code": "ordered", "label": loc.fill(AS_ORDERED), "position": 2},
    ]
    question = turn_pending.ask(BASIS_KIND, options, asked_at_turn=run.asked_at_turn, expects="pick", payload=payload)
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
    filters: dict[str, list[str]] | None = None,
    header_only_sold: bool = False,
) -> Result:
    filters = filters or {}
    if header_only_sold:
        asks = [a for a in asks if a.get("measure") in SOLD]
    # A sold ask with no period, then one with no basis, waits for its one question
    # (G3, then Q4), unless the contact may not see sales at all (then it is refused on
    # its line, no question) or its axis is one no query answers.
    answerable = [
        a
        for a in asks
        if a.get("measure") in SOLD
        and GRANT[a["measure"]] in run.grants
        and not (a.get("group_by") and a.get("group_by") not in AXIS)
    ]
    no_period = [a for a in answerable if not _window(a)]
    no_basis = [a for a in answerable if _window(a) and not (a.get("basis") in BASIS_WORD or basis)]
    waiting = no_period + no_basis
    calls = _Calls(run, filters)
    for a in asks:
        if a.get("measure") == "sold_by" and a.get("group_by") in AXIS:
            key = (_window(a), a.get("basis") if a.get("basis") in BASIS_WORD else basis)
            calls.sold_groups.setdefault(key, a["group_by"])
    blocks: list[str] = []
    for product in products:
        ids = product.ids(family)
        head = (
            run.localizer.fill(FAMILY_HEADER, code=product.code, n=str(len(ids)))
            if (family or product.exact is None) and len(ids) > 1
            else product.code
        )
        lines = [head]
        for ask in asks:
            if ask in waiting:
                continue
            lines.extend(_lines(run, calls, ask, ids, basis))
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    question = None
    if waiting:
        kind = PERIOD_KIND if no_period else BASIS_KIND
        line, question = _sold_question(run, kind, products, waiting, family=family, basis=basis, filters=filters)
        text = text + "\n" + line
    answered = [r for p in products for r in (p.family if (family or p.exact is None) and p.family else [p.exact])]
    return Result(
        text=text,
        question=question,
        trace={
            "family": family,
            "basis": basis,
            "filters": sorted(filters),
            "calls": [name for name, _ in calls.made],
            "failed": calls.failures,
        },
        focus_products=answered,
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
    try:
        if measure == "received_qty":
            rows = calls.rows(GRN_TOOL, {"product_ids": ids, **_dates(window)})
            return [f"{label}: {_qty(sum(_num(r.get('received_qty')) for r in rows))}"]
        if measure == "last_receipt":
            # The single newest SPO line of the family; a tie takes the first returned.
            rows = [r for r in calls.rows(SPO_TOOL, {"product_ids": ids, "top_n": 1}) if r.get("spo_date")]
            if not rows:
                return [f"{label}: {loc.fill(NONE_ON_RECORD)}"]
            last = max(rows, key=lambda r: str(r.get("spo_date")))
            value = loc.fill(
                RECEIPT_VALUE,
                date=_date(last.get("spo_date")),
                qty=_qty(_num(last.get("spo_quantity"))),
                spo=str(last.get("spo_number") or ""),
            )
            return [f"{label}: {value}"]
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
            # `include_sellable` makes the route answer `stock_summary` (the policy's own
            # allowed-location totals) in EVERY stock policy mode; the detailed default
            # answers paged rows and no summary (review B1). Only `total_on_hand` is read:
            # the sellable figure the flag adds is never printed.
            # `limit`: the route's own maximum. Its summary covers only the rows of the page
            # it answers (50 by default), so a family over 50 stock rows read short (review R1).
            payload = calls.payload(STOCK_TOOL, {"product_ids": ids, "include_sellable": True, "limit": STOCK_LIMIT})
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
            **calls.filters,
            **_dates(window),
            "basis": ask_basis,
            "measure": "qty",
        }
        group = ask["group_by"] if measure == "sold_by" else calls.sold_groups.get((window, ask_basis))
        if group:
            args.update(group_by=group, top_n=REPORT_TOP_N)
        payload = calls.payload(REPORT_TOOL, args)
        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or status not in (None, "ok"):
            calls.failed(measure, REPORT_TOOL, status=status, message=(payload or {}).get("message") if isinstance(payload, dict) else None)
            if status == "busy":
                return [f"{label}: {loc.fill(TOO_MANY_REPORTS)}"]
            return [f"{label}: {loc.fill(CHECK_FAILED)}"]
        if measure == "sold_total":
            return [f"{label}: {_qty(_num((payload.get('total') or {}).get('qty')))}"]
        rows = [r for r in payload.get("rows") or [] if isinstance(r, dict)]
        if not rows:
            return [f"{label}: 0"]
        return [f"{label}:"] + [
            f"{r.get('rank') or i}. {r.get('name') or '-'}: {_qty(_num(r.get('qty')))}" for i, r in enumerate(rows, 1)
        ]
    except _CallFailed as failed:
        calls.failed(measure, failed.tool, status=failed.status, message=failed.message)
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
    if code in ("", "MYR", "RM"):
        from app.services.chatbot import composers

        # CHAT-COMPOSER M2: one currency word for the ringgit (R13).
        return "MYR" if composers.enabled() else "RM"
    return code


# --------------------------------------------------------------------------- #
# Tool calls, one per distinct (tool, args)
# --------------------------------------------------------------------------- #


class _CallFailed(Exception):
    def __init__(self, tool: str = "", status: Any = None, message: Any = None) -> None:
        super().__init__(tool)
        self.tool, self.status, self.message = tool, status, message


class _Calls:
    def __init__(self, run: Run, filters: dict[str, list[str]] | None = None) -> None:
        self.run = run
        #: The customer / brand / sales agent ids every sold line is narrowed by (G2).
        self.filters = dict(filters or {})
        self.made: list[tuple[str, dict[str, Any]]] = []
        #: (window, basis) -> the group_by of a sold_by ask with that window and basis.
        #: A sold_total asked beside it reads the grouped call's own `total` (the whole
        #: set), so the message makes ONE report call, not two: `crm_report_ask` is rate
        #: limited per contact (crew live check, 9 Oct: the second call came back busy).
        self.sold_groups: dict[tuple[Any, Any], str] = {}
        #: Why each failed line failed, for the trace (crew: a line that says it could
        #: not check must say which call and why).
        self.failures: list[dict[str, Any]] = []
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
            raise _CallFailed(name, status="exception")
        if isinstance(value, dict) and (value.get("error") or (value.get("status_code") or 0) >= 400):
            raise _CallFailed(name, status=value.get("status_code") or "error", message=value.get("error") or value.get("detail"))
        return value

    def failed(self, measure: Any, tool: str, *, status: Any = None, message: Any = None) -> None:
        row = {"measure": measure, "tool": tool, "status": status, "message": str(message)[:200] if message else None}
        self.failures.append(row)
        logger.warning("sub_asks: the %s line could not be answered: %s", measure, row)

    def rows(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self.payload(name, args)
        data = payload.get("data") if isinstance(payload, dict) else payload
        return [r for r in data or [] if isinstance(r, dict)] if isinstance(data, list) else []
