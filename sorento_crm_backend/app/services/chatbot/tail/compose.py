"""Port of `crossdomain-compose.js`: fold the cross-domain block in, re-seal the reply.

It sits between `compile-current-state` and the sender so the escalate phrase reaches the
customer text AND the persisted state in ONE place. Every fail-silent exit returns the
envelope UNTOUCHED, so a turn with no cross-domain block is byte-identical to what the
compiler sealed.

**Two branches, and which one runs is a structural question, not a textual one.** The JS
asked `/^Previous turn \\(/` of the state it had just written; the port takes
`answered_domain` from `CompiledState` instead (R3, D11). On an ANSWERED turn the block
plus the escalate phrase are APPENDED and the quick replies gain the two offer buttons;
on a TOTAL MISS the block is inserted at the start of the winning marker's own line, so
the direct fact outranks a consolation list and never sits below the escalate question.

**The markers are matched case-insensitively.** Several live arms render them in lower
case, and under a case-sensitive scan every one of them fell through to the append-at-END
fallback - i.e. below the numbered picker and below the escalate question, which is the
exact ordering defect this block exists to prevent. Only a lower-cased COPY is searched;
the original string is what gets sliced, so no casing in the customer's text is altered.
"""
from __future__ import annotations

from typing import Any, Mapping

from app.services.chatbot import jsc
from app.services.chatbot.tail.reply_ladder import sanitize_em_dash, seal, strip_undefined
from app.services.chatbot.turn.task import REFER_TO_SALESMAN

# Earliest wins, matched case-insensitively.
MARKERS = (
    "Related products:",  # build-suggest-offer D3 sibling picker
    "Try:",  # D2 alternatives, list mode
    "Did you mean",  # D1 single-token code mode and D1 multi-token
    "Here are the closest matches:",  # D1 numbered mode and D2 numbered mode
    "Would you like me to escalate",  # catch-all: the frozen phrase, on every offer branch
    # ESCALATION-CONTROL (owner ruling Q2): a blocked contact's miss carries the salesman
    # line where the offer would be, and the block goes above it the same way.
    REFER_TO_SALESMAN,
)


def _cross_domain_block(result: Any) -> Mapping[str, Any]:
    """`$('build-result').first().json.result.xd.block || {}`.

    `build-result` emits `{...validator, result: {rows, has_result, is_valid, tool, promo,
    xd, tier_probe}}`, so the double `result` is the real shape, not a typo. A `result`
    payload without it is a broken caller and n8n threw on it; this raises with a sentence
    instead, and the engine records a failed turn with a reason.
    """
    inner = jsc.get(result, "result")
    if not isinstance(inner, dict):
        raise ValueError(
            "crossdomain-compose: the `result` payload carries no nested `result` object, "
            "so there is no cross-domain block to read"
        )
    xd = jsc.get(inner, "xd")
    if not isinstance(xd, dict):
        raise ValueError("crossdomain-compose: `result.xd` is missing, so the block cannot be read")
    block = jsc.get(xd, "block")
    return block if isinstance(block, dict) else {}


