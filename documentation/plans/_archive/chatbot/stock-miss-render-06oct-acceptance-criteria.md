# UAC: STOCK-MISS-RENDER (small fix track)

Plan: `PLAN-stock-miss-render-06oct.md`. All ACs are pinned by `sorento_crm_backend/tests/chatbot/test_stock_miss_render.py` unless named otherwise.

- AC-SMR-1 [journey] An availability-only dealer typing "Srt 364-hp x 1 stock available?" (parser: raw "Srt 364-hp", hint product, quantity 1, canonical_code null) with SRT364-HP and SRT364-HP-BL in the catalogue gets "SRT364-HP x 1: Got stock." and exactly one `crm_inventory_stock_balance_list` call carrying the SRT364-HP uuid only.
- AC-SMR-2 [journey] "SRT364-HP X 1" on the same catalogue still answers "SRT364-HP x 1: Got stock." (production turn #17d3, the control).
- AC-SMR-3 [bridge] A miss whose resolver match carries a uuid but whose fetch the runner refused before any tool call (scope gate, outcome not_found) never contains "No stock" or "no incoming" and climbs no ladder rung (no `mcp_probe` call).
- AC-SMR-4 [bridge] A real empty tool result still says "No stock and no incoming for X" (`test_crossdomain_ladder.py:226/274`, `test_s6c_answer_lane.py:2804`, unchanged).
- AC-SMR-5 [engine] `_exact_codes_only` rewrites the `resolved_kinds` row under the resolver's own token for an uppercase, hyphenated typed code ("SRT364-HP" -> row "SRT364HP" reads product: 1 after the -BL variant is cut); the lowercase bare shape ("srt5674") rewrites as before; the input map is never mutated.
- AC-SMR-6 [rule] No new typo, word or fuzzy matching anywhere: every candidate the filter or the quantity join reads is one the resolver already placed; the parser prompt is untouched.
- AC-SMR-7 [trace] A skipped rung writes a `crossdomain` trace entry with `skipped: primary_tool_not_run`, and `trace_detail._crossdomain` carries the key only on that entry (a climbed rung keeps its five-key shape, `test_turn_detail_api.py`).
- AC-SMR-8 [ruling (a)] On a resolver not_found exit the bridge answers with the miss text alone and climbs no rung (`test_rearch_r4_miss_engine.py::TestAC1705IncomingMissWithStockLadder`, expectation flipped; AC-1705 in `chatbot-answer-half-reattach-acceptance-criteria.md` annotated).
