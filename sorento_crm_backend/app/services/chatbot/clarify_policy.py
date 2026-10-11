"""CLARIFY-POLICY S1: the loop breaker (`documentation/plans/chatbot/PLAN-clarify-policy-11oct.md`,
UAC AC-1..AC-7, owner ruling Q4 11 Oct 2026).

A clarification is fingerprinted by what the BOT asked (ask kind, parameter, subject code),
never by the user's text and never by the reply wording. The fingerprint is stored with the
bot's own reply (`chatbot.turns.response["ask_fingerprint"]`, beside the `reply` that
`turn_runtime.previous_reply_text` reads), so there is no session schema change. When a turn
would send the same clarification a third time in a row, the reply becomes one plain line saying
what is missing plus the handover offer (with escalation barred, "Please refer to your
salesman."). Never a guessed answer and no fetched data.

Applied once, at the single exit every turn passes (`engine._run_turn`, `apply_loop_breaker`),
so every ask site is covered, including those that arm no pending question (the DO / SO period
ask, the attachment type ask, the domain menu, the LLM clarifier).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Q4 (a): how many times in a row the same clarification may be sent; the next one breaks.
#: The ONE place this limit is read (`should_break`), so the DB-configurable policy can
#: replace it here.
MAX_IDENTICAL_ASKS = 2

#: The key the fingerprint is stored under on the turn's stored response.
RESPONSE_KEY = "ask_fingerprint"

#: Plain words for what is missing, by ask kind (AC-120: the break line names it).
_MISSING_WORDS = {
    "do_period": "the period",
    "so_period": "the period",
    "attachment_type": "the attachment type",
    "carry_combine": "which products to include",
    "carry_unclear": "which products to include",
}


@dataclass(frozen=True)
class ClarifyRule:
    """One `chatbot_clarify_policy` row (AC-110)."""

    ask_kind: str
    label: str
    mode: str
    allowed_modes: tuple[str, ...]
    default_note: str = ""
    repeat_limit: int = MAX_IDENTICAL_ASKS


def _row(kind: str, label: str, mode: str = "ask", others: tuple[str, ...] = (), note: str = "") -> dict[str, Any]:
    return {
        "ask_kind": kind,
        "label": label,
        "mode": mode,
        "allowed_modes": [mode, *others],
        "default_note": note,
        "repeat_limit": MAX_IDENTICAL_ASKS,
    }


#: The ONE seed of `chatbot_clarify_policy` (the CARD's table, owner rulings 11 Oct 2026): every
#: kind at today's mode, except `generic_menu` / `entities_only` = default (Q5) and the carried
#: subject rows = ask (R2, Q9). A kind has more than one allowed mode only where the other
#: behaviour already exists in code (Q7). Frozen like `turn/policy_rows.py`: the migration
#: `clp_0001_clarify_policy` inserts these rows, so edit a row by a new migration, not here.
CLARIFY_POLICY_ROWS: tuple[dict[str, Any], ...] = (
    _row("promo_tier", "Promotion access level"),
    _row("tier_pick", "Generic access level pick"),
    _row("promo_subject", "Promotion subject"),
    _row("attachment_type", "Attachment type"),
    _row("attachment_kind", "Attachment kind"),
    _row("cost_product", "Purchase cost product"),
    _row("do_period", "Which period, for delivery orders", "ask", ("default",), "newest 20, no date window"),
    _row("so_period", "Which period, for sales orders", "ask", ("default",), "newest 20, no date window"),
    _row("ranking_fields", "Ranking period, metric, basis, top N"),
    _row("ranking_drill", "Ranking drill scope"),
    _row("top_selling", "Top selling grain, metric, who"),
    _row("low_stock", "Low stock category, supplier, grouping"),
    _row("idea_text", "Ideation idea"),
    _row("outstanding_scope", "Outstanding for which document", "ask", ("default",), "both SO and DO"),
    _row("outstanding_customer", "Outstanding report customer"),
    _row("product_pick", "Product picker", "collapse"),
    _row("customer_pick", "Customer picker"),
    _row("roster_pick", "Roster pick"),
    _row("kind_pick", "Kind pick"),
    _row("account_customer", "Account N customer"),
    _row("stock_qty", "Stock quantity"),
    _row("stock_family", "Stock family pick"),
    _row("qty_scope", "Quantity for all or one"),
    _row("demand_qty", "Stock denial demand quantity"),
    _row("did_you_mean", "Did you mean"),
    _row("generic_menu", "Domain menu", "default", ("ask",), "answer the subject's default domain"),
    _row("llm_clarify", "LLM clarifier"),
    _row("escalation_pick", "Escalation team, company, member"),
    _row("entities_only", "What would you like me to know", "default", ("ask",), "answer the subject's default domain"),
    _row("carry_combine", "Carried subject with a new subject", "ask", ("carry", "drop"), "ask, keep or drop"),
    # Crew ruling 11 Oct: `carry` until the parser version with `continuation` is promoted;
    # the release step flips it to `ask` with that promotion.
    _row("carry_unclear", "Carried subject, no reference", "carry", ("ask", "drop"), "ask, keep or drop"),
)


def _rule(row: Any) -> ClarifyRule:
    return ClarifyRule(
        ask_kind=str(row["ask_kind"]),
        label=str(row.get("label") or row["ask_kind"]),
        mode=str(row["mode"]),
        allowed_modes=tuple(row.get("allowed_modes") or (row["mode"],)),
        default_note=str(row.get("default_note") or ""),
        repeat_limit=int(row.get("repeat_limit") or MAX_IDENTICAL_ASKS),
    )


_SEED: dict[str, ClarifyRule] = {r["ask_kind"]: _rule(r) for r in CLARIFY_POLICY_ROWS}


def load_clarify_policy(db: Any) -> dict[str, ClarifyRule]:
    """The policy, one rule per kind (AC-113): the table's rows over the seed. An empty or
    missing table, or any read error, is the seed, never "no ask". A row whose mode is not in
    its own allowed modes (the service-level check beside the DB CHECK, AC-111) is ignored."""
    rules = dict(_SEED)
    if db is None:
        return rules
    nested = None
    try:
        from sqlalchemy import text

        # A savepoint, so a missing table never aborts the caller's transaction.
        nested = db.begin_nested() if hasattr(db, "begin_nested") else None
        rows = db.execute(
            text(
                "SELECT ask_kind, label, mode, allowed_modes, default_note, repeat_limit "
                "FROM chatbot_clarify_policy"
            )
        ).mappings().all()
        if nested is not None:
            nested.commit()
    except Exception:  # noqa: BLE001 - an unreadable table is the seed
        if nested is not None and nested.is_active:
            nested.rollback()
        logger.warning("chatbot: clarify policy unreadable, using the seed", exc_info=True)
        return rules
    for row in rows:
        rule = _rule(dict(row))
        if rule.mode in rule.allowed_modes:
            rules[rule.ask_kind] = rule
    return rules


def seed_mode(kind: str) -> str:
    """The seeded mode for `kind` (a policy built with no clarify rows reads this)."""
    rule = _SEED.get(kind)
    return rule.mode if rule is not None else "ask"


def mode_of(ctx: Any, kind: str) -> str:
    """The mode for `kind` from the turn's one policy read, carried on the lane ctx
    (`turn_runtime.make_tool_runner`, AC-113); a ctx without it reads the seed, never the DB."""
    modes = ctx.get("clarify_modes") if isinstance(ctx, dict) else None
    if isinstance(modes, dict) and kind in modes:
        return str(modes[kind])
    return seed_mode(kind)


#: The turn's `repeat_limit` per kind, set by the engine from its one policy read
#: (`remember_turn_limits`) and read by the loop breaker at the reply choke point.
_TURN_LIMITS: ContextVar[dict[str, int] | None] = ContextVar("clarify_turn_limits", default=None)


def remember_turn_limits(limits: Any) -> None:
    """Called once per turn by the engine with `Policy.clarify_limits` (None clears it)."""
    _TURN_LIMITS.set(dict(limits) if limits is not None else None)


def ask_fingerprint(kind: str, param: str, subject: str | None) -> str:
    """The identity of one clarification: kind, parameter and subject code (case-insensitive,
    trimmed). Takes no text, so wording can never enter the check (AC-2)."""
    code = (subject or "").strip().casefold()
    return f"{(kind or '').strip()}|{(param or '').strip()}|{code}"


def should_break(previous: list[str | None], current: str | None, limit: int = MAX_IDENTICAL_ASKS) -> bool:
    """True when the bot's last `limit` replies all carried `current` (AC-120).
    `previous` is oldest first; a non-clarification reply is None and resets the count (AC-123)."""
    limit = max(int(limit or MAX_IDENTICAL_ASKS), 1)
    if not current or len(previous) < limit:
        return False
    return all(f == current for f in previous[-limit:])


def _subject_code(focus: Any, *, printable: bool = False) -> str | None:
    """The turn's subject off the focus the turn ended with (products first, then customers):
    resolved values, never the message text. The fingerprint key may fall back to a row's
    uuid; the PRINTED subject (`printable`) never does: only a code or a name, else None
    (security review, CLARIFY-POLICY round 4)."""
    if not isinstance(focus, dict):
        return None
    for key in ("products", "customers"):
        values = set()
        for row in focus.get(key) or []:
            if not isinstance(row, dict):
                continue
            label = row.get("canonical_code") or row.get("name") or row.get("display_name")
            values.add(str(label if printable else (row.get("canonical_code") or row.get("uuid")) or "").strip())
        values -= {""}
        if values:
            return ",".join(sorted(values))
    return None


def turn_fingerprint(trace: Any) -> tuple[str | None, str | None, str | None]:
    """`(fingerprint, kind, subject)` of the clarification this turn's reply carries, read off
    the turn's own trace records (what the bot decided, not what it said), else all None.

    * `do_range` with `asked` / `so_list` with `period_asked`: the DO / SO period ask;
    * the turn plan's `ask` (`apply` record, e.g. `attachment_type_ask`);
    * the turn plan's `clarification` lane (the domain menu, the LLM clarifier)."""
    records = [r for r in trace or [] if isinstance(r, dict)]
    focus = None
    plan: dict[str, Any] = {}
    kind = param = None
    for rec in records:
        name = rec.get("kind")
        if name == "state" and isinstance(rec.get("focus"), dict):
            focus = rec["focus"]
        elif name == "apply" and isinstance(rec.get("plan"), dict):
            plan = rec["plan"]
        elif name == "do_range" and rec.get("asked"):
            kind, param = "do_period", "date_range"
        elif name == "so_list" and rec.get("period_asked"):
            kind, param = "so_period", "date_range"
    if kind is None and isinstance(plan.get("ask"), str) and plan["ask"]:
        kind, param = _PLAN_ASK_KINDS.get(plan["ask"], plan["ask"]), "ask"
    if kind is None and plan.get("lane") == "clarification":
        kind, param = "llm_clarify", "topic"
    if kind is None:
        return None, None, None
    return ask_fingerprint(kind, param or "", _subject_code(focus)), kind, _subject_code(focus, printable=True)


