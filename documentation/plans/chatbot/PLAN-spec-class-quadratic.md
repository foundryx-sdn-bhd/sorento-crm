# PLAN: SPEC-CLASS-QUADRATIC - spec class lookup is quadratic in message words

Status: in progress (small fix track: no migration, no auth/RBAC change, no new ingest surface; backend only, replies byte-identical)

## Incident

Prod (main 9373f6c90), 6 Oct 2026 20:02 MYT: the owner sent a 15-line stock check (14 unique
codes as "CODE x QTY" lines, one "TAP SRTWT5608" prefix, one duplicate line, then "check stock")
and got n8n's error-arm reply because POST /api/v1/external/chat/turn overran the n8n budget.
A dry-run replay on the Mac returned HTTP 200 in 50.5 s with `chatbot slow turn: 50.0s ...
understood=6745ms, routed=41943ms`; the MCP stock fetch only started after 42 s.

## Root cause (verified in code; measurements from crew scout's cProfile of the replay)

- `app/api/v1/system/references.py:3211` calls `search_specs(exact=True, free_terms=...)`. The
  free terms come from `product_spec_understanding.understand_phrase`
  (`app/services/product_spec_understanding.py:607` / `:676`): the WHOLE message is always
  `free_terms[0]` (57 words on the replay), plus the single tokens the model returned.
- `app/services/product_spec_search.py:1227` and `:1229` each call `_exact_classes` on every
  free term. The second call is a duplicate pass over the same inputs: `_exact_classes` is a
  pure function of `(db rows, term)` and nothing writes between the two lines.
- `_exact_classes` (`product_spec_search.py:1099`) enumerates every word span of the term
  (57 words -> 57*58/2 = 1,653 spans) and calls `resolve_classes_for_term`
  (`app/services/product_class_signal.py:228`) per span, plus a second call per span whose
  last word ends in "s". Each call runs three queries uncached: ProductCategory `.all()`
  (`:254`), `stored_class_labels` DISTINCT over `product_specifications` (`:297`), and the
  registry `.first()` (`:278`).
- Measured on the replay: 34 `_exact_classes` calls, 3,406 `resolve_classes_for_term`,
  3,407 `stored_class_labels`, 10,581 SQL statements, 18.1 s psycopg2 self time. Cost grows
  with the square of the word count.

## Fix (at the seam, behaviour unchanged)

1. Per-request memo for the class vocabulary: `resolve_classes_for_term` reads the three row
   sets once per session (keyed on the SQLAlchemy `Session` via `session.info`, so it never
   outlives the request and never serves rows from another transaction) and answers every
   term from them. No process-wide cache of DB rows.
2. `search_specs` computes `_exact_classes` once per distinct free term and reuses the result
   for both the implied-class set and the two-types check (removes the duplicate pass).
3. No skip heuristic on message text: owner rule is that the parser decides, so the span
   enumeration stays and the memo alone carries the cost (one span lookup becomes a dict
   probe, no SQL).

## Proof

- Red-first test `tests/test_spec_class_quadratic.py`: a 15-line code-list message through
  `search_specs(exact=True)` asserts an SQL statement bound and at most one
  `resolve_classes_for_term` DB read per session. Fails on main with the measured shape.
- Replay of the exact 15-line message through the same route (dry run, test DB) with
  before/after wall time and SQL counts in the PR.
- Every existing `product_spec_search`, `product_class_signal`, resolve-route and chatbot
  grounding test stays green (byte-identical replies).

UAC: `spec-class-quadratic-acceptance-criteria.md`.
