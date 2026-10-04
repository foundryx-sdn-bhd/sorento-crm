"""S6c tester slice: the business lane's answer + miss half (AC-607 to AC-609, D10,
D14, H16, H22/H23, H39, H40, H45).

RED FIRST (PRINCIPLES.md Phase 2). None of `app/services/chatbot/lanes/business/answer.py`,
`sub_answer.py`, `miss_suggest.py` exist yet, and `services.py` has no `AnswerServices`
bundle - every test below fails at import time with `ModuleNotFoundError` /
`AttributeError`, which is the RIGHT reason: the contract is pinned here, the coder
makes it green.

**The contract, in the shapes the coder implements (see each test's docstring for the
n8n node whose by-name reads it reproduces)**:

`app/services/chatbot/lanes/business/answer.py`
    validator(result, *, semantic_parser, not_allowed_check_stock=False) -> dict
    promo_picker(item, *, parser, resolved) -> dict
    crossdomain_zeroset(item, *, parser, resolved, session_block) -> dict
    crossdomain_probe_args(zeroset, *, parser, entities_names, contact_id, space_id) -> dict
    crossdomain_render(probe_result, *, zeroset, validator) -> dict
    run_crossdomain(validator_result, *, parser, resolved, session_block, entities_names,
                    services, contact_id, space_id, dry_run=False) -> dict
        # `session_block` is `ctx.session` (get-session-vars' own response shape), never a
        # database session - named to hold against `test_no_answer_lane_function_takes_a_db_session`
    build_result(item, *, validator, promo, zeroset, tool=None, tier_probe=None,
                 crossdomain_render=None) -> dict
    not_found_error_message(item, *, parser, resolved, gate) -> dict
    access_level_choice_message(item, *, parser) -> dict
    build_suggest_offer(item, *, parser, resolved, gate, dym_annotate=None,
                        sibling_probe=None, sibling_transform=None) -> dict
    dispatch(result) -> Literal["sub_answer", "miss_suggest"]           # If6
    aggregate_response_intro(result) -> list[str]                       # Aggregate1

`app/services/chatbot/lanes/business/__init__.py` (new seam beside `run_until_exit`)
    complete_answer(...) -> {"reply": dict, "actions": list}   # engine.run_turn calls this
        when `delegate.delegate_for(branch_kind, engine._enabled_lanes(db))` is None, and
        sets `delegate = None` from its `{reply, actions}` return instead of delegating.

`app/services/chatbot/lanes/business/sub_answer.py`
    answer_input(trigger) -> dict                                        # raises if trigger.item is not an object
    central_exchange(item) -> dict
    miss_roster_check(item, *, build_result, parser) -> dict
    miss_roster_plan(item, *, build_result, parser, gate, central_exchange=None) -> dict
    build_miss_member_offer(item, *, central_exchange, roster_plan) -> dict
    dym_transform_partial(item, *, parser, gate, resolved, central_exchange=None) -> dict
    dym_annotate_partial(item) -> dict
    answer_result(item, *, central_exchange=None, member_offer=None,
                 dym_annotate_partial=None) -> dict

`app/services/chatbot/lanes/business/miss_suggest.py`
    dym_transform(item, *, parser, resolved, central_exchange=None) -> dict
    dym_annotate(item) -> dict
    miss_suggest_result(item, *, dym_annotate=None, sibling_transform=None,
                        sibling_probe=None) -> dict   # sub-miss-suggest's own exit/carrier
    run_miss_lane(not_found_item, *, parser, resolved, gate, services,
                 dry_run=False) -> dict

`app/services/chatbot/lanes/business/services.py` (new bundle beside `ResolveGateServices`)
    class McpProbeFn(Protocol): def __call__(self, name: str, args: dict) -> Any: ...
    class FamilyFetchFn(Protocol): def __call__(self, query: str) -> Any: ...
    class AnswerServices:  mcp_probe: McpProbeFn; family_fetch: FamilyFetchFn
    production_answer_services(db) -> AnswerServices

Everything here is a pure function over plain dicts (D10: no session held across an MCP
call) except the two `production_answer_services` contract checks, which only inspect
signatures - no database, no network, no LLM.
"""
from __future__ import annotations

import inspect
import re as re_mod
from pathlib import Path
from typing import Any

import pytest

from tests.chatbot import _corpus, divergences
from tests.chatbot.conftest import validating_resolve_entity
from tests.chatbot.test_engine import (  # noqa: F401  - fixtures used by name (S6a precedent)
    _envelope,
    seeded,
    stub_access,
    stub_parser,
)

# --------------------------------------------------------------------------- #
# Fixture loading, LOCAL to this file rather than added to the shared
# `_corpus.NODE_SLUGS`. `scripts/chatbot_fixture_coverage.py` (gate 0, AC-008) iterates
# that shared dict and cross-references it against `CAPTURE_REPORT` - a table of which
# workflow VERSIONS the capture agent has actually scanned. None of the slugs below
# (`live-spine-sorento-consume-main`, `sub-answer-rs`, `sub-miss-suggest-rs`, ...) has a
# `CAPTURE_REPORT` entry yet, so registering them there would make gate 0 report every
# S6c cell "SHORT" and block - a real, useful signal once someone runs the capture-agent
# campaign S6a went through (`documentation/plans/chatbot/PLAN-chatbot-turn-engine.md`
# S6 note), but not this tester slice's call to make. `_corpus.NODE_SLUGS` itself stays
# untouched; this file loads the same fixture JSON shape directly.
# --------------------------------------------------------------------------- #

S6C_NODE_SLUGS: dict[str, tuple[str, ...]] = {
    "validator": ("live-spine-sorento-consume-main",),
    "promo-picker": ("live-spine-sorento-consume-main",),
    "crossdomain-zeroset": ("live-spine-sorento-consume-main",),
    "crossdomain-render": ("live-spine-sorento-consume-main",),
    "not-found-error-message": ("live-spine-sorento-consume-main",),
    "access-level-choice-message": ("clone-spine-RS", "live-spine-sorento-consume-main"),
    "build-suggest-offer": ("live-spine-sorento-consume-main",),
    # `sub-answer-live` (36 captures each) is the richer corpus from the 5 Sep capture
    # batch (help-crm), a SIBLING worktree of the n8n repo, not merged into the main
    # checkout - point `CHATBOT_FIXTURES_DIR` at it to grade against it. The sparse
    # `sub-answer` / `sub-answer-rs` directories in the main checkout stay listed too
    # (union by file stem via `_load_dir`, same discipline as `_corpus.full_corpus`).
    "miss-roster-plan": ("live-spine-sorento-consume-main", "sub-answer-live"),
    "build-miss-member-offer": ("live-spine-sorento-consume-main", "sub-answer-live"),
    "dym-annotate-partial": ("live-spine-sorento-consume-main", "sub-answer-live"),
    # `sub-send-attachments` / `-rs` are EXCLUDED (same class as S6a's
    # `disallowed-entity-gate` exclusions): that sub's own `central-exchange.js` is a
    # name-preserving STUB re-emitting `trigger.attachments_src` (12 lines), not the
    # real 28-line node this port replicates (`output.output`/markdown-fence unwrap).
    # Measured: `cat export/sub-send-attachments-rs/nodes/central-exchange.js`.
    "central-exchange": (
        "live-spine-sorento-consume-main",
        "sub-answer-rs",
        "sub-answer-live",
    ),
    "miss-roster-check": (
        "clone-spine-RS",
        "live-spine-sorento-consume-main",
        "sub-answer",
        "sub-answer-rs",
        "sub-answer-live",
    ),
    "dym-transform-partial": (
        "live-spine-sorento-consume-main",
        "sub-answer-rs",
        "sub-answer-live",
    ),
    "answer-input": ("sub-answer-rs", "sub-answer-live"),
    "answer-result": ("sub-answer-rs", "sub-answer-live"),
    # `sub-miss-suggest-live` (the 5 Sep batch): confirmed by direct grep of that live
    # workflow's export - its live version (`f42de9c6`) has NO `build-suggest-offer`
    # node of its own (that composer stays on the spine, see `miss-suggest-result`
    # below); captured nodes there are `dym-transform`, `dym-annotate`,
    # `sibling-transform`, `miss-suggest-result`. `promo-dym-plan` exists in the export
    # but never fired in the scanned pool (0 captures) - no node/test is built against
    # it here; it is a real zero cell for `scripts/chatbot_fixture_coverage.py`, not an
    # oversight.
    "dym-transform": (
        "clone-spine-RS",
        "live-spine-sorento-consume-main",
        "sub-miss-suggest-rs",
        "sub-miss-suggest-live",
    ),
    "dym-annotate": (
        "clone-spine-RS",
        "live-spine-sorento-consume-main",
        "sub-miss-suggest-rs",
        "sub-miss-suggest-live",
    ),
    # `miss-suggest-result`: `sub-miss-suggest`'s OWN exit/carrier node (RS-7 errata) -
    # only ever captured under `sub-miss-suggest-live` (38 files), never on the spine
    # (the spine inlines everything and has no sub-workflow boundary to carry across).
    "miss-suggest-result": ("sub-miss-suggest-live",),
    # `sub-answer-rs` / `sub-answer-live` are EXCLUDED, same class: `sub-answer`'s own
    # `build-result.js` is a NAMED-VALUE carrier (`return [{json:
    # trigger.result ?? {}}]`, 12 lines) re-emitting the contract's `result` value
    # verbatim - not `sub-main-processing`'s real 88-line node this port replicates.
    # Measured: `cat export/sub-answer-rs/nodes/build-result.js`.
    "build-result": (
        "clone-sub-main-processing",
        "clone-spine-RS",
        "live-spine-sorento-consume-main",
    ),
}


def _s6c_full_corpus(node: str) -> list[_corpus.Fixture]:
    """`_corpus.full_corpus`, but reading `S6C_NODE_SLUGS` instead of the shared dict."""
    root = _corpus.corpus_root()
    if root is None:
        return []
    out: list[_corpus.Fixture] = []
    for slug in S6C_NODE_SLUGS.get(node, ()):
        out.extend(_corpus._load_dir(node, root / "nodes" / slug / node, prefix=f"{slug}/"))
    return out


# --------------------------------------------------------------------------- #
# Shared upstream readers. Fixtures come from TWO shapes (D8/S6a precedent,
# `test_replay.py`'s `_sub_ctx` family): the sub-workflow's OWN graph, which wraps the
# resolver behind `build-ctx-resolved` (`ctx.resolved` / `ctx.gate`), and the LIVE
# SPINE's monolithic copy, which reads `resolve-entity` / `disallowed-entity-gate`
# directly with no wrapper. Every S6c node the corpus actually captured (see
# `_corpus.NODE_SLUGS`) came from the live spine, so the fallback path is the one that
# fires on every fixture today; the wrapped path is kept for the day a sub-workflow's
# own capture is added.
# --------------------------------------------------------------------------- #


def _bc_ctx(fixture: _corpus.Fixture) -> dict:
    """`$('build-ctx').first().json.ctx` - one shape regardless of which graph ran it."""
    return (fixture.first("build-ctx") or {}).get("ctx") or {}


def _parser_output(fixture: _corpus.Fixture) -> dict:
    return ((_bc_ctx(fixture).get("parse") or {}) or {}).get("output") or {}


def _session_block(fixture: _corpus.Fixture) -> dict:
    return _bc_ctx(fixture).get("session") or {}


def _named(fixture: _corpus.Fixture, node: str) -> Any:
    """`$(node).isExecuted ? $(node).first().json : null` - the three-state by-name read."""
    items = fixture.upstream(node)
    return items[0].get("json") if items else None


def _resolved(fixture: _corpus.Fixture) -> dict:
    bcr = _named(fixture, "build-ctx-resolved")
    if bcr is not None:
        return (bcr.get("ctx") or {}).get("resolved") or {}
    return _named(fixture, "resolve-entity") or {}


def _gate(fixture: _corpus.Fixture) -> dict:
    bcr = _named(fixture, "build-ctx-resolved")
    if bcr is not None:
        return (bcr.get("ctx") or {}).get("gate") or {}
    return _named(fixture, "disallowed-entity-gate") or {}


def _input_json(fixture: _corpus.Fixture) -> dict:
    return (fixture.input[0].get("json") or {}) if fixture.input else {}


def _rt(value: Any) -> Any:
    return _corpus.json_round_trip(value)


def _wrap(result: Any) -> list:
    """`tests/harness/n8n-shim.js`'s own `normalizeReturn`: a Code node returning a
    plain object produces exactly ONE item (`[{"json": result}]`, `test_replay.py`'s
    own shape); returning `None`/`undefined` produces ZERO items - n8n's shim treats a
    null return as "no items", not an error (measured live, exec 14126032), which is
    also why `_corpus.Fixture.expected` folds a fixture's `"expected": null` to `[]`
    (`self.data.get("expected") or []`). Every `_run_*` below returns through this so
    `_replay` compares two item LISTS, matching every captured `expected` array."""
    if result is None:
        return []
    return [{"json": result}]


# --------------------------------------------------------------------------- #
# AC-608: one runner per ported node, named exactly like `test_replay.py`'s. Only a
# REAL capture (`source.expected_from == "runData"`) grades the port - see `_corpus.py`.
# --------------------------------------------------------------------------- #


