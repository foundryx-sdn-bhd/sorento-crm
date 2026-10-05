# State: focus + pending + profile (PLAN-chatbot-turn-rearch.md "APPLY contract").
# Dataclasses only - no pydantic here, no I/O, and nothing imported outside the stdlib
# but `turn/task.py`, the one axis that carries a dataclass of its own (ported from PR
# #1118, feat/chatbot-dealer-stock-verdict, not merged, owner ruling 24 Sep 2026, for
# chatbot-stock-ask-v2 S3).
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.services.chatbot.turn.task import Task, task_from_wire, task_to_wire


def focus_row_label(row: Mapping[str, Any]) -> Any:
    """Which field of a focus/roster row names it, in one preference order shared by
    every caller that prints a row's own subject: `turn/compose.py::_subject_line` and
    `tail/scope_block.py::_focus_words`. A DB-filled `display_name` (present only on a
    LOCAL copy a caller filled just before printing - see `turn_runtime.
    fill_customer_names`'s own docstring; never written back onto `Focus` itself)
    wins over the pick's own `name` (stamped only for a single-identity option),
    which wins over the raw text the contact typed, which wins over the option's
    shared rollup `canonical_code`.

    Hand pass 12 Phase 3 finding P1: `_subject_line` used to carry its OWN, narrower
    ladder (`name or canonical_code or raw`, no `display_name` at all), so a caller
    that HAD filled `display_name` onto a fresh roster's carried rows still printed
    the customer ROLLUP code instead of naming every ledger.

    #1262 slice 5 (F4), AC-S5-4: a row carrying the parser's own `quantity` prints
    it beside the code, "M210-GM (x5)" - the ONE place this reads, never a second
    regex pulling a quantity back out of `raw` (owner ruling 1). A row with no
    quantity at all (every row before this slice) prints exactly as it always did.

    Only a row THIS message named prints it (the S1 nit, fix lane round 2): a CARRIED
    row (`current_message: False`, `focus_from_wire`) keeps its `quantity` for the
    stock task's per-product loop (main, chatbot-stock-ask-v2 S3), but a later subject
    line never repeats an earlier message's "(x5)".
    """
    label = row.get("display_name") or row.get("name") or row.get("raw") or row.get("canonical_code")
    quantity = row.get("quantity")
    if row.get("current_message") is False:
        return label
    if label and isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and quantity:
        return f"{label} (x{quantity:g})"
    return label


def fold_token(value: str) -> str:
    """Strip the resolver's own separator fold (hyphens and whitespace) from `value` -
    case is untouched, the caller casefolds around this the way it already does. ONE
    copy, in the lowest module both `narrow.py` and `turn_runtime.py` already import
    from, so the two sides of any join against the resolver's own `unresolved_tokens`
    keys can never fold differently again (R1, PLAN-chatbot-answer-half-reattach.md:
    `narrow._token_of` folding one way and `turn_runtime._token_key` folding another
    let a hyphenated or spaced code the resolver could not place come back as a
    one-option roster echoing the customer's own typed token).

    Plain string iteration, not the `re` module (`resolve_gate._PRODUCT_FOLD`'s own
    `[-\\s]+`, reproduced without a regex call - this package may not use one,
    `narrow.py::_without_brackets`'s own rule, AC-1520's no-regex-in-the-pure-core
    guard, pinned by a source scan in the S2 apply-is-pure suite). Character
    deletion, not substitution, so a RUN of separators folds identically to one
    deleted individually.
    """
    return "".join(ch for ch in value if ch != "-" and not ch.isspace())


def token_key(value: Any) -> str:
    """The one join key both sides of a resolver-token map fold to - `fold_token`
    plus the strip/casefold every caller was already doing around it by hand.

    Hand pass 12 Phase 3 finding F8: `turn_runtime._token_key` and `turn/
    reconcile._key` were two copies of this exact same three-line body (one via
    `jsc.nullish_str`, one via a bare `None` guard - the same result either way).
    ONE copy, here, beside `fold_token` itself, in the lowest module both `turn_
    runtime.py` and `turn/reconcile.py` already import from (`turn/reconcile.py`
    cannot import `turn_runtime` - circular). `turn_runtime.py` still exposes it as
    `_token_key` (its own established name, several other modules' docstrings refer
    to it by), a plain re-import, not a second definition.
    """
    return fold_token(str(value).strip().casefold()) if value is not None else ""


# Entity kinds that get their own plural Focus field. Anything else lands in
# `Focus.extra`, keyed by kind - a kind this turn's tests never exercise on Focus
# directly still has somewhere safe to sit rather than being silently dropped.
KIND_FIELD_MAP: dict[str, str] = {
    "product": "products",
    "customer": "customers",
    "warehouse": "warehouse",
    "brand": "brands",
}

