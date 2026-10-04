"""S6 turn replay: the gate (AC-1590, AC-1591, PLAN-chatbot-turn-rearch.md "Turn
replay (the gate)"; recorded by `scripts/chatbot_record_turn.py`).

Parametrized over every `tests/chatbot/replay_turns/**/*.json` (`contract/`, `console/`,
`prod_sample/` - `DIVERGENCES.md` in the same tree is not a case file and is skipped).
Each file is `{"turns": [...]}` - a length-1 list for a standalone turn, a longer list
for a CHAIN replayed sequentially. Every turn runs through the REAL `engine.run_turn`
on the private Postgres schema (`tests/_pg_fixture.blank_schema_engine`, same fixture
`tests/chatbot/conftest.py::session_factory` builds), with four seams stubbed from the
recorded case rather than reached live:

- the parser -> the case's own `verdict` (`stub_parser`, `test_engine.py`'s existing
  fixture - never a live LLM call, per "Testing seams" in the plan).
- `engine.check_access` -> the case's own recorded `access` payload (falls back to an
  always-allow default when a hand-built contract case has none).
- `ResolveGateServices.resolve_entity` / `.probe` -> the case's `resolutions` field.
  **Design decision beyond the brief's literal item-1 shape, flagged to the captain
  rather than silently assumed**: a recorded turn resolves against the SOURCE
  database's real product/customer catalogue, which this lane's private test schema
  does not and must not hold (CI's database has no data - LESSONS-LEARNT; a blank
  schema is not a prod copy). Stubbing resolution at this seam - the same one
  `tests/chatbot/test_s6c_answer_lane.py::_stub_bundle` already uses for the same
  reason - lets a case replay with the SAME resolved entity ids the original turn
  got, without needing this lane's schema to carry a matching row for every prod
  product code any recorded turn ever mentioned.
- MCP tool calls -> the case's `tool_results` (`_no_real_mcp_calls` in this
  directory's `conftest.py` already forbids the real call; this stubs it to return
  the recorded envelope instead of raising).

**Known gap, not solved here, flagged to the captain**: a lane that resolves a
product SET by calling `resolve_product_set`'s own direct SQL rather than going
through `resolve_entity` (attribute-first counted-set/paging, certificate/promotion
coverage - the same seam the S6 tester handoff's "NOT fixed" section names) is NOT
made to work by these four stubs; such a case still needs real seeded catalogue rows
and is out of scope for this file. The corpus this slice ships avoids that lane
family; a future case in it will fail for an environment reason (empty count) rather
than an engine reason unless seeded separately.

Comparison is STRUCTURAL (AC-1591): `branch_kind`, tool names + arg key sets, entity
ids actually fetched, action kinds, pending kind + option labels, and - only when a
case's `expected.text` is non-null (a case "pins" it) - the reply text verbatim.
`expected.canned` (sentences that must appear somewhere in the reply, order-free) is
checked when the list is non-empty. A structural mismatch is a FAILURE unless
`tests/chatbot/replay_turns/DIVERGENCES.md` carries a signed entry
`- <group>/<slug>: <field>: <reason> (signed <initials> <date>)` for that exact
`<group>/<slug>` and `<field>`.

**Chain state carries step to step via `session_patch`, not the harness key - by
design, not because the harness key is broken.** `engine.run_turn`'s dry-run path
(D14) writes nothing to `respond_contacts` and returns what it WOULD have written as
`TurnResult.session_patch` (`engine.py run_tail`, `session_patch=session_patch if
dry_run else None`) - exactly the top-level five-key shape
`test_rearch_s3_journey_chain.py::_set_session_vars` writes. Every step here stays
`is_test=True` (D14, and the brief's own words); this harness takes step N's
`session_patch` and writes it onto the contact's `session_vars` ROW itself before step
N+1 runs, the same row `engine.run_turn` reads from at the start of the next step,
rather than sending it back as `previous_conversation_state`. `_build_envelope`
(~line 828) strips any `previous_conversation_state` a case's captured envelope
carries for exactly this reason: since Fix 3 (`engine._inject_harness_session`)
that key IS honoured for real, and the `replay_turns/console/*.json` captures predate
several current wire-shape conventions (singular `focus.customer`, `focus.
order_status`, `open_question.options[].idx` - see that function's own comment), so
letting a stale capture through would override the row this runner just wrote with
shape the current read-back code cannot parse. Flagged as a design decision, not a
silent guess.

**`system_settings` switches are per-case, applied before each step (`_apply_switches`,
S6 tester triage cluster 6).** `engine.py::_read_switches` / `turn/policy.py::
load_policy` read `chatbot_stock_denial_enabled` etc. off the singleton row once per
turn; a blank private schema starts with no row, so before this fix every replay ran
under the hard-coded all-off `_TurnSwitches()` default regardless of what the recorded
turn actually ran under, and `stock_denied`/`demand_qty` were structurally unreachable.
`scripts/chatbot_record_turn.py` now captures the source DB's singleton at record time
(`turn["switches"]`, `switches_source` says whether a source row existed); a case
recorded before that field existed carries no `switches` key and this is a no-op for
it, unchanged from before.

**No network.** `tests/chatbot/conftest.py`'s autouse fixture blocks the real MCP
server at the application seam; this file adds a socket-level backstop (any outbound
connection that is not to `localhost`/`127.0.0.1` - Postgres/Redis - raises) so a
parser call that slipped past the `stub_parser` seam is a hard failure here, not a
silent 8-second HTTP wait against OpenAI.
"""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot.contracts import Envelope
from tests.chatbot._turn_helpers import verdict as _default_verdict
from tests.chatbot.test_engine import stub_parser  # noqa: F401 - fixture import

REPLAY_ROOT = Path(__file__).resolve().parent / "replay_turns"
DIVERGENCES_PATH = REPLAY_ROOT / "DIVERGENCES.md"

# Same pattern `scripts/chatbot_record_turn.py::_is_uuid` uses - kept local rather than
# imported, since that module is a script, not a package this file should depend on.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _entity_uuids_from_args(args: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key, value in args.items():
        if not (key.endswith("_id") or key.endswith("_ids")):
            continue
        values = value if isinstance(value, list) else [value]
        for v in values:
            if _is_uuid(v):
                found.add(v)
    return found

SPACE_ID = "364817"
SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

# --------------------------------------------------------------------------- #
# Case discovery
# --------------------------------------------------------------------------- #


def _case_files() -> list[Path]:
    if not REPLAY_ROOT.exists():
        return []
    return sorted(
        p
        for p in REPLAY_ROOT.rglob("*.json")
        if p.is_file()
    )


CASE_FILES = _case_files()
CASE_IDS = [str(p.relative_to(REPLAY_ROOT)) for p in CASE_FILES]


# --------------------------------------------------------------------------- #
# DIVERGENCES.md - signed entries excuse a specific <group>/<slug>: <field>
# --------------------------------------------------------------------------- #


_SIGNED_LINE_RE = re.compile(
    r"^(?P<case>[^:]+):\s*(?:step\s+(?P<step>\d+)\s*:\s*)?(?P<field>[^:]+):\s*(?P<reason>.+)$"
)


