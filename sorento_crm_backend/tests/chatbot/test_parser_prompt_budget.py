"""S3 parser prompt token ceiling - tester-first RED, from the UAC and the lane A
contract (section 6.3 "How the budget is enforced").

Covers AC-MEM061.

**Ceiling baseline, coordinator ruling 26 Sep 2026**: `CEILING = 37_153`, not the plan's
own 22,100. The plan's number is a `chars / 4` estimate over a DIFFERENT rendering (the
live n8n text alone, before this repo's own growth addenda and before this file's own
`bytes / 3` formula); 37,153 is what THIS file's own `_rendered_production_prompt()`
measures at commit `232182ae` (`SEMANTIC_PARSER_PROMPT` + every growth addendum + the
policy-blocks seed, `est_tokens = ceil(utf8_bytes / 3)`). The rule that matters is
unchanged - **the static prompt may not grow** - so the ceiling is pinned to today's
measured value rather than to the plan's figure for a different measurement. Any
S3 cut lowers the real number without needing a matching CEILING edit; a genuine new
addendum that must land raises CEILING in the same commit, with the new measured value
named the same way this one is.

**Ambiguity flagged to the captain**: the token estimator here is a LOCAL, self-contained
`ceil(utf8_bytes / 3)` (the contract's own formula, section 6.2), not imported from
`app.services.chatbot.turn.context` - that module is S3's own deliverable and does not
exist yet, and coupling THIS file's collection to its existence would turn a real,
meaningful "the prompt is over budget" assertion into an unrelated `ImportError`. Once
`context.est_tokens` exists, the coder is free to import it here instead (same formula,
same answer) - flagged as a choice, not a defect.

Pure Python: no database, no LLM. `render_prompt_blocks`/`prompt_blocks_hash` (which DO
need a database) are not used - "the policy blocks seed" is read from the already-
committed `tests/chatbot/fixtures/prompt_blocks_seed.txt`, the same fixture
`test_rearch_s4_prompt_blocks.py` renders and compares as its own golden file.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.services.chatbot_parser_prompt import (
    BLOCKS_BEGIN,
    BLOCKS_END,
    GROWTH_R1_ADDENDUM,
    LAST_COST_ADDENDUM,
    LOW_STOCK_ADDENDUM,
    MEMORY_ADDENDUM,
    SALES_REPORT_ADDENDUM,
    SEMANTIC_PARSER_PROMPT,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
POLICY_BLOCKS_SEED_FILE = FIXTURES_DIR / "prompt_blocks_seed.txt"
# 37,153 is the coordinator's ceiling (26 Sep 2026). It was measured on a rendering that
# appended the four growth addenda a SECOND time (`SEMANTIC_PARSER_PROMPT` already carries
# them), which counted 7,272 est. tokens twice (reviewer pass at d89110c0, S3). On the
# rendering a published version really has (`chatbot_rearch_s4._body`: the constant plus
# the policy blocks, below) the figures are: base 232182ae 29,901; lane at d89110c0 29,866;
# main 11bf373e 32,231 (its own `STOCK_TASK_ADDENDUM`); this lane merged over it 32,395.
# The ceiling stays 37,153 and is now measured on the right text.
#
# Re-pinned 29 Sep 2026 (fix round 7 on PR #1304): main moved the prompt past 37,153 on its
# own. Main fd521c20 measures 40,599 on this same rendering (+10,698 since 232182ae), grown
# by PRs the owner merged since the 26 Sep ruling: #833 (specification addendum and the
# code-first rule), #1273 (top selling) and #1323 (escalation confirmation). So CEILING is
# main's own measured prompt at fd521c20, and it bounds everything EXCEPT this lane's
# MEMORY_ADDENDUM (the lane's body edits save 179 against main: 40,420 at 8371dbee).
# Same day, second re-pin: main bc75eb96 (#1353, issue #1352 "a pick never overrides the
# message's own domain": one DOMAIN IN MESSAGE rule plus the open numbered question and
# stock task edits) measures 41,163 (+564); this lane merged over it without the addendum
# measures 40,984 (still 179 under main).
# Re-pinned 29 Sep 2026 (PLAN-po-spo-warehouse-29sep M3): PO_SPO_WAREHOUSE_ADDENDUM (the SPO
# routing rule, the warehouse cue under PO/SPO, the sort_by/sort_dir vocabulary) and the
# "SPO" -> spo_allocation in-body edit take the prompt without MEMORY_ADDENDUM to 42,356 est.
# tokens and the whole prompt to the 42,872 this test prints; CEILING is 42,872 - 512 = 42,360
# so both assertions hold (+1,197 over 41,163).
# Review round (same PR, reviewer items 3 and 4): the DOCUMENT section's second in-body edit
# ("shipment"/"container" name no paper) and the "last in names no sort" line take the
# prompt without MEMORY_ADDENDUM to 42,424 and the whole prompt to 42,939; CEILING is
# 42,939 - 512 = 42,427 so both assertions hold.
# Third re-pin, 29 Sep 2026 (PR #1365, CHATBOT-CUSTOMER-SCOPE, PLAN-chatbot-customer-scope-
# 29sep D2): `SELF_REFERENCE_ADDENDUM` teaches the boolean `self_reference` ("my" / "me" /
# "our" as the asker's own account, Malay and Chinese forms included) and is a genuine new
# addendum the owner ruled must land (grill Q5), so per the rule above CEILING rises in the
# same commit to the new measured value: 41,466 without the memory addendum, and 41,981
# with it (the estimator rounds per text, so the two do not add up exactly); the pin is
# the combined figure minus the addendum's own 512 bound, so both assertions below hold
# on the measured prompt. The memory lane's rendered body measures 41,390 on the same text.
# Fourth re-pin, 29 Sep 2026 (PR #1365 merged over #1373): both addenda in the constant
# (SELF_REFERENCE beneath ESCALATION_CONFIRMATION, PO_SPO_WAREHOUSE beneath that, MEMORY the
# tail) measure 42,906 without the memory addendum and 43,421 with it; CEILING is
# 43,421 - 512 = 42,909 so both assertions hold on the combined prompt.
# Fifth re-pin, 2 Oct 2026 (ACCOUNT-LEDGER): ACCOUNT_LEDGER_ADDENDUM (the `account` entity key,
# owner-approved verbatim text) sits between PO_SPO_WAREHOUSE and MEMORY and takes the prompt
# without MEMORY_ADDENDUM to 43,425 est. tokens and the whole prompt to 44,016; CEILING is
# 44,016 - 512 = 43,504 so both assertions hold.
CEILING = 43_504
# The memory addendum on its own, bounded separately so this PR's growth stays bounded.
# 26 Sep baseline (lane d89110c0): 339 est. tokens. Round 4 (baf4c813, 28 Sep: the history
# question in any wording, the number re-run, commercial_request) took it to 512, which is
# pinned here; returning to 339 needs a prompt cut, which is the owner's open decision on
# #1275. With the addendum in, the published prompt is 41,499 over main bc75eb96
# (limit 41,675).
MEMORY_ADDENDUM_CEILING = 512


def _est_tokens(text: str) -> int:
    """`ceil(utf8_bytes / 3)` - contract section 6.2, verbatim. See module docstring
    for why this is a local copy rather than an import from `turn/context.py`."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def _rendered_production_prompt(
    *, current_date: str = "Thursday, 25 September 2026", prompt: str = SEMANTIC_PARSER_PROMPT
) -> str:
    """Exactly the shape `chatbot_rearch_s4._body` publishes (and `mem_0002_parser_memory`
    reuses): the constant, which already carries every addendum, then the policy blocks
    between their markers, with `{{current_date}}` substituted as `parser.resolve_config`
    does. The committed seed fixture stands in for a live `chatbot_domains` render,
    exactly as `test_rearch_s4_prompt_blocks.py`'s own golden file does."""
    policy_blocks = POLICY_BLOCKS_SEED_FILE.read_text(encoding="utf-8")
    body = f"{prompt.rstrip()}\n\n{BLOCKS_BEGIN}\n{policy_blocks}{BLOCKS_END}\n"
    return body.replace("{{current_date}}", current_date)


