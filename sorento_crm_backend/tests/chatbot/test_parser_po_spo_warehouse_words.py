"""RED tests - the parser knows the PO / SPO warehouse and sort words.

`documentation/plans/chatbot/PLAN-po-spo-warehouse-29sep.md` sections S2, S3, S4;
`documentation/plans/chatbot/po-spo-warehouse-29sep-acceptance-criteria.md` AC-3, AC-4, AC-5.

No LLM is called: the corpus JSON beside this file grades the CONTRACT between the prompt,
the declared vocabulary and the schema (same mechanism as `test_parser_low_stock_words.py`).
`PO_SPO_WAREHOUSE_ADDENDUM` is imported inside a helper so a missing name is one red test,
not a collection error.

Coupling the coder owes, which no test here can express: `test_parser_prompt_is_live.py`
(`_without_growth_r1_addendum`, `CONSTANT_CHARS` +6) and
`test_parser_growth_r1_reachability.py::test_the_addendum_is_appended_to_both_bodies` strip a
fixed suffix chain and must peel the new addendum right after `MEMORY_ADDENDUM`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import contracts

PHRASES_FILE = Path(__file__).parent / "fixtures" / "parser_po_spo_warehouse_phrases.json"

REQUIRED_PHRASES = (
    "PO to BRW",
    "SPO for BRW-BB",
    "SPO SRT79-SS",
    "last in SRTWC286 at BRW",
    "latest PO for SRT79-SS",
    "SPO biggest quantity first at BRW",
    "oldest PO first",
    "SPO by GR date",
    "PO by supplier",
    "SPO by amount",
    "biggest quantity first",
)
SORT_BY_ENUM = (
    "date", "expected_date", "quantity", "outstanding",
    "received_date", "received_quantity", "product", "supplier",
)


def _phrases() -> list[dict[str, Any]]:
    return json.loads(PHRASES_FILE.read_text(encoding="utf-8"))["phrases"]


def _prompt():
    import app.services.chatbot_parser_prompt as mod

    addendum = getattr(mod, "PO_SPO_WAREHOUSE_ADDENDUM", None)
    assert addendum is not None, (
        "app.services.chatbot_parser_prompt.PO_SPO_WAREHOUSE_ADDENDUM does not exist yet (AC-4)"
    )
    return mod, addendum


def _schema_props() -> dict[str, Any]:
    from app.services.chatbot.head.parser import PARSE_OUTPUT_JSON_SCHEMA

    return PARSE_OUTPUT_JSON_SCHEMA["properties"]


# --------------------------------------------------------------------------- #
# AC-3 - the body no longer sends SPO to incoming
# --------------------------------------------------------------------------- #


class TestTheBodyNoLongerSendsSpoToIncoming:
    def test_the_body_does_not_map_spo_to_incoming(self) -> None:
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert '"SPO" ->\nincoming' not in SEMANTIC_PARSER_PROMPT

    def test_the_body_maps_spo_to_spo_allocation_once(self) -> None:
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert SEMANTIC_PARSER_PROMPT.count('"SPO" ->\nspo_allocation') == 1


# --------------------------------------------------------------------------- #
# AC-4 - the addendum is taught, in the right place
# --------------------------------------------------------------------------- #


class TestTheAddendumIsTaught:
    def test_the_addendum_exists(self) -> None:
        _prompt()

    def test_the_addendum_stacks_between_escalation_and_memory(self) -> None:
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            ESCALATION_CONFIRMATION_ADDENDUM,
            MEMORY_ADDENDUM,
            LOW_STOCK_FILTERS_ADDENDUM,
            SEMANTIC_PARSER_PROMPT,
        )

        _mod, addendum = _prompt()
        assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM)
        # ACCOUNT_LEDGER_ADDENDUM (ACCOUNT-LEDGER) now sits between this one and MEMORY.
        before_memory = SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(
            LOW_STOCK_FILTERS_ADDENDUM
        ).removesuffix(
            ACCOUNT_LEDGER_ADDENDUM
        )
        assert before_memory.endswith(addendum), (
            "PO_SPO_WAREHOUSE_ADDENDUM must sit directly before ACCOUNT_LEDGER_ADDENDUM"
        )
        assert before_memory.removesuffix(addendum).endswith(ESCALATION_CONFIRMATION_ADDENDUM), (
            "PO_SPO_WAREHOUSE_ADDENDUM must stack directly after ESCALATION_CONFIRMATION_ADDENDUM"
        )

    def test_the_corpus_covers_the_required_phrases(self) -> None:
        phrases = {s["phrase"] for s in _phrases()}
        assert phrases >= set(REQUIRED_PHRASES), set(REQUIRED_PHRASES) - phrases

    @pytest.mark.parametrize("sample", _phrases(), ids=lambda s: s["phrase"])
    def test_every_sample_cue_is_taught_verbatim(self, sample: dict) -> None:
        _mod, addendum = _prompt()
        assert sample["cue"] in addendum, (
            f"{sample['phrase']!r}: the addendum does not carry the cue {sample['cue']!r}"
        )

    @pytest.mark.parametrize("sample", _phrases(), ids=lambda s: s["phrase"])
    def test_every_sample_expects_declared_vocabulary(self, sample: dict) -> None:
        expect = sample["expect"]
        if expect.get("domain_hint") is not None:
            assert expect["domain_hint"] in contracts.DOMAIN_HINTS
        if expect.get("intent_hint") is not None:
            assert expect["intent_hint"] in contracts.INTENT_HINTS
        for ent in expect.get("entities") or []:
            assert ent["hint"] in ("warehouse", "product"), ent
        if expect.get("sort_by") is not None:
            assert expect["sort_by"] in _schema_props()["sort_by"]["enum"]
        if expect.get("sort_dir") is not None:
            assert expect["sort_dir"] in _schema_props()["sort_dir"]["enum"]

    def test_the_addendum_names_the_spo_rule_and_never_incoming_for_it(self) -> None:
        _mod, addendum = _prompt()
        assert "spo_allocation" in addendum
        assert "check_spo" in addendum
        assert "warehouse" in addendum.lower()

    def test_the_addendum_names_both_new_output_keys(self) -> None:
        _mod, addendum = _prompt()
        assert "sort_by" in addendum and "sort_dir" in addendum

    def test_an_amount_sort_is_taught_as_null(self) -> None:
        by_phrase = {s["phrase"]: s for s in _phrases()}
        assert "SPO by amount" in by_phrase
        assert by_phrase["SPO by amount"]["expect"]["sort_by"] is None
        assert "rank_by" in _prompt()[1], "the addendum must say an amount sort is never rank_by"


# --------------------------------------------------------------------------- #
# AC-5 - the schema declares the sort keys
# --------------------------------------------------------------------------- #


class TestTheSchemaDeclaresTheSortKeys:
    def test_sort_by_is_a_nullable_enum(self) -> None:
        prop = _schema_props().get("sort_by")
        assert prop is not None, "PARSE_OUTPUT_JSON_SCHEMA has no sort_by property (AC-5)"
        assert set(prop["enum"]) == set(SORT_BY_ENUM) | {None}

    def test_sort_dir_is_a_nullable_enum(self) -> None:
        prop = _schema_props().get("sort_dir")
        assert prop is not None, "PARSE_OUTPUT_JSON_SCHEMA has no sort_dir property (AC-5)"
        assert set(prop["enum"]) == {"asc", "desc", None}

    @pytest.mark.parametrize("key", ["sort_by", "sort_dir"])
    def test_both_are_required_and_tolerated_absent(self, key: str) -> None:
        from app.services.chatbot.head.parser import (
            DECLARED_KEYS,
            PARSE_OUTPUT_JSON_SCHEMA,
            TOLERATED_ABSENT,
        )

        assert key in PARSE_OUTPUT_JSON_SCHEMA["required"], key
        assert key in DECLARED_KEYS, key
        assert key in TOLERATED_ABSENT, key

    def test_the_prompt_output_text_names_both_keys(self) -> None:
        from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

        assert '"sort_by"' in SEMANTIC_PARSER_PROMPT
        assert '"sort_dir"' in SEMANTIC_PARSER_PROMPT

    def test_a_sort_only_message_is_not_idle_chat(self) -> None:
        from app.services.chatbot.turn.apply import _IDLE_CHAT_DISQUALIFIERS

        assert "sort_by" in _IDLE_CHAT_DISQUALIFIERS
