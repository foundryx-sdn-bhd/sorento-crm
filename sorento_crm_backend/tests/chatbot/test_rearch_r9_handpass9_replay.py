"""R9 RED tests, PR #952 chatbot turn engine re-architecture - hand pass 9 (owner
console, 20 Sep 2026 21:55-22:42 MYT, contact 900000008, clone DB
`sorento_ai_automation_rearch`, :8081 @ 19d5f7789 v39). Written by tester 42 BEFORE the
coder's next pass, per the captain's brief. Every scenario replays a RECORDED parser
verdict pulled from `chatbot.turns` by id (psql SELECT against the clone, read-only) -
zero live OpenAI calls, no journey runner, no turn to :8081. Chains are also written to
`tests/chatbot/journeys/handpass9-*.json` (4 files) for a future live/agent-browser pass;
this file is the graded pytest artifact.

**Test shape**: every test drives `engine.run_turn` via `test_rearch_r5_production_
decides.py`'s own `_run_turn_real` / `_run_turn_with_mcp_call` (real resolver/gate/
narrower over seeded Postgres rows, doubling only the MCP boundary) - the same
convention `test_rearch_r7_live_parity_replay.py` and `test_rearch_r6_review_round.py`
set. Postgres only (`session_factory`, blank schema, `tests/_pg_fixture.py`); every row
seeded fresh per test, nothing borrowed from the clone beyond the VERDICT SHAPE
(uuids/codes re-pointed at rows seeded here - CI's DB is empty).

# Measurement notes (this session, tester 42)

**1. Main's roster-close rule** - `sorento_crm_backend/app/services/chatbot/tail/
compile_state.py`, function `_picker_carry` (def at line 1802). A `require_specific`
("disambiguation") roster is minted as `variables["picker_last_result_set"]` /
`picker_selection_context` the turn it is BORN (`born_now`, lines ~1833-1841). On every
LATER turn, `carried = prev_picker if (born is None and not offer_born_this_turn and
jsc.is_array(prev_picker) and len(prev_picker) > 0 and not fresh_typed) else None`
(lines 1902-1911) - `fresh_typed` (lines 1868-1881) is TRUE only when the customer's OWN
raw entity list carries a `current_message: true` entry with no ordinal/dym_slot, i.e. a
message that TYPES something new. There is no "was this already answered" bit anywhere
in this function: a bare positional pick ("1", "2", "4", ...) is never `fresh_typed`, so
the SAME roster carries forward across as many picks as the customer makes, and only a
message that types a fresh entity (a genuine new ask) retires it. Contract 36 in this
lane's own `turn/pending.py` (`ROSTER_KINDS = {"product_pick", "customer_pick",
"kind_pick"}`, `with_answered_positions`) is this exact rule, ported - `tier_pick` is
NOT in that set today (`test_rearch_r3_answer_bridge.py::TestStickyRosterContract36::
test_tier_pick_is_not_a_roster_kind_by_contract_but_a_pick_is_still_a_pending`), which
main's own mechanism draws no such distinction for: `_picker_carry` only ever reasons
about ONE kind of require_specific/disambiguation-shaped roster, and the tier ask is the
identical shape (a numbered list, one axis, `_offer_carry`'s own `tier_offer` shares the
same "born this turn re-seats, else carried" family) - hand pass 9's D3 is that same gap
surfacing on the tier axis. This ruling supersedes the older `TestStickyRosterContract36`
assumption; a coder fix here should also update or retire that contract test.

**2. Main's miss bullets for a picked product + attachment_type** - `sorento_crm_
backend/app/services/chatbot/lanes/business/answer.py`. `by_type` (built lines
2661-2680) groups every RESOLVED `compatible_entities` row by `entity_type`, in the
order entities were resolved - a turn that resolved BOTH a product (the picked roster
option) and an attachment_type (the carried "photo" word) has BOTH keys. `found_lines`
(lines 2795-2821) renders one `f"• {entity_type}: {rendered}{extra}"` bullet PER
resolved type, and `build_breakdown_msg` (lines 2902, 2935-2936) joins them under
"Here's what you want:\n". For the picked-product-then-miss shape (chain A, turn
eb71ccca-e878-4006-b834-be3b99a8e576), the picked product resolves FIRST (it is the
option the customer's position picked), so main's own bullet ORDER is
`• product: <code>` then `• attachment_type: Product Photos` - both present,
never attachment_type alone.

**3. Main's reply to a subject-less multi-domain ask** - `lanes/business/gate.py`.
`ALLOWED` (lines 44-73) has no `purchase_order` row at all, so a PO-only ask always
"passes through unscoped" (line 331, `gate_reason = "domain '...' not in matrix;
passing through unscoped"`) - that leg being unscoped is BY DESIGN on main, not a
regression. `ALLOWS_EMPTY` (lines 86-95) is `{"inventory": False, "incoming": True,
"forms": True, "portal_link": True, "master_products": False, "product_attachment":
False, "inventory": False, "order": False}`; `run_gate`'s own zero-entity branch (lines
350-363) reads it: `gate_passed = ALLOWS_EMPTY.get(domain) is True or intent_allows_
empty`, else `gate_reason = "no entities and '{domain}' requires a scoping entity"`. So
for a bare "stock and incoming and PO" with ZERO entities and no window, main's gate
REFUSES the inventory/stock leg outright (asks for a product) while incoming/PO are
allowed to run broad by explicit design. Chain D(b) below pins the inventory half only -
the one the rearch lane's own recorded turn got wrong.
"""
from __future__ import annotations