class TestPromptUnderCeiling:
    def test_production_prompt_is_at_most_the_measured_ceiling(self) -> None:
        assert SEMANTIC_PARSER_PROMPT.endswith(MEMORY_ADDENDUM)
        without_memory = _rendered_production_prompt(
            prompt=SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM)
        )
        tokens = _est_tokens(without_memory)
        assert tokens <= CEILING, (
            f"the production parser prompt without MEMORY_ADDENDUM is {tokens} est. "
            f"tokens, over the {CEILING} ceiling (contract section 6.3 / AC-MEM061: main "
            f"bc75eb96's own measured prompt, re-pinned 29 Sep 2026 after #833, #1273, "
            f"#1323 and #1353 grew it past the 26 Sep 2026 coordinator figure of 37,153) - "
            f"the static prompt may not grow"
        )
        total = _est_tokens(_rendered_production_prompt())
        assert total <= CEILING + MEMORY_ADDENDUM_CEILING, (
            f"the production parser prompt is {total} est. tokens, over "
            f"{CEILING} + {MEMORY_ADDENDUM_CEILING} (main bc75eb96 plus the memory addendum)"
        )

    def test_memory_addendum_growth_is_bounded(self) -> None:
        tokens = _est_tokens(MEMORY_ADDENDUM)
        assert tokens <= MEMORY_ADDENDUM_CEILING, (
            f"MEMORY_ADDENDUM is {tokens} est. tokens, over its {MEMORY_ADDENDUM_CEILING} "
            f"bound (339 at the 26 Sep 2026 baseline d89110c0, 512 after round 4 baf4c813) "
            f"- this PR's own prompt growth may not grow further"
        )

    def test_each_addendum_is_counted_once(self) -> None:
        """The rendering is the published shape: every addendum appears once (reviewer
        pass at d89110c0, S3: they used to be appended a second time)."""
        rendered = _rendered_production_prompt()
        for name, addendum in (
            ("GROWTH_R1_ADDENDUM", GROWTH_R1_ADDENDUM),
            ("LAST_COST_ADDENDUM", LAST_COST_ADDENDUM),
            ("LOW_STOCK_ADDENDUM", LOW_STOCK_ADDENDUM),
            ("SALES_REPORT_ADDENDUM", SALES_REPORT_ADDENDUM),
        ):
            assert rendered.count(addendum.strip()) == 1, f"{name} counted more than once"
        assert rendered.count(BLOCKS_BEGIN) == 1

    def test_kill_test_appending_extra_text_is_reported_over(self) -> None:
        """The SAME check function, fed a prompt padded with extra chars, must report
        it over - proving the check function itself catches an overshoot rather than
        always passing (or always failing). The padding is exactly one est. token past
        CEILING."""
        rendered = _rendered_production_prompt()
        rendered += "z" * (3 * (CEILING - _est_tokens(rendered)) + 3)
        tokens = _est_tokens(rendered)
        assert tokens > CEILING, (
            f"padding by 3000 chars must push the estimate over {CEILING}, got {tokens} "
            f"- the check function is not sensitive to prompt growth"
        )


