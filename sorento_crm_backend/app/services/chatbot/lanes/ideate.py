"""The `ideate` lane: ideation is an MCP TOOL, not a special case (D6, AC-303).

The owner said it twice - "ideation should be at the MCP side" - so this lane does what
every business tool does: it calls `crm_ideation_turn` through the same
`MCPRuntimeClient` the AI assistant uses (D10) - an HTTP JSON-RPC client against the
configured MCP server, not an in-process call - with exactly the arguments
`ideate-turn-http` sends today. Nothing here knows that ideation is different from a stock
lookup, which is the whole point: the day a second write tool arrives it needs no lane.

Two ports, both small and both verbatim:

* `build_arguments` is `ideate-turn-http`'s `jsonBody` expression, including the
  `media_selection` derivation - which is derived from the parser's EXISTING
  `reference_positions` / `select_all_expanded` signals rather than by keyword-matching
  the customer's text (D11), and is OMITTED entirely when no media menu is open, because
  omitting is behaviourally identical to dismissing and a mid-selection CRM interrupt must
  never be swallowed.
* `build_reply` is `build-ideate-reply.js`: the endpoint already embeds the deep link in
  `reply_text` on `complete`, so the link is appended only when it is not already there
  (unconditional appending rendered it twice), and `session_vars.ideation` is read through
  BOTH shapes because the endpoint keys it flat while n8n's own writer nests it.
"""
from __future__ import annotations

import json
import logging
import mimetypes
from typing import Any, Mapping

from app.services.chatbot import jsc
from app.services.chatbot import required_fields as rf

logger = logging.getLogger(__name__)

TOOL_NAME = "crm_ideation_turn"

ASK_NAME = "ideation"
ASK_QUESTION = "What is the idea? Tell me the problem or what you would like built."


def _resolve_problem(_db: Any, word: str, _extras: dict[str, Any]) -> rf.Resolved:
    text = (word or "").strip()
    return rf.Resolved("ok", text, text) if text else rf.Resolved("unknown")


#: The one required field is the idea. The core turn decides (`ask_idea`) and speaks in the
#: user's language. The question, `cancelled` and `give_up` texts are the helper's required
#: fallbacks and are never shown: the lane never passes a reply to `rf.collect`, so a "cancel"
#: reply reaches the core as an answer and gets the core's own give-up line.
IDEATION_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(rf.FieldSpec(
        name="problem", noun="idea", question=ASK_QUESTION, resolve=_resolve_problem, allow_all=False,
    ),),
    reroute={
        "message_type": "business_query",
        "intent_hint": "submit_idea",
        "domain_hint": "ideate",
    },
    cancelled="Okay, I have dropped that idea.",
    give_up="I could not get the idea. Send it in one message when you are ready.",
))