def crossdomain_compose(
    item: Mapping[str, Any],
    *,
    result: Any = None,
    answered: bool = False,
    include_offer: bool = True,
    covers: bool = False,
) -> dict[str, Any]:
    """`{reply}` in, `{reply}` out. `result` is the `build-result` carrier (nullable).

    `include_offer` (#1262 slice 11, F8): staff (`turn.state.is_staff_profile`) get
    the rung's own block folded in exactly as before, but no bot-initiated "Would you
    like me to escalate" phrase - the ladder's own offer site, the ONE gate shared
    with `turn/compose.py`'s (that module's own composer offer, this one's
    cross-domain rung).
    """
    if result is None:
        return dict(item)  # build-result did not run: pass the turn through byte-identical

    block = _cross_domain_block(result)
    if jsc.get(block, "any") is not True or not jsc.truthy(jsc.get(block, "block")):
        return dict(item)

    patch = jsc.get(jsc.get(item, "reply"), "session_patch")
    if not isinstance(patch, dict):
        return dict(item)
    # A deep copy, because the JS edits a `JSON.parse(JSON.stringify(o))` clone and the
    # original object is still referenced by the item this function may return unchanged.
    out = _deep_copy(patch)
    variables = out.get("variables") or {}
    user_response = out.get("user_response")
    if not isinstance(user_response, str) or (user_response.strip() == "" and not covers):
        return dict(item)

    # LOCKED WORDING: this exact prefix is the contract `output_exchange.offer_is_open`
    # reads (and R3's `pending` marker mirrors). Do not reword.
    phrase = f"Would you like me to escalate to {jsc.js_string(jsc.get(block, 'team'))} team?"

    if answered:
        # PARTIAL turn: some asked products came back empty. Only speak when the turn
        # actually answered something.
        last_result_set = variables.get("last_result_set")
        if not jsc.is_array(last_result_set) or len(last_result_set) == 0:
            return dict(item)
        block_text = jsc.js_string(jsc.get(block, "block"))
        body = f"{user_response}\n\n{block_text}" if user_response.strip() else block_text
        out["user_response"] = f"{body}\n\n{phrase}" if include_offer else body
        # BOTH strings: the visible text so the customer can act, the state so the parser
        # can reconcile the "yes".
        if include_offer:
            variables["response"] = f"{jsc.js_string(variables.get('response'))}. {phrase}"
            existing = out.get("quick_reply")
            out["quick_reply"] = (
                f"{jsc.js_string(existing)},Yes escalate,No it's okay"
                if jsc.truthy(existing)
                else "Yes escalate,No it's okay"
            )
    else:
        # TOTAL MISS: the block goes directly under the miss sentence and ABOVE whatever
        # continues the message. State already carries the phrase on this branch, so
        # state is left alone. `include_offer=False` (staff) never printed the phrase
        # onto `user_response` in the first place (the miss arm that built it reads the
        # SAME gate), so there is nothing to hold back here - the markers below are
        # about WHERE the block goes, not whether the phrase exists.
        hay = user_response.lower()
        index = -1
        for marker in MARKERS:
            found = hay.find(marker.lower())
            if found != -1 and (index == -1 or found < index):
                index = found
        if index == -1:
            out["user_response"] = (
                jsc.js_string(jsc.get(block, "block"))
                if covers
                else f"{user_response}\n{jsc.js_string(jsc.get(block, 'block'))}"
            )
        else:
            # Insert at the start of the winning marker's own SENTENCE or LINE, never
            # mid-line: on the multi-token arm the marker sits inside a line, where the
            # raw index would tear the picker's header off its token.
            newline = user_response.rfind("\n", 0, index + 1)
            # `+ 2`, not `+ 1`: JS lastIndexOf allows a match STARTING at `index`, and a
            # two-character needle needs the slice to reach `index + 2` for that.
            dot = user_response.rfind(". ", 0, index + 2)
            at = max(0 if newline == -1 else newline + 1, 0 if dot == -1 else dot + 2)
            head = "" if covers else user_response[:at].rstrip()
            out["user_response"] = (
                (f"{head}\n" if head else "")
                + f"{jsc.js_string(jsc.get(block, 'block'))}\n\n"
                + user_response[at:]
            )

    out["variables"] = variables
    # Fold em-dashes AGAIN: this node introduces new dynamic text (the block is
    # CRM-field-derived) AFTER the compiler already sanitised its own output, and this is
    # what the sender and the session write both see.
    sanitize_em_dash(out)
    # Re-SEAL rather than write `text` / `quick_replies` by hand: `out` is the edited
    # patch and the two views are derived from it, so an appender that forgets one of
    # them is not expressible. Stripped at the same boundary the compiler strips at: this
    # is a serialised n8n item, so a `quick_reply` the turn never set is an ABSENT key,
    # not a sentinel - and the sentinel is not JSON, so it would fail the turn row's own
    # write on the way past.
    return strip_undefined({"reply": seal(out)})


def _deep_copy(value: Any) -> Any:
    """`JSON.parse(JSON.stringify(o))` - structure only, no shared references."""
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value