def _parse_divergences(text: str) -> dict[tuple[str, str], set[int | None]]:
    """`{(case_id, field): {step_no, ...}}` from every signed line in `text`
    (DIVERGENCES.md's own format - a pure function of the text, so a unit test can
    exercise the parsing without touching the real file).

    A line reads `- <group>/<slug>: <field>: <reason> (signed <initials> <date>)` -
    a WHOLE-CASE signature, excusing that field on every step of that case (recorded
    as `None` in the step set). A line may instead name one step it excuses:
    `- <group>/<slug>: step 3: <field>: <reason> (signed <initials> <date>)` - that
    excuses ONLY step 3's divergence on that field, leaving every other step's own
    divergence on the same field still red. Both forms coexist per `(case_id, field)`;
    a case may carry a whole-case signature for one field and a step-scoped one for
    another.

    Unsigned lines (no `(signed ...)`) do not count - a template/placeholder entry
    must not silently excuse a real failure.
    """
    out: dict[tuple[str, str], set[int | None]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- ") or "(signed " not in line:
            continue
        body = line[2:]
        match = _SIGNED_LINE_RE.match(body)
        if match is None:
            continue
        case_id = match.group("case").strip()
        field = match.group("field").strip()
        step_raw = match.group("step")
        step_no = int(step_raw) if step_raw is not None else None
        out.setdefault((case_id, field), set()).add(step_no)
    return out


def _signed_divergences() -> dict[tuple[str, str], set[int | None]]:
    if not DIVERGENCES_PATH.exists():
        return {}
    return _parse_divergences(DIVERGENCES_PATH.read_text())


DIVERGENCES = _signed_divergences()


# --------------------------------------------------------------------------- #
# PENDING-LIVE-RERUN.md - R-A (captain ruling, 17 Sep 2026): a case listed there
# is a genuine recording-staleness problem (captured under an older engine/prompt
# version, or needs a source-DB state this blank-seed harness cannot reproduce),
# not a signable DIVERGENCES.md entry and not an engine defect either. It is
# SKIPPED here (reason recorded, never silently dropped) so the gate stays honest
# and CI-green; a case NOT listed here and NOT signed in DIVERGENCES.md still
# fails, per AC-1591's "every difference is either signed or a failure."
#
# Generalized (tester 20, 17 Sep 2026): the file lists pending cases under SEVERAL
# `## ` headings, not just "Composite / cascading chains" - the parser below reads
# every heading (one parser, one rule), and the heading a stem was found under
# becomes that case's skip reason, so a skip always names where it came from. It
# only reads a "list paragraph" - one whose ENTIRE content (once whitespace is
# collapsed) is backtick-quoted, comma-separated tokens ending in an optional
# period - so a case id merely MENTIONED inline inside a prose sentence (an
# example, a "not pending" note, a field-name backtick) is never mistaken for an
# enumerated pending entry. A prose paragraph never matches that shape.
# --------------------------------------------------------------------------- #

PENDING_PATH = REPLAY_ROOT / "PENDING-LIVE-RERUN.md"

_PENDING_HEADING_RE = re.compile(r"^## (?P<title>.+)$", re.M)
_PENDING_BRACE_RE = re.compile(r"^(?P<prefix>.*)\{(?P<opts>[^}]+)\}$")
_PENDING_BACKTICK_TOKEN = r"`[^`]+`"
_PENDING_LIST_PARAGRAPH_RE = re.compile(
    rf"^{_PENDING_BACKTICK_TOKEN}(?:\s*,\s*{_PENDING_BACKTICK_TOKEN})*\.?$"
)


def _pending_heading_sections(text: str) -> list[tuple[str, str]]:
    """Split PENDING-LIVE-RERUN.md into `(heading title, body)` pairs, one per
    top-level `## ` heading - the file's own section structure, not one heading
    hardcoded by name. `body` runs to the next `## ` heading or EOF."""
    headings = list(_PENDING_HEADING_RE.finditer(text))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(headings):
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        sections.append((m.group("title").strip(), text[start:end]))
    return sections


def _pending_list_paragraph_stems(body: str) -> set[str]:
    """Every stem in `body`'s "pure list" paragraphs, with the file's own
    `{a,b,c}` brace-list shorthand expanded (e.g. `console/case-{008,010}` ->
    `{"console/case-008", "console/case-010"}`). A paragraph that mixes prose
    with a list (an inline example, a "seven more added because..." lead-in)
    contributes nothing - only a paragraph that IS purely the list counts."""
    stems: set[str] = set()
    for raw_paragraph in re.split(r"\n\s*\n", body):
        paragraph = " ".join(raw_paragraph.split())
        if not paragraph or not _PENDING_LIST_PARAGRAPH_RE.match(paragraph):
            continue
        for token in re.findall(r"`([^`]+)`", paragraph):
            brace = _PENDING_BRACE_RE.match(token)
            if brace is None:
                stems.add(token)
                continue
            for opt in brace.group("opts").split(","):
                stems.add(brace.group("prefix") + opt.strip())
    return stems


def _parse_pending_stems(text: str) -> dict[str, str]:
    """`{stem: heading title}` for every case stem listed in a pure-list
    paragraph under ANY `## ` heading in PENDING-LIVE-RERUN.md. A pure function
    of the file's text so the expansion is unit-testable without touching the
    real file (same shape as `_parse_divergences` above). First heading to claim
    a stem wins (document order) if the same stem is somehow listed twice."""
    out: dict[str, str] = {}
    for title, body in _pending_heading_sections(text):
        for stem in _pending_list_paragraph_stems(body):
            out.setdefault(stem, title)
    return out


def _stem_matches_case(case_stem: str, stem: str) -> bool:
    """`case_stem` (a real case id, minus `.json`) matches `stem` (one PENDING-
    LIVE-RERUN.md entry) when `stem` is a prefix AND the next character (if any)
    is not a digit - so `..-900000008` does not also swallow a hypothetical
    `..-9000000080-...` case that merely shares the same leading digits."""
    if not case_stem.startswith(stem):
        return False
    if len(case_stem) == len(stem):
        return True
    return not case_stem[len(stem)].isdigit()


def _pending_case_ids(stem_reasons: dict[str, str], case_ids: list[str]) -> dict[str, str]:
    """`{case_id: reason}` for every real case file whose stem is listed under
    some heading in PENDING-LIVE-RERUN.md. `reason` names the heading, per
    AC-1591 - a skip always says where it came from, never one blanket reason."""
    out: dict[str, str] = {}
    for case_id in case_ids:
        stem_of_case = case_id[:-5] if case_id.endswith(".json") else case_id
        for stem, heading in stem_reasons.items():
            if _stem_matches_case(stem_of_case, stem):
                out[case_id] = (
                    "recorded under the old prompt / composite chain, or needs a "
                    "live re-run; re-record "
                    f'(tests/chatbot/replay_turns/PENDING-LIVE-RERUN.md, "{heading}")'
                )
                break
    return out


def _pending_cases() -> dict[str, str]:
    if not PENDING_PATH.exists():
        return {}
    return _pending_case_ids(_parse_pending_stems(PENDING_PATH.read_text()), CASE_IDS)


PENDING_CASES = _pending_cases()


# --------------------------------------------------------------------------- #
# Per-case xfail(strict) - genuine, unsigned, already-tracked ENGINE DEFECTS
# (tester 17's classification table; AC-1591's "not excused" cases stay a live
# assertion so a real fix flips them loudly instead of vanishing into a skip).
# Neither belongs in PENDING-LIVE-RERUN.md - that file is for recording
# staleness, not code defects - so this is its own small map, not a third
# mechanism layered onto the pending-skip one above.
# --------------------------------------------------------------------------- #

_XFAIL_REPLAY_CASES = {
    "console/case-011-an-out-of-range-tier-pick-keeps-the-product-in-scope.json": (
        "step 2 branch_kind: expected 'check_promotion', got 'clarify_menu' "
        "(already-tracked Finding 9/Row 1 tier-pick gap) (engine defect, AC-1591, "
        "follow-up PR #952)"
    ),
}


def _excused(case_id: str, field: str, step_no: int | None = None) -> bool:
    """A whole-case signature (no `step N:` in its line, recorded as `None`) excuses
    EVERY step's divergence on that field; a step-scoped signature excuses only the
    one step it names. Passing no `step_no` (the historical call shape, still used by
    every caller that has not been updated to thread it through) only matches a
    whole-case signature - a step-scoped-only signature does not excuse an unnumbered
    check, since it was written to cover one specific step and no other.
    """
    steps = DIVERGENCES.get((case_id, field))
    if not steps:
        return False
    if None in steps:
        return True
    return step_no is not None and step_no in steps


# --------------------------------------------------------------------------- #
# Socket guard - no network at all from this file's tests
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect

    def _guarded_connect(self, address, *a, **kw):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(
                f"tests/chatbot/test_turn_replay.py: no network - blocked connect to {address!r}. "
                "A live parser or MCP call slipped past its stub."
            )
        return real_connect(self, address, *a, **kw)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


# --------------------------------------------------------------------------- #
# Seeding: one contact/workspace/company-link chain per distinct contact id.
# --------------------------------------------------------------------------- #


def _seed_contact(session_factory, *, contact_id: Any) -> None:
    from sqlalchemy import text

    from app.models.respond_workspace import RespondWorkspace

    db = session_factory()
    existing_ws = db.query(RespondWorkspace).filter(RespondWorkspace.space_id == SPACE_ID).first()
    if existing_ws is None:
        ws = RespondWorkspace(space_id=SPACE_ID, name="replay workspace", api_key_ciphertext="replay-cipher")
        db.add(ws)
        db.commit()
        workspace_id = ws.id
    else:
        workspace_id = existing_ws.id

    existing = db.execute(
        text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(contact_id)}
    ).first()
    if existing is not None:
        contact_row_id = existing.id
    else:
        db.execute(
            text(
                "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
                "VALUES (gen_random_uuid()::text, :cid, :phone, CAST('{}' AS jsonb), :wid)"
            ),
            {"cid": str(contact_id), "phone": f"+6000{str(contact_id)[-7:]}", "wid": workspace_id},
        )
        db.commit()
        contact_row_id = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(contact_id)}
        ).first().id

    linked = db.execute(
        text("SELECT 1 FROM respond_contact_companies WHERE respond_contact_id = :rcid"),
        {"rcid": contact_row_id},
    ).first()
    if linked is None:
        db.execute(
            text(
                "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
                "VALUES (gen_random_uuid(), :rcid, :cid)"
            ),
            {"rcid": contact_row_id, "cid": SORENTO_COMPANY_ID},
        )
        db.commit()

    # handpass1-002 step 2 finding (16 Sep, tester): `_fake_check_access` above only
    # short-circuits to the recorded/hard-coded access when the case CARRIES an `access`
    # payload; a case that has none (or one corrected to `null`, the stale-recording
    # fix applied to handpass1-002 step 2 in its own JSON) falls through to whatever
    # `check_access` would really decide - and `turn_runtime.with_routing_agent_default`
    # (landed 16 Sep) means an unrouted turn's `suggested_agent` is `general_enquiries`
    # (`contracts.DEFAULT_SUGGESTED_AGENT`), not `None`. A blank schema starts with no
    # `access_agents`/`contact_agent_access` rows at all, so that default agent was
    # unreachable for every replayed contact regardless of the recorded case. Seeded
    # once per contact, real DB rows (not a stub), so a case relying on the harness's
    # fallback default is exercising the SAME grant the fixture actually holds.
    from app.services.chatbot.contracts import DEFAULT_SUGGESTED_AGENT

    agent_row = db.execute(
        text("SELECT id FROM access_agents WHERE code = :code"), {"code": DEFAULT_SUGGESTED_AGENT}
    ).first()
    if agent_row is None:
        db.execute(
            text(
                "INSERT INTO access_agents "
                "(id, code, name, is_active, assign_to_new_internal_contacts, synced_to_excel) "
                "VALUES (gen_random_uuid(), :code, :name, true, false, false)"
            ),
            {"code": DEFAULT_SUGGESTED_AGENT, "name": "General Enquiries"},
        )
        db.commit()
        agent_row = db.execute(
            text("SELECT id FROM access_agents WHERE code = :code"), {"code": DEFAULT_SUGGESTED_AGENT}
        ).first()
    agent_id = agent_row.id

    granted = db.execute(
        text(
            "SELECT 1 FROM contact_agent_access WHERE respond_contact_id = :rcid AND agent_id = :aid"
        ),
        {"rcid": contact_row_id, "aid": agent_id},
    ).first()
    if granted is None:
        db.execute(
            text(
                "INSERT INTO contact_agent_access "
                "(id, respond_contact_id, respond_contact_phone, agent_id, is_allowed, synced_to_excel) "
                "VALUES (gen_random_uuid(), :rcid, :phone, :aid, true, false)"
            ),
            {"rcid": contact_row_id, "phone": f"+6000{str(contact_id)[-7:]}", "aid": agent_id},
        )
        db.commit()


