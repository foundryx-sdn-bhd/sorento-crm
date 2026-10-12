"""The `low_signal` lane: small talk and clarification (S4, AC-401 to AC-403).

Port of the n8n sub-workflow `sub-casual-llm` plus the one node from `sub-answer` that
reads its answer:

* `resolve-entity-clarification` (an httpRequest onto `POST /system/references/resolve`)
  becomes `resolve_for_prompt`, an in-process call to the same service function the route
  calls. Same body, no HTTP hop, no API key round trip;
* `construct-user-prompt.js` becomes `construct_user_prompt`, a pure function;
* the `Basic LLM Chain` node becomes `resolve_clarifier_config` + `call_clarifier`, split
  exactly the way `head/parser.py` splits the semantic parser call, for the identical
  reason (below);
* `central-exchange.js` from `sub-answer` becomes `central_exchange`. It lives here rather
  than in a shared module because this is the first lane to need it; the second lane that
  does is what should move it (S6's answer path reads the same node).

**The config / call split is a capacity rule, not a style.** `resolve_clarifier_config`
takes a `Session` and `call_clarifier` takes none, so the caller can close its session
before the provider I/O and reopen after. The evidence is the 96/100-connection incident
the engine's module docstring records, and `tests/chatbot/test_s4_casual_lane.py` asserts
the open-session count is zero while the clarifier runs.

**D11.** Nothing here matches the customer's raw text. `user_goal` and the message text are
COPIED into the prompt (that is the whole point of a clarifier: hand the words to the model
and let it read them), never inspected, and no regex touches either.

`Aggregate` is not ported. In the live workflow it is orphaned with no connections at all,
so `$('Aggregate').isExecuted` is `false` on every turn that reaches this sub and the
access-level intersection it guards has never run. Its own body says so.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.chatbot import jsc

logger = logging.getLogger(__name__)

PROMPT_KEY = "chatbot_clarifier"

# The `OpenAI Chat Model` node feeding `Basic LLM Chain` hard-codes this. An
# `ai_prompt_registry.agent_model` override for the key wins over it, the same way the
# semantic parser's does.
DEFAULT_MODEL = "gpt-4.1-mini"

CLARIFIER_MAX_TOKENS = 512

# The SETUP arm's customer text. n8n has no reply for these paths at all - a missing API
# key or an unset AI-assistant config never reaches `sub-error-logger2`, because in n8n the
# credential is bound to the node and the turn simply dies - so there is nothing to be
# faithful to and this is a new sentence rather than a ported one (divergences.py, H32).
#
# Fixed, and never `str(exc)`: those messages name providers, configuration keys and
# occasionally a URL. A customer gets a sentence that tells them what to do; the operator
# gets the real reason on the turn row and the trace.
CLARIFIER_UNAVAILABLE_REPLY = (
    "Sorry, I can't reply to that right now. Please try again in a moment."
)

# `resolve-entity-clarification`'s own literal, used for all three of query, tokens and
# allowed_entity_types when the turn named nothing. It is a real string the resolver
# searches for, not a sentinel, which is why it is reproduced rather than replaced by None.
NOTHING = "nothing"

_FENCE_RE = re.compile(r"```[\s\S]*?```")
_FENCE_MARK_RE = re.compile(r"```json?|```")


class ClarifierError(RuntimeError):
    """The clarifier call could not be completed.

    Caught at the lane's call site in `engine._run_stages`, NOT by `run_turn`'s outer
    catch-all: routing has already succeeded by the time this can be raised, so the turn
    keeps `branch_kind = "low_signal"` and fails at `stage = "casual_llm"`. The catch-all
    would null the branch kind and send the generic parser-error reply instead.
    """


@dataclass(frozen=True)
class ClarifierConfig:
    """Everything the LLM call needs, resolved BEFORE the session is released."""

    system_prompt: str
    prompt_version: int | None
    provider: str
    model: str
    api_key: str


# --------------------------------------------------------------------------- #
# resolve-entity-clarification (httpRequest -> in-process service call)
# --------------------------------------------------------------------------- #


def _qf(ctx: Any) -> dict:
    """`ctx.parse.output`, the parser's post-processed emission."""
    return jsc.get(jsc.get(ctx, "parse"), "output") or {}


