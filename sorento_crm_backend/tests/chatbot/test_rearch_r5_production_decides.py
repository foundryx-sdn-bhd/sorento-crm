"""R5 RED tests, engine level - "production decides" (PLAN-chatbot-answer-half-reattach.md
slice R5, UAC AC-1690, AC-1694 to AC-1696, AC-1699, AC-1701 to AC-1705). Written BEFORE the
coder's R5 pass, from the captain's brief and tester 31's live parity scoreboard
(`.claude/handoffs/20260920T012240Z-chatbot-rearch-tester31-parity-corpus-done.md`) and
coder 28's own R4 report (`.claude/handoffs/20260920T083000Z-rearch-coder28-r4-report.md`).

The rule this slice implements (PLAN "Design"): for a SINGLE-domain business turn, the
resolver EXIT is the only decider of what the reply says -
  * exit `offer` -> bridge offer (the gate's own annotated picker)
  * exit `access_ask` or fetch arm `tier-ask` -> bridge access-level picker
  * `not_found`, OR a fetch that ran and returned ZERO rows -> bridge miss
  * `continue` + rows -> bridge HIT (scope block + production intro)
`turn/narrow.py::decide` stops raising roster asks for those turns (it keeps
`not_applicable`, `list_all`, `optional_filter`, `narrow_by_type`).

**Test shape, throughout**: every test below drives `engine.run_turn` (via this file's own
`_run_turn_real`/`_run_turn_with_mcp_call`, both built on
`tests.chatbot.test_outstanding_lane._run_turn`'s `real_resolver=True` wiring, plus a safe
probe/access_types stub - see `_real_resolve_with_safe_probe`) with the REAL
resolver/gate/narrower over seeded Postgres rows - only the MCP tool boundary
(`FetchServices.mcp_call`) is a double. No test calls `answer_bridge.answer_for` directly or
hand-builds `payload["_exit_kind"]`: coder 28's own report names exactly this class of
false-green (a unit test that bypasses `engine.py`'s precedence never catches a precedence
bug). Every "expected" reply string not already produced by a live production function is
computed either by CALLING the real function that same turn produced it with (a spied
call-through on `lanes.business.fetch.output_structurer` / `turn_runtime.resolve_kinds`), or
by `_expected_scope_block`, a direct, cited port of origin/main's
`tail/compile_state.py::_search_scope_header` (deleted on this branch by the S3 turn
re-architecture, `129c403c7`) restricted to the two "always" axes (Customer, Product) plus
the Dates line - the three lines every scenario here actually exercises; the other four axes
(Order/Transporter/Container/Warehouse) never fire because no entity of those kinds is named
in any scenario below.

Postgres only (`session_factory`, blank schema). Every row seeded fresh; nothing borrowed.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.models.base import set_company_scope
from app.services.chatbot import copy as copy_mod
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business import gate as gate_mod
from app.services.chatbot.lanes.business import miss_suggest as miss_mod
from app.services.chatbot.lanes.business import resolve_gate
from app.services.chatbot.lanes.business.services import AnswerServices, ResolveGateServices
from app.services.chatbot.turn import narrow as narrow_mod
from app.services.chatbot.turn.state import Focus, Profile
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import customer as seed_customer
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import (
    _present_response,
    _run_turn,
    _seed_contact as _seed_business_contact,
    _session_of,
)
from tests.chatbot.test_product_attachment_picker_stamp import (
    _seed_attachment_type,
    _seed_file_for,
)


def _seed_contact_and_get(session_factory) -> None:
    """The ONE contact/workspace shape `_run_turn_real(...)` needs:
    `respond_contacts` bound to `DEFAULT_COMPANY_ID` via a workspace at space_id
    "364817" (`_run_turn` hardcodes `default_space_id -> "364817"`)."""
    _seed_business_contact(session_factory, variables={})


def _grant_contact_company(session_factory, company_id: Any) -> None:
    """A SECOND `respond_contact_companies` grant, additive to `_seed_contact_and_get`'s
    own Sorento-only one (`test_outstanding_lane.py::_seed_contact`'s own comment: "bound
    to Sorento so a REAL company-scoped DB read ... sees the SAME company"). A contact
    with no grant into a company resolves to ZERO rows there regardless of any `gate.py`/
    resolver fix (`company_scope_resolver.py`'s own fail-closed rule, AC-F3) - measured
    directly writing `TestCustomerOptionCarriesFamily`: the seeded Mocha customer row was
    invisible to the real resolver with only the default Sorento grant in place, a second,
    independent gap from the `compatible_entities`/`uuids` question the test's own
    assertions pin (coder 29's report, item 4's "Separately" note)."""
    from sqlalchemy import text as _sql_text

    db = session_factory()
    db.execute(
        _sql_text(
            "INSERT INTO respond_contact_companies (id, respond_contact_id, company_id) "
            "SELECT gen_random_uuid(), id, :company_id FROM respond_contacts WHERE respond_io_id = :cid "
            "ON CONFLICT DO NOTHING"
        ),
        {"cid": str(CONTACT_ID), "company_id": company_id},
    )
    db.commit()
    db.close()


def _order_envelope_json(order_rows: list[dict[str, Any]]) -> str:
    """`crm_order_management_orders_list`'s route body, rendered through the REAL MCP
    presenter (`sorento_crm_mcp.presenters.present_response`) - the same "production
    path, not a shortcut" convention `test_outstanding_lane.py::_capturing_mcp` uses for
    `crm_outstanding_report`/`crm_sales_report`. Fed to `_run_turn` as a STRING (its own
    `isinstance(response, str): return response` branch), because `_run_turn`'s generic
    dict branch is `json.dumps(response)` VERBATIM with no presenter pass for any tool
    name it does not special-case - `crm_order_management_orders_list` is not one of
    them, so a raw `{"data": [...]}` dict handed to `_run_turn` never reaches
    `_orders_list`'s own row builder and reads back as the tool's own "no items key"
    empty envelope instead (measured directly, see this file's own commit history)."""
    return _present_response()("crm_order_management_orders_list", json.dumps({"data": order_rows}))


def _mcp_double(*, other: Any):
    """A fake `mcp_call(name, args)` for scenarios that need MORE than one distinct
    tool's own answer in the SAME turn (the incoming/stock ladder) - `_run_turn`'s own
    built-in double only ever answers one canned `response` regardless of tool name.
    `other(name, args) -> raw JSON string` decides every call."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _call(name: str, args: dict[str, Any]) -> Any:
        captured.append((name, dict(args)))
        return other(name, args)

    return _call, captured


def _mcp_probe_for(tool_rows: dict[str, list[dict[str, Any]]]):
    """An `AnswerServices.mcp_probe(name, args)` double keyed by TOOL NAME, each answer
    rendered through the REAL MCP presenter (`_present_response`, the same "production
    path, not a shortcut" convention `_order_envelope_json` uses) and PARSED back to a
    dict - `AnswerServices.mcp_probe`'s production binding (`services.py::_mcp_probe`)
    already returns a parsed envelope (its own inner `call` runs `parse_mcp_content`), so
    a caller of this bundle (`miss_suggest.run_miss_lane`, `answer.run_crossdomain`) never
    parses a second time. A tool name with no entry answers the harmless `{"data": []}`
    both callers' own `dym_ok`/`crossdomain_render` already degrade cleanly from."""

    def _probe(name: str, args: dict[str, Any]) -> Any:
        rows = tool_rows.get(name)
        if rows is None:
            return {"data": []}
        return json.loads(_present_response()(name, json.dumps({"data": rows})))

    return _probe


def _real_resolve_with_safe_probe(monkeypatch) -> None:
    """`production_services` wired to the REAL `resolve_entity` (bound per-db, the same
    binding `business_services._resolve_entity(db)` builds -
    `test_engine_company_scope._real_resolve_entity`) with `probe` and `access_types`
    stubbed SAFE rather than left real.

    `test_outstanding_lane._run_turn_real(real_resolver=True)` leaves `production_services`
    entirely untouched (real resolve_entity AND real probe AND real access_types), which
    is fine for a turn with no ambiguous-customer picker; it is NOT fine for one that
    has one - `resolve_gate.run`'s own customer-picker arm calls `services.probe(...)`
    for the "has DO" stamp, which reaches the real `MCPRuntimeClient.call_tool` and trips
    `tests/chatbot/conftest.py`'s "never call the real MCP server" guard (measured
    directly writing this file's `TestScopeBlockAfterPick`/`TestCustomerOptionCarriesFamily`
    tests). `probe=lambda **_: None` is the same "no probe available" stub several
    existing engine-level tests already use (`test_rearch_r3_bridge_engine.py::
    _one_tier_resolve_services`)."""
    from app.services.chatbot import engine as engine_mod
    from tests.chatbot.test_engine_company_scope import _real_resolve_entity

    def _bundle(db, *, space_id: str | None = None) -> ResolveGateServices:
        return ResolveGateServices(
            access_types=lambda **_: [],
            resolve_entity=_real_resolve_entity(db),
            probe=lambda **_: None,
        )

    monkeypatch.setattr(engine_mod.business_services, "production_services", _bundle)


def _run_turn_with_mcp_call(
    session_factory, monkeypatch, *, qf, text_body, msg_id, mcp_call, answer_mcp_probe=None
):
    """The SAME real-resolver wiring `test_outstanding_lane.py::_run_turn_real(real_resolver=
    True)` does, PLUS a safe probe/access_types stub (`_real_resolve_with_safe_probe`),
    with a caller-supplied `mcp_call(name, args)` double instead of its own
    single-canned-response one - needed where a turn's ladder calls MORE than one tool
    (the incoming-miss-falls-back-to-stock scenario), which `_run_turn`'s own
    `_capturing_mcp` cannot answer differently per tool name, or where the turn raises
    an ambiguous-customer picker (see `_real_resolve_with_safe_probe`'s own docstring).

    `answer_mcp_probe(name, args) -> dict`, optional: the SECOND MCP seam a business
    turn can reach - `AnswerServices.mcp_probe`, which `miss_suggest.run_miss_lane`'s
    did-you-mean/require-specific stamp probe and `answer.run_crossdomain`'s ladder
    probe both call through `answer_bridge.answer_for`'s own `services=business_services.
    production_answer_services(db)` (`engine.py:1918` - the ONLY live call site;
    `answer_services_for` has NO live caller anywhere under `app/`, measured via `grep -rn
    "answer_services_for(" app/` excluding its own def, tester 33). Stubbing
    `answer_services_for` alone (as this function did before tester 33) leaves
    `production_answer_services` real, so a probe-dependent turn reaches the actual
    `MCPRuntimeClient` and trips `conftest.py`'s "never call the real MCP server" guard -
    caught by both callers' own broad `except Exception`, so the turn silently degrades
    to "probe did not run" rather than failing loud (measured directly: `TestRequireSpecificRosterCopy`'s
    and `TestIncomingMissLadderProduct`'s own reds, both from this exact gap). Defaults to
    a harmless `{"data": []}` for every OTHER test in this file, which resolves to the SAME
    "probe not attempted" branch `dym_annotate`/`run_crossdomain` already degrade to on the
    forbidden-call path today - no behaviour change for a test that does not pass one."""
    from app.services.chatbot.head import parser as parser_mod
    from app.services.chatbot.lanes.business.services import AnswerServices, FetchServices
    from tests.chatbot.test_outstanding_lane import _enable_business_lane
    from tests.chatbot.test_engine import _envelope

    _enable_business_lane(session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": [], "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
    _real_resolve_with_safe_probe(monkeypatch)
    monkeypatch.setattr(
        engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=mcp_call)
    )
    probe_fn = answer_mcp_probe if answer_mcp_probe is not None else (lambda name, args: {"data": []})
    monkeypatch.setattr(
        engine_mod.business_services,
        "answer_services_for",
        lambda session_factory: AnswerServices(
            mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
        ),
    )
    monkeypatch.setattr(
        engine_mod.business_services,
        "production_answer_services",
        lambda db: AnswerServices(mcp_probe=probe_fn, family_fetch=lambda query: {"data": []}),
    )
    envelope = _envelope()
    envelope.message["message"]["messageId"] = msg_id
    envelope.message["message"]["message"]["text"] = text_body
    return engine_mod.run_turn(envelope, session_factory=session_factory)


def _run_turn_real(
    session_factory, monkeypatch, *, qf, text_body, msg_id, mcp_response, answer_mcp_probe=None
):
    """`_run_turn_with_mcp_call` with a single canned `mcp_response` for every tool
    call this turn makes (a STRING, already rendered through the real MCP presenter
    where relevant - see `_order_envelope_json`), the common case."""
    from tests.chatbot.test_outstanding_lane import _capturing_mcp

    mcp_call, captured = _capturing_mcp(mcp_response)
    result = _run_turn_with_mcp_call(
        session_factory, monkeypatch, qf=qf, text_body=text_body, msg_id=msg_id, mcp_call=mcp_call,
        answer_mcp_probe=answer_mcp_probe,
    )
    return result, captured


def _family_base(stem: str) -> str:
    """A unique product-code PREFIX with no separators of its own -
    `tests._pg_fixture.unique_code` always inserts a hyphen (`ZZT-<stem>-<hash>`), and a
    hyphenated base fed into an ambiguous-family scenario (a token meant to prefix-match
    several `<base><suffix>` codes) tripped `gate.py`'s own typed-token relevance filter
    in a way a plain code never does (measured directly while writing this file:
    `gate.run_gate` returned `require_specific: False` for a hyphenated base and
    `require_specific: True`, the correct roster, for the identical scenario with a
    hyphen-free one) - a fixture artifact of this file's own code generation, not a
    product bug, so avoided here rather than pinned as another red."""
    return unique_code(stem).replace("-", "")


def _order_row(*, order_number: str, debtor_name: str, product_code: str, order_date: str = "2026-01-15") -> dict[str, Any]:
    return {
        "order_number": order_number,
        "debtor_name": debtor_name,
        "order_date": order_date,
        "order_status": None,
        "lines": [{"product": {"product_code": product_code}, "quantity": 1}],
    }


# --------------------------------------------------------------------------- #
# The scope-block port (Customer / Product / Dates lines only - see module
# docstring). Cited line-for-line against origin/main's own algorithm; values are
# always read off the REAL objects the turn under test produced, never typed by hand.
# --------------------------------------------------------------------------- #


def _norm_tok(value: Any) -> str:
    return re.sub(r"[-\s]+", "", str(value or "")).strip().lower()


def _raw_of_tok(qf: dict[str, Any], token: Any) -> str:
    key = _norm_tok(token)
    for ent in qf.get("entities") or []:
        if _norm_tok(ent.get("raw")) == key or _norm_tok(ent.get("canonical_code")) == key:
            return str(ent.get("raw") or token)
    return str(token)


def _format_date(value: Any) -> str | None:
    if not value:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value))
    if not m:
        return str(value)
    y, mo, d = m.groups()
    return f"{d}/{mo}/{y}"


def _axis_words(
    gate_json: dict[str, Any], resolver_json: dict[str, Any], qf: dict[str, Any], *, types: set[str]
) -> str | None:
    gate_entities = gate_json.get("compatible_entities") or []
    rows = [e for e in gate_entities if str(e.get("entity_type")) in types]
    if not rows:
        return None
    words: list[str] = []
    for res in resolver_json.get("resolutions") or []:
        hits = any(str(m.get("entity_type")) in types for m in (res.get("matches") or []))
        token = str(res.get("token") or "").strip()
        if hits and token:
            raw = _raw_of_tok(qf, token)
            if raw not in words:
                words.append(raw)
    if not words:
        for e in qf.get("entities") or []:
            hint = str(e.get("hint") or "")
            if hint in types or (hint == "customer" and "customer" in types) or (hint == "product" and "product" in types):
                v = str(e.get("raw") or "").strip()
                if v and v not in words:
                    words.append(v)
    if not words:
        for row in rows:
            v = str(row.get("code") or row.get("title") or "").strip()
            if v and v not in words:
                words.append(v)
    return ", ".join(words) if words else None


def _expected_scope_block(qf: dict[str, Any], gate_json: dict[str, Any], resolver_json: dict[str, Any]) -> str:
    """Direct port of origin/main `tail/compile_state.py::_search_scope_header`'s
    Customer/Product axes (both `always: True`) plus the Dates line - see module
    docstring for why the other four axes are omitted."""
    start = qf.get("date_filter_start")
    end = qf.get("date_filter_end")
    if not start and not end:
        dates = "all dates"
    elif start and end and start == end:
        dates = _format_date(start) or "all dates"
    else:
        dates = f"{_format_date(start) if start else 'earliest'} to {_format_date(end) if end else 'today'}"
    customer_words = _axis_words(gate_json, resolver_json, qf, types={"customer"})
    product_words = _axis_words(gate_json, resolver_json, qf, types={"product"})
    return "\n".join(
        [
            f"Customer: {customer_words or 'all customers'}",
            f"Product: {product_words or 'all products'}",
            f"Dates: {dates}",
        ]
    )


def _spy_resolve_payload(monkeypatch) -> list[dict[str, Any]]:
    """Call-through spy on `turn_runtime.resolve_kinds`, recording each call's
    `ResolveOutcome.payload` (the resolver's raw `{"resolved", "gate", ...}` dict,
    `resolve_gate.run`'s own `fields`) - the SAME objects `_expected_scope_block` needs,
    read back rather than re-derived."""
    from app.services.chatbot import turn_runtime as turn_runtime_mod

    real = turn_runtime_mod.resolve_kinds
    calls: list[dict[str, Any]] = []

    def _spy(db, **kwargs):
        result = real(db, **kwargs)
        if result.payload is not None:
            calls.append(result.payload)
        return result

    monkeypatch.setattr(engine_mod.turn_runtime, "resolve_kinds", _spy)
    return calls


def _spy_output_structurer(monkeypatch) -> list[dict[str, Any]]:
    """Call-through spy on `lanes.business.fetch.output_structurer` - the REAL, live
    hit-composer `turn_runtime.make_tool_runner` reaches via `lanes.business.run_fetch`
    (confirmed: `lanes/business/__init__.py:1397`). Records each call's own return
    value, so a test can assert against production's OWN numbered-row text without
    retyping it."""
    real = fetch_mod.output_structurer
    calls: list[dict[str, Any]] = []

    def _spy(result, ctx):
        out = real(result, ctx)
        calls.append(out)
        return out

    monkeypatch.setattr(fetch_mod, "output_structurer", _spy)
    return calls


# --------------------------------------------------------------------------- #
# Item 1 - AC-1696 (F1/F5): a plain order HIT opens with the scope block, then
# production's own "Here are the orders I found." intro and numbered rows.
# --------------------------------------------------------------------------- #


class TestHitScopeBlock:
    @pytest.mark.parametrize(
        "date_filter_start, date_filter_end",
        [
            pytest.param(None, None, id="unfiltered-all-dates"),
            pytest.param("2026-10-01", "2026-10-31", id="dated-window"),
        ],
    )
    def test_order_hit_opens_with_the_scope_block(
        self, session_factory, monkeypatch, date_filter_start, date_filter_end
    ) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTORD")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            entities=[
                {
                    "raw": code, "hint": "product", "canonical_code": None,
                    "current_message": True, "confident": True,
                }
            ],
            date_filter_start=date_filter_start,
            date_filter_end=date_filter_end,
        )
        payload_calls = _spy_resolve_payload(monkeypatch)
        structurer_calls = _spy_output_structurer(monkeypatch)
        row = _order_row(order_number="ZZT-ORD-1", debtor_name="ZZT PEARL BATHROOM SDN BHD", product_code=code)

        result, captured2 = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"delivery for {code}",
            msg_id=f"zzt-r5-hit-scope-{date_filter_start}", 
            mcp_response=_order_envelope_json([row]),
        )
        if not date_filter_start and not date_filter_end:
            # A dateless DO list ask asks which period first and fetches nothing, so there is
            # no hit to open with a scope block (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            assert captured2 == [], captured2
            assert "Which period" in ((result.reply or {}).get("text") or ""), result.reply
            return
        assert captured2 and captured2[0][0] == "crm_order_management_orders_list", captured2
        assert payload_calls, "resolve_kinds must have run for this turn"
        assert structurer_calls, (
            "lanes.business.fetch.output_structurer must have composed the hit fragment "
            "(the live hit path this slice wraps, per lanes/business/__init__.py:1397)"
        )
        payload = payload_calls[-1]
        expected_scope = _expected_scope_block(qf, payload["gate"], payload["resolved"])
        expected_fragment = structurer_calls[-1].get("response") or ""
        assert "Here are the orders I found." in expected_fragment, (
            "test setup sanity: the production hit fragment must carry its own intro"
        )
        expected_text = f"{expected_scope}\n\n{expected_fragment}"

        reply = (result.reply or {}).get("text") or ""
        assert reply == expected_text, (
            "AC-1696: a plain order hit must open with the Customer/Product/Dates scope "
            f"block ahead of production's own intro+rows.\n--- actual ---\n{reply!r}\n"
            f"--- expected ---\n{expected_text!r}"
        )


