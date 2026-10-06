# PLAN: stock_qty fill turn answers the task's product (STOCK-FILL-PREDICATE)

Status: in review (PR #1502, round 8: the live console replay found the no-task sibling open on real data; fixed, see "Round 8"); track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

UAC: `stock-fill-predicate-acceptance-criteria.md` (alongside).

## Report

Owner, 6 Oct 2026 13:20 MYT, production, prompt v47, turn 121 of one dealer conversation.
Turn 120: the customer sent a photo; the bot replied "I read SRTWC8383-RL-GY and SORENTO
from that photo. How many units of SRTWC8383-RL-GY?". State after turn 120:

- `focus.products = [SRTWC8383-RL-GY, uuid settled]`
- `focus.extra.promotion = [{raw "SORENTO", hint "promotion", canonical_code "SORENTO"}]`
  (a parser mis-tag of the brand word; prompt matter, out of scope here)
- `tasks = [{kind stock_qty, slots [{SRTWC8383-RL-GY: null}], domain inventory, status open}]`

Turn 121: the customer typed "1". Parser: no entities, `entity_op reuse`, `check_stock`
under `inventory`, `open_question_answer {mode fill, items [{qty 1, code SRTWC8383-RL-GY,
position 1}]}`. Reply sent:

```
*stock* for SRTWC8383-RL-GY:

Please refer to your salesman.
```

Expected: `SRTWC8383-RL-GY x 1: ✅ Got stock.` with the discontinued marker (the product is
discontinued), or the no-stock line.

## Root cause (crew scout, origin/main 929e3bd23)

1. The fill turn handed the carried uuid-less SORENTO row to the resolver
   (`turn_runtime.with_carried_entities(unsettled_only=True)`, called from `engine.py`).
2. `resolve_gate.resolve_entity_body` adds `require={"stock": true}` for `check_stock`; the
   guard that drops it fires only on a typed product CODE, and SORENTO is a promotion hint.
   The resolver counted every product with stock (356 KB payload).
3. The predicate rode into the inventory fetch (`make_tool_runner` -> `gate["predicate"]`).
4. `output_structurer` withheld the rows of a set past `SET_LIST_MAX`, leaving only the set
   header; `envelope_of` stripped it (not a counted set): `lane_text ""`, no figures.
5. The composer printed the bare domain header; `envelope_missed` filed it as a total miss
   and the salesman line closed it.

## Fix (code only; the parser prompt is not touched)

A. (Owner ruling, 6 Oct 2026 17:40: the principle, not the turn type.) A fetch already
   scoped to a specific resolved product (a spec entity with a real uuid) never takes a
   set predicate, on ANY turn: a task fill, a plain reuse, a fresh pick. A set predicate
   exists only for a set question with no specific product. A stray non-product entity (a
   stale brand / promotion / category / product_type row without uuid) may resolve on its
   own, and may even describe a set of its own, but never gates or widens the product
   lookup. Three seams, each the narrowest place for its half:
   - `turn_runtime.make_tool_runner`'s gate, where the predicate is attached: a spec that
     names a product by uuid (`scoped_to_a_resolved_product`) drops the predicate unless
     the spec's products ARE the population the resolver placed this turn
     (`_spec_is_the_set`). A set question, and a follow-up over a carried set ("cert?"
     after a basin set), re-resolve the members onto the spec (the reconciled plan's
     `list_all` over `resolved_candidates`), so they keep theirs; a task slot or a carried
     subject is not something the resolver placed this turn, so whatever set the stray
     word described is not the fetch's subject. (An earlier revision keyed on the
     predicate's `description`; reviewer round 5 showed a stale word tagged `category`
     binding to a brand and describing a set, and the dealer's "1" refused for scope.) The
     key is popped, not left unset: the gate starts as the resolver's own gate dict.
   - `engine.py`, the `_pass_scope_gate` precedent: on a plan whose every fetch is scoped
     to a resolved product AND every token the resolver was asked about was carried
     (`current_message: False` on each; a class word the dealer TYPED this turn that
     resolves to nothing is still a miss the reply names, pinned by the attribute-asks
     round 4 and 8 replays), the resolver's `not_found` exit is passed with reason
     "product subject": the "no entities and ... requires a scoping entity" gate
     (inventory) and the passed-but-zero-set exit of a domain whose gate permits a broad
     query (incoming) alike, so the answer bridge never answers a fetch that answered the
     product in full as the turn's miss ("Couldn't find any product with stock.").
   - `turn_runtime.unplaced_tokens`: a carried NON-PRODUCT row (`current_message: False`,
     a brand / promotion / category word) is not a token this message named, so its miss
     is not said back ("Couldn't find: SORENTO." under the product's line) and
     production's not-found composer does not re-render the rows (which dropped the
     discontinued marker). A carried product CODE that never resolved is the
     conversation's subject and stays counted: it is what the R6 "never unfiltered" guard
     reads to stop a zero-uuid tool call (reviewer round 6).
   - Round 8 (live console replay, 6 Oct 2026, real parser on the shared dev data).
     `engine.py`, the same `product_subject` condition, the resolver's OTHER exit. On a
     catalogue that holds stock the stray word plus the intent's `require` is a
     population (every product with stock; a word that binds as a brand: that brand's
     products), the exit is `continue`, and the re-entry into apply re-made the plan
     from it: the population replaced the carried product in the fetch and on the focus.
     A plain `check stock` asked "How many units for each?" over the catalogue; the fill
     turn answered right (the task drives its own fetch) and left the population as the
     next turn's subject. Rounds 1 to 7 could not see it: the blank test schema held no
     stock rows, so the word's set was empty and only the `not_found` exit was ever
     exercised. Now, when the resolver was asked about carried NON-product words alone
     and placed products, that population is dropped before the plan is re-made and
     before the gate is read (`turn_runtime.drop_stray_population`; trace
     `stray_carried_word_population_dropped`). The product stays the plan and the focus.
   - Round 8, the other half: a carried DESCRIBED set ("stock?" after "any gunmetal
     basin has incoming?") leaves exactly the same shape on the focus (products with
     uuids beside uuid-less words) and must keep being re-described. Nothing on the
     focus told the two apart, and comparing the carried products with the re-resolved
     population does not either (a follow-up that changes the `require` changes the
     population). So the resolver's rows for a described set (a predicate with a
     `description`) are marked `described_set` when they are placed
     (`turn_runtime.mark_described_set`), and a spec made of marked rows is not a
     product subject for the drop (`spec_is_a_described_set`). A product the dealer
     types or picks later is a fresh, unmarked row.
   The engine's carry hand-off itself is unchanged: the stray word still reaches the
   resolver and may resolve, and it stays on `focus.extra`.
