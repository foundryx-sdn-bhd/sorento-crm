# UAC: a partial quantity answer closes the stock ask; trap specs parse and search reliably (STOCK-QTY-PARTIAL-FILL)

Plan: `PLAN-stock-qty-partial-fill-06oct.md`. Card: `stock-qty-partial-fill-behaviour-card.md`.
Tests in `sorento_crm_backend/tests/chatbot/` unless said otherwise.

## P1: partial answer (owner R1)

AC-1  Over an open stock task of N >= 2 lines, a message that fills SOME lines (by position,
      by code, by the parser's `open_question_answer` fill items, or `entities[].quantity`
      beside a listed code) fetches ONLY those lines (the inventory `FetchSpec` carries those
      entities and `requested_quantities`, no `not_checked`), asks no question
      (`trace.task_question is None`) and fires `task_partial_answered_stock_qty`.
      `test_stock_qty_partial_fill.py`.
AC-2  The task after that turn is OPEN, keeps every line in its original position, the filled
      lines noted; `task.open_question` reports kind `quantities` with `owed` = the unfilled
      positions and the filled lines' `qty`.
AC-3  After the stock tool answers the partial fetch (a block with only the fetched rows, none
      `needs_quantity`), `after_reply` returns no question text and the merged task: covered
      slots at their `requested_qty`, uncovered slots still owed, status OPEN while any is
      owed. The reply is the answered lines only; "How many units for each?" is not printed.
      A block that still marks one of those rows `needs_quantity` is not a partial answer:
      it rebuilds from the block as before (one line, "How many units of X?"), never the
      merged list.
AC-4  A later message filling another line answers that line alone (the earlier answered line
      is not fetched again). A bare number over the open lines fills and fetches the owed
      lines only (F2 kept, Q3 (a)).
AC-5  A restated quantity for a line already answered ("make 8152 10", or "8152 x 6" again)
      re-answers that line alone, never the list.
AC-6  Console replay of the 6 Oct transcript (five S trap lines): "I need 8152 6 unit" ->
      `SRTWC8152-SH-300 x 6: ...` only; "8357 x 10" -> `SRTWC8357-300-RL x 10: ...` only;
      "SRTWC8152-SH-300 x 6 Units Have stock?" -> the same one line; the list never appears
      after turn 1. A new product ask ("stock SRTWC9999 x 5") proceeds and parks the task
      silently (Q2 (a)).
AC-7  Unchanged rulings, pinned: "10 for all" fills every line, the answered ones included
      (mode all, 26 Sep F2, `apply.py` unchanged); "done" with
      blanks and "just proceed" answer what is noted and name the rest `Not checked:`;
      "cancel" closes; a one-line task still asks "How many units of X?"; the resume arm
      ("back to the stock check") asks only what is still owed.
AC-8  The two earlier tests that pinned the re-ask (`test_open_question_answer_fill_fills_lines
      _and_reasks_owed`, `test_per_line_reply_fills_those_lines_and_asks_the_rest_in_point_form`
      and their siblings) are rewritten to R1 with the ruling cited; no other stock-ask test
      changes.

## P2: trap specs parse (parser addendum, patch file)

AC-9  `alembic/data/chatbot_semantic_parser.trap-specs.addendum.txt` states: "s trap" /
      "s-trap" -> trap_type s_trap, "p trap" -> p_trap (value, not null); a number in mm
      beside a trap word, before or after, is trap_length and NEVER dim_width / dim_length /
      dim_height; two specification entities; the category and brand words; worked examples
      for the five prod sentences and a Malay one; a width control ("600mm wide" ->
      dim_width). No em or en dash. `test_trap_specs_prompt_patch.py` (static, always runs).
AC-10 `chatbot_semantic_parser.list-fragment.addendum.txt` states: a fragment of ONE listed
      code with a quantity is mode fill with the listed code in full; a product entity for it
      carries `canonical_code` = the listed code; answered lines are answered at once and the
      rest stay open. Static test.
AC-11 The production text file is untouched; the committed `.patch` equals the diff derived
      from the two addenda and applies cleanly with GNU patch; the anchors occur exactly once.
AC-12 The code fallback `SEMANTIC_PARSER_PROMPT` carries `TRAP_SPECS_ADDENDUM` (same rules)
      so a fresh install's v1 teaches it. Static test.
AC-13 Live parser (opt-in `CHATBOT_LIVE_LLM=1`, crew runs on the Mac): the patched text parses
      "Any WC with S trap 300mm available" as category + trap_type s_trap + trap_length 300 +
      check_stock; the same for the lower-case 10:07 sentence, the 12:45 code sentence
      (product SRTWC8066-SH qty 50 + the two specs), the Malay paraphrase; the v42 text is
      xfail non-strict. Control: "any water closet 600mm wide" -> dim_width 600 on the
      patched text. `test_trap_specs_live_parser.py`.
AC-14 Console eval `console_cases/2026-10-06-trap-specs.yaml`: the same sentences with
      `expect.parser.entities` (hint, spec_key, spec_value per entity), the 15:29 brand
      sentence `expected_red_until: "#1509"`; shape pinned by a pytest that reads the file.

## P3: spec search narrowing (DB tests)

AC-15 `tests/chatbot/test_trap_spec_search.py` (Postgres, `blank_session`): with S trap and
      P trap water closets at 250 and 300 mm under two brands and stock on some,
      `resolve_product_set(require={"stock": True}, specs=[trap_type s_trap, trap_length 300],
      scope_terms=["water closet"], brand="Sorento")` returns exactly the Sorento S trap 300mm
      products that have stock; dropping `brand` adds the other brand's match; `trap_length
      250` returns the 250 ones only; without `require.stock` the out-of-stock match is counted.
AC-16 `narrow_code_tokens(query="SRTWC8066-SH s trap 250mm 50pc", tokens=[...])` replaces the
      code token by the 250 mm variants only when the family carries 250 and 300 variants
      (Q5 (a)); when every variant carries the same trap spec the family is left alone (the
      colour pick, today's behaviour).
AC-17 R2 / Q4 (a), owner ruled: a product carried from focus with no code word in the
      message is NOT narrowed by `narrow_code_tokens` (returns None; the measurement the card
      cites), so `narrow_code_tokens_by_specs` narrows the carried code's family by the
      grounded spec values instead: `trap_type p_trap` over SRTWC286-SH picks SRTWC286-SH-P
      (unit test and the resolve route); a family with no variant carrying the spec is left as
      it is (None); a product that itself carries the spec stays the answer even when siblings
      carry it too; a brand spec and a non-product token are never narrowed. The pin pruning
      that follows drops only the replaced token's pin, matched the resolver's way.

## Process

AC-18 Red first: AC-1 to AC-6 fail on `main` and pass with the change; kill test: reverting the
      fill-arm change alone makes AC-1/AC-6 red, reverting the `_rebuilt` merge alone makes
      AC-3 red.
AC-19 Full `tests/chatbot` + `tests/test_product_predicate_service.py` +
      `tests/test_resolve_predicate.py` green in the sandbox; reviewer (opus) run once; console
      replay table and the hand-test script on the PR.
