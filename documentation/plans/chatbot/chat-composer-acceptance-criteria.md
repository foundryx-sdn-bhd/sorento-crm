# CHAT-COMPOSER acceptance criteria (UAC)

Card: `chat-composer-behaviour-card.md` (rules R1-R13, crew answers Q1-Q4 = (a), 17 Oct). Plan:
`PLAN-chat-composer-17oct.md`. "Gate on" = `CHAT_COMPOSER_ENABLED=true`; "gate off" = default.
Each AC names the card rule or change it proves. S0/S1 are detailed; later slices are detailed when they start.

## Every slice

- AC-G1 Gate off: every chatbot reply is byte-identical to main. Proof: L1 replay main vs lane, grounding and turn
  tiers, 0 unintended diffs.
- AC-G2 Gate on: the replay's changed replies are exactly the slice's listed changes (diff list reviewed per slice).
- AC-G3 No composer output contains " · ", a UUID, an account count ("(N accounts)", "and N more" after a customer
  name) or a brand / size count line (R1, R2, R3, R5).
- AC-G4 A composer is a pure function of facts: it takes no customer message and reads no parser text (brief rule).
- AC-G5 Every sentence a composer prints in English has its ms and zh catalogue entry (no English fallback).

## S0 scaffolding (no reply change)

- AC-0.1 `Settings.chat_composer_enabled` defaults to False; env `CHAT_COMPOSER_ENABLED=true` sets it True.
- AC-0.2 Shared line rule: a fact prints as `*Label:* value`, one per line (R1).
- AC-0.3 Dates print dd/mm/yyyy for an ISO date, an ISO datetime and a date / datetime object; anything else prints
  unchanged (R13).
- AC-0.4 Money prints `MYR 1,234.00` (thousands separator, 2 decimals) (R13).
- AC-0.5 An un-asked empty field is left out; an asked field with no value gives `No <attr> on record for <product>.`
  (R4, Q3).
- AC-0.6 A capped list ends with `Reply "more" to see the next N.` where N is how many the next page shows (R9, Q4);
  an uncapped list (a picker) has no such line.
- AC-0.7 A composer returns `Reply{text, question, offer}`; `question` and `offer` are structured (options carry real
  names), so code downstream reads flags, not English.

## S1 stock (gate on)

- AC-1.1 Dealer availability lines keep today's words and marks: `CODE x Q: ✅ Got stock.`, `✅ Only N got stock.`,
  `❌ No stock, ETA dd/mm/yyyy.`, `❌ No stock and no incoming. Please refer to your salesman.`, the too-big line,
  the discontinued flag line and the `Alternative:` line (R6; TRAIN cp0-7b70831ab3, cp0-2ec090127f, cp0-b5eb30e1b9).
- AC-1.2 Staff compact and detailed stock blocks keep today's layout (TRAIN cp0-2e2ebf11cb, cp0-37456a72da), except
  that an empty Warehouse / System Location line is left out (Q3).
- AC-1.3 A stock miss on the dealer path reads the same on every path: `Couldn't find X.` (several: `Couldn't find X,
  Y.`), and with candidates `Couldn't find X. Did you mean:` + one numbered real name per line (card s.4 stock (a);
  today three wordings, TRAIN cp0-0498229323).
- AC-1.4 The quantity question has one wording per case: one product `How many units of X?`; several `How many units
  for each?` + one numbered product per line; no option line ends in a dangling " - " (card s.4 stock (b); TRAIN
  cp0-29c26e8809).
- AC-1.5 Stock rows shown under another answer (an ETA ask with no incoming) use the same numbered stock block as a
  stock answer, not the "- *Product Code:*" variant (card s.4 stock (c); TRAIN cp0-5430419641, cp0-338d26fba4).
- AC-1.6 Refer marking, escalation-offer suppression and the open quantity question behave as on the gate-off path
  for the same facts (they read the structured flags).
- AC-1.7 ms and zh: every S1 sentence translates as it does gate off; new sentences have catalogue entries (AC-G5).

## Later slices (outline; detailed when each starts)

- S2 ETA: dates dd/mm/yyyy (TRAIN cp0-0654e3e74a); one "Container" label; an ETA miss says it once
  (TRAIN cp0-5430419641); dealer `CODE: ✅ ETA d` line unchanged.
- S3 not found / clarify / hand-over: one miss grammar; one "more" line; no internal names (TRAIN cp0-75e516bd40); no
  offer or picker left open after a full exact answer (R12; TRAIN cp0-b1f73c72c0, cp0-958b275668, cp0-4430299976);
  access-level question only after a spec-matching product is found (R10).
- S4 files + promotions: one "attached" sentence, one dedupe; one missing-file sentence; "EXPIRED" on every path, kept
  after re-sorting (R7); dates dd/mm/yyyy (TRAIN cp0-b90acd1184).
- S5 order status: every customer header is the group name (R3); one period reply.
- S6 product info + price: `No <attr> on record for <product>.` per product; price answer = code, description,
  list price; un-asked empty fields left out (TRAIN cp0-245d689a0c); fit line unchanged (R8).
- Sets (after #1744 merges): a set reuses the single-code block, capped at 50 with the "more" line, never a reply
  without products (R2, R9; TRAIN cp0-665ba3e897, cp0-75e516bd40).