B. `turn/compose.py` never-stuck, with the #1501 principle (owner, 6 Oct 17:50: no claim
   without a lookup). A section that rendered nothing for its named subjects prints the
   existing "No {label} found for {codes}." miss line ONLY when a tool actually ran for
   those codes and came back empty (`envelope_of`'s `tool_ran`: `fetch.tool` is stamped by
   a real call, and an `outcome: not_found` fragment was a call not about them). When the
   tool answered but the lane rendered nothing (a withheld set), or when no tool ran for
   the codes at all (a set predicate's refusal, a gate refusal), it prints the `error`
   arm's existing retry sentence under the header. Never a bare header, never a stock
   claim nothing looked up.

## Tests

`sorento_crm_backend/tests/chatbot/test_stock_fill_predicate.py` (17 tests; since round 8 every
replay runs on a catalogue that holds stock rows, `_seed_stock_rows`, and asserts the focus the
turn leaves is the one product): two engine
replays of the turn 121 state (in stock + discontinued; no stock, no incoming) asserting the
whole reply (the availability line and the discontinued marker, nothing else); the same fill
with the stale word tagged promotion / brand / category / product_type and BINDING to a
seeded brand of that name (reviewer round 5's shape); the sibling replay (a plain
`check stock` reuse turn over the carried product and the stale extra, no task: the quantity
question, then the availability line) plain and with a binding category word; each replay
also asserts the gate the lane received (no predicate, the product alone) and the stock
tool's product ids. A tool-runner gate test (task and carried specs drop the predicate,
described or not; a set question's member spec and a class-word spec keep it), an
`unplaced_tokens` test (carried rows are not named), three compose tests (one per fallback
branch) and an `envelope_of` test for `tool_ran`. Four older expectations in three files pinned a bare header over an
empty hand-built envelope: the three header-grammar tests now read the header over a
row, and the blocked-contact escalation test expects the miss line before the salesman
line.

Round 8 adds `test_the_resolver_marks_the_members_of_a_described_set_and_nothing_else` and, in
round 8 of the attribute asks (`test_attribute_asks_round8.py`),
`test_a_one_word_stock_follow_up_answers_as_the_described_set`: a carried described set still
answers a no-entity follow-up in the set's own shape (the guard for the marker).

Live console cases (real parser, `scripts/chatbot_console_check.py --live`, not collected by
pytest): `tests/chatbot/console_cases/2026-10-06-stock-fill-predicate.yaml` (the dealer steps:
turn 121's state then "1", the no stock control, the no-task reuse) and
`2026-10-06-stock-fill-predicate-set-control.yaml` (the set control, on a contact with the
full stock view).

## Follow-ups (not this PR)

- For the owner: on the "Availability only" stock view a set question ("which basins have
  stock") is answered `How many units for each?` over the first 20 codes of the set (the stock
  tool is called with 50 ids), where the full stock view answers `Here's what you want: wash
  basins with stock (541)` and a line per brand. Measured live on 6 Oct 2026 on the shared dev
  data, identical on main (pre-fix code) and on this branch. Whether a dealer should be asked
  a quantity for every member of a set is a product decision, not a defect of this change.
- A set carried on a session from before this change has unmarked rows: its first no-entity
  follow-up after deploy lists the same carried members under the lane's generic header
  ("Stock summary for the requested products.") instead of the set's own header and count
  (reviewer round 8, N1). One turn per such session; the next set question marks its rows.
- Reviewer round 8, S1 (already so before round 8, not a regression of it): "cert?" over a
  carried product beside a stray word that binds as a brand calls the attachment tool with
  the product's id alone and keeps the focus, but when the product has no certificate the
  miss reply reads `Here's what you want: Sorento products (2)` and lists the brand's other
  product. The line comes from the miss composer (`lanes/business/answer.py`,
  `build_breakdown_msg`), which reads the resolver's raw `resolved` payload; the drop leaves
  that payload alone (N2: it also feeds `scope_block_mod` and `apply_crossdomain_hit`).
  Rule A says the stray word's set is never said back, so this wants its own lane: either
  the drop also clears the population from `resolved`, or the miss composer reads the gate.
- The known limit recorded on `_spec_is_the_set` (a stray word whose described set is exactly
  the carried product) is gone for carried subjects: the population is dropped before the
  runner's gate is reached.
