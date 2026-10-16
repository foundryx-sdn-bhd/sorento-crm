"""The parser schema is written in key order by the model: message_type is derived FROM
intent_hint and domain_hint (prompt v94), so it must be emitted after them."""

from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

# Frozen copy of main's property order at 05f030e15e, message_type first.
MAIN_ORDER = [
    "message_type",
    "intent_hint",
    "domain_hint",
    "scope_intent",
    "is_affirmative",
    "user_goal",
    "continuation",
    "access_levels",
    "broaden_axis",
    "broaden_to",
    "date_mode",
    "date_filter_start",
    "date_filter_end",
    "match_mode",
    "demand_qty",
    "entities",
    "entity_op",
    "domain_in_message",
    "requested_attributes",
    "contains_flyer",
    "reference_positions",
    "reference_target",
    "person_mention",
    "is_active",
    "order_status",
    "group_by",
    "top_n",
    "sales_channel",
    "rank_by",
    "basis",
    "rank_group",
    "rank_direction",
    "sort_by",
    "sort_dir",
    "ranking_refine",
    "ranking_drill",
    "measure",
    "sales_basis",
    "sales_company",
    "promotion_kind",
    "portal_form",
    "correction",
    "self_reference",
    "routing",
    "escalation",
    "document",
    "status",
    "asks",
    "sub_asks",
    "family",
    "topic_reset",
    "proceed_anyway",
    "open_question_answer",
    "low_stock",
    "chat_report",
    "po_refs",
    "find",
    "anaphora",
    "profile_statements",
]


def test_message_type_follows_intent_and_domain_hint():
    keys = list(PARSE_OUTPUT_JSON_SCHEMA["properties"])
    assert keys.index("intent_hint") < keys.index("domain_hint") < keys.index("message_type")
    assert keys.index("message_type") == keys.index("domain_hint") + 1


def test_every_other_key_keeps_its_relative_order():
    keys = list(PARSE_OUTPUT_JSON_SCHEMA["properties"])
    assert [k for k in keys if k != "message_type"] == [k for k in MAIN_ORDER if k != "message_type"]
    assert set(keys) == set(MAIN_ORDER)
    assert PARSE_OUTPUT_JSON_SCHEMA["properties"]["message_type"] == {"type": "string"}
