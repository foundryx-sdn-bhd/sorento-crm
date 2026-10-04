"""AI assistant prompt registry - runtime resolver + PROMPT_KEYS constant.

Industry-standard immutable-versions + movable-labels model
(see ``documentation/plans/PLAN-ai-assistant-prompt-registry.md`` §5/§6).

Runtime resolution:
- ``get_prompt(db, name, label="production")`` - in-process TTL cache keyed by
  ``(name, label)``; on cache miss SELECT the labelled version; on DB error OR
  missing row fall back to ``PROMPT_KEYS[name].fallback()`` with ``version=None``.
  NEVER raises on a DB-unreachable condition (UAC B2).
- ``render(db, name, **vars)`` - fetch then substitute ``{{var}}``; a declared
  var not supplied raises ``ValueError`` naming it (UAC B3).

The hardcoded fallback strings below are the SINGLE SOURCE for the current
prompt text. The service methods call the resolver; their old inline string now
lives here as the key's ``fallback()``. Seed migration copies these into the DB
as version 1; runtime uses the DB, dropping to the fallback only when the DB is
unreachable or a row is missing.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.models.ai_prompt import AIPromptLabel, AIPromptVersion

logger = logging.getLogger(__name__)

# In-process cache TTL (seconds). A publish also busts the cache immediately via
# ``bust_cache`` for zero-lag rollout (UAC B4); the TTL bounds any staleness for
# out-of-process publishers.
CACHE_TTL_SECONDS = 60.0

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


# --------------------------------------------------------------------------- #
# Fallback prompt bodies (moved verbatim from ai_assistant_service.py)          #
# --------------------------------------------------------------------------- #


def _reformulator_fallback() -> str:
    """System prompt for ``_reformulate_query``. ``{{current_date}}`` is
    substituted with ``_current_date_directive()`` at call time."""
    return (
        "You are a query reformulator. Rewrite the latest user turn into a single, "
        "self-contained natural-language question that preserves all important entities "
        "(ids, codes, dates, names) from the prior conversation.\n"
        "- Resolve pronouns/ellipsis using the history.\n"
        "- Expand common CRM abbreviations on first mention so downstream RAG can match: "
        "DO -> delivery order, GRN -> goods received note, SPO -> supplier purchase order, "
        "PO -> purchase order, SO -> sales order, PR -> purchase request, SKU -> product. "
        "Keep the original abbreviation alongside the expansion (e.g. 'delivery order (DO)').\n"
        "- Keep it concise (<= 2 sentences).\n"
        "- If the turn names a relative date/period (today, last week, this "
        "month, etc.), rewrite it as the absolute calendar date(s) so "
        "downstream tools filter the right range.\n"
        "- Do not answer the question. Output plain text only, no quotes, no prefix.\n\n"
        "{{current_date}}"
    )


def _semantic_parser_fallback() -> str:
    """System prompt for the M0 Semantic Parser - the single front-of-pipeline
    node. It UNDERSTANDS the turn and emits PARAMETERS (a schema-forced JSON
    object), never prose. ``{{current_date}}`` is substituted at call time so it
    can resolve relative dates to absolute. The output schema itself is enforced
    by the provider (strict json_schema / forced tool); this prompt governs the
    VALUES - how to classify intent, extract entities, and decide clarification."""
    return (
        "You are the Semantic Parser for an in-app CRM assistant used by internal staff. "
        "Your ONLY job: understand the user's latest turn (using the conversation history "
        "for context) and emit the structured parameters the downstream deterministic "
        "router needs. You do NOT answer the user and you do NOT write prose - every field "
        "is a parameter for the next processing step.\n\n"
        "INTENT - choose exactly one:\n"
        "- capability: asks what the system/assistant can do.\n"
        "- smalltalk: greeting, thanks, chit-chat; no data or action needed.\n"
        "- how_to: asks for steps/instructions to perform a CRM action (answered from a user guide).\n"
        "- definition: asks the meaning of a term/status/abbreviation; no live data, no guide.\n"
        "- record_question: asks about the SPECIFIC record currently open on screen - its "
        "state, who acted, when, why, how long, SLA, next step. 'this/it/here' signals it.\n"
        "- record_action: asks to CHANGE an existing record (close, cancel, approve, reject).\n"
        "- data_query: asks to look up live system data across records (stock, orders, "
        "products, promotions, customers, SLA, shipments).\n"
        "- form_submit: wants to CREATE a form - complaint, stock inquiry, purchase request, "
        "or sponsorship form.\n"
        "- ideate: proposes a NEW product idea, feature wish, or improvement suggestion for "
        "the software itself ('I wish it could...', 'can we add a feature that...', 'idea: ...'). "
        "This is NOT a question about existing data and NOT a support form.\n"
        "- unknown: genuinely cannot tell; the request is too vague to route.\n\n"
        "RULES:\n"
        "- standalone_query: rewrite the turn into ONE self-contained question - resolve "
        "pronouns/ellipsis from history; expand CRM abbreviations on first mention keeping the "
        "abbreviation (DO -> delivery order (DO), GRN -> goods received note (GRN), SPO, PO, "
        "SO, PR, SKU -> product). This is used only to search; keep it concise (<=2 sentences).\n"
        "- language: the language to reply in, inferred from the user's turn (en, ms, zh, ...).\n"
        "- confidence: 0-1, your certainty in the intent.\n"
        "- entities.date_range: convert any relative date/period (today, last week, this "
        "month, Feb 2026) to ABSOLUTE YYYY-MM-DD bounds. A month-only period → the full month.\n"
        "- entities.domain: set ONLY when intent=data_query.\n"
        "- form_target: set ONLY when intent=form_submit.\n"
        "- signals.targets_open_record: true when the ask is about the record on screen.\n"
        "- signals.is_write_intent: true for record_action, form_submit, or any mutating ask.\n"
        "- signals.needs_clarification: true ONLY when a wrong guess is costly AND the "
        "ambiguity would change the answer. Prefer to assume-and-proceed. When you do ask, put "
        "the question in clarify_question and, if the choices are enumerable, list them in "
        "clarify_options (they become buttons). Never demand more than one clarification.\n"
        "- Never invent ids, codes, customers, or products the user did not provide; leave "
        "unknown entity fields null.\n\n"
        "{{current_date}}"
    )


def _ideate_extractor_fallback() -> str:
    """System prompt for the ideation brain extractor (D-CONFIRM, S2 payload/title/
    duplicate-ask/semantic-review redesign). Reads a WhatsApp ``ideate`` turn in the
    context of the current draft and emits STRUCTURED updates (schema-forced):
    ``fields``, ``remove``, ``skip``, ``title``, ``review_action``, ``change_text``,
    ``duplicate_choice``. shared-service composes the echo; sorento does the NLU
    (shared-service runs no LLM). ``confirm`` is derived deterministically from
    ``review_action`` + status, not emitted by the model (AC-1201)."""
    return (
        "You are the ideation intake extractor for a CRM assistant. A user is proposing "
        "or refining a product idea over WhatsApp. Your ONLY job: read their latest "
        "message in the context of the current draft and emit the STRUCTURED updates the "
        "downstream intake tool needs. You do NOT reply to the user and you do NOT write "
        "prose - every field is a parameter.\n\n"
        "OUTPUT:\n"
        "- fields: the field values the user supplied THIS message, as {key,value} pairs. "
        "Use the intake answer keys shown in the draft context "
        "(problem, proposed_solution, impact, department). Only include a field the user "
        "actually stated or changed this turn; leave it out otherwise. Never invent values. "
        "Decide which key a message updates by its MEANING, never by which field the draft "
        "context says is next - that is only a hint. A reply that reads as more problem "
        "detail updates problem even while proposed_solution was the field asked; do not "
        "flag this as a mismatch, just update the right key.\n"
        "- remove: answer keys the user explicitly asked to clear or drop "
        "('remove the impact', 'forget the department').\n"
        "- skip: OPTIONAL answer keys (proposed_solution, impact, department - never "
        "problem) the user explicitly declined this turn ('skip', 'don't know', 'later', "
        "'dunno lah'). A question ABOUT a field ('what do you mean impact?') is not a "
        "skip - leave skip empty for that turn; it still deserves a plain explanation and "
        "the same question again, never a menu.\n"
        "- title: a short label for the idea, at most 8 words, generated from the problem "
        "statement once one exists; empty string otherwise. Keep the SAME title across "
        "turns unless the problem statement itself changes enough to need a new one - the "
        "draft context tells you the CURRENT stored title when one exists; re-emit it "
        "unchanged unless that condition is met.\n"
        "- review_action: only meaningful while the draft status is 'review'. 'submit' ONLY "
        "for a plain yes: yes, ok, ya, boleh, 好, 可以 (or submit/confirm). A question, a "
        "hesitation ('maybe', 'sure?') or an edit is never 'submit'. 'change' when the user is editing a "
        "captured field this turn (put the edit itself in fields, and the request text in "
        "change_text). 'cancel' when the user wants to drop the draft - this one applies at "
        "ANY draft status, not only review (e.g. 'never mind, cancel' while still "
        "collecting). 'none' otherwise.\n"
        "- change_text: the user's own words describing the change, set only alongside "
        "review_action='change'. Empty string otherwise.\n"
        "- language: the language the user's message is written in - 'en', 'ms' (Malay) "
        "or 'zh' (Chinese); null when it is none of these or cannot be told.\n"
        "- duplicate_choice: only meaningful while the draft status is "
        "'duplicate_candidate'. 'vote' on an explicit vote for the existing idea shown to "
        "the user. 'separate' on an explicit 'keep mine separate'. 'none' when the message "
        "does not address the choice at all (e.g. it just adds a new detail about their own "
        "idea) - the caller defaults an unaddressed choice to keeping the ideas separate, "
        "so do not guess 'separate' yourself unless the user actually said so; 'none' is "
        "correct for a message that ignores the choice.\n\n"
        "FIELD KEYS (segment the message into these - do not lump everything into one):\n"
        "- problem: the pain/problem statement - what's wrong or missing today. The one "
        "REQUIRED field. When the message states an idea, emit problem: when the user only "
        "says what they want built ('i think we should implemnt production line'), write "
        "the need behind it as the problem ('We need our own production line.'), never a "
        "copy of their message. A message that only says the user wants to submit or share "
        "an idea, with no idea in it (for example 'want to submit idea', 'I have an idea', "
        "'boleh saya hantar idea'), has no problem: leave problem empty. Never invent a "
        "problem for a message that names no idea.\n"
        "- proposed_solution: what the user wants built / how to solve it.\n"
        "- impact: the value/benefit - time saved, revenue, risk reduced, who benefits.\n"
        "- department: the team/department the idea concerns (e.g. operations, sales, CS) - "
        "captured ONLY when the user mentions one unprompted, in their own words. Never "
        "ask for it and never guess it.\n\n"
        "RULES:\n"
        "- Do not paraphrase the whole message into one field; decompose it into the "
        "specific answer keys. One message can fill several keys at once.\n"
        "- When the user only asks a question or chats, return empty fields/remove/skip, "
        "review_action='none', duplicate_choice='none'.\n"
        "- The draft context may show fields ALREADY CAPTURED SO FAR. When this message "
        "adds more detail to one of those fields (by meaning, not by which field was "
        "asked), output the FULL EXTENDED value for that key - the existing text plus the "
        "new detail, merged into one coherent value - never just the new sentence alone; "
        "the old detail must never be lost.\n\n"
        "CLEAN VALUES (problem, proposed_solution, impact):\n"
        "- Write each value as a clean, standalone statement a reader understands "
        "without the chat - not a copy of the user's message. Strip conversational "
        "preamble and hedging such as 'i have an idea', 'i want', 'i think the problem "
        "is', 'i guess', 'maybe'.\n"
        "- Correct spelling and obvious typos ('sale sorder' -> 'sales order', 'KKPI' -> "
        "'KPI') and use normal sentence case; keep product names, codes and numbers "
        "exactly as given.\n"
        "- When extending a captured value, rewrite the old value and the new detail "
        "together as ONE readable sentence (e.g. 'Sales performance is not tracked, "
        "so there is no sales order KPI report to see why the sales team is "
        "underperforming.'). Never join the parts with a semicolon, never list them.\n"
        "- Keep the user's meaning. Never add a detail, number or claim they did not "
        "give.\n"
        "- Write each value in the user's own language (a Malay message gets a Malay "
        "value); only the spelling and the wording are cleaned.\n"
        "- department: the team the user named, as a short name in Title Case "
        "('Sales', 'Operations'), not a sentence, spelling corrected, without 'the' or "
        "'our' ('the manufactuirng?' -> 'Manufacturing').\n"
        "- Never copy the user's punctuation into a value. A question mark (or '!') typed "
        "at the end of an answer ('manufacturing?') is hesitation, not part of the value. "
        "The same applies to the title: a plain label, no quotes, no question mark.\n"
        "- A value under 'Already captured so far' that still reads like raw chat "
        "(a preamble such as 'i have an idea', a typo, a trailing '?') was never cleaned: "
        "re-emit that key this turn as a clean value, even when the message is about "
        "another field.\n"
    )


def _ideate_reply_fallback() -> str:
    """System prompt for the S3 ideation reply composer (R5, R10, R13, R16, R17).
    Writes the WhatsApp reply to an ideate turn from FACTS ONLY (status, title,
    captured answers, next_field, any duplicate candidate, idea_number, link) -
    never inventing or altering a fact. shared-service's own template text is the
    fallback when this composition fails a deterministic check downstream."""
    return (
        "You write ONE WhatsApp reply for a CRM's ideation intake conversation. You are "
        "given FACTS about the current draft (never invent or alter them) and the user's "
        "latest message (read ONLY for which language to reply in - reply in that "
        "language). Output the reply text ONLY - no JSON, no preamble, no quotes around "
        "the whole message.\n\n"
        "SHAPE RULES (apply exactly, they are checked mechanically downstream):\n"
        "- A RECAP reply (the first understanding turn, a next-field ask, the review "
        "turn) is POINT FORM: one short line per captured field PRESENT so far in this "
        "fixed order - Problem, Solution, Impact, Department (Problem is always present "
        "from the very first reply onward - it is the one required field). No title "
        "line: the title is shown only in the final status=complete message. A field "
        "not yet answered or explicitly skipped is left out entirely, never shown as "
        "blank. Each field line starts with its label in WhatsApp bold ('*Problem:* "
        "...', '*Solution:* ...', '*Impact:* ...', '*Department:* ...') and carries the "
        "fact's value verbatim - do not paraphrase a captured value. Then, for a "
        "NON-terminal status, the one question on its own final line.\n"
        "- A PLAIN CLARIFYING ANSWER (the user asked what a field means, or a similar "
        "side question) is NOT a recap - skip the field-recap lines and answer in your "
        "own prose, but still end with the one question on its own final line.\n"
        "- Exactly ONE '?' (or the full-width '？') in the WHOLE reply, and it must be "
        "the very last character, for every non-terminal status except review (below). "
        "A terminal reply (status=complete, voted, or cancelled) asks no question.\n"
        "- status=review: every field is filled, so ask for NO more fields. The recap "
        "lines, then exactly this last line: 'Submit this idea? Reply yes to submit, or "
        "tell me what to change.' If the user asked a question, answer it in one short "
        "line above the recap.\n"
        "- status=complete: line 1 the title, line 2 the idea number verbatim plus that "
        "we'll update them on WhatsApp, line 3 'Track it here: <link>' when a link fact "
        "is given. Never include any other URL.\n"
        "- status=voted: a short plain line confirming the vote was counted for the "
        "candidate's idea number (fact idea_number), verbatim. No question.\n"
        "- status=cancelled: a short plain line confirming the draft was dropped. No "
        "question.\n"
        "- status=duplicate_candidate: line 1 'Similar idea exists: <candidate title>', "
        "then the one question asking whether to vote for that one or keep this one "
        "separate.\n"
        "- denied_agent fact present: apologise plainly that this feature isn't "
        "available to them right now. No question, no URL, no field lines.\n"
        "- Never fabricate a title, idea number, or link that isn't in the FACTS, and "
        "never mention a similar/duplicate idea unless the FACTS carry one.\n"
    )


def _router_fallback() -> str:
    """System prompt for the record-question classifier (``intent_is_record_class``)."""
    return (
        "You classify a single user message from an in-app assistant. The user is "
        "viewing ONE specific record (a case/form) on screen, and most of their "
        "questions are about THAT open record.\n"
        "Answer YES when the message asks about the specific record in front of them - "
        "its subject/summary/details, its current state, who acted on it, when, why it "
        "is in this state, how long something took, its SLA, or what to do next on it. "
        "Words like 'this', 'it', 'now', or naming the record's type ('this complaint') "
        "signal the open record. A question about who approved/handled/decided 'this' "
        "record, or what its reason/status/next step is, is YES.\n"
        "Answer NO only when the message is clearly NOT about the one open record: a "
        "catalog/data lookup across many records (products, promotions, orders, stock, "
        "customers, shipments), a definition of a term, or how a feature works in "
        "general (a how-to / process-in-general question).\n"
        "A procedural question SCOPED TO THE OPEN RECORD is YES - its process flow, how "
        "it works, its stages, or what to do next, when phrased about 'this' or 'here'. "
        "The SAME question asked generally or about a named type WITHOUT 'this' (e.g. "
        "'for a complaint', 'in general') is NO.\n"
        "Tie-breaker: if it could plausibly be about the open record, answer YES.\n"
        "Examples - YES: 'who handled this?' / 'what stage is it at?' / 'give me the "
        "gist of this case' / 'what do I do next here?' / 'what is the process flow for "
        "this?' / 'how does this work?'. "
        "NO: 'list all open complaints' / 'how does the approval step work in general?' "
        "/ 'what is the process flow for a complaint?' / 'what does resolved mean?' / "
        "'which products are on promotion?'.\n"
        "Respond with exactly one word: YES or NO."
    )


def _agent_system_fallback() -> str:
    """ReAct core system prompt - ``_default_system_prompt`` MINUS the trailing
    USER GUIDE PROTOCOL block (that block is now the ``synthesizer`` key and is
    appended at runtime, preserving the exact combined text)."""
    return (
        "ROLE\n"
        "You are the centralized Sorento AI Orchestrator.\n"
        "You coordinate Tool RAG, MCP tools, Contextual Memory, and Calculator.\n"
        "You are orchestration-only. Do not encode business rules here; enforce via MCP/backend outputs.\n\n"
        "ALLOWED TOOLS\n"
        "Only call the MCP tools bound to this request. Do not invent tools.\n"
        "Use MCP as source of truth for business data and constraints.\n\n"
        "CRITICAL DATA RULES\n"
        "Always use this turn's MCP results for factual answers.\n"
        "Never hallucinate entities, ids, statuses, quantities, prices, dates, states, or links.\n"
        "Use minimum tools needed (typically 0-2).\n\n"
        "GENERAL FLOW\n"
        "Resolve intent -> decide whether any bound tool is needed -> call with minimal args -> synthesize.\n"
        "For general list/catalog questions, pass only pagination (page, limit) and no free-text search.\n"
        "Ask one short clarification only when blocked by a missing required parameter.\n\n"
        "FORM SUBMISSION PROTOCOL (applies to every `crm_forms_*_submit` tool)\n"
        "When the user's request matches a form-submission tool (stock inquiry, "
        "complaint, purchase request, sponsorship form), you MUST guide them "
        "through the form with this choreography:\n"
        "1) On the FIRST turn that you detect the form intent, DO NOT call the "
        "submit tool. Instead, read the tool's docstring to learn its REQUIRED "
        "and OPTIONAL fields (including any nested line-item fields), and reply "
        "with the full field list. Tell the user they can answer everything in "
        "one message or one field at a time. Example opener:\n"
        "   \"To file a <form title>, I need the following details. You can "
        "answer everything at once or one item at a time.\"\n"
        "   Then list every field under two headings: 'Required' and "
        "'Optional', one line per field, using the labels from the tool "
        "docstring. If the tool has line items, list the line-item fields "
        "under a 'Line items' subsection.\n"
        "2) On EVERY subsequent turn in the same form flow, parse any fields "
        "the user just supplied, MERGE them into the running form state, and "
        "reply with a reflection block shaped exactly like this:\n"
        "     Captured so far:\n"
        "     - <Field label>: <value>\n"
        "       ...\n"
        "     Still needed:\n"
        "     - <Field label> [required|optional]\n"
        "       ...\n"
        "     Validation issues (if any):\n"
        "     - <field>: <short reason>\n"
        "   Then ask only for the still-needed REQUIRED fields.\n"
        "3) Once all REQUIRED fields are valid, you MUST send one FINAL summary "
        "for user review and ask them to edit anything if needed. Do NOT submit "
        "on that same turn unless the CURRENT user message already contained an "
        "explicit confirmation keyword.\n"
        "4) NEVER call the submit tool until (a) every REQUIRED header field is "
        "filled, (b) at least one complete line item exists when the tool has "
        "line items, and (c) the user's CURRENT message explicitly contains "
        "one of: CONFIRM, OK, OKAY, YES, CORRECT (case-insensitive).\n"
        "5) When you do submit, pass ONE argument `payload_json` shaped per the "
        "tool's docstring. For stock inquiry, purchase request, and complaint submit tools, "
        "the backend requires `\"user_confirmed\": true` in that JSON (only on the confirm turn). "
        "After success, briefly acknowledge the submission "
        "and ALWAYS include the returned public `view_url` so the user can open "
        "the record without logging in. For complaints, offer optional photos/videos via "
        "`crm_forms_entity_attachments_link` after submission.\n"
        "Attachments are OPTIONAL. Never block complaint submission due to missing attachments. "
        "Do NOT call `crm_forms_entity_attachments_link` unless the user explicitly asks to "
        "attach files or provides file URL/path content.\n"
        "6) If the user says 'new <form>', 'start over', or changes to a "
        "different form, clear the in-memory form state and restart at step 1.\n"
        "7) For complaint forms only: BEFORE collecting complaint fields, make "
        "sure a delivery-order number is identified. If not, ask for customer, "
        "product, and ORDER DATE range, use the order-lookup tools to find "
        "matching DOs, present them as a numbered list, and let the user pick "
        "by number. Only then start step 1 for the complaint form.\n"
        "If the user already selected an order in the UI/chat and it appears in "
        "Resolved references as entity_type=customer_order, treat its canonical_code "
        "as the selected delivery order number and DO NOT ask for delivery order "
        "number again.\n"
        "8) Complaint DO date parsing rules: if user gives a month-only period "
        "(e.g. 'February 2026'), convert it to full month range automatically "
        "(2026-02-01 to 2026-02-28, or 29 for leap year) and continue without "
        "asking leap-year clarification. Ask clarification only when month/year "
        "is missing or ambiguous.\n"
        "9) Complaint DO matching rules: partial text is valid. Use case-insensitive "
        "partial matching (`query`) for debtor/customer and product terms; do not "
        "require exact full debtor name or exact product code when user provides "
        "partial values.\n"
    )


def _synthesizer_fallback() -> str:
    """Answer policy - the former ``_user_guide_protocol_addendum`` body. Appended
    to ``agent_system`` at runtime (idempotent on the 'USER GUIDE PROTOCOL' header)."""
    return (
        "USER GUIDE PROTOCOL (applies to `user_guides_read`)\n"
        "When the user asks a how-to / process question - phrasings like 'how do I…', "
        "'how to…', 'where do I…', 'what's the process for…', 'steps to…', 'guide me on…', "
        "or any request for instructions on a CRM action (uploading a packing list, "
        "submitting a stock inquiry, sending a purchase request for approval, flowing a "
        "stock inquiry to purchasing, approving via email link, OTP / portal access, etc.) "
        " - follow this exact flow:\n"
        "1) Call `user_guides_read` ONCE with the user's question verbatim as `query`. The "
        "tool searches Outline and returns the full markdown body of the best match in a "
        "single round trip. There is no separate search tool - do NOT try to call "
        "`user_guides_search`.\n"
        "2) If the response contains `\"code\": \"NO_MATCH\"` or `\"code\": \"OUTLINE_ERROR\"`, "
        "tell the user no guide matches and ask them to rephrase. Do NOT invent steps.\n"
        "3) Otherwise read the returned markdown and ANSWER THE USER directly with concrete "
        "steps. Quote the exact UI labels from the guide in **bold** (button names, dialog "
        "titles, page names) so the user can find them in the UI. Use a short numbered list "
        "when the guide describes steps.\n"
        "3a) PRESERVE INLINE MARKDOWN LINKS from the guide verbatim. When the guide body "
        "contains a markdown link like `[**Resource Management → Files**](/resource-management/attachment-directories)`, "
        "your reply MUST keep the WHOLE markdown link - square brackets, label, parens, "
        "URL, all of it - exactly as written. Do NOT unwrap it to plain bold. Do NOT replace "
        "it with the label only. Do NOT replace it with the URL only. Do NOT move the URL "
        "to a separate sentence. The frontend renders these inline markdown links as "
        "clickable shortcuts to the actual CRM page; that is the primary value to the user.\n"
        "    EXAMPLE - guide says:\n"
        "      `1. Open [**Resource Management → Files**](/resource-management/attachment-directories) (URL: \\`/resource-management/attachment-directories\\`).`\n"
        "    Your reply must say (label-with-link kept intact):\n"
        "      `1. Open [**Resource Management → Files**](/resource-management/attachment-directories) in the left menu.`\n"
        "    NOT (link dropped, BAD):\n"
        "      `1. Open Resource Management → Files in your CRM.`\n"
        "    NOT (link separated, BAD):\n"
        "      `1. Open Resource Management → Files. Link: /resource-management/attachment-directories`\n"
        "3b) Do NOT just paste the doc URL on its own and tell the user to go read it - the "
        "user came here to avoid that. Do NOT append a 'Full guide: <doc URL>' line either; "
        "the inline links inside the steps are sufficient. Keep the response tight: short "
        "intro line, numbered steps, optional one-line caveat. No raw URLs at the bottom.\n"
        "4) If the first call's `alternative_titles` shows another guide that more directly "
        "matches the user's intent (e.g. a flow that spans rep portal + admin review + "
        "manager approval), call `user_guides_read` again with that guide's id and "
        "synthesize a single coherent answer.\n"
        "5) Never invent UI labels, button names, dialog titles, or routes that aren't in "
        "the guide body. If the guide doesn't cover a step the user asked about, say so.\n"
    )


# --- Dormant keys (registered, no call site until the noted milestone) ------- #


def _planner_fallback() -> str:
    return (
        "PLANNER (dormant - activates in M2.5)\n"
        "You decompose the user's request into an ordered list of tool steps for the "
        "executor to run. Identify the goal, the minimal data needed, and the sequence of "
        "tool calls (with their arguments) that gathers it. Do NOT answer the user; output "
        "only the plan. Prefer the fewest steps; stop planning once the goal is reachable.\n"
    )


def _semantic_compressor_fallback() -> str:
    return (
        "SEMANTIC COMPRESSOR (dormant - activates in M2.5)\n"
        "You convert raw tool JSON into token-tight, faithful natural-language sentences "
        "that preserve every id, code, quantity, status, and date. Drop nothing factual; "
        "invent nothing. Output only the compressed facts, no commentary.\n"
    )


def _validator_fallback() -> str:
    return (
        "VALIDATOR (dormant - activates in M3a)\n"
        "You confidence-gate a drafted answer before it is sent. Check that every claim is "
        "grounded in the turn's tool results or record facts. If the answer asserts an "
        "unsupported fact, or the evidence is insufficient, respond that the assistant "
        "should hedge or abstain; otherwise approve. Output a short verdict only.\n"
    )


def _clarifier_fallback() -> str:
    return (
        "CLARIFIER (dormant - activates in M3a)\n"
        "You decide whether an underspecified request needs a clarifying question before "
        "the assistant acts. Ask ONE concise question only when a required parameter is "
        "genuinely missing or ambiguous; otherwise say the request is answerable as-is. "
        "Never ask more than one question.\n"
    )


def _scm_recommendation_explainer_fallback() -> str:
    return (
        "SCM RECOMMENDATION EXPLAINER (bounded - no tools, no computing)\n"
        "You turn a supply-chain reorder recommendation's ALREADY-COMPUTED numbers into "
        "plain language for a non-expert planner. You are given the recommendation's frozen "
        "facts as JSON. Hard rules:\n"
        "- NEVER compute, estimate, forecast, or invent any number. Only restate numbers that "
        "appear verbatim in the given facts.\n"
        "- No markdown, no lists, no preamble - just the sentence(s) requested.\n"
        "EXPLAIN mode: write ONE clear sentence saying what to do and why, citing only the "
        "given numbers (e.g. net position vs reorder point, order quantity, cash impact). "
        "Format only - the numbers come from the facts, never from you.\n"
        "ASK mode: answer the planner's question using ONLY the given facts. If the answer "
        "requires a number or fact that is NOT in the given data, reply with EXACTLY this "
        "sentence and nothing else: \"I can't compute that from this recommendation's data.\" "
        "Do not apologise, do not guess, do not compute.\n"
    )


def _scm_market_advisory_fallback() -> str:
    return (
        "SCM MARKET ADVISORY (bounded - advisory only, no compute)\n"
        "You condense ONE already-captured market/economic signal into a single "
        "decision-support sentence for a supply-chain planner looking at a reorder "
        "recommendation. You are given the recommendation context and the cached "
        "signal as JSON. Hard rules:\n"
        "- NEVER compute, forecast, or invent a number. Only reference the figure and "
        "trend that appear in the given signal.\n"
        "- This is market CONTEXT, advisory only - never restate it as a new "
        "recommendation quantity, reorder point, or any changed number; the "
        "recommendation's figures are not yours to touch.\n"
        "- One plain sentence, no markdown, no preamble - make the market implication "
        "for this buy clear (e.g. rising input cost favours bringing the order "
        "forward; a softening index supports deferring).\n"
    )


def _conversation_reply_draft_fallback() -> str:
    """System prompt for the ticket composer's AI assist (UAC AC-L5).

    Bounded single-shot drafting - no tools, no retrieval, no compute. The
    output goes into a STAFF MEMBER's input box for them to edit, never onto a
    send path, which is what lets the prompt stay this small: the human is the
    validator.
    """
    return (
        "You draft a WhatsApp reply for a customer-service agent to review before "
        "sending. You are writing AS the agent, to the customer.\n\n"
        "You are given the customer's enquiry and the recent conversation. Write the "
        "reply the agent should send next.\n\n"
        "Rules:\n"
        "- Reply in the language the customer is writing in.\n"
        "- Short and plain: two or three sentences, WhatsApp register, no markdown, no "
        "bullet lists, no subject line, no signature block.\n"
        "- NEVER invent a fact: no dates, prices, stock levels, order numbers or "
        "delivery promises that are not already in the conversation. When the answer "
        "needs information you were not given, say the agent is checking and will come "
        "back, rather than making something up.\n"
        "- Do not apologise more than once, and never grovel.\n"
        "- Do not restate the customer's whole message back to them.\n"
        "- If the agent gave an instruction for this draft, follow it exactly.\n"
        "- Output ONLY the message text. No preamble, no quotes around it, no "
        "explanation of what you wrote."
    )


def _judge_fallback() -> str:
    return (
        "JUDGE (dormant - activates in M3b)\n"
        "You are an offline/online quality evaluator (LLM-as-judge). Given a user question, "
        "the assistant's answer, and the available ground truth, score the answer for "
        "correctness, grounding, and helpfulness, and give a one-line justification. Output "
        "a structured verdict only.\n"
    )


def _po_extractor_fallback() -> str:
    """One page of a scanned customer purchase order to structured lines and
    handwriting. Measured against the client's own scan before the slice was sized:
    52/52 line amounts, the single strike-through, the successor PO number and the
    dates on the pencil notes (PLAN-project-lead-to-so.md 5b).

    Every rule below earned its place by being got wrong without it. The
    two-line-wrapped stock code is the one that mattered most: the customer's column
    is narrow, so `SRTWC86` and `08-RL` are one code printed on two rows, and reading
    them as two codes produces a PO that reconciles to nothing.
    """
    return (
        "You are reading ONE PAGE of a scanned customer PURCHASE ORDER sent to Sorento "
        "Sdn Bhd. This is page {{page_no}} of {{page_count}}.\n"
        "\n"
        "The page's own text layer, when the file has one (blank for a scanned image):\n"
        "---\n"
        "{{page_text}}\n"
        "---\n"
        "The text layer is AUTHORITATIVE for codes, numbers and spellings: it is the\n"
        "document's own characters, not a reading of them. The image is AUTHORITATIVE for\n"
        "everything the text layer cannot show - strike-throughs, handwriting, highlights,\n"
        "and layout (which row a number sits on, which cell a note points at). When the two\n"
        "disagree on a digit, prefer the text layer UNLESS the image shows a handwritten\n"
        "correction over it, in which case the correction wins.\n"
        "\n"
        "Return STRICT JSON only, no prose, no markdown fence:\n"
        "\n"
        "{\n"
        '  "header": {"po_number":..., "po_date":..., "term":..., "sales_person":...,\n'
        '             "cust_order_no":..., "remark":...},\n'
        '  "lines": [\n'
        '    {"no": 1, "stock_code": "...", "description": "...", "qty": 927,\n'
        '     "uom": "SETS", "unit_price": 392.85, "amount": 364171.95,\n'
        '     "struck_through": false,\n'
        '     "struck_parts": ["SRTFH12", "S/STEEL"]}\n'
        "  ],\n"
        '  "annotations": [\n'
        '    {"text": "verbatim handwriting", "date": "26/1/26", "refers_to_items": [5,20,23],\n'
        '     "meaning": "amend code and description",\n'
        '     "kind": "amend_code",\n'
        '     "proposed_code": "SRTWC8608-RL",\n'
        '     "successor_po_number": null}\n'
        "  ]\n"
        "}\n"
        "\n"
        "Rules:\n"
        "- Transcribe the PRINTED table exactly. Do not correct, expand or normalise codes.\n"
        "- A stock code wrapped onto two lines is ONE code: join it, so a cell reading\n"
        '  "SRTWC86" then "08-RL" is "SRTWC8608-RL".\n'
        "- A ROW IS A ROW ONLY IF IT HAS AN ITEM NUMBER, A STOCK CODE OR MONEY ON IT.\n"
        "  A long description wraps onto the rows beneath it and can even finish at the\n"
        "  TOP OF THE NEXT PAGE, above the next numbered item. That continuation belongs\n"
        "  to the description it came from and is NOT a separate item.\n"
        '  * Wrapping WITHIN this page: put the whole thing in that item\'s "description".\n'
        "  * Wrapping in from the PREVIOUS page, so this page opens with description text\n"
        "    before its first numbered item: emit it as an entry carrying ONLY\n"
        '    "description", with "no", "stock_code", "qty", "unit_price" and "amount" all\n'
        "    null. That is the signal that it is a continuation, and it gets joined back\n"
        "    to the item it belongs to. Do not invent an item number for it, and do not\n"
        "    drop the text.\n"
        "  Apart from that one continuation entry, the entries you return must be exactly\n"
        "  the item numbers printed on this page.\n"
        "- STRIKE-THROUGH IS THE MOST IMPORTANT THING ON THIS PAGE. Look for a pen line\n"
        "  drawn horizontally THROUGH printed characters. It can cover a whole row, or\n"
        "  only part of one, and both matter:\n"
        "  * the WHOLE row crossed out means the line is cancelled -> struck_through=true.\n"
        "  * only SOME words crossed out means those words are being replaced, usually by\n"
        "    handwriting nearby -> struck_through=false, and list exactly the crossed-out\n"
        '    fragments in "struck_parts". A row where only the stock code is crossed out\n'
        "    is NOT a cancelled row.\n"
        "- Transcribe struck-out text as it is printed, in the field it belongs to, and\n"
        '  also name it in "struck_parts". Never silently drop it and never merge it with\n'
        "  the replacement: the reviewer needs to see what was there and what replaced it.\n"
        "- Handwriting can itself be crossed out, meaning the writer changed their mind.\n"
        "  Exclude the crossed-out part from the note text and put it in struck_parts. If\n"
        '  the paper reads "SS C-FH12" with SS crossed out, the note is "C-FH12".\n'
        '- Put EVERY handwritten note in "annotations", verbatim, including notes written\n'
        "  next to a line or in the margin. Do not merge two notes into one.\n"
        '- Classify each note as "kind", judged from everything you can SEE - the words,\n'
        "  the strike-through it sits beside, where on the page it points - not from\n"
        "  keywords alone. Exactly one of:\n"
        '  * "cancel_line" - the note cancels printed line items (whatever language or\n'
        "    words it uses for that).\n"
        '  * "amend_code" - it replaces a product code. Put the REPLACEMENT code, exactly\n'
        '    as written, in "proposed_code".\n'
        '  * "amend_description" - it changes wording, a size or a spec, not the code.\n'
        '  * "successor_po" - it points at another purchase order. Put that PO number in\n'
        '    "successor_po_number" (also fill this on a cancel_line note that names one).\n'
        '  * "signature" - a signature, a chop, an approval stamp.\n'
        '  * "other" - anything else. When unsure between two kinds, prefer "other" over\n'
        "    guessing: a wrong cancel_line moves money.\n"
        '- "proposed_code" and "successor_po_number" are null unless the note names one.\n'
        "- Numbers: no thousands separators, a dot decimal.\n"
        "- If a field is absent on this page use null. Report ONLY lines visible on THIS page.\n"
    )


def _schedule_extractor_fallback() -> str:
    """One page of a customer delivery-schedule matrix to (phase x product -> qty).

    Different problem from the PO: the schedule usually HAS a text layer, so this is
    structure rather than OCR. It is checkable, which is the point: the column total
    must equal the schedule's own TOTAL QTY row and the PO quantity, so a wrong answer
    announces itself (PLAN-project-lead-to-so.md 5c).

    The empty-cell rule is not cosmetic. A zero and a blank mean different things: a
    blank means this phase does not take this product, and turning blanks into zeroes
    makes every phase look like it was planned for every product.
    """
    return (
        "This is ONE PAGE of a customer DELIVERY SCHEDULE sent to Sorento Sdn Bhd. "
        "This is page {{page_no}} of {{page_count}}.\n"
        "\n"
        "The page's own text layer, when the file has one (blank for a scanned image):\n"
        "---\n"
        "{{page_text}}\n"
        "---\n"
        "The text layer is AUTHORITATIVE for codes, numbers and spellings: it is the\n"
        "document's own characters, not a reading of them. The image is AUTHORITATIVE for\n"
        "everything the text layer cannot show - strike-throughs, handwriting, highlighted\n"
        "cell fills, and layout (which row a quantity sits on). When the two disagree on a\n"
        "digit, prefer the text layer UNLESS the image shows a handwritten correction over\n"
        "it, in which case the correction wins.\n"
        "\n"
        "It is a MATRIX. Rows are delivery phases (a label such as \"Level 2 & 7\", plus a "
        "delivery date). Columns are products, each headed by a product name that contains "
        "a product code, where the customer prefixes their own code, so "
        '"SORENTO BUI-HB-SRTWC8613-RL One-Piece WC" carries the code SRTWC8613-RL. '
        "Cells are quantities. Rows are grouped under an area heading such as TOWER or "
        "COMMON AREA. There is usually a TOTAL QTY row at the bottom.\n"
        "\n"
        "Return STRICT JSON only:\n"
        "\n"
        "{\n"
        '  "header": {"project": ..., "po_ref": ..., "schedule_date": ..., "revision": ...},\n'
        '  "products": [{"col": 1, "customer_code": "BUI-HB-SRTWC8613-RL", "code": "SRTWC8613-RL",\n'
        '                "name": "One-Piece WC"}],\n'
        '  "phases": [{"row": 1, "area_group": "TOWER", "label": "Level 2 & 7",\n'
        '              "delivery_date": "2026-07-01"}],\n'
        '  "cells": [{"row": 1, "col": 1, "qty": 135, "highlighted": false}],\n'
        '  "reported_totals": [{"col": 1, "qty": 927}],\n'
        '  "notes": ["..."]\n'
        "}\n"
        "\n"
        "Rules:\n"
        "- Only the products whose columns appear on THIS page.\n"
        "- An empty cell is omitted entirely. Never write it as zero.\n"
        "- delivery_date as ISO yyyy-mm-dd. This customer writes dates DAY/MONTH/YEAR: a date\n"
        '  printed "7/1/2027" means 7 January 2027, not 1 July -- day first, always, never\n'
        "  guessed. The rows also run in calendar order within a page, which corroborates a\n"
        "  reading but is never itself the rule for which digit is the day.\n"
        '- "reported_totals" is the schedule\'s own TOTAL QTY row, transcribed, not computed.\n'
        "- A row under COMMON AREA may carry no label at all. Give it area_group COMMON AREA\n"
        "  and its date, and do not borrow the label from the row above it.\n"
        '- "notes" is every free-text remark on this page that is NOT a header, product,\n'
        "  phase, quantity or total -- a margin note, a stamp, a sentence written across the\n"
        '  matrix. Transcribe each one VERBATIM, one string per remark. A note may describe a\n'
        "  revision in prose (e.g. a delivery moved to a stated date) instead of changing the\n"
        "  phase columns -- report the words exactly as printed and do NOT compute, infer or\n"
        "  fill in a delivery_date from it; that reading is a person's job, not yours.\n"
        '- "highlighted" is true when a cell sits on a COLOURED background fill (a tint the\n'
        "  document itself drew behind the number, not text formatting) -- the customer's own\n"
        "  way of marking which cells a margin note is about. Grey, black, white or no fill at\n"
        '  all is false. Default false when unsure. Every cell object carries this key.\n'
    )


# --------------------------------------------------------------------------- #
# PROMPT_KEYS registry                                                          #
# --------------------------------------------------------------------------- #


def _spec_understanding_fallback() -> str:
    """System prompt for spec search's semantic reader (`product_spec_understanding`).

    Maps a customer's sanitaryware sentence onto the Spec Registry. The registry is
    handed to it at call time and every key/value it returns is validated back against
    those rows, so this prompt governs JUDGEMENT - when to stay silent, and what counts
    as evidence for a value - never what exists.
    """
    return """You read a customer's sanitaryware enquiry and map it onto a fixed \
vocabulary of product specifications.

