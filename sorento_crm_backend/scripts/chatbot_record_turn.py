#!/usr/bin/env python3
"""Record `chatbot.turns` rows into `tests/chatbot/replay_turns/<group>/<slug>.json`
for the S6 turn-replay gate (AC-1590, PLAN-chatbot-turn-rearch.md "Turn replay (the
gate)").

**Read-only.** Every query against `--db-url` is a SELECT; this script never writes to
the source database, and it never writes anywhere except under
`tests/chatbot/replay_turns/`.

    venv/bin/python scripts/chatbot_record_turn.py --db-url "$SOURCE_URL" \\
        --turn-id 19a27cfb-1d25-440e-9037-9ef9dc7b2577 --group contract --slug \\
        line-001-stock-by-location

    venv/bin/python scripts/chatbot_record_turn.py --db-url "$SOURCE_URL" \\
        --contact 900000008 --since 2026-09-15 --until 2026-09-16 --group console \\
        --slug-prefix owner-15sep

    venv/bin/python scripts/chatbot_record_turn.py --db-url "$SOURCE_URL" \\
        --test-run-id console-check-1789442546 --group console --slug-prefix run-1789442546

    venv/bin/python scripts/chatbot_record_turn.py --db-url "$SOURCE_URL" \\
        --branch-kind business_query --sample 40 --group prod_sample --is-test false

One JSON file per turn (`--contact`/`--test-run-id`/`--branch-kind` modes write one file
per matching row, numbered `<prefix>-<NNN>.json`; `--turn-id` writes exactly the file
named by `--slug`).

**Both trace shapes read the same way.** Measured against real rows in the 0915 prod
copy and this lane's own dev-stack turns (see `scripts/chatbot_record_turn.py`'s own
tests / the S6 handoff): `main` (pre-rearch) turns and turns run by this lane's engine
both put the parser's raw JSON at `trace[stage="understood"].raw.parser_raw` - the extra
S3+ verdict keys (`document`, `status`, `anaphora`) are simply absent on an older row,
never renamed or moved. No branch on trace shape is needed for the verdict read. Tool
calls are the trace entries with no `stage` and `kind == "tool"` in both shapes
(`{at, ms, kind, name, args, envelope}`); the `access` stage's own `raw` is the
`check_access()` return shape verbatim in both shapes too.

**Two fields beyond the brief's literal JSON shape, recorded for replay to work on a
BLANK test schema rather than a prod copy** (ambiguity flagged to the captain - the
brief's item 1 JSON shape names only `tool_results`; this script also writes `access`
and `resolutions`):

- `access`: the `access` stage's raw `check_access()` payload, so replay stubs
  `engine.check_access` with the SAME grant the original turn actually had, rather than
  a generic `stub_access()` default that would silently change which fields a turn was
  allowed to see.
- `resolutions`: derived, not captured directly (no trace event carries it verbatim) -
  built by pairing each verdict entity (`raw`/`canonical_code`) with any UUID that
  appears in a later tool call's args under a `*_id`/`*_ids` key. This is what lets
  `test_turn_replay.py` stub `ResolveGateServices.resolve_entity` (the seam
  `tests/chatbot/test_s6c_answer_lane.py::_stub_bundle` already uses for the same
  reason) with the SAME resolution the original turn got, instead of needing this
  lane's blank private schema to hold a matching row for every product/customer code
  a real prod turn ever mentioned - which it cannot, by the "CI's database has no
  data" rule (LESSONS-LEARNT). A token with no matching UUID anywhere downstream is
  recorded as unresolved. **This is a best-effort reconstruction, not a captured
  fact** - a domain whose tool never echoes the resolved UUID back in its args
  (none observed in the corpus this script has recorded so far) would resolve to
  nothing here; flagged for the captain rather than silently assumed complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid as uuid_mod
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

REPLAY_ROOT = Path(__file__).resolve().parent.parent / "tests" / "chatbot" / "replay_turns"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    value = _SLUG_RE.sub("-", value.lower()).strip("-")
    return value or "case"


def _stage(trace: list[dict[str, Any]] | None, name: str) -> dict[str, Any] | None:
    for entry in trace or []:
        if entry.get("stage") == name:
            return entry
    return None


def _tool_events(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every trace entry with no `stage` and `kind == "tool"` (both trace shapes)."""
    events = []
    for entry in trace or []:
        if entry.get("stage") is None and entry.get("kind") == "tool":
            events.append(entry)
    return events


