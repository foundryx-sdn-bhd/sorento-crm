"""The `sales_ranking` ask: sales ranked or totalled by one dimension (REPORT-ENGINE slice 1b).

`documentation/plans/chatbot/PLAN-report-engine.md` section 11. The lane half of
`crm_report_ask` (`GET /api/v1/order-management/report-ask`): every gate (grant, audience,
company, location policy) is the ROUTE's, from `contact_id`; this module only turns the
parser's reading into the route's params, and asks for the two fields the route requires
(the period and how many, then, for a ranking, the metric, and always the basis) through the
shared `required_fields` helper. A ranking never defaults the metric or the basis: it asks
(owner ruling, 4 Oct 2026), through `ranking_ask.next_question`, as top selling does.

* `take_words` (engine seam): a FRESH ask's brand, sales agent and category words come off
  the entity list onto `report_ask_words`, so the generic resolver never reads them as
  customers (the top selling lesson, `engine._top_selling_narrowing`).
* `settle` (lane): words to ids, `group_by` to the catalogue's word, the required fields;
  either a line to send instead of running, or the outcome to build the args from.
* `route_args`: the settled outcome as the route's query params.
* `error_line`: a route refusal (403) or a 404, said as one line.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.chatbot import jsc
from app.services.chatbot import required_fields as rf
from app.services.sales_report_service import TOP_SELLING_N_CEILING
from app.services.chatbot.lanes.business import ranking_ask
from app.services.chatbot.lanes.business import services as business_services

ASK_NAME = "sales_ranking"
TOOL = "crm_report_ask"

PERIOD_QUESTION = "Which period? For example this month, September, 2026, or 1 to 15 Sep."
TOP_N_QUESTION = "How many? For example top 5."
#: Said above the ranking that ran with the default count after two unreadable replies.
DEFAULT_TOP_N = 10
DEFAULT_TOP_N_NOTE = "I couldn't read how many, so here is the top 10."
CANCELLED = "Sales ranking cancelled."
GIVE_UP = (
    "I still can't read '{word}'. Ask again in one message, "
    "e.g. top 5 sales agents for Sorento this month by amount, delivered."
)
#: The route's own refusal for a dealer's staff breakdown (`report_ask._DEALER_MESSAGE`).
DEALER_REFUSAL = "That breakdown is not available for your account."
CATALOGUE_LINE = (
    "I can rank sales by customer, product, brand, category, sales agent, location, channel or month."
)

#: The parser's `group_by` -> the route's (`reports.ask.DIMENSIONS`). Anything else, `date`
#: included, is outside the catalogue and is said, never guessed.
GROUP_BY = {
    "customer": "customer",
    "product": "product",
    "brand": "brand",
    "category": "category",
    "sales_agent": "sales_agent",
    "warehouse": "location",
    "location": "location",
    "channel": "channel",
    "month": "month",
}

#: The entity hints `take_words` moves, and the noun each is said with when it names nothing.
WORD_HINTS = {"brand": "brand", "sales_agent": "sales agent", "category": "category"}

#: One ceiling, shared with the route and the top-selling lane (owner, 30 Sep 2026).
TOP_N_MIN, TOP_N_MAX = 1, TOP_SELLING_N_CEILING
#: A month breakdown is a trend, never asked "How many?": every month of the period (1b code
#: review S2). This is its own row count, not a second top-N limit.
MONTH_TOP_N = 100

#: The plural each word hint's "matches several" line names (1b code review S3).
SEVERAL_NOUNS = {"brand": "brands", "category": "categories"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# The required fields
# --------------------------------------------------------------------------- #


def _day(value: Any) -> date | None:
    text = jsc.js_string(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _label(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


def period_from(start: Any, end: Any) -> rf.Resolved | None:
    """The parser's two dates as the settled period. A start alone ("since September") runs
    to today, Malaysia time; an end alone is no period (1b code review N2: it is asked)."""
    from app.services.reports.registry import today_malaysia

    first, last = _day(start), _day(end)
    if first is None:
        return None
    if last is None:
        last = max(first, today_malaysia())
    if first > last:
        first, last = last, first
    return rf.Resolved(
        "ok", value={"from": first.isoformat(), "to": last.isoformat()}, label=f"{_label(first)} to {_label(last)}"
    )


def _resolve_period(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    """An answering turn's period: the reply's OWN parsed dates (`settle` hands them in as
    `reply_dates`); a reply with no date is a miss."""
    dates = _dict(extras.get("reply_dates"))
    return period_from(dates.get("start"), dates.get("end")) or rf.Resolved("unknown")


def top_n_from(value: Any) -> rf.Resolved:
    if isinstance(value, bool):
        return rf.Resolved("unknown")
    if isinstance(value, (int, float)) and float(value).is_integer() and TOP_N_MIN <= int(value) <= TOP_N_MAX:
        return rf.Resolved("ok", value=int(value), label=str(int(value)))
    if isinstance(value, (int, float)) and float(value).is_integer() and value > TOP_N_MAX:
        from app.services.chatbot.lanes.business import top_selling_ceiling_note  # the one wording; lazy, the package imports this module

        return rf.Resolved("unknown", note=top_selling_ceiling_note({"top_n": value}) or "")
    return rf.Resolved("unknown")


def _resolve_top_n(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    """An answering turn's count: the PARSER's own `top_n` for the reply (`settle` hands it in
    as `reply_top_n`, as it does `reply_dates`); the reply's words are not looked at."""
    return top_n_from(extras.get("reply_top_n"))


