"""The ideation-turn write tool for the MCP server (D6, AC-303, AC-307).

One tool is registered here:

- ``crm_ideation_turn``  POST  /api/v1/external/ideation/turn

It is an ``external=True`` catalog spec (see ``catalog.py``) and therefore has a
dedicated handler rather than the generic HTTP template, for one reason that
matters to the caller: the template types every body param as ``str``, so
``session_vars`` would arrive at the model as a STRING and the ideation pointer
(a nested object carried turn to turn) would have to be JSON-encoded by hand at
every call site. Here it is declared as an object, which is what the endpoint's
``IdeationTurnRequest`` actually takes and what the chatbot's ``ideate`` lane
already builds.

Path + method + description come from the shared ToolSpec so there is a single
source of truth for what the admin catalog shows and what this posts.

**The optional arguments are omitted, never sent as null.** ``media_selection``
in particular is behaviourally significant: the endpoint reads its ABSENCE as
"no media menu answer in this turn" (the n8n ``ideate-turn-http`` body omits it
for exactly that reason), and a null would be one more shape for the service to
have to read the same way.

**It IS a write tool, and the in-app assistant is told so by name.** The
assistant's gate matched a write-verb SUFFIX (``_create`` / ``_submit`` /
``_close`` ...) and this tool is named for the turn it runs, per AC-307 and the
lane that calls it - so it read as a read tool and neither the write-confirm gate
nor the prompt dry-run suppression applied to it. The NAME is a published
contract (this catalog and n8n both reference it), so it stays and write-ness is
declared instead: ``ai_assistant_service._WRITE_TOOL_NAMES`` lists it, and
``_WRITE_TOOL_PERMISSIONS`` requires ``ideation.board.view`` of the confirming
user. The chatbot's ``ideate`` lane, which is what this exists for, calls it
without a confirm because there is no user there to ask - its own guard is
``is_test`` (#1179): a dry-run turn calls it flagged as a test turn, so the idea
is stored hidden from the board and the endpoint persists no session state.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import Context

from sorento_crm_mcp.catalog import CATALOG
from sorento_crm_mcp.settings import Settings

logger = logging.getLogger(__name__)

IDEATION_TOOLS: tuple[str, ...] = ("crm_ideation_turn",)

_SPECS = {s.name: s for s in CATALOG if s.name in IDEATION_TOOLS}


def register_ideation_tools(mcp: Any, settings: Settings) -> None:
    """Register the ideation write tool on the given FastMCP instance."""
    _ = settings  # the lifespan client carries the base URL and the API key

    async def crm_ideation_turn(
        ctx: Context,
        respond_io_id: str,
        message_text: str,
        session_vars: dict[str, Any] | None = None,
        submitter_name: str | None = None,
        media_selection: str | None = None,
        is_new_idea: bool | None = None,
        is_test: bool | None = None,
        ask_reply: bool | None = None,
    ) -> str:
        """Record or continue one turn of an idea a customer is submitting.

        ``respond_io_id`` is the respond.io contact id (not an internal UUID) and
        ``message_text`` is what they just said. ``session_vars`` carries the
        ``ideation`` pointer returned by the previous turn (omit it on the
        first). ``submitter_name`` is a display-name fallback used only when the
        contact row has no name. ``media_selection`` answers an OPEN photo menu
        ("1,3" or "all") and must be omitted when no menu is open.
        ``is_new_idea`` starts a fresh draft over an open one. ``ask_reply`` is
        true when this message answers the bot's ask-back for an idea: a second
        reply with still no idea ends the ask with a clear message. ``is_test`` marks
        a test turn: the idea is stored but hidden from the board, and no
        session state is persisted for the contact.

        Returns ``{status, reply_text, link?, language?, session_vars, offered_media}`` as
        JSON text (``offered_media``: the media menu this reply offered, #1277).
        """
        client = ctx.request_context.lifespan_context["client"]
        spec = _SPECS["crm_ideation_turn"]
        # `respond_io_id` is `str()`-coerced for the same reason the n8n node
        # does it: some webhook shapes deliver the contact id as a number, and
        # the endpoint keys a text column on it.
        body: dict[str, Any] = {
            "respond_io_id": str(respond_io_id),
            "message_text": message_text,
        }
        if session_vars is not None:
            body["session_vars"] = session_vars
        if submitter_name is not None:
            body["submitter_name"] = submitter_name
        if media_selection is not None:
            body["media_selection"] = media_selection
        if is_new_idea is not None:
            body["is_new_idea"] = is_new_idea
        if is_test is not None:
            body["is_test"] = is_test
        if ask_reply is not None:
            body["ask_reply"] = ask_reply
        return await client.request(
            spec.method,
            spec.path,
            body=body,
            tool_name="crm_ideation_turn",
        )

    handlers = {"crm_ideation_turn": crm_ideation_turn}
    for name, fn in handlers.items():
        spec = _SPECS[name]
        mcp.add_tool(fn, name=name, description=spec.description)
        logger.debug("Registered ideation MCP tool %s", name)