# handpass3's own finding (coordinator, 16 Sep 2026): a bare customer-name token ("hanlim",
# "chin chun") resolves against the recorded case's empty `resolutions` stub to NOTHING -
# `tests/_mc_lookup_seed.py::customer`'s precedent (real `customers` rows, real names) is
# what a genuine narrow needs, but `_fake_resolve_entity` never queries the DB at all, so a
# seeded row alone changes nothing UNLESS the case's own `resolutions` field is corrected to
# name it. Fixed UUIDs (not `mc_customer`'s random ones) so the JSON can reference them by a
# stable value run to run - `handpass1-003-delivery-to-hanlim-customer-roster.json`'s own
# "known_gap_not_corrected_here" note names this exact same gap for the same token.
HANLIM_CUSTOMER_1_ID = "00000000-0000-0000-0000-0000000000a1"
HANLIM_CUSTOMER_2_ID = "00000000-0000-0000-0000-0000000000a2"
CHIN_CHUN_CUSTOMER_ID = "00000000-0000-0000-0000-0000000000a3"

HAND_PASS_2_HANLIM_FAMILY: tuple[tuple[str, str, str], ...] = (
    # (uuid, customer_code, customer_name) - REAL rows, read off the source clone
    # `sorento_ai_automation_rearch` (AC-1593 hand pass 2, 17 Sep 2026 MYT), so
    # `entity_ids` assertions can grade the SAME uuids production actually used
    # rather than synthetic ones. Six ledgers, matching finding 1's own count
    # ("six-line customer roster, one per ledger").
    ("3c15f4e4-be46-4fd7-9de8-3430a9b1217a", "300-H030", "HANLIM TRADING SDN BHD"),
    ("2d0cd958-9f5e-4eee-8b6e-ed31a94bee44", "300-H118", "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)"),
    ("c2f38bdf-767a-4b04-b92d-1c0d56cfd4d3", "300-H030", "HANLIM TRADING SDN BHD [A/C I]"),
    ("6f5a419b-840a-4e4d-8107-291971dd3bb8", "300-H070", "HANLIM TRADING SDN BHD [A/C II]"),
    ("6b52807a-537b-437d-9f55-12f7fda29df8", "300-H118", "HANLIM TRADING SDN BHD [A/C III]"),
    ("2a4575e0-836b-4a5d-8566-73223465020d", "300-H119", "HANLIM TRADING SDN BHD [A/C IV]"),
)
HAND_PASS_2_GOLDEN_WIN: tuple[tuple[str, str, str], ...] = (
    ("32d49e9b-5b55-4d23-9764-279306de295d", "300-G013", "GOLDEN WIN HARDWARE SDN BHD - [A/C I]"),
    ("47a7ded5-86ce-4763-b598-d4b0cb16715d", "300-G014", "GOLDEN WIN HARDWARE SDN BHD - [CERAMIC]"),
)