# --------------------------------------------------------------------------- #
# Item 2 - AC-1694/AC-1695 (F2): an ambiguous-customer pick answers with the HIT
# scope block naming the PICKED customer + the ORIGINAL product token, never
# "No matching results found."
# --------------------------------------------------------------------------- #


class TestScopeBlockAfterPick:
    def test_customer_pick_then_hit_carries_the_picked_customer_and_original_product(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTPIK")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        db = session_factory()
        cust1 = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT CHIN CHUN HARDWARE SDN BHD")
        cust2 = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT CHIN CHUN HOMEMART SDN BHD")
        db.commit()

        qf1 = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            # OR-mode: each named token resolves per its OWN candidates
            # (`resolve_gate.resolve_entity_body`'s `resolutions[]` shape) - AND-mode
            # (the parser's default) instead tries to find ONE row every token can
            # jointly describe, and a customer-name token beside a product-code token
            # never intersects on anything, so the customer half is silently dropped
            # (measured directly against `resolve_gate.resolve_entity_body` +
            # `_real_resolve_entity` before writing this test).
            match_mode="or",
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            entities=[
                {"raw": "zzt chin chun", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ],
        )
        row = _order_row(order_number="ZZT-ORD-PICK-1", debtor_name="ZZT CHIN CHUN HARDWARE SDN BHD", product_code=code)
        result1, captured1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"delivery for zzt chin chun {code}",
            msg_id="zzt-r5-pick-1", mcp_response=_order_envelope_json([row]),
        )
        reply1 = (result1.reply or {}).get("text") or ""
        assert "Please choose" in reply1 or "choose" in reply1.lower(), (
            f"test setup sanity: two same-family-token, different-base customers must "
            f"raise the ambiguous-customer picker: {reply1!r}"
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") in ("customer_pick", "customer"), open_question
        # Tester 38, 20 Sep 2026: HARDWARE and HOMEMART are equally similar to the typed
        # "zzt chin chun" token and the gate applies no stable tiebreak between them
        # (coder 32 measured 2 failures in 5 identical runs with a hardcoded position 1 -
        # see the handoff), so roster order is not guaranteed. Read the roster the engine
        # actually returned and answer the position whose OWN label names the intended
        # customer, rather than assuming it landed at position 1.
        options = open_question.get("options") or []
        hardware_option = next(
            (o for o in options if "HARDWARE" in str(o.get("label") or "").upper()), None
        )
        assert hardware_option is not None, (
            f"test setup sanity: the seeded HARDWARE customer must appear in the roster: "
            f"{options!r}"
        )
        hardware_position = hardware_option.get("position")
        assert hardware_position is not None, hardware_option

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[hardware_position],
        )
        result2, captured2 = _run_turn_real(
            session_factory, monkeypatch, qf=qf2, text_body=str(hardware_position),
            msg_id="zzt-r5-pick-2", mcp_response=_order_envelope_json([row]),
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert "No matching results found" not in reply2, (
            f"AC-1695: the family's own order genuinely exists - never a bare miss: {reply2!r}"
        )
        assert "Here are the orders I found." in reply2, reply2
        assert code in reply2, (
            f"AC-1695: the ORIGINAL product token must still scope the post-pick reply: {reply2!r}"
        )
        # AC-1695's own literal example ("Customer: CHIN CHUN HARDWARE SDN BHD (MCH,
        # SRT)" / "Product: srtwc286" / "Dates: all dates") - the scope-block HEADER
        # LINES, not merely the customer's name and product code appearing anywhere in
        # the reply (which the numbered order ROW itself already carries via
        # `_orders_list`'s own "*Customer:*"/"*Products:*" fields - a substring check
        # alone would pass today for the wrong reason).
        assert f"Customer: ZZT CHIN CHUN HARDWARE SDN BHD" in reply2, (
            f"AC-1695: the scope block's own Customer line must name the PICKED "
            f"customer (not the numbered row's own '*Customer:*' field): {reply2!r}"
        )
        assert f"Product: {code}" in reply2, (
            f"AC-1695: the scope block's own Product line must carry the ORIGINAL "
            f"product token: {reply2!r}"
        )
        assert "Dates: 01/09/2026 to 30/09/2026" in reply2, reply2


# --------------------------------------------------------------------------- #
# Item 3 - AC-1699/AC-1702 (F4/F7): a resolver that resolved cleanly, whose fetch
# then genuinely runs and returns ZERO rows, is a MISS (the rich text), never the
# bare "No matching results found."
# --------------------------------------------------------------------------- #


class TestFetchedEmptyIsAMiss:
    def test_dated_order_fetch_with_zero_rows_is_the_rich_miss(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTMISS")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
            date_filter_start="2026-10-01",
            date_filter_end="2026-10-31",
        )
        result, captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"delivery for {code} in October",
            msg_id="zzt-r5-fetched-empty-order", 
            mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply != "No matching results found.", (
            f"AC-1699: a genuinely-resolved subject whose fetch ran and found zero rows "
            f"is the RICH miss (scope block, 'Here's what you want:', the date-window "
            f"sentence, the CS member picker) - never the bare generic miss: {reply!r}"
        )
        assert "Here's what you want:" in reply, reply
        assert "Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct)" in reply, reply

    def test_attachment_fetch_with_zero_rows_is_the_rich_miss(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTATTM")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        _seed_attachment_type(session_factory, "Product Photos")
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                {
                    "raw": "product photos", "hint": "attachment_type", "canonical_code": "photo",
                    "current_message": True, "confident": True,
                },
            ],
            # `not_found_error_message`/`run_miss_lane` read `parser.routing.
            # suggested_team` VERBATIM, no domain/policy fallback of their own (measured:
            # `answer.build_suggest_offer`'s own team pick is `company_team or
            # routing.suggested_team or "customer_service"`) - a real LLM parser emission
            # for a `product_attachment` ask carries "marketing_product" here
            # (`test_product_attachment_picker_stamp.py::_parser`'s own literal, the SAME
            # domain), which this fixture omitted, defaulting to the base's own
            # `suggested_team: None` and stamping the generic "customer service team"
            # regardless of domain. `suggested_agent`/`team_source` stay the base's own
            # `None` - only the ONE field this test's own assertion needs changes.
            routing={"suggested_team": "marketing_product", "suggested_agent": None, "team_source": None},
        )
        result, captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"photo for {code}",
            msg_id="zzt-r5-fetched-empty-attachment",
            mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply != "No matching results found.", (
            f"AC-1702: a resolved product with zero matching files is the rich breakdown "
            f"miss, never the bare generic one: {reply!r}"
        )
        assert "Here's what you want:" in reply, reply
        assert "Would you like me to escalate to marketing product team?" in reply, reply


