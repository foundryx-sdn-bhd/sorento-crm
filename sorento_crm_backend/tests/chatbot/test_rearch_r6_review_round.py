"""R6 RED tests, PR #952 review round - Phase 3 findings from both reviewers
(`.claude/handoffs/rearch-phase3-security.md`, `rearch-phase3-reviewer.md`), written
BEFORE the coder's R6 fix pass per the tester brief (20 Sep 2026, tester 35).

**Zero live runs**: no journey runner, no turns to :8081, no OpenAI calls - every test
here is pure/DB-fixture pytest (Postgres, `session_factory` blank schema) or a direct
call into the module under test. Every scenario seeds its own rows.

**Test shape**: engine-level tests drive `engine.run_turn` (via harnesses reused or
adapted from `test_rearch_r5_production_decides.py`) with the REAL resolver, gate and
narrower over seeded rows, doubling only the MCP boundary (`FetchServices.mcp_call` and
`AnswerServices.mcp_probe`). A handful of items (11a, the `narrow.decide`/`_miss_question`
unit checks) are deliberately function-level, matching the precedent
`test_rearch_r5_production_decides.py::TestSilentPrefixFilter` and
`test_rearch_r1_answer_half_reattach_defects.py::TestNoRosterIsEverAskedWithFewerThanTwoOptions`
already set for "this item is about the DECISION function's own policy classification,
not engine precedence."

No test calls `answer_bridge.answer_for` directly (except where explicitly noted as a
function-level unit check of a helper `answer_for` itself calls, never the bridge's own
precedence path) and no test hand-builds `payload["_exit_kind"]` or seeds the asking
turn's own Pending - every roster is raised by a REAL first turn.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.models.access import ContactAccessType
from app.services.chatbot import answer_bridge
from app.services.chatbot import engine as engine_mod
from app.services.chatbot.lanes.business import answer as answer_mod
from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.services import (
    AnswerServices,
    FetchServices,
    ResolveGateServices,
)
from app.services.chatbot.lanes.business.tier_gate import recompose
from app.services.chatbot.tail import scope_block as scope_block_mod
from app.services.chatbot.turn import narrow as narrow_mod
from app.services.chatbot.turn.policy import default_policy
from app.services.chatbot.turn.state import Focus, Profile
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot._turn_helpers import do_window
from tests.chatbot.test_engine import (
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,  # noqa: F401 - pytest fixture, must stay module-level to be discovered
    stub_access,  # noqa: F401
    stub_parser,  # noqa: F401
)
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import (
    _capturing_mcp,
    _present_response,
    _run_turn as _run_turn_fake_resolver,
    _seed_contact as _seed_business_contact,
    _session_of,
)
from tests.chatbot.test_rearch_r3_bridge_engine import (
    _ambiguous_hanlim_resolve_services,
)
from tests.chatbot.test_rearch_r3_roster_cap import (  # noqa: F401 - pytest fixtures
    _permissions,
    client,
    pg_db,
)
from tests.chatbot.test_rearch_r5_production_decides import (
    _mcp_double,
    _mcp_probe_for,
    _order_envelope_json,
    _real_resolve_with_safe_probe,
    _seed_contact_and_get,
)
from tests.chatbot.test_sales_report_lane import SALES_REPORT_DENIAL


# --------------------------------------------------------------------------- #
# Harness: the r5 file's own `_run_turn_with_mcp_call`, PLUS a real, DB-backed
# `access_types` (r5's own `_real_resolve_with_safe_probe` stubs `access_types` to
# `lambda **_: []` - fine for turns with no tier entitlement question, wrong for
# every test in this file, which is ABOUT entitlement) and a configurable
# `attributes` grant list (r5's own harness hardcodes `attributes: []` - wrong for
# item 9, which needs `purchase_orders.placed`).
# --------------------------------------------------------------------------- #


def _mark_workspace_default(session_factory) -> None:
    """`app.services.chatbot.head.access.default_space_id` reads
    `RespondWorkspaceService.get_default()`, which filters on `is_default IS TRUE` -
    `test_outstanding_lane._seed_contact`'s raw INSERT never sets it. Every test in
    this file needs the REAL `access_types` binding (`ContactAccessTypeService`),
    which needs a real, non-null `space_id`, which needs this flag - without it
    `default_space_id` returns `None` and `resolve_active_access_levels_for_contact`
    raises `contact_id and space_id are both required`, degrading the whole
    resolver to `resolve_kinds`'s own broad `except Exception` ("the resolver did
    not answer") - a test-harness gap, not a production one, since production reads
    a real seeded default workspace."""
    from sqlalchemy import text as _sql_text

    db = session_factory()
    db.execute(_sql_text("UPDATE respond_workspaces SET is_default = true WHERE space_id = '364817'"))
    db.commit()
    db.close()


def _seed_access_type(session_factory, *, code: str, name: str) -> None:
    db = session_factory()
    if db.query(ContactAccessType).filter(ContactAccessType.code == code).first() is None:
        db.add(ContactAccessType(code=code, name=name))
        db.commit()
    db.close()


def _grant_access_type(session_factory, *, code: str) -> None:
    from sqlalchemy import text as _sql_text

    db = session_factory()
    db.execute(
        _sql_text(
            "INSERT INTO respond_contact_access_types (contact_id, access_type_code) "
            "SELECT id, :code FROM respond_contacts WHERE respond_io_id = :cid "
            "ON CONFLICT DO NOTHING"
        ),
        {"cid": str(CONTACT_ID), "code": code},
    )
    db.commit()
    db.close()


def _real_access_types(db) -> Any:
    from app.services.contact_access_type_service import ContactAccessTypeService

    def call(*, contact_id: Any, space_id: Any) -> list[dict[str, Any]]:
        return ContactAccessTypeService(db).resolve_active_access_levels_for_contact(
            str(contact_id or "").strip(), str(space_id or "").strip()
        )

    return call


def _real_resolve_with_real_entitlement(monkeypatch) -> None:
    """The SAME real `resolve_entity` `_real_resolve_with_safe_probe` wires, plus a
    REAL, DB-backed `access_types` (`ContactAccessTypeService`, the production
    binding `lanes/business/services.py::_access_types` builds) instead of that
    helper's own `lambda **_: []` - every test in this file is ABOUT entitlement, so
    a stubbed-empty one would prove nothing."""
    from tests.chatbot.test_engine_company_scope import _real_resolve_entity

    def _bundle(db, *, space_id: str | None = None) -> ResolveGateServices:
        return ResolveGateServices(
            access_types=_real_access_types(db),
            resolve_entity=_real_resolve_entity(db),
            probe=lambda **_: None,
        )

    monkeypatch.setattr(engine_mod.business_services, "production_services", _bundle)


def _run_turn_engine(
    session_factory,
    monkeypatch,
    *,
    qf,
    text_body: str,
    msg_id: str,
    mcp_call,
    answer_mcp_probe=None,
    attributes: list[str] | None = None,
    real_entitlement: bool = False,
):
    """`test_rearch_r5_production_decides.py::_run_turn_with_mcp_call`, copied rather
    than imported so this file can vary the two things that function hardcodes:
    `attributes` (item 9 needs `purchase_orders.placed`) and whether `access_types`
    is real-DB-backed (every tier/entitlement test in this file needs it; r5's own
    helper always stubs it empty, which is right for r5's own non-tier scenarios but
    wrong for every test here)."""
    from app.services.chatbot.head import parser as parser_mod
    from tests.chatbot.test_engine import _envelope
    from tests.chatbot.test_outstanding_lane import _enable_business_lane

    _enable_business_lane(session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": attributes or [], "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")

    def fake_resolve_config(db, *, current_date, override_version_id=None):
        return parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=1, provider="openai", model="gpt-test", api_key="sk-test",
        )

    monkeypatch.setattr(parser_mod, "resolve_config", fake_resolve_config)
    monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
    if real_entitlement:
        _real_resolve_with_real_entitlement(monkeypatch)
    else:
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


def _run_turn_engine_real(
    session_factory, monkeypatch, *, qf, text_body, msg_id, mcp_response, **kwargs
):
    mcp_call, captured = _capturing_mcp(mcp_response)
    result = _run_turn_engine(
        session_factory, monkeypatch, qf=qf, text_body=text_body, msg_id=msg_id, mcp_call=mcp_call,
        **kwargs,
    )
    return result, captured


def _capturing_probe(tool_rows: dict[str, list[dict[str, Any]]]):
    """Like `test_rearch_r5_production_decides.py::_mcp_probe_for`, but also records
    every `(name, args)` call - needed where the assertion is about WHETHER/HOW a
    tool was probed, not only what it answered."""
    base = _mcp_probe_for(tool_rows)
    calls: list[tuple[str, dict[str, Any]]] = []

    def _probe(name: str, args: dict[str, Any]) -> Any:
        calls.append((name, dict(args)))
        return base(name, args)

    return _probe, calls


THREE_TIERS = ["Sorento Dealer", "Sorento Office", "End User"]


def _seed_three_tiers_two_entitled(session_factory) -> None:
    """A system with THREE access tiers (security item 1's own wording); the
    contact is entitled to exactly TWO of them ("Sorento Dealer", "Sorento
    Office"), never the third ("End User")."""
    _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")
    _seed_access_type(session_factory, code="sorento_office", name="Sorento Office")
    _seed_access_type(session_factory, code="end_user", name="End User")
    _grant_access_type(session_factory, code="sorento_dealer")
    _grant_access_type(session_factory, code="sorento_office")


# --------------------------------------------------------------------------- #
# SECURITY item 1 (B1) - a tier PICK turn's own promotion fetch must be scoped to
# the RESOLVER's entitlement, never the parser's (empty, 249/249 real captures)
# `access_levels`.
# --------------------------------------------------------------------------- #


class TestTierPickFetchUsesResolverEntitlementNeverParserWords:
    @pytest.mark.parametrize(
        "answer_text,answer_overrides,chosen_tiers",
        [
            pytest.param("1", {"reference_positions": [1]}, ["office"], id="single"),
            pytest.param(
                "1 and 2", {"reference_positions": [1, 2]}, ["office", "dealer"], id="one-and-two"
            ),
            pytest.param("all", {"broaden_axis": "all"}, ["office", "dealer"], id="all"),
        ],
    )
    def test_promotion_fetch_access_levels_is_the_entitled_subset_never_empty_never_the_c_tier(
        self, session_factory, monkeypatch, answer_text: str, answer_overrides: dict, chosen_tiers: list[str]
    ) -> None:
        """AC-1698 + security B1: the ANSWERING turn's own parser verdict carries
        `access_levels: []` - the measured real-capture shape (249/249,
        `documentation/plans/chatbot/parser-prompt-inventory.md:106`), never the
        contact's entitled compound names (that assumption is what
        `test_rearch_r3_bridge_engine.py`'s own `entitled_names` fixture wrongly
        fed the answer turn - see the sibling fix below). The promotion tool's own
        `access_levels` argument must still be exactly the picked, ENTITLED tier(s)
        - computed from resolver entitlement, never from what the parser said."""
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_two_entitled(session_factory)
        code = unique_code("ZZTTIER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        entitled_names = ["Sorento Dealer", "Sorento Office"]

        qf1 = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )
        result1, captured1 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"promo for {code}",
            msg_id=f"zzt-r6-tier-ask-{answer_text}", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        reply1 = (result1.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "tier_pick", (
            f"test setup sanity: a promotion ask for a contact entitled to 2 of 3 "
            f"tiers must raise the tier picker: {reply1!r}"
        )
        options = open_question.get("options") or []
        assert len(options) == 2, (
            f"the ask's own options must be ENTITLED-only (2), never all 3 system "
            f"tiers: {options!r}"
        )
        for opt in options:
            assert "End" not in str(opt.get("label") or ""), (
                f"the third, unentitled tier must never be offered: {options!r}"
            )

        # The REAL capture shape: the answering turn's own parser verdict carries
        # NO access_levels at all - never the contact's entitled names.
        answer_qf = _parser_output(
            domain_hint=None, intent_hint=None, entities=[], access_levels=[],
            **answer_overrides,
        )
        result2, captured2 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=answer_qf, text_body=answer_text,
            msg_id=f"zzt-r6-tier-answer-{answer_text}", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        promo_calls = [c for c in captured2 if c[0] == fetch_mod.TIER_PROBE_TOOL]
        assert promo_calls, f"{answer_text!r} must run at least one promotion fetch: {captured2}"
        requested_access_levels: set[str] = set()
        for _name, args in promo_calls:
            requested_access_levels.update(args.get("access_levels") or [])
        expected_access_levels = set(recompose(chosen_tiers, [], entitled_names)["access_levels"])
        assert requested_access_levels != set(), (
            "security B1: the promotion fetch's access_levels must never be empty - "
            f"an empty list is read downstream as 'no tier filter at all' "
            f"(contact_access_type_service.translate_names_to_codes), answering "
            f"with promotions of EVERY tier: {captured2!r}"
        )
        assert "End User" not in requested_access_levels, (
            f"the unentitled third tier must never reach the fetch: {captured2!r}"
        )
        assert requested_access_levels == expected_access_levels, (
            f"{answer_text!r} (chosen tiers {chosen_tiers}) must reach the promotion "
            f"fetch scoped to exactly the RESOLVER's entitled, recomposed access "
            f"levels - got {requested_access_levels!r}, expected "
            f"{expected_access_levels!r}: {captured2!r}"
        )


# Item 1's second half (fix the existing fixture that hid this bug) is done
# directly on `test_rearch_r3_bridge_engine.py::TestPromotionAskMultiSelect::
# test_answering_the_tier_pick_runs_the_promotion_fetch_per_chosen_tier` - its own
# `answer_qf` now sends `access_levels=[]` (the measured real-capture shape)
# instead of `entitled_names`, and is red for the identical reason as the class
# above (confirmed by running it: 3 failed, `access_levels` arg is `[]`). Not
# duplicated here - one assertion, one place.


# --------------------------------------------------------------------------- #
# SECURITY item 2 (S1) - a bare "promo" with NO product entity at all. `resolve_
# kinds` early-returns on `not entities` (`turn_runtime.resolve_kinds`: "if not
# entities: return ResolveOutcome(...)"), so the bridge's entitlement-aware
# access_ask arm (which needs a `resolver_payload`) never fires; `narrow.decide`'s
# own `tier_pick` arm (`policy_value == "narrow_by_tier"`, no candidates, no
# profile.tier) mints an EMPTY-options ask, and `turn/apply.py:1152-1166` fills it
# from `policy.tier_order` - literally every tier the system has, regardless of
# entitlement - under `turn/compose.py`'s generic "Which one do you mean?" header
# (R4 deleted the `tier_pick` entry from `_ASK_HEADERS`).
# --------------------------------------------------------------------------- #


class TestBarePromoTierAskIsEntitlementScoped:
    def test_options_are_entitled_only_and_header_is_the_access_level_question(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_two_entitled(session_factory)
        entitled_names = ["Sorento Dealer", "Sorento Office"]

        qf = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion", entities=[],
        )
        result, captured = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf, text_body="promo",
            msg_id="zzt-r6-bare-promo-ask", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        reply = (result.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []

        # Computed via the REAL production function this ask should be answered by,
        # never hand-typed - `answer.access_level_choice_message`'s own numbered
        # tier ask, fed the contact's real entitlement.
        expected_text = answer_mod.access_level_choice_message(
            {"name": entitled_names, "entitled_tiers": ["dealer", "office"]},
            parser=qf,
        )["escalate_message"]

        assert "Which one do you mean?" not in reply, (
            f"security S1: a bare promo tier ask must never print the generic "
            f"narrow.py fallback header: {reply!r}"
        )
        assert reply == expected_text, (
            f"security S1: a bare promo tier ask must be the production access-"
            f"level question, entitled tiers only: got {reply!r}, expected "
            f"{expected_text!r}"
        )
        assert len(options) == 2, (
            f"security S1: the ask's own options must be ENTITLED-only (2), never "
            f"every tier in policy.tier_order (3): {options!r}"
        )
        for opt in options:
            assert str(opt.get("code") or opt.get("label") or "").strip().lower() not in (
                "end_user", "end user"
            ), f"the unentitled third tier must never be offered: {options!r}"

    def test_the_fetch_that_follows_the_pick_is_still_tier_filtered(
        self, session_factory, monkeypatch
    ) -> None:
        """Second invariant, same family as item 1: whichever tier the bare ask's
        pick settles, the resulting promotion fetch's own `access_levels` must
        never be empty and must never carry the unentitled third tier."""
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_two_entitled(session_factory)
        code = unique_code("ZZTBAREP")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf1 = _parser_output(domain_hint="promotion", intent_hint="check_promotion", entities=[])
        _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf1, text_body="promo",
            msg_id="zzt-r6-bare-promo-pick-ask", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert (open_question.get("options") or []), (
            f"test setup sanity: a bare promo ask must raise SOME tier picker: {open_question!r}"
        )

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[1], access_levels=[],
        )
        result2, captured2 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf2, text_body="1",
            msg_id="zzt-r6-bare-promo-pick-answer", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        promo_calls = [c for c in captured2 if c[0] == fetch_mod.TIER_PROBE_TOOL]
        assert promo_calls, f"picking a bare-ask tier option must run a promotion fetch: {captured2!r}"
        access_levels: set[str] = set()
        for _name, args in promo_calls:
            access_levels.update(args.get("access_levels") or [])
        assert access_levels != set(), (
            f"security B1 (same family): the post-pick promotion fetch must never "
            f"go out with an empty access_levels filter: {captured2!r}"
        )
        assert "End User" not in access_levels, (
            f"the unentitled third tier must never reach the post-pick fetch: {captured2!r}"
        )


# --------------------------------------------------------------------------- #
# SECURITY item 3 (S2) - the cross-domain probe sends the PARSER's claimed access
# levels verbatim (`answer_bridge.py:549 entities_names=None` ->
# `crossdomain_probe_args`'s `else: access_levels = list(parser_levels)`), never
# intersected with what the contact actually holds.
# --------------------------------------------------------------------------- #


class TestCrossdomainProbeIntersectsClaimedLevelsWithEntitlement:
    def test_a_claimed_but_unentitled_access_level_never_reaches_the_probe(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_access_type(session_factory, code="sorento_dealer", name="Sorento Dealer")
        _grant_access_type(session_factory, code="sorento_dealer")
        code = unique_code("ZZTXDL")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
            # The parser CLAIMS an access level this contact does NOT hold - a
            # real shape (the LLM parser reads the customer's own words, not
            # their entitlement row).
            access_levels=["Sorento End User"],
        )

        def _primary(name: str, args: dict[str, Any]) -> str:
            return json.dumps({"has_result": False, "items": []})

        mcp_call, _captured_primary = _mcp_double(other=_primary)
        probe_fn, probe_calls = _capturing_probe(
            {
                "crm_inventory_stock_balance_list": [
                    {
                        "product_code": code, "total_qty": 5, "outstanding_qty": 0,
                        "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 5}],
                    }
                ]
            }
        )
        _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=f"incoming for {code}",
            msg_id="zzt-r6-xd-entitlement", mcp_call=mcp_call, answer_mcp_probe=probe_fn,
            real_entitlement=True,
        )
        stock_probes = [c for c in probe_calls if c[0] == "crm_inventory_stock_balance_list"]
        assert stock_probes, f"the cross-domain stock probe must have run: {probe_calls!r}"
        _name, args = stock_probes[0]
        sent_levels = (args.get("semantic_input") or {}).get("access_levels")
        assert sent_levels != ["Sorento End User"], (
            "security S2: the probe's own access_levels must never be the parser's "
            f"claimed level VERBATIM, unintersected with the contact's real "
            f"entitlement (here, only 'Sorento Dealer'): {args!r}"
        )
        assert "Sorento End User" not in (sent_levels or []), (
            f"an unentitled claimed level must never reach the cross-domain probe: {args!r}"
        )


# --------------------------------------------------------------------------- #
# SECURITY item 4 (S2, engine.py ordering) - extends AC-1686's own class
# (`test_sales_report_grant_security.py::TestSF1RosterMustNotPrecedeTheGrantRefusal`)
# with the OTHER trigger the security review names: TWO distinct customer WORDS in
# one ask (not one ambiguous name resolving to several DB rows) reaches `turn/
# narrow.py`'s `must_narrow_one` roster arm via the FOCUS carry alone (the resolver
# never ran - SF-1's own fix gates `resolve_kinds` on the refusal), which `engine.py`'s
# ASK section (`:2025`) composes BEFORE the refusal at `:2056` even though `plan.ask`
# is not `None`.
# --------------------------------------------------------------------------- #


class TestSF1TwoNamedCustomerWordsAlsoDeniedBeforeAnyRoster:
    def test_two_customer_words_in_one_ungranted_sales_report_ask_is_denied_only(
        self, session_factory, monkeypatch
    ) -> None:
        from tests.chatbot.test_outstanding_lane import _qf, _seed_contact

        _seed_contact(session_factory, variables={})
        result, captured = _run_turn_fake_resolver(
            session_factory, monkeypatch,
            qf=_qf(
                order_status="sales_report",
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                    {"raw": "chin chun", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
            ),
            text_body="sales report for hanlim and chin chun",
            msg_id="zzt-r6-sf1-two-words-1",
            attributes=[],
        )
        reply = (result.reply or {}).get("text") or ""
        assert captured == [], (
            f"no tool may run for an ungranted sales-report ask naming two customer "
            f"words: {captured}"
        )
        assert reply.strip() == SALES_REPORT_DENIAL, (
            f"security S2: a contact with no sales_orders.sales_report grant must be "
            f"refused outright, never shown a roster of their own typed words first: "
            f"{reply!r}"
        )
        open_question = (_session_of(session_factory).get("open_question") or {})
        assert not open_question, (
            f"no roster/pick may be armed for a turn that was refused outright: "
            f"{open_question!r}"
        )
        for word in ("hanlim", "chin chun", "Hanlim", "Chin Chun"):
            assert word not in reply, (
                f"a typed customer word must never be echoed as an option on a "
                f"refused turn: {reply!r}"
            )


# --------------------------------------------------------------------------- #
# SECURITY item 5 (N1) - `engine.py`'s scope-block gate reads `not envelope_missed
# (envelopes[0])`, but `turn/fetch.py::envelope_missed` returns False for a DENIED
# envelope, so a whole-domain refusal (`turn_runtime.envelope_of`'s own
# `outcome == "access_denied"` -> `denied: True`) can get the Customer/Product/
# Dates scope block prepended.
#
# Measured: the ONLY producer of an envelope-level `denied=True` today is the
# `purchase_cost` domain's whole-domain grant gate (`answer.DOMAIN_GRANT_REQUIRED`),
# and `tail/scope_block.py::_DATE_SCOPE_DOMAINS` is `{"order"}` only - so no domain
# combination in production TODAY can make this bug user-visible (confirmed:
# `test_last_cost_gate.py::TestAC16DomainRefusedWithoutGrant` gets the bare refusal
# text, no scope block, because `search_scope_header` already returns `None` for
# "purchase_cost"). This test isolates the ENGINE gate's own missing "not denied"
# check by widening `_DATE_SCOPE_DOMAINS` for its own duration (a test double on a
# frozen constant, never `app/` source) - the real fix belongs in `engine.py`'s own
# condition, which this proves independently of whether any domain happens to
# combine both properties today.
# --------------------------------------------------------------------------- #


class TestDeniedEnvelopeNeverGetsTheScopeBlockPrepended:
    def test_a_whole_domain_refusal_is_never_prefixed_by_the_scope_block(
        self, session_factory, seeded, stub_parser, stub_access, monkeypatch
    ) -> None:
        from app.services.chatbot_reply_copy import CHATBOT_REPLY_ACCESS_DENIED
        from tests.chatbot.test_last_cost_gate import _cost_parser_output, _wire

        expected_text = CHATBOT_REPLY_ACCESS_DENIED.replace("{{team}}", "purchase cost")

        def mcp_call(name: str, args: dict) -> str:
            raise AssertionError(f"no tool call is expected without the grant: {name}")

        _wire(session_factory, monkeypatch, mcp_call=mcp_call)
        stub_parser(_cost_parser_output())
        stub_access(attributes=[])
        # Test double only: widen the scope block's own domain gate so this
        # already-denied envelope is no longer excluded by `search_scope_header`'s
        # own `domain != "order"` early return - isolates the ENGINE's own missing
        # `denied` guard, per the class docstring.
        monkeypatch.setattr(
            scope_block_mod, "_DATE_SCOPE_DOMAINS", frozenset({"order", "purchase_cost"})
        )

        result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

        text = result.reply.get("text") or ""
        assert text == expected_text, (
            "security N1: a whole-domain refusal must never be prefixed by the "
            f"Customer/Product/Dates scope block: {text!r}"
        )


# --------------------------------------------------------------------------- #
# SECURITY item 6 (N2) - `tail/scope_block.py` has no uuid guard, unlike
# `turn_runtime._answer_subject`. Defence in depth: a uuid-shaped code/title must
# never print in the Customer/Product line, whatever produced it.
# --------------------------------------------------------------------------- #


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class TestScopeBlockNeverPrintsARawUuid:
    def test_a_uuid_shaped_gate_row_title_never_reaches_the_customer_line(self) -> None:
        uuid_value = "9f5b2b1a-1e2b-4c3d-8e9f-0a1b2c3d4e5f"
        qf = {"entities": [], "date_filter_start": None, "date_filter_end": None}
        gate_json = {
            "compatible_entities": [
                {"entity_type": "customer", "code": uuid_value, "title": uuid_value}
            ]
        }
        header = scope_block_mod.search_scope_header(
            domain="order", qf=qf, gate_json=gate_json, resolver_json={},
        )
        assert header is not None, "test setup sanity: the order domain must produce a header"
        assert not _UUID_RE.search(header), (
            f"security N2: a uuid-shaped gate row value must never print in the "
            f"scope block: {header!r}"
        )

    def test_a_uuid_shaped_product_code_never_reaches_the_product_line(self) -> None:
        uuid_value = "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
        qf = {"entities": [], "date_filter_start": None, "date_filter_end": None}
        gate_json = {
            "compatible_entities": [
                {"entity_type": "product", "code": uuid_value, "title": uuid_value}
            ]
        }
        header = scope_block_mod.search_scope_header(
            domain="order", qf=qf, gate_json=gate_json, resolver_json={},
        )
        assert header is not None, "test setup sanity: the order domain must produce a header"
        assert not _UUID_RE.search(header), (
            f"security N2: a uuid-shaped gate row value must never print in the "
            f"scope block: {header!r}"
        )


# --------------------------------------------------------------------------- #
# SECURITY item 7 (S3) - `roster_cap` has a floor (`ge=2`) but no ceiling. Captain
# ruling: valid range is 2 to 50 inclusive, enforced at the API, no new migration.
# --------------------------------------------------------------------------- #


class TestRosterCapHasACeiling:
    @pytest.mark.parametrize("value", [0, 1, 51])
    def test_put_rejects_out_of_range_roster_cap(self, client, value: int) -> None:
        from tests.chatbot.test_rearch_r3_roster_cap import ENTITY_KINDS_BASE, _kind_body

        kind = unique_code("kind")
        create_resp = client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=10))
        assert create_resp.status_code in (200, 201), create_resp.text
        resp = client.put(f"{ENTITY_KINDS_BASE}/{kind}", json=_kind_body(kind, roster_cap=value))
        assert resp.status_code == 422, (
            f"security S3: roster_cap={value} must be rejected 422 - a cap this "
            f"wide (or non-positive) drives an unbounded printed roster and MCP "
            f"probe list (gate.py's own reps/cust_probe_entities): {resp.text}"
        )

    @pytest.mark.parametrize("value", [2, 50])
    def test_put_accepts_boundary_values(self, client, value: int) -> None:
        from tests.chatbot.test_rearch_r3_roster_cap import ENTITY_KINDS_BASE, _kind_body

        kind = unique_code("kind")
        create_resp = client.post(ENTITY_KINDS_BASE, json=_kind_body(kind, roster_cap=10))
        assert create_resp.status_code in (200, 201), create_resp.text
        resp = client.put(f"{ENTITY_KINDS_BASE}/{kind}", json=_kind_body(kind, roster_cap=value))
        assert resp.status_code == 200, resp.text
        assert resp.json().get("roster_cap") == value


# --------------------------------------------------------------------------- #
# REVIEWER item 8 (B1) - `turn/apply.py::_did_you_mean` mints a ONE-option roster
# echoing the customer's own unplaced token, bypassing the bridge entirely
# (`engine.py`'s miss arm is guarded on `plan.ask is None`, and `_did_you_mean`
# sets `plan.ask` before the narrower or the bridge ever runs).
# --------------------------------------------------------------------------- #


_DID_YOU_MEAN_DOMAINS = [d.name for d in default_policy().domains if d.supported]


class TestOneOptionDidYouMeanNeverEchoesTheUnplacedToken:
    @pytest.mark.parametrize("domain", _DID_YOU_MEAN_DOMAINS)
    def test_an_unresolved_product_token_never_rosters_fewer_than_two_or_the_typed_word(
        self, session_factory, monkeypatch, domain: str
    ) -> None:
        _seed_contact_and_get(session_factory)
        token = unique_code("ZZTDYMB")

        qf = _parser_output(
            domain_hint=domain, intent_hint=f"check_{domain}",
            entities=[
                {"raw": token, "hint": "product", "canonical_code": None, "current_message": True, "confident": False}
            ],
        )
        result, _captured = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf, text_body=f"{domain} for {token}",
            msg_id=f"zzt-r6-dym-b1-{domain}", mcp_response={"data": []},
        )
        reply = (result.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        kind = str(open_question.get("kind") or "")

        # AC-1691 is about ROSTERS. Production's own miss + escalate offer
        # (`engine._question_offered`, `engine.py:3689-3696`) is a `team_pick`
        # with exactly one "Yes" option and `expects: "yes_no"` - a yes/no
        # confirmation, not a roster, and correct per
        # `test_rearch_r4_bridge_miss.py:808`. Scope the option-count floor to
        # actual roster kinds only: `*_pick` AND `expects != "yes_no"`.
        is_roster_kind = kind.endswith("_pick") and open_question.get("expects") != "yes_no"
        if is_roster_kind:
            assert not (0 < len(options) < 2), (
                f"AC-1691 [{domain}]: no ROSTER may ever carry fewer than two "
                f"options - a one-option roster is not a real choice: {open_question!r}"
            )
        for opt in options:
            label = str(opt.get("label") or opt.get("code") or "")
            assert label.strip().lower() != token.strip().lower(), (
                f"AC-1692 [{domain}]: the unplaced typed token must never be offered "
                f"back as the roster's own only option: {open_question!r}"
            )
        assert reply != f"Which one do you mean?\n1. {token}", (
            f"[{domain}]: the exact one-option did-you-mean echo the reviewer "
            f"measured must never be the reply: {reply!r}"
        )


class TestAC1691RosterActuallyRostersNotJustAbsent:
    """Reviewer N9: `test_rearch_r1_answer_half_reattach_defects.py::
    TestNoRosterIsEverAskedWithFewerThanTwoOptions::test_roster_pick_never_has_
    fewer_than_two_options` early-returns the moment `outcome.ask_kind is None`,
    so it passes identically whether `decide()` correctly settled a single
    candidate OR simply never rosters anything at all - an absence-only guard
    that cannot tell the two apart. This is the companion, NOT a replacement:
    the SAME parametrization, but with TWO real, distinct ambiguous candidates
    (a shape every one of these policy values must genuinely roster, `must_
    narrow_one` included - `_choices(candidates, family_grouping) > 1` is
    exactly its own trigger for that case) - `ask_kind` must NEVER be None here.

    `kind="customer"` ONLY, not "product" too: AC-1690 (R5/R6, already correct
    and independently pinned by `test_rearch_r5_production_decides.py::
    TestSilentPrefixFilter`) makes `decide()` return `ask_kind=None` for
    `kind == "product"` UNCONDITIONALLY now - gate.py owns that roster - so
    including "product" here would wrongly flag an intentional, already-shipped
    design decision as an N9-class absence bug."""

    @pytest.mark.parametrize("kind", ["customer"])
    @pytest.mark.parametrize("policy_value", sorted(narrow_mod._ROSTER_POLICIES))
    def test_two_distinct_candidates_genuinely_roster(self, policy_value: str, kind: str) -> None:
        candidate_a = {
            "raw": "ZZTROSTERA", "canonical_code": "ZZTROSTERA",
            "uuid": "aaaaaaaa-0000-0000-0000-000000000001", "current_message": True,
        }
        candidate_b = {
            "raw": "ZZTROSTERB", "canonical_code": "ZZTROSTERB",
            "uuid": "bbbbbbbb-0000-0000-0000-000000000002", "current_message": True,
        }
        focus = Focus()
        outcome = narrow_mod.decide(
            kind=kind, policy_value=policy_value, focus=focus, profile=Profile(),
            resolved_candidates=[candidate_a, candidate_b],
        )
        assert outcome.ask_kind is not None, (
            f"policy_value={policy_value!r} kind={kind!r}: TWO distinct, genuinely "
            f"ambiguous candidates must roster a real choice, never silently settle "
            f"through as though there were nothing to ask about: {outcome!r}"
        )
        assert len(outcome.ask_options) >= 2, (
            f"policy_value={policy_value!r} kind={kind!r}: a roster over two "
            f"distinct candidates must offer at least those two: {outcome!r}"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 9 (B3) - AC-1705's PO rung never runs through the bridge:
# `answer_bridge.py::_fold_crossdomain_ladder` calls `answer.run_crossdomain` with
# NO `crossdomain_ladder` argument, so `_next_crossdomain_rung` always returns
# `None` regardless of what `system_settings.chatbot_crossdomain_ladder` (migration
# `491_chatbot_ladder_incoming_po`, D7 owner ruling 8 Sep 2026) actually configures.
#
# Repro named in the migration's own docstring: "hav incoming?" for a product with
# no incoming, no stock, but an open PO line - answered "No incoming and no stock
# for CODE." and offered escalation, never the PO rung's own on-order sentence.
#
# Measured how main's own `complete_answer` gets this right, for the docstring:
# `lanes/business/__init__.py::complete_answer` reads `engine._crossdomain_ladder`
# (a plain read of `system_settings.chatbot_crossdomain_ladder`, defaulting to
# `answer.DEFAULT_CROSSDOMAIN_LADDER` when no row/column exists) and passes it as
# `run_crossdomain(..., crossdomain_ladder=ladder)` at its own `_run_miss_half`
# call site - the bridge's `_fold_crossdomain_ladder` is the direct analogue that
# never received the same argument.
# --------------------------------------------------------------------------- #


class TestIncomingMissClimbsToThePORungThroughTheBridge:
    def test_no_incoming_no_stock_but_an_open_po_line_reaches_the_po_rung(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        code = unique_code("ZZTPORUNG")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        qf = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )

        def _primary(name: str, args: dict[str, Any]) -> str:
            # The primary ask tool (`crm_incoming_stock_list`) - a genuine miss.
            return json.dumps({"has_result": False, "items": []})

        mcp_call, _captured_primary = _mcp_double(other=_primary)
        probe_fn, probe_calls = _capturing_probe(
            {
                # First rung (stock) ALSO a miss - forces the climb to the second,
                # PO rung.
                "crm_inventory_stock_balance_list": [],
                "crm_procurement_po_placed_list": [
                    {
                        "fields": [
                            {"key": "po_number", "label": "PO Number", "value": "PO-9001"},
                            {"key": "product_code", "label": "Product Code", "value": code},
                            {"key": "ordered_qty", "label": "Ordered Qty", "value": 10},
                            {"key": "outstanding_qty", "label": "Outstanding Qty", "value": 10},
                        ],
                        "kind": "po",
                    }
                ],
            }
        )
        _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=f"hav incoming for {code}?",
            msg_id="zzt-r6-po-rung", mcp_call=mcp_call, answer_mcp_probe=probe_fn,
            attributes=["purchase_orders.placed"], real_entitlement=True,
        )
        probed_tools = [name for name, _args in probe_calls]
        assert "crm_procurement_po_placed_list" in probed_tools, (
            "reviewer B3: an incoming ask with no incoming, no stock, but an open PO "
            f"line must climb the ladder to the PO rung - the bridge never passes "
            f"`crossdomain_ladder` into `run_crossdomain`, so `_next_crossdomain_rung` "
            f"always returns None regardless of the configured ladder: probed "
            f"{probed_tools!r}"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 10 (S1) - `turn/fetch.py::_climb` re-fetches
# `crm_inventory_stock_balance_list` on the SAME turn `answer_bridge.
# _fold_crossdomain_ladder` also probes it for - two live MCP calls (one via each
# seam) for one fact, and the `_climb` rung's own envelope is thrown away
# (`engine.py` composes from `envelopes[0]` only).
# --------------------------------------------------------------------------- #


class TestOneCrossdomainLadderPerTurn:
    def test_stock_is_probed_exactly_once_across_both_mcp_seams(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTONELADDER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        qf = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )

        stock_row = {
            "product_code": code, "total_qty": 42, "outstanding_qty": 0,
            "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 42}],
        }

        def _primary(name: str, args: dict[str, Any]) -> str:
            if name == "crm_inventory_stock_balance_list":
                return _present_response()(name, json.dumps({"data": [stock_row]}))
            return json.dumps({"has_result": False, "items": []})

        mcp_call, captured_primary = _mcp_double(other=_primary)
        probe_fn, probe_calls = _capturing_probe(
            {"crm_inventory_stock_balance_list": [stock_row]}
        )
        _run_turn_engine(
            session_factory, monkeypatch, qf=qf, text_body=f"incoming for {code}",
            msg_id="zzt-r6-one-ladder", mcp_call=mcp_call, answer_mcp_probe=probe_fn,
        )
        primary_stock_calls = [c for c in captured_primary if c[0] == "crm_inventory_stock_balance_list"]
        probe_stock_calls = [c for c in probe_calls if c[0] == "crm_inventory_stock_balance_list"]
        total = len(primary_stock_calls) + len(probe_stock_calls)
        assert total == 1, (
            "reviewer S1: one incoming miss must probe "
            "crm_inventory_stock_balance_list EXACTLY ONCE across BOTH MCP seams "
            f"(the old turn/fetch.py::_climb rung AND the bridge's own ladder) - "
            f"got {total} ({len(primary_stock_calls)} via FetchServices.mcp_call, "
            f"{len(probe_stock_calls)} via AnswerServices.mcp_probe)"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 11a (S5) - `answer_bridge._miss_question`'s did-you-mean roster
# arm mints a Pending with NO minimum-two-options guard, unlike `_tier_options`
# and `_offer_answer` (both use `_MIN_ROSTER_OPTIONS`). Function-level (this is
# `answer_for`'s own INTERNAL helper, never the bridge's precedence path - no
# `answer_bridge.answer_for` call here, per the module docstring's own carve-out).
# --------------------------------------------------------------------------- #


class TestMissArmRosterHasNoMinimumTwoGuard:
    def test_a_one_row_suggest_last_result_set_mints_no_roster(self) -> None:
        """S5's own fix (implemented, `answer_bridge.py:520`) is "never mint a
        roster Pending with fewer than two options" - with one candidate row and
        no escalate-catalog/member-offer producer, the honest answer is NO
        Pending at all (`None`), not a smaller roster. Unsatisfiable as
        `>= 2 options` since one row cannot produce two."""
        offer = {"suggest_last_result_set": [{"uuid": "u1", "code": "X1", "entity_type": "product"}]}
        result = answer_bridge._miss_question(
            offer, {}, gate=None, parser={"routing": {}, "domain_hint": "incoming"},
            asked_at_turn=1, text="1. X1",
        )
        assert result is None, (
            "reviewer S5: a ONE-row did-you-mean set must mint NO roster Pending "
            f"(not a sub-minimum one, and no other producer offers a substitute "
            f"yes/no ask here): {result!r}"
        )

    def test_two_rows_still_mint_a_real_two_option_roster(self) -> None:
        offer = {
            "suggest_last_result_set": [
                {"uuid": "u1", "code": "X1", "entity_type": "product"},
                {"uuid": "u2", "code": "X2", "entity_type": "product"},
            ]
        }
        result = answer_bridge._miss_question(
            offer, {}, gate=None, parser={"routing": {}, "domain_hint": "incoming"},
            asked_at_turn=1, text="1. X1",
        )
        assert result is not None and len(result.options) == 2, (
            "reviewer S5: TWO genuinely distinct candidates must still mint a "
            f"real roster, not be swept up by the minimum-two guard too: {result!r}"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 11b (S4) - `turn/narrow.py::_ROSTER_CAP` (10 then, 50 since
# PICKER-NO-CAP) is a literal AC-1710
# forbids by name, still live for `customer`/`attachment_type`/`kind_pick` rosters
# (the `product` kind's own roster moved to `gate.py`, R5/R6). `decide()` has no
# `roster_cap` parameter today - AC-1710 says this must key off `chatbot_entity_
# kinds.roster_cap`, never a hardcoded 10.
# --------------------------------------------------------------------------- #


class TestNarrowPyCustomerRosterHonoursConfiguredCapNotTheLiteral:
    def test_a_configured_cap_is_actually_read_by_decide(self) -> None:
        focus = Focus(customers=[])
        resolved = [
            {"raw": f"ZZTCUST{i}", "canonical_code": f"ZZTCUST{i}", "uuid": f"cust-{i:03d}"}
            for i in range(15)
        ]
        outcome = narrow_mod.decide(
            kind="customer", policy_value="must_narrow_one", focus=focus, profile=Profile(),
            resolved_candidates=resolved, roster_cap=3,
        )
        assert len(outcome.ask_options) <= 3, (
            f"AC-1710/S4: a customer roster must honour a CONFIGURED cap, never "
            f"narrow.py's own literal `_ROSTER_CAP`: {outcome.ask_options!r}"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 11c (S6) - `tail/scope_block.py` is a narrower port than main's:
# the four ask-scoped axes (Order/Transporter/Container/Warehouse) never print,
# and a raise inside `apply_scope_block` is not caught locally - it fails the
# WHOLE turn (`engine.py`'s own fetch/compose `except Exception` renders the
# generic "Could not look an answer up." over a hit that genuinely found rows).
# --------------------------------------------------------------------------- #


class TestScopeBlockAskScopedAxesAndBestEffortWrapper:
    def test_an_order_number_scoped_ask_prints_the_order_axis(self) -> None:
        qf = {"entities": [], "date_filter_start": None, "date_filter_end": None}
        gate_json = {
            "compatible_entities": [
                {"entity_type": "order", "code": "SO12345", "title": "SO12345"}
            ]
        }
        header = scope_block_mod.search_scope_header(
            domain="order", qf=qf, gate_json=gate_json, resolver_json={},
        )
        assert header is not None
        assert "Order:" in header and "SO12345" in header, (
            f"reviewer S6: an order-number-scoped ask must print the Order axis "
            f"line, ported from origin/main's own four ask-scoped axes: {header!r}"
        )

    def test_a_raise_inside_the_scope_block_never_fails_the_whole_hit(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        code = unique_code("ZZTSCOPERAISE")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)
        qf = _parser_output(
            domain_hint="order", intent_hint="check_order",
            # Dated: a dateless DO list ask asks which period first (DO-ASK-SIMPLIFY, owner 4 Oct 2026).
            **do_window(),
            entities=[
                {"raw": code, "hint": "product", "canonical_code": None, "current_message": True, "confident": True}
            ],
        )
        row = {
            "order_number": "ZZT-ORD-SCOPE-1", "debtor_name": "ZZT SCOPE RAISE SDN BHD",
            "order_date": "2026-01-15", "order_status": None,
            "lines": [{"product": {"product_code": code}, "quantity": 1}],
        }

        def _raiser(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("a disclosure bug in the scope block itself")

        monkeypatch.setattr(scope_block_mod, "search_scope_header", _raiser)
        result, _captured = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf, text_body=f"delivery for {code}",
            msg_id="zzt-r6-scope-raise", mcp_response=_order_envelope_json([row]),
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Could not look an answer up." not in reply, (
            "reviewer S6: a raise inside the scope block must be caught LOCALLY "
            f"(main's own best-effort wrapper, 'a disclosure bug must never block "
            f"the answer') - the found rows must still reach the customer, without "
            f"the header, never as a failed lane: {reply!r}"
        )
        assert "Here are the orders I found." in reply, (
            f"the genuinely-found order rows must still answer even though the "
            f"scope block itself raised: {reply!r}"
        )


# --------------------------------------------------------------------------- #
# REVIEWER item 11d (S7) - a MULTI-domain ambiguous-customer ask still reaches
# `turn/compose.compose_question`'s generic "Which one do you mean?" fallback,
# where `484c79d92` read "Which customer do you mean?" (`gate.py`'s own header).
#
# Tester 36 re-measured the exact path (captain ruling, 20 Sep 2026), the coder's
# rebuttal (worktree `agent-aa7b10e854453193b`, item 7(d)) does not survive: that
# rebuttal instrumented a DIFFERENT resolver seam (a product entity, checking
# whether `resolve_gate`'s own `allowed_lookup` derivation raises anything) and
# concluded no picker fires at all for `domain_hint=None`. Re-run with a CUSTOMER
# entity through THIS file's own `_ambiguous_hanlim_resolve_services` fixture
# (the real gate.py pipeline, fake only at the resolve-entity/probe I/O boundary)
# and a monkeypatch spy on `app.services.chatbot.turn.apply.narrow_decide`:
# a Pending DOES fire, with `kind="customer_pick"`, from `turn/apply.py::
# _narrow_and_plan` (`turn/apply.py:1044`, `narrow_decide(kind="customer",
# policy_value="must_narrow_one", ...)`), NOT from `gate.py`.
#
# Why: `engine.py`'s own BRIDGE (`engine.py:1668-1692`, the block whose own
# comment reads "where this and narrow.decide's own roster arms would both ask,
# the bridge wins for a single-domain plan") is the ONLY caller of `resolve_gate.
# run`'s rich `offer` exit that ever reaches the customer, and it is gated on
# `len(plan.domains) <= 1` (`engine.py:1692`). For this two-domain ask the guard
# is False, so the bridge never engages, `plan.ask` (the plain `customer_pick`
# `_narrow_and_plan` already minted) is left standing, and it reaches
# `turn_compose.compose_question` (`turn/compose.py:487`) with no entry for
# `customer_pick` in `_ASK_HEADERS` (`turn/compose.py:409-424`) - hence the
# generic default header. The fixture's parser verdict shape (`domain_hint=None`,
# `asks=[{"domain": ...}, {"domain": ...}]`) is the REAL shape a genuine
# multi-domain verdict carries (confirmed against
# `tests/chatbot/replay_turns/console/handbuilt-rp-004-two-domains-in-one-
# message-fan-out-in-message-order-contract-122.json`'s own recorded `verdict`).
#
# The finding is REAL and left red. The final exact-string pin against
# `_expected_customer_picker_text` (a SINGLE-domain-only production chain) is
# dropped - it presupposes a specific fix shape (that the multi-domain reply
# would come out byte-identical to the single-domain one, "(SRT)" suffix and
# all) the coder has not chosen yet; pinning the HEADER is the finding, not the
# exact wording of a fix nobody has written.
# --------------------------------------------------------------------------- #


class TestMultiDomainAmbiguousCustomerUsesGatesOwnHeader:
    def test_two_domains_named_still_prints_the_gates_customer_header(
        self, session_factory, monkeypatch
    ) -> None:
        def spy_probe(*, tool: str, contact_id: Any, entities: Any, semantic_input: dict, user_prompt: str) -> Any:
            return {"items": [], "has_result": False}

        _seed_business_contact(session_factory, variables={})
        result, captured = _run_turn_fake_resolver(
            session_factory, monkeypatch,
            qf=_parser_output(
                domain_hint=None, intent_hint=None,
                entities=[
                    {"raw": "hanlim", "hint": "customer", "canonical_code": None,
                     "current_message": True, "confident": True},
                ],
                # "order" narrows `customer: must_narrow_one`; "incoming" only
                # narrows `product` (and, per AC-1690, a product roster never asks
                # here at all - a silent prefix filter) - so "order" is the ONLY
                # domain of the two that can raise an ask, isolating the customer
                # picker's own header/precedence question this test is about.
                # `domain_hint=None` + a two-entry `asks` list is the real shape a
                # genuine multi-domain verdict carries (see module comment above).
                asks=[{"domain": "order"}, {"domain": "incoming"}],
            ),
            text_body="orders and incoming for hanlim",
            msg_id="zzt-r6-multi-domain-customer",
            attributes=["sales_orders.outstanding"],
            resolve_services=_ambiguous_hanlim_resolve_services(spy_probe),
        )
        reply = (result.reply or {}).get("text") or ""
        assert "Which one do you mean?" not in reply, (
            f"reviewer S7: a multi-domain ambiguous-customer ask must never fall "
            f"through to turn/compose.py's generic header (turn/compose.py:487, "
            f"`customer_pick` absent from `_ASK_HEADERS`): {reply!r}"
        )
        assert reply.startswith("Which customer do you mean? Please choose:"), (
            f"reviewer S7: gate.py's own customer-picker header must win even for "
            f"a multi-domain ask - engine.py's own bridge (engine.py:1668-1692) "
            f"must not skip the offer exit just because len(plan.domains) > 1: "
            f"{reply!r}"
        )