_CASE_CUSTOMERS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "console/handpass3-contact-n-escalation-offer.json": (
        (HANLIM_CUSTOMER_1_ID, "ZZT-" + HANLIM_CUSTOMER_1_ID[-4:], "HANLIM TRADING SDN BHD [A/C II]"),
        (HANLIM_CUSTOMER_2_ID, "ZZT-" + HANLIM_CUSTOMER_2_ID[-4:], "HANLIM TRADING SDN BHD [A/C I]"),
        (CHIN_CHUN_CUSTOMER_ID, "ZZT-" + CHIN_CHUN_CUSTOMER_ID[-4:], "CHIN CHUN HARDWARE SDN BHD - [A/C I]"),
    ),
    "console/handpass2-owner-17sep-hanlim-delivery-miss-picks.json": HAND_PASS_2_HANLIM_FAMILY,
    "console/handpass2-owner-17sep-hanlim-rpacc-sticky-pick.json": HAND_PASS_2_HANLIM_FAMILY,
    "console/handpass2-owner-17sep-outstanding-do-7445-scope.json": HAND_PASS_2_HANLIM_FAMILY,
    "console/handpass2-owner-17sep-golden-win.json": HAND_PASS_2_GOLDEN_WIN,
}


def _seed_case_customers(session_factory, *, case_id: str) -> None:
    from sqlalchemy import text

    rows = _CASE_CUSTOMERS.get(case_id)
    if not rows:
        return
    db = session_factory()
    for customer_id, code, name in rows:
        existing = db.execute(
            text("SELECT 1 FROM customers WHERE id = :id"), {"id": customer_id}
        ).first()
        if existing is not None:
            continue
        db.execute(
            text(
                "INSERT INTO customers (id, customer_code, customer_name, company_id, is_active) "
                "VALUES (:id, :code, :name, :cid, true)"
            ),
            {
                "id": customer_id,
                "code": code,
                "name": name,
                "cid": SORENTO_COMPANY_ID,
            },
        )
    db.commit()


# AC-1593 hand pass 2 (17 Sep 2026 MYT): the SRTWT7445 and SRTWC286 product families,
# real rows off the same source clone - `test_turn_replay.py`'s blank schema seeds no
# products at all, so a family-listing ruling (finding 3, "purchase cost lists all
# variants") has nothing to list without this.
HAND_PASS_2_SRTWC286_FAMILY: tuple[tuple[str, str], ...] = (
    ("0d0ed752-fd6f-4759-ad8f-0b40e0cbc601", "SRTWC286-SH"),
    ("78c96bfa-edea-4695-a215-4a02cc0c53a0", "SRTWC286-SH-150"),
    ("65514803-1609-4fe8-8b60-2e908c8f9bd4", "SRTWC286-SH-200"),
    ("77a5e4da-877a-462d-a0f8-cc558fdc5a51", "SRTWC286-SH-NEW"),
    ("13ffa633-91db-4db6-9050-5de7a0deb9b8", "SRTWC286-SH-NEW-150"),
    ("dcaeb074-6b9f-46bd-9e86-97a16e976b59", "SRTWC286-SH-NEW-200"),
    ("677b6eb7-f21f-4a65-8195-b969a0a465dd", "SRTWC286-SH-NEW-P"),
    ("d73a33f3-956b-401f-90c7-0ad6ff792ea1", "SRTWC286-SH-P"),
    ("7a8f2543-ef3b-4e5f-9dad-e29f6f688485", "SRTWC286-SH-PP"),
    ("d55d8829-5e3d-4386-ab77-6c93e26d73e7", "SRTWC286-SH-UF"),
)
HAND_PASS_2_SRTWT7445_FAMILY: tuple[tuple[str, str], ...] = (
    ("90afd8db-dbd8-40c2-a3b4-072feb01b08a", "SRTWT7445"),
    ("b3261772-aae1-4614-b7a8-b89b93734ff7", "SRTWT7445-LV"),
    ("ed13ab1e-2f13-4f57-800d-1928173ba201", "SRTWT7445-LV-BL"),
    ("445b035d-5e17-4345-9a1a-d41a6bda4868", "SRTWT7445-LV-BL-NEW"),
    ("2f949bd7-6426-4cad-8263-41f86f9368e4", "SRTWT7445-LV-GM"),
    ("e6d87826-d2c4-4332-98ec-7381ea821a9f", "SRTWT7445-LV-NEW"),
    ("e44a6c78-93ee-4126-9ce8-8c3092dab9de", "SRTWT7445-LV-WEPLS"),
    ("7ccd90c1-e4d5-4a0c-b6bc-5783cad6c411", "SRTWT7445-NEW"),
    ("bf98c511-4063-4d1b-8737-eeec2cc00d18", "SRTWT7445-NL"),
)

_CASE_PRODUCTS: dict[str, tuple[tuple[str, str], ...]] = {
    "console/handpass2-owner-17sep-purchase-cost-all.json": HAND_PASS_2_SRTWC286_FAMILY,
    "console/handpass2-owner-17sep-two-domain-asks.json": HAND_PASS_2_SRTWC286_FAMILY,
    "console/handpass2-owner-17sep-stock-incoming.json": HAND_PASS_2_SRTWC286_FAMILY,
    "console/handpass2-owner-17sep-outstanding-do-7445-scope.json": HAND_PASS_2_SRTWT7445_FAMILY,
    "console/handpass2-owner-17sep-incoming-stock-7445.json": HAND_PASS_2_SRTWT7445_FAMILY,
    # AC-1593 hand pass 3 (17 Sep 2026 MYT): same SRTWC286 family, same source clone -
    # the stock/incoming/word-number chain and the purchase-cost/PO chain both ask
    # about SRTWC286 first.
    "console/handpass3-owner-17sep-stock-incoming-multipick-word-number.json": HAND_PASS_2_SRTWC286_FAMILY,
    "console/handpass3-owner-17sep-purchase-cost-po.json": HAND_PASS_2_SRTWC286_FAMILY,
    "console/handpass3-owner-17sep-promo-tier.json": HAND_PASS_2_SRTWC286_FAMILY,
}