import json
from typing import Any

from app.services.chatbot.lanes.business import fetch as fetch_mod
from app.services.chatbot.lanes.business.tier_gate import recompose
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests.chatbot.test_engine import _parser_output
from tests.chatbot.test_engine_company_scope import _seed_product
from tests.chatbot.test_outstanding_lane import _present_response, _session_of
from tests.chatbot.test_product_attachment_picker_stamp import _seed_file_for
from tests.chatbot.test_rearch_r5_production_decides import (
    _mcp_double,
    _mcp_probe_for,
    _run_turn_real,
    _run_turn_with_mcp_call,
    _seed_contact_and_get,
)
from tests.chatbot.test_rearch_r6_review_round import (
    _mark_workspace_default,
    _run_turn_engine_real,
    _seed_three_tiers_two_entitled,
)
from tests.chatbot.test_rearch_r7_live_parity_replay import _seed_real_attachment_type


# --------------------------------------------------------------------------- #
# Chain A - photo roster (product_attachment). D1 + D2.
# Live turns: 9c8b0d60-6de4-4968-addd-31627fa8c7ce (roster, correct),
# eb71ccca-e878-4006-b834-be3b99a8e576 ("1", D1), 93d45184-5530-4d04-8f6d-bdda695d241e
# ("2", D2), 640b6464-fc9b-44c8-9aaf-9dce42358d51 ("4", D2).
# --------------------------------------------------------------------------- #


def _seed_photo_family(session_factory, base: str) -> dict[str, tuple[str, str]]:
    """Three codes sharing `base` as a prefix (an ambiguous family, matching r7's own
    `_family_base` convention) - only the THIRD has a real Product Photos file, so a
    pick that lands on it is a genuine hit and a pick on either of the first two is a
    genuine miss."""
    a_code, b_code, c_code = f"{base}A", f"{base}B", f"{base}C"
    a_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=a_code)
    b_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=b_code)
    c_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=c_code)
    type_id = _seed_real_attachment_type(session_factory, "Product Photos")
    _seed_file_for(
        session_factory, product_id=c_id, attachment_type_id=type_id,
        company_id=DEFAULT_COMPANY_ID, filename=f"{c_code}.jpg",
    )
    return {"a": (a_code, a_id), "b": (b_code, b_id), "c": (c_code, c_id)}


def _photo_roster_ask_qf(base: str) -> dict[str, Any]:
    return _parser_output(
        domain_hint="product_attachment", intent_hint="check_product_attachment",
        entities=[
            {"raw": base, "hint": "product", "canonical_code": None,
             "current_message": True, "confident": True},
            {"raw": "photo", "hint": "attachment_type", "canonical_code": "photo",
             "current_message": True, "confident": True},
        ],
        routing={"suggested_team": "marketing_product", "suggested_agent": None, "team_source": None},
    )


def _position_of(options: list[dict[str, Any]], code: str) -> int | None:
    return next(
        (o.get("position") for o in options if str(o.get("code") or "").upper() == code.upper()),
        None,
    )