# --------------------------------------------------------------------------- #
# Item 4 - AC-1705 (F9): incoming miss + the stock ladder, scoped to ONE product,
# through the bridge (never `_climb`'s own raw presenter intro).
# --------------------------------------------------------------------------- #


class TestIncomingMissLadderProduct:
    def test_incoming_miss_falls_back_to_stock_for_that_product_only(self, session_factory, monkeypatch) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTINC")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        qf = _parser_output(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )

        def _other(name: str, args: dict[str, Any]) -> str:
            # `crm_incoming_stock_list` (the primary, must MISS) also contains the word
            # "stock" - matched on the exact rung tool name only
            # (`crm_inventory_stock_balance_list`, measured live via a throwaway probe
            # while writing this test), never a loose substring.
            if name == "crm_inventory_stock_balance_list":
                return _present_response()(
                    name,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "product_code": code, "total_qty": 42, "outstanding_qty": 0,
                                    "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 42}],
                                }
                            ]
                        }
                    ),
                )
            return json.dumps({"has_result": False, "items": []})

        mcp_call, captured2 = _mcp_double(other=_other)
        # The bridge's own AC-1705 ladder (`answer.run_crossdomain`) reaches for the SAME
        # stock row through the SECOND MCP seam (`AnswerServices.mcp_probe`), never
        # through the primary `FetchServices.mcp_call` double `_other` above answers -
        # `answer_bridge._fold_crossdomain_ladder` is called with `services=
        # business_services.production_answer_services(db)` (`engine.py:1918`), which
        # left real crashes into the "never call the real MCP server" guard before this
        # fix (measured directly writing this file).
        answer_probe = _mcp_probe_for(
            {
                "crm_inventory_stock_balance_list": [
                    {
                        "product_code": code, "total_qty": 42, "outstanding_qty": 0,
                        "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 42}],
                    }
                ]
            }
        )
        result = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf, text_body=f"incoming for {code}",
            msg_id="zzt-r5-incoming-ladder", mcp_call=mcp_call, answer_mcp_probe=answer_probe,
        )
        reply = (result.reply or {}).get("text") or ""
        assert "But no incoming matched these." in reply, reply
        assert "But here are the stock details for the requested products:" in reply, reply
        assert code in reply, reply
        # Measured live writing this test: today `turn/fetch.py::_climb`'s OWN rung
        # genuinely answers this turn (its own raw MCP-presenter intro, "Stock details
        # found for the requested products.", glued under the primary's own generic
        # "Here are the results." intro) - never the bridge's AC-1705 ladder sentence.
        assert "Stock details found for the requested products." not in reply, (
            "the OLD turn/fetch.py::_climb rung composer (the stock tool's own raw MCP "
            f"presenter intro) must never answer this turn - only the bridge's own "
            f"AC-1705 ladder sentence: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Item 6 - AC-1690: a family token in a domain OUTSIDE gate.REQUIRE_SPECIFIC_DOMAINS
# is a SILENT PREFIX FILTER - `turn/narrow.py::decide` never mints a roster for it,
# whatever the domain's own (today still roster-shaped) policy value says.
#
# `purchase_order` and `spo_allocation` (`turn/policy_rows.py`) both configure their
# own "product" kind as `narrow_to_code` - a ROSTER policy (`narrow._ROSTER_POLICIES`)
# - despite neither domain appearing in `gate.REQUIRE_SPECIFIC_DOMAINS`
# (`{"incoming", "product_attachment"}`, measured via
# `grep REQUIRE_SPECIFIC_DOMAINS app/services/chatbot/lanes/business/gate.py`). AC-1690
# says a product roster is asked ONLY in the domains that set names - so this is a
# real, currently-live violation R5/R6 must retire, not a hypothetical.
# --------------------------------------------------------------------------- #


class TestSilentPrefixFilter:
    @pytest.mark.parametrize("domain, policy_value", [("purchase_order", "narrow_to_code"), ("spo_allocation", "narrow_to_code")])
    def test_an_ambiguous_family_token_never_rosters_outside_require_specific_domains(
        self, domain, policy_value
    ) -> None:
        assert domain not in gate_mod.REQUIRE_SPECIFIC_DOMAINS, (
            f"test setup sanity: {domain} must be outside REQUIRE_SPECIFIC_DOMAINS for "
            "this to be the AC-1690 violation it claims to be"
        )
        # The FOCUS candidate is what THIS message named - the bare, unresolved family
        # prefix, current_message True, carrying NO uuid (an un-uuid'd focus row is a
        # token the resolver has not yet settled, `narrow.decide`'s own "typed_now"
        # signal). `resolved_candidates` is the resolver's OWN answer for that same
        # token this turn: TWO real family members, each its own uuid'd row - the
        # shape `resolve_kinds` hands `decide` every turn (measured against
        # `decide`'s own source: an ALREADY-uuid'd focus candidate is read as
        # "settled_carry" and never reaches the roster-vs-filter branch at all, so a
        # focus row carrying its own uuid would test the wrong thing).
        focus_token = [{"raw": "ZZTFAM01", "canonical_code": None, "current_message": True}]
        resolved_candidates = [
            {"raw": "ZZTFAM01-A", "canonical_code": "ZZTFAM01-A", "uuid": "11111111-1111-1111-1111-111111111111"},
            {"raw": "ZZTFAM01-B", "canonical_code": "ZZTFAM01-B", "uuid": "22222222-2222-2222-2222-222222222222"},
        ]
        focus = Focus(products=list(focus_token))
        outcome = narrow_mod.decide(
            kind="product",
            policy_value=policy_value,
            focus=focus,
            profile=Profile(),
            resolved_candidates=resolved_candidates,
        )
        assert outcome.ask_kind is None, (
            f"AC-1690: '{domain}' is not in REQUIRE_SPECIFIC_DOMAINS, so an ambiguous "
            f"product family must be a silent prefix filter (every candidate reaches the "
            f"fetch), never a roster ask: {outcome.ask_kind!r} / options={outcome.ask_options!r}"
        )
        assert len(outcome.entities) == len(resolved_candidates), (
            f"the whole family must reach the fetch unfiltered by a pick: {outcome.entities!r}"
        )


# --------------------------------------------------------------------------- #
# Item 5 - AC-1701 (F6): the require-specific domains' OWN roster copy
# (`gate.py`'s "<domain> search needs to be more specific..." + has/no stamps) must
# win over `turn/narrow.py`'s still-live generic "Which one do you mean?" fallback,
# through the REAL engine (not a direct `gate.run_gate` call, which would bypass the
# exact precedence bug coder 28's own report names).
# --------------------------------------------------------------------------- #


class TestRequireSpecificRosterCopy:
    def test_product_attachment_ambiguous_family_uses_gates_own_header_and_stamps(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = _family_base("ZZTATP")
        has_code, no_code = f"{base}A", f"{base}B"
        has_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=has_code)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=no_code)
        type_id = _seed_attachment_type(session_factory, "Product Photos")
        _seed_file_for(
            session_factory, product_id=has_id, attachment_type_id=type_id,
            company_id=DEFAULT_COMPANY_ID, filename=f"{has_code}.jpg",
        )
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": base, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                {
                    "raw": "product photos", "hint": "attachment_type", "canonical_code": "photo",
                    "current_message": True, "confident": True,
                },
            ],
        )
        # The has/no Product Photos STAMPS are a SECOND MCP seam (`AnswerServices.
        # mcp_probe`, `crm_master_product_attachments_list` - `miss_suggest.
        # run_miss_lane`'s own require-specific stamp probe), never the primary
        # `FetchServices.mcp_call` this turn's `mcp_response` answers - only the
        # HAS_CODE product carries a file, so the probe answers with ONE row (the
        # real MCP tool would never return a row for a product with no attachment).
        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": has_code},
                        "attachment": {
                            "attachment_type": "Product Photos",
                            "original_filename": f"{has_code}.jpg",
                        },
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        result, captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"photo for {base}",
            msg_id="zzt-r5-attachment-roster", mcp_response={"data": []},
            answer_mcp_probe=answer_probe,
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Which product do you mean? Please choose:" in reply, (
            f"AC-1701: gate.py's own require-specific header must win over narrow.py's "
            f"generic fallback: {reply!r}"
        )
        assert "Which one do you mean?" not in reply, (
            f"AC-1701: never the generic narrow.py fallback: {reply!r}"
        )
        assert f"{has_code} - has Product Photos" in reply, reply
        assert f"{no_code} - no Product Photos" in reply, reply

    def test_incoming_ambiguous_family_already_uses_gates_own_header(self, session_factory, monkeypatch) -> None:
        """GREEN CONTROL, not a red - measured directly writing this file. The brief's
        own premise (tester 31's F6/roster-copy-stamps evidence, coder 28's own report)
        names `product_attachment` AND `purchase_order` as domains where narrow.py's
        generic fallback wins over gate.py's own richer text; this test's own bare
        "ambiguous 2-member family, no attachment_type filter" scenario for `incoming`
        already gets gate.py's own header + has/no stamps TODAY, on `24b56e848`, no
        fixture defect involved (confirmed with the SAME hyphen-free-code fix the
        product_attachment sibling test needed). Kept as a regression guard: R5/R6 must
        not regress this while fixing the confirmed `product_attachment` case above."""
        _seed_contact_and_get(session_factory)
        base = _family_base("ZZTINP")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{base}A")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{base}B")
        qf = _parser_output(
            domain_hint="incoming",
            intent_hint="check_incoming",
            entities=[
                {"raw": base, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )
        result, captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"incoming for {base}",
            msg_id="zzt-r5-incoming-roster", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Which product do you mean? Please choose:" in reply, (
            f"AC-1701's own domain-parametrized rule, incoming leg: gate.py's own header "
            f"must win, not narrow.py's generic fallback: {reply!r}"
        )
        assert "Which one do you mean?" not in reply, reply


# --------------------------------------------------------------------------- #
# Item 7 - AC-1703/F8: an unplaced hyphenated/spaced code must reach the
# did-you-mean composer, never a "counted-set" header built off an UNFILTERED
# fetch (`answer.build_set_header`'s "N products have X. Showing M." line, the
# fetch.py:2611 caller measured live for "SRTWT165-FT CERT" and "Technical
# drawings sttwc286-SH" - tester 31's own diagnosis, class (d)).
# --------------------------------------------------------------------------- #


class TestDidYouMeanWinsOverGenericCountedSetMiss:
    def test_unplaced_hyphenated_code_never_reaches_the_counted_set_header(
        self, session_factory, monkeypatch
    ) -> None:
        """GREEN CONTROL, not a red - measured directly writing this file, same finding
        as `TestRequireSpecificRosterCopy::test_incoming_ambiguous_family_already_uses_
        gates_own_header`. Live's exact "SRTWT165-FT CERT" / "Technical drawings
        sttwc286-SH" repro (tester 31's F8, `answer.build_set_header`'s "N products have
        X. Showing M." leak) could NOT be reproduced against this file's isolated
        blank-schema seed: with no pre-existing catalogue the counted-set fetch this
        composer would leak from returns 0 either way, and (unlike
        `test_product_attachment_picker_stamp.py`'s own docstring claim) this session's
        `search_path` DOES include `public`, so `pg_trgm` and the real did-you-mean
        neighbour search both work here, unlike whatever made live differ. Kept as a
        regression guard (the did-you-mean shape must still win in this clean scenario);
        the live "N products have X" leak itself needs a live-shaped repro (a seeded
        catalogue large enough for the counted-set fetch to answer something), which is
        out of this file's own budget - reported, not solved, here."""
        _seed_contact_and_get(session_factory)
        # A real neighbour exists (so a did-you-mean COULD name it), but the exact typed
        # token never resolves - "SRTWT165-FT"-shaped, i.e. a real family PREFIX plus an
        # unmatched suffix. Hyphen-free (`_family_base`, see its own docstring): a
        # hyphenated base tripped an UNRELATED parser/resolver word-splitting quirk
        # (measured directly: "ZZT-ZZTDYM-<hash>-FT" was read as the class word "zzt
        # zztdym" plus noise, reproducing tester 31's OWN separately-flagged, out-of-
        # scope F8 tokenisation finding rather than this test's own target).
        neighbour_base = _family_base("ZZTDYM")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=f"{neighbour_base}X")
        typed = f"{neighbour_base}FT"
        qf = _parser_output(
            domain_hint="product_attachment",
            intent_hint="check_product_attachment",
            entities=[
                {"raw": typed, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
                {
                    "raw": "certificate", "hint": "attachment_type", "canonical_code": "certificate",
                    "current_message": True, "confident": True,
                },
            ],
        )
        result, captured = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"{typed} CERT",
            msg_id="zzt-r5-dym-wins", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert "products have" not in reply, (
            f"AC-1703: an unplaced subject token must never reach the counted-set fetch "
            f"(answer.build_set_header's 'N products have X. Showing M.' line) - the "
            f"subject did not resolve, so the fetch must be a miss, not an unfiltered "
            f"dump over the whole class: {reply!r}"
        )
        assert "Showing 0" not in reply and re.search(r"Showing \d+\.", reply) is None, reply
        assert f'Couldn\'t find "{typed}"' in reply or "Did you mean" in reply, (
            f"AC-1703: the reply must be the did-you-mean shape: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# Item 8 - the customer option's `uuids` must carry EVERY family member, and the
# post-pick fetch must be scoped to that SAME set - measured mismatch, tester 31's
# F2 finding 3 (the picker's own "has DO" stamp pools the family; the post-pick
# fetch narrows to the representative's single uuid only).
# --------------------------------------------------------------------------- #


class TestCustomerOptionCarriesFamily:
    def test_multi_company_customer_option_uuids_equals_the_post_pick_fetch_customer_ids(
        self, session_factory, monkeypatch
    ) -> None:
        """`gate.py`'s ambiguous-customer picker only fires over DISTINCT trading NAMES
        (`len(bases) > 1`) - two ledgers of the SAME name fold to ONE roster line before
        any picker is even reached (contract 103, already correct, confirmed while
        writing this test: that shape never mints a `customer_pick` `Pending` at all).
        The "(MCH, SRT)" multi-company suffix the brief names fires on a DIFFERENT axis:
        one trading NAME that itself has an account in more than one company. So this
        scenario needs TWO distinct names (to reach the picker) where ONE of them spans
        two companies (to exercise the family-uuid question the brief is actually
        about)."""
        from tests._mc_lookup_seed import seed_mocha

        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTFAMC")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        db = session_factory()
        mocha = seed_mocha(db)
        alpha_srt = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT PICK ALPHA TRADING SDN BHD")
        alpha_mch = seed_customer(db, company_id=mocha.id, name="ZZT PICK ALPHA TRADING SDN BHD")
        beta_srt = seed_customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT PICK BETA TRADING SDN BHD")
        db.commit()
        # The contact must be GRANTED into Mocha too, or the real resolver sees zero rows
        # there no matter what `gate.py` does with them (see `_grant_contact_company`'s
        # own docstring) - a second, independent fixture gap from the `uuids` mismatch
        # this test's own assertions are pinning.
        _grant_contact_company(session_factory, mocha.id)

        qf = _parser_output(
            domain_hint="order",
            intent_hint="check_order",
            match_mode="or",  # see TestScopeBlockAfterPick's own comment on this
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            entities=[
                {"raw": "zzt pick", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True},
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True},
            ],
        )
        result1, captured1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf, text_body=f"delivery for zzt pick {code}",
            msg_id="zzt-r5-family-1", mcp_response={"data": []},
        )
        reply1 = (result1.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        assert len(options) == 2, (
            f"test setup sanity: TWO distinct trading names must raise a 2-option "
            f"customer picker: {reply1!r} / options={options!r}"
        )
        alpha_option = next(
            (o for o in options if "ALPHA" in str(o.get("label") or "").upper()), None
        )
        assert alpha_option is not None, options
        got_uuids = set(alpha_option.get("uuids") or [])
        expected_uuids = {alpha_srt.id, alpha_mch.id}
        assert got_uuids == expected_uuids, (
            "AC-1694/contract 103: a multi-company customer option's own uuids must be "
            f"the WHOLE family (both companies' accounts): got {got_uuids!r}, want "
            f"{expected_uuids!r} (option={alpha_option!r})"
        )

        mcp_call, captured2 = _mcp_double(other=lambda name, args: _order_envelope_json([]))
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch,
            qf=_parser_output(
                message_type="casual", intent_hint=None, domain_hint=None, entities=[],
                reference_positions=[alpha_option["position"]],
            ),
            text_body=str(alpha_option["position"]), msg_id="zzt-r5-family-2", mcp_call=mcp_call,
        )
        assert captured2 and captured2[0][0] == "crm_order_management_orders_list", captured2
        _name, args = captured2[0]
        got_customer_ids = set(args.get("customer_ids") or [])
        assert got_customer_ids == expected_uuids, (
            "the fetch args' customer_ids must be the SAME family set the picked option "
            f"named (both companies), not just the representative's own single uuid: "
            f"got {got_customer_ids!r}, want {expected_uuids!r}, args={args!r}"
        )


