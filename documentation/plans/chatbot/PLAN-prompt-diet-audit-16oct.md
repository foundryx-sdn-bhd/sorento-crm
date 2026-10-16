# PLAN: PROMPT-DIET audit of the production parser prompt (16 Oct 2026)

Status: Deliverable 1 DONE, filed for owner annotation (`PROMPT-DIET-16oct.html`); stages wait on the owner's
annotations. Track: docs only (no product code, no LLM spend).

Result: 91 sections, 35,585 tokens today, about 20,435 after the proposed five stages (-43%). Two text bugs
(v94:891-893 vs :900; missing comma v94:765), one dead section (REFERENCE TARGET), two stored-only keys
(`contains_flyer`, `correction`), policy block drifted from the DB (`has_massage`, `purchase_order`).

Lane: PROMPT-DIET (16 Oct), branch `crew/prompt-diet-16oct` off origin/main, its own draft PR. Brief:
`crew/state/briefs/PROMPT-DIET.md`. The older branch `crew/prompt-diet` (draft PR #1568, the 10-11 Oct
"prompt diet measurement record, STOPPED at DP1" spike) is INPUT only: what was measured and why it stopped.

## Why

Owner, 16 Oct, reading the production parser prompt (`chatbot_semantic_parser`, prod v63 = prod v52 + live
`{{find_filters}}`): "very very overloaded ... bloated". Section-by-section doubts: domain_in_message,
teaching rules ("if previous offered X then Y"), message_type / user_goal unused?, attachment types and
access levels hard-coded although the DB has the lists, flyer flag, never-an-entity and pronoun teaching,
broaden axis repeats, positional reference when the user answers with a code, specification block length.

## Source text

Dev `sorento_cagent_stack` v94 (`CHAT-FIND: v91 with the live {{find_filters}} block`); v91 is
`PROD v52 plain (md5 f447ad50)`. v94 differs from v91 by exactly one line (`{{find_filters}}`, v94:1847), so
v94 is the prod v63 text. Crew lanes never read prod or the prod clone, so the prod row itself is not read.
1,849 lines, 140,897 chars, 35,585 tokens (o200k_base) before `{{current_date}}` / `{{find_filters}}` render.

## Deliverable 1 (this plan)

One self-contained HTML page, `documentation/plans/chatbot/PROMPT-DIET-16oct.html`, for Lavish:

1. Every section (91, split at `== X ==` headings and banner titles): lines, tokens, what it tells the model,
   output fields it drives.
2. Code readers of each field, `file:line`, or "unused".
3. Static lists that exist in the DB (attachment types, access levels, spec registry, brands, product
   types, domains) and whether a `chatbot_prompt_vars.py` variable already renders them.
4. Teaching logic the engine already knows deterministically (could be structured context instead).
5. Duplicates / repeats, and each owner doubt answered with evidence.
6. Parser-gold TRAIN items that exercise each section (`items_TRAIN_lane.jsonl` only; never held-out).
7. Verdict per section: KEEP / SHRINK / MAKE DYNAMIC / MOVE TO CONTEXT / DELETE, with risk.
8. Tokens today vs after diet; staged plan (3-5 stages, each its own prompt version, gated by parser-gold
   TRAIN no-drop + crew held-out, cost per stage).

Method: scout subagents read the prompt, the parser schema/consumers, `chatbot_prompt_vars.py` and the TRAIN
file, each writing findings to the session scratchpad; the lead assembles and spot-checks every
`file:line`. Filed as ONE `crew report --ask` with the repo path.

## Later (only after owner annotations)

Each stage is its own PR + unlabelled prompt version; never published or labelled without owner PASS.
No overlap with CHAT-ADVISOR-SPIKE (#1720/#1730/#1731/#1737, F5 `typed_as`).

## Known constraint from 11 Oct (DP1, PR #1568)

The static parser prompt is ~96% served from the provider prompt cache, so token count is not the main
cost driver (`project_prompt_diet_dp1_11oct`). The diet's value is attention / instruction load and
latency, judged by parser-gold pass rate, cost per turn and p95, not tokens alone.
