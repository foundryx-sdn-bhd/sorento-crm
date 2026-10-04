# LOWSTOCK-FILTER-ASK cloud browser pass, LIVE parser (3 Oct 2026)

Code under test: `c5ef8dbe`. Same sandbox stack and synthetic seed as `../README.md`, with
ONE difference: the parser is the real one (`gpt-5.4-mini`, prompt `chatbot_semantic_parser`
v3 labelled production), reached through the sandbox agent proxy
(`harness/run_backend_live.py`; only file storage is swapped for local disk).

## Findings this pass produced (fixed red-first, each through tester, coder, reviewer)

1. A bare "low stock report" asked right after a turn naming "water closet": the live
   parser emitted the carried word as a current-message category, and the report ran
   scoped. Fixed in 989dc44a (a category/brand word counts only when the message text has it).
2. "cancel" as the answer: the live parser emitted `is_affirmative: false` and
   `topic_reset: true`; the reroute kept them and the turn fell into the generic low-signal
   reply. Fixed in c5ba6734 + c5ef8dbe (a rerouted answer is never a yes/no or a new topic).

## Results at c5ef8dbe (all PASS)

| # | Message(s) | Width | Seen |
|---|---|---|---|
| O1 | "low stock report water closet taiyang" | 1280 | `Low stock report (water closet, supplier XIAMEN TAIYANG TECHNOLOGY CO.,LTD, no grouping) - as of 03/10/2026` / `Low: 2 of 2` + xlsx |
| O2 | "low stock report by supplier, water closet only" | 1280 | `Low stock report (water closet, all suppliers, by supplier) - as of ...` / `Low: 4 of 4`, no "Could not find" |
| O3 | O1 with the worker stopped | 1280 | header + `Preparing the low stock report - test turn: it will be in My Downloads, nothing is sent to WhatsApp.` |
| 1 | "Sorento water tap low stock list" | 1280 | `Low stock report (Sorento water tap, all suppliers, no grouping) ...` / `Low: 1 of 1` |
| 2 | "low stock report" | 1280, 375 | the category question; chat runs unchanged |
| 3 | -> "water closet" | 1280 | `Low stock report (water closet, all suppliers, no grouping) ...` / `Low: 4 of 4` |
| 4 | "low stock report" right after step 3 -> "all" | 1280 | the question (no carried word), then `Low stock report (all categories, ...)` / `Low: 5 of 5` |
| 5 | "low stock report" -> "spaceship" -> "rocket" | 1280 | miss line + question, then the give-up line; no run |
| 6 | "low stock water closet by supplier" | 1280 | `... (water closet, all suppliers, by supplier) ...`; exactly one run |
| 7 | "low stock report" -> "cancel" | 1280, 375 | `Low stock report cancelled.`; no run |
| 8 | step 6 workbook | - | one Low/All sheet pair per supplier, WC rows only |
| D | WhatsApp delivery | - | 0 of 41 downloads claimed |
| R | 375 layout | 375 | page scrollWidth 360 == clientWidth 360 |
