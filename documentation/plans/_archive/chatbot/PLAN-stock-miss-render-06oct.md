# PLAN: stock miss never claims "No stock" without a lookup; a spaced code reaches the fetch

Status: small fix track, implemented, reviewer VERDICT: READY on head, PR #1501 (crew lane STOCK-MISS-RENDER)
UAC: `stock-miss-render-06oct-acceptance-criteria.md`
Owner report (6 Oct 2026 13:07 MYT, production chatbot console turns #f763 / #17d3): the "No stock and no incoming" line is wrong when no stock lookup ran.

## Journey

A dealer (availability-only profile) types "Srt 364-hp x 1 stock available?". Today the reply is

```
Couldn't find: "Srt 364-hp" (product).
No stock and no incoming for SRT364-HP.
Please refer to your salesman.
```

with tool=null on the trace: nothing looked the stock up. After: "SRT364-HP x 1: Got stock." (the same reply "SRT364-HP X 1" already gets), and on any turn whose primary tool did not run, no "No stock" / "no incoming" claim at all.

## Defects (`sorento_crm_backend/`)

1. `app/services/chatbot/answer_bridge.py::answer_for` (miss arm) ran `_run_crossdomain_ladder` before the miss text for every miss, with an empty item. `answer.crossdomain_zeroset` counts every resolver code absent from the primary result as missing (every code when no primary result exists) and `crossdomain_render` printed the stock claim over a product nothing had looked up.
2. `app/services/chatbot/engine.py::_exact_codes_only` (availability-mode exact-code filter) and `app/services/chatbot/turn_runtime.py::_spec_quantities` joined the typed word to the resolver's placed code by casefold alone. The resolver matches with separators folded (`resolve_gate._PRODUCT_FOLD` / `_token_of`, `entity_resolver._norm_token_map`), so "srt 364-hp" never equalled "srt364-hp": the placed uuid and the "x 1" quantity were dropped, the word was listed as a miss, and `make_tool_runner` refused the fetch for having no subject (scope gate, `turn_runtime.py` ~3010).

## Fix

1. The ladder runs only when the primary tool ran: `via_fetched_empty` (kind "result", zero rows) or a fetch item naming its `tool`. Otherwise the inactive shape `{"zeroset": None, "render": None}` and a `crossdomain` trace entry `skipped: primary_tool_not_run` (shown by `trace_detail._crossdomain`). Miss text unchanged. Crew ruling (a): the rule is WIDE, it covers the resolver's own not_found exit too; AC-1705's resolver-exit expectation flipped accordingly.
2. Both joins use `turn.state.token_key` (case and separators folded). No new typo, word or fuzzy matching: only candidates the resolver already placed are re-joined. Intended widening: the `resolved_kinds` rewrite loop in `_exact_codes_only` is keyed by the resolver's own token ("SRT364HP", case kept) and now folds that side too, so it fires for uppercase or hyphenated codes where it was dead before (pinned by a test).

## Tests (written first)

`tests/chatbot/test_stock_miss_render.py` (5 tests): turn #f763 through the real engine (`AvailConsole`), the #17d3 control, the uppercase and lowercase kinds-rewrite pins, and the bridge miss arm with a placed uuid plus a scope-gate refusal. Kill proof per fixed file in the PR evidence comment. Related suites kept green: `test_crossdomain_ladder.py`, `test_s6c_answer_lane.py`, `test_rearch_r13_stock_no_subject.py`, `test_rearch_r4_bridge_miss.py`, `test_rearch_r4_miss_engine.py` (AC-1705 flipped), `test_avail_mode_scenarios.py`.

## Known follow-ups

- `turn/task.py:773` (`_asked_tokens`), `task.py:795` (`_group`) and `turn/apply.py:2224` (D29 exact code when a quantity is named) still join typed tokens by casefold only. A spaced family word with no exact code ("srtwc 286 stock?") now reaches the fetch but builds no which-one picker; a staff contact typing "srt 364-hp x 1" is not narrowed to the exact code (`_exact_codes_only` runs for availability-only contacts). Same class, same `token_key` fix, separate lane.
- `token_key` does not fold U+2212 / U+2013 the way `resolve_gate._UNICODE_DASH_FOLD` does; pre-existing, unchanged.

## Out of scope

Parser prompt (untouched by owner rule). Frontend (none). The `ci-full` merge gate is the crew's.
