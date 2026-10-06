"""The code reader: ONE narrow model question the shared resolver asks about product
tokens that missed every code tier (RESOLVE-SEMANTIC-FALLBACK, owner ruling 6 Oct 2026).

Prod turn 6 Oct 20:02 MYT: "TAP SRTWT5608 x 5" reached `entity_resolver.resolve_references`
as the folded token "TAPSRTWT5608" and matched no code tier (exact / prefix / substring on
`product_code`), so the dealer read "I could not find TAP SRTWT5608" while SRTWT5608 exists.
The owner's rules: no fuzzy, typo or word-shape matching in code; the parser decides what a
code is; a semantic fallback is fine; the same behaviour in every place that resolves.

So the resolver, after every deterministic tier has missed on a product-typed token, asks
the parser model one question about the typed text: is there a product code inside it
(`code`), only part of one (`family`), or no code at all (`none`)? The answer is a COPY of
characters the dealer typed - `guard` refuses a value that is not inside the typed text, so
the model can never correct a typo or invent a neighbour - and the catalogue decides whether
that code exists: the resolver re-runs its own exact tier on a `code`, its own
prefix-then-substring product probe on a `family` (one row answers as that code, several
are the existing picker), and a `none` leaves the token in `unresolved_tokens` for the
existing not-found wording.

Bounds (card Q3, Q5): one call per resolve request covering every missed product token, at
most `TOKENS_PER_CALL` tokens (the rest are reported `not_asked`), `BUDGET_SECONDS` for the
one attempt, no retry; a timeout, a provider error, a non-JSON answer or no configured
provider is an `error` reading - the token stays a miss, the call never fails. The model
sees the message and the tokens only, never a candidate list. Usage lands in
`ai_assistant_usage_logs` (`feature="resolver_code_reader"`) unless the call is a dry run,
the same bookkeeping `product_spec_understanding` does for its reader in the same route.

Prompt and model: the registry key `resolver_code_reader` (Prompts screen; `agent_model`
override), falling back to the chatbot parser's own provider and model, then the global
assistant config. Recorded and replayed through `llm_cassette` like every provider call.

Seams: `read_codes` is the live call (tests stub `_resolve_provider`); `per_request` is the
cap-memo-never-raises wrapper the route puts around it so `fallback_to_all_types`' re-run
of the unresolved tokens never spends a second call; `reader_for` is the two together.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROMPT_KEY = "resolver_code_reader"
FEATURE = "resolver_code_reader"
#: The parser's own key, whose per-agent provider / model this reader inherits by default.
PARSER_PROMPT_KEY = "chatbot_semantic_parser"

#: The whole call, one attempt (owner: 3 s, then not found).
BUDGET_SECONDS = 3.0
#: How many missed tokens one request may ask about; the rest are `not_asked`.
TOKENS_PER_CALL = 5
_MAX_OUTPUT_TOKENS = 400
_TEMPERATURE = 0.0

KINDS = frozenset({"code", "family", "none"})

#: `(token, typed text or None)`: the resolver's folded token and the pre-fold text the
#: caller sent for it (`raw_tokens`), when it did.
Missed = tuple[str, str | None]


@dataclass(frozen=True)
class CodeReading:
    """The reader's verdict for one token.

    `kind` is `code` / `family` / `none` (the model's three answers, guarded), `error`
    (no answer: provider, budget, shape) or `not_asked` (over the per-request cap).
    `value` is the characters read out of the typed text for `code` / `family`, else None.
    `reason` says why a `none` was forced or what failed, for the trace.
    """

    kind: str
    value: str | None = None
    reason: str | None = None


CodeReader = Callable[[list[Missed]], dict[str, CodeReading]]

_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]+")
#: What a value may be made of: letters, digits, and the two separators the catalogue's
#: own probes already fold away (`entity_resolver._strip_all_ws`). Anything else (`_`,
#: `%`, punctuation) is refused BEFORE the value reaches an `ilike`, so the model cannot
#: smuggle a wildcard or a character the dealer never typed into the probe
#: (security review, 6 Oct 2026, finding 1).
_VALUE_CHARS = re.compile(r"^[0-9A-Za-z -]+$")

#: Fixed reason codes, the only text a `fallback.reason` ever carries: a provider's own
#: error text (which can quote a masked key or a vendor body) stays in the log
#: (security review, finding 2).
REASON_NO_PROVIDER = "no_provider"
REASON_PROVIDER_LOOKUP_FAILED = "provider_lookup_failed"
REASON_PROVIDER_ERROR = "provider_error"
REASON_TIMED_OUT = "timed_out"
REASON_ANSWER_UNREADABLE = "answer_unreadable"
REASON_NO_READING = "no_reading"
REASON_UNKNOWN_KIND = "unknown_kind"
REASON_EMPTY_VALUE = "empty_value"
REASON_BAD_CHARACTERS = "bad_characters"
REASON_NOT_IN_TYPED_TEXT = "not_in_typed_text"
REASON_OVER_CAP = "over_cap"
REASON_READER_RAISED = "reader_raised"


def _fold(value: Any) -> str:
    """Letters and digits only, casefolded: the comparison the exact tier itself makes
    (`entity_resolver._strip_all_ws` + lower, widened to every separator)."""
    return _NOT_ALNUM.sub("", str(value or "")).casefold()


def _typed_span(value: str, typed: str) -> bool:
    """Does `value` (letters and digits, in order, any dashes or spaces between them) occur
    in `typed` as a WHOLE word run: nothing alphanumeric right before or right after it?
    The owner's rule is "copy what the dealer typed, never complete or cut a code": a value
    that stops in the middle of a typed code ("SRTWC8066" out of "WC SRTWC8066SS") would
    resolve the base product instead of the variant the dealer named, and a fragment cut
    out of a longer code ("5699" out of "SRTWT5699") would open another family's picker
    (reviewer blocker, 6 Oct 2026). Case folded; separators inside the value are free, the
    same `[-\\s]` the catalogue probes fold away."""
    letters = [ch for ch in value if ch.isalnum()]
    if not letters:
        return False
    pattern = r"(?<![0-9A-Za-z])" + r"[\s-]*".join(re.escape(ch) for ch in letters) + r"(?![0-9A-Za-z])"
    return re.search(pattern, typed, flags=re.IGNORECASE) is not None


def guard(reading: CodeReading, typed: str | None, token: str) -> CodeReading:
    """The one rule that keeps the owner's "no typo matching": a `code` or `family` value
    must be made of letters, digits, spaces and hyphens only, and must occur in what the
    dealer typed as a whole word run (`_typed_span`), else the reading is `none` with a
    reason code. With no typed text sent (an API caller without `raw_tokens`) the folded
    token is the only text, so only a value equal to the whole token can pass. `none`,
    `error` and `not_asked` pass through."""
    if reading.kind not in ("code", "family"):
        if reading.kind in ("none", "error", "not_asked"):
            return reading
        return CodeReading("none", None, reason=REASON_UNKNOWN_KIND)
    raw_value = str(reading.value or "").strip()
    if not _fold(raw_value):
        return CodeReading("none", None, reason=REASON_EMPTY_VALUE)
    if not _VALUE_CHARS.match(raw_value):
        return CodeReading("none", None, reason=REASON_BAD_CHARACTERS)
    if not _typed_span(raw_value, typed or token):
        return CodeReading("none", None, reason=REASON_NOT_IN_TYPED_TEXT)
    return CodeReading(reading.kind, raw_value)


def _resolve_provider(db: Session):
    """(provider, provider_name, model_name) this reader runs on; provider None when
    unconfigured. The key's own `agent_model` override first, then the parser's, then the
    global assistant config - the same ladder `product_spec_understanding._resolve_provider`
    walks, with the parser as the middle rung because this question is the parser's job."""
    from app.models.ai_assistant import AIAssistantConfig
    from app.services.ai_prompt_registry import agent_model
    from app.services.llm_provider import get_provider, resolve_api_key, resolve_model

    try:
        cfg = db.query(AIAssistantConfig).order_by(AIAssistantConfig.created_at.asc()).first()
    except Exception:  # noqa: BLE001 - no config row readable means no provider
        logger.warning("resolver code reader: assistant config unreadable", exc_info=True)
        _recover(db)
        return None, "openai", ""
    # The provider and the model come from ONE rung of the ladder (reviewer finding 3): a
    # reader row set to one vendor with no model must not borrow the parser's model for
    # another vendor and post it there.
    own_provider, own_model = agent_model(db, PROMPT_KEY)
    parser_provider, parser_model = agent_model(db, PARSER_PROMPT_KEY)
    if own_provider:
        provider_name, model_override = own_provider, own_model
    elif parser_provider:
        provider_name, model_override = parser_provider, parser_model
    else:
        provider_name, model_override = (cfg.provider if cfg else "openai") or "openai", own_model or parser_model
    model_name = resolve_model(cfg, provider_name, model_override)
    api_key = resolve_api_key(cfg, provider_name)
    if not api_key:
        return None, provider_name, model_name
    try:
        return get_provider(provider_name, api_key, model=model_name), provider_name, model_name
    except ValueError:
        logger.warning("resolver code reader: unknown provider %r", provider_name)
        return None, provider_name, model_name


#: The most text one call sends to the model (security review, 6 Oct 2026, final head
#: finding 1): the feature is on by default for every API-key caller, so an unbounded
#: `query` would bill a maximum-size input per request. A dealer's longest real stock
#: check is a few hundred characters; a code is under 40.
MESSAGE_CHARS = 2000
TYPED_CHARS = 200


def _user_message(message_text: str | None, missed: list[Missed]) -> str:
    return json.dumps(
        {
            "message": (message_text or "")[:MESSAGE_CHARS],
            "tokens": [
                {"token": token[:TYPED_CHARS], "typed": (typed[:TYPED_CHARS] if typed else typed)}
                for token, typed in missed
            ],
        },
        ensure_ascii=False,
    )


def _readings_from(content: str, missed: list[Missed]) -> dict[str, CodeReading]:
    """Parse the model's `{"readings": [{token, kind, value}]}`; raise on a shape that is
    not that. A token the model left out is `none`; one it was not asked about is dropped."""
    payload = json.loads(content or "")
    if not isinstance(payload, dict) or not isinstance(payload.get("readings"), list):
        raise ValueError("answer is not {\"readings\": [...]}")
    asked = {token: typed for token, typed in missed}
    out: dict[str, CodeReading] = {}
    for item in payload["readings"]:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "")
        if token not in asked or token in out:
            continue
        raw_value = item.get("value")
        value = str(raw_value) if raw_value is not None else None
        # The model owns three answers; `error` / `not_asked` are the code's own verdicts
        # and anything else is no reading at all.
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            kind, value = "none", None
        out[token] = guard(CodeReading(kind, value), asked[token], token)
    for token in asked:
        out.setdefault(token, CodeReading("none", None, reason=REASON_NO_READING))
    return out