class TestPhotoRosterStaysAnswerableAndNamesThePickedProduct:
    """Hand pass 9 chain A. D1 (turn eb71ccca-e878-4006-b834-be3b99a8e576, "1"): the
    miss breakdown must name the PICKED product, not attachment_type alone. D2 (turns
    93d45184.../640b6464..., "2"/"4"): the roster must still be answerable for a
    SECOND, DIFFERENT position after the first pick answered - see module docstring
    measurement note 1."""

    def test_d1_a_miss_after_a_pick_names_the_picked_product_in_the_breakdown(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTHP9PHA").replace("-", "")
        family = _seed_photo_family(session_factory, base)
        no_code, _no_id = family["a"]

        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=_photo_roster_ask_qf(base),
            text_body=f"photo for {base}", msg_id="zzt-hp9-d1-roster", mcp_response={"data": []},
        )
        reply1 = (result1.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        assert len(options) == 3, (
            f"test setup sanity: a 3-member ambiguous family must roster all of them: "
            f"{reply1!r} {options!r}"
        )
        no_position = _position_of(options, no_code)
        assert no_position is not None, (no_code, options)

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[no_position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body=str(no_position),
            msg_id="zzt-hp9-d1-pick", mcp_call=_mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))[0],
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert "But no Product Photos matched these." in reply2, (
            f"test setup sanity: picking the no-photo member must be a genuine miss: {reply2!r}"
        )
        assert f"• product: {no_code}" in reply2, (
            f"D1: live turn eb71ccca-e878-4006-b834-be3b99a8e576 printed only "
            f"'• attachment_type: Product Photos' in the breakdown, dropping the "
            f"PICKED product's own bullet entirely - main's own by_type/found_lines "
            f"(answer.py:2661-2821) always includes one bullet per RESOLVED entity "
            f"type, product included: {reply2!r}"
        )
        # ATTACHMENT-MULTI Q5 (a): the kind is said in words, never `attachment_type`.
        assert "• attachment type: Product Photos" in reply2, reply2

    def test_d2_a_second_different_position_still_resolves_after_the_first_pick(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTHP9PHB").replace("-", "")
        family = _seed_photo_family(session_factory, base)
        no_code, _no_id = family["a"]
        has_code, has_id = family["c"]

        answer_probe = _mcp_probe_for(
            {
                "crm_master_product_attachments_list": [
                    {
                        "product": {"product_code": has_code},
                        "attachment": {"attachment_type": "Product Photos", "original_filename": f"{has_code}.jpg"},
                        "company_name": "Sorento",
                    }
                ]
            }
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=_photo_roster_ask_qf(base),
            text_body=f"photo for {base}", msg_id="zzt-hp9-d2-roster", mcp_response={"data": []},
            answer_mcp_probe=answer_probe,
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        no_position = _position_of(options, no_code)
        has_position = _position_of(options, has_code)
        assert no_position is not None and has_position is not None, (no_code, has_code, options)

        qf_first_pick = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[no_position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf_first_pick, text_body=str(no_position),
            msg_id="zzt-hp9-d2-pick-1", mcp_call=_mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))[0],
            answer_mcp_probe=answer_probe,
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert "But no Product Photos matched these." in reply2, (
            f"test setup sanity: the first pick must be a genuine miss: {reply2!r}"
        )

        qf_second_pick = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[has_position],
        )

        # Branch by tool name AND product_ids, the same convention
        # `TestIncomingMissAfterAPickUsesTheStockFallbackLadder._other` (this same file)
        # uses - a fixed `{"data": []}` double answers EVERY pick identically, which
        # made the first pick's genuine miss and this SECOND, different pick's genuine
        # hit indistinguishable to the fetch seam. Only a fetch scoped to `has_id`
        # (the roster option this turn actually picked) returns the real row, so the
        # assertion below tests the engine's own carry-and-refetch behaviour, not the
        # test double.
        #
        # `"file_path"` on the row (re-pinned, coder 36's ladder-before-miss fix
        # session, 21 Sep 2026): measured directly that
        # `sorento_crm_mcp.presenters.present_response`'s own `_Builder.attach`
        # (`sorento_crm_mcp/sorento_crm_mcp/presenters.py:326`) only appends to
        # `b.attachments` when the row's `file_path`/`url` is filled - without one
        # (the shape this double used before) `b.attachments` stays empty and the
        # intro falls through to `_DEFAULT_INTRO["crm_master_product_attachments_
        # list"]` ("Here are the product files I found.", `presenters.py:73`) instead
        # of the "has real attachments" intro (`presenters.py:1611-1613`,
        # `"I have attached the file(s) below."`) production's own genuinely-filed
        # live turns render. Adding the same `file_path` a real DB-backed attachment
        # row always carries (`test_product_attachment_picker_stamp.py::
        # _seed_file_for`'s own convention) makes this double produce the SAME live
        # intro, not a fixture invention.
        def _second_pick_other(name: str, args: dict[str, Any]) -> str:
            if name == "crm_master_product_attachments_list" and has_id in (
                args.get("product_ids") or []
            ):
                return _present_response()(
                    name, json.dumps({"data": [
                        {
                            "product": {"product_code": has_code},
                            "attachment": {
                                "attachment_type": "Product Photos",
                                "original_filename": f"{has_code}.jpg",
                                "file_path": f"https://example.test/zzt-{has_code}.jpg",
                            },
                            "company_name": "Sorento",
                        }
                    ]}),
                )
            return json.dumps({"data": []})

        result3 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf_second_pick, text_body=str(has_position),
            msg_id="zzt-hp9-d2-pick-2", mcp_call=_mcp_double(other=_second_pick_other)[0],
            answer_mcp_probe=answer_probe,
        )
        reply3 = (result3.reply or {}).get("text") or ""
        assert reply3 != "No matching results found.", (
            f"D2: live turns 93d45184-5530-4d04-8f6d-bdda695d241e ('2') and "
            f"640b6464-fc9b-44c8-9aaf-9dce42358d51 ('4') both printed the bare "
            f"'No matching results found.' when a SECOND, different position was "
            f"picked over the SAME still-open roster - the roster is not sticky past "
            f"the first pick. Picking the ACTUAL has-photo member ({has_code}, "
            f"position {has_position}) after the no-photo member ({no_code}, "
            f"position {no_position}) was already answered must still return the "
            f"real file, per main's own carry rule (module docstring measurement "
            f"note 1): {reply3!r}"
        )
        # "I have attached the file(s) below." IS the live intro here (re-pinned,
        # coder 36's ladder-before-miss fix session, 21 Sep 2026) - only once the
        # double's row carries a real `file_path` (see `_second_pick_other`'s own
        # comment above for the measured presenter mechanics); pinning it before that
        # fix would have asserted a shape the double could never produce, whatever the
        # engine did. Three things matter, none weakened: the SECOND pick's own
        # product names the reply (never the first pick's, `no_code`), and the
        # `send_attachments` action carries that SAME product's Product Photos file,
        # not just text that mentions it.
        assert "I have attached the file(s) below." in reply3, reply3
        assert has_code in reply3, (
            f"D2: the second pick must name {has_code}'s own product in the reply, "
            f"not repeat the first pick's: {reply3!r}"
        )
        assert no_code not in reply3, (
            f"D2: the reply must never carry the FIRST pick's own product "
            f"({no_code}) once a SECOND, different pick has answered: {reply3!r}"
        )
        send_attachments = [
            a for a in (result3.actions or []) if isinstance(a, dict) and a.get("kind") == "send_attachments"
        ]
        assert send_attachments, f"D2: the second pick must carry a send_attachments action: {result3.actions!r}"
        attached_files = [
            f for a in send_attachments for f in (a.get("attachments_src") or []) if isinstance(f, dict)
        ]
        assert any(
            f.get("attachmentType") == "Product Photos" and has_code in str(f.get("filename") or "")
            for f in attached_files
        ), (
            f"D2: the send_attachments action must carry {has_code}'s own Product "
            f"Photos file, not the first pick's: {attached_files!r}"
        )