def _verdict_of(trace: list[dict[str, Any]] | None) -> dict[str, Any]:
    understood = _stage(trace, "understood")
    if understood is None:
        return {}
    raw = understood.get("raw") or {}
    return raw.get("parser_raw") or raw.get("parsed") or {}


def _access_of(trace: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    access = _stage(trace, "access")
    return access.get("raw") if access else None


# The per-turn switches `engine.py::_read_switches`/`turn/policy.py::load_policy` read off
# the `system_settings` singleton - S6 tester triage cluster 6: `test_turn_replay.py` never
# set these on the private test DB, so every replay ran under the hard-coded ALL-FALSE
# `_TurnSwitches()` default regardless of what the original live turn actually ran under,
# making `stock_denied`/`demand_qty` structurally unreachable in replay. No trace stage
# captures these (checked: neither `received` nor `understood` carries them), and the
# singleton has no history, so this is a best-effort "what is it set to NOW" read at record
# time, not the value at the turn's original instant - `switches_source` says which.
_SWITCH_COLUMNS = (
    "chatbot_stock_denial_enabled",
    "chatbot_business_lane_enabled",
    "chatbot_ordering_enabled",
    "chatbot_unsupported_domains",
    "chatbot_completed_lanes",
    "chatbot_crossdomain_ladder",
    "chatbot_tier_order",
)

_SWITCH_DEFAULTS: dict[str, Any] = {
    "chatbot_stock_denial_enabled": False,
    "chatbot_business_lane_enabled": False,
    "chatbot_ordering_enabled": False,
    "chatbot_unsupported_domains": None,
    "chatbot_completed_lanes": None,
    "chatbot_crossdomain_ladder": None,
    "chatbot_tier_order": None,
}


def _source_switches(conn: Any) -> dict[str, Any]:
    """One read of the source DB's `system_settings` singleton, reused for every turn
    this script invocation records (the columns above are not per-turn history - a
    singleton row only ever has ONE current value)."""
    row = conn.execute(
        text(f"SELECT {', '.join(_SWITCH_COLUMNS)} FROM system_settings LIMIT 1")
    ).first()
    if row is None:
        return {**_SWITCH_DEFAULTS, "switches_source": "no_source_row_defaults"}
    values = {col: getattr(row, col) for col in _SWITCH_COLUMNS}
    values["switches_source"] = "source_db_at_record_time"
    return values


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _entity_uuids_from_tool_args(tool_events: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for event in tool_events:
        args = event.get("args") or {}
        for key, value in args.items():
            if not (key.endswith("_id") or key.endswith("_ids")):
                continue
            values = value if isinstance(value, list) else [value]
            for v in values:
                if _is_uuid(v):
                    found.add(v)
    return found


def _derive_resolutions(verdict: dict[str, Any], tool_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-effort `resolve_entity` response, from the verdict's entities and the
    UUIDs the recorded tool calls actually used (see module docstring).

    Shape MUST match the real `resolve-entity` HTTP response `resolve_gate.py` (and
    `gate.py`) consume: `{"resolutions": [{"token", "resolved", "matches": [{"uuid",
    "entity_type", "canonical_code"}, ...]}], ...}` - ONE entry per token, each
    carrying a LIST of matches, never a flat token->match record. Found this session
    (tester, 16 Sep 2026): the prior shape (`{"token", "uuid", "entity_type",
    "canonical_code"}` with no `matches` wrapper) fed straight through
    `_install_stubs`'s `_fake_resolve_entity` stub made every recorded resolution
    read back as ZERO matches (`resolve_gate.py:373`'s own `jsc.get(resolutions[0],
    "matches")` finds nothing on a flat dict), so a turn that resolved a real
    product live replayed as if nothing had resolved at all - the narrower asked to
    narrow instead of fetching, and `route()`'s `_ASK_BRANCH.get(kind,
    "clarify_menu")` catch-all turned that into a `branch_kind` divergence on
    almost every case with a confident entity, corpus-wide.
    """
    entity_uuids = sorted(_entity_uuids_from_tool_args(tool_events))
    entities = verdict.get("entities") or []
    resolutions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for idx, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        token = entity.get("canonical_code") or entity.get("raw")
        # No captured link from a token to ITS OWN uuid (see docstring): pair
        # positionally against the recorded tool uuids as a best effort, which is
        # exact for the (overwhelmingly common) single-entity turn and approximate
        # for a multi-entity one - flagged, not hidden.
        if idx < len(entity_uuids):
            resolutions.append(
                {
                    "token": token,
                    "resolved": True,
                    "matches": [
                        {
                            "uuid": entity_uuids[idx],
                            "entity_type": entity.get("hint"),
                            "canonical_code": entity.get("canonical_code") or entity.get("raw"),
                        }
                    ],
                }
            )
        elif token:
            resolutions.append({"token": token, "resolved": False, "matches": []})
            unresolved.append(token)
    return {
        "tokens": [e.get("raw") for e in entities if isinstance(e, dict)],
        "resolutions": resolutions,
        "unresolved_tokens": unresolved,
    }


def _pending_of(trace: list[dict[str, Any]], row: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The ACTUAL open question this turn left, from the `remembered` stage's own
    `session_patch` - never from `reply.result_set`, which also carries a plain
    multi-row ANSWER listing (measured: a real 11-row stock answer with no question
    at all still populates `reply.result_set`, and grading against it produced two
    false divergences before this fix - `git log` on this script for the before/
    after). Two trace shapes, two nesting paths for the SAME fact (main's pre-rearch
    `session_patch.variables.pending`, this lane's own `session_patch.open_question`
    - measured on real rows of each shape); whichever is present wins, and a case
    genuinely asking nothing records `pending: null`, not an empty dict.

    Second source (T2, coder-7 cluster report, 16 Sep 2026): a CANNED lane
    (`escalate_offer`, `demand_qty`, `clarify_menu`-as-domain-menu, ...) never emits a
    `remembered` trace stage at all - `engine.py`'s canned-lane arm (~L1596-1630)
    returns straight from `_complete_canned_lane`, which calls no
    `_record_memory_trace` - measured: 122/122 sampled escalation-offer turns in
    `focus_full` have NO `remembered` stage even though every one of them opened a
    real question, so this function returned `None` for all of them and the recorded
    `expected.pending` read `null` against a real carried pending (finding 2a made this
    visible - the harness only compares `pending` when EITHER side is non-null, and
    before 2a's fix `TurnResult.session_patch` was always None too, so the actual side
    silently read `None` as well). `reply["quick_replies"]` (n8n's comma-joined option-
    label string, `_quick_replies_of`/`compile_state`, AC-507) is non-null EXACTLY when
    a question was asked - unlike `result_set`, which the docstring above already ruled
    out for false-positiving on a plain multi-row answer - so it is a precise second
    source. `kind` is left `null` from this source (not recoverable from the string);
    `test_turn_replay.py::_compare` grades `option_labels` only, never `kind`.
    """
    remembered = _stage(trace, "remembered")
    if remembered is not None:
        session_patch = (remembered.get("raw") or {}).get("session_patch") or {}
        open_question = session_patch.get("open_question")
        if open_question is None:
            open_question = (session_patch.get("variables") or {}).get("pending")
        if open_question:
            options = (
                open_question.get("options")
                or open_question.get("roster")
                or open_question.get("candidates")
                or []
            )
            labels = [
                (o.get("label") or o.get("code") or o.get("name"))
                for o in options
                if isinstance(o, dict)
            ]
            return {
                "kind": open_question.get("kind") or open_question.get("type"),
                "option_labels": labels,
            }

    if row is not None:
        reply = ((row.get("response") or {}).get("reply")) or {}
        quick_replies = reply.get("quick_replies")
        if quick_replies:
            labels = [s.strip() for s in str(quick_replies).split(",") if s.strip()]
            if labels:
                return {"kind": None, "option_labels": labels}

    return None


def _expected_of(row: dict[str, Any], trace: list[dict[str, Any]], tool_events: list[dict[str, Any]]) -> dict[str, Any]:
    response = row.get("response") or {}
    actions = response.get("actions") or []
    reply = response.get("reply") or {}
    tools = []
    entity_ids: set[str] = set()
    for event in tool_events:
        args = event.get("args") or {}
        tools.append({"tool": event.get("name"), "args_keys": sorted(args.keys())})
        for key, value in args.items():
            if key.endswith("_id") or key.endswith("_ids"):
                values = value if isinstance(value, list) else [value]
                entity_ids.update(v for v in values if _is_uuid(v))
    action_kinds = [a.get("kind") for a in actions if isinstance(a, dict)]
    canned = []
    text_value = reply.get("text")
    return {
        "branch_kind": row.get("branch_kind"),
        "tools": tools,
        "entity_ids": sorted(entity_ids),
        "action_kinds": action_kinds,
        "pending": _pending_of(trace, row),
        "focus_after": None,  # not captured on a dry-run/test row - see script docstring
        "canned": canned,
        "text": text_value,
    }


_BACKEND_ID_URL_RE = re.compile(r"^https?://\S+$")


def _scrub_assignee(assignee: dict[str, Any] | None) -> None:
    """The staff member handling a contact - a FIXED placeholder ("ZZT-agent"), never
    id-derived like a customer contact, because `_scrub_contact` below would produce
    "ZZT-None" for the many assignee rows recorded with `id: null`. Its own function
    so `_scrub_contact`'s internal `contact.get("assignee")` call and
    `_scrub_nested_pii`'s key-aware recursion (an assignee found nested with no
    parent "contact" wrapper, e.g. `envelope.media.message.contact.assignee`) apply
    the exact same rule rather than two independently-drifting copies."""
    if not isinstance(assignee, dict):
        return
    assignee_id = assignee.get("id")
    if "firstName" in assignee:
        assignee["firstName"] = "ZZT-agent"
    if "lastName" in assignee:
        assignee["lastName"] = ""
    if assignee.get("email"):
        assignee["email"] = f"zzt-agent-{assignee_id}@example.invalid" if assignee_id else "zzt-agent@example.invalid"


_FAKE_ID_PREFIX = "900000"
_CONTACT_ID_KEYS = ("contactId", "contact_id", "respond_io_id", "respond_id", "contact_respond_id")


def _fake_contact_id(real_id: Any) -> str:
    """The one stable fake id every contact-id position of a recording derives from:
    twelve digits, `900000` then six digits taken from a hash of the real id. The same
    real id always maps to the same fake one across runs, a fake id maps to itself
    (so scrubbing twice changes nothing), and `+60` plus it is a fake phone the PII
    guard accepts (a zero run straight after the prefix). No id keeps the `None` label."""
    if real_id is None:
        return "None"
    text = str(real_id)
    if len(text) == 12 and text.startswith(_FAKE_ID_PREFIX):
        return text
    digest = hashlib.sha256(text.encode()).hexdigest()
    return _FAKE_ID_PREFIX + f"{int(digest, 16) % 1000000:06d}"


_MEDIA_HOST = "https://cdn.example.invalid/"
_MEDIA_PARENT_KEYS = ("attachment", "media")
_MEDIA_URL_KEYS = ("url", "source_url")


def _collect_real_ids(node: Any, found: set[str], *, key: str | None = None) -> None:
    """Every real contact id text inside a raw row: id-named keys, plus `id` of a
    contact-shaped dict. Digit strings of six or more only (so `id: 3` never counts)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (str, int)) and not isinstance(v, bool):
                is_id = k in _CONTACT_ID_KEYS or (k == "id" and key in ("contact", None) and "firstName" in node)
                if is_id and str(v).isdigit() and len(str(v)) >= 6:
                    found.add(str(v))
            _collect_real_ids(v, found, key=k)
    elif isinstance(node, list):
        for item in node:
            _collect_real_ids(item, found, key=key)


def _final_scrub(node: Any, real_ids: set[str], media_map: dict[str, str], *, under: bool = False) -> Any:
    """Last pass over a whole recorded turn: inbound media links (`url` / `source_url`
    under an `attachment` or `media` key) become a placeholder-host link, and every
    remaining occurrence of a real id inside any string becomes its fake."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            child_under = under or k in _MEDIA_PARENT_KEYS
            node[k] = _final_scrub(v, real_ids, media_map, under=child_under)
        return node
    if isinstance(node, list):
        return [_final_scrub(i, real_ids, media_map, under=under) for i in node]
    if isinstance(node, str):
        for url in sorted(media_map, key=len, reverse=True):
            node = node.replace(url, media_map[url])
        for real in real_ids:
            node = re.sub(rf"(?<!\d){re.escape(real)}(?!\d)", _fake_contact_id(real), node)
    return node


def _collect_media_urls(node: Any, out: dict[str, str], under: bool = False) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            child_under = under or k in _MEDIA_PARENT_KEYS
            if child_under and k in _MEDIA_URL_KEYS and isinstance(v, str) and v:
                out.setdefault(v, "")
            _collect_media_urls(v, out, child_under)
    elif isinstance(node, list):
        for i in node:
            _collect_media_urls(i, out, under)


def _scrub_contact(contact: dict[str, Any] | None) -> None:
    """One contact-shaped dict, in place - the shared body `_scrub_pii` and
    `_scrub_nested_pii` both call, so a contact found nested three levels deep inside
    a tool result gets exactly the same treatment as the envelope's own top-level one
    (security review nit, 16 Sep 2026: a real staff assignee - name, `@foundryx.my`
    email - was found unscrubbed inside a `tool_results[].envelope`, not the
    top-level `envelope` this function used to be the only caller of).

    NEVER call this on an assignee dict directly - `contact_id`-derived values
    (`ZZT-<id>`) are wrong for an assignee, which recorded rows carry `id: null` on
    (measured); `_scrub_assignee` above is the assignee's own rule."""
    if not isinstance(contact, dict):
        return
    contact_id = _fake_contact_id(contact.get("id"))
    if "id" in contact and contact["id"] is not None:
        contact["id"] = int(contact_id)
    if "firstName" in contact:
        contact["firstName"] = f"ZZT-{contact_id}"
    if "lastName" in contact:
        contact["lastName"] = ""
    if "phone" in contact and contact["phone"]:
        contact["phone"] = f"+60{contact_id}"
    if "email" in contact and contact["email"]:
        contact["email"] = f"zzt-{contact_id}@example.invalid"
    custom_fields = contact.get("custom_fields")
    if isinstance(custom_fields, list):
        for field in custom_fields:
            if not isinstance(field, dict):
                continue
            # `backend_id`: the internal admin URL for this contact
            # (`https://fe-sorento.foundryx.my/user-management/contacts/<uuid>`) -
            # the hostname reveals the company's own admin domain even though the
            # uuid alone identifies nobody.
            if field.get("name") == "backend_id" and isinstance(field.get("value"), str):
                if _BACKEND_ID_URL_RE.match(field["value"]):
                    field["value"] = f"https://internal.example.invalid/contacts/{contact_id}"
    _scrub_assignee(contact.get("assignee"))
    for field in ("profilePic",):
        if contact.get(field):
            contact[field] = None


# Any dict carrying at least two of these three keys is treated as a contact-shaped
# record wherever it turns up, not only at the envelope's own known paths - a tool
# response (ideation, media, an echoed webhook payload) can embed the SAME shape at
# an arbitrary depth. Checked only when the PARENT key is not already "assignee"
# (that shape gets `_scrub_assignee`'s own, different rule instead - see its
# docstring for why treating an assignee as a contact corrupts it).
_CONTACT_SHAPE_KEYS = ("firstName", "lastName", "email")


def _scrub_nested_pii(node: Any, *, key: str | None = None) -> Any:
    """Recursively scrub any contact/assignee-shaped dict found anywhere inside
    `node` (a `tool_results[].envelope` / `.args`, or any other nested structure) -
    the same fields `_scrub_contact` / `_scrub_assignee` clear at the envelope's own
    known paths. Also scrubs a whatsapp-channel-shaped dict's own `name` (the
    company's real number) wherever ONE of those turns up nested too
    (`envelope.media.message.channel`, measured - the two targeted `message.channel`
    calls in `_scrub_pii` only reach the envelope's own top-level copy).

    `key` is the dict key `node` was found under in its parent, threaded through the
    recursion so an "assignee" gets `_scrub_assignee`'s rule and never
    `_scrub_contact`'s id-derived one (which produces "ZZT-None" for the many
    assignee rows recorded with `id: null` - measured, the bug this parameter
    fixes)."""
    if isinstance(node, dict):
        if key == "assignee":
            _scrub_assignee(node)
        else:
            shape_hits = sum(1 for k in _CONTACT_SHAPE_KEYS if k in node)
            if shape_hits >= 2:
                _scrub_contact(node)
        if node.get("source") == "whatsapp_business" and node.get("name"):
            node["name"] = "Internal (scrubbed)"
        for id_key in _CONTACT_ID_KEYS:
            if node.get(id_key) is not None and not isinstance(node[id_key], (dict, list)):
                fake = _fake_contact_id(node[id_key])
                node[id_key] = int(fake) if isinstance(node[id_key], int) else fake
        for child_key, value in node.items():
            _scrub_nested_pii(value, key=child_key)
    elif isinstance(node, list):
        for item in node:
            _scrub_nested_pii(item, key=key)
    return node


def _scrub_pii(envelope: dict[str, Any] | None) -> dict[str, Any] | None:
    """Real names, phone numbers and emails out of a recorded envelope before it is
    ever written to disk (this corpus is committed to git).

    Contact 900000008 (`test_engine.py::CONTACT_ID`, "Jayson"/"ZZT") is the team's
    OWN standing console-test contact, already hardcoded across dozens of committed
    test files - scrubbing it too keeps ONE rule ("every envelope is scrubbed") over
    a carve-out, and costs nothing since no test reads a name/phone value. Every
    other id in a `--branch-kind` `prod_sample` is a REAL customer sampled from the
    0915 prod copy (measured: 32 distinct real names/phone numbers across one
    `--sample 40` run before this function existed) - scrubbing keeps the ENGINE
    behaviour (branch routing, tool args, reply shape) the corpus exists to gate,
    while removing what a real person could be identified by. `contact.id` itself
    is kept: it is an opaque respond.io integer the engine uses as a lookup key and
    identifies nobody on its own.
    """
    if not envelope:
        return envelope
    envelope = json.loads(json.dumps(envelope))  # deep copy, JSON-safe values only

    _scrub_contact(envelope.get("contact"))
    message = envelope.get("message") or {}
    _scrub_contact(message.get("contact"))
    channel = message.get("channel") or {}
    if channel.get("name"):
        # The COMPANY's own WhatsApp Business channel name/number (measured:
        # "Internal (+60 11-0000 0524)") - not a customer's, but still the
        # company's real number, publicly readable in a committed corpus.
        channel["name"] = "Internal (scrubbed)"
    if isinstance(channel.get("meta"), str):
        # A JSON string embedding `profile.name` / `wa_id` (WhatsApp's own webhook
        # shape) - real name/phone AGAIN, one level down as text rather than a key.
        try:
            meta = json.loads(channel["meta"])
        except (TypeError, ValueError):
            meta = None
        if isinstance(meta, dict):
            profile = ((meta.get("meta") or {}).get("profile")) or {}
            if "name" in profile:
                profile["name"] = "ZZT"
            if "wa_id" in (meta.get("meta") or {}):
                meta["meta"]["wa_id"] = "60100000000"
            if "firstName" in (meta.get("meta") or {}):
                meta["meta"]["firstName"] = "ZZT"
            channel["meta"] = json.dumps(meta)

    # A blanket recursive pass over the WHOLE envelope, last (security review nit,
    # 16 Sep 2026): `envelope.media.message.contact.assignee` was found carrying a
    # real staff email unscrubbed - a FULL nested copy of the message/contact shape
    # under an audio/media intake field the two targeted calls above never visit.
    # Idempotent over what `_scrub_contact` already touched above (re-deriving the
    # same `ZZT-<id>` twice is a no-op), so this is additive coverage, not a redo.
    return _scrub_nested_pii(envelope)


def _record_row(row: dict[str, Any], *, db_label: str, switches: dict[str, Any]) -> dict[str, Any]:
    real_ids: set[str] = set()
    _collect_real_ids(row, real_ids)
    turn = _record_row_raw(row, db_label=db_label, switches=switches)
    urls: dict[str, str] = {}
    _collect_media_urls(turn, urls)
    media_map = {u: _MEDIA_HOST + (u.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "media") for u in urls}
    return _final_scrub(turn, real_ids, media_map)


def _record_row_raw(row: dict[str, Any], *, db_label: str, switches: dict[str, Any]) -> dict[str, Any]:
    trace = row.get("trace") or []
    tool_events = _tool_events(trace)
    verdict = _verdict_of(trace)
    return {
        "source": {
            "db": db_label,
            "turn_id": str(row["id"]),
            "recorded_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "prompt_version": (_stage(trace, "understood") or {}).get("facts", {}).get("prompt_version"),
            "expected_from": "recorded",
        },
        "envelope": _scrub_pii(row.get("envelope")),
        "verdict": verdict,
        "tool_results": _scrub_nested_pii(
            json.loads(
                json.dumps(
                    [
                        {"tool": e.get("name"), "args": e.get("args"), "envelope": e.get("envelope")}
                        for e in tool_events
                    ]
                )
            )
        ),
        "access": _access_of(trace),
        "resolutions": _derive_resolutions(verdict, tool_events),
        # T4 (coordinator ruling, 16 Sep 2026): the "received" stage's own `raw.
        # session_vars` - what the contact's REAL session held the instant this turn
        # started, before this turn's own write. `test_turn_replay.py`'s harness seeds
        # a brand-new contact with `session_vars = "{}"` for a chain's first step, so a
        # recorded call that carried ids/focus from the contact's PRIOR real
        # conversation (never itself a turn in this file) was structurally unreachable
        # in replay - measured as the single largest remaining cluster after the two
        # signed rulings (`replay_turns/DIVERGENCES.md`'s "T4 diagnosed" section, 24
        # files failing on `entity_ids` alone). Present on every turn in a chain, not
        # only the first - the harness decides which one it actually needs (step 1
        # only; later steps already carry state forward via `session_patch`).
        "received_session_vars": _scrub_nested_pii(
            (_stage(trace, "received") or {}).get("raw", {}).get("session_vars")
        ),
        "expected": _expected_of(row, trace, tool_events),
        "switches": switches,
    }


def _write(group: str, slug: str, turns: list[dict[str, Any]]) -> Path:
    """Every file is `{"turns": [...]}` - a length-1 list for a standalone case, a
    longer list for a CHAIN replayed sequentially (state carried by the real engine
    run between steps, `test_turn_replay.py`'s own design - see that file's
    docstring). One shape for both, so the harness has one reader."""
    out_dir = REPLAY_ROOT / group
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_slugify(slug)}.json"
    out_path.write_text(json.dumps({"turns": turns}, indent=2, sort_keys=True, default=str) + "\n")
    return out_path


_ROW_COLUMNS = (
    "id, contact_respond_id, message_id, envelope, is_test, status, branch_kind, "
    "trace, response, created_at"
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "contact_respond_id": row.contact_respond_id,
        "message_id": row.message_id,
        "envelope": row.envelope,
        "is_test": row.is_test,
        "status": row.status,
        "branch_kind": row.branch_kind,
        "trace": row.trace,
        "response": row.response,
        "created_at": row.created_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", required=True, help="Source Postgres URL (read-only)")
    parser.add_argument("--group", required=True, choices=["contract", "console", "prod_sample"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--turn-id")
    mode.add_argument("--turn-ids", help="Comma-separated turn ids, IN ORDER, as one chain")
    mode.add_argument("--contact")
    mode.add_argument("--test-run-id")
    mode.add_argument("--branch-kind")
    parser.add_argument("--slug", help="Required with --turn-id/--turn-ids: the output file's slug")
    parser.add_argument("--slug-prefix", help="Prefix for --contact/--test-run-id/--branch-kind output files")
    parser.add_argument("--since", help="ISO date/timestamp, inclusive, with --contact")
    parser.add_argument("--until", help="ISO date/timestamp, exclusive, with --contact")
    parser.add_argument("--sample", type=int, default=40, help="Row cap for --branch-kind (default 40)")
    parser.add_argument("--is-test", choices=["true", "false"], help="Filter is_test (e.g. false for prod_sample)")
    parser.add_argument("--limit", type=int, default=200, help="Row cap for --contact/--test-run-id")
    parser.add_argument(
        "--chain-by",
        choices=["test_run_id", "none"],
        default="test_run_id",
        help="With --contact: group matching rows into one chain file per "
        "envelope.test_run_id (default), or 'none' for one file per row",
    )
    args = parser.parse_args(argv)

    if args.turn_id and not args.slug:
        parser.error("--turn-id requires --slug")
    if args.turn_ids and not args.slug:
        parser.error("--turn-ids requires --slug")

    engine = create_engine(args.db_url)
    db_label = engine.url.database or "unknown"
    written: list[Path] = []

    with engine.connect() as conn:
        switches = _source_switches(conn)
        if args.turn_id:
            row = conn.execute(
                text(f"SELECT {_ROW_COLUMNS} FROM chatbot.turns WHERE id = :id"),
                {"id": args.turn_id},
            ).first()
            if row is None:
                print(f"no turn found for id={args.turn_id}", file=sys.stderr)
                return 1
            turn = _record_row(_row_to_dict(row), db_label=db_label, switches=switches)
            written.append(_write(args.group, args.slug, [turn]))
        elif args.turn_ids:
            ids = [i.strip() for i in args.turn_ids.split(",") if i.strip()]
            by_id: dict[str, Any] = {}
            rows = conn.execute(
                text(f"SELECT {_ROW_COLUMNS} FROM chatbot.turns WHERE id::text = ANY(:ids)"),
                {"ids": ids},
            ).fetchall()
            for r in rows:
                by_id[str(r.id)] = r
            missing = [i for i in ids if i not in by_id]
            if missing:
                print(f"turn id(s) not found: {missing}", file=sys.stderr)
                return 1
            turns = [_record_row(_row_to_dict(by_id[i]), db_label=db_label, switches=switches) for i in ids]
            written.append(_write(args.group, args.slug, turns))
        elif args.test_run_id:
            rows = conn.execute(
                text(
                    f"SELECT {_ROW_COLUMNS} FROM chatbot.turns "
                    "WHERE envelope->>'test_run_id' = :run_id ORDER BY created_at ASC LIMIT :limit"
                ),
                {"run_id": args.test_run_id, "limit": args.limit},
            ).fetchall()
            if not rows:
                print(f"no turns found for test_run_id={args.test_run_id}", file=sys.stderr)
                return 1
            turns = [_record_row(_row_to_dict(r), db_label=db_label, switches=switches) for r in rows]
            slug = args.slug_prefix or _slugify(args.test_run_id)
            written.append(_write(args.group, slug, turns))
        elif args.contact:
            clauses = ["contact_respond_id = :contact"]
            params: dict[str, Any] = {"contact": args.contact}
            if args.since:
                clauses.append("created_at >= :since")
                params["since"] = args.since
            if args.until:
                clauses.append("created_at < :until")
                params["until"] = args.until
            if args.is_test is not None:
                clauses.append("is_test = :is_test")
                params["is_test"] = args.is_test == "true"
            where = " AND ".join(clauses)
            rows = conn.execute(
                text(
                    f"SELECT {_ROW_COLUMNS} FROM chatbot.turns WHERE {where} "
                    "ORDER BY created_at ASC LIMIT :limit"
                ),
                {**params, "limit": args.limit},
            ).fetchall()
            prefix = args.slug_prefix or _slugify(f"contact-{_fake_contact_id(args.contact)}")
            if args.chain_by == "none":
                for i, row in enumerate(rows, start=1):
                    turn = _record_row(_row_to_dict(row), db_label=db_label, switches=switches)
                    written.append(_write(args.group, f"{prefix}-{i:03d}", [turn]))
            else:
                chains: dict[str, list[Any]] = {}
                for row in rows:
                    run_id = (row.envelope or {}).get("test_run_id") or "no-run-id"
                    chains.setdefault(run_id, []).append(row)
                for i, (run_id, chain_rows) in enumerate(sorted(chains.items()), start=1):
                    turns = [_record_row(_row_to_dict(r), db_label=db_label, switches=switches) for r in chain_rows]
                    written.append(_write(args.group, f"{prefix}-chain-{i:03d}-{_slugify(run_id)}", turns))
        else:  # --branch-kind (prod_sample): independent real turns, one per file
            clauses = ["branch_kind = :branch_kind"]
            params = {"branch_kind": args.branch_kind}
            if args.is_test is not None:
                clauses.append("is_test = :is_test")
                params["is_test"] = args.is_test == "true"
            where = " AND ".join(clauses)
            rows = conn.execute(
                text(
                    f"SELECT {_ROW_COLUMNS} FROM chatbot.turns WHERE {where} "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {**params, "limit": args.sample},
            ).fetchall()
            prefix = args.slug_prefix or _slugify(args.branch_kind)
            for i, row in enumerate(rows, start=1):
                turn = _record_row(_row_to_dict(row), db_label=db_label, switches=switches)
                written.append(_write(args.group, f"{prefix}-{i:03d}", [turn]))

    for path in written:
        print(f"wrote {path.relative_to(REPLAY_ROOT.parent.parent.parent)}")
    print(f"{len(written)} case(s) written to {args.group}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