# --------------------------------------------------------------------------- #
# Item 9 - regression guards, the brief's own expectation was to write these GREEN
# now (against `24b56e848`, before the `plan.ask is None` guard is removed by R5/R6)
# and keep them green through the slice.
#
# MEASURED, WRITING THIS FILE: the brief's own premise for the FIRST one below is
# wrong - this exact shape (a promotion ask whose product is GENUINELY not_found, for
# a contact entitled to 3 tiers) is RED TODAY on `24b56e848`, not green. Confirmed
# directly: `TestPromotionAskUsesProductionCopy::test_two_entitled_tiers_reply_starts_
# and_ends_per_production` (test_rearch_r3_bridge_engine.py, the test the brief's own
# wording echoes) uses a FAKE resolver that answers the product token WITH a match
# (`_one_tier_resolve_services`) - the product is never actually not_found there. This
# test's own resolver instead genuinely fails to place the product
# (`unresolved_tokens: asked`), and on that shape production still answers with
# `turn/compose.py`'s own generic, entitlement-blind tier ask ("Which one do you
# mean?\n1. dealer\n2. office\n3. end user"), not `answer.access_level_choice_message`'s
# richer text - the SAME class of gap `TestRequireSpecificRosterCopy`'s
# `product_attachment` case names (`narrow.py`'s still-live generic arm winning), just
# on the tier axis instead of the product-roster one. Left as a genuine NEW red rather
# than forced to a fabricated green, per the brief's own "anything you found wrong"
# instruction.
# --------------------------------------------------------------------------- #


