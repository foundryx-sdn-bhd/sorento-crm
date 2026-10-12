# PLAN: DEV-set failure coverage (DEV-FAILURES, 12 Oct)

Status: Merge-ready. RC-A and RC-B done; crew eval gate passed on e448f3da (eval v62), reviewer READY on 938e6387. Small fix track (under ~300 lines per root cause, no auth change; RC-A carries one data-only prompt publish migration, label unmoved).

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

## RC-A (done, rebuilt on v56 per crew 12 Oct)

- Base is main's trimmed v56 text (`alembic/data/chatbot_semantic_parser.ablation-20261011.txt`).
- `alembic/data/chatbot_semantic_parser.dev-failures.addendum.txt` (946 chars): eight one-line
  business rules, one English example in total, no word lists, no Chinese or Malay (owner 12 Oct,
  #1542 audit). A test pins the shape.
- `alembic/versions/devf_0001_parser_data_rules.py` publishes v56 + both addenda, unlabelled;
  promoting is the owner's label move after the crew eval gate.

## RC-B (done)

- Parser key `portal_form` (one of `portal_service.SUPPORTED_TYPES`, else null):
  `alembic/data/chatbot_semantic_parser.portal-form.addendum.txt` (262 chars), also in the fallback
  constant so the strict schema and the prompt declare the same keys.
- `run_fetch` sends it as `submission_type`, so the link opens that portal tab; a price tag
  request is not deep-linkable and gets the bare link.
- The reply names the form from `CHATBOT_REPLY_COPY` (owner approved the five lines in en/ms/zh,
  12 Oct), seeded insert-only by `devf_0002_portal_link_copy`, editable in Settings > AI Prompts.

## Tests

- `tests/chatbot/test_dev_failures_parser_publish.py`, `tests/chatbot/test_dev_failures_portal_form.py`
  (CI, mocked, no LLM).
- `tests/chatbot/test_dev_failures_live_parser.py` (opt-in live, made-up phrasing, one call per case).