def read_codes(
    db: Session,
    missed: list[Missed],
    *,
    message_text: str | None,
    user_id: str | None,
    dry_run: bool,
    budget_seconds: float = BUDGET_SECONDS,
) -> dict[str, CodeReading]:
    """One provider call about `missed`; never raises. See the module docstring."""
    missed = list(missed)
    if not missed:
        return {}

    def failed(reason: str) -> dict[str, CodeReading]:
        return {token: CodeReading("error", None, reason=reason) for token, _typed in missed}

    try:
        provider, provider_name, model_name = _resolve_provider(db)
        if provider is None:
            return failed(REASON_NO_PROVIDER)
        from app.services.ai_prompt_registry import get_prompt

        system_text = get_prompt(db, PROMPT_KEY).text
    except Exception as exc:  # noqa: BLE001 - never a failed resolve
        logger.warning("resolver code reader: provider lookup failed: %s", exc)
        return failed(REASON_PROVIDER_LOOKUP_FAILED)

    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": _user_message(message_text, missed)},
    ]
    started = time.monotonic()
    try:
        result = provider.chat(
            messages,
            temperature=_TEMPERATURE,
            max_tokens=_MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            timeout=budget_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - a provider failure is a miss, with its reason
        from app.services.chatbot.llm_call import is_timeout

        logger.warning("resolver code reader: provider call failed: %s", exc)
        return failed(REASON_TIMED_OUT if is_timeout(exc) else REASON_PROVIDER_ERROR)
    elapsed_ms = int((time.monotonic() - started) * 1000)

    try:
        readings = _readings_from(result.content or "", missed)
    except Exception as exc:  # noqa: BLE001 - a malformed answer is a miss, with its reason
        logger.warning("resolver code reader: answer not readable: %s", exc)
        readings = failed(REASON_ANSWER_UNREADABLE)

    if not dry_run:
        _log_usage(db, provider_name, model_name, result, elapsed_ms, user_id, readings)
    return readings


def _log_usage(db: Session, provider_name: str, model_name: str, result: Any, elapsed_ms: int,
               user_id: str | None, readings: dict[str, CodeReading]) -> None:
    from app.models.ai_assistant import AIAssistantUsageLog

    # The row is written under a SAVEPOINT so a failed insert (an `act_as_user_id` that is
    # no `users.id`, say) rolls back the bookkeeping alone and never the caller's own
    # pending work: on the chatbot path `db` is the turn's session (security review,
    # 6 Oct 2026, finding 3). The commit that follows is the same one the spec reader
    # in this route has always made.
    try:
        with db.begin_nested():
            db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    feature=FEATURE,
                    provider=provider_name,
                    model=model_name,
                    prompt_tokens=int(getattr(result, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(result, "completion_tokens", 0) or 0),
                    total_tokens=int(getattr(result, "total_tokens", 0) or 0),
                    tool_calls_count=0,
                    response_time_ms=elapsed_ms,
                    was_answered=any(r.kind in ("code", "family") for r in readings.values()),
                )
            )
            db.flush()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - never fail a resolve over bookkeeping
        logger.warning("resolver code reader: usage log failed: %s", exc)
        _recover(db)


