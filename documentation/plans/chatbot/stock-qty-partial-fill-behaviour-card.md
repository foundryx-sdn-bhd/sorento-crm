# Behaviour card: a partial quantity answer closes the stock ask; trap specs parse and search reliably (STOCK-QTY-PARTIAL-FILL)

Status: RULED (6 Oct 2026). Q1 (a), Q2 (a), Q3 (a), Q5 (a) by crew; Q4 (a) by the owner,
parser-decided through the EXISTING fields (entity_op reuse + the specification entity), no
new parser field. See "Rulings" at the end. Owner rulings before the card (6 Oct 22:35): R1 "when
we ask how many units for each and the user answers like 5 for the 2nd one, we answer that one
immediately; the remaining 4 stay OPEN, they can continue answering, or switch to other products,
or other domains; the design must be flexible like this." R2 "apply the spec to the product in
focus first" is NOT a code heuristic: frame it as parser-decided, show what carries it and what
happens when it is absent, owner picks.
Plan: `PLAN-stock-qty-partial-fill-06oct.md` (same folder). Lane: size M, LEAD pattern.
Coordinates with #1509 (CLASS-HINT-KIND-PICK, brand kind), #1511 (canonical_code / semantic
fallback), #1506 (ROGUE class A/B/C/G), #1502 (stock fill predicate: a product-scoped stock fetch
never takes a set predicate).

## Owner report (6 Oct 2026, 22:30, gist)

"This is 80% of our problem": the most common dealer ask on prod is a P trap / S trap water
closet question, and on 6 Oct (prompt v47, main 9373f6c90) two things went wrong with it.

## Step 1: what happens today (`main` cdd459b6)

### Problem 1: "How many units for each?" forces a quantity for every line

Prod, contact 550337739, 10:07 MYT, "Any WC with s trap 300mm available?":

1. The parser read category WC, `trap_type s_trap`, `trap_length 300`; the spec search placed
   five products with stock; the stock tool answered `needs_quantity` for each (the dealer is an
   availability-only contact).
2. `turn/task.py::after_reply` (:846) built one `stock_qty` task with five slots and the reply was
   `StockQtyTask.question` (:354): `How many units for each?` plus `1. CODE - ` per line.
3. 13:44, "I need 8152 6 unit". The parser is handed the question as the `Open question:` object
   (`task.open_question`, :1160: the numbered lines with their codes) and answers it in
   `open_question_answer` (`STOCK_TASK_ADDENDUM`, `chatbot_parser_prompt.py:360-364`: quantities
   for some lines are mode `fill`, one item per line; "a code from the list is an item with that
   code"). `turn/apply.py::_open_question_answer` (:2771) wrote line 2 at 6 as `SLOT_QUANTITIES`.
   `_placed_line` (:2716) places an item by its FULL code or its position only, so line 2 being
   filled means the parser itself resolved "8152" to `SRTWC8152-SH-300` inside the list. That is
   the resolver step the owner wants kept, and it is the parser's, not a code rule.
4. `task.run` (:582-628): the fill moved one slot, `still_missing` was four slots, so no fetch
   (`spec = impl.to_fetch(filled) if (moved and not still_missing)`, :599) and the reply was
   `impl.question(filled)` (:626): the SAME five-line list, line 2 filled. 13:46 the same again.
   Nothing answers until every line is filled: the stuck, repeating state.
5. The only ways a line is dropped today are "just proceed" (`proceed_anyway`, :286) and the list
   pasted back with blanks (mode `done`, `apply.py:2863`), both of which name the dropped lines in
   a `Not checked: ...` line (`turn/compose.py:719`, off `filters.not_checked`).

### Problem 2: trap specs parse one way at 10:07 and another at 14:19

The parser's policy block says (prod snapshot `alembic/data/chatbot_semantic_parser.prod-20261001.txt`
lines 1701, 1737, 1738):

```
Specification dim_width ("Width"): a number in mm. Words: width, wide.
Specification trap_length ("Trap outlet length"): a number in mm. Words: trap.
Specification trap_type ("Trap"): choices S trap = s_trap (s trap, s-trap, floor outlet, floor waste); P trap = p_trap (p trap, p-trap, wall outlet, wall waste).
```

and `SPECIFICATION_ADDENDUM`'s only trap example is "f trap" -> `trap_type` with `spec_value null`
(`chatbot_parser_prompt.py:423, :440`). Nothing says what a bare "300mm" beside a trap word is, so:

| time | contact | message | parse | result |
| --- | --- | --- | --- | --- |
| 10:07 | 550337739 | "Any WC with s trap 300mm available?" | trap_type s_trap + trap_length 300 | 5 right products |
| 14:19 | 477071887 | "Any WC with S trap 300mm available" | trap_type s_trap + dim_width 300 | 2 wrong products (SRTWC201, SRTWC204), "no stock matched" |
| 12:45 | 550337635 | "Srtwc8066-sh s trap 250mm 50pc" | product SRTWC8066-SH x 50 + trap_type + trap_length 250 | which-one pick of the four -SH colour variants |
| 14:50 | 550337680 | "How about p trap" after SRTWC286-SH x 10 | trap_type p_trap, user_goal "for the current product context", entity_op replace_combine | domain master_products, "P trap water closets (182)" + brand breakdown; "Sorento" -> 50-product dump |
| 15:29 | 550337608 | "FIND S TRAP 200MM SORENTO WC" | SORENTO -> kind_pick (customer / promotion / form / transporter) | lane #1509 owns the pick; this lane owns what happens once the brand is kept |

How a grounded spec reaches the search today (verified, the chain this lane tests):

- `head/grounding.py::specification_entities` keeps only emissions whose key and value the
  registry knows; `lanes/business/resolve_gate.py:637` sends them as `extracted_specs` (a value
  the registry does not know goes as `unknown_values` and is said back, never searched).
- The ask's own predicate: `predicate.derive_require` maps `check_stock` (and the words "stock" /
  "available") to `{"stock": true}` (`predicate.py:27, :110`); the category entity is the
  `scope_terms` (`resolve_gate.py:687`); a brand entity is an AND-scope over the whole set, never
  a union member (`references.py:2929-2935`).
- `references.py:2850`: the set is counted ONLY when no token matched a product by code
  (`_code_matched`). A code beside spec words takes the other path: `narrow_code_tokens`
  (`product_code_family.py:146`, `references.py:2799`) reads the words beside the code and picks
  among that code's variants ("Srtwc7604 p trap price"). That is why 12:45 was a colour pick:
  the four SRTWC8066-SH variants all carry the same trap spec, so the pick is right; whether the
  variant narrowing honours `trap_length` when the variants differ by it is what this lane's
  search tests settle.
- `resolve_product_set` counts the set with `stock_policy` when `require.stock` is set
  (`references.py:2996`), so "available" keeps the stock requirement through to the answer.

## Proposed behaviour

### P1. A partial answer is answered now; the rest stays open, never re-listed (R1)

When the stock task has two or more lines and the message fills SOME of them (by line number,
by code, by a fragment of a listed code the parser resolves to exactly one line, or "CODE x N"):

- the filled lines are answered NOW: the stock lookup runs for those lines only and the reply is
  their normal availability lines, nothing else;
- the unfilled lines stay on the task, still owed, and the reply does NOT print the list again;
- the parser keeps seeing the task as the `Open question:` (kind `quantities`, the filled lines
  with their quantity, `owed` the rest), so a later "8357 x 10", "line 4: 10" or "10 for the
  first" fills and answers that line the same way, and so on until the dealer stops;
- "all N" / "N each" / "N for all of them" still fills every line, the answered ones included
  (mode `all`, 26 Sep F2, unchanged: "all" is the dealer's own word for every line);
- a message naming a product NOT on the list, or another domain, simply proceeds: the task is
  parked exactly as today (`task.run` arm 4, silent), the open lines never block it, and the
  parser's own `entity_op` / continuation decides what the new ask reuses (no code rule);
- "just proceed" and the list pasted back with blanks keep today's behaviour (answer what is
  noted, drop the rest, `Not checked:` line): those are explicit instructions to close.

Wording: no new wording. The reply to a partial answer is the stock lines the product would get
on a plain "CODE x N" ask. No line about the lines still open (one fact per line; the dealer did
not ask for them).

### P2. Trap specs: parser addendum, no regex

A new `TRAP_SPECS_ADDENDUM` for `chatbot_semantic_parser`, shipped as addendum text files plus
a committed unified diff (`alembic/data/chatbot_semantic_parser.trap-specs.patch`) that crew
sequences onto the live combined version as the next UNLABELLED version (PROMPT-DYNAMIC #1405:
no migration rebuilds a version from code; promoted by the owner):

- "s trap", "s-trap", "S trap", "strap" said of a toilet -> specification `trap_type` =
  `s_trap`; "p trap", "p-trap" -> `p_trap` (the value, never null; null stays for a letter the
  registry has no choice for, "f trap", as today);