# Hand pass 12, Group B: kinds that share ONE `Focus.extra` slot rather than each
# getting their own. The resolver types an order token "order", "customer_order" or
# "order_number" depending on how it matched (`lanes.business.answer._ORDER_TYPES`,
# `fetch.TYPE_TO_PARAM` - both already treat the three as one axis feeding the same
# `order_ids` param), but a did-you-mean roster over an unplaced order token settles
# with entity_type "customer_order" while the ORIGINAL miss that opened the roster is
# recorded under "order" - so a pick had no bucket in common with the miss it answers,
# and the missed raw sat on `focus.extra["order"]` forever, unreachable and unreplaced.
# Canonicalised HERE, once, so `apply._set_kind_field`'s write and `narrow._candidates`'s
# read can never disagree about which bucket either kind is in.
EXTRA_KIND_ALIASES: dict[str, str] = {"customer_order": "order", "order_number": "order"}


@dataclass
class Focus:
    products: list[dict[str, Any]] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    warehouse: list[dict[str, Any]] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    # #1262 slice 9 (F1a) follow-up: the outstanding report's OWN brand carry - already
    # RESOLVED uuids (never re-resolved, D10), settled by `_settle_question_subject` off
    # the scope-ask's stored filters and read back by `turn_runtime.outstanding_carry`.
    # A dedicated slot, deliberately NOT `brands` above: that field holds plain CODE
    # strings for the tier-gate's own brand x tier entitlement recompose
    # (`turn_runtime.py`'s `recompose(tiers, focus.brands, entitled)`), an unrelated
    # consumer this must never disturb.
    outstanding_brand_ids: list[str] = field(default_factory=list)
    tier: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    document: list[str] = field(default_factory=list)
    status: str | None = None
    # PLAN-chatbot-sales-report.md S4 wiring point 2: the sales report's own channel
    # filter ("dealer" / "project" / None), an axis of the same kind as `status` and
    # carried the same way. A sales report ask that stopped at the customer picker, or
    # whose detail offer is answered with a bare "1", names no channel word on the turn
    # that resumes it - the focus is the one carry in this engine, so it rides here
    # rather than on a session key of its own (the retired head kept a second copy on
    # `outstanding_filters` and the two could disagree).
    sales_channel: str | None = None
    date_window: dict[str, Any] | None = None
    # The PO/SPO sort axis {"by", "dir"} (PLAN-po-spo-warehouse-29sep S5).
    sort: dict[str, Any] | None = None
    # The twelfth slot (AC-1534, contract 115): the set a too-long counted answer asked
    # "how many should I show?" about, `{"set_key": ...}`, read only by that question's
    # answer (no paging, owner ruling 26 Sep 2026). Its own slot rather than a bag entry:
    # it is a focus axis like any other, and it has to be cleared by a topic reset with
    # the rest.
    set_page: dict[str, Any] | None = None
    # Round 4 R5 (owner console test on PR #833): the ask a clarify question was about,
    # `{"term", "options", "ask"}`, read only by that question's answer ("tap" after "Did
    # you mean tap or wash basin?") and cleared by every other turn.
    set_clarify: dict[str, Any] | None = None
    # PLAN-chatbot-top-x-hot-selling-24sep.md "Lane wiring (S4)" point 8: a top selling
    # ask's own axes (`rank_by`, `basis`, `rank_group`, `top_n`, the category words and a
    # picked row's `detail_code` / `category_code`), carried while `status ==
    # "top_selling"`. The metric, grain, basis and count questions are answered in a
    # SECOND message that names only the answer, so the ask they complete has to be
    # somewhere; `turn/apply._top_selling_rules` is its one writer. One slot, not four
    # fields: every key lives and dies with the one ask.
    top_selling: dict[str, Any] | None = None
    # LOWSTOCK-FILTER-ASK: the question `required_fields` left open (the slot it returned),
    # read and consumed by `required_fields.reply_verdict` on the NEXT message only. One
    # turn long: a lane that asks again hands back a fresh one.
    required_ask: dict[str, Any] | None = None
    # LOWSTOCK-SEMANTIC: the filters the last low stock report was built with
    # (`low_stock_ask.route_filters` "frame"), so a refinement narrows it.
    low_stock: dict[str, Any] | None = None
    # Ported from PR #1118 (not merged) for chatbot-stock-ask-v2 S3: what the
    # conversation still OWES (Focus.tasks, D21). A tuple of `turn/task.py::Task`, at
    # most one per kind. Its own axis rather than a flag on `products`, because
    # `apply._set_kind_field` REPLACES an axis wholesale on any turn that names
    # entities of that kind - turn 1's four products would be gone the moment turn 2
    # answered two of them - and rather than a `pending`, because a new ask CLOSES a
    # roster and must only PARK a task.
    tasks: tuple[Task, ...] = ()
    extra: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class Profile:
    tier: str | None = None
    language: str | None = None
    # None = unrestricted (every domain answers). A concrete list, possibly empty,
    # switches a domain to deny-by-default: granted only when the domain's own
    # `reveal_key` (or, absent one, its bare name) is a member.
    grants: list[str] | None = None
    default_ledgers: list[str] | None = None
    # S6 (owner ruling, 16 Sep 2026): may this contact ask for stock. A CRM fact on the
    # contact row (`respond_contacts.chatbot_stock_allowed`), default ON, so a contact
    # with no row at all is allowed. Carried on the profile because it is read at the
    # same moment as the tier and the language, off the same SELECT, and the engine's
    # stock-denial gate (contract 61, 62) is the one reader.
    stock_allowed: bool = True
    # Chatbot stock ask v2 S2 (PLAN-chatbot-stock-ask-v2-24sep.md, R7): both default
    # OFF, unlike stock_allowed above - a contact with no row, or two ambiguous rows,
    # gets no salesman notification and no packing list attachment by default.
    notify_salesman: bool = False
    packing_list_allowed: bool = False
    # Owner ruling 26 Sep 2026 (hand test F1): "dealer ask cannot have escalation,
    # cannot have direct escalation to warehouse, their contact point is sales person".
    # A dealer is a contact whose stock visibility policy is "Availability only"
    # (`stock_visibility.resolve_policy(...).mode == "availability"`), read once with the
    # rest of the profile so the stock ask can refer them to their salesman instead of
    # offering a team. Default OFF: an unresolved contact keeps today's behaviour.
    stock_availability_only: bool = False
    # ESCALATION-CONTROL (owner, 30 Sep 2026): may this contact be offered, or force, a
    # hand-off to a person: `respond_contacts.escalation_allowed`, the one per-contact
    # switch (`turn_runtime._escalation_allowed`). Default ON: an unresolved contact keeps
    # today's behaviour.
    escalation_allowed: bool = True