def _run_validator(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import validator

    edit_fields2 = _named(fixture, "Edit Fields2")
    return _wrap(
        validator(
            _rt(_input_json(fixture)),
            semantic_parser=_rt(_bc_ctx(fixture).get("parse") or {}),
            not_allowed_check_stock=bool((edit_fields2 or {}).get("not_allowed_check_stock")),
        )
    )


def _run_promo_picker(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import promo_picker

    return _wrap(
        promo_picker(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            resolved=_rt(_resolved(fixture)),
        )
    )


def _run_crossdomain_zeroset(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

    return _wrap(
        crossdomain_zeroset(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            resolved=_rt(_resolved(fixture)),
            session_block=_rt(_session_block(fixture)),
        )
    )


def _run_crossdomain_render(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import crossdomain_render

    zeroset = _named(fixture, "crossdomain-zeroset") or {}
    return _wrap(
        crossdomain_render(
            _rt(_input_json(fixture)),
            zeroset=_rt(zeroset.get("_xd") or {}),
            validator=_rt(_named(fixture, "validator") or {}),
        )
    )


def _run_build_result(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import build_result

    call = _named(fixture, "Call 'sub-fetch-results'")
    tool = (call or {}).get("tool") if call is not None else _named(fixture, "tool-filter")
    tier_probe = (call or {}).get("tier_probe") if call is not None else None
    crossdomain_render = _named(fixture, "crossdomain-render")
    return _wrap(
        build_result(
            _rt(_input_json(fixture)),
            validator=_rt(_named(fixture, "validator") or {}),
            promo=_rt(_named(fixture, "promo-picker") or {}),
            zeroset=_rt(_named(fixture, "crossdomain-zeroset") or {}),
            tool=_rt(tool) if tool is not None else None,
            tier_probe=_rt(tier_probe) if tier_probe is not None else None,
            crossdomain_render=_rt(crossdomain_render) if crossdomain_render is not None else None,
        )
    )


def _run_not_found_error_message(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    return _wrap(
        not_found_error_message(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            resolved=_rt(_resolved(fixture)),
            gate=_rt(_gate(fixture)),
        )
    )


def _run_access_level_choice_message(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import access_level_choice_message

    return _wrap(
        access_level_choice_message(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
        )
    )


def _get_results(fixture: _corpus.Fixture) -> list | None:
    """`$('Call \\'sub-get-results\\'').all(0, ri)` for `ri` in `0..24` (D2's alternatives
    scan): a list of RUNS, each run itself a list of that run's own `.json` bodies. The
    harness only ever captures run 0 (the comment on the node itself: "get-results runs
    EXACTLY ONCE per turn"), so this wraps that one run rather than inventing the others -
    `None` when the node never ran at all, matching the node's own `isExecuted` guard."""
    items = fixture.upstream("Call 'sub-get-results'")
    if not items:
        return None
    return [[i.get("json") for i in items]]


def _execution_id(fixture: _corpus.Fixture) -> Any:
    """`$execution.id`, off the fixture's own capture metadata (same read as
    `test_replay.py::_execution_id`, informal here since no fixture lacks it)."""
    return (fixture.data.get("execution") or {}).get("id") or (
        fixture.data.get("source") or {}
    ).get("execution_id")


def _run_build_suggest_offer(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.answer import build_suggest_offer

    dym_annotate = _named(fixture, "dym-annotate")
    sibling_probe = _named(fixture, "sibling-probe")
    sibling_transform = _named(fixture, "sibling-transform")
    get_results = _get_results(fixture)
    return _wrap(
        build_suggest_offer(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            resolved=_rt(_resolved(fixture)),
            gate=_rt(_gate(fixture)),
            dym_annotate=_rt(dym_annotate) if dym_annotate is not None else None,
            sibling_probe=_rt(sibling_probe) if sibling_probe is not None else None,
            sibling_transform=_rt(sibling_transform) if sibling_transform is not None else None,
            get_results=_rt(get_results) if get_results is not None else None,
            execution_id=_execution_id(fixture),
        )
    )


def _run_answer_input(fixture: _corpus.Fixture) -> Any:
    """`answer-input`: `sub-answer`'s item carrier. Reads the sub's OWN trigger
    (`$('When Executed by Another Workflow').first().json.item`) POSITIONALLY-blind -
    never `$input` - and raises if the trigger carried no `item` object."""
    from app.services.chatbot.lanes.business.sub_answer import answer_input

    trigger = fixture.first("When Executed by Another Workflow")
    return _wrap(answer_input(_rt(trigger)))


def _run_central_exchange(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.sub_answer import central_exchange

    return _wrap(central_exchange(_rt(_input_json(fixture))))


def _run_miss_roster_check(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.sub_answer import miss_roster_check

    build_result_out = _named(fixture, "build-result") or {}
    return _wrap(
        miss_roster_check(
            _rt(_input_json(fixture)),
            build_result=_rt(build_result_out.get("result") or {}),
            parser=_rt(_parser_output(fixture)),
        )
    )


def _run_miss_roster_plan(fixture: _corpus.Fixture) -> Any:
    """`build_result.tool.name` is `build-result`'s own shape when that consolidated node
    ran (the `sub-main-processing`/clone-sub-main-processing slugs). The LIVE SPINE has no
    such node - its own `miss-roster-plan.js` reads `$('tool-filter').first().json` for
    the tool DIRECTLY - so the fallback rebuilds the same `{tool: ...}` shape from
    `tool-filter`'s own output when `build-result` never ran."""
    from app.services.chatbot.lanes.business.sub_answer import miss_roster_plan

    build_result_out = _named(fixture, "build-result")
    if build_result_out is not None:
        build_result = build_result_out.get("result") or {}
    else:
        tool = _named(fixture, "tool-filter")
        build_result = {"tool": tool} if tool is not None else {}
    central_exchange = _named(fixture, "central-exchange")
    return _wrap(
        miss_roster_plan(
            _rt(_input_json(fixture)),
            build_result=_rt(build_result),
            parser=_rt(_parser_output(fixture)),
            gate=_rt(_gate(fixture)),
            central_exchange=_rt(central_exchange) if central_exchange is not None else None,
        )
    )


def _run_build_miss_member_offer(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.sub_answer import build_miss_member_offer

    return _wrap(
        build_miss_member_offer(
            _rt(_input_json(fixture)),
            central_exchange=_rt(_named(fixture, "central-exchange") or {}),
            roster_plan=_rt(_named(fixture, "miss-roster-plan") or {}),
        )
    )


def _run_dym_transform_partial(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.sub_answer import dym_transform_partial

    central_exchange = _named(fixture, "central-exchange")
    return _wrap(
        dym_transform_partial(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            gate=_rt(_gate(fixture)),
            resolved=_rt(_resolved(fixture)),
            central_exchange=_rt(central_exchange) if central_exchange is not None else None,
        )
    )


def _run_dym_annotate_partial(fixture: _corpus.Fixture) -> Any:
    """`dym-annotate-partial.js`'s own by-name reads: `_PAYLOAD_SRC = 'central-exchange'`
    (the payload the downstream renderer expects back), `_XF_SRC = 'dym-transform-partial'`
    (this lane's planner). Neither is optional on this deployment - both are direct
    upstreams of every capture - so absence still threads through as `None` rather than
    being silently skipped."""
    from app.services.chatbot.lanes.business.sub_answer import dym_annotate_partial

    payload = _named(fixture, "central-exchange")
    transform = _named(fixture, "dym-transform-partial")
    return _wrap(
        dym_annotate_partial(
            _rt(_input_json(fixture)),
            payload=_rt(payload) if payload is not None else None,
            transform=_rt(transform) if transform is not None else None,
        )
    )


def _run_answer_result(fixture: _corpus.Fixture) -> Any:
    from app.services.chatbot.lanes.business.sub_answer import answer_result

    central_exchange = _named(fixture, "central-exchange")
    member_offer = _named(fixture, "build-miss-member-offer")
    dym_annotate_partial = _named(fixture, "dym-annotate-partial")
    return _wrap(
        answer_result(
            _rt(_input_json(fixture)),
            central_exchange=_rt(central_exchange) if central_exchange is not None else None,
            member_offer=_rt(member_offer) if member_offer is not None else None,
            dym_annotate_partial=(
                _rt(dym_annotate_partial) if dym_annotate_partial is not None else None
            ),
        )
    )


def _run_dym_transform(fixture: _corpus.Fixture) -> Any:
    """Same `input: null` / `expected: null` pair as `dym-annotate` on the SAME 5
    captures (measured: `clone-spine-RS/rs7-precond-t1/t2/t3`,
    `sub-miss-suggest-rs/rs7-t2-d2-reqspec/rs7-t3-promodym` all carry it here too) -
    `dym-transform` is upstream of `dym-annotate` in the same turn, so if it never ran
    neither did its successor. Checked before the import for the same reason."""
    if not fixture.input:
        return _wrap(None)
    from app.services.chatbot.lanes.business.miss_suggest import dym_transform

    central_exchange = _named(fixture, "central-exchange")
    return _wrap(
        dym_transform(
            _rt(_input_json(fixture)),
            parser=_rt(_parser_output(fixture)),
            resolved=_rt(_resolved(fixture)),
            # `gate` is the UPSTREAM IF that decides whether the require-specific PICKER
            # lane fires (`_dym_plan.picker_cands()` reads `gate.require_specific` /
            # `gate.gate_clarification` / `gate.compatible_entities`) rather than the D1
            # lane - omitting it silently drops every capture into "d1", which is the
            # WRONG lane whenever the gate asked a numbered picker question.
            gate=_rt(_gate(fixture)),
            central_exchange=_rt(central_exchange) if central_exchange is not None else None,
        )
    )


def _run_dym_annotate(fixture: _corpus.Fixture) -> Any:
    """5 captures (`clone-spine-RS/rs7-precond-t1/t2/t3`,
    `sub-miss-suggest-rs/rs7-t2-d2-reqspec/rs7-t3-promodym`) carry `input: null` AND
    `expected: null` together - n8n gave this node ZERO input items in that real
    execution, so its body never ran at all (a Code node with no input items produces
    no output items; `normalizeReturn`'s null branch is the SAME "no items" case, not
    an error). Reproduced by not calling the port at all rather than feeding it a
    fabricated empty dict `fixture.input` was never handed - checked BEFORE the import
    so these 5 grade even before `dym_annotate` itself exists."""
    if not fixture.input:
        return _wrap(None)
    from app.services.chatbot.lanes.business.miss_suggest import dym_annotate

    # `dym-annotate.js`'s own by-name reads: `_PAYLOAD_SRC = 'not-found-error-message'`
    # (the payload the downstream renderer expects back), `_XF_SRC = 'dym-transform'` (this
    # lane's planner). `probe_items = $input.all()` - the FULL input list to this node's own
    # execution, only D18's per-candidate promotion lane reads it.
    payload = _named(fixture, "not-found-error-message")
    transform = _named(fixture, "dym-transform")
    probe_items = [item.get("json") for item in fixture.input]
    return _wrap(
        dym_annotate(
            _rt(_input_json(fixture)),
            payload=_rt(payload) if payload is not None else None,
            transform=_rt(transform) if transform is not None else None,
            probe_items=_rt(probe_items),
        )
    )


def _run_miss_suggest_result(fixture: _corpus.Fixture) -> Any:
    """`miss-suggest-result`: `sub-miss-suggest`'s OWN exit/carrier (its live graph
    (`f42de9c6`) has no `build-suggest-offer` of its own - that node stays on the
    SPINE and reads this carrier's `outcome_fragment` one hop later, RS-7 errata). Picks
    `dym-annotate`'s own output when it ran, else re-emits the input, and always attaches
    `outcome_fragment = {dym-annotate, sibling-transform, sibling-probe}`."""
    from app.services.chatbot.lanes.business.miss_suggest import miss_suggest_result

    dym_annotate = _named(fixture, "dym-annotate")
    sibling_transform = _named(fixture, "sibling-transform")
    sibling_probe = _named(fixture, "sibling-probe")
    return _wrap(
        miss_suggest_result(
            _rt(_input_json(fixture)),
            dym_annotate=_rt(dym_annotate) if dym_annotate is not None else None,
            sibling_transform=_rt(sibling_transform) if sibling_transform is not None else None,
            sibling_probe=_rt(sibling_probe) if sibling_probe is not None else None,
        )
    )


RUNNERS: dict[str, Any] = {
    "validator": _run_validator,
    "promo-picker": _run_promo_picker,
    "crossdomain-zeroset": _run_crossdomain_zeroset,
    "crossdomain-render": _run_crossdomain_render,
    "not-found-error-message": _run_not_found_error_message,
    "access-level-choice-message": _run_access_level_choice_message,
    "build-suggest-offer": _run_build_suggest_offer,
    "answer-input": _run_answer_input,
    "central-exchange": _run_central_exchange,
    "miss-roster-check": _run_miss_roster_check,
    "miss-roster-plan": _run_miss_roster_plan,
    "build-miss-member-offer": _run_build_miss_member_offer,
    "dym-transform-partial": _run_dym_transform_partial,
    "dym-annotate-partial": _run_dym_annotate_partial,
    "answer-result": _run_answer_result,
    "dym-transform": _run_dym_transform,
    "dym-annotate": _run_dym_annotate,
    "miss-suggest-result": _run_miss_suggest_result,
}
# `build-result` reads three producers this file also wires directly (validator,
# promo-picker, crossdomain-zeroset) - registered separately so its own runner name
# does not collide with the upstream helpers above.
RUNNERS["build-result"] = _run_build_result

PORTED_NODES = sorted(RUNNERS)


def _replay(fixture: _corpus.Fixture) -> None:
    """Same shape as `test_replay.py::_replay` (AC-005): a registered divergence with
    `strip_paths` is FIELD-scoped - those paths come off both sides and the remainder must
    still be byte-equal - while a blanket entry (no `strip_paths`) excuses the whole
    fixture. Applying `strip_paths` here (rather than only checking `registered is not
    None`) is what keeps the dym-transform/dym-annotate stale-spine entries a real,
    field-scoped gate instead of a rubber stamp on any mismatch."""
    actual = _rt(RUNNERS[fixture.node](fixture))
    expected = _rt(fixture.expected)
    registered = divergences.find(fixture.node, fixture.name.split("/")[-1])
    if registered is not None and registered.strip_paths:
        stripped_actual = divergences.strip(actual, registered.strip_paths)
        stripped_expected = divergences.strip(expected, registered.strip_paths)
        assert stripped_actual == stripped_expected, (
            f"{fixture.node}/{fixture.name} diverges outside the registered "
            f"{registered.hazard} fields\nfixture: {fixture.path}"
        )
        return
    if actual == expected:
        if registered is not None:
            pytest.fail(
                f"{fixture.node}/{fixture.name}: registered divergence "
                f"{registered.hazard} no longer diverges - retire the entry"
            )
        return
    if registered is not None:
        return
    assert actual == expected, (
        f"{fixture.node}/{fixture.name} diverges from the captured n8n output and is not "
        f"registered in tests/chatbot/divergences.py\nfixture: {fixture.path}"
    )


@pytest.mark.parametrize(
    "fixture",
    _corpus.graded([f for node in PORTED_NODES for f in _s6c_full_corpus(node)]) or [None],
    ids=lambda f: f"{f.node}/{f.name}" if f is not None else "corpus-absent",
)
def test_replay(fixture) -> None:
    """AC-608: every `runData` fixture for the S6c nodes (AC-607) replayed through the
    Python port, byte-equal after a JSON round trip. Parametrised over the FULL n8n
    corpus (sibling checkout, `CHATBOT_FIXTURES_DIR` override honoured via
    `_corpus.corpus_root()`) rather than a vendored subset - S6c has not vendored a
    committed subset yet (AC-008 is a coder/reviewer follow-up, mirroring how S6a
    vendored progressively); this is the always-graded gate once that lands.
    """
    if fixture is None:
        pytest.skip(_corpus.corpus_skip_reason())
    _replay(fixture)


@pytest.mark.parametrize("node", PORTED_NODES)
def test_full_corpus_has_at_least_one_capture(node: str) -> None:
    """Each of the 17 AC-607 nodes has at least one real capture in the sibling n8n
    checkout - if this goes empty for a node, `test_replay` would pass by having
    nothing to grade for it, which is exactly the silent-gate failure AC-008 exists to
    catch (measured per-node counts belong in COVERAGE.md once vendored)."""
    root = _corpus.corpus_root()
    if root is None:
        pytest.skip(_corpus.corpus_skip_reason())
    fixtures = _s6c_full_corpus(node)
    assert fixtures, (
        f"no captures for {node} under any of {S6C_NODE_SLUGS.get(node, ())} - "
        "test_replay would grade nothing for this node"
    )


# --------------------------------------------------------------------------- #
# AC-607: If6 dispatch (validator/is_valid -> sub_answer, else the miss lane) and
# Aggregate1 (n8n's built-in Aggregate node, `fieldsToAggregate: [response_intro]`).
# --------------------------------------------------------------------------- #


class TestIf6Dispatch:
    def test_has_result_and_is_valid_goes_to_sub_answer(self) -> None:
        from app.services.chatbot.lanes.business.answer import dispatch

        assert dispatch({"has_result": True, "is_valid": True}) == "sub_answer"

    @pytest.mark.parametrize(
        ("has_result", "is_valid"),
        [(False, True), (True, False), (False, False)],
    )
    def test_anything_else_goes_to_the_miss_lane(self, has_result: bool, is_valid: bool) -> None:
        from app.services.chatbot.lanes.business.answer import dispatch

        assert dispatch({"has_result": has_result, "is_valid": is_valid}) == "miss_suggest"

    def test_miss_lane_carries_aggregate1s_response_intro(self) -> None:
        """`Aggregate1` (n8n `n8n-nodes-base.aggregate`, `fieldsToAggregate:
        [response_intro]`) runs on the miss branch and collects the `response_intro`
        field off the item(s) it sees. The port must hand the SAME value to the miss
        lane rather than dropping it on the floor when it restructures the dispatch."""
        from app.services.chatbot.lanes.business.answer import aggregate_response_intro, dispatch

        result = {
            "has_result": False,
            "is_valid": True,
            "response_intro": "No stock records found for: SRTWC8517.",
        }
        assert dispatch(result) == "miss_suggest"
        assert aggregate_response_intro(result) == ["No stock records found for: SRTWC8517."]
        assert aggregate_response_intro({"response_intro": None}) == []


# --------------------------------------------------------------------------- #
# RETIRED (AC-1592, this session): `TestChatbotCompletedLanesEngineWiring` (2 methods)
# and `TestAC604FetchErrorIsAnOutcomeNotAnEmptyTurn` (2 methods) pinned the S6a-era
# "shadow lane" wiring this comment block itself describes - `engine.decide` (a
# ctx-in/branch-kind-out dispatcher, deleted, AttributeError, grep-confirmed),
# `result.delegate_payload["_exit_kind"]`, and `lanes.business.run_until_exit` /
# `complete_answer` (the old lane-exit seams, replaced by `turn/fetch.py::run_fetch` +
# `turn_runtime.make_tool_runner`, calling the SAME kept `lanes/business.run_fetch`
# directly - tester 11's finding, `test_engine_failure_paths.py`). `chatbot_completed_
# lanes` no longer gates completion at all (contract 73 superseded) - `business_query`
# is in `CRM_COMPLETED_BRANCH_KINDS` unconditionally today.
#
# Replacement coverage, already landed:
# - The completion/delegate property (`TestChatbotCompletedLanesEngineWiring`):
#   `test_s3_switch_and_complete_by_body.py::TestTheCompletedLaneSwitch`.
# - AC-604's own property (`TestAC604FetchErrorIsAnOutcomeNotAnEmptyTurn` - a RETURNED
#   error fragment is answered as a miss, never left empty or failed):
#   `test_engine_failure_paths.py::TestTheBusinessLaneOnFetchFailure.
#   test_a_returned_error_fragment_is_answered_as_a_miss_not_a_failure` (tester 11) -
#   measured the CURRENT engine's identical guarantee: a returned error-shaped fragment
#   (any `outcome`, including `not_found`) never raises, so the turn completes `done`
#   with an ordinary miss reply - only a RAISED exception fails a turn today, a real
#   architecture change from the old `_fetch_arm == "error" and outcome is None` split.
# `TestErrorArmRendersTheMissLane` below (a different class, still green) is unaffected -
# it drives `lanes.business.complete_answer` directly as a unit, not through `run_turn`.
# --------------------------------------------------------------------------- #


def stub_resolve_gate_bundle(calls: list[str]):
    """A `ResolveGateServices` bundle that records which of its three seams a turn
    reached, without resolving anything for real. Kept as a MODULE function (not a
    retired class's staticmethod) - `test_s6c_engine_paths.py` and
    `test_s6_s7_integration.py` both import this by name, unrelated to the S6a shadow-
    lane classes retired above."""
    from app.services.chatbot.lanes.business.services import ResolveGateServices

    def _access_types(*, contact_id, space_id):
        calls.append("access_types")
        return [{"name": "Sorento Dealer"}]

    def _resolve_entity(body):
        calls.append("resolve_entity")
        return {"tokens": [], "resolutions": [], "unresolved_tokens": []}

    def _probe(**kwargs):
        calls.append("probe")
        return None

    return ResolveGateServices(
        access_types=_access_types, resolve_entity=validating_resolve_entity(_resolve_entity), probe=_probe
    )


class TestErrorArmRendersTheMissLane:
    """The other half of AC-604: what `complete_answer` DOES with the error arm.

    The miss lane runs on it (`not-found-error-message` then `build-suggest-offer`), so
    the customer gets the itemised miss and the escalate offer rather than silence.
    """

    def test_the_error_arm_reaches_the_miss_renderer(self, monkeypatch) -> None:
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.lanes.business import complete_answer
        from app.services.chatbot.lanes.business.services import AnswerServices

        captured: dict[str, Any] = {}

        class _Completed:
            reply = {"text": "stub", "quick_replies": []}
            actions: list = []
            session_patch = None
            status = "done"
            stage = "remembered"

        def _complete_turn(turn_id, fragments, *, session_factory, compose_send_action=False, lane_trace=None):
            captured["fragments"] = fragments
            return _Completed()

        monkeypatch.setattr(engine_mod, "complete_turn", _complete_turn)

        ctx = {
            "contact": {"id": "c1"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "user_goal": "stock for SRTWC8517",
                    "entities": [{"raw": "SRTWC8517", "hint": "product"}],
                    "access_levels": [],
                }
            },
            "session": {},
        }
        payload = {
            "_exit_kind": "continue",
            "resolved": {"unresolved_tokens": ["SRTWC8517"], "resolutions": [], "by_entity_type": {}},
            "gate": {"gate_passed": True, "gate_debug": {"domain": "inventory"}, "compatible_entities": []},
            "fetch": {
                "_fetch_arm": "error",
                "error": "no MCP tool matched this question",
                # R2-S2: a genuine ABSENCE (zero tools matched), not an outage - without
                # this key `complete_answer` now reads the arm as an infrastructure
                # failure and raises instead of rendering the miss lane.
                "outcome": "not_found",
            },
        }

        complete_answer(
            payload,
            turn_id="t1",
            ctx=ctx,
            item={},
            branch_kind="business_query",
            services=AnswerServices(
                mcp_probe=lambda name, args: {"answers": [], "has_result": False},
                family_fetch=lambda query: {"data": []},
            ),
            session_factory=lambda: None,
        )

        fragments = captured["fragments"]
        assert "not_found" in fragments, "the error arm must render the miss lane"
        assert "SRTWC8517" in fragments["not_found"]["escalate_message"]
        # Owner ruling 22 Sep 2026, R6 - `ctx.parse.output` above carries NO `routing`
        # key at all (a bare `parser` dict, the shape `not_found_error_message`'s own
        # belt-and-braces fallback exists for): `domain_hint = "inventory"` must still
        # reach the customer as "warehouse", never the generic "customer_service"
        # literal. Deleting `answer.py`'s own `default_policy()` fallback (kept
        # alongside `turn_runtime.lane_parse_output`'s domain-aware fill, which this
        # direct `complete_answer` call bypasses entirely - no `engine.run_turn`, no
        # `policy` in the loop) turns this assertion red.
        assert "escalate to warehouse team?" in fragments["not_found"]["escalate_message"], (
            fragments["not_found"]["escalate_message"]
        )

    def test_pre_fetch_not_found_arm_still_offers_the_sibling_family(self, monkeypatch) -> None:
        """Owner console defect item 3: `_run_miss_half`'s call site for the PRE-FETCH
        `exit_kind == 'not_found'` arm (`lanes/business/__init__.py` around line 472) does
        not pass `build_result=`, so it defaults to `None` and `_sibling_gate`
        (`miss_suggest.py:1130`, `if build_result is None: return False`) short-circuits -
        even though every OTHER `_sibling_gate` condition is satisfied.

        This is the arm a "check eta <code>" turn for a partially-typed variant code
        actually takes: the resolver's own fallback tier matches the BASE product
        (SRTWB1542) and the gate synthesises a compatible `product` entity for it
        (`gate.py`'s ambiguous/partial-match synthesis, verbatim reproduced from
        `TestMissLaneProbesAndFamilyFetch.test_not_found_path_probes_and_fetches_the_family_before_offering`'s
        own `resolved`/`gate` shapes) while marking the token unresolved and the gate
        failed - which is exactly `if3_miss`'s clause 1 (`gate_passed is False`), landing
        on `exit_kind == 'not_found'`, NOT the post-fetch `dispatch == 'miss_suggest'` arm
        (which already passes `build_result` correctly at line 570 and is not the one at
        fault here).
        """
        from app.services.chatbot import engine as engine_mod
        from app.services.chatbot.lanes.business import complete_answer
        from app.services.chatbot.lanes.business.services import AnswerServices

        captured: dict[str, Any] = {}

        class _Completed:
            reply = {"text": "stub", "quick_replies": []}
            actions: list = []
            session_patch = None
            status = "done"
            stage = "remembered"

        def _complete_turn(turn_id, fragments, *, session_factory, compose_send_action=False, lane_trace=None):
            captured["fragments"] = fragments
            return _Completed()

        monkeypatch.setattr(engine_mod, "complete_turn", _complete_turn)

        ctx = {
            "contact": {"id": "c1"},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "incoming",
                    "user_goal": "eta for SRTWB1542-MG",
                    "entities": [{"raw": "SRTWB1542-MG", "hint": "product"}],
                    "access_levels": [],
                }
            },
            "session": {},
        }
        payload = {
            # The PRE-FETCH miss - `resolve_gate.if3_miss`'s clause 1 (`gate_passed is
            # False`), never a fetch outcome.
            "_exit_kind": "not_found",
            "resolved": {
                "by_entity_type": {},
                "unresolved_tokens": ["SRTWB1542-MG"],
                "resolutions": [
                    {
                        "token": "SRTWB1542-MG",
                        "matches": [
                            {
                                "entity_type": "product",
                                "canonical_code": "SRTWB1542-MG-XL",
                                "uuid": "22222222-2222-2222-2222-222222222222",
                                "match_tier": "prefix",
                            }
                        ],
                    }
                ],
            },
            "gate": {
                "gate_passed": False,
                "gate_reason": "no exact match",
                "gate_debug": {"domain": "incoming"},
                "require_specific": False,
                "compatible_entities": [
                    {"entity_type": "product", "code": "SRTWB1542", "uuid": None},
                ],
            },
            "fetch": {},
        }

        probe_calls: list[tuple[str, dict]] = []
        fetch_calls: list[str] = []

        def mcp_probe(name: str, args: dict) -> dict:
            probe_calls.append((name, args))
            return {"answers": [], "has_result": False}

        def family_fetch(query: str) -> dict:
            fetch_calls.append(query)
            return {"data": [{"canonical_code": "SRTWB1542-MG"}]}

        complete_answer(
            payload,
            turn_id="t1",
            ctx=ctx,
            item={},
            branch_kind="business_query",
            services=AnswerServices(mcp_probe=mcp_probe, family_fetch=family_fetch),
            session_factory=lambda: None,
        )

        assert fetch_calls, (
            "family-fetch never ran - `_sibling_gate` short-circuited on `build_result "
            "is None` even though the gate/product/require_specific conditions all hold"
        )
        fragments = captured["fragments"]
        offer = fragments.get("suggest_offer") or {}
        assert "SRTWB1542-MG" in (offer.get("suggest_response") or ""), (
            "the sibling family must be named in the offer text: "
            f"{offer.get('suggest_response')!r}"
        )
        assert offer.get("suggest_quick_reply"), (
            "the sibling offer must carry a non-empty quick-reply set (Yes/No to escalate)"
        )


# --------------------------------------------------------------------------- #
# AC-607: crossdomain-probe, the SECOND MCP call the answer lane makes on a turn
# (the first is the fetch itself, S6b). `semantic_input` shape is byte-for-byte the
# live node's own expression (export/sub-main-processing-live/nodes not-a-.js-file -
# read from workflow.json's `crossdomain-probe` `workflowInputs.value`).
# --------------------------------------------------------------------------- #


class TestCrossdomainProbe:
    def test_zeroset_active_triggers_exactly_one_probe_with_the_semantic_input_shape(
        self,
    ) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset, run_crossdomain
        from app.services.chatbot.lanes.business.services import AnswerServices

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
            "user_goal": "check stock for SRTWC8517",
            "access_levels": ["Dealer", "End User"],
            "is_active": True,
        }
        resolved = {
            "resolutions": [
                {
                    "token": "SRTWC8517",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC8517",
                            "uuid": "prod-uuid-1",
                            "match_tier": "exact",
                        }
                    ],
                }
            ]
        }
        session_block = {"session_vars": {"variables": {}}}
        validator_result = {
            "answers": [{"fields": [{"label": "Product Code", "value": "SRTOTHER"}]}],
            "response": "Some other stock line.",
        }

        zeroset = crossdomain_zeroset(
            validator_result, parser=parser, resolved=resolved, session_block=session_block
        )
        assert zeroset["_xd"]["active"] is True, "SRTWC8517 was asked for but never returned"

        calls: list[tuple[str, dict]] = []

        def mcp_probe(name: str, args: dict) -> dict:
            calls.append((name, args))
            return {"answers": [], "has_result": False}

        services = AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []})
        run_crossdomain(
            validator_result,
            parser=parser,
            resolved=resolved,
            session_block=session_block,
            entities_names=None,
            services=services,
            contact_id="164838271",
            space_id="900001",
        )

        assert len(calls) == 1, (
            "crossdomain-probe is the answer lane's SECOND mcp call on this turn "
            f"(the fetch itself is the first, S6b) - got {len(calls)} calls: {calls}"
        )
        name, args = calls[0]
        assert name == "crm_incoming_stock_list"
        assert args["semantic_input"] == {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
            "user_goal": "check stock for SRTWC8517",
            "access_levels": ["Dealer", "End User"],
            "contact_id": "164838271",
            "space_id": "900001",
            "is_active": True,
        }

    def test_access_levels_are_the_sorted_intersection_with_entitlement(self) -> None:
        """When the resolver's entitlement aggregate ran, `semantic_input.access_levels`
        is `ctx.entities.name` (the entitlement union) sorted and intersected with the
        parser's own `access_levels` - never the raw entitlement list unfiltered, and
        never the parser's list unsorted."""
        from app.services.chatbot.lanes.business.answer import crossdomain_probe_args

        zeroset = {"active": True, "other_tool": "crm_incoming_stock_list", "origin_domain": "inventory",
                   "probe_entities": [{"uuid": "u1", "entity_type": "product", "code": "X"}]}
        parser = {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
            "user_goal": "g",
            "access_levels": ["Dealer", "End User"],
            "is_active": None,
        }
        args = crossdomain_probe_args(
            zeroset,
            parser=parser,
            entities_names=["Office", "Dealer", "End User"],
            contact_id="1",
            space_id="900001",
        )
        assert args["semantic_input"]["access_levels"] == ["Dealer", "End User"]


class TestCrossdomainRenderRowOrder:
    """`crossdomain-render.js:96,101-102`: quantity desc when ANY row has a quantity,
    otherwise soonest ETA first.

    The incoming direction is the one that has no quantity at all:
    `sorento_crm_mcp/presenters.py::_incoming_list` emits `estimated_arrival_date`
    (label "ETA") and never a `quantity_on_hand` key, so `fieldPref` misses and JS's
    `?? NaN` sends every one of those rows down the ETA branch. Reading the miss as
    `Number(null) === 0` instead would make the quantity branch win for a set that
    carries no quantity, and the rows would come back in the CRM's own order - which
    the node's comment (crossdomain-render.js:88-90) records as jittery between calls.
    """

    @staticmethod
    def _incoming_row(code: str, eta: str) -> dict:
        """One `crm_incoming_stock_list` item, in the presenter's own shape."""
        return {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": code},
                {"key": "estimated_arrival_date", "label": "ETA", "value": eta},
            ]
        }

    def _render(self, rows: list[dict]) -> str:
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        out = crossdomain_render(
            {"items": rows, "has_result": True},
            zeroset={
                "active": True,
                "origin_domain": "inventory",
                "team": "warehouse",
                "missing": [{"code": "SRTWC8517", "_n": "SRTWC8517", "uuid": "u1"}],
            },
            validator={},
        )
        return out["_xdBlock"]["block"]

    def test_rows_with_no_quantity_key_sort_by_soonest_eta(self) -> None:
        rows = [
            self._incoming_row("SRTWC8517", "2026-11-30"),
            self._incoming_row("SRTWC8517", "2026-09-15"),
            self._incoming_row("SRTWC8517", "2026-10-02"),
        ]
        block = self._render(rows)
        order = re_mod.findall(r"\*ETA:\* (\d{4}-\d{2}-\d{2})", block)
        assert order == ["2026-09-15", "2026-10-02", "2026-11-30"], (
            "an absent quantity_on_hand key must read as NaN (JS `?? NaN`), not 0 - "
            "with 0 the quantity branch wins and the incoming rows keep the CRM's "
            f"own jittery order: {order}"
        )

    def test_a_present_quantity_still_wins_over_eta(self) -> None:
        """Parity in the other direction: one real quantity anywhere in the set and the
        quantity branch takes it, placeholder rows sorting as 0 (`_qty(b) || 0`)."""
        rows = [
            {
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 2},
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-09-15"},
                ]
            },
            {
                "fields": [
                    {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
                    {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 9},
                    {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-11-30"},
                ]
            },
        ]
        block = self._render(rows)
        assert re_mod.findall(r"\*Quantity On Hand:\* (\d+)", block) == ["9", "2"]


# --------------------------------------------------------------------------- #
# Owner console defect H (owner ruling: "name it, no stock and offer escalation").
# `crossdomain_render`'s "positive facts only" rule (answer.py:711-712, `if not rows:
# continue`) means a requested code with ZERO rows on EITHER side is simply never
# mentioned - the reply names only the codes that had SOMETHING to show. The owner wants
# the code named explicitly ("no stock and no incoming") plus an escalation offer, not
# silence.
# --------------------------------------------------------------------------- #


class TestThirdCodeWithNoStockAndNoIncomingIsNamedWithEscalation:
    @staticmethod
    def _incoming_row(code: str, eta: str) -> dict:
        return {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": code},
                {"key": "estimated_arrival_date", "label": "ETA", "value": eta},
            ]
        }

    def test_the_zero_stock_zero_incoming_code_is_named_and_escalation_is_offered(
        self,
    ) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        # Three requested codes had zero rows in the PRIMARY (stock) query, so all three
        # are in `zeroset.missing`. The crossdomain (incoming) probe found rows for TWO
        # of them; the third, MSK11A-QT, has nothing on either side.
        rows = [
            self._incoming_row("SRTWC8517", "2026-09-15"),
            self._incoming_row("SRTWB7096", "2026-10-02"),
        ]
        out = crossdomain_render(
            {"items": rows, "has_result": True},
            zeroset={
                "active": True,
                "origin_domain": "inventory",
                "team": "warehouse",
                "missing": [
                    {"code": "SRTWC8517", "_n": "SRTWC8517", "uuid": "u1"},
                    {"code": "SRTWB7096", "_n": "SRTWB7096", "uuid": "u2"},
                    {"code": "MSK11A-QT", "_n": "MSK11A-QT", "uuid": "u3"},
                ],
            },
            validator={},
        )
        block = out["_xdBlock"]["block"]

        # Guard: the two positive codes still render exactly as today.
        assert "SRTWC8517" in block and "2026-09-15" in block
        assert "SRTWB7096" in block and "2026-10-02" in block

        assert "MSK11A-QT" in block, (
            f"the third code must be NAMED rather than silently dropped: {block!r}"
        )
        assert "no stock" in block.lower() and "no incoming" in block.lower(), (
            f"MSK11A-QT must be stated as having no stock and no incoming: {block!r}"
        )
        # The offer is written ONCE by `tail/compose.crossdomain_compose` from
        # `block["team"]` (8 Sep 2026): the block itself names the absence only.
        assert "escalate" not in block.lower(), (
            f"the cross-domain block must not carry the offer itself: {block!r}"
        )


# --------------------------------------------------------------------------- #
# AC-607: the miss lane - dym-transform, its probes, family-fetch (never a raw IP,
# H52's family-fetch half; D10), then build-suggest-offer.
# --------------------------------------------------------------------------- #


class TestMissLaneProbesAndFamilyFetch:
    def test_not_found_path_probes_and_fetches_the_family_before_offering(self) -> None:
        """`_sibling_gate` (`sibling-gate`'s own four AND conditions, verbatim) is the ONE
        branch where `family_fetch` AND `mcp_probe` both fire on the same turn - `run_miss_lane`
        docstring's graph: `sibling-gate TRUE -> family-fetch -> sibling-transform ->
        sibling-probe`. It requires an `incoming`-domain gate with a non-uuid product code in
        `compatible_entities`, `require_specific` not True, and a `build_result` whose own
        `has_result` is False - the `dym-transform` branch below it (FALSE) never calls
        `family_fetch` at all, so a gate that fails these four conditions grades the wrong
        branch."""
        from app.services.chatbot.lanes.business.miss_suggest import run_miss_lane
        from app.services.chatbot.lanes.business.services import AnswerServices

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "incoming",
            "entities": [{"hint": "product", "raw": "SRTWC286"}],
            "access_levels": [],
        }
        resolved = {
            "by_entity_type": {},
            "unresolved_tokens": ["SRTWC286"],
            "resolutions": [
                {
                    "token": "SRTWC286",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC286-SH",
                            "uuid": "11111111-1111-1111-1111-111111111111",
                            "match_tier": "prefix",
                        }
                    ],
                }
            ],
        }
        gate = {
            "gate_passed": False,
            "gate_reason": "no exact match",
            "gate_debug": {"domain": "incoming"},
            "require_specific": False,
            "compatible_entities": [
                {"entity_type": "product", "code": "SRTWC286", "uuid": None},
            ],
        }
        not_found_item = {
            "escalate_message": "Could not find product SRTWC286.",
            "is_clarification": False,
        }

        probe_calls: list[tuple[str, dict]] = []
        fetch_calls: list[str] = []

        def mcp_probe(name: str, args: dict) -> dict:
            probe_calls.append((name, args))
            return {"answers": [], "has_result": False}

        def family_fetch(query: str) -> dict:
            fetch_calls.append(query)
            return {"data": [{"canonical_code": "SRTWC286-SH-200"}]}

        services = AnswerServices(mcp_probe=mcp_probe, family_fetch=family_fetch)

        out = run_miss_lane(
            not_found_item,
            parser=parser,
            resolved=resolved,
            gate=gate,
            services=services,
            # `_sibling_gate`'s fourth condition: the fetch genuinely came back empty.
            build_result={"has_result": False},
        )

        assert probe_calls, (
            "the miss lane never probed a did-you-mean candidate through mcp_probe - "
            "dym-probe / promo-dym-probe / sibling-probe are the same seam by domain"
        )
        assert fetch_calls, "family-fetch never ran - the sibling family lookup is skipped"
        assert "SRTWC286" in fetch_calls[0]
        assert out.get("dym_offer") is not None
        assert out["dym_offer"].get("ttl") == 3
        assert out["dym_offer"].get("picked") == []

    def test_a_failed_dym_probe_still_offers_the_did_you_mean(self) -> None:
        """`dym-probe` is the ONE node in `sub-miss-suggest-live/workflow.json` (and the
        same node on the live spine) that carries an `onError`, and it is
        `continueRegularOutput`: the probe fails, an item is still emitted,
        `dym-annotate` runs on it and the customer gets the BARE offer. Unwrapped, an
        MCP timeout here propagates out of `complete_answer` and the turn answers with
        `GENERIC_ERROR_REPLY` instead."""
        from app.services.chatbot.lanes.business.miss_suggest import run_miss_lane
        from app.services.chatbot.lanes.business.services import AnswerServices

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
            "user_goal": "stock for SRTWC286",
            "entities": [{"hint": "product", "raw": "SRTWC286"}],
            "access_levels": [],
        }
        resolved = {
            "by_entity_type": {},
            "unresolved_tokens": ["SRTWC286"],
            "resolutions": [
                {
                    "token": "SRTWC286",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC286-SH",
                            "uuid": "11111111-1111-1111-1111-111111111111",
                            "match_tier": "prefix",
                        }
                    ],
                }
            ],
        }
        # `gate_debug.domain` is inventory, so `sibling-gate` is FALSE and the turn takes
        # the `dym-transform -> dym-probe` leg rather than the family one.
        gate = {
            "gate_passed": False,
            "gate_reason": "no exact match",
            "gate_debug": {"domain": "inventory"},
            "require_specific": False,
            "compatible_entities": [],
        }
        probes: list[str] = []

        def mcp_probe(name: str, args: dict) -> dict:
            probes.append(name)
            raise RuntimeError("MCP read timed out")

        out = run_miss_lane(
            {"escalate_message": "Could not find product SRTWC286.", "is_clarification": False},
            parser=parser,
            resolved=resolved,
            gate=gate,
            services=AnswerServices(mcp_probe=mcp_probe, family_fetch=lambda q: {"data": []}),
            build_result={"has_result": False},
        )

        assert probes, "the did-you-mean probe never ran - this test grades the wrong branch"
        offer = out.get("dym_offer") or {}
        assert [c["code"] for c in offer.get("candidates") or []] == ["SRTWC286-SH"], (
            "a failed dym-probe must still reach the offer (onError: continueRegularOutput)"
        )

    def test_family_fetch_takes_a_query_string_never_a_raw_url(self) -> None:
        """D10/H52 (family-fetch's own httpRequest node hits `https://72.62.195.20/...`
        directly today - the plan's H52 disposition is `fix S6b (config URL)`, and
        family-fetch is the one S6c read that would otherwise repeat the same mistake).
        The seam signature takes a bare query string; the production binding is the
        in-process products service, never a socket the coder has to remember to
        configure."""
        from app.services.chatbot.lanes.business.services import FamilyFetchFn

        sig = inspect.signature(FamilyFetchFn.__call__)
        params = [p for p in sig.parameters if p not in ("self",)]
        assert params == ["query"], (
            f"FamilyFetchFn.__call__ takes {params}, expected just `query` - a URL or "
            "host parameter here is exactly the raw-IP hazard this seam exists to close"
        )


