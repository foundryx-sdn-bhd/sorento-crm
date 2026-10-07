#!/usr/bin/env python3
"""The owner's console check: run a file of real turns and grade the replies.

One YAML file of cases, one line of output per case, exit 1 if any case fails. It is the
step between "the tests are green" and "the owner tries it in WhatsApp": pytest proves the
code does what the contract says, this proves a REAL turn through a REAL backend still says
the right thing to a customer.

    python scripts/chatbot_console_check.py tests/chatbot/console_cases/2026-09-06.yaml \\
        --base-url http://localhost:8004

Every turn is a DRY RUN (D14): the envelope carries `is_test: true` and a `test_run_id`, so
nothing outside `chatbot.turns` is written and no WhatsApp message can leave. The parser is
the REAL one unless `--mock-parser` is passed AND the case carries its own `parser:` block -
the point of the check is that the whole turn works, and the parse is most of a turn.

**The envelope is a real one, borrowed.** A turn's first act is to read the contact's stored
state, and an unknown contact fails at `received`; so rather than inventing a shape, the
script reads the LATEST `chatbot.turns.envelope` for the contact out of this checkout's own
`DATABASE_URL` and swaps the text and the message id. That is also why it refuses a
non-local `--base-url` without `--i-know`: the database it reads and the backend it posts to
are two different pieces of configuration, and nothing here can check they agree.

**A multi-turn case feeds its own memory forward.** Each turn's `reply.session_patch`
carries `variables`, and the next turn sends it as `previous_conversation_state` - the
harness key the engine honours on a dry run (`engine.HARNESS_KEYS`). That is what makes a
picker sequence ("check stock for X" then "1") checkable without writing session state.

**`cold: true` on a case is the same key with an EMPTY map** (AC-112): the engine reads
`previous_conversation_state` by MEMBERSHIP, so `{}` says "this contact remembers nothing"
and absence says "use the stored row". The script borrows a real contact's envelope, and
that contact has real stored state, so a turn the owner hit cold is not reproducible here
without it. Spell it `cold: true` rather than `previous_conversation_state: {}` - both work
and the flag is the one that survives a YAML round trip unambiguously.

**`--prompt-version` grades a prompt the tenant has not promoted.** A prompt change ships as
a new, UNLABELLED `ai_prompt_versions` row (migrations 475 / 480 / 487 / 490 all do this) and
reaches a customer only when the owner moves the `production` label. That is the right order,
and it leaves a hole in this check: the "before the PR" run would grade the OLD prompt and
report green about vocabulary the new one adds. So the flag pins one version FOR THE RUN, by
putting it on each envelope's `prompt_overrides` harness key - the same key the Prompts
screen's "Run a turn" uses (AC-807), which the engine honours on a DRY RUN ONLY
(`engine._prompt_override` returns None on a live turn whatever the envelope says). Nothing
is promoted and nothing is written; run the file once without the flag and once with it, and
the pair says what the tenant gets today and what it would get after the label moves.

    # the id of the row to pin, and the body it carries
    venv/bin/python -c "from app.database import SessionLocal; from app.models.ai_prompt \
        import AIPromptVersion; db=SessionLocal(); \
        print([(v.id, v.version, len(v.template)) for v in db.query(AIPromptVersion) \
        .filter(AIPromptVersion.name=='chatbot_semantic_parser').all()])"

**`expected_red_until: <branch or slice>` marks a case another lane owns.** A case whose
expectation this lane cannot meet is still worth keeping - it is what measures whether the
lane that owns it did the job - but a permanent red teaches the reader to ignore the
output. Such a case reports `XFAIL` and does NOT count as a failure while it fails, and
reports `XPASS` and DOES count as a failure the moment it passes: the marker is now a lie
and has to be removed. Never reach for it to quieten a case this lane owns.

**The runner owns the lane switches.** `chatbot_business_lane_enabled` and
`chatbot_completed_lanes` decide whether the CRM ANSWERS a turn or delegates it to n8n, and
a delegated turn comes back with an empty reply - which would grade the handoff, not the
answer. So the script reads them, prints them, turns every lane on for the run and restores
the exact values in a `finally`. On a PRODUCTION checkout it refuses to write them at all
(the Settings > Chatbot screen is the only sanctioned way there) and runs against whatever
is already set, after `--production` acknowledges where it is pointed.

**Replay by default (LLM-TEST-COST, 4 Oct 2026).** A graded run is meant to answer from
recorded parses: start the backend with `LLM_CASSETTE_MODE=replay` (see
`app/services/llm_cassette.py`). The run reads the backend's mode off each turn's
`understood` facts and stops after the first turn that was parsed live, unless `--live`
says to spend.

Reads `EXTERNAL_API_KEY` from the environment (or `--api-key`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
# The registry key `--prompt-version` pins. One parser, one key; spelled here rather than
# imported from `head/parser` because this script is core-side tooling and that module is
# inside the chatbot package (AC-002).
PARSER_PROMPT_KEY = "chatbot_semantic_parser"
TURN_PATH = "/api/v1/external/chat/turn"
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)


def _script_session():
    from app.database import SessionLocal

    return SessionLocal()


def _base_envelope(contact: str) -> dict[str, Any]:
    """The contact's most recent envelope, as the shape to borrow, with their phone on it.

    Raises rather than inventing one: a hand-built envelope that happens to be missing a
    field the engine reads fails the turn at `received`, and a check whose failures are its
    own is worse than no check. The PHONE is filled in from `respond_contacts` when the
    borrowed envelope has none - a real webhook always carries it, and the escalation lane's
    assignee read is a 400 without it, which would read as a routing defect.
    """
    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text(
                "SELECT envelope FROM chatbot.turns WHERE contact_respond_id = :c "
                "AND envelope IS NOT NULL ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": str(contact)},
        ).fetchone()
        phone = db.execute(
            text("SELECT phone_number FROM respond_contacts WHERE respond_io_id = :c"),
            {"c": str(contact)},
        ).scalar()
    finally:
        db.close()
    if row is None:
        raise SystemExit(
            f"no chatbot.turns envelope for contact {contact!r} in this checkout's "
            "database - run one turn for that contact first, or pass --contact"
        )
    envelope = dict(row[0])
    stored = envelope.get("contact") or {}
    if not stored.get("phone") and phone:
        envelope["contact"] = {**stored, "phone": phone}
    return envelope


def _envelope_for(
    base: dict[str, Any],
    *,
    contact: str,
    message: str,
    run_id: str,
    parser: Any = None,
    previous_state: Any = None,
    prompt_version: str | None = None,
    quote: str | None = None,
) -> dict[str, Any]:
    """The borrowed envelope with this turn's words in it. Never mutates `base`.

    `quote` (QUOTED-REPLY, 7 Oct 2026) makes the message a WhatsApp reply to a bot message
    carrying that text; the id is synthetic, so the engine resolves it by the text."""
    envelope = json.loads(json.dumps(base))
    # The WIRE shape: respond.io puts a numeric contact id in the webhook body, and this
    # check exists to catch what production would hit. Sending it as a string is what let
    # #874 (`contact_id  Input should be a valid string ... input_type=int`) past the gate.
    wire_contact = int(contact) if str(contact).isdigit() else contact
    envelope["contact"] = {**(envelope.get("contact") or {}), "id": wire_contact}
    envelope.setdefault("message", {})
    envelope["message"]["contact"] = {"id": wire_contact}
    inner = envelope["message"].setdefault("message", {})
    inner["contactId"] = wire_contact
    inner["messageId"] = f"console-check-{uuid.uuid4().hex[:12]}"
    inner["message"] = {"type": "text", "text": message}
    if quote is not None:
        inner["replyTo"] = {
            "id": f"console-quote-{uuid.uuid4().hex[:12]}",
            "sender": {"source": "n8n"},
            "message": {"text": quote},
        }
    else:
        inner.pop("replyTo", None)
    envelope["message"]["event_type"] = "message.received"
    # D14, always both markers: a console check that could answer a real customer is not a
    # check, it is an incident.
    envelope["is_test"] = True
    envelope["test_run_id"] = run_id
    envelope["shadow_of"] = None
    if parser is not None:
        envelope["mock_reformulator_output"] = parser
    else:
        envelope.pop("mock_reformulator_output", None)
    if previous_state is not None:
        envelope["previous_conversation_state"] = previous_state
    else:
        envelope.pop("previous_conversation_state", None)
    if prompt_version:
        # `engine.HARNESS_KEYS`' own `prompt_overrides`, dry-run only by construction.
        envelope["prompt_overrides"] = {PARSER_PROMPT_KEY: str(prompt_version)}
    else:
        envelope.pop("prompt_overrides", None)
    return envelope


def _post(session: Any, url: str, api_key: str, envelope: dict[str, Any], timeout: float) -> dict:
    response = session.post(
        url,
        json={"envelope": envelope},
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=timeout,
    )
    if response.status_code != 200:
        return {"_http_error": f"HTTP {response.status_code}: {response.text[:200]}"}
    return response.json()


_RETRY_WAIT_SECONDS = 20.0


def _is_429(body: dict[str, Any]) -> bool:
    """A turn the parser's own OpenAI call hit a rate limit on - "429" appears either in
    the endpoint's own HTTP error text or in a failed turn's `error` field (the parser
    call's exception message, surfaced verbatim by `run_turn`'s failure path)."""
    return "429" in str(body.get("_http_error") or "") or "429" in str(body.get("error") or "")


def _post_with_pacing(
    session: Any,
    url: str,
    api_key: str,
    envelope: dict[str, Any],
    timeout: float,
    *,
    sleep_seconds: float = 0.0,
    live: bool = True,
) -> dict:
    """`_post`, plus the owner's two pacing rules (8 Sep 2026, a graded run sharing the
    parser's OpenAI key with someone testing live): sleep `sleep_seconds` BEFORE every
    call (so a caller that paces the whole run paces the first turn too), and a SINGLE
    automatic retry, after a fixed 20s wait, of a turn whose error contains "429" - one
    retry only, and it counts as ONE attempt in the report (the caller sees only the
    retry's own result, not two graded turns for one customer message)."""
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    body = _post(session, url, api_key, envelope, timeout)
    if _is_429(body):
        print(f"      (429 from the parser - waiting {_RETRY_WAIT_SECONDS:g}s and retrying once)")
        time.sleep(_RETRY_WAIT_SECONDS)
        body = _post(session, url, api_key, envelope, timeout)
    refusal = _cassette_refusal(body, live)
    if refusal:
        raise SystemExit(refusal)
    return body


def _cassette_refusal(body: dict[str, Any], live: bool) -> str | None:
    """Why this run must stop, or None. LLM-TEST-COST: a scripted run answers from
    recorded parses (the backend in `LLM_CASSETTE_MODE=replay`) unless `--live` says to
    spend. The backend's mode is read off the turn's own `understood` facts - the 200
    body carries no trace, so off the stored row - and at most ONE live turn is paid
    before the run stops."""
    if live or "_http_error" in body:
        return None
    turn_id = body.get("turn_id")
    trace = _row_trace(turn_id)
    if turn_id and not trace:
        # The backend wrote this turn somewhere this script cannot see (another
        # DATABASE_URL), so whether it was replayed cannot be checked: stop, never assume.
        return (
            f"stopped: cannot confirm replay, turn {turn_id} is not in this script's "
            "database (is the backend on another DATABASE_URL?). Pass --live to run unchecked."
        )
    for record in trace:
        if not (isinstance(record, dict) and record.get("stage") == "understood"):
            continue
        facts = record.get("facts") or {}
        if facts.get("parser_bypassed"):
            return None
        mode = facts.get("llm_cassette")
        if mode == "replay":
            if record.get("status") == "failed":
                # A cassette miss: nothing spent, but the run cannot grade anything.
                return f"stopped: replay could not answer this turn: {record.get('error')}"
            return None
        return (
            f"stopped: the backend parsed this turn live (LLM_CASSETTE_MODE="
            f"{mode or 'unset, backend predates LLM-TEST-COST'}), which spends real "
            "OpenAI tokens. Start the backend in replay (LLM_CASSETTE_MODE=replay, or a "
            "harness launcher with no flag), or pass --live to spend on purpose (a "
            "--record backend needs --live too)."
        )
    return None


def _row_trace(turn_id: str | None) -> list[Any]:
    """`chatbot.turns.trace` for one turn, as a list (empty when there is none)."""
    if not turn_id:
        return []
    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text("SELECT trace FROM chatbot.turns WHERE id = :id"), {"id": turn_id}
        ).fetchone()
    finally:
        db.close()
    trace = (row[0] if row is not None else None) or []
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            return []
    return trace if isinstance(trace, list) else []


