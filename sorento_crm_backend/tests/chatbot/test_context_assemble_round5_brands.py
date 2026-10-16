"""Integration round 5 of chatbot memory lane A (PR #1304, merge of main b299bf6e):
#1301's `Known brands:` line (#1262 slice 9, F1a) reaches the parser through
`turn/context.py::assemble` at EVERY level.

Main passes `brands` into `parser.build_user_block`; this lane builds the parser block
with `context.assemble`, which knew nothing of brands, so without this the merged engine
would have dropped the brand list for every contact and a misspelt brand word
("sorneto") would have nothing to resolve against.

Pure Python, no database.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.head import parser
from app.services.chatbot.turn import context

PREVIOUS = "How many units of SRTWB1455 do you need?"
BRANDS = [
    {"brand_name": "Mocha", "brand_code": "MCH"},
    {"brand_name": "Sorento", "brand_code": "SRT"},
    # The same brand active in a second company prints once.
    {"brand_name": "Sorento", "brand_code": "SRT"},
]
BRANDS_LINE = "Known brands: Mocha (MCH), Sorento (SRT)"


def _layers(level: str, **overrides) -> context.ContextLayers:
    kwargs = dict(
        level=level,
        profile_facts=[{"key": "customer", "value": "Chin Chun Trading"}],
        summaries=["Thu 25 Sep: stock SRTWB1455 (answered)."],
        earlier_messages=[{"created_at": "Thu 10:00", "text": "hello"}],
        previous_response=PREVIOUS,
        current_subject="domain inventory; product SRTWB1455",
        pending_kind=None,
        pending_options=None,
        settings_profile_line=None,
        current_message="sorneto stock",
        reply_to=None,
        media_line=None,
        brands=BRANDS,
    )
    kwargs.update(overrides)
    return context.ContextLayers(**kwargs)


class TestOffLevelIsMainsBlock:
    def test_off_level_equals_mains_build_user_block_with_brands(self) -> None:
        recent = [("stock SRTWB1455", PREVIOUS)]
        main_block = parser.build_user_block(
            previous_response=PREVIOUS,
            latest_user_message="sorneto stock",
            pending_kind="quantity",
            pending_options=["BRW", "KCH"],
            profile_block="Profile: tier gold",
            focus=None,
            brands=BRANDS,
            recent_exchanges=recent,
        )
        text, _report = context.assemble(
            _layers(
                "off",
                current_subject=None,
                pending_kind="quantity",
                pending_options=["BRW", "KCH"],
                settings_profile_line="Profile: tier gold",
                recent_exchanges=recent,
            )
        )
        assert text == main_block, (text, main_block)
        assert BRANDS_LINE in text


class TestEveryMemoryLevelCarriesTheBrandLine:
    @pytest.mark.parametrize("level", ["conversation", "episodes", "full"])
    def test_the_line_is_in_l2_before_the_current_message(self, level: str) -> None:
        text, report = context.assemble(_layers(level))
        assert text.count(BRANDS_LINE) == 1, text
        assert text.index(BRANDS_LINE) < text.index("Current subject:") < text.index(
            "Current user message:"
        ), text
        l2 = next(row for row in report["layers"] if row["layer"] == "L2")
        assert l2["est_tokens"] >= context.est_tokens(BRANDS_LINE)

    def test_no_brands_no_line(self) -> None:
        for brands in (None, [], [{"brand_name": " ", "brand_code": "X"}]):
            text, _ = context.assemble(_layers("full", brands=brands))
            assert "Known brands" not in text, text

    def test_the_brand_line_is_kept_whole_when_the_subject_shrinks(self) -> None:
        text, report = context.assemble(
            _layers("conversation", current_subject="x" * 3000)
        )
        assert BRANDS_LINE in text
        l2 = next(row for row in report["layers"] if row["layer"] == "L2")
        assert l2["dropped"] is True
        # PR-C AC-C1: the vocabulary line is outside the L2 cap, so the layer is cap + the line.
        assert l2["est_tokens"] <= context.CAPS["L2"] + context.est_tokens(BRANDS_LINE)


def test_build_user_block_and_assemble_share_one_brand_line() -> None:
    assert context.known_brands_line(BRANDS) == BRANDS_LINE
    assert context.known_brands_line([{"brand_name": "Cabana", "brand_code": ""}]) == (
        "Known brands: Cabana"
    )
    assert context.known_brands_line(None) is None