# AC-1593 hand pass 3, row 1 (coordinator, 17 Sep 2026): "10 promotions exist on the
# clone for SRTWC286: seed two" - the private test DB has none at all, so a promo-tier
# case has nothing to answer with regardless of the apply()-level fix. Two rows only
# (not ten): enough for `crm_marketing_promotions_list`'s stub to return a non-empty
# answer without hand-building the clone's full ten-row set this session never measured
# field-for-field.
_CASE_PROMOTIONS: dict[str, tuple[tuple[str, str, tuple[str, ...]], ...]] = {
    # (promotion_id, promotion_group_id, access_levels) - both ids fixed valid-hex
    # UUIDs (never string-sliced from one another, which risked a non-hex tail).
    "console/handpass3-owner-17sep-promo-tier.json": (
        ("00000000-0000-0000-0000-0000000ea001", "00000000-0000-0000-0000-0000000eb001", ("dealer", "office", "end user")),
        ("00000000-0000-0000-0000-0000000ea002", "00000000-0000-0000-0000-0000000eb002", ("dealer",)),
    ),
}


def _seed_case_promotions(session_factory, *, case_id: str) -> None:
    import json as _json

    from sqlalchemy import text

    rows = _CASE_PROMOTIONS.get(case_id)
    if not rows:
        return
    product_rows = _CASE_PRODUCTS.get(case_id) or ()
    if not product_rows:
        return
    db = session_factory()
    for promo_id, group_id, access_levels in rows:
        existing = db.execute(text("SELECT 1 FROM promotions WHERE id = :id"), {"id": promo_id}).first()
        if existing is not None:
            continue
        db.execute(
            text(
                "INSERT INTO promotions (id, access_levels, company_id, is_active) "
                "VALUES (:id, CAST(:levels AS jsonb), :cid, true)"
            ),
            {"id": promo_id, "levels": _json.dumps(list(access_levels)), "cid": SORENTO_COMPANY_ID},
        )
        db.execute(
            text(
                "INSERT INTO promotion_groups (id, promotion_id, group_name, company_id, sort_order) "
                "VALUES (:id, :pid, 'ZZT handpass3 group', :cid, 0)"
            ),
            {"id": group_id, "pid": promo_id, "cid": SORENTO_COMPANY_ID},
        )
        for product_id, _code in product_rows[:2]:
            db.execute(
                text(
                    "INSERT INTO promotion_products "
                    "(id, promotion_id, promotion_group_id, product_id, company_id) "
                    "VALUES (gen_random_uuid(), :pid, :gid, :prod, :cid)"
                ),
                {"pid": promo_id, "gid": group_id, "prod": product_id, "cid": SORENTO_COMPANY_ID},
            )
    db.commit()

_PRODUCT_SEED_CATEGORY_ID = "00000000-0000-0000-0000-00000000ca01"
_PRODUCT_SEED_UOM_ID = "00000000-0000-0000-0000-00000000c0a1"


def _seed_case_products(session_factory, *, case_id: str) -> None:
    from sqlalchemy import text

    rows = _CASE_PRODUCTS.get(case_id)
    if not rows:
        return
    db = session_factory()
    if db.execute(
        text("SELECT 1 FROM product_categories WHERE id = :id"), {"id": _PRODUCT_SEED_CATEGORY_ID}
    ).first() is None:
        db.execute(
            text(
                "INSERT INTO product_categories (id, category_code, category_name, company_id) "
                "VALUES (:id, 'ZZT-CAT', 'ZZT category', :cid)"
            ),
            {"id": _PRODUCT_SEED_CATEGORY_ID, "cid": SORENTO_COMPANY_ID},
        )
    if db.execute(
        text("SELECT 1 FROM units_of_measure WHERE id = :id"), {"id": _PRODUCT_SEED_UOM_ID}
    ).first() is None:
        db.execute(
            text(
                "INSERT INTO units_of_measure (id, uom_code, uom_name, company_id) "
                "VALUES (:id, 'ZZT-UOM', 'Each', :cid)"
            ),
            {"id": _PRODUCT_SEED_UOM_ID, "cid": SORENTO_COMPANY_ID},
        )
    db.commit()
    for product_id, code in rows:
        existing = db.execute(text("SELECT 1 FROM products WHERE id = :id"), {"id": product_id}).first()
        if existing is not None:
            continue
        db.execute(
            text(
                "INSERT INTO products "
                "(id, product_code, product_name, category_id, base_uom_id, list_price, "
                "is_active, company_id) "
                "VALUES (:id, :code, :code, :cat, :uom, 0, true, :cid)"
            ),
            {
                "id": product_id,
                "code": code,
                "cat": _PRODUCT_SEED_CATEGORY_ID,
                "uom": _PRODUCT_SEED_UOM_ID,
                "cid": SORENTO_COMPANY_ID,
            },
        )
    db.commit()


_SWITCH_FIELDS = (
    "chatbot_stock_denial_enabled",
    "chatbot_business_lane_enabled",
    "chatbot_ordering_enabled",
    "chatbot_unsupported_domains",
    "chatbot_completed_lanes",
    "chatbot_crossdomain_ladder",
    "chatbot_tier_order",
)


def _apply_switches(session_factory, switches: dict[str, Any] | None) -> None:
    """Write a recorded case's `system_settings` switches onto the private test DB's
    singleton row before the turn runs (S6 tester triage cluster 6, root cause flagged
    in `DIVERGENCES.md`): `engine.py::_read_switches` / `turn/policy.py::load_policy`
    read this row once per turn, and a blank schema starts with NO row at all, so every
    switch silently took its hard-coded ALL-FALSE `_TurnSwitches()` default regardless
    of what the recorded turn actually ran under - `stock_denied`/`demand_qty` were
    structurally unreachable in replay on every case, not case-by-case noise.

    No-op for a case recorded before `scripts/chatbot_record_turn.py` captured this
    field (`switches` is `None`) - leaves whatever a prior step in the same chain
    already wrote, or no row at all, which is the pre-existing behaviour. A JSONB
    field recorded as `None` (no source row at record time - `switches_source:
    "no_source_row_defaults"`) is skipped rather than written, since those columns are
    NOT NULL and a fresh `SystemSetting()` row's own column `default=` already supplies
    the real default; only a genuinely recorded value overrides it.

    Restored automatically, not explicitly: `session_factory`'s one shared
    connection/transaction is rolled back at fixture teardown
    (`tests/chatbot/conftest.py::session_factory`), so nothing written here survives
    past the one parametrized case it was set for.
    """
    if not switches:
        return
    from app.models.user import SystemSetting

    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    for field in _SWITCH_FIELDS:
        if field in switches and switches[field] is not None:
            setattr(row, field, switches[field])
    db.commit()


def _write_session_vars(session_factory, *, contact_id: Any, payload: dict[str, Any]) -> None:
    from sqlalchemy import text

    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :c"),
        {"sv": json.dumps(payload), "c": str(contact_id)},
    )
    db.commit()


# --------------------------------------------------------------------------- #
# Stubs, built from ONE recorded turn dict
# --------------------------------------------------------------------------- #