def _pending_kind(body: dict[str, Any], turn_id: str | None) -> str | None:
    """What this turn LEFT the customer looking at: `variables.open_question.kind`
    (`variables.pending.kind` on a trace recorded before the rename; see `_pending_of`).

    Fixed 13 Sep 2026 (console run 4): this read `response.pending` off the
    `chatbot.turns` row, a key nothing has ever written - so every `pending_kind`
    assertion in every run of every case graded `None`, including turns the trace proves
    were correctly armed. The marker is written by the tail into the session patch, and
    the row keeps it in the trace's `remembered` stage
    (`raw.session_patch.variables.open_question`); the persisted `response.reply` is the
    SEALED customer reply and carries no session state at all.

    The 200 body is tried first - a DRY-RUN turn returns its would-be patch there and
    writes nothing - then the row's trace.
    """
    variables = _next_state(body)
    kind = _pending_of(variables)
    if kind or not turn_id:
        return kind

    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text("SELECT trace FROM chatbot.turns WHERE id = :id"), {"id": turn_id}
        ).fetchone()
    finally:
        db.close()
    trace = (row[0] if row is not None else None) or []
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            return None
    for stage in trace if isinstance(trace, list) else []:
        if not isinstance(stage, dict) or stage.get("stage") != "remembered":
            continue
        patch = (stage.get("raw") or {}).get("session_patch")
        kind = _pending_of((patch or {}).get("variables") if isinstance(patch, dict) else None)
        if kind:
            return kind
    return None