#: A turn plan's `ask` name -> its policy kind (`CLARIFY_POLICY_ROWS`).
_PLAN_ASK_KINDS = {"attachment_type_ask": "attachment_type", "carry_scope": "carry_unclear"}


def previous_fingerprints(
    db: Any,
    *,
    contact_respond_id: str,
    ingress: str | None,
    is_test: bool,
    turn_id: Any,
    limit: int = MAX_IDENTICAL_ASKS,
) -> list[str | None]:
    """The fingerprints stored with this contact's last `limit` completed replies,
    oldest first, scoped exactly as `turn_runtime.previous_reply_text` scopes its read."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import turn_runtime

    query = db.query(ChatbotTurn.response).filter(
        ChatbotTurn.contact_respond_id == str(contact_respond_id),
        ChatbotTurn.status == "done",
        ChatbotTurn.is_test.is_(bool(is_test)),
        ChatbotTurn.id != turn_id,
    )
    if str(ingress or "") == turn_runtime._CONSOLE_INGRESS:
        query = query.filter(ChatbotTurn.ingress == turn_runtime._CONSOLE_INGRESS)
    else:
        query = query.filter(ChatbotTurn.ingress != turn_runtime._CONSOLE_INGRESS)
    rows = query.order_by(*turn_runtime._newest_turn_first(ChatbotTurn)).limit(max(int(limit or MAX_IDENTICAL_ASKS), 1)).all()
    out = [(r[0] or {}).get(RESPONSE_KEY) if isinstance(r[0], dict) else None for r in rows]
    return list(reversed(out))


def missing_line(kind: str | None, subject: str | None) -> str:
    """AC-120: one plain line naming what is still missing, with the subject code."""
    missing = _MISSING_WORDS.get(kind or "", "more detail")
    return f"I still need {missing} for {subject}." if subject else f"I still need {missing}."


def break_reply(profile: Any, kind: str | None, subject: str | None = None) -> str:
    """Q4: never a guessed answer, no fetched data. The plain line naming what is missing, then
    the handover offer when this contact is offered one, or the refer line when its escalation
    is barred (AC-120)."""
    from app.services.chatbot import escalation_control
    from app.services.chatbot.turn import state as turn_state

    line = missing_line(kind, subject)
    if turn_state.escalation_barred(profile):
        return f"{line}\n\n{escalation_control.barred_reply(profile)}"
    if turn_state.offers_escalation(profile):
        return f"{line}\n\n{_ESCALATE_OFFER}"
    return line


#: Today's short offer sentence (catalogued in `label_catalog.INLINE`).
_ESCALATE_OFFER = "Would you like me to escalate?"


def repeat_limit(kind: str | None) -> int:
    """The loop breaker's limit for this ask kind (AC-116): this turn's policy read
    (`remember_turn_limits`), else the seed. Never a table read of its own (AC-113)."""
    limits = _TURN_LIMITS.get()
    if isinstance(limits, dict) and kind in limits:
        return int(limits[kind] or MAX_IDENTICAL_ASKS)
    rule = _SEED.get(kind or "")
    return rule.repeat_limit if rule is not None else MAX_IDENTICAL_ASKS


def apply_loop_breaker(result: Any, session_factory: Any, *, turn_id: Any, dry_run: bool) -> None:
    """The one choke point (`engine._run_turn`, after the arms have finished). Two jobs:

    * the loop breaker (AC-120): stamp this turn's ask fingerprint on its stored response and,
      on a repeat past `repeat_limit`, replace the reply with `break_reply`, disarm any pending
      question and store no fingerprint (the breaking reply is not a clarification);
    * the generic menu's subject answer (AC-130): add the "Something else?" line.

    Best effort: a failure leaves the turn as composed."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot import turn_runtime

    if getattr(result, "duplicate", False) or not isinstance(getattr(result, "reply", None), dict):
        return
    db = session_factory()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        if row is None or not isinstance(row.response, dict):
            return
        current, kind, subject = turn_fingerprint(row.trace)
        if current is None:
            menu_subject = _menu_subject(row.trace)
            old_text = jsc_text((result.reply or {}).get("text"))
            if menu_subject and old_text:
                _rewrite(db, row, result, f"{old_text.rstrip()}\n\n{something_else_line(menu_subject)}", {})
                db.commit()
            return
        limit = repeat_limit(kind)
        previous = previous_fingerprints(
            db,
            contact_respond_id=row.contact_respond_id,
            ingress=row.ingress,
            is_test=bool(row.is_test),
            turn_id=row.id,
            limit=limit,
        )
        if not should_break(previous, current, limit):
            row.response = {**row.response, RESPONSE_KEY: current}
            db.commit()
            return
        profile, _recall = turn_runtime.load_profile(db, str(row.contact_respond_id or ""))
        _rewrite(
            db,
            row,
            result,
            break_reply(profile, kind, subject),
            {RESPONSE_KEY: None, "loop_broken": current},
            trace_entry={"kind": "loop_breaker", "fingerprint": current, "previous": previous},
        )
        if not dry_run:
            _disarm_pending(db, row)
        db.commit()
        if isinstance(result.session_patch, dict) and result.session_patch.get("open_question"):
            result.session_patch = {**result.session_patch, "open_question": None}
    except Exception:  # noqa: BLE001 - the breaker never fails a turn that answered
        db.rollback()
        logger.warning("chatbot: the loop breaker did not run", exc_info=True)
    finally:
        db.close()