# --------------------------------------------------------------------------- #
# Chain B - promotion tier roster. D3.
# Live turns: ad1777a8-a768-4d13-a2dd-4db76b4be114 (roster, correct),
# 99c14381-0fbd-4d30-8f29-c8587410bdfd ("1", correct - Office),
# 25dcef1d-decc-407b-a6eb-08d8112ee814 ("2", D3 - repeats Office instead of Dealer).
# --------------------------------------------------------------------------- #


class TestPromotionTierRosterStaysAnswerableAcrossMultiplePicks:
    """See module docstring measurement note 1 - `tier_pick` shares the same
    "answered but still open" gap main's own `_picker_carry` never draws a
    kind-based distinction for."""

    def test_a_second_pick_over_the_same_tier_roster_uses_the_second_tier(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        _mark_workspace_default(session_factory)
        _seed_three_tiers_two_entitled(session_factory)
        entitled_names = ["Sorento Dealer", "Sorento Office"]
        code = unique_code("ZZTHP9TIER")
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=code)

        qf1 = _parser_output(
            domain_hint="promotion", intent_hint="check_promotion",
            entities=[{"raw": code, "hint": "product", "canonical_code": None,
                       "current_message": True, "confident": True}],
        )
        result1, _c1 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"promo for {code}",
            msg_id="zzt-hp9-tier-ask", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        assert open_question.get("kind") == "tier_pick", (
            f"test setup sanity: an entitled-to-two-of-three tier ask must raise the "
            f"tier picker: {(result1.reply or {}).get('text')!r}"
        )
        options = open_question.get("options") or []
        assert len(options) == 2, f"test setup sanity: 2 entitled options: {options!r}"

        qf2 = _parser_output(
            domain_hint=None, intent_hint=None, entities=[], access_levels=[],
            reference_positions=[1],
        )
        result2, captured2 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf2, text_body="1",
            msg_id="zzt-hp9-tier-pick-1", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        promo_calls_2 = [c for c in captured2 if c[0] == fetch_mod.TIER_PROBE_TOOL]
        assert promo_calls_2, f"'1' must run a promotion fetch: {captured2!r}"
        levels_2: set[str] = set()
        for _n, args in promo_calls_2:
            levels_2.update(args.get("access_levels") or [])
        expected_office = set(recompose(["office"], [], entitled_names)["access_levels"])
        assert levels_2 == expected_office, (
            f"test setup sanity: '1' must fetch Office promotions: {captured2!r}"
        )

        qf3 = _parser_output(
            domain_hint=None, intent_hint=None, entities=[], access_levels=[],
            reference_positions=[2],
        )
        result3, captured3 = _run_turn_engine_real(
            session_factory, monkeypatch, qf=qf3, text_body="2",
            msg_id="zzt-hp9-tier-pick-2", mcp_response={"has_result": False, "items": []},
            real_entitlement=True,
        )
        promo_calls_3 = [c for c in captured3 if c[0] == fetch_mod.TIER_PROBE_TOOL]
        assert promo_calls_3, (
            f"D3: 'answering the tier roster a SECOND time with a different position "
            f"must still run its own promotion fetch - live turn "
            f"25dcef1d-decc-407b-a6eb-08d8112ee814 re-printed turn 1's own reply "
            f"verbatim, no fresh fetch at all: {captured3!r}"
        )
        levels_3: set[str] = set()
        for _n, args in promo_calls_3:
            levels_3.update(args.get("access_levels") or [])
        expected_dealer = set(recompose(["dealer"], [], entitled_names)["access_levels"])
        assert levels_3 == expected_dealer, (
            f"D3: picking position 2 over the SAME still-open tier roster must fetch "
            f"DEALER promotions, not repeat Office - live turn "
            f"25dcef1d-decc-407b-a6eb-08d8112ee814's own tool args stayed "
            f"access_levels=['Sorento Office','Mocha Office','Cabana Office'] "
            f"byte-identical to turn 1's own: got {levels_3!r}, expected "
            f"{expected_dealer!r} ({captured3!r})"
        )