#: The parser's keys for an answer to the metric question: `measure` (qty | amount) or the top
#: selling key `rank_by` (a bare reply under the question parses as a top selling reading).
METRIC_FROM_RANK_BY = {"quantity": "qty", "amount": "amount"}
BASES = ("delivered", "ordered", "outstanding")


def metric_from(parse_output: dict[str, Any]) -> str | None:
    """The metric the parser read: `measure`, else `rank_by` (quantity is qty); None if neither."""
    if parse_output.get("measure") in ("qty", "amount"):
        return parse_output["measure"]
    return METRIC_FROM_RANK_BY.get(parse_output.get("rank_by"))


def basis_from(parse_output: dict[str, Any]) -> str | None:
    basis = parse_output.get("basis")
    return basis if basis in BASES else None


def _resolve_metric(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    """An answering turn's metric: the PARSER's own reading of the reply (`reply_metric`)."""
    metric = extras.get("reply_metric")
    return rf.Resolved("ok", value=metric, label=metric) if metric in ("qty", "amount") else rf.Resolved("unknown")


def _resolve_basis(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    """An answering turn's basis: the PARSER's own reading of the reply (`reply_basis`)."""
    basis = extras.get("reply_basis")
    return rf.Resolved("ok", value=basis, label=basis) if basis in BASES else rf.Resolved("unknown")


def _metric_question() -> str:
    asked = ranking_ask.next_question(metric_settled=False, basis_settled=False, basis_line=ranking_ask.RANKING_BASIS_LINE)
    return asked[1] if asked else ranking_ask.METRIC_LINE


def _basis_question() -> str:
    asked = ranking_ask.next_question(metric_settled=True, basis_settled=False, basis_line=ranking_ask.RANKING_BASIS_LINE)
    return asked[1] if asked else ranking_ask.RANKING_BASIS_LINE


PERIOD = rf.FieldSpec(name="period", noun="period", question=PERIOD_QUESTION, resolve=_resolve_period, allow_all=False)
TOP_N = rf.FieldSpec(name="top_n", noun="number", question=TOP_N_QUESTION, resolve=_resolve_top_n, allow_all=False)

METRIC = rf.FieldSpec(name="metric", noun="measure", question=ranking_ask.METRIC_LINE, question_fn=_metric_question, resolve=_resolve_metric, allow_all=False)
BASIS = rf.FieldSpec(name="basis", noun="basis", question=ranking_ask.RANKING_BASIS_LINE, question_fn=_basis_question, resolve=_resolve_basis, allow_all=False)

SALES_RANKING_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(PERIOD, TOP_N, METRIC, BASIS),
    reroute={
        "message_type": "business_query",
        "domain_hint": "order",
        "intent_hint": "check_order",
        "order_status": ASK_NAME,
    },
    cancelled=CANCELLED,
    give_up=GIVE_UP,
    answer_drops=("sales_channel", "report_ask_words"),
))


