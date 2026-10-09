"""CA-1 facts: the pure outcome rules and the trace flattener (tester-first RED).

Plan: documentation/plans/chatbot/PLAN-chat-analytics-ca1-facts-09oct.md, "Outcome rules"
(first match wins) and "Code" (`turn_outcome`, `FAILED_OUTCOMES`, `flatten`).

`app.services.chatbot.analytics.outcome` does not exist yet, so every test fails with
`ModuleNotFoundError` (a missing-module red, not a fixture bug). No DB: both functions are
pure over the `memory._turn_to_digest_dict` shape plus `status`.

Two inferences the plan does not spell out, pinned here so the coder sees them:
* `failure_reason` equals the outcome for the three failed outcomes and is None otherwise
  (the design's failure_reason vocabulary is `cannot_do` / `missed` / `phantom` /
  `technical`).
* `named_codes` always carries both keys (`product`, `brand`), each a de-duplicated list
  in trace order; an entity with no `canonical_code` contributes nothing.
"""
from __future__ import annotations

import json

import pytest

from tests.chatbot._chat_facts_helpers import (
    CUSTOMER_WORDS,
    REPLY_WORDS,
    answered_trace,
    apply_event,
    entity_dict,
    event,
    looked_up,
    outcome_mod,
    replied,
    stage,
    turn_dict,
)


def _outcome(turn):
    return outcome_mod().turn_outcome(turn)


def test_failed_outcomes_constant():
    assert outcome_mod().FAILED_OUTCOMES == ("technical", "cannot_do", "missed")


# ------------------------------------------------------------------ rule 1: technical


def test_failed_status_is_technical_and_failed():
    assert _outcome(turn_dict(status="failed")) == ("technical", "technical")


def test_failed_status_beats_every_other_signal():
    turn = turn_dict(
        apply_event(lane="escalation_barred", ask="which one?"),
        looked_up(missed=["inventory"]),
        event("required_ask", ask="x"),
        event("domain_grant", domain="order", skipped="not_granted", needs="k"),
        status="failed",
        branch_kind="low_signal",
    )
    assert _outcome(turn) == ("technical", "technical")


# ------------------------------------------------------------------ rule 2: noise


def test_low_signal_is_noise_and_not_a_failure():
    assert _outcome(turn_dict(branch_kind="low_signal")) == ("noise", None)


def test_noise_beats_a_refusal_lane():
    turn = turn_dict(apply_event(lane="escalation_barred"), branch_kind="low_signal")
    assert _outcome(turn) == ("noise", None)


# ------------------------------------------------------------------ rule 3: escalation barred


def test_escalation_barred_lane_is_refused_policy_even_when_out_of_scope():
    turn = turn_dict(apply_event(lane="escalation_barred"), branch_kind="out_of_scope")
    assert _outcome(turn) == ("refused_policy", None)


def test_escalation_barred_lane_alone_is_refused_policy():
    assert _outcome(turn_dict(apply_event(lane="escalation_barred"))) == ("refused_policy", None)


# ------------------------------------------------------------------ rule 4: handover


def _escalating(*, message_type=None, confirmation=False, teams=None, rules_fired=(), **kwargs):
    """An `apply` event the way the engine persists it: the verdict carries `message_type` and
    `escalation`, the event carries `rules_fired`."""
    record = apply_event(lane="escalation", **kwargs)
    record["verdict"]["message_type"] = message_type
    record["verdict"]["escalation"] = {"is_escalation_confirmation": confirmation, "named_teams": teams or []}
    record["rules_fired"] = list(rules_fired)
    return record


def test_an_escalation_confirmation_is_handover():
    turn = turn_dict(_escalating(confirmation=True), branch_kind="out_of_scope")
    assert _outcome(turn) == ("handover", None)


def test_an_escalation_message_type_is_handover():
    turn = turn_dict(_escalating(message_type="escalation"), branch_kind="out_of_scope")
    assert _outcome(turn) == ("handover", None)