def _recover(db: Session) -> None:
    """A failed statement leaves a Postgres transaction aborted; the resolver's own
    queries after this one would then raise `PendingRollbackError` and 500 the route or
    fail the turn. Roll back only when the session says it needs it (security review,
    6 Oct 2026, final head finding 2)."""
    try:
        if not db.is_active:
            db.rollback()
    except Exception:  # noqa: BLE001 - nothing more to do for a dead session
        logger.warning("resolver code reader: session rollback failed", exc_info=True)


def per_request(reader: CodeReader, *, cap: int = TOKENS_PER_CALL) -> CodeReader:
    """`reader`, asked once per token, at most `cap` tokens per request, never raising.

    The route re-runs the still-unresolved tokens when `fallback_to_all_types` is on
    (`references._resolve_input`), so the SAME token can reach the resolver twice in one
    request; the memo makes the second pass free. A token over the cap is `not_asked`."""
    answered: dict[str, CodeReading] = {}
    asked_count = 0

    def call(missed: list[Missed]) -> dict[str, CodeReading]:
        nonlocal asked_count
        missed = list(missed)
        out: dict[str, CodeReading] = {}
        fresh: list[Missed] = []
        for token, typed in missed:
            if token in answered:
                out[token] = answered[token]
            elif asked_count + len(fresh) < cap:
                fresh.append((token, typed))
            else:
                out[token] = CodeReading("not_asked", None, reason=REASON_OVER_CAP)
        if fresh:
            asked_count += len(fresh)
            try:
                readings = reader(fresh)
            except Exception as exc:  # noqa: BLE001 - a reader failure is a miss, never a failed call
                logger.warning("resolver code reader: reader raised: %s", exc)
                readings = {token: CodeReading("error", None, reason=REASON_READER_RAISED) for token, _ in fresh}
            for token, _typed in fresh:
                reading = readings.get(token) if isinstance(readings, dict) else None
                if reading is not None:
                    answered[token] = reading
                    out[token] = reading
        return out

    return call


def reader_for(db: Session, *, message_text: str | None, user_id: str | None, dry_run: bool) -> CodeReader:
    """The live reader for one resolve request: `read_codes` under `per_request`."""
    return per_request(
        lambda missed: read_codes(
            db, missed, message_text=message_text, user_id=user_id, dry_run=dry_run
        )
    )
