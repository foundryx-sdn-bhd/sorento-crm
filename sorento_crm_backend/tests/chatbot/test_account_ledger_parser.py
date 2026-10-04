"""Phase 2 RED tests - the parser learns `account` (ACCOUNT-LEDGER, AC-4).

Plan `documentation/plans/chatbot/PLAN-account-ledger-2oct.md`, card section "Q5: the exact
parser addendum". The addendum text below is copied VERBATIM from that card (the content of
its ```text fence, starting with the blank line), not read from the card at test time, so
archiving the plan does not break this file.

* The strict schema's entity item declares `account` as integer or null and requires it.
* `ACCOUNT_LEDGER_ADDENDUM` equals the card's text and sits after PO_SPO_WAREHOUSE_ADDENDUM and
  before MEMORY_ADDENDUM (MEMORY stays the prompt's tail).
"""
from __future__ import annotations

from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA
from app.services.chatbot_parser_prompt import (
    MEMORY_ADDENDUM,
    LOW_STOCK_FILTERS_ADDENDUM,
    PO_SPO_WAREHOUSE_ADDENDUM,
    SEMANTIC_PARSER_PROMPT,
)

CARD_ADDENDUM = '\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nCUSTOMER ACCOUNT NUMBER\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nEvery entity object carries one more key, exactly as if it were listed there:\n\n  "account": an integer, or null\n\n== ACCOUNT: which numbered account of a customer the message names ==\nA customer can have several accounts, numbered 1, 2, 3 ... Set "account" on the CUSTOMER\nentity when the CURRENT message names an account number for it, in any spelling or\nlanguage. Roman numerals become integers.\n  - "account 1", "acc 1", "a/c 1", "A/C I", "ac1", "akaun 1", "户口1", "第一个户口" -> 1\n  - "account 2", "acc2", "A/C II", "a/c ii", "akaun 2" -> 2\n  - "A/C III" -> 3, "A/C IV" -> 4\nThe account words are NOT part of the name: leave them out of raw and canonical_code.\n  - "Soon Heng account 1 outstanding" -> entities [{"raw": "Soon Heng", "hint":\n    "customer", "account": 1}]\n  - "hanlim acc 2 sales" -> entities [{"raw": "hanlim", "hint": "customer", "account": 2}]\n  - "Hanlim A/C II and 1 Living A/C I" -> two customer entities, account 2 and account 1\nAn account number with NO customer name in the current message ("account 2 outstanding")\n-> emit ONE entity {"raw": null, "hint": "customer", "account": 2, "current_message":\ntrue}. Never copy a customer name from earlier in the conversation to fill it.\n"my account" with no number names no account: "account" stays null and self_reference\nis true as usual. "my account 2" -> self_reference true AND the entity {"raw": null,\n"hint": "customer", "account": 2}.\nEvery non-customer entity, and every customer entity without an account number, has\n"account": null. Never guess a number.\n'



def _entity_item() -> dict:
    return PARSE_OUTPUT_JSON_SCHEMA["properties"]["entities"]["items"]


def test_entity_schema_declares_account_integer_or_null_and_requires_it() -> None:
    item = _entity_item()
    assert "account" in item["properties"], sorted(item["properties"])
    kind = item["properties"]["account"]["type"]
    assert sorted(kind if isinstance(kind, list) else [kind]) == ["integer", "null"], kind
    assert "account" in item["required"]
    assert item["additionalProperties"] is False


def _addendum() -> str:
    from app.services import chatbot_parser_prompt

    assert hasattr(chatbot_parser_prompt, "ACCOUNT_LEDGER_ADDENDUM"), "ACCOUNT_LEDGER_ADDENDUM is not defined"
    return chatbot_parser_prompt.ACCOUNT_LEDGER_ADDENDUM


def test_addendum_is_the_cards_text_verbatim() -> None:
    assert _addendum() == CARD_ADDENDUM


def test_addendum_starts_with_the_blank_line() -> None:
    assert _addendum().startswith("\n")


def test_prompt_carries_the_addendum_between_po_spo_warehouse_and_memory() -> None:
    addendum = _addendum()
    assert addendum in SEMANTIC_PARSER_PROMPT
    assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM), "MEMORY must stay the tail"
    # LOWSTOCK-SEMANTIC: LOW_STOCK_FILTERS_ADDENDUM sits between this one and MEMORY.
    before_memory = SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM)
    assert before_memory.endswith(PO_SPO_WAREHOUSE_ADDENDUM + addendum), (
        "ACCOUNT_LEDGER_ADDENDUM must sit directly after PO_SPO_WAREHOUSE_ADDENDUM and before MEMORY_ADDENDUM"
    )


def test_prompt_teaches_the_spellings_and_the_unnamed_rule() -> None:
    for needle in ('"akaun 1"', "户口1", "Never copy a customer name from earlier", '"raw": null'):
        assert needle in SEMANTIC_PARSER_PROMPT, needle