def test_an_accepted_offer_is_handover():
    turn = turn_dict(_escalating(rules_fired=["answer_pending_accept"]), branch_kind="out_of_scope")
    assert _outcome(turn) == ("handover", None)


def test_a_named_team_is_handover():
    turn = turn_dict(_escalating(teams=["accounts"]), branch_kind="out_of_scope")
    assert _outcome(turn) == ("handover", None)


def test_a_help_request_that_names_a_business_is_handover():
    for kwargs in ({"domain_hint": "inventory"}, {"domains": ["inventory"]}, {"intent_hint": "stock"}):
        turn = turn_dict(_escalating(message_type="request_for_help", **kwargs), branch_kind="out_of_scope")
        assert _outcome(turn) == ("handover", None), kwargs


def test_a_help_request_that_asks_for_no_person_and_names_no_business_is_off_topic():
    turn = turn_dict(_escalating(message_type="request_for_help"), branch_kind="out_of_scope")
    assert _outcome(turn) == ("off_topic", None)


def test_off_topic_is_not_a_failure_and_not_noise():
    assert "off_topic" not in outcome_mod().FAILED_OUTCOMES
    assert _outcome(turn_dict(_escalating(message_type="request_for_help"), branch_kind="out_of_scope"))[1] is None


def test_the_barred_escalation_lane_still_beats_off_topic():
    turn = turn_dict(apply_event(lane="escalation_barred"), branch_kind="out_of_scope")
    assert _outcome(turn) == ("refused_policy", None)


# ------------------------------------------------------------------ rule 5: refused_policy


@pytest.mark.parametrize("branch", ["access_denied", "ai_not_allowed", "stock_denied"])
def test_denial_branches_are_refused_policy(branch):
    assert _outcome(turn_dict(branch_kind=branch)) == ("refused_policy", None)


def test_domain_grant_not_granted_is_refused_policy():
    turn = turn_dict(event("domain_grant", domain="order", skipped="not_granted", needs="orders.reveal"))
    assert _outcome(turn) == ("refused_policy", None)


def test_domain_grant_refusal_beats_a_miss():
    turn = turn_dict(
        event("domain_grant", domain="order", skipped="not_granted", needs="orders.reveal"),
        looked_up(missed=["order"]),
    )
    assert _outcome(turn) == ("refused_policy", None)


def test_a_domain_grant_event_that_skipped_for_another_reason_is_not_a_refusal():
    turn = turn_dict(event("domain_grant", domain="order", skipped="something_else"), looked_up(sections=2))
    assert _outcome(turn) == ("answered", None)


def test_denied_with_nothing_fetched_is_refused_policy():
    turn = turn_dict(apply_event(denied=["order"], fetch=[]))
    assert _outcome(turn) == ("refused_policy", None)


def test_denied_but_something_fetched_is_not_a_refusal():
    turn = turn_dict(apply_event(denied=["order"], fetch=["inventory"]), looked_up(sections=2))
    assert _outcome(turn) == ("answered", None)


# ------------------------------------------------------------------ rule 6: cannot_do


def test_not_supported_is_cannot_do_and_failed():
    assert _outcome(turn_dict(branch_kind="not_supported")) == ("cannot_do", "cannot_do")


def test_not_supported_beats_a_clarifying_ask():
    turn = turn_dict(apply_event(ask="which one?"), branch_kind="not_supported")
    assert _outcome(turn) == ("cannot_do", "cannot_do")


# ------------------------------------------------------------------ rule 7: clarified


@pytest.mark.parametrize("branch", ["clarify_menu", "demand_qty", "escalate_offer", "offer_hold"])
def test_clarifying_branches_are_clarified(branch):
    assert _outcome(turn_dict(branch_kind=branch)) == ("clarified", None)


def test_plan_ask_is_clarified():
    assert _outcome(turn_dict(apply_event(ask="which size?"))) == ("clarified", None)


def test_required_ask_event_still_asking_is_clarified():
    # The low-stock and report lanes write `done: False` while they are still asking.
    assert _outcome(turn_dict(event("required_ask", ask="low_stock_report", done=False))) == ("clarified", None)