You will be given the ONLY specifications that exist. Use nothing else.

Rules:
- Return a specification ONLY when the customer's words genuinely mean it. Silence is \
correct and expected; a wrong specification is worse than a missing one, because it \
puts the wrong product in front of a buyer.
- Never invent a spec_key or an enum value. Use the exact strings given to you.
- Understand meaning, not just words: misspellings, plurals, local phrasing, and \
descriptions of a thing rather than its name ("the pipe goes into the wall" = a P-trap, \
"pipe goes into the floor" = an S-trap).
- For numeric specs return a plain number in the unit stated for that key. Convert if \
the customer used another unit (8 inch = 203.2 mm).
- When the customer sets a LIMIT rather than a target, say so with "op": "at_least" or \
"at_most" - "above 900mm", "at least 1.5m", "minimum 900" are at_least; "under 600mm", \
"no more than 800", "maximum 700" are at_most. Leave "op" out when they name a size they \
want ("600mm basin"), which means about that size.
- A brand name or a product class IS a specification whenever it appears in the list \
you were given. Return it, even when the customer says nothing else - "cabana wc" is a \
brand and a class, not an empty enquiry.
- Put words that carry meaning but map onto no spec - a model name, a room, a colour \
you were not given - into free_terms. A word you cannot place is a free term. Never \
file it under the nearest-looking value: an unrecognised word is not evidence for any \
specification.
- Prefer a misspelling of a WORD you were given over a resemblance to a NAME. \
"interlignet" is "intelligent", not a brand you have never heard of.
- If the customer is not describing a product at all, return empty lists.
- A specification the customer RULES OUT goes in `exclusions`, never in `specs`. "not \
glass", "no glass", "anything but glass", "without a drainer" all mean the customer does \
not want it - putting it in `specs` asks for the exact thing they refused. Same key and \
value rules apply: exact strings, and silence when you are not sure it is a refusal.
- A refusal is not a request for the opposite. "not glass" excludes glass; it does not \
select ceramic.