def _install_stubs(monkeypatch, stub_parser, *, turn: dict[str, Any]) -> list[dict[str, Any]]:
    """Installs the four stubs (AC-1590's own list) and returns the list every REAL
    MCP tool call this step makes gets appended to (`{"tool": name, "args": {...}}`,
    the full argument dict, not a placeholder) - reviewer finding B5: `_compare`'s old
    `tools` check read `result.actions`, which carries no tool identity at all (an
    internal fetch detail), so the disjoint check was vacuously true on every case.
    `_compare` grades THIS list against the recorded `tool_results` entries instead.
    """
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot.lanes.business.services import ResolveGateServices

    verdict = turn.get("verdict") or _default_verdict()
    stub_parser(verdict)

    access = turn.get("access")

    def _fake_check_access(db, *, agent_code, contact_id, space_id):
        if access is not None:
            return access
        return {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General Enquiries",
            "attributes": None,
            "all_attributes_allowed": None,
        }

    monkeypatch.setattr(engine_mod, "check_access", _fake_check_access)
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: SPACE_ID)

    resolutions = turn.get("resolutions") or {"tokens": [], "resolutions": [], "unresolved_tokens": []}

    def _fake_resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return resolutions

    def _fake_access_types(*, contact_id, space_id):
        return [{"name": "Sorento Dealer"}]

    def _fake_probe(**kwargs):
        return None

    from tests.chatbot.conftest import validating_resolve_entity

    bundle = ResolveGateServices(
        access_types=_fake_access_types,
        resolve_entity=validating_resolve_entity(_fake_resolve_entity),
        probe=_fake_probe,
    )
    monkeypatch.setattr(engine_mod.business_services, "production_services", lambda db, *, space_id=None: bundle)

    tool_results = turn.get("tool_results") or []
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for entry in tool_results:
        by_tool.setdefault(entry.get("tool"), []).append(entry)

    tool_calls: list[dict[str, Any]] = []

    from app.services.ai_assistant_service import MCPRuntimeClient

    def _fake_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        tool_calls.append({"tool": name, "args": dict(arguments)})
        bucket = by_tool.get(name)
        if not bucket:
            raise AssertionError(
                f"replay case called tool {name!r} with no recorded tool_results entry for it"
            )
        entry = bucket.pop(0) if len(bucket) > 1 else bucket[0]
        return json.dumps(entry.get("envelope") or {})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _fake_call_tool)
    return tool_calls


def _build_envelope(turn: dict[str, Any], *, message_id: str) -> Envelope:
    envelope_dict = dict(turn.get("envelope") or {})
    # This runner chains state via the contact's `session_vars` ROW (this file's own
    # note above, "Chain state carries step to step via `session_patch`, not the
    # harness key") - a captured `previous_conversation_state` was never what the
    # engine actually ran on. It predates the five-key wire shape besides (singular
    # `focus.customer`, `focus.order_status`, `open_question.options[].idx`), and now
    # that `engine._inject_harness_session` honours it for real, letting it through
    # here would override the row the runner just wrote with stale shape the current
    # read-back code cannot parse. Stripped before every step; `referenced_result_set`
    # and `prompt_overrides` are unaffected.
    envelope_dict.pop("previous_conversation_state", None)
    envelope_dict.setdefault("message", {})
    envelope_dict.setdefault("contact", {"id": 999999999})
    envelope_dict["is_test"] = True
    envelope_dict.setdefault("test_run_id", "replay")
    message = dict(envelope_dict.get("message") or {})
    inner_message = dict(message.get("message") or {})
    inner_message.setdefault("messageId", message_id)
    inner_message.setdefault("type", "text")
    inner_message.setdefault("text", message_id)
    message["message"] = inner_message
    message.setdefault("contactId", envelope_dict["contact"].get("id"))
    envelope_dict["message"] = message
    return Envelope(**envelope_dict)


# --------------------------------------------------------------------------- #
# Structural comparison (AC-1591)
# --------------------------------------------------------------------------- #


