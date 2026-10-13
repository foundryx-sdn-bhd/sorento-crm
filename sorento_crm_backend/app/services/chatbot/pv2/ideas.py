"""Ideation dialogue for parser v2 (CONTRACT-PV2-13oct.md sections 3 and 5 "Ideation";
PLAN-pv2-ideas.md Design B).

Shares only the dialogue mechanics: the similar-ideas list, the missing-field question and the
multi-idea question become PendingQuestions, and a parser-resolved answer is mapped onto the
additive kwargs of `handle_capture_turn`. Extraction and save stay ideation's own. No text
matching here: an answer's `option_key` must EXACTLY equal a pending option key, else the
turn is not ours (`None`, CARRY's miss path). Ids never appear in option keys, labels or asked
text.

`ask` and `pending` are the contract models or plain dicts; `_get` reads either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.ideation_capture_replies import render_reply
from app.services.ideation_capture_service import (
    _captured_pointer,
    _idea_link,
    handle_capture_turn,
)

IDEA_PENDING_KINDS = frozenset({"idea_similar", "idea_missing_field", "idea_multi"})

_LANGS = ("en", "ms", "zh")

#: Copy lives here tonight (crew decision 13 Oct); the asked line takes the idea count.
_MULTI_ASKED = {
    "en": "I read {n} ideas in this message.",
    "ms": "Saya baca {n} idea dalam mesej ini.",
    "zh": "我在这条消息中读到 {n} 个想法。",
}
_MULTI_LABELS = {
    "en": ("Save as one idea", "Send them one at a time"),
    "ms": ("Simpan sebagai satu idea", "Hantar satu demi satu"),
    "zh": ("保存为一个想法", "逐个发送"),
}
_NEW_IDEA_LABEL = {"en": "New idea", "ms": "Idea baharu", "zh": "新想法"}


@dataclass
class IdeaTurnResult:
    status: str
    reply_text: str
    language: str
    session_vars: dict
    pending: dict | None  # PendingQuestion shape (contract section 3) as a plain dict
    idea_draft_id: str | None  # for Held.idea_draft_id
    link: str | None = None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` from a dict or an attribute object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _lang(value: Any) -> str:
    return value if value in _LANGS else "en"


def _pending(kind: str, asked: str, options: list[dict], turn_no: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "asked": asked,
        "options": options,
        "asked_turn": turn_no,
        "misses": 0,
        "v1_kind": None,
    }


def _matched_option(pending: Any, option_key: Any) -> Any | None:
    """The pending option whose key EXACTLY equals `option_key`, else None."""
    if not isinstance(option_key, str):
        return None
    for opt in _get(pending, "options") or []:
        if _get(opt, "key") == option_key:
            return opt
    return None


def _map_result(res: dict[str, Any], language_in: str, turn_no: int, db: Any) -> IdeaTurnResult:
    """Capture result -> turn result: the pending it leaves and the idea it now holds."""
    status = res.get("status")
    reply = res.get("reply_text") or ""
    lang = _lang(res.get("language") or language_in)
    sv = res.get("session_vars") or {}
    pointer = sv.get("ideation") if isinstance(sv, dict) else None
    pending: dict[str, Any] | None = None
    draft_id: str | None = None

    if status == "similar_offered" and isinstance(pointer, dict):
        options = [
            {"key": str(n), "label": str(s.get("title") or ""), "payload": {"choice": n}}
            for n, s in enumerate(pointer.get("similar") or [], start=1)
        ]
        options.append({"key": "new", "label": _NEW_IDEA_LABEL[lang], "payload": {"choice": "new"}})
        # The lead line only: the numbered lines carry each idea's CRM link (an id in a URL), and the
        # options already list the titles for MEMORY.
        pending = _pending("idea_similar", reply.split("\n", 1)[0], options, turn_no)
    elif status == "ask_idea":
        pending = _pending("idea_missing_field", reply, [], turn_no)
    elif status in ("complete", "similar_picked") and isinstance(pointer, dict):
        if pointer.get("status") == "captured":
            draft_id = pointer.get("idea_id")

    return IdeaTurnResult(
        status=status,
        reply_text=reply,
        language=lang,
        session_vars=sv,
        pending=pending,
        idea_draft_id=draft_id,
        link=res.get("link"),
    )


def run_idea_turn(
    db: Any,
    *,
    ask: Any,
    language: str,
    respond_io_id: str,
    message_text: str,
    session_vars: dict,
    pending: Any,
    turn_no: int,
    is_test: bool = False,
    submitter_name: str | None = None,
) -> IdeaTurnResult | None:
    """One ideation turn. `None` = not an idea turn (an answer whose key is no pending option)."""
    lang = _lang(language)

    def capture(text: str, *, ask_reply: bool = False, choice_in: Any = None) -> IdeaTurnResult:
        res = handle_capture_turn(
            db,
            respond_io_id=respond_io_id,
            message_text=text,
            submitter_name=submitter_name,
            session_vars_in=session_vars if is_test else None,
            is_test=is_test,
            ask_reply=ask_reply,
            choice_in=choice_in,
            update_held=True,
        )
        return _map_result(res, lang, turn_no, db)

    def missing_field_question() -> IdeaTurnResult:
        reply = render_reply("ask_idea", {}, user_message=message_text, language=lang, db=db)
        return IdeaTurnResult(
            status="ask_idea",
            reply_text=reply,
            language=lang,
            session_vars=session_vars,
            pending=_pending("idea_missing_field", reply, [], turn_no),
            idea_draft_id=None,
        )

    kind = _get(pending, "kind")
    if _get(ask, "act") == "answers_bot_question" and kind in IDEA_PENDING_KINDS:
        if kind == "idea_missing_field":
            return capture(message_text, ask_reply=True)
        opt = _matched_option(pending, _get(_get(ask, "answer"), "option_key"))
        if opt is None:
            return None
        payload = _get(opt, "payload") or {}
        choice = _get(payload, "choice")
        if kind == "idea_similar":
            valid = choice == "new" or (isinstance(choice, int) and not isinstance(choice, bool))
            return capture(message_text, choice_in=choice) if valid else None
        # idea_multi
        if choice == "one":
            return capture(_get(payload, "message_text") or message_text)
        if choice == "separate":
            return missing_field_question()
        return None

    ideation = _get(ask, "ideation")
    if _get(ideation, "adds_to_held_idea"):
        held = _captured_pointer((session_vars or {}).get("ideation"), is_test)
        if held is not None:
            # There is no ss update API: point at the idea, the user adds to it there.
            reply = render_reply(
                "similar_picked",
                {"idea_number": held.get("idea_number"), "link": _idea_link(held.get("idea_id"))},
                user_message=message_text,
                language=lang,
                db=db,
            )
            return IdeaTurnResult(
                status="held_idea_open",
                reply_text=reply,
                language=lang,
                session_vars=session_vars,
                pending=None,
                idea_draft_id=held.get("idea_id"),
            )

    count = _get(ideation, "idea_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 2:
        one, separate = _MULTI_LABELS[lang]
        asked = _MULTI_ASKED[lang].format(n=count)
        reply = f"{asked}\n1. {one}\n2. {separate}"
        options = [
            {"key": "one", "label": one, "payload": {"choice": "one", "message_text": message_text}},
            {"key": "separate", "label": separate, "payload": {"choice": "separate"}},
        ]
        return IdeaTurnResult(
            status="idea_multi_offered",
            reply_text=reply,
            language=lang,
            session_vars=session_vars,
            pending=_pending("idea_multi", reply, options, turn_no),
            idea_draft_id=None,
        )

    return capture(message_text)
