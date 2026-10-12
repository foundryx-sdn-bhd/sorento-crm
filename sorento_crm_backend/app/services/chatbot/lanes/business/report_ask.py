"""The `sales_ranking` ask: sales ranked or totalled by one dimension (REPORT-ENGINE slice 1b).

`documentation/plans/chatbot/PLAN-report-engine.md` section 11. The lane half of
`crm_report` (`GET /api/v1/order-management/report`, REPORTS-STEP2 S2; it was `crm_report_ask`): every gate (grant, audience,
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

from app.services import chatbot_reply_copy as reply_copy_words
from app.services.chatbot import jsc
from app.services.chatbot import required_fields as rf
from app.services.sales_report_service import TOP_SELLING_N_CEILING
from app.services.chatbot.lanes.business import ranking_ask
from app.services.chatbot.lanes.business import services as business_services

ASK_NAME = "sales_ranking"
TOOL = "crm_report"

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

#: REPORT-DRILLDOWN (`documentation/plans/chatbot/PLAN-report-drilldown-08oct.md`): a ranking
#: of the rows the last ranking printed. The nouns each axis is said with, one and several.
AXIS_NOUNS = {
    "customer": ("customer", "customers"),
    "product": ("product", "products"),
    "brand": ("brand", "brands"),
    "category": ("category", "categories"),
    "sales_agent": ("sales agent", "sales agents"),
    "location": ("location", "locations"),
    "channel": ("channel", "channels"),
    "month": ("month", "months"),
}
#: Said when a drill names rows ("these agents") and no ranking is held: never a guess.
NO_RANKING_LINE = (
    "I don't have a ranking to take those from. "
    "Ask the ranking first, for example top 5 sales agents for a product this year."
)
#: A channel or a month list is not a drill source: those are named in words.
NOT_A_SOURCE_LINES = {
    "month": "I can't narrow by the months above. Name the period instead, for example September.",
    "channel": "I can't narrow by the channels above. Name the channel instead, for example dealer only.",
}
#: The route's per-filter cap (`order_management/report_ask._MAX_VALUES`).
DRILL_MAX_KEYS = 50
TOO_MANY_LINE = "That is more than {max} {plural} to narrow by. Ask for the top {max} or fewer first."
NO_KEYS_LINE = "I can't narrow by {name} above. Ask again naming the {plural} you want."
NOT_IN_ROWS_LINE = "{name} is not one of the {plural} above."
SCOPE_GIVE_UP = (
    "I still can't tell which you meant. Ask again in one message, "
    "e.g. top 10 customers of these agents, or top 10 customers overall."
)
#: The route params that filter each axis's own dimension. A drill ranks ACROSS its target axis,
#: so a held filter on that same dimension is not carried (crew decision, 8 Oct 2026: "top 5
#: products these customers buy" after a ranking for one product lists every product).
_AXIS_FILTERS = {
    "product": ("product_code", "product_ids"),
    "customer": ("customer_ids",),
    "sales_agent": ("sales_agent_ids",),
    "brand": ("brand_ids",),
    "category": ("category_ids",),
    "location": ("warehouse_codes",),
    "channel": ("channel",),
}
#: The frame keys that are not route params.
_FRAME_ONLY = ("date_from", "date_to", "top_n", "shown")


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

METRIC = rf.FieldSpec(name="metric", noun="measure", question=ranking_ask.METRIC_LINE, question_fn=_metric_question, resolve=_resolve_metric, allow_all=False, choices=("qty", "amount"))
BASIS = rf.FieldSpec(name="basis", noun="basis", question=ranking_ask.RANKING_BASIS_LINE, question_fn=_basis_question, resolve=_resolve_basis, allow_all=False, choices=BASES)

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
    stated_to_parser=True,
))


# --------------------------------------------------------------------------- #
# Engine seam
# --------------------------------------------------------------------------- #


def as_top_selling(verdict: dict[str, Any], held_report: Any) -> dict[str, Any]:
    """Engine seam, before `hold_words`: the parser read a product or category ranking as
    `sales_ranking` but filled its own top selling field (`rank_group`). Products and categories
    ranked are the top selling ask (the parser prompt's rule), unless this is a scoped or unsure
    drill of a held sales ranking. The metric the ranking reading calls `measure` is the top
    selling `rank_by`."""
    if jsc.js_string(verdict.get("order_status") or "").strip() != ASK_NAME:
        return verdict
    if jsc.js_string(verdict.get("group_by") or "").strip().lower() not in ("product", "category"):
        return verdict
    if verdict.get("rank_group") not in ("item", "category"):
        return verdict
    # The top selling route filters by brand, category, sales agent and customer, and counts delivered or
    # ordered sales: a location or another basis would be dropped silently, so that ask stays here.
    hints = {
        jsc.js_string(e.get("hint") or "").strip().lower()
        for e in jsc.array(verdict.get("entities"))
        if isinstance(e, dict) and e.get("current_message") is not False
    }
    if not hints <= {"brand", "category", "sales_agent", "customer"}:
        return verdict
    if verdict.get("basis") not in (None, "delivered", "ordered"):
        return verdict
    held_ranking = isinstance(held_report, dict) and bool(held_report) and not held_report.get("kind")
    if held_ranking and verdict.get("ranking_drill") in ("scoped", "unsure"):
        return verdict
    out = {**verdict, "order_status": "top_selling"}
    if out.get("rank_by") is None:
        metric = {"qty": "quantity", "amount": "amount"}.get(out.get("measure"))
        if metric:
            out["rank_by"] = metric
    return out


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
        isinstance(e, dict)
        and e.get("current_message") is not False
        and jsc.js_string(e.get("hint") or "").strip()
        # A stray word the parser itself is unsure of and filed under no real kind ("other") is not
        # a filter, so it never turns an answer to the open question into a new ask.
        and not (jsc.js_string(e.get("hint") or "").strip().lower() == "other" and e.get("confident") is False)
        for e in jsc.array(verdict.get("entities"))
    ):
        return True
    raw_group = jsc.js_string(verdict.get("group_by") or "").strip().lower()
    if not raw_group:
        return False
    extras = _dict(_dict(open_slot).get("extras"))
    scope = _dict(extras.get("scope"))
    if scope:
        # The scope question names two axes ("customers of the sales agents above"): a reply
        # echoing either is its answer, not a new ask.
        return GROUP_BY.get(raw_group, raw_group) not in (scope.get("source"), _dict(scope.get("scoped")).get("args", {}).get("group_by"))
    held = _dict(extras.get("args")).get("group_by")
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
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool = False, say: Any = None
) -> tuple[dict[str, Any] | None, str | None]:
    """The route params a fresh ask names (every one but the period and top_n), or the one
    line to say instead of running. `dealer`: the turn's contact is customer-scoped."""
    raw_group = jsc.js_string(parse_output.get("group_by") or "").strip().lower()
    group_by = GROUP_BY.get(raw_group) if raw_group else None
    if raw_group and group_by is None:
        return None, CATALOGUE_LINE
    if dealer and _dealer_outside(parse_output, group_by):
        return None, _say(say, "report_dimension_denied")

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


#: Said (with this ask's `top N ... by ...` in front) when a restated ask has no held pieces to name.
EXAMPLE_ASK = "top 5 sales agents for Sorento by amount, delivered"


def _plural(group_by: Any) -> str:
    raw = jsc.js_string(group_by or "").strip().lower()
    return AXIS_NOUNS.get(GROUP_BY.get(raw, raw), ("", ""))[1]


def _held_named(parse_output: dict[str, Any]) -> list[str]:
    """The values the parser named for this ask (words, then gate entities), once each:
    whitespace-normalised, deduped case-insensitively, first spelling and order kept."""
    words = _dict(parse_output.get("report_ask_words"))
    raw = [w for hint in WORD_HINTS for w in jsc.array(words.get(hint))]
    raw += [
        e.get("raw") or ""
        for e in jsc.array(parse_output.get("entities"))
        if isinstance(e, dict) and e.get("current_message") is not False
    ]
    named: list[str] = []
    seen: set[str] = set()
    for w in raw:
        text = " ".join(jsc.js_string(w).split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            named.append(text)
    return named


def held_ask(parse_output: dict[str, Any]) -> str:
    """The ranking ask this message restates, said in a few words off the parser's fields
    ("top 20 sales agents for Sorento by amount"); empty when it names nothing to say."""
    plural = _plural(parse_output.get("group_by"))
    top_n = parse_output.get("top_n")
    count = int(top_n) if isinstance(top_n, (int, float)) and not isinstance(top_n, bool) and float(top_n).is_integer() else None
    named = _held_named(parse_output)
    if not plural and count is None and not named:
        return ""
    head = f"top {count} {plural}".strip() if count is not None else (plural or "sales")
    text = f"{head} for {', '.join(named)}" if named else head
    metric = metric_from(parse_output)
    if metric:
        text += " by " + ("quantity" if metric == "qty" else "amount")
    return text


def _say(say: Any, name: str, **words: str) -> str:
    if say is not None:
        return say(name, **words)
    from app.services.chatbot import copy as reply_copy  # the package imports this module

    return reply_copy.fallback_copy().render(name, **words)


def settle(
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool = False, say: Any = None,
    language: str | None = None,
) -> tuple[rf.Outcome | None, str | None]:
    """Every required field settled, or the line to send instead of running. Returns
    `(outcome, None)` (send `outcome.reply` with `outcome.slot` unless `outcome.done`), or
    `(None, line)` for a word or a dimension the ask cannot run with.

    The slot's extras carry the first message's args and its number (`top_n`), so an
    answering turn runs the first message's ask; the period question is asked first."""
    localized = language in reply_copy_words.REPORT_ASKED_FALLBACK  # ms / zh: no English summary
    slot = parse_output.get("required_ask")
    if isinstance(slot, dict) and slot.get("ask") == ASK_NAME and slot.get("asking") == "scope":
        return _scope_answer(db, parse_output, slot), None
    if isinstance(slot, dict) and slot.get("ask") == ASK_NAME:
        extras = _dict(slot.get("extras"))
        args = _dict(extras.get("args"))
        reply_dates = {"start": parse_output.get("date_filter_start"), "end": parse_output.get("date_filter_end")}
        given = {**_given_choices(args), **_given_top_n(args, extras.get("top_n")), **_reply_choices(args, parse_output)}
        own_count = top_n_from(parse_output.get("top_n"))
        if (
            args.get("group_by") and slot.get("asking") != "top_n" and own_count.status == "ok"
            and parse_output.get("ranking_refine") is True
        ):
            # An answering turn the parser marks as restating the ranking wins over the held count (like its
            # metric and basis); a stray count in a period answer ("last 3 months") does not.
            given["top_n"] = own_count
            slot = {**slot, "values": {k: v for k, v in _dict(slot.get("values")).items() if k != "top_n"}}
        outcome = rf.collect(
            db,
            SALES_RANKING_ASK,
            slot=slot,
            reply=jsc.js_string(parse_output.get("required_ask_reply") or ""),
            given=given,
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
    frame = frame if isinstance(frame, dict) else {}
    drill = parse_output.get("ranking_drill")
    if frame.get("kind") == "sales_report":
        frame = {}  # a held plain sales report is not a ranking: nothing to refine or drill
    raw_group = jsc.js_string(parse_output.get("group_by") or "").strip().lower()
    # A refine never changes the axis: a verdict naming another axis than the held ranking's
    # is not one, whatever its `ranking_refine` says (crew live pass, 8 Oct 2026).
    new_axis = bool(frame) and bool(raw_group) and GROUP_BY.get(raw_group, raw_group) != frame.get("group_by")
    if parse_output.get("ranking_refine") is True and frame and not new_axis:
        return _refine(db, parse_output, frame, dealer=dealer, say=say)
    if drill in ("scoped", "unsure") or (new_axis and drill != "global" and _dict(frame.get("shown"))):
        # No reading on a new axis right after a ranking is asked, never guessed (owner rule).
        reading = drill if drill in ("scoped", "unsure") else "unsure"
        drilled = _drill(db, parse_output, frame, entities, reading=reading, dealer=dealer, say=say)
        if drilled is not None:
            return drilled
        # Unsure with nothing above to narrow by: the global reading is the only one left.
    if new_axis and parse_output.get("ranking_refine") is True:
        # Not a refine (it names another axis): the fresh ask reads it whole, its dates included.
        parse_output = {**parse_output, "ranking_refine": False}

    args, line = _fresh_args(db, parse_output, entities, dealer=dealer, say=say)
    if args is None:
        return None, line
    if frame and _restates_held_ranking(parse_output, frame, args):
        return _refine(db, {**parse_output, "ranking_refine": True}, frame, dealer=dealer, say=say)
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
    outcome = rf.collect(db, SALES_RANKING_ASK, given=given, extras={"args": args, "top_n": top_n})
    restated = parse_output.get("report_ask_restated")
    if isinstance(restated, dict) and outcome.slot is not None and outcome.slot.get("asking") == "period":
        # A restated ask while the period is pending: its new values are kept (they are this
        # outcome's extras), the question is not repeated word for word, and the re-ask is a
        # miss like a slot answer's, so it ends at the same bound.
        misses = int(restated.get("misses") or 0) + 1
        if misses >= rf.MAX_MISSES:
            if localized:
                names = ", ".join(_held_named(parse_output))
                example = (
                    f"{names} {reply_copy_words.REPORT_EXAMPLE_PERIOD[language]}"
                    if names
                    else reply_copy_words.REPORT_EXAMPLE_FALLBACK[language]
                )
            else:
                held = held_ask(parse_output)
                example = f"{held} this month" if held else EXAMPLE_ASK
            line = _say(say, "report_period_give_up", example=example)
            return rf.Outcome(values=outcome.values, reply=line, slot=None, extras=outcome.extras), None
        slot = {**outcome.slot, "misses": misses}
        if localized:
            asked = ", ".join(_held_named(parse_output)) or reply_copy_words.REPORT_ASKED_FALLBACK[language]
        else:
            asked = held_ask(parse_output) or "that"
        still = _say(say, "report_period_missing", asked=asked)
        return rf.Outcome(
            values=outcome.values, reply=f"{still}\n\n{PERIOD_QUESTION}", slot=slot, extras=outcome.extras
        ), None
    return outcome, None


def _filters_of(args: dict[str, Any]) -> dict[str, Any]:
    """The filter keys of a route args dict (not the axis, choices, period, count or provenance),
    with list values order-free, empties dropped."""
    skip = _NON_FILTER_ARGS | set(_FRAME_ONLY) | {"carried"}
    return {
        k: (sorted(str(i) for i in v) if isinstance(v, list) else v)
        for k, v in args.items()
        if k not in skip and v not in (None, [], "")
    }


def _restates_held_ranking(parse_output: dict[str, Any], frame: dict[str, Any], args: dict[str, Any]) -> bool:
    """The parser read a fresh ask that is the held ranking again (same axis, same filters) and set
    no period: only the count (or the choices) changed, so it is a refine of the held one."""
    if parse_output.get("ranking_refine") is True or not frame.get("group_by"):
        return False
    if args.get("group_by") != frame.get("group_by"):
        return False
    if period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end")) is not None:
        return False
    return _filters_of(args) == _filters_of(frame)


def _refine(
    db: Any, parse_output: dict[str, Any], frame: dict[str, Any], *, dealer: bool = False, say: Any = None
) -> tuple[rf.Outcome | None, str | None]:
    """The parser said this message only refines the sales ranking that ran (`ranking_refine`):
    the held route args re-run with ONLY the keys the verdict changed (count, period, basis,
    measure). A dealer's refine passes the same check a fresh ask does, over the HELD args."""
    if dealer:
        from app.services.reports.ask import DEALER_KEYS

        if (
            frame.get("group_by") not in (None, *DEALER_KEYS)
            or frame.get("sales_agent_ids")
            or frame.get("customer_agent_ids")
            or frame.get("warehouse_codes")
            or frame.get("channel")
        ):
            return None, _say(say, "report_dimension_denied")
    args = {k: v for k, v in frame.items() if k not in _FRAME_ONLY}
    held = dict(args)
    channel = parse_output.get("sales_channel")
    if channel in ("dealer", "project"):
        if dealer:
            return None, _say(say, "report_dimension_denied")
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
    if parse_output.get("rank_direction") in ("top", "bottom"):
        # The same mapping a fresh ask uses; none keeps the held direction.
        args["sort"] = "asc" if parse_output["rank_direction"] == "bottom" else "desc"
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
    # Provenance: the params whose value is the held entry's, not said in this message.
    # Built from what THIS message set, never by comparing values: a held brand named again is said now.
    said = set(filters) | ({"channel"} if channel in ("dealer", "project") else set())
    carried = [k for k, v in args.items() if k not in _NON_FILTER_ARGS and k not in said and held.get(k) == v and v not in (None, [], "")]
    if period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end")) is None:
        carried += [k for k in ("date_from", "date_to") if frame.get(k)]
    return rf.collect(
        db, SALES_RANKING_ASK, given=given, extras={"args": args, "top_n": top_n, "carried": carried}
    ), None


def _named_words(parse_output: dict[str, Any], axis: str) -> list[str]:
    """The words this message named on `axis` (as the parser gave them), for a line."""
    hint = "sales_agent" if axis == "sales_agent" else axis
    words = [" ".join(jsc.js_string(w).split()) for w in jsc.array(_dict(parse_output.get("report_ask_words")).get(hint))]
    words += [
        " ".join(jsc.js_string(e.get("raw") or "").split())
        for e in _current_entities(parse_output, "warehouse" if axis == "location" else hint)
    ]
    return [w for w in words if w]


def _unsure_line(group_by: str, top_n: Any, source: str, count: int) -> str:
    """"Top 10 customers of the 3 sales agents above, or across all customers?" (card rule 1)."""
    one, many = AXIS_NOUNS[group_by]
    src_one, src_many = AXIS_NOUNS[source]
    got = top_n_from(top_n)
    head = f"Top {got.value} {many if got.value != 1 else one}" if got.status == "ok" else many.capitalize()
    rows = f"the {count} {src_many}" if count != 1 else f"the {src_one}"
    return f"{head} of {rows} above, or across all {many}?"


def _drill(
    db: Any, parse_output: dict[str, Any], frame: dict[str, Any], entities: Any, *, reading: str,
    dealer: bool = False, say: Any = None,
) -> tuple[rf.Outcome | None, str | None] | None:
    """The parser read this ranking as about the rows the last ranking printed
    (`ranking_drill` scoped), or maybe so (unsure, asked; nothing runs). Scoped keeps every
    held filter, the period, the basis and the measure; the message's own filters, basis,
    measure, period and direction replace held ones (card rule 2); the declared link
    (`reports.ask.drill_param`) narrows to exactly the rows printed, the same axis included
    ("bottom 2 of these agents"). A scoped drill with nothing held is said, never guessed;
    None means an unsure one with nothing above to narrow by, which is the fresh ask."""
    from app.services.reports.ask import drill_param

    shown = _dict(frame.get("shown"))
    rows = [r for r in jsc.array(shown.get("rows")) if isinstance(r, dict)]
    source = jsc.js_string(shown.get("group_by") or "")
    unsure = reading == "unsure"
    drillable = bool(rows) and source in AXIS_NOUNS and drill_param(source, source) is not None
    if unsure and not drillable:
        return None
    if not rows or source not in AXIS_NOUNS:
        return None, NO_RANKING_LINE

    # The message's own reading, exactly as a fresh ask reads it (axis, filters, choices).
    own, line = _fresh_args(db, parse_output, entities, dealer=dealer, say=say)
    if own is None:
        return None, line
    group_by = own.get("group_by")
    if group_by is None:
        return None, CATALOGUE_LINE
    param = drill_param(source, group_by)
    if param is None:
        return None, NOT_A_SOURCE_LINES.get(source, CATALOGUE_LINE)
    keys: list[str] = []
    for row in rows:
        row_keys = [k for k in (jsc.js_string(v) for v in jsc.array(row.get("keys"))) if k]
        if not row_keys:
            # A printed row naming nothing to narrow by ("(blank)"): said, never dropped.
            if unsure:
                return None  # nothing to scope to: the global reading is the only one left
            return None, NO_KEYS_LINE.format(name=jsc.js_string(row.get("name")), plural=AXIS_NOUNS[source][1])
        keys.extend(k for k in row_keys if k not in keys)
    if len(keys) > DRILL_MAX_KEYS:
        if unsure:
            return None
        return None, TOO_MANY_LINE.format(max=DRILL_MAX_KEYS, plural=AXIS_NOUNS[source][1])

    held = {k: v for k, v in frame.items() if k not in _FRAME_ONLY and k not in _AXIS_FILTERS.get(group_by, ())}
    if dealer and any(held.get(k) for k in ("sales_agent_ids", "customer_agent_ids", "warehouse_codes", "channel")):
        return None, _say(say, "report_dimension_denied")
    message_filters = {k: v for k, v in own.items() if k not in _NON_FILTER_ARGS}
    named = message_filters.get(param)
    if isinstance(named, list):
        # The message names its own value on the link's param ("these agents, ALICE only"):
        # it narrows within the rows printed; one that is not among them is said, never dropped.
        inside = [k for k in named if k in keys]
        if not inside:
            names = ", ".join(_named_words(parse_output, source)) or "That"
            return None, NOT_IN_ROWS_LINE.format(name=names, plural=AXIS_NOUNS[source][1])
        keys = inside
    same_axis = group_by == source
    sort = own["sort"]
    if same_axis and parse_output.get("rank_direction") not in ("top", "bottom"):
        sort = frame.get("sort") or sort  # "these agents by quantity" keeps the held direction
    args = {**held, **message_filters, "group_by": group_by, "sort": sort, param: keys}
    for key in ("basis", "measure"):
        if own.get(key):
            args[key] = own[key]
    own_period = period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end"))
    period = own_period or period_from(frame.get("date_from"), frame.get("date_to"))
    top_n = parse_output.get("top_n")
    if top_n is None and same_axis:
        top_n = frame.get("top_n")
    scoped = _branch(args, top_n, period, held=True)
    if unsure:
        # One question, nothing runs. The slot holds BOTH readings, so the reply only has to
        # say which ("the agents above" / "all"); its axis, count and period are the slot's.
        question = _unsure_line(group_by, parse_output.get("top_n"), source, len(rows))
        scope = {
            "question": question,
            "source": source,
            "scoped": scoped,
            "global": _branch(own, parse_output.get("top_n"), own_period, held=False),
        }
        slot = {"ask": ASK_NAME, "values": {}, "asking": "scope", "options": [], "misses": 0,
                "extras": {"scope": scope}}
        return rf.Outcome(values={}, reply=question, slot=slot), None
    return _collect_branch(db, scoped), None


def _branch(args: dict[str, Any], top_n: Any, period: rf.Resolved | None, *, held: bool) -> dict[str, Any]:
    """One reading of a ranking, kept as plain data (it may wait in a slot): its args, count,
    period, and whether its metric and basis are the held ranking's (never asked again)."""
    return {
        "args": args,
        "top_n": top_n,
        "period": {"value": period.value, "label": period.label} if period is not None else None,
        "held": held,
    }


def _collect_branch(db: Any, branch: dict[str, Any]) -> rf.Outcome:
    args, top_n = _dict(branch.get("args")), branch.get("top_n")
    given = {**_given_choices(args), **_given_top_n(args, top_n)}
    if branch.get("held"):
        # A drill has both choices (the held args ran); only the count is asked when not named.
        for name, key in (("metric", "measure"), ("basis", "basis")):
            if name not in given:
                given[name] = rf.Resolved("ok", value=args.get(key), label=args.get(key) or "")
    period = _dict(branch.get("period"))
    if period:
        given["period"] = rf.Resolved("ok", value=period.get("value"), label=period.get("label") or "")
    return rf.collect(db, SALES_RANKING_ASK, given=given, extras={"args": args, "top_n": top_n})


def _scope_answer(db: Any, parse_output: dict[str, Any], slot: dict[str, Any]) -> rf.Outcome:
    """The reply to the scope question: the PARSER's reading of it (`ranking_drill` scoped or
    global) picks the reading the slot holds. Anything else asks once more, then gives up."""
    scope = _dict(_dict(slot.get("extras")).get("scope"))
    reading = parse_output.get("ranking_drill")
    if reading in ("scoped", "global") and isinstance(scope.get(reading), dict):
        return _collect_branch(db, scope[reading])
    if int(slot.get("misses") or 0) >= 1:
        return rf.Outcome(reply=SCOPE_GIVE_UP, slot=None)
    return rf.Outcome(reply=jsc.js_string(scope.get("question") or ""), slot={**slot, "misses": 1})


def route_args(outcome: rf.Outcome) -> dict[str, Any]:
    """The settled ask in the lane's words (basis + measure figure; `fetch` turns them into `crm_report`'s registry key), plus `carried` on a refine."""
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
    carried = outcome.extras.get("carried")
    if carried:
        args["carried"] = list(carried)
    return args


#: The route's 403 `detail.code` -> the reply copy that says it. The route's own words are never
#: echoed; any other code is the general refusal.
FORBIDDEN_COPY = {
    "customer_not_permitted": "report_own_account",
    "report_dimension_not_allowed": "report_dimension_denied",
    "sales_report_not_enabled": "report_not_enabled",
    "chat_analytics_not_enabled": "report_chat_not_enabled",
}


def error_line(envelope: Any, say: Any = None) -> str | None:
    """A route refusal said as its own line (403: by `detail.code`, see `FORBIDDEN_COPY`), a 404
    as `I couldn't find that <thing>.`; None for any other error (the generic error path answers it)."""
    if not isinstance(envelope, dict):
        return None
    status = envelope.get("status_code")
    detail = _dict(envelope.get("detail"))
    if status == 403:
        return _say(say, FORBIDDEN_COPY.get(jsc.js_string(detail.get("code") or ""), "report_refused"))
    message = jsc.js_string(detail.get("message") or "").strip()
    if status == 404:
        thing = message.split(" not found", 1)[0].strip().lower() if " not found" in message else ""
        return f"I couldn't find that {thing}." if thing else "I couldn't find that."
    return None