def _pending_of(variables: Any) -> str | None:
    """`variables.open_question.kind` as a string, or None - the one shape both sources use.

    `open_question` is the key the tail writes (`turn/tail.py`, the re-architected turn);
    `pending` was its name before that and is still read so an older recorded trace grades.
    Measured 6 Oct 2026 (DO-LIST-REPLY-LABEL sandbox replay): with only `pending` read here,
    every `pending_kind` expectation graded None on turns whose next turn answered the
    question, so the check could never see an armed question.
    """
    if not isinstance(variables, dict):
        return None
    pending = variables.get("open_question")
    if not isinstance(pending, dict):
        pending = variables.get("pending")
    kind = pending.get("kind") if isinstance(pending, dict) else None
    return str(kind) if kind else None


# Every branch the router can decide. The CRM only ANSWERS a turn whose branch is in
# `chatbot_completed_lanes` with `chatbot_business_lane_enabled` on (AC-809/AC-810); off
# either one the head DELEGATES to n8n and every reply comes back empty, so the check would
# grade the handoff instead of the answer.
ALL_LANES = (
    "access_denied",
    "escalate_offer",
    "out_of_scope",
    "ideate",
    "offer_hold",
    "escalation_declined",
    "check_promotion",
    "low_signal",
    "clarify_menu",
    "not_supported",
    "stock_denied",
    "demand_qty",
    "business_query",
)


