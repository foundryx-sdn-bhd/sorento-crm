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
    _row("carry_unclear", "Carried subject, no reference", "ask", ("carry", "drop"), "ask, keep or drop"),
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


def mode(db: Any, kind: str) -> str:
    """The mode the bot uses for `kind` this turn (`load_clarify_policy`)."""
    rule = load_clarify_policy(db).get(kind)
    return rule.mode if rule is not None else "ask"


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


def _subject_code(focus: Any) -> str | None:
    """The turn's subject, as codes off the focus the turn ended with (products first, then
    customers): resolved codes, never the message text."""
    if not isinstance(focus, dict):
        return None
    for key in ("products", "customers"):
        codes = sorted(
            {
                str(row.get("canonical_code") or row.get("uuid") or "").strip()
                for row in focus.get(key) or []
                if isinstance(row, dict)
            }
            - {""}
        )
        if codes:
            return ",".join(codes)
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
    subject = _subject_code(focus)
    return ask_fingerprint(kind, param or "", subject), kind, subject


#: A turn plan's `ask` name -> its policy kind (`CLARIFY_POLICY_ROWS`).
_PLAN_ASK_KINDS = {"attachment_type_ask": "attachment_type"}


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


def repeat_limit(db: Any, kind: str | None) -> int:
    """The loop breaker's limit for this ask kind (AC-116): its policy row, else the seed."""
    rule = load_clarify_policy(db).get(kind or "")
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
        limit = repeat_limit(db, kind)
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
