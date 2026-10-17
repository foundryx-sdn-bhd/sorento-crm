"""Owner console pass 4, item 2 (7 Sep 2026): "last month" after a resolved customer pick
loses the customer scope on the actual tool call, though the header still names it right.

Production turns 5bb0426e-df3b-4765-bb2b-48b6bf44e20c ("3" -> L.A.W. Transport) then
cc0075ae-ac49-4fbb-a7bf-9adb9b564280 / 0d9332a0-8115-4747-af5b-e7568fca4f05 ("last month",
`turns-lane3` + round 12 `LAW-t3`). Both "last month" captures: `selection_context:
disambiguation` is STILL carried from the earlier picker turn, `_pending_pick: true`,
`ctx.parse.output.entities` correctly carries the picked customer with its real uuid
(`bec07281-8c52-4581-af8a-ad2d7a5f8e16`, canonical_code `301-C001`) and
`date_filter_start`/`date_filter_end` (round 12's capture keeps them; the earlier one nulled
them, registered as a separate, already-resolved divergence). The reply HEADER is built from
`qf.entities` and is right ("Customer: L. A. W. Transport (K.L.) Sdn. Bhd. (SRT)" / "Dates:
01/08/2026 to 31/08/2026"), but the ROWS are for a completely different customer (SILK
CABINETS SDN BHD) - the actual order-tool call never carried a customer filter at all.

`resolve_entity_body` (`app/services/chatbot/lanes/business/resolve_gate.py` ~:296-347) omits
`entity_pins` whenever `match_mode == "and"` (the route's own 400 rule) - which is this
turn's `match_mode` - so a CARRIED entity that already has a resolved `uuid` from a prior
turn is sent back to the resolver as a bare text token
(`"L. A. W. Transport (K.L.) Sdn. Bhd. (SRT)"`) with no pin, and whatever that free-text
search comes back with (or fails to) is what reaches `gate.compatible_entities` and, from
there, `entity_ids_transformer`'s `customer_ids`. This test seeds the SAME customer under
its real name and measures what the resolver, the gate and the tool call actually do with it
end to end - the production seam (LESSONS-LEARNT #102), not a hand-built `compatible_entities`
that would beg the question.

MEASURED against the LOCAL prod-copy database (7 Sep 2026, throwaway harness: the dump's own
`request_item` fed to `run_turn` with its `_parser_raw` as the parse and the MCP call
captured). The cause is NOT what the first pass guessed, and both halves of the guess are
recorded here so nobody re-runs them:

* **the date window is NOT dropped.** `2026-08-01` / `2026-08-31` survive the parser
  post-processor, `ctx.parse.output` and `resolve_gate`'s `semantic_input`. The production
  header states them, and every wrong row returned IS inside the window;
* **the resolver does not mis-rank anything, and `REQUIRE_SPECIFIC_DOMAINS` is not
  involved.** `gate_passed: true`, `require_specific: false`, `gate_reason: "ok"`,
  `unresolved_tokens: []`, full token coverage.

What actually happens: `resolve_entity_body` sends `tokens: ["301-C001"]` - the carried
entity's `canonical_code` - and OMITS `entity_pins`, because the resolver route refuses a
pin in AND mode by design (`app/api/v1/system/references.py` ~:1652, "AND-mode intersection
has no per-token view to narrow"; PR #456's contract is OR-mode only). So the ONE thing that
identifies the row the customer picked - `uuid bec07281-8c52-4581-af8a-ad2d7a5f8e16` - is
thrown away, and a debtor code that **99 rows share in the production database** is
re-resolved from scratch. The resolver returns 15 of those 99 (its own `limit`), the picked
row is not among them, and the gate then PASSES the lot: `cust_pinned` is true (the entity
is carried and carries a uuid), which suppresses the "which company do you mean" ask - so
the pin stops the QUESTION without constraining the ANSWER. The header renders `entity.raw`
(right) while the rows are 15 other customers' orders (wrong).

**Fixed at the gate, not at the resolver (issue #715, closed 7 Sep 2026, H77/AC-825).**
AND-mode `entity_pins` stays refused, exactly as PR #456 shipped it
(`references.py:1650-1655`) - the two reasons that refusal was authored for (an
intersection has no per-token view to narrow, and a zero-intersection AND request
retries under `force_mode="or"` where a pin would suddenly start applying) are still
true, and `resolve_entity_body` is unchanged. `gate.py`'s own "A PINNED PICK WINS OVER
FUZZY RE-RESOLUTION" mechanism already re-seated a carried pick's uuid (exec
13705266's fix widened its ENTRY gate to carried pins, `pin_uuids_all`) but never
widened its FILTER: `pin_types` / `pin_bases` / `pin_codes` and `_keep`'s own uuid
check stayed built from `pins` / `pin_uuids` (this-turn only), so a carried pick with
no current-turn pin left `pin_types` empty and `_keep` kept every resolver row
"untouched" - the pin stopped the QUESTION without constraining the ANSWER, which is
this test's own account above. Widened those four reads to `pins_all` /
`pin_uuids_all`, the same set the entry gate already used - the resolver's own wrong
rows for the shared debtor code are then REPLACED by the picked uuid, never merged
with it. See `TestACarriedCustomerPickIsPinnedAtTheGateNotAtTheResolver`.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from app.services.chatbot import engine as engine_mod
from tests.chatbot.conftest import set_chatbot_switches
from tests.chatbot.test_engine import CONTACT_ID, _envelope, _parser_output
from tests.chatbot.test_engine_company_scope import _wire_real_resolve_entity
from tests.chatbot.test_r3_pending_end_to_end import _stub_parser

CUSTOMER_LABEL = "L. A. W. Transport (K.L.) Sdn. Bhd. (SRT)"  # roster label, brand-decorated
CUSTOMER_NAME = "L. A. W. Transport (K.L.) Sdn. Bhd."  # the REAL customers.customer_name column
DISTRACTOR_NAME = "SILK CABINETS SDN BHD"


@pytest.fixture()
def seeded(session_factory):
    db = session_factory()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, session_vars) "
            "VALUES (gen_random_uuid()::text, :cid, :phone, CAST(:sv AS jsonb))"
        ),
        {"cid": str(CONTACT_ID), "phone": "+60000000009", "sv": json.dumps({"variables": {}})},
    )
    db.commit()
    return db


def _seed_customers_and_orders(session_factory) -> tuple[str, str]:
    """The picked customer (L.A.W. Transport) and a DISTRACTOR (Silk Cabinets) with orders
    in the last-month window, so an unscoped fetch would visibly return the wrong one -
    exactly the production shape."""
    from app.models.company import Company
    from app.models.order import Customer, Order
    from tests._pg_fixture import unique_code

    db = session_factory()
    company = Company(name=unique_code("LAW"), code=unique_code("LAW")[:50])
    db.add(company)
    db.flush()
    picked = Customer(
        customer_code="301-C001",
        customer_name=CUSTOMER_NAME,
        is_active=True,
        company_id=company.id,
    )
    distractor = Customer(
        customer_code="ZZT-SILK",
        customer_name=DISTRACTOR_NAME,
        is_active=True,
        company_id=company.id,
    )
    # The REST of the picker's own roster, real fuzzy-overlapping names off the production
    # capture (`LAW KOK SIM`, `SUN CREST TRANSPORT`, `TSLAW LAND`, `CASH - MR. LAW`) - the
    # very reason a picker existed in the first place. A clean two-row database cannot
    # stress the free-text re-resolution AND mode forces on a carried, already-pinned
    # entity (`resolve_entity_body` skips `entity_pins` whenever `match_mode == "and"');
    # this roster can.
    siblings = [
        Customer(customer_code="302-L030", customer_name="LAW KOK SIM (PROJECT-CASH)", is_active=True, company_id=company.id),
        Customer(customer_code="301-S032", customer_name="SUN CREST TRANSPORT SDN BHD", is_active=True, company_id=company.id),
        Customer(customer_code="301-C048", customer_name="TSLAW LAND", is_active=True, company_id=company.id),
        Customer(customer_code="301-C002", customer_name="CASH (SRT) - MR. LAW", is_active=True, company_id=company.id),
    ]
    db.add_all([picked, distractor, *siblings])
    db.commit()
    db.add_all(
        [
            Order(
                order_number="ZZT-LAW-0001",
                customer_id=picked.id,
                debtor_name=picked.customer_name,
                actual_delivery_date=date(2026, 8, 15),
                company_id=company.id,
            ),
            Order(
                order_number="ZZT-SILK-0001",
                customer_id=distractor.id,
                debtor_name=distractor.customer_name,
                actual_delivery_date=date(2026, 8, 19),
                company_id=company.id,
            ),
        ]
    )
    db.commit()
    return str(picked.id), str(company.id)


def _seed_prior_disambiguation_state(
    session_factory, *, customer_id: str, company_id: str
) -> None:
    """The state a real "3" pick over a 5-name customer picker persists - the FULL shape
    production turn 0d9332a0's `previous_conversation_state` carries (round 12, `LAW-t3`),
    not a trimmed-down guess: `current_message: true` on the pick (the customer picker
    keeps the ACTIVE pick current on the turn right after it, only the NEXT topic change
    clears it), `picker_last_result_set` / `picker_domain` / `picker_selection_context`
    alongside the plain `last_result_set` / `selection_context` pair. A trimmed seed missing
    these took a DIFFERENT, already-fixed path (the domain/date-continuity nulling captured
    in the earlier `cc0075ae` turn) instead of the one this chain is about."""
    roster = [
        {"idx": 1, "uuid": "ZZT-law-roster-1", "label": "ZZT Roster One", "entity_type": "customer", "product": "ZZT-1"},
        {"idx": 2, "uuid": "ZZT-law-roster-2", "label": "ZZT Roster Two", "entity_type": "customer", "product": "ZZT-2"},
        {"idx": 3, "uuid": customer_id, "label": CUSTOMER_NAME, "entity_type": "customer", "product": "301-C001"},
        {"idx": 4, "uuid": "ZZT-law-roster-4", "label": "ZZT Roster Four", "entity_type": "customer", "product": "ZZT-4"},
        {"idx": 5, "uuid": "ZZT-law-roster-5", "label": "ZZT Roster Five", "entity_type": "customer", "product": "ZZT-5"},
    ]
    variables = {
        "message_type": "business_query",
        "intent_hint": "check_order",
        "domain_hint": "order",
        "user_goal": "trying to select an item from the previous list",
        "query_brands": [],
        "access_levels": [],
        "entities": [
            {
                "raw": CUSTOMER_LABEL,
                "hint": "customer",
                "ordinal": 3,
                "current_message": True,
                "uuid": customer_id,
                "canonical_code": "301-C001",
            }
        ],
        "routing": {"suggested_team": "customer_service", "suggested_agent": "order_enquiries"},
        "escalation": {"is_escalation_confirmation": False},
        "response": "Previous turn (order): returned 1 records",
        "last_result_set": roster,
        "selection_context": "disambiguation",
        "date_filter_start": None,
        "requested_attributes": ["delivery"],
        "date_filter_end": None,
        "date_mode": None,
        "match_mode": "and",
        "contains_flyer": False,
        "dym_offer": None,
        "dym_candidates": [],
        "ideation": None,
        "picker_last_result_set": roster,
        "picker_families": {},
        "picker_domain": "order",
        "picker_selection_context": "disambiguation",
        "routing_roster_plan": None,
        "routing_brand": None,
        "routing_brand_source": None,
        "routing_company": company_id,
        "routing_companies": [
            {
                "company_id": company_id,
                "company_name": "ZZT LAW Co",
                "brand_code": None,
                "codes": ["301-C001"],
                "labels": [CUSTOMER_NAME],
            }
        ],
        "pending": None,
    }
    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET session_vars = CAST(:sv AS jsonb) WHERE respond_io_id = :cid"),
        {"cid": str(CONTACT_ID), "sv": json.dumps({"variables": variables})},
    )
    db.commit()


def _wire(session_factory, monkeypatch) -> list[tuple[str, dict]]:
    from app.services.chatbot.lanes.business import services as business_services_mod

    set_chatbot_switches(session_factory, business_lane=True)
    db = session_factory()
    db.execute(
        text("UPDATE system_settings SET chatbot_completed_lanes = CAST(:l AS jsonb)"),
        {"l": '["business_query"]'},
    )
    db.commit()
    _wire_real_resolve_entity(monkeypatch)

    calls: list[tuple[str, dict]] = []

    def recording_mcp_call(name: str, args: dict) -> str:
        calls.append((name, dict(args)))
        return json.dumps({"answers": [{"note": "stub"}], "has_result": True})

    def fake_fetch_services(db: Any):
        # Only the MCP seam is stubbed. The turn parses as `order`, whose `DOMAIN_SPEC`
        # tool is `crm_order_management_orders_list` - the name this used to hand back as
        # a search hit.
        FetchServices = business_services_mod.FetchServices
        return FetchServices(mcp_call=recording_mcp_call)

    monkeypatch.setattr(engine_mod.business_services, "fetch_services", fake_fetch_services)
    return calls


PICKED_UUID = "bec07281-8c52-4581-af8a-ad2d7a5f8e16"


def _wrong_debtor_code_sibling(name: str, uuid: str) -> dict[str, Any]:
    """One of the OTHER accounts production's own debtor code 301-C001 is shared by -
    a different company, a different `uuid`, the SAME `canonical_code` a bare-text
    re-search returns it under."""
    return {
        "uuid": uuid,
        "entity_type": "customer",
        "canonical_code": "301-C001",
        "match_tier": "exact",
        "display": {"customer_name": name},
    }


class TestACarriedCustomerPickIsPinnedAtTheGateNotAtTheResolver:
    """Issue #715 (H77/AC-825), closed by `gate.py`'s own "PINNED PICK WINS OVER FUZZY
    RE-RESOLUTION" mechanism - NOT by an AND-mode `entity_pins`, which stays refused by
    PR #456's own contract (references.py:1650-1655: an AND-mode intersection has no
    per-token view to narrow, and a zero-intersection AND request retries under
    `force_mode='or'`, where a pin would suddenly start applying). `resolve_entity_body`
    is unchanged and still omits `entity_pins` in AND mode - asserted below, so this
    class cannot silently start relying on a pin that was never sent.

    The mechanism already existed for a CURRENT-turn pick (`pins`, `current_message is
    True`) and was already entered on a CARRIED one (`pins_all`, exec 13705266's own
    fix, gated at `len(pin_uuids_all) > 0`) - it re-seated the picked uuid so the
    ANSWER at least always carried it. What it never did for a carried pick was
    FILTER: `pin_types` / `pin_bases` / `pin_codes` and `_keep`'s own uuid check were
    all still built from `pins` / `pin_uuids` (this-turn only), so a carried pick with
    zero current-turn pins left `pin_types` empty, and `_keep`'s first line (`if t not
    in pin_types: return True`) kept every resolver row "untouched" - the pin stopped
    the QUESTION ("which company do you mean?") without constraining the ANSWER. Fixed
    by widening those four reads to `pins_all` / `pin_uuids_all`, the same set the
    entry gate already uses.

    Exercises `run_gate` directly rather than the resolver seam, matching the fix's
    OWN layer: the resolver's bare re-search of `301-C001` is exactly what production
    measured (99 rows share the code, the resolver returns up to its own limit of
    them, the picked uuid is not necessarily among them) - reproduced here at a
    tractable scale (3 siblings, not 99) because the MECHANISM being tested has no
    dependency on the count."""

    def test_resolve_entity_body_still_never_sends_a_pin_in_and_mode(self) -> None:
        """The companion fact the class docstring states: nothing upstream of the gate
        changed. If this ever starts asserting an `entity_pins` key, the "REPLACE at
        the gate, never intersect at the resolver" account above is stale."""
        from app.services.chatbot.lanes.business.resolve_gate import resolve_entity_body

        ctx = {
            "contact": {"id": "ZZT-item2", "phone": "+60000000009"},
            "text": {
                "message": {
                    "messageId": "ZZT-item2-unit",
                    "message": {"type": "text", "text": "last month"},
                }
            },
            "session": {"session_vars": {"variables": {}}},
            "parse": {
                "output": {
                    "message_type": "business_query",
                    "intent_hint": "check_order",
                    "domain_hint": "order",
                    "match_mode": "and",
                    "access_levels": [],
                    "date_filter_start": "2026-08-01",
                    "date_filter_end": "2026-08-31",
                    "entities": [
                        {
                            "raw": CUSTOMER_LABEL,
                            "hint": "customer",
                            "ordinal": 3,
                            "current_message": False,
                            "uuid": PICKED_UUID,
                            "canonical_code": "301-C001",
                        }
                    ],
                }
            },
            "access": {"allowed": True, "decision": "allow"},
            "media": None,
        }
        body = resolve_entity_body(ctx)
        assert body["tokens"] == ["301-C001"], body["tokens"]
        assert "entity_pins" not in body, body.get("entity_pins")

    def test_the_picked_uuid_replaces_the_resolvers_wrong_debtor_code_siblings(self) -> None:
        from app.services.chatbot.lanes.business.gate import run_gate

        parser = {
            "domain_hint": "order",
            "entities": [
                {
                    "raw": CUSTOMER_LABEL,
                    "hint": "customer",
                    "ordinal": 3,
                    "current_message": False,
                    "uuid": PICKED_UUID,
                    "canonical_code": "301-C001",
                }
            ],
        }
        # The resolver's OWN bare re-search of "301-C001" (AND-mode, no pin, per the
        # test above) - three OTHER accounts sharing the code, the picked row not
        # among them (production's own shape: the resolver returns up to its own
        # limit, 15 of 99, and the picked uuid is not guaranteed to be one of them).
        resolver = {
            "resolutions": [
                {
                    "token": "301-C001",
                    "matches": [
                        _wrong_debtor_code_sibling("SILK CABINETS SDN BHD", "zzt-wrong-1"),
                        _wrong_debtor_code_sibling("LAW KOK SIM (PROJECT-CASH)", "zzt-wrong-2"),
                        _wrong_debtor_code_sibling("SUN CREST TRANSPORT SDN BHD", "zzt-wrong-3"),
                    ],
                }
            ],
        }

        gate = run_gate({}, parser=parser, resolver=resolver, session=None)

        customer_uuids = {
            e["uuid"] for e in gate["compatible_entities"] if e.get("entity_type") == "customer"
        }
        assert customer_uuids == {PICKED_UUID}, (
            "a resolved customer pick must REPLACE the resolver's own re-resolved "
            "rows for the shared debtor code, never merge with them - the picked "
            f"uuid must be the ONLY customer entity the gate hands the order tool: {customer_uuids!r}"
        )


class TestLastMonthAfterAResolvedPickKeepsTheCustomerScope:
    """The SMALL-SCALE guard. Green today, and that green means "the lane wires the picked
    customer through when the code is unambiguous", NOT "item 2 is fixed" - see the class
    above and the module docstring. It is kept because it is the only end-to-end cover of
    the customer-scope-plus-window path, and it would catch a regression that broke it for
    everyone rather than only where a debtor code is shared."""

    def test_the_order_tool_call_carries_the_picked_customer_and_the_date_window(
        self, seeded, session_factory, monkeypatch
    ):
        from tests.chatbot._turn_helpers import make_office_tier

        # V1-DOC-SCOPE: an unlinked contact picking any customer's orders is staff.
        make_office_tier(session_factory, CONTACT_ID)
        customer_id, company_id = _seed_customers_and_orders(session_factory)
        _seed_prior_disambiguation_state(session_factory, customer_id=customer_id, company_id=company_id)
        calls = _wire(session_factory, monkeypatch)
        monkeypatch.setattr(
            engine_mod, "_contact_company_scope", lambda factory, cid: frozenset({company_id})
        )

        qf = _parser_output(
            message_type="business_query",
            intent_hint=None,
            domain_hint=None,
            entities=[],
            entity_op="reuse",
            date_mode=None,
            date_filter_start="2026-08-01",
            date_filter_end="2026-08-31",
        )
        _stub_parser(monkeypatch, qf)
        envelope = _envelope(is_test=True)
        envelope.message["message"]["messageId"] = "ZZT-item2-last-month"
        envelope.message["message"]["message"]["text"] = "last month"

        head = engine_mod.run_turn(envelope, session_factory=session_factory)
        assert head.status in ("done", "delegated"), head.error

        order_calls = [c for c in calls if c[0] == "crm_order_management_orders_list"]
        assert order_calls, f"the order tool was never called: {calls!r}"
        _, args = order_calls[0]

        assert args.get("customer_ids") == [customer_id], (
            "the picked customer's uuid must reach the order tool call's customer_ids - "
            f"it must not go out unscoped: {args!r}"
        )
        assert args.get("actual_delivery_date_from") == "2026-08-01", args
        assert args.get("actual_delivery_date_to") == "2026-08-31", args
