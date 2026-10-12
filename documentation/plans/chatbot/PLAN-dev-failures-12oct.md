# PLAN: DEV-set failure coverage (DEV-FAILURES, 12 Oct)

Status: Plan. Small fix track (expected diff under ~300 lines per root cause, no migration, no auth change).

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

## Root causes

(filled in after classification)