def _read_switches() -> tuple[bool, Any]:
    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text(
                "SELECT chatbot_business_lane_enabled, chatbot_completed_lanes "
                "FROM system_settings LIMIT 1"
            )
        ).fetchone()
    finally:
        db.close()
    return (bool(row[0]), row[1]) if row is not None else (False, [])


def _write_switches(enabled: bool, lanes: Any) -> None:
    """The two lane switches, written to the ONE `system_settings` row.

    No WHERE, and that is correct rather than an oversight: `system_settings` is a singleton
    - one row per install, read everywhere as `query(SystemSetting).first()` - so there is no
    key to filter on and an UPDATE touches exactly that row. Adding a `WHERE id = ...` here
    would need an id this script has no business knowing, and the restore in `_lanes_on`
    depends on this statement reaching the same row the read came from.
    """
    from sqlalchemy import text

    db = _script_session()
    try:
        db.execute(
            text(
                "UPDATE system_settings SET chatbot_business_lane_enabled = :e, "
                "chatbot_completed_lanes = CAST(:l AS jsonb)"
            ),
            {"e": enabled, "l": json.dumps(list(lanes))},
        )
        db.commit()
    finally:
        db.close()


@contextmanager
def _lanes_on(production: bool) -> Any:
    """Business lane on and every branch answered FOR THE RUN, restored in a `finally`.

    The restore is the whole point: this is shared dev state, and a check that left the
    switches flipped would change how the next person's turns behave without saying so. The
    `finally` runs on a failed case, a raised exception and a Ctrl-C alike.

    Refused on a PRODUCTION checkout, the same rule `chatbot_load.py` states: the Settings >
    Chatbot screen is the only sanctioned way to flip a live tenant's lanes, and a script
    that writes `system_settings` there is one typo from answering real customers with a
    half-promoted engine. On production the switches must already be right; the check reads
    them, says so, and runs against them unchanged.
    """
    before_enabled, before_lanes = _read_switches()
    print(f"lane switches before: enabled={before_enabled} lanes={before_lanes!r}")
    if production:
        if not before_enabled or not set(ALL_LANES) & set(before_lanes or []):
            raise SystemExit(
                "this checkout is production and the chatbot lanes are not on. Flip them "
                "on the Settings > Chatbot screen and re-run; this script never writes "
                "system_settings on a production database."
            )
        yield
        return
    _write_switches(True, ALL_LANES)
    print(f"lane switches for the run: enabled=True lanes={len(ALL_LANES)}")
    try:
        yield
    finally:
        _write_switches(before_enabled, before_lanes or [])
        after = _read_switches()
        print(f"lane switches restored: enabled={after[0]} lanes={after[1]!r}")