# --------------------------------------------------------------------------- #
# R1 / H1: the demand-quantity answer. `Edit Fields2` stamps `not_allowed_check_stock`
# on the ONE edge `If7`'s TRUE output takes, and `validator` reads it to replace the raw
# stock rows with "Quantity of N for product X can/cannot be fulfilled." Both switch
# positions are graded: with `chatbot_stock_denial_enabled` off no turn can reach the
# arm at all, so nothing is stamped and nothing is rewritten.
# --------------------------------------------------------------------------- #


class TestR1DemandQuantityAnswer:
    @staticmethod
    def _ctx(demand_qty: Any) -> dict:
        return {
            "contact": {
                "id": "ZZT-1",
                "custom_fields": [{"name": "is_allowed_stock", "value": "false"}],
            },
            "text": {"message": {"message": {"type": "text", "text": "5 units of SRTWC8517"}}},
            "session": {"session_vars": {"variables": {}}},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_stock",
                    "domain_hint": "inventory",
                    "entities": [{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
                    "demand_qty": demand_qty,
                }
            },
            "access": {"allowed": True, "decision": "allow"},
            "media": None,
        }

    @staticmethod
    def _lane(ctx: dict, branch_kind: str) -> dict:
        from app.services.chatbot.lanes.business import run_until_exit
        from app.services.chatbot.lanes.business.services import ResolveGateServices

        services = ResolveGateServices(
            access_types=lambda **_: [],
            resolve_entity=validating_resolve_entity(
                lambda body: {"tokens": [], "resolutions": [], "unresolved_tokens": []}
            ),
            probe=lambda **_: None,
        )
        return run_until_exit(
            ctx, {"branch_kind": branch_kind}, branch_kind=branch_kind, services=services
        )

    _ANSWERS = {
        "answers": [
            {"product": "SRTWC8517", "stock_qty": 2},
            {"product": "SRTWC8517", "stock_qty": 1},
        ],
        "response": "Warehouse A: 2\nWarehouse B: 1",
        "has_result": True,
    }

    @staticmethod
    def _decide(ctx: dict, *, stock_denial_enabled: bool, session_factory=None) -> str:
        """AC-1592 port: `head.route.decide` is deleted. Contract 61/62 (stock_denied /
        demand_qty) are decided from the CONTACT's own record before a plan exists at
        all (`turn/route.py`'s own docstring), which the rearch settles in
        `engine.py::run_turn` via `_stock_check_denied`/`_demand_qty_missing` - the
        real seam this test now targets, not a hand-rolled reimplementation of the
        rule.

        S6 ruling (coordinator, 16 Sep 2026): the contact's record is now
        `respond_contacts.chatbot_stock_allowed`, read via a real db session - the
        envelope's `custom_fields` (still set on `ctx["contact"]` above for the OTHER
        assertions this class makes) is no longer read by `_stock_check_denied` at all.
        """
        from sqlalchemy import text

        from app.services.chatbot.contracts import Envelope
        from app.services.chatbot.engine import _demand_qty_missing, _stock_check_denied

        envelope = Envelope(contact=ctx["contact"], message={
            "event_type": "message.received",
            "contact": {"id": ctx["contact"]["id"]},
            "message": {
                "messageId": "ZZT-r1-msg",
                "contactId": ctx["contact"]["id"],
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": ctx["text"]["message"]["message"],
            },
        })
        verdict = ctx["parse"]["output"]
        if stock_denial_enabled:
            assert session_factory is not None, "stock_denial_enabled=True needs session_factory"
            db = session_factory()
            db.execute(
                text(
                    "INSERT INTO respond_contacts (id, respond_io_id, phone_number, "
                    "session_vars, chatbot_stock_allowed) VALUES (gen_random_uuid()::text, "
                    ":cid, '+60000000001', CAST('{}' AS jsonb), false)"
                ),
                {"cid": str(ctx["contact"]["id"])},
            )
            db.commit()
            if _stock_check_denied(db, envelope, verdict):
                return "demand_qty" if _demand_qty_missing(verdict) else "stock_denied"
        return "business_query"

    def test_with_the_switch_on_the_arm_stamps_and_the_answer_is_the_quantity_verdict(
        self, session_factory
    ) -> None:
        from app.services.chatbot.lanes.business.answer import validator

        ctx = self._ctx(5)
        branch_kind = self._decide(ctx, stock_denial_enabled=True, session_factory=session_factory)
        assert branch_kind == "stock_denied"

        payload = self._lane(ctx, branch_kind)["payload"]
        assert payload["not_allowed_check_stock"] is True

        out = validator(
            dict(self._ANSWERS),
            semantic_parser=ctx["parse"],
            not_allowed_check_stock=bool(payload.get("not_allowed_check_stock")),
        )
        assert out["response"] == (
            "Quantity of 5 for product SRTWC8517 cannot be fulfilled. "
            "Total available quantity is 3."
        )

    def test_a_demand_the_stock_covers_is_answered_can_be_fulfilled(self) -> None:
        from app.services.chatbot.lanes.business.answer import validator

        out = validator(
            dict(self._ANSWERS),
            semantic_parser=self._ctx(3)["parse"],
            not_allowed_check_stock=True,
        )
        assert out["response"] == "Quantity of 3 for product SRTWC8517 can be fulfilled."

    def test_with_the_switch_off_nothing_is_stamped_and_the_rows_stand(self) -> None:
        """Default R1 position: the route cannot decide `stock_denied` at all, the
        `business_query` arm carries no stamp, and `validator` leaves the fetched
        response untouched."""
        from app.services.chatbot.lanes.business.answer import validator

        ctx = self._ctx(5)
        branch_kind = self._decide(ctx, stock_denial_enabled=False)
        assert branch_kind == "business_query"

        payload = self._lane(ctx, branch_kind)["payload"]
        assert payload.get("not_allowed_check_stock") is None

        out = validator(
            dict(self._ANSWERS),
            semantic_parser=ctx["parse"],
            not_allowed_check_stock=bool(payload.get("not_allowed_check_stock")),
        )
        assert out["response"] == self._ANSWERS["response"]
        assert out["is_valid"] is True


