# UAC: rogue dealer conversation, 6 Oct (ROGUE-CONVO-REVIEW)

Plan: `PLAN-rogue-convo-review-06oct.md`. Each criterion is a general rule; the 6 Oct turns
are its red tests. Fake fixture ids in every test; product codes are catalogue codes.

## Class A: zero-scope list asks for a filter

- AC-RC-A1: a fetch in any domain whose `ALLOWS_EMPTY` row is False, with zero resolved
  scoping entities, no filter, no date window and no explicit broaden, calls no tool and
  replies with the scope sentence for that domain ("That would search every product we
  have - I need at least one filter ..."). Red: bare "product" (turn 2); "srtwc-8318"
  typed while a kind_pick is open falls to master_products with no uuid (turn 7).
- AC-RC-A2: the refusal is one mechanism for every such domain (the `ALLOWS_EMPTY` dict),
  not a per-domain list in the engine.
- AC-RC-A3 (control): a scoped product ask ("SRTWC286 price") fetches with that id; bare
  "PO" still lists the open book; `low_stock_report` with no entities still runs.
- AC-RC-A4 (if Q1 = a): the chatbot's product list never returns a product whose
  `is_searchable` is false, whose category's `is_searchable` is false, or whose
  `is_active` is false; the admin products list is unchanged.

## Class B: an unplaced typed product token is "Couldn't find X"

- AC-RC-B1 (owner ruling Q3): a bare number typed as a product matches its code family
  through the resolver's own probe and is offered the same which-one picker compact mode
  gives ("8066 matches 3 products. Which one?"); a bare number inside exactly one code
  answers as that code; the scope sentence never answers a message that named a product
  token. Red: "8066 stock ?" (turn 3).
- AC-RC-B2: a bare number inside no code stays an unplaced miss (named in the reply, never
  the scope sentence); a WORD the parser tagged as a product is still never a product.
- AC-RC-B3: the three seams that read a typed token against resolved codes
  (`_exact_codes_only`, `_group`, the quantity carry) share one reader, `code_or_number`.
- AC-RC-B6: the photo-text shapes (turns 8 and 10, "SORENTO PSJLSRTSC07 x50") are class F
  (the parser glues the brand to the code); once split, the code token takes AC-RC-B1/B2.
- AC-RC-B4 (control): bare "stock?" still gets the scope sentence; "Srts07" still gets
  "Couldn't find SRTS07. Did you mean: 1. SRTSC07 ..." with quick replies (turn 13).
- AC-RC-B5: `test_rearch_r13_stock_no_subject` T4 has an availability-only twin that
  fails on main.

## Class C: a miss never lists what the customer did not name

- AC-RC-C1: "Here's what you want:" lists only entities the customer named (typed this
  message or carried on the focus) that the resolver placed. A miss on the customer's own
  product token prints no header, no "(+N more)", and names the token in "Couldn't find".
  Red: turns 4, 5 (35 and 145 spec_search members under the header), turn 11.
- AC-RC-C2: no `set_page` is carried when the message's own product token is unplaced.
  Red: turn 5 (145 water closets, require price).
- AC-RC-C3 (control): a described-set miss with zero qualifying products keeps its
  described-members reply; a resolved product that missed stock keeps "Here's what you
  want: • product: X / But no inventory matched these."
- AC-RC-C4: no word-matching is added to code for this class (`_CODE_SHAPED` and
  `_names_a_typed_code` are unchanged); the parse of a description with a code inside is
  class F.

## Class G: a missing media caption is None

- AC-RC-G1: an attachment with no `description` (absent key or null) gives
  `caption=None` to media intake; `rendered_text` carries no "null" or "undefined" line.
  Red: turns 8 and 10.
- AC-RC-G2 (control): a caption "X5" still reaches `rendered_text` as its own first line
  above the code lines.

## Class F: parser prompt (eval, not code)

- AC-RC-F1: with the code-in-description addendum, each F1 shape parses the code as the
  product raw, the brand as its own entity and the words as category/specification,
  5/5 on the patched dev v60 text.
- AC-RC-F2: with the bare-count addendum, "X 50 unit" and "x46 pcs" give entities [] and
  the number in `demand_qty` (or the open answer), 5/5.
- AC-RC-F3: the five controls stay 5/5 on both base and patched.
- AC-RC-F4: `test_rogue_convo_prompt_patch.py` (static) is green in CI; the live file is
  `live_external` and never runs in CI.

## Classes D and E: handed over

- AC-RC-D1: PR #1504 carries the turn-6 shape as a red test (confident brand beside a
  code never arms a kind_pick). Posted as a crew-note.
- AC-RC-E1: PR #1502 carries the turn-15/16 shape as red tests (stale `category` extra
  after a did-you-mean pick never takes the fetch). Posted as a crew-note.
