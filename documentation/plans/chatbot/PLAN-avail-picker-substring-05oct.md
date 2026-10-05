# PLAN: availability picker for a substring code token (AVAIL-PICKER)

Status: Review passed on PR #1494 (track: small fix, no migration, no
auth/RBAC change, no new ingest surface); reviewer round 1 fixed, sandbox console pass green on 8e95591b, owner hand test pending. LEAD
pattern: behaviour card posted as crew-ask on the PR, built on its recommendations
(Q1 (a) print every option, Q2 (a) one substring hit answers directly, Q3 (a) full mode
untouched). UAC: `avail-picker-substring-acceptance-criteria.md`.

## Owner trace (5 Oct 2026, AVAILABILITY contact 437264483, prompt v42)

"wc286 check stock" dead-ended:

```
Here's what you want:
• product: SRTWC286-SH (+9 more)

Couldn't find: "wc286" (product).

But no inventory matched these. Please refer to your salesman.
```

The owner expected the which-one picker the availability flow promises ("which SRTWC286
variant?").

## Root cause (measured on origin/main 05ddec69)

Paths under `sorento_crm_backend/app/services/`.

- The resolver DID place "wc286": `entity_resolver.py:1883-1905` `_prefix_probe_product`
  probes `product_code` by prefix ILIKE first, then by substring ILIKE (`match_tier`
  "substring"), capped at `PREFIX_LIMIT = 20` (`:1868`). 10 hits, the SRTWC286-SH family,
  `ambiguous=True`, not in `unresolved_tokens`.
- The inventory gate has no ambiguity picker (`lanes/business/gate.py:163-164`
  `REQUIRE_SPECIFIC_DOMAINS = {incoming, product_attachment}`), so all 10 pass as
  `compatible_entities` (`:442`). That is the "SRTWC286-SH (+9 more)" line
  (`lanes/business/answer.py:3575-3590`).
- ROOT CAUSE: the AVAILABILITY profile runs `engine._exact_codes_only` after the resolver
  (`engine.py:4854-4860`, function `:511-575`). Per typed token it keeps a candidate only
  when `code_of(c) == fold` or `code_of(c).startswith(fold)` (`:549-550`). "wc286" is a
  SUBSTRING of every SRTWC286-* code, so the token lands in `misses` (`:555-556`), all 10
  rows are stripped from `compatible` (`:569-573`), the token is unplaced, and
  `turn_runtime._answered_unfiltered` (`:3929`) turns the fetch into `outcome: not_found`
  ("But no inventory matched these.", `answer.py:3788`).
- The family pick (`engine._stock_ask_reply` `:6416-6495` over `turn/task.py::after_reply`
  `:860-945`) needs a `stock_availability` block from a fetch that ran (`:6456-6461`);
  impossible once `compatible` is empty.
- The same prefix-only reading sits in `turn_runtime.py:2481-2497` (a token that is the
  prefix of exactly ONE resolved code carries its quantity).

## Rule (proposed; owner confirms via crew-ask)

For an availability-access stock or ETA turn, per product token:

1. a code equal to the token is the product (unchanged);
2. else a code-like token keeps the codes it is the PREFIX of (unchanged);
3. else (NEW) a code-like token keeps the codes it is a SUBSTRING of, read off the
   resolver's own deterministic `product_code` lookup. No new matching anywhere: the
   resolver already returned those rows with `match_tier: substring`; the availability
   filter just stops discarding them. No fuzzy or typo rule is added (owner rule stands).
4. else a code-like token is not found and goes to the did-you-mean (unchanged).

The kept family then runs the inventory fetch, and the existing family pick / quantity
rule produces the which-one picker ("WC286 matches 10 products. Which one?" + numbered
list), or the quantity question when exactly one code matches.

`turn_runtime.py:2481-2497` gets the same fallback: a typed token that is the substring
of exactly ONE resolved code carries its quantity to that code.

## Slices (one branch, one PR)

1. Red tests: `tests/chatbot/test_avail_mode_scenarios.py` console journeys (S55-S55e) and
   `tests/chatbot/test_avail_picker_substring.py` on the three seams.
2. `engine._exact_codes_only` substring fallback; `turn/task.py::_group` and the
   `turn_runtime` quantity carry aligned, every substring reading gated on a code-like
   token (letters AND digits, `turn/task.py::code_like`) so a word or bare number the
   parser tagged as a product never names a family (reviewer finding 1).
3. Catalogue row in `tests/chatbot/AVAIL-MODE-SCENARIOS.md`; hand-test script
   `laneboard/scripts/<PR>.md`.
