"""S6 RED tests - the low stock report's render envelope (#892).

`low-stock-report-acceptance-criteria.md` AC-61; `PLAN-low-stock-report.md` section S6.

The route answers one of three shapes (`ready` / `pending` / `busy`, AC-43/AC-44/AC-49) and
`present_response` turns each into the minimal envelope the chatbot lane consumes verbatim
- `response` (the reply text), `has_result`, and for this tool `attachments`, which is what
makes the engine emit a `send_attachments` action.

Red today with a `KeyError`/shape mismatch, because `present_response` has no branch for
this tool and falls through to the generic item/field envelope (or returns `raw`
unchanged), which carries neither the two lines nor `attachments`.
"""
from __future__ import annotations

import json
import re

from sorento_crm_mcp.presenters import present_response

TOOL = "crm_low_stock_report"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The 8-4-4-4-12 shape. No UUID may ever reach a customer's screen - the standing cursor
#: rule - and this tool's payload carries two of them (`run_id`, `download_id`).
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

ATTACHMENT = {
    "url": "https://cdn.example.com/exports/low-stock/x/low-stock-10092026.xlsx",
    "filename": "low-stock-10092026.xlsx",
    "mimeType": MIME,
    "attachmentType": "file",
}

READY = {
    "status": "ready",
    "run_id": "3f1b6f2a-0c4d-4a1e-9f20-6b0f0f1c2d3e",
    "as_of": "2026-09-10",
    "low_count": 12,
    "all_count": 340,
    "attachments": [ATTACHMENT],
}

PENDING = {
    "status": "pending",
    "run_id": "3f1b6f2a-0c4d-4a1e-9f20-6b0f0f1c2d3e",
    "download_id": "9a7c5e11-2b3d-4f56-8899-aabbccddeeff",
}

BUSY = {"status": "busy"}


def _envelope(payload):
    return json.loads(present_response(TOOL, json.dumps(payload)))


# --------------------------------------------------------------------- AC-61


def test_ready_envelope_two_lines_and_attachments_passthrough():
    """AC-61: two lines, no more. The date is rendered dd/mm/yyyy (the only date format
    this product writes for a reader), and the count line says how many of the planned
    products are low - the "of <m>" half is what stops "Low: 12" reading as the whole
    catalogue.

    `attachments` is handed through UNCHANGED from the route: the presenter is not the
    place that decides what a Respond.io attachment entry looks like, and re-shaping it
    here would be a second copy of that contract.
    """
    env = _envelope(READY)
    assert env["result_type"] == "low_stock_report", env
    assert env["has_result"] is True, env
    assert env["response"].splitlines() == [
        "Low stock report - as of 10/09/2026",
        "Low: 12 of 340 planned products",
    ], repr(env["response"])
    assert env["attachments"] == [ATTACHMENT], env["attachments"]


def test_pending_envelope_one_line():
    """AC-61: the turn could not wait for the file, so the bot says so and the worker
    pushes it (AC-44). `has_result` stays True - this IS the answer to what was asked, and
    a False here would send the turn down the escalate path for a report that is being
    prepared perfectly well.
    """
    env = _envelope(PENDING)
    assert env["result_type"] == "low_stock_report", env
    assert env["has_result"] is True, env
    assert env["response"] == (
        "Preparing the low stock report - it will be sent here when ready."
    ), repr(env["response"])
    assert env.get("attachments") in ([], None), (
        f"nothing to attach yet: {env.get('attachments')!r}"
    )


def test_busy_envelope_one_line():
    """AC-61 / AC-49: a plan is already running for the company, so a second one cannot
    start. The bot says when to come back rather than surfacing a 409.

    The line NAMES the report (console round 3, defect C): a bare "a plan is already
    running" left the reader guessing which plan, and the console assertion with it."""
    env = _envelope(BUSY)
    assert env["result_type"] == "low_stock_report", env
    assert env["has_result"] is True, env
    assert env["response"] == (
        "A low stock report plan is already running - try again in a minute."
    ), repr(env["response"])


def test_no_uuid_in_any_line():
    """The standing rule: no UUID in the UI, and a WhatsApp reply is UI. The ready payload
    carries `run_id` and the pending one carries `run_id` + `download_id`, so all three
    shapes are checked rather than only the one that obviously has ids in it."""
    for payload in (READY, PENDING, BUSY):
        env = _envelope(payload)
        found = _UUID_RE.search(env["response"])
        assert found is None, (
            f"{payload['status']}: a UUID reached the reply text: {found.group(0)!r} in "
            f"{env['response']!r}"
        )