def test_required_ask_event_that_is_done_is_not_clarified():
    turn = turn_dict(event("required_ask", ask="low_stock_report", done=True), looked_up(sections=2))
    assert _outcome(turn) == ("answered", None)


def test_a_required_ask_answered_or_dropped_does_not_hide_a_miss():
    """The engine writes `{verdict_rule, ask}` on the turn AFTER the question: the customer
    answered it (or it was dropped), so nothing was asked on this turn."""
    for rule in ("required_ask_answer", "required_ask_dropped"):
        turn = turn_dict(event("required_ask", verdict_rule=rule, ask="low_stock_report"), looked_up(missed=["stock"]))
        assert _outcome(turn) == ("missed", "missed"), rule


def test_clarified_beats_missed():
    turn = turn_dict(apply_event(ask="which size?"), looked_up(missed=["inventory"]))
    assert _outcome(turn) == ("clarified", None)


# ------------------------------------------------------------------ rule 8: missed


def test_looked_up_missed_is_missed_and_failed():
    assert _outcome(turn_dict(looked_up(missed=["inventory"]))) == ("missed", "missed")


def test_crossdomain_with_no_answer_is_missed():
    turn = turn_dict(event("crossdomain", domain="inventory", rungs_tried=["incoming_po"], answered=None))
    assert _outcome(turn) == ("missed", "missed")


def test_crossdomain_that_answered_is_not_a_miss():
    turn = turn_dict(
        event("crossdomain", domain="inventory", rungs_tried=["incoming_po"], answered="incoming_po"),
        looked_up(sections=1),
    )
    assert _outcome(turn) == ("answered", None)


def test_a_crossdomain_event_of_the_skipped_shape_is_not_a_miss():
    """Only an event that reports `answered: null` found nothing; a skipped ladder
    writes no `answered` key at all."""
    turn = turn_dict(event("crossdomain", domain="inventory", skipped="no_ladder"), looked_up(sections=1))
    assert _outcome(turn) == ("answered", None)


def test_missed_beats_answered_empty():
    assert _outcome(turn_dict(looked_up(sections=0, missed=["inventory"]))) == ("missed", "missed")


# ------------------------------------------------------------------ rule 9: answered_empty


def test_zero_sections_and_no_miss_is_answered_empty_and_not_failed():
    assert _outcome(turn_dict(looked_up(sections=0))) == ("answered_empty", None)


def test_a_looked_up_stage_with_no_sections_fact_is_not_answered_empty():
    assert _outcome(turn_dict(looked_up(sections=None))) == ("answered", None)


def test_nonzero_sections_is_answered():
    assert _outcome(turn_dict(looked_up(sections=3))) == ("answered", None)


# ------------------------------------------------------------------ rule 10: answered


def test_everything_else_is_answered():
    assert _outcome(turn_dict(*answered_trace())) == ("answered", None)


def test_an_empty_trace_is_answered():
    assert _outcome(turn_dict()) == ("answered", None)


# ------------------------------------------------------------------ phantom_answer


def test_phantom_answer_sets_the_flag_and_leaves_the_outcome():
    turn = turn_dict(*answered_trace(), event("phantom_answer", open_question_answer=1))
    assert _outcome(turn) == ("answered", None)
    assert outcome_mod().flatten(turn)["phantom_dropped"] is True


def test_no_phantom_event_means_the_flag_is_false():
    assert outcome_mod().flatten(turn_dict(*answered_trace()))["phantom_dropped"] is False


def test_phantom_answer_on_a_failed_turn_stays_technical():
    turn = turn_dict(event("phantom_answer"), status="failed")
    assert _outcome(turn) == ("technical", "technical")
    assert outcome_mod().flatten(turn)["phantom_dropped"] is True


# ------------------------------------------------------------------ flatten