def jsc_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


#: AC-130: the line a generic menu's subject answer ends with.
SOMETHING_ELSE = "Something else? Tell me what you need about {subject}."


def something_else_line(subject: str) -> str:
    return SOMETHING_ELSE.replace("{subject}", subject)


def _menu_subject(trace: Any) -> str | None:
    """The subject code a generic menu turn was answered for instead (`turn/apply.py`
    `SUBJECT_DEFAULT_RULE` on the `apply` record), read off the parsed verdict."""
    from app.services.chatbot.turn.apply import SUBJECT_DEFAULT_RULE

    for rec in trace or []:
        if not isinstance(rec, dict) or rec.get("kind") != "apply":
            continue
        if SUBJECT_DEFAULT_RULE not in (rec.get("rules_fired") or []):
            return None
        verdict = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else {}
        codes = [
            str(e.get("canonical_code"))
            for e in verdict.get("entities") or []
            if isinstance(e, dict) and e.get("current_message") is True and e.get("canonical_code")
        ]
        return ", ".join(dict.fromkeys(codes)) or None
    return None


def _rewrite(
    db: Any,
    row: Any,
    result: Any,
    text: str,
    response_extra: dict[str, Any],
    *,
    trace_entry: dict[str, Any] | None = None,
) -> None:
    """Replace the turn's reply text everywhere it is held: the result, the stored response
    and the `sent` / `replied` trace records (the same three `engine._localize_result`
    brings in line)."""
    old_text = (result.reply or {}).get("text")
    reply = {**result.reply, "text": text, "quick_replies": None}
    actions = [
        {**a, "text": text, "quick_replies": None}
        if isinstance(a, dict) and a.get("kind") == "send_message" and a.get("text") == old_text
        else a
        for a in (result.actions or [])
    ]
    stored = row.response
    row.response = {
        **stored,
        **({"reply": {**(stored.get("reply") or {}), **reply}} if "reply" in stored else {}),
        **({"actions": actions} if "actions" in stored else {}),
        **response_extra,
    }
    trace = [dict(r) if isinstance(r, dict) else r for r in (row.trace or [])]
    for record in trace:
        if not isinstance(record, dict) or record.get("stage") not in ("sent", "replied"):
            continue
        raw = record.get("raw")
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("reply"), dict):
            record["raw"] = raw = {**raw, "reply": {**raw["reply"], "text": text}}
        if isinstance(raw.get("actions"), list):
            record["raw"] = {**raw, "actions": actions}
    if trace_entry is not None:
        trace.append(trace_entry)
    row.trace = trace
    result.reply, result.actions = reply, actions