class TestRegressionGuardsMustStayGreen:
    def test_promotion_ask_for_a_not_found_product_still_shows_the_three_tier_picker(
        self, session_factory, monkeypatch
    ) -> None:
        """MEASURED RED, not the green control the brief expected - see the class
        docstring above. A promotion ask reaches `resolve_gate.run`'s `access_check`
        entry BEFORE resolve-entity even runs (the tier-gate's own `If4` returns
        `exit_kind='access_ask'` before the product is looked up at all), so the tier
        picker asking is correct; what is wrong is WHICH composer answers it once the
        product itself is genuinely not_found."""
        _seed_contact_and_get(session_factory)

        def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            asked = list(body.get("tokens") or [])
            return {"tokens": asked, "resolutions": [], "unresolved_tokens": asked}

        resolve_services = ResolveGateServices(
            access_types=lambda **_: [
                {"name": "Sorento Dealer"}, {"name": "Sorento Office"}, {"name": "End User"}
            ],
            resolve_entity=_resolve_entity,
            probe=lambda **_: None,
        )
        qf = _parser_output(
            domain_hint="promotion",
            intent_hint="check_promotion",
            entities=[
                {"raw": "ZZTNOPROMO", "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )
        result, captured = _run_turn(
            session_factory, monkeypatch, qf=qf, text_body="promo for ZZTNOPROMO",
            msg_id="zzt-r5-guard-promo-3tier", resolve_services=resolve_services,
            mcp_response={"has_result": False, "items": []},
        )
        reply = (result.reply or {}).get("text") or ""
        assert reply.startswith("Which access level do you need for ZZTNOPROMO?"), reply
        assert 'Reply with the number(s), e.g. "1", "1 and 2", or "all".' in reply, reply
        for label in ("Dealer", "Office", "End user"):
            assert label in reply, (label, reply)
        assert reply != "Would you like me to escalate to marketing product team?", reply