# --------------------------------------------------------------------------- AC-44a
# CODER-AUTHORED (Phase 3, reviewer S1 / N6, refined by console round 3): the error shape.
# The red set covered only ready/pending/busy. First the console found a 400 rendering as
# pending; then it found that a has_result-False error routes into the inventory domain's
# GENERIC miss ("Could not find inventory - escalate to warehouse team?"). So an error is a
# TERMINAL answer rendered verbatim (has_result True), like busy/pending - the bot's own
# words, never pending and never the generic miss.

ERROR = {"status": "error", "message": "Could not run the low stock report right now."}


def test_error_status_renders_the_error_line_verbatim():
    """An `error` payload is the fixed "could not run" line, `has_result` True so the lane
    states it verbatim rather than dropping into the generic inventory miss; no attachment."""
    env = _envelope(ERROR)
    assert env["result_type"] == "low_stock_report", env
    assert env["has_result"] is True, env
    assert env["response"] == "Could not run the low stock report right now.", repr(
        env["response"]
    )
    assert env.get("attachments") in ([], None), env.get("attachments")


def test_unknown_status_and_raw_error_body_render_the_error_line_never_pending():
    """A status the presenter does not know (a raw route error body, a shape drift) must
    NOT fall through to the pending line, and must show this tool's own error wording."""
    for payload in ({"status": "boom"}, {"message": "x", "code": "company_unresolved"}, {}):
        env = _envelope(payload)
        assert env["has_result"] is True, (payload, env)
        assert env["response"] == "Could not run the low stock report right now.", (
            payload, env["response"],
        )


def test_ready_without_counts_drops_the_count_line():
    """reviewer S3 guard, presenter side: a `ready` payload missing its counts still sends
    the file, but prints only the header - never "Low: None of None"."""
    env = _envelope({**READY, "low_count": None, "all_count": None})
    assert env["has_result"] is True, env
    assert env["response"] == "Low stock report - as of 10/09/2026", repr(env["response"])
    assert env["attachments"] == [ATTACHMENT], env["attachments"]


# --------------------------------------------------------------------------- #
# Console round 3, defects C and D. CODER-AUTHORED.
#
# C: the two busies have different fixes (wait a minute vs wait for the window to roll),
#    so they get different lines, and both name the report.
# D: a scope the plan admitted nothing for rendered "Low: 0 of 0 planned products" next to
#    an empty workbook, which reads as a broken report.
# --------------------------------------------------------------------------- #


def test_busy_rate_limited_says_so_and_names_the_window():
    env = _envelope({"status": "busy", "reason": "rate_limited"})
    assert env["has_result"] is True, env
    assert env["response"] == (
        "Too many low stock reports in the last 10 minutes - try again shortly."
    ), repr(env["response"])
    assert env.get("attachments") in ([], None), env.get("attachments")


def test_busy_in_flight_reason_matches_the_default_wording():
    """An explicit `in_flight` and an absent reason render the same line - an unknown
    reason must not fall through to the rate-limit wording, which tells the caller to wait
    ten minutes for something that clears in one."""
    explicit = _envelope({"status": "busy", "reason": "in_flight"})
    implicit = _envelope({"status": "busy"})
    unknown = _envelope({"status": "busy", "reason": "something_new"})
    for env in (explicit, implicit, unknown):
        assert env["response"] == (
            "A low stock report plan is already running - try again in a minute."
        ), repr(env["response"])


def test_empty_scope_sends_one_line_and_no_attachment():
    """defect D: `all_count == 0` means the plan admitted nothing for that scope. One line,
    no file - never "Low: 0 of 0 planned products" beside an empty workbook."""
    env = _envelope({**READY, "low_count": 0, "all_count": 0, "as_of": "2026-09-14"})
    assert env["has_result"] is True, env
    assert env["response"] == (
        "Nothing was planned for that scope - no low stock report to send."
    ), repr(env["response"])
    assert env["attachments"] == [], env["attachments"]


def test_null_as_of_is_treated_as_an_empty_scope():
    """A run that froze no rows has no as-of at all; rendering "as of -" beside a count
    was the other half of the same defect."""
    env = _envelope({**READY, "as_of": None, "low_count": 0, "all_count": 0})
    assert env["response"] == (
        "Nothing was planned for that scope - no low stock report to send."
    ), repr(env["response"])
    assert env["attachments"] == [], env["attachments"]


def test_a_dry_run_pending_says_nothing_is_sent():
    """LOWSTOCK-FILTER-ASK tester finding: a console (dry run) turn never pushes the file
    to WhatsApp, so its pending line must not promise "sent here"."""
    env = _envelope({**PENDING, "dry_run": True})
    assert env["has_result"] is True, env
    assert env["response"] == (
        "Preparing the low stock report - test turn: it will be in My Downloads, "
        "nothing is sent to WhatsApp."
    ), repr(env["response"])
