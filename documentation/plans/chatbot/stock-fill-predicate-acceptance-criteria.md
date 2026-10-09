# UAC: STOCK-FILL-PREDICATE

Plan: `PLAN-stock-fill-predicate-06oct.md`.

- AC-SFP-1: on a `stock_qty` task fill turn (`open_question_answer.mode fill` or
  `entity_op reuse` over an open task), the inventory tool is called with the task's
  product ids only, with the filled quantities.
- AC-SFP-2: a stray non-product entity carried without a uuid (promotion / brand /
  category / product_type row) may resolve on its own, and may describe a set of its own,
  but never gates or widens the product lookup: the gate the lane receives names the
  product alone, the stock tool is called with its id alone, the resolver's "no entities"
  exit is not the turn's miss, and the word is not said back as a miss. Holds on a task
  fill turn and on a plain `check stock` reuse turn over the carried product (the sibling
  case), whether or not the word binds.
- AC-SFP-3: a fetch scoped to a specific resolved product (a spec entity with a uuid whose
  products are not the population the resolver placed this turn) never carries a set
  predicate on its gate, on any turn; a set question's fetch (its spec IS that population)
  and a fetch that names only a class word keep theirs (set questions such as "which basins
  have stock", and follow-ups over a carried set, are unchanged).
- AC-SFP-4: the reply for a product in stock is its availability line
  (`<code> x <qty>: ✅ Got stock.`) plus the discontinued marker when the product is
  discontinued; no "*stock* for" header, no salesman line.
- AC-SFP-5: the reply for a product with no stock and no incoming is
  `<code> x <qty>: ❌ No stock and no incoming. Please refer to your salesman.`, no bare
  header.
- AC-SFP-6 (never-stuck, no claim without a lookup): a one-product section with an empty
  lane text and no rows never composes to a header-only block. A tool that ran for the
  code and came back empty prints "No {label} found for {code}."; a tool that answered but
  rendered nothing, or no tool run for the code at all, prints the existing retry sentence
  under the header, never "No stock found".
- AC-SFP-7: no new wording; every sentence printed exists on main today.
- AC-SFP-8 (round 8, the live replay): AC-SFP-2 holds on a catalogue that HOLDS stock, where
  the stray word plus the intent's `require` resolves to a population: the plan is not
  re-made from that population, on a fill turn and on a plain reuse turn alike. The reply is
  the product's (`How many units of <code>?`, then its availability line), the stock tool is
  called with the product's id alone, and the focus the turn leaves is that product, never
  the population.
- AC-SFP-9: a carried DESCRIBED set is not a product subject. A no-entity follow-up over it
  ("stock?" after "any gunmetal basin has incoming?") re-describes the set and answers in the
  set's own shape (header and count), as before.