def test_flatten_carries_outcome_branch_and_failure_reason():
    flat = outcome_mod().flatten(turn_dict(looked_up(missed=["inventory"]), branch_kind="answer_lookup"))
    assert flat["outcome"] == "missed"
    assert flat["failure_reason"] == "missed"
    assert flat["branch_kind"] == "answer_lookup"


def test_flatten_domain_prefers_the_plan_then_the_hint():
    both = turn_dict(apply_event(domain_hint="product", domains=["inventory"]))
    hint_only = turn_dict(apply_event(domain_hint="product", domains=[]))
    neither = turn_dict(apply_event())
    flatten = outcome_mod().flatten
    assert flatten(both)["domain"] == "inventory"
    assert flatten(hint_only)["domain"] == "product"
    assert flatten(neither)["domain"] is None


def test_flatten_intent_is_the_verdict_intent_hint():
    assert outcome_mod().flatten(turn_dict(apply_event(intent_hint="stock")))["intent"] == "stock"
    assert outcome_mod().flatten(turn_dict(apply_event()))["intent"] is None


def test_flatten_tools_are_the_distinct_tool_names_in_call_order():
    turn = turn_dict(
        event("tool", name="crm_stock", args={}, envelope={}, ms=3),
        event("tool", name="crm_price", args={}, envelope={}, ms=3),
        event("tool", name="crm_stock", args={}, envelope={}, ms=3, alternative_of="ZZTOLD"),
    )
    assert outcome_mod().flatten(turn)["tools"] == ["crm_stock", "crm_price"]


def test_flatten_with_no_tools_gives_an_empty_list():
    assert outcome_mod().flatten(turn_dict())["tools"] == []


def test_flatten_rows_offered_reads_the_replied_facts():
    assert outcome_mod().flatten(turn_dict(replied(rows_offered=7)))["rows_offered"] == 7
    assert outcome_mod().flatten(turn_dict(replied()))["rows_offered"] is None
    assert outcome_mod().flatten(turn_dict())["rows_offered"] is None


def test_flatten_named_codes_by_hint_deduplicated_in_order():
    turn = turn_dict(
        apply_event(
            entities=[
                entity_dict("product", "ZZTP1"),
                entity_dict("brand", "ZZTB1"),
                entity_dict("product", "ZZTP2"),
                entity_dict("product", "ZZTP1"),
                entity_dict("customer", "ZZTC1"),
                entity_dict("product", None, raw="that blue one"),
            ]
        )
    )
    named = outcome_mod().flatten(turn)["named_codes"]
    assert named == {"product": ["ZZTP1", "ZZTP2"], "brand": ["ZZTB1"]}


def test_flatten_named_codes_keeps_both_keys_when_nothing_was_named():
    assert outcome_mod().flatten(turn_dict())["named_codes"] == {"product": [], "brand": []}


def test_flatten_shown_codes_come_from_the_reply_result_set():
    turn = turn_dict(
        replied(
            rows_offered=2,
            result_set=[
                {"idx": 1, "label": "ZZT one", "code": "ZZTP1", "entity_type": "product"},
                {"idx": 2, "label": "ZZT two", "code": "ZZTP2", "entity_type": "product"},
            ],
        )
    )
    assert outcome_mod().flatten(turn)["shown_codes"] == ["ZZTP1", "ZZTP2"]
    assert outcome_mod().flatten(turn_dict())["shown_codes"] == []


def test_flatten_output_is_json_serialisable_and_has_the_contract_keys():
    flat = outcome_mod().flatten(turn_dict(*answered_trace(named=(("product", "ZZTP1"),), shown=(("product", "ZZTP2"),))))
    json.dumps(flat)
    for key in (
        "domain", "intent", "outcome", "failure_reason", "branch_kind", "tools",
        "rows_offered", "phantom_dropped", "named_codes", "shown_codes",
    ):
        assert key in flat, key


# ------------------------------------------------------------------ privacy guard