def _compare(
    case_id: str,
    step_no: int,
    expected: dict[str, Any],
    result: Any,
    failures: list[str],
    *,
    turn: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    field = "branch_kind"
    if expected.get("branch_kind") and result.branch_kind != expected["branch_kind"]:
        if not _excused(case_id, field, step_no):
            failures.append(
                f"step {step_no} {field}: expected {expected['branch_kind']!r}, got {result.branch_kind!r}"
            )

    actions = result.actions or []
    field = "action_kinds"
    expected_kinds = expected.get("action_kinds") or []
    if expected_kinds:
        actual_kinds = [a.get("kind") for a in actions]
        if actual_kinds != expected_kinds and not _excused(case_id, field, step_no):
            failures.append(f"step {step_no} {field}: expected {expected_kinds!r}, got {actual_kinds!r}")

    field = "tools"
    # Reviewer finding B5: `result.actions` carries no tool identity at all (an
    # internal fetch detail) and the old comparison hard-coded every actual arg-key
    # tuple to `()`, so the "disjoint" check above was true on every case regardless
    # of what the stub was actually called with. Graded now against the CALLS
    # `_fake_call_tool` really received (name + sorted arg keys), against the
    # recorded `tool_results` entries directly (the ground truth `_pending_of`/
    # `_expected_of` derived `expected.tools`' `args_keys` summary FROM, so this is
    # the more precise source, not a second copy that can drift from it).
    recorded_tool_results = (turn or {}).get("tool_results") or []
    if recorded_tool_results or tool_calls:
        expected_pairs = {
            (r.get("tool"), tuple(sorted((r.get("args") or {}).keys()))) for r in recorded_tool_results
        }
        actual_pairs = {(c["tool"], tuple(sorted(c["args"].keys()))) for c in (tool_calls or [])}
        if actual_pairs != expected_pairs and not _excused(case_id, field, step_no):
            failures.append(f"step {step_no} {field}: expected {expected_pairs!r}, got {actual_pairs!r}")

    field = "entity_ids"
    # AC-1591: "entity ids actually fetched" - the recorded `expected.entity_ids` was
    # written by `chatbot_record_turn.py::_expected_of` but never read back here
    # (reviewer finding B5). Compared as a set (fan-out, contract 122, may reorder).
    expected_entity_ids = set(expected.get("entity_ids") or [])
    if expected_entity_ids or tool_calls:
        actual_entity_ids: set[str] = set()
        for c in tool_calls or []:
            actual_entity_ids |= _entity_uuids_from_args(c["args"])
        if actual_entity_ids != expected_entity_ids and not _excused(case_id, field, step_no):
            failures.append(
                f"step {step_no} {field}: expected {sorted(expected_entity_ids)!r}, "
                f"got {sorted(actual_entity_ids)!r}"
            )

    field = "pending"
    expected_pending = expected.get("pending")
    reply = result.reply or {}
    # The ACTUAL open question, read the same way `chatbot_record_turn.py::_pending_of`
    # reads a recorded one - `session_patch.open_question`, never `reply.result_set`
    # (that field also carries a plain multi-row ANSWER listing with no question
    # attached, so grading against it produces a false divergence on an ordinary
    # multi-row stock reply - measured, see that function's own docstring).
    actual_open_question = (result.session_patch or {}).get("open_question")
    actual_labels = [
        (o.get("label") or o.get("code") or o.get("name"))
        for o in (actual_open_question or {}).get("options", [])
        if isinstance(o, dict)
    ]
    if expected_pending is not None or actual_open_question is not None:
        expected_labels = (expected_pending or {}).get("option_labels")
        if actual_labels != expected_labels and not _excused(case_id, field, step_no):
            failures.append(
                f"step {step_no} {field}: expected options {expected_labels!r}, got {actual_labels!r}"
            )

    field = "canned"
    for sentence in expected.get("canned") or []:
        text_value = reply.get("text") or ""
        if sentence not in text_value and not _excused(case_id, field, step_no):
            failures.append(f"step {step_no} {field}: {sentence!r} not found in reply text {text_value!r}")

    field = "text"
    pinned_text = expected.get("text")
    if pinned_text is not None and expected.get("_pin_text"):
        actual_text = reply.get("text")
        if actual_text != pinned_text and not _excused(case_id, field, step_no):
            failures.append(f"step {step_no} {field}: expected {pinned_text!r}, got {actual_text!r}")


# --------------------------------------------------------------------------- #
# The test
# --------------------------------------------------------------------------- #


def _replay_params() -> list:
    """One `pytest.param` per case, marked `xfail(strict=True)` for the two ids in
    `_XFAIL_REPLAY_CASES` and plain otherwise - a decorator-level mark, not a
    runtime `request.node.add_marker` call from inside the test body: measured
    that a fixture-named parameter with a Python default (`request=None`, to keep
    the two direct, non-parametrized calls below working) is EXCLUDED from
    pytest's fixture injection entirely (`_pytest.compat.getfuncargnames` skips
    defaulted params), so `request` was always `None` even under real
    parametrized collection and the marker never landed. `pytest.param(...,
    marks=...)` has no such ambiguity and does not touch `test_replay`'s own
    signature at all."""
    params = []
    for case_path, case_id in zip(CASE_FILES, CASE_IDS):
        marks = []
        if case_id in _XFAIL_REPLAY_CASES:
            marks.append(pytest.mark.xfail(strict=True, reason=_XFAIL_REPLAY_CASES[case_id]))
        params.append(pytest.param(case_path, marks=marks, id=case_id))
    return params


@pytest.mark.parametrize("case_path", _replay_params())
def test_replay(case_path: Path, session_factory, stub_parser, monkeypatch) -> None:
    from app.services.chatbot import engine as engine_mod

    case_id = str(case_path.relative_to(REPLAY_ROOT))
    if case_id in PENDING_CASES:
        pytest.skip(PENDING_CASES[case_id])
    payload = json.loads(case_path.read_text())
    turns = payload.get("turns") or []
    assert turns, f"{case_id}: no turns recorded"

    contact_id = ((turns[0].get("envelope") or {}).get("contact") or {}).get("id") or 999999999
    _seed_contact(session_factory, contact_id=contact_id)
    _seed_case_customers(session_factory, case_id=case_id)
    _seed_case_products(session_factory, case_id=case_id)
    _seed_case_promotions(session_factory, case_id=case_id)
    # T4 (coordinator ruling, 16 Sep 2026): `_seed_contact` above leaves a brand-new
    # contact's `session_vars` at `{}`. A recorded chain's step 1 ran against the
    # SOURCE contact's REAL prior session (never itself a recorded turn in this file)
    # - `scripts/chatbot_record_turn.py` now captures that as `received_session_vars`
    # (the "received" trace stage's own `raw.session_vars`, PII-scrubbed). Writing it
    # here, BEFORE step 1, is what makes a step-1 tool call that carried ids/focus
    # from real prior conversation state reachable in replay - measured as the single
    # largest remaining cluster after the two signed DIVERGENCES.md rulings (24 files
    # failing on `entity_ids` alone). A case recorded before this field existed
    # (`received_session_vars` absent/null) is a no-op, unchanged from before - same
    # pattern `_apply_switches` already uses for a pre-existing field.
    chain_start_session_vars = turns[0].get("received_session_vars")
    if chain_start_session_vars:
        _write_session_vars(session_factory, contact_id=contact_id, payload=chain_start_session_vars)

    failures: list[str] = []
    for step_no, turn in enumerate(turns, start=1):
        _apply_switches(session_factory, turn.get("switches"))
        tool_calls = _install_stubs(monkeypatch, stub_parser, turn=turn)
        envelope = _build_envelope(turn, message_id=f"ZZT-replay-{case_id}-{step_no}")
        result = engine_mod.run_turn(envelope, session_factory=session_factory)

        expected = turn.get("expected") or {}
        _compare(case_id, step_no, expected, result, failures, turn=turn, tool_calls=tool_calls)

        if step_no < len(turns) and result.session_patch is not None:
            _write_session_vars(session_factory, contact_id=contact_id, payload=result.session_patch)

    assert not failures, (
        f"{case_id}: {len(failures)} structural divergence(s) with no signed "
        f"DIVERGENCES.md entry:\n  " + "\n  ".join(failures)
    )


def test_no_case_files_found_is_reported_not_silently_skipped() -> None:
    """A parametrize list of zero cases collects zero tests and reports nothing red -
    exactly the failure mode AC-1590 exists to prevent. This one always collects and
    fails loudly if the corpus is empty, so an empty `replay_turns/` tree is a red
    suite rather than a quiet, misleadingly-green one."""
    assert CASE_FILES, "tests/chatbot/replay_turns/ has no recorded cases - the S6 gate is empty"


def test_divergences_file_exists() -> None:
    assert DIVERGENCES_PATH.exists(), "tests/chatbot/replay_turns/DIVERGENCES.md is missing"


# --------------------------------------------------------------------------- #
# R-A: PENDING-LIVE-RERUN.md skip rule (captain ruling, 17 Sep 2026).
# --------------------------------------------------------------------------- #


def test_pending_live_rerun_file_exists() -> None:
    assert PENDING_PATH.exists(), "tests/chatbot/replay_turns/PENDING-LIVE-RERUN.md is missing"


class TestPendingLiveRerunParsing:
    SAMPLE = (
        "## Composite / cascading chains (2 files)\n\n"
        "Every file below fails on 3+ of the 5 comparison fields across many steps.\n\n"
        "`console/case-{008,010}`, `prod_sample/demand-qty-900000017`.\n\n"
        "Not exhaustively broken down per file this session.\n\n"
        "## Some other heading, checked live (tester 20)\n\n"
        "This prose sentence mentions `console/case-099-not-a-list-item` inline as an "
        "example - it must NOT be swallowed, only a paragraph that IS purely the list "
        "counts.\n\n"
        "`console/case-030-a-second-heading-case`.\n"
    )

    def test_expands_brace_list_into_one_stem_per_option(self) -> None:
        stems = _parse_pending_stems(self.SAMPLE)
        assert stems["console/case-008"] == "Composite / cascading chains (2 files)"
        assert stems["console/case-010"] == "Composite / cascading chains (2 files)"

    def test_a_stem_with_no_braces_is_kept_as_is(self) -> None:
        stems = _parse_pending_stems(self.SAMPLE)
        assert "prod_sample/demand-qty-900000017" in stems

    def test_missing_section_yields_no_stems(self) -> None:
        assert _parse_pending_stems("# Some other doc\n\nno matching section here.\n") == {}

    def test_a_second_heading_is_also_read_not_just_the_first(self) -> None:
        # Generalization: a stem listed under ANY heading is picked up (one
        # parser, one rule for the whole file), tagged with THAT heading's title.
        stems = _parse_pending_stems(self.SAMPLE)
        assert stems["console/case-030-a-second-heading-case"] == "Some other heading, checked live (tester 20)"

    def test_a_backtick_mention_inside_prose_is_not_a_pending_stem(self) -> None:
        stems = _parse_pending_stems(self.SAMPLE)
        assert "console/case-099-not-a-list-item" not in stems

    def test_stem_matches_a_real_case_filename_with_a_suffix(self) -> None:
        assert _stem_matches_case("console/case-008-console-abcd1234", "console/case-008") is True

    def test_stem_does_not_match_a_case_sharing_only_a_leading_digit_run(self) -> None:
        # A hypothetical case id "..-9000000080-.." must NOT be swallowed by the
        # stem "..-900000008" merely because it starts with the same digits.
        assert _stem_matches_case("prod_sample/business-query-9000000080-chain", "prod_sample/business-query-900000008") is False

    def test_stem_matches_an_exact_case_id_with_no_suffix(self) -> None:
        assert _stem_matches_case("console/handbuilt-rp-001", "console/handbuilt-rp-001") is True

    def test_pending_case_ids_maps_every_matching_case_id_to_a_heading_named_reason(self) -> None:
        stem_reasons = {"console/case-008": "Composite / cascading chains (2 files)"}
        case_ids = ["console/case-008-console-abcd1234.json", "console/case-011-console-zzzz9999.json"]
        result = _pending_case_ids(stem_reasons, case_ids)
        assert list(result) == ["console/case-008-console-abcd1234.json"]
        reason = result["console/case-008-console-abcd1234.json"]
        assert "Composite / cascading chains (2 files)" in reason
        assert "PENDING-LIVE-RERUN.md" in reason

    def test_the_real_pending_file_names_owner_15sep_chain_001_but_not_002(self) -> None:
        # `owner-15sep-chain-002` is explicitly EXCLUDED by PENDING-LIVE-RERUN.md's
        # own "Named by the coordinator, checked this session" note (superseded,
        # resolved this session) - it must stay a live FAILURE, never silently
        # skipped, so a regression there is never hidden by this rule.
        chain_001 = "console/owner-15sep-chain-001-console-52b91f3f-0f51-41c1-ae04-6d35e5c79d35.json"
        chain_002 = "console/owner-15sep-chain-002-console-8113f96b-0d9f-463e-b052-4282daf04f7a.json"
        assert chain_001 in PENDING_CASES
        assert chain_002 not in PENDING_CASES

    def test_a_case_not_named_anywhere_in_pending_live_rerun_is_not_pending(self) -> None:
        assert "console/case-057-console-focus-009.json" not in PENDING_CASES

    def test_case_011_and_focus_003_stay_failures_not_swept_into_the_skip_list(self) -> None:
        # Both are genuine, unsigned, already-tracked ENGINE DEFECTS (tester 17's
        # classification, restated under "Not pending" in the real file) - they
        # must never be silently skipped, per AC-1591.
        case_011 = "console/case-011-an-out-of-range-tier-pick-keeps-the-product-in-scope.json"
        focus_003 = "console/focus-003-c-roster-survives-a-declined-escalate-offer-ac-1015-ac-1017.json"
        assert case_011 not in PENDING_CASES
        assert focus_003 not in PENDING_CASES

    def test_cluster_e_and_f_handpass_files_are_now_skipped(self) -> None:
        # These 16 files were the ones the un-generalized (single-section) parser
        # missed entirely, per the coordinator's brief - confirm the fix reaches
        # them, tagged with the heading they are actually listed under.
        handpass2 = "console/handpass2-owner-17sep-golden-win.json"
        handpass3_v26 = "console/handpass3-owner-17sep-hanlim-chinchun-all-refinement-this-month.json"
        assert handpass2 in PENDING_CASES
        assert "Clusters E" in PENDING_CASES[handpass2]
        assert handpass3_v26 in PENDING_CASES
        assert "v26" in PENDING_CASES[handpass3_v26]


class TestPendingCaseIsSkippedBeforeAnyEngineWork:
    def test_test_replay_skips_a_pending_case_without_touching_its_fixtures(self, monkeypatch) -> None:
        pending_id = next(iter(PENDING_CASES))
        case_path = REPLAY_ROOT / pending_id
        # session_factory/stub_parser are left `None`: if the skip check below
        # `payload = json.loads(...)` did not fire first, this would blow up on
        # `None()` well before the assertion - proving the skip is the FIRST thing
        # that happens, not merely that the test ends up skipped for some other
        # reason.
        with pytest.raises(pytest.skip.Exception) as exc_info:
            test_replay(case_path, session_factory=None, stub_parser=None, monkeypatch=monkeypatch)
        assert "PENDING-LIVE-RERUN.md" in str(exc_info.value)

    def test_a_case_not_in_pending_and_not_signed_still_reaches_the_engine(self, monkeypatch) -> None:
        # A case outside PENDING_CASES must NOT hit the `pytest.skip` branch - it
        # should fall through to `json.loads`, which raises a plain (non-skip)
        # error against a `session_factory=None` fixture we never gave it a real
        # DB for. This proves the skip branch is scoped to PENDING_CASES only.
        non_pending = [cid for cid in CASE_IDS if cid not in PENDING_CASES]
        assert non_pending, "every case is pending - the skip rule would be untestable"
        case_path = REPLAY_ROOT / non_pending[0]
        with pytest.raises(Exception) as exc_info:
            test_replay(case_path, session_factory=None, stub_parser=None, monkeypatch=monkeypatch)
        assert not isinstance(exc_info.value, pytest.skip.Exception)


# --------------------------------------------------------------------------- #
# _excused step granularity (harness fix, 16 Sep 2026) - a signed line may name
# one step; an unnumbered line still excuses every step of that case/field.
# --------------------------------------------------------------------------- #


class TestExcusedStepGranularity:
    def test_a_whole_case_signature_excuses_every_step(self) -> None:
        divergences = _parse_divergences(
            "- group/case-a.json: branch_kind: a whole-case rule (signed JT 2026-09-16)\n"
        )
        assert divergences == {("group/case-a.json", "branch_kind"): {None}}

    def test_a_step_scoped_signature_excuses_only_its_own_step(self) -> None:
        divergences = _parse_divergences(
            "- group/case-b.json: step 3: pending: a step-3-only rule (signed JT 2026-09-16)\n"
        )
        assert divergences == {("group/case-b.json", "pending"): {3}}

    def test_a_step_scoped_signature_does_not_excuse_a_different_step_on_the_same_field(
        self,
    ) -> None:
        divergences = _parse_divergences(
            "- group/case-c.json: step 1: pending: only step 1 (signed JT 2026-09-16)\n"
        )
        DIVERGENCES_local = divergences
        assert _excused_against("group/case-c.json", "pending", 1, DIVERGENCES_local) is True
        assert _excused_against("group/case-c.json", "pending", 2, DIVERGENCES_local) is False

    def test_a_whole_case_and_a_step_scoped_signature_coexist_per_field(self) -> None:
        divergences = _parse_divergences(
            "- group/case-d.json: tools: whole case (signed JT 2026-09-16)\n"
            "- group/case-d.json: step 2: pending: step 2 only (signed JT 2026-09-16)\n"
        )
        assert divergences == {
            ("group/case-d.json", "tools"): {None},
            ("group/case-d.json", "pending"): {2},
        }

    def test_an_unsigned_line_excuses_nothing(self) -> None:
        divergences = _parse_divergences(
            "- group/case-e.json: step 1: pending: not yet signed off, no signature marker at all\n"
        )
        assert divergences == {}


def _excused_against(
    case_id: str, field: str, step_no: int | None, divergences: dict[tuple[str, str], set[int | None]]
) -> bool:
    """Same logic as `_excused`, but against an explicit divergences map rather than
    the module-level `DIVERGENCES` global (which is read from the real
    DIVERGENCES.md at import time) - lets the step-granularity tests above stay
    independent of whatever is currently signed in the real file."""
    steps = divergences.get((case_id, field))
    if not steps:
        return False
    if None in steps:
        return True
    return step_no is not None and step_no in steps
