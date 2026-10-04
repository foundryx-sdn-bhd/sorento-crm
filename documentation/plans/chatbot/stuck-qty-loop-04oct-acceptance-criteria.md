# UAC: STUCK-QTY-LOOP (PR #1471)

- AC-1: An availability contact with an open "How many units for each?" question who asks for a
  low stock report never gets the quantity question back, on that turn or the next.
- AC-2: After the contact is switched from availability to compact (or any access change: stock
  mode, escalation allowed, stock allowed, packing list, tier, field reveals), the next message
  never replays a question built under the old access.
- AC-3: "clear" read by the parser as `topic_reset` ends every held question (open question,
  stock task, low stock category ask, set page / clarify, top selling questions).
- AC-4: An inventory message with no intent and nothing named never replays the STORED quantity
  question ("back to the stock check", a `check_stock` intent, still resumes it).
- AC-5: A message whose intent differs from the conversation's and answers nothing held drops
  every held question and is answered as its own question, for every pending kind.
- AC-6: A genuine answer (a picked position, a quantity, the parser's `open_question_answer`) is
  never dropped by AC-5.
- AC-7: A held question untouched for more than 6 turns is gone.
- AC-8: A refinement under a different intent starts fresh: the old intent's carried entities
  (e.g. a sales agent from a ranking) never reach the new intent's resolver.
- AC-9: A lane's own required question is never replaced by a "Could not find" miss.
- AC-10: A new `Focus`/`State` field or pending kind that the central rule does not classify
  fails the contract test.