def _disarm_pending(db: Any, row: Any) -> None:
    """A live turn's session: the breaking reply arms no pending question (AC-1). Through the
    session store's own read and overwrite (`conversation_variables_service`)."""
    from app.services import conversation_variables_service as session_store

    respond_io_id = str(row.contact_respond_id or "")
    state = session_store.get_for_contact(db, respond_io_id=respond_io_id)
    if state.get("open_question"):
        session_store.overwrite_for_contact(db, respond_io_id=respond_io_id, state={**state, "open_question": None})


# --------------------------------------------------------------------------- #
# CLARIFY-POLICY S4: the carried subject (owner rule R2, Q9, Q10; AC-140..AC-148)
# --------------------------------------------------------------------------- #

#: The pending kind the carried-subject ask arms (`turn/pending.py`).
CARRY_SCOPE = "carry_scope"

#: The carried kinds this ask covers, in the order a carried subject is named, with their
#: plural for "all <A plural>".
_CARRY_KINDS: tuple[tuple[str, str, str], ...] = (
    ("product", "products", "products"),
    ("customer", "customers", "customers"),
)

#: The ask label per domain ("Delivery for ..."); any other domain uses its policy label.
_ASK_LABELS = {"order": "Delivery"}


@dataclass(frozen=True)
class CarryOutcome:
    """What the carried-subject rule does this turn: `ask` (with `pending` and the reply text
    on its payload), `drop` (the carried kind's focus field is emptied), or `carry` (today)."""

    action: str
    kind: str
    field: str
    pending: Any = None


