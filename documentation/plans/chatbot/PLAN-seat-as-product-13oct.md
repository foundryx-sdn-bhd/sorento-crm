# PLAN: seat cover = product resolution (SEAT-AS-PRODUCT)

Status: Plan (lane SEAT-AS-PRODUCT). Track: standard (chatbot pipeline change, red-first tests).

## Owner rule (13 Oct)

"Resolving seat cover should just be like resolving product. The domain behind should depend on
what's resolved by the parser, as usual. No new lanes in answering. Stock representation should
reuse our existing function, no new function. Same for ETA."

## Failures on main (#1601, parser v88)

1. "seat cover for SRTWC8383-RL-MBL": one contact got a Technical Specifications miss, another got
   the #1601 find reply (`Code: SRTSCBD823-MBL / Stock: 11`).
2. "check stock seat cover for SRTWC8122": a new stock format, not the existing stock answer.
3. "eta seat cover for SRTWC8383-RL-MBL": answered stock instead of ETA.

Details, contract and test list follow after the code read.