# --------------------------------------------------------------------------- #
# AC-609 / H45: did-you-mean never re-offers a row the answer already showed.
#
# ONE outcome-level predicate, and it is the LIVE one: `build-suggest-offer.js:288-323`'s
# answered-token rule. "Already shown" means "queried this turn", read off
# `gate.compatible_entities` by UUID - outside REQUIRE_SPECIFIC domains the gate lifts
# every compatible match of an ambiguous token into that list and the fetch queries them
# all, so the miss that reaches the offer is the CRM's ANSWER over those candidates.
# Applied per CANDIDATE, not per token: a candidate the gate never lifted is still a real
# suggestion, and a token left with none is named in `dym_answered_tokens` rather than
# silently dropped. Graded through `run_miss_lane`, the lane's own entry point, so the
# rule is proven where the offer is actually built.
# --------------------------------------------------------------------------- #


class TestH45NoReofferOfAnsweredRows:
    _UF = "11111111-1111-1111-1111-111111111111"
    _200 = "22222222-2222-2222-2222-222222222222"
    _PARSER = {
        "message_type": "business_query",
        "intent_hint": "check_stock",
        "domain_hint": "inventory",
        "user_goal": "stock for SRTWC8517",
        "entities": [{"hint": "product", "raw": "SRTWC8517"}],
        "access_levels": [],
    }

    @property
    def _resolved(self) -> dict:
        return {
            "by_entity_type": {},
            "unresolved_tokens": ["SRTWC8517"],
            "resolutions": [
                {
                    "token": "SRTWC8517",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC8517-SH-UF",
                            "uuid": self._UF,
                            "match_tier": "prefix",
                        },
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC8517-SH-200",
                            "uuid": self._200,
                            "match_tier": "prefix",
                        },
                    ],
                }
            ],
        }

    def _offer(self, compatible_entities: list[dict]) -> dict:
        from app.services.chatbot.lanes.business.miss_suggest import run_miss_lane
        from app.services.chatbot.lanes.business.services import AnswerServices

        return run_miss_lane(
            {"escalate_message": "Could not find product SRTWC8517.", "is_clarification": False},
            parser=self._PARSER,
            resolved=self._resolved,
            gate={
                "gate_passed": False,
                "gate_reason": "no exact match",
                "gate_debug": {"domain": "inventory"},
                "require_specific": False,
                "compatible_entities": compatible_entities,
            },
            services=AnswerServices(
                mcp_probe=lambda name, args: {"answers": [], "has_result": False},
                family_fetch=lambda query: {"data": []},
            ),
            build_result={"has_result": False},
        )

    @staticmethod
    def _entity(uuid: str, code: str) -> dict:
        return {"entity_type": "product", "uuid": uuid, "code": code}

    def test_a_candidate_the_answer_already_queried_is_not_offered_again(self) -> None:
        out = self._offer([self._entity(self._UF, "SRTWC8517-SH-UF")])
        codes = [c["code"] for c in (out.get("dym_offer") or {}).get("candidates") or []]
        assert codes == ["SRTWC8517-SH-200"], (
            "SRTWC8517-SH-UF was queried this turn (its uuid is in compatible_entities), "
            f"so the offer must not hand it back: {codes}"
        )

    def test_a_token_whose_candidates_were_all_queried_leaves_the_offer_named(self) -> None:
        out = self._offer(
            [
                self._entity(self._UF, "SRTWC8517-SH-UF"),
                self._entity(self._200, "SRTWC8517-SH-200"),
            ]
        )
        assert out.get("dym_offer") is None, "the whole token was answered - there is nothing to offer"
        assert out.get("dym_answered_tokens") == ["SRTWC8517"], (
            "a token that loses every candidate is NAMED, never a silent drop"
        )

    def test_an_empty_compatible_entities_excludes_nothing(self) -> None:
        """UAC SR-U5: a genuine miss is never silenced. Nothing was queried, so both
        candidates are still real suggestions."""
        out = self._offer([])
        codes = [c["code"] for c in (out.get("dym_offer") or {}).get("candidates") or []]
        assert codes == ["SRTWC8517-SH-UF", "SRTWC8517-SH-200"]
        assert out.get("dym_answered_tokens") is None