def build_arguments(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """`ideate-turn-http`'s body, field for field.

    `respond_io_id` is `String(...)`-coerced because respond.io contact ids arrive as
    numbers on some webhook shapes and the endpoint keys a text column on it.
    """
    qf = jsc.get(jsc.get(ctx, "parse"), "output") or {}
    session_vars = jsc.get(jsc.get(ctx, "session"), "session_vars") or {}
    # BOTH shapes, same as the JS: n8n's writer nests under `variables`, the endpoint
    # keys `ideation` flat.
    nested = jsc.get(jsc.get(session_vars, "variables"), "ideation")
    ideation = nested if jsc.truthy(nested) else jsc.get(session_vars, "ideation")
    ideation = ideation if ideation is not None else None

    inner = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message") or {}
    body: dict[str, Any] = {
        "respond_io_id": jsc.js_string(jsc.get(jsc.get(ctx, "contact"), "id")),
        "message_text": jsc.get(inner, "text"),
        "session_vars": {"ideation": ideation},
    }
    name = jsc.get(jsc.get(ctx, "contact"), "firstName")
    if jsc.truthy(name):
        body["submitter_name"] = name

    # The answering turn of an open ideation ask: the reply text is the message, and the core
    # turn knows it is the answer to its own ask-back.
    slot = jsc.get(qf, "required_ask")
    reply_text = jsc.get(qf, "required_ask_reply")
    if (
        isinstance(slot, dict)
        and slot.get("ask") == ASK_NAME
        and isinstance(reply_text, str)
        and reply_text.strip()
    ):
        body["ask_reply"] = True
        body["message_text"] = reply_text

    # ONLY when a media menu is outstanding. No menu open means the field is omitted and
    # the turn routes normally.
    if jsc.truthy(ideation) and jsc.truthy(jsc.get(ideation, "pending_media")):
        positions = jsc.get(qf, "reference_positions")
        positions = positions if jsc.is_array(positions) else []
        if len(positions) > 0:
            body["media_selection"] = ",".join(jsc.js_string(p) for p in positions)
        elif jsc.get(qf, "select_all_expanded") is True:
            body["media_selection"] = "all"
    return body


def call_ideation_tool(**kwargs: Any) -> dict[str, Any]:
    """Call `crm_ideation_turn` through the MCP client (D6, D10).

    A SEAM, deliberately: every test in the suite patches this one name, so nothing below
    it has to be mocked and no test reaches a live MCP server. The tool returns its result
    as MCP content text, so it is decoded here rather than at the call site - a caller
    that had to know MCP's envelope would be a caller that knows ideation is special.
    """
    from app.services.ai_assistant_service import MCPRuntimeClient
    from app.services.ai_assistant_service import settings as ai_settings

    # The SAME configured endpoint the AI assistant calls (H52: the URL is configuration,
    # never a literal), so there is one MCP address in the install and not two.
    client = MCPRuntimeClient(
        ai_settings.ai_assistant_mcp_url,
        timeout_seconds=ai_settings.ai_assistant_mcp_timeout_seconds,
    )
    raw = client.call_tool(TOOL_NAME, dict(kwargs))
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        # The tool answered with prose rather than JSON, which is a broken tool and not a
        # shape to guess at. Raised so the turn is recorded `failed` with the text.
        raise RuntimeError(f"{TOOL_NAME} returned a non-JSON payload: {str(raw)[:200]}")
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{TOOL_NAME} returned {type(decoded).__name__}, expected an object")
    return decoded


def build_reply(result: Mapping[str, Any]) -> dict[str, Any]:
    """`build-ideate-reply.js` - the item it puts on the shared compile path.

    `ideation` is the pointer the tail persists, and it is read through both session-vars
    shapes. `ideate_status` defaults to `'error'`, which is the JS's own fallback and the
    reason a tool that answers without a status still reads as a failure on the trace.

    No raw ``link`` append (AC-1216): the capture turn's reply copy already
    carries the link itself, deliberately, via the facts block - appending it again
    here would risk a doubled URL rather than fixing a missing one.
    """
    r = result if isinstance(result, dict) else {}
    response = jsc.get(r, "reply_text") or ""

    session_vars = jsc.get(r, "session_vars") or {}
    if jsc.has(session_vars, "ideation"):
        ideation = jsc.get(session_vars, "ideation")
    else:
        ideation = jsc.get(jsc.get(session_vars, "variables"), "ideation")
    return {
        "response": response,
        "manualResponse": True,
        "includeResponse": True,
        "ideation": ideation if ideation is not None else None,
        "ideate_status": jsc.get(r, "status") or "error",
    }


def offered_images(result: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """#1277: the images the reply's media menu offered, as `send_attachments` entries.

    One entry per image, captioned with its menu number so the customer can match the
    picture to the "1, 2, ..." list the text asks about. Other kinds keep their number in
    the list but are not sent. `None`, not `[]`, when there is nothing to send: the
    engine's `_send_actions` emits a `send_attachments` action for any non-None value.
    The window needs no check of its own: this is the reply to a message the customer
    just sent, the same as every other chatbot attachment send.
    """
    entries: list[dict[str, Any]] = []
    for media in jsc.get(result, "offered_media") or []:
        if not isinstance(media, dict) or media.get("kind") != "image" or not media.get("url"):
            continue
        position = media.get("position")
        filename = media.get("filename") or f"image-{position}.jpg"
        mime, _ = mimetypes.guess_type(filename)
        entries.append(
            {
                "url": media["url"],
                "filename": filename,
                "mimeType": mime if mime and mime.startswith("image/") else "image/jpeg",
                "attachmentType": "image",
                "caption": jsc.js_string(position),
            }
        )
    return entries or None


def run(
    ctx: Mapping[str, Any], item: Mapping[str, Any], *, dry_run: bool = False
) -> dict[str, Any]:
    """The whole lane: build the arguments, call the tool, hand the tail its fragment.

    **A dry run calls the tool too, as a test turn** (#1179, owner ruling 24 Sep 2026).
    `crm_ideation_turn` mints or mutates a REAL idea record in the shared service, so the
    first cut of D14 did not call it at all and stood a placeholder reply in - which left
    ideation untestable anywhere but live WhatsApp. Now `is_test` rides the call: the
    shared service stores the idea flagged as a test (hidden from the board by default),
    the endpoint skips its own session write, and the console reads the real intake
    replies. The rest of the dry run is unchanged - the tail writes nothing, the action
    carries `dry_run: true`, and nothing here sends.

    The reply rides on `item.outcome_fragment['build-ideate-reply']` - RS-6.1c's own
    mechanism, and the exact key `build-outcome` reads - so the tail needs no ideate arm.

    **The item is `build-ideate-reply`'s own output, and it carries NO `branch_kind`.**
    That is not tidiness. `entry-gate` fires on `(item.branch_kind ?? '') !== ''`, so an
    item that kept the router's tag would run `escalate-catalog` on a turn n8n never runs
    it on - and `ideate` is not one of its nine arms, so it would fall through the switch
    and put an EMPTY response into the outcome hub. The compile ladder happens to check
    the ideate fragment first, so today that empty arm loses; a ladder edit is all it
    would take for it to win, which is exactly the shape S4 hit on `low_signal`.
    """
    # `is_test` is always sent, true or false: the shared service keys the board filter on
    # it, and a live turn that omitted it would be one more shape to read as "live".
    result = call_ideation_tool(**build_arguments(ctx), is_test=dry_run)
    reply = build_reply(result)
    # The core turn decided to ask for the idea: open the slot so the next short reply comes
    # back here. The reply shown stays the tool's own localised text.
    required_ask = None
    if reply["ideate_status"] == "ask_idea":
        required_ask = rf.collect(None, IDEATION_ASK, given={}).slot
    return {
        "required_ask": required_ask,
        "item": {**reply, "outcome_fragment": {"build-ideate-reply": reply}},
        "reply_extras": {
            "manualResponse": reply["manualResponse"],
            "includeResponse": reply["includeResponse"],
            "ideate_status": reply["ideate_status"],
            # #1277: the offered images ride the engine's existing `send_attachments`
            # action, sent after the text (n8n does the Respond.io send, D9).
            "attachments_src": offered_images(result),
        },
    }
