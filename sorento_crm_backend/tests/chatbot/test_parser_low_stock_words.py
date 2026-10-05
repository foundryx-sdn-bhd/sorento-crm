"""S7 RED tests - the parser knows the low stock words (#893).

`documentation/plans/scm/PLAN-low-stock-report.md` section S7;
`low-stock-report-acceptance-criteria.md` AC-70, AC-71.

`LOW_STOCK_ADDENDUM` is a new constant in `app/services/chatbot_parser_prompt.py`,
APPENDED to both published bodies the way `GROWTH_R1_ADDENDUM` and `LAST_COST_ADDENDUM`
already are - never woven in. The FULL body has to stay a mechanical derivation of the
live n8n system message (`test_parser_prompt_is_live.py`'s whole subject), and a trailing
block is the only edit shape that keeps that true.

**No LLM is called here, by design.** The repo's offline mechanism for this is the corpus
JSON beside `parser_growth_r1_phrases.json`: each row names a phrasing, the CUE the prompt
teaches it by, and the parse keys it must produce, and the test grades the CONTRACT between
the prompt, the declared vocabulary and the code. Whether the model obeys is the console
case's job (`tests/chatbot/console_cases/2026-09-14-low-stock-report.yaml`), not pytest's.
There is no offline golden that produces a real parse without a provider call, so none is
written - see the file's own note.

**One coupling the coder owes, which no test here can express:** appending a new addendum
breaks `test_parser_prompt_is_live.py::_without_growth_r1_addendum` and
`test_parser_growth_r1_reachability.py::test_the_addendum_is_appended_to_both_bodies`,
both of which strip a fixed chain of suffixes before asserting. `LAST_COST_ADDENDUM` had
to be added to that chain when it landed; `LOW_STOCK_ADDENDUM` has to be added the same
way, in the same commit. `test_the_addendum_stacks_after_last_cost` below pins the ORDER
those strips assume.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import contracts

PHRASES_FILE = Path(__file__).parent / "fixtures" / "parser_low_stock_phrases.json"

INTENT = "low_stock_report"
TRIGGER_PHRASES = ("low stock report", "low stock", "reorder report", "stock below level")


def _phrases() -> list[dict[str, Any]]:
    return json.loads(PHRASES_FILE.read_text(encoding="utf-8"))["phrases"]


def _prompt():
    """The three names this slice adds or reads. Imported inside the helper so a missing
    `LOW_STOCK_ADDENDUM` is one red test per assertion rather than a collection error that
    takes the whole file down."""
    import app.services.chatbot_parser_prompt as mod

    addendum = getattr(mod, "LOW_STOCK_ADDENDUM", None)
    assert addendum is not None, (
        "app.services.chatbot_parser_prompt.LOW_STOCK_ADDENDUM does not exist yet (AC-70)"
    )
    return mod, addendum


def _bodies():
    """AC-1592/D8 port (like `test_parser_warehouse_arrival_cue.py`'s SLIM retirement):
    `SEMANTIC_PARSER_PROMPT_SLIM` is retired outright ("one prompt lineage (v3 shape),
    v1 and SLIM retired") - there is no second body left to assert on, so every test
    below that loops `for name, body in _bodies().items()` naturally becomes a
    single-body (FULL-only) check without needing its own rewrite."""
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT

    return {"FULL": SEMANTIC_PARSER_PROMPT}


# --------------------------------------------------------------------------- #
# AC-70 - the addendum is published on both bodies
# --------------------------------------------------------------------------- #


class TestBothPublishedBodiesCarryTheVocabulary:
    def test_the_addendum_is_appended_to_both_bodies(self) -> None:
        """One body ships now (D8, AC-1594: SLIM retired) - `_bodies()` carries only
        `FULL`, so this loop is a single-body check; kept generic (not rewritten as a
        bare assertion) so a future second lineage, if one is ever added, is covered
        for free.

        `SALES_REPORT_ADDENDUM` (PLAN-chatbot-sales-report.md S4) stacked AFTER this one,
        newest outermost, so it is stripped first before `LOW_STOCK_ADDENDUM` is asserted
        as the tail - the same treatment `LAST_COST_ADDENDUM` got here when
        `LOW_STOCK_ADDENDUM` landed. `STOCK_TASK_ADDENDUM` and `SALES_ANALYSIS_ADDENDUM`
        stacked after THAT, then `QUANTITY_ADDENDUM` (issue #1262 slice 5, 26 Sep 2026),
        then `KNOWN_BRANDS_ADDENDUM` (issue #1262 slice 9, 26 Sep 2026), then
        `ESCALATION_CONFIRMATION_ADDENDUM` (#1323), then `MEMORY_ADDENDUM` (chatbot memory
        lane A, migration `mem_0002_parser_memory`) after that, so it comes off first of
        all."""
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            ESCALATION_CONFIRMATION_ADDENDUM,
            SELF_REFERENCE_ADDENDUM,
            KNOWN_BRANDS_ADDENDUM,
            MEMORY_ADDENDUM,
            LOW_STOCK_FILTERS_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            QUANTITY_ADDENDUM,
            SALES_ANALYSIS_ADDENDUM,
            SALES_REPORT_ADDENDUM,
            SPECIFICATION_ADDENDUM,
            STOCK_TASK_ADDENDUM,
            TOP_SELLING_ADDENDUM,
        )


        # `TOP_SELLING_ADDENDUM` (PLAN-chatbot-top-x-hot-selling-24sep.md S4) is newer
        # still, peeled right after ESCALATION_CONFIRMATION_ADDENDUM, the tail.
        _mod, addendum = _prompt()
        for name, body in _bodies().items():
            assert (
                body.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM)
                .removesuffix(ESCALATION_CONFIRMATION_ADDENDUM).removesuffix(SELF_REFERENCE_ADDENDUM)
                .removesuffix(TOP_SELLING_ADDENDUM)
                .removesuffix(KNOWN_BRANDS_ADDENDUM)
                .removesuffix(QUANTITY_ADDENDUM)
                .removesuffix(SPECIFICATION_ADDENDUM)
                .removesuffix(SALES_ANALYSIS_ADDENDUM)
                .removesuffix(STOCK_TASK_ADDENDUM)
                .removesuffix(SALES_REPORT_ADDENDUM)
                .endswith(addendum)
            ), (
                f"{name} body does not end with LOW_STOCK_ADDENDUM once the newer "
                "MEMORY_ADDENDUM/KNOWN_BRANDS_ADDENDUM/QUANTITY_ADDENDUM/SALES_ANALYSIS_ADDENDUM/"
                "STOCK_TASK_ADDENDUM/SALES_REPORT_ADDENDUM are stripped - "
                "LOW_STOCK_ADDENDUM must stay the tail beneath them"
            )

    def test_the_addendum_stacks_after_last_cost(self) -> None:
        """The ORDER the existing pins' strip chains assume. `test_parser_prompt_is_live`
        and `test_parser_growth_r1_reachability` both peel suffixes in a fixed sequence to
        get back to the live-derived body; a new addendum inserted anywhere but the end
        makes both of them wrong in a way whose failure message points at the wrong
        constant."""
        from app.services.chatbot_parser_prompt import (
            ACCOUNT_LEDGER_ADDENDUM,
            KNOWN_BRANDS_ADDENDUM,
            LAST_COST_ADDENDUM,
            ESCALATION_CONFIRMATION_ADDENDUM,
            MEMORY_ADDENDUM,
            LOW_STOCK_FILTERS_ADDENDUM,
            PO_SPO_WAREHOUSE_ADDENDUM,
            SELF_REFERENCE_ADDENDUM,
            QUANTITY_ADDENDUM,
            SALES_ANALYSIS_ADDENDUM,
            SALES_REPORT_ADDENDUM,
            SPECIFICATION_ADDENDUM,
            STOCK_TASK_ADDENDUM,
            TOP_SELLING_ADDENDUM,
        )

        _mod, addendum = _prompt()
        for name, body in _bodies().items():
            assert (
                body.removesuffix(MEMORY_ADDENDUM).removesuffix(LOW_STOCK_FILTERS_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM)
                .removesuffix(ESCALATION_CONFIRMATION_ADDENDUM).removesuffix(SELF_REFERENCE_ADDENDUM)
                .removesuffix(TOP_SELLING_ADDENDUM)
                .removesuffix(KNOWN_BRANDS_ADDENDUM)
                .removesuffix(QUANTITY_ADDENDUM)
                .removesuffix(SPECIFICATION_ADDENDUM)
                .removesuffix(SALES_ANALYSIS_ADDENDUM)
                .removesuffix(STOCK_TASK_ADDENDUM)
                .removesuffix(SALES_REPORT_ADDENDUM)
                .removesuffix(addendum)
                .endswith(LAST_COST_ADDENDUM)
            ), f"{name}: LOW_STOCK_ADDENDUM must stack AFTER LAST_COST_ADDENDUM"

    @pytest.mark.parametrize("phrase", TRIGGER_PHRASES)
    def test_both_bodies_teach_each_trigger_phrase(self, phrase: str) -> None:
        """AC-70's four trigger phrases, verbatim. A phrase the prompt does not name is a
        phrase the model has no reason to map to this intent - and the fallback is
        `check_stock`, which answers a stock balance question instead of running a plan."""
        for name, body in _bodies().items():
            assert phrase in body, f"{name} body does not teach {phrase!r}"

    def test_both_bodies_name_the_intent(self) -> None:
        for name, body in _bodies().items():
            assert INTENT in body, f"{name} body never names the {INTENT} intent"

    def test_the_addendum_states_the_scoping_rules(self) -> None:
        """AC-70: three sentences the model cannot infer. A location word is a WAREHOUSE
        entity (not a product, which is what a bare token defaults to under the inventory
        domain's `bare_entity_type`), a product code is a PRODUCT entity, and a date
        phrase fills the two horizon keys rather than being ignored."""
        _mod, addendum = _prompt()
        lowered = addendum.lower()
        assert "warehouse" in lowered, (
            "the addendum must say a location word is a warehouse entity"
        )
        assert "product" in lowered, (
            "the addendum must say a product code is a product entity"
        )
        assert "date_filter_start" in addendum and "date_filter_end" in addendum, (
            "the addendum must name both horizon keys a date phrase fills"
        )


# --------------------------------------------------------------------------- #
# AC-70 - the declared vocabulary agrees with the prompt
# --------------------------------------------------------------------------- #


class TestDeclaredVocabulary:
    def test_intent_hints_declares_low_stock_report(self) -> None:
        """`contracts.INTENT_HINTS` is derived from `DOMAIN_SPEC[...].intents` and is what
        `IntentHint` (the strict provider schema's Literal) is built from - a value the
        prompt teaches but the schema does not declare is rejected by the provider before
        the lane ever sees it."""
        assert INTENT in contracts.INTENT_HINTS, (
            f"INTENT_HINTS must carry {INTENT!r}: {contracts.INTENT_HINTS}"
        )

    @pytest.mark.parametrize("sample", _phrases(), ids=lambda s: s["phrase"])
    def test_every_sample_cue_is_taught_by_the_prompt(self, sample: dict) -> None:
        """The cue is quoted in the addendum, so both published bodies teach it."""
        _mod, addendum = _prompt()
        assert f'"{sample["cue"]}"' in addendum, (
            f"{sample['phrase']!r} is a corpus sample whose cue {sample['cue']!r} the "
            "addendum does not quote - the model has no way to produce the expected parse"
        )

    @pytest.mark.parametrize("sample", _phrases(), ids=lambda s: s["phrase"])
    def test_every_sample_expects_declared_vocabulary(self, sample: dict) -> None:
        expect = sample["expect"]
        assert expect["domain_hint"] in contracts.DOMAIN_HINTS
        assert expect["intent_hint"] in contracts.INTENT_HINTS
        for hint in expect.get("entity_hints") or []:
            assert hint in ("warehouse", "product"), hint
        for key in expect.get("date_keys") or []:
            assert key in ("date_filter_start", "date_filter_end"), key

    def test_the_samples_cover_the_journey_and_every_trigger_phrase(self) -> None:
        """The corpus must actually exercise what AC-70 lists, or it is four rows that
        happen to agree with each other."""
        samples = _phrases()
        cues = {s["cue"] for s in samples}
        assert cues >= set(TRIGGER_PHRASES), (
            f"the corpus does not cover every trigger phrase: missing "
            f"{set(TRIGGER_PHRASES) - cues}"
        )
        hints = {h for s in samples for h in (s["expect"].get("entity_hints") or [])}
        assert hints == {"warehouse", "product"}, (
            f"the corpus must cover both scoping entity types: {hints}"
        )
        assert any(s["expect"].get("date_keys") for s in samples), (
            "the corpus must cover the date-phrase horizon"
        )
        assert all(s["expect"]["intent_hint"] == INTENT for s in samples)