def _customer_words(body: dict[str, Any]) -> str:
    """Everything this turn would actually say to the customer, in one string.

    NOT just `reply.text`. The escalation lane's assignment arm composes no reply at all -
    `escalate-catalog` carries `includeResponse: false` for `out_of_scope` - and sends its
    two sentences as `send_message` ACTIONS instead, so grading `reply.text` alone would
    call a turn silent that says two things, and would let a genuinely silent turn pass on a
    negative expectation.
    """
    parts = [((body.get("reply") or {}).get("text")) or ""]
    for action in body.get("actions") or []:
        if isinstance(action, dict) and isinstance(action.get("text"), str):
            parts.append(action["text"])
    return "\n".join(p for p in parts if p)


def _parser_verdict(body: dict[str, Any], turn_id: str | None) -> dict[str, Any]:
    """The parser's own emission for this turn, off the trace's `understood` stage.

    Read from the TRACE, never from the reply: a parser expectation is about what the
    model understood, and the reply is several deterministic stages downstream of that.
    The 200 body is tried first (a dry run carries its trace inline), then the row.
    """
    for record in body.get("trace") or []:
        if isinstance(record, dict) and record.get("stage") == "understood":
            derived = (record.get("raw") or {}).get("derived")
            if isinstance(derived, dict):
                return derived
    if not turn_id:
        return {}

    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text("SELECT trace FROM chatbot.turns WHERE id = :id"), {"id": turn_id}
        ).fetchone()
    finally:
        db.close()
    trace = (row[0] if row is not None else None) or []
    if isinstance(trace, str):
        try:
            trace = json.loads(trace)
        except ValueError:
            return {}
    for record in trace if isinstance(trace, list) else []:
        if not isinstance(record, dict) or record.get("stage") != "understood":
            continue
        derived = (record.get("raw") or {}).get("derived")
        if isinstance(derived, dict):
            return derived
    return {}


def _grade_parser(wanted: dict[str, Any], got: dict[str, Any]) -> list[str]:
    """Every `expect.parser` key that the emission did not match (AC-1551).

    EQUALITY per key, not containment: "the parser emitted `document: ["SO", "DO"]`
    where the case wanted `["DO"]`" is exactly the class of drift these cases exist to
    catch, and a subset test would call it a pass. A key the emission does not carry at
    all is reported as absent rather than as null, because the two mean different things
    about the prompt (a key it was never asked for versus one it declined to fill).

    One other form: `{one_of: [...]}` passes on any listed value, for a key where more
    than one emission is correct (#1262 N9: T5's `domain_hint` may be null or "order").
    OBJECTS are the one exception (#1323): a dict names only the keys it grades, so an
    entity can be pinned by `canonical_code` / `hint` / `current_message` without also
    spelling out every flag the parser stamps on it, and `escalation` by its one verdict.
    Lists stay whole: same length, same order, each item graded by this same rule.
    """
    failures: list[str] = []
    if not got:
        return [f"no parser verdict on the trace to grade {sorted(wanted)} against"]
    for key, expected in (wanted or {}).items():
        if key not in got:
            failures.append(f"parser emitted no {key!r} at all, expected {expected!r}")
            continue
        actual = got[key]
        if isinstance(expected, dict) and set(expected) == {"one_of"}:
            if actual not in (expected["one_of"] or []):
                failures.append(f"parser {key} is {actual!r}, expected one of {expected['one_of']!r}")
        elif not _parser_value_matches(expected, actual):
            failures.append(f"parser {key} is {actual!r}, expected {expected!r}")
    return failures