# --------------------------------------------------------------------------- #
# H39: two product tokens whose did-you-mean candidates overlap must each keep at
# least one suggestion. `dym-transform-partial.js` / `dym-transform.js` apply a GLOBAL
# cap (`dym_candidate_codes.slice(0, _cap)`) over the concatenated candidate list, which
# can zero out a whole token's block if an earlier token's candidates already filled
# the cap - reproduce that first (plan hazard table: "S6c: reproduce first, fix as
# registered divergence if owner says go"), and the FIXED behaviour is asserted
# `xfail(strict=False)` under the hazard id so the fix is visible the day it lands.
# --------------------------------------------------------------------------- #


class TestH39CrossTokenDedupe:
    _PARSER = {
        "message_type": "business_query",
        "domain_hint": "inventory",
        "entities": [
            {"hint": "product", "raw": "SRTWC286"},
            {"hint": "product", "raw": "SRTWB247"},
        ],
    }
    _RESOLVED = {
        "unresolved_tokens": ["SRTWC286", "SRTWB247"],
        "resolutions": [
            {
                "token": "SRTWC286",
                "matches": [
                    {"entity_type": "product", "canonical_code": f"SRTWC286-{i}", "uuid": f"u286-{i}"}
                    for i in range(1, 12)  # 11 siblings: enough to fill a small global cap alone
                ],
            },
            {
                "token": "SRTWB247",
                "matches": [
                    {"entity_type": "product", "canonical_code": "SRTWB247-SH", "uuid": "u247-1"}
                ],
            },
        ],
    }
    _GATE = {"gate_passed": False, "gate_reason": "no exact match"}
    _NOT_FOUND_ITEM = {"escalate_message": "Could not find these products.", "is_clarification": False}

    def test_js_cap_can_zero_a_tokens_block(self) -> None:
        """Today's behaviour (parity, D8): the global cap is allowed to starve the
        second token entirely. This is the RED-phase parity assertion - it documents
        what the port must reproduce first, and it is expected to need a divergence
        entry once dym-transform is ported (H39)."""
        from app.services.chatbot.lanes.business.miss_suggest import dym_transform

        out = dym_transform(self._NOT_FOUND_ITEM, parser=self._PARSER, resolved=self._RESOLVED)
        codes = set(out.get("dym_candidate_codes") or [])
        assert not codes.issuperset({"SRTWC286-1", "SRTWB247-SH"}), (
            "if this assertion fails, the JS parity behaviour has already changed - "
            "retire this test and keep only the xfail below"
        )

    @pytest.mark.xfail(strict=False, reason="H39: fix is a registered divergence, owner sign-off pending")
    def test_each_token_keeps_at_least_one_suggestion_fixed_behaviour(self) -> None:
        from app.services.chatbot.lanes.business.miss_suggest import dym_transform

        out = dym_transform(self._NOT_FOUND_ITEM, parser=self._PARSER, resolved=self._RESOLVED)
        codes = out.get("dym_candidate_codes") or []
        assert any(c.startswith("SRTWC286") for c in codes), "SRTWC286's own token lost every suggestion"
        assert any(c.startswith("SRTWB247") for c in codes), "SRTWB247's own token lost every suggestion"


