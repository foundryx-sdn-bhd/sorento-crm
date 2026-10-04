"""Phase 2 RED tests - S4 lane wiring for the outstanding report.

`documentation/plans/chatbot/PLAN-chatbot-outstanding-report.md` "S4 on main" (13 numbered
wiring points) + "Tester's list"; `chatbot-outstanding-report-acceptance-criteria.md`
AC-1130 to AC-1142 (S4 slice). Written BEFORE any of the wiring exists - S1 to S3 (the
presenter, the route/service, the MCP tool) are already merged on this branch; this file is
about the CHATBOT LANE consuming them.

**Two ways a test in this file is red, both legitimate (see each class docstring for which
one it is):**

1. A literal/table assertion against code that already exists (`gate.ALLOWED["order"]`,
   `fetch.DATE_PARAMS`, the parser prompt text, `fetch.CHATBOT_READ_ONLY_TOOLS`) - fails
   because the entry is simply absent yet.
2. A behavioural assertion through `app.services.chatbot.lanes.business.run_fetch` (the
   real call site `engine.run_turn` uses for the fetch step) or through a full
   `engine.run_turn` turn with the parser/MCP/resolver seams faked, the same pattern
   `tests/chatbot/test_foundre_rung_end_to_end.py` and `test_pass4_item1a_team_clarify_
   consumed.py` already use for an analogous "the CRM decides, not n8n" mechanism. These
   fail today because `select_tool` always returns the domain's first-listed tool
   (`crm_order_management_orders_list`) regardless of `order_status`, so the fake MCP
   client below sees either the wrong tool name or no call at all.

**Tester's own choices, made explicit** (the plan/UAC describe the behaviour, not every
function name - same latitude `test_s6b_fetch_lane.py` took):

* The reply-composition tests (AC-1130, AC-1141) assert only the EXACT strings the plan
  itself pins verbatim in "The reply (contract for Phase 1)" / "Scope question (D2)" / S4
  point 12 - copy the CRM composes itself, independent of how the two-block REPORT text
  gets rendered (a question this file deliberately stays agnostic on: whether the MCP
  server renders it server-side via `view=render` + `present_response`, per S4 point 5, or
  the backend lane builds it - both are live readings of the current code, see
  `sorento_crm_mcp/sorento_crm_mcp/presenters.py`'s own module docstring above
  `_outstanding_report`). AC-1132/AC-1135/AC-1138 therefore assert on the MCP CALL ARGS and
  the SESSION STATE (`pending`, `selection_context`, `last_result_set`,
  `outstanding_filters`) - both unambiguous per the plan - and only require the reply text
  be non-empty.
* `outstanding_filters` (S4 point 4's own name) is assumed to be a new top-level session
  variable, written the same turn `pending` is (`tail/compile_state.py`), read back the
  next turn - the plan names it explicitly, this file just picks its shape (a dict with
  `product_code` / `date_filter_start` / `date_filter_end` / `customer_ids` /
  `warehouse_codes`).
* `services.resolve_warehouse_token(db, token)` (AC-1133) is a new function name this file
  proposes for "the warehouse resolver in `lanes/business/services.py`" the plan names but
  does not spell.
* `fetch.ORDER_STATUS_TO_SCOPE` (AC-1131's fetch half) is a new table name this file
  proposes for "fetch maps so_outstanding/do_outstanding/outstanding_both to scope
  so/do/both".

Postgres only (`session_factory`, blank schema). Every row seeded here.

SESSION-SHAPE PORT DONE (AC-1592, queue item 2c, 16 Sep 2026): the findings below (2-4,
diagnosed by tester 12/13) are FIXED - coder 11's `2453e64d0`/`57bb7abe8` land "no offer
on a hit" (finding 2), "the lane's own question wins" (finding 3), and "scope question
arms" (finding 4). `pending`/`selection_context`/`last_result_set`/`outstanding_filters`
-> `open_question` (kind/options/payload.filters), `_session_of(session_factory)
["variables"]` -> a reader of the current flat five-key shape, ported across every call
site the session-shape rename alone unblocks (this session, tester 13). Two classes of
finding along the way, both preserved as-measured, not silently forced green:

* **`_seed_open_outstanding_detail`/`_seed_open_outstanding_scope`'s hand-written
  `variables` dict is invisible to the current engine** - `session_state.five_keys()`'s
  legacy-nest fallback only re-checks the FIVE_KEY names inside `variables`, never
  these files' own older field names (`selection_context`, `pending`, ...), so a test
  seeded this way starts every turn with NO prior state at all (measured: `open_question`
  reads back `{}`). Where a test's own assertion only needed the "before" state to be
  ABSENT/CLOSED, this is harmless (the port is still correct, just less of a guard than
  it reads). Where a test's own claim was "X must SURVIVE a later turn", the seed cannot
  establish X in the first place - two such tests
  (`TestDetailOfferIsSticky::test_detail_offer_survives_a_casual_turn`/
  `test_detail_offer_survives_an_out_of_range_pick`) were re-armed with a REAL turn 1
  instead (the same pattern `TestDetailPickRerunsToolWithDetail::test_a_real_hit_arms_
  the_pending_and_the_next_1_reruns_with_detail_so` already used) - see each test's own
  docstring.
* **Three of the sixteen now reveal a DIFFERENT, still-open gap once armed honestly**:
  the outstanding lane's own open question does not yet win over a generic business-
  query/casual read on every call path - a real "1"/"thanks"/"3" turn over a genuinely
  armed `outstanding_detail` offer still reaches `crm_order_management_orders_list`
  instead of resolving against the offer. This is the SAME remaining wiring the file's
  original 43-red group already named (contracts 38/39, coder 12's queue per the
  coordinator's own routing, 16 Sep 2026) - these three simply reach it via the
  session-shape port rather than staying masked behind a `KeyError`. Kept red, not
  worked around; each test's own docstring names the finding.

CONTRACTS 38/39 LANDED (coder 12, `2a69c51b5`, 16 Sep 2026) - "the outstanding report's
own two questions are answered": the ~43-red group above dropped to 17 the moment this
session merged it, without any test-file edit. Tester 14 (this session) then ported the
remaining TWO stale `_session_of(session_factory)["variables"]` reads (mechanical,
`TestExactProductCodeWinsOverSiblings::test_the_scope_question_arm_stores_the_typed_code_
too`, `TestOwnerRoundFivePickerAndOfferScope::test_a_customer_pick_after_an_outstanding_
ask_arms_the_scope_question`) plus every trailing `["variables"]` read still sitting
downstream of a still-failing assertion, so a future fix does not also trip a stale
`KeyError` the moment its own earlier assertion goes green. Item 2's landed fix
(`c09f12183`, customer roster DO stamps) also turned `TestOutstandingAskPickerHasNoDelive
ryHint::test_plain_delivery_ask_picker_keeps_the_do_hint` green on its own, unrelated to
any test-file edit. 74 passed, 15 failed (was 72/17) at that point.

COORDINATOR RULING, 17 Sep 2026 (items c/d): the S6 cluster-4 owner ruling ("an open
question is answered only when the PARSER says so"; a casual aside CARRIES the pending
silently, never re-prints it) applies to EVERY pending kind including the two outstanding
ones - RETIRED three of `TestDetailOfferIsSticky`'s reds
(`test_detail_offer_survives_a_casual_turn`, `test_a_casual_turn_under_an_open_offer_
reprints_it_rather_than_greeting`, `test_the_reprint_uses_the_same_offer_form_the_report_
used`) whose own premise (re-print) the ruling amends AC-1143c/R22 away from; replacement
coverage is `test_rearch_s6_open_question_parser.py::TestAbsentAnswerCarriesThePendingWith
outReprinting`, parametrized over every `PENDING_KINDS` value. Also fixed
`test_detail_offer_survives_an_out_of_range_pick`'s own wrong premise - "3" is the valid
"Both lists" option on an `outstanding_both` offer (R14), not out of range; changed to "4".
76 passed, 10 failed after this ruling.

**The remaining 10 are NOT one uniform gap - measured this session, three distinct
findings**, each already confirmed for the right reason (a real behavioural
AssertionError against a real fetch/reply, never a fixture bug):

* **R15 refinement (date/location under an open offer) - a coder is wiring it, stays red
  by design** (`TestDateNarrowingUnderAnOpenOffer` x5 +
  `TestScopeQuestionCarriesTheFullHeader::test_refinement_reask_prints_customer_too`,
  named by its own docstring as "R19 x R15", the same mechanism - 6 of the 10): a
  date-only/location-only turn over a genuinely armed offer still reaches
  `crm_order_management_orders_list` instead of resolving against (or re-printing) the
  open question.
* **The scope question's own Customer line prints the roster's generic family label
  instead of the real resolved rows, and drops a carried date filter**
  (`TestScopeQuestionCustomerLineMatchesReportHeader`,
  `TestAllOnTheCustomerPickerKeepsTheQuestion::test_all_on_the_customer_picker_arms_the_
  scope_question_for_every_family` - 2 of the 10): measured directly - the reply's
  `Customer:` line reads `"CHIN CHUN HARDWARE SDN BHD"` (one name) where the real picked
  rows are three, and `Order date: all` where a carried `2026-01-01`/`2026-12-31` window
  should print.
* **`TestOpenOfferCanBeLeft` x2** (`test_a_second_unreadable_turn_closes_the_offer`,
  `test_a_refinement_after_one_reprint_still_works`): the same "offer/question does not
  yet win over a generic business read" gap, on the CLOSING side (a second unreadable
  turn / a refinement after a reprint) rather than the R15-refinement side above -
  distinct from that group because the FIRST unreadable turn (the reprint itself) is
  already green; not a re-print-premise defect, a real remaining fetch.

Not a tester-owned fix (behavioural engine gaps, not a test/fixture defect) - flagged for
the coordinator to route, same as this file's original queue-2 flag.
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.models.user import SystemSetting
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business.services import (
    AnswerServices,
    FetchServices,
    ResolveGateServices,
)
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import customer as mc_customer
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output, seeded  # noqa: F401
from tests.chatbot.test_engine import stub_access, stub_parser  # noqa: F401

PRODUCT_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PRODUCT_CODE = "SRTWT7445"
CUSTOMER_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CUSTOMER_NAME = "Dealer A Sdn Bhd"

_XFAIL_HMM_OVER_OPEN_OFFER_FIRES_A_FETCH = (
    "a casual 'hmm' with zero entities/domain_hint over an open outstanding_detail "
    "pending fires a real fetch and clears the pending, instead of re-printing per "
    "R22(b)/AC-1168 - S6 cluster 4 owner ruling vs R22 (follow-up, PR #952)"
)


def _qf(**overrides: Any) -> dict[str, Any]:
    """`_parser_output`, defaulted to an "order" domain product ask."""
    base = dict(
        domain_hint="order",
        intent_hint="check_order",
        entities=[
            {
                "raw": PRODUCT_CODE,
                "hint": "product",
                "canonical_code": None,
                "current_message": True,
                "confident": True,
            }
        ],
    )
    base.update(overrides)
    return _parser_output(**base)


def _resolve_services(matches: dict[str, dict[str, Any]]) -> ResolveGateServices:
    """A `resolve_entity` seam that answers only the tokens `body["tokens"]` actually
    asks about, out of `matches` - never every raw token in `matches` unconditionally.

    Fixed 17 Sep 2026 (coder 15's measurement, coordinator relay): the old
    unconditional version answered a stale carried token even when the real turn's own
    resolve-gate body never named it, which the engine now hands over correctly since
    `with_carried_entities` gained `unsettled_only` (item 1, `6dc96c237`) - before that
    fix the resolver was never called on the affected turn at all, so the stub's own
    dishonesty never had a turn to bite on. `_resolve_gate.py`'s real seam only ever
    resolves the tokens it is asked about; a test double must keep the same contract."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        asked = list(body.get("tokens") or [])
        resolved = {raw: match for raw, match in matches.items() if raw in asked}
        return {
            "tokens": asked,
            "resolutions": [
                {"raw": raw, "token": raw, "matches": [match]}
                for raw, match in resolved.items()
            ],
            "unresolved_tokens": [raw for raw in asked if raw not in resolved],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


def _present_response():
    """The REAL MCP presenter, imported the way
    `tests/chatbot/test_field_reveal_keys_pinned_to_catalog.py` imports the catalogue:
    append the `sorento_crm_mcp` that sits beside THIS checkout's backend, so a stale
    editable install in the shared venv cannot win (the "lane backend imports primary
    MCP catalog" gotcha). Skipped, never failed, where the package is absent - the
    backend container legitimately does not carry it."""
    repo_root = Path(__file__).resolve().parents[3]
    mcp_root = repo_root / "sorento_crm_mcp"
    if str(mcp_root) not in sys.path:
        sys.path.append(str(mcp_root))
    try:
        from sorento_crm_mcp.presenters import present_response
    except ImportError:  # pragma: no cover - only where the package is not on disk
        pytest.skip("sorento_crm_mcp is not importable in this environment")
    return present_response


def _report_route_body(report: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """The body `GET /api/v1/order-management/outstanding-report` returns for `args`.

    The route drops the block of a scope that was not asked (AC-1117), and ECHOES the
    caller's own `warehouse_codes` / `location_token` / `so_refused` / `detail` onto the
    body so the presenter can render the header, the refusal line and the detail swap
    from the response alone. Reproduced here rather than hand-writing a fixture per
    test, so this double stays the route's shape as the route grows."""
    body = copy.deepcopy(report)
    scope = str(args.get("scope") or "both")
    if scope not in ("so", "both"):
        body.pop("so", None)
        body["so_by_location"] = []
        body["so_by_customer"] = []
        body["so_rows"] = []
    if scope not in ("do", "both"):
        body.pop("do", None)
        body["do_by_location"] = []
        body["do_by_customer"] = []
        body["do_rows"] = []
    # AC-1119: the route echoes the EXACTLY resolved `product_code` back on the body
    # (`outstanding_report_service._resolve_product`), which is what the header prints.
    if args.get("product_code"):
        body["product_code"] = args["product_code"]
    body["warehouse_codes"] = list(args.get("warehouse_codes") or [])
    body["location_token"] = args.get("location_token")
    body["so_refused"] = bool(args.get("so_refused"))
    if args.get("detail") in ("so", "do"):
        body["detail"] = args["detail"]
    return body


def _capturing_mcp(response: Any = None):
    """A fake `mcp_call(name, args)`. `captured` records every `(name, args)` call; a test
    asserting "no tool call happened" reads `captured == []`.

    **The production path, not a shortcut** (review S4/S5, 13 Sep 2026): the lane always
    sends `view=render` (`fetch.entity_ids_transformer`), so what comes back from
    `crm_outstanding_report` is what `sorento_crm_mcp.presenters.present_response`
    rendered from the route body - never the route body itself. A double that handed the
    lane a dict fed the reply composer a shape production never produces, and that is
    what hid six user-visible defects behind 33 green tests. So a dict `response` here is
    treated as the ROUTE's payload and pushed through the real presenter; a string is
    returned verbatim (a caller that wants to pin an exact rendering)."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        if response is None:
            return json.dumps({"has_result": False, "items": []})
        if isinstance(response, str):
            return response
        if name == "crm_outstanding_report":
            return _present_response()(name, json.dumps(_report_route_body(response, args)))
        if name == "crm_sales_report":
            # Same reason as the `crm_outstanding_report` branch above: the lane reads
            # the PRESENTER's envelope (`has_result`, `response`, `options`), never the
            # route body raw, so a fake that skipped the presenter armed nothing off a
            # real hit. A drill pick re-runs with `group_by`, which the real route echoes
            # on its body and drops from the options it offers next (AC-SR-24: never the
            # view just shown) - reproduced here, the way `_report_route_body` does for
            # the outstanding report, so this double stays the route's shape.
            body = dict(response)
            group_by = args.get("group_by")
            if group_by:
                body["group_by"] = group_by
                body["options"] = [o for o in (body.get("options") or []) if o.get("key") != group_by]
            return _present_response()(name, json.dumps(body))
        return json.dumps(response)

    return _call, captured


def _enable_business_lane(session_factory, *, extra_completed_lanes: list[str] | None = None) -> None:
    """`chatbot_business_lane_enabled` ON and `business_query` in `chatbot_completed_lanes`
    - same two-step switch `test_foundre_rung_end_to_end.py::_run_stock_turn` flips.

    `extra_completed_lanes`, given, is APPENDED - called fresh on EVERY `_run_turn`
    (coder round 9 finding, 13 Sep 2026), so a lane a test seeded before a later turn
    for that turn's OWN benefit (e.g. `low_signal`, so an R22(b) turn that must
    complete in-process rather than delegate) is wiped back to `["business_query"]`
    on the very next call unless it is asked for again here, every time."""
    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    row = db.query(SystemSetting).first()
    if row is None:
        row = SystemSetting()
        db.add(row)
    row.chatbot_completed_lanes = ["business_query", *(extra_completed_lanes or [])]
    db.commit()


def _wire_business_services(
    monkeypatch, *, resolve_services: ResolveGateServices, mcp_call
) -> None:
    monkeypatch.setattr(
        engine_mod.business_services, "production_services", lambda db, *, space_id=None: resolve_services
    )
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call)
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: AnswerServices(
            mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
        ),
    )


def _session_of(session_factory) -> dict:
    db = session_factory()
    row = db.execute(
        text("SELECT session_vars FROM respond_contacts WHERE respond_io_id = :cid"),
        {"cid": str(CONTACT_ID)},
    ).first()
    raw = row.session_vars if row is not None else {}
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


#: `_run_turn` monkeypatches `engine_mod.default_space_id` to this literal, and
#: `_contact_company_scope` resolves the company scope from `(contact_respond_id,
#: default_space_id(db))` - `resolve_contact_id`'s Respond.io-id branch JOINS
#: `respond_workspaces` on `space_id` when one is given, so the seeded workspace
#: below must carry this SAME value or the join (and so the scope) finds nothing.
_SPACE_ID = "364817"


def _seed_contact(session_factory, *, variables: dict[str, Any]) -> None:
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_workspaces (id, space_id, name, api_key_ciphertext) "
            "VALUES (gen_random_uuid(), :sid, 'ZZT outstanding-report workspace', 'ZZT-cipher') "
            "ON CONFLICT DO NOTHING"
        ),
        {"sid": _SPACE_ID},
    )
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars, workspace_id) "
            "VALUES ("
            "  gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb),"
            "  (SELECT id FROM respond_workspaces WHERE space_id = :sid LIMIT 1)"
            ")"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": variables}), "sid": _SPACE_ID},
    )
    # S1 (security review, 13 Sep 2026): bound to Sorento so a REAL company-scoped
    # DB read this contact's turn makes (`crm_outstanding_report`'s warehouse
    # lookup, AC-1133's pipeline half - `resolve_warehouse_token` reads on the
    # engine's own per-contact-scoped session, same as every other owned read the
    # turn makes) sees the SAME company `TestLocationTokenPipeline`'s own seeded
    # warehouses land in. A contact with no `respond_contact_companies` row
    # legitimately resolves to zero owned rows everywhere (AC-F3, fail-closed) -
    # every OTHER test in this file never exercises a real DB read on this
    # contact's session at all, so this insert is a no-op for them.
    db.execute(
        text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid"
        ),
        {"cid": str(CONTACT_ID), "company_id": DEFAULT_COMPANY_ID},
    )
    db.commit()


def _run_turn(
    session_factory,
    monkeypatch,
    *,
    qf: dict[str, Any],
    text_body: str,
    msg_id: str,
    attributes: list[str] | None = None,
    matches: dict[str, dict[str, Any]] | None = None,
    mcp_response: Any = None,
    real_resolver: bool = False,
    capture_user_block: list[str] | None = None,
    resolve_services: ResolveGateServices | None = None,
    extra_completed_lanes: list[str] | None = None,
):
    """One real `engine.run_turn`, business lane on, parser/access/resolver/MCP faked.

    `real_resolver=True` leaves the RESOLVER alone (the real
    `business_services.production_services`, which reads the seeded `products` table
    through `POST /api/v1/system/references/resolve` in process) - the only way to grade
    which product code a typed token actually lands on (AC-1119, reviewer N5).

    `capture_user_block`, given a list, has this turn's `user_block` (the text the
    engine actually sends to `parser.parse`) appended to it - D17 (owner design ruling,
    13 Sep 2026): the parser is meant to read the open question's OWN option labels off
    this text, so a test asserting on what the parser was actually shown reads this list
    rather than guessing at an internal builder's name.

    `resolve_services`, given a `ResolveGateServices`, is used VERBATIM instead of
    `_resolve_services(matches)` - the only way to hand the turn a `probe` callable that
    is not the shared helper's `lambda **_: None` (R20, owner round 7, 13 Sep 2026): a
    test grading whether the customer-picker probe ran AT ALL needs a probe that would
    answer if called, not one that always renders the "probe failed" arm regardless.

    `extra_completed_lanes`, given, is appended to THIS turn's `chatbot_completed_
    lanes` beside `business_query` (see `_enable_business_lane`'s own docstring) - the
    only way a turn that must complete in a different in-process lane (`low_signal`)
    is actually told to, since this function resets the row on every call."""
    _enable_business_lane(session_factory, extra_completed_lanes=extra_completed_lanes)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True,
            "decision": "allow",
            "agent_name": "General",
            "attributes": attributes or [],
            "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    from app.services.chatbot.head import parser as parser_mod

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)

    def _fake_parse(config, user_block):
        if capture_user_block is not None:
            capture_user_block.append(user_block)
        return qf

    monkeypatch.setattr(parser_mod, "parse", _fake_parse)

    call, captured = _capturing_mcp(mcp_response)
    if real_resolver:
        monkeypatch.setattr(
            engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=call)
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )
    else:
        _wire_business_services(
            monkeypatch,
            resolve_services=resolve_services or _resolve_services(matches or {}),
            mcp_call=call,
        )

    envelope = _envelope()
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text_body
    result = engine_mod.run_turn(envelope, session_factory=session_factory)
    return result, captured


#: The ROUTE's own body for a product that HAS both an SO backlog and a DO pending
#: (the shape `OutstandingReportResponse` declares). `_capturing_mcp` renders it through
#: the real presenter and drops the block of whichever scope was not asked, exactly as
#: the route does - so a `scope=so` turn in a test sees the same one-block report a
#: `scope=so` turn sees in production.
REPORT_HIT = {
    "product_code": PRODUCT_CODE,
    "customer_name": None,
    "warehouse_codes": [],
    "order_date_from": None,
    "order_date_to": None,
    "so": {
        "ordered_qty": 10, "transferred_qty": 3, "outstanding_qty": 7, "so_count": 1,
        "order_date_min": "2026-01-01", "order_date_max": "2026-01-01",
    },
    "do": {
        # R6 (owner testing round 3, 13 Sep 2026): `do_qty` / `delivered_qty` are BACK
        # on the block (over EVERY DO in scope, pending and delivered) - a double that
        # still omitted them would be claiming a wire shape production no longer
        # produces. `do_qty=10` here is one delivered DO of 3 plus this pending one of 7.
        "do_qty": 10, "delivered_qty": 3, "pending_qty": 7, "do_count": 1,
        "do_date_min": "2026-02-03", "do_date_max": "2026-02-03",
    },
    "so_by_location": [{"code": "BRW-IB", "ordered_qty": 10, "outstanding_qty": 7}],
    "so_by_customer": [{"customer_name": CUSTOMER_NAME, "ordered_qty": 10, "outstanding_qty": 7}],
    "do_by_location": [{"code": "BRW-IB", "do_qty": 10, "pending_qty": 7}],
    "do_by_customer": [{"customer_name": CUSTOMER_NAME, "do_qty": 10, "pending_qty": 7}],
    "so_rows": [
        {
            "so_number": "SO1", "customer_name": CUSTOMER_NAME, "location": "BRW-IB",
            "ordered_qty": 10, "transferred_qty": 3, "outstanding_qty": 7, "order_date": "2026-01-01",
        }
    ],
    "do_rows": [
        {
            "do_number": "DO1", "customer_name": CUSTOMER_NAME, "location": "BRW-IB",
            "do_qty": 7, "delivered_qty": 0, "pending_qty": 7, "do_date": "2026-02-03",
        }
    ],
}

#: The same body for a product with NOTHING open on either side (AC-1107): both blocks
#: present, every count zero - the route's answer to a real miss, not an error.
REPORT_MISS = {
    **REPORT_HIT,
    "so": {
        "ordered_qty": 0, "transferred_qty": 0, "outstanding_qty": 0, "so_count": 0,
        "order_date_min": None, "order_date_max": None,
    },
    "do": {
        "do_qty": 0, "delivered_qty": 0, "pending_qty": 0, "do_count": 0,
        "do_date_min": None, "do_date_max": None,
    },
    "so_by_location": [], "so_by_customer": [], "do_by_location": [], "do_by_customer": [],
    "so_rows": [], "do_rows": [],
}


# --------------------------------------------------------------------------- #
# AC-1131 - parser vocabulary + the fetch-side scope mapping
# --------------------------------------------------------------------------- #


class TestScopeWordsBind:
    def test_parser_prompt_names_do_outstanding_and_outstanding_both(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        addendum = prompt_mod.GROWTH_R1_ADDENDUM
        assert "do_outstanding" in addendum, "the parser vocabulary must teach do_outstanding"
        assert "outstanding_both" in addendum, "the parser vocabulary must teach outstanding_both"

    def test_fetch_maps_order_status_to_scope(self) -> None:
        """S4 point 3: `so_outstanding` -> so, `do_outstanding` -> do, `outstanding_both`
        -> both. Tester's own name, see module docstring."""
        table = fetch_mod.ORDER_STATUS_TO_SCOPE
        assert table == {"so_outstanding": "so", "do_outstanding": "do", "outstanding_both": "both"}


class TestPromptTeachesScopeWordsInsideLongerSentences:
    """R12 (parser gap found live, 13 Sep 2026): `"Srtwc8518-SH dealer delivery order
    outstanding how many at BRW?"` parsed `order_status: "outstanding"` (bare) instead of
    `do_outstanding`, so the bot (re-)asked a scope question the message had already
    answered. D17 stands - this is a PROMPT fix (no deterministic code reads this word),
    so the red test pins the ADDENDUM TEXT the coder must extend, not any behaviour."""

    def test_addendum_carries_examples_of_scope_words_embedded_in_a_longer_sentence(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        addendum = prompt_mod.GROWTH_R1_ADDENDUM
        assert "dealer delivery order outstanding how many at BRW" in addendum, (
            "the addendum must carry the exact live phrasing that mis-parsed as an "
            "example of do_outstanding, embedded inside a longer sentence"
        )
        assert "sales order outstanding for IB" in addendum, (
            "the addendum must carry a so_outstanding example embedded in a longer sentence"
        )
        assert "outstanding both" in addendum, (
            "the addendum must carry an outstanding_both example (may already be present "
            "from the standalone vocabulary - R12 is about EMBEDDED examples specifically)"
        )

    def test_addendum_states_the_document_word_decides_scope_wherever_it_sits(self) -> None:
        from app.services import chatbot_parser_prompt as prompt_mod

        addendum = prompt_mod.GROWTH_R1_ADDENDUM.lower()
        assert "wherever it sits" in addendum or "anywhere in the message" in addendum, (
            "the addendum must state the rule in words: the document word (sales order / "
            "delivery order / both) decides the scope wherever it sits in the message, "
            "not only when the message is otherwise bare"
        )


# --------------------------------------------------------------------------- #
# AC-1133 - location token resolution (D5), reading the real `warehouses` table
# --------------------------------------------------------------------------- #


class TestLocationTokenResolution:
    """Tester's own function name (module docstring): `services.resolve_warehouse_token`."""

    def _seed_warehouses(self, db) -> None:
        from app.models.inventory import Warehouse

        db.add_all(
            [
                Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-BRW-IB", warehouse_name="BRW IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-MWH-IB", warehouse_name="MWH IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="ZZT-BRW-BB", warehouse_name="BRW BB", is_active=True),
            ]
        )
        db.commit()

    def test_exact_code_matches_only_itself(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        self._seed_warehouses(db)
        assert business_services.resolve_warehouse_token(db, "ZZT-BRW-IB") == ["ZZT-BRW-IB"]

    def test_suffix_token_matches_every_code_ending_in_it(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        self._seed_warehouses(db)
        assert set(business_services.resolve_warehouse_token(db, "IB")) == {"ZZT-BRW-IB", "ZZT-MWH-IB"}

    def test_unknown_token_resolves_to_nothing(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import services as business_services

        db = session_factory()
        self._seed_warehouses(db)
        assert business_services.resolve_warehouse_token(db, "ZZZ-NO-SUCH-TOKEN") == []


class TestLocationTokenPipeline:
    """AC-1133's PIPELINE half, found by the coder after `services.resolve_warehouse_token`
    landed: the function above is correct in isolation, but nothing in the fetch pipeline
    ever calls it with the customer's own location word, so a live "... outstanding for
    IB" turn sends no `warehouse_codes` to `crm_outstanding_report` at all. Same fake-fetch
    style as `TestScopeAnswerRunsReportWithCarriedFilters` (AC-1132): a REAL `warehouses`
    table via `session_factory` (the location resolver is DB-backed and must not be
    faked), the product resolved through the same `_resolve_services` seam every other
    test in this file uses, and the tool call captured through the fake `mcp_call`."""

    def _seed_warehouses(self, session_factory) -> None:
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add_all(
            [
                Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW-IB", warehouse_name="BRW IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="MWH-IB", warehouse_name="MWH IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW", warehouse_name="BRW", is_active=True),
            ]
        )
        db.commit()

    def test_location_token_in_message_reaches_tool_as_warehouse_codes(self, session_factory, monkeypatch) -> None:
        self._seed_warehouses(session_factory)
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                entities=[
                    {
                        "raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                    {
                        "raw": "IB", "hint": "warehouse", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="SRTWT7445 sales order outstanding for IB",
            msg_id="ZZT-outstanding-location-ib-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the report must still be fetched"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert set(args.get("warehouse_codes") or []) == {"BRW-IB", "MWH-IB"}, (
            f"the 'IB' suffix token must resolve to every warehouse code ending in it "
            f"and reach the tool as warehouse_codes: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Location: IB (BRW-IB, MWH-IB)" in reply, (
            f"the header must echo the token and what it resolved to (D5/AC-1105): {reply!r}"
        )

    def test_exact_code_token_in_message_reaches_tool(self, session_factory, monkeypatch) -> None:
        self._seed_warehouses(session_factory)
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                entities=[
                    {
                        "raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                    {
                        "raw": "BRW", "hint": "warehouse", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="SRTWT7445 sales order outstanding for BRW",
            msg_id="ZZT-outstanding-location-brw-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the report must still be fetched"
        _name, args = captured[0]
        assert args.get("warehouse_codes") == ["BRW"], (
            f"an exact warehouse-code token must resolve to itself only, no other code: {args}"
        )


# --------------------------------------------------------------------------- #
# AC-1119 - the typed code wins over its family siblings (reviewer N5)
# --------------------------------------------------------------------------- #


class TestExactProductCodeWinsOverSiblings:
    """Console run 3: the owner typed `SRTWT7445`, which exists exactly on the prod copy,
    and the report ran for `SRTWT7445-LV-GM`. AC-1119 pins the ROUTE to exact-code, and
    the route obeys it - the substitution happens UPSTREAM, in entity resolution, so the
    route never sees the code the customer typed. Graded here through the REAL resolver
    (`real_resolver=True`), which is the only place the substitution is visible."""

    def _seed_products(self, session_factory) -> None:
        from tests._mc_lookup_seed import product as seed_product

        # The SIBLINGS are inserted first on purpose: the resolver's prefix probe has no
        # ORDER BY, so the family comes back in insertion order and the lane used to take
        # whichever product landed first. On the prod copy that was `SRTWT7445-LV-GM`.
        db = session_factory()
        for code in ("ZZT7445-LV-GM", "ZZT7445-NL", "ZZT7445"):
            seed_product(db, company_id=DEFAULT_COMPANY_ID, code=code)
        db.commit()

    def test_the_typed_code_is_what_the_report_runs_for(self, session_factory, monkeypatch) -> None:
        self._seed_products(session_factory)
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                entities=[
                    {
                        "raw": "Zzt7445", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="Zzt7445 sales order outstanding",
            msg_id="ZZT-outstanding-exact-code-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
            real_resolver=True,
        )
        assert captured, "the report must run: the typed code exists, so nothing is ambiguous"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("product_code") == "ZZT7445", (
            f"the code the customer typed exists exactly, so it is the subject - never a "
            f"family sibling: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith("Product: ZZT7445\n"), (
            f"the header must name the code the customer typed: {reply!r}"
        )

    def test_the_scope_question_arm_stores_the_typed_code_too(
        self, session_factory, monkeypatch
    ) -> None:
        """Finding 5 (console run 4): the typed-code rule landed on the DIRECT report
        path only. A BARE "outstanding" arms the scope question instead of fetching, and
        the filter set it stores took the first product entity - the sibling - so the
        answering turn reported the wrong product under a header that named it."""
        self._seed_products(session_factory)
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="outstanding",
                entities=[
                    {
                        "raw": "Zzt7445", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="Zzt7445 outstanding",
            msg_id="ZZT-outstanding-exact-code-arm-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
            real_resolver=True,
        )
        assert captured == [], f"the scope question fetches nothing: {captured}"
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith("Product: ZZT7445\n"), (
            f"the question must name the code the customer typed: {reply!r}"
        )
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): outstanding_filters ->
        # open_question.payload.filters, the same rename every other class in this
        # file already reads - customer-visible assertion (the stored filter) kept
        # unchanged, only the internal key path updated.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("payload", {}).get("filters", {}).get("product_code") == "ZZT7445", (
            f"the carried filter set must hold the typed code, or the ANSWER turn reports "
            f"the wrong product: {open_question.get('payload', {}).get('filters')}"
        )

    def test_the_answer_turn_reports_the_typed_code(self, session_factory, monkeypatch) -> None:
        """The other half of finding 5, end to end: arm the question on the typed code,
        answer "3", and the report runs for what the customer typed."""
        self._seed_products(session_factory)
        _seed_contact(session_factory, variables={})
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="outstanding",
                entities=[
                    {
                        "raw": "Zzt7445", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="Zzt7445 outstanding",
            msg_id="ZZT-outstanding-exact-code-arm-2",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
            real_resolver=True,
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                # D17 (owner design ruling, 13 Sep 2026) replaced this turn's shape: the
                # parser used to hint the answer word itself as an entity (console run 4,
                # finding 6) and is now instructed to emit the POSITION and nothing else,
                # which is also what the head's structural new-ask guard now requires - an
                # entity beats a position, so a junk entity here would be a new ask. The
                # assertions below are unchanged: the typed code on the answering turn
                # (finding 5) and no "Couldn't find" echo (finding 6).
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
            text_body="3",
            msg_id="ZZT-outstanding-exact-code-answer-2",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
            real_resolver=True,
        )
        assert captured, "the answer must run the report"
        _name, args = captured[0]
        assert args.get("product_code") == "ZZT7445", (
            f"the answering turn must report the code the customer typed: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Couldn't find" not in reply and "not found" not in reply, (
            f"the answer word is not an entity to look up, and its 'not found' echo must "
            f"never be appended to the report (console run 4, finding 6): {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1134 - date params, order_date not actual_delivery_date
# --------------------------------------------------------------------------- #


class TestDateParams:
    def test_crm_outstanding_report_uses_order_date_params(self) -> None:
        assert fetch_mod.DATE_PARAMS.get("crm_outstanding_report") == (
            "order_date_from", "order_date_to",
        )


# --------------------------------------------------------------------------- #
# S4 point 7 - the gate admits a warehouse entity into the "order" domain
# --------------------------------------------------------------------------- #


class TestGateAdmitsWarehouseForOrder:
    def test_warehouse_is_an_allowed_type_for_order(self) -> None:
        assert "warehouse" in gate_mod.ALLOWED["order"], (
            f"S4 point 7: ALLOWED['order'] must admit a warehouse entity: {gate_mod.ALLOWED['order']}"
        )


# --------------------------------------------------------------------------- #
# S4 point 12 / AC-1142 - the reveal key exists on both sides
# --------------------------------------------------------------------------- #


class TestSoKeyListedInFieldRevealKeysAndCatalog:
    def test_so_key_listed_in_field_reveal_keys_and_catalog(self) -> None:
        import sys
        from pathlib import Path

        from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS

        pair = ("sales_orders.outstanding", "Sales order outstanding")
        assert pair in FIELD_REVEAL_KEYS, (
            f"FIELD_REVEAL_KEYS must carry {pair}, the key that gates AC-1140/AC-1141: "
            f"{FIELD_REVEAL_KEYS}"
        )

        repo_root = Path(__file__).resolve().parents[3]
        mcp_root = repo_root / "sorento_crm_mcp"
        if str(mcp_root) not in sys.path:
            sys.path.append(str(mcp_root))
        from sorento_crm_mcp.catalog import CATALOG

        spec = next(s for s in CATALOG if s.name == "crm_outstanding_report")
        restricted = getattr(spec, "restricted_fields", None) or ()
        assert pair in restricted, (
            f"crm_outstanding_report's ToolSpec.restricted_fields must carry {pair}: {restricted}"
        )


# --------------------------------------------------------------------------- #
# S4 point 2 - tool pick: domain "order" + product + an outstanding order_status
# --------------------------------------------------------------------------- #


class TestToolPick:
    """Through `run_fetch` directly - the real call site `run_until_exit`'s caller uses,
    not a hand-rolled re-implementation of the pick (module docstring)."""

    def _payload(self, *, order_status: str, entities: list[dict[str, Any]], attributes=None):
        return {
            "gate": {"compatible_entities": entities},
            "tier_gate": None,
            "ctx": {
                "parse": {"output": _qf(order_status=order_status, entities=[])},
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": attributes or ["sales_orders.outstanding"]},
            },
        }

    def test_outstanding_with_product_picks_report_tool(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        payload = self._payload(
            order_status="outstanding",
            entities=[{"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}],
        )
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, _args = captured[0]
        assert name == "crm_outstanding_report", (
            f"domain=order + a resolved product + order_status=outstanding must pick "
            f"crm_outstanding_report, not {name!r} (S4 point 2)"
        )

    def test_outstanding_without_a_subject_keeps_order_list(self, session_factory) -> None:
        """S4 point 2's carve-out, REWRITTEN by R13 (13 Sep 2026): a customer IS a
        subject now and reaches the report (`TestCustomerOnlyOutstandingAskReachesThe
        Report`). What still keeps the plain order-list path is an outstanding ask with
        NO subject at all - no product and no customer - because there is nothing for
        the report to be about."""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp()
        payload = self._payload(order_status="so_outstanding", entities=[])
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, _args = captured[0]
        assert name == "crm_order_management_orders_list", (
            f"an outstanding ask with no subject must keep today's order-list tool, "
            f"not {name!r}"
        )


# --------------------------------------------------------------------------- #
# S4 point 7 - a warehouse entity on a PLAIN order ask (no outstanding word)
# --------------------------------------------------------------------------- #


class TestWarehouseOnAPlainOrderAsk:
    def test_warehouse_entity_becomes_warehouse_codes_on_the_order_list(
        self, session_factory
    ) -> None:
        """`ALLOWED["order"]` admits a warehouse entity (S4 point 7), and
        `TYPE_TO_PARAM` maps it to `warehouse_ids` - which neither order-list tool
        declares, so the MCP dropped it silently and the answer was presented as though
        it had been scoped to that warehouse. Both tools DO take `warehouse_codes`
        (S3), so the resolved code is what travels."""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp({"data": [], "has_result": False})
        payload = {
            "gate": {
                "compatible_entities": [
                    {"uuid": PRODUCT_UUID, "entity_type": "product", "code": PRODUCT_CODE},
                    {
                        "uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                        "entity_type": "warehouse",
                        "code": "BRW",
                    },
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {"output": _qf(order_status=None, entities=[], **do_window())},  # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": []},
            },
        }
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, args = captured[0]
        assert name == "crm_order_management_orders_list", name
        assert args.get("warehouse_codes") == ["BRW"], (
            f"the resolved warehouse must reach the order list as warehouse_codes: {args}"
        )
        assert "warehouse_ids" not in args, (
            f"warehouse_ids is not a param either order-list tool declares - sending it "
            f"is a silent drop, and the answer then claims a scope it never had: {args}"
        )


# --------------------------------------------------------------------------- #
# AC-1136 - a resolved customer entity becomes customer_ids on the report call
# --------------------------------------------------------------------------- #


class TestCustomerEntityBecomesCustomerIds:
    def test_customer_entity_becomes_customer_ids(self, session_factory) -> None:
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        payload = {
            "gate": {
                "compatible_entities": [
                    {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                    {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME},
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {"output": _qf(order_status="outstanding", entities=[])},
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": ["sales_orders.outstanding"]},
            },
        }
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "no MCP tool was ever called"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("customer_ids") == [CUSTOMER_UUID], (
            f"the resolved customer must be sent as customer_ids (AC-1113b/AC-1136): {args}"
        )
        assert args.get("product_code") == PRODUCT_CODE, (
            f"crm_outstanding_report takes product_code (exact string), not product_ids "
            f"(the route's own contract - see PLAN 'Backend contract'): {args}"
        )


# --------------------------------------------------------------------------- #
# AC-1140 / AC-1141 - the sales_orders.outstanding gate, before any fetch
# --------------------------------------------------------------------------- #


class TestFieldRevealGateBeforeFetch:
    def _payload(self, *, order_status: str, attributes: list[str]):
        return {
            "gate": {
                "compatible_entities": [
                    {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {"output": _qf(order_status=order_status, entities=[])},
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": attributes},
            },
        }

    def test_no_so_key_skips_question_and_runs_do_only(self, session_factory) -> None:
        """AC-1140: no `sales_orders.outstanding` -> bare 'outstanding' runs scope=do,
        no question, no SO query."""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        run_fetch(self._payload(order_status="outstanding", attributes=[]), services=FetchServices(mcp_call=call))
        assert captured, "the DO-only report must still run without the grant"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("scope") == "do", (
            f"without the grant, scope must be forced to 'do': {args}"
        )
        assert "so" not in {k for k in args if k == "scope" and args[k] == "so"}

    def test_no_so_key_explicit_so_ask_refuses_then_do_block(self, session_factory, monkeypatch) -> None:
        """AC-1141: an EXPLICIT SO ask ('sales order outstanding') without the grant still
        runs (scope forced to do) and the customer-facing reply carries the refusal line
        BEFORE the DO block. Composition is a full-turn concern - see module docstring for
        why only this pinned line is asserted, not the DO block's own content."""
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding"),
            text_body="sales order outstanding for SRTWT7445",
            msg_id="ZZT-outstanding-so-refuse-1",
            attributes=[],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the DO block must still be fetched even when SO is refused"
        _name, args = captured[0]
        assert args.get("scope") == "do", (
            f"an explicit SO ask without the grant must still force scope=do: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Sales order figures are not enabled for your account." in reply, (
            f"D13's exact refusal line must be in the reply: {reply!r}"
        )
        # AC-1141 / S4 point 11 (review round, 13 Sep 2026): PLACEMENT, not mere
        # presence. The line belongs AFTER the four header lines and BEFORE the DO
        # block - opening the whole reply with it reads as a refusal of the question
        # the customer asked, rather than a note about the half that is withheld.
        lines = reply.splitlines()
        refusal_at = lines.index("Sales order figures are not enabled for your account.")
        header_at = [i for i, line in enumerate(lines) if line.startswith("Order date:")]
        do_block_at = [i for i, line in enumerate(lines) if line == "*Delivery order outstanding*"]
        assert header_at and refusal_at > header_at[0], (
            f"the refusal must come after the header's four lines: {reply!r}"
        )
        assert do_block_at and refusal_at < do_block_at[0], (
            f"the refusal must come before the Delivery order outstanding block: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1130 - bare "outstanding" + the SO key arms the scope question, no fetch
# --------------------------------------------------------------------------- #


class TestBareOutstandingWithKeyArmsScopeQuestion:
    def test_bare_outstanding_with_key_arms_scope_question(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding"),
            text_body="SRTWT7445 outstanding",
            msg_id="ZZT-outstanding-scope-ask-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        )
        assert captured == [], (
            f"no report/order tool may be called while the scope question is open: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        assert "1. Sales orders (not yet transferred to DO)" in reply, reply
        assert "2. Delivery orders (not yet delivered)" in reply, reply
        assert "3. Both" in reply, reply

        # AC-1592 test triage (queue item 2c, session-shape rename, 16 Sep 2026):
        # `selection_context`/`pending`/`last_result_set`/`outstanding_filters` were
        # this file's own pre-rearch names for what `open_question` now carries whole -
        # `open_question.kind` is both the old `selection_context` and `pending.kind`
        # (the same string), `open_question.options` is `last_result_set`, and
        # `open_question.payload.filters` is `outstanding_filters` (measured directly
        # via a scratch probe, not guessed).
        stored = _session_of(session_factory)
        open_question = stored.get("open_question") or {}
        assert open_question.get("kind") == "outstanding_scope", open_question.get("kind")
        assert len(open_question.get("options") or []) == 3, open_question.get("options")
        assert open_question.get("payload", {}).get("filters", {}).get("product_code") == PRODUCT_CODE, (
            open_question.get("payload")
        )

    def test_a_new_bare_outstanding_ask_after_a_hit_asks_the_scope_question(
        self, session_factory, monkeypatch
    ) -> None:
        """D2 one-turn life, console run 3 (13 Sep 2026): a report HIT leaves
        `pending.kind = "outstanding_detail"` for the next turn's "1"/"2". A LATER bare
        "outstanding" ask is a NEW ask, not an answer to that offer, so it must ask the
        scope question - it ran the full report instead, because the stale pending
        suppressed the scope-ask signal. Resetting the session made the question fire,
        which is what named the pending as the cause."""
        _seed_contact(session_factory, variables={})
        _r1, captured1 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding"),
            text_body="SRTWT7445 sales order outstanding",
            msg_id="ZZT-outstanding-stale-pending-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured1 and captured1[0][0] == "crm_outstanding_report", captured1
        assert (_session_of(session_factory).get("open_question") or {}).get(
            "kind"
        ) == "outstanding_detail"

        result, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding"),
            text_body="SRTWT7445 outstanding",
            msg_id="ZZT-outstanding-stale-pending-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured2 == [], (
            f"a new bare-word ask must ask the scope question first, not fetch: {captured2}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        stored = _session_of(session_factory)
        assert (stored.get("open_question") or {}).get("kind") == "outstanding_scope", stored.get(
            "open_question"
        )


# --------------------------------------------------------------------------- #
# AC-1132 - the scope answer restores the filters and runs the report
# --------------------------------------------------------------------------- #


def _seed_open_outstanding_scope(session_factory, *, filters: dict[str, Any] | None = None) -> None:
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [],
            "selection_context": "outstanding_scope",
            "last_result_set": [
                {"idx": 1, "label": "Sales orders", "value": "so"},
                {"idx": 2, "label": "Delivery orders", "value": "do"},
                {"idx": 3, "label": "Both", "value": "both"},
            ],
            "outstanding_filters": filters
            or {
                "product_code": PRODUCT_CODE,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": ["ZZT-BRW-IB", "ZZT-MWH-IB"],
                "location_token": "IB",
            },
            "pending": {"kind": "outstanding_scope"},
        },
    )


class TestScopeAnswerRunsReportWithCarriedFilters:
    def test_reply_2_picks_do_scope_and_restores_filters(self, session_factory, monkeypatch) -> None:
        _seed_open_outstanding_scope(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2",
            msg_id="ZZT-outstanding-scope-answer-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the scope answer must run the report in the SAME turn (no re-parse)"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("scope") == "do", f"'2' must resolve to scope=do: {args}"
        assert args.get("product_code") == PRODUCT_CODE, (
            f"the carried product_code must be restored, not re-parsed: {args}"
        )
        assert args.get("customer_ids") == [CUSTOMER_UUID], (
            f"the carried customer_ids must be restored: {args}"
        )
        # AC-1132 / AC-1138 (review round, 13 Sep 2026): the LOCATION is part of the
        # filter set. Dropping it re-ran the report over every warehouse and printed
        # `Location: all` under a question the customer asked about one location, so
        # the answering turn quietly answered a wider question than the one asked.
        assert args.get("warehouse_codes") == ["ZZT-BRW-IB", "ZZT-MWH-IB"], (
            f"the carried warehouse_codes must be restored: {args}"
        )
        assert args.get("location_token") == "IB", (
            f"the carried location token must be restored so the header still echoes it: {args}"
        )
        reply = (_result.reply or {}).get("text") or ""
        assert "Location: IB (ZZT-BRW-IB, ZZT-MWH-IB)" in reply, (
            f"the answering turn must print the SAME location header as the asking turn: {reply!r}"
        )

    def test_a_new_ask_during_the_scope_question_is_not_hijacked(
        self, session_factory, monkeypatch
    ) -> None:
        """D2's one-turn life, review round 13 Sep 2026: a message that brings its OWN
        question - a domain word, or a new product code - is a NEW ASK while an
        `outstanding_scope` question is open. The carve-out existed for
        `outstanding_detail` only, so "stock for SRTWC8517" was rewritten into the
        carried outstanding ask and answered with the scope question AGAIN: the customer
        could not leave the question except by answering it."""
        _seed_open_outstanding_scope(session_factory)
        other_uuid = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                intent_hint="check_stock",
                domain_hint="inventory",
                entities=[
                    {
                        "raw": "SRTWC8517", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="stock for SRTWC8517",
            msg_id="ZZT-outstanding-scope-hijack-1",
            attributes=["sales_orders.outstanding"],
            matches={"SRTWC8517": {"uuid": other_uuid, "entity_type": "product", "canonical_code": "SRTWC8517"}},
            mcp_response={
                "items": [
                    {
                        "title": "SRTWC8517",
                        "fields": [{"key": "product_code", "label": "Product", "value": "SRTWC8517"}],
                    }
                ],
                "has_result": True,
                "intro": "Here are the results.",
            },
        )
        assert captured, "the new ask must be answered, not swallowed by the open question"
        name, _args = captured[0]
        assert name != "crm_outstanding_report", (
            f"a stock ask must not be rewritten into the carried outstanding ask: {name}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" not in reply, (
            f"the open scope question must be DROPPED by a new ask, never re-asked: {reply!r}"
        )

    def test_a_scope_word_with_a_new_product_is_a_new_ask_not_an_answer(
        self, session_factory, monkeypatch
    ) -> None:
        """Reviewer N2: the carve-out fired only when nothing was picked, and a SCOPE WORD
        picks - so "sales order outstanding for SRTWC8517" typed while a scope question
        about SRTWT7445 was open kept the OLD customer, location and product and answered
        about the wrong thing. A turn that names a product is a new ask, whatever else it
        says."""
        _seed_open_outstanding_scope(session_factory)
        other_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                date_filter_start="2026-01-01",
                date_filter_end="2026-12-31",
                entities=[
                    {
                        "raw": "SRTWC8517", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="sales order outstanding for SRTWC8517",
            msg_id="ZZT-outstanding-scope-new-product-1",
            attributes=["sales_orders.outstanding"],
            matches={"SRTWC8517": {"uuid": other_uuid, "entity_type": "product", "canonical_code": "SRTWC8517"}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the new ask must still be answered"
        _name, args = captured[0]
        assert args.get("product_code") == "SRTWC8517", (
            f"the product the customer just typed is the subject, not the carried one: {args}"
        )
        assert not args.get("customer_ids"), (
            f"a new ask must not inherit the previous question's customer: {args}"
        )
        assert not args.get("warehouse_codes"), (
            f"a new ask must not inherit the previous question's location: {args}"
        )
        assert "location_token" not in args, args
        assert args.get("order_date_from") == "2026-01-01", (
            f"the new message's own window must reach the tool: {args}"
        )

    def test_a_date_in_the_answering_turn_wins_over_the_carried_one(
        self, session_factory, monkeypatch
    ) -> None:
        """Reviewer N2, second half: the carried dates were restored UNCONDITIONALLY, so
        a pick that narrowed the window ("2, but only 2026") was answered over the
        previous question's dates. The turn's own window wins; the rest of the filter set
        still carries, because this turn named nothing else of its own."""
        _seed_open_outstanding_scope(
            session_factory,
            filters={
                "product_code": PRODUCT_CODE,
                "date_filter_start": "2025-01-01",
                "date_filter_end": "2025-12-31",
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
            },
        )
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
                date_filter_start="2026-01-01",
                date_filter_end="2026-12-31",
            ),
            text_body="2 in 2026",
            msg_id="ZZT-outstanding-scope-own-date-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the pick must still run the report"
        _name, args = captured[0]
        assert args.get("product_code") == PRODUCT_CODE, args
        assert args.get("scope") == "do", args
        assert args.get("order_date_from") == "2026-01-01", (
            f"the window the customer just named must win over the carried one: {args}"
        )
        assert args.get("order_date_to") == "2026-12-31", args

    def test_out_of_range_number_reasks(self, session_factory, monkeypatch) -> None:
        _seed_open_outstanding_scope(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[9],
            ),
            text_body="9",
            msg_id="ZZT-outstanding-scope-oor-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], f"an out-of-range pick must not run any report: {captured}"
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, (
            f"an out-of-range pick must re-ask the same scope question: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# D17 (owner design ruling, 13 Sep 2026) - REPLACES R4/R5/R8 as briefed, and
# RETIRES D16's word tables. "Deterministic code never reads words; the parser
# (LLM) reads the answer and the head maps a POSITION to an option." A word
# answer ("all", "DO list", "delivery to hanlim") is now resolved upstream, in
# the PARSER - untestable by pytest (there is no LLM in this suite) and
# verified instead by the console run
# (`tests/chatbot/console_cases/2026-09-13-outstanding-report.yaml`). What
# pytest CAN and must still pin: the deterministic head resolves ONLY
# `reference_positions` (+ the `order_status` enum for a scope word in a FRESH
# ask, unrelated to this pending), the retired word table is actually gone, and
# a turn that also names its own entity is a new ask even if it carries a
# stray `reference_positions` (defensive - the parser is told never to emit
# both, the head still guards structurally).
# --------------------------------------------------------------------------- #


class TestScopeAnswerByPositionOnly:
    """D17 point 4's own words: "Unit tests that previously fed the words `all` /
    `DO list` through the head now feed `reference_positions: [3]` / `[2]` with an
    open pending and assert the same outcomes." These exercise the SAME resolver
    `test_reply_2_picks_do_scope_and_restores_filters` already covers for one
    position; parametrized here over all three so a regression on any one of them
    cannot slip past this class alone."""

    @pytest.mark.parametrize("position,expected_scope", [(1, "so"), (2, "do"), (3, "both")])
    def test_scope_position_resolves_to_the_stored_option(
        self, session_factory, monkeypatch, position: int, expected_scope: str
    ) -> None:
        _seed_open_outstanding_scope(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[position],
            ),
            text_body=str(position),
            msg_id=f"ZZT-outstanding-scope-position-{position}",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, f"position {position} must resolve the scope question"
        name, args = captured[0]
        assert name == "crm_outstanding_report", (position, name, args)
        assert args.get("scope") == expected_scope, (
            f"position {position} must resolve to scope={expected_scope!r}: {args}"
        )
        assert args.get("product_code") == PRODUCT_CODE, (position, args)


class TestScopeQuestionGuardsAgainstAStrayPositionWithAnEntity:
    def test_customer_entity_alongside_a_stray_position_is_a_new_ask_not_an_answer(
        self, session_factory, monkeypatch
    ) -> None:
        """D17 point 3, measured as a genuine gap in TODAY's code: `_apply_outstanding_
        pending`'s new-ask guard only ever checks `names_product` (entities hinted
        "product") before falling through to `_outstanding_scope_pick`, which reads
        `reference_positions` alone. A turn that names a CUSTOMER and ALSO carries a
        `reference_positions` (the parser is told never to emit this combination, but
        the head must guard defensively anyway, D17 point 3) is read as answering the
        open scope question with the carried product/customer/location intact - never
        as the new ask it actually is. This is the deterministic-side guard the
        original "delivery to hanlim" regression needs; the word-matching half of that
        regression is retired with D16 and re-verified live by the console case."""
        _seed_open_outstanding_scope(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint="check_order", domain_hint="order",
                # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                **do_window(),
                entities=[
                    {
                        "raw": "hanlim", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                reference_positions=[2],
            ),
            text_body="delivery to hanlim",
            msg_id="ZZT-outstanding-scope-new-ask-guard-1",
            attributes=["sales_orders.outstanding"],
            matches={"hanlim": {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
        )
        assert captured, "the new ask must still be answered, not swallowed"
        name, _args = captured[0]
        assert name != "crm_outstanding_report", (
            f"a turn naming a customer is a NEW ask, never an answer to the open scope "
            f"question, however many reference_positions ride along with it: {name}"
        )
        reply = (_result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" not in reply, (
            f"the open scope question must be DROPPED by the new ask, never re-asked: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1135 - a hit arms the detail offer and suppresses the escalate offer
# --------------------------------------------------------------------------- #


class TestHitArmsOutstandingDetailAndNoEscalateOffer:
    def test_hit_arms_detail_offer_and_no_escalate_text(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding"),
            text_body="SRTWT7445 sales order outstanding",
            msg_id="ZZT-outstanding-hit-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the SO report must be fetched"
        reply = (result.reply or {}).get("text") or ""
        assert reply.strip(), "a hit must produce a non-empty reply"
        assert "would you like me to escalate" not in reply.lower(), (
            f"a hit must never also offer to escalate: {reply!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_detail", open_question.get("kind")
        labels = {row.get("label") for row in (open_question.get("options") or [])}
        assert "Sales order list" in labels, (
            f"only the SO scope was requested/present, so only its option is offered: {labels}"
        )


    def test_report_skips_search_scope_header(self, session_factory, monkeypatch) -> None:
        """AC-1139 / S4 point 10 (never written until the review round): the generic
        delivery-order search-scope header ("Customer: ... / Product: ... / Dates: ...")
        must NOT print above a report that carries its own Product / Customer / Location
        / Order date lines. The console check read both, one under the other."""
        _seed_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="so_outstanding"),
            text_body="SRTWT7445 sales order outstanding",
            msg_id="ZZT-outstanding-header-skip-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith(f"Product: {PRODUCT_CODE}"), (
            f"the report's own header must be the first thing in the reply: {reply!r}"
        )
        assert "Dates: all dates" not in reply, (
            f"the generic search-scope header must not print above the report: {reply!r}"
        )
        assert "Customer: all customers" not in reply, (
            f"the generic search-scope header must not print above the report: {reply!r}"
        )

    def test_scope_question_skips_search_scope_header(self, session_factory, monkeypatch) -> None:
        """The same on the ASKING turn (the journey says the question carries nothing
        else): the scope question is three lines and a product, not a search summary."""
        _seed_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding"),
            text_body="SRTWT7445 outstanding",
            msg_id="ZZT-outstanding-header-skip-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith(f"Product: {PRODUCT_CODE}"), reply
        assert "Dates: all dates" not in reply, (
            f"the scope question must carry nothing but itself: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1107 - a TOTAL miss escalates, on the rendered-text path
# --------------------------------------------------------------------------- #


class TestTotalMissEscalates:
    def test_empty_report_offers_the_escalation(self, session_factory, monkeypatch) -> None:
        """AC-1107: both requested scopes empty is an ABSENCE, so the existing escalate
        offer + team picker follow. On the rendered path the reply text is never empty
        (the header always renders), so `has_result` cannot be read off the text - the
        presenter's envelope has to carry it, or every miss reads as a hit and the
        customer is left with "No open sales order." and no way forward."""
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding_both"),
            text_body="SRTWT7445 outstanding both",
            msg_id="ZZT-outstanding-miss-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_MISS,
        )
        assert captured, "the report must still have been fetched"
        reply = (result.reply or {}).get("text") or ""
        assert "escalate" in reply.lower(), (
            f"a total miss must reach the existing escalate offer: {reply!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_detail", (
            f"a miss offers no detail list: {open_question!r}"
        )

    def test_total_miss_keeps_the_report_block_lines_then_offers_escalation(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1107 in full (owner's approved miss mock on the lavish page): the reply is
        the REPORT's own header and block lines, then the escalate offer - not the
        generic miss lane's `Customer / Product / Dates` header and "Here's what you
        want" bullets, which say nothing about sales orders or delivery orders and drop
        the two lines the owner signed off on."""
        _seed_contact(session_factory, variables={})
        result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding_both"),
            text_body="SRTWT7445 outstanding both",
            msg_id="ZZT-outstanding-miss-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_MISS,
        )
        reply = (result.reply or {}).get("text") or ""
        assert "*Sales order outstanding*\nNo open sales order." in reply, reply
        assert "*Delivery order outstanding*\nNo outstanding delivery order." in reply, reply
        offer_at = reply.index("Would you like me to escalate")
        assert reply.index("No open sales order.") < offer_at, reply
        assert reply.index("No outstanding delivery order.") < offer_at, reply
        assert reply.startswith(f"Product: {PRODUCT_CODE}"), (
            f"the report's own header opens the miss, not the generic one: {reply!r}"
        )
        assert "Here's what you want:" not in reply, (
            f"the generic miss bullets must not print over the report's own lines: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# AC-1138 - the detail pick re-runs the SAME tool with detail=so|do
# --------------------------------------------------------------------------- #


def _seed_open_outstanding_detail(
    session_factory,
    *,
    filters: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [],
            "selection_context": "outstanding_detail",
            "last_result_set": rows
            or [
                {"idx": 1, "label": "Sales order list", "value": "so"},
                {"idx": 2, "label": "Delivery order list", "value": "do"},
            ],
            "outstanding_filters": filters
            or {
                "product_code": PRODUCT_CODE,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [],
                "warehouse_codes": [],
            },
            "pending": {"kind": "outstanding_detail"},
        },
    )


class TestDetailPickRerunsToolWithDetail:
    def test_reply_1_reruns_with_detail_so(self, session_factory, monkeypatch) -> None:
        _seed_open_outstanding_detail(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-detail-pick-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the detail pick must re-run the tool, not read a cached list"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("detail") == "so", f"'1' must ask for the SO detail: {args}"
        assert args.get("product_code") == PRODUCT_CODE, args

    def test_reply_2_reruns_with_detail_do(self, session_factory, monkeypatch) -> None:
        _seed_open_outstanding_detail(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2",
            msg_id="ZZT-outstanding-detail-pick-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the detail pick must re-run the tool"
        _name, args = captured[0]
        assert args.get("detail") == "do", f"'2' must ask for the DO detail: {args}"

    def test_a_real_hit_arms_the_pending_and_the_next_1_reruns_with_detail_so(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1135 + AC-1138 end to end, two turns, no hand-seeded pending (the gap the
        console check found): turn 1 is a genuine report hit, turn 2 is "1". The
        previously-passing pair seeded `pending.kind = outstanding_detail` directly, so
        a hit that never armed it still looked green.

        Queue item 2c (session-shape rename, 16 Sep 2026): turn 1's own assertions
        (`open_question.kind`/`payload.filters`) are the mechanical port and pass.
        Turn 2 ("1") is CONFIRMED, currently red for a real reason, not fixed here:
        `crm_order_management_orders_list` runs instead of a `crm_outstanding_report`
        re-run with `detail="so"` - the outstanding lane's own open question does not
        yet win over the generic business-query read on THIS call path (waiting on
        coder 12's contract 38/39 wiring, per the coordinator's own routing)."""
        _seed_contact(session_factory, variables={})
        _result1, captured1 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding_both"),
            text_body="SRTWT7445 outstanding both",
            msg_id="ZZT-outstanding-live-hit-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured1 and captured1[0][0] == "crm_outstanding_report", captured1
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_detail", (
            f"a real hit must arm the detail pending the next turn reads: {open_question!r}"
        )
        assert open_question.get("payload", {}).get("filters", {}).get("product_code") == PRODUCT_CODE, (
            f"the hit must carry its own filters forward: {open_question.get('payload')!r}"
        )

        _result2, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-live-hit-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured2, "the detail pick must re-run the tool"
        name, args = captured2[0]
        assert name == "crm_outstanding_report", name
        assert args.get("detail") == "so", f"'1' must ask for the SO detail: {args}"
        reply = (_result2.reply or {}).get("text") or ""
        assert "*SO Number:* SO1" in reply, (
            f"the reply must be the numbered SO detail list (AC-1106): {reply!r}"
        )

    def test_the_live_pick_shape_reruns_the_report_not_the_order_list(
        self, session_factory, monkeypatch
    ) -> None:
        """Finding 7 (console run 4), with the LIVE parser output as its contract - read
        off `chatbot.turns.trace` for the real "1" turn on the prod copy:

            parser raw: message_type casual, entities [], reference_positions [1]
            received:   pending outstanding_detail, last_result_set the two offer rows

        which is what the two tests above already feed. They pass because their FAKE
        resolver resolves every token to the product, so the gate always saw one. Through
        the REAL resolver the turn goes the way production did: the generic
        "reference_positions -> entities" step (`output_exchange.py`) rewrites the turn's
        entities to the picked ROW LABEL ("Sales order list", hinted order), the carried
        product entity is gone, the final `_apply_outstanding_pending` pass then reads
        those entities as a NEW ask and drops the pending - so the lane has no product,
        falls through the report override and answers with the plain order list, exactly
        as the console run read."""
        from tests._mc_lookup_seed import product as seed_product

        db = session_factory()
        seed_product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT7445")
        db.commit()
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "outstanding_detail",
                "last_result_set": [
                    {"idx": 1, "label": "Sales order list", "value": "so"},
                    {"idx": 2, "label": "Delivery order list", "value": "do"},
                ],
                "outstanding_filters": {
                    "product_code": "ZZT7445",
                    "date_filter_start": "2026-01-01",
                    "date_filter_end": "2026-12-31",
                    "customer_ids": [],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "pending": {"kind": "outstanding_detail"},
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-live-pick-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
            real_resolver=True,
        )
        assert captured, "the detail pick must re-run a tool"
        name, args = captured[0]
        assert name == "crm_outstanding_report", (
            f"'1' against an open detail offer re-runs the REPORT, never the plain order "
            f"list: {name} {args}"
        )
        assert args.get("detail") == "so", f"'1' must ask for the SO detail: {args}"
        assert args.get("product_code") == "ZZT7445", (
            f"the carried product must survive the positional pick: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "*SO Number:*" in reply, f"the reply must be the numbered SO list: {reply!r}"
        assert "Couldn't find" not in reply, (
            f"the picked ROW LABEL is not an entity to look up: {reply!r}"
        )

    def test_a_new_product_code_drops_the_pending(self, session_factory, monkeypatch) -> None:
        _seed_open_outstanding_detail(session_factory)
        other_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        _result, _captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status=None, entities=[
                {"raw": "SRTWC999", "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ]),
            text_body="SRTWC999",
            msg_id="ZZT-outstanding-detail-new-code-1",
            attributes=["sales_orders.outstanding"],
            matches={"SRTWC999": {"uuid": other_uuid, "entity_type": "product", "canonical_code": "SRTWC999"}},
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_detail", (
            f"a new product code must drop the outstanding_detail pending, not answer it: {open_question!r}"
        )


# --------------------------------------------------------------------------- #
# R2 (owner ruling, 13 Sep 2026) - the detail offer is STICKY
# --------------------------------------------------------------------------- #


class TestDetailOfferIsSticky:
    """"After '1' (SO list), typing '2' must give the DO list" - the owner's own
    report. Today `outstanding_detail` is a ONE-TURN pending: `tail/compile_state.py::
    _offer_carry` explicitly excludes it (S4 points 4/5's own comment, "answered on
    the very next turn or not at all"), so the first pick consumes it and a second
    pick falls into the generic order lane. Rule (owner, 13 Sep 2026): the offer stays
    open across picks and casual turns until a NEW ASK (a product code or a domain
    word) or a topic change - `_offer_carry`'s OWN condition, the one `suggest_offer`
    and the tier offer already carry under (a new label this turn replaces it; a
    domain change clears it; otherwise it survives, including across the answer this
    turn just produced), never `member_offer`'s TTL. Every assertion here is on the
    RENDERED TEXT / the tool call args, the same as a real turn - not a hand-inspected
    session key, because that is what a customer and the owner both actually see.
    """

    def test_detail_offer_survives_a_pick(self, session_factory, monkeypatch) -> None:
        """Report -> "1" -> "2" gives the DO detail with the SAME carried filters,
        with NO re-seed between the two picks - "2" only resolves at all if the
        offer the report armed is still open after "1" answered it."""
        _seed_open_outstanding_detail(session_factory)
        result1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1", msg_id="ZZT-sticky-pick-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured1 and captured1[0][1].get("detail") == "so", captured1
        reply1 = (result1.reply or {}).get("text") or ""
        assert "*SO Number:*" in reply1, reply1

        result2, captured2 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2", msg_id="ZZT-sticky-pick-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured2, (
            "the offer must still be open after '1' answered it - '2' must re-run the "
            "report, not fall into a different lane with no tool call at all"
        )
        name2, args2 = captured2[0]
        assert name2 == "crm_outstanding_report", (name2, args2)
        assert args2.get("detail") == "do", f"'2' must give the DO detail: {args2}"
        assert args2.get("product_code") == PRODUCT_CODE, (
            f"the SAME carried filters, not re-parsed from a bare '2': {args2}"
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert "*DO Number:*" in reply2, reply2

    # * **`test_detail_offer_survives_a_casual_turn`**, **`test_a_casual_turn_under_an_
    #   open_offer_reprints_it_rather_than_greeting`**, **`test_the_reprint_uses_the_
    #   same_offer_form_the_report_used`** - RETIRED (coordinator ruling, 17 Sep 2026).
    #   The S6 cluster-4 owner ruling ("an open question is answered only when the
    #   PARSER says so"; absent/null `answers_open_question` is NOT an attempt to
    #   answer, and the correct behaviour is CARRY THE PENDING SILENTLY, never
    #   re-print it) applies to EVERY pending kind, including `outstanding_scope` /
    #   `outstanding_detail` - these three tests' own premise (a casual aside RE-
    #   PRINTS the open offer's text) is the ruling this session amends AC-1143c/R22
    #   away from. Replacement coverage:
    #   `test_rearch_s6_open_question_parser.py::TestAbsentAnswerCarriesThePendingWith
    #   outReprinting` - parametrized over every one of `PENDING_KINDS` (including the
    #   two outstanding kinds), proves the pending survives untouched AND that `apply()`
    #   returns no fetch for a casual aside, without asserting any re-print text at all.
    def test_detail_offer_drops_on_a_new_ask(self, session_factory, monkeypatch) -> None:
        """A product code after the report is a NEW ASK, not a pick against the old
        offer: a fresh report for the NEW product runs, and the old offer is gone -
        typing "2" afterwards must never resolve against the FIRST product's rows."""
        _seed_open_outstanding_detail(session_factory)
        other_uuid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        other_code = "SRTWC999"
        other_hit = {**REPORT_HIT, "product_code": other_code}
        _result1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_qf(order_status="outstanding_both", entities=[
                {"raw": other_code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ]),
            text_body=f"{other_code} outstanding both",
            msg_id="ZZT-sticky-new-ask-1",
            attributes=["sales_orders.outstanding"],
            matches={other_code: {"uuid": other_uuid, "entity_type": "product", "canonical_code": other_code}},
            mcp_response=other_hit,
        )
        assert captured1 and captured1[0][1].get("product_code") == other_code, (
            f"a product code is a NEW ASK and must run its own report: {captured1}"
        )

        result2, captured2 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2", msg_id="ZZT-sticky-new-ask-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        if captured2:
            assert captured2[0][1].get("product_code") != PRODUCT_CODE, (
                f"the ORIGINAL offer must be gone - '2' must never resolve against the "
                f"first product's stale filters: {captured2}"
            )
        reply2 = (result2.reply or {}).get("text") or ""
        assert PRODUCT_CODE not in reply2, (
            f"the first product's detail must never come back from a bare '2' once a "
            f"new ask replaced the offer: {reply2!r}"
        )

    def test_detail_offer_survives_an_out_of_range_pick(self, session_factory, monkeypatch) -> None:
        """Only options 1, 2 and 3 are offered on an `outstanding_both` detail offer
        (SO / DO / "Both lists", R14); "4" is out of range and must not close the
        offer - a subsequent "1" must still resolve, the same re-ask rule AC-1132
        already gives the scope question.

        FIXED (coordinator ruling, 17 Sep 2026, item d): this test's own premise was
        wrong - "3" is NOT out of range on an `outstanding_both` offer, it is the
        valid "Both lists" option (R14) - a test asserting position 3 fails to resolve
        was measuring a defect in the TEST, not the engine. Position 4 is the first
        genuinely invalid one.

        AC-1592 test triage (queue item 2c, session-shape rename, 16 Sep 2026): armed
        with a REAL turn 1 rather than `_seed_open_outstanding_detail`'s hand-written
        `variables` dict, which is invisible to the current engine (same finding as
        this class's other real-turn-1 ports)."""
        _seed_contact(session_factory, variables={})
        _result0, captured0 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding_both"),
            text_body="SRTWT7445 outstanding both",
            msg_id="ZZT-sticky-oor-0",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured0 and captured0[0][0] == "crm_outstanding_report", captured0
        _result1, captured1 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[4],
            ),
            text_body="4", msg_id="ZZT-sticky-oor-1",
            attributes=["sales_orders.outstanding"],
        )
        assert not captured1, "an out-of-range pick must not run any report"
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_detail", (
            f"an out-of-range pick must not close the offer: {open_question!r}"
        )

        result2, captured2 = _run_turn(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1", msg_id="ZZT-sticky-oor-2",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured2, "'1' after the out-of-range pick must still resolve against the offer"
        name2, args2 = captured2[0]
        assert name2 == "crm_outstanding_report", (name2, args2)
        assert args2.get("detail") == "so", f"'1' must give the SO detail: {args2}"
        reply2 = (result2.reply or {}).get("text") or ""
        assert "*SO Number:*" in reply2, reply2


# --------------------------------------------------------------------------- #
# D17 (owner design ruling, 13 Sep 2026) - RETIRES D16's detail-offer word
# table. The parser resolves "DO list" / "SO list" / "delivery to hanlim" now;
# the head only ever maps a POSITION. Live behaviour (word -> position) is the
# console case's job (`2026-09-13-outstanding-report.yaml`); pytest keeps the
# deterministic, position-only half.
# --------------------------------------------------------------------------- #


class TestDetailAnswerByPositionOnly:
    def test_position_1_after_an_so_only_report_still_gives_the_so_list(
        self, session_factory, monkeypatch
    ) -> None:
        """R9 (owner testing round 3, 13 Sep 2026): a single-scope report's offer is
        now rendered as ONE sentence (`Reply 1 for the sales order list.`), not a
        numbered list - `test_report_so_scope_only_omits_do_block_and_uses_single_line_
        offer` (presenter suite) pins the TEXT change. This is the regression guard on
        the other side: the deterministic resolver must still accept "1" against a
        single-option offer exactly as it did against a two-option one - unaffected by
        how the offer reads, because it only ever reads `last_result_set` /
        `reference_positions`, never the rendered sentence."""
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "outstanding_detail",
                "last_result_set": [
                    {"idx": 1, "label": "Sales order list", "value": "so"},
                ],
                "outstanding_filters": {
                    "product_code": PRODUCT_CODE,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [],
                    "warehouse_codes": [],
                },
                "pending": {"kind": "outstanding_detail"},
            },
        )
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-detail-position-so-only-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "'1' must still resolve the single-option offer"
        name, args = captured[0]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("detail") == "so", f"'1' must give the SO detail: {args}"

    def test_position_not_on_offer_reprints_rather_than_falling_through(
        self, session_factory, monkeypatch
    ) -> None:
        """Only option 1 is on offer (the DO block was empty) - position 2 names
        nothing stored, so it must NOT resolve as a pick (no tool call) and must NOT
        fall into the generic order lane; the SAME offer is re-printed instead. This
        is the structural half of what used to be `test_do_list_word_after_an_so_
        only_report_reprints_the_offer` (D16, retired) - the WORD "DO list" no longer
        reaches this function at all under D17, but an out-of-range POSITION still
        must re-ask rather than silently drop through."""
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "outstanding_detail",
                "last_result_set": [
                    {"idx": 1, "label": "Sales order list", "value": "so"},
                ],
                "outstanding_filters": {
                    "product_code": PRODUCT_CODE,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [],
                    "warehouse_codes": [],
                },
                "pending": {"kind": "outstanding_detail"},
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2",
            msg_id="ZZT-outstanding-detail-position-unoffered-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"position 2 names a scope that was never offered - it must not run any "
            f"report at all: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Sales order list" in reply, (
            f"the SAME offer must be re-printed, not a fall-through to the generic "
            f"order lane: {reply!r}"
        )
        assert "Transporter" not in reply and "Lorry Plate" not in reply, (
            f"an unoffered position must never fall into the generic order-list lane: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# S2 (security review, 13 Sep 2026) - the legacy so_outstanding bucket, gated
# --------------------------------------------------------------------------- #


class TestNoSoKeyCustomerOnlySoAskFallsToDoBucket:
    """The D13 gate on a customer-only SO ask ("outstanding SO for Dealer A", no
    product), REWRITTEN for R13 (owner ruling, 13 Sep 2026).

    S2 (security review, 13 Sep 2026) pinned this against the LEGACY
    `crm_order_management_orders_list?order_status=so_outstanding` bucket, because a
    customer-only ask could not reach the report at all then. R13 retires that
    carve-out: the ask now goes to `crm_outstanding_report` with `customer_ids`, and
    the SAME gate applies there, before any fetch - scope forced to `do`, the refusal
    line in the reply, no SO figure computed. The property under test is unchanged
    (without the grant, no per-SO outstanding quantity reaches the customer); only the
    tool it is enforced on has moved, which is the point of R13."""

    def test_no_so_key_customer_only_so_ask_falls_to_do_bucket(self, session_factory, monkeypatch) -> None:
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                requested_attributes=["quantity"],
                entities=[
                    {
                        "raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="outstanding SO for Dealer A",
            msg_id="ZZT-so-bucket-gate-1",
            attributes=[],
            matches={CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
            mcp_response={
                "items": [{"title": "DO1", "fields": [{"key": "do_number", "label": "DO Number", "value": "DO1"}]}],
                "has_result": True,
                "intro": "Here are the results.",
            },
        )
        assert captured, "the DO half must still be fetched, never nothing"
        name, args = captured[0]
        assert name == "crm_outstanding_report", (
            f"R13: a customer-only outstanding ask reaches the report now: {name}"
        )
        assert args.get("scope") == "do", (
            f"without the grant the SO scope is refused before any fetch: {args}"
        )
        assert args.get("customer_ids") == [CUSTOMER_UUID], args
        assert "include_pipeline" not in args, (
            f"include_pipeline (carries so_outstanding_qty) must never ride along: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Sales order figures are not enabled for your account." in reply, reply

    def test_with_the_grant_the_so_bucket_runs_unredirected(self, session_factory, monkeypatch) -> None:
        """The grant is the ONLY thing the gate reads - same ask, held key, no redirect."""
        _seed_contact(session_factory, variables={})
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="so_outstanding",
                entities=[
                    {
                        "raw": CUSTOMER_NAME, "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="outstanding SO for Dealer A",
            msg_id="ZZT-so-bucket-gate-2",
            attributes=["sales_orders.outstanding"],
            matches={CUSTOMER_NAME: {"uuid": CUSTOMER_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_NAME}},
            mcp_response={"data": [], "has_result": False},
        )
        assert captured, "the SO half must still be fetched"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("scope") == "so", (
            f"with the grant, the SO scope reaches the report unredirected: {args}"
        )
        assert args.get("customer_ids") == [CUSTOMER_UUID], args


# --------------------------------------------------------------------------- #
# N4 (security review, 13 Sep 2026) - outstanding_filters does not outlive its turn
# --------------------------------------------------------------------------- #


class TestOutstandingFiltersDoNotOutliveTheAnsweringTurn:
    def test_a_later_unrelated_turn_clears_outstanding_filters(self, session_factory, monkeypatch) -> None:
        """`outstanding_filters` used to fall back to `prev.outstanding_filters`
        unconditionally whenever this turn armed no NEW outstanding ask - so a scope
        or detail ask that MISSED (no `outstanding_ask` re-armed) left the customer's
        resolved product/customer/location sitting in session state forever, since
        nothing ever wrote over or cleared it again. `pending` for
        `outstanding_scope`/`outstanding_detail` is already one-turn-life
        (`compile_state._offer_carry`'s own exclusion); `outstanding_filters` must
        share that lifetime - carried only across the ONE turn that answers an open
        ask, gone on any turn after."""
        _seed_contact(
            session_factory,
            variables={
                "outstanding_filters": {
                    "product_code": PRODUCT_CODE, "date_filter_start": None, "date_filter_end": None,
                    "customer_ids": [], "warehouse_codes": [],
                },
                # The answering turn already ran and moved on - no open outstanding
                # ask survives it, same as after any other one-turn pending.
                "pending": None,
                "selection_context": None,
            },
        )
        other_uuid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status=None,
                entities=[
                    {
                        "raw": "SRTWC999", "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="stock for SRTWC999",
            msg_id="ZZT-outstanding-filters-clear-1",
            matches={"SRTWC999": {"uuid": other_uuid, "entity_type": "product", "canonical_code": "SRTWC999"}},
            mcp_response={
                "items": [{"title": "SRTWC999", "fields": [{"key": "product_code", "label": "Product", "value": "SRTWC999"}]}],
                "has_result": True,
                "intro": "Here are the results.",
            },
        )
        # AC-1592 test triage (queue item 2c, session-shape rename, 16 Sep 2026):
        # `outstanding_filters` is `open_question.payload.filters` now, one-turn-life
        # with the rest of `open_question` (`turn/pending.py` has no separate carry
        # for it) - an unrelated later turn that arms no scope/detail question of its
        # own leaves NO `open_question` at all, the flat equivalent of the old "key
        # absent" assertion.
        open_question = _session_of(session_factory).get("open_question")
        assert not open_question, (
            f"outstanding_filters (now open_question.payload.filters) must not "
            f"survive past the one turn that answers an open scope/detail ask: "
            f"{open_question}"
        )


# --------------------------------------------------------------------------- #
# D17 (owner design ruling, 13 Sep 2026) - the parser reads words, the head only
# ever maps a POSITION. Three more things this ruling pins in pytest: the open
# question's own option labels reach the text the engine sends to the parser,
# the retired D16 word table is actually GONE, and the prompt itself carries the
# new instruction (the live word-to-position behaviour is verified by the
# console case, `tests/chatbot/console_cases/2026-09-13-outstanding-report.yaml`,
# not by pytest - there is no LLM in this suite).
# --------------------------------------------------------------------------- #


class TestParserContextCarriesTheOpenQuestionsOptions:
    """D17 point 1: "the parser prompt carries the open question's numbered
    options, the same way `last_result_set` / `selection_context` are surfaced to
    the parser today" - today that surfacing is `head/parser.py::build_user_block`'s
    `pending_kind` fact line (`engine.py:1205-1209`), the ONE per-turn fact about an
    open ask this text carries. `capture_user_block` (`_run_turn`'s own new kwarg,
    this file's addition) reads the ACTUAL text handed to `parser.parse`, so these
    tests do not have to guess an internal builder's parameter name."""

    def test_open_scope_question_surfaces_its_option_labels_to_the_parser(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_scope(session_factory)
        blocks: list[str] = []
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-parser-context-scope-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
            capture_user_block=blocks,
        )
        assert blocks, "the turn must have reached the parser at all"
        user_block = blocks[0]
        for label in ("Sales orders", "Delivery orders", "Both"):
            assert label in user_block, (
                f"the open scope question's own option {label!r} must reach the text "
                f"handed to the parser, so it can read a word answer against what was "
                f"actually offered: {user_block!r}"
            )

    def test_open_detail_offer_surfaces_its_option_labels_to_the_parser(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_detail(session_factory)
        blocks: list[str] = []
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-parser-context-detail-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
            capture_user_block=blocks,
        )
        assert blocks, "the turn must have reached the parser at all"
        user_block = blocks[0]
        assert "Sales order list" in user_block, user_block
        assert "Delivery order list" in user_block, user_block

    def test_no_open_question_carries_no_options(self, session_factory, monkeypatch) -> None:
        """A turn with no open outstanding ask must carry no option text at all - the
        labels are the OPEN question's own, never a standing roster. Passes already
        (nothing surfaces options today); kept as the contract's other half, so a
        later change that always attaches the last-seen options cannot regress past
        this class unnoticed."""
        _seed_contact(session_factory, variables={})
        blocks: list[str] = []
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding"),
            text_body="SRTWT7445 outstanding",
            msg_id="ZZT-outstanding-parser-context-none-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            capture_user_block=blocks,
        )
        assert blocks, "the turn must have reached the parser at all"
        user_block = blocks[0]
        assert "Sales orders" not in user_block, user_block
        assert "Delivery orders" not in user_block, user_block


# `TestOutstandingWordTableIsRetired` (B2, AC-1592): RETIRED, not ported. It asserted
# `head/output_exchange.py` carries neither `_OUTSTANDING_WORD_VALUES` nor
# `_outstanding_word_pick` - D17's "the parser resolves words, the head only ever maps a
# position". `head/output_exchange.py` is now deleted outright (AC-1594), so the
# assertion is true in the strongest possible way and the import it needed
# (`from app.services.chatbot.head import output_exchange`) raises ModuleNotFoundError
# before it ever runs. Nothing to port to: there is no successor "head" module that maps
# a bare position the way D17 described, because the whole head/route split is gone -
# the parser-only decider (`turn/policy.py`, `turn/narrow.py`) reads a position straight
# off the verdict. AC-1594's own grep guard test is what would catch the table's return.


class TestParserPromptTeachesOpenQuestionAnswers:
    def test_prompt_instructs_reference_positions_for_an_open_question_answer(self) -> None:
        """D17 point 1's instruction text, in substance (this file's own choice of
        exact wording - the addendum's phrasing is the coder's to write): the parser
        must emit the open question's position for a numbered OR worded answer, and
        must treat a message naming something new as not an answer at all."""
        from app.services import chatbot_parser_prompt as prompt_mod

        addendum = prompt_mod.GROWTH_R1_ADDENDUM
        assert "reference_positions" in addendum, (
            "the addendum must instruct the parser to emit reference_positions for an "
            "open numbered question, whether the answer is a number or a word naming "
            "an option"
        )
        assert "not an answer" in addendum.lower() or "new ask" in addendum.lower(), (
            "the addendum must say that a message naming something new (a product, a "
            "customer, an order, another topic) is NOT an answer to the open question"
        )


# --------------------------------------------------------------------------- #
# R13 (owner ruling, 13 Sep 2026) - the customer-only carve-out (D11, S4 point 2)
# is RETIRED for outstanding asks: a customer with no product now reaches
# crm_outstanding_report too, never the legacy so_outstanding/include_pipeline
# bucket. "when we generate the outstanding summary for customer and for
# product it is different, they should be the same."
# --------------------------------------------------------------------------- #

CUSTOMER_ONLY_UUID = "cccccccc-1111-cccc-1111-cccccccc1111"
CUSTOMER_ONLY_NAME = "Hanlim Trading"

#: What the ROUTE returns for a CUSTOMER-subject ask (R13): no product echo, and the
#: By product group in place of By customer.
CUSTOMER_SUBJECT_HIT = {
    **REPORT_HIT,
    "product_code": None,
    "customer_name": CUSTOMER_ONLY_NAME,
    "so_by_product": [{"product_code": PRODUCT_CODE, "ordered_qty": 10, "outstanding_qty": 7}],
    "do_by_product": [{"product_code": PRODUCT_CODE, "do_qty": 7, "pending_qty": 7}],
}
del CUSTOMER_SUBJECT_HIT["so_by_customer"]
del CUSTOMER_SUBJECT_HIT["do_by_customer"]


class TestAnswerTurnTakesItsSubjectFromTheStoredFilters:
    """Live on the restarted stack, 13 Sep 2026: "outstanding report for hanlim" armed
    the scope question correctly, and "3" then ended with "That would search every
    delivery order we have - I need at least one filter to narrow it down" and no tool
    call at all.

    The answering turn's parse is JUST a position - no entities, nothing to resolve - so
    the restored filters are the only subject there is. With `product_code: None` and six
    carried `customer_ids` nothing treated the customer as a subject, the resolve+gate
    step found no entity to put in scope, and the turn fell to the order lane's
    empty-filter refusal. Both tests below feed exactly that shape: an empty parse plus a
    position, with a CUSTOMER-ONLY filter set."""

    def _seed_customer_only_scope(self, session_factory) -> None:
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "outstanding_scope",
                "last_result_set": [
                    {"idx": 1, "label": "Sales orders", "value": "so"},
                    {"idx": 2, "label": "Delivery orders", "value": "do"},
                    {"idx": 3, "label": "Both", "value": "both"},
                ],
                "outstanding_filters": {
                    "product_code": None,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [CUSTOMER_ONLY_UUID],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "pending": {"kind": "outstanding_scope"},
            },
        )

    def test_scope_answer_with_customer_only_filters_runs_the_report(
        self, session_factory, monkeypatch
    ) -> None:
        self._seed_customer_only_scope(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
            text_body="3",
            msg_id="ZZT-outstanding-answer-customer-only-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=CUSTOMER_SUBJECT_HIT,
        )
        assert captured, (
            "the answering turn must run the report on the carried customer - it ended "
            "with the order lane's empty-filter refusal and no tool call at all"
        )
        name, args = captured[0]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("customer_ids") == [CUSTOMER_ONLY_UUID], args
        assert args.get("scope") == "both", args
        assert not args.get("product_code"), (
            f"no product was ever named, so none may be invented: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith("Product: all\n"), reply
        assert "*_By product_*" in reply, reply

    def test_detail_pick_after_a_customer_subject_report_reruns_with_the_customer(
        self, session_factory, monkeypatch
    ) -> None:
        """The same on the detail offer: "2" after a customer-subject report must re-run
        with the carried customer, never fall through for want of a product."""
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "outstanding_detail",
                "last_result_set": [
                    {"idx": 1, "label": "Sales order list", "value": "so"},
                    {"idx": 2, "label": "Delivery order list", "value": "do"},
                    {"idx": 3, "label": "Both lists", "value": "both"},
                ],
                "outstanding_filters": {
                    "product_code": None,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [CUSTOMER_ONLY_UUID],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "pending": {"kind": "outstanding_detail"},
            },
        )
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
            ),
            text_body="2",
            msg_id="ZZT-outstanding-answer-customer-only-2",
            attributes=["sales_orders.outstanding"],
            mcp_response=CUSTOMER_SUBJECT_HIT,
        )
        assert captured, "the detail pick must re-run the report on the carried customer"
        name, args = captured[0]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("customer_ids") == [CUSTOMER_ONLY_UUID], args
        assert args.get("detail") == "do", args
        assert not args.get("product_code"), args


class TestCustomerOnlyOutstandingAskReachesTheReport:
    def test_bare_outstanding_customer_only_arms_the_scope_question(
        self, session_factory, monkeypatch
    ) -> None:
        """Today: `has_product` is a HARD requirement of the outstanding-report
        override (`lanes/business/__init__.py`, "S4 point 2"), so a customer-only
        bare "outstanding" ask never arms the scope question at all - it falls
        through to the plain order-list tool / the legacy so_outstanding bucket."""
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint="check_order", domain_hint="order",
                order_status="outstanding",
                entities=[
                    {
                        "raw": "hanlim", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="outstanding report for hanlim",
            msg_id="ZZT-outstanding-customer-only-scope-1",
            attributes=["sales_orders.outstanding"],
            matches={"hanlim": {"uuid": CUSTOMER_ONLY_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_ONLY_NAME}},
        )
        assert captured == [], (
            f"no report/order tool may be called while the scope question is open: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_scope", (
            f"a customer-only outstanding ask must arm the scope question exactly like "
            f"a product ask does: {open_question!r}"
        )
        assert open_question.get("payload", {}).get("filters", {}).get("customer_ids") == [
            CUSTOMER_ONLY_UUID
        ], (
            f"the resolved customer must be stored in the carried filter set even with "
            f"no product: {open_question.get('payload')}"
        )

    def test_explicit_scope_word_customer_only_picks_the_report_with_customer_ids(
        self, session_factory
    ) -> None:
        """A customer-only ask that ALREADY names its scope (`do_outstanding` here,
        e.g. "delivery order outstanding for hanlim") must run the report directly,
        `customer_ids` set, no `product_code` needed at all."""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        payload = {
            "gate": {
                "compatible_entities": [
                    {"uuid": CUSTOMER_ONLY_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_ONLY_NAME},
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": _parser_output(
                        domain_hint="order", intent_hint="check_order", order_status="do_outstanding",
                        entities=[
                            {
                                "raw": "hanlim", "hint": "customer", "canonical_code": None,
                                "current_message": True, "confident": True,
                            },
                        ],
                    )
                },
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": ["sales_orders.outstanding"]},
            },
        }
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, (
            "a customer-only ask that names its own scope must still reach "
            "crm_outstanding_report - today it needs a product entity to be picked at all"
        )
        name, args = captured[0]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("customer_ids") == [CUSTOMER_ONLY_UUID], args
        assert args.get("scope") == "do", args
        assert not args.get("product_code"), (
            f"no product was named, so product_code must be absent/empty: {args}"
        )

    def test_delivery_to_hanlim_without_an_outstanding_word_stays_on_the_order_lane(
        self, session_factory, monkeypatch
    ) -> None:
        """Regression lock: R13 relaxes the PRODUCT requirement, never the SCOPE
        requirement - a customer ask that names no outstanding/delivery-status word
        at all is still a plain order-list ask, not a report. (This is expected to
        pass already; kept here so a later change cannot fold "any order-domain
        customer ask" into the report by accident.)"""
        from app.services.chatbot.lanes.business import run_fetch

        call, captured = _capturing_mcp(REPORT_HIT)
        payload = {
            "gate": {
                "compatible_entities": [
                    {"uuid": CUSTOMER_ONLY_UUID, "entity_type": "customer", "canonical_code": CUSTOMER_ONLY_NAME},
                ]
            },
            "tier_gate": None,
            "ctx": {
                "parse": {
                    "output": _parser_output(
                        domain_hint="order", intent_hint="check_order", order_status=None,
                        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
                        **do_window(),
                        entities=[
                            {
                                "raw": "hanlim", "hint": "customer", "canonical_code": None,
                                "current_message": True, "confident": True,
                            },
                        ],
                    )
                },
                "contact": {"id": CONTACT_ID},
                "access": {"attributes": ["sales_orders.outstanding"]},
            },
        }
        run_fetch(payload, services=FetchServices(mcp_call=call))
        assert captured, "the plain order ask must still be answered"
        name, _args = captured[0]
        assert name != "crm_outstanding_report", (
            f"a customer ask with no outstanding word must stay on the plain order "
            f"lane: {name}"
        )


# --------------------------------------------------------------------------- #
# R15 (owner ruling, 13 Sep 2026) - a turn under an open outstanding question
# that PICKS nothing but carries its OWN filter (dates, a warehouse, a
# customer on a product-subject report) is a REFINEMENT, not an answer and
# not a new ask: the parser's own verdict decides it (`entity_op: "reuse"`, or
# `replace_combine` naming a filter on a DIFFERENT axis from the stored
# subject) - never a bespoke "dates-only" special case, per the owner's
# "too many hardcoding" pushback mid-round. The SAME report re-runs with the
# stored subject overlaid by this turn's own filter, and the SAME offer is
# re-armed with the new window/location - never re-printed unchanged.
# --------------------------------------------------------------------------- #


class TestDateNarrowingUnderAnOpenOffer:
    """AC-1157/AC-1158. Traced from the owner's own turn on the lane stack, 13 Sep
    2026: `outstanding dealer quantity for CNK HARDWARE` -> scope `3` (both) -> the
    detail offer -> `i want to see this month only` (parser: casual, no entities, no
    reference_positions, `date_filter_start/end` on September, `entity_op: "reuse"`)
    RE-PRINTED the offer instead of narrowing it. "you are anticipating me to reply
    for the detail list after offering me the detail list, but i just want to shrink
    the search by date."
    """

    def test_a_date_only_turn_under_the_detail_offer_reruns_the_report_with_the_new_window(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_detail(
            session_factory,
            filters={
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": [],
                "location_token": None,
                "scope": "both",
            },
            rows=[
                {"idx": 1, "label": "Sales order list", "value": "so"},
                {"idx": 2, "label": "Delivery order list", "value": "do"},
                {"idx": 3, "label": "Both lists", "value": "both"},
            ],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-outstanding-date-narrow-detail-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        assert len(captured) == 1, (
            f"exactly one report call, no re-parse of the carried subject: {captured}"
        )
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("order_date_from") == "2026-09-01", (
            f"this turn's own window must reach the tool: {args}"
        )
        assert args.get("order_date_to") == "2026-09-30", args
        assert args.get("scope") == "both", (
            f"the carried scope must survive the date narrowing: {args}"
        )
        assert args.get("customer_ids") == [CUSTOMER_UUID], (
            f"the carried customer subject must survive: {args}"
        )
        assert "detail" not in args, (
            f"a date-only refinement re-runs the REPORT, not a detail pick: {args}"
        )
        assert not args.get("product_code"), (
            f"no product was ever named on this offer, so none must appear now: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "*Sales order outstanding*" in reply, (
            f"the reply must be the report, not the re-printed offer alone: {reply!r}"
        )
        assert "1. Sales order list" in reply and "2. Delivery order list" in reply, (
            f"the re-run must re-arm the detail offer: {reply!r}"
        )
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # .kind, outstanding_filters -> open_question.payload.filters.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_detail", open_question
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("date_filter_start") == "2026-09-01", filters_out
        assert filters_out.get("date_filter_end") == "2026-09-30", filters_out
        assert filters_out.get("customer_ids") == [CUSTOMER_UUID], filters_out

    def test_a_pick_after_the_narrowing_lists_the_new_window_only(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_detail(
            session_factory,
            filters={
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": [],
                "location_token": None,
                "scope": "both",
            },
            rows=[
                {"idx": 1, "label": "Sales order list", "value": "so"},
                {"idx": 2, "label": "Delivery order list", "value": "do"},
                {"idx": 3, "label": "Both lists", "value": "both"},
            ],
        )
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-outstanding-date-narrow-detail-2a",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        _result2, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-date-narrow-detail-2b",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        # Owner hand test, 3 Oct 2026 (PR #1435): on a CUSTOMER-subject offer (no product,
        # as here) "1" is the SO list (`so_status.list_text`), no longer the report's own
        # SO detail. The point of this test stands: the pick runs over the NARROWED window.
        reply2 = (_result2.reply or {}).get("text") or ""
        assert "crm_outstanding_report" not in [name for name, _ in captured2], captured2
        assert "1 Sep 2026 to 30 Sep 2026" in reply2, (
            f"the pick must run over the NARROWED window, not the original one: {reply2!r}"
        )

    def test_a_date_only_turn_under_the_detail_offer_keeps_a_product_subject(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_detail(
            session_factory,
            filters={
                "product_code": PRODUCT_CODE,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
                "scope": "so",
            },
            rows=[
                {"idx": 1, "label": "Sales order list", "value": "so"},
            ],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="just this month",
            msg_id="ZZT-outstanding-date-narrow-product-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the date-only refinement must re-run the report"
        _name, args = captured[0]
        assert args.get("product_code") == PRODUCT_CODE, (
            f"the carried product subject must survive the date narrowing: {args}"
        )
        assert args.get("scope") == "so", args
        assert args.get("order_date_from") == "2026-09-01", args
        assert args.get("order_date_to") == "2026-09-30", args
        assert "detail" not in args, args
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith(f"Product: {PRODUCT_CODE}\n"), (
            f"the re-run must print the report's own header: {reply!r}"
        )

    def test_a_date_only_turn_under_the_scope_question_reasks_with_the_new_window(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_scope(
            session_factory,
            filters={
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": [],
                "location_token": None,
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-outstanding-date-narrow-scope-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"a date-only turn under an open SCOPE question must not fetch anything: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        assert "1. Sales orders" in reply and "2. Delivery orders" in reply, reply
        assert "Order date: 01/09/2026 to 30/09/2026" in reply, (
            f"the re-asked scope question must show the NEW window, in the real "
            f"presenter format: {reply!r}"
        )
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # .kind, outstanding_filters -> open_question.payload.filters.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_scope", open_question
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("date_filter_start") == "2026-09-01", filters_out
        assert filters_out.get("date_filter_end") == "2026-09-30", filters_out
        assert filters_out.get("customer_ids") == [CUSTOMER_UUID], (
            f"the customer subject must be unchanged by the date narrowing: {filters_out}"
        )

    def test_a_pick_carrying_its_own_dates_still_picks(self, session_factory, monkeypatch) -> None:
        """Regression guard (N2's existing rule): a turn that BOTH picks (a
        `reference_positions`) AND carries its own dates is an ANSWER, not a
        refinement - the pick wins and the dates narrow the same re-run. Kept here
        deliberately alongside R15's new tests, since the two rules share the same
        `_apply_outstanding_pending` code path and a fix for one must not break the
        other. May already be green today (grep `names_own_dates` /
        `test_a_date_in_the_answering_turn_wins_over_the_carried_one`) - if so this
        is a guard, not a red test, and the report says so."""
        _seed_open_outstanding_detail(session_factory)
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[2],
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
            ),
            text_body="2, but only September",
            msg_id="ZZT-outstanding-pick-own-dates-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert captured, "the pick must still resolve"
        _name, args = captured[0]
        assert args.get("detail") == "do", f"'2' must give the DO detail: {args}"
        assert args.get("order_date_from") == "2026-09-01", (
            f"the pick's own dates must reach the re-run: {args}"
        )
        assert args.get("order_date_to") == "2026-09-30", args

    def test_a_location_only_turn_under_the_detail_offer_narrows_by_location(
        self, session_factory, monkeypatch
    ) -> None:
        """A warehouse entity under a customer-subject offer is a filter on a
        DIFFERENT axis from the stored subject (customer) - a REFINEMENT, not a new
        ask, even though `entity_op` here is `replace_combine` (the prompt's own
        rule for a bare value under a business domain, not `reuse`)."""
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW", warehouse_name="BRW", is_active=True))
        db.commit()
        _seed_open_outstanding_detail(
            session_factory,
            filters={
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": [],
                "location_token": None,
                "scope": "both",
            },
            rows=[
                {"idx": 1, "label": "Sales order list", "value": "so"},
                {"idx": 2, "label": "Delivery order list", "value": "do"},
                {"idx": 3, "label": "Both lists", "value": "both"},
            ],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None,
                entity_op="replace_combine",
                entities=[
                    {
                        "raw": "BRW", "hint": "warehouse", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                reference_positions=[],
            ),
            text_body="only BRW",
            msg_id="ZZT-outstanding-location-narrow-detail-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        assert len(captured) == 1, (
            f"exactly one report call, the location refines the SAME offer: {captured}"
        )
        name, args = captured[0]
        assert name == "crm_outstanding_report", (name, args)
        assert args.get("warehouse_codes") == ["BRW"], (
            f"the location word must reach the tool as warehouse_codes: {args}"
        )
        assert args.get("customer_ids") == [CUSTOMER_UUID], (
            f"the carried customer subject must survive a location refinement: {args}"
        )
        assert args.get("scope") == "both", args
        assert "detail" not in args, (
            f"a location-only refinement re-runs the REPORT, not a detail pick: {args}"
        )
        _result_unused = result
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # .kind, outstanding_filters -> open_question.payload.filters.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_detail", open_question
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("warehouse_codes") == ["BRW"], filters_out
        assert filters_out.get("customer_ids") == [CUSTOMER_UUID], filters_out

    def test_a_replacing_product_under_a_product_offer_is_still_a_new_ask(
        self, session_factory, monkeypatch
    ) -> None:
        """Guard: a DIFFERENT product named under an open PRODUCT-subject offer is
        still a new ask (never a "refinement" of the product axis - there is only
        ever one product subject), so the old offer must be gone afterwards. Checked
        against `test_detail_offer_drops_on_a_new_ask` (same file) first: that test
        runs a second "2" turn and asserts only on the SECOND turn's captured call
        and reply text, never on the stored `pending` right after the replacing
        turn itself - so this is not a duplicate, it pins the intermediate state
        that test never reads."""
        _seed_open_outstanding_detail(session_factory)
        other_uuid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        other_code = "SRTWC999"
        other_hit = {**REPORT_HIT, "product_code": other_code}
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint="check_order", domain_hint="order",
                entity_op="replace_combine",
                entities=[
                    {
                        "raw": other_code, "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                order_status="outstanding_both",
            ),
            text_body=f"{other_code} outstanding both",
            msg_id="ZZT-outstanding-guard-replace-product-1",
            attributes=["sales_orders.outstanding"],
            matches={other_code: {"uuid": other_uuid, "entity_type": "product", "canonical_code": other_code}},
            mcp_response=other_hit,
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_detail" or (
            open_question.get("payload", {}).get("filters") or {}
        ).get("product_code") != PRODUCT_CODE, (
            f"a different product replaces the offer, it does not extend it: "
            f"{open_question!r}"
        )
        if captured:
            _name, args = captured[0]
            assert args.get("product_code") != PRODUCT_CODE, (
                f"the OLD product's offer must not answer for the new one: {captured}"
            )


# --------------------------------------------------------------------------- #
# Owner round 5 (13 Sep 2026, lane stack, prompt v22): a live turn (`outstanding
# dealer quantity for hanlim`, contact 437264483) hit an ambiguous CUSTOMER picker
# (7 families: HANLIM TRADING SDN BHD (SRT) plus six "STOCK TRANSFER - BRW TO ..."
# companies), and picking "1" replayed the OLD per-product order summary via
# `crm_order_management_orders_list` instead of the outstanding scope question. Trace:
# the "1" turn's derived entities carried the resolved HANLIM customer PLUS an
# UNRESOLVED `{"raw": "BRW", "hint": "warehouse", "current_message": false}` entity left
# over from the PRIOR turn's "only BRW" refinement (R15) - the channel through which a
# warehouse WORD got searched as a customer token and inflated 1 real family to 7.
#
# R16/R17/R18 (owner rulings, 13 Sep 2026), AC-1159/AC-1160/AC-1161.
# --------------------------------------------------------------------------- #


HANLIM_UUID_1 = "11111111-1111-1111-1111-111111111111"
HANLIM_UUID_2 = "22222222-2222-2222-2222-222222222222"
HANLIM_CODE_1 = "300-H070"
HANLIM_CODE_2 = "300-H071"


def _seed_open_outstanding_customer_pick(session_factory) -> None:
    """The state after an outstanding ask ("outstanding dealer quantity for hanlim")
    hit an ambiguous CUSTOMER picker, in the REAL "did-you-mean" shape a customer
    picker persists (`selection_context`/`last_result_set` re-seated from
    `picker_last_result_set`/`picker_selection_context`, `tests/chatbot/
    test_pass4_item2_last_month_keeps_customer_scope.py::_seed_prior_disambiguation_
    state`'s own convention) rather than a hand-invented shape.

    SEED CHANGED BY THE CODER (R16, 13 Sep 2026), per the brief's own allowance, with
    every assertion below left untouched. The proposed `pending: {"kind":
    "outstanding_customer_pick", "order_status": "outstanding"}` is not a shape
    production can produce: `tail/pending.py::derive` returns a marker only for the
    `outstanding_scope` / `outstanding_detail` / `team_clarify` / `member_offer`
    contexts and an open escalation offer, so a turn that stops at the gate's
    ambiguous-customer picker (`selection_context: "disambiguation"`, no offer open)
    persists `pending: null`. What it DOES persist is the ask's own axes - and R16 adds
    `order_status` to them, beside the `date_filter_*` / `requested_attributes` the
    session already kept, which is the key the head's `reuse` carry reads on the pick
    turn. So the seed carries `order_status: "outstanding"` and `pending: None`: the
    real state, measured, rather than a hand-invented marker.
    """
    roster = [
        {
            "idx": 1, "label": "HANLIM TRADING SDN BHD (SRT)", "uuid": HANLIM_UUID_1,
            "product": HANLIM_CODE_1, "entity_type": "customer",
        },
        {
            "idx": 2, "label": "HANLIM TRADING (JB) SDN BHD (SRT)", "uuid": HANLIM_UUID_2,
            "product": HANLIM_CODE_2, "entity_type": "customer",
        },
    ]
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [],
            "selection_context": "disambiguation",
            "last_result_set": roster,
            "picker_last_result_set": roster,
            "picker_selection_context": "disambiguation",
            "picker_domain": "order",
            "outstanding_filters": {
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
            },
            # The interrupted ask's own delivery-status axis, persisted by
            # `tail/compile_state.py` on the turn that asked it (R16).
            "order_status": "outstanding",
            "pending": None,
        },
    )


class TestOwnerRoundFivePickerAndOfferScope:
    def test_a_customer_pick_after_an_outstanding_ask_arms_the_scope_question(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1159/R16: picking the customer that resolved an ambiguous-customer
        picker, opened by an OUTSTANDING ask, must arm the scope question for the
        picked customer - never fetch `crm_order_management_orders_list` (today's
        actual gap, and the owner's own trace)."""
        _seed_open_outstanding_customer_pick(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse",
                order_status=None,
            ),
            text_body="1",
            msg_id="ZZT-outstanding-round5-pick-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"the pick must arm the scope question, never fetch the plain order list: "
            f"{captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        assert "1. Sales orders (not yet transferred to DO)" in reply, reply
        assert "2. Delivery orders (not yet delivered)" in reply, reply
        assert "3. Both" in reply, reply

        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # .kind, outstanding_filters -> open_question.payload.filters - the same rename
        # every other class in this file already reads. Customer-visible assertions
        # (the resumed scope question, the picked customer stored as the subject) kept
        # unchanged, only the internal key path updated.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_scope", (
            f"the picker's own outstanding ask must be resumed as the scope question, "
            f"not left in whatever pending kind the picker itself used: {open_question!r}"
        )
        assert open_question.get("payload", {}).get("filters", {}).get("customer_ids") == [
            HANLIM_UUID_1
        ], (
            f"the CUSTOMER JUST PICKED (position 1) must be the stored subject: "
            f"{open_question.get('payload', {}).get('filters')}"
        )

    def test_a_pick_then_a_scope_answer_runs_the_report_for_the_picked_customer(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1159/R16, continued: answering the re-armed scope question with "3"
        (both) must run the report for the customer picked in the PRIOR turn - no
        product, `scope=both`, no warehouse (the picker's own family list is not a
        location filter)."""
        _seed_open_outstanding_customer_pick(session_factory)
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse",
                order_status=None,
            ),
            text_body="1",
            msg_id="ZZT-outstanding-round5-pick-2a",
            attributes=["sales_orders.outstanding"],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
            text_body="3",
            msg_id="ZZT-outstanding-round5-pick-2b",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        assert captured, "the scope answer must run the report in the same turn"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("customer_ids") == [HANLIM_UUID_1], (
            f"the customer picked two turns ago must be the report's subject: {args}"
        )
        assert args.get("scope") == "both", args
        assert not args.get("warehouse_codes"), (
            f"the customer picker's family list must not leak in as a location filter: {args}"
        )
        _result_unused = result

    def test_a_refinement_location_dies_with_the_offer(self, session_factory, monkeypatch) -> None:
        """AC-1160/R17: after the "only BRW" refinement (R15) narrows an open detail
        offer by location, the raw warehouse entity that carried the narrowing must
        NOT survive in the persisted session `entities` past that turn - that is the
        channel the owner's round-5 leak used (a stale, unresolved `hint: "warehouse"`
        entity read back by the parser on a LATER, unrelated ask and searched as a
        customer token). The refinement's OWN filter set (`outstanding_filters`,
        checked below) is meant to carry the location - `entities` is not.

        Then, on a fresh outstanding ask naming its OWN subject (a customer, no
        product, no location), `outstanding_filters` must start CLEAN - no
        `warehouse_codes` from the earlier refinement, no `location_token`, no dates -
        and the resolver must never be asked to resolve "BRW" as a customer for a
        message that never mentions it."""
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add(Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW", warehouse_name="BRW", is_active=True))
        db.commit()
        _seed_open_outstanding_detail(
            session_factory,
            filters={
                "product_code": None,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [CUSTOMER_UUID],
                "warehouse_codes": [],
                "location_token": None,
                "scope": "both",
            },
            rows=[
                {"idx": 1, "label": "Sales order list", "value": "so"},
                {"idx": 2, "label": "Delivery order list", "value": "do"},
                {"idx": 3, "label": "Both lists", "value": "both"},
            ],
        )
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint=None, domain_hint=None,
                entity_op="replace_combine",
                entities=[
                    {
                        "raw": "BRW", "hint": "warehouse", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                reference_positions=[],
            ),
            text_body="only BRW",
            msg_id="ZZT-outstanding-round5-refine-1",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        # AC-1592 test triage (queue item 2c, session-shape rename, 16 Sep 2026):
        # RETIRED, not ported - the OLD top-level `entities` key is gone (never one of
        # the five), and its check does not translate to `focus.warehouse`: the new
        # `Focus` model is DESIGNED to carry a warehouse entity forward across turns
        # (that is what narrowing IS now), so a raw, uuid-less "BRW" sitting in
        # `focus.warehouse` after this turn is normal, not the round-5 leak (measured:
        # it is there). The customer-facing half of the SAME concern - a later,
        # unrelated ask must never have the carried warehouse token misread as a
        # CUSTOMER search - is what the second turn below actually proves
        # (`customer_ids == [hanlim_uuid]`, no stray customer search for "BRW"), and
        # it is unaffected by this retirement.

        # A brand-new ask, its own subject, no scope/location word about the earlier
        # refinement at all.
        hanlim_uuid = "44444444-4444-4444-4444-444444444444"
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="business_query", intent_hint="check_order", domain_hint="order",
                entity_op="replace_combine",
                order_status="outstanding",
                entities=[
                    {
                        "raw": "hanlim", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                reference_positions=[],
            ),
            text_body="outstanding dealer quantity for hanlim",
            msg_id="ZZT-outstanding-round5-new-ask-1",
            attributes=["sales_orders.outstanding"],
            matches={"hanlim": {"uuid": hanlim_uuid, "entity_type": "customer", "canonical_code": "300-H070"}},
        )
        assert captured == [], (
            f"a customer-only outstanding ask arms the scope question, it does not "
            f"fetch: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" in reply, reply
        open_question = _session_of(session_factory).get("open_question") or {}
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("warehouse_codes") == [], (
            f"a new ask's filter set must not inherit the earlier refinement's "
            f"location: {filters_out}"
        )
        assert filters_out.get("location_token") is None, filters_out
        assert filters_out.get("customer_ids") == [hanlim_uuid], filters_out
        assert filters_out.get("date_filter_start") is None, filters_out
        assert filters_out.get("date_filter_end") is None, filters_out

    def test_a_warehouse_word_never_enters_the_customer_picker(self) -> None:
        """AC-1161/R18, at the gate level (`test_last_cost_gate.py`'s own
        `gate_mod.run_gate` direct-call pattern - the picker's construction is a pure
        function of `parser` + `resolver`, no session/engine machinery needed to grade
        it). Reproduces the round-5 trace's actual defect: the resolver, asked to
        resolve BOTH the ambiguous "hanlim" customer word AND a co-travelling
        `hint: "warehouse"` token "BRW", answered "BRW" with a CUSTOMER-type match too
        (the real bug measured on the prod copy: `customers.customer_name ilike
        '%hanlim%'` = one real family, `ilike 'STOCK TRANSFER%BRW%'` = a second,
        unrelated family - both fed into `gate.py`'s "AMBIGUOUS CUSTOMER" picker,
        which today keys ONLY on the resolved `entity_type`, never on which raw token
        or parser hint it came from). R18 says a warehouse-hinted token is never a
        customer candidate - the picker must list only the family "hanlim" itself
        matched.

        If a fix instead makes the RESOLVER never mis-type "BRW" in the first place,
        this same fixture (a resolver that already returns a customer-type match for
        it) stops being reachable in production but stays a valid GATE-level regression
        guard for the rule R18 states - the docstring says so rather than deleting the
        test, per the brief's "if the resolver seam ... cannot express X, assert on Y
        and say so" allowance.
        """
        resolver = {
            "tokens": ["hanlim", "BRW"],
            "resolutions": [
                {
                    "token": "hanlim",
                    "matches": [
                        {
                            "entity_type": "customer",
                            "canonical_code": HANLIM_CODE_1,
                            "uuid": HANLIM_UUID_1,
                            "company_code": "SRT",
                            "display": {"customer_name": "HANLIM TRADING SDN BHD"},
                        },
                        {
                            "entity_type": "customer",
                            "canonical_code": HANLIM_CODE_2,
                            "uuid": HANLIM_UUID_2,
                            "company_code": "SRT",
                            # A genuinely DIFFERENT family, not a bracket-suffixed
                            # variant of the first: `gate._cust_base` strips
                            # brackets/parens (`_BRACKET_OR_PAREN`) before grouping, so
                            # "HANLIM TRADING (JB) SDN BHD" collapsed onto "HANLIM
                            # TRADING SDN BHD" as ONE family - the fixture bug the
                            # coder found, review round 13 Sep 2026. "HANLIM HARDWARE"
                            # shares no base string with "HANLIM TRADING" at all, so
                            # this is unambiguously the second real company the
                            # ambiguous "hanlim" word matched.
                            "display": {"customer_name": "HANLIM HARDWARE SDN BHD"},
                        },
                    ],
                },
                {
                    "token": "BRW",
                    "matches": [
                        {
                            "entity_type": "customer",
                            "canonical_code": "300-ST01",
                            "uuid": "33333333-3333-3333-3333-333333333333",
                            "company_code": "SRT",
                            "display": {"customer_name": "STOCK TRANSFER - BRW TO SORENTO"},
                        },
                    ],
                },
            ],
        }
        parser = {
            "domain_hint": "order",
            "order_status": "outstanding",
            "entities": [
                {
                    "raw": "hanlim", "hint": "customer", "canonical_code": None,
                    "current_message": True, "confident": True,
                },
                {
                    "raw": "BRW", "hint": "warehouse", "canonical_code": None,
                    "current_message": False, "confident": True,
                },
            ],
            "reference_positions": [],
        }
        out = gate_mod.run_gate({}, parser=parser, resolver=resolver)
        assert out.get("gate_passed") is False, out
        assert "'order' customer token matches 2 different companies" in (
            out.get("gate_reason") or ""
        ), (
            f"the warehouse-hinted token must never inflate the customer count: "
            f"{out.get('gate_reason')!r}"
        )
        clarification = out.get("gate_clarification") or ""
        assert "STOCK TRANSFER" not in clarification.upper(), (
            f"a warehouse token resolved (however wrongly) as a customer must never "
            f"become a line in the customer picker: {clarification!r}"
        )
        assert "1. HANLIM TRADING SDN BHD (SRT)" in clarification, clarification
        assert "2. HANLIM HARDWARE SDN BHD (SRT)" in clarification, clarification


# --------------------------------------------------------------------------- #
# R19 (owner ruling, 13 Sep 2026, live trace): "i have chosen the customer
# already, but you only say Product ... what about the customer, sometimes i
# might even have dates, location filters, they should be stated down in this
# message also." EVERY scope question - first ask, R15 refinement re-ask, ask
# after a customer-picker pick - prints the SAME four header lines the report
# itself prints, same order, same wording, `all` for anything not given.
# AC-1162 (header, tests 1-4) / AC-1163 (distinct customer names, test 5, in
# tests/test_outstanding_report.py::test_customer_header_dedupes_ledger_names).
# --------------------------------------------------------------------------- #


class TestScopeQuestionCarriesTheFullHeader:
    def test_first_ask_prints_all_four_header_lines(self, session_factory, monkeypatch) -> None:
        """A bare outstanding ask naming a product, a customer, a location word and a
        date window must print all four report-header lines, filled, before the
        question - not just `Product:` (today's actual gap).

        R19b (coder round 7): the `Customer:` line now comes ONLY from real
        `customers` rows for the resolved `customer_ids` (`outstanding_report_
        service._customer_echo`), no entity-label fallback - so the picked customer
        needs a REAL row, not just a resolver match. `tests._mc_lookup_seed.customer`
        mints its OWN id; this test uses the returned `row.id` in place of the old
        `CUSTOMER_UUID` constant (the brief's other option, adding an id-taking
        parameter to that shared helper, would touch every other file that imports
        it for no gain here)."""
        from app.models.inventory import Warehouse

        db = session_factory()
        db.add_all(
            [
                Warehouse(id=str(uuid.uuid4()), warehouse_code="BRW-IB", warehouse_name="BRW IB", is_active=True),
                Warehouse(id=str(uuid.uuid4()), warehouse_code="MWH-IB", warehouse_name="MWH IB", is_active=True),
            ]
        )
        cust = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name=CUSTOMER_NAME)
        db.commit()
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(
                order_status="outstanding",
                entities=[
                    {
                        "raw": PRODUCT_CODE, "hint": "product", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                    {
                        "raw": "Dealer A", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                    {
                        "raw": "IB", "hint": "warehouse", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
                date_filter_start="2026-01-01", date_filter_end="2026-03-31",
            ),
            text_body="Srtwt7445 sales order outstanding for Dealer A in IB from Jan to March",
            msg_id="ZZT-outstanding-r19-first-ask-1",
            attributes=["sales_orders.outstanding"],
            matches={
                PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE},
                "Dealer A": {
                    "uuid": cust.id, "entity_type": "customer", "canonical_code": CUSTOMER_NAME,
                    "display": {"customer_name": CUSTOMER_NAME},
                },
            },
        )
        assert captured == [], (
            f"the scope question fetches nothing: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        expected_header = (
            f"Product: {PRODUCT_CODE}\n"
            f"Customer: {CUSTOMER_NAME}\n"
            "Location: IB (BRW-IB, MWH-IB)\n"
            "Order date: 01/01/2026 to 31/03/2026\n"
            "Outstanding for which document?\n"
        )
        assert reply.startswith(expected_header), reply

    def test_first_ask_prints_all_for_every_missing_filter(self, session_factory, monkeypatch) -> None:
        """A product-only ask must still print all four lines - `Customer:` /
        `Location:` / `Order date:` say `all` rather than being omitted, exactly the
        way the REPORT's own header never omits a line."""
        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_qf(order_status="outstanding"),
            text_body="SRTWT7445 outstanding",
            msg_id="ZZT-outstanding-r19-all-filters-1",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
        )
        assert captured == [], captured
        reply = (result.reply or {}).get("text") or ""
        expected_header = (
            f"Product: {PRODUCT_CODE}\n"
            "Customer: all\n"
            "Location: all\n"
            "Order date: all\n"
            "Outstanding for which document?\n"
        )
        assert reply.startswith(expected_header), reply

    def test_scope_question_after_a_customer_pick_names_the_picked_customer(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1162, R19 x R16: the owner's own live trace - after picking a customer
        off the picker the re-armed scope question printed `Product: SRTKT39SS` and
        nothing else. The picked customer's own name (looked up from the real
        `customers` row, R19b) must appear on a `Customer:` line, and the two axes
        nobody named (`Location:` / `Order date:`) must say `all`, not be silently
        dropped.

        R19b (coder round 7): the `Customer:` line is the customer ROW's own name,
        never the picker's roster label - `(SRT)` there is a company-code suffix the
        picker prints so the reader can tell two accounts apart, not part of the
        customer's name, and R19b forbids it on this line. The picked row (idx 1)
        gets a REAL `customers` row, in place of the old `HANLIM_UUID_1` constant
        (the id-substitution option, same as the other two tests in this class); the
        unpicked row (idx 2) needs none, since only the RESOLVED `customer_ids`
        (here, just the pick) reach the name lookup."""
        product_uuid = "66666666-6666-6666-6666-666666666666"
        product_code = "SRTKT39SS"
        db = session_factory()
        hanlim_trading = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="HANLIM TRADING SDN BHD")
        db.commit()
        roster = [
            {
                "idx": 1, "label": "HANLIM TRADING SDN BHD (SRT)", "uuid": hanlim_trading.id,
                "product": HANLIM_CODE_1, "entity_type": "customer",
            },
            {
                "idx": 2, "label": "HANLIM TRADING (JB) SDN BHD (SRT)", "uuid": HANLIM_UUID_2,
                "product": HANLIM_CODE_2, "entity_type": "customer",
            },
        ]
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [
                    {
                        "raw": product_code, "hint": "product", "uuid": product_uuid,
                        "canonical_code": product_code, "current_message": False,
                    },
                ],
                "selection_context": "disambiguation",
                "last_result_set": roster,
                "picker_last_result_set": roster,
                "picker_selection_context": "disambiguation",
                "picker_domain": "order",
                "outstanding_filters": {
                    "product_code": product_code,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "order_status": "outstanding",
                "pending": None,
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse",
                order_status=None,
            ),
            text_body="1",
            msg_id="ZZT-outstanding-r19-pick-scope-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"the resumed ask arms the scope question, it does not fetch: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert f"Product: {product_code}" in reply, reply
        assert "Customer: HANLIM TRADING SDN BHD" in reply, (
            f"the JUST-PICKED customer's own name must appear on the Customer line: {reply!r}"
        )
        assert "Customer: HANLIM TRADING SDN BHD (SRT)" not in reply, (
            f"the picker's company-code suffix is not part of the customer's name and "
            f"must never reach this line (R19b): {reply!r}"
        )
        assert "Location: all" in reply, reply
        assert "Order date: all" in reply, reply
        assert reply.index("Product:") < reply.index("Customer:") < reply.index("Location:") < (
            reply.index("Order date:")
        ), reply

    def test_refinement_reask_prints_customer_too(self, session_factory, monkeypatch) -> None:
        """AC-1162, R19 x R15: the SAME re-armed scope question a date-only refinement
        prints (`test_a_date_only_turn_under_the_scope_question_reasks_with_the_new_
        window`, `TestDateNarrowingUnderAnOpenOffer`) must ALSO name the stored
        customer subject - today it prints only `Order date:`, dropping the customer
        the question is actually about. Asserted on the reply text only (the brief's
        own preference) - the storage shape (an entities list carrying the resolved
        customer's name, mirrored here from the OTHER tests in this class) is the
        coder's to choose.

        R19b (coder round 7): the `Customer:` line comes ONLY from a real
        `customers` row for the stored `customer_ids`, so this test seeds one and
        uses the returned `row.id` in place of the old `CUSTOMER_UUID` constant (the
        same id-substitution choice made for the other two tests in this class)."""
        db = session_factory()
        cust = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name=CUSTOMER_NAME)
        db.commit()
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [
                    {
                        "raw": CUSTOMER_NAME, "hint": "customer", "uuid": cust.id,
                        "canonical_code": CUSTOMER_NAME, "current_message": False,
                    },
                ],
                "selection_context": "outstanding_scope",
                "last_result_set": [
                    {"idx": 1, "label": "Sales orders", "value": "so"},
                    {"idx": 2, "label": "Delivery orders", "value": "do"},
                    {"idx": 3, "label": "Both", "value": "both"},
                ],
                "outstanding_filters": {
                    "product_code": None,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [cust.id],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "pending": {"kind": "outstanding_scope"},
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-outstanding-r19-refine-reask-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"a date-only turn under an open scope question must not fetch: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Order date: 01/09/2026 to 30/09/2026" in reply, reply
        assert f"Customer: {CUSTOMER_NAME}" in reply, (
            f"the re-asked question must still name the customer it is about: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Owner round 7 (13 Sep 2026, live): `outsatnidng dealer quantity for chin chun
# product SRTKT39SS in 2026` -> ambiguous customer picker, EVERY line stamped
# "- no DO" and "None of these have a matching DO." (Cause 1: the picker's probe
# is `crm_order_management_orders_list`'s DELIVERED-DO population - the OPPOSITE
# of what this outstanding ask is asking about) -> `1` -> scope question whose
# `Customer:` line named the roster's own label, company-code suffix and all
# ("CHIN CHUN HARDWARE SDN BHD (MCH, SRT)") -> `all` -> the REPORT's own
# `Customer:` line named the real customer rows instead (Cause 2: two different
# name sources for the "same" line). "it is still kinda strange for me though,
# to say no DO, then later when i get the summary, there is DO."
# R20 (AC-1164): an OUTSTANDING ask's customer picker prints no DO hint at all,
# and does not probe for one. R19b (AC-1165): the scope question's `Customer:`
# line is byte-equal to the report's own line for the same `customer_ids`.
# --------------------------------------------------------------------------- #


def _customer_header_line(reply: str) -> str:
    for line in reply.split("\n"):
        if line.startswith("Customer:"):
            return line
    return ""


def _ambiguous_hanlim_resolve_services(probe: Any) -> ResolveGateServices:
    """The R18 picker fixture's own two-family shape (HANLIM TRADING SDN BHD /
    HANLIM HARDWARE SDN BHD), reached through a REAL turn this time (`_run_turn`'s
    `resolve_services=` override) rather than a direct `gate.run_gate` call - R20
    lives in `resolve_gate.py`'s probe/annotate step, which a direct gate call never
    reaches. `_resolve_services` (this file's shared fake) hands one match per raw
    token, so an ambiguous single-token pick needs its own `resolve_entity`."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": ["hanlim"],
            "resolutions": [
                {
                    "token": "hanlim",
                    "matches": [
                        {
                            "entity_type": "customer", "canonical_code": HANLIM_CODE_1,
                            "uuid": HANLIM_UUID_1, "company_code": "SRT",
                            "display": {"customer_name": "HANLIM TRADING SDN BHD"},
                        },
                        {
                            "entity_type": "customer", "canonical_code": HANLIM_CODE_2,
                            "uuid": HANLIM_UUID_2, "company_code": "SRT",
                            "display": {"customer_name": "HANLIM HARDWARE SDN BHD"},
                        },
                    ],
                },
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=probe,
    )


class TestOutstandingAskPickerHasNoDeliveryHint:
    def test_outstanding_ask_picker_has_no_do_hint(self, session_factory, monkeypatch) -> None:
        """AC-1164/R20: an OUTSTANDING ask's ambiguous-customer picker must print no
        `- has DO` / `- no DO` suffix and no "None of these have a ... DO." sentence -
        and the probe must not even run, because there is nothing this outstanding
        ask needs it to measure (the probe's own population, DELIVERED DOs, is the
        opposite of what the report's DO block counts). The probe here WOULD answer
        with rows carrying no delivery date if called, so a pass here proves the
        turn chose not to call it, not that the answer happened to come back empty."""
        probe_calls: list[dict[str, Any]] = []

        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: Any, user_prompt: Any) -> Any:
            probe_calls.append({"tool": tool, "entities": entities})
            return {
                "items": [
                    {
                        "title": "SO1",
                        "fields": [{"key": "customer_name", "label": "Customer", "value": "HANLIM TRADING SDN BHD"}],
                    }
                ],
                "has_result": True,
            }

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status="outstanding",
                entities=[
                    {
                        "raw": "hanlim", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="outstanding dealer quantity for hanlim",
            msg_id="ZZT-outstanding-r20-picker-1",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        assert captured == [], captured
        reply = (result.reply or {}).get("text") or ""
        assert "Which customer do you mean?" in reply, reply
        assert " - no DO" not in reply, reply
        assert " - has DO" not in reply, reply
        assert "None of these have" not in reply, (
            f"an outstanding ask's picker must not claim a DO measurement it never took: {reply!r}"
        )
        assert probe_calls == [], (
            f"an outstanding ask's picker must not probe for a delivery order at all: {probe_calls}"
        )

    def test_plain_delivery_ask_picker_keeps_the_do_hint(self, session_factory, monkeypatch) -> None:
        """Guard: R20 is scoped to an OUTSTANDING ask - a plain order/delivery ask
        against the SAME ambiguous customer keeps today's `- has DO` / `- no DO`
        hint unchanged. `pickers.py`'s own unit-level guards already pin the
        underlying `annotate_customer` behaviour this end-to-end turn exercises
        (`tests/chatbot/test_resolve_gate_unit.py::TestPickerProbeArms::
        test_a_defaulted_window_bounds_the_miss_claim`,
        `test_an_order_with_no_delivery_order_is_not_counted_as_one`); this test is
        the LANE-level regression lock that a sloppy R20 implementation (e.g. gating
        on `domain_hint == "order"` instead of the order_status axis) does not also
        silence the hint for every other order-domain picker."""

        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: Any, user_prompt: Any) -> Any:
            return {"items": [], "has_result": False}

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                domain_hint="order", intent_hint="check_order", order_status=None,
                entities=[
                    {
                        "raw": "hanlim", "hint": "customer", "canonical_code": None,
                        "current_message": True, "confident": True,
                    },
                ],
            ),
            text_body="orders for hanlim",
            msg_id="ZZT-outstanding-r20-guard-1",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        assert captured == [], captured
        reply = (result.reply or {}).get("text") or ""
        assert " - no DO" in reply, (
            f"a plain (non-outstanding) ask must keep today's DO hint unchanged: {reply!r}"
        )


class TestScopeQuestionCustomerLineMatchesReportHeader:
    def test_scope_question_customer_line_equals_the_report_header_line(
        self, session_factory, monkeypatch
    ) -> None:
        """AC-1165/R19b: the scope question's `Customer:` line must be byte-equal to
        the REPORT's own `Customer:` line for the SAME resolved `customer_ids` - both
        built from the customer rows (`outstanding_report_service._customer_echo`'s
        distinct, first-seen names), never from the picker's roster label (which
        carries a company-code suffix like "(MCH, SRT)" that is not a customer name
        at all). Real `customers` rows, seeded the way `tests/test_outstanding_
        report.py` seeds them (`tests._mc_lookup_seed.customer`) - `_customer_echo`
        is a DB read, and this is the seam through which R19b's fix reaches it: the
        lane harness fakes the resolver, but the CUSTOMER NAMES here come from the
        real `customers` table via `session_factory`'s own Postgres connection, not
        from anything in the resolver map.

        The picked roster line stands for a THREE-ROW account family - the same
        mechanism `gate.py`'s "A picked CUSTOMER selects its whole ACCOUNT FAMILY"
        re-seat already expands today (proven empirically: `outstanding_filters
        ["customer_ids"]` already holds all three ids after the pick) - so this test
        is pinned on the NAME SOURCE the header reads, not on whether the family
        widens at all.
        """
        db = session_factory()
        c1 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HARDWARE SDN BHD")
        c2 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HARDWARE SDN BHD [A/C I]")
        c3 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HARDWARE SDN BHD - [CERAMIC]")
        db.commit()
        base_key = gate_mod._cust_base(
            {"display": {"customer_name": "CHIN CHUN HARDWARE SDN BHD"}, "canonical_code": None}
        )
        roster = [
            {
                "idx": 1, "label": "CHIN CHUN HARDWARE SDN BHD (MCH, SRT)", "uuid": c1.id,
                "product": None, "entity_type": "customer",
            },
        ]
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [],
                "selection_context": "disambiguation",
                "last_result_set": roster,
                "picker_last_result_set": roster,
                "picker_selection_context": "disambiguation",
                "picker_domain": "order",
                "picker_families": {base_key: [c1.id, c2.id, c3.id]},
                "outstanding_filters": {
                    "product_code": None,
                    "date_filter_start": None,
                    "date_filter_end": None,
                    "customer_ids": [],
                    "warehouse_codes": [],
                    "location_token": None,
                },
                "order_status": "outstanding",
                "pending": None,
            },
        )
        pick_result, pick_captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1], reference_target="dym", entity_op="reuse",
                order_status=None,
            ),
            text_body="1",
            msg_id="ZZT-outstanding-r19b-pick-1",
            attributes=["sales_orders.outstanding"],
        )
        assert pick_captured == [], pick_captured
        expected_name = (
            "CHIN CHUN HARDWARE SDN BHD, "
            "CHIN CHUN HARDWARE SDN BHD [A/C I], "
            "CHIN CHUN HARDWARE SDN BHD - [CERAMIC]"
        )
        pick_reply = (pick_result.reply or {}).get("text") or ""
        assert _customer_header_line(pick_reply) == f"Customer: {expected_name}", (
            f"the scope question's Customer line must name the real customer rows, "
            f"first-seen order, never the roster label: {pick_reply!r}"
        )
        assert "(MCH, SRT)" not in pick_reply, (
            f"a picker label's company-code suffix must never reach the Customer line: {pick_reply!r}"
        )

        report_result, report_captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
            text_body="3",
            msg_id="ZZT-outstanding-r19b-answer-1",
            attributes=["sales_orders.outstanding"],
            mcp_response={**REPORT_HIT, "customer_name": expected_name},
        )
        assert report_captured, "the scope answer must run the report"
        name, args = report_captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("customer_ids") == [c1.id, c2.id, c3.id], args
        report_reply = (report_result.reply or {}).get("text") or ""
        assert _customer_header_line(report_reply) == _customer_header_line(pick_reply), (
            f"the scope question's Customer line and the report's own Customer line "
            f"must be byte-equal for the same customer_ids: "
            f"scope={_customer_header_line(pick_reply)!r} report={_customer_header_line(report_reply)!r}"
        )


# --------------------------------------------------------------------------- #
# Owner round 8 (13 Sep 2026, live): `outsatnidng dealer quantity for chin chun
# product SRTKT39SS in 2026` -> customer picker (3 families) -> `all` -> the
# PLAIN order list for all three families instead of the outstanding scope
# question - `crm_order_management_orders_list`, a total miss. The SAME ask
# answered with `1` worked (armed the scope question correctly). "why when i
# say all for customer picker it didn't work, but when i choose 1 it worked?"
#
# Cause (traced): `entity_op: "clear"` (the parser's raw read of "all") runs
# through the ENTITY OPERATION EXECUTOR - the block that wipes `entities` and
# stamps `entity_op_applied` - BEFORE the "ALL/SEMUA on a numbered menu"
# structural arm re-labels the turn `entity_op: "reuse"` and expands
# `reference_positions` to every offered position. R16's carry (order_status,
# dates, requested_attributes, is_active - the `elif op == "reuse":` branch)
# lives INSIDE that same executor pass and never re-runs once the label
# changes, so a multi-pick "all" loses every axis the SAME executor already
# carries correctly for a single "1" pick (which arrives as `entity_op: reuse`
# from the START, so it takes that branch the first time).
#
# R21 (owner ruling, 13 Sep 2026): a did-you-mean pick, whether one option or
# all of them, continues the question the picker interrupted - the SAME
# carried `order_status` and dates apply. For an outstanding ask that means the
# scope question arms for the UNION of every picked family's `customer_ids`,
# same header (Customer line = every picked family, distinct, first-seen;
# Order date from the original ask), and `crm_order_management_orders_list` is
# never called. AC-1166.
# --------------------------------------------------------------------------- #


PRODUCT_CODE_R21 = "SRTKT39SS"
PRODUCT_UUID_R21 = "77777777-7777-7777-7777-777777777777"


def _seed_open_outstanding_three_family_picker(session_factory) -> tuple[str, str, str]:
    """The state after `outsatnidng dealer quantity for chin chun product
    SRTKT39SS in 2026` hit an ambiguous CUSTOMER picker with THREE families -
    real `customers` rows (R19b: the header can only ever name a row that
    exists), one row per family (a family of size one needs no `picker_families`
    widening - the roster's own uuid IS the whole family). Returns the three
    seeded ids in roster order.

    `date_filter_start` / `date_filter_end` are persisted at the TOP LEVEL
    (never inside `outstanding_filters`, which is this file's OWN carried-filter
    convention, not a session field production writes before a scope question
    is ever armed) - the SAME generic date-carry key the reuse arm already
    reads for every domain (`head/output_exchange.py` ~1917-1922), matching
    what a genuine "... in 2026" ask persists BEFORE any picker interrupts it.
    """
    db = session_factory()
    c1 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HARDWARE SDN BHD")
    c2 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HOMEMART SDN BHD")
    c3 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN TRADING SDN BHD")
    db.commit()
    roster = [
        {
            "idx": 1, "label": "CHIN CHUN HARDWARE SDN BHD (MCH, SRT)", "uuid": c1.id,
            "product": None, "entity_type": "customer",
        },
        {
            "idx": 2, "label": "CHIN CHUN HOMEMART SDN BHD (SRT)", "uuid": c2.id,
            "product": None, "entity_type": "customer",
        },
        {
            "idx": 3, "label": "CHIN CHUN TRADING SDN BHD (SRT)", "uuid": c3.id,
            "product": None, "entity_type": "customer",
        },
    ]
    _seed_contact(
        session_factory,
        variables={
            "message_type": "business_query",
            "domain_hint": "order",
            "entities": [
                {
                    "raw": PRODUCT_CODE_R21, "hint": "product", "uuid": PRODUCT_UUID_R21,
                    "canonical_code": PRODUCT_CODE_R21, "current_message": False,
                },
            ],
            "selection_context": "disambiguation",
            "last_result_set": roster,
            "picker_last_result_set": roster,
            "picker_selection_context": "disambiguation",
            "picker_domain": "order",
            "outstanding_filters": {
                "product_code": PRODUCT_CODE_R21,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
            },
            "order_status": "outstanding",
            "date_filter_start": "2026-01-01",
            "date_filter_end": "2026-12-31",
            "pending": None,
        },
    )
    return c1.id, c2.id, c3.id


def _all_pick_parser_output() -> dict[str, Any]:
    """The parser's OWN raw read of "all" over the picker, verbatim from the owner's
    trace: `entity_op: "clear"`, `broaden_axis: "all"`, `scope_intent: "broaden"`,
    no `reference_positions`, no `order_status`, no dates. The STRUCTURAL "ALL on a
    numbered menu" arm is what turns this into a pick-all - never a parser field
    this file invents."""
    return _parser_output(
        message_type="casual", intent_hint=None, domain_hint=None, entities=[],
        reference_positions=[], reference_target=None, entity_op="clear",
        broaden_axis="all", scope_intent="broaden", order_status=None,
    )


class TestAllOnTheCustomerPickerKeepsTheQuestion:
    def test_all_on_the_customer_picker_arms_the_scope_question_for_every_family(
        self, session_factory, monkeypatch
    ) -> None:
        c1_id, c2_id, c3_id = _seed_open_outstanding_three_family_picker(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_all_pick_parser_output(),
            text_body="all",
            msg_id="ZZT-outstanding-r21-all-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"the pick-all must arm the scope question, never fetch the plain order "
            f"list: {captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        expected_header = (
            f"Product: {PRODUCT_CODE_R21}\n"
            "Customer: CHIN CHUN HARDWARE SDN BHD, CHIN CHUN HOMEMART SDN BHD, "
            "CHIN CHUN TRADING SDN BHD\n"
            "Location: all\n"
            "Order date: 01/01/2026 to 31/12/2026\n"
            "Outstanding for which document?\n"
        )
        assert reply.startswith(expected_header), reply
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # .kind, outstanding_filters -> open_question.payload.filters.
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "outstanding_scope", open_question
        filters_out = open_question.get("payload", {}).get("filters") or {}
        assert filters_out.get("customer_ids") == [c1_id, c2_id, c3_id], (
            f"every picked family's id must be in the stored subject: {filters_out}"
        )
        assert filters_out.get("date_filter_start") == "2026-01-01", filters_out
        assert filters_out.get("date_filter_end") == "2026-12-31", filters_out

    def test_all_pick_then_scope_answer_runs_the_report_for_every_family(
        self, session_factory, monkeypatch
    ) -> None:
        c1_id, c2_id, c3_id = _seed_open_outstanding_three_family_picker(session_factory)
        _run_turn(
            session_factory,
            monkeypatch,
            qf=_all_pick_parser_output(),
            text_body="all",
            msg_id="ZZT-outstanding-r21-all-2a",
            attributes=["sales_orders.outstanding"],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[3],
            ),
            text_body="3",
            msg_id="ZZT-outstanding-r21-all-2b",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        assert captured, "the scope answer must run the report in the same turn"
        name, args = captured[0]
        assert name == "crm_outstanding_report", name
        assert args.get("customer_ids") == [c1_id, c2_id, c3_id], (
            f"the report must run for EVERY picked family, not just one: {args}"
        )
        assert args.get("order_date_from") == "2026-01-01", args
        assert args.get("order_date_to") == "2026-12-31", args
        assert args.get("scope") == "both", args
        _result_unused = result

    def test_all_on_the_customer_picker_under_a_plain_ask_still_lists_every_family(
        self, session_factory, monkeypatch
    ) -> None:
        """Guard: R21 is scoped to an OUTSTANDING ask. A PLAIN order ask against the
        same three-family picker keeps today's behaviour - `all` still lists orders
        for every family via `crm_order_management_orders_list`. No existing test
        pins this exact shape (a `disambiguation` customer picker's own "ALL on a
        numbered menu" pick, as opposed to a product did-you-mean's `suggest_offer`
        roster, which `test_r3_pending_end_to_end.py`'s own chain covers) - checked
        by grep before writing this, per the brief."""
        db = session_factory()
        c1 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HARDWARE SDN BHD")
        c2 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN HOMEMART SDN BHD")
        c3 = mc_customer(db, company_id=DEFAULT_COMPANY_ID, name="CHIN CHUN TRADING SDN BHD")
        db.commit()
        roster = [
            {
                "idx": 1, "label": "CHIN CHUN HARDWARE SDN BHD (MCH, SRT)", "uuid": c1.id,
                "product": None, "entity_type": "customer",
            },
            {
                "idx": 2, "label": "CHIN CHUN HOMEMART SDN BHD (SRT)", "uuid": c2.id,
                "product": None, "entity_type": "customer",
            },
            {
                "idx": 3, "label": "CHIN CHUN TRADING SDN BHD (SRT)", "uuid": c3.id,
                "product": None, "entity_type": "customer",
            },
        ]
        _seed_contact(
            session_factory,
            variables={
                "message_type": "business_query",
                "domain_hint": "order",
                "entities": [
                    {
                        "raw": PRODUCT_CODE_R21, "hint": "product", "uuid": PRODUCT_UUID_R21,
                        "canonical_code": PRODUCT_CODE_R21, "current_message": False,
                    },
                ],
                "selection_context": "disambiguation",
                "last_result_set": roster,
                "picker_last_result_set": roster,
                "picker_selection_context": "disambiguation",
                "picker_domain": "order",
                "pending": None,
            },
        )
        _result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf={**_all_pick_parser_output(), **do_window()},  # Dated: the pick-all is a DO list ask, which asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            text_body="all",
            msg_id="ZZT-outstanding-r21-guard-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured, "a plain ask's pick-all must still be answered"
        name, args = captured[0]
        assert name == "crm_order_management_orders_list", (
            f"a plain (non-outstanding) ask must keep today's behaviour unchanged: {name}"
        )
        assert set(args.get("customer_ids") or []) == {c1.id, c2.id, c3.id}, args


# --------------------------------------------------------------------------- #
# Owner round 9 (13 Sep 2026, live): with a single-scope detail offer open
# (`Reply 1 for the delivery order list.`), the owner typed `hi`, `hi`, `no`,
# `stop` - every one re-printed the offer. "wud i can't reset now?". Trace:
# `no` / `stop` parsed `message_type: "casual"`, `is_affirmative: false`,
# `reference_positions: []`, `entity_op: "reuse"`; `hi` / `hmm` parsed casual
# with `is_affirmative: null`. AC-1143(c)'s re-print (`_apply_outstanding_
# pending`'s arm 3) fires on EVERY non-answer with no exit except a new ask -
# `is_affirmative` is never read there at all today (measured: a decline and an
# unreadable turn take the exact same re-print arm).
#
# R22 (owner ruling, 13 Sep 2026):
# (a) A DECLINE (`is_affirmative: false`, no positions, no entity) under an open
#     `outstanding_detail` offer or `outstanding_scope` question CLOSES it:
#     pending dropped, `outstanding_filters` gone, nothing fetched, a non-empty
#     reply that is not the offer.
# (b) The re-print happens AT MOST ONCE per offer: a SECOND consecutive
#     unreadable turn (casual, `is_affirmative: null`, no positions, no entity,
#     no refinement) closes the offer the same way, and that turn gets its
#     normal reply. A pick or an R15 refinement still works after the first
#     re-print - unaffected by (b), since neither is "unreadable" at all.
# AC-1167 (decline closes it), AC-1168 (second unreadable turn closes it).
# --------------------------------------------------------------------------- #


class TestOpenOfferCanBeLeft:
    """AC-1168/R22(b) guard "a pick still works after one re-print" is NOT a new
    method here - `TestDetailOfferIsSticky::test_detail_offer_survives_a_casual_turn`
    already runs exactly that shape (a casual "thanks" under the open detail offer,
    then a "2" pick that must still resolve against it) and stays green through this
    round unmodified, per the brief's "name it instead of duplicating"."""

    def test_no_under_the_detail_offer_closes_it(self, session_factory, monkeypatch) -> None:
        """AC-1167/R22(a). Measured today: "no" re-prints the SAME offer text
        unchanged and leaves `pending`/`outstanding_filters` exactly as they were -
        `is_affirmative` is never read by the re-print arm at all."""
        _seed_open_outstanding_detail(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", is_affirmative=False, reference_positions=[],
                entities=[], domain_hint=None, entity_op="reuse",
            ),
            text_body="no",
            msg_id="ZZT-outstanding-r22-no-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"a decline fetches nothing - it closes the offer, it does not answer it: "
            f"{captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Sales order list" not in reply, reply
        assert "Delivery order list" not in reply, reply
        assert "Reply 1 for" not in reply, reply
        assert reply.strip() != "", "a decline must still get SOME acknowledgement"
        open_question = _session_of(session_factory).get("open_question")
        assert (open_question or {}).get("kind") != "outstanding_detail", (
            f"the offer must be closed, not left open: {open_question!r}"
        )
        assert not open_question, (
            f"the carried filter set (open_question.payload.filters) dies with the "
            f"closed offer: {open_question!r}"
        )

    def test_stop_under_the_scope_question_closes_it(self, session_factory, monkeypatch) -> None:
        """AC-1167/R22(a), the scope-question half. Same measured gap: "stop" today
        re-prints "Outstanding for which document?" unchanged."""
        _seed_open_outstanding_scope(session_factory)
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", is_affirmative=False, reference_positions=[],
                entities=[], domain_hint=None, entity_op="reuse",
            ),
            text_body="stop",
            msg_id="ZZT-outstanding-r22-stop-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured == [], (
            f"a decline fetches nothing, neither the report nor the plain order list: "
            f"{captured}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" not in reply, reply
        assert reply.strip() != "", "a decline must still get SOME acknowledgement"
        open_question = _session_of(session_factory).get("open_question")
        assert (open_question or {}).get("kind") != "outstanding_scope", (
            f"the question must be closed, not left open: {open_question!r}"
        )
        assert not open_question, (
            f"the carried filter set (open_question.payload.filters) dies with the "
            f"closed question: {open_question!r}"
        )

    @pytest.mark.xfail(strict=True, reason=_XFAIL_HMM_OVER_OPEN_OFFER_FIRES_A_FETCH)
    def test_a_second_unreadable_turn_closes_the_offer(self, session_factory, monkeypatch) -> None:
        """AC-1168/R22(b). Measured today: BOTH "hmm" and the follow-up "hi" print the
        identical re-armed offer, forever - there is no exit at all short of a new ask.
        The FIRST unreadable turn still re-prints (existing, unchanged behaviour); the
        SECOND one closes the offer instead, so the customer's own "hi" finally gets an
        ordinary reply rather than a third copy of a list they never asked to see
        again."""
        _seed_open_outstanding_detail(session_factory)
        result1, captured1 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying something else",
            ),
            text_body="hmm",
            msg_id="ZZT-outstanding-r22-second-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured1 == [], captured1
        reply1 = (result1.reply or {}).get("text") or ""
        assert "Sales order list" in reply1 and "Delivery order list" in reply1, (
            f"the FIRST unreadable turn must still re-print the offer, unchanged: {reply1!r}"
        )

        # The SECOND unreadable turn must close the offer AND complete in-process as
        # `low_signal` (the greeting), rather than delegate - `low_signal` has to be in
        # `chatbot_completed_lanes` for that (production carries it; the coder measured
        # it on the prod copy), and `_enable_business_lane`'s per-turn reset wipes any
        # lane list a test seeded before THIS call unless asked for again here, every
        # time. Clarifier stubbed the same two-line way `test_s4_casual_lane.py`'s own
        # `_install_stub_lane` does, so this reaches no LLM.
        from app.services.chatbot.lanes import casual as casual_mod

        monkeypatch.setattr(casual_mod, "resolve_clarifier_config", lambda db, **_: object())
        monkeypatch.setattr(
            casual_mod,
            "call_clarifier",
            lambda config, user_prompt: '{"response": "Hi! How can I help you today?"}',
        )
        result2, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying hi",
            ),
            text_body="hi",
            msg_id="ZZT-outstanding-r22-second-2",
            attributes=["sales_orders.outstanding"],
            extra_completed_lanes=["low_signal"],
        )
        assert captured2 == [], captured2
        assert result2.status == "done", result2.error
        reply2 = (result2.reply or {}).get("text") or ""
        assert "Sales order list" not in reply2, (
            f"a SECOND unreadable turn must close the offer, not print a third copy "
            f"of it: {reply2!r}"
        )
        assert "Delivery order list" not in reply2, reply2
        assert reply2 == "Hi! How can I help you today?", (
            f"the offer is gone, so this turn's OWN reply (the greeting) is what the "
            f"customer sees: {reply2!r}"
        )
        # SESSION-SHAPE PORT (AC-1592, tester 14, 16 Sep 2026): pending -> open_question
        # (a closed offer = no open_question at all); outstanding_filters only ever
        # lived inside the offer's own payload, never a sibling key, so a closed
        # open_question already covers "the filter set dies with it".
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_detail", (
            f"the offer must be closed after the second unreadable turn: {open_question!r}"
        )
        assert not open_question, (
            f"the closed offer's filter set dies with it: {open_question!r}"
        )

        result3, captured3 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[1],
            ),
            text_body="1",
            msg_id="ZZT-outstanding-r22-second-3",
            attributes=["sales_orders.outstanding"],
            matches={PRODUCT_CODE: {"uuid": PRODUCT_UUID, "entity_type": "product", "canonical_code": PRODUCT_CODE}},
            mcp_response=REPORT_HIT,
        )
        assert not (
            captured3 and captured3[0][0] == "crm_outstanding_report" and captured3[0][1].get("detail")
        ), (
            f"the offer is gone, so a bare '1' must not resolve as its detail pick "
            f"any more - whatever the generic order lane does with it is not this "
            f"test's concern: {captured3}"
        )
        _result3_unused = result3

    @pytest.mark.xfail(strict=True, reason=_XFAIL_HMM_OVER_OPEN_OFFER_FIRES_A_FETCH)
    def test_a_refinement_after_one_reprint_still_works(self, session_factory, monkeypatch) -> None:
        """AC-1168/R22(b) guard: "hmm" (the first, still-re-printing unreadable turn)
        followed by an R15 date refinement must still re-run the report with the new
        window - measured GREEN today, and must stay green once R22(b) lands (a
        refinement is answered, never counted as a second unreadable turn)."""
        _seed_open_outstanding_detail(session_factory)
        _result1, captured1 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], user_goal="saying something else",
            ),
            text_body="hmm",
            msg_id="ZZT-outstanding-r22-refine-1",
            attributes=["sales_orders.outstanding"],
        )
        assert captured1 == [], captured1

        result2, captured2 = _run_turn(
            session_factory,
            monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[], entity_op="reuse", broaden_axis="date",
                date_filter_start="2026-09-01", date_filter_end="2026-09-30",
                user_goal="trying to see this month only",
            ),
            text_body="i want to see this month only",
            msg_id="ZZT-outstanding-r22-refine-2",
            attributes=["sales_orders.outstanding"],
            mcp_response=REPORT_HIT,
        )
        assert captured2, "the refinement after one re-print must still run the report"
        name2, args2 = captured2[0]
        assert name2 == "crm_outstanding_report", (name2, args2)
        assert args2.get("order_date_from") == "2026-09-01", args2
        assert args2.get("order_date_to") == "2026-09-30", args2
        reply2 = (result2.reply or {}).get("text") or ""
        assert "detail" not in captured2[0][1], (
            f"a refinement re-runs the report, not a detail pick: {captured2[0][1]}"
        )
        _reply2_unused = reply2


# --------------------------------------------------------------------------- #
# Owner round 9b (13 Sep 2026, live): with the SRTWT7443 single-scope detail
# offer open, `delivery status for hanlim` RE-RAN the SRTWT7443 outstanding
# report with `Customer: all` and re-offered - the hanlim customer named in the
# very same message never reached the report at all. "I kind of can't escape
# this loop." Trace: `message_type: "business_query"`, `domain_hint: "order"`,
# `intent_hint: "check_order"`, `requested_attributes: ["delivery"]`,
# `order_status: null`, one customer entity, `entity_op: "replace_combine"`, no
# `reference_positions`. Compare the two REAL refinements this lane already
# has: `only BRW` and `i want to see this month only` both parsed
# `message_type: "casual"`, `domain_hint: null`.
#
# R24 (owner ruling, 13 Sep 2026, CORRECTS R15's refinement test): a turn the
# parser classifies as a business question of ITS OWN (`message_type:
# "business_query"` with a NON-NULL `domain_hint`) is a NEW ASK under an open
# `outstanding_detail` offer or `outstanding_scope` question, WHATEVER its
# entities' axes - `_outstanding_keeps_subject`'s axis test (R15) fired here
# because "customer" differs from the stored subject's "product" axis, and
# called a plain delivery enquiry a refinement of the OLD product's report. The
# pending is dropped (`outstanding_pending_dropped`), its filters go with it,
# and the turn runs its own path - here, a plain delivery enquiry for hanlim,
# `crm_order_management_orders_list`, no product carried, no `so_outstanding`
# stamped. A refinement stays the CASUAL-shaped turn (`message_type: "casual"`,
# `domain_hint: null`) that keeps the subject, exactly as R15 already defines
# it otherwise. Picks are unchanged. AC-1170 (a correction to AC-1157's own
# refinement definition).
# --------------------------------------------------------------------------- #


def _r24_business_query_qf() -> dict[str, Any]:
    """The trace's own parser output, verbatim: a business question of its own
    (`domain_hint: "order"`), never a refinement's casual shape."""
    return _parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        requested_attributes=["delivery"], order_status=None,
        # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
        **do_window(),
        entities=[
            {
                "raw": "hanlim", "hint": "customer", "canonical_code": None,
                "current_message": True, "confident": True,
            },
        ],
        entity_op="replace_combine", reference_positions=[],
    )


class TestABusinessQueryUnderAnOpenOfferIsANewAsk:
    """Guards named rather than duplicated, per the brief - all three already pin the
    shape R24 must leave unaffected, and all three are green today:

    * `TestDateNarrowingUnderAnOpenOffer::
      test_a_location_only_turn_under_the_detail_offer_narrows_by_location` - "only
      BRW" is `message_type: "business_query"` too, but `domain_hint: None`, so R24's
      new-ask test (which requires a NON-NULL `domain_hint`) does not fire and the
      turn stays a refinement.
    * `TestDateNarrowingUnderAnOpenOffer::
      test_a_date_only_turn_under_the_scope_question_reasks_with_the_new_window` -
      "i want to see this month only" is `message_type: "casual"`, `domain_hint:
      None`; unaffected for the same reason.
    * `TestCustomerOnlyOutstandingAskReachesTheReport::
      test_bare_outstanding_customer_only_arms_the_scope_question` - a genuinely NEW
      outstanding ask ("outstanding report for hanlim", `business_query` +
      `domain_hint: "order"` + `order_status: "outstanding"`, no open offer at all)
      still arms the scope question for hanlim with no carried product/location -
      the S4/R13 new-ask-arming path R24 does not touch.
    """

    def test_delivery_status_for_a_customer_under_a_product_offer_is_a_new_ask(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_detail(
            session_factory, rows=[{"idx": 1, "label": "Sales order list", "value": "so"}],
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_r24_business_query_qf(),
            text_body="delivery status for hanlim",
            msg_id="ZZT-outstanding-r24-detail-1",
            attributes=["sales_orders.outstanding"],
            matches={
                "hanlim": {
                    "uuid": CUSTOMER_ONLY_UUID, "entity_type": "customer",
                    "canonical_code": CUSTOMER_ONLY_NAME,
                },
            },
        )
        assert captured, "the new ask must still be answered, not dropped on the floor"
        name, args = captured[0]
        assert name == "crm_order_management_orders_list", (
            f"a plain delivery enquiry must run the plain order lane, never the "
            f"OLD product's outstanding report: {name}"
        )
        assert args.get("customer_ids") == [CUSTOMER_ONLY_UUID], (
            f"the customer named THIS turn must reach the tool: {args}"
        )
        assert args.get("product_code") != PRODUCT_CODE, (
            f"the offer's OLD product must not be carried into an unrelated new ask: {args}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Sales order outstanding" not in reply, reply
        assert "Reply 1 for" not in reply, reply
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_detail", (
            f"the old offer must be dropped, not answered by an unrelated turn: "
            f"{open_question!r}"
        )
        # AC-1592 test triage (queue item 2c, 16 Sep 2026): the old blanket "no
        # outstanding_filters key at all" check over-translates to "no open_question
        # of ANY kind" - a NEW ask is free to arm its own (measured: this one arms an
        # unrelated `team_pick` escalate offer, nothing to do with the dropped
        # outstanding product). The narrower, still-faithful claim is that if a
        # filter set of the OLD shape survived at all, it does not carry the old
        # offer's product.
        assert open_question.get("payload", {}).get("filters", {}).get("product_code") != PRODUCT_CODE, (
            f"the old offer's product must not survive in a new ask's own filters: "
            f"{open_question!r}"
        )

    def test_a_business_query_under_the_scope_question_is_a_new_ask(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_open_outstanding_scope(
            session_factory,
            filters={
                "product_code": PRODUCT_CODE,
                "date_filter_start": None,
                "date_filter_end": None,
                "customer_ids": [],
                "warehouse_codes": [],
                "location_token": None,
            },
        )
        result, captured = _run_turn(
            session_factory,
            monkeypatch,
            qf=_r24_business_query_qf(),
            text_body="delivery status for hanlim",
            msg_id="ZZT-outstanding-r24-scope-1",
            attributes=["sales_orders.outstanding"],
            matches={
                "hanlim": {
                    "uuid": CUSTOMER_ONLY_UUID, "entity_type": "customer",
                    "canonical_code": CUSTOMER_ONLY_NAME,
                },
            },
        )
        assert captured, "the new ask must still be answered"
        name, args = captured[0]
        assert name != "crm_outstanding_report", (
            f"a plain delivery enquiry must not be answered as the OLD product's "
            f"outstanding report: {name}"
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Outstanding for which document?" not in reply, (
            f"the old scope question must be dropped, not re-asked: {reply!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") != "outstanding_scope", open_question
        # AC-1592 test triage (queue item 2c, 16 Sep 2026): same narrowing as the
        # sibling test above - a new ask is free to arm its own open_question, the
        # faithful claim is only that the OLD offer's product does not survive in it.
        assert open_question.get("payload", {}).get("filters", {}).get("product_code") != PRODUCT_CODE, (
            f"the old question's product must not survive in a new ask's own "
            f"filters: {open_question!r}"
        )
