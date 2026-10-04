"""End-to-end simulation of the SORENTO `ideate` brain-path turn (ideation pipeline).

Drives ``POST /api/v1/external/ideation/turn`` via FastAPI ``TestClient`` across a
MULTI-TURN WhatsApp conversation for a single contact, demonstrating the D-CONFIRM
relay + ``session_vars.ideation`` round-trip end-to-end through the REAL endpoint and
the REAL ``handle_turn`` merge logic (``app/services/ideation_turn_service.py``).

What is real vs. stubbed
------------------------
- REAL: the HTTP route, request/response schemas, ``handle_turn`` read-modify-write of
  ``session_vars`` (keep on collecting/review, MERGE the pointer, CLEAR on
  complete/duplicate while preserving every other CRM key), the confirm gate wiring.
- Round-tripped: ``session_vars`` lives in an in-memory per-contact store so each turn
  reads back the blob the previous turn wrote - exactly what a real DB would do.
- Stubbed (no live deps): the brain extractor's LLM output (``extract_ideate_turn`` →
  ``{fields, remove, confirm}``) and the shared-service ``create_idea`` HTTP call.
  If ``ideation_shared_service_url`` + ``ideation_intake_api_key`` are configured AND
  the service is reachable, the REAL ``create_idea`` is called instead (responses are
  printed; scripted-status assertions relax to structural checks in that mode).

This script changes NO feature code. Run it:

    cd sorento_crm_backend && venv/bin/python scripts/simulate_ideate_turn.py

Exit code 0 == the transcript ran clean and every scripted status / session_vars
assertion matched.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

# Ensure the backend package is importable when run from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_HERE)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

import httpx  # noqa: E402

import app.services.ideation_turn_service as svc  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.ideation_extractor import IdeateExtraction  # noqa: E402

TURN_URL = "/api/v1/external/ideation/turn"

# Accumulated markdown transcript lines (also echoed to stdout).
_LINES: list[str] = []
_FAILURES: list[str] = []


def out(line: str = "") -> None:
    print(line)
    _LINES.append(line)


def check(cond: bool, label: str) -> None:
    """Assert-but-keep-going: record failures so the whole transcript still prints."""
    mark = "PASS" if cond else "FAIL"
    if not cond:
        _FAILURES.append(label)
    out(f"    [{mark}] {label}")


# --------------------------------------------------------------------------- #
# Scripted conversation                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    label: str
    respond_io_id: str
    user_message: str
    # Brain extraction the stubbed extractor yields for THIS turn.
    fields: dict[str, str] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)
    confirm: bool = False
    # create_idea response the stubbed shared-service returns for THIS turn.
    create_idea: dict[str, Any] = field(default_factory=dict)
    # Expectations the sim asserts against the relayed response.
    expect_status: str = ""
    expect_link: bool = False
    expect_ideation_present: bool = True
    expect_ideation_status: str | None = None
    expect_ideation_missing: list[str] | None = None
    note: str = ""


_ALICE = "rio-alice-001"
_BOB = "rio-bob-002"

# A pre-existing, unrelated CRM key that MUST survive every ideate turn (AC-16).
_CRM_KEY = "referenced_result_set"
_CRM_VALUE = [{"id": "prev-stock-query", "sku": "ABC-123"}]

_LINK = "https://fe-sorento.foundryx.my/ideas/idea-9001"

SCRIPT: list[Turn] = [
    # ---- Alice: full collecting -> review -> revise -> remove -> re-add -> confirm ----
    Turn(
        label="T1 incomplete -> collecting (pointer set)",
        respond_io_id=_ALICE,
        user_message="I've got an idea - the system should remind me before a quotation expires",
        fields={"what": "remind me before a quotation expires"},
        create_idea={
            "draft_id": "d-100",
            "status": "collecting",
            "captured": {"what": "remind me before a quotation expires"},
            "missing": ["module", "who", "impact"],
            "reply_text": "Love it. Which module is this about, who benefits, and what's the impact?",
        },
        expect_status="collecting",
        expect_ideation_status="collecting",
        expect_ideation_missing=["module", "who", "impact"],
        note="First turn: draft_id OMITTED in the create_idea payload; pointer persisted.",
    ),
    Turn(
        label="T2 partial -> collecting (missing shrinks)",
        respond_io_id=_ALICE,
        user_message="it's mainly for the sales team",
        fields={"who": "sales team"},
        create_idea={
            "draft_id": "d-100",
            "status": "collecting",
            "captured": {"what": "remind me before a quotation expires", "who": "sales team"},
            "missing": ["module", "impact"],
            "reply_text": "Got the sales team. Which module, and what's the impact?",
        },
        expect_status="collecting",
        expect_ideation_status="collecting",
        expect_ideation_missing=["module", "impact"],
        note="Continuation: draft_id d-100 passed through; missing shrank from 3 -> 2.",
    ),
    Turn(
        label="T3 rest supplied -> REVIEW (echo + confirm ask; NOT cleared)",
        respond_io_id=_ALICE,
        user_message="module is Order Management and it'd save us chasing expired quotes",
        fields={"module": "Order Management", "impact": "saves chasing expired quotes"},
        create_idea={
            "draft_id": "d-100",
            "status": "review",
            "captured": {
                "what": "remind me before a quotation expires",
                "who": "sales team",
                "module": "Order Management",
                "impact": "saves chasing expired quotes",
            },
            "missing": [],
            "reply_text": (
                "Here's what I have - What: remind before a quotation expires | Who: sales team "
                "| Module: Order Management | Impact: saves chasing expired quotes. "
                "Reply 'confirm' to submit, or tell me what to change."
            ),
        },
        expect_status="review",
        expect_ideation_status="review",
        expect_ideation_missing=[],
        note="All fields in -> REVIEW gate. Pointer KEPT (never cleared before confirm).",
    ),
    Turn(
        label="T4 'change team to Operations' -> review re-echoed (merged)",
        respond_io_id=_ALICE,
        user_message="actually change the team to Operations",
        fields={"who": "Operations"},
        confirm=False,  # not a confirmation, just a revision while in review
        create_idea={
            "draft_id": "d-100",
            "status": "review",
            "captured": {
                "what": "remind me before a quotation expires",
                "who": "Operations",
                "module": "Order Management",
                "impact": "saves chasing expired quotes",
            },
            "missing": [],
            "reply_text": (
                "Updated Who -> Operations. What: remind before a quotation expires | "
                "Who: Operations | Module: Order Management | Impact: saves chasing expired quotes. "
                "Confirm?"
            ),
        },
        expect_status="review",
        expect_ideation_status="review",
        note="Revise while reviewing: fields merged, still REVIEW, pointer persists.",
    ),
    Turn(
        label="T5 'remove the impact' -> collecting re-opened (per schema)",
        respond_io_id=_ALICE,
        user_message="remove the impact, I'm not sure yet",
        remove=["impact"],
        create_idea={
            "draft_id": "d-100",
            "status": "collecting",
            "captured": {
                "what": "remind me before a quotation expires",
                "who": "Operations",
                "module": "Order Management",
            },
            "missing": ["impact"],
            "reply_text": "Cleared the impact. What's the impact when you're ready?",
        },
        expect_status="collecting",
        expect_ideation_status="collecting",
        expect_ideation_missing=["impact"],
        note="Removing a required field re-opens COLLECTING; pointer still not cleared.",
    ),
    Turn(
        label="T6 add more info -> REVIEW re-echoed (merged)",
        respond_io_id=_ALICE,
        user_message="the impact is it saves us about 2 hours a week",
        fields={"impact": "saves about 2 hours a week"},
        create_idea={
            "draft_id": "d-100",
            "status": "review",
            "captured": {
                "what": "remind me before a quotation expires",
                "who": "Operations",
                "module": "Order Management",
                "impact": "saves about 2 hours a week",
            },
            "missing": [],
            "reply_text": (
                "Great. What: remind before a quotation expires | Who: Operations | "
                "Module: Order Management | Impact: saves ~2 hours a week. Confirm?"
            ),
        },
        expect_status="review",
        expect_ideation_status="review",
        expect_ideation_missing=[],
        note="Re-supplying the field returns to REVIEW; impact merged in.",
    ),
    Turn(
        label="T7 'yes confirm' -> COMPLETE + link; pointer CLEARED",
        respond_io_id=_ALICE,
        user_message="yes, confirm that",
        confirm=True,
        create_idea={
            "draft_id": "d-100",
            "status": "complete",
            "captured": {},
            "missing": [],
            "reply_text": "Logged! You can track your idea here.",
            "link": _LINK,
        },
        expect_status="complete",
        expect_link=True,
        expect_ideation_present=False,
        note="Explicit confirm -> COMPLETE + product-domain link; session_vars.ideation CLEARED; CRM key intact.",
    ),
    # ---- Bob: one-shot fully-complete first turn STILL returns review until confirm ----
    Turn(
        label="T8 (Bob) one-shot complete FIRST turn -> still REVIEW (not complete)",
        respond_io_id=_BOB,
        user_message=(
            "Idea: in Inventory, warehouse ops should get a low-stock alert - "
            "it prevents stockouts."
        ),
        fields={
            "what": "low-stock alert",
            "who": "warehouse ops",
            "module": "Inventory",
            "impact": "prevents stockouts",
        },
        create_idea={
            "draft_id": "d-200",
            "status": "review",
            "captured": {
                "what": "low-stock alert",
                "who": "warehouse ops",
                "module": "Inventory",
                "impact": "prevents stockouts",
            },
            "missing": [],
            "reply_text": "Everything's here - What/Who/Module/Impact all set. Reply 'confirm' to submit.",
        },
        expect_status="review",
        expect_ideation_status="review",
        note="Even a first turn with EVERY field routes through REVIEW; no auto-complete.",
    ),
    Turn(
        label="T9 (Bob) 'confirm' -> COMPLETE + link; pointer CLEARED",
        respond_io_id=_BOB,
        user_message="confirm",
        confirm=True,
        create_idea={
            "draft_id": "d-200",
            "status": "complete",
            "captured": {},
            "missing": [],
            "reply_text": "Submitted! Track it here.",
            "link": "https://fe-sorento.foundryx.my/ideas/idea-9002",
        },
        expect_status="complete",
        expect_link=True,
        expect_ideation_present=False,
        note="Confirm closes Bob's draft: COMPLETE + link, pointer cleared.",
    ),
]


# --------------------------------------------------------------------------- #
# Seams / harness                                                             #
# --------------------------------------------------------------------------- #
class _FakeContact:
    def __init__(self, phone_number: str, session_vars: dict):
        self.phone_number = phone_number
        self.session_vars = session_vars


def _shared_service_reachable(base_url: str) -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            client.get(base_url)
        return True
    except Exception:
        return False


def build_client(script: list[Turn]):
    """Patch every DB/LLM/HTTP seam of the turn service and return a TestClient +
    the in-memory session_vars store + whether create_idea is REAL."""
    from fastapi.testclient import TestClient

    from app.dependencies import get_db, get_external_api_user
    from app.main import app

    # Per-contact in-memory session_vars store, seeded with a pre-existing CRM key.
    store: dict[str, dict[str, Any]] = {
        _ALICE: {_CRM_KEY: [dict(x) for x in _CRM_VALUE]},
        _BOB: {_CRM_KEY: [dict(x) for x in _CRM_VALUE]},
    }
    phones = {_ALICE: "+60100000004", _BOB: "+60100000003"}

    # Per-(contact,message) scripted extraction, so the endpoint's real extractor call
    # is replaced by the scripted D-CONFIRM structured update.
    extraction_by_key: dict[tuple[str, str], IdeateExtraction] = {}
    create_idea_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for t in script:
        key = (t.respond_io_id, t.user_message)
        extraction_by_key[key] = IdeateExtraction(
            fields=dict(t.fields), remove=list(t.remove), confirm=t.confirm
        )
        create_idea_by_key[key] = t.create_idea

    # Track which respond_io_id / message the current turn is for (endpoint passes them
    # into handle_turn; we recover them from the seam calls).
    current = {"rio": None, "msg": None}

    def _fake_get_contact_row(_db, respond_io_id):  # noqa: ANN001
        current["rio"] = respond_io_id
        return _FakeContact(phones[respond_io_id], dict(store[respond_io_id]))

    def _fake_resolve_product_id(_db):  # noqa: ANN001
        return "prod-uuid-ideation-binding"

    def _fake_extract(_db, *, message_text, status=None, missing=None, **_kw):  # noqa: ANN001
        current["msg"] = message_text
        rio = current["rio"]
        key = (rio, message_text)
        ex = extraction_by_key.get(key, IdeateExtraction())
        # Mirror the real deterministic guard: confirm only counts while reviewing.
        if status != "review":
            ex = IdeateExtraction(fields=ex.fields, remove=ex.remove, confirm=False)
        return ex

    real_create_idea = bool(settings.ideation_shared_service_url and settings.ideation_intake_api_key)
    if real_create_idea:
        real_create_idea = _shared_service_reachable(settings.ideation_shared_service_url)

    def _fake_call_create_idea(_base_url, _api_key, payload):  # noqa: ANN001
        rio = current["rio"]
        msg = payload.get("message_text")
        return dict(create_idea_by_key.get((rio, msg), {}))

    def _overwrite(_db, *, respond_io_id, state):  # noqa: ANN001
        store[respond_io_id] = dict(state)
        return store[respond_io_id]

    svc._get_contact_row = _fake_get_contact_row  # type: ignore[assignment]
    svc._resolve_product_id = _fake_resolve_product_id  # type: ignore[assignment]
    svc.extract_ideate_turn = _fake_extract  # type: ignore[assignment]
    svc.overwrite_for_contact = _overwrite  # type: ignore[assignment]
    if not real_create_idea:
        svc.call_create_idea = _fake_call_create_idea  # type: ignore[assignment]
    # Ensure config gates are open so handle_turn does not fail-closed.
    settings.ideation_shared_service_url = settings.ideation_shared_service_url or "https://shared.test"
    settings.ideation_intake_api_key = settings.ideation_intake_api_key or "intake-key"

    # No-op the integration_log write (no DB in the sim).
    import app.api.v1.external.ideation as ep

    class _NoLogService:
        def __init__(self, _db):  # noqa: ANN001
            pass

        def create_integration_log(self, *_a, **_k):  # noqa: ANN001
            return None

    ep.IntegrationLogService = _NoLogService  # type: ignore[assignment]

    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_external_api_user] = lambda: {"id": "system"}

    return TestClient(app), store, real_create_idea


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #
def run() -> int:
    client, store, real_create_idea = build_client(SCRIPT)

    out("# Ideation `ideate` turn - end-to-end SORENTO simulation transcript")
    out()
    out(
        "Drives `POST /api/v1/external/ideation/turn` via FastAPI TestClient across a "
        "multi-turn WhatsApp conversation. The endpoint, request/response schemas, and "
        "`handle_turn` session_vars merge logic are REAL; the brain extractor and "
        "`create_idea` are stubbed (no live LLM / shared-service)."
    )
    out()
    mode = "REAL shared-service create_idea" if real_create_idea else "STUBBED create_idea (D-CONFIRM sequence)"
    out(f"- create_idea mode: **{mode}**")
    out(f"- Pre-existing CRM key seeded on every contact: `{_CRM_KEY}` = `{json.dumps(_CRM_VALUE)}`")
    out()
    out("---")
    out()

    for t in SCRIPT:
        out(f"## {t.label}")
        out(f"- Contact: `{t.respond_io_id}`")
        out()
        out(f"**USER (WhatsApp):** {t.user_message}")
        out()
        out(
            "**Brain extraction {fields, remove, confirm}:** "
            f"`{json.dumps({'fields': t.fields, 'remove': t.remove, 'confirm': t.confirm})}`"
        )
        out()

        resp = client.post(
            TURN_URL,
            json={"respond_io_id": t.respond_io_id, "message_text": t.user_message},
        )
        check(resp.status_code == 200, f"HTTP 200 (got {resp.status_code})")
        body = resp.json()
        status_val = body.get("status")
        reply_text = body.get("reply_text")
        link = body.get("link")
        sv = body.get("session_vars", {})
        ideation = sv.get("ideation")

        out(f"**Relayed status:** `{status_val}`")
        out(f"**Relayed reply_text:** {reply_text}")
        out(f"**Relayed link:** {link if link else '(none)'}")
        out(f"**session_vars.ideation:** `{json.dumps(ideation)}`")
        out(f"**Full session_vars keys:** `{json.dumps(sorted(sv.keys()))}`")
        if t.note:
            out()
            out(f"> {t.note}")
        out()

        # ---- assertions (structural only in real-service mode) ----
        if not real_create_idea:
            check(status_val == t.expect_status, f"status == {t.expect_status!r}")
            if t.expect_link:
                check(bool(link), "link present on complete")
            else:
                check(link is None, "no link before complete")
            if t.expect_ideation_present:
                check(ideation is not None, "session_vars.ideation PRESENT (not cleared)")
                if ideation is not None:
                    if t.expect_ideation_status is not None:
                        check(
                            ideation.get("status") == t.expect_ideation_status,
                            f"ideation.status == {t.expect_ideation_status!r}",
                        )
                    if t.expect_ideation_missing is not None:
                        check(
                            ideation.get("missing") == t.expect_ideation_missing,
                            f"ideation.missing == {t.expect_ideation_missing}",
                        )
                    check("draft_id" in ideation, "ideation carries draft_id")
                    check("updated_at" in ideation, "ideation carries updated_at")
            else:
                check(ideation is None, "session_vars.ideation CLEARED")
        else:
            check(status_val is not None, "real service returned a status")

        # The pre-existing CRM key must survive EVERY turn (AC-16).
        check(sv.get(_CRM_KEY) == _CRM_VALUE, f"CRM key `{_CRM_KEY}` intact")
        out()
        out("---")
        out()

    out("## Result")
    if _FAILURES:
        out(f"- FAILED assertions: {len(_FAILURES)}")
        for f in _FAILURES:
            out(f" - {f}")
    else:
        out("- All status / session_vars / CRM-key assertions PASSED.")

    return 1 if _FAILURES else 0


def main() -> int:
    rc = run()
    # Save the transcript.
    transcript_path = os.path.join(
        _BACKEND_ROOT,
        "..",
        "documentation",
        "plans",
        "ideation",
        "ideation-ideate-turn-simulation-transcript.md",
    )
    transcript_path = os.path.abspath(transcript_path)
    try:
        with open(transcript_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_LINES) + "\n")
        print(f"\n[transcript saved] {transcript_path}")
    except OSError as exc:
        print(f"\n[WARN] could not save transcript: {exc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