def offers_escalation(profile: "Profile | None") -> bool:
    """The ONE check every bot-initiated escalation offer site reads: no offer to staff
    (`is_staff_profile`, #1262 slice 11) and none to a contact whose escalation is
    barred (ESCALATION-CONTROL). A missing profile still offers (the old default)."""
    return not is_staff_profile(profile) and not escalation_barred(profile)


def escalation_barred(profile: "Profile | None") -> bool:
    """ESCALATION-CONTROL: this contact may not be offered, nor force, a hand-off."""
    return getattr(profile, "escalation_allowed", True) is False


def is_staff_profile(profile: "Profile | None") -> bool:
    """#1262 slice 11 (F8), owner ruling 2: staff (a Mocha CS sales rep) is
    `respond_contacts.chatbot_profile.tier == "office"`. The ONE check every
    bot-initiated escalation offer site reads (`turn/compose.py`'s own composer
    offer, `answer_bridge.py`'s cross-domain ladder offer) - staff get no offer,
    dealer/end_user keep R6 (22 Sep) unchanged.
    """
    return getattr(profile, "tier", None) == "office"


@dataclass
class State:
    focus: Focus
    pending: Any = None
    profile: Profile = field(default_factory=Profile)
    turn_no: int = 0
    # The five-key `ideation` pointer as the session holds it (issue #1178): the idea
    # draft the intake tool is still collecting, or None. Read by `apply()` the way it
    # reads `pending` - an open draft is a question the ideate lane is still asking, so a
    # short or question-shaped turn that names nothing of its own belongs to that lane
    # and not to the domain menu or the casual lane. Never written here: the intake tool
    # owns the pointer and the tail persists whatever it answered.
    ideation: Any = None


# --------------------------------------------------------------------------- #
# The wire shape: what `respond_contacts.session_vars.focus` holds between turns
# (AC-1504). ONE shape, not two - `apply()` works on this dataclass and the session
# stores the same axes, so nothing has to map a singular field onto a plural one and
# lose a ledger family on the way (journey step 5, D7).
# --------------------------------------------------------------------------- #

FOCUS_LIST_FIELDS = (
    "products", "customers", "warehouse", "brands", "outstanding_brand_ids",
    "tier", "domains", "document",
)


