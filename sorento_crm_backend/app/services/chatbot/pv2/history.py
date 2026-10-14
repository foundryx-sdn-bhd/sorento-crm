"""PV2 parser input, the parts PV2-CARRY supplies (CONTRACT-PV2-13oct.md section 2): the
last 3 exchanges of this session as chat history, and the final user message = the MEMORY
block (structured state, fixed key order, every key printed, `none` when empty) followed
by the new message verbatim.

MEMORY is for the model, not the user: it prints index keys (domains, attachment type,
spec attributes) and option keys the model must echo back exactly, but never a ref's
live id: refs print their label (a product whose label is a description, its code).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.chatbot.pv2.apply import FREE_VALUE_PENDING
from app.services.chatbot.pv2.register import _clean, latest
from app.services.chatbot.pv2.state import ConversationState, Held, PendingQuestion, topic_key

PV2_HISTORY_EXCHANGES = 3
TIMEZONE = "Asia/Kuala_Lumpur"
NONE = "none"


@dataclass
class MemoryProfile:
    """The CONTACT line and EARLIER SESSIONS. Built by `turn.py` from the v1 profile
    (`turn/memory.py profile_block`, `turn/profile_facts.py`) and closed episodes
    (`turn/memory.history_items`)."""

    audience: str | None = None
    role: str | None = None
    company: str | None = None
    language: str | None = None
    brands: list[str] = field(default_factory=list)
    earlier_sessions: list[str] = field(default_factory=list)


def _v(value: Any) -> str:
    if value is None or value == "" or value == []:
        return NONE
    return str(value)


def _today(now: datetime) -> str:
    local = now.astimezone(ZoneInfo(TIMEZONE)) if now.tzinfo else now
    return f"{local:%Y-%m-%d %a} ({TIMEZONE})"


def _period(period: Any) -> str:
    if period is None:
        return NONE
    if period.date_from or period.date_to:
        return f"{_v(period.date_from)}..{_v(period.date_to)}"
    if period.preset in ("last_n_days", "last_n_months") and period.n:
        return f"{period.preset}({period.n})"
    return _v(period.preset)


def _spec_value(spec: Any) -> str:
    value = spec.value
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = f"{spec.attribute}={value}"
    if spec.value_to is not None:
        to = int(spec.value_to) if float(spec.value_to).is_integer() else spec.value_to
        text = f"{text}..{to}"
    if spec.comparator not in ("eq",):
        text = f"{text} ({spec.comparator})"
    if spec.unit:
        text = f"{text} {spec.unit}"
    return text


def _report(report: Any) -> str:
    parts = [
        f"dataset={_v(report.dataset)}",
        f"measures={_v(','.join(report.measures))}",
        f"basis={_v(report.basis)}",
        f"group_by={_v(','.join(report.group_by))}",
        f"period={_period(report.period)}",
        f"top_n={_v(report.top_n)}",
    ]
    for f in report.filters:
        vals = ",".join(f.keys) or ",".join(str(v) for v in f.values)
        parts.append(f"filter={f.dimension} {f.op} {vals}")
    if report.sort:
        parts.append(f"sort={report.sort}")
    return "; ".join(parts)


def _held_label(r) -> str:
    """R4a: a held product whose label is a description (it does not lead with the code) prints its code, the
    identifier the parser copies; a uuid key is never printed."""
    code = _clean(r.key) if r.kind == "product" else None
    return code if code and not r.label.startswith(code) else r.label


def _ref_text(r) -> str:
    q, label = r.quantity, _held_label(r)
    return label if q is None else f"{label} x{q.number:g}" + (f" {q.unit}" if q.unit else "")


def held_lines(held: Held) -> list[str]:
    """`HELD:` and its indented field lines (only fields that are set)."""
    if held.is_empty():
        return [f"HELD: {NONE}"]
    lines = [f"HELD: domain={_v(held.domain)}"]
    for kind in sorted(held.refs):
        refs = held.refs[kind]
        if refs:
            lines.append(f"  {kind}: {', '.join(_ref_text(r) for r in refs)}")
    if held.attachment_type or held.attachment_types:
        lines.append(f"  attachment_type: {', '.join(held.attachment_types or [held.attachment_type])}")
    if held.specs:
        lines.append(f"  spec: {'; '.join(_spec_value(s) for s in held.specs)}")
    if held.period is not None:
        lines.append(f"  period: {_period(held.period)}")
    if held.report is not None:
        lines.append(f"  report: {_report(held.report)}")
    if held.idea_draft_id:
        lines.append("  idea: held draft")
    return lines


def _topic_line(held: Held) -> str:
    parts = [_v(held.domain)]
    for kind in sorted(held.refs):
        if held.refs[kind]:
            parts.append(f"{kind}: {', '.join(r.label for r in held.refs[kind])}")
    if held.attachment_type or held.attachment_types:
        parts.append(f"attachment_type: {', '.join(held.attachment_types or [held.attachment_type])}")
    if held.report is not None:
        measures = ",".join(held.report.measures) or NONE
        group = ",".join(held.report.group_by) or NONE
        parts.append(f"report: {measures} by {group}, {_period(held.report.period)}")
    return " | ".join(parts)


def _missed(q: PendingQuestion) -> str | None:
    """The parsed words a question with no options was raised for: the entity that raised it, else the first
    named entity of the ask it interrupted."""
    for e in [q.raised_by, *((q.origin or {}).get("entities") or [])]:
        words = isinstance(e, dict) and (e.get("phrase") or e.get("doc_number"))
        if words:
            return str(words)
    return None


def _last_reply(state: ConversationState) -> str:
    """R4a: what the bot did last turn, from state alone (never the user's text): the open question's kind, the
    words a question with no options found nothing for (an open question asking for a value is asked), the
    domain a lookup answered, else none."""
    q = state.pending
    if q is not None:
        missed = None if q.options or q.kind in FREE_VALUE_PENDING else _missed(q)
        return f"not found {missed}" if missed else f"asked {q.kind}"
    if state.last_lookup_ask is not None:
        domain = state.held.domain or state.last_lookup_ask.get("domain")
        return f"answered {domain}" if domain else "answered"
    return NONE


def render_memory(state: ConversationState, profile: MemoryProfile, now: datetime) -> str:
    """The `<memory>` block (section 2), fixed key order, every key printed."""
    p = profile
    lines = [
        "<memory>",
        f"TODAY: {_today(now)}",
        (
            f"CONTACT: audience={_v(p.audience)}; role={_v(p.role)}; company={_v(p.company)}; "
            f"language={_v(p.language)}; brands={_v(','.join(p.brands))}"
        ),
        *held_lines(state.held),
    ]
    if state.pending is None:
        lines.append(f"PENDING QUESTION: {NONE}")
    else:
        q = state.pending
        lines.append(f'PENDING QUESTION: kind={q.kind}; asked="{q.asked}"')
        if q.options:
            lines.append(
                "  options: " + " | ".join(f"{n}={o.key} ({o.label})" for n, o in enumerate(q.options, start=1))
            )
        else:
            lines.append(f"  options: {NONE}")
    if state.topics:
        lines.append("TOPICS (switch_back keys, newest first):")
        lines.extend(f"  {topic_key(i)}: {_topic_line(t)}" for i, t in enumerate(state.topics))
    else:
        lines.append(f"TOPICS (switch_back keys, newest first): {NONE}")
    lines.append(f"LAST REPLY: {_last_reply(state)}")
    shown = latest(state.register)
    if shown:
        # R4a: the rows the latest reply printed (the register), a child marked with its parent's number.
        number = {e.id: n for n, e in enumerate(shown, start=1)}
        lines.append("LAST ANSWER ROWS: " + " | ".join(
            f"{n}={e.kind} {e.label}" + (f" (in {number[e.parent]})" if e.parent in number else "")
            for n, e in enumerate(shown, start=1)
        ))
    elif state.last_rows:
        lines.append("LAST ANSWER ROWS: " + " | ".join(f"{n}={o.label}" for n, o in enumerate(state.last_rows, 1)))
    else:
        lines.append(f"LAST ANSWER ROWS: {NONE}")
    if p.earlier_sessions:
        lines.append("EARLIER SESSIONS:")
        lines.extend(f"  {s}" for s in p.earlier_sessions[:3])
    else:
        lines.append(f"EARLIER SESSIONS: {NONE}")
    lines.append("</memory>")
    return "\n".join(lines)


def render_user_message(state: ConversationState, profile: MemoryProfile, now: datetime, text: str) -> str:
    """The final `user` message: MEMORY, then the new message verbatim."""
    return f"{render_memory(state, profile, now)}\n<message>{text}</message>"


#: A history reply's budget (crew eval 14 Oct: one report follow-up reached 24,356 prompt
#: tokens against a 12k bar, every token of it the last 3 replies' report tables). The
#: model needs what the bot SAID, not its rows: what was asked is in MEMORY's HELD block
#: (the report spec, the subject) and the rows it printed are LAST ANSWER ROWS.
REPLY_CHAR_CAP = 400
#: A user message in history is capped too (a pasted list); the current message never is.
USER_CHAR_CAP = 400


def compact_reply(text: str, cap: int = REPLY_CHAR_CAP) -> str:
    """`text` whole when it fits `cap`, else its opening lines up to `cap` characters and
    one marker naming how many lines were left out. Cut at a line, never inside one,
    unless the first line alone is longer than `cap`."""
    text = text or ""
    if len(text) <= cap:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > cap:
            break
        kept.append(line)
        used += len(line) + 1
    if not kept:
        kept = [lines[0][:cap]]
    rest = sum(1 for line in lines[len(kept):] if line.strip())
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + f"\n[... {rest} more lines not shown]"


def compact_exchanges(exchanges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The last 3 exchanges with every message capped (`compact_reply`)."""
    return [
        (compact_reply(u, USER_CHAR_CAP), compact_reply(b, REPLY_CHAR_CAP))
        for u, b in exchanges[-PV2_HISTORY_EXCHANGES:]
    ]


def history_messages(exchanges: list[tuple[str, str]]) -> list[dict[str, str]]:
    """The last 3 exchanges, oldest first, as chat messages (user text, bot reply)."""
    out: list[dict[str, str]] = []
    for user_text, bot_text in exchanges[-PV2_HISTORY_EXCHANGES:]:
        out.append({"role": "user", "content": user_text})
        out.append({"role": "assistant", "content": bot_text})
    return out


def load_exchanges(
    db: Any,
    *,
    contact_respond_id: str,
    ingress: str | None,
    is_test: bool,
    since: datetime | None,
    test_run_id: str | None = None,
) -> list[tuple[str, str]]:
    """This session's last 3 completed exchanges, oldest first: the same rows and scopes
    as `turn_runtime.recent_exchanges` (same contact, same world, same side of the
    console boundary), limited to turns at or after `since` (section 2: this session
    only). A lookup failure is no history, never a failed turn."""
    from app.models.chatbot_turn import ChatbotTurn
    from app.services.chatbot.turn_runtime import _CONSOLE_INGRESS, _envelope_text, _newest_turn_first

    try:
        query = db.query(ChatbotTurn.envelope, ChatbotTurn.response).filter(
            ChatbotTurn.contact_respond_id == str(contact_respond_id),
            ChatbotTurn.status == "done",
            ChatbotTurn.is_test.is_(bool(is_test)),
        )
        if str(ingress or "") == _CONSOLE_INGRESS:
            query = query.filter(ChatbotTurn.ingress == _CONSOLE_INGRESS)
        else:
            query = query.filter(ChatbotTurn.ingress != _CONSOLE_INGRESS)
        if since is not None:
            query = query.filter(ChatbotTurn.created_at >= since)
        if test_run_id:
            # A console chat is its run: an earlier run's turns are not this chat's history.
            query = query.filter(ChatbotTurn.envelope["test_run_id"].as_string() == str(test_run_id))
        rows = query.order_by(*_newest_turn_first(ChatbotTurn)).limit(PV2_HISTORY_EXCHANGES * 2).all()
    except Exception:  # noqa: BLE001 - no history is no lines, never a failure
        return []
    pairs: list[tuple[str, str]] = []
    for envelope, response in rows:
        reply = response.get("reply") if isinstance(response, dict) else None
        answer = reply.get("text") if isinstance(reply, dict) else None
        if not (isinstance(answer, str) and answer.strip()):
            continue
        pairs.append((_envelope_text(envelope) or "(media)", answer))
        if len(pairs) == PV2_HISTORY_EXCHANGES:
            break
    return list(reversed(pairs))