def _parser_value_matches(expected: Any, actual: Any) -> bool:
    """Equality, except that an expected dict grades only the keys it names."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and _parser_value_matches(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_parser_value_matches(e, a) for e, a in zip(expected, actual))
        )
    return actual == expected


def _grade(
    expect: dict[str, Any],
    body: dict[str, Any],
    pending: str | None,
    parser_verdict: dict[str, Any] | None = None,
) -> list[str]:
    """Every expectation that did NOT hold, as sentences. Empty list is a pass."""
    failures: list[str] = []
    if "_http_error" in body:
        return [body["_http_error"]]
    reply = _customer_words(body)
    branch = body.get("branch_kind")

    # SILENCE IS A FAILURE, always and without being asked for. A turn that says nothing
    # satisfies every negative expectation on the case vacuously, so a check that passes it
    # is worse than no check.
    if not reply.strip():
        failures.append("the turn would say nothing to the customer")

    wanted_branch = expect.get("branch_kind")
    if wanted_branch and branch != wanted_branch:
        failures.append(f"branch_kind is {branch!r}, expected {wanted_branch!r}")
    for needle in expect.get("reply_contains") or []:
        if str(needle).lower() not in reply.lower():
            failures.append(f"reply does not contain {needle!r}")
    for needle in expect.get("reply_not_contains") or []:
        if str(needle).lower() in reply.lower():
            failures.append(f"reply contains {needle!r} and must not")
    if expect.get("no_uuid") is True:
        found = UUID_RE.search(reply)
        if found:
            failures.append(f"reply prints a uuid: {found.group(0)}")
    wanted_pending = expect.get("pending_kind")
    if wanted_pending and pending != wanted_pending:
        failures.append(f"pending kind is {pending!r}, expected {wanted_pending!r}")
    wanted_parser = expect.get("parser")
    if wanted_parser:
        failures += _grade_parser(wanted_parser, parser_verdict or {})
    return failures


def _initial_state(case: dict[str, Any]) -> Any:
    """The `previous_conversation_state` this case's FIRST turn sends, or None for absent.

    `cold: true` is `{}`, and `{}` is not the same as absent: the engine tests MEMBERSHIP
    of the key (`engine._harness_keys_present`), so an empty map says "this contact
    remembers nothing" while an absent key leaves the stored session row in place. An
    explicit `previous_conversation_state:` in the file still wins, so a case can inject
    real memory instead.
    """
    if "previous_conversation_state" in case:
        return case["previous_conversation_state"]
    return {} if case.get("cold") is True else None


def _turns_of(case: dict[str, Any]) -> list[dict[str, Any]]:
    """A single-turn case and a `turns:` case, as one list of turns."""
    if isinstance(case.get("turns"), list):
        return [t for t in case["turns"] if isinstance(t, dict)]
    return [{"text": case.get("text"), "expect": case.get("expect") or {}}]


def _next_state(body: dict[str, Any]) -> Any:
    """What the NEXT turn of this case remembers: the seal's own `variables`."""
    patch = (body.get("reply") or {}).get("session_patch")
    if not isinstance(patch, dict):
        patch = body.get("session_patch")
    if not isinstance(patch, dict):
        return None
    variables = patch.get("variables")
    return variables if isinstance(variables, dict) else patch


