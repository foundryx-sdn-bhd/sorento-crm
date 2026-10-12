# PLAN: DEV-set failure coverage (DEV-FAILURES, 12 Oct)

Status: Build, RC-A done (awaiting crew eval gate), RC-B next. Small fix track (under ~300 lines per root cause, no auth change; RC-A carries one data-only prompt publish migration, label unmoved).

## Journey

The 16 DEV-set failures on main (parser v50) are classified by first failing layer
(parse / resolve / tool params / reply / clarify / carry), grouped by root cause, and the top
2-3 root causes are fixed. The case pack itself is crew-only and is never committed; tests use
made-up phrasing in the same shapes.

## Owner rules

- The parser decides meaning: parse misses are fixed in the parser PROMPT, never by code word-matching.
- Resolve misses are fixed with data or resolver rules, never fuzzy or name matching.
- Clarify instead of assume; replies never leave the user stuck.
- Mock the LLM in tests; at most 30 live parser calls per round.

## Root causes (16 DEV cases, classification table in the PR's crew-note)

| RC | Layer | Cases | Fix |
|---|---|---|---|
| A | parse: meaning read, mapped to the wrong records | 7 | Prompt addendum of business rules, English examples only (owner rule 11 Oct: no language teaching) |
| B | tool params: portal link never carries which form | 2 | Parser names the portal form from data, params map it to `submission_type`, reply names the form |
| D | annotation probe checks less than the answering fetch | 2 | Needs traces before a fix |
| C, E, ladder | resolver single winner, stale miss carry, zero-stock base code | 1 each | Later |
| data | eval contact seed, attachment type alias, promotion rows | 3 | Data, not code |

## RC-A (done)

- `alembic/data/chatbot_semantic_parser.dev-failures.addendum.txt`: eight rules under "WHICH DATA
  ANSWERS THE QUESTION".
- `alembic/versions/devf_0001_parser_data_rules.py` publishes v50 + addendum, unlabelled; promoting
  is the owner's label move after the crew eval gate.
- Tests: `tests/chatbot/test_dev_failures_parser_publish.py` (CI, Postgres) and
  `tests/chatbot/test_dev_failures_live_parser.py` (opt-in live, made-up phrasing): base v50 5/8,
  patched 8/8, one call each (16 live calls).