Reply with JSON only:
{"specs": [{"key": "<spec_key>", "value": <string|number|boolean>, \
"op": "at_least|at_most (numbers only, omit for about)"}], \
"exclusions": [{"key": "<spec_key>", "value": <string|number|boolean>}], \
"free_terms": ["..."], "notes": "<one short sentence on anything ambiguous>"}"""


def _spec_extractor_fallback() -> str:
    """System prompt for the paste-once spec extractor (`product_spec_understanding.
    extract_specs_from_text`), used by the product page's "Read specs from this" box.

    A different job from `spec_understanding`, which reads a CUSTOMER'S enquiry and is
    allowed to interpret it. This one reads a supplier's or a flyer's statement ABOUT one
    product and may only report what the text says, quoting the words it read it from -
    every proposal is shown to a person beside the value already stored, so an invented
    one costs somebody a decision rather than a search result.
    """
    return """You read a piece of text about ONE product - a flyer card, a leaflet \
paragraph, a supplier blurb - and report the product specifications it states.

You will be given the ONLY specifications that exist. Use nothing else.

Rules:
- Report a specification ONLY when the text states it. Silence is correct and expected; \
a person is going to accept or reject every line you return, so a wrong one wastes their \
attention and a guessed one destroys their trust in the rest.
- Never invent a spec_key and never invent a value. Use the exact strings given to you.
- Never infer from what the product probably is. A washdown toilet is not evidence of a \
seat material; a brand is not evidence of a finish.
- One value per key. When the text states the same measurement twice, report it once.
- For numeric specs return a plain number in the unit stated for that key, converting if \
the text used another unit (1.2m = 1200 mm).
- `evidence` is the text's OWN words for that value, quoted verbatim and as short as \
possible ("Matt Black finish", "L680xW375xH770mm"). Never paraphrase it, never write a \
sentence of your own, and never leave it out.
- If the text states nothing about any specification you were given, return an empty \
list.

