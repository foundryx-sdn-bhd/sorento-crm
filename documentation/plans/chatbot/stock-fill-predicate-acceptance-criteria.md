# UAC: STOCK-FILL-PREDICATE

Plan: `PLAN-stock-fill-predicate-06oct.md`.

- AC-SFP-1: on a `stock_qty` task fill turn (`open_question_answer.mode fill` or
  `entity_op reuse` over an open task), the inventory tool is called with the task's
  product ids only, with the filled quantities.
- AC-SFP-2: on that turn, no resolver body carries a `require` leg or a carried uuid-less
  extra token (promotion / brand / category rows with no uuid).
- AC-SFP-3: a task's own fetch never carries the turn's set predicate on its gate; a plain
  fetch keeps it (set questions such as "which basins have stock" are unchanged).
- AC-SFP-4: the reply for a product in stock is its availability line
  (`<code> x <qty>: ✅ Got stock.`) plus the discontinued marker when the product is
  discontinued; no "*stock* for" header, no salesman line.
- AC-SFP-5: the reply for a product with no stock and no incoming is
  `<code> x <qty>: ❌ No stock and no incoming. Please refer to your salesman.`, no bare
  header.
- AC-SFP-6 (never-stuck): a one-product section with an empty lane text and no rows never
  composes to a header-only block: the tool's miss prints "No {label} found for {code}.";
  a tool that answered but rendered nothing prints the existing retry sentence under the
  header, never "No stock found".
- AC-SFP-7: no new wording; every sentence printed exists on main today.