def resolve_for_prompt(db: Session, *, ctx: dict) -> dict:
    """The body `resolve-entity-clarification` posts, sent in process. Session-bound.

    `contact_id` and `space_id` ride on that node's URL query string and neither is read
    by `_resolve_input` (grepped: no reference), so they carry no signal and are not
    threaded here.

    **No `dry_run` parameter, deliberately.** S6a added `ResolveReferenceRequest.dry_run`
    for exactly this rule, and what it suppresses is the `ai_assistant_usage_logs` row the
    spec-search reader writes - in `resolve_reference_post`, ABOVE `_resolve_input`. This
    lane calls `_resolve_input` directly and so never reaches that write; the function
    itself is read-only (no `db.add` / `db.commit` / INSERT in its body). A flag here would
    guard nothing. The trigger to add one is named: the first write `_resolve_input` itself
    grows.

    `access_levels` comes straight from the parse output. The live node wraps it in an
    `$('Aggregate').isExecuted ? intersection(...) : ...` ternary whose true arm is
    unreachable - see the module docstring.
    """
    from app.api.v1.system.references import _resolve_input

    out = _qf(ctx)
    entities = jsc.array(out.get("entities"))
    raws = [jsc.get(e, "raw") for e in entities]
    # REF-PROBES: a reference hint is replaced by the kinds this asker may see (the engine
    # puts them on `ctx["reference_kinds"]`), so a hidden kind never reaches the prompt.
    from app.services.chatbot.lanes.business.resolve_gate import _allowed_entity_types

    hints = _allowed_entity_types(ctx, entities)
    goal = out.get("user_goal")

    return _resolve_input(
        db,
        goal if jsc.truthy(goal) else NOTHING,
        raws if len(raws) else [NOTHING],
        match_mode="or",
        allowed_entity_types=hints if len(raws) else [NOTHING],
        access_levels=jsc.array(out.get("access_levels")),
        fallback_to_all_types=True,
    )


# --------------------------------------------------------------------------- #
# construct-user-prompt.js
# --------------------------------------------------------------------------- #


def construct_user_prompt(ctx: dict, resolved: dict | None) -> dict[str, Any]:
    """The six fields the LLM chain reads. Pure.

    The entity list is the node's own `flatMap`: every match of every resolution, reduced
    to `entity_type` + `canonical_code` and nothing else. A resolution with no `matches`
    contributes nothing (the `|| []`).
    """
    out = _qf(ctx)
    message_type = out.get("message_type")
    session_vars = jsc.get(jsc.get(ctx, "session"), "session_vars")

    entities: list[dict[str, Any]] = []
    for res in jsc.array(jsc.get(resolved, "resolutions")):
        for match in jsc.array(jsc.get(res, "matches")):
            entities.append(
                {
                    "entity_type": jsc.get(match, "entity_type"),
                    "canonical_code": jsc.get(match, "canonical_code"),
                }
            )

    # `ctx.text.message.message.text || ...attachment.description` - a voice note or an
    # image arrives with no `text`, and its description is what the customer "said".
    message = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message")
    input_msg = jsc.get(message, "text")
    if not jsc.truthy(input_msg):
        input_msg = jsc.get(jsc.get(message, "attachment"), "description")

    goal = out.get("user_goal")

    # Small talk carries no scope worth remembering, so the previous turn's state is not
    # shown to the clarifier at all: it would invite a reply about the last order when the
    # customer only said hello.
    if message_type == "casual" or message_type == "unknown":
        session_vars = {}

    return {
        "message_type": message_type,
        "intent_hint": out.get("intent_hint"),
        "domain_hint": out.get("domain_hint"),
        "session_vars": session_vars,
        "entities": entities,
        "user_goal": goal if jsc.truthy(goal) else input_msg,
    }


def render_user_message(prompt: dict[str, Any]) -> str:
    """The `Basic LLM Chain` node's own `text` template, field for field.

    Trailing spaces on each line are the node's, reproduced rather than tidied: this is
    the string the live model has been trained on by every turn so far.
    """
    return (
        f"message_type: {jsc.js_string(prompt.get('message_type'))}  \n"
        f"intent_hint: {jsc.js_string(prompt.get('intent_hint'))}  \n"
        f"domain_hint: {jsc.js_string(prompt.get('domain_hint'))}   \n"
        f"user_goal: {jsc.js_string(prompt.get('user_goal'))}   \n"
        f"entities: {json.dumps(prompt.get('entities'), separators=(',', ':'))}   \n"
        f"session_vars: {json.dumps(prompt.get('session_vars'), separators=(',', ':'))}  "
    )


# --------------------------------------------------------------------------- #
# central-exchange.js (sub-answer)
# --------------------------------------------------------------------------- #