# --------------------------------------------------------------------------- #
# Chain C - incoming miss after a pick. D4.
# Live turns: 0c192960-6dde-4ac3-9f56-e0f74a1d84f9 (roster, correct),
# 139f5282-4968-4802-bd11-501de55bca50 ("1", D4), f1f5e61d-8121-4a61-817b-e470b9f5d7dc
# ("8", correct control).
# --------------------------------------------------------------------------- #


class TestIncomingMissAfterAPickUsesTheStockFallbackLadder:
    """See module docstring measurement note 2 for the exact production bullet/ladder
    shape; the FRESH-ask version of this same ladder is already pinned green by
    `test_rearch_r5_production_decides.py::TestIncomingMissLadderProduct` (AC-1705)."""

    def test_picking_a_no_incoming_family_member_still_runs_the_full_ladder(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTHP9INC").replace("-", "")
        no_code, has_code = f"{base}A", f"{base}B"
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=no_code)
        _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=has_code)

        qf1 = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[{"raw": base, "hint": "product", "canonical_code": None,
                       "current_message": True, "confident": True}],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"incoming for {base}",
            msg_id="zzt-hp9-inc-roster", mcp_response={"data": []},
        )
        reply1 = (result1.reply or {}).get("text") or ""
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        no_position = _position_of(options, no_code)
        assert no_position is not None, (
            f"test setup sanity: the no-incoming member must be in the roster: "
            f"{reply1!r} {options!r}"
        )

        def _other(name: str, args: dict[str, Any]) -> str:
            if name == "crm_inventory_stock_balance_list":
                return _present_response()(
                    name, json.dumps({"data": [
                        {"product_code": no_code, "total_qty": 5, "outstanding_qty": 0,
                         "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 5}]}
                    ]}),
                )
            return json.dumps({"data": []})

        mcp_call, _c2 = _mcp_double(other=_other)
        answer_probe = _mcp_probe_for(
            {"crm_inventory_stock_balance_list": [
                {"product_code": no_code, "total_qty": 5, "outstanding_qty": 0,
                 "warehouse_allocations": [{"warehouse_code": "ZZT-WH", "qty": 5}]}
            ]}
        )
        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[no_position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body=str(no_position),
            msg_id="zzt-hp9-inc-pick", mcp_call=mcp_call, answer_mcp_probe=answer_probe,
        )
        reply2 = (result2.reply or {}).get("text") or ""
        # MEASURED (this session): the after-a-pick miss reaches `turn/fetch.py`'s OWN
        # OLD `_climb` rung composer (its raw MCP-presenter intro, "Stock details
        # found for the requested products.") instead of the bridge's AC-1705 ladder -
        # the identical class of defect `TestIncomingMissLadderProduct` (this same
        # file's own FRESH-ask green control) already guards against for a fresh ask
        # ("the OLD turn/fetch.py::_climb rung composer ... must never answer this
        # turn - only the bridge's own AC-1705 ladder sentence").
        assert "Stock details found for the requested products." not in reply2, (
            f"D4: live turn 139f5282-4968-4802-bd11-501de55bca50 (and this replay) "
            f"reach the OLD turn/fetch.py::_climb rung composer's own raw MCP-"
            f"presenter intro after a PICK - only the bridge's AC-1705 ladder "
            f"sentence must ever answer this turn, matching "
            f"TestIncomingMissLadderProduct's own already-green guard for the "
            f"FRESH-ask shape of the identical scenario: {reply2!r}"
        )
        assert reply2 != "No matching results found.", (
            f"D4: live turn 139f5282-4968-4802-bd11-501de55bca50 answered a picked "
            f"no-incoming family member with the bare generic miss followed only by "
            f"the raw stock summary, dropping the breakdown/ladder entirely: {reply2!r}"
        )
        assert "But no incoming matched these." in reply2, reply2
        assert f"No incoming for {no_code}." in reply2, reply2
        assert "But here are the stock details for the requested products:" in reply2, reply2
        assert "Would you like me to escalate to purchasing team?" in reply2, reply2
        assert f"• product: {no_code}" in reply2, (
            f"D4: the breakdown bullet must name the PICKED product: {reply2!r}"
        )

    def test_picking_a_has_incoming_family_member_still_works_control(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTHP9INCOK").replace("-", "")
        no_code, has_code = f"{base}A", f"{base}B"
        no_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=no_code)
        has_id = _seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=has_code)

        qf1 = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[{"raw": base, "hint": "product", "canonical_code": None,
                       "current_message": True, "confident": True}],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"incoming for {base}",
            msg_id="zzt-hp9-inc-ok-roster", mcp_response={"data": []},
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        has_position = _position_of(options, has_code)
        assert has_position is not None, (has_code, options)

        # `crm_incoming_stock_list`'s real presenter builder (`_incoming_list`,
        # sorento_crm_mcp/presenters.py:831-863) reads SHIPMENT rows with a nested
        # `lines` array, never a flat `product_code` key - measured this session via
        # a throwaway debug script after the flat shape silently rendered zero items.
        def _other(name: str, args: dict[str, Any]) -> str:
            if name == "crm_incoming_stock_list":
                return _present_response()(
                    name, json.dumps({"data": [
                        {
                            "company_name": "Sorento", "shipping_container_number": "ZZTCONT1",
                            "estimated_arrival_date": "2026-09-08",
                            "lines": [{
                                "product_code": has_code, "remaining_incoming_quantity": 33,
                                "warehouse_allocations": [],
                            }],
                        }
                    ]}),
                )
            return json.dumps({"data": []})

        mcp_call, _c2 = _mcp_double(other=_other)
        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=[has_position],
        )
        result2 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body=str(has_position),
            msg_id="zzt-hp9-inc-ok-pick", mcp_call=mcp_call,
        )
        reply2 = (result2.reply or {}).get("text") or ""
        assert has_code in reply2 and "No matching results found." not in reply2, (
            f"control (matches live turn f1f5e61d-8121-4a61-817b-e470b9f5d7dc, "
            f"unchanged/correct): picking the has-incoming member must return its "
            f"own rows: {reply2!r}"
        )


