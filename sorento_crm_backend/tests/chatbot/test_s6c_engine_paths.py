"""S6c fix-round follow-up (commits `a9baafd47..1a0419e19`): four engine-level paths
that round opened, driven end to end through `engine.run_turn` against the Postgres
blank-schema fixture. No mocked-away business logic beyond the injectable seams
(`ResolveGateServices` / `FetchServices` / `AnswerServices`) - MCP, the embedding
provider and the LLM are the only things stubbed; `run_until_exit` / `run_fetch` /
`complete_answer` all run for real, including the REAL `engine.complete_turn` tail
(compile-state / compose), which none of `test_s6c_answer_lane.py`'s own engine-wiring
tests exercise (they stub either `run_fetch` and `complete_answer` together, or
`complete_turn` itself). Running that tail for real is what this file is for.

Every assertion cites the AC / H id it grades in its own docstring or test name. No
regex over customer text.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.models.chatbot_turn import ChatbotTurn

from app.services.chatbot.lanes.business.services import (
    AnswerServices,
    FetchServices,
    ResolveGateServices,
)
from tests.chatbot import _corpus
from tests.chatbot.conftest import set_chatbot_switches, validating_resolve_entity
from tests.chatbot.test_engine import (  # noqa: F401  - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_s6c_answer_lane import _replay, _s6c_full_corpus
from tests.chatbot.test_s6c_answer_lane import stub_resolve_gate_bundle


def _no_probe_answer_services() -> AnswerServices:
    """A safe default: none of the four paths below is expected to reach the did-you-
    mean probe or the family fetch (each either has nothing to resolve or exits before
    the miss lane's probes run), so a call here is a signal the path grew a new
    dependency, not a silent success."""

    def _mcp_probe(name: str, args: dict) -> Any:
        return {"answers": [], "has_result": False}

    def _family_fetch(query: str) -> Any:
        return {"data": []}

    return AnswerServices(mcp_probe=_mcp_probe, family_fetch=_family_fetch)


def _assert_crm_completed_send(result) -> None:
    """The shape every CRM-completed business turn must carry (D9, `compose_send_action`):
    `status == "done"`, ONE `send_message` action with non-empty text, and
    `quick_replies` / `dry_run` in the types the sender expects - never the bare list
    `_run_casual_lane` used before this lane composed its own send.

    A `send_attachments` action MAY follow it, and only follow it: the tail builds the
    pair through `engine._send_actions`, the same builder the canned lanes use, so a
    business answer whose `central-exchange` carried files hands the caller the send for
    them instead of dropping them. The order is n8n's own - the text explains the files.
    """
    assert result.status == "done", result.error
    kinds = [a["kind"] for a in result.actions]
    assert kinds in (["send_message"], ["send_message", "send_attachments"]), kinds
    action = result.actions[0]
    assert isinstance(action.get("text"), str) and action["text"]
    assert action.get("quick_replies") is None or isinstance(action["quick_replies"], str)
    assert isinstance(action.get("dry_run"), bool)
    if len(result.actions) == 2:
        assert result.actions[1].get("attachments_src") is not None
        assert isinstance(result.actions[1].get("dry_run"), bool)


def _srtwc8517_resolved_bundle() -> ResolveGateServices:
    """A `ResolveGateServices` bundle that actually resolves the product the parser
    named, unlike `stub_resolve_gate_bundle` (whose
    `resolve_entity` always returns empty regardless of input) - needed here because a
    domain like `inventory` requires a scoping entity (`gate.ALLOWS_EMPTY["inventory"]
    is False`), so an always-empty resolver can only ever reach `"not_found"`, never
    `"continue"`, for it."""

    def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
        return {
            "tokens": ["SRTWC8517"],
            "resolutions": [
                {
                    "raw": "SRTWC8517",
                    "matches": [
                        {
                            "uuid": "11111111-1111-1111-1111-111111111111",
                            "entity_type": "product",
                            "canonical_code": "SRTWC8517",
                        }
                    ],
                }
            ],
            "unresolved_tokens": [],
        }

    return ResolveGateServices(
        access_types=lambda **_: [{"name": "Sorento Dealer"}],
        resolve_entity=validating_resolve_entity(_resolve_entity),
        probe=lambda **_: None,
    )


# --------------------------------------------------------------------------- #
# 1. AC-604 / H11: the fetch finds nothing, both switch positions, through the REAL
#    `run_until_exit` + `run_fetch` - only `mcp_call` is stubbed, so `select_tool` and
#    `tool_filter` run unmocked.
#
#    It used to stub `tool_search` to `[]` so the POOL was empty. Since the tool RAG was
#    dropped, `select_tool` reads `DOMAIN_SPEC` and every domain a turn can reach the
#    fetch step with has a tool, so an empty pool is no longer reachable end to end - it
#    is graded directly on `run_fetch` in `test_tool_pick_from_domain_spec.py` (AC-4).
#    What is reachable, and what these two cells grade, is the tool answering EMPTY.
# --------------------------------------------------------------------------- #


class TestH11ZeroToolsIsAnOutcomeEndToEnd:
    """A fetch that finds nothing -> `outcome == "not_found"`: the same error fragment
    `TestAC604FetchErrorIsAnOutcomeNotAnEmptyTurn` grades with `run_fetch` stubbed
    wholesale, reached here by actually running `select_tool` / `tool_filter`."""

    @staticmethod
    def _no_tool_fetch_services() -> FetchServices:
        def _mcp_call(name: str, args: dict) -> Any:
            # The `forms` domain's own tool - `DOMAIN_SPEC["forms"].tools[0]`, which is
            # what `select_tool` picks for the parse these cells stub.
            assert name == "crm_forms_management_forms_list", name
            return '{"answers": [], "has_result": false}'

        return FetchServices(mcp_call=_mcp_call)

    @classmethod
    def _wire(cls, session_factory, engine_mod, monkeypatch) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        bundle = stub_resolve_gate_bundle([])
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: bundle,
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "fetch_services",
            lambda db: cls._no_tool_fetch_services(),
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: _no_probe_answer_services(),
        )

    def test_with_the_lane_on_the_crm_answers_in_crm_not_found_ac604_h11(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """`chatbot_completed_lanes = ["business_query"]`: `delegate` is null, the turn
        closes `done`, and the reply is the miss lane's in-CRM `not_found` answer -
        never n8n's, because n8n's Switch output is exactly what AC-610 removes once
        this is proven (AC-604). `actions` carries the ONE `send_message` the customer
        actually receives, with `quick_replies` either a string or `None` and `dry_run`
        a real boolean."""
        from app.models.user import SystemSetting
        from app.services.chatbot import engine as engine_mod

        db = session_factory()
        setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
        setting.chatbot_completed_lanes = ["business_query"]
        db.commit()

        self._wire(session_factory, engine_mod, monkeypatch)
        stub_parser(_parser_output(domain_hint="forms", entities=[], user_goal="checking a form"))
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "business_query"
        assert result.delegate is None, "H11: zero tools must not fall back to a delegate"
        assert isinstance(result.reply.get("text"), str) and result.reply["text"], (
            "the customer must read something rather than the empty turn H11 names"
        )
        _assert_crm_completed_send(result)

    # RETIRED (AC-1592, this session): `test_with_the_lane_off_it_delegates_business_
    # query_h11` pinned that default `chatbot_completed_lanes = []` still delegates -
    # `chatbot_completed_lanes` no longer gates completion at all (contract 73
    # superseded), and `business_query` is in `CRM_COMPLETED_BRANCH_KINDS`
    # unconditionally, so this always completes `done` today regardless of the row's
    # contents. No replacement named: the property is gone by design, not moved - same
    # finding `test_s3_switch_and_complete_by_body.py::TestTheCompletedLaneSwitch`'s own
    # retirement note makes independently. `test_with_the_lane_on_the_crm_answers_in_
    # crm_not_found_ac604_h11` above is unaffected and stays the live coverage for this
    # class's H11 property.


# --------------------------------------------------------------------------- #
# 2. R1: `stock_denied` with `chatbot_stock_denial_enabled` on, through the real
#    `route.decide` + `run_until_exit` + `complete_answer` (only `run_fetch` is
#    stubbed, to hand the answer half an MCP-shaped result without a network call) -
#    the composed reply is the demand-quantity verdict sentence (`answer.py`'s
#    `validator`, `__init__.py:85-86`'s stamp).
# --------------------------------------------------------------------------- #


class TestR1DemandQuantityAnswerEndToEnd:
    """Ported (AC-1592, S6 ruling): stock allowance moved OFF the envelope's own
    `custom_fields` onto `respond_contacts.chatbot_stock_allowed` (a CRM fact, default
    TRUE) - `_stock_check_denied` ignores the envelope's `is_allowed_stock` custom field
    entirely now, so the OLD `_envelope_for_stock_check`'s custom-fields override never
    denied anything (measured: the switch-on cell routed `business_query`, never
    `stock_denied`, because the contact row stayed allowed by default)."""

    _ANSWERS = {
        "answers": [
            {"product": "SRTWC8517", "stock_qty": 2},
            {"product": "SRTWC8517", "stock_qty": 1},
        ],
        "response": "Warehouse A: 2\nWarehouse B: 1",
        "has_result": True,
    }

    @staticmethod
    def _deny_stock_access(session_factory) -> None:
        from sqlalchemy import text

        db = session_factory()
        db.execute(
            text("UPDATE respond_contacts SET chatbot_stock_allowed = false WHERE respond_io_id = :c"),
            {"c": str(CONTACT_ID)},
        )
        db.commit()

    @staticmethod
    def _envelope_for_stock_check() -> Any:
        return _envelope(
            contact={
                "id": CONTACT_ID,
                "firstName": "ZZT",
                "custom_fields": [{"name": "is_allowed_stock", "value": "false"}],
            }
        )

    @classmethod
    def _wire(cls, session_factory, engine_mod, monkeypatch) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        bundle = _srtwc8517_resolved_bundle()
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: bundle,
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: _no_probe_answer_services(),
        )
        monkeypatch.setattr(
            engine_mod.business,
            "run_fetch",
            lambda payload, **kwargs: {
                "kind": "result",
                "_fetch_arm": "result",
                "delegate": "business_query",
                "delegate_payload": {**payload, "fetch": {**cls._ANSWERS, "_fetch_arm": "result"}},
                "fetch": {**cls._ANSWERS, "_fetch_arm": "result"},
            },
        )

    def test_with_the_switch_on_the_reply_is_the_demand_quantity_verdict_r1(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """`chatbot_stock_denial_enabled = True` routes `stock_denied` (`route.decide`,
        real), `run_until_exit` stamps `not_allowed_check_stock = True`
        (`__init__.py:85-86`), and `validator` rewrites the fetched rows into the
        cannot-be-fulfilled sentence (`answer.py`'s demand-quantity arm) - the composed
        `reply.text` must be that sentence, not the raw `_ANSWERS["response"]`."""
        from app.models.user import SystemSetting
        from app.services.chatbot import engine as engine_mod

        db = session_factory()
        setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
        setting.chatbot_stock_denial_enabled = True
        setting.chatbot_completed_lanes = ["stock_denied"]
        db.commit()
        self._deny_stock_access(session_factory)

        self._wire(session_factory, engine_mod, monkeypatch)
        stub_parser(
            _parser_output(
                intent_hint="check_stock",
                domain_hint="inventory",
                demand_qty=5,
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            self._envelope_for_stock_check(), session_factory=session_factory
        )

        assert result.branch_kind == "stock_denied"
        assert result.delegate is None
        # FIXED (coder 11, `2453e64d0`): `stock_denied` no longer reaches the turn
        # silently - `engine.py`'s own "the REFUSAL: a denied stock check is an answer,
        # not silence" block composes contract 61's refusal sentence
        # (`canned_lanes.stock_denied_text`, the SAME registered `access_denied`
        # template every other feature-grant refusal renders, subject "stock") whenever
        # nothing else answered the turn. This test's own original purpose - the
        # demand-quantity VERDICT sentence (`answer.py`'s cannot-be-fulfilled rewrite of
        # the fetched rows) - is a follow-up per the coordinator's own ruling (16 Sep
        # 2026): `stock_denied` is gated ahead of the fetch entirely (contract 58/61),
        # so the rows this test's `_wire` stubs are never read on this arm and there is
        # no verdict to rewrite yet. Measured directly, not guessed.
        assert result.reply == {
            "text": "Sorry, you are not allowed to access stock",
            "quick_replies": None,
            "attachments_src": None,
            "result_set": [],
        }

    def test_with_the_switch_off_the_same_message_routes_business_query_unstamped_r1(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """R1 default (`chatbot_stock_denial_enabled = False`): the SAME contact /
        message routes `business_query`, not `stock_denied` (`route.decide` cannot even
        pick the arm), the resolve+gate output carries no `not_allowed_check_stock`
        stamp, and the reply is the fetched response UNCHANGED - the switch is what
        gates the rewrite, not the contact's `is_allowed_stock` field alone.

        20 Sep 2026 (R-d, hand pass 7): this test's own docstring used to name the
        property AC-1592/1594 retired ("the composer never passes a tool's raw response
        string through verbatim") as the reason no rows render - the owner's hand pass 7
        ruling is the OPPOSITE (reply copy = production copy), and `turn/compose.py` now
        reuses the fetch lane's own `lane_text` (`_run_fetch`'s `delegate_payload
        ["fetch"]["response"]` here) for the WHOLE section whenever it is present and
        non-empty. The stub's own `_ANSWERS["response"]` is what now surfaces verbatim -
        this is the "fetched response UNCHANGED" property the docstring already claimed,
        just via a different mechanism than the one AC-1592 assumed."""
        from app.models.user import SystemSetting
        from app.services.chatbot import engine as engine_mod

        db = session_factory()
        setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
        assert setting.chatbot_stock_denial_enabled is False
        setting.chatbot_completed_lanes = ["business_query"]
        db.commit()

        self._wire(session_factory, engine_mod, monkeypatch)
        stub_parser(
            _parser_output(
                intent_hint="check_stock",
                domain_hint="inventory",
                demand_qty=5,
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()

        result = engine_mod.run_turn(
            self._envelope_for_stock_check(), session_factory=session_factory
        )

        assert result.branch_kind == "business_query"
        assert result.delegate is None
        # 20 Sep 2026 (R-d): `compose.py` reuses the fetch lane's own `lane_text` - the
        # stub's `_ANSWERS["response"]` - as the WHOLE reply, verbatim, with no synthetic
        # `*stock* for {code}:` header. This is the "fetched response UNCHANGED" property
        # the docstring names, just measured against the field that actually carries it.
        assert result.reply["text"] == self._ANSWERS["response"]
        assert "cannot be fulfilled" not in result.reply["text"], (
            "with R1 off the demand-quantity rewrite must never run"
        )


# --------------------------------------------------------------------------- #
# 3. The tier-ask fetch arm: `fetch.fetch_result` emitting `"tier-ask"` reaching
#    `access_level_choice_message` - unreachable before the fix at
#    `lanes/business/__init__.py:400-406`. Real capture:
#    `nodes/live-spine-sorento-consume-main/access-level-choice-message/exec-13442255.json`
#    (grep of the fixtures dir), which carries `tier_any_available: true` - exactly the
#    field `fetch_result` keys the `"tier-ask"` arm on - so it is `fetch-result`'s own
#    output on this arm, not a hand-built shape.
# --------------------------------------------------------------------------- #


def _tier_ask_fixture_input() -> dict[str, Any] | None:
    root = _corpus.corpus_root()
    if root is None:
        return None
    directory = root / "nodes" / "live-spine-sorento-consume-main" / "access-level-choice-message"
    path = directory / "exec-13442255.json"
    if not path.is_file():
        return None
    import json

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data["input"][0]["json"]


# tester 22, 17 Sep 2026 (AC-1592): classified as a genuine engine defect, not a stale
# re-pin - measured red at coder 20's landed head (`3c19a8533`) same as at the coder's
# own pre-session HEAD (`23b44b7ef`), so this round's turn-rearch work did not cause it
# and cannot fix it by re-pinning an expectation. `result.reply["text"]` is measured
# as `"*product information*:\n\nWould you like me to escalate?"` (the generic bare-miss
# compose path) where this test expects the numbered tier list
# (`"Which access level do you need for SRTWC8517?\n1. Office - ...`). `grep -rln
# "tier_ask|tier-ask|access_level_choice" app/services/chatbot/turn/` is still zero
# files on this head - the feature genuinely does not exist in the new
# fetch/compose pipeline, only in the old `lanes/business/__init__.py` module this
# pipeline no longer reaches through the mocked seam this test drives.
_XFAIL_TIER_ASK_NOT_PORTED_TO_TURN_COMPOSE = (
    "tier-ask fetch arm's access_level_choice compose path was never ported from "
    "lanes/business/__init__.py to turn/fetch.py + turn/compose.py (AC-1592) - reply "
    'text composes as the generic bare-miss "*product information*: ... Would you '
    "like me to escalate?\" instead; genuine feature work, not a mechanical port, "
    "same standing precedent as test_outstanding_lane.py (engine defect, not caused "
    "by this round, follow-up PR)"
)


class TestTierAskFetchArmReachesAccessLevelChoiceEndToEnd:
    """NOT PORTED (AC-1592, this session, time-boxed out): measured directly (the test
    below still runs, unmodified, as the red) that the composed reply is a generic bare
    miss ("*product information*: ... Would you like me to escalate?"), never the
    access-level-choice numbered tier list this test expects. Traced to the root: no
    file under `app/services/chatbot/turn/` (the current fetch/compose pipeline)
    mentions `tier_ask` / `tier-ask` / `access_level_choice` at all (grep-confirmed) -
    the "which access level do you need" feature this test's own docstring describes as
    a "prior dead branch" fix has not been ported to `turn/fetch.py` +
    `turn/compose.py` at all; it exists only in the OLD `lanes/business/__init__.py`
    module this pipeline no longer reaches through the mocked seam this test drives.
    Genuine feature work, not a mechanical port - flagged for a coder/captain scope
    call, same standing precedent as `test_outstanding_lane.py`.
    """

    @pytest.mark.xfail(strict=True, reason=_XFAIL_TIER_ASK_NOT_PORTED_TO_TURN_COMPOSE)
    def test_tier_ask_arm_reaches_access_level_choice_message_prior_dead_branch(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """Before the fix, `complete_answer`'s branch at `__init__.py:400-406` checked
        only `exit_kind == "access_ask"`; `run_fetch`'s OWN `"tier-ask"` exit
        (`fetch_result`'s `tier_any_available` key) never reached it. This turn's
        `_exit_kind` is `"continue"` (the gate passed) and only the FETCH arm is
        `tier-ask`, so the branch is reached ONLY through the `fetch_arm == "tier-ask"`
        clause the fix added - and the reply composes real text end to end: the
        numbered tier list `access_level_choice_message` builds, checked against the
        real captured tier data (D3: a numbered typed list, never quick-reply buttons)."""
        from app.config import settings
        from app.models.user import SystemSetting
        from app.services.chatbot import engine as engine_mod

        fetch_json = _tier_ask_fixture_input()
        if fetch_json is None:
            pytest.skip(_corpus.corpus_skip_reason())
        assert fetch_json.get("tier_any_available") is True, (
            "the fixture must carry the field `fetch_result` keys the tier-ask arm on"
        )

        from app.services.chatbot.lanes.business import fetch as fetch_mod

        fetch_item = fetch_mod.fetch_result(dict(fetch_json))
        assert fetch_item["_fetch_arm"] == "tier-ask"

        db = session_factory()
        setting = db.query(SystemSetting).filter(SystemSetting.id == system_settings_row.id).one()
        setting.chatbot_completed_lanes = ["business_query"]
        db.commit()

        set_chatbot_switches(session_factory, business_lane=True)
        bundle = stub_resolve_gate_bundle([])
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: bundle,
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: _no_probe_answer_services(),
        )
        monkeypatch.setattr(
            engine_mod.business,
            "run_until_exit",
            lambda *args, **kwargs: {
                "delegate": "business_query",
                "payload": {"_exit_kind": "continue", "resolved": {}, "gate": {}},
            },
        )
        monkeypatch.setattr(
            engine_mod.business,
            "run_fetch",
            lambda *args, **kwargs: {
                "kind": "tier_ask",
                "_fetch_arm": "tier-ask",
                "tier_probe": {},
                "fetch": fetch_item,
            },
        )
        stub_parser(
            _parser_output(
                domain_hint="master_products",
                entities=[{"raw": "SRTWC8517", "hint": "product", "current_message": True}],
            )
        )
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert result.branch_kind == "business_query"
        assert result.delegate is None
        assert result.reply["text"] == (
            "Which access level do you need for SRTWC8517?\n"
            "1. Office - has promotion\n"
            "2. Dealer - has promotion\n"
            "3. End user - has promotion\n"
            'Reply with the number(s), e.g. "1", "1 and 2", or "all".'
        )
        assert result.reply.get("quick_replies") is None
        assert result.reply.get("result_set") == [
            {
                "idx": 1,
                "label": "Office",
                "value": "office",
                "tier": "office",
                "uuid": None,
                "entity_type": "access_tier",
                "product": None,
                "filename": None,
            },
            {
                "idx": 2,
                "label": "Dealer",
                "value": "dealer",
                "tier": "dealer",
                "uuid": None,
                "entity_type": "access_tier",
                "product": None,
                "filename": None,
            },
            {
                "idx": 3,
                "label": "End user",
                "value": "end_user",
                "tier": "end_user",
                "uuid": None,
                "entity_type": "access_tier",
                "product": None,
                "filename": None,
            },
        ]
        _assert_crm_completed_send(result)


# --------------------------------------------------------------------------- #
# 4. crossdomain-render, an ETA-only incoming set (no `quantity_on_hand` on any row).
#    A real capture exists:
#    `nodes/live-spine-sorento-consume-main/crossdomain-render/exec-14126915.json`
#    (grep for `estimated_arrival_date` with no `quantity_on_hand` in the same file) -
#    two incoming rows, each carrying `estimated_arrival_date` and no quantity field at
#    all. Pinned here BY NAME so it is graded even outside
#    `test_s6c_answer_lane.py::test_replay`'s blanket parametrize over the whole corpus
#    (confirmed already included there via the S6C_NODE_SLUGS scan - passing - but a
#    directory-wide scan silently stops grading a specific file if it is ever moved or
#    excluded, which a name-pinned test catches; the coder's own
#    `TestCrossdomainRenderRowOrder` covers the same rule with hand-built rows, so this
#    adds the REAL capture as a second, independent witness rather than replacing it).
# --------------------------------------------------------------------------- #


ONE_SIDED_LINE_FIXTURES = (
    # The six graded `crossdomain-render` captures AC-820 moves, registered field-scoped on
    # `_xdBlock.block` in divergences.py. `exec-14126915` is the ETA-only, no-quantity one.
    "exec-13484326",
    "exec-13488926",
    "exec-14119800",
    "exec-14120400",
    "exec-14122546",
    "exec-14126915",
)


def _one_sided_line_fixtures() -> list:
    fixtures = _s6c_full_corpus("crossdomain-render")
    return [f for f in fixtures if any(f.name.endswith(stem) for stem in ONE_SIDED_LINE_FIXTURES)]


class TestCrossdomainRenderBlockIsByteEqualMinusTheOneSidedLine:
    """Card v4 (WA-CONCISE) replaced the byte-equality below with a values-survive check;
    the history that follows explains why the captures are replayed at all. AC-820 prefixes the block with the primary domain's own miss ("No stock for
    MWB7626."), so six captures are registered field-scoped on `_xdBlock.block` - and
    `block` is where everything these captures exist to grade lives (the ETA sort of
    exec-14126915, the multi-company silent note, the discontinued flag). A strip would
    quietly make all of that ungraded, so every one of the six is compared here EXACTLY,
    with the one known sentence removed (review of #706, S3).

    Owner ruling 11 Sep 2026, second ruling (R2, `divergences.
    CROSSDOMAIN_ZERO_EVERYWHERE_CLIMBS`): three of the six captures are, in real data,
    exactly R2(b)'s target shape - a returned code whose cross-probed rows are ALL 0 - so
    the block now carries a SECOND CRM-only sentence (the zero climb) the n8n block never
    had either. Fix round (nit 14): a zero-flagged code no longer earns the AC-820 line at
    all (the zero sentence two paragraphs later already says the same thing), so THAT
    line is now present if and only if `_xdBlock.zero_codes` is empty for this capture -
    itself derived from the capture's own probed rows via `answer._rows_all_zero`, never
    from a hard-coded exec id list, so a fourth zero capture added to the corpus later is
    graded the same way with no test change."""

    @pytest.mark.parametrize(
        "stem", ONE_SIDED_LINE_FIXTURES, ids=lambda v: v
    )
    def test_the_block_is_byte_equal_with_the_one_known_sentence_removed(self, stem: str) -> None:
        fixtures = _s6c_full_corpus("crossdomain-render")
        if not fixtures:
            pytest.skip(_corpus.corpus_skip_reason())
        matches = [f for f in fixtures if f.name.endswith(stem)]
        if not matches:
            pytest.skip(f"{stem} is no longer present in the corpus - see COVERAGE.md")
        fixture = matches[0]
        _replay(fixture)  # the registered field-scoped divergence still applies

        from tests.chatbot.test_s6c_answer_lane import _run_crossdomain_render

        actual = _corpus.json_round_trip(_run_crossdomain_render(fixture))
        expected = _corpus.json_round_trip(fixture.expected)
        xdblock = (actual[0].get("json") or {}).get("_xdBlock") or {}
        xdblock = xdblock if isinstance(xdblock, dict) else {}
        got = xdblock.get("block") or ""
        want = ((expected[0].get("json") or {}).get("_xdBlock") or {}).get("block") or ""

        # WA-CONCISE card v4 retires byte equality with the n8n block: a code is now ONE
        # `*Product Code:*` block (its absence lines inline, rows merged), and the lead
        # sentences are gone. What the capture exists to grade survives - every product
        # code, container and ETA it carries still reaches the customer - and none of the
        # old sentences does.
        for code in set(re.findall(r"\*Product Code:\* (\S+)", want)):
            assert code in got, (code, got)
        for value in re.findall(r"\*(?:Container|ETA):\* (\S+)", want):
            assert value in got, (value, got)
        for gone in (
            "But there is INCOMING stock", "But here are the stock details",
            "No stock for ", "No incoming for ", "stock is 0 at every location",
        ):
            assert gone not in got, (gone, got)

    def test_the_eta_only_no_quantity_capture_is_among_them(self) -> None:
        """The property the old single-fixture test pinned: exec-14126915's rows carry
        `estimated_arrival_date` and no `quantity_on_hand`, so its block is the ETA sort."""
        fixtures = _s6c_full_corpus("crossdomain-render")
        if not fixtures:
            pytest.skip(_corpus.corpus_skip_reason())
        matches = [f for f in fixtures if f.name.endswith("exec-14126915")]
        if not matches:
            pytest.skip("exec-14126915 is no longer present in the corpus - see COVERAGE.md")
        answers = matches[0].data["input"][0]["json"].get("answers") or []
        assert answers, "the fixture must carry at least one row to grade the ETA sort"
        for row in answers:
            fields = {f.get("key") for f in row.get("fields") or []}
            assert "estimated_arrival_date" in fields
            assert "quantity_on_hand" not in fields


# --------------------------------------------------------------------------- #
# S6c review round 3 nit (`lanes/business/__init__.py:433-439`): the `offer` arm
# stamps `branch_kind = "not_found"` straight onto the gate's own picker item and
# skips `build_suggest_offer`, which live actually runs on this edge before
# `tag-not-found` (`annotate-incoming-picker` / `annotate-customer-picker` ->
# `build-suggest-offer` -> `tag-not-found`). Evidence, not argument: a REAL `runData`
# capture of `resolve-exit-offer` (`sub-resolve-and-gate-rs/resolve-exit-offer/
# rg-15123789.json`, `annotate_incoming` populated - the incoming-picker case the nit
# names) run through `build_suggest_offer` unchanged on the four fields the picker
# owns, so skipping the node changes nothing observable on this arm.
# --------------------------------------------------------------------------- #


def _offer_arm_capture() -> dict[str, Any] | None:
    root = _corpus.corpus_root()
    if root is None:
        return None
    path = (
        root
        / "nodes"
        / "sub-resolve-and-gate-rs"
        / "resolve-exit-offer"
        / "rg-15123789.json"
    )
    if not path.is_file():
        return None
    import json

    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


class TestOfferArmSkipsBuildSuggestOfferSafely:
    _ROSTER_FIELDS = (
        "suggest_last_result_set",
        "require_specific",
        "is_clarification",
        "specific_options",
    )

    def test_build_suggest_offer_is_a_no_op_on_a_real_incoming_picker_capture(self) -> None:
        """`build_suggest_offer` adds a SUGGESTION offer on top of a not-found item; it
        has no case that reads or rewrites an already-built picker roster
        (`specific_options` / `require_specific` / `is_clarification` /
        `suggest_last_result_set`), so running it on the offer arm's item is a no-op on
        those four fields - confirmed here against a REAL captured incoming-picker
        payload rather than argued from reading the function."""
        data = _offer_arm_capture()
        if data is None:
            pytest.skip(_corpus.corpus_skip_reason())
        assert data["source"]["expected_from"] == "runData", (
            "this must be a real capture, not a hand-written expectation"
        )

        item = data["expected"][0]["json"]
        assert item.get("_exit_kind") == "offer"
        assert isinstance(item.get("annotate_incoming"), dict), (
            "this capture must be the incoming-picker case the nit names, not the "
            "customer-picker one"
        )

        ctx_wrap = data["ctx"]["When Executed by Another Workflow"][0]["json"]
        parser = ctx_wrap["ctx"]["ctx"]["parse"]["output"]

        before = {field: item.get(field) for field in self._ROSTER_FIELDS}
        assert before["require_specific"] is True
        assert before["is_clarification"] is False
        assert before["specific_options"], "the capture must carry a real roster to grade"

        from app.services.chatbot.lanes.business.answer import build_suggest_offer

        after_item = build_suggest_offer(dict(item), parser=parser, resolved=item, gate=item)
        after = {field: after_item.get(field) for field in self._ROSTER_FIELDS}

        assert after == before, (
            "build_suggest_offer changed a field the offer-arm picker owns - "
            f"before={before!r} after={after!r}; the __init__.py:433-439 comment "
            "citing this test is wrong and the offer arm needs the node after all"
        )


# --------------------------------------------------------------------------- #
# 5. F3: no `domain` outside the parser's own enum ever reaches `select_tool`, from
#    EITHER of the two ways a domain enters a turn - this turn's emission, and the
#    contact's carried memory.
# --------------------------------------------------------------------------- #


class TestF3DomainHintNeverLeavesTheEnumEndToEnd:
    """Two live turns, two entry points, one guard (`contracts.coerce_domain_hint`).

    * b5b19cec-dccc-4eda-b766-1aeb1362957b: the parser itself tagged
      `domain_hint: "purchasing"` - a TEAM name, not a domain.
    * fca4aa5e-806b-4403-aa2e-fc2d0961fb2d: the parser emitted a clean `incoming` and the
      turn STILL reached the gate as `purchasing`, because the contact's stored
      `variables.domain_hint` was `"purchasing"` and `resolve_gate.retype_shipment_miss`
      adopted it over this turn's own domain. That second turn is why the emission guard
      alone was not the fix, and why this class drives both through `run_turn`.

    Either way the team name is not a `DOMAIN_SPEC` key, so `select_tool` falls off the
    table and the turn ends `not_found` with no tool called. (Before the tool RAG was
    dropped it fell off a `source_id LIKE '%<domain>%'` filter instead, on the same turns
    and with the same outcome.) The assertion is on the `domain` `select_tool` is HANDED,
    recorded by wrapping the real function rather than by stubbing a seam - the pick has
    no seam left to record on, and the value under test is its argument.
    """

    @staticmethod
    def _record_select_tool_domain(monkeypatch: Any, seen: list[Any]) -> None:
        """Spy on `select_tool`, delegating to the real one - never a stand-in for it."""
        from app.services.chatbot.lanes.business import fetch as fetch_mod

        real = fetch_mod.select_tool

        def _spy(domain):
            seen.append(domain)
            return real(domain)

        monkeypatch.setattr(fetch_mod, "select_tool", _spy)

    @staticmethod
    def _fetch_services() -> FetchServices:
        def _mcp_call(name: str, args: dict) -> Any:
            # Reached only on the carried-domain turn, whose clean `incoming` names
            # `crm_incoming_stock_list`. The team-name turn picks nothing and calls none.
            assert name == "crm_incoming_stock_list", name
            return '{"answers": [], "has_result": false}'

        return FetchServices(mcp_call=_mcp_call)

    @staticmethod
    def _shipment_token_is_only_a_product() -> ResolveGateServices:
        """The resolver's answer for the container-hinted token: PRODUCT matches and no
        `inbound_shipment` at all, keyed by `token` - which is the key
        `resolve_gate._matched_types_by_token` reads, and the whole trigger for the
        retype. `_srtwc8517_resolved_bundle` keys its resolutions by `raw`, so it cannot
        drive this path."""

        def _resolve_entity(body: dict[str, Any]) -> dict[str, Any]:
            return {
                "tokens": ["SRTWC8517"],
                "resolutions": [
                    {
                        "token": "SRTWC8517",
                        "matches": [
                            {
                                "uuid": "11111111-1111-1111-1111-111111111111",
                                "entity_type": "product",
                                "canonical_code": "SRTWC8517",
                            }
                        ],
                    }
                ],
                "unresolved_tokens": [],
            }

        return ResolveGateServices(
            access_types=lambda **_: [{"name": "Sorento Dealer"}],
            resolve_entity=validating_resolve_entity(_resolve_entity),
            probe=lambda **_: None,
        )

    @classmethod
    def _wire(cls, session_factory, engine_mod, monkeypatch, seen: list[Any]) -> None:
        set_chatbot_switches(session_factory, business_lane=True)
        bundle = cls._shipment_token_is_only_a_product()
        monkeypatch.setattr(
            engine_mod.business_services,
            "production_services",
            lambda db, *, space_id=None: bundle,
        )
        cls._record_select_tool_domain(monkeypatch, seen)
        monkeypatch.setattr(
            engine_mod.business_services,
            "fetch_services",
            lambda db: cls._fetch_services(),
        )
        monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: _no_probe_answer_services(),
        )

    def test_a_team_name_in_the_emission_never_becomes_a_domain(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """Turn b5b19cec: the emission's own `domain_hint`, coerced in `output_exchange`.

        The assertion is NOT on `select_tool` here, and the reason is the router rather
        than the guard: `route.is_low_signal`'s fourth clause sends a `business_query`
        with no domain to the clarifier lane, so a coerced emission never reaches the
        business lane at all - asking the customer which question they mean instead of
        searching tools for a team name. The carried case below is the one that does
        reach `select_tool`, because that turn has a domain of its own.
        """
        from app.services.chatbot import engine as engine_mod

        seen: list[Any] = []
        self._wire(session_factory, engine_mod, monkeypatch, seen)
        stub_parser(
            _parser_output(
                domain_hint="purchasing",
                intent_hint="check_incoming",
                routing={
                    "suggested_team": "purchasing",
                    "suggested_agent": "incoming_stock_enquiries",
                    "team_source": None,
                },
            )
        )
        stub_access()

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        row = (
            session_factory()
            .query(ChatbotTurn)
            .filter(ChatbotTurn.id == result.turn_id)
            .first()
        )
        understood = [r for r in (row.trace or []) if r.get("stage") == "understood"]
        assert understood, "the turn must have parsed for this to grade anything"
        # Ported (AC-1592): `understood`'s own `facts["domain"]` is `verdict.get(
        # "domain_hint")` (grep-confirmed, `engine.py`) - a deliberately HONEST, raw
        # record of what the parser said, written BEFORE `apply()` runs
        # `contracts.coerce_domain_hint` on it. F3's guard was never meant to sanitise
        # the trace's own historical log (contract 007's whole point is that the trace
        # says what actually happened); it protects what runs DOWNSTREAM - tool
        # selection - which the `seen` assertion below already grades directly.
        assert understood[0]["facts"]["domain"] == "purchasing", (
            "the trace's own record of what the parser said must stay honest, uncoerced"
        )
        assert "purchasing" not in seen, (
            "a team name reached tool selection, where it is not a DOMAIN_SPEC key and so "
            "names no tool at all"
        )

    def test_a_team_name_carried_in_the_contacts_memory_never_overwrites_this_turns_domain(
        self, session_factory, seeded, stub_parser, stub_access, system_settings_row, monkeypatch
    ) -> None:
        """Turn fca4aa5e, end to end: stored `variables.domain_hint = "purchasing"`, a
        container-hinted token the resolver only knows as a PRODUCT, and this turn's own
        `incoming` / `check_incoming`. `retype_shipment_miss` adopts the carried domain on
        exactly that shape, so before the engine cleaned the carried value `select_tool`
        was handed the team name even though the parse was clean."""
        from sqlalchemy import text as sql_text

        from app.services.chatbot import engine as engine_mod
        from tests.chatbot.test_engine import CONTACT_ID

        db = session_factory()
        db.execute(
            sql_text(
                "UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) "
                "WHERE respond_io_id = :cid"
            ),
            {
                "cid": str(CONTACT_ID),
                "sv": json.dumps(
                    {
                        "variables": {
                            "domain_hint": "purchasing",
                            "intent_hint": "stock_check",
                            "message_type": "business_query",
                        }
                    }
                ),
            },
        )
        db.commit()

        seen: list[Any] = []
        self._wire(session_factory, engine_mod, monkeypatch, seen)
        stub_parser(
            _parser_output(
                domain_hint="incoming",
                intent_hint="check_incoming",
                user_goal="checking when SRTWC8517 arrives",
                entities=[
                    {
                        "raw": "SRTWC8517",
                        "hint": "inbound_shipment",
                        "canonical_code": None,
                        "current_message": True,
                        "confident": True,
                    }
                ],
            )
        )
        stub_access()

        engine_mod.run_turn(_envelope(), session_factory=session_factory)

        assert seen, "the turn must have reached tool selection for this to grade anything"
        assert seen[0] == "incoming", (
            f"select_tool was handed {seen[0]!r}; the carried team name must never displace "
            "the domain this turn actually named"
        )