def _row_label(row: dict[str, Any]) -> str:
    return str(row.get("name") or row.get("display_name") or row.get("canonical_code") or row.get("raw") or "").strip()


def _carried_rows(rows: list[Any], own_ids: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The rows of one focus field that are a subject carried from an earlier question: never
    the contact's own linked customer (`own_ids`, from its customer scope)."""
    return [
        r for r in rows or [] if isinstance(r, dict) and str(r.get("uuid") or "") not in own_ids and _row_label(r)
    ]


def _date_words(verdict: dict[str, Any]) -> str:
    """The parsed date mode as words for the ask label ("today"), from the parser's own field."""
    date_mode = verdict.get("date_mode")
    return str(date_mode).replace("_", " ").strip() if isinstance(date_mode, str) and date_mode else ""


#: Report and ranking asks (`order_status` values, `focus.status`): a filter carried into a
#: report or a ranking drill is that report's own scope, not a subject this ask covers.
_REPORT_STATUSES = frozenset(
    {"sales_ranking", "sales_report", "sales_analysis", "top_selling", "low_stock", "low_stock_report"}
)


def _is_report(verdict: dict[str, Any], focus: Any, domain: str) -> bool:
    """Is this turn a report or a ranking (parsed fields and the held status only)? The
    `sales` domain is reports throughout; a ranking ask carries `group_by` / `top_n` /
    `ranking_drill`."""
    if domain == "sales":
        return True
    if verdict.get("order_status") in _REPORT_STATUSES or getattr(focus, "status", None) in _REPORT_STATUSES:
        return True
    return any(verdict.get(key) for key in ("group_by", "top_n", "ranking_drill"))


def carry_conflict(
    focus: Any,
    verdict: dict[str, Any],
    decision: Any,
    policy: Any,
    *,
    turn_no: int | None,
    candidates: dict[str, list[dict[str, Any]]] | None = None,
    answers_task: bool = False,
    own_customer_ids: tuple[str, ...] | None = None,
) -> CarryOutcome | None:
    """The one check (AC-140..AC-145): does the contact HOLD a subject of kind A from an earlier
    question (`focus` is the focus before this message) that the new ask could use, without
    the message restating it or saying so? Called by `turn/apply.py` before the focus rules.

    Clear (None, today's carry): the parser read an explicit reference
    (`anaphora.backward_reference`), a follow-up on the same subject (`continuation`), the
    turn REFINEs or answers the held question, or the message restates A. No clock, no turn
    count (AC-148). Parsed fields only, never the message text."""
    from app.services.chatbot.turn.decide import ANSWER, REFINE
    from app.services.chatbot.turn.pending import ask as pending_ask

    if getattr(decision, "kind", None) in (ANSWER, REFINE) or answers_task:
        return None  # it answers or narrows the held question (an open task included)
    anaphora = verdict.get("anaphora") if isinstance(verdict.get("anaphora"), dict) else {}
    if anaphora.get("backward_reference") is True:
        return None
    names_own = any(
        isinstance(e, dict) and e.get("current_message") is True for e in verdict.get("entities") or []
    )
    if verdict.get("continuation") is True and not names_own:
        # `continuation` is false by its own definition when the message names a subject of
        # its own (CONTINUATION_ADDENDUM); read live, the parser still set it beside a newly
        # named product (cand-0230), so it clears only a message that names nothing.
        return None
    domain = verdict.get("domain_hint")
    domain_row = policy.domain(domain) if isinstance(domain, str) and domain else None
    if domain_row is None or _is_report(verdict, focus, domain_row.name):
        return None
    named = [e for e in verdict.get("entities") or [] if isinstance(e, dict) and e.get("current_message") is True]
    named_kinds = {str(e.get("hint") or "") for e in named}
    for kind, field, plural in _CARRY_KINDS:
        if kind in named_kinds or kind not in domain_row.narrowing:
            continue  # restated or replaced (AC-144), or a kind this ask cannot use
        if kind == "customer" and own_customer_ids is None:
            continue  # the contact's own links are not read yet (`Profile.own_customer_ids`)
        own = frozenset(own_customer_ids or ()) if kind == "customer" else frozenset()
        carried = _carried_rows(getattr(focus, field, None) or [], own)
        if not carried:
            continue
        # The new subject B is another carried-subject kind (a product or a customer); a
        # container or an order number named alone narrows the ask, it is no second subject.
        carry_kinds = {k for k, _f, _p in _CARRY_KINDS}
        others = [e for e in named if str(e.get("hint") or "") != kind and str(e.get("hint") or "") in carry_kinds]
        if not others and verdict.get("domain_in_message") is False:
            # No new subject and no domain of its own ("this month", "50"): the message goes
            # on with the question already held (the period ask's answer is such a message,
            # `do_ask.py`), not a new question the earlier subject is carried into.
            continue
        row = "carry_combine" if others else "carry_unclear"
        mode = policy.clarify_mode(row)
        if mode in ("carry", "drop"):
            return CarryOutcome(action=mode, kind=kind, field=field)
        subject = ", ".join(dict.fromkeys(_row_label(r) for r in carried))
        if kind == "customer":
            from app.services.ledger_family import family_words

            subject = family_words(list(dict.fromkeys(_row_label(r) for r in carried))) or subject
        parts = {
            "label": _ASK_LABELS.get(domain_row.name, domain_row.label),
            "subject": subject,
            "plural": plural,
            "date_words": "" if others else _date_words(verdict),
            "new_kinds": sorted({str(e.get("hint") or "") for e in others}),
            "new_subject": ", ".join(dict.fromkeys(_named_label(e, focus, candidates) for e in others)),
        }
        options = [
            {"position": 1, "label": f"Only {subject}" if others else subject, "entity_type": None, "payload": {"keep": True}},
            {"position": 2, "label": f"All {plural}", "entity_type": None, "payload": {"keep": False}},
        ]
        pending = pending_ask(
            CARRY_SCOPE,
            options,
            team=None,
            asked_at_turn=turn_no,
            payload={
                "row": row,
                "kind": kind,
                "field": field,
                "domain": domain_row.name,
                "domains": [domain_row.name],
                "carry_text": parts,
            },
        )
        return CarryOutcome(action="ask", kind=kind, field=field, pending=pending)
    return None


def carry_scope_text(parts: dict[str, Any], new_subject: str | None = None, localizer: Any = None) -> str:
    """The carried-subject question, exactly (AC-140, AC-141), in the reply's language through
    the label catalog (`localizer`; English when None). Codes and names stay verbatim.
    `new_subject` is the newly named subject as the turn's focus names it once resolved
    (`turn/compose.py`), else the code the ask stored."""
    from app.services.chatbot.label_catalog import IDENTITY

    loc = localizer if localizer is not None else IDENTITY
    label = loc.sentence(str(parts.get("label") or ""))
    subject = str(parts.get("subject") or "")
    plural = loc.sentence(str(parts.get("plural") or ""))
    every = loc.fill("All {plural}", plural=plural)
    if parts.get("new_kinds"):
        named = new_subject or str(parts.get("new_subject") or "")
        question = loc.fill(
            "{label} for {named}: only {subject} (from earlier) or all {plural}?",
            label=label, named=named, subject=subject, plural=plural,
        )
        return f"{question}\n1. {loc.fill('Only {subject}', subject=subject)}\n2. {every}"
    words = str(parts.get("date_words") or "")
    if words:
        question = loc.fill(
            "{label} {when}: for {subject} (from earlier) or all {plural}?",
            label=label, when=loc.sentence(words), subject=subject, plural=plural,
        )
    else:
        question = loc.fill(
            "{label}: for {subject} (from earlier) or all {plural}?", label=label, subject=subject, plural=plural
        )
    return f"{question}\n1. {subject}\n2. {every}"


def _named_label(entity: dict[str, Any], focus: Any, candidates: dict[str, list[dict[str, Any]]] | None) -> str:
    """The newly named subject as the ask prints it: the resolver's own name for it (this
    turn's candidates of that kind, a customer's account name), else the focus row this turn
    settled for it, else the entity's own code."""
    from app.services.chatbot.turn.state import KIND_FIELD_MAP

    kind = str(entity.get("hint") or "")
    rows = [r for r in (candidates or {}).get(kind) or [] if isinstance(r, dict)]
    names = [str(r.get("name") or r.get("display_name") or "").strip() for r in rows]
    names = [n for n in names if n]
    if names and kind == "customer":
        # One account family named once ("HANLIM TRADING SDN BHD", not each ledger), the
        # way the SO list names its customers (`ledger_family.family_words`).
        from app.services.ledger_family import family_words

        words = family_words(list(dict.fromkeys(names)))
        if words:
            return words
    if names:
        return ", ".join(dict.fromkeys(names))
    field = KIND_FIELD_MAP.get(kind)
    focus_rows = (getattr(focus, field, None) or []) if field else []
    for row in focus_rows:
        if isinstance(row, dict) and row.get("raw") == entity.get("raw") and row.get("name"):
            return str(row["name"]).strip()
    return str(entity.get("canonical_code") or entity.get("raw") or "").strip()


def carry_scope_keeps(pending: Any, verdict: dict[str, Any], positions: list[int]) -> bool | None:
    """The answer to a `carry_scope` ask (AC-143): True keeps the carried subject (position 1),
    False drops it (position 2 or the parser's `all`), None when the message did not answer.
    Read off the parser's `open_question_answer` and the decided positions, never a word."""
    answer = verdict.get("open_question_answer") if isinstance(verdict.get("open_question_answer"), dict) else {}
    if answer.get("mode") == "all":
        return False
    picked = [p for p in positions if isinstance(p, int)]
    if not picked and answer.get("mode") == "pick":
        picked = [p for p in answer.get("picked") or [] if isinstance(p, int) and not isinstance(p, bool)]
    for option in getattr(pending, "options", None) or []:
        if option.get("position") in picked:
            return bool((option.get("payload") or {}).get("keep"))
    return None