# --------------------------------------------------------------------------- #
# Chain D - "all" then a subject-less domain switch. D5(a) + D5(b).
# Live turns: e2e920b6-585f-47c7-b037-bedcbd70d8dc (roster),
# 05bd3233-acec-4012-a797-0606a898ea98 ("all", correct),
# 554cf9c4-9249-4e45-ab2a-7a154b813009 ("stock and incoming and PO", D5).
# --------------------------------------------------------------------------- #


class TestAllPickCarriesTheRostersSubjectToASubjectlessDomainSwitch:
    """D5(a): once 'all' has answered a roster, a LATER message that names a domain
    but no entity at all must reuse the roster's own products as its subject - see
    module docstring measurement note 3 and `turn/decide.py`'s own four-row table
    (`domain_in_message=True, entities=[]` row: "a domain switch over the standing
    subject")."""

    def test_a_bare_domain_switch_after_all_reuses_the_rosters_own_products(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        base = unique_code("ZZTHP9ALL").replace("-", "")
        codes = [f"{base}A", f"{base}B", f"{base}C"]
        ids = [_seed_product(session_factory, company_id=DEFAULT_COMPANY_ID, code=c) for c in codes]

        qf1 = _parser_output(
            domain_hint="incoming", intent_hint="check_incoming",
            entities=[{"raw": base, "hint": "product", "canonical_code": None,
                       "current_message": True, "confident": True}],
            routing={"suggested_team": "purchasing", "suggested_agent": "incoming_stock_enquiries"},
        )
        result1, _c1 = _run_turn_real(
            session_factory, monkeypatch, qf=qf1, text_body=f"incoming for {base}",
            msg_id="zzt-hp9-all-roster", mcp_response={"data": []},
        )
        open_question = _session_of(session_factory).get("open_question") or {}
        options = open_question.get("options") or []
        assert len(options) == len(codes), (
            f"test setup sanity: a {len(codes)}-member ambiguous family must roster "
            f"all of them: {(result1.reply or {}).get('text')!r} {options!r}"
        )
        roster_uuids = {o.get("uuid") for o in options if o.get("uuid")}
        assert roster_uuids == set(ids), (roster_uuids, set(ids))

        qf2 = _parser_output(
            message_type="casual", intent_hint=None, domain_hint=None, entities=[],
            reference_positions=list(range(1, len(codes) + 1)),
        )
        mcp_call2, captured2 = _mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))
        _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf2, text_body="all",
            msg_id="zzt-hp9-all-pick", mcp_call=mcp_call2,
        )
        assert captured2, f"test setup sanity: 'all' must run a real fetch: {captured2!r}"
        for name, args in captured2:
            assert set(args.get("product_ids") or []) == set(ids), (
                f"test setup sanity (control, matches live turn "
                f"05bd3233-acec-4012-a797-0606a898ea98 which is already correct): "
                f"'all' must fetch exactly the roster's own products: "
                f"tool={name!r} args={args!r}"
            )

        qf3 = _parser_output(
            domain_hint="inventory", intent_hint="check_stock", entities=[], entity_op="clear",
            asks=[
                {"domain": "inventory", "intent": "check_stock"},
                {"domain": "incoming", "intent": "check_incoming"},
                {"domain": "purchase_order", "intent": "check_po"},
            ],
            broaden_to="all", broaden_axis="all", document=["PO"],
        )
        mcp_call3, captured3 = _mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))
        result3 = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf3, text_body="stock and incoming and PO",
            msg_id="zzt-hp9-all-domain-switch", mcp_call=mcp_call3,
        )
        reply3 = (result3.reply or {}).get("text") or ""
        assert captured3, (
            f"test setup sanity: a domain-only ask over an open, all-answered roster "
            f"must still fetch: {reply3!r}"
        )
        for name, args in captured3:
            sent = set(args.get("product_ids") or [])
            assert sent == set(ids), (
                f"D5(a): live turn 554cf9c4-9249-4e45-ab2a-7a154b813009 sent EVERY "
                f"tool call with entities_in=0, total_uuids_passed=0 and printed 50 "
                f"unrelated stock rows + 50 unrelated PO rows - a domain-only switch "
                f"after 'all' must REUSE the roster's own subject (decide.py's four-"
                f"row table, 'domain_in_message: true, entities: no' row), never "
                f"clear it: tool={name!r} product_ids={sent!r}, expected the "
                f"roster's own {set(ids)!r}"
            )


