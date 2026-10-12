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
    TRAP_SPECS_FALLBACK_BLOCK,
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
# Sixth re-pin, 4 Oct 2026 (LOWSTOCK-SEMANTIC, owner: "remove the rules entirely"):
# LOW_STOCK_FILTERS_ADDENDUM (the `low_stock` key that replaces the lane's hard-coded
# reading rules, 1,380 est. tokens) sits between ACCOUNT_LEDGER and MEMORY and takes the
# prompt without MEMORY_ADDENDUM to 44,883 est. tokens and the whole prompt to 45,399
# (crew design review round: "supplier_category" only when BOTH are named);
# CEILING is 45,399 - 512 = 44,887 so both assertions hold.
# Seventh re-pin, 4 Oct 2026 (PROMPT-DYNAMIC merged over LOWSTOCK-SEMANTIC, PLAN-prompt-dynamic-30sep
# D9): main 690d41fc measures 44,883 without MEMORY_ADDENDUM; the owner's `sales` domain row adds one
# domain line to the policy blocks (+88): 44,971 without MEMORY_ADDENDUM and 45,487 with it;
# CEILING is 45,487 - 512 = 44,975 so both assertions hold. Nothing else grew.
# Eighth re-pin, 6 Oct 2026 (RESOLVE-SEMANTIC-FALLBACK, PR #1511, owner ruling card Q1 = a):
# CANONICAL_CODE_ADDENDUM (a type / colour / brand word beside a code is ONE product entity
# whose canonical_code is the bare code as typed; 786 est. tokens) sits between
# LOW_STOCK_FILTERS and MEMORY and takes the prompt without MEMORY_ADDENDUM to 45,757 est.
# tokens and the whole prompt to 46,273; CEILING is 46,273 - 512 = 45,761 so both
# assertions hold. A genuine new addendum the owner ruled must land, per the rule above.
# Sixth re-pin, 2 Oct 2026 (REPORT-ENGINE slice 1b, PLAN-report-engine.md section 11):
# REPORT_ASK_ADDENDUM (the `sales_ranking` vocabulary, 505 est. tokens on its own) sits between
# ACCOUNT_LEDGER and MEMORY and takes the prompt without MEMORY_ADDENDUM to 44,005 est. tokens
# and the whole prompt to 44,521; CEILING is 44,521 - 512 = 44,009 so both assertions hold.
# 1b code review S1 (same lane): the addendum's "not a sales ranking" carve-out takes it to 585
# est. tokens, the prompt without MEMORY_ADDENDUM to 44,086 and the whole prompt to 44,601 (+80);
# CEILING is 44,601 - 512 = 44,089.
# Seventh re-pin, 3 Oct 2026: restoring "SA" and "(dealer / project)" in REPORT_ASK_ADDENDUM
# (review round 2) takes the whole prompt to 44,603 (+2); CEILING is 44,603 - 512 = 44,091.
# Eighth re-pin, 4 Oct 2026 (owner rule: SEMANTIC ONLY, the parser decides, code reads no text):
# REPORT_ASK_ADDENDUM now teaches ranking_refine (a follow-up that only changes the count,
# period, basis or measure of the ranking on screen), the measure key, the new-ask rule and the
# product-ranking carve-outs, in place of the removed regex rules, and the live refine fix; prompt without
# MEMORY_ADDENDUM 44,848 est. tokens and the whole prompt 45,364 (+761, with the live refine fix, the
# how-many answer bullet, the people-ranking-is-a-new-ask rule and the singular no-count rule);
# CEILING is 45,364 - 512 = 44,852.
# PR #1447 (report engine): REPORT_ASK_ADDENDUM teaches the outstanding basis, +27 est. tokens.
# PR #1447 (rankings clarify): the basis answer line, the delivered answer and the outstanding refine, +114 est. tokens (review B1 removed the sales ranking metric bullet, -63).
# Ranking keys come from the current message only (REPORT_ASK_ADDENDUM), +52 est. tokens.
# Merge of main (#1470 LOWSTOCK-SEMANTIC) with PR #1447 (report engine): merged prompt measures 46,424 est. tokens without MEMORY_ADDENDUM and 46,939 with it, so CEILING is 46,939 - 512.
# Merge of main (#1405 PROMPT-DYNAMIC) with PR #1447 (report engine): the owner's `sales` domain line (+88) on top of the #1470 merge: 46,512 est. tokens without MEMORY_ADDENDUM and 47,028 with it, so CEILING is 47,028 - 512.
# Eighth re-pin, 6 Oct 2026 (STOCK-QTY-PARTIAL-FILL, PR #1513, owner: "this is 80% of our
# problem"): TRAP_SPECS_FALLBACK_BLOCK (the trap-specs and list-fragment addenda, the same
# text as the committed `chatbot_semantic_parser.trap-specs.patch` crew sequences onto the
# live version; the code fallback carries it so a fresh install's v1 teaches the rules) sits
# between LOW_STOCK_FILTERS_ADDENDUM and MEMORY_ADDENDUM: 46,595 without MEMORY_ADDENDUM
# (+1,624) and 47,110 with it; CEILING is 47,110 - 512 = 46,598 so both assertions hold.
# Since PROMPT-DYNAMIC (#1405) the live parser runs the owner's published text, not this
# constant, so production grows by the two addenda alone. Nothing else grew.
# Tenth re-pin, 7 Oct 2026 (STOCK-QTY-PARTIAL-FILL R2, same PR): the list-fragment addendum
# gains the bare-number rule (crew's real-parser runs on v60 and v63 read "10" over seven owed
# lines as a fill on line 1; Q3 (a) is one number for every line still owed): 46,996 without
# MEMORY_ADDENDUM (+401) and 47,511 with it; CEILING is 47,511 - 512 = 46,999 so both
# assertions hold. Nothing else grew.
# Eleventh re-pin, 7 Oct 2026 (RESOLVE-SEMANTIC-FALLBACK merged over STOCK-QTY-PARTIAL-FILL R2):
# both addenda in the constant (CANONICAL_CODE_ADDENDUM after LOW_STOCK_FILTERS, then
# TRAP_SPECS_FALLBACK_BLOCK, MEMORY the tail) measure 47,782 without MEMORY_ADDENDUM (+786,
# the canonical-code addendum alone) and 48,297 with it; CEILING is 48,297 - 512 = 47,785 so
# both assertions hold. Nothing else grew.
# Twelfth re-pin, 8 Oct 2026 (SO-PO-REF merged over RESOLVE-SEMANTIC-FALLBACK): PO_REFS_ADDENDUM
# (the `po_refs` key, a customer's own PO number) now sits between LOW_STOCK_FILTERS_ADDENDUM
# and CANONICAL_CODE_ADDENDUM: 48,069 without MEMORY_ADDENDUM (+287 over the eleventh pin) and
# 48,585 with it; CEILING is 48,585 - 512 = 48,073 so both assertions hold. Nothing else grew.
# Merge of main (#1513 STOCK-QTY-PARTIAL-FILL, TRAP_SPECS_FALLBACK_BLOCK +2,025) with PR #1447 (report engine):
# 48,536 est. tokens without MEMORY_ADDENDUM and 49,052 with it, so CEILING is 49,052 - 512. Nothing else grew.
# REPORT-ENGINE AC-HT8 (PR #1447): the dealer / customer / salesman ranked-noun examples, the
# sales_ranking domain_in_message rule and the ranking words in DOMAIN IN MESSAGE:
# 48,758 est. tokens without MEMORY_ADDENDUM (+222), so CEILING is 48,762 (the same +4 margin as before).
# AC-HT8 r2 (bare `top` dropped from the list, the repaired salesman line and P2 line): 49,275
# with MEMORY_ADDENDUM, so CEILING is 49,275 - 512.
# AC-HT8 r3 (business model, any-language ranked noun, channel refine, Chinese category words):
# 49,069 est. tokens without MEMORY_ADDENDUM, so CEILING is 49,073 (the same +4 margin).
# Merge of main (#1511 RESOLVE-SEMANTIC-FALLBACK, CANONICAL_CODE_ADDENDUM) with PR #1447 (report engine):
# 49,855 est. tokens without MEMORY_ADDENDUM, so CEILING is 49,859 (the same +4 margin).
# AC-HT8 r4 (category canonical_code rule moves to the canonical-code block, channel null rules,
# refine closing line): 49,972 est. tokens without MEMORY_ADDENDUM, so CEILING is 49,976 (+4).
# AC-HT8 r5 (category class word scoped to ranking asks, the synonym pairs): 50,053 est. tokens
# without MEMORY_ADDENDUM, so CEILING is 50,057 (+4).
# AC-HT8 r6 (Chinese basis words in the ranking and top selling addenda): 50,195 est. tokens
# without MEMORY_ADDENDUM, so CEILING is 50,199 (+4).
# AC-HT8 r6b (the outstanding basis clause leaves the top selling addendum): 50,181 est. tokens
# without MEMORY_ADDENDUM, so CEILING is 50,185 (+4).
# Merge of main (#1528 SO-PO-REF, PO_REFS_ADDENDUM) with PR #1447 (report engine): 50,469 est. tokens
# without MEMORY_ADDENDUM, so CEILING is 50,473 (+4).
# REPORT-DRILLDOWN (PR #1531, card rev 3, owner 8 Oct 2026): the `ranking_drill` reading in
# REPORT_ASK_ADDENDUM (scoped / global / unsure, the reply to the unsure question): 50,759 est.
# tokens without MEMORY_ADDENDUM (+290), so CEILING is 50,763 (+4). Nothing else grew.
# Review round 1 (same PR): products and categories reach the drill (the top_selling exception)
# and the unsure reply's count exception: 50,863 (+100), so CEILING is 50,867 (+4).
# Crew live-pass round (same PR): a different ranked noun is never a refine, "global" only when
# said, doubt is "unsure": 51,040 (+177), so CEILING is 51,044 (+4).
# Merge of main (#1531 REPORT-DRILLDOWN) with PROMO-SUBJECT-FILTER (PROMOTION_KIND_ADDENDUM with its
# PROMOTION SUBJECT section, after CANONICAL_CODE_ADDENDUM): 51,321 est. tokens without MEMORY_ADDENDUM,
# so CEILING is 51,325 (+4).
# PROMO-SUBJECT-FILTER final nits: PROMOTION KIND says a bare number or a numbered-list reply is
# never a kind: 51,363 est. tokens without MEMORY_ADDENDUM, so CEILING is 51,367 (+4).
# PROMO-SUBJECT-FILTER round 3 (AC-29): PROMOTION KIND and PROMOTION SUBJECT answer from the
# Known brands / Product types / Promotion kinds lines, typo to the listed name, unplaced word
# unresolved: 51,454 est. tokens without MEMORY_ADDENDUM (+91), so CEILING is 51,458 (+4).
# PROMO-SUBJECT-FILTER round 3b (AC-36): PROMOTION SUBJECT emits a listed qualifier as a
# specification entity from the Product details line: 51,600 est. tokens without
# MEMORY_ADDENDUM (+142), so CEILING is 51,604 (+4).
# re-pin 10 Oct 2026 (CA-3-CHAT-DATASET, PR #1553, design approved 9 Oct): CHAT_REPORT_ADDENDUM
# (502 est. tokens, trimmed from 1,404) plus the chat_analytics line of the policy blocks seed:
# 52,149 est. tokens without MEMORY_ADDENDUM (+545), so CEILING is 52,153 (+4).
# CA-3 review round 5 (10 Oct 2026): CHAT_REPORT_ADDENDUM generalised (no golden wording), 690 est. tokens:
# 52,329 est. tokens without MEMORY_ADDENDUM, so CEILING is 52,333 (+4).
# re-pin 10 Oct 2026 (CA-3-CHAT-DATASET, PR #1553, extractor ruling): CHAT_REPORT_ADDENDUM shrinks to
# routing only (240 est. tokens, the focused extractor reads the fields): 51,879 est. tokens without
# MEMORY_ADDENDUM (-450), so CEILING is 51,883 (+4).
# MULTI-ASK-PLAN S2 (PR #1542, owner card 9 Oct 2026): SUB_ASKS_ADDENDUM (the `sub_asks` and
# `family` keys, one entry per thing a message asks) between PO_REFS_ADDENDUM and
# CANONICAL_CODE_ADDENDUM: 51,809 est. tokens without MEMORY_ADDENDUM (+769), so CEILING is
# 51,813 (+4). Nothing else grew.
# Crew live parser checks (same PR, 9 Oct 2026): received vs incoming, no period on a "last"
# ask, `other` still an ask, and replies to the two questions read as picks: 52,119 est.
# tokens without MEMORY_ADDENDUM (+310), so CEILING is 52,123 (+4). Nothing else grew.
# Crew live checks round 2 (same PR): the date AND quantity of one event is one ask
# ("last in date and quantity"): 52,165 est. tokens without MEMORY_ADDENDUM (+46), so
# CEILING is 52,169 (+4). Nothing else grew.
# R-LASTIN (owner ruling 9 Oct 2026, same PR): the last_receipt measure is the last SPO
# line's delivery date, a lone "last in date and quantity" example, and a plain "sold" is
# null whatever an earlier turn said: 52,226 est. tokens without MEMORY_ADDENDUM (+57), so
# CEILING is 52,230 (+4). Nothing else grew.
# Round 2a (same PR): incoming is only not-yet-arrived goods, last_receipt is one ask that
# carries its own date and quantity, a zh example: 52,288 est. tokens without MEMORY_ADDENDUM
# (+62), so CEILING is 52,292 (+4). Nothing else grew.
# MULTI-ASK-PLAN r6 (owner hand test FAIL 9 Oct 2026): varied Examples (zh / en / ms, code before
# or after, 2 to 6 asks), the SRTWT7445 six-ask example removed, the received_qty words widened,
# 2+ things listed as sub_asks whatever they are: 52,379 est. tokens without MEMORY_ADDENDUM
# (+91), so CEILING is 52,383 (+4). Nothing else grew.
# Merge of main (#1527 PROMO-SUBJECT-FILTER, PROMOTION_KIND_ADDENDUM) with MULTI-ASK-PLAN r6:
# 52,939 est. tokens without MEMORY_ADDENDUM, so CEILING is 52,943 (+4).
# MULTI-ASK-PLAN r7 (live evidence, period and basis only from the message, 进仓库 never incoming, jual is sold,
# a bare name asks what is wanted): 53,139 est. tokens without MEMORY_ADDENDUM (+200), so CEILING is 53,143 (+4).
# Merge of main (#1553 CA-3, CHAT_REPORT_ADDENDUM) with MULTI-ASK-PLAN r7: 53,417 est. tokens without
# MEMORY_ADDENDUM, so CEILING is 53,421 (+4).
# REF-PROBES (11 Oct 2026): REF_KINDS_ADDENDUM (the reference-number kind hints, after
# CHAT_REPORT_ADDENDUM): 52,264 est. tokens without MEMORY_ADDENDUM (+381), so CEILING is 52,268 (+4).
# RELEASE-FIX (12 Oct 2026, PR #1598): the five REF-PROBES reference kinds get their
# `chatbot_entity_kinds` rows (AC-1502), so the policy blocks seed carries five more
# "Entity kind" lines: 52,437 est. tokens without MEMORY_ADDENDUM (+169), so CEILING is
# 52,441 (+4). Nothing else grew.
# Merge of main (#1569 REF-PROBES) with MULTI-ASK-PLAN2 (SUB_ASKS_ADDENDUM trimmed to 1,716 chars,
# its measure list rendered from data): 52,883 est. tokens without MEMORY_ADDENDUM (+615 over
# main's 52,264), so CEILING is 52,887 (+4).
# Merge of main (#1598 RELEASE-FIX, five entity kind lines) with MULTI-ASK-PLAN2: 53,055 est. tokens
# without MEMORY_ADDENDUM (52,437 on main + SUB_ASKS_ADDENDUM), so CEILING is 53,059 (+4).
# DEV-FAILURES RC-B (12 Oct, merged over RELEASE-FIX #1598): PORTAL_FORM_ADDENDUM (the
# `portal_form` output key, 262 chars): 52,524 est. tokens without MEMORY_ADDENDUM (+83) and
# 53,040 with it, so CEILING is 52,528 (+4; the whole-prompt assertion is CEILING + 512).
# Merge of main (#1583 DEV-FAILURES, PORTAL_FORM_ADDENDUM) with MULTI-ASK-PLAN2: 53,143 est. tokens
# without MEMORY_ADDENDUM (main's 52,524 + SUB_ASKS_ADDENDUM), so CEILING is 53,147 (+4).
CEILING = 53_147
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