# --------------------------------------------------------------------------- #
# Engine seam
# --------------------------------------------------------------------------- #


def hold_words(verdict: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Engine seam before grounding, a `sales_ranking` verdict: its brand, sales agent and
    category entities, held out of the descriptor grounding and handed back after it."""
    if jsc.js_string(verdict.get("order_status") or "").strip() != ASK_NAME:
        return verdict, []
    entities = verdict.get("entities")
    if not isinstance(entities, list):
        return verdict, []
    held = [
        e for e in entities
        if isinstance(e, dict) and jsc.js_string(e.get("hint") or "").strip().lower() in WORD_HINTS
    ]
    if not held:
        return verdict, []
    return {**verdict, "entities": [e for e in entities if not any(e is h for h in held)]}, held


def take_words(verdict: dict[str, Any], text: str) -> dict[str, Any]:
    """Engine seam, a FRESH `sales_ranking` ask: this message's brand, sales agent and
    category words move off the entity list onto `report_ask_words` (raw words per hint).
    A carried row of those hints is dropped too: it is not this ask's filter, and the
    generic resolver would read it as a customer. `text` is taken for the seam's shape: the
    parser, never the words, decides what the message meant."""
    _ = text
    if jsc.js_string(verdict.get("order_status") or "").strip() != ASK_NAME or verdict.get("required_ask"):
        return verdict
    kept: list[Any] = []
    words: dict[str, list[str]] = {hint: [] for hint in WORD_HINTS}
    for e in verdict.get("entities") or []:
        if not isinstance(e, dict):
            continue
        hint = jsc.js_string(e.get("hint") or "").strip().lower()
        raw = " ".join(jsc.js_string(e.get("raw") or "").split())
        if hint == "category":
            # AC-HT8-12: the parser's English class word, before the verbatim raw.
            raw = " ".join(jsc.js_string(e.get("canonical_code") or "").split()) or raw
        if hint not in WORD_HINTS:
            kept.append(e)
            continue
        if raw and e.get("current_message") is not False and raw not in words[hint]:
            words[hint].append(raw)
    return {**verdict, "entities": kept, "report_ask_words": words}


def names_its_own_ask(verdict: dict[str, Any], open_slot: dict[str, Any] | None = None) -> bool:
    """Engine seam before `required_fields.reply_verdict`: a `sales_ranking` verdict that is not a
    refine is a NEW ask, so an open period / how many question is dropped, not answered by it, when
    it names a current-message filter entity or a `group_by` DIFFERENT from the one the open slot
    holds (the same axis echoed is an answer). Parser fields only."""
    if jsc.js_string(verdict.get("order_status") or "").strip() != ASK_NAME or verdict.get("ranking_refine") is True:
        return False
    if any(
        isinstance(e, dict) and e.get("current_message") is not False and jsc.js_string(e.get("hint") or "").strip()
        for e in jsc.array(verdict.get("entities"))
    ):
        return True
    raw_group = jsc.js_string(verdict.get("group_by") or "").strip().lower()
    if not raw_group:
        return False
    held = _dict(_dict(_dict(open_slot).get("extras")).get("args")).get("group_by")
    return GROUP_BY.get(raw_group, raw_group) != held


def dealer_location_words(parse_output: dict[str, Any]) -> dict[str, Any]:
    """Engine seam, a DEALER's (customer-scoped) fresh `sales_ranking` ask, 1b fix round F1:
    this message's location words come off the resolver's entity list onto
    `report_ask_words["warehouse"]`, so no warehouse code is ever looked up for a dealer;
    the lane refuses the ask off the word alone."""
    if jsc.js_string(parse_output.get("order_status") or "").strip() != ASK_NAME or parse_output.get("required_ask"):
        return parse_output
    moved = [
        " ".join(jsc.js_string(e.get("raw") or e.get("canonical_code") or "").split())
        for e in _current_entities(parse_output, "warehouse")
    ]
    if not moved:
        return parse_output
    words = dict(_dict(parse_output.get("report_ask_words")))
    words["warehouse"] = [w for w in moved if w]
    return {
        **parse_output,
        "entities": [
            e for e in jsc.array(parse_output.get("entities"))
            if not (isinstance(e, dict) and e in _current_entities(parse_output, "warehouse"))
        ],
        "report_ask_words": words,
    }


# --------------------------------------------------------------------------- #
# The lane's call
# --------------------------------------------------------------------------- #


def _resolve_word(db: Any, hint: str, word: str) -> tuple[list[str], list[str]]:
    """`(ids, names)` a word names. The resolvers let an exact name or code win alone."""
    if db is None:
        return [], []
    if hint == "brand":
        rows = [(b[0], b[1]) for b in business_services.resolve_brand_token(db, word)]
    elif hint == "sales_agent":
        rows = [(a[0], a[1]) for a in business_services.resolve_sales_agent_token(db, word)]
    else:
        rows = [(c[0], c[2] or c[1]) for c in business_services.resolve_category_token(db, word)]
    return [i for i, _n in rows], [n for _i, n in rows]


def _current_kinds(parse_output: dict[str, Any]) -> set[str]:
    return {
        jsc.js_string(e.get("hint") or "").strip().lower()
        for e in jsc.array(parse_output.get("entities"))
        if isinstance(e, dict) and e.get("current_message") is not False
    }


def _gate_ids(entities: Any, kind: str) -> list[str]:
    from app.services.chatbot.lanes.business.fetch import is_uuid

    out: list[str] = []
    for e in jsc.array(entities):
        if isinstance(e, dict) and e.get("entity_type") == kind and is_uuid(e.get("uuid")):
            uuid = jsc.js_string(e["uuid"])
            if uuid not in out:
                out.append(uuid)
    return out


def _current_entities(parse_output: dict[str, Any], hint: str) -> list[dict[str, Any]]:
    return [
        e
        for e in jsc.array(parse_output.get("entities"))
        if isinstance(e, dict)
        and e.get("current_message") is not False
        and jsc.js_string(e.get("hint") or "").strip().lower() == hint
    ]


def _dealer_outside(parse_output: dict[str, Any], group_by: str | None) -> bool:
    """1b fix round F1: does a dealer's ask reach past `reports.ask.DEALER_KEYS`? A staff
    dimension, or a sales agent, location or channel filter. Read off the words alone,
    BEFORE any is resolved, so the unknown-word line can never probe agent or location
    names for a dealer (the route refuses the same asks, `report_dimension_not_allowed`)."""
    from app.services.reports.ask import DEALER_KEYS

    words = _dict(parse_output.get("report_ask_words"))
    named = any(
        " ".join(jsc.js_string(w).split())
        for hint in ("sales_agent", "warehouse")
        for w in jsc.array(words.get(hint))
    )
    return (
        (group_by is not None and group_by not in DEALER_KEYS)
        or named
        or bool(_current_entities(parse_output, "warehouse"))
        or parse_output.get("sales_channel") in ("dealer", "project")
    )


_WORD_PARAMS = (("brand", "brand_ids"), ("sales_agent", "sales_agent_ids"), ("category", "category_ids"))


def _word_filters(
    db: Any, parse_output: dict[str, Any], pairs: tuple[tuple[str, str], ...] = _WORD_PARAMS
) -> tuple[dict[str, Any] | None, str | None]:
    """The brand / sales agent / category ids the verdict's `report_ask_words` resolve to, or
    the one line to say instead. Shared by a fresh ask and a ranking refine."""
    out: dict[str, Any] = {}
    words = _dict(parse_output.get("report_ask_words"))
    for hint, param in pairs:
        ids: list[str] = []
        for word in jsc.array(words.get(hint)):
            word = " ".join(jsc.js_string(word).split())
            if not word:
                continue
            if hint == "category":
                # One definition with top selling's category filter (owner, 4 Oct 2026):
                # code or name, then the class vocabulary ("water closet").
                from app.services.chatbot.lanes import business as business_pkg

                found, _named, ambiguous, unknown = business_pkg._top_selling_category_ids(
                    {"category_words": [word]}, db=db
                )
                if unknown or (not found and not ambiguous):
                    # A word that matched nothing is said, never dropped from the filter.
                    return None, f"I don't know '{word}' as a {WORD_HINTS[hint]}."
                if ambiguous:
                    listed = ", ".join(sorted(set(ambiguous)))
                    return None, f"'{word}' matches several {SEVERAL_NOUNS[hint]}: {listed}. Ask again naming one."
                ids.extend(i for i in found if i not in ids)
                continue
            found, names = _resolve_word(db, hint, word)
            if not found:
                return None, f"I don't know '{word}' as a {WORD_HINTS[hint]}."
            distinct = {" ".join(n.split()).casefold(): " ".join(n.split()) for n in reversed(names)}
            if len(distinct) > 1 and hint in SEVERAL_NOUNS:
                # 1b code review S3: never a silent widening (the PR #1273 rule). Hits sharing
                # one name (a brand per company) are ONE brand, their ids a union; several
                # sales agent rows for one word stay a union too: one person's accounts.
                listed = ", ".join(sorted(distinct.values()))
                return None, f"'{word}' matches several {SEVERAL_NOUNS[hint]}: {listed}. Ask again naming one."
            ids.extend(i for i in found if i not in ids)
        if ids:
            out[param] = ids
    return out, None


def _fresh_args(
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    """The route params a fresh ask names (every one but the period and top_n), or the one
    line to say instead of running. `dealer`: the turn's contact is customer-scoped."""
    raw_group = jsc.js_string(parse_output.get("group_by") or "").strip().lower()
    group_by = GROUP_BY.get(raw_group) if raw_group else None
    if raw_group and group_by is None:
        return None, CATALOGUE_LINE
    if dealer and _dealer_outside(parse_output, group_by):
        return None, DEALER_REFUSAL

    # Neither the basis nor the metric is defaulted: one the message did not name is asked.
    args: dict[str, Any] = {"sort": "asc" if parse_output.get("rank_direction") == "bottom" else "desc"}
    if basis_from(parse_output):
        args["basis"] = basis_from(parse_output)
    if metric_from(parse_output):
        args["measure"] = metric_from(parse_output)
    if group_by:
        args["group_by"] = group_by
    channel = parse_output.get("sales_channel")
    if channel in ("dealer", "project"):
        args["channel"] = channel

    filters, line = _word_filters(db, parse_output)
    if filters is None:
        return None, line
    args.update(filters)

    # The gate's resolved rows, of the kinds THIS message named (a carried subject from an
    # earlier question is not this ask's filter).
    kinds = _current_kinds(parse_output)
    customer_ids = _gate_ids(entities, "customer") if "customer" in kinds else []
    if customer_ids:
        args["customer_ids"] = customer_ids
    if "product" in kinds:
        # 1b code review S4: the sales report's PREFIX rule (S19), so "SRT5674" covers
        # "SRT5674-N"; several codes travel as the prefix they share.
        from app.services.chatbot.lanes.business.fetch import sales_report_product_code

        products = [e for e in jsc.array(entities) if isinstance(e, dict) and e.get("entity_type") == "product"]
        code = sales_report_product_code(products, {})
        if code:
            args["product_code"] = code

    codes: list[str] = []
    for e in _current_entities(parse_output, "warehouse"):
        token = jsc.js_string(e.get("raw") or e.get("canonical_code") or "").strip()
        if not token:
            continue
        found = business_services.resolve_warehouse_token(db, token) if db is not None else []
        if not found:
            return None, f"I don't know '{token}' as a location."
        codes.extend(c for c in found if c not in codes)
    if codes:
        args["warehouse_codes"] = codes
    return args, None


#: The keys `_fresh_args` always sets (or an axis); any other key it sets is a filter.
_NON_FILTER_ARGS = frozenset({"basis", "measure", "sort", "group_by"})


def _given_top_n(args: dict[str, Any], top_n: Any) -> dict[str, Any]:
    """The number as the helper's `given`: a total needs none (settled as None, never
    asked); a number the first message named is taken, or read as a word (a miss)."""
    if not args.get("group_by"):
        return {"top_n": rf.Resolved("ok", value=None, label="")}
    got = top_n_from(top_n)
    if args.get("group_by") == "month" and got.status != "ok":
        return {"top_n": rf.Resolved("ok", value=MONTH_TOP_N, label=str(MONTH_TOP_N))}
    if top_n is None:
        return {}
    return {"top_n": got if got.status == "ok" else jsc.js_string(top_n)}


def _given_choices(args: dict[str, Any]) -> dict[str, Any]:
    """The metric and the basis as the helper's `given`, off the args a message named. A total
    and a month trend show both measures, so they are never asked the metric (settled as None);
    the basis is always asked unless named. Anything not named is left out, so it is asked."""
    given: dict[str, Any] = {}
    if not args.get("group_by") or args.get("group_by") == "month":
        given["metric"] = rf.Resolved("ok", value=args.get("measure"), label=args.get("measure") or "")
    elif args.get("measure"):
        given["metric"] = rf.Resolved("ok", value=args["measure"], label=args["measure"])
    if args.get("basis"):
        given["basis"] = rf.Resolved("ok", value=args["basis"], label=args["basis"])
    return given


def _reply_choices(args: dict[str, Any], parse_output: dict[str, Any]) -> dict[str, Any]:
    """An answering turn that carries the OTHER choice too ("qty delivered"): taken from the
    parser's keys when the first message left it open. The asked field is read by the helper."""
    given: dict[str, Any] = {}
    metric, basis = metric_from(parse_output), basis_from(parse_output)
    if metric and args.get("group_by") not in (None, "month") and not args.get("measure"):
        given["metric"] = rf.Resolved("ok", value=metric, label=metric)
    if basis and not args.get("basis"):
        given["basis"] = rf.Resolved("ok", value=basis, label=basis)
    return given


def settle(
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool = False
) -> tuple[rf.Outcome | None, str | None]:
    """Every required field settled, or the line to send instead of running. Returns
    `(outcome, None)` (send `outcome.reply` with `outcome.slot` unless `outcome.done`), or
    `(None, line)` for a word or a dimension the ask cannot run with.

    The slot's extras carry the first message's args and its number (`top_n`), so an
    answering turn runs the first message's ask; the period question is asked first."""
    slot = parse_output.get("required_ask")
    if isinstance(slot, dict) and slot.get("ask") == ASK_NAME:
        extras = _dict(slot.get("extras"))
        args = _dict(extras.get("args"))
        reply_dates = {"start": parse_output.get("date_filter_start"), "end": parse_output.get("date_filter_end")}
        outcome = rf.collect(
            db,
            SALES_RANKING_ASK,
            slot=slot,
            reply=jsc.js_string(parse_output.get("required_ask_reply") or ""),
            given={**_given_choices(args), **_given_top_n(args, extras.get("top_n")), **_reply_choices(args, parse_output)},
            extras={
                "reply_dates": reply_dates,
                "reply_top_n": parse_output.get("top_n"),
                "reply_metric": metric_from(parse_output),
                "reply_basis": basis_from(parse_output),
            },
        )
        if slot.get("asking") == "top_n" and outcome.slot is None and not outcome.done and not outcome.cancelled:
            # The helper gave up on the count (a second miss): run the default top 10 and say
            # so (crew ruling). The period field keeps the helper's own give-up.
            values = {**outcome.values, "top_n": {"value": DEFAULT_TOP_N, "label": str(DEFAULT_TOP_N)}}
            return rf.Outcome(values=values, extras={**outcome.extras, "note": DEFAULT_TOP_N_NOTE}), None
        return outcome, None

    frame = parse_output.get("sales_ranking_frame")
    if parse_output.get("ranking_refine") is True and isinstance(frame, dict) and frame:
        return _refine(db, parse_output, frame, dealer=dealer)

    args, line = _fresh_args(db, parse_output, entities, dealer=dealer)
    if args is None:
        return None, line
    if (
        parse_output.get("ranking_refine") is not True
        and not dealer  # the route forces a dealer's links, so a plain total can never widen
        and not args.get("group_by")
        and not set(args) - _NON_FILTER_ARGS
    ):
        # Not a valid ask: no axis and nothing to total. Say what can be ranked and open nothing.
        return None, CATALOGUE_LINE
    top_n = parse_output.get("top_n")
    given = {**_given_choices(args), **_given_top_n(args, top_n)}
    period = period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end"))
    if period is not None and parse_output.get("ranking_refine") is not True:
        # A refine with no ranking held has nothing to refine: it is a fresh ask, and a
        # period alone never runs a total (it asks).
        given["period"] = period
    return rf.collect(db, SALES_RANKING_ASK, given=given, extras={"args": args, "top_n": top_n}), None


def _refine(
    db: Any, parse_output: dict[str, Any], frame: dict[str, Any], *, dealer: bool = False
) -> tuple[rf.Outcome | None, str | None]:
    """The parser said this message only refines the sales ranking that ran (`ranking_refine`):
    the held route args re-run with ONLY the keys the verdict changed (count, period, basis,
    measure). A dealer's refine passes the same check a fresh ask does, over the HELD args."""
    if dealer:
        from app.services.reports.ask import DEALER_KEYS

        if (
            frame.get("group_by") not in (None, *DEALER_KEYS)
            or frame.get("sales_agent_ids")
            or frame.get("warehouse_codes")
            or frame.get("channel")
        ):
            return None, DEALER_REFUSAL
    args = {k: v for k, v in frame.items() if k not in ("date_from", "date_to", "top_n")}
    channel = parse_output.get("sales_channel")
    if channel in ("dealer", "project"):
        if dealer:
            return None, DEALER_REFUSAL
        args["channel"] = channel
    # A brand or category word the refine names replaces that dimension; the rest stays held.
    filters, line = _word_filters(db, parse_output, (("brand", "brand_ids"), ("category", "category_ids")))
    if filters is None:
        return None, line
    args.update(filters)
    if parse_output.get("basis") in ("ordered", "delivered", "outstanding"):
        args["basis"] = parse_output["basis"]
    if parse_output.get("measure") in ("qty", "amount"):
        args["measure"] = parse_output["measure"]
    top_n = parse_output.get("top_n")
    if top_n is None:
        top_n = frame.get("top_n")
    period = period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end")) or period_from(
        frame.get("date_from"), frame.get("date_to")
    )
    # A held ranking has both choices (its args ran): a refine never asks.
    given = {**_given_choices(args), **_given_top_n(args, top_n)}
    for name, key in (("metric", "measure"), ("basis", "basis")):
        if name not in given:
            given[name] = rf.Resolved("ok", value=args.get(key), label=args.get(key) or "")
    if period is not None:
        given["period"] = period
    return rf.collect(db, SALES_RANKING_ASK, given=given, extras={"args": args, "top_n": top_n}), None