def main(argv: list[str] | None = None) -> int:
    import requests
    import yaml

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="?", help="the YAML case file (omit with --say)")
    parser.add_argument(
        "--say",
        action="append",
        default=None,
        metavar="TEXT",
        help=(
            "an ad-hoc turn. Repeatable, and the repeats are ONE conversation: each reply's "
            "session variables feed the next turn exactly as a YAML `turns:` list does. "
            "Nothing is graded - the reply is printed for a human to read."
        ),
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--contact", default=None, help="default respond.io contact id")
    parser.add_argument("--api-key", default=os.getenv("EXTERNAL_API_KEY"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help=(
            "pace turns by sleeping this long between each one (default 0 = as fast as "
            "the server answers). Owner ruling, 8 Sep 2026: a graded run shares the "
            "parser's OpenAI key with anyone testing live at the same time, and a burst "
            "of 58 back-to-back turns hit 429 on 6 of them plus 2 MCP timeouts."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "allow live parser calls (LLM-TEST-COST). Without it the run stops after the "
            "first turn the backend did not answer from a recorded cassette "
            "(LLM_CASSETTE_MODE=replay on the backend)."
        ),
    )
    parser.add_argument(
        "--mock-parser",
        action="store_true",
        help="use each case's own `parser:` block instead of calling the model",
    )
    parser.add_argument(
        "--prompt-version",
        default=None,
        help=(
            "pin one `ai_prompt_versions.id` of `chatbot_semantic_parser` for this run, so "
            "an UNPROMOTED version can be graded before the label moves. Dry-run only by "
            "construction; nothing is promoted and nothing is written."
        ),
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="acknowledge a production checkout: the lane switches are read, never written",
    )
    parser.add_argument(
        "--i-know",
        action="store_true",
        help="allow a non-local --base-url (the database read is this checkout's own)",
    )
    args = parser.parse_args(argv)

    if not args.api_key:
        print("EXTERNAL_API_KEY is not set (or pass --api-key)", file=sys.stderr)
        return 2
    host = urlparse(args.base_url).hostname or ""
    if host not in LOCAL_HOSTS and not args.i_know:
        print(
            f"--base-url points at {host!r}, which is not local. The envelopes are read "
            "from THIS checkout's DATABASE_URL, and nothing here can check the two match; "
            "pass --i-know once you have.",
            file=sys.stderr,
        )
        return 2

    run_id = f"console-check-{int(time.time())}"
    url = args.base_url.rstrip("/") + TURN_PATH
    session = requests.Session()
    session.trust_env = False

    if args.say:
        return _run_ad_hoc(args, session, url, run_id)
    if not args.cases:
        print("give a YAML case file, or --say \"<text>\"", file=sys.stderr)
        return 2
    with open(args.cases, encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    default_contact = args.contact or document.get("contact")
    cases = [c for c in (document.get("cases") or []) if isinstance(c, dict)]
    if not cases:
        print(f"{args.cases} has no cases", file=sys.stderr)
        return 2

    from app.config import settings as app_settings

    is_production = app_settings.environment == "production"
    if is_production and not args.production:
        print(
            "this checkout's ENVIRONMENT is production - pass --production to run against "
            "it (the lane switches are then read, never written).",
            file=sys.stderr,
        )
        return 2


    pinned = (
        f"  parser prompt pinned to version {args.prompt_version}"
        if args.prompt_version
        else "  parser prompt: whatever the `production` label points at"
    )
    print(f"{run_id}  {len(cases)} cases against {args.base_url}{pinned}")
    with _lanes_on(is_production):
        failed = _run_cases(cases, session, url, args, default_contact, run_id)

    print(f"\n{len(cases) - failed} passed, {failed} failed  ({run_id})")
    return 1 if failed else 0


def _latest_live_contact() -> str | None:
    """The contact of the most recent chatbot turn - who the bot last spoke to.

    The default for `--say`, because the envelope borrowing already needs a contact with a
    stored turn and this is the one most likely to have one.
    """
    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text(
                "SELECT contact_respond_id FROM chatbot.turns WHERE envelope IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).fetchone()
    finally:
        db.close()
    return str(row[0]) if row else None


def _trace_line(turn_id: str | None) -> str:
    """One line of WHY: the tool, the rungs, the reveals - off the persisted trace."""
    if not turn_id:
        return "trace: (no turn id)"
    from sqlalchemy import text

    db = _script_session()
    try:
        row = db.execute(
            text("SELECT trace FROM chatbot.turns WHERE id = :id"), {"id": turn_id}
        ).fetchone()
    finally:
        db.close()
    events = [e for e in ((row[0] if row else None) or []) if isinstance(e, dict) and e.get("kind")]
    parts: list[str] = []
    for event in events:
        if event["kind"] == "tool":
            args = {
                k: v for k, v in (event.get("args") or {}).items()
                if k not in ("view", "contact_id", "space_id", "_diagnostics")
            }
            parts.append(f"tool={event.get('name')} args={json.dumps(args, default=str)}")
        elif event["kind"] == "crossdomain":
            parts.append(f"rung={event.get('rung')}({event.get('rows')} rows)")
        elif event["kind"] == "reveals":
            dropped = event.get("dropped") or []
            if dropped:
                parts.append("reveals dropped=" + ",".join(str(d) for d in dropped))
    return "trace: " + ("  ".join(parts) if parts else "no tool call")


def _run_ad_hoc(args, session, url, run_id) -> int:
    """`--say` turns as ONE conversation. Nothing is graded; the reply is printed.

    Reuses the YAML path's own two functions - `_envelope_for` builds the borrowed
    envelope (so `is_test` / `test_run_id` are set the same way, D14) and `_next_state`
    feeds each reply's session variables into the next turn, which is exactly what a
    `turns:` list does. So an ad-hoc conversation and a multi-turn case exercise the same
    engine path, and neither writes anything outside `chatbot.turns`.
    """
    contact = str(args.contact or _latest_live_contact() or "")
    if not contact:
        print("no contact: pass --contact, or run one turn first", file=sys.stderr)
        return 2
    base = _base_envelope(contact)
    previous_state = None
    with _lanes_on(False):
        for index, text_in in enumerate(args.say, start=1):
            envelope = _envelope_for(
                base,
                contact=contact,
                message=text_in,
                run_id=run_id,
                previous_state=previous_state,
                prompt_version=args.prompt_version,
            )
            body = _post_with_pacing(
                session,
                url,
                args.api_key,
                envelope,
                args.timeout,
                sleep_seconds=args.sleep_seconds,
                live=args.live,
            )
            print(f"\n--- turn {index}  contact {contact} ---")
            print(f"> {text_in}")
            if "_http_error" in body:
                print(f"  {body['_http_error']}")
                return 1
            print(f"  branch_kind: {body.get('branch_kind')}")
            reply = (body.get("reply") or {}).get("text")
            print(f"  reply.text: {reply if reply else '(none - this lane speaks in actions)'}")
            for action in body.get("actions") or []:
                if isinstance(action, dict) and action.get("kind") == "send_message":
                    print(f"  send_message: {action.get('text')}")
                    if action.get("quick_replies"):
                        print(f"  quick_replies: {action['quick_replies']}")
            print("  " + _trace_line(body.get("turn_id")))
            previous_state = _next_state(body)
    return 0


def _run_cases(cases, session, url, args, default_contact, run_id) -> int:
    """Every case, in file order. Returns how many failed."""
    failed = 0
    for case in cases:
        name = str(case.get("name") or case.get("text") or "case")
        contact = str(case.get("contact") or default_contact or "")
        if not contact:
            print(f"FAIL  {name}\n      - no contact (pass --contact or set one in the file)")
            failed += 1
            continue
        base = _base_envelope(contact)
        previous_state = _initial_state(case)
        case_failures: list[str] = []
        last_line = ""
        last_branch = None
        for index, turn in enumerate(_turns_of(case)):
            envelope = _envelope_for(
                base,
                contact=contact,
                message=str(turn.get("text") or ""),
                run_id=run_id,
                parser=(turn.get("parser") or case.get("parser")) if args.mock_parser else None,
                previous_state=previous_state,
                prompt_version=args.prompt_version,
                quote=turn["quote"] if "quote" in turn else case.get("quote"),
            )
            body = _post_with_pacing(
                session,
                url,
                args.api_key,
                envelope,
                args.timeout,
                sleep_seconds=args.sleep_seconds,
                live=args.live,
            )
            pending = _pending_kind(body, body.get("turn_id"))
            reply = _customer_words(body)
            last_branch = body.get("branch_kind")
            last_line = reply.replace("\n", " ")[:120]
            prefix = f"turn {index + 1}: " if len(_turns_of(case)) > 1 else ""
            # Only read when a case asks for it: the fallback is a database round trip
            # per turn, and most cases grade the reply alone.
            wanted = turn.get("expect") or {}
            verdict = (
                _parser_verdict(body, body.get("turn_id")) if wanted.get("parser") else None
            )
            case_failures += [prefix + f for f in _grade(wanted, body, pending, verdict)]
            previous_state = _next_state(body)
        owned_by = str(case.get("expected_red_until") or "").strip()
        if owned_by:
            # Inverted on purpose (see the module docstring): failing is the expected
            # state, passing is the news.
            verdict = "XFAIL" if case_failures else "XPASS"
            is_failure = not case_failures
            suffix = f"  [{'expected red until ' + owned_by if case_failures else 'NOW PASSES - remove expected_red_until: ' + owned_by}]"
        else:
            verdict = "FAIL" if case_failures else "PASS"
            is_failure = bool(case_failures)
            suffix = ""
        failed += 1 if is_failure else 0
        print(f"{verdict}  {name:<44} branch={last_branch}  {last_line!r}{suffix}")
        for failure in case_failures:
            print(f"      - {failure}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