class TestDateAtTheEnd:
    """AC-MEM071: `CURRENT DATE: {{current_date}}` is the LAST section, so the first
    20,000 chars are byte-identical across two different dates.

    Genuinely red today: grepping the live constant shows the `CURRENT DATE` section
    sitting near the START of the body (right after the opening paragraph), not the
    end - swapping in two dates of DIFFERENT lengths shifts every byte after it, so the
    first 20,000 chars differ today. This is the position AC-MEM071 asks S3 to move.
    """

    def test_current_date_section_is_the_last_section_of_the_body(self) -> None:
        marker = "CURRENT DATE"
        last_marker_at = SEMANTIC_PARSER_PROMPT.rfind(marker)
        assert last_marker_at != -1, "CURRENT DATE section not found at all"
        tail_after = SEMANTIC_PARSER_PROMPT[last_marker_at:]
        # "Last section" - nothing of substance after the current-date sentence itself
        # (allow trailing whitespace only).
        assert len(tail_after) < 400, (
            f"CURRENT DATE must be the LAST section of the system prompt; found "
            f"{len(SEMANTIC_PARSER_PROMPT) - last_marker_at} chars after it"
        )

    def test_first_20000_chars_are_byte_identical_across_two_dates(self) -> None:
        short_date = "Mon, 5 May 2025"
        long_date = "Wednesday, 25 November 2026"
        rendered_a = _rendered_production_prompt(current_date=short_date)
        rendered_b = _rendered_production_prompt(current_date=long_date)
        assert rendered_a[:20000] == rendered_b[:20000], (
            "the first 20,000 chars must be byte-identical regardless of the current "
            "date - only true once CURRENT DATE sits at the END of a prompt whose "
            "static prefix is itself at least 20,000 chars"
        )
