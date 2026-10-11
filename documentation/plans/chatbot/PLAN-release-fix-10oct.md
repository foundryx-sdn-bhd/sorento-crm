# PLAN: six full-suite failures blocking the 10 Oct release (RELEASE-FIX-10OCT)

Status: PR open (small fix track: under 300 lines; one migration, owner-directed by the
lane brief; no auth/RBAC change, no new ingest surface).

Scheduled full-suite run 38003659524 on c8fbc4db1 had six backend failures. Each one was
reproduced red on main e4c487cd5 before its fix, and bisected where the cause was not obvious
from the failure text.

| # | Test | Introduced by | Cause | Fix |
|---|------|---------------|-------|-----|
| 1 | `test_oi_email_print.py::test_migration_is_the_single_head` | #1543 | Stale: it hardcoded `oihr_0005_print_one_page` as THE head; later migrations stack on it. | Assert one head with the revision on its ancestry (the pattern `test_autocount_compare_mapping.py` uses). |
| 2 | `test_schema_uuid_id_principle` | #1551 | `chatbot.chat_topics` was keyed on `code`, no uuid `id`, no exemption. A new table, so not a LEGACY candidate. | `chat_topics_0003_uuid_id` (onto `ppl_0001_parser_candidate`): the `298_market_segments_uuid_id` treatment. Surrogate uuid PK, `code` UNIQUE, both FKs (`parent_code`, `chat_label_audits.corrected_topic`) re-pointed at it. Idempotent, schema-aware like `chat_topics_0001`. |
| 3 | `test_chatbot_r6_rate_limit::test_no_chatbot_module_calls_a_provider_outside_the_wrapper` | #1551 | Real: `analytics/labeller.production_chat` called `get_provider(...).chat` directly, skipping the rate-limit wait and the time budget. | Route through `llm_call.chat`. The wrapper takes an optional `deadline_seconds` (default unchanged) so the nightly 40-ask batch gets 120 s per attempt / 180 s per call instead of a dealer turn's 20 / 35. |
| 4 | `test_rearch_r9...::test_a_second_pick_over_the_same_tier_roster_uses_the_second_tier` | #1527 | Stale fixture: AC-10 (owner) shows no tier menu when the probe finds nothing at every level. #1527 gave the r3/r6 tier tests a probe double that finds a promo; r9/r10 kept the empty one. | Same fixture as #1527's r3/r6 change. |
| 5 | `test_rearch_r10...::test_a_fresh_promotion_ask_after_a_tier_pick_reopens_the_roster` | #1527 | Same stale fixture. With it fixed, the sibling control `test_control_a_message_that_names_the_level_answers_straight` went red: a real regression the empty fixture masked. `engine.py`'s `plan_tier_ask` turned the gate into a tier ask (`as_tier_ask` wipes `tier_stated`) even when the message named the level. | `plan_tier_ask` skips a `continue` turn whose gate proceeds on a stated level (the rule `tier_gate.needs_tier_ask` already applies). A `not_found` turn, which showed the plan's roster before #1527, keeps the stamped menu (`test_promo_menu_digits` A2). |
| 6 | `test_rearch_r13...::test_the_reply_is_productions_own_scope_needed_sentence` | #1527 | Real: #1527 let `resolve_reference_post`'s predicate arm run with `understanding=None` (meant for an empty phrase beside grounded specs). With nothing describing a set, it built one off the carried `product_set`'s ids, so the R13 refusal (AC-1790..1792) never fired and the dealer read "Couldn't find any product with stock." | That arm answers as a plain resolution when there is no understanding, no spec/brand, no scope terms and no free terms. #1527's grounded-specs case is unchanged. |
