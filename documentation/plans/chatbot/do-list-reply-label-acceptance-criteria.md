# UAC - DO-LIST-REPLY-LABEL

Plan: `PLAN-do-list-reply-label.md`

- AC-DLRL-1: With an `outstanding_detail` offer open (one option, position 1, label
  "Delivery order list", payload value "do", filters scope "do"), a message the parser
  reads as a pick of position 1 that ALSO carries `document: ["DO"]` (the quick-reply label
  "Delivery order list") is answered exactly as "1" is: the delivery order LIST runs
  (`trace.outstanding.detail == "do"`), the offer stays open, the summary is not re-run.
- AC-DLRL-2: The same over the two-scope (three-option) offer (1. Sales order list,
  2. Delivery order list, 3. Both lists): a pick of position 2 with `document: ["DO"]`
  runs the DO list; a pick of position 3 with `document: ["SO", "DO"]` runs both lists.
  At the scope question (1. Sales orders, 2. Delivery orders, 3. Both), "Delivery orders"
  read as position 2 plus `document: ["DO"]` answers the question (report for DO, question
  cleared), the same outcome the named document gave before. A position nobody offered
  never reads as a scope pick.
- AC-DLRL-3: "Sales order" typed after the DO detail list (`document: ["SO"]` with
  `reference_positions: [1]` on the same verdict, the only option being "Delivery order
  list") still reads as a new ask for the SO scope (`NEW_ASK named_document`, scope "so").
  No regression of decide.py's named-document rule.
- AC-DLRL-4: No word of the customer's message is read in code; the engine reads only the
  parser's `reference_positions` / `open_question_answer` and `document` fields against
  the pending's own stored option payload.
- AC-DLRL-5: No wording change to any reply; no prompt change; no `[skip ci]` on the head
  commit.
- AC-DLRL-6 (console replay, run live by crew on the Mac): turn A an outstanding ask that
  yields the summary + offer; turn B "Delivery order list"; turn C "1". B and C print the
  same delivery order list; `open_question_answer` for B and C is recorded on the PR.