def test_flatten_holds_no_customer_or_reply_text():
    sentence = "ZZT-RAW-SENTENCE could you quote me the big white basin for the Tan project"
    turn = turn_dict(
        apply_event(
            domain_hint="product",
            entities=[
                # A resolved entity: only its canonical code may travel.
                {"hint": "product", "raw": sentence, "canonical_code": "ZZTP1"},
                # An unresolved one: nothing of it may travel.
                {"hint": "product", "raw": sentence + " again", "canonical_code": None},
            ],
        ),
        replied(
            rows_offered=1,
            result_set=[{"idx": 1, "label": "ZZT-LABEL-TEXT white basin", "code": "ZZTP2", "entity_type": "product"}],
        ),
        stage("understood", facts={"note": sentence}, raw={"message": CUSTOMER_WORDS}),
        message=CUSTOMER_WORDS,
        envelope={"message": {"text": CUSTOMER_WORDS}},
    )
    dumped = json.dumps(outcome_mod().flatten(turn))
    for forbidden in (sentence, CUSTOMER_WORDS, REPLY_WORDS, "ZZT-LABEL-TEXT"):
        assert forbidden not in dumped, forbidden
    assert "ZZTP1" in dumped and "ZZTP2" in dumped


def test_a_blank_or_null_string_hint_names_no_business():
    """The parser sometimes writes the literal string "null" for an absent hint."""
    for hint in ("null", " NULL ", "", "   "):
        turn = turn_dict(_escalating(message_type="request_for_help", domain_hint=hint, intent_hint=hint), branch_kind="out_of_scope")
        assert _outcome(turn) == ("off_topic", None), repr(hint)


# ------------------------------------------------------------------ turns before the apply event (5 to 24 Sep)


def _understood(*, domain=None, intent=None, message_type="business_query", entities=0):
    """The `understood` STAGE record the engine wrote before the `apply` kind event existed:
    the parser verdict as facts, `entities` an integer COUNT."""
    return stage(
        "understood",
        facts={
            "domain": domain,
            "intent": intent,
            "message_type": message_type,
            "entities": entities,
            "parser_bypassed": False,
            "prompt_version": "zzt-v1",
            "tokens": 123,
        },
    )


def test_no_apply_event_domain_and_intent_come_from_the_understood_stage():
    turn = turn_dict(
        _understood(domain="inventory", intent="stock_check"), branch_kind="business_query"
    )
    flat = outcome_mod().flatten(turn)
    assert flat["domain"] == "inventory"
    assert flat["intent"] == "stock_check"


def test_no_apply_event_out_of_scope_naming_a_domain_is_handover():
    turn = turn_dict(
        _understood(domain="order", message_type="request_for_help"), branch_kind="out_of_scope"
    )
    assert _outcome(turn) == ("handover", None)


@pytest.mark.parametrize("domain", [None, "null", "", "NULL"])
@pytest.mark.parametrize("message_type", ["casual", "request_for_help"])
def test_no_apply_event_out_of_scope_naming_no_domain_is_off_topic(domain, message_type):
    turn = turn_dict(
        _understood(domain=domain, intent=domain, message_type=message_type),
        branch_kind="out_of_scope",
    )
    assert _outcome(turn) == ("off_topic", None)


def test_no_apply_event_escalation_message_type_is_handover():
    turn = turn_dict(_understood(message_type="escalation"), branch_kind="out_of_scope")
    assert _outcome(turn) == ("handover", None)


def test_no_apply_event_integer_entity_count_names_no_items():
    turn = turn_dict(
        _understood(domain="inventory", intent="stock_check", entities=3),
        branch_kind="business_query",
    )
    flat = outcome_mod().flatten(turn)
    assert flat["named_codes"] == {"product": [], "brand": []}
    assert flat["domain"] == "inventory"


def test_the_apply_verdict_wins_over_the_understood_stage():
    turn = turn_dict(
        _understood(domain="order", intent="order_status"),
        apply_event(domain_hint="inventory", intent_hint="stock"),
        branch_kind="business_query",
    )
    flat = outcome_mod().flatten(turn)
    assert flat["domain"] == "inventory"
    assert flat["intent"] == "stock"
