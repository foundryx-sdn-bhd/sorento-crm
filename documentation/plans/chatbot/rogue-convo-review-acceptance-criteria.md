# UAC: rogue dealer conversation, 6 Oct (ROGUE-CONVO-REVIEW)

Plan: `PLAN-rogue-convo-review-06oct.md`. Each criterion is a general rule; the 6 Oct turns
are its red tests. Fake fixture ids in every test; product codes are catalogue codes.

## Class A: zero-scope list asks for a filter

- AC-RC-A1: a fetch in any domain whose `ALLOWS_EMPTY` row is False, with zero resolved
  scoping entities, no filter, no date window and no explicit broaden, calls no tool and
  replies with the scope sentence for that domain ("That would search every product we
  have - I need at least one filter ..."). Red: bare "product" (turn 2); "srtwc-8318"
  typed while a kind_pick is open falls to master_products with no uuid (turn 7).
- AC-RC-A2: the refusal is one mechanism (one constant, `gate.REFUSES_EMPTY_FETCH`, beside
  `ALLOWS_EMPTY`) read by the runner's probe, the runner's "named but none placed" guard
  and the fan-out seam, not a per-domain literal in the engine. Its members are
  `inventory` and `master_products`: the suite measured that every other `ALLOWS_EMPTY`
  False domain has an entity-less ask with its own flow (sales report, top selling, order
  broaden, promotion from the focus brand).
- AC-RC-A3 (control): a scoped product ask ("SRTWC286 price") fetches with that id; bare
  "PO" still lists the open book; `low_stock_report` with no entities still runs.
- AC-RC-A4 (Q1 = a + brand): the chatbot's product list never returns a product whose
  `is_searchable` is false, whose category's `is_searchable` is false, or whose
  `is_active` is false, and a brand-scoped contact sees only its brands' products (the
  existing session criterion, stamped from `contact_id` + `space_id`); the admin products
  list is unchanged.
- AC-RC-A5: a product token typed in `master_products` that the resolver cannot place is
  answered with its own miss wording (the token named) and no fetch, never the scope
  sentence and never the catalogue. Red: turn 7's "srtwc-8318".

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
  fails on main (`test_miss_says_what_it_could_not_find.py::
  TestBareNumberMatchesItsFamilyOnAvailability::test_the_stock_tool_is_never_called_unfiltered`
  and the three picker tests beside it).
- AC-RC-B7 (owner, 6 Oct, second round): the miss line names the token the customer
  TYPED, spelling and separators kept (`Couldn't find: "Srt-hp"`), never a folded token
  ("srthp") and never a word the set reader derived from it ("srt"). Red: turn 11.

## Class C: a miss never lists what the customer did not name

- AC-RC-C1: "Here's what you want:" lists only entities the customer named (typed this
  message or carried on the focus) that the resolver placed, or, on the described-set
  miss, the typed token itself with nothing under it (the #833 structure). A miss on the
  customer's own product token lists no set member and no "(+N more)" under the header,
  and names the token AS TYPED in "Couldn't find": either the product-code miss (`Couldn't find:
  "<line>" (product).`, when the words around the token read a set) or the described-set
  miss's own structure (`Here's what you want: Srt-hp / Couldn't find: "Srt-hp".`, fix
  round 9 on PR #833, when nothing was readable) with the typed token in place of the
  reader's fragments. The "own token missed" join folds separators on both sides (the
  resolver lists the token as sent). Red: turns 4, 5 (35 and 145 spec_search members
  under the header), turn 11; the real-engine replay of turns 4 and 11.
- AC-RC-C2: no `set_page` is carried when the message's own product token is unplaced and
  no class word of the customer's own named the set (the set was read off the words inside
  the token). Red: turn 5 (145 water closets, require price). Control: "which tap has cert
  ZZQ9917" lists the taps, names the missed code, and stays carried for the follow-up
  count (reviewer finding 2: the reply and the carry agree).
- AC-RC-C3 (control): a described-set miss with zero qualifying products keeps its
  described-members reply; a resolved product that missed stock keeps "Here's what you
  want: • product: X / But no inventory matched these."
- AC-RC-C4: no word-matching is added to code for this class (`_CODE_SHAPED` and
  `_names_a_typed_code` are unchanged); the parse of a description with a code inside is
  class F.

## Class G: a missing media caption is None

- AC-RC-G1: an attachment with no `description` (absent key or null) gives
  `caption=None` to media intake, so `rendered_text` carries no "null" or "undefined"
  line. Red: turns 8 and 10. Pinned on the caption handed to intake for the absent-key
  envelope; the null case takes the same `jsc.truthy` read.
- AC-RC-G2 (control): a caption "X5" still reaches `rendered_text` as its own first line
  above the code lines.

## Class F: parser prompt (eval, not code)

- AC-RC-F1: with the code-in-description addendum, each F1 shape parses the code as the
  product entity's `canonical_code` (`raw` stays the typed text, #1511 card Q1 = a), the
  brand as its own entity and the words as category/specification, 5/5 on the patched
  text.
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