# --------------------------------------------------------------------------- #
# H40: duplicate product codes across companies ("twins"). `dym-annotate.js`'s own F1
# amendment (owner ruling, 2026-09-01) already fixes this in the n8n body - has/no
# certificate is stamped only when the (code, company) composite join finds EXACTLY
# ONE owner; more than one candidate sharing a code with no company match on the
# answer row is `dym_ambiguous_codes`, rendered bare, never guessed. The port
# reproduces this AS THE BASELINE (not a pending hazard - the JS already has the fix).
# --------------------------------------------------------------------------- #


class TestH40CertTwinLabelling:
    def _row_keys(self) -> list[dict]:
        return [
            {"uuid": "uuid-sorento", "code": "MWC-SC08B", "company": "Sorento"},
            {"uuid": "uuid-mocha", "code": "MWC-SC08B", "company": "Mocha"},
        ]

    def test_has_certificate_only_when_the_company_join_is_unambiguous(self) -> None:
        from app.services.chatbot.lanes.business.miss_suggest import dym_annotate

        item = {
            "probe_predicate": "row_present_with_type",
            "probe_uuid_keyed": True,
            "dym_probe_row_keys": self._row_keys(),
            "answers": [
                {
                    "fields": [
                        {"label": "Product Code", "value": "MWC-SC08B"},
                        {"label": "Company", "value": "Sorento"},
                        {"label": "Attachment Type", "value": "Certification"},
                    ]
                }
            ],
        }
        out = dym_annotate(item)
        assert out["dym_available_codes"] == ["uuid-sorento"], (
            "only Sorento's twin was probed as having a certificate - Mocha's twin must "
            "not inherit the label"
        )
        assert out.get("dym_ambiguous_codes") == []

    def test_no_company_match_and_more_than_one_owner_is_ambiguous_not_guessed(self) -> None:
        from app.services.chatbot.lanes.business.miss_suggest import dym_annotate

        item = {
            "probe_predicate": "row_present_with_type",
            "probe_uuid_keyed": True,
            "dym_probe_row_keys": self._row_keys(),
            "answers": [
                {
                    "fields": [
                        {"label": "Product Code", "value": "MWC-SC08B"},
                        {"label": "Attachment Type", "value": "Certification"},
                        # no Company field on this row - cannot join to one twin
                    ]
                }
            ],
        }
        out = dym_annotate(item)
        assert out["dym_available_codes"] == [], (
            "an ambiguous (code, company) join must never mark EVERY owner as having a "
            "certificate - that is exactly the false-positive F1 was written to close"
        )
        assert "mwc-sc08b" in [c.lower() for c in (out.get("dym_ambiguous_codes") or [])]


# --------------------------------------------------------------------------- #
# H16: `not_found_error_message` iterates only ENTITY-TYPE keys of
# `resolved.by_entity_type` - a metadata key injected onto the same dict must never
# reach the customer-facing reply text.
# --------------------------------------------------------------------------- #


class TestH16ByEntityTypeIterationIsEntityKeysOnly:
    def test_a_metadata_key_on_by_entity_type_never_reaches_the_reply(self) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        resolved = {
            "by_entity_type": {
                "product": [],
                # not a real entity type - if the node iterates blindly this leaks in.
                "__debug_meta__": {"leaked": True, "internal": "should never be spoken"},
            },
            "tokens": ["SRT123"],
            "unresolved_tokens": ["SRT123"],
        }
        parser = {
            "domain_hint": "master_products",
            "entities": [{"hint": "product", "raw": "SRT123"}],
            "routing": {"suggested_team": "purchasing"},
            "access_levels": [],
        }
        gate = {"gate_passed": False, "gate_reason": "no match", "gate_debug": {"allowed_lookup": []}}

        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        message = out.get("escalate_message") or ""
        assert "__debug_meta__" not in message
        assert "leaked" not in message
        assert "internal" not in message


# --------------------------------------------------------------------------- #
# Owner console defect A (items 2 + 15): `disp_by_uuid` at answer.py:1846-1875 is built
# from a FIXED key list (product_name, customer_name, debtor_name, type_name,
# description). An `inbound_shipment` display (entity_resolver.py:1363-1369) carries
# `shipment_number` / `shipping_container_number` instead, and 11 of 17 real
# `inbound_shipments` rows have a null `shipment_number` - so the by_type ladder at
# answer.py:1914-1932 falls through disp_by_uuid -> compat.code -> compat.uuid and
# prints the raw uuid: "inbound_shipment: ecfdaf8f-... (Mocha)".
# --------------------------------------------------------------------------- #


class TestUuidNeverPrintedInReplyText:
    _UUID_RE = re_mod.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re_mod.I)

    def test_inbound_shipment_with_only_a_container_number_names_the_container_not_the_uuid(
        self,
    ) -> None:
        """The entity has NO shipment_number (the null case) but DOES have a
        shipping_container_number - `disp_by_uuid` must read that field, not fall
        through to the raw uuid."""
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        ship_uuid = "ecfdaf8f-1111-4a11-9a11-111111111111"
        match = {
            "entity_type": "inbound_shipment",
            "uuid": ship_uuid,
            "canonical_code": None,
            "display": {
                "shipment_number": None,
                "shipping_container_number": "TGHU6295708",
            },
        }
        resolved = {
            "tokens": ["TGHU6295708"],
            "unresolved_tokens": [],
            "resolutions": [{"token": "TGHU6295708", "matches": [match]}],
            "intersection": [match],
            "by_entity_type": {"inbound_shipment": [match]},
        }
        parser = {
            "domain_hint": "inbound_shipment",
            "entities": [{"hint": "inbound_shipment", "raw": "TGHU6295708"}],
            "routing": {"suggested_team": "logistics"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": ship_uuid, "entity_type": "inbound_shipment", "code": None}
            ],
        }

        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        found_summary = out.get("found_summary") or ""
        escalate_message = out.get("escalate_message") or ""
        rendered = found_summary + "\n" + escalate_message

        assert not self._UUID_RE.search(rendered), (
            "the reply must never print the raw entity uuid - it did here: "
            f"{rendered!r}"
        )
        assert "TGHU6295708" in rendered, (
            "the container number is the only human-readable identifier on this "
            f"entity and must be what is shown: {rendered!r}"
        )

    def test_an_entity_with_no_code_and_an_empty_display_is_dropped_not_shown_as_a_uuid(
        self,
    ) -> None:
        """No match resolves this compatible entity at all (empty display, no
        canonical_code) - the candidate must be DROPPED from the found breakdown,
        never fall through to its raw uuid."""
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        blank_uuid = "ecfdaf8f-2222-4a22-9a22-222222222222"
        resolved = {
            "tokens": [],
            "unresolved_tokens": [],
            "resolutions": [],
            "intersection": [],
            "by_entity_type": {},
        }
        parser = {
            "domain_hint": "inbound_shipment",
            "entities": [],
            "routing": {"suggested_team": "logistics"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": blank_uuid, "entity_type": "inbound_shipment", "code": None}
            ],
        }

        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        found_summary = out.get("found_summary") or ""
        escalate_message = out.get("escalate_message") or ""
        rendered = found_summary + "\n" + escalate_message

        assert not self._UUID_RE.search(rendered), (
            "an entity with no code and no usable display name must be dropped from "
            f"the breakdown, never surfaced as its raw uuid: {rendered!r}"
        )

    def test_real_turn_shape_two_compat_entities_product_and_inbound_shipment_multi_company(
        self,
    ) -> None:
        """Reproduces the exact turn (3551290e) that surfaced the bug: a CARRIED
        inbound_shipment entity (current_message: false) sits alongside a resolved
        product in the same multi-company breakdown. Live printed
        "inbound_shipment: 544400ef-... (Sorento)" next to
        "product: IBKS7245-NG-BL (Mocha)" - the fix must name the container instead."""
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        product_uuid = "544400ef-aaaa-4aaa-9aaa-aaaaaaaaaaaa"
        shipment_uuid = "544400ef-bbbb-4bbb-9bbb-bbbbbbbbbbbb"
        product_match = {
            "entity_type": "product",
            "uuid": product_uuid,
            "canonical_code": "IBKS7245-NG-BL",
            "company_name": "Mocha",
            "display": {},
        }
        shipment_match = {
            "entity_type": "inbound_shipment",
            "uuid": shipment_uuid,
            "canonical_code": None,
            "company_name": "Sorento",
            "display": {
                "shipment_number": None,
                "shipping_container_number": "DFSU6642819",
            },
        }
        resolved = {
            "tokens": ["IBKS7245-NG-BL", "DFSU6642819"],
            "unresolved_tokens": [],
            "resolutions": [
                {"token": "IBKS7245-NG-BL", "matches": [product_match]},
                {"token": "DFSU6642819", "matches": [shipment_match]},
            ],
            "intersection": [product_match, shipment_match],
            "by_entity_type": {
                "product": [product_match],
                "inbound_shipment": [shipment_match],
            },
        }
        parser = {
            "domain_hint": "inventory",
            "entities": [
                {"hint": "product", "raw": "IBKS7245-NG-BL"},
                {"hint": "inbound_shipment", "raw": "DFSU6642819"},
            ],
            "routing": {"suggested_team": "warehouse"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": product_uuid, "entity_type": "product", "code": "IBKS7245-NG-BL"},
                {"uuid": shipment_uuid, "entity_type": "inbound_shipment", "code": None},
            ],
        }

        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        found_summary = out.get("found_summary") or ""
        escalate_message = out.get("escalate_message") or ""
        rendered = found_summary + "\n" + escalate_message

        assert not self._UUID_RE.search(rendered), (
            f"no uuid must appear anywhere in the reply: {rendered!r}"
        )
        assert "inbound shipment: DFSU6642819 (Sorento)" in rendered, (
            "the carried inbound_shipment entity must be named by its container "
            f"number, qualified by company (multi-company turn): {rendered!r}"
        )
        assert "product: IBKS7245-NG-BL (Mocha)" in rendered, (
            f"the product bullet must be unaffected by the fix: {rendered!r}"
        )


# --------------------------------------------------------------------------- #
# H22 / H23: a did-you-mean offer's carried PICKS only ride into a cross-domain read
# when the offer's own domain agrees with the turn's current domain. A promotion-thread
# offer must not pollute an inventory-domain crossdomain probe.
#
# The guard is `sub-main-processing-live`'s `_offerDomainOk` ("DOMAIN GUARD 2026-09-01"),
# and the ACTIVE spine's copy of the node does NOT carry it, so this is a registered CRM
# divergence (`divergences.CROSSDOMAIN_DYM_OFFER_DOMAIN_GUARD`, with both shas) and these
# are the tests it is registered against. No capture grades it: all five predate it.
# --------------------------------------------------------------------------- #


class TestH22H23DymOfferDomainCleared:
    def test_a_promotion_threads_carried_pick_is_not_read_on_an_inventory_turn(self) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        parser = {"message_type": "business_query", "domain_hint": "inventory"}
        resolved = {}
        session_block = {
            "session_vars": {
                "variables": {"dym_offer": {"domain": "promotion", "picked": ["SRT-OLD-PICK"]}}
            }
        }
        validator_result = {"answers": [], "response": "no rows"}

        out = crossdomain_zeroset(
            validator_result, parser=parser, resolved=resolved, session_block=session_block
        )
        requested = out["_xd"]["requested"]
        assert "SRT-OLD-PICK" not in requested, (
            "a pick carried from a PROMOTION-domain offer must not name a product on an "
            "inventory-domain crossdomain read - the offer's own domain guards it"
        )

    def test_a_same_domain_carried_pick_is_read(self) -> None:
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset

        parser = {"message_type": "business_query", "domain_hint": "inventory"}
        resolved = {}
        session_block = {
            "session_vars": {
                "variables": {"dym_offer": {"domain": "inventory", "picked": ["SRT-SAME-DOMAIN"]}}
            }
        }
        validator_result = {"answers": [], "response": "no rows"}

        out = crossdomain_zeroset(
            validator_result, parser=parser, resolved=resolved, session_block=session_block
        )
        requested = out["_xd"]["requested"]
        assert "SRT-SAME-DOMAIN" in requested


# --------------------------------------------------------------------------- #
# D14: a dry-run turn still runs every probe and family_fetch (parity - a read costs
# nothing); the answer lane never accepts or touches a database session at all - the
# only writer on the whole turn path is the S2 tail.
# --------------------------------------------------------------------------- #


