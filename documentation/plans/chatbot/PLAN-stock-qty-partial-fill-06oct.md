# PLAN: a partial quantity answer closes the stock ask; trap specs parse and search reliably (STOCK-QTY-PARTIAL-FILL)

Status: BUILDING (6 Oct 2026), red-first on PR #1513 (branch `claude/stock-qty-partial-fill-kv7i1d`,
cloud sandbox, LEAD pattern). Track: full (engine behaviour change + parser prompt patch + DB
search tests; no migration, no auth change, no UI). Card:
`stock-qty-partial-fill-behaviour-card.md` (owner rulings R1, R2 recorded there; Q1 to Q5 open
on the PR, recommendations stand until answered). UAC:
`stock-qty-partial-fill-acceptance-criteria.md`.

## Journey

A dealer asks "Any WC with s trap 300mm available?". The bot lists the five S trap 300mm water
closets with stock and asks "How many units for each?" with a numbered line per product. The
dealer answers one line ("I need 8152 6 unit"). The bot answers THAT product's stock at once.
The other four lines stay open: the dealer may fill another ("8357 x 10"), say one number for
the rest, ask about another product, or move on. The bot never prints the list again. The
same sentence parses the same way every time: "S trap 300mm" is trap_type s_trap plus
trap_length 300, never a width.

## Facts measured (`main` cdd459b6, `sorento_crm_backend/` paths)

- Task lifecycle: `app/services/chatbot/turn/task.py`. `run()` :500 (claims -> fill -> fetch /
  question), fill arm :582-628: a fill that leaves any slot owed asks `question()` :354 (the
  whole list) and fetches nothing (:599). `after_reply` :846 / `_rebuilt` :1076 rebuild the task
  from the stock tool's `stock_availability` block and ask the list again while any row still
  `needs_quantity`.
- Parser answer: `turn/apply.py::_open_question_answer` :2771 writes `SLOT_QUANTITIES` per placed
  line; `_placed_line` :2716 places by full code or position. The parser resolves a fragment of a
  listed code itself (prompt `STOCK_TASK_ADDENDUM`, `chatbot_parser_prompt.py:360-376`).
- Fetch replacement: `apply.py:3489` `task_locked = task_outcome.fetch is not None`; :3744 the
  task's own `FetchSpec` replaces the turn's inventory spec (`task_drives_the_fetch`).
- Reply: `engine._stock_ask_reply` :6548 writes `after_reply`'s tasks onto the focus and says
  the task's question when the block still owes a quantity.
- Prompt: the production text is `alembic/data/chatbot_semantic_parser.prod-20261001.txt`
  (pdyn_0003 requires it byte-identical). Since PROMPT-DYNAMIC (#1405) a wording change ships as
  an addendum text + unified diff that crew sequences onto the live combined version
  (ORDER-BROADEN precedent, `tests/chatbot/_order_broaden_prompt.py`), never as a migration that
  rebuilds a version from the code constant (`chatbot_prompt_vars.wording_layer_exists`).
- Spec search: grounded specs travel as `extracted_specs` (`lanes/business/resolve_gate.py:637`),
  the category entity as `scope_terms` (:687), `require` from `predicate.derive_require`
  (`check_stock` -> `{"stock": true}`, `predicate.py:27`). `references.py:2850` counts the set
  only when no token matched a code; a code plus words goes through
  `product_code_family.narrow_code_tokens` (:146), which reads the words beside the code OFF
  THE MESSAGE and needs the code word in the message (`_code_word_for` :54), so a product
  carried from focus with no code in the message is never narrowed by it.

## Decisions

D1 **Partial answer (R1).** In `task.run`'s fill arm, when the task has two or more slots and
   this turn gave or restated a quantity for some of them while others stay owed: the fetch
   covers ONLY the slots this turn quantified (`to_fetch` over those slots), the task stays
   OPEN with every slot, and no question is asked. Rule `task_partial_answered_stock_qty`.
   Nothing is dropped and no `not_checked` is written (Q1 (a)).
D2 **The fetch after a partial answer never re-answers a line answered earlier.** The slots
   fetched are the ones this turn changed or named; a bare number over the open lines (F2,
   Q3 (a)) fills and fetches the owed ones only.
D3 **Merge, never rebuild, after a partial fetch.** `_rebuilt` keeps the existing OPEN task's
   slots the block did not cover (still owed, same positions), updates the covered ones from
   `requested_qty`, and sets OPEN while any slot is owed, else ANSWERED. A reply whose block
   is fully answered says the stock lines only (`after_reply`'s early return, unchanged).
D4 **"done" / "just proceed" / "all" / "cancel" unchanged.** They are explicit instructions and
   keep their rulings (drop + `Not checked:`, every line, close).
D5 **A new product or domain ask proceeds** exactly as today: the task is parked silently
   (`run` arm 4); the parser's `entity_op` decides reuse vs replace; no code rule (R1).
D6 **Prompt: patch file, no migration.** `alembic/data/chatbot_semantic_parser.trap-specs
   .addendum.txt` (the TRAP SPECS rules + examples, inside the SPECIFICATIONS block after its
   last example) and `chatbot_semantic_parser.list-fragment.addendum.txt` (a fragment of one
   listed code is that line, inside QUANTITIES and LAST_ANSWER), applied by
   `tests/chatbot/_trap_specs_prompt.py`, committed as `chatbot_semantic_parser.trap-specs
   .patch`. The code fallback `SEMANTIC_PARSER_PROMPT` gains the same text as
   `TRAP_SPECS_ADDENDUM` so a fresh install carries it. Crew sequences the patch onto the live
   version and runs the live eval (the cloud has no parser key).
D7 **Eval.** `tests/chatbot/console_cases/2026-10-06-trap-specs.yaml` grades
   `expect.parser.entities` (spec_key / spec_value) on the five prod sentences plus
   paraphrases; the 15:29 brand sentence is `expected_red_until: "#1509"`. A `live_external`
   pytest (`test_trap_specs_live_parser.py`) runs the same sentences against v42 (xfail,
   non-strict) and the patched text (green), the ORDER-BROADEN pattern.
D8 **Search proof.** `tests/chatbot/test_trap_spec_search.py` seeds S trap / P trap water
   closets at 250 and 300 mm, two brands, stock on some: `resolve_product_set` with
   `trap_type` + `trap_length` + class + brand + `require.stock` returns exactly the matching
   in-stock products; `narrow_code_tokens` on "SRTWC8066-SH s trap 250mm" keeps only the 250
   variants (Q5 (a), already the behaviour when the catalogue text carries the length).
D9 **R2 / Q4 not built until ruled.** The carried-product variant narrowing does not exist
   (`narrow_code_tokens` needs the code in the message); the card states that honestly and the
   owner picks. A test pins today's class-wide behaviour as the documented baseline.

## Slices

S1 Card + plan + UAC (this file). DONE.
S2 Red tests: partial fill (apply + task + Console replay of the 6 Oct transcript), prompt
   patch statics, search proofs, eval YAML. Then the engine change in `turn/task.py`.
S3 Prompt addendum text, applier, committed patch, code-fallback constant, live test.
S4 Reviewer (opus) + kill tests; console replay table on the PR; hand test script.

## Triggers deliberately not built

- A "Still open: 1, 3, 4, 5" line (Q1 (b)): only if the owner asks.
- Carried-product variant narrowing for "how about p trap" (Q4 (a)): a small resolver
  extension, built only on the owner's pick.
- A which-one clarify for a bare number over the mixed open task (Q3 (b)): on ruling.