- a number in mm said beside a trap word, before or after it, with or without "mm" ("s trap
  300mm", "300mm s trap", "S trap 300", "250mm P-trap") is `trap_length`, NEVER `dim_width`,
  `dim_length` or `dim_height`: two specification entities, one per key;
- "WC", "water closet", "toilet", "close couple(d)", "one piece" is the category; "available" /
  "got stock" is the stock ask; a brand word (Sorento) is the brand entity (the #1509 kind);
- in any language ("tandas s trap 300mm", "S弯 300mm 马桶").

Eval: `tests/chatbot/console_cases/2026-10-06-trap-specs.yaml`, `expect.parser.entities` with
`spec_key` / `spec_value` per entity, red on v47 (the 14:19 sentence), green on the new version;
the 15:29 sentence carried as a control marked `expected_red_until: #1509`. Live run on the Mac
by crew (the cloud has no parser key).

### P3. Spec search narrowing, proven by tests

`trap_type` + `trap_length` + class + brand narrow the set to the products holding every value
(exact mode, `search_specs(exact=True)`), and `require.stock` keeps only the ones with stock.
Code + trap words narrow to the code's variants carrying that trap spec. Tests in
`tests/chatbot/` and `tests/test_resolve_predicate.py`'s harness, seeded catalogue, no `LIMIT 1`.

### P4 (R2, owner picks). "How about p trap" after a product answer

Today the parser emitted `entity_op replace_combine` with ONLY the specification entity, so the
product in focus was dropped and the set was counted class-wide (182 P trap water closets). The
parser's own fields already carry the other reading, and the code path for it exists:

- `entity_op "reuse"` (or `"modify"`) keeps the product in focus beside the new specification
  entity; `resolve_gate` then sends the carried code as a token and the grounded spec as
  `extracted_specs`. CORRECTED after measurement (`test_trap_spec_search.py::test_the_message
  _words_cannot_narrow_a_carried_product`): the existing `narrow_code_tokens` reads the variant
  words OFF THE MESSAGE and needs the code word in it (`product_code_family.py:54`), so a
  carried product is never narrowed by it. The resolver half is therefore one small addition,
  `narrow_code_tokens_by_specs` (`product_code_family.py`): the code's family (the same family
  rule) narrowed by the grounded spec VALUES the parser emitted, no word read. It picks
  `SRTWC286-SH-P` for trap_type p_trap; a family every variant of which carries the spec is
  left alone; a family with NO variant carrying it is left as it is (the product itself is
  answered, exactly as a word no variant carries is ignored today, rule 3), never a silent
  widening to the class. No heuristic over the message: the parser's `entity_op` is the switch,
  and the prompt lines that teach it are in the trap-specs addendum ("A SPEC SAID RIGHT AFTER A
  PRODUCT ANSWER").
- When the parser does NOT carry the product (`replace_combine`, or no product in focus), the
  ask stays class-wide exactly as today.
- When the product has no variant carrying the spec, the code is left as it is and the
  product itself is answered (no new wording; the corrected bullet above), never a silent
  widening to the class.

## Examples (expected parse -> reply)

Real prod sentences, plus paraphrases the lane covers. Codes are the prod ones where known.

1. "Any WC with s trap 300mm available?" -> category "WC", specification trap_type s_trap,
   specification trap_length 300, require stock -> the S trap 300mm water closets with stock ->
   `How many units for each?` + 5 lines (unchanged).
2. "I need 8152 6 unit" over that list -> open_question_answer fill, item code SRTWC8152-SH-300
   qty 6 -> the stock lines for SRTWC8152-SH-300 x 6. Lines 1, 3, 4, 5 stay open. No list.
3. "8357 x 10" next -> fill, line 4 -> the stock lines for SRTWC8357-300-RL x 10.
4. "SRTWC8152-SH-300 x 6 Units Have stock?" (the frustrated 13:50 message) over the open list ->
   the same answer as 2, never the list.
5. "10 for all" / "semua 10" -> mode all -> every open line at 10, one answer (unchanged).
6. "Any WC with S trap 300mm available" (14:19) -> the SAME parse as 1: trap_length 300, not
   dim_width.
7. "Srtwc8066-sh s trap 250mm 50pc" -> product SRTWC8066-SH quantity 50 + trap_type s_trap +
   trap_length 250 -> the variants of SRTWC8066-SH that are S trap 250mm; several with the same
   trap spec -> the colour pick, as today; the variants differing by trap length -> only the
   250 ones.
8. "FIND S TRAP 200MM SORENTO WC" -> brand Sorento + category WC + trap_type s_trap +
   trap_length 200 -> the Sorento S trap 200mm water closets (control; red until #1509 keeps the
   brand).
9. "How about p trap" after "SRTWC286-SH x 10" -> (P4 a) entity_op reuse, product SRTWC286-SH
   carried + trap_type p_trap -> SRTWC286-SH-P x 10 stock lines; (P4 b, today) class-wide.
10. "tandas s trap 300mm ada stok?" -> as 1, in Malay.

## Edge cases

- A fragment that matches two listed lines ("8152" when both SRTWC8152-SH-300 and
  SRTWC8152-SH-UF-300 are listed, as they were on 6 Oct): the parser places it on the line it
  reads; when it places nothing (`_placed_line` finds no line), the message falls to the ordinary
  path and is a stock ask for that fragment, which the resolver answers with its exact-code /
  family rule. Not a stuck state either way.
- A bare number over several open lines ("10") keeps the ruled behaviour (owner 26 Sep, F2): it
  fills every open line, one answer. Question Q3.
- A quantity for a line already answered ("make 8152 10") replaces it and re-answers that line
  (`fill`'s own restate rule, unchanged).
- The dealer answers a line, asks about another product, then comes back ("SRTWC8357-300-RL x
  5"): the parked task is not the open question any more, so the message is a plain stock ask
  for that code and gets the same stock lines. Flexible, no re-list.
- "cancel" / "never mind the stock check" closes the task (mode cancel, unchanged).
- Until the owner promotes the new prompt version, v47 keeps parsing trap lengths
  inconsistently; P1 lands with the merge regardless (it is engine code).

## Questions (crew-ask)

- **Q1. The reply to a partial answer.** (a) only the answered line's stock lines; (b) add one
  line naming the lines still open ("Still open: 1, 3, 4, 5"). **Recommend (a)**: one fact per
  line, the dealer did not ask for them, and the parser still sees them as the open question.
- **Q2. A code in the answer that is NOT on the list** ("SRTWC9999 x 5" over the list). (a) a
  new stock ask for that code, the list parked silently, exactly as a new product ask today;
  (b) answer it AND re-list. **Recommend (a)**: R1's "switch to other products" and never a
  re-list.
- **Q3. One bare number over several open lines** ("10"). (a) keep F2: it fills every open line
  and one answer comes back; (b) ask "Is 10 for all 4 lines, or for one of them?". **Recommend
  (a)**: ruled 26 Sep, it is an answer rather than a question, and "10 for 8357" is one word
  away for the dealer.
- **Q4. "How about p trap" after a product answer (R2).** (a) parser-carried: the addendum
  teaches entity_op reuse for a bare spec follow-up, the resolver's existing variant narrowing
  answers with the sibling, and a product with no such variant is said so; (b) leave class-wide
  as today. **Recommend (a)**: the parser already wrote "for the current product context" on
  that turn, so the decision is its own; the code adds no heuristic.
- **Q5. Trap specs on a code ask with variants that DIFFER by trap length** (SRTWC8066-SH-300
  beside SRTWC8066-SH-250). (a) the spec filters the family first, then the colour pick over
  what is left; (b) the pick lists every variant as today. **Recommend (a)**: the dealer said
  250, a 300 is a wrong toilet.

## Rulings (6 Oct 2026)

- **Q1 (a)** (crew): the reply to a partial answer is that line's stock lines only; no "still
  open" line.
- **Q2 (a)** (crew): a code not on the list is a new stock ask; the list is parked silently.
  One stock task per conversation (R1 scope cut), so the new ask's own answered check replaces
  the parked list; a later "8357 x 10" is a plain stock ask for that code and gets the same
  stock lines.
- **Q3 (a)** (crew): one bare number over the open lines fills every open line (26 Sep F2).
  The lines answered earlier are not fetched again.
- **Q4 (a)** (owner): parser-decided, through the existing fields (`entity_op "reuse"` + the
  specification entity). The resolver half is `narrow_code_tokens_by_specs` (above). No new
  parser field: the existing ones express it, so none is named.
- **Ceiling re-pin (a)** (crew, 6 Oct 2026, review round): the code-fallback prompt constant
  carries the trap-specs block, so `test_parser_prompt_budget.CEILING` is re-pinned to the
  measured 46,598 in the same commit (the test's own rule for a genuine new addendum; the
  eighth such re-pin). The live prompt grows by the two addenda alone under either option.
- **Q5 (a)** (crew): the stated spec filters the family first; the colour pick runs over what
  is left. Measured: `narrow_code_tokens` already does this when the catalogue text carries the
  length ("S-TRAP 250MM"), and `narrow_code_tokens_by_specs` does it off the derived values.

## Search finding (measured while proving P3)

`resolve_product_set` keeps a NUMERIC binding as a ranking boost, not a membership filter
(R27 / AC-1352 on PR #833: "trap_length 250 stays a boost inside the set"), EXCEPT a number the
parser said and `head/grounding.py` checked against the registry: a `grounded: True` numeric
spec defines membership like a named choice (`product_spec_search.filter_specs`, fix round 8 on
#833). The chatbot sends every grounded spec that way (`resolve_gate.py:638`), so "S trap
300mm" from the parser filters the set to the 300 ones; a length the deterministic reader bound
by proximity only ranks. Both are pinned in `test_trap_spec_search.py`. No change to that rule.