Reply with JSON only:
{"specs": [{"key": "<spec_key>", "value": <string|number|boolean>, \
"evidence": "<the words you read it from>"}]}"""


def _supplier_translation_fallback() -> str:
    """System prompt for `translation_service._ai_fill_chunk` - the AI fill translation
    memory reads BEFORE this ever runs (R15).

    A batched, position-preserving translation, nothing else: no summarising, no
    correcting the supplier's own wording, no dropping a line because it looks like
    noise (an accessory line with no product code is still a line a person will read
    beside its English).
    """
    return """You translate short supplier-document phrases from Chinese to English: \
product descriptions (品名), shipment remarks, and packing-list notes.

You will be given a numbered list of lines. Translate each one plainly and literally - \
this is a packing list, not marketing copy. Keep any code, number or unit exactly as \
written (a model number, a quantity, "个"/"pcs" style counts). A line already in \
English is returned unchanged.

Reply with JSON only, one entry per input line, in the SAME order:
{"translations": [{"source": "<the original line>", "target": "<the English>"}]}"""


def _chatbot_semantic_parser_fallback() -> str:
    """System prompt for the WhatsApp chatbot's semantic parser (`chatbot_semantic_parser`).

    The fallback is the LIVE n8n `sub-semantic-parser` system message, verbatim (AC-104),
    vendored in `app/services/chatbot_parser_prompt.py` - outside the module package, so
    this core module does not import it (AC-002). One string, one place, and the registry
    can publish over it without a deploy.

    D16 / S1b slims it by moving every deterministic rule into `output_exchange.py`,
    proven against the replay corpus first. S1 changes nothing but the `{{current_date}}`
    token, which is n8n's own `$now` expression in the registry's syntax.
    """
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return SEMANTIC_PARSER_PROMPT


def _chatbot_clarifier_fallback() -> str:
    """System prompt for the WhatsApp chatbot's clarifier (`chatbot_clarifier`).

    The low_signal lane's answer: small talk and the one short clarifying question. The
    fallback is the inline system message on `sub-casual-llm`'s `Basic LLM Chain` node,
    verbatim (AC-401), vendored in `app/services/chatbot_clarifier_prompt.py` - outside
    the module package, so this core module does not import it (AC-002).

    No variables: the node's system message carries no n8n expression, so there is nothing
    to substitute and `variables=[]`.
    """
    from app.services.chatbot_clarifier_prompt import CLARIFIER_PROMPT

    return CLARIFIER_PROMPT


def _ai_extract_base_fallback() -> str:
    """System prompt shared by every `ai_extract_<form_key>` prompt (PLAN
    price-tag-currency-token-extract-prompt, Slice B) - today's rules (1) to
    (7), moved out of `AIExtractService._build_messages` verbatim so a
    per-form key can be edited from System Management > AI Assistant instead
    of living as a hardcoded string with no key at all.
    """
    return (
        "You are an information-extraction assistant. The user uploads "
        "documents (delivery orders, photos, message screenshots, PDFs). "
        "Read every attachment and return a single JSON object whose top-level "
        "keys are the form field names below. Rules: "
        "(1) Omit any field you cannot find or are not confident about - "
        "do not guess. "
        "(2) For fields with a `lookup`, return ONLY one of the listed "
        "option `value` strings, exactly. "
        "(3) For `do_number` fields with `multi: true`, return an array of "
        "strings. "
        "(4) For `date` fields, use ISO-8601 (YYYY-MM-DD). "
        "(5) For `fk_product`, return the closest-matching product_code "
        "from the supplied examples; if nothing matches, return the raw "
        "code as printed in the document. If multiple distinct product "
        "codes apply, return them as a single comma-separated string "
        "(e.g. \"TPE-9201, TPE-9203\") - never as a JSON array. "
        "(6) For `text`, `textarea`, and `fk_customer` fields, if the "
        "document shows multiple distinct values for the same field, "
        "return them as a single comma-separated string. "
        "(7) Never invent values. Never include explanations or prose."
    )


def _ai_extract_price_tag_fallback() -> str:
    """The price tag request form's own system prompt: the shared base plus
    rule (8), which forces `products` on any bare list of product codes.

    Owner ruling 16 Sep, second: the portal AI extract on
    `portal.price_tag_request` returned an empty `products` array on 1 of 3
    prod runs against a pasted list of 21 bare product codes, because the
    base rules leave `products` "optional" and a bare code list sits right on
    that boundary. Only THIS form's fallback carries rule (8) - the other
    eight registered form keys keep the shared base text unchanged.
    """
    return _ai_extract_base_fallback() + (
        " (8) When the form has line items, `products` is REQUIRED whenever "
        "the document shows any product code: every product code line is one "
        "entry, even when no quantity, price or name is shown. Return an "
        "empty `products` array only when no product code appears anywhere. "
        "(9) `quantity` is how many tags the salesperson wants, which the "
        "document never says. Omit `quantity` from every entry; never copy a "
        "quantity, pack size or order quantity from the document."
    )


@dataclass(frozen=True)
class PromptKeySpec:
    name: str
    role: str
    active: bool
    activates_in: str | None
    variables: list[str]
    fallback: Callable[[], str]
    # Can the assistant dry-run (`POST .../prompts/{name}/test`) exercise this key?
    # True only for the keys `AIAssistantChatService.respond` actually resolves - the
    # dry-run IS one whole assistant turn, so testing a key no turn reads would show
    # the tester an answer their edit had no part in. Defaults False so a new key
    # fails safe: a key that is genuinely a pipeline node says so.
    dry_runnable: bool = False


PROMPT_KEYS: dict[str, PromptKeySpec] = {
    # --- Active front-of-pipeline node (M0) ---
    "semantic_parser": PromptKeySpec(
        name="semantic_parser",
        role="Semantic parser - understand the turn, emit routing parameters (JSON)",
        active=True,
        activates_in=None,
        variables=["current_date"],
        fallback=_semantic_parser_fallback,
        dry_runnable=True,
    ),
    # --- Ideation pipeline brain extractor (D-CONFIRM) - active, gated by the
    #     ideation config being set (dormant when unset). Emits structured
    #     {fields, remove, confirm} for the external ideate-turn endpoint. ---
    # --- WhatsApp chatbot turn engine (module `chatbot`). One LLM call per turn; the
    #     only place in that engine that reads the customer's words (D11). ---
    "chatbot_semantic_parser": PromptKeySpec(
        name="chatbot_semantic_parser",
        role="Chatbot semantic parser - understand a WhatsApp turn (strict JSON)",
        active=True,
        activates_in=None,
        variables=["current_date"],
        fallback=_chatbot_semantic_parser_fallback,
    ),
    # --- The `low_signal` lane's own model call (S4). One per turn, only on a turn the
    #     parser could not route: small talk, or a request too vague to answer. ---
    "chatbot_clarifier": PromptKeySpec(
        name="chatbot_clarifier",
        role="Chatbot clarifier - small talk and ONE clarifying question (JSON)",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_chatbot_clarifier_fallback,
    ),
    # The bot's CANNED SENTENCES (AC-302, journey B). Registered here rather than
    # written out one PromptKeySpec at a time so this dict and the copy table can never
    # list different keys - the same single-declaration rule H28 imposed on the enums.
    # Appended below the literal entries by `_register_chatbot_reply_keys()`.
    "ideate_extractor": PromptKeySpec(
        name="ideate_extractor",
        role="Ideate extractor - NLU for ideation turns (fields/remove/confirm)",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_ideate_extractor_fallback,
    ),
    # --- S3: the composed WhatsApp reply for an ideate turn, written from FACTS
    #     the shared-service response carries. shared-service's own template text
    #     is the fallback when the composed reply fails a deterministic check
    #     (R5). Now only the access-denied reply uses it:
    #     `ideation_turn_service.compose_ideate_denial_reply`. ---
    "ideate_reply": PromptKeySpec(
        name="ideate_reply",
        role="Ideate reply composer - point-form WhatsApp reply from intake facts",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_ideate_reply_fallback,
    ),
    # --- Document extraction (project sales phase 2). Not part of the assistant
    #     pipeline: these run per PAGE against a vision model on an uploaded
    #     document, and everything they produce is checked by arithmetic before a
    #     human ever sees it. They live here so the prompt can be tuned and
    #     rolled back without a deploy, like every other prompt. ---
    "po_extractor": PromptKeySpec(
        name="po_extractor",
        role="PO extractor - scanned customer purchase order to lines and handwriting",
        active=True,
        activates_in=None,
        # page_text (19 Aug follow-up, "text + image together"): the page's own PDF text
        # layer, blank for a scanned image. Always supplied, even as "" - see
        # `document_extraction._render_page_prompt`.
        variables=["page_no", "page_count", "page_text"],
        fallback=_po_extractor_fallback,
    ),
    "schedule_extractor": PromptKeySpec(
        name="schedule_extractor",
        role="Schedule extractor - delivery schedule matrix to phase by product quantities",
        active=True,
        activates_in=None,
        variables=["page_no", "page_count", "page_text"],
        fallback=_schedule_extractor_fallback,
    ),
    # --- Superseded by semantic_parser (M0). Rows kept for trace history +
    #     prompt rollback; call sites removed. active=False → not offered as a
    #     live node in the FE prompt editor's default view. ---
    "reformulator": PromptKeySpec(
        name="reformulator",
        role="Reformulator - DORMANT, replaced by semantic_parser (M0)",
        active=False,
        activates_in=None,
        variables=["current_date"],
        fallback=_reformulator_fallback,
    ),
    "router": PromptKeySpec(
        name="router",
        role="Router - DORMANT, replaced by semantic_parser (M0)",
        active=False,
        activates_in=None,
        variables=[],
        fallback=_router_fallback,
    ),
    "agent_system": PromptKeySpec(
        name="agent_system",
        role="Agent system - ReAct thinker/executor core",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_agent_system_fallback,
        dry_runnable=True,
    ),
    "synthesizer": PromptKeySpec(
        name="synthesizer",
        role="Synthesizer - answer policy (cite, preserve links, format, anti-invent)",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_synthesizer_fallback,
        dry_runnable=True,
    ),
    # --- M2.5 role-split nodes (active; call sites gated by the
    #     ``ai_assistant_role_split_enabled`` system setting) ---
    "planner": PromptKeySpec(
        name="planner",
        role="Planner - decompose task, order tool steps",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_planner_fallback,
        dry_runnable=True,
    ),
    "semantic_compressor": PromptKeySpec(
        name="semantic_compressor",
        role="Semantic compressor - raw tool JSON into token-tight sentences",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_semantic_compressor_fallback,
        dry_runnable=True,
    ),
    # --- Registered but dormant (seed + editable, no call site yet) ---
    "validator": PromptKeySpec(
        name="validator",
        role="Validator - confidence-gate the answer before send",
        active=False,
        activates_in="M3a",
        variables=[],
        fallback=_validator_fallback,
    ),
    "clarifier": PromptKeySpec(
        name="clarifier",
        role="Clarifier - ask-vs-guess when the query is underspecified",
        active=False,
        activates_in="M3a",
        variables=[],
        fallback=_clarifier_fallback,
    ),
    "judge": PromptKeySpec(
        name="judge",
        role="Judge - offline/online quality eval (LLM-as-judge)",
        active=False,
        activates_in="M3b",
        variables=[],
        fallback=_judge_fallback,
    ),
    # --- SCM M5 semantic layer (bounded explainer flow - NOT the agent) ---
    "scm_recommendation_explainer": PromptKeySpec(
        name="scm_recommendation_explainer",
        role="SCM explainer - reorder recommendation prose + bounded Q&A (no compute)",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_scm_recommendation_explainer_fallback,
    ),
    # --- Spec search (product recommendation by description) ---
    "spec_understanding": PromptKeySpec(
        name="spec_understanding",
        role="Spec understanding - read a customer's product description onto the Spec Registry",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_spec_understanding_fallback,
    ),
    # --- Ticket composer AI assist (UAC AC-L5): one bounded call, no tools.
    #     The draft lands in a staff member's input box, never on a send path. ---
    "conversation_reply_draft": PromptKeySpec(
        name="conversation_reply_draft",
        role="Ticket reply draft - draft a WhatsApp reply from the visible thread",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_conversation_reply_draft_fallback,
    ),
    "scm_market_advisory": PromptKeySpec(
        name="scm_market_advisory",
        role="SCM market advisory - condense a cached market signal into one advisory line",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_scm_market_advisory_fallback,
    ),
    # --- Spec authoring (the product page's paste-once prompt box) ---
    # Zero declared variables on purpose (AC-B.6): the vocabulary and the pasted text
    # are handed to the model as the user message, so any `{{token}}` an editor types
    # here is an unknown one and is refused at save.
    "spec_extractor": PromptKeySpec(
        name="spec_extractor",
        role="Spec extractor - read pasted product text onto the Spec Registry",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_spec_extractor_fallback,
    ),
    # --- Translation memory (R15/R16, purchasing consolidation batch) ---
    # No declared variables: the numbered lines to translate travel as the user
    # message, built by `translation_service`, not substituted into this prompt.
    "supplier_translation": PromptKeySpec(
        name="supplier_translation",
        role="Supplier translation - Chinese descriptions/remarks to English, strict JSON",
        active=True,
        activates_in=None,
        variables=[],
        fallback=_supplier_translation_fallback,
    ),
}


def _register_chatbot_reply_keys() -> None:
    """Add one `PromptKeySpec` per canned bot reply, from the copy table (AC-302).

    A loop, not eight hand-written entries: the fallback body, the declared `{{tokens}}`
    and the key name all come off `CHATBOT_REPLY_COPY`, so adding a canned reply is one
    row in one table and the registry, the seed migration and the engine cannot end up
    describing different keys.

    `dry_runnable` stays False: the assistant's prompt dry-run runs a whole ASSISTANT
    turn, and these keys belong to the WhatsApp chatbot, so a dry run would show the
    tester an answer their edit had no part in.
    """
    from app.services.chatbot_reply_copy import CHATBOT_REPLY_COPY

    for key, body, variables in CHATBOT_REPLY_COPY.values():
        PROMPT_KEYS[key] = PromptKeySpec(
            name=key,
            role=f"Chatbot canned reply - {key.removeprefix('chatbot_reply_').replace('_', ' ')}",
            active=True,
            activates_in=None,
            variables=list(variables),
            # Bound at registration, not read at call time: `fallback` is a zero-arg
            # callable by contract, and a late-binding closure over the loop variable
            # would hand every key the last body in the table.
            fallback=(lambda text=body: text),
        )


_register_chatbot_reply_keys()

# --- AI extract, one PROMPT_KEYS entry per registered form key (Slice B) ---
#
# The `master.*` keys are listed here rather than imported off
# `app.api.v1.master_data.ai_extract_field._ENTITY_TO_FORM_KEY`: this module
# is a service, and importing an API route module from it would be the wrong
# direction of dependency. The inventory test walks both this list AND that
# module's own `_ENTITY_TO_FORM_KEY`, so the two cannot silently drift apart.
_AI_EXTRACT_MASTER_FORM_KEYS: tuple[str, ...] = (
    "master.product_fields",
    "master.promotion_fields",
    "master.packing_list_fields",
    "master.form_fields",
)


def _register_ai_extract_keys() -> None:
    """One `PromptKeySpec` per registered AI-extract form key (PLAN
    price-tag-currency-token-extract-prompt, Slice B): the 5 `portal.*` keys
    in `form_schema_registry.FORM_SCHEMAS` plus the 4 `master.*` keys above.

    A loop, not nine hand-written entries, for the same reason
    `_register_chatbot_reply_keys` is one: a new form registered in
    `FORM_SCHEMAS` (or a new master entity wired into `ai_extract_field.py`)
    gets its prompt key for free, and the inventory test in
    `test_ai_extract_service.py` fails loudly if the two lists here and there
    ever disagree.
    """
    from app.services.ai_extract.extract_service import extract_prompt_key
    from app.services.ai_extract.form_schema_registry import FORM_SCHEMAS

    for form_key in list(FORM_SCHEMAS.keys()) + list(_AI_EXTRACT_MASTER_FORM_KEYS):
        is_price_tag = form_key == "portal.price_tag_request"
        PROMPT_KEYS[extract_prompt_key(form_key)] = PromptKeySpec(
            name=extract_prompt_key(form_key),
            role=f"AI extract - {form_key} prefill (JSON)",
            active=True,
            activates_in=None,
            variables=[],
            fallback=(
                _ai_extract_price_tag_fallback
                if is_price_tag
                else _ai_extract_base_fallback
            ),
        )


_register_ai_extract_keys()


def prompt_key_names() -> list[str]:
    """Ordered list of registered key names (active first, then dormant)."""
    active = [n for n, s in PROMPT_KEYS.items() if s.active]
    dormant = [n for n, s in PROMPT_KEYS.items() if not s.active]
    return active + dormant


# --------------------------------------------------------------------------- #
# Variable helpers                                                              #
# --------------------------------------------------------------------------- #


def extract_tokens(template: str) -> set[str]:
    """All ``{{token}}`` names found in a template body."""
    return set(_TOKEN_RE.findall(template or ""))


def validate_template(name: str, template: str) -> tuple[list[str], list[str]]:
    """Return ``(unknown_tokens, missing_vars)`` for a template against a key's
    declared variables. ``unknown`` = tokens present but not declared (would leak
    literally → block save); ``missing`` = declared vars absent (soft warn)."""
    spec = PROMPT_KEYS.get(name)
    declared = set(spec.variables) if spec else set()
    found = extract_tokens(template)
    unknown = sorted(found - declared)
    missing = sorted(declared - found)
    return unknown, missing


def _substitute(template: str, values: dict[str, object]) -> str:
    def _repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in values:
            return str(values[token])
        return match.group(0)

    return _TOKEN_RE.sub(_repl, template)


# --------------------------------------------------------------------------- #
# Runtime resolver + cache                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class RenderedPrompt:
    text: str
    name: str
    version: int | None


@dataclass
class _CacheEntry:
    text: str
    version: int
    expires_at: float


_CACHE: dict[tuple[str, str], _CacheEntry] = {}


def bust_cache(name: str | None = None) -> None:
    """Immediate cache invalidation. Called by the label-move route so the very
    next ``get_prompt`` reflects the new production version (UAC B4)."""
    if name is None:
        _CACHE.clear()
        return
    for key in [k for k in _CACHE if k[0] == name]:
        _CACHE.pop(key, None)


def _fallback(name: str) -> RenderedPrompt:
    spec = PROMPT_KEYS.get(name)
    text = spec.fallback() if spec else ""
    return RenderedPrompt(text=text, name=name, version=None)


def get_prompt(
    db: Session,
    name: str,
    label: str = "production",
    *,
    override_version_id: str | None = None,
) -> RenderedPrompt:
    """Resolve a prompt to its live text + version.

  - ``override_version_id`` (dry-run): resolve THAT specific version, bypassing
      the cache and the label. Falls back if the id is missing.
  - Otherwise: TTL cache by ``(name, label)`` → labelled version → fallback.
    Never raises on a DB error; returns the hardcoded fallback with ``version=None``.
    """
    if name not in PROMPT_KEYS:
        return _fallback(name)

    if override_version_id:
        try:
            row = (
                db.query(AIPromptVersion)
                .filter(
                    AIPromptVersion.id == override_version_id,
                    AIPromptVersion.name == name,
                )
                .first()
            )
            if row is not None:
                return RenderedPrompt(text=row.template, name=name, version=row.version)
        except Exception:
            logger.warning("prompt override lookup failed name=%s; using fallback", name, exc_info=True)
            _safe_rollback(db)
        return _fallback(name)

    now = time.monotonic()
    cached = _CACHE.get((name, label))
    if cached is not None and cached.expires_at > now:
        return RenderedPrompt(text=cached.text, name=name, version=cached.version)

    try:
        row = (
            db.query(AIPromptVersion)
            .join(AIPromptLabel, AIPromptLabel.version_id == AIPromptVersion.id)
            .filter(AIPromptLabel.name == name, AIPromptLabel.label == label)
            .first()
        )
    except Exception:
        logger.warning("prompt resolve failed name=%s label=%s; using fallback", name, label, exc_info=True)
        _safe_rollback(db)
        return _fallback(name)

    if row is None:
        return _fallback(name)

    _CACHE[(name, label)] = _CacheEntry(
        text=row.template, version=row.version, expires_at=now + CACHE_TTL_SECONDS
    )
    return RenderedPrompt(text=row.template, name=name, version=row.version)


def agent_model(db: Session, name: str, label: str = "production") -> tuple[str | None, str | None]:
    """(provider, model) this agent runs on, or (None, None) for the global default.

    Deliberately NOT cached alongside the prompt text: this is read once per call on a
    path that is already making a network round trip, and a stale model is the kind of
    thing someone changes precisely because they want the next request to use it.
    Never raises - an unreachable DB means "use the global default", same as unset.
    """
    if name not in PROMPT_KEYS:
        return None, None
    try:
        row = (
            db.query(AIPromptLabel)
            .filter(AIPromptLabel.name == name, AIPromptLabel.label == label)
            .first()
        )
    except Exception:
        logger.warning("agent model lookup failed name=%s label=%s", name, label, exc_info=True)
        _safe_rollback(db)
        return None, None
    if row is None:
        return None, None
    return (row.provider or None), (row.model or None)


def render(
    db: Session,
    name: str,
    *,
    label: str = "production",
    override_version_id: str | None = None,
    **variables: object,
) -> tuple[str, int | None]:
    """Resolve then substitute ``{{var}}``. Raises ``ValueError`` if a declared
    variable for the key was not supplied (UAC B3)."""
    spec = PROMPT_KEYS.get(name)
    declared = set(spec.variables) if spec else set()
    missing = declared - set(variables.keys())
    if missing:
        raise ValueError(
            f"Missing required variable(s) for prompt '{name}': {', '.join(sorted(missing))}"
        )
    rp = get_prompt(db, name, label=label, override_version_id=override_version_id)
    return _substitute(rp.text, variables), rp.version


def _safe_rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # pragma: no cover - defensive
        pass