def central_exchange(item: dict | None) -> Any:
    """The fence-stripping parse both answer lanes share. Pure, and faithfully odd.

    Four arms, in the node's own order:

    * `output` is already an object -> take it whole;
    * otherwise coerce `output` (else `text`) to a string, strip Markdown fences, and
      parse from the first `{` to the last `}` - the model wraps its JSON in prose often
      enough that this slice is load-bearing;
    * no `{` anywhere -> RETURN THE RAW STRING. The JS then does
      `output.quick_reply = input.quick_reply`, which is a silent no-op on a string
      primitive, so the node really does return a bare string here. Reproduced, not fixed;
    * nothing to read at all -> return the input unchanged.
    """
    item = item or {}
    output = jsc.get(item, "output")
    # `typeof output === 'object'` in the JS, which is TRUE for an array and for `null`
    # (guarded here by the `&&` the node writes before it). `isinstance(dict)` is narrower:
    # an ARRAY reaching this node would take the string branch here and the object branch
    # there. Left narrow deliberately - `central-exchange`'s producer is an LLM chain whose
    # `output` is a string or a JSON object, never an array, and 49 real captures agree -
    # but named, because it is a real difference and not an oversight.
    if isinstance(output, dict):
        return output

    raw = jsc.js_string(output if jsc.truthy(output) else (jsc.get(item, "text") or ""))
    if not raw:
        return item

    raw = _FENCE_RE.sub(lambda m: _FENCE_MARK_RE.sub("", m.group(0)), raw)
    idx = raw.find("{")
    if idx == -1:
        return raw
    start_slice = raw[idx:]
    last = start_slice.rfind("}")
    return json.loads(start_slice[: last + 1] if last != -1 else start_slice)


# --------------------------------------------------------------------------- #
# Basic LLM Chain (config resolved on a session, called without one)
# --------------------------------------------------------------------------- #


def resolve_clarifier_config(
    db: Session, *, override_version_id: str | None = None
) -> ClarifierConfig:
    """Read the prompt, the per-key model override and the API key. Session-bound.

    Call this, then CLOSE the session, then call `call_clarifier`.

    `override_version_id` mirrors `head/parser.resolve_config`: it pins a specific prompt
    VERSION for the Prompts screen's dry-run turn (AC-807), and the engine only ever
    supplies it on a dry run.
    """
    from app.services.ai_assistant_service import AIAssistantConfigService
    from app.services.ai_prompt_registry import agent_model, render
    from app.services.llm_provider import resolve_api_key

    system_prompt, prompt_version = render(
        db, PROMPT_KEY, override_version_id=override_version_id
    )
    provider_override, model_override = agent_model(db, PROMPT_KEY)

    config = AIAssistantConfigService(db).get()
    if config is None:
        raise ClarifierError("AI assistant configuration is not set")
    provider = provider_override or config.provider
    model = model_override or DEFAULT_MODEL
    api_key = resolve_api_key(config, provider)
    if not api_key:
        raise ClarifierError(f"no API key configured for provider {provider!r}")
    return ClarifierConfig(
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        provider=provider,
        model=model,
        api_key=api_key,
    )


def call_clarifier(config: ClarifierConfig, user_prompt: str) -> str:
    """One chat call, raw content back. NOTHING here touches the database.

    Returns the provider's text unparsed: `central_exchange` is what reads it, because in
    n8n that parse happens in a different sub-workflow and a turn where the model answers
    but the JSON is malformed must fail in the same place it does today.
    """
    from app.services.chatbot import llm_call

    messages = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        # A rate limit is waited out inside the call (PR #1247 round 6, ruling 3).
        result = llm_call.chat(
            config.provider,
            config.api_key,
            config.model,
            messages,
            temperature=0.0,
            max_tokens=CLARIFIER_MAX_TOKENS,
        )
    except llm_call.RateLimited as exc:
        raise ClarifierRateLimited(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - any provider/transport failure is the lane's
        raise ClarifierError(str(exc)) from exc
    return (result.content or "").strip()


class ClarifierRateLimited(ClarifierError):
    """Every attempt was refused with a 429 (PR #1247 round 6, ruling 3). The dealer is
    told `llm_call.RATE_LIMITED_REPLY`, never the provider's text."""


class ClarifierAnswerEmpty(ClarifierError):
    """The clarifier answered, and the answer had nothing to say to the customer."""


def reply_text(parsed: Any) -> str:
    """The `response` field the clarifier promised, or the raw answer if it did not.

    `central_exchange` returns a bare STRING when the model answered in prose with no JSON
    at all. n8n sends that string, so this does too rather than failing a turn the customer
    would have been happy with.

    A JSON object with no usable `response`, though, is a FAILED turn and not an empty
    message. This is a deliberate divergence from n8n, which would send "" and leave the
    customer looking at a blank bubble with no record that anything went wrong (H32 - the
    dropped turn). Raising instead puts the reason on the row, sends today's error text,
    and is visible on the trace screen.
    """
    if isinstance(parsed, dict):
        response = parsed.get("response")
        if not jsc.truthy(response):
            raise ClarifierAnswerEmpty(
                "the clarifier returned an object with no `response` text"
            )
        return jsc.js_string(response)
    text = jsc.js_string(parsed)
    if not text.strip():
        raise ClarifierAnswerEmpty("the clarifier returned nothing to say")
    return text