def focus_to_wire(focus: Focus) -> dict[str, Any]:
    wire: dict[str, Any] = {name: list(getattr(focus, name)) for name in FOCUS_LIST_FIELDS}
    wire["status"] = focus.status
    wire["sales_channel"] = focus.sales_channel
    wire["date_window"] = focus.date_window
    wire["sort"] = dict(focus.sort) if focus.sort else None
    wire["set_page"] = focus.set_page
    wire["set_clarify"] = focus.set_clarify
    wire["top_selling"] = dict(focus.top_selling) if focus.top_selling else None
    wire["required_ask"] = dict(focus.required_ask) if focus.required_ask else None
    wire["low_stock"] = dict(focus.low_stock) if focus.low_stock else None
    # Ported from PR #1118 (not merged): the open tasks travel INSIDE the focus, not
    # on a session key of their own - the focus is the context, and a second key
    # could disagree with it.
    wire["tasks"] = [task_to_wire(task) for task in (focus.tasks or ())]
    wire["extra"] = {k: list(v) for k, v in (focus.extra or {}).items()}
    return wire


def focus_from_wire(raw: Any) -> Focus:
    """The inverse. Tolerant by design: a slot written by an older build may hold a bare
    string where this one holds an entity dict, and a focus that cannot be read is a
    forgotten conversation, not a failed turn.

    Every entity read back here is CARRIED: it was named by an earlier message, whatever
    flag the row was persisted with (`_entity` down-flags `current_message`). This is the
    one seam a stored focus becomes a turn's state through, and it is the only place that
    can say so - the writer cannot, because at the moment it writes, the rows it is
    storing WERE this message's. Without it `current_message` stayed true on an entity for
    the rest of the conversation, and a rule that asks "did THIS message name this token"
    (`narrow.decide`'s ambiguous-filter roster, hand pass 2 item 6) had no honest signal
    to read; a RECORDED session carries the flag set the same way, so down-flagging on
    the write path alone would have left every replayed turn lying.
    """
    if not isinstance(raw, dict):
        return Focus()
    focus = Focus()
    for name in FOCUS_LIST_FIELDS:
        value = raw.get(name)
        if not isinstance(value, list):
            continue
        if name in ("brands", "outstanding_brand_ids", "tier", "domains", "document"):
            setattr(focus, name, [v for v in value if isinstance(v, str)])
        else:
            setattr(focus, name, [_entity(v) for v in value if v is not None])
    # `customer` singular is what the first cut of the wire shape wrote; read forward so
    # a contact mid-conversation at deploy keeps the customer they already named.
    if not focus.customers and isinstance(raw.get("customer"), dict):
        focus.customers = [_entity(raw["customer"])]
    # `order_status` is what the pre-rearch wire shape called this axis (contract 34;
    # `conversation_variables_service` maps the same name forward on its own read path).
    # Read forward here too, or a contact whose focus was persisted by an older build
    # loses its status filter the first time this build reads the slot back. The current
    # name wins when both are present.
    status = raw.get("status")
    if not isinstance(status, str):
        status = raw.get("order_status")
    focus.status = status if isinstance(status, str) else None
    channel = raw.get("sales_channel")
    focus.sales_channel = channel if isinstance(channel, str) else None
    window = raw.get("date_window")
    focus.date_window = window if isinstance(window, dict) else None
    sort = raw.get("sort")
    focus.sort = dict(sort) if isinstance(sort, dict) else None
    page = raw.get("set_page")
    focus.set_page = page if isinstance(page, dict) else None
    clarify = raw.get("set_clarify")
    focus.set_clarify = clarify if isinstance(clarify, dict) else None
    top_selling = raw.get("top_selling")
    focus.top_selling = dict(top_selling) if isinstance(top_selling, dict) else None
    required_ask = raw.get("required_ask")
    focus.required_ask = dict(required_ask) if isinstance(required_ask, dict) else None
    low_stock = raw.get("low_stock")
    focus.low_stock = dict(low_stock) if isinstance(low_stock, dict) else None
    tasks = raw.get("tasks")
    if isinstance(tasks, list):
        # Ported from PR #1118 (not merged): a focus persisted before this slice
        # shipped carries no `tasks` key at all, which reads as "nothing owed", never
        # as a broken read.
        focus.tasks = tuple(
            task for task in (task_from_wire(row) for row in tasks) if task is not None
        )
    extra = raw.get("extra")
    if isinstance(extra, dict):
        focus.extra = {
            k: [_entity(v) for v in value if v is not None]
            for k, value in extra.items()
            if isinstance(value, list)
        }
    return focus


def _entity(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        # Read back from the session, so named by an EARLIER message - see the docstring
        # above. A copy, never the caller's dict: the wire payload is read by other
        # readers too and this rule is about the STATE, not about the stored row.
        # #1262 fix lane round 2 (S1's nit) keeps `quantity` on a carried row for the
        # stock task (main, chatbot-stock-ask-v2 S3); `focus_row_label` is what never
        # prints a carried row's "(x5)" in a later subject line.
        return {**value, "current_message": False}
    return {"raw": value, "canonical_code": value, "current_message": False}
