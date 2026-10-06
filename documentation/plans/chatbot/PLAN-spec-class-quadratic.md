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

## Why the gate opened only with "TAP" (owner evidence, 20:06 re-send answered in 8.4 s)

The spec fallback runs only when `references.py:3090-3098` finds a caller token the resolver
could not place (`not _every_caller_token_resolved` at `:1878`, then `_has_unresolved_tokens`
at `:1928`). "TAP SRTWT5608" (or "TAP" alone) matches no code, so the gate opens and the
whole message goes to `search_specs`. Without "TAP" every token resolves, the gate
short-circuits and `search_specs` never runs. `exact_codes_only` (`engine.py:4981-4986`) runs
AFTER the resolver and only filters its answer; it does not gate spec search.

## Fix A, this PR (at the seam, behaviour unchanged, small fix track)

1. `ClassVocabulary` (`product_class_signal.py`): the three row sets are read ONCE into a
   word -> labels index (plus the shipped `CLASS_SYNONYMS` fallback, same precedence) and
   every term or span is a dict probe. Built per call and handed down, never held on the
   process, so a row written in the same session is seen by the next lookup.
   `resolve_classes_for_term(db, term)` is `ClassVocabulary(db).resolve(term)`: one
   implementation, same answers.
2. `search_specs` builds one snapshot for the whole call and computes `_exact_classes` once
   per distinct free term, reading that verdict for both the implied-class set and the
   two-types check (the duplicate pass at `:1229` is gone).
3. No skip heuristic on message text: owner rule is that the parser decides, so the span
   enumeration stays; it just stops hitting the database.

## Follow-ups (not in this PR; crew decision on PR #1508: option (a), S fix only)

### Whole-sentence free term -> unresolved tokens only (separate M lane, behaviour card first)

Owner rule (6 Oct 2026 20:35): "parse the entities first; only an entity the resolver cannot
find goes to spec search; never the entire sentence."

Today: `references.py:3145` -> `product_spec_understanding.understand_phrase` puts the WHOLE
message first in `free_terms` (`:607` deterministic, `:676` model path; comment `:673-675`
"the whole phrase always stays available as free text"). History is squashed in this clone;
the scout's blame is ac2c94b8e (7 Aug, #98/#100) for `:676` and 74f95933e (15 Aug, PR #142
"Spec search on raw customer text") for `:607` + `references.py:3145`, added because n8n's
entity-built `free_terms` dropped "thickness 1.0mm" (live turn 12303548).

Proposal: in `references.py`, when the caller sent `tokens`, the ranker's `free_terms` become
caller `free_terms` + the model's own terms + the tokens whose resolution has no matches, and
the whole `query` is dropped from `free_terms`. It stays the input of `understand_phrase`'s
spec reading (`:589`, linear), which is what carries the thickness. Query-only callers (the
Product Specifications preview) keep today's whole-phrase behaviour.

Tests that pin the current behaviour and must stay green: `test_spec_raw_text_hardening.py:443`
(12303548, query only), `test_resolve_brand_routing.py:157` (12303509, tokens + sentence;
brand comes from the baseline specs, class from the unresolved token),
`test_product_spec_understanding.py:178` (whole phrase kept beside the model's terms),
`test_resolve_spec_fallback.py` (query + explicit `free_terms`). The chatbot's grounding
already covers numeric specs (`head/grounding.py:334-342`, forwarded as `extracted_specs` by
`resolve_gate.py`; `tests/chatbot/test_attribute_asks_round10.py:313`).

Test gap the behaviour card must settle: a sentence whose class word sits OUTSIDE every entity
the parser emitted ("any basin, srtwt5608 x 2" where the parser emits only the code) stops
reaching the ranker under the rule. No test pins either outcome today; the card decides which
one is right, then the lane writes it red-first.

## Proof

- Red-first test `tests/test_spec_class_quadratic.py`: the owner's 15-line shape (14 codes,
  line 8 prefixed "TAP", one duplicate line, "check stock") through `search_specs(exact=True)`
  and through the resolve route with the chatbot's body, asserting a statement bound and one
  vocabulary read per search. Red on main: 59.6 s and 3,543 `stored_class_labels` scans for
  the TAP variant; the no-TAP variant (0.10 s, no spec search) is pinned green.
- Replay of the exact 15-line message through the same route (dry run, test DB) with
  before/after wall time and SQL counts in the PR.
- Every existing `product_spec_search`, `product_class_signal`, resolve-route and chatbot
  grounding test stays green (byte-identical replies).

UAC: `spec-class-quadratic-acceptance-criteria.md`.
