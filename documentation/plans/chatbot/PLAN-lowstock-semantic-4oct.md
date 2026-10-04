# PLAN: low stock asks fully semantic, hard-coded rules removed (LOWSTOCK-SEMANTIC)

Status: in review, PR #1470 (4 Oct 2026); reviewer round 1 fixed, tests/chatbot 5538 passed, live parser 33/33. Track: M (LEAD pattern). No schema migration; one
data migration (`lss_0001_parser_vocab`) publishes a new UNLABELLED parser prompt version.
Card: `lowstock-semantic-behaviour-card.md` (Step 1 trace with file:line, rulings Q1-Q4).
Hand test: `laneboard/scripts/1470.md`.

## Owner report (4 Oct 2026, 03:00, gist)

"I thought I said we need to be semantic and flexible... remove the rules entirely, this is
hard coded, I don't want."

## Design (as built)

1. **Parser output** `head/parser.py`: a new strict-schema key `low_stock` =
   `{group_by: supplier|category|supplier_category|brand|warehouse|none|null, categories[],
   all_categories, brands[], suppliers[]}`, required (strict mode) and `TOLERATED_ABSENT`
   (older prompt versions and recorded emissions lack it).
2. **Prompt** `chatbot_parser_prompt.LOW_STOCK_FILTERS_ADDENDUM`, appended beneath
   `ACCOUNT_LEDGER_ADDENDUM` (`MEMORY_ADDENDUM` stays the tail): the key, translation of
   Malay / Chinese category words to English, grouping paraphrases, supplier names, the
   refinement of a report just shown (`domain_in_message` false) and the answer to the lane's
   own open question. Published by `alembic/versions/lss_0001_parser_vocab.py` through the s4
   body formula (constant plus policy blocks), unlabelled.
3. **Lane** `lanes/business/low_stock_ask.py`: every reading rule deleted (digit shape,
   grouping regexes, "all categories" regex, leftover-word supplier search, `_in_text`). It
   resolves the parser's words against `product_categories` (code, name, class vocabulary,
   `brand_hint`) and active `suppliers`; grouping is a third field (`GROUPING`, optional,
   asked only for a split the workbook lacks). Supplier is optional with `ask_unknown`.
4. **Shared helper** `required_fields.py`: `FieldSpec.ask_unknown` / `pick_head`, a
   caller-resolved reply, a parser-declared cancel, and the missed word kept on an optional
   field settled after two misses. `reply_verdict` captures only a reply the parser reads as
   answering (`open_question_answer.mode`) or as the same ask; the three-word and "?" rules
   are gone.
5. **Open question** `turn/question.of_required_ask`: the pending low stock question is the
   parser's `Open question:` object (`about: low_stock_report`, `owed`, options for a pick),
   ranked above a stale stock task.
6. **Frame** `focus.low_stock` (`turn/state.py`, `contracts.Focus`): the filters a report ran
   with, carried back on the envelope (`low_stock_frame`, `turn_runtime.py`) and stored by the
   engine; a refinement inherits what it does not name, a brand alone narrows the frame's
   categories. `low_stock_frame` and `required_ask_answer` are engine keys (stripped off the
   parser's output).

## Rollout

The owner moves the `production` label onto the version `lss_0001_parser_vocab` publishes in
the same release. Until then the old prompt emits no `low_stock`, so each fresh low stock ask
asks its category once and the reply runs it. No rule fills that gap.

## Tests

- `tests/chatbot/test_low_stock_filter_ask.py`: console turns on the new contract, rulings
  Q2-Q4, refinement, and `TestTheRulesAreGone` (kill tests: text alone is never read, a
  product without digits is never a category, the rule names and `import re` are gone).
- `tests/chatbot/test_required_fields.py`: helper changes, reply capture by the parser.
- `tests/chatbot/test_parser_low_stock_filters_publish.py`: the publish.
- `tests/chatbot/test_low_stock_live_parser.py` (opt-in `LOW_STOCK_LIVE_PARSER=1`): 27 live
  parser paraphrase cases and 6 end-to-end console flows on `gpt-5.4-mini`.
- `tests/chatbot/console_cases/2026-10-04-lowstock-semantic.yaml`: the post-deploy console check.