class TestD14DryRunReadsButWritesNothing:
    def test_no_answer_lane_function_takes_a_db_session(self) -> None:
        from app.services.chatbot.lanes.business import answer, miss_suggest, sub_answer

        offenders = []
        for module in (answer, sub_answer, miss_suggest):
            for name, obj in vars(module).items():
                if name.startswith("_") or not callable(obj):
                    continue
                try:
                    sig = inspect.signature(obj)
                except (TypeError, ValueError):
                    continue
                if "db" in sig.parameters or "session" in sig.parameters:
                    offenders.append(f"{module.__name__}.{name}{sig}")
        assert not offenders, (
            "the S6c answer lane must stay read-only via `services` (D10) - a db/session "
            f"parameter here means a write path snuck into the lane the tail (S2) owns: "
            f"{offenders}"
        )

    def test_dry_run_still_exercises_every_probe_and_the_family_fetch(self) -> None:
        """D14: `dry_run=True` suppresses WRITES, never READS - the plan is explicit
        that a test turn does zero writes but the would-be state is still computed, so
        every probe this turn would have made still runs."""
        from app.services.chatbot.lanes.business.answer import crossdomain_zeroset, run_crossdomain
        from app.services.chatbot.lanes.business.services import AnswerServices

        parser = {
            "message_type": "business_query",
            "intent_hint": "check_stock",
            "domain_hint": "inventory",
            "user_goal": "g",
            "access_levels": [],
            "is_active": None,
        }
        resolved = {
            "resolutions": [
                {
                    "token": "SRTWC8517",
                    "matches": [
                        {
                            "entity_type": "product",
                            "canonical_code": "SRTWC8517",
                            "uuid": "prod-uuid-1",
                            "match_tier": "exact",
                        }
                    ],
                }
            ]
        }
        session_block = {"session_vars": {"variables": {}}}
        validator_result = {"answers": [], "response": "no rows"}
        zeroset = crossdomain_zeroset(
            validator_result, parser=parser, resolved=resolved, session_block=session_block
        )
        assert zeroset["_xd"]["active"] is True

        calls: list[str] = []
        services = AnswerServices(
            mcp_probe=lambda name, args: calls.append("probe") or {"answers": []},
            family_fetch=lambda q: calls.append("fetch") or {"data": []},
        )
        run_crossdomain(
            validator_result,
            parser=parser,
            resolved=resolved,
            session_block=session_block,
            entities_names=None,
            services=services,
            contact_id="1",
            space_id="900001",
            dry_run=True,
        )
        assert "probe" in calls, "a dry run must not skip the crossdomain probe - only writes are suppressed"


# --------------------------------------------------------------------------- #
# Capacity: never hold a database session across an MCP probe call. D10's own
# precedent (`lanes/business/services.py::_probe`, S6a) is that the probe seam takes
# NO session parameter at all - the same must hold for `mcp_probe`.
# --------------------------------------------------------------------------- #


class TestNoSessionAcrossProbeCalls:
    def test_mcp_probe_protocol_takes_no_database_session(self) -> None:
        from app.services.chatbot.lanes.business.services import McpProbeFn

        sig = inspect.signature(McpProbeFn.__call__)
        params = set(sig.parameters) - {"self"}
        assert "db" not in params and "session" not in params, (
            "mcp_probe is a network call to the MCP server (D10) - a db/session "
            "parameter here is the same hold-a-connection-across-I/O hazard the plan's "
            f"96/100-connection incident is evidence against; got parameters {params}"
        )

    def test_family_fetch_protocol_takes_no_database_session(self) -> None:
        """`family_fetch`'s PRODUCTION binding legitimately opens its own session (it
        reads the products table), but the SEAM signature itself must not thread one
        in from the caller - the caller is the answer lane, which D14's own test above
        proves never holds one."""
        from app.services.chatbot.lanes.business.services import FamilyFetchFn

        sig = inspect.signature(FamilyFetchFn.__call__)
        params = set(sig.parameters) - {"self"}
        assert "db" not in params and "session" not in params


# --------------------------------------------------------------------------- #
# D11: everything after the parser works on structured state. No regex/fuzzy match
# over raw customer text or a previous reply string in the three new S6c modules.
# --------------------------------------------------------------------------- #


def test_no_raw_text_regex_in_answer_modules() -> None:
    """D11. A line that reproduces an EXISTING n8n text-sniffing site for parity (the
    plan's own text-sniffing inventory) is allowed if it carries the marker comment
    `# D11-reproduced` naming why; anything else is a new regex/fuzzy match over raw
    text, which D11 forbids outright for anything ported after the parser."""
    root = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "chatbot"
        / "lanes"
        / "business"
    )
    module_names = ("answer.py", "sub_answer.py", "miss_suggest.py")
    missing = [name for name in module_names if not (root / name).exists()]
    assert not missing, f"S6c modules not created yet: {missing}"

    forbidden = re_mod.compile(r"\bre\.(search|match|fullmatch|findall|finditer|sub|subn)\(")
    offenders: list[str] = []
    for name in module_names:
        text = (root / name).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if forbidden.search(line) and "# D11-reproduced" not in line:
                offenders.append(f"{name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "new `re.*` call over raw text in the S6c answer lane, not marked "
        "`# D11-reproduced` (a named parity reproduction of an existing n8n "
        "text-sniffing site):\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# Owner console defect D, REFRAMED TWICE.
#
# (owner rev 3, turn evidence) NOT a same-attachment duplicate: two DIFFERENT presigned
# files legitimately share a filename under two different companies, and neither may be
# dropped or read as the other.
#
# (review of #700, 6 Sep 2026) The first fix keyed on `entry["id"]` and `entry["company"]`,
# and the PRODUCER emits neither. `sorento_crm_mcp/presenters.py::attach` builds exactly
# `{url, filename, mimeType, attachmentType[, uploadedAt]}` - measured over 7402 attachment
# entries in the capture corpus, zero with an id or a company - so that fix was dead code on
# real data. These tests therefore use the REAL shapes and nothing else:
#
#   * the value on `outcome_fragment['central-exchange']` is the ANSWER ENVELOPE, and the
#     list the executor sends from is `envelope.attachments` (`sub-send-attachments`' own
#     `central-exchange` stub: `const a = n.first().json.attachments`);
#   * identity is the `url` - the key the executor's `Remove Duplicates` node already uses;
#   * the company is not on the entry, so it comes from the ROW that produced the file. The
#     rows here are copied from capture `rs8a-t4-tierask-misroute.json`, which is the
#     collision in the wild: one promotion flyer filename, two urls, a Mocha row and a
#     Sorento row carrying that filename as their own value.
# --------------------------------------------------------------------------- #


class TestAttachmentDedupeAndFilenameDisambiguation:
    @staticmethod
    def _row(company: str, value: str) -> dict:
        return {
            "fields": [
                {"key": "company_name", "label": "Company", "value": company},
                {"key": "promotion", "label": "Promotion", "value": value},
            ]
        }

    def test_the_same_url_reachable_twice_is_sent_once(self) -> None:
        from app.services.chatbot.engine import _attachments_src

        entry = {
            "url": "https://cdn-sorento.com/promotion/7e5155f1/A3 Flyer.pdf",
            "filename": "A3 Flyer.pdf",
            "mimeType": "application/pdf",
            "attachmentType": "Promotion",
        }
        answer = {
            "outcome_fragment": {
                "central-exchange": {
                    "response": "Here you go.",
                    "items": [self._row("Mocha", "A3 Flyer.pdf")],
                    "attachments": [entry, dict(entry)],
                }
            }
        }
        out = _attachments_src(answer)
        assert isinstance(out, dict), (
            "the value handed back must stay the ENVELOPE the executor reads "
            f"`.attachments` off, not a bare list: {out!r}"
        )
        assert len(out["attachments"]) == 1, (
            "the SAME url reachable twice (e.g. under two company scopes) must be sent "
            f"once, not once per reachable path: {out['attachments']!r}"
        )

    def test_two_files_sharing_a_filename_are_labelled_from_their_own_rows(self) -> None:
        """The real collision, from capture `rs8a-t4-tierask-misroute.json`: one flyer name,
        two urls, and two rows - Mocha's and Sorento's - each carrying that filename as its
        own value. Both files must send, and each must read as its own company's."""
        from app.services.chatbot.engine import _attachments_src

        name = "MOCHA A3 Flyer 03082026_compressed.pdf"
        answer = {
            "outcome_fragment": {
                "central-exchange": {
                    "response": "Here are the promotions.",
                    "items": [self._row("Mocha", name), self._row("Sorento", name)],
                    "attachments": [
                        {
                            "url": f"https://cdn-sorento.com/promotion/7e5155f1/{name}",
                            "filename": name,
                            "mimeType": "application/pdf",
                            "attachmentType": "Promotion",
                        },
                        {
                            "url": f"https://cdn-sorento.com/promotion/3c434a77/{name}",
                            "filename": name,
                            "mimeType": "application/pdf",
                            "attachmentType": "Promotion",
                        },
                    ],
                }
            }
        }
        out = _attachments_src(answer)["attachments"]
        assert len(out) == 2, (
            f"both distinct files must be sent, never collapsed by filename alone: {out!r}"
        )
        assert [a["filename"] for a in out] == [
            "MOCHA A3 Flyer 03082026_compressed (Mocha).pdf",
            "MOCHA A3 Flyer 03082026_compressed (Sorento).pdf",
        ], (
            "each file must be qualified with the company of the ROW that produced it, in "
            f"row order, and the label must sit BEFORE the extension: {out!r}"
        )

    def test_a_packing_list_is_claimed_by_the_row_whose_container_it_is_named_after(
        self,
    ) -> None:
        """The owner's own example: "<container> - WH.xlsx" under two companies. The row
        value is CONTAINED in the filename rather than equal to it, which is how a packing
        list is named."""
        from app.services.chatbot.engine import _attachments_src

        def row(company: str) -> dict:
            return {
                "fields": [
                    {"key": "company_name", "label": "Company", "value": company},
                    {"key": "shipping_container_number", "label": "Container", "value": "TLLU4618098"},
                ]
            }

        answer = {
            "outcome_fragment": {
                "central-exchange": {
                    "items": [row("Sorento"), row("Mocha")],
                    "attachments": [
                        {"url": "s3://sorento/pending", "filename": "TLLU4618098 - WH.xlsx"},
                        {"url": "s3://mocha/allocated", "filename": "TLLU4618098 - WH.xlsx"},
                    ],
                }
            }
        }
        out = _attachments_src(answer)["attachments"]
        assert [a["filename"] for a in out] == [
            "TLLU4618098 - WH (Sorento).xlsx",
            "TLLU4618098 - WH (Mocha).xlsx",
        ], out

    def test_an_unattributable_collision_is_left_exactly_as_it_arrived(self) -> None:
        """No row claims the filename (35 of the corpus's 91 collisions carry no rows at
        all), so nothing establishes which company either file belongs to. Labelling one of
        them "Mocha" would be an assertion the data does not support - both go as they are,
        and neither is dropped."""
        from app.services.chatbot.engine import _attachments_src

        answer = {
            "outcome_fragment": {
                "central-exchange": {
                    "items": [],
                    "attachments": [
                        {"url": "s3://a/IAAU1689748 - WH.xlsx", "filename": "IAAU1689748 - WH.xlsx"},
                        {"url": "s3://b/IAAU1689748 - WH.xlsx", "filename": "IAAU1689748 - WH.xlsx"},
                    ],
                }
            }
        }
        out = _attachments_src(answer)["attachments"]
        assert len(out) == 2
        assert {a["filename"] for a in out} == {"IAAU1689748 - WH.xlsx"}

    def test_the_casual_lane_envelope_passes_through_untouched(self) -> None:
        """`central-exchange` on the low-signal lane is `{response}` with no attachments at
        all, and the promotion answer's envelope carries other keys this must not disturb."""
        from app.services.chatbot.engine import _attachments_src

        central = {"response": "Hi! How can I help?"}
        assert _attachments_src({"outcome_fragment": {"central-exchange": central}}) == central
        assert _attachments_src({}) is None


# --------------------------------------------------------------------------- #
# Owner ruling 10 Sep 2026 REVERSES the 6 Sep 2026 ruling below (kept as history: the
# G2 pass had wanted the estimated delivery date SAID alongside the status). Measured
# fact since: `orders.estimated_delivery_date` is not a real promise - the import stamps
# it as `order_date + 2 business days` (`order_service.py` ~2824) on every master row, not
# a carrier-confirmed date. It may still show in the CRM UI (order list / detail), but it
# must never reach the chatbot / turn API - `not_found_error_message`'s delivered-status
# arm must name the status and stop there: "Order <code> (<customer>) hasn't been
# delivered yet - current status: <status>. Would you like me to escalate to <team>
# team?", with no "(estimated delivery <date>)" suffix at all.
# --------------------------------------------------------------------------- #


class TestStatusAwareMissMessageOmitsTheEtaDate:
    def test_a_delivered_status_filter_miss_names_status_and_omits_estimated_delivery_date(
        self,
    ) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        order_uuid = "33333333-3333-4333-9333-333333333333"
        order_match = {
            "entity_type": "order",
            "uuid": order_uuid,
            "canonical_code": "DO12345",
            "display": {
                "customer_name": "ACME Sdn Bhd",
                "status": "processing",
                "estimated_delivery_date": "2026-09-10",
            },
        }
        resolved = {
            "tokens": ["DO12345"],
            "unresolved_tokens": [],
            "resolutions": [{"token": "DO12345", "matches": [order_match]}],
            "intersection": [order_match],
            "by_entity_type": {"order": [order_match]},
        }
        parser = {
            "domain_hint": "order",
            "order_status": "delivered",
            "entities": [{"hint": "order", "raw": "DO12345"}],
            "routing": {"suggested_team": "customer_service"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": order_uuid, "entity_type": "order", "code": "DO12345"},
            ],
        }

        # DO-ASK-SIMPLIFY security B1: the status is named only with its reveal grant.
        out = not_found_error_message(
            {}, parser=parser, resolved=resolved, gate=gate, granted_keys=["delivery_orders.status"]
        )
        message = out.get("escalate_message") or ""

        # AC-1863: a single resolved order must be byte-identical to before the
        # AC-1860/AC-1861/AC-1862 fix (the escalate question stays on the SAME line,
        # a space away, never a newline) - pinned exactly, not just by substring.
        assert message == (
            "Order DO12345 (ACME Sdn Bhd) hasn't been delivered yet - current status: "
            "processing. Would you like me to escalate to customer service team?"
        ), message

        assert "hasn't been delivered yet" in message
        assert "current status: processing" in message, (
            f"the current status must be named: {message!r}"
        )
        assert "2026-09-10" not in message, (
            "owner ruling 10 Sep 2026: the stamped estimated delivery date is not a real "
            f"promise and must not reach the turn output: {message!r}"
        )
        assert "estimated delivery" not in message, (
            f"no estimated-delivery phrasing at all in the miss message: {message!r}"
        )


# --------------------------------------------------------------------------- #
# PLAN-chatbot-order-status-all-orders-23sep / AC-1860..1865 (prod, 23 Sep 2026, contact
# 482766833, turn 48): "STATUS DELIVERY / PS202609-0374 / PS202609-0398 / PS202609-0410"
# resolved THREE orders (`gate.compatible_entities` had three `customer_order` rows) but
# the status-filter-aware branch above took the FIRST match only and named one order,
# silently dropping the other two. The fix loops every resolved order, deduped by uuid,
# in `gate.compatible_entities` order, one line each, ONE escalate question at the end.
# --------------------------------------------------------------------------- #


class TestStatusAwareMissMessageNamesEveryResolvedOrder:
    def _order_match(self, code: str, uuid: str, customer: str, status: str) -> dict:
        return {
            "entity_type": "customer_order",
            "uuid": uuid,
            "canonical_code": code,
            "display": {"customer_name": customer, "status": status},
        }

    def _matches(self) -> list[dict]:
        return [
            self._order_match(
                "PS202609-0374",
                "33333333-3333-4333-9333-333333333333",
                "MATRIX EXCELCON SDN BHD (PROJECT)",
                "New Order",
            ),
            self._order_match(
                "PS202609-0398",
                "44444444-4444-4444-9444-444444444444",
                "MATRIX EXCELCON SDN BHD (PROJECT)",
                "New Order",
            ),
            self._order_match(
                "PS202609-0410",
                "55555555-5555-4555-9555-555555555555",
                "COMMERCE HOUSE SDN BHD (PROJECT)",
                "New Order",
            ),
        ]

    def _gate(self, matches: list[dict]) -> dict:
        return {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": m["uuid"], "entity_type": "customer_order", "code": m["canonical_code"]}
                for m in matches
            ],
        }

    def _parser(self, order_status: str) -> dict:
        return {
            "domain_hint": "order",
            "order_status": order_status,
            "entities": [
                {"hint": "order", "raw": "PS202609-0374"},
                {"hint": "order", "raw": "PS202609-0398"},
                {"hint": "order", "raw": "PS202609-0410"},
            ],
            "routing": {"suggested_team": "customer_service"},
            "access_levels": [],
        }

    def test_ac_1860_delivered_names_all_three_orders_one_line_each(self) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        matches = self._matches()
        resolved = {
            "tokens": [m["canonical_code"] for m in matches],
            "unresolved_tokens": [],
            "resolutions": [{"token": m["canonical_code"], "matches": [m]} for m in matches],
            "intersection": matches,
            "by_entity_type": {"customer_order": matches},
        }
        out = not_found_error_message(
            {},
            parser=self._parser("delivered"),
            resolved=resolved,
            gate=self._gate(matches),
            granted_keys=["delivery_orders.status"],
        )
        message = out.get("escalate_message") or ""

        assert message.count("hasn't been delivered yet") == 3, message
        assert message.count("Would you like me to escalate") == 1, message
        # the three lines appear in `compatible_entities` order, each naming its own
        # code, customer and status - the earlier bug rendered PS202609-0374 only.
        idx_374 = message.index("PS202609-0374")
        idx_398 = message.index("PS202609-0398")
        idx_410 = message.index("PS202609-0410")
        assert idx_374 < idx_398 < idx_410, message
        for code, customer in (
            ("PS202609-0374", "MATRIX EXCELCON SDN BHD (PROJECT)"),
            ("PS202609-0398", "MATRIX EXCELCON SDN BHD (PROJECT)"),
            ("PS202609-0410", "COMMERCE HOUSE SDN BHD (PROJECT)"),
        ):
            assert f"Order {code} ({customer}) hasn't been delivered yet - current status: New Order." in message, (
                message
            )
        assert message.rstrip().endswith(
            "Would you like me to escalate to customer service team?"
        ), message

    def test_ac_1861_outstanding_names_all_three_orders_one_line_each(self) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        matches = self._matches()
        resolved = {
            "tokens": [m["canonical_code"] for m in matches],
            "unresolved_tokens": [],
            "resolutions": [{"token": m["canonical_code"], "matches": [m]} for m in matches],
            "intersection": matches,
            "by_entity_type": {"customer_order": matches},
        }
        out = not_found_error_message(
            {}, parser=self._parser("outstanding"), resolved=resolved, gate=self._gate(matches)
        )
        message = out.get("escalate_message") or ""

        assert message.count("has no outstanding items") == 3, message
        assert message.count("Would you like me to escalate") == 1, message

    def test_ac_1862_duplicate_matches_across_intersection_by_type_and_resolutions_render_once(
        self,
    ) -> None:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        matches = self._matches()
        # each match appears in `intersection`, `by_entity_type` AND `resolutions` - the
        # real shape `all_matches` is built from - so a naive concat triples every uuid.
        resolved = {
            "tokens": [m["canonical_code"] for m in matches],
            "unresolved_tokens": [],
            "resolutions": [{"token": m["canonical_code"], "matches": [m]} for m in matches],
            "intersection": matches,
            "by_entity_type": {"customer_order": matches},
        }
        out = not_found_error_message(
            {}, parser=self._parser("delivered"), resolved=resolved, gate=self._gate(matches)
        )
        message = out.get("escalate_message") or ""

        assert message.count("PS202609-0374") == 1, message
        assert message.count("PS202609-0398") == 1, message
        assert message.count("PS202609-0410") == 1, message
        assert message.count("hasn't been delivered yet") == 3, message