def route_args(outcome: rf.Outcome) -> dict[str, Any]:
    """The settled ask as `crm_report_ask`'s params (the route's names)."""
    args = dict(_dict(outcome.extras.get("args")))
    for name, key in (("metric", "measure"), ("basis", "basis")):
        settled = _dict(outcome.values.get(name)).get("value")
        if settled:
            args[key] = settled
    period = _dict(_dict(outcome.values.get("period")).get("value"))
    args["date_from"] = period.get("from")
    args["date_to"] = period.get("to")
    top_n = (outcome.values.get("top_n") or {}).get("value")
    if args.get("group_by") and isinstance(top_n, int):
        args["top_n"] = top_n
    return args


def error_line(envelope: Any) -> str | None:
    """A route refusal said as its own message (403: `report_dimension_not_allowed`,
    `customer_not_permitted`, `sales_report_not_enabled`), a 404 as `I couldn't find that
    <thing>.`; None for any other error (the generic error path answers it)."""
    if not isinstance(envelope, dict):
        return None
    status = envelope.get("status_code")
    detail = _dict(envelope.get("detail"))
    message = jsc.js_string(detail.get("message") or "").strip()
    if status == 403 and message:
        return message
    if status == 404:
        thing = message.split(" not found", 1)[0].strip().lower() if " not found" in message else ""
        return f"I couldn't find that {thing}." if thing else "I couldn't find that."
    return None