class TestSubjectlessMultiDomainAskNeverDumpsTheCatalogue:
    """D5(b): a bare 'stock and incoming and PO' with NO prior roster and NO
    product named at all - contrast with D5(a) above, which is the CARRY-after-a-
    roster half. See module docstring measurement note 3: main's own
    `ALLOWS_EMPTY["inventory"] is False` refuses this leg outright with zero
    entities; incoming/PO are allowed to run broad by design and are not asserted
    here."""

    def test_a_bare_multi_domain_ask_never_runs_stock_unscoped(
        self, session_factory, monkeypatch
    ) -> None:
        _seed_contact_and_get(session_factory)
        # Real catalogue-scale noise (tester 41's own item 1 lesson: an empty scratch
        # schema has nothing for an unscoped fetch to leak, so the assertion is on
        # the ARGS sent, not on the row count a thin double renders anyway).
        for i in range(5):
            _seed_product(
                session_factory, company_id=DEFAULT_COMPANY_ID,
                code=unique_code(f"ZZTHP9NOISE{i}"),
            )

        qf = _parser_output(
            domain_hint="inventory", intent_hint="check_stock", entities=[], entity_op="clear",
            asks=[
                {"domain": "inventory", "intent": "check_stock"},
                {"domain": "incoming", "intent": "check_incoming"},
                {"domain": "purchase_order", "intent": "check_po"},
            ],
            broaden_to="all", broaden_axis="all", document=["PO"],
        )
        mcp_call, captured = _mcp_double(other=lambda *_a, **_k: json.dumps({"data": []}))
        result = _run_turn_with_mcp_call(
            session_factory, monkeypatch, qf=qf, text_body="stock and incoming and PO",
            msg_id="zzt-hp9-bare-multi-domain", mcp_call=mcp_call,
        )
        reply = (result.reply or {}).get("text") or ""
        stock_calls = [
            (name, args) for name, args in captured if name == "crm_inventory_stock_balance_list"
        ]
        for name, args in stock_calls:
            assert args.get("product_ids"), (
                f"D5(b): origin/main's own gate.py never lets an 'inventory' ask "
                f"through unscoped with zero entities (ALLOWS_EMPTY['inventory'] is "
                f"False, gate.py:86-95/350-363: \"no entities and 'inventory' "
                f"requires a scoping entity\") - a subject-less 'stock and incoming "
                f"and PO' must never run a bare, unscoped stock fetch. Live turn "
                f"554cf9c4-9249-4e45-ab2a-7a154b813009 ran exactly this "
                f"(product_ids=[], entities_in=0) and printed 50 unrelated stock "
                f"rows: tool={name!r} args={args!r} reply={reply!r}"
            )