# --------------------------------------------------------------------------- #
# Owner console defect A, the REGRESSION (prod turn 631d4b65, 6 Sep 2026). Item A widened
# `_DISPLAY_NAME_KEYS` so an `inbound_shipment` with a null `shipment_number` printed its
# container rather than its uuid - and put `product_name` at the head of that list, so a
# PRODUCT now prints its description instead of its code. `check eta for IBKS7245-NG-BL`
# answered "product: Iborn. Bidet. (+7 more)": the code the customer typed, and the only
# string they can check the answer against, was gone.
# --------------------------------------------------------------------------- #


class TestAProductIsNamedByItsCode:
    """Owner rule: for a PRODUCT the CODE is the identity.

    Name-first is right only for types with no customer-facing code - a customer, a
    transporter, a form, a brand are known by name and their `canonical_code` is an
    internal account or slug. A product code is what the customer typed, what is on the
    carton and what they will type again; a `product_name` is a free-text description
    maintained for a different audience ("Iborn. Bidet."), and there is no way for the
    reader to tell that it means the code they asked about.
    """

    CODE = "IBKS7245-NG-BL"
    UUID = "aaaa1111-1111-4a11-9a11-111111111111"

    def _match(self, code: str, uuid: str, name: str) -> dict:
        return {
            "entity_type": "product",
            "uuid": uuid,
            "canonical_code": code,
            "display": {"product_name": name, "is_active": True},
        }

    def _run(self, matches: list[dict], *, token: str) -> str:
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        resolved = {
            "tokens": [token],
            "unresolved_tokens": [],
            "resolutions": [{"token": token, "matches": matches}],
            "intersection": matches,
            "by_entity_type": {"product": matches},
        }
        parser = {
            "domain_hint": "incoming",
            "entities": [{"hint": "product", "raw": token}],
            "routing": {"suggested_team": "purchasing"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": m["uuid"], "entity_type": "product", "code": m["canonical_code"]}
                for m in matches
            ],
        }
        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        return (out.get("found_summary") or "") + "\n" + (out.get("escalate_message") or "")

    def test_the_breakdown_names_the_code_never_the_description_alone(self) -> None:
        rendered = self._run(
            [self._match(self.CODE, self.UUID, "Iborn. Bidet.")], token=self.CODE
        )
        assert f"product: {self.CODE}" in rendered, (
            "the product line must open with the code the customer typed: "
            f"{rendered!r}"
        )
        assert "product: Iborn. Bidet." not in rendered, (
            f"the description must never stand in for the code: {rendered!r}"
        )

    def test_the_typed_code_is_the_representative_of_a_collapsed_family(self) -> None:
        """The "(+7 more)" collapse stays; what must not happen is a SIBLING being the one
        name the customer sees. The loose IB* prefix match that pulled the siblings in is
        pre-existing and is a different problem."""
        siblings = [
            self._match(f"IBKS7245-NG-{n}", f"aaaa1111-1111-4a11-9a11-11111111111{i}", "Iborn. Bidet.")
            for i, n in enumerate(("RD", "GR", "WH"), start=2)
        ]
        rendered = self._run(
            [self._match(self.CODE, self.UUID, "Iborn. Bidet."), *siblings], token=self.CODE
        )
        assert f"product: {self.CODE}" in rendered, (
            f"the typed code must be the representative of the collapsed set: {rendered!r}"
        )

    def test_a_customer_is_still_named_by_its_display_name(self) -> None:
        """Item I stays green: a customer HAS no customer-facing code, so name-first is
        the right ladder there and the account code is the internal identifier."""
        from app.services.chatbot.lanes.business.answer import not_found_error_message

        cust_uuid = "bbbb2222-2222-4b22-9b22-222222222222"
        match = {
            "entity_type": "customer",
            "uuid": cust_uuid,
            "canonical_code": "300-H070",
            "display": {"customer_name": "HANLIM TRADING SDN BHD"},
        }
        resolved = {
            "tokens": ["hanlim"],
            "unresolved_tokens": [],
            "resolutions": [{"token": "hanlim", "matches": [match]}],
            "intersection": [match],
            "by_entity_type": {"customer": [match]},
        }
        parser = {
            "domain_hint": "order",
            "entities": [{"hint": "customer", "raw": "hanlim"}],
            "routing": {"suggested_team": "customer_service"},
            "access_levels": [],
        }
        gate = {
            "gate_passed": True,
            "compatible_entities": [
                {"uuid": cust_uuid, "entity_type": "customer", "code": "300-H070"}
            ],
        }
        out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate)
        rendered = (out.get("found_summary") or "") + "\n" + (out.get("escalate_message") or "")
        assert "HANLIM TRADING SDN BHD" in rendered, rendered
        assert "300-H070" not in rendered, rendered


# --------------------------------------------------------------------------- #
# Owner console pass 4, item G (prod turn 858c9c54, after #705). The three-code stock
# turn now names MSK11A-QT - but only INSIDE the cross-domain block, as
# "But there is INCOMING stock (ETA) for the requested products: MSK11A-QT container
# TEMU6355180 ...". Read top to bottom that reply answers a stock question with two
# codes' stock and then, with no seam between them, an incoming fact about a third; the
# customer is left to infer the thing they actually asked, which is that MSK11A-QT has
# no stock. The owner's ruling: SAY IT, and say it before the incoming block.
#
# The negative belongs where the evidence for it is. `crossdomain_render` is the only
# place that knows BOTH that the primary render did not echo the code and that the other
# domain was actually probed for it - the stock composer upstream knows neither - and it
# already owns the sibling sentence for the both-empty case ("No stock and no incoming
# for X"). Its block is appended UNDER the primary answer, so a line at the head of that
# block is exactly the stock section's last word before the incoming lead.
# --------------------------------------------------------------------------- #


class TestAZeroStockCodeIsNamedBeforeTheIncomingBlock:
    ZEROSET = {
        "active": True,
        "origin_domain": "inventory",
        "team": "warehouse",
        "returned_codes": ["MWT5727SS-CR", "MHS1028"],
        "missing": [{"code": "MSK11A-QT", "_n": "MSK11A-QT", "uuid": "u-msk"}],
    }

    @staticmethod
    def _incoming_row(code: str) -> dict:
        return {
            "fields": [
                {"key": "product_code", "label": "Product Code", "value": code},
                {"key": "container_number", "label": "Container", "value": "TEMU6355180"},
                {"key": "estimated_arrival_date", "label": "ETA", "value": "2026-10-02"},
            ]
        }

    def _render(self, rows: list[dict], **zs_overrides) -> str:
        from app.services.chatbot.lanes.business.answer import crossdomain_render

        zs = {**self.ZEROSET, **zs_overrides}
        out = crossdomain_render(
            {"items": rows, "has_result": True}, zeroset=zs, validator={"has_result": True}
        )
        return out["_xdBlock"]["block"]

    def test_the_stock_miss_is_stated_above_the_incoming_lead(self) -> None:
        block = self._render([self._incoming_row("MSK11A-QT")])
        assert "No stock for MSK11A-QT" in block, (
            f"the customer asked about stock and was never told there is none: {block!r}"
        )
        assert block.index("No stock for MSK11A-QT") < block.index("INCOMING stock"), (
            f"the stock answer must finish before the incoming block starts: {block!r}"
        )

    def test_the_incoming_direction_says_no_incoming(self) -> None:
        """The mirror: an INCOMING question whose code has none, answered with its stock.
        The word follows the question that was asked, exactly as the both-empty sentence
        already does."""
        block = self._render([self._incoming_row("MSK11A-QT")], origin_domain="incoming")
        assert "No incoming for MSK11A-QT" in block, block
        assert block.index("No incoming for MSK11A-QT") < block.index("stock details"), block

    def test_a_code_with_nothing_on_either_side_keeps_its_own_sentence(self) -> None:
        """Guard, #705's H: a code the probe answered with NOTHING still gets the single
        combined sentence and the escalation offer, not two half-sentences."""
        block = self._render([])
        assert "No stock and no incoming for MSK11A-QT" in block, block
        assert "No stock for MSK11A-QT." not in block, (
            f"the both-empty case must not also emit the one-sided line: {block!r}"
        )
        assert "escalate" not in block.lower(), block  # compose writes the offer (8 Sep 2026)

    def test_a_code_the_primary_render_did_echo_is_not_called_missing(self) -> None:
        """Guard: `missing` means "the primary render did not echo this code", which is
        only the same statement as "there is none" when the render is product-keyed. A
        warehouse breakdown answers ABOUT the code without printing it, and `can_state_absence`
        is what stops "No stock" landing under the stock it just printed."""
        block = self._render(
            [self._incoming_row("MSK11A-QT")], returned_codes=[]
        )
        assert "No stock for MSK11A-QT" not in block, (
            f"an absence was asserted where the render could not establish one: {block!r}"
        )
        assert "INCOMING stock" in block, block
